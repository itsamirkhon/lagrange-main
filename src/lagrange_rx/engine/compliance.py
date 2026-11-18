"""Independent legality checker (DECISION_ENGINE.md section 5).

Deliberately self-contained: it re-implements P19 ceilings, P20 indexation, P22 cut, the 15% rule
on the legal base and the plan feasibility rules. It may import only the stdlib, the ontology,
the rules loader and the shared engine types (an AST test enforces this).

Tolerance for float quantities: relative 1e-6 plus absolute 1e-6. Integer pack counts of the
route limits are compared exactly.
"""
from __future__ import annotations

import math
from collections.abc import Iterable, Sequence
from typing import NamedTuple

from lagrange_rx.engine.types import CheckResult, Plan, Scenario, ScenarioOutcome
from lagrange_rx.engine._legacy_ontology import CalculationGraph, Route
from lagrange_rx.engine.rules import Rules

REL_TOL = 1e-6
ABS_TOL = 1e-6
MONTHS = 12
GROWTH_DECIMALS = 9
EVERY_SCENARIO = "*"

RULE_CEILING = "UZ-3735-P19-CEILING"
RULE_CONTRACT = "CONTRACT-PRICE"
RULE_MARKUP = "UZ-PF13-WHOLESALE-MARKUP"
RULE_UNIFORM = "UZ-INV-UNIFORM-WAREHOUSE-PRICE"
RULE_PHYSICAL = "PLAN-PHYSICAL"
RULE_LIMITS = "PLAN-ROUTE-LIMITS"


# ---- tolerance helpers -------------------------------------------------------------------------
def _within(observed: float, limit: float) -> bool:
    return observed <= limit * (1.0 + REL_TOL) + ABS_TOL


def _close(a: float, b: float) -> bool:
    return abs(a - b) <= REL_TOL * abs(b) + ABS_TOL


def _result(rule_id: str, sid: str, observed: float, limit: float, passed: bool, detail: str = "") -> CheckResult:
    return CheckResult(
        rule_id=rule_id, scenario_id=sid, observed=float(observed), limit=float(limit), passed=bool(passed), detail=detail
    )


# ---- P20 indexation and P22 cut --------------------------------------------------------------------
class _Pending(NamedTuple):
    eff: int
    growth: float
    fx: float


class _State(NamedTuple):
    base: float
    factor: float
    pending: _Pending | None


def _calendar_month(horizon_start: str, offset: int) -> int:
    return (int(horizon_start[5:7]) - 1 + offset) % 12 + 1


def _apply_pending(state: _State, m: int) -> _State:
    if state.pending is not None and m >= state.pending.eff:
        return _State(state.pending.fx, state.factor * (1.0 + state.pending.growth), None)
    return state


def _react(state: _State, m: int, fx: float, on_index_date: bool, lag_months: int, rules: Rules) -> _State:
    if state.pending is not None:
        return state
    growth = round(fx / state.base - 1.0, GROWTH_DECIMALS)
    if growth > rules.threshold_immediate:
        return _State(state.base, state.factor, _Pending(m + lag_months, growth, fx))
    if growth >= rules.threshold_scheduled and on_index_date:
        return _State(fx, state.factor * (1.0 + growth), None)
    return state


def _monthly_factors(scenario: Scenario, horizon_start: str, rules: Rules) -> list[float]:
    if len(scenario.ref_fx_path) != MONTHS:
        raise ValueError("ref_fx_path must have 12 monthly values")
    lag_months = math.ceil(rules.lag_days / 30)
    index_months = {m for (m, d) in rules.index_dates if d == 1}
    state = _State(scenario.ref_fx_start, 1.0, None)
    monthly: list[float] = []
    for m in range(MONTHS):
        state = _apply_pending(state, m)
        on_date = _calendar_month(horizon_start, m) in index_months
        state = _react(state, m, scenario.ref_fx_path[m], on_date, lag_months, rules)
        monthly.append(state.factor)
    if scenario.cut_month is not None:
        for m in range(scenario.cut_month - 1, MONTHS):
            monthly[m] *= 1.0 - scenario.cut_depth
    return monthly


def published_ceilings(graph: CalculationGraph, scenario: Scenario, rules: Rules) -> tuple[float, float]:
    """(wholesale cap, retail-chain cap), both per pack excl. VAT, averaged over the horizon."""
    monthly = _monthly_factors(scenario, graph.horizon_start, rules)
    f = sum(monthly) / len(monthly)
    wholesale = graph.product.chun_uzs * f / (1.0 + rules.vat)
    retail = graph.product.chchn_uzs * f / (1.0 + rules.vat) / (1.0 + rules.retail_markup)
    return wholesale, retail


