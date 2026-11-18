"""TDD tests for sim/ui backend API, HTTP server, and static frontend compliance."""

from __future__ import annotations

import ast
import json
import re
import shutil
import subprocess
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.generator.cli import run_generation  # noqa: E402
from sim.ui.api import (  # noqa: E402
    _detect_delimiter,
    _sanitize_scalar,
    evaluate_scenario,
    get_document_preview,
    get_overview,
    get_truth_table,
    get_world_manifest,
    list_truth_tables,
    regenerate_world_action,
    resolve_document_path,
)
from sim.ui.server import create_server, dispatch_request  # noqa: E402


def _copy_sim_inputs(dest_sim: Path) -> None:
    shutil.copytree(ROOT / "sim" / "config", dest_sim / "config")
    shutil.copytree(ROOT / "sim" / "inputs", dest_sim / "inputs")


@pytest.fixture(scope="module")
def sim_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    base = tmp_path_factory.mktemp("ui_sim") / "sim"
    _copy_sim_inputs(base)
    run_generation(sim_dir=base, seed=42)
    return base


_NO_PROXY_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _http_request(url: str, method: str = "GET", payload: dict | None = None) -> tuple[int, str, bytes]:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"Content-Type": "application/json"} if payload is not None else {}
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _NO_PROXY_OPENER.open(req, timeout=240) as resp:
            return resp.status, resp.headers.get("Content-Type", ""), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, err.headers.get("Content-Type", ""), err.read()


def test_get_overview_structure(sim_dir: Path) -> None:
    overview = get_overview(sim_dir)
    assert overview["seed"] == 42
    assert overview["company"]["name"] == "ООО «CardioImport Distribution»"
    assert 145_500 <= overview["calibration_report"]["annual_sales_packs"] <= 154_500

    summary = overview["worlds_summary"]
    assert set(summary.keys()) == {"L0", "L1", "L2", "L3", "L4"}

    assert summary["L0"]["total_docs"] >= 20
    assert summary["L0"]["total_corruptions"] == 0
    assert summary["L0"]["recoverable_count"] == 0
    assert summary["L0"]["unrecoverable_count"] == 0
    assert summary["L0"]["omitted_documents_count"] == 0

    for lvl in ("L1", "L2", "L3"):
        assert summary[lvl]["total_corruptions"] > 0
        assert summary[lvl]["recoverable_count"] == summary[lvl]["total_corruptions"]
        assert summary[lvl]["unrecoverable_count"] == 0

    l4 = summary["L4"]
    assert l4["unrecoverable_count"] > 0
    assert l4["omitted_documents_count"] > 0
    assert sum(l4["operator_counts"].values()) == l4["total_corruptions"]
    assert sum(l4["doc_type_counts"].values()) == l4["total_docs"]
    assert l4["doc_type_counts"]["J1"] >= 1
    assert l4["operator_counts"]["J1"] >= 1


def test_get_world_manifest_and_validation(sim_dir: Path) -> None:
    man_l0 = get_world_manifest(sim_dir, "L0")
    assert man_l0["level"] == "L0"
    assert len(man_l0["documents"]) >= 20

    first = man_l0["documents"][0]
    assert first["filename"]
    assert first["size_bytes"] > 0
    assert first["ext"].startswith(".")
    assert first["preview_kind"] in {"pdf", "image", "xlsx", "csv", "eml"}
    assert first["download_url"] == f"/api/worlds/L0/document/{first['filename']}"

    with pytest.raises(ValueError):
        get_world_manifest(sim_dir, "L9")


def test_resolve_document_path_security(sim_dir: Path) -> None:
    path = resolve_document_path(sim_dir, "L0", "D1_1c_sales_2025.csv")
    assert path.is_file()
    assert path.name == "D1_1c_sales_2025.csv"

    for bad_name in ("", "../manifest.json", "sub/file.csv", "sub\\file.csv", ".."):
        with pytest.raises(ValueError):
            resolve_document_path(sim_dir, "L0", bad_name)

    with pytest.raises(ValueError):
        resolve_document_path(sim_dir, "BAD", "D1_1c_sales_2025.csv")

    with pytest.raises(FileNotFoundError):
        resolve_document_path(sim_dir, "L0", "does_not_exist.pdf")


