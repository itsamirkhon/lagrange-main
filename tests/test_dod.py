"""Definition-of-Done (Section 10 of SIMULATION.md) integration tests."""

from __future__ import annotations

import ast
import concurrent.futures
import csv
import hashlib
import json
import re
import shutil
import subprocess
import sys
from collections import Counter
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.generator.calibration import CalibrationError, validate_calibration
from sim.generator.cli import main as cli_main
from sim.generator.config_loader import load_levels_config, load_world_config
from sim.generator.domain import DOC_TYPES, WATERMARK_TEXT
from sim.generator.evaluate import evaluate_plan_on_truth, get_baseline_plan
from sim.generator.xlsx_io import read_xlsx
from sim.tools.ub_quality_gate import run_dryer

_PK_MAP: dict[str, str] = {
    "suppliers": "supplier_id",
    "carriers": "carrier_id",
    "routes": "route_id",
    "skus": "sku_id",
    "registry_events": "event_id",
    "quotes": "quote_id",
    "contracts": "contract_id",
    "shipments": "shipment_id",
    "lots": "lot_id",
    "customs": "customs_id",
    "sales": "sale_id",
    "writeoffs": "writeoff_id",
    "credit": "credit_id",
    "costs": "cost_id",
}


def _hash_tree(root: Path) -> dict[str, str]:
    return {
        str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(root.rglob("*"))
        if p.is_file()
    }


def _copy_sim_inputs(dest_sim: Path) -> None:
    shutil.copytree(ROOT / "sim" / "config", dest_sim / "config")
    shutil.copytree(ROOT / "sim" / "inputs", dest_sim / "inputs")


@pytest.fixture(scope="module")
def generated_sim_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    sim_copy = tmp_path_factory.mktemp("dod_sim") / "sim"
    _copy_sim_inputs(sim_copy)
    rc = cli_main(["--seed", "42", "--sim-dir", str(sim_copy)])
    assert rc == 0
    return sim_copy


