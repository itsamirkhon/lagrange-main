"""One-stage robust MILP. Linear, discrete shipments, one common wholesale contract price.

The price decision is shared across sourcing lots and across modeled scenarios.
This prevents arbitrary supplier-specific sales prices from inflating profit.

Units: x_i = lots of packs, n_i = shipments, y_i = binary activation,
       p = THOUSANDS of UZS/pack, z = MILLIONS of UZS (worst-case profit).
"""

from __future__ import annotations

import math

import numpy as np
from scipy.optimize import Bounds, LinearConstraint, milp

from .economics import MILLION, ASSUMPTIONS, baseline, evaluate, make_allocations, unit_economics
from .models import OptimizeResponse, PharmaCase


class SolverFailure(RuntimeError):
    """Unresolved computational failure; not equivalent to proven infeasibility."""


def optimize(case: PharmaCase, risk_aversion: float = 0.35, *, time_limit: float = 10.0) -> OptimizeResponse:
    if not 0 <= risk_aversion <= 1 or not math.isfinite(risk_aversion):
        raise ValueError("risk_aversion must be a finite number from 0 to 1")
    n = len(case.options)
    lot = case.lot_size_packs
    demand_lots = case.annual_demand_packs // lot
    x = lambda i: i
    shipments = lambda i: n + i
    active = lambda i: 2 * n + i
    price = 3 * n
    worst = 3 * n + 1
    var_count = 3 * n + 2

    upper = np.full(var_count, np.inf)
    lower = np.zeros(var_count)
    integrality = np.zeros(var_count, dtype=int)
    lower[worst] = -np.inf
    upper[price] = case.assumed_max_contract_wholesale_uzs / 1000

    for i, o in enumerate(case.options):
        integrality[[x(i), shipments(i), active(i)]] = 1
        upper[x(i)] = o.capacity_packs // lot if o.eligible else 0
        upper[shipments(i)] = o.max_shipments if o.eligible else 0
        upper[active(i)] = 1 if o.eligible else 0

    constraints: list[np.ndarray] = []
    lb: list[float] = []
    ub: list[float] = []

    def add(items: dict[int, float], lower_bound: float = -np.inf, upper_bound: float = np.inf):
        row = np.zeros(var_count, dtype=float)
        for index, coeff in items.items():
            row[index] = coeff
        constraints.append(row)
        lb.append(lower_bound)
        ub.append(upper_bound)

    # Fulfill all annual demand exactly; this model does not silently accept shortages.
    add({x(i): 1 for i in range(n)}, demand_lots, demand_lots)

    for i, option in enumerate(case.options):
        # A used route must be activated, and every activated route has shipments.
        add({x(i): 1, active(i): -option.capacity_packs // lot}, upper_bound=0)
        add({shipments(i): 1, active(i): -option.max_shipments}, upper_bound=0)
        add({active(i): 1, shipments(i): -1}, upper_bound=0)
        # Truck / container lower and upper shipment sizes.
        add({x(i): 1, shipments(i): -(option.min_shipment_packs // lot)}, lower_bound=0)
        add({x(i): 1, shipments(i): -(option.max_shipment_packs // lot)}, upper_bound=0)

    # The signed price is common to all supply routes and all stress scenarios.
    # If a route is not used, its markup constraint is deactivated by a provably
    # sufficient M: the contract maximum, because the markup ceiling is nonnegative.
    big_m = case.assumed_max_contract_wholesale_uzs / 1000
    capping_scenarios = []
    for s in case.scenarios:
        cap_thousands = case.assumed_registered_wholesale_cap_uzs * (
            1 + s.wholesale_ceiling_change_pct / 100) / 1000
        add({price: 1}, upper_bound=cap_thousands)
        for i, o in enumerate(case.options):
            economics = unit_economics(case, o, s)
            max_markup_thousands = economics.max_markup_wholesale_uzs / 1000
            add({price: 1, active(i): big_m}, upper_bound=max_markup_thousands + big_m)
        capping_scenarios.append(s)

    # Capital must cover acquisition and fixed shipment/activation costs in EVERY
    # modeled scenario. It is not merely an expected-value budget check.
    for s in case.scenarios:
        coeffs: dict[int, float] = {}
        for i, o in enumerate(case.options):
            coeffs[x(i)] = lot * unit_economics(case, o, s).acquisition_base_uzs / MILLION
            coeffs[shipments(i)] = o.shipment_fixed_uzs / MILLION
            coeffs[active(i)] = o.activation_fixed_uzs / MILLION
        add(coeffs, upper_bound=case.working_capital_budget_uzs / MILLION)

    # z <= scenario profit. Both sides in million UZS.
    # Pi_s = demand*p/1000 - sum_i (acq+below)*lot*x_i/1e6
    #        -sum_i fixedshipment_i*n_i/1e6 -sum_i activation_i*y_i/1e6
    for s in case.scenarios:
        coeffs = {worst: 1, price: -case.annual_demand_packs / 1000}
        for i, o in enumerate(case.options):
            e = unit_economics(case, o, s)
            coeffs[x(i)] = lot * (e.acquisition_base_uzs + e.below_line_uzs) / MILLION
            coeffs[shipments(i)] = o.shipment_fixed_uzs / MILLION
            coeffs[active(i)] = o.activation_fixed_uzs / MILLION
        add(coeffs, upper_bound=0)

    # Minimize negative weighted profit (expected and worst case).
    c = np.zeros(var_count)
    c[worst] = -risk_aversion
    c[price] = -(1 - risk_aversion) * case.annual_demand_packs / 1000
    for i, o in enumerate(case.options):
        expected_var_cost = sum(
            s.probability * lot * (
                unit_economics(case, o, s).acquisition_base_uzs + o.below_line_cost_uzs
            ) / MILLION for s in case.scenarios
        )
        c[x(i)] = (1 - risk_aversion) * expected_var_cost
        c[shipments(i)] = (1 - risk_aversion) * o.shipment_fixed_uzs / MILLION
        c[active(i)] = (1 - risk_aversion) * o.activation_fixed_uzs / MILLION

    result = milp(
        c=c,
        integrality=integrality,
        bounds=Bounds(lower, upper),
        constraints=LinearConstraint(np.asarray(constraints), np.asarray(lb), np.asarray(ub)),
        options={"time_limit": time_limit, "mip_rel_gap": 0.001},
    )
    bline, baseline_note = baseline(case, risk_aversion)

    if result.status == 2:
        diagnostics = ["No allocation meets ALL demand, shipment, markup, and cash constraints."]
        total_capacity = sum(min(o.capacity_packs, o.max_shipments * o.max_shipment_packs)
                             for o in case.options if o.eligible)
        if total_capacity < case.annual_demand_packs:
            diagnostics.append(f"Eligible shipping capacity {total_capacity:,} packs < required {case.annual_demand_packs:,} packs.")
        if baseline_note:
            diagnostics.append(baseline_note)
        return OptimizeResponse(case_id=case.id, status="infeasible", risk_aversion=risk_aversion,
                                solver_message=str(result.message), diagnostics=diagnostics,
                                baseline=bline, assumptions=ASSUMPTIONS)
    if result.status not in (0, 1) or result.x is None:
        raise SolverFailure(f"HiGHS did not return a verified feasible allocation: {result.message}")

    values = result.x
    rounded = []
    for i in range(n):
        if abs(values[x(i)] - round(values[x(i)])) > 1e-5 or abs(values[shipments(i)] - round(values[shipments(i)])) > 1e-5:
            raise SolverFailure("Solver returned nonintegral shipment or pack counts")
        rounded.append((int(round(values[x(i)])), int(round(values[shipments(i)]))))
    allocations = make_allocations(case, rounded)
    # Regenerate exact feasible price from the allocation to eliminate numerical
    # overshoot. Common price is monotonic in profit; maximum legal price is optimal.
    from .economics import allowable_price
    computed_price = allowable_price(case, allocations)
    if values[price] * 1000 > computed_price + 0.05:
        raise SolverFailure("Solver price exceeds independently recalculated limit")
    solution = evaluate(case, allocations, computed_price, risk_aversion)

    diagnostics = [
        f"{len(allocations)} eligible source/route configurations selected.",
        "Common contract price and procurement budget checked across every scenario.",
    ]
    if baseline_note:
        diagnostics.append(baseline_note)
    if any(abs(solution.wholesale_price_uzs - unit_economics(case, o, s).max_markup_wholesale_uzs) < 0.1
           for o in case.options for s in case.scenarios if o.id in {a.option_id for a in allocations}):
        diagnostics.append("15% synthetic markup bound is binding for at least one active route/scenario.")
    if any(abs(solution.wholesale_price_uzs - case.assumed_registered_wholesale_cap_uzs *
               (1 + s.wholesale_ceiling_change_pct / 100)) < 0.1 for s in case.scenarios):
        diagnostics.append("Assumed registered wholesale ceiling is binding in at least one scenario.")
    if solution.worst_case_cash_used_uzs > 0.99 * case.working_capital_budget_uzs:
        diagnostics.append("Working-capital budget is within 1% of being fully utilized.")
    if result.status == 1:
        diagnostics.append("Time limit reached; allocation is feasible, NOT certified optimal.")

    gap = getattr(result, "mip_gap", None)
    if gap is not None:
        gap = float(gap) if math.isfinite(float(gap)) else None
    return OptimizeResponse(
        case_id=case.id, status="optimal" if result.status == 0 else "feasible",
        risk_aversion=risk_aversion, solver_message=str(result.message),
        relative_optimality_gap=gap, diagnostics=diagnostics,
        baseline=bline, optimized=solution,
        expected_profit_delta_uzs=(solution.expected_profit_uzs - bline.expected_profit_uzs) if bline else None,
        assumptions=ASSUMPTIONS,
    )
