"""Synthetic L0 judge graph is discoverable even without a populated data volume."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from lagrange_rx.api.app import create_app
from lagrange_rx.api import store as graph_store


def test_bundled_graph_is_visible_and_readable(tmp_path, monkeypatch):
    from lagrange_rx.fixtures.l0 import build_l0_graph
    bundled = tmp_path / "release" / "demo" / "graphs"
    bundled.mkdir(parents=True)
    graph = build_l0_graph()
    path = bundled / "demo1234567890abcdef.json"
    path.write_text(json.dumps(graph.model_dump(mode="json", exclude_computed_fields=True)), encoding="utf-8")
    monkeypatch.setattr(graph_store, "_BUNDLED_DEMO_GRAPHS", bundled)
    with TestClient(create_app(tmp_path / "empty-state", serve_web=False)) as client:
        ids = client.get("/ontology/graphs").json()["graph_ids"]
        assert ids == [path.stem]
        response = client.get(f"/ontology/graphs/{path.stem}")
        assert response.status_code == 200, response.text
        assert response.json()["client_id"] == graph.client_id


def test_user_graph_takes_precedence_and_demo_fallback_is_restricted(tmp_path, monkeypatch):
    from lagrange_rx.fixtures.l0 import build_l0_graph
    bundled = tmp_path / "bundle" / "demo" / "graphs"
    persisted = tmp_path / "state" / "graphs"
    bundled.mkdir(parents=True)
    persisted.mkdir(parents=True)
    monkeypatch.setattr(graph_store, "_BUNDLED_DEMO_GRAPHS", bundled)
    graph = build_l0_graph()
    payload = graph.model_dump(mode="json", exclude_computed_fields=True)
    (bundled / "demoabc.json").write_text(json.dumps(payload), encoding="utf-8")
    saved = graph.model_copy(update={"client_id": "persisted-client"})
    (persisted / "demoabc.json").write_text(
        json.dumps(saved.model_dump(mode="json", exclude_computed_fields=True)), encoding="utf-8"
    )
    store = graph_store.GraphStore(tmp_path / "state")
    assert store.ids() == ["demoabc"]
    assert store.get("demoabc").client_id == "persisted-client"
    assert store.get("../demoabc") is None
    assert store.get("random") is None
