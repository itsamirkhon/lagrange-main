"""API password protection (HTTP Basic) and the non-loopback guard."""
import base64

from fastapi.testclient import TestClient

from lagrange_rx import cli
from lagrange_rx.api.app import create_app


def basic(user, password):
    return {'Authorization': 'Basic ' + base64.b64encode(f'{user}:{password}'.encode()).decode()}


def test_no_password_means_open_app(tmp_path):
    c = TestClient(create_app(tmp_path, serve_web=False))
    assert c.get('/ontology/rules').status_code == 200


def test_password_protects_api_and_ui_but_not_health(tmp_path):
    web = tmp_path / 'ui'; web.mkdir(); (web / 'index.html').write_text('<h1>ok</h1>')
    c = TestClient(create_app(tmp_path / 'd', web_dir=web, auth_password='s3cret'))
    assert c.get('/health').status_code == 200
    for path in ('/', '/ontology/rules', '/engine/plans/x', '/docs'):
        r = c.get(path)
        assert r.status_code == 401 and 'Basic' in r.headers['www-authenticate'], path
    assert c.get('/ontology/rules', headers=basic('lagrange', 'wrong')).status_code == 401
    assert c.get('/ontology/rules', headers=basic('lagrange', 's3cret')).status_code == 200
    assert c.get('/', headers=basic('lagrange', 's3cret')).status_code == 200


def test_password_comes_from_environment(tmp_path, monkeypatch):
    monkeypatch.setenv('LAGRANGE_AUTH_PASSWORD', 'envpw')
    c = TestClient(create_app(tmp_path, serve_web=False))
    assert c.get('/ontology/rules').status_code == 401
    assert c.get('/ontology/rules', headers=basic('lagrange', 'envpw')).status_code == 200


def test_non_loopback_requires_password_even_with_flag(capsys, monkeypatch):
    monkeypatch.delenv('LAGRANGE_AUTH_PASSWORD', raising=False)
    assert cli.main(['serve', '--host', '0.0.0.0', '--allow-non-loopback']) == 2
    assert 'LAGRANGE_AUTH_PASSWORD' in capsys.readouterr().err


def test_non_loopback_allowed_with_password_and_flag(monkeypatch, tmp_path):
    import uvicorn
    seen = {}
    monkeypatch.setenv('LAGRANGE_AUTH_PASSWORD', 'pw')
    monkeypatch.setattr(uvicorn, 'run', lambda app, host, port: seen.update(host=host))
    assert cli.main(['serve', '--host', '0.0.0.0', '--allow-non-loopback', '--data-dir', str(tmp_path)]) == 0
    assert seen['host'] == '0.0.0.0'
