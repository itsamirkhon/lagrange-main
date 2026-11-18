"""Cross the real FastAPI boundary: untrusted or incomplete evidence never reaches MILP."""
from datetime import date
from fastapi.testclient import TestClient
from lagrange_rx.api.app import create_app
from lagrange_rx.api.regulatory import fixture
from lagrange_rx.engine.adapter import to_calculation_graph
from lagrange_rx.fixtures.l0 import build_l0_graph, build_l0_config

def test_regulated_endpoint_rejects_missing_evidence_before_solver(tmp_path, monkeypatch):
    graph = to_calculation_graph(build_l0_graph(), build_l0_config()).model_dump(mode='json')
    route = graph['routes'][0]
    packet = fixture('eligible')
    offer = packet.offer.model_copy(update={
        'route_id': route['id'],
        'product_id': graph['product']['id'],
        'supplier_id': route['supplier_id'],
    })
    packet = packet.model_copy(update={'offer': offer})
    def should_not_solve(*args, **kwargs):
        raise AssertionError('Regulated optimizer ran on synthetic/unreviewed evidence')
    monkeypatch.setattr('lagrange_rx.api.engine.guarded_optimize', should_not_solve)
    with TestClient(create_app(tmp_path, serve_web=False, auth_password='')) as c:
        r = c.post('/engine/optimize/regulated', json={
            'graph': graph, 'as_of': date(2026, 11, 18).isoformat(),
            'packets': [packet.model_dump(mode='json')],
            'n': 10, 'k': 1,
        })
        assert r.status_code == 200, r.text
        body = r.json()
        assert body['plan'] is None and body['legal'] is None
        assert body['regulatory_status'] == 'hold_or_blocked'
        assert body['can_auto_order'] is False
        assert body['live_government_checks'] is False
        assert all(x['decision'] != 'eligible' for x in body['regulatory_checks'])

def test_regulated_endpoint_rejects_unknown_route(tmp_path):
    graph = to_calculation_graph(build_l0_graph(), build_l0_config()).model_dump(mode='json')
    with TestClient(create_app(tmp_path, serve_web=False, auth_password='')) as c:
        r = c.post('/engine/optimize/regulated', json={
            'graph': graph, 'as_of': '2026-11-18',
            'packets': [fixture('eligible').model_dump(mode='json')],
        })
        assert r.status_code == 422
        assert r.json()['detail']['error'] == 'regulatory_preflight_invalid'
