import pytest

from lagrange_rx.engine.optimize import OptimizerConfig
from lagrange_rx.engine.scenarios import ScenarioConfig
from lagrange_rx.engine.service import CalibrationRequired, run_optimize, run_what_if
from lagrange_rx.fixtures.l0 import build_l0_graph
from lagrange_rx.engine._legacy_ontology import ActualResult

SC = ScenarioConfig(n=300, k=20)
OC = OptimizerConfig(risk_mode="cvar", time_limit_s=20)


@pytest.fixture(scope="module")
def l0_result():
    return run_optimize(build_l0_graph(), scen_cfg=SC, opt_cfg=OC)


def test_l0_end_to_end_is_legal_and_proven(l0_result):
    r = l0_result
    assert r.plan is not None and r.solve.status == "OPTIMAL" and r.calibration.calibrated
    assert all(c.passed for c in r.checks) and len(r.checks) > 50
    assert r.plan.snapshot_id == r.snapshot.snapshot_id
    assert r.waterfall and r.waterfall[-1].expected_profit_uzs >= r.waterfall[0].expected_profit_uzs
    assert any(c.kind == "legal" and c.is_binding for c in r.constraints)
    assert r.seconds < 20


def test_same_snapshot_same_plan(l0_result):
    again = run_optimize(build_l0_graph(), scen_cfg=SC, opt_cfg=OC)
    assert again.snapshot.snapshot_id == l0_result.snapshot.snapshot_id
    assert again.plan == l0_result.plan


def test_changed_input_changes_snapshot(l0_result):
    g = build_l0_graph()
    g2 = g.model_copy(update={"penalty_uzs_per_pack": 3500.0})
    assert run_optimize(g2, scen_cfg=SC, opt_cfg=OC).snapshot.snapshot_id != l0_result.snapshot.snapshot_id


def test_uncalibrated_twin_blocks_solver_unless_demo():
    g = build_l0_graph()
    bad = g.model_copy(update={"actual": ActualResult(revenue_uzs=g.actual.revenue_uzs,
                                                      total_cost_uzs=g.actual.total_cost_uzs * 0.9)})
    with pytest.raises(CalibrationRequired) as e:
        run_optimize(bad, scen_cfg=SC, opt_cfg=OC)
    assert not e.value.calibration.calibrated
    r = run_optimize(bad, scen_cfg=SC, opt_cfg=OC, demo_mode=True)
    assert any("NOT calibrated" in w for w in r.warnings) and r.plan is not None


def test_unconfirmed_route_never_in_plan(l0_result):
    assert "R-ROAD-X" not in l0_result.plan.packs_by_route()


def test_what_if_after_shock_is_fast(l0_result):
    w = run_what_if(l0_result, {"fx_shift": 0.15})
    assert w.status in {"OPTIMAL", "FEASIBLE"}


def test_impossible_service_reported_with_reason():
    r = run_optimize(build_l0_graph(), scen_cfg=SC,
                     opt_cfg=OptimizerConfig(risk_mode="expected", service_level=0.98, budget_uzs=2_000_000_000.0))
    assert r.plan is None and r.impossible is not None and not r.impossible.feasible_as_stated
    assert any("budget" in rs or "service" in rs for rs in r.impossible.relax_sets)
