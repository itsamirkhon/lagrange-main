"""Boundary suite for the independent legality checker.

Every case takes the module under test `c` and a `Rules` object. All expected limits are
hand-computed literals (never derived from compliance.py nor from Rules), so the same suite
can be run against mutated sources of compliance.py and against mutated Rules objects.
"""
from __future__ import annotations

from collections.abc import Callable

from lagrange_rx.engine.types import Plan, PlanLine, Scenario, ScenarioOutcome
from lagrange_rx.engine.inputs import CalculationGraph, EngineGraph, DemandSpec, ProductSpec, RouteSpec, SupplierSpec

FX = {"USD": 12000.0, "EUR": 13000.0}
PRICE_OK = 100_000.0  # below every limit of the default fixture


def route(rid: str = "R1", **kw) -> RouteSpec:
    base = dict(
        id=rid, supplier_id="S1", name=rid, mode="sea", currency="USD", kc=10.0, duty_rate=0.1,
        fees_uzs=1000.0, pr_uzs=500.0, pr_confirmed=True, freight_uzs=800.0, lot_size=100,
        min_batch=100, max_batch=5000, capacity_packs=10_000, max_shipments=4,
    )
    base.update(kw)
    return RouteSpec(**base)


R1 = route()  # legal base 10*12000*1.1 + 1000 + 500 = 133500
R2 = route("R2", currency="EUR", kc=20.0, duty_rate=0.05, fees_uzs=2000.0, pr_uzs=None, pr_confirmed=False)  # 275000
E1 = route(
    "E1", kc=12.0, pr_uzs=None, pr_confirmed=False, emergency=True, emergency_cap_packs=300,
    lot_size=1, min_batch=0, max_batch=1000, capacity_packs=0, max_shipments=0,
)  # 12*12000*1.1 + 1000 = 159400


def graph(routes=(R1,), chun=1_000_000.0, chchn=2_000_000.0, contract=None, horizon="2026-11") -> CalculationGraph:
    return EngineGraph(
        client_id="c",
        product=ProductSpec(id="P", name="p", chun_uzs=chun, chchn_uzs=chchn, contract_price_uzs=contract),
        suppliers=(SupplierSpec(id="S1", name="s", reliability=0.9),),
        routes=tuple(routes),
        demand=DemandSpec(p10=800, p50=1000, p90=1200),
        nominal_fx=FX,
        horizon_start=horizon,
    ).for_calculation()


def scen(sid: str = "s1", **kw) -> Scenario:
    base = dict(id=sid, name=sid, fx=FX, ref_fx_start=100.0, ref_fx_path=(100.0,) * 12, demand=1000.0)
    base.update(kw)
    return Scenario(**base)


def make_plan(lines=(("R1", 1000, 1),), hedge: float = 0.5) -> Plan:
    return Plan(
        plan_id="p", sku_id="P", horizon="2026-11", fx_hedge_share=hedge,
        lines=tuple(PlanLine(route_id=r, packs=n, shipments=s) for r, n, s in lines),
    )


def default_sbr(plan: Plan, s: Scenario, emergency, sold: float) -> dict[str, float]:
    """Sales spread over the available ordered packs (plus emergency packs) proportionally."""
    weights = {r: n for r, n in plan.packs_by_route().items() if s.available(r) and n > 0}
    for r, n in (emergency or {}).items():
        weights[r] = weights.get(r, 0) + n
    total = sum(weights.values())
    return {r: sold * w / total for r, w in weights.items() if w > 0} if total > 0 else {}


def outcome(price: float, s: Scenario, sold: float, shortfall=None, emergency=None, revenue=None, sbr=None) -> ScenarioOutcome:
    return ScenarioOutcome(
        scenario_id=s.id, sold=sold, shortfall=s.demand - sold if shortfall is None else shortfall,
        emergency=emergency or {}, sold_by_route=sbr or {}, price_uzs=price, revenue_uzs=price * sold if revenue is None else revenue,
        profit_uzs=0.0,
    )


def run(c, rules, price, g=None, plan=None, scenario=None, sold=None, **kw):
    g, plan, s = g or graph(), plan or make_plan(), scenario or scen()
    sold = s.demand if sold is None else sold
    if "sbr" not in kw:
        kw["sbr"] = default_sbr(plan, s, kw.get("emergency"), sold)
    return c.check_plan(g, plan, [s], [outcome(price, s, sold, **kw)], rules)


def failed(results) -> set[str]:
    return {r.rule_id for r in results if not r.passed}


def failed_details(results, rule_id: str) -> set[str]:
    return {r.detail for r in results if r.rule_id == rule_id and not r.passed}


