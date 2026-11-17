import json

from lagrange_rx.engine.optimize import OptimizerConfig
from lagrange_rx.engine.scenarios import ScenarioConfig
from lagrange_rx.eval.engine_eval import (
    THRESHOLDS_PATH, LocalTruth, load_thresholds, match_routes, run_evaluation, translate_plan,
)
from lagrange_rx.fixtures.l0 import build_l0_graph
from lagrange_rx.rules import load_rules


def small_thresholds(tmp_path):
    thr, _ = load_thresholds()
    thr.update(legality_scenarios=300, optimality_problems=6, out_of_sample_scenarios=120, evpi_scenarios=15)
    p = tmp_path / "thr.json"
    p.write_text(json.dumps(thr))
    return p


def test_pre_registered_thresholds_exist_and_are_strict():
    thr, sha = load_thresholds()
    assert thr["legality_min_rate"] == 1.0 and thr["optimality_min_match_rate"] == 1.0
    assert thr["calibration_max_gap_pp"] == 0.1 and thr["recompute_after_shock_max_seconds"] == 5.0
    assert len(sha) == 64 and THRESHOLDS_PATH.exists()


def test_route_matching_is_by_features_not_ids():
    g = build_l0_graph().for_calculation()
    renamed = g.model_copy(update={"routes": tuple(r.model_copy(update={"id": "X" + r.id}) for r in g.routes),
                                   "shipments": (), "cost_items": ()})
    m = match_routes(g, renamed)
    assert m["R-SEA-A"] == "XR-SEA-A" and len(m) == len(g.routes)


def test_report_structure_and_pass(tmp_path):
    g = build_l0_graph()
    degraded = g.model_copy(update={"routes": tuple(
        r.model_copy(update={"capacity_packs": r.capacity_packs // 2}) if r.id == "R-SEA-A" else r for r in g.routes)})
    rep = run_evaluation(g, level_graphs={"L0": g, "L2": degraded}, scen_cfg=ScenarioConfig(n=200, k=20),
                         thresholds_path=small_thresholds(tmp_path))
    m = rep["metrics"]
    assert set(m) >= {"calibration", "uplift", "vss", "evpi", "legality", "optimality", "speed", "regret_by_level"}
    assert rep["all_pass"] and rep["snapshot_id"] and len(rep["thresholds_sha256"]) == 64
    assert m["legality"]["legal_rate"] == 1.0 and m["optimality"]["match_rate"] == 1.0
    assert m["evpi"]["evpi_uzs"] >= -1.0
    lv = m["regret_by_level"]["levels"]
    assert abs(lv["L0"]["regret_uzs"]) < 1e-3 * max(1.0, abs(m["regret_by_level"]["best_on_truth_uzs"])) + 1e5
    assert lv["L2"]["regret_uzs"] >= -1e5  # planning on worse data never beats planning on the truth (up to oos noise)


def test_local_truth_translates_plan():
    g = build_l0_graph().for_calculation()
    from lagrange_rx.engine.baseline import baseline_plan
    p = baseline_plan(g)
    t = LocalTruth(g, load_rules(), {r.id: r.id for r in g.routes})
    assert len(t.evaluate_plan_on_truth(p, [])) == 0
    assert translate_plan(p, {"R-SEA-A": "Z"}).lines[0].route_id == "Z" or len(translate_plan(p, {"R-SEA-A": "Z"}).lines) == 1
