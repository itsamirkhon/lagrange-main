from copy import deepcopy

from fastapi.testclient import TestClient

from lagrange_rx.api import app
from lagrange_rx.demo_data import SYNTHETIC_CASE

client = TestClient(app)


def test_health_case_and_openapi_contract():
    response = client.get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["data"] == "synthetic"
    case = client.get("/api/v1/demo-case")
    assert case.status_code == 200
    assert case.json()["annual_demand_packs"] == 150_000
    schema = client.get("/openapi.json").json()
    assert "/api/v1/optimize" in schema["paths"]
    assert "/api/v1/demo-case" in schema["paths"]
    assert "OptimizeResponse" in schema["components"]["schemas"]


def test_optimize_post_with_defaults():
    response = client.post("/api/v1/optimize", json={})
    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "optimal"
    assert body["optimized"]["allocations"]
    assert body["data_classification"] == "synthetic"
    assert body["relative_optimality_gap"] < 0.002


def test_custom_case_and_infeasible_status():
    raw = deepcopy(SYNTHETIC_CASE)
    for o in raw["options"]:
        o["eligible"] = False
    response = client.post("/api/v1/optimize", json={"risk_aversion": 0, "case": raw})
    assert response.status_code == 200
    assert response.json()["status"] == "infeasible"
    assert response.json()["optimized"] is None


def test_untrusted_invalid_inputs_are_422():
    assert client.post("/api/v1/optimize", json={"risk_aversion": 1.2}).status_code == 422
    assert client.post("/api/v1/optimize", json={"not_a_real_parameter": 1}).status_code == 422
    malicious = deepcopy(SYNTHETIC_CASE)
    malicious["options"][0]["name"] = "A" * 5_000
    assert client.post("/api/v1/optimize", json={"case": malicious}).status_code == 422
    malicious = deepcopy(SYNTHETIC_CASE)
    malicious["scenarios"][0]["probability"] = -1
    assert client.post("/api/v1/optimize", json={"case": malicious}).status_code == 422


def test_cost_plus_experiment_has_computed_not_hardcoded_values():
    baseline = client.get('/api/v1/experiments/cost-plus?discount_pct=0')
    assert baseline.status_code == 200
    assert baseline.json()['profit_delta_uzs'] == 0
    changed = client.get('/api/v1/experiments/cost-plus?discount_pct=5')
    assert changed.status_code == 200
    assert changed.json()['after']['factory_cost_uzs'] < changed.json()['before']['factory_cost_uzs']
    assert changed.json()['profit_delta_uzs'] < 0
    assert changed.json()['data_classification'] == 'synthetic'
    assert client.get('/api/v1/experiments/cost-plus?discount_pct=30').status_code == 422
