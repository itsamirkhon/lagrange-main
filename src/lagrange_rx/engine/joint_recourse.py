"""Second-stage scenario recourse that respects the *portfolio-wide* service floor.

The first-stage MILP constrains expected sales over its scenario set. A set of
independent profit-maximising recourse LPs can choose not to sell, even when the
first-stage procurement plan has feasible 90% coverage. This evaluator solves
one coupled second-stage LP with the same risk objective and a shared weighted
service constraint. It is independent of the MILP's q variables and checks the
physical plan selected by the MILP rather than silently declaring it invalid.

This is an internal business policy, NOT a legal compliance guarantee.
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import numpy as np
from scipy.optimize import linprog
from scipy.sparse import coo_matrix

from lagrange_rx.engine.twin import (
    TwinError, _Recourse, _binding, below_line, evaluate_plan_set,
    plan_fixed_cost, purchase_cost, unit_economics,
)
from lagrange_rx.engine.types import ScenarioOutcome


def evaluate_service_recourse(graph, plan, scenarios: Sequence, rules, *,
                              service_level: float | None,
                              risk_mode: str = "expected", alpha: float = 0.95,
                              lam: float = 0.5,
                              allow_emergency: bool = True) -> list[ScenarioOutcome]:
    """Compute admissible second-stage outcomes for a fixed procurement plan.

    Scales: sales q = thousands of packs; revenue/profit = millions of UZS.
    Service policy is expected (weight-normalized) fill, not per-scenario fill.
    The LP reproduces the two-stage model's risk objective on a fixed plan.
    """
    if service_level is None:
        return evaluate_plan_set(graph, plan, scenarios, rules, allow_emergency=allow_emergency)
    if not (0 < service_level <= 1) or not math.isfinite(service_level):
        raise ValueError("service_level must be finite and in (0, 1]")
    if risk_mode not in ("expected", "cvar", "worst_case"):
        raise ValueError("unsupported risk mode")
    if not 0 < alpha < 1 or not 0 <= lam <= 1:
        raise ValueError("invalid CVaR policy")
    if not scenarios:
        raise ValueError("cannot verify service without scenarios")

    total_weight = sum(float(s.weight) for s in scenarios)
    if not math.isfinite(total_weight) or total_weight <= 0:
        raise ValueError("invalid scenario weights")
    weights = [float(s.weight) / total_weight for s in scenarios]
    weighted_demand = sum(w * s.demand / 1000.0 for w, s in zip(weights, scenarios))
    if weighted_demand <= 0:
        raise ValueError("cannot validate service for zero-demand scenarios")

    cost: list[float] = []
    bounds: list[tuple[float | None, float | None]] = []
    ub_i: list[int] = []
    ub_j: list[int] = []
    ub_v: list[float] = []
    ub_rhs: list[float] = []
    eq_i: list[int] = []
    eq_j: list[int] = []
    eq_v: list[float] = []
    eq_rhs: list[float] = []

    def variable(lo=None, hi=None, objective=0.0):
        index = len(cost)
        bounds.append((lo, hi))
        cost.append(float(objective))
        return index

    def row(entries, rhs, *, equality=False):
        i = len(eq_rhs) if equality else len(ub_rhs)
        rows, cols, vals = (eq_i, eq_j, eq_v) if equality else (ub_i, ub_j, ub_v)
        for j, v in entries.items():
            if v:
                rows.append(i)
                cols.append(j)
                vals.append(float(v))
        (eq_rhs if equality else ub_rhs).append(float(rhs))

    regular = plan.packs_by_route()
    fixed_cost = plan_fixed_cost(graph, plan)
    below_variable, below_fixed = below_line(graph)
    scenario_columns = []
    service_row = {}
    profit_columns = []
    for s, w in zip(scenarios, weights):
        ue = unit_economics(graph, s, rules)
        sells = []
        # Do not treat an unavailable route as delivered inventory.
        for rid, packs in sorted(regular.items()):
            if packs <= 0 or not s.available(rid):
                continue
            sells.append((rid, variable(0.0, packs / 1000.0), False, 0.0))
        if allow_emergency:
            for r in sorted(graph.emergency_routes(), key=lambda r: r.id):
                if s.available(r.id) and r.emergency_cap_packs > 0:
                    premium = ue.routes[r.id].unit_cost + r.emergency_premium_uzs
                    sells.append((r.id, variable(0.0, r.emergency_cap_packs / 1000.0), True, premium))
        qcols = [j for _, j, _, _ in sells]
        revenue = variable(0.0, None)
        profit = variable(None, None)
        profit_columns.append(profit)
        scenario_columns.append((s, w, ue, sells, revenue, profit))
        # Market cannot consume more than scenario demand.
        row({j: 1.0 for j in qcols}, s.demand / 1000.0)
        caps = [ue.caps.wholesale, ue.caps.retail]
        if ue.caps.contract is not None:
            caps.append(ue.caps.contract)
        for cap in caps:
            row({revenue: 1.0, **{j: -cap / 1000.0 for j in qcols}}, 0.0)
        # Common wholesale revenue bounded by route-weighted legal markup.
        if graph.apply_wholesale_markup_rule:
            row({revenue: 1.0, **{j: -(1.0 + rules.max_markup) * ue.routes[rid].legal_base / 1000.0
                                 for rid, j, _, _ in sells}}, 0.0)
        # Exact profit equation, including costs on all purchased packs.
        fixed_m = (-purchase_cost(graph, plan, ue, s) - fixed_cost
                   - graph.penalty_uzs_per_pack * s.demand - below_fixed) / 1e6
        profit_eq = {profit: 1.0, revenue: -1.0}
        for _, j, _, emergency_unit_cost in sells:
            marginal = (graph.penalty_uzs_per_pack - below_variable - emergency_unit_cost) / 1000.0
            profit_eq[j] = -marginal
            service_row[j] = service_row.get(j, 0.0) - w
        row(profit_eq, fixed_m, equality=True)

    # Portfolio-wide mean of physical sold packs is constrained, exactly as in the MILP.
    row(service_row, -service_level * weighted_demand)
    if risk_mode == "expected":
        for (_, w, _, _, _, p) in scenario_columns:
            cost[p] -= w
    elif risk_mode == "cvar":
        eta = variable(None, None, objective=-lam)
        for (_, w, _, _, _, p) in scenario_columns:
            cost[p] -= (1.0 - lam) * w
            shortfall = variable(0.0, None, objective=lam * w / (1.0 - alpha))
            row({eta: 1.0, p: -1.0, shortfall: -1.0}, 0.0)
    else:
        worst = variable(None, None, objective=-1.0)
        for p in profit_columns:
            row({worst: 1.0, p: -1.0}, 0.0)

    n = len(cost)
    A = coo_matrix((ub_v, (ub_i, ub_j)), shape=(len(ub_rhs), n)).tocsr()
    Aeq = coo_matrix((eq_v, (eq_i, eq_j)), shape=(len(eq_rhs), n)).tocsr()
    solution = linprog(np.asarray(cost), A_ub=A, b_ub=np.asarray(ub_rhs),
                       A_eq=Aeq, b_eq=np.asarray(eq_rhs), bounds=bounds, method="highs")
    if not solution.success:
        raise TwinError(f"independent service-constrained recourse failed: {solution.message}")

    outcomes = []
    for s, w, ue, sells, revenue_col, profit_col in scenario_columns:
        sold_by_route = {rid: float(solution.x[j]) * 1000.0
                         for rid, j, _, _ in sells if solution.x[j] > 1e-10}
        sold = sum(sold_by_route.values())
        if sold > s.demand + 1e-3:
            raise TwinError("joint recourse exceeded scenario demand")
        emergency = {rid: sold_by_route[rid] for rid, j, is_emergency, _ in sells
                     if is_emergency and rid in sold_by_route}
        emergency_cost = sum(sold_by_route.get(rid, 0.0) * ec
                             for rid, j, is_emergency, ec in sells if is_emergency)
        revenue_uzs = float(solution.x[revenue_col]) * 1e6 if sold > 1e-8 else 0.0
        price = max(revenue_uzs / sold, 0.0) if sold > 1e-8 else 0.0
        if sold > 1e-8:
            caps = [ue.caps.wholesale, ue.caps.retail]
            if ue.caps.contract is not None:
                caps.append(ue.caps.contract)
            price = min(price, *caps)
            revenue_uzs = price * sold
        profit_uzs = (revenue_uzs - purchase_cost(graph, plan, ue, s) - fixed_cost
                      - emergency_cost - graph.penalty_uzs_per_pack * max(s.demand - sold, 0.0)
                      - below_variable * sold - below_fixed)
        # Catch incorrect units, variable mapping, and sign errors before returning money.
        expected_p = float(solution.x[profit_col]) * 1e6
        if abs(profit_uzs - expected_p) > 2.0 + 1e-7 * max(abs(profit_uzs), abs(expected_p)):
            raise TwinError(f"joint recourse profit equation mismatch for scenario {s.id}")
        rec = _Recourse(sold_by_route, emergency, revenue_uzs, emergency_cost)
        outcomes.append(ScenarioOutcome(scenario_id=s.id, sold=sold,
                                        shortfall=max(s.demand - sold, 0.0), emergency=emergency,
                                        sold_by_route=sold_by_route, price_uzs=price, revenue_uzs=revenue_uzs,
                                        profit_uzs=profit_uzs, binding=_binding(graph, ue, rec, price, rules)))
    return outcomes
