#!/usr/bin/env python3
"""Real-browser rehearsal of the synthetic judge demo (no outside network).

Build a human-approved synthetic L0 fixture outside the product package,
then click through the actual Russian web UI: select graph, twin, plan, FX
shock, evidence. Requires playwright + Chromium. Produces screenshots.
This is NOT certification of real-client ingest, profitability, or law.
"""
from __future__ import annotations

import argparse
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

import uvicorn

from lagrange_rx.api.app import create_app

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=Path("evidence/judge_browser"))
    args = parser.parse_args()

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise SystemExit("Install playwright and chromium before running the browser rehearsal") from exc

    with tempfile.TemporaryDirectory(prefix="lagrange-judge-") as temp:
        data = Path(temp) / "data"
        env = dict(os.environ)
        env["LAGRANGE_AI_MODE"] = "off"
        env.pop("LAGRANGE_AUTH_PASSWORD", None)
        prepared = subprocess.run(
            [sys.executable, str(ROOT / "scripts" / "prepare_demo.py"),
             "--data-dir", str(data)],
            cwd=ROOT, env=env, capture_output=True, text=True, timeout=240, check=False,
        )
        if prepared.returncode:
            raise RuntimeError(f"demo fixture failed: {prepared.stdout[-1500:]} {prepared.stderr[-4000:]}")
        gid = json.loads(prepared.stdout)["graph_id"]

        server_app = create_app(data, samples_dir=ROOT / "sim" / "worlds", auth_password="")
        port = free_port()
        server = uvicorn.Server(uvicorn.Config(server_app, host="127.0.0.1", port=port, log_level="error"))
        server.install_signal_handlers = lambda: None
        thread = threading.Thread(target=server.run, daemon=True)
        thread.start()

        try:
            for _ in range(100):
                try:
                    with urlopen(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                        if response.status == 200:
                            break
                except OSError:
                    time.sleep(0.1)
            else:
                raise RuntimeError("demo HTTP server did not start")

            out = args.out.resolve()
            out.mkdir(parents=True, exist_ok=True)
            with sync_playwright() as playwright:
                browser = playwright.chromium.launch(headless=True)
                page = browser.new_page(viewport={"width": 1440, "height": 900})
                errors: list[str] = []
                page.on("pageerror", lambda error: errors.append(str(error)))
                try:
                    page.goto(f"http://127.0.0.1:{port}/", wait_until="domcontentloaded")
                    page.locator("#graph-pick").wait_for(timeout=20000)
                    assert gid in page.locator("#graph-pick").inner_text()
                    page.locator("#graph-pick").select_option(gid)
                    page.locator("#btn-use-graph").click()

                    page.locator("#btn-twin").click()
                    page.wait_for_function("S.twin !== null", timeout=30000)
                    assert page.evaluate("S.twin.calibration.calibrated") is True
                    assert page.evaluate("S.twin.calibration.gap_pp <= 0.1") is True
                    page.screenshot(path=str(out / "01-twin.png"), full_page=True)

                    page.locator('a[data-screen="plan"]').click()
                    page.locator("#p-risk").select_option("expected")
                    page.locator("#p-n").fill("100")
                    page.locator("#p-k").fill("12")
                    page.locator("#p-service").fill("0.90")
                    page.locator("#btn-plan").click()
                    page.wait_for_function("S.plan !== null", timeout=30000)
                    assert page.evaluate("S.plan.supply_status") == "ready"
                    assert page.evaluate("S.plan.planned_packs > 0")
                    assert page.evaluate("S.plan.legal_status") == "passed"
                    page.screenshot(path=str(out / "02-plan.png"), full_page=True)

                    page.locator('a[data-screen="shock"]').click()
                    page.locator("#s-fx").fill("15")
                    page.locator('[data-shock="fx"]').click()
                    page.locator("#shock-body h2").filter(has_text="Результат:").wait_for(timeout=30000)
                    page.screenshot(path=str(out / "03-fx-shock.png"), full_page=True)

                    page.locator('a[data-screen="evidence"]').click()
                    page.locator("#evidence-session").get_by_text("Проверки", exact=False).wait_for(timeout=30000)
                    page.screenshot(path=str(out / "04-evidence.png"), full_page=True)

                    # Proof must be visible and source-labelled, not invented UI numbers.
                    page.locator('a[data-screen="progress"]').click()
                    page.locator("#progress-summary .proof-tile").first.wait_for(timeout=30000)
                    tiles = page.locator("#progress-summary .proof-tile")
                    assert tiles.count() == 4
                    assert "50 / 50" in tiles.nth(0).inner_text()
                    assert "10000" in "".join(tiles.nth(1).inner_text().split()), tiles.nth(1).inner_text()
                    assert "Архивный отчёт" in page.locator("#progress-summary").inner_text() or "Отчёт:" in page.locator("#progress-summary").inner_text()
                    assert "НЕ прошёл" in page.locator("#progress-summary").inner_text()
                    page.screenshot(path=str(out / "05-results.png"), full_page=True)

                    # Real clicks on pharmaceutical safety demo, backed by the API.
                    page.locator('a[data-screen="fefo"]').click()
                    page.locator("#fefo-lots .fefo-lot").first.wait_for(timeout=30000)
                    assert page.locator("#fefo-lots .fefo-lot").count() == 6
                    assert page.locator("#fefo-lots .fefo-lot.blocked").count() == 4
                    assert page.locator("#fefo-lots .fefo-lot.allocated").count() == 2
                    page.screenshot(path=str(out / "06-fefo-50.png"), full_page=True)
                    page.locator('[data-fefo-preset="200"]').click()
                    page.get_by_text("Недостаточно допустимого запаса", exact=False).wait_for(timeout=30000)
                    assert "140" in page.locator("#fefo-summary").inner_text()
                    assert page.locator("#fefo-lots .fefo-lot.blocked").count() == 4
                    page.screenshot(path=str(out / "07-fefo-shortage.png"), full_page=True)
                    if errors:
                        raise AssertionError(f"Javascript errors in judge demo: {errors}")
                except Exception:
                    page.screenshot(path=str(out / "failed-current-page.png"), full_page=True)
                    raise
                finally:
                    browser.close()

            print(json.dumps({
                "status": "passed", "data": "synthetic prepared L0",
                "graph_id": gid, "screens": ["twin", "plan", "fx-shock", "evidence", "results", "fefo"],
                "screenshots": str(out),
            }, ensure_ascii=False))
        finally:
            server.should_exit = True
            thread.join(timeout=5)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