def test_get_document_preview_all_formats(sim_dir: Path) -> None:
    xlsx_prev = get_document_preview(sim_dir, "L0", "D2_sales_tashkent_city.xlsx")
    assert xlsx_prev["kind"] == "xlsx"
    assert xlsx_prev["sheets"]
    sheet = next(iter(xlsx_prev["sheets"].values()))
    assert "headers" in sheet and "rows" in sheet and "total_rows" in sheet and "merged_cells" in sheet
    assert len(sheet["rows"]) <= 200
    assert xlsx_prev["manifest_entry"]["doc_type"] == "D2"
    assert len(xlsx_prev["truth_preview"]) > 0

    csv_prev = get_document_preview(sim_dir, "L0", "D1_1c_sales_2025.csv")
    assert csv_prev["kind"] == "csv"
    assert csv_prev["encoding"] == "cp1251"
    assert csv_prev["headers"][0] == "Период"
    assert len(csv_prev["rows"]) == 200
    assert csv_prev["total_rows"] > 200

    eml_prev = get_document_preview(sim_dir, "L0", "D12_email_quotes_2025.eml")
    assert eml_prev["kind"] == "eml"
    assert "KP-2025-001" in eml_prev["subject"]
    assert eml_prev["from"]
    assert eml_prev["to"]
    assert eml_prev["date"]
    assert eml_prev["body"]
    assert len(eml_prev["attachments"]) >= 1
    assert eml_prev["attachments"][0]["size_bytes"] > 0
    assert eml_prev["attachments"][0]["filename"] != "attachment"

    pdf_prev = get_document_preview(sim_dir, "L0", "D3_quote_KP-2025-001.pdf")
    assert pdf_prev["kind"] == "pdf"
    assert pdf_prev["media_url"] == "/api/worlds/L0/document/D3_quote_KP-2025-001.pdf"

    img_prev = get_document_preview(sim_dir, "L0", "D4_carrier_chat_auto_kz.png")
    assert img_prev["kind"] == "image"
    assert img_prev["media_url"] == "/api/worlds/L0/document/D4_carrier_chat_auto_kz.png"

    l4_man = get_world_manifest(sim_dir, "L4")
    l4_corrupted_doc = next(d["filename"] for d in l4_man["documents"] if d["corruptions"])
    l4_prev = get_document_preview(sim_dir, "L4", l4_corrupted_doc)
    assert len(l4_prev["manifest_entry"]["corruptions"]) > 0


def test_csv_cp1251_cyrillic_decoding(sim_dir: Path, tmp_path: Path) -> None:
    copy_dir = tmp_path / "sim_cp1251"
    shutil.copytree(sim_dir / "config", copy_dir / "config")
    shutil.copytree(sim_dir / "truth", copy_dir / "truth")
    shutil.copytree(sim_dir / "worlds", copy_dir / "worlds")
    csv_path = copy_dir / "worlds" / "L0" / "documents" / "D13_registry_extract_2025.csv"
    csv_path.write_bytes("код,наименование\nSKU1,Кардиозан\n".encode("cp1251"))

    prev = get_document_preview(copy_dir, "L0", "D13_registry_extract_2025.csv")
    assert prev["kind"] == "csv"
    assert prev["encoding"] == "cp1251"
    assert prev["delimiter"] == ","
    assert prev["headers"] == ["код", "наименование"]
    assert prev["rows"][0] == ["SKU1", "Кардиозан"]
    assert _detect_delimiter("a,b,c;d") == ","
    assert _detect_delimiter("a;b;c,d") == ";"


def test_list_and_get_truth_tables(sim_dir: Path, tmp_path: Path) -> None:
    tables = list_truth_tables(sim_dir)
    names = {t["name"] for t in tables}
    assert {
        "suppliers",
        "carriers",
        "routes",
        "skus",
        "fx",
        "registry_events",
        "quotes",
        "contracts",
        "shipments",
        "lots",
        "customs",
        "sales",
        "inventory_monthly",
        "writeoffs",
        "credit",
        "costs",
    }.issubset(names)
    assert all(t["row_count"] > 0 and len(t["columns"]) > 0 for t in tables)

    sales = get_truth_table(sim_dir, "sales", limit=15, sku_id="SKU_CARDIO_01")
    assert sales["table"] == "sales"
    assert len(sales["rows"]) == 15
    assert sales["total_rows"] > 15
    assert all(r["sku_id"] == "SKU_CARDIO_01" for r in sales["rows"])

    all_sales = get_truth_table(sim_dir, "sales", limit=10)
    assert all_sales["total_rows"] >= sales["total_rows"]
    sup = get_truth_table(sim_dir, "suppliers", sku_id="SKU_CARDIO_01")
    assert sup["total_rows"] == 4

    with pytest.raises(ValueError):
        get_truth_table(sim_dir, "../config/world")

    with pytest.raises(FileNotFoundError):
        get_truth_table(sim_dir, "nonexistent_table")

    # Test JSON fallback when parquet is absent
    copy_truth = tmp_path / "sim_json_fallback" / "truth"
    copy_truth.mkdir(parents=True)
    shutil.copy(sim_dir / "truth" / "routes.json", copy_truth / "routes.json")
    routes_fb = get_truth_table(tmp_path / "sim_json_fallback", "routes", limit=10)
    assert routes_fb["total_rows"] == 4


