"""Judge-facing acceptance: prepared synthetic L0 company -> twin -> plan -> FX shock.

Unlike the empty-page browser smoke and isolated engine fixtures, this checks
the actual documented preparation command and the same graph-id HTTP API
path that the Russian seven-step UI uses. It never reads simulation truth
through product APIs and never asserts commercial uplift.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lagrange_rx.api.app import create_app


ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture(scope="module")
def live_demo(tmp_path_factory):
    data = tmp_path_factory.mktemp("judge-demo-data")
    env = {**os.environ, "LAGRANGE_AI_MODE": "off"}
    env.pop("LAGRANGE_AUTH_PASSWORD", None)
    command = [
        sys.executable, str(ROOT / "scripts" / "prepare_demo.py"),
        "--data-dir", str(data),
        "--source", str(ROOT / "sim" / "worlds" / "L0" / "documents"),
    ]
    completed = subprocess.run(
        command, cwd=ROOT, env=env,
        capture_output=True, text=True, timeout=240, check=False,
    )
    assert completed.returncode == 0, (
        f"Demo preparation failed: stdout={completed.stdout[-2000:]} stderr={completed.stderr[-4000:]}"
    )
    try:
        prepared = json.loads(completed.stdout)
    except json.JSONDecodeError as exc:
        pytest.fail(f"Preparation output is not machine-readable: {completed.stdout[-1500:]} ({exc})")
    graph_id = prepared["graph_id"]
    assert graph_id.startswith("demo")
    assert prepared["source"] == "SYNTHETIC L0 only"
    assert prepared["sales_records"] > 0
    assert (data / "graphs" / f"{graph_id}.json").is_file()

    with TestClient(create_app(data, samples_dir=ROOT / "sim" / "worlds", eval_dir=ROOT / "eval")) as client:
        yield prepared, graph_id, client


def test_presentation_first_screen_and_prepared_company(live_demo):
    prepared, graph_id, client = live_demo
    assert client.get("/health").status_code == 200
    home = client.get("/")
    assert home.status_code == 200 and "Документы клиента" in home.text
    assert "План" in home.text and "Шок" in home.text
    graphs = client.get("/ontology/graphs")
    assert graphs.status_code == 200 and graph_id in graphs.json()["graph_ids"]
    samples = client.get("/ingest/samples").json()["samples"]
    assert any(sample["name"] == "L0" and sample["files"] >= 60 for sample in samples)
    assert prepared["documents"] >= 60


def test_presentation_calibration_plan_and_fx_shock(live_demo):
    _, graph_id, client = live_demo
    ref = {"graph_id": graph_id}
    twin = client.post("/engine/twin", json=ref)
    assert twin.status_code == 200, twin.text[:1500]
    calibration = twin.json()["calibration"]
    assert calibration["calibrated"] is True, calibration
    assert calibration["gap_pp"] is not None and calibration["gap_pp"] <= 0.1, calibration

    plan_resp = client.post(
        "/engine/optimize",
        json={**ref, "risk_mode": "expected", "n": 100, "k": 12,
              "service_level": 0.90, "time_limit_s": 20},
    )
    assert plan_resp.status_code == 200, plan_resp.text[:2000]
    result = plan_resp.json()
    assert result["supply_status"] == "ready", result.get("impossible")
    assert result["planned_packs"] > 0, result
    assert result["plan"] is not None and result["plan"]["lines"], result
    assert result["legal_status"] == "passed" and result["checks_failed"] == 0, result

    shock = client.post(
        "/engine/what-if",
        json={"plan_id": result["id"], "change": {"fx_shift": 0.15}},
    )
    assert shock.status_code == 200, shock.text[:2000]
    changed = shock.json()
    assert changed["text"], changed
    assert changed.get("new_expected_uzs") is not None, changed
    assert changed.get("delta_expected_uzs") is not None, changed
