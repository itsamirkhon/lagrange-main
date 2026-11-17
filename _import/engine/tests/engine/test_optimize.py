import pytest

from lagrange_rx.engine.optimize import OptimizerConfig, cvar, optimize, risk_metrics, spread_months
from lagrange_rx.engine.twin import evaluate_plan
from lagrange_rx.eval.oracle import brute_force, random_mini_problem
from lagrange_rx.rules import load_rules

R = load_rules()
EXP = OptimizerConfig(risk_mode="expected", time_limit_s=10)


def test_cvar_helper():
    assert cvar([1, 2, 3, 4], [1, 1, 1, 1], 0.5) == pytest.approx(1.5)
    assert cvar([1, 2, 3, 4], [1, 1, 1, 1], 0.95) == pytest.approx(1.0)
    m = risk_metrics([-1, 3], [0.5, 0.5], 0.5)
    assert m.expected_uzs == 1 and m.worst_uzs == -1 and m.p_loss == 0.5


def test_spread_months_wraps_year():
    assert spread_months("2026-11", 2) == ("2027-02", "2027-08")
    assert spread_months("2026-11", 0) == ()


@pytest.mark.parametrize("seed", range(50))
def test_matches_brute_force_on_50_mini_problems(seed):
    graph, scen = random_mini_problem(seed)
    oracle = brute_force(graph, scen, R)
    res = optimize(graph, scen, R, EXP)
    assert res.info.status == "OPTIMAL"
    assert res.metrics.expected_uzs == pytest.approx(oracle.best_expected_uzs, rel=1e-6, abs=1.0)
    # the returned plan, evaluated independently by the twin, achieves the same value
    tw = sum(s.weight for s in scen)
    ev = sum(s.weight * evaluate_plan(graph, res.plan, s, R).profit_uzs for s in scen) / tw
    assert ev == pytest.approx(oracle.best_expected_uzs, rel=1e-6, abs=1.0)


@pytest.mark.parametrize("seed", range(5))
def test_milp_scenario_profits_equal_twin(seed):
    graph, scen = random_mini_problem(seed)
    res = optimize(graph, scen, R, OptimizerConfig(risk_mode="cvar", time_limit_s=10))
    for s, p in zip(scen, res.scenario_profits_uzs):
        assert p == pytest.approx(evaluate_plan(graph, res.plan, s, R).profit_uzs, rel=1e-6, abs=1.0)


def test_worst_case_has_best_worst(tiny):
    graph, scen = random_mini_problem(3)
    worst = optimize(graph, scen, R, OptimizerConfig(risk_mode="worst_case", time_limit_s=10))
    exp = optimize(graph, scen, R, EXP)
    assert worst.metrics.worst_uzs >= exp.metrics.worst_uzs - 1.0
    assert exp.metrics.expected_uzs >= worst.metrics.expected_uzs - 1.0


def test_cvar_between_modes(tiny):
    graph, scen = random_mini_problem(7)
    cfg = OptimizerConfig(risk_mode="cvar", alpha=0.7, lam=0.5, time_limit_s=10)
    res = optimize(graph, scen, R, cfg)
    assert res.plan.risk_mode == "cvar" and res.info.status == "OPTIMAL"


def test_hedge_never_worse_than_no_hedge():
    graph, scen = random_mini_problem(11)
    a = optimize(graph, scen, R, EXP)
    b = optimize(graph, scen, R, OptimizerConfig(risk_mode="expected", hedge_enabled=True, time_limit_s=10))
    assert b.metrics.expected_uzs >= a.metrics.expected_uzs - 1.0
    ev = sum(s.weight * evaluate_plan(graph, b.plan, s, R).profit_uzs for s in scen) / sum(s.weight for s in scen)
    assert ev == pytest.approx(b.metrics.expected_uzs, rel=1e-6, abs=1.0)


def test_status_and_gap_reported():
    graph, scen = random_mini_problem(1)
    res = optimize(graph, scen, R, EXP)
    assert res.info.proven_optimal and res.info.gap is not None and res.info.seconds >= 0
