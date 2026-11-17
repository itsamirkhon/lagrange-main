"""Economic definitions and independently reusable solution auditing.

This is a synthetic simplified decision model, NOT a legal pricing certification.
Retail ceiling in the hackathon brief is background; wholesale cap is an assumption.
All money modeled on one illustrative normalized tax basis. VAT is not computed.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from .models import (
    Allocation, PharmaCase, Scenario, ScenarioResult, Solution, SupplyOption,
)

MILLION = 1_000_000.0
ASSUMPTIONS = [
    "Supplier quotations, route eligibility, capacities, costs, demand and scenario probabilities are invented.",
    "150,000 packs and 150,000 UZS maximum retail price come from challenge #16; tax basis not supplied.",
    "125,000 UZS assumed wholesale ceiling and contract maximum are NOT official registry values.",
    "One wholesale price is applied across all chosen routes and modeled scenarios (conservative common-price policy).",
    "15% markup check uses synthetic invoice acquisition base; real legal base and VAT require expert review.",
    "Scenario price-cap changes represent an explicit hypothetical official revision, not automatic FX repricing.",
    "FX-sensitive fraction and route costs are scenario assumptions. No client or patient data are used.",
    "No lead-time, inventory/expiry, marketing causality, liquidity timing or localization model is implemented.",
    "A MILP optimum is only optimal within these assumptions; real-world profit uplift is unverified.",
]


@dataclass(frozen=True)
class UnitEconomics:
    acquisition_base_uzs: float
    below_line_uzs: float
    max_markup_wholesale_uzs: float
    registered_wholesale_cap_uzs: float


def unit_economics(case: PharmaCase, option: SupplyOption, scenario: Scenario) -> UnitEconomics:
    factory = option.factory_cost_uzs * (1 + option.fx_exposed_fraction * scenario.fx_change_pct / 100)
    freight = option.freight_uzs * (1 + scenario.freight_change_pct / 100)
    base = factory + option.import_fees_uzs + freight
    cap = case.assumed_registered_wholesale_cap_uzs * (1 + scenario.wholesale_ceiling_change_pct / 100)
    return UnitEconomics(
        acquisition_base_uzs=base,
        below_line_uzs=option.below_line_cost_uzs,
        max_markup_wholesale_uzs=base * (1 + case.illustrative_markup_limit),
        registered_wholesale_cap_uzs=cap,
    )


def allowable_price(case: PharmaCase, allocations: list[Allocation]) -> float:
    """Max *common* sale price that is legal under every modeled scenario/active route."""
    active = {a.option_id for a in allocations if a.packs > 0}
    if not active:
        return 0.0
    upper = float(case.assumed_max_contract_wholesale_uzs)
    for scenario in case.scenarios:
        upper = min(upper, case.assumed_registered_wholesale_cap_uzs *
                    (1 + scenario.wholesale_ceiling_change_pct / 100))
        for option in case.options:
            if option.id in active:
                upper = min(upper, unit_economics(case, option, scenario).max_markup_wholesale_uzs)
    return max(0.0, upper)


def make_allocations(case: PharmaCase, counts: list[tuple[int, int]]) -> list[Allocation]:
    result = []
    for option, (lots, shipments) in zip(case.options, counts, strict=True):
        if lots <= 0:
            continue
        result.append(Allocation(
            option_id=option.id, option_name=option.name,
            packs=lots * case.lot_size_packs, shipments=shipments,
            activation_cost_uzs=option.activation_fixed_uzs,
            shipment_cost_uzs=shipments * option.shipment_fixed_uzs,
        ))
    return result


def validate_allocation(case: PharmaCase, allocations: list[Allocation], price: float, tol: float = 0.01) -> list[str]:
    """Independent, post-solver checks. Never trust a floating-point solver without checks."""
    issues = []
    lookup = {o.id: o for o in case.options}
    if len({a.option_id for a in allocations}) != len(allocations):
        issues.append("Duplicate route")
    if sum(a.packs for a in allocations) != case.annual_demand_packs:
        issues.append("Demand not fully met")
    if price < 0 or price > case.assumed_max_contract_wholesale_uzs + tol:
        issues.append("Contract sale-price cap exceeded")
    for a in allocations:
        o = lookup.get(a.option_id)
        if o is None:
            issues.append(f"Unknown option: {a.option_id}")
            continue
        if not o.eligible:
            issues.append(f"Ineligible option: {o.id}")
        if a.packs <= 0 or a.packs % case.lot_size_packs:
            issues.append(f"Invalid lot quantity: {o.id}")
        if a.packs > o.capacity_packs:
            issues.append(f"Capacity violated: {o.id}")
        if a.shipments < 1 or a.shipments > o.max_shipments:
            issues.append(f"Shipment count violated: {o.id}")
        if a.packs < o.min_shipment_packs * a.shipments or a.packs > o.max_shipment_packs * a.shipments:
            issues.append(f"Shipment size violated: {o.id}")
        if a.shipment_cost_uzs != a.shipments * o.shipment_fixed_uzs:
            issues.append(f"Fixed shipment expense mismatch: {o.id}")
        if a.activation_cost_uzs != o.activation_fixed_uzs:
            issues.append(f"Activation expense mismatch: {o.id}")
    if not allocations:
        issues.append("No allocation")
    for scenario in case.scenarios:
        legal_cap = case.assumed_registered_wholesale_cap_uzs * (1 + scenario.wholesale_ceiling_change_pct / 100)
        if price > legal_cap + tol:
            issues.append(f"Registered wholesale cap violated in {scenario.id}")
        cash = sum(a.packs * unit_economics(case, lookup[a.option_id], scenario).acquisition_base_uzs
                   + a.shipment_cost_uzs + a.activation_cost_uzs for a in allocations if a.option_id in lookup)
        if cash > case.working_capital_budget_uzs + tol:
            issues.append(f"Cash budget violated in {scenario.id}")
        for a in allocations:
            if a.option_id in lookup and price > unit_economics(case, lookup[a.option_id], scenario).max_markup_wholesale_uzs + tol:
                issues.append(f"15% purchase-base markup bound violated: {a.option_id} / {scenario.id}")
    return issues


def evaluate(case: PharmaCase, allocations: list[Allocation], price: float, risk_aversion: float) -> Solution:
    lookup = {o.id: o for o in case.options}
    if (problems := validate_allocation(case, allocations, price)):
        raise ValueError("Invalid allocation: " + "; ".join(problems))
    outputs: list[ScenarioResult] = []
    for s in case.scenarios:
        revenue = case.annual_demand_packs * price
        acquisition = sum(a.packs * unit_economics(case, lookup[a.option_id], s).acquisition_base_uzs
                          for a in allocations)
        below_line = sum(a.packs * lookup[a.option_id].below_line_cost_uzs for a in allocations)
        fixed = sum(a.shipment_cost_uzs + a.activation_cost_uzs for a in allocations)
        cash = acquisition + fixed
        profit = revenue - acquisition - below_line - fixed
        max_cap = case.assumed_registered_wholesale_cap_uzs * (1 + s.wholesale_ceiling_change_pct / 100)
        compliant = all(price <= unit_economics(case, lookup[a.option_id], s).max_markup_wholesale_uzs + 0.01
                        for a in allocations)
        outputs.append(ScenarioResult(
            scenario_id=s.id, name=s.name, probability=s.probability,
            wholesale_price_uzs=price, wholesale_cap_uzs=max_cap,
            revenue_uzs=revenue, acquisition_cost_uzs=acquisition,
            below_line_cost_uzs=below_line, fixed_cost_uzs=fixed,
            profit_uzs=profit, margin_pct=100 * profit / revenue if revenue else 0,
            working_capital_used_uzs=cash,
            under_budget=cash <= case.working_capital_budget_uzs + 0.01,
            markup_compliant=compliant,
            registered_ceiling_compliant=price <= max_cap + 0.01,
        ))
    expected = sum(r.probability * r.profit_uzs for r in outputs)
    worst = min(r.profit_uzs for r in outputs)
    revenue = case.annual_demand_packs * price
    return Solution(
        wholesale_price_uzs=price, allocations=allocations,
        expected_profit_uzs=expected, worst_case_profit_uzs=worst,
        expected_margin_pct=100 * expected / revenue if revenue else 0,
        worst_case_margin_pct=100 * worst / revenue if revenue else 0,
        scenario_results=outputs,
        objective_uzs=(1 - risk_aversion) * expected + risk_aversion * worst,
        worst_case_cash_used_uzs=max(r.working_capital_used_uzs for r in outputs),
    )


def baseline(case: PharmaCase, risk_aversion: float) -> tuple[Solution | None, str | None]:
    o = next(opt for opt in case.options if opt.id == case.baseline_option_id)
    if not o.eligible:
        return None, "Baseline option is ineligible"
    shipments = math.ceil(case.annual_demand_packs / o.max_shipment_packs)
    allocs = make_allocations(case, [
        (case.annual_demand_packs // case.lot_size_packs, shipments) if item.id == o.id else (0, 0)
        for item in case.options
    ])
    price = allowable_price(case, allocs)
    issues = validate_allocation(case, allocs, price)
    if issues:
        return None, "Baseline infeasible: " + "; ".join(issues)
    return evaluate(case, allocs, price, risk_aversion), None
