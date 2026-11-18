"""Regression for the original 500: profit-only recourse underfilled the MILP service policy."""
import pytest

from lagrange_rx.engine.decision_policy import independently_check_service
from lagrange_rx.engine.optimize import OptimizerConfig
from lagrange_rx.engine.scenarios import ScenarioConfig
from lagrange_rx.engine.service import run_optimize
from lagrange_rx.engine.twin import evaluate_plan_set
from lagrange_rx.fixtures.l0 import build_l0_calculation_graph
from lagrange_rx.engine.rules import load_rules


@pytest.mark.parametrize('risk', ['expected', 'cvar', 'worst_case'])
def test_real_synthetic_fixture_service_floor_is_enforced_in_operational_outcomes(risk):
    g = build_l0_calculation_graph()
    res = run_optimize(g, scen_cfg=ScenarioConfig(n=100, k=12),
                       opt_cfg=OptimizerConfig(risk_mode=risk, service_level=0.9))
    assert res.plan is not None, res.impossible
    achieved = independently_check_service(res.outcomes, res.context.scenarios, .9)
    assert achieved >= .9 - 1e-6
    assert sum(line.packs for line in res.plan.lines) > 0
    assert res.checks and all(c.passed for c in res.checks)


def test_unconstrained_optimizer_still_uses_existing_legacy_outcomes():
    g = build_l0_calculation_graph()
    res = run_optimize(g, scen_cfg=ScenarioConfig(n=60, k=10),
                       opt_cfg=OptimizerConfig(risk_mode='expected', service_level=None))
    assert res.plan is not None
    expected = evaluate_plan_set(g, res.plan, res.context.scenarios, load_rules())
    assert [o.sold for o in res.outcomes] == pytest.approx([o.sold for o in expected])