def test_dod_1_and_2_cli_generation_and_idempotent_sha256(generated_sim_dir: Path) -> None:
    assert (generated_sim_dir / "truth" / "calibration_report.json").exists()
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        assert (generated_sim_dir / "worlds" / lvl / "manifest.json").exists()

    hashes_1 = {
        "truth": _hash_tree(generated_sim_dir / "truth"),
        "worlds": _hash_tree(generated_sim_dir / "worlds"),
    }

    proc = subprocess.run(
        [sys.executable, "-m", "sim.generator", "--seed", "42", "--sim-dir", str(generated_sim_dir)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "seed=42" in proc.stdout
    hashes_2 = {
        "truth": _hash_tree(generated_sim_dir / "truth"),
        "worlds": _hash_tree(generated_sim_dir / "worlds"),
    }
    assert hashes_1 == hashes_2


def test_dod_3_calibration_report_and_validation_failures(generated_sim_dir: Path) -> None:
    world_cfg = load_world_config(generated_sim_dir / "config" / "world.yaml")
    report = json.loads((generated_sim_dir / "truth" / "calibration_report.json").read_text(encoding="utf-8"))

    validate_calibration(report, world_cfg)
    assert 145_500 <= report["annual_sales_packs"] <= 154_500
    assert report["bx_uzs"] == pytest.approx(round(0.02 * report["rn_uzs"], 2), abs=0.05)
    assert report["chchn_with_vat_uzs"] == pytest.approx(150_000.0, abs=0.01)
    assert report["chun_with_vat_uzs"] == pytest.approx(125_000.0, abs=0.01)
    assert report["wholesale_ex_vat_uzs"] == pytest.approx(111_607.14, abs=1.0)
    assert report["calc_base_ex_vat_uzs"] == pytest.approx(97_049.69, abs=10.0)
    assert report["landed_unit_cost_std_uzs"] > 500.0
    assert abs(report["mean_landed_unit_cost_uzs"] - report["calc_base_ex_vat_uzs"]) > 100.0
    assert 0.02 <= report["net_margin_ex_vat"] <= 0.03
    assert 0.25 <= report["pre_reform_net_margin_ex_vat"] <= 0.30

    bad_margin = dict(report, net_margin_ex_vat=0.05)
    with pytest.raises(CalibrationError):
        validate_calibration(bad_margin, world_cfg)

    bad_vol = dict(report, annual_sales_packs=120_000)
    with pytest.raises(CalibrationError):
        validate_calibration(bad_vol, world_cfg)


def test_dod_4_causal_consistency(generated_sim_dir: Path) -> None:
    truth_dir = generated_sim_dir / "truth"
    shipments = pd.read_parquet(truth_dir / "shipments.parquet")
    lots = pd.read_parquet(truth_dir / "lots.parquet")
    sales = pd.read_parquet(truth_dir / "sales.parquet")
    inv = pd.read_parquet(truth_dir / "inventory_monthly.parquet")
    writeoffs = pd.read_parquet(truth_dir / "writeoffs.parquet")

    for _, row in shipments.iterrows():
        disp = date.fromisoformat(str(row["dispatch_date"]))
        arr = date.fromisoformat(str(row["arrival_date"]))
        assert arr == disp + timedelta(days=int(row["transit_days"]))

    assert (inv["sold_qty"] <= inv["available_stock"]).all()
    assert (inv["closing_stock"] >= 0).all()

    assert (lots["remaining_qty"] >= 0).all()
    assert ((lots["initial_qty"] - lots["sold_qty"] - lots["writeoff_qty"]) == lots["remaining_qty"]).all()

    lot_arrival_month = {
        str(r["lot_id"]): str(r["arrival_date"])[:7]
        for _, r in lots.iterrows()
    }
    for _, srow in sales.iterrows():
        assert str(srow["month"]) >= lot_arrival_month[str(srow["lot_id"])]

    lot_expiry = {str(r["lot_id"]): date.fromisoformat(str(r["expiry_date"])) for _, r in lots.iterrows()}
    for _, wrow in writeoffs.iterrows():
        wo_date = date.fromisoformat(str(wrow["writeoff_date"]))
        assert (lot_expiry[str(wrow["lot_id"])] - wo_date).days <= 90


def test_dod_5_and_6_doc_types_and_manifest_integrity(generated_sim_dir: Path) -> None:
    truth_dir = generated_sim_dir / "truth"
    id_sets = {
        tbl: set(pd.read_parquet(truth_dir / f"{tbl}.parquet")[col].astype(str))
        for tbl, col in _PK_MAP.items()
    }

    for level in ("L0", "L1", "L2", "L3", "L4"):
        world_dir = generated_sim_dir / "worlds" / level
        manifest = json.loads((world_dir / "manifest.json").read_text(encoding="utf-8"))
        present_types = {doc["doc_type"] for doc in manifest["documents"]}
        for dtype in DOC_TYPES:
            assert dtype in present_types, f"{dtype} missing in {level}"

        corrs = []
        for doc in manifest["documents"]:
            doc_file = world_dir / "documents" / Path(doc["document"]).name
            assert doc_file.exists(), f"Missing {doc_file}"
            for ref in doc["truth_refs"]:
                tbl, rid = ref.split("/", 1)
                assert rid in id_sets[tbl], f"Unknown truth_ref {ref}"
            corrs.extend(doc["corruptions"])

        if level == "L0":
            assert len(corrs) == 0
        elif level in ("L1", "L2", "L3"):
            assert len(corrs) > 0
            assert all(c["recoverable"] is True for c in corrs)
        else:
            assert "J1" in present_types
            assert manifest.get("omitted_documents")
            assert any(c["recoverable"] is False for c in corrs)


def test_dod_7_zero_src_or_lagrange_rx_imports() -> None:
    for py_file in sorted((ROOT / "sim").rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("src"), f"Forbidden import in {py_file}: {alias.name}"
                    assert "lagrange_rx" not in alias.name, f"Forbidden import in {py_file}: {alias.name}"
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert not mod.startswith("src"), f"Forbidden import in {py_file}: {mod}"
                assert "lagrange_rx" not in mod, f"Forbidden import in {py_file}: {mod}"


def test_dod_8_watermarks_and_no_real_company_names(generated_sim_dir: Path) -> None:
    forbidden_real_names = ("grand pharm", "asklepiy", "novartis", "pfizer", "bayer", "sanofi")
    for level in ("L0", "L2", "L4"):
        docs_dir = generated_sim_dir / "worlds" / level / "documents"
        for pdf_path in sorted(docs_dir.glob("*.pdf")):
            text = subprocess.check_output(["pdftotext", str(pdf_path), "-"], text=True)
            assert WATERMARK_TEXT in text, f"Missing PDF watermark in {pdf_path}"
            lowered = text.lower()
            for real_name in forbidden_real_names:
                assert real_name not in lowered, f"Forbidden real name {real_name} in {pdf_path}"

        for img_path in sorted(list(docs_dir.glob("*.png")) + list(docs_dir.glob("*.jpg"))):
            with Image.open(img_path) as im:
                arr = np.asarray(im.convert("RGB"))
            footer_strip = arr[max(0, arr.shape[0] - 28) :, :, :]
            red_mask = (
                (footer_strip[:, :, 0].astype(int) - footer_strip[:, :, 1].astype(int) > 25)
                & (footer_strip[:, :, 0].astype(int) - footer_strip[:, :, 2].astype(int) > 25)
            )
            assert int(red_mask.sum()) > 50, f"Missing footer watermark pixels in {img_path}"


def test_dod_9_evaluate_plan_on_truth(generated_sim_dir: Path) -> None:
    truth_dir = generated_sim_dir / "truth"
    baseline = get_baseline_plan(truth_dir)
    base_res = evaluate_plan_on_truth(baseline, truth_dir)
    assert 0.02 <= base_res.net_margin_ex_vat <= 0.03
    assert base_res.regret_vs_baseline_uzs == pytest.approx(0.0, abs=1e-2)

    under_plan = {
        **baseline,
        "monthly_orders": [
            {**o, "qty_packs": max(0, int(o["qty_packs"] * 0.5))}
            for o in baseline["monthly_orders"]
        ],
    }
    under_res = evaluate_plan_on_truth(under_plan, truth_dir)
    assert float(under_res) < float(base_res)
    assert under_res.regret_vs_baseline_uzs > 0.0

    breach_plan = {**baseline, "selling_price_ex_vat_uzs": baseline["selling_price_ex_vat_uzs"] * 1.15}
    breach_res = evaluate_plan_on_truth(breach_plan, truth_dir)
    assert float(breach_res) < float(base_res)
    assert breach_res.regret_vs_baseline_uzs > 0.0


def test_dod_10_readme_and_dryer_gate() -> None:
    readme_path = ROOT / "sim" / "README.md"
    assert readme_path.exists()
    content = readme_path.read_text(encoding="utf-8")
    for required in (
        "python3 -m sim.generator --seed 42",
        "evaluate_plan_on_truth",
        "fx_cbu.csv",
        "cbu.uz",
        "3735",
        "ChChN",
        "ChUN",
        "calc_base_ex_vat_uzs",
        "Domain Glossary",
    ):
        assert required in content, f"Missing {required!r} in sim/README.md"

    assert run_dryer(threshold=0.8) == []


_INTERNAL_ID_RE = re.compile(
    r"\b(SKU_CARDIO_01|SKU_BG_0\d|sup_0\d|car_0\d|rte_0\d|REG_[A-Z_]+|"
    r"q_\d{4}|cnt_\d{4}|shp_\d{4}|lot_\d{4}|cus_\d{4}|sal_\d{5}|cst_\d{4}|crd_\d{4}|reg_\d{4})\b"
)


def _extract_doc_text(doc_path: Path) -> str:
    ext = doc_path.suffix.lower()
    if ext == ".csv":
        raw = doc_path.read_bytes()
        try:
            return raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            return raw.decode("cp1251", errors="replace")
    if ext == ".eml":
        return doc_path.read_text(encoding="utf-8", errors="replace")
    if ext == ".xlsx":
        wb = read_xlsx(doc_path)
        parts: list[str] = []
        for sheet in wb.values():
            for row in sheet.get("all_rows", []):
                parts.append(" ".join(str(cell) for cell in row if cell is not None))
        return "\n".join(parts)
    if ext == ".pdf":
        return subprocess.check_output(["pdftotext", str(doc_path), "-"], text=True)
    return ""


def test_dod_11_real_cbu_fx_zero_id_leakage_and_density(generated_sim_dir: Path) -> None:
    fx_path = generated_sim_dir / "inputs" / "fx_cbu.csv"
    with fx_path.open("r", encoding="utf-8", newline="") as handle:
        fx_rows = list(csv.DictReader(handle))
    assert len(fx_rows) >= 405
    usd_by_date = {r["date"]: (float(r["rate_uzs"]), r["source"]) for r in fx_rows if r["currency"] == "USD"}
    expected_usd = {
        "2025-01-01": 12920.48,
        "2025-01-23": 12990.0,
        "2025-01-31": 12957.01,
        "2025-12-30": 12025.33,
    }
    for d_str, exp_rate in expected_usd.items():
        assert d_str in usd_by_date
        rate, src = usd_by_date[d_str]
        assert rate == pytest.approx(exp_rate, abs=0.01)
        assert "cbu.uz" in src

    levels_cfg = load_levels_config(generated_sim_dir / "config" / "levels.yaml")
    doc_paths: list[Path] = []
    for level in ("L0", "L1", "L2", "L3", "L4"):
        world_dir = generated_sim_dir / "worlds" / level
        docs_dir = world_dir / "documents"
        manifest = json.loads((world_dir / "manifest.json").read_text(encoding="utf-8"))
        docs = manifest["documents"]
        corrs = [c for d in docs for c in d["corruptions"]]
        op_counts = Counter(c["operator"] for c in corrs)

        if level == "L0":
            assert len(docs) >= 70
            assert len(corrs) == 0
        elif level == "L1":
            assert len(corrs) >= 40
        elif level == "L2":
            assert len(corrs) >= 100
        elif level == "L3":
            assert len(corrs) >= 200
            for op in levels_cfg["L3"]["operators"]:
                assert op_counts[op] >= 6, f"L3 operator {op} count={op_counts[op]}"
        else:
            assert len(corrs) >= 300
            for op in levels_cfg["L4"]["operators"]:
                assert op_counts[op] >= 6, f"L4 operator {op} count={op_counts[op]}"
            assert sum(1 for c in corrs if not c["recoverable"]) >= 15

        d1_header = (docs_dir / "D1_1c_sales_2025.csv").read_bytes().decode("cp1251").splitlines()[0]
        for col in ("Период", "Номенклатура", "Количество", "Сумма с НДС"):
            assert col in d1_header

        for doc_file in sorted(docs_dir.iterdir()):
            assert not _INTERNAL_ID_RE.search(doc_file.name), f"Internal ID in filename: {doc_file.name}"
            if doc_file.suffix.lower() in {".csv", ".xlsx", ".eml", ".pdf"}:
                doc_paths.append(doc_file)

    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
        for path, text in zip(doc_paths, pool.map(_extract_doc_text, doc_paths)):
            match = _INTERNAL_ID_RE.search(text)
            assert match is None, f"Internal ID {match.group(0)!r} leaked in {path}"