def close(a: float, b: float) -> bool:
    return abs(a - b) <= 1e-9 * max(1.0, abs(b))


def assert_boundary(c, rules, limit, rule_id, **kw):
    """price == limit and within tolerance passes; +0.1% fails exactly `rule_id`."""
    assert failed(run(c, rules, limit, **kw)) == set()
    assert failed(run(c, rules, limit * (1 + 5e-7), **kw)) == set()
    assert failed(run(c, rules, limit * 1.001, **kw)) == {rule_id}


MARKUP, CEIL, CONTRACT = "UZ-PF13-WHOLESALE-MARKUP", "UZ-3735-P19-CEILING", "CONTRACT-PRICE"
UNIFORM, PHYS, LIMITS = "UZ-INV-UNIFORM-WAREHOUSE-PRICE", "PLAN-PHYSICAL", "PLAN-ROUTE-LIMITS"


# ---- price limits -------------------------------------------------------------------------
def case_markup_boundary(c, rules):
    # freight 800 and hedge 0.5 are present in the fixture but excluded from the legal base
    assert_boundary(c, rules, 153_525.0, MARKUP)


def case_markup_uses_scenario_fx(c, rules):
    s = scen(fx={"USD": 12500.0, "EUR": 13000.0})  # base 10*12500*1.1+1500 = 139000 -> 159850
    assert_boundary(c, rules, 159_850.0, MARKUP, scenario=s)


def case_markup_pooled_average_is_pack_weighted(c, rules):
    g, p = graph((R1, R2)), make_plan((("R1", 600, 1), ("R2", 400, 1)))
    assert_boundary(c, rules, 218_615.0, MARKUP, g=g, plan=p)  # (133500*600+275000*400)/1000*1.15


def case_markup_ignores_unavailable_routes(c, rules):
    g, p = graph((R1, R2)), make_plan((("R1", 600, 1), ("R2", 400, 1)))
    s = scen(demand=600.0, route_available={"R2": False})
    assert_boundary(c, rules, 153_525.0, MARKUP, g=g, plan=p, scenario=s)


def case_markup_pool_includes_emergency(c, rules):
    g, p = graph((R1, E1)), make_plan((("R1", 700, 1),))
    # (133500*700 + 159400*300)/1000 = 141270 -> *1.15
    assert_boundary(c, rules, 162_460.5, MARKUP, g=g, plan=p, emergency={"E1": 300.0})


def case_markup_pool_is_sold_not_ordered(c, rules):
    g, p = graph((R1, R2)), make_plan((("R1", 600, 1), ("R2", 400, 1)))
    s = scen(demand=500.0)
    sbr = {"R1": 400.0, "R2": 100.0}  # (133500*400+275000*100)/500 = 161800 -> *1.15
    assert_boundary(c, rules, 186_070.0, MARKUP, g=g, plan=p, scenario=s, sbr=sbr)
    assert_boundary(c, rules, 153_525.0, MARKUP, g=g, plan=p, scenario=s, sbr={"R1": 500.0})


def case_markup_empty_pool_fails(c, rules):
    assert MARKUP in failed(run(c, rules, PRICE_OK, plan=make_plan(()), sold=10.0))
    assert MARKUP in failed(run(c, rules, PRICE_OK, sbr={}))


def case_wholesale_ceiling(c, rules):
    g = graph(chun=112_000.0)  # 112000/1.12 = 100000
    assert_boundary(c, rules, 100_000.0, CEIL, g=g)
    assert failed_details(run(c, rules, 100_100.0, g=g), CEIL) == {"wholesale"}


def case_retail_ceiling(c, rules):
    g = graph(chchn=134_400.0)  # 134400/1.12/1.2 = 100000
    assert_boundary(c, rules, 100_000.0, CEIL, g=g)
    assert failed_details(run(c, rules, 100_100.0, g=g), CEIL) == {"retail"}


def case_ceiling_after_cut(c, rules):
    g, s = graph(chun=112_000.0), scen(cut_month=1, cut_depth=0.37)
    assert_boundary(c, rules, 63_000.0, CEIL, g=g, scenario=s)


def case_contract_price(c, rules):
    assert_boundary(c, rules, 90_000.0, CONTRACT, g=graph(contract=90_000.0))


def case_no_contract_rule_without_contract(c, rules):
    assert CONTRACT not in {r.rule_id for r in run(c, rules, PRICE_OK)}


# ---- uniform price, zero sales, missing outcomes ----------------------------------------------
def case_uniform_price(c, rules):
    assert failed(run(c, rules, PRICE_OK, revenue=PRICE_OK * 1000 * (1 + 5e-7))) == set()
    assert failed(run(c, rules, PRICE_OK, revenue=PRICE_OK * 1000 * 1.001)) == {UNIFORM}
    assert failed(run(c, rules, PRICE_OK, revenue=PRICE_OK * 1000 * 0.999)) == {UNIFORM}


