"""apply_wholesale_markup_rule: the 15% rule (UZ-PF13-WHOLESALE-MARKUP, needs_legal_review) can be switched off in
the engine (twin, optimizer, joint recourse); compliance then reports it as not applied. Default is True."""
import pytest

from lagrange_rx.engine import compliance
from lagrange_rx.engine.adapter import EngineConfig
from lagrange_rx.engine.joint_recourse import evaluate_service_recourse
from lagrange_rx.engine.optimize import OptimizerConfig, optimize
from lagrange_rx.engine.twin import evaluate_plan
from lagrange_rx.engine.types import Plan, PlanLine
from tests.conftest import make_graph, make_scenario


def plan():
    return Plan(plan_id="p", sku_id="P1", horizon="h", lines=(PlanLine(route_id="R1", packs=1000, shipments=1),))


def graphs():
    on = make_graph().for_calculation()
    off = make_graph(apply_wholesale_markup_rule=False).for_calculation()
    return on, off


def test_default_is_on():
    assert EngineConfig(horizon_start="2026-11").apply_wholesale_markup_rule is True
    assert make_graph().for_calculation().apply_wholesale_markup_rule is True


def test_twin_rule_on_caps_price_at_115_percent_of_base(rules):
    on, _ = graphs()
    o = evaluate_plan(on, plan(), make_scenario(demand=800.0), rules)
    assert o.price_uzs == pytest.approx(1.15 * 67_000) and o.binding == "markup15"


def test_twin_rule_off_price_goes_to_registry_cap(rules):
    _, off = graphs()
    o = evaluate_plan(off, plan(), make_scenario(demand=800.0), rules)
    assert o.price_uzs == pytest.approx(100_000) and o.binding == "registry"


def test_optimizer_rule_off_sells_more_valuable_plan(rules):
    on, off = graphs()
    cfg = OptimizerConfig(risk_mode="expected")
    sc = [make_scenario(demand=800.0)]
    r_on, r_off = optimize(on, sc, rules, cfg), optimize(off, sc, rules, cfg)
    assert r_off.metrics.expected_uzs > r_on.metrics.expected_uzs


def test_joint_recourse_respects_switch(rules):
    on, off = graphs()
    sc = [make_scenario(demand=800.0)]
    o_on = evaluate_service_recourse(on, plan(), sc, rules, service_level=0.5)[0]
    o_off = evaluate_service_recourse(off, plan(), sc, rules, service_level=0.5)[0]
    assert o_on.price_uzs == pytest.approx(1.15 * 67_000, rel=1e-6)
    assert o_off.price_uzs == pytest.approx(100_000, rel=1e-6)


def _markup_checks(res):
    return [c for c in res if c.rule_id == compliance.RULE_MARKUP]


def test_compliance_rule_on_flags_a_price_above_115(rules):
    on, _ = graphs()
    sc = make_scenario(demand=800.0)
    o = evaluate_plan(graphs()[1], plan(), sc, rules)  # priced at 100 000 > 77 050
    res = _markup_checks(compliance.check_plan(on, plan(), [sc], [o], rules))
    assert res and not res[0].passed


def test_compliance_rule_off_reports_not_applied(rules):
    _, off = graphs()
    sc = make_scenario(demand=800.0)
    o = evaluate_plan(off, plan(), sc, rules)
    res = _markup_checks(compliance.check_plan(off, plan(), [sc], [o], rules))
    assert len(res) == 1 and res[0].passed
    assert "not applied (needs_legal_review)" in res[0].detail
    assert all(c.passed for c in compliance.check_plan(off, plan(), [sc], [o], rules))
