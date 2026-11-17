"""Hypothesis strategies: random valid CalculationGraphs, scenarios and route-legal plans.

    from tests.strategies import calc_graphs, scenarios_for, legal_plans
"""
from __future__ import annotations

import math

from hypothesis import strategies as st

from lagrange_rx.engine.types import Plan, PlanLine, Scenario
from lagrange_rx.ontology import (
    CalculationGraph, ClientGraph, CostItem, DemandForecast, Finance, Product, Route, Supplier,
)

CURRENCIES = ("USD", "EUR", "CNY", "INR")
MODES = ("sea", "rail", "air", "road")


def _money(lo: float, hi: float):
    return st.floats(min_value=lo, max_value=hi, allow_nan=False, allow_infinity=False)


@st.composite
def _route(draw, rid: str, supplier_ids: list[str], currencies: list[str], emergency: bool) -> Route:
    lot = draw(st.sampled_from([1, 10, 50, 100]))
    min_k = draw(st.integers(0, 5))
    max_k = draw(st.integers(max(min_k, 1), 20))
    pr = draw(st.one_of(st.none(), _money(0.0, 5000.0)))
    return Route(
        id=rid, supplier_id=draw(st.sampled_from(supplier_ids)), name=rid, mode=draw(st.sampled_from(MODES)),
        currency=draw(st.sampled_from(currencies)), kc=draw(_money(0.1, 100.0)),
        duty_rate=draw(_money(0.0, 0.3)), fees_uzs=draw(_money(0.0, 5000.0)),
        pr_uzs=pr, pr_confirmed=draw(st.booleans()), freight_uzs=draw(_money(0.0, 3000.0)),
        lot_size=lot, min_batch=lot * min_k, max_batch=lot * max_k,
        capacity_packs=lot * draw(st.integers(1, 100)), max_shipments=draw(st.integers(0, 6)),
        fixed_per_shipment_uzs=draw(_money(0.0, 1e6)), fixed_activation_uzs=draw(_money(0.0, 1e6)),
        lead_time_days=draw(st.integers(0, 90)), supplier_terms_days=draw(st.integers(0, 60)),
        is_eligible=draw(st.sampled_from([True, True, True, False])), emergency=emergency,
        emergency_premium_uzs=draw(_money(0.0, 5000.0)) if emergency else 0.0,
        emergency_cap_packs=lot * draw(st.integers(0, 20)) if emergency else 0,
    )


@st.composite
def calc_graphs(draw) -> CalculationGraph:
    """1-4 regular + 0-2 emergency routes; internally consistent; confirmed data only."""
    suppliers = [Supplier(id=f"S{i}", name=f"s{i}", reliability=draw(_money(0.5, 1.0))) for i in range(draw(st.integers(1, 3)))]
    others = draw(st.lists(st.sampled_from(CURRENCIES[1:]), min_size=1, max_size=3, unique=True))
    currencies = ["USD", *others]
    nominal_fx = {cur: draw(_money(500.0, 20_000.0)) for cur in currencies}
    sup_ids = [s.id for s in suppliers]
    regular = [draw(_route(f"R{i}", sup_ids, currencies, False)) for i in range(draw(st.integers(1, 4)))]
    emergency = [draw(_route(f"E{i}", sup_ids, currencies, True)) for i in range(draw(st.integers(0, 2)))]
    routes = regular + emergency
    chun = draw(_money(1000.0, 2_000_000.0))
    contract = draw(st.one_of(st.none(), _money(0.3, 1.2).map(lambda k: round(chun * k / 1.12, 2) or 1.0)))
    items = [
        CostItem(id=f"c{r.id}", name="acq", layer="acquisition", route_id=r.id, per_pack_uzs=draw(_money(0.0, 2000.0)))
        for r in routes if draw(st.booleans())
    ] + [CostItem(id="bl", name="below", layer="below_line", per_pack_uzs=draw(_money(0.0, 500.0)))]
    q = sorted(draw(st.lists(_money(0.0, 50_000.0), min_size=3, max_size=3)))
    year, month = draw(st.integers(2026, 2027)), draw(st.integers(1, 12))
    return ClientGraph(
        client_id="c", product=Product(
            id="P", name="p", chun_uzs=chun, chchn_uzs=chun * draw(_money(1.0, 2.0)), contract_price_uzs=contract,
        ),
        suppliers=tuple(suppliers), routes=tuple(routes), cost_items=tuple(items),
        demand=DemandForecast(p10=q[0], p50=q[1], p90=q[2]), finance=Finance(), nominal_fx=nominal_fx,
        horizon_start=f"{year}-{month:02d}", penalty_uzs_per_pack=draw(_money(0.0, 5000.0)),
    ).for_calculation()


@st.composite
def _scenario(draw, graph: CalculationGraph, sid: str) -> Scenario:
    ref = graph.product.reference_currency
    currencies = {r.currency for r in graph.routes} | {ref}
    fx = {cur: graph.nominal_fx[cur] * draw(_money(0.7, 1.5)) for cur in sorted(currencies)}
    start = graph.nominal_fx[ref] * draw(_money(0.8, 1.2))
    steps = draw(st.lists(_money(0.97, 1.04), min_size=12, max_size=12))
    path = tuple(start * math.prod(steps[: i + 1]) for i in range(12))
    cut_month = draw(st.one_of(st.none(), st.integers(1, 12)))
    return Scenario(
        id=sid, name=sid, kind="sample", weight=draw(_money(0.1, 3.0)), fx=fx, ref_fx_start=start, ref_fx_path=path,
        cut_month=cut_month, cut_depth=draw(_money(0.10, 0.37)) if cut_month else 0.0,
        freight_mult={m: draw(_money(0.8, 3.0)) for m in MODES if draw(st.booleans())},
        route_available={r.id: False for r in graph.routes if draw(st.sampled_from([True, True, True, False]))},
        demand=draw(_money(0.0, 60_000.0)),
    )


@st.composite
def scenarios_for(draw, graph: CalculationGraph) -> list[Scenario]:
    """1-4 valid scenarios (12-value ref_fx_path, random cut / availability / freight / demand)."""
    return [draw(_scenario(graph, f"s{i}")) for i in range(draw(st.integers(1, 4)))]


@st.composite
def _line(draw, route: Route) -> PlanLine | None:
    shipments = draw(st.integers(0, min(route.max_shipments, 6)))
    lo = -(-route.min_batch * shipments // route.lot_size)
    hi = min(route.max_batch * shipments, route.capacity_packs) // route.lot_size
    if shipments == 0 or lo > hi:
        return None
    return PlanLine(route_id=route.id, packs=route.lot_size * draw(st.integers(lo, hi)), shipments=shipments)


@st.composite
def legal_plans(draw, graph: CalculationGraph) -> Plan:
    """A plan that satisfies every PLAN-ROUTE-LIMITS rule (lot, batch, capacity, shipments)."""
    lines = [ln for r in graph.eligible_routes() if (ln := draw(_line(r))) is not None]
    return Plan(plan_id="p", sku_id=graph.product.id, horizon=graph.horizon_start, lines=tuple(lines))