# ---- legal base and pooled 15% limit -------------------------------------------------------------------
def legal_base(route: Route, scenario: Scenario) -> float:
    """PKM-185 base: Kc x customs rate x (1+duty) + fees (+ Pr if confirmed). No freight, no hedge."""
    pr = route.pr_uzs if route.pr_confirmed and route.pr_uzs is not None else 0.0
    return route.kc * scenario.fx[route.currency] * (1.0 + route.duty_rate) + route.fees_uzs + pr


def _sold_pool(graph: CalculationGraph, scenario: Scenario, outcome: ScenarioOutcome) -> list[tuple[float, float]]:
    """(legal base, packs actually sold) for every known route that sold something."""
    known = {r.id: r for r in graph.routes}
    return [
        (legal_base(known[rid], scenario), packs)
        for rid, packs in outcome.sold_by_route.items()
        if rid in known and packs > 0
    ]


def _markup_limit(pool: list[tuple[float, float]], rules: Rules) -> float | None:
    total = sum(packs for _, packs in pool)
    if total <= 0:
        return None
    return (1.0 + rules.max_markup) * sum(base * packs for base, packs in pool) / total


# ---- price checks -------------------------------------------------------------------------------------------
def _price_result(rule_id: str, outcome: ScenarioOutcome, limit: float, detail: str = "") -> CheckResult:
    ok = outcome.sold == 0 or _within(outcome.price_uzs, limit)
    return _result(rule_id, outcome.scenario_id, outcome.price_uzs, limit, ok, detail)


def _ceiling_checks(graph: CalculationGraph, scenario: Scenario, outcome: ScenarioOutcome, rules: Rules) -> list[CheckResult]:
    wholesale, retail = published_ceilings(graph, scenario, rules)
    return [
        _price_result(RULE_CEILING, outcome, wholesale, "wholesale"),
        _price_result(RULE_CEILING, outcome, retail, "retail"),
    ]


def _contract_checks(graph: CalculationGraph, outcome: ScenarioOutcome) -> list[CheckResult]:
    contract = graph.product.contract_price_uzs
    return [] if not contract else [_price_result(RULE_CONTRACT, outcome, contract)]


def _markup_check(graph: CalculationGraph, scenario: Scenario, outcome: ScenarioOutcome, rules: Rules) -> CheckResult:
    limit = _markup_limit(_sold_pool(graph, scenario, outcome), rules)
    if limit is None:
        return _result(RULE_MARKUP, outcome.scenario_id, outcome.price_uzs, 0.0, outcome.sold == 0, "empty sold pool")
    return _price_result(RULE_MARKUP, outcome, limit)


def _uniform_check(outcome: ScenarioOutcome) -> CheckResult:
    implied = outcome.price_uzs * outcome.sold
    return _result(RULE_UNIFORM, outcome.scenario_id, outcome.revenue_uzs, implied, _close(outcome.revenue_uzs, implied))


# ---- physical feasibility ---------------------------------------------------------------------------------------
def _emergency_limit(route: Route | None, scenario: Scenario) -> float:
    valid = route is not None and route.is_eligible and route.emergency and scenario.available(route.id)
    return float(route.emergency_cap_packs) if valid else 0.0


def _supply(graph: CalculationGraph, plan: Plan, scenario: Scenario, outcome: ScenarioOutcome) -> float:
    known = {r.id for r in graph.routes}
    ordered = sum(n for rid, n in plan.packs_by_route().items() if rid in known and scenario.available(rid))
    return ordered + sum(outcome.emergency.values())


def _emergency_checks(graph: CalculationGraph, scenario: Scenario, outcome: ScenarioOutcome) -> list[CheckResult]:
    known = {r.id: r for r in graph.routes}
    return [
        _result(
            RULE_PHYSICAL, scenario.id, packs, _emergency_limit(known.get(rid), scenario),
            packs >= -ABS_TOL and _within(packs, _emergency_limit(known.get(rid), scenario)), f"emergency:{rid}",
        )
        for rid, packs in outcome.emergency.items()
    ]


def _sold_route_limit(route: Route | None, rid: str, plan: Plan, scenario: Scenario, outcome: ScenarioOutcome) -> float:
    if route is None:
        return 0.0
    if route.emergency:
        return float(outcome.emergency.get(rid, 0.0))
    return float(plan.packs_by_route().get(rid, 0)) if scenario.available(rid) else 0.0


