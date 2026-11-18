"""End-to-end HTTP flow on the ONE app (recorded / no-network mode):

upload zip of L0 documents -> question queue -> answers -> commit -> twin -> optimize
-> what-if (FX +15%, ceiling -37%, supplier unavailable) -> "impossible, and why".

Ingest half: runs on the small fixtures (AI off, no network).
Engine half: the engine still consumes its own graph type, so the engine-side graph is built with the
public fixture builder and registered via POST /graphs. The ONLY place that changes when the engine
moves to ontology.ClientGraph is api/seam.py::graph_for_engine (see the xfail below).
"""
import io
import zipfile

import pytest
from fastapi.testclient import TestClient

from lagrange_rx.api.app import create_app
from lagrange_rx.fixtures.l0 import build_l0_graph

FAST = {'n': 200, 'k': 20}


@pytest.fixture(scope='module')
def client(tmp_path_factory):
    mp = pytest.MonkeyPatch()
    mp.setenv('LAGRANGE_AI_MODE', 'off')  # recorded/no-network
    yield TestClient(create_app(tmp_path_factory.mktemp('data'), serve_web=False))
    mp.undo()


@pytest.fixture(scope='module')
def engine_graph():
    return build_l0_graph().model_dump(mode='json')


@pytest.fixture(scope='module')
def committed(client):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, 'w') as z:
        z.writestr('sales.csv', 'Месяц,Количество\n2026-01,10\n2026-02,11\n2026-03,12\n')
        z.writestr('costs.csv', 'Месяц,Статья,Сумма UZS,Номенклатура\n2026-01,distribution,1200,Drug A\n')
    r = client.post('/ingest/batches', files=[('files', ('l0_documents.zip', buf.getvalue(), 'application/zip'))])
    assert r.status_code == 200
    batch_id = r.json()['batch_id']
    queue = client.get(f'/ingest/batches/{batch_id}/review').json()
    return {'batch_id': batch_id, 'queue': queue}


def test_health_and_rules(client):
    assert client.get('/health').json()['status'] == 'ok'
    rules = client.get('/ontology/rules').json()['rules']
    assert any(r['rule_id'] == 'TRUST-NO-UNCONFIRMED' for r in rules)


def test_upload_zip_builds_question_queue(client, committed):
    batch = client.get('/ingest/batches/' + committed['batch_id']).json()
    assert len(batch['documents']) == 2
    assert committed['queue'] and all(q['status'] == 'pending' for q in committed['queue'])
    assert all(i['status'] == 'extracted_unconfirmed' for i in batch['proposed_instances'])


def test_answers_then_commit_registers_graph(client, committed):
    for q in committed['queue']:
        assert client.post('/ingest/review/' + q['question_id'], json={'action': 'confirm'}).status_code == 200
    r = client.post(f"/ingest/batches/{committed['batch_id']}/commit")
    assert r.status_code == 200 and r.json()['target'] == 'ClientGraph' and r.json()['snapshot_id']
    graph = client.get('/ontology/graphs/' + committed['batch_id'])
    assert graph.status_code == 200
    assert committed['batch_id'] in client.get('/ontology/graphs').json()['graph_ids']
    assert client.post('/ontology/validate', json=graph.json()).json()['ready_for_calculation'] is True
    assert client.get('/ontology/graphs/doesnotexist').status_code == 404


def test_committed_graph_reaches_engine_by_graph_id(client, committed):
    """Committed graph is resolvable by /engine/* (found; not-usable is a precise 422, never a 404)."""
    r = client.post('/engine/twin', json={'graph_id': committed['batch_id']})
    assert r.status_code == 422 and r.json()['detail']['error'] == 'graph_not_usable_by_engine'


@pytest.mark.xfail(strict=True, reason='needs engine stage: engine still on engine._legacy_ontology.ClientGraph; '
                                       'api/seam.py::graph_for_engine has no ontology->engine adapter yet')
def test_committed_graph_twin_via_ontology_graph(client, committed):
    assert client.post('/engine/twin', json={'graph_id': committed['batch_id']}).status_code == 200


@pytest.mark.xfail(strict=True, reason='needs ingest stage: committed graph from the small fixture is empty '
                                       '(SalesRecord/CostItem rows miss product/region/channel/price/layer/driver), '
                                       'so there is no Product/Route/Supplier to optimise')
def test_fixture_documents_commit_nonempty_graph(client, committed):
    r = client.post(f"/ingest/batches/{committed['batch_id']}/commit").json()
    assert r['committed'] > 0 and not r['incomplete']


def test_twin_optimize_whatif_impossible(client, engine_graph):
    assert client.post('/graphs', json=engine_graph).json() == {'graph_id': 'L0-demo-client'}
    gid = {'graph_id': 'L0-demo-client'}

    twin = client.post('/engine/twin', json=gid).json()
    assert twin['calibration']['calibrated'] and twin['unit_economics']['routes']
    assert client.post('/engine/scenarios', json={**gid, **FAST}).json()['full_count'] == 200

    opt = client.post('/engine/optimize', json={**gid, **FAST})
    assert opt.status_code == 200
    plan = opt.json()
    assert plan['legal'] and plan['checks_failed'] == 0 and plan['plan']['snapshot_id']
    assert client.get('/engine/plans/' + plan['id']).json()['plan'] == plan['plan']

    route_id = engine_graph['routes'][0]['id']
    changes = [{'fx_shift': 0.15}, {'ceiling_cut': 0.37}]  # FX +15%, ceiling -37% (cut depth)
    changes.append({'route_unavailable': route_id})
    for change in changes:
        w = client.post('/engine/what-if', json={'plan_id': plan['id'], 'change': change})
        assert w.status_code == 200, (change, w.text)
        assert 'text' in w.json()

    imp = client.post('/engine/optimize', json={**gid, 'service_level': 0.98, 'budget_uzs': 2e9,
                                                'risk_mode': 'expected', **FAST}).json()
    assert imp['plan'] is None and imp['impossible']['relax_sets'] and imp['legal'] is True


def test_unknown_routes_and_errors(client):
    assert client.post('/engine/optimize', json={'graph_id': 'nope'}).status_code == 404
    assert client.get('/engine/plans/nope').status_code == 404
    assert client.get('/ontology/graphs/..%2Fx').status_code == 404
