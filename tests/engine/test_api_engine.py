from fastapi.testclient import TestClient

from lagrange_rx.api.engine import create_app
from lagrange_rx.fixtures.l0 import build_l0_calculation_graph
from lagrange_rx.engine.inputs import ActualSpec

client = TestClient(create_app())
GRAPH = build_l0_calculation_graph().model_dump(mode="json")
FAST = {"n": 200, "k": 20}


def test_full_flow_l0():
    assert client.post("/graphs", json=GRAPH).json() == {"graph_id": "L0-demo-client"}
    t = client.post("/engine/twin", json={"graph_id": "L0-demo-client"}).json()
    assert t["calibration"]["calibrated"] and t["unit_economics"]["routes"]
    s = client.post("/engine/scenarios", json={"graph_id": "L0-demo-client", **FAST}).json()
    assert s["full_count"] == 200 and len(s["stress"]) == 4
    r = client.post("/engine/optimize", json={"graph_id": "L0-demo-client", **FAST})
    assert r.status_code == 200
    body = r.json()
    assert body["legal"] and body["solve"]["status"] == "OPTIMAL" and body["plan"]["snapshot_id"]
    assert body["plan"]["lambda"] == 0.5 and body["checks_failed"] == 0
    g = client.get(f"/engine/plans/{body['id']}").json()
    assert g["plan"] == body["plan"]
    w = client.post("/engine/what-if", json={"plan_id": body["id"], "change": {"fx_shift": 0.15}})
    assert w.status_code == 200 and "expected profit" in w.json()["text"]


def test_same_request_same_plan():
    a = client.post("/engine/optimize", json={"graph": GRAPH, **FAST}).json()
    b = client.post("/engine/optimize", json={"graph": GRAPH, **FAST}).json()
    assert a["plan"] == b["plan"] and a["plan"]["snapshot_id"] == b["plan"]["snapshot_id"]


def test_uncalibrated_is_409_and_demo_flag_allows():
    bad = dict(GRAPH, actual={**GRAPH["actual"], "total_cost_uzs": GRAPH["actual"]["total_cost_uzs"] * 0.9})
    r = client.post("/engine/optimize", json={"graph": bad, **FAST})
    assert r.status_code == 409 and r.json()["detail"]["error"] == "twin_not_calibrated"
    ok = client.post("/engine/optimize", json={"graph": bad, "demo_mode": True, **FAST})
    assert ok.status_code == 200 and any("NOT calibrated" in w for w in ok.json()["warnings"])


def test_impossible_returns_reason():
    r = client.post("/engine/optimize", json={"graph": GRAPH, "service_level": 0.98, "budget_uzs": 2e9,
                                              "risk_mode": "expected", **FAST}).json()
    assert r["plan"] is None and r["impossible"]["relax_sets"] and r["legal"] is True


def test_errors():
    assert client.post("/engine/optimize", json={"graph_id": "nope"}).status_code == 404
    assert client.get("/engine/plans/nope").status_code == 404
    assert client.post("/engine/what-if", json={"plan_id": "nope", "change": {}}).status_code == 404
    bad = client.post("/engine/optimize", json={"graph": GRAPH, "risk_mode": "yolo"})
    assert bad.status_code == 422
