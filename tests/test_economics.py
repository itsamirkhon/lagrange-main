"""Pricing, invariants, and failure-path tests."""

from copy import deepcopy

import pytest
from pydantic import ValidationError

from lagrange_rx.demo_data import SYNTHETIC_CASE, demo_case
from lagrange_rx.economics import allowable_price, baseline, evaluate, validate_allocation
from lagrange_rx.models import Allocation, PharmaCase, Scenario
from lagrange_rx.solver import optimize


def test_demo_case_reproduces_base_case_not_risk_weighted_average():
    case = demo_case()
    bline, note = baseline(case, 0.35)
    assert note is None and bline is not None
    assert bline.wholesale_price_uzs == pytest.approx(125_000, abs=0.1)
    base = next(row for row in bline.scenario_results if row.scenario_id == "base")
    assert base.revenue_uzs == pytest.approx(18_750_000_000)
    assert base.acquisition_cost_uzs == pytest.approx(150_000 * 108_696)
    assert base.below_line_cost_uzs == pytest.approx(150_000 * 13_200)
    assert base.profit_uzs == pytest.approx(150_000 * 3_104 - 8 * 300_000)
    assert 2.4 <= base.margin_pct <= 2.5


def test_lower_invoice_cost_can_lower_net_profit_when_markup_binding():
    raw = deepcopy(SYNTHETIC_CASE)
    raw["options"] = [deepcopy(raw["options"][0])]
    raw["scenarios"] = [dict(id="base", name="Only", probability=1.0)]
    raw["annual_demand_packs"] = 10_000
    raw["working_capital_budget_uzs"] = 2_000_000_000
    raw["options"][0]["factory_cost_uzs"] = 100_000
    raw["options"][0]["import_fees_uzs"] = 0
    raw["options"][0]["freight_uzs"] = 0
    raw["options"][0]["below_line_cost_uzs"] = 8_000
    high = PharmaCase.model_validate(raw)
    raw["options"][0]["factory_cost_uzs"] = 90_000
    low = PharmaCase.model_validate(raw)
    high_b, _ = baseline(high, 0)
    low_b, _ = baseline(low, 0)
    assert high_b.wholesale_price_uzs == pytest.approx(115_000, abs=0.01)
    assert low_b.wholesale_price_uzs == pytest.approx(103_500, abs=0.01)
    assert low_b.expected_profit_uzs < high_b.expected_profit_uzs


def test_price_and_cash_invariants_for_default_solution():
    case = demo_case()
    result = optimize(case)
    assert result.status == "optimal"
    assert result.relative_optimality_gap is not None
    assert result.optimized is not None
    assert validate_allocation(case, result.optimized.allocations, result.optimized.wholesale_price_uzs) == []
    assert result.expected_profit_delta_uzs == pytest.approx(
        result.optimized.expected_profit_uzs - result.baseline.expected_profit_uzs
    )
    assert all(s.under_budget and s.markup_compliant and s.registered_ceiling_compliant
               for s in result.optimized.scenario_results)
    assert sum(a.packs for a in result.optimized.allocations) == 150_000


def test_infeasible_capacity_is_reported_not_hallucinated():
    raw = deepcopy(SYNTHETIC_CASE)
    for opt in raw["options"]:
        opt["capacity_packs"] = 10_000
    case = PharmaCase.model_validate(raw)
    result = optimize(case)
    assert result.status == "infeasible"
    assert result.optimized is None
    assert any("capacity" in item.lower() for item in result.diagnostics)


def test_infeasible_budget_is_reported():
    raw = deepcopy(SYNTHETIC_CASE)
    raw["working_capital_budget_uzs"] = 1_000_000
    result = optimize(PharmaCase.model_validate(raw))
    assert result.status == "infeasible"
    assert result.optimized is None


def test_option_disabled_ineligible():
    raw = deepcopy(SYNTHETIC_CASE)
    for opt in raw["options"]:
        opt["eligible"] = False
    result = optimize(PharmaCase.model_validate(raw))
    assert result.status == "infeasible"


def test_baseline_can_be_infeasible_without_dropping_optimized_result():
    raw = deepcopy(SYNTHETIC_CASE)
    raw["options"][0]["eligible"] = False
    result = optimize(PharmaCase.model_validate(raw))
    assert result.status in {"optimal", "infeasible"}
    assert result.baseline is None
    assert any("Baseline" in d for d in result.diagnostics)


def test_detects_corrupted_markup_and_caps_and_cash():
    case = demo_case()
    allocations = baseline(case, 0)[0].allocations
    issues = validate_allocation(case, allocations, 170_000)
    assert any("cap" in item.lower() for item in issues)
    assert any("markup" in item.lower() for item in issues)
    assert any("contract" in item.lower() for item in issues)


def test_detects_corrupted_shipment_count():
    case = demo_case()
    source = baseline(case, 0)[0].allocations[0]
    broken = Allocation.model_validate({**source.model_dump(), "shipments": 99})
    assert any("Shipment" in issue for issue in validate_allocation(case, [broken], 125000))


def test_scenario_must_sum_to_one_and_input_cannot_be_nan():
    raw = deepcopy(SYNTHETIC_CASE)
    raw["scenarios"][0]["probability"] = 0.6
    with pytest.raises(ValidationError, match="sum to 1"):
        PharmaCase.model_validate(raw)
    with pytest.raises(ValidationError):
        Scenario(id="nan", name="nan", probability=float("nan"))


def test_cap_revision_scenario_requires_lower_common_price():
    raw = deepcopy(SYNTHETIC_CASE)
    for scenario in raw["scenarios"]:
        scenario["wholesale_ceiling_change_pct"] = -10
    case = PharmaCase.model_validate(raw)
    result = optimize(case)
    assert result.status == "optimal"
    assert result.optimized.wholesale_price_uzs <= 112_500.01
    assert all(s.registered_ceiling_compliant for s in result.optimized.scenario_results)


def test_case_rejects_duplicate_options_and_bad_lot_step():
    raw = deepcopy(SYNTHETIC_CASE)
    raw["options"].append(deepcopy(raw["options"][0]))
    with pytest.raises(ValidationError):
        PharmaCase.model_validate(raw)
    raw = deepcopy(SYNTHETIC_CASE)
    raw["annual_demand_packs"] = 150_001
    with pytest.raises(ValidationError, match="divisible"):
        PharmaCase.model_validate(raw)