def test_evaluate_scenario_variants_and_validation(sim_dir: Path) -> None:
    base = evaluate_scenario(sim_dir, {})
    assert base["metrics"]["regret_vs_baseline_uzs"] == pytest.approx(0.0, abs=1e-2)
    assert 0.02 <= base["metrics"]["net_margin_ex_vat"] <= 0.03

    shortage = evaluate_scenario(sim_dir, {"order_qty_multiplier": 0.5})
    assert shortage["metrics"]["stockout_packs"] > 0
    assert shortage["metrics"]["regret_vs_baseline_uzs"] > 0

    overstock = evaluate_scenario(sim_dir, {"order_qty_multiplier": 2.0})
    assert overstock["metrics"]["writeoff_packs"] > base["metrics"]["writeoff_packs"]
    assert overstock["metrics"]["regret_vs_baseline_uzs"] > 0

    air = evaluate_scenario(sim_dir, {"route_id": "rte_04"})
    assert air["metrics"]["cogs_ex_vat_uzs"] != base["metrics"]["cogs_ex_vat_uzs"]

    breach = evaluate_scenario(sim_dir, {"selling_price_ex_vat_uzs": 130000.0})
    assert breach["metrics"]["net_profit_uzs"] < base["metrics"]["net_profit_uzs"]

    mkt = evaluate_scenario(sim_dir, {"marketing_multiplier": 2.0})
    assert mkt["metrics"]["operating_costs_uzs"] > base["metrics"]["operating_costs_uzs"]

    for bad_payload in (
        {"order_qty_multiplier": 0},
        {"order_qty_multiplier": float("nan")},
        {"order_qty_multiplier": float("inf")},
        {"selling_price_ex_vat_uzs": -100},
        {"marketing_multiplier": -0.5},
        {"route_id": "rte_99"},
    ):
        with pytest.raises(ValueError):
            evaluate_scenario(sim_dir, bad_payload)


def test_regenerate_world_action(tmp_path: Path) -> None:
    sim_copy = tmp_path / "sim_regen"
    _copy_sim_inputs(sim_copy)
    run_generation(sim_dir=sim_copy, seed=42)

    updated = regenerate_world_action(sim_copy, seed=77)
    assert updated["seed"] == 77
    assert updated["worlds_summary"]["L0"]["seed"] == 77

    for bad_seed in (-5, True):
        with pytest.raises(ValueError):
            regenerate_world_action(sim_copy, seed=bad_seed)


