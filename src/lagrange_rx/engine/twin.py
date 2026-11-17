"""A. Digital twin v2: deterministic unit economics per route and scenario, plan evaluation, calibration.

No LLM, no randomness. Takes only `graph.for_calculation()` output.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

import numpy as np
from scipy.optimize import linprog

from lagrange_rx.engine.indexation import annual_ceiling_factor
from lagrange_rx.engine.types import Plan, Scenario, ScenarioOutcome
from lagrange_rx.ontology import CalculationGraph, Route, require_calculation_graph
from lagrange_rx.rules import Rules

CALIBRATION_TOLERANCE_PP = 0.5  # percentage points
DAYS = 365.0


class TwinError(ValueError):
    """The twin refuses to compute and names the reason."""


@dataclass(frozen=True)
class Caps:
    wholesale: float  # ChUN / 1.12, excl. VAT
    retail: float  # ChChN / 1.12 / 1.20
    contract: float | None
    factor: float  # annual ceiling factor (P20/P22)
    published_chun: float
    published_chchn: float

    def binding_cap(self) -> tuple[str, float]:
        items = [("registry", self.wholesale), ("retail", self.retail)]
        if self.contract is not None:
            items.append(("contract", self.contract))
        return min(items, key=lambda kv: kv[1])


@dataclass(frozen=True)
class RouteEconomics:
    route_id: str
    legal_base: float  # b_is: Kc*fx*(1+duty)+fees(+Pr if confirmed); no hedge, no freight
    unit_cost: float  # c_is: economic cost per pack incl. freight, acquisition items, finance (spot, unhedged)
    hedge_unit: float  # Kc*(forward - spot): extra cost per pack if 100% hedged


@dataclass(frozen=True)
class UnitEconomics:
    scenario_id: str
    routes: dict[str, RouteEconomics]
    caps: Caps
    warnings: tuple[str, ...] = ()


@dataclass(frozen=True)
class CalibrationIssue:
    gap_pp: float
    unexplained_cost_uzs: float
    missing_data: tuple[str, ...]


@dataclass(frozen=True)
class CalibrationResult:
    twin_margin: float | None
    actual_margin: float | None
    gap_pp: float | None
    calibrated: bool
    issues: tuple[CalibrationIssue, ...] = ()
    missing_data: tuple[str, ...] = field(default_factory=tuple)


# --------------------------------------------------------------------------- ceilings

def caps_for(graph: CalculationGraph, scenario: Scenario, rules: Rules) -> Caps:
    require_calculation_graph(graph)
    f = annual_ceiling_factor(
        scenario.ref_fx_start, scenario.ref_fx_path, scenario.cut_month, scenario.cut_depth,
        graph.horizon_start, rules,
    )
    p = graph.product
    chun, chchn = p.chun_uzs * f, p.chchn_uzs * f
    return Caps(
        wholesale=chun / (1.0 + rules.vat),
        retail=chchn / (1.0 + rules.vat) / (1.0 + rules.retail_markup),
        contract=p.contract_price_uzs,
        factor=f, published_chun=chun, published_chchn=chchn,
    )


def formula_warnings(graph: CalculationGraph, rules: Rules) -> tuple[str, ...]:
    """Published ChUN vs the P19 formula (assumption in rules yaml): >1% divergence -> warning."""
    p = graph.product
    if p.rn_foreign is None:
        return ()
    formula = (p.rn_foreign * graph.nominal_fx[p.reference_currency]
               * (1.0 + rules.formula_wholesale_markup) * (1.0 + rules.vat))
    gap = abs(p.chun_uzs - formula) / formula
    if gap > rules.formula_tolerance:
        return (f"published ChUN differs from P19 formula by {gap:.1%} (>{rules.formula_tolerance:.0%})",)
    return ()


# --------------------------------------------------------------------------- costs

def legal_base(route: Route, fx: float) -> float:
    customs = route.kc * fx
    pr = route.pr_uzs if (route.pr_confirmed and route.pr_uzs is not None) else 0.0
    return customs * (1.0 + route.duty_rate) + route.fees_uzs + pr


def acquisition_extra(graph: CalculationGraph, route_id: str) -> float:
    return sum(i.per_pack_uzs for i in graph.cost_items if i.layer == "acquisition" and i.route_id == route_id)


def finance_per_pack(graph: CalculationGraph, route: Route, cash_cost: float) -> float:
    fin = graph.finance
    days = max(0, route.lead_time_days + fin.inventory_days + fin.receivable_days - route.supplier_terms_days)
    return cash_cost * fin.credit_rate_annual * days / DAYS


def hedge_unit(graph: CalculationGraph, route: Route, fx_spot: float) -> float:
    forward = graph.nominal_fx[route.currency] * (1.0 + graph.finance.hedge_forward_premium)
    return route.kc * (forward - fx_spot)


def route_economics(graph: CalculationGraph, route: Route, scenario: Scenario) -> RouteEconomics:
    fx = scenario.fx[route.currency]
    base = legal_base(route, fx)
    cash = base + route.freight_uzs * scenario.freight(route.mode) + acquisition_extra(graph, route.id)
    cost = cash + finance_per_pack(graph, route, cash)
    return RouteEconomics(route.id, base, cost, hedge_unit(graph, route, fx))


def unit_economics(graph: CalculationGraph, scenario: Scenario, rules: Rules) -> UnitEconomics:
    require_calculation_graph(graph)
    below_line(graph)  # refuses on unallocated items
    routes = {r.id: route_economics(graph, r, scenario) for r in graph.routes if r.is_eligible}
    return UnitEconomics(scenario.id, routes, caps_for(graph, scenario, rules), formula_warnings(graph, rules))


def capital_per_pack(graph: CalculationGraph, route: Route) -> float:
    """Working-capital outlay per pack at nominal rates (cash cost incl. freight, acquisition items)."""
    base = legal_base(route, graph.nominal_fx[route.currency])
    return base + route.freight_uzs + acquisition_extra(graph, route.id)


def below_line(graph: CalculationGraph) -> tuple[float, float]:
    """(variable UZS per pack sold, fixed UZS per year) attributable to the product."""
    var = fixed = 0.0
    for it in graph.cost_items:
        if it.layer != "below_line":
            continue
        if it.allocation == "unallocated":
            raise TwinError(f"cost item '{it.name}' is unallocated: set allocation before calculating")
        if it.allocation == "direct":
            share = 1.0 if it.share is None else it.share
        elif it.share is None:
            raise TwinError(f"cost item '{it.name}' ({it.allocation}) needs a share")
        else:
            share = it.share
        var += it.per_pack_uzs * share
        fixed += it.annual_uzs * share
    return var, fixed


# --------------------------------------------------------------------------- plan evaluation

def plan_fixed_cost(graph: CalculationGraph, plan: Plan) -> float:
    total = 0.0
    for route_id, packs in plan.packs_by_route().items():
        r = graph.route(route_id)
        n = sum(ln.shipments for ln in plan.lines if ln.route_id == route_id)
        total += r.fixed_per_shipment_uzs * n + (r.fixed_activation_uzs if packs > 0 else 0.0)
    return total


def purchase_cost(graph: CalculationGraph, plan: Plan, ue: UnitEconomics, scenario: Scenario) -> float:
    """Cost of ordered packs (only delivered ones) plus forward-hedge settlement on all contracted packs."""
    total = 0.0
    for route_id, packs in plan.packs_by_route().items():
        eco = ue.routes[route_id]
        if scenario.available(route_id):
            total += eco.unit_cost * packs
        total += plan.fx_hedge_share * eco.hedge_unit * packs
    return total


def _emergency_routes(graph: CalculationGraph, scenario: Scenario, allow: bool) -> list[Route]:
    if not allow:
        return []
    return [r for r in graph.emergency_routes() if scenario.available(r.id) and r.emergency_cap_packs > 0]


@dataclass(frozen=True)
class _Recourse:
    sold_by_route: dict[str, float]
    emergency: dict[str, float]
    revenue: float
    emergency_cost: float


def _solve_recourse(graph, plan, scenario, ue, bl_var, markup, allow_emergency) -> _Recourse:
    supply = {rid: float(p) for rid, p in plan.packs_by_route().items() if scenario.available(rid) and p > 0}
    emerg = _emergency_routes(graph, scenario, allow_emergency)
    keys = list(supply) + [r.id for r in emerg]
    n = len(keys)
    if n == 0 or scenario.demand <= 0:
        return _Recourse({}, {}, 0.0, 0.0)
    eco = [ue.routes[k] for k in keys]
    cair = [0.0] * len(supply) + [ue.routes[r.id].unit_cost + r.emergency_premium_uzs for r in emerg]
    pen = graph.penalty_uzs_per_pack
    # variables: q_k (k=0..n-1), R. maximise R + sum((pen - bl - cair_k) q_k) -> minimise negative
    c = np.array([-(pen - bl_var - ck) for ck in cair] + [-1.0])
    caps = [ue.caps.wholesale, ue.caps.retail] + ([ue.caps.contract] if ue.caps.contract is not None else [])
    a_ub, b_ub = [], []
    for cap in caps:  # R <= cap * sum q
        a_ub.append([-cap] * n + [1.0]); b_ub.append(0.0)
    a_ub.append([-(1.0 + markup) * e.legal_base for e in eco] + [1.0]); b_ub.append(0.0)
    a_ub.append([1.0] * n + [0.0]); b_ub.append(scenario.demand)  # sum q <= D
    ub = list(supply.values()) + [float(r.emergency_cap_packs) for r in emerg]
    res = linprog(c, A_ub=np.array(a_ub), b_ub=np.array(b_ub),
                  bounds=[(0.0, u) for u in ub] + [(0.0, None)], method="highs")
    if res.status != 0:
        raise TwinError(f"recourse LP failed in scenario {scenario.id}: {res.message}")
    q = np.maximum(res.x[:n], 0.0)
    sold = {k: float(v) for k, v in zip(keys, q) if v > 1e-9}
    emergency = {r.id: sold[r.id] for r in emerg if r.id in sold}
    em_cost = sum(sold[r.id] * (ue.routes[r.id].unit_cost + r.emergency_premium_uzs) for r in emerg if r.id in sold)
    return _Recourse(sold, emergency, float(res.x[n]), em_cost)


def evaluate_plan(graph: CalculationGraph, plan: Plan, scenario: Scenario, rules: Rules,
                  *, allow_emergency: bool = True) -> ScenarioOutcome:
    """Operational result of a plan in one scenario with optimal second-stage recourse."""
    require_calculation_graph(graph)
    ue = unit_economics(graph, scenario, rules)
    bl_var, bl_fixed = below_line(graph)
    rec = _solve_recourse(graph, plan, scenario, ue, bl_var, rules.max_markup, allow_emergency)
    sold = sum(rec.sold_by_route.values())
    shortfall = max(scenario.demand - sold, 0.0)
    revenue = rec.revenue if sold > 0 else 0.0
    price = revenue / sold if sold > 1e-9 else 0.0
    if sold > 1e-9:  # price from the cap formula (never above any limit, regardless of LP round-off)
        price = min(price, ue.caps.wholesale, ue.caps.retail, *(([ue.caps.contract]) if ue.caps.contract else []))
        revenue = price * sold
    profit = (revenue - purchase_cost(graph, plan, ue, scenario) - plan_fixed_cost(graph, plan)
              - rec.emergency_cost - graph.penalty_uzs_per_pack * shortfall - bl_var * sold - bl_fixed)
    return ScenarioOutcome(
        scenario_id=scenario.id, sold=sold, shortfall=shortfall, emergency=rec.emergency,
        sold_by_route=rec.sold_by_route, price_uzs=price, revenue_uzs=revenue, profit_uzs=profit,
        binding=_binding(ue, rec, price, rules),
    )


def _binding(ue: UnitEconomics, rec: _Recourse, price: float, rules: Rules) -> str:
    if price <= 0:
        return "none"
    pool = sum(rec.sold_by_route.values())
    markup_cap = (1.0 + rules.max_markup) * sum(ue.routes[k].legal_base * v for k, v in rec.sold_by_route.items()) / pool
    name, cap = ue.caps.binding_cap()
    if markup_cap <= cap * (1.0 + 1e-9):
        return "markup15"
    return name


def evaluate_plan_set(graph: CalculationGraph, plan: Plan, scenarios: Sequence[Scenario], rules: Rules,
                      *, allow_emergency: bool = True) -> list[ScenarioOutcome]:
    return [evaluate_plan(graph, plan, s, rules, allow_emergency=allow_emergency) for s in scenarios]
