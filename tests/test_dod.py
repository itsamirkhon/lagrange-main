"""Definition-of-Done (Section 10 of SIMULATION.md) integration tests."""

from __future__ import annotations

import ast
import hashlib
import json
import shutil
import subprocess
import sys
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
from sim.generator.config_loader import load_world_config
from sim.generator.domain import DOC_TYPES, WATERMARK_TEXT
from sim.generator.evaluate import evaluate_plan_on_truth, get_baseline_plan
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


def test_dod_1_and_2_cli_generation_and_idempotent_sha256(tmp_path: Path) -> None:
    sim_copy = tmp_path / "sim"
    _copy_sim_inputs(sim_copy)

    proc1 = subprocess.run(
        [sys.executable, "-m", "sim.generator", "--seed", "42", "--sim-dir", str(sim_copy)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    assert "seed=42" in proc1.stdout
    assert (sim_copy / "truth" / "calibration_report.json").exists()
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        assert (sim_copy / "worlds" / lvl / "manifest.json").exists()

    hashes_1 = {
        "truth": _hash_tree(sim_copy / "truth"),
        "worlds": _hash_tree(sim_copy / "worlds"),
    }

    subprocess.run(
        [sys.executable, "-m", "sim.generator", "--seed", "42", "--sim-dir", str(sim_copy)],
        cwd=str(ROOT),
        capture_output=True,
        text=True,
        check=True,
    )
    hashes_2 = {
        "truth": _hash_tree(sim_copy / "truth"),
        "worlds": _hash_tree(sim_copy / "worlds"),
    }
    assert hashes_1 == hashes_2


def test_dod_3_calibration_report_and_validation_failures(generated_sim_dir: Path) -> None:
    world_cfg = load_world_config(generated_sim_dir / "config" / "world.yaml")
    report = json.loads((generated_sim_dir / "truth" / "calibration_report.json").read_text(encoding="utf-8"))

    validate_calibration(report, world_cfg)
    assert 145_500 <= report["annual_sales_packs"] <= 154_500
    assert report["chchn_with_vat_uzs"] == pytest.approx(150_000.0, abs=0.01)
    assert report["chun_with_vat_uzs"] == pytest.approx(125_000.0, abs=0.01)
    assert report["wholesale_ex_vat_uzs"] == pytest.approx(111_607.14, abs=1.0)
    assert report["calc_base_ex_vat_uzs"] == pytest.approx(97_049.69, abs=10.0)
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
