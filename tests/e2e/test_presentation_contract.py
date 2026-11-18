"""Static UI checks; real Chromium smoke is separate (scripts/browser_smoke.py)."""
from pathlib import Path

WEB = Path(__file__).resolve().parents[2] / 'web'


def test_service_input_and_provenance_are_visible():
    html = (WEB / 'index.html').read_text(encoding='utf-8')
    javascript = (WEB / 'app.js').read_text(encoding='utf-8')
    assert 'id="p-service"' in html
    assert 'DEMO / SYNTHETIC' in html
    assert 'service_level: service' in javascript
    assert 'legal_status' in javascript
    assert 'presentation-mode' in javascript
    assert 'id="main-content"' in html
