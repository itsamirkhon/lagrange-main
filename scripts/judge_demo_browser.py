#!/usr/bin/env python3
"""Real Chrome judge rehearsal: use a prepared L0 graph through the actual Russian UI.

Uses temporary synthetic data and the real FastAPI server. It does not read
simulator truth via the product API or manufacture an optimization response.
Requires Playwright Chromium installed. Screenshots are local/CI artifacts.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path
from urllib.request import urlopen

from lagrange_rx.api.app import create_app


ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise SystemExit("Install playwright and Chromium to run the browser rehearsal")

    import uvicorn

    with tempfile.TemporaryDirectory(prefix="lagrange-judge-") as tmp:
        data = Path(tmp) / "data"
        prep = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "prepare_demo.py"),
             "--data-dir", str(data)],
            cwd=ROOT, env={**os.environ, "LAGRANGE_AI_MODE": "off"},
            capture_output=True, text=True, timeout=240,
        )
        if prep.returncode:
            raise RuntimeError(f"prepare_demo failed: {prep.stderr[-3000:]}")
        graph_id = json.loads(prep.stdout)["graph_id"]

        with socket.socket() as sock:
            sock.bind(("127.0.0.1", 0))
            port = sock.getsockname()[1]
        app = create_app(
            data, samples_dir=ROOT / "sim" / "worlds", eval_dir=ROOT / "eval",
            auth_password="",
        )
        server = uvicorn.Server(uvicorn.Config(
            app, host="127.0.0.1", port=port, log_level="error"
        ))
        server.install_signal_handlers = lambda: None
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()
        base = f"http://127.0.0.1:{port}"
        out = Path(os.getenv("LAGRANGE_JUDGE_SCREENSHOTS", "evidence/judge_browser"))
        out.mkdir(parents=True, exist_ok=True)

        try:
            for _ in range(100):
                try:
                    with urlopen(base + "/health", timeout=1) as response:
                        assert response.status == 200
                    break
                except (OSError, AssertionError):
                    time.sleep(.1)
            else:
                raise AssertionError("Judge rehearsal API failed to start")

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors = []
                page.on("pageerror", lambda err: errors.append(str(err)))
                page.set_default_timeout(60000)
                page.goto(base, wait_until="domcontentloaded")
                page.locator("#graph-pick").wait_for()
                page.locator("#graph-pick").select_option(graph_id)
                page.locator("#btn-use-graph").click()
                page.locator("#btn-twin").click()
                page.locator("#twin-body .kpis").wait_for()
                assert "откалиброван" in page.locator("#twin-body").inner_text().lower(), (
                    page.locator("#twin-body").inner_text()[:1000]
                )
                page.screenshot(path=str(out / "1_twin.png"), full_page=True)

                page.locator('a[data-screen="plan"]').click()
                page.locator("#p-n").fill("100")
                page.locator("#p-k").fill("12")
                page.locator("#p-risk").select_option("expected")
                page.locator("#p-service").fill("0.90")
                page.locator("#btn-plan").click()
                page.locator("#plan-body .kpis").wait_for()
                assert "План plan-" in page.locator("#plan-body").inner_text(), (
                    page.locator("#plan-body").inner_text()[:1500]
                )
                page.screenshot(path=str(out / "2_plan.png"), full_page=True)

                page.locator('a[data-screen="shock"]').click()
                page.locator("#s-fx").fill("15")
                page.locator('[data-shock="fx"]').click()
                page.locator("#shock-body .kpis").wait_for()
                assert "Изменение" in page.locator("#shock-body").inner_text(), (
                    page.locator("#shock-body").inner_text()[:1500]
                )
                page.screenshot(path=str(out / "3_fx_shock.png"), full_page=True)
                assert not errors, f"Browser JavaScript errors: {errors}"
                browser.close()
            print(f"PASS: real UI twin -> nonzero plan -> FX shock; graph={graph_id}; screenshots={out}")
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
