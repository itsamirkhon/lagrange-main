#!/usr/bin/env python3
"""Real Chromium smoke across desktop/mobile, keyboard and screen controls.

Requires: pip install playwright; python -m playwright install chromium
Does not claim that a simulated graph is a production dataset.
"""
from __future__ import annotations
import argparse
import socket
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from lagrange_rx.api.app import create_app


def _port():
    with socket.socket() as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--out', default='evidence/review_browser')
    args = ap.parse_args()
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit('Missing playwright; install it and Chromium first') from exc
    import uvicorn
    port = _port()
    with tempfile.TemporaryDirectory(prefix='lagrange-browser-') as temp:
        app = create_app(Path(temp) / 'data')
        server = uvicorn.Server(uvicorn.Config(app, host='127.0.0.1', port=port, log_level='error'))
        server.install_signal_handlers = lambda: None
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        try:
            for _ in range(100):
                try:
                    with urlopen(f'http://127.0.0.1:{port}/health', timeout=1) as response:
                        assert response.status == 200
                    break
                except (OSError, AssertionError):
                    time.sleep(.1)
            else:
                raise RuntimeError('Local demo server did not become healthy')
            out = Path(args.out)
            out.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                for name, viewport in [('desktop', {'width': 1440, 'height': 900}),
                                       ('mobile', {'width': 390, 'height': 844})]:
                    context = browser.new_context(viewport=viewport, color_scheme='light')
                    page = context.new_page()
                    errors = []
                    page.on('pageerror', lambda err: errors.append(str(err)))
                    page.goto(f'http://127.0.0.1:{port}/', wait_until='domcontentloaded')
                    page.locator('.demo-disclaimer').wait_for()
                    assert page.locator('#p-service').count() == 1
                    page.locator('a[data-screen="plan"]').click()
                    page.locator('#p-service').fill('0.95')
                    page.locator('#btn-plan').click()
                    assert page.locator('#plan-body .notice').count() == 1
                    # Exercise the judge-facing regulatory gate in a real browser.
                    page.locator('a[data-screen="compliance"]').click()
                    page.locator('#btn-compliance').click()
                    page.locator('#compliance-results').get_by_text('ПРОЙДЕНЫ ТЕСТОВЫЕ ПРОВЕРКИ').wait_for()
                    page.locator('#compliance-case').select_option('recalled')
                    page.locator('#compliance-results').get_by_text('ЗАБЛОКИРОВАНО').wait_for()
                    assert 'BATCH' in page.locator('#compliance-results').inner_text()
                    page.screenshot(path=str(out / (name + '-regulatory.png')), full_page=True)
                    if errors:
                        raise AssertionError(f'JavaScript errors ({name}): {errors}')
                    overflow = page.evaluate('document.documentElement.scrollWidth > window.innerWidth + 3')
                    if overflow:
                        raise AssertionError(f'Horizontal document overflow at {viewport["width"]}px')
                    page.screenshot(path=str(out / (name + '.png')), full_page=True)
                    if name == 'desktop':
                        page.locator('#btn-present').click()
                        assert page.locator('#btn-present').get_attribute('aria-pressed') == 'true'
                        page.screenshot(path=str(out / 'presentation.png'), full_page=True)
                    context.close()
                browser.close()
            print(f'Chromium checks passed; screenshots in {out}')
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
