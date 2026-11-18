from fastapi.testclient import TestClient

from lagrange_rx import cli
from lagrange_rx.api.app import create_app


def test_loopback_detection():
    assert cli.is_loopback('127.0.0.1') and cli.is_loopback('::1') and cli.is_loopback('localhost')
    assert not cli.is_loopback('0.0.0.0') and not cli.is_loopback('192.168.1.5') and not cli.is_loopback('example.com')


def test_serve_refuses_non_loopback_without_flag(capsys):
    assert cli.main(['serve', '--host', '0.0.0.0']) == 2
    assert 'no authentication' in capsys.readouterr().err


def test_serve_default_host_is_loopback():
    assert cli.build_parser().parse_args(['serve']).host == '127.0.0.1'


def test_serve_runs_uvicorn_on_loopback(monkeypatch, tmp_path):
    import uvicorn
    seen = {}
    monkeypatch.setattr(uvicorn, 'run', lambda app, host, port: seen.update(host=host, port=port))
    assert cli.main(['serve', '--data-dir', str(tmp_path)]) == 0
    assert seen == {'host': '127.0.0.1', 'port': 8000}


def test_static_ui_mounted_when_web_dir_exists(tmp_path):
    web = tmp_path / 'ui'
    web.mkdir()
    (web / 'index.html').write_text('<h1>ui</h1>')
    c = TestClient(create_app(tmp_path / 'd', web_dir=web))
    assert c.get('/').text == '<h1>ui</h1>' and c.get('/health').json()['status'] == 'ok'
    assert TestClient(create_app(tmp_path / 'd2', serve_web=False)).get('/').status_code == 404


def test_ontology_validate_rejects_bad_graph(tmp_path):
    c = TestClient(create_app(tmp_path, serve_web=False))
    r = c.post('/ontology/validate', json={'products': [{'nonsense': 1}]}).json()
    assert r['valid'] is False and r['errors']
    assert c.post('/ontology/validate', json={}).json()['ready_for_calculation'] is True
