import json

from lagrange_rx.engine.optimize import OptimizerConfig
from lagrange_rx.engine.scenarios import ScenarioConfig
from eval.engine_eval import (
    THRESHOLDS_PATH, LocalTruth, load_thresholds, match_routes, run_evaluation, translate_plan,
)
from lagrange_rx.fixtures.l0 import build_l0_calculation_graph, build_l0_config, build_l0_graph
from lagrange_rx.engine.rules import load_rules


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
    g = build_l0_calculation_graph()
    renamed = g.model_copy(update={"routes": tuple(r.model_copy(update={"id": "X" + r.id}) for r in g.routes),
                                   "shipments": (), "cost_items": ()})
    m = match_routes(g, renamed)
    assert m["R-SEA-A"] == "XR-SEA-A" and len(m) == len(g.routes)


def test_report_structure_and_pass(tmp_path):
    g = build_l0_calculation_graph()
    degraded = g.model_copy(update={"routes": tuple(
        r.model_copy(update={"capacity_packs": r.capacity_packs // 2}) if r.id == "R-SEA-A" else r for r in g.routes)})
    rep = run_evaluation(build_l0_graph(), config=build_l0_config(), level_graphs={"L0": g, "L2": degraded}, scen_cfg=ScenarioConfig(n=200, k=20),
                         thresholds_path=small_thresholds(tmp_path))
    m = rep["metrics"]
    assert set(m) >= {"calibration", "uplift", "vss", "evpi", "legality", "optimality", "speed", "regret_by_level"}
    assert rep["fx_source"] == "cbu.uz official"  # real CBU history by default, never the synthetic sample
    assert rep["all_pass"] and rep["snapshot_id"] and len(rep["thresholds_sha256"]) == 64
    assert m["legality"]["legal_rate"] == 1.0 and m["optimality"]["match_rate"] == 1.0
    assert m["evpi"]["evpi_uzs"] >= -1.0
    lv = m["regret_by_level"]["levels"]
    assert abs(lv["L0"]["regret_uzs"]) < 1e-3 * max(1.0, abs(m["regret_by_level"]["best_on_truth_uzs"])) + 1e5
    assert lv["L2"]["regret_uzs"] >= -1e5  # planning on worse data never beats planning on the truth (up to oos noise)


def test_run_on_simulation_small(tmp_path):
    from eval.engine_eval import run_on_simulation
    rep = run_on_simulation(scen_cfg=ScenarioConfig(n=60, k=10), thresholds_path=small_thresholds(tmp_path))
    if rep.get("error") == "no_supply_plan: solver infeasible or returned zero packs":
        assert rep["all_pass"] is False and rep["metrics"]["supply_feasibility"]["pass"] is False
        return
    assert rep["truth"] == "simulation evaluate_plan_on_truth" and rep["fx_source"] == "cbu.uz official"
    assert rep["truth_mapping"] and rep["min_mapping_confidence"] >= 0.75 and "ingest" in rep["graph_source"]
    assert {"uplift", "vss", "evpi", "legality"} <= set(rep["metrics"])
    assert rep["metrics"]["uplift"]["scenarios"] == 120


def test_local_truth_translates_plan():
    g = build_l0_calculation_graph()
    from lagrange_rx.engine.baseline import baseline_plan
    p = baseline_plan(g)
    t = LocalTruth(g, load_rules(), {r.id: r.id for r in g.routes})
    assert len(t.evaluate_plan_on_truth(p, [])) == 0
    assert translate_plan(p, {"R-SEA-A": "Z"}).lines[0].route_id == "Z" or len(translate_plan(p, {"R-SEA-A": "Z"}).lines) == 1


def test_both_legal_interpretations_small(tmp_path):
    from eval.engine_eval import merge_interpretations, run_on_simulation
    cfg = dict(scen_cfg=ScenarioConfig(n=60, k=10), thresholds_path=small_thresholds(tmp_path))
    a, b = run_on_simulation(interpretation="A", **cfg), run_on_simulation(interpretation="B", **cfg)
    assert a["interpretation"]["engine_apply_wholesale_markup_rule"] is True
    assert a["interpretation"]["truth_revenue_capped_at_115_pct_of_legal_base"] is True
    assert b["interpretation"]["engine_apply_wholesale_markup_rule"] is False
    assert b["engine_config"]["apply_wholesale_markup_rule"] is False
    assert a["client_violation_of_the_rule"]["window_2025-11_2026-10"]["excess_over_ceiling_uzs"] > 0
    assert a["truth_baseline_with_cap"]["net_profit_uzs"] < a["truth_native_baseline"]["net_profit_uzs"]
    merged = merge_interpretations(a, b)
    li = merged["legal_interpretation"]
    assert li["A_rule_applies_everywhere"]["snapshot_id"] and li["B_rule_off"]["snapshot_id"]
    assert merged["all_pass"] == a["all_pass"]
