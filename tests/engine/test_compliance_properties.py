"""Property tests: constructed-maximal prices always pass, 0.1% above the binding cap always fails."""
from __future__ import annotations

from hypothesis import HealthCheck, assume, given, settings
from hypothesis import strategies as st

from lagrange_rx.engine import compliance as c
from lagrange_rx.engine.types import ScenarioOutcome
from lagrange_rx.rules import load_rules
from tests.strategies import calc_graphs, legal_plans, scenarios_for

RULES = load_rules()
PRICE_RULES = {"UZ-3735-P19-CEILING", "UZ-PF13-WHOLESALE-MARKUP", "CONTRACT-PRICE"}
CFG = settings(max_examples=200, deadline=None, suppress_health_check=list(HealthCheck))


@st.composite
def emergency_buys(draw, graph, scenario) -> dict[str, float]:
    out = {}
    for r in graph.emergency_routes():
        if scenario.available(r.id) and draw(st.booleans()):
            out[r.id] = float(draw(st.integers(0, r.emergency_cap_packs)))
    return out


def sold_split(graph, plan, scenario, emergency, fraction) -> dict[str, float]:
    """Sell `fraction` of every available ordered / emergency pack (so sold_by_route <= bought)."""
    avail = {r: n for r, n in plan.packs_by_route().items() if scenario.available(r)}
    sold = {r: n * fraction for r, n in avail.items()}
    for r, n in emergency.items():
        sold[r] = sold.get(r, 0.0) + n * fraction
    return {r: n for r, n in sold.items() if n > 0}


def max_legal_price(graph, scenario, sbr) -> float:
    """Independent oracle: min of ceilings, contract and 15% over the sold-pooled legal base."""
    wholesale, retail = c.published_ceilings(graph, scenario, RULES)
    routes = {r.id: r for r in graph.routes}
    total = sum(sbr.values())
    pooled = sum(c.legal_base(routes[r], scenario) * n for r, n in sbr.items()) / total
    caps = [wholesale, retail, (1 + RULES.max_markup) * pooled]
    if graph.product.contract_price_uzs:
        caps.append(graph.product.contract_price_uzs)
    return min(caps)


@st.composite
def worlds(draw, scale: float):
    graph = draw(calc_graphs())
    scenarios, plan = draw(scenarios_for(graph)), draw(legal_plans(graph))
    outcomes = []
    for s in scenarios:
        em = draw(emergency_buys(graph, s))
        sbr = sold_split(graph, plan, s, em, draw(st.floats(0.0, 1.0)))
        sold = sum(sbr.values())
        # demand is exactly sold + shortfall: align the scenario-independent demand via a copy
        s2 = s.model_copy(update={"demand": sold + draw(st.floats(0.0, 1000.0))})
        price = max_legal_price(graph, s2, sbr) * scale if sold > 0 else 0.0
        outcomes.append((s2, ScenarioOutcome(
            scenario_id=s.id, sold=sold, shortfall=s2.demand - sold, emergency=em, sold_by_route=sbr,
            price_uzs=price, revenue_uzs=price * sold, profit_uzs=0.0,
        )))
    return graph, plan, [s for s, _ in outcomes], [o for _, o in outcomes]


@CFG
@given(worlds(1.0))
def test_maximal_legal_price_always_passes(world):
    graph, plan, scenarios, outcomes = world
    results = c.check_plan(graph, plan, scenarios, outcomes, RULES)
    assert c.is_legal(results), [r for r in results if not r.passed][:3]


@CFG
@given(worlds(1.001))
def test_price_above_binding_cap_always_fails(world):
    graph, plan, scenarios, outcomes = world
    selling = [o for o in outcomes if o.sold > 0]
    assume(selling)
    results = c.check_plan(graph, plan, scenarios, outcomes, RULES)
    for o in selling:
        failing = {r.rule_id for r in results if r.scenario_id == o.scenario_id and not r.passed}
        assert failing & PRICE_RULES, o.scenario_id
    assert not c.is_legal(results)


@CFG
@given(st.data())
def test_published_ceilings_without_drivers_equal_registry(data):
    graph = data.draw(calc_graphs())
    s = data.draw(scenarios_for(graph))[0]
    flat = s.model_copy(update={"ref_fx_path": (s.ref_fx_start,) * 12, "cut_month": None, "cut_depth": 0.0})
    w, r = c.published_ceilings(graph, flat, RULES)
    assert abs(w - graph.product.chun_uzs / 1.12) < 1e-6 * w
    assert abs(r - graph.product.chchn_uzs / 1.12 / 1.2) < 1e-6 * r
