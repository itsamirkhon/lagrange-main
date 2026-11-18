"""The visual FEFO screen is backed by the actual released-stock allocator."""
from fastapi.testclient import TestClient

from lagrange_rx.api.app import create_app


def test_fefo_demo_uses_actual_allocator_and_never_releases_blocked_lots(tmp_path):
    with TestClient(create_app(tmp_path / "empty", serve_web=False, auth_password="")) as client:
        response = client.post("/pharma/demo/fefo", json={"requested_packs": 50})
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["synthetic"] is True
        assert data["algorithm"] == "allocate_released_fefo"
        assert data["requested_packs"] == 50
        assert data["allocated_packs"] == 50
        assert data["shortfall_packs"] == 0
        assert [(r["lot_number"], r["packs"]) for r in data["allocation"]] == [
            ("LOT-102", 20), ("LOT-104", 30)
        ]
        rejected = {r["id"]: r for r in data["lots"] if not r["eligible"]}
        assert set(rejected) == {"LOT-107", "LOT-109", "LOT-111", "LOT-115"}
        assert all(r["allocated_packs"] == 0 for r in rejected.values())
        assert "срок" in rejected["LOT-107"]["reason"]
        assert "отозвана" in rejected["LOT-109"]["reason"]


def test_fefo_shortage_and_zero_demand_never_bypass_safety(tmp_path):
    with TestClient(create_app(tmp_path / "empty", serve_web=False, auth_password="")) as client:
        more = client.post("/pharma/demo/fefo", json={"requested_packs": 200}).json()
        assert more["allocated_packs"] == 60 and more["shortfall_packs"] == 140
        assert all(x["lot_number"] in {"LOT-102", "LOT-104"} for x in more["allocation"])
        zero = client.post("/pharma/demo/fefo", json={"requested_packs": 0}).json()
        assert zero["allocated_packs"] == 0 and zero["shortfall_packs"] == 0
        assert not zero["allocation"]


def test_fefo_bad_input_is_rejected_and_screen_is_served(tmp_path):
    with TestClient(create_app(tmp_path / "empty", auth_password="")) as client:
        for body in ({"requested_packs": -1}, {"requested_packs": 5001},
                     {"requested_packs": 1.5}, {"requested_packs": "NaN"}):
            assert client.post("/pharma/demo/fefo", json=body).status_code == 422
        home = client.get("/")
        assert home.status_code == 200
        assert 'data-screen="fefo"' in home.text
        assert 'id="fefo-demand"' in home.text
        js = client.get("/app.js").text
        assert 'api(\'/pharma/demo/fefo\'' in js
