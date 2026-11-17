"""Cross-checks between independent implementations: twin vs compliance, optimizer plans vs compliance."""
import pytest
from hypothesis import HealthCheck, given, settings

from lagrange_rx.engine import compliance
from lagrange_rx.engine.optimize import OptimizerConfig, optimize
from lagrange_rx.engine.twin import caps_for, evaluate_plan_set
from lagrange_rx.eval.oracle import random_mini_problem
from lagrange_rx.rules import load_rules
from tests.strategies import calc_graphs, scenarios_for

R = load_rules()
SLOW = settings(max_examples=150, deadline=None, suppress_health_check=list(HealthCheck))


@given(gs=calc_graphs().flatmap(lambda g: scenarios_for(g).map(lambda s: (g, s))))
@settings(max_examples=300, deadline=None, suppress_health_check=list(HealthCheck))
def test_twin_and_compliance_agree_on_ceilings(gs):
    g, scs = gs
    for s in scs:
        c = caps_for(g, s, R)
        w, r = compliance.published_ceilings(g, s, R)
        assert w == pytest.approx(c.wholesale, rel=1e-6) and r == pytest.approx(c.retail, rel=1e-6)


def _assert_plan_legal(g, scs, cfg):
    res = optimize(g, scs, R, cfg)
    if res.plan is None:
        return
    outs = evaluate_plan_set(g, res.plan, scs, R, allow_emergency=cfg.emergency_enabled)
    bad = [c for c in compliance.check_plan(g, res.plan, scs, outs, R) if not c.passed]
    assert not bad, bad[:3]


@given(gs=calc_graphs().flatmap(lambda g: scenarios_for(g).map(lambda s: (g, s))))
@SLOW
def test_optimizer_plans_pass_independent_check_on_random_graphs(gs):
    g, scs = gs
    scs = [s.model_copy(update={"weight": 1.0}) for s in scs[:4]]
    _assert_plan_legal(g, scs, OptimizerConfig(risk_mode="cvar", hedge_enabled=True, time_limit_s=5))


@pytest.mark.slow
def test_10000_random_cases_zero_violations():
    modes = ["expected", "cvar", "worst_case"]
    for seed in range(10_000):
        g, scs = random_mini_problem(seed)
        _assert_plan_legal(g, scs, OptimizerConfig(risk_mode=modes[seed % 3], hedge_enabled=seed % 2 == 0,
                                                   time_limit_s=5))
