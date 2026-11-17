"""Exercise real frontend JS against a real local FastAPI server in Chromium.

This container's Chromium has an admin URLBlocklist blocking localhost navigation.
To avoid bypassing that policy, render HTML via set_content and bridge ONLY
/api requests through Python's standard urllib. Thus this tests DOM behavior and
frontend-backend integration, but NOT web network navigation / deployment.

Requires: pip install playwright, plus a Chromium executable.
Run: python scripts/smoke_browser.py
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

from playwright.sync_api import expect, sync_playwright

ROOT = Path(__file__).resolve().parents[1]
BASE = "http://127.0.0.1:8766"


def ready() -> bool:
    try:
        with urllib.request.urlopen(BASE + "/api/v1/health", timeout=1) as response:
            return response.status == 200
    except Exception:
        return False


def api_bridge(source, url: str, opts: dict | None = None) -> dict:
    # Never permit the browser bridge to access arbitrary local files or hosts.
    if not url.startswith("/api/v1/"):
        raise RuntimeError("Test bridge only supports local /api/v1 paths")
    opts = opts or {}
    data = opts.get("body")
    request = urllib.request.Request(
        BASE + url,
        data=data.encode("utf-8") if data is not None else None,
        method=opts.get("method", "GET"),
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return {"status": response.status, "body": json.loads(response.read())}
    except urllib.error.HTTPError as exc:
        return {"status": exc.code, "body": json.loads(exc.read())}


def render_html(page) -> None:
    html = (ROOT / "web" / "index.html").read_text(encoding="utf-8")
    css = (ROOT / "web" / "styles.css").read_text(encoding="utf-8")
    js = (ROOT / "web" / "app.js").read_text(encoding="utf-8")
    html = html.replace('<link rel="stylesheet" href="/assets/styles.css">', f"<style>{css}</style>")
    html = html.replace('<script src="/assets/app.js" defer></script>', "")
    page.set_content(html, wait_until="domcontentloaded")
    page.evaluate("""() => { window.fetch = async (url, options = {}) => {
        const result = await window.__api_bridge(String(url), options);
        return new Response(JSON.stringify(result.body), {
            status: result.status, headers: {'content-type': 'application/json'}
        });
    }; }""")
    page.add_script_tag(content=js)


def main() -> None:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(ROOT / "src")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "lagrange_rx.api:app", "--host", "127.0.0.1", "--port", "8766", "--log-level", "warning"],
        cwd=ROOT, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
    )
    try:
        for _ in range(80):
            if ready():
                break
            if server.poll() is not None:
                raise RuntimeError(f"Server exited early: {server.stderr.read().decode()}")
            time.sleep(0.2)
        else:
            raise RuntimeError("Server health endpoint timed out")

        errors: list[str] = []
        with sync_playwright() as p:
            args = {"headless": True, "args": ["--no-sandbox"]}
            chrome = shutil.which("chromium") or shutil.which("google-chrome")
            if chrome:
                args["executable_path"] = chrome
            browser = p.chromium.launch(**args)
            try:
                page = browser.new_page(viewport={"width": 1440, "height": 1000}, device_scale_factor=1, accept_downloads=True)
                page.expose_binding("__api_bridge", api_bridge)
                page.on("pageerror", lambda exc: errors.append(str(exc)))
                render_html(page)
                expect(page.locator("#status")).to_have_text("OPTIMAL", timeout=20000)
                expect(page.locator("#total-packs")).to_contain_text("150,000")
                expect(page.locator("#paradox-result")).to_contain_text("reduces", timeout=10000)
                (ROOT / "evidence").mkdir(exist_ok=True)
                page.screenshot(path=str(ROOT / "evidence" / "desktop.png"), full_page=True)

                old = page.locator("#expected").inner_text()
                page.locator("#fx").evaluate("el => {el.value=18;el.dispatchEvent(new Event('input',{bubbles:true}));}")
                page.locator("#run").click()
                expect(page.locator("#status")).to_have_text("OPTIMAL", timeout=20000)
                assert old != page.locator("#expected").inner_text(), "FX shock did not change computed result"

                page.locator("#supplier").select_option("regional_pool")
                page.locator("#run").click()
                expect(page.locator("#status")).to_have_text("OPTIMAL", timeout=20000)
                assert "Regional pooling" not in page.locator("#allocations").inner_text()

                page.locator("#impossible").click()
                expect(page.locator("#status")).to_have_text("INFEASIBLE", timeout=20000)
                expect(page.locator("#allocations")).to_contain_text("constraints conflict")
                page.screenshot(path=str(ROOT / "evidence" / "infeasible.png"), full_page=False)

                page.locator("#reset").click()
                expect(page.locator("#status")).to_have_text("OPTIMAL", timeout=20000)
                with page.expect_download() as info:
                    page.locator("#download").click()
                download = info.value
                assert download.suggested_filename == "lagrange-rx-evidence.json"
                download.save_as(ROOT / "evidence" / "browser_evidence.json")
                file = json.loads((ROOT / "evidence" / "browser_evidence.json").read_text(encoding="utf-8"))
                assert file["result"]["status"] == "optimal"

                page.set_viewport_size({"width": 390, "height": 844})
                page.screenshot(path=str(ROOT / "evidence" / "mobile.png"), full_page=True)
                overflow = page.evaluate("""() => ({screen:innerWidth,scroll:document.documentElement.scrollWidth, offenders:[...document.querySelectorAll('*')].filter(e=>e.getBoundingClientRect().right > innerWidth+1).map(e=>({tag:e.tagName,class:e.className,right:Math.round(e.getBoundingClientRect().right)})).slice(0,20)})""")
                assert overflow['scroll'] <= overflow['screen'], f"Horizontal overflow on mobile: {overflow}"
                assert not errors, f"Page JS errors: {errors}"
                print("PASS browser: live solver response, shock, outage, infeasibility, reset, export, mobile, zero JS exceptions")
            finally:
                browser.close()
    finally:
        server.terminate()
        try:
            server.wait(timeout=5)
        except subprocess.TimeoutExpired:
            server.kill()
            server.wait()


if __name__ == "__main__":
    main()