def case_no_sales_skips_price_checks(c, rules):
    res = run(c, rules, 9e12, sold=0.0)
    assert failed(res) == set() and {CEIL, MARKUP} <= {r.rule_id for r in res}


def case_missing_outcome_fails(c, rules):
    s = scen()
    res = c.check_plan(graph(), make_plan(), [s], [], rules)
    assert not c.is_legal(res) and {r.scenario_id for r in res if not r.passed} == {"s1"}
    assert {MARKUP, CEIL, UNIFORM, PHYS} <= failed(res)


# ---- physical feasibility ----------------------------------------------------------------------
def phys(c, rules, g=None, plan=None, scenario=None, sold=None, **kw) -> set[str]:
    return failed_details(run(c, rules, PRICE_OK, g, plan, scenario, sold, **kw), PHYS)


def case_physical_supply_boundary(c, rules):
    assert phys(c, rules) == set()
    assert phys(c, rules, scenario=scen(demand=1001.0)) == {"supply", "sold_route:R1"}
    assert phys(c, rules, scenario=scen(route_available={"R1": False})) == {"supply", "sold_sum"}


def case_physical_emergency(c, rules):
    g, p = graph((R1, E1)), make_plan((("R1", 700, 1),))
    assert phys(c, rules, g, p, emergency={"E1": 300.0}) == set()
    assert phys(c, rules, g, p, emergency={"E1": 301.0}) == {"emergency:E1"}
    assert phys(c, rules, g, p, emergency={"R1": 300.0}, sold=700.0, sbr={"R1": 700.0}) == {"emergency:R1"}
    assert phys(c, rules, g, p, emergency={"Z": 300.0}, sold=700.0, sbr={"R1": 700.0}) == {"emergency:Z"}
    s = scen(demand=701.0, route_available={"E1": False})
    assert phys(c, rules, g, p, s, emergency={"E1": 1.0}) == {"emergency:E1"}


def case_physical_sold_by_route(c, rules):
    g, p = graph((R1, R2, E1)), make_plan((("R1", 700, 1),))
    em = {"E1": 300.0}
    assert phys(c, rules, g, p, emergency=em, sbr={"R1": 700.0, "E1": 300.0}) == set()
    assert phys(c, rules, g, p, emergency=em, sbr={"R1": 701.0, "E1": 299.0}) == {"sold_route:R1"}
    assert phys(c, rules, g, p, emergency=em, sbr={"R1": 699.0, "E1": 301.0}) == {"sold_route:E1"}
    assert phys(c, rules, g, p, emergency=em, sbr={"R1": 600.0, "E1": 300.0}) == {"sold_sum"}
    assert phys(c, rules, g, p, emergency=em, sbr={}) == {"sold_sum"}
    assert phys(c, rules, g, p, emergency=em, sbr={"R1": 700.0, "R2": 300.0}) == {"sold_route:R2"}
    p2, s2 = make_plan((("R1", 600, 1), ("R2", 400, 1))), scen(demand=600.0, route_available={"R2": False})
    assert phys(c, rules, g, p2, s2, sbr={"R1": 500.0, "R2": 100.0}) == {"sold_route:R2"}


def case_physical_ineligible_emergency_route(c, rules):
    g, p = graph((R1, route("E1", emergency=True, is_eligible=False, emergency_cap_packs=300))), make_plan()
    assert phys(c, rules, g, p, scen(demand=1100.0), emergency={"E1": 100.0}) == {"emergency:E1"}


def case_physical_balance_and_signs(c, rules):
    assert phys(c, rules, shortfall=5.0) == {"balance"}
    assert phys(c, rules, sold=-1.0, shortfall=1001.0) == {"nonneg"}


# ---- route limits -------------------------------------------------------------------------------
def limits(c, rules, g, lines) -> set[str]:
    sold = float(sum(n for _, n, _ in lines))
    return failed_details(run(c, rules, PRICE_OK, g, make_plan(lines), scen(demand=sold)), LIMITS)


def case_route_limits_lot(c, rules):
    assert limits(c, rules, graph(), (("R1", 1000, 1),)) == set()
    assert limits(c, rules, graph(), (("R1", 1050, 1),)) == {"R1:lot"}
    assert limits(c, rules, graph(), (("R1", 150, 1), ("R1", 50, 1))) == set()  # aggregated per route