def test_dispatch_request_direct_and_edge_cases(sim_dir: Path) -> None:
    import numpy as np
    import pandas as pd

    assert _sanitize_scalar(None) is None
    assert _sanitize_scalar(np.int64(7)) == 7
    assert _sanitize_scalar(float("nan")) is None
    assert _sanitize_scalar(float("inf")) is None
    assert _sanitize_scalar(pd.NA) is None
    assert _sanitize_scalar((1, 2)) == "(1, 2)"

    assert dispatch_request("GET", "/", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/static/styles.css", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/static/../secret", b"", sim_dir)[0] == 400
    assert dispatch_request("GET", "/static/missing.css", b"", sim_dir)[0] == 404
    assert dispatch_request("GET", "/api/overview", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/api/evaluate/baseline", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/api/worlds/L0", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/api/worlds/L0/document/D1_1c_sales_2025.csv", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/api/worlds/L0/preview/D1_1c_sales_2025.csv", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/api/worlds/L0/unknown/foo", b"", sim_dir)[0] == 404
    assert dispatch_request("GET", "/api/truth", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/api/truth/routes?limit=2", b"", sim_dir)[0] == 200
    assert dispatch_request("GET", "/api/truth/missing_tbl", b"", sim_dir)[0] == 404
    assert dispatch_request("POST", "/api/evaluate", b"", sim_dir)[0] == 200
    assert dispatch_request("POST", "/api/evaluate", b"[]", sim_dir)[0] == 400
    assert dispatch_request("POST", "/api/regenerate", b"{}", sim_dir)[0] == 400
    assert dispatch_request("POST", "/api/unknown", b"{}", sim_dir)[0] == 404
    assert dispatch_request("PUT", "/api/overview", b"", sim_dir)[0] == 405


def test_http_server_live_endpoints(sim_dir: Path, tmp_path: Path) -> None:
    sim_copy = tmp_path / "sim_http"
    shutil.copytree(sim_dir, sim_copy)

    server = create_server("127.0.0.1", 0, sim_copy)
    port = server.server_address[1]
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base_url = f"http://127.0.0.1:{port}"

        status, ctype, body = _http_request(f"{base_url}/")
        assert status == 200 and "text/html" in ctype
        assert b"LAGRANGE Rx" in body

        status, ctype, _ = _http_request(f"{base_url}/static/styles.css")
        assert status == 200 and "text/css" in ctype

        status, ctype, _ = _http_request(f"{base_url}/static/app.js")
        assert status == 200 and "javascript" in ctype

        status, _, _ = _http_request(f"{base_url}/static/../api/overview")
        assert status in (400, 404)

        status, _, raw = _http_request(f"{base_url}/api/overview")
        assert status == 200
        assert json.loads(raw)["seed"] == 42

        status, _, raw = _http_request(f"{base_url}/api/worlds/L2")
        assert status == 200
        assert json.loads(raw)["level"] == "L2"

        status, ctype, raw = _http_request(f"{base_url}/api/worlds/L0/document/D3_quote_KP-2025-001.pdf")
        assert status == 200 and "application/pdf" in ctype
        assert raw.startswith(b"%PDF")

        status, _, raw = _http_request(f"{base_url}/api/worlds/L0/preview/D2_sales_tashkent_city.xlsx")
        assert status == 200
        assert json.loads(raw)["kind"] == "xlsx"

        status, _, raw = _http_request(f"{base_url}/api/truth")
        assert status == 200
        assert len(json.loads(raw)["tables"]) >= 16

        status, _, raw = _http_request(f"{base_url}/api/truth/routes?limit=2")
        assert status == 200
        assert len(json.loads(raw)["rows"]) == 2

        status, _, raw = _http_request(f"{base_url}/api/evaluate/baseline")
        assert status == 200
        assert json.loads(raw)["metrics"]["regret_vs_baseline_uzs"] == pytest.approx(0.0, abs=1e-2)

        status, _, raw = _http_request(f"{base_url}/api/evaluate", method="POST", payload={"order_qty_multiplier": 0.5})
        assert status == 200
        assert json.loads(raw)["metrics"]["stockout_packs"] > 0

        status, _, _ = _http_request(f"{base_url}/api/evaluate", method="POST", payload={"order_qty_multiplier": -1})
        assert status == 400

        status, _, raw = _http_request(f"{base_url}/api/regenerate", method="POST", payload={"seed": 99})
        assert status == 200
        assert json.loads(raw)["seed"] == 99

        status, _, _ = _http_request(f"{base_url}/api/does-not-exist")
        assert status == 404
    finally:
        server.shutdown()
        server.server_close()


def test_frontend_static_compliance() -> None:
    static_dir = ROOT / "sim" / "ui" / "static"
    html = (static_dir / "index.html").read_text(encoding="utf-8")
    css = (static_dir / "styles.css").read_text(encoding="utf-8")
    js = (static_dir / "app.js").read_text(encoding="utf-8")

    assert html.startswith("<!DOCTYPE html>")
    assert '<html lang="ru">' in html
    assert '<meta name="viewport" content="width=device-width, initial-scale=1.0">' in html
    assert len(re.findall(r"<h1\b", html)) == 1
    for landmark in ("<header", "<nav", "<main", "<search", "<footer"):
        assert landmark in html

    assert 'role="tablist"' in html
    assert html.count('role="tab"') == 4
    assert html.count('role="tabpanel"') == 4
    assert '<dialog id="doc-preview-modal" closedby="any" aria-labelledby="modal-title">' in html
    assert 'role="dialog"' not in html
    assert "aria-modal" not in html
    assert '<form method="dialog">' in html
    assert not re.search(r"\bon[a-z]+\s*=", html, flags=re.IGNORECASE)

    assert "100vw" not in css
    assert ":user-invalid" in css
    assert not re.search(r"(?<!user-):invalid\b", css)
    assert "dialog:modal" in css
    assert "dialog::backdrop" in css
    assert "overscroll-behavior: contain" in css
    assert "@container (width < 600px)" in css
    assert "border-block-end" in css
    assert "border-collapse: separate" in css

    assert "innerHTML" not in js
    assert "showModal()" in js
    assert "returnValue" in js
    assert "closedBy" in js
    assert "getBoundingClientRect" in js
    assert "aria-invalid" in js


def test_ui_cli_help_and_no_forbidden_imports() -> None:
    proc = subprocess.run(
        [sys.executable, "-m", "sim.ui", "--help"],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "--port" in proc.stdout and "--sim-dir" in proc.stdout

    for py_file in sorted((ROOT / "sim" / "ui").glob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src")
                    assert "lagrange_rx" not in alias.name
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("src")
                assert "lagrange_rx" not in mod
