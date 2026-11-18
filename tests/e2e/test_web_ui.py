"""Stage 9: demo web UI. New endpoints (/ingest/samples, /evidence/reports), static serving, and a smoke test that
the UI only calls endpoints that exist in the OpenAPI schema."""
import io
import json
import re
import zipfile
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from lagrange_rx.api.app import create_app

WEB = Path(__file__).resolve().parents[2] / 'web'


def client(tmp_path, **kw):
    return TestClient(create_app(tmp_path / 'data', **kw))


def test_samples_unconfigured_is_explicit(tmp_path, monkeypatch):
    monkeypatch.delenv('LAGRANGE_SAMPLES_DIR', raising=False)
    body = client(tmp_path, serve_web=False).get('/ingest/samples').json()
    assert body == {'configured': False, 'directory_exists': False, 'samples': []}


def test_samples_list_and_archive_feed_ingest(tmp_path):
    samples = tmp_path / 'samples'
    (samples / 'L0' / 'documents').mkdir(parents=True)
    (samples / 'L0' / 'documents' / 'sales.csv').write_text('Месяц,Количество\n2026-01,10\n', encoding='utf-8')
    (samples / 'L1').mkdir()
    (samples / 'L1' / 'a.csv').write_text('x,y\n1,2\n', encoding='utf-8')
    (samples / 'empty').mkdir()
    c = client(tmp_path, serve_web=False, samples_dir=samples)
    body = c.get('/ingest/samples').json()
    assert body['configured'] and [s['name'] for s in body['samples']] == ['L0', 'L1']
    assert body['samples'][0]['files'] == 1 and body['samples'][0]['bytes'] > 0
    z = c.get('/ingest/samples/L0/archive')
    assert z.status_code == 200 and zipfile.ZipFile(io.BytesIO(z.content)).namelist() == ['sales.csv']
    assert c.get('/ingest/samples/..%2F/archive').status_code == 404
    assert c.get('/ingest/samples/empty/archive').status_code == 404
    r = c.post('/ingest/batches', files=[('files', ('L0.zip', z.content, 'application/zip'))])
    assert r.status_code == 200


def test_evidence_reports_serve_eval_json_with_snapshot_id(tmp_path):
    ev = tmp_path / 'ev'
    ev.mkdir()
    (ev / 'a.json').write_text(json.dumps({'snapshot_id': 'abc', 'metrics': {'x': 1}}))
    (ev / 'b.json').write_text(json.dumps({'ingest_snapshot_id': 'def'}))
    (ev / 'c.json').write_text(json.dumps({'no': 'snapshot'}))
    (ev / 'bad.json').write_text('{')
    (ev / 'note.txt').write_text('ignored')
    c = client(tmp_path, serve_web=False, eval_dir=ev)
    body = c.get('/evidence/reports').json()
    by = {r['name']: r for r in body['reports']}
    assert set(by) == {'a', 'b', 'c', 'bad'}
    assert by['a']['snapshot_id'] == 'abc' and by['a']['data']['metrics'] == {'x': 1}
    assert by['b']['snapshot_id'] == 'def' and by['c']['snapshot_id'] is None
    assert 'invalid JSON' in by['bad']['skipped'] and 'data' not in by['bad']
    assert c.get('/evidence/reports/a').json()['snapshot_id'] == 'abc'
    assert c.get('/evidence/reports/nope').status_code == 404
    assert c.get('/evidence/reports/..%2Fa').status_code == 404


def test_evidence_missing_dir_is_explicit(tmp_path):
    body = client(tmp_path, serve_web=False, eval_dir=tmp_path / 'none').get('/evidence/reports').json()
    assert body == {'directory_exists': False, 'reports': []}


def test_repo_eval_reports_are_exposed(tmp_path):
    body = client(tmp_path, serve_web=False).get('/evidence/reports').json()
    names = {r['name'] for r in body['reports']}
    assert {'e2e_report_L0', 'engine_report'} <= names
    assert 'ingest_graph_L0' not in {r['name'] for r in body['reports'] if 'data' in r}  # 2 MB graph dump is not a report


def test_ui_served_at_root(tmp_path):
    c = client(tmp_path)
    r = c.get('/')
    assert r.status_code == 200 and 'LAGRANGE' in r.text
    assert c.get('/app.js').status_code == 200 and c.get('/styles.css').status_code == 200
    assert c.get('/health').json()['status'] == 'ok'  # API routes win over the static mount


def _ui_paths():
    src = '\n'.join(p.read_text(encoding='utf-8') for p in WEB.glob('*.js')) + (WEB / 'index.html').read_text(encoding='utf-8')
    found = set(re.findall(r"""(?:fetch|api)\(\s*[`'"](/[^`'"?]*)""", src))
    return {re.sub(r'\$\{[^}]*\}', '{}', p) for p in found}


def test_ui_calls_only_existing_endpoints(tmp_path):
    schema = client(tmp_path, serve_web=False).get('/openapi.json').json()['paths']
    patterns = [re.compile('^' + re.sub(r'\\\{[^}]*\\\}', '[^/]+', re.escape(p)) + '$') for p in schema]
    paths = _ui_paths()
    assert len(paths) >= 12, paths
    missing = sorted(p for p in paths if not any(rx.match(p.replace('{}', 'x')) for rx in patterns))
    assert not missing, f'UI calls endpoints that do not exist: {missing}'


def test_ui_has_seven_screens_and_no_inline_numbers():
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    for title in ('Документы клиента', 'Что мы поняли', 'Проблемы', 'Двойник', 'План', 'Шок', 'Доказательства'):
        assert title in html
    # no hard-coded numbers in markup text nodes (digits only inside attributes / ids / the screen numbering)
    text = re.sub(r'<[^>]+>', ' ', re.sub(r'<(script|style)[\s\S]*?</\1>', '', html))
    assert not re.findall(r'\d+[.,]?\d*\s*(%|UZS|сум|шт)', text)