def case_route_limits_batch(c, rules):
    assert limits(c, rules, graph(), (("R1", 200, 2),)) == set()
    assert limits(c, rules, graph(), (("R1", 100, 2),)) == {"R1:min_batch"}
    assert limits(c, rules, graph(), (("R1", 5000, 1),)) == set()
    assert limits(c, rules, graph(), (("R1", 5100, 1),)) == {"R1:max_batch"}
    assert limits(c, rules, graph(), (("R1", 5100, 2),)) == set()


def case_route_limits_capacity_and_shipments(c, rules):
    assert limits(c, rules, graph(), (("R1", 10_000, 4),)) == set()
    assert limits(c, rules, graph(), (("R1", 10_100, 4),)) == {"R1:capacity"}
    assert limits(c, rules, graph(), (("R1", 1000, 5),)) == {"R1:max_shipments"}


def case_route_limits_eligibility(c, rules):
    assert limits(c, rules, graph((route(is_eligible=False),)), (("R1", 1000, 1),)) == {"R1:eligible"}
    assert "E1:eligible" in limits(c, rules, graph((R1, E1)), (("E1", 100, 1),))
    assert limits(c, rules, graph(), (("X", 100, 1),)) == {"X:exists"}


# ---- legal base ------------------------------------------------------------------------------------
def case_legal_base_components(c, rules):
    s = scen()
    assert close(c.legal_base(R1, s), 133_500.0)
    assert close(c.legal_base(route(freight_uzs=99_999.0), s), 133_500.0)  # freight excluded
    assert close(c.legal_base(route(pr_confirmed=False), s), 133_000.0)  # unconfirmed Pr ignored
    assert close(c.legal_base(route(pr_uzs=None), s), 133_000.0)
    assert close(c.legal_base(route(duty_rate=0.0, fees_uzs=0.0, pr_uzs=None), s), 120_000.0)
    assert close(c.legal_base(R1, scen(fx={"USD": 12500.0})), 139_000.0)


# ---- indexation (P20) and cut (P22) --------------------------------------------------------------------
def ceil_w(c, rules, path, horizon="2026-11", **kw) -> float:
    """Wholesale ceiling for chun=112000 -> 100000 * annual factor."""
    g = graph(chun=112_000.0, chchn=134_400.0, horizon=horizon)
    w, r = c.published_ceilings(g, scen(ref_fx_path=tuple(path), **kw), rules)
    assert close(w, r)
    return w / 100_000.0


def const(v: float) -> list[float]:
    return [v] * 12


def case_indexation_below_scheduled_threshold(c, rules):
    assert close(ceil_w(c, rules, const(102.9)), 1.0)
    assert close(ceil_w(c, rules, const(90.0)), 1.0)


def case_indexation_scheduled_on_january(c, rules):
    assert close(ceil_w(c, rules, const(103.0)), (2 + 10 * 1.03) / 12)  # 3.0% applies on Jan (offset 2)
    assert close(ceil_w(c, rules, const(105.0)), (2 + 10 * 1.05) / 12)  # 5.0% is NOT immediate
    assert close(ceil_w(c, rules, const(103.0), horizon="2026-12"), (1 + 11 * 1.03) / 12)
    assert close(ceil_w(c, rules, const(103.0), horizon="2026-07"), 1.03)  # July is an index date


def case_indexation_immediate_with_lag(c, rules):
    assert close(ceil_w(c, rules, const(105.1)), (1 + 11 * 1.051) / 12)  # base is re-set after it applies


def case_indexation_chained_steps(c, rules):
    path = [103.0] * 8 + [106.09] * 4
    assert close(ceil_w(c, rules, path), (2 + 6 * 1.03 + 4 * 1.0609) / 12)


def case_cut_applies_from_month(c, rules):
    assert close(ceil_w(c, rules, const(100.0), cut_month=7, cut_depth=0.37), (6 + 6 * 0.63) / 12)
    assert close(ceil_w(c, rules, const(100.0), cut_month=1, cut_depth=0.1), 0.9)
    assert close(ceil_w(c, rules, const(100.0), cut_month=12, cut_depth=0.37), (11 + 0.63) / 12)


def case_retail_over_wholesale_ceiling_ratio(c, rules):
    g = graph(chun=112_000.0, chchn=268_800.0)  # wholesale cap 100000, retail-chain cap 200000
    w, r = c.published_ceilings(g, scen(), rules)
    assert close(w, 100_000.0) and close(r, 200_000.0)


CASES: list[Callable] = [
    v for k, v in sorted(globals().items()) if k.startswith("case_") and callable(v)
]


def run_all(c, rules) -> list[str]:
    """Names of the cases that fail against module `c` (an exception counts as a failure)."""
    bad = []
    for case in CASES:
        try:
            case(c, rules)
        except Exception:  # noqa: BLE001 - any breakage means the case caught the module
            bad.append(case.__name__)
    return bad
