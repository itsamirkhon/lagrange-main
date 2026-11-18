"""Mutation harness: textual mutants of compliance.py and mutated Rules must all be caught
by the boundary suite (compliance_suite.CASES); the unmutated module and rules must survive.

Equivalent mutants (deliberately absent): `<=` -> `<` on the *price* comparisons is masked by the
1e-6 tolerance (price == limit still passes), so those comparisons are mutated through the
tolerance instead (TOL_* mutants). Integer route limits are compared exactly and are mutated.
"""
from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path

import pytest

from lagrange_rx.engine import compliance as real
from lagrange_rx.engine.rules import load_rules
from tests.engine import compliance_suite as suite

RULES = load_rules()
SRC = Path(real.__file__).read_text()

MUTANTS: dict[str, tuple[str, str]] = {
    "markup_plus_one_point": ("(1.0 + rules.max_markup)", "(1.01 + rules.max_markup)"),
    "drop_vat_wholesale": ("graph.product.chun_uzs * f / (1.0 + rules.vat)", "graph.product.chun_uzs * f"),
    "drop_vat_retail": (
        "chchn_uzs * f / (1.0 + rules.vat) / (1.0 + rules.retail_markup)", "chchn_uzs * f / (1.0 + rules.retail_markup)",
    ),
    "drop_retail_markup": (" / (1.0 + rules.retail_markup)", ""),
    "retail_uses_wholesale_price": ("graph.product.chchn_uzs * f", "graph.product.chun_uzs * f"),
    "freight_in_base": ("+ route.fees_uzs + pr", "+ route.fees_uzs + route.freight_uzs + pr"),
    "hedge_in_base": ("+ route.fees_uzs + pr", "+ route.fees_uzs + 0.02 * route.kc * scenario.fx[route.currency] + pr"),
    "drop_fees": ("+ route.fees_uzs + pr", "+ pr"),
    "drop_duty": ("(1.0 + route.duty_rate)", "1.0"),
    "drop_pr_confirmed": (
        "route.pr_uzs if route.pr_confirmed and route.pr_uzs is not None else 0.0",
        "route.pr_uzs if route.pr_uzs is not None else 0.0",
    ),
    "scheduled_threshold_strict": ("growth >= rules.threshold_scheduled", "growth > rules.threshold_scheduled"),
    "immediate_threshold_inclusive": ("growth > rules.threshold_immediate", "growth >= rules.threshold_immediate"),
    "forget_round": ("round(fx / state.base - 1.0, GROWTH_DECIMALS)", "(fx / state.base - 1.0)"),
    "wrong_calendar_month": ("(int(horizon_start[5:7]) - 1 + offset)", "(int(horizon_start[5:7]) + offset)"),
    "lag_one_month_longer": ("m + lag_months, growth", "m + lag_months + 1, growth"),
    "cut_starts_one_month_late": ("range(scenario.cut_month - 1, MONTHS)", "range(scenario.cut_month, MONTHS)"),
    "annual_factor_is_last_month": ("f = sum(monthly) / len(monthly)", "f = monthly[-1]"),
    "base_not_reset_after_scheduled": (
        "return _State(fx, state.factor * (1.0 + growth), None)", "return _State(state.base, state.factor * (1.0 + growth), None)",
    ),
    "base_not_reset_after_immediate": (
        "_State(state.pending.fx, state.factor", "_State(state.base, state.factor",
    ),
    "pool_not_weighted_by_sold": (
        "for rid, packs in outcome.sold_by_route.items()\n        if rid in known",
        "for rid, packs in {k: 1.0 for k in outcome.sold_by_route}.items()\n        if rid in known",
    ),
    "tol_dropped": ("limit * (1.0 + REL_TOL) + ABS_TOL", "limit"),
    "tol_widened": ("REL_TOL = 1e-6", "REL_TOL = 1e-3"),
    "contract_loosened": ("_price_result(RULE_CONTRACT, outcome, contract)", "_price_result(RULE_CONTRACT, outcome, contract * 1.01)"),
    "price_checked_even_without_sales": ("outcome.sold == 0 or _within(", "_within("),
    "empty_pool_passes": ('outcome.sold == 0, "empty sold pool"', 'True, "empty sold pool"'),
    "uniform_price_unchecked": ("_close(outcome.revenue_uzs, implied)", "True"),
    "capacity_strict": ("packs <= route.capacity_packs", "packs < route.capacity_packs"),
    "min_batch_strict": ("route.min_batch * shipments <= packs", "route.min_batch * shipments < packs"),
    "lot_unchecked": ("packs % route.lot_size == 0", "True"),
    "emergency_ignored_in_supply": ("+ sum(outcome.emergency.values())", "+ 0.0"),
    "unavailable_route_may_sell": ("if scenario.available(rid) else 0.0", "if True else 0.0"),
    "missing_outcome_passes": ('0.0, 0.0, False, "missing outcome"', '0.0, 0.0, True, "missing outcome"'),
}

RULE_MUTANTS = {
    "max_markup_16": dict(max_markup=0.16),
    "max_markup_14": dict(max_markup=0.14),
    "vat_zero": dict(vat=0.0),
    "vat_15": dict(vat=0.15),
    "retail_markup_zero": dict(retail_markup=0.0),
    "retail_markup_25": dict(retail_markup=0.25),
    "immediate_threshold_6": dict(threshold_immediate=0.06),
    "immediate_threshold_4": dict(threshold_immediate=0.04),
    "scheduled_threshold_2": dict(threshold_scheduled=0.02),
    "scheduled_threshold_4": dict(threshold_scheduled=0.04),
    "lag_60_days": dict(lag_days=60),
    "no_july_index_date": dict(index_dates=((1, 1),)),
}


def load_mutant(name: str, old: str, new: str, tmp_path: Path):
    assert SRC.count(old) == 1, f"mutation anchor for {name} must occur exactly once"
    path = tmp_path / f"mutant_{name}.py"
    path.write_text(SRC.replace(old, new))
    spec = importlib.util.spec_from_file_location(f"mutant_{name}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
    return module


def test_there_are_enough_mutants():
    assert len(MUTANTS) >= 12 and len(RULE_MUTANTS) >= 5


def test_unmutated_module_and_rules_survive_all_cases():
    assert suite.run_all(real, RULES) == []


@pytest.mark.parametrize("name", MUTANTS)
def test_source_mutant_is_killed(name, tmp_path):
    old, new = MUTANTS[name]
    assert old != new
    mutant = load_mutant(name, old, new, tmp_path)  # must import cleanly: not killed by a crash at import
    assert suite.run_all(mutant, RULES), f"mutant {name} survived"


@pytest.mark.parametrize("name", RULE_MUTANTS)
def test_rules_mutant_flips_boundary_cases(name):
    mutated = dataclasses.replace(RULES, **RULE_MUTANTS[name])
    assert suite.run_all(real, mutated), f"rules mutant {name} survived"


def test_markup_boundary_flips_when_rule_changes():
    at_limit, above = 153_525.0, 153_525.0 * 1.001
    assert suite.failed(suite.run(real, RULES, above)) == {suite.MARKUP}
    looser = dataclasses.replace(RULES, max_markup=0.16)
    assert suite.failed(suite.run(real, looser, above)) == set()
    tighter = dataclasses.replace(RULES, max_markup=0.14)
    assert suite.failed(suite.run(real, tighter, at_limit)) == {suite.MARKUP}