def _sold_route_checks(
    graph: CalculationGraph, plan: Plan, scenario: Scenario, outcome: ScenarioOutcome
) -> list[CheckResult]:
    known = {r.id: r for r in graph.routes}
    checks = []
    for rid, packs in outcome.sold_by_route.items():
        limit = _sold_route_limit(known.get(rid), rid, plan, scenario, outcome)
        checks.append(_result(RULE_PHYSICAL, scenario.id, packs, limit, _within(packs, limit), f"sold_route:{rid}"))
    total = sum(outcome.sold_by_route.values())
    checks.append(_result(RULE_PHYSICAL, scenario.id, total, outcome.sold, _close(total, outcome.sold), "sold_sum"))
    return checks


def _physical_checks(
    graph: CalculationGraph, plan: Plan, scenario: Scenario, outcome: ScenarioOutcome
) -> list[CheckResult]:
    sid = scenario.id
    supply = _supply(graph, plan, scenario, outcome)
    smallest = min([outcome.sold, outcome.shortfall, *outcome.sold_by_route.values()])
    balance = outcome.sold + outcome.shortfall
    return [
        _result(RULE_PHYSICAL, sid, outcome.sold, supply, _within(outcome.sold, supply), "supply"),
        _result(RULE_PHYSICAL, sid, balance, scenario.demand, _close(balance, scenario.demand), "balance"),
        _result(RULE_PHYSICAL, sid, smallest, 0.0, smallest >= -ABS_TOL, "nonneg"),
        *_emergency_checks(graph, scenario, outcome),
        *_sold_route_checks(graph, plan, scenario, outcome),
    ]


# ---- route limits (scenario independent) ------------------------------------------------------------------------
def _route_checks(rid: str, route: Route | None, packs: int, shipments: int) -> list[CheckResult]:
    def res(detail: str, observed: float, limit: float, passed: bool) -> CheckResult:
        return _result(RULE_LIMITS, EVERY_SCENARIO, observed, limit, passed, f"{rid}:{detail}")

    if route is None:
        return [res("exists", 0, 1, False)]
    usable = route.is_eligible and not route.emergency
    return [
        res("eligible", float(usable), 1.0, usable),
        res("lot", packs % route.lot_size, 0, packs % route.lot_size == 0),
        res("min_batch", route.min_batch * shipments, packs, route.min_batch * shipments <= packs),
        res("max_batch", packs, route.max_batch * shipments, packs <= route.max_batch * shipments),
        res("capacity", packs, route.capacity_packs, packs <= route.capacity_packs),
        res("max_shipments", shipments, route.max_shipments, shipments <= route.max_shipments),
    ]


def _route_limit_checks(graph: CalculationGraph, plan: Plan) -> list[CheckResult]:
    known = {r.id: r for r in graph.routes}
    packs = plan.packs_by_route()
    shipments: dict[str, int] = {}
    for line in plan.lines:
        shipments[line.route_id] = shipments.get(line.route_id, 0) + line.shipments
    return [res for rid in packs for res in _route_checks(rid, known.get(rid), packs[rid], shipments[rid])]


# ---- entry points -----------------------------------------------------------------------------------------------------
def _scenario_checks(
    graph: CalculationGraph, plan: Plan, scenario: Scenario, outcome: ScenarioOutcome, rules: Rules
) -> list[CheckResult]:
    return [
        *_ceiling_checks(graph, scenario, outcome, rules),
        *_contract_checks(graph, outcome),
        _markup_check(graph, scenario, outcome, rules),
        _uniform_check(outcome),
        *_physical_checks(graph, plan, scenario, outcome),
    ]


def _missing_outcome(graph: CalculationGraph, scenario: Scenario) -> list[CheckResult]:
    rule_ids = [RULE_CEILING, RULE_MARKUP, RULE_UNIFORM, RULE_PHYSICAL]
    if graph.product.contract_price_uzs:
        rule_ids.append(RULE_CONTRACT)
    return [_result(rid, scenario.id, 0.0, 0.0, False, "missing outcome") for rid in rule_ids]


def check_plan(
    graph: CalculationGraph,
    plan: Plan,
    scenarios: Sequence[Scenario],
    outcomes: Sequence[ScenarioOutcome],
    rules: Rules,
) -> list[CheckResult]:
    by_id = {o.scenario_id: o for o in outcomes}
    results = _route_limit_checks(graph, plan)
    for scenario in scenarios:
        outcome = by_id.get(scenario.id)
        if outcome is None:
            results += _missing_outcome(graph, scenario)
        else:
            results += _scenario_checks(graph, plan, scenario, outcome, rules)
    return results


def is_legal(results: Iterable[CheckResult]) -> bool:
    return all(r.passed for r in results)

