import numpy as np
import pytest

from lagrange_rx.engine.scenarios import ScenarioConfig, generate_scenarios, kmedoids


def test_kmedoids_recovers_clusters():
    rng = np.random.default_rng(0)
    pts = np.vstack([rng.normal(c, 0.05, (30, 2)) for c in (0, 5, 10)])
    d = np.linalg.norm(pts[:, None] - pts[None], axis=2)
    med, labels = kmedoids(d, 3, np.random.default_rng(1))
    assert len(set(med)) == 3
    assert [len(set(labels[i * 30:(i + 1) * 30])) for i in range(3)] == [1, 1, 1]
    assert (labels[med] == np.arange(3)).all()


def test_kmedoids_k_ge_n():
    d = np.zeros((4, 4))
    med, labels = kmedoids(d, 9, np.random.default_rng(0))
    assert list(med) == [0, 1, 2, 3] and list(labels) == [0, 1, 2, 3]


def test_distance_report(graph, fx, rules):
    r = generate_scenarios(graph, fx, rules, ScenarioConfig(n=500, k=40)).distance_report
    for key in ("mean_dist_to_medoid", "max_dist_to_medoid", "gap_demand_mean", "gap_fx_mean",
                "gap_freight_mean", "gap_cut_frequency", "gap_supplier_failure_frequency"):
        assert key in r
    assert set(r["gap_fx_mean"]) == {"USD", "EUR", "RUB"} and set(r["gap_freight_mean"]) == {"sea", "air", "rail", "road"}
    assert 0 <= r["mean_dist_to_medoid"] <= r["max_dist_to_medoid"]
    assert r["gap_demand_mean"] < 0.1


def test_more_medoids_shrinks_distance(graph, fx, rules):
    a = generate_scenarios(graph, fx, rules, ScenarioConfig(n=500, k=10)).distance_report
    b = generate_scenarios(graph, fx, rules, ScenarioConfig(n=500, k=60)).distance_report
    assert b["mean_dist_to_medoid"] < a["mean_dist_to_medoid"]
