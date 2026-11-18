"""Black-box regulatory router tests (no external API calls)."""
from fastapi import FastAPI
from fastapi.testclient import TestClient
from lagrange_rx.api.regulatory import DEMO_DAY, fixture, make_router


def client():
    app = FastAPI()
    app.include_router(make_router())
    return TestClient(app)


def test_demo_cases_are_implemented_with_actual_gate():
    expected = {
        'eligible': 'eligible', 'overprice': 'blocked', 'unlicensed': 'blocked',
        'recalled': 'blocked', 'controlled': 'hold', 'cold_chain': 'blocked',
        'missing_record': 'hold', 'public_procurement': 'hold',
        'wrong_spec': 'blocked', 'bad_marking': 'blocked',
    }
    with client() as c:
        for case, decision in expected.items():
            res = c.post('/pharma/demo/compliance', json={'case': case})
            assert res.status_code == 200, res.text
            data = res.json()
            assert data['decision'] == decision, (case, data)
            assert data['synthetic'] is True and data['live_government_checks'] is False
            assert data['checks'] and sum(data['summary'].values()) == len(data['checks'])
            assert data['offer']['registration_no'].startswith('DEMO-')


def test_untrusted_snapshot_is_held_despite_valid_synthetic_fields():
    with client() as c:
        res = c.post('/pharma/compliance/evaluate', json={
            'on': DEMO_DAY.isoformat(),
            'packet': fixture('eligible').model_dump(mode='json'),
        })
        assert res.status_code == 200
        data = res.json()
        assert data['decision'] == 'hold' and data['assurance'] == 'operator_supplied_snapshots_only'
        assert data['can_auto_order'] is False


def test_catalog_discloses_no_live_integration():
    with client() as c:
        data = c.get('/pharma/compliance/sources').json()
        assert data['live_government_integrations'] is False
        assert {x['id'] for x in data['sources']} >= {'MED_REG','REF_PRICE','LICENSE','BATCH','LAW'}


def test_bad_cases_and_extra_input_rejected():
    with client() as c:
        assert c.post('/pharma/demo/compliance', json={'case': 'made_up'}).status_code == 422
        assert c.post('/pharma/compliance/evaluate', json={'on': 'bad', 'packet': {}}).status_code == 422
