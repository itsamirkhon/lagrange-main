"""Independent brute-force MILP oracle on a deliberately tiny integer instance.

No calls to optimization or valuation helpers in the enumerator. It independently
enumerates discrete choices, computes the maximum shared price, and scores risk.
"""

from copy import deepcopy
from itertools import product

import pytest

from lagrange_rx.demo_data import SYNTHETIC_CASE
from lagrange_rx.models import PharmaCase
from lagrange_rx.solver import optimize


def tiny_case() -> PharmaCase:
    raw = deepcopy(SYNTHETIC_CASE)
    raw["id"] = "tiny_independent_oracle"
    raw["lot_size_packs"] = 1
    raw["annual_demand_packs"] = 6
    raw["working_capital_budget_uzs"] = 900_000
    raw["assumed_registered_wholesale_cap_uzs"] = 125_000
    raw["assumed_max_contract_wholesale_uzs"] = 125_000
    raw["options"] = [
        dict(id="a", name="A", factory_cost_uzs=95_000, import_fees_uzs=6_000,
             freight_uzs=5_000, below_line_cost_uzs=10_000, fx_exposed_fraction=0.8,
             capacity_packs=6, min_shipment_packs=1, max_shipment_packs=3,
             max_shipments=3, shipment_fixed_uzs=350, activation_fixed_uzs=800),
        dict(id="b", name="B", factory_cost_uzs=97_000, import_fees_uzs=5_000,
             freight_uzs=3_000, below_line_cost_uzs=7_000, fx_exposed_fraction=0.5,
             capacity_packs=6, min_shipment_packs=2, max_shipment_packs=3,
             max_shipments=2, shipment_fixed_uzs=700, activation_fixed_uzs=1800),
        dict(id="c", name="C", factory_cost_uzs=100_000, import_fees_uzs=5_000,
             freight_uzs=2_000, below_line_cost_uzs=5_000, fx_exposed_fraction=0.3,
             capacity_packs=6, min_shipment_packs=1, max_shipment_packs=2,
             max_shipments=3, shipment_fixed_uzs=500, activation_fixed_uzs=1900),
    ]
    raw["baseline_option_id"] = "a"
    raw["scenarios"] = [
        dict(id="s0", name="Normal", probability=0.6,
             fx_change_pct=0, freight_change_pct=0),
        dict(id="s1", name="FX +10%", probability=0.4,
             fx_change_pct=10, freight_change_pct=25),
    ]
    return PharmaCase.model_validate(raw)


def brute_force(case: PharmaCase, risk_aversion: float) -> float:
    """All integer allocations and shipment combinations, with direct-unit formulas."""
    assert case.lot_size_packs == 1
    best = float("-inf")
    options = case.options
    for counts in product(range(7), repeat=len(options)):
        if sum(counts) != case.annual_demand_packs:
            continue
        for ship_counts in product(range(4), repeat=len(options)):
            bad = False
            for units, trips, option in zip(counts, ship_counts, options, strict=True):
                if units == 0 and trips != 0:
                    bad = True
                if units > 0 and (trips == 0 or trips > option.max_shipments or units > option.capacity_packs):
                    bad = True
                if units > 0 and not (option.min_shipment_packs * trips <= units <= option.max_shipment_packs * trips):
                    bad = True
            if bad:
                continue
            fixed = sum((trips * opt.shipment_fixed_uzs + (opt.activation_fixed_uzs if count else 0))
                        for count, trips, opt in zip(counts, ship_counts, options, strict=True))
            max_price = case.assumed_max_contract_wholesale_uzs
            scenario_costs = []
            for s in case.scenarios:
                max_price = min(max_price, case.assumed_registered_wholesale_cap_uzs *
                                (1 + s.wholesale_ceiling_change_pct / 100))
                acquisitions = []
                for count, o in zip(counts, options, strict=True):
                    base = o.factory_cost_uzs * (1 + o.fx_exposed_fraction * s.fx_change_pct / 100)
                    base += o.import_fees_uzs + o.freight_uzs * (1 + s.freight_change_pct / 100)
                    acquisitions.append(base)
                    if count:
                        max_price = min(max_price, base * (1 + case.illustrative_markup_limit))
                cash = sum(units * base for units, base in zip(counts, acquisitions, strict=True)) + fixed
                if cash > case.working_capital_budget_uzs + 0.01:
                    bad = True
                scenario_costs.append(sum(count * (base + o.below_line_cost_uzs)
                                          for count, base, o in zip(counts, acquisitions, options, strict=True)))
            if bad:
                continue
            profits = [case.annual_demand_packs * max_price - cost - fixed for cost in scenario_costs]
            expected = sum(s.probability * profit for s, profit in zip(case.scenarios, profits, strict=True))
            best = max(best, (1 - risk_aversion) * expected + risk_aversion * min(profits))
    return best


@pytest.mark.parametrize("risk", [0.0, 0.2, 0.6, 1.0])
def test_milp_matches_independent_bruteforce(risk):
    case = tiny_case()
    expected = brute_force(case, risk)
    actual = optimize(case, risk_aversion=risk)
    assert actual.status == "optimal"
    assert actual.optimized.objective_uzs == pytest.approx(expected, abs=0.01)
    assert actual.relative_optimality_gap is not None
    assert actual.relative_optimality_gap < 0.002
