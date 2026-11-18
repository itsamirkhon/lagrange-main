"""API contract, including invalid caller attempts to disable a supply requirement."""
import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from lagrange_rx.api.app import create_app
from lagrange_rx.api.engine import OptimizeRequest
from lagrange_rx.engine.decision_policy import DEFAULT_SERVICE_LEVEL


def test_optimize_api_rejects_null_zero_and_negative_service_level():
    assert OptimizeRequest().service_level == DEFAULT_SERVICE_LEVEL
    for invalid in (None, 0, -0.1, 1.1):
        with pytest.raises(ValidationError):
            OptimizeRequest(service_level=invalid)


def test_solver_api_requires_explicit_business_service_and_reports_outcome(tmp_path):
    from lagrange_rx.fixtures.l0 import build_l0_graph, build_l0_config
    from lagrange_rx.engine.adapter import to_calculation_graph

    graph = to_calculation_graph(build_l0_graph(), build_l0_config()).model_dump(mode='json')
    with TestClient(create_app(tmp_path / 'data', serve_web=False)) as client:
        assert client.post('/graphs', json=graph).status_code == 200
        gid = graph['client_id']
        req = {'graph_id': gid, 'n': 100, 'k': 12, 'risk_mode': 'cvar'}
        resp = client.post('/engine/optimize', json=req)
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body['service_policy']['minimum_expected_fill_rate'] == DEFAULT_SERVICE_LEVEL
        if body['supply_status'] == 'ready':
            assert body['planned_packs'] > 0
            assert body['legal_status'] in ('passed', 'failed')
            assert body['legal'] is not None
        else:
            # If 90% supply is impossible, the response must say so without inventing legal success.
            assert body['supply_status'] == 'infeasible'
            assert body['plan'] is None
            assert body['legal_status'] == 'not_evaluated'
            assert body['legal'] is None
        assert client.post('/engine/optimize', json={**req, 'service_level': None}).status_code == 422
        assert client.post('/engine/optimize', json={**req, 'service_level': 0}).status_code == 422


def test_concurrent_solver_request_rejected_with_429(tmp_path):
    """A busy judge session must not open another unbounded CPU job."""
    from lagrange_rx.api.engine import _solver_slot

    with TestClient(create_app(tmp_path / 'data', serve_web=False)) as client:
        assert _solver_slot.acquire(blocking=False)
        try:
            # Registering a valid graph still must succeed; only optimization is gated.
            from lagrange_rx.fixtures.l0 import build_l0_graph, build_l0_config
            from lagrange_rx.engine.adapter import to_calculation_graph
            graph = to_calculation_graph(build_l0_graph(), build_l0_config()).model_dump(mode='json')
            client.post('/graphs', json=graph)
            response = client.post('/engine/optimize', json={
                'graph_id': graph['client_id'], 'n': 100, 'k': 12,
            })
            assert response.status_code == 429
        finally:
            _solver_slot.release()
