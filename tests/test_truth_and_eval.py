"""TDD tests for ground-truth simulation, calibration, and plan evaluation."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.generator.calibration import (
    CalibrationError,
    build_calibration_report,
    save_calibration_report,
    validate_calibration,
)
from sim.generator.config_loader import load_world_config
from sim.generator.evaluate import (
    PlanProfit,
    evaluate_plan_on_truth,
    get_baseline_plan,
)
from sim.generator.truth_gen import TRUTH_TABLE_NAMES, generate_truth

SIM_DIR = ROOT / "sim"
CONFIG_PATH = SIM_DIR / "config" / "world.yaml"
INPUTS_DIR = SIM_DIR / "inputs"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


@pytest.fixture(scope="module")
def world_config() -> dict:
    return load_world_config(CONFIG_PATH)


@pytest.fixture(scope="module")
def generated_truth(tmp_path_factory, world_config: dict):
    truth_dir = tmp_path_factory.mktemp("truth_main")
    tables = generate_truth(
        config=world_config,
        seed=42,
        truth_dir=truth_dir,
        inputs_dir=INPUTS_DIR,
    )
    report = build_calibration_report(tables, world_config)
    save_calibration_report(report, truth_dir / "calibration_report.json")
    return truth_dir, tables, report


def test_truth_tables_completeness_and_serialization(generated_truth):
    truth_dir, tables, _ = generated_truth
    expected_tables = {
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
    }
    assert set(TRUTH_TABLE_NAMES) == expected_tables
    assert set(tables.keys()) == expected_tables
    for name in expected_tables:
        df = tables[name]
        assert isinstance(df, pd.DataFrame)
        assert len(df) > 0, f"Table {name} must not be empty"
        pq_path = truth_dir / f"{name}.parquet"
        js_path = truth_dir / f"{name}.json"
        assert pq_path.exists(), f"Missing {pq_path}"
        assert js_path.exists(), f"Missing {js_path}"
        loaded = json.loads(js_path.read_text(encoding="utf-8"))
        assert isinstance(loaded, list)
        assert len(loaded) == len(df)


def test_truth_determinism_sha256(tmp_path, world_config: dict):
    dir_a = tmp_path / "run_a"
    dir_b = tmp_path / "run_b"
    dir_c = tmp_path / "run_c"

    generate_truth(world_config, seed=42, truth_dir=dir_a, inputs_dir=INPUTS_DIR)
    generate_truth(world_config, seed=42, truth_dir=dir_b, inputs_dir=INPUTS_DIR)
    generate_truth(world_config, seed=99, truth_dir=dir_c, inputs_dir=INPUTS_DIR)

    files_a = sorted(p.name for p in dir_a.iterdir() if p.is_file())
    files_b = sorted(p.name for p in dir_b.iterdir() if p.is_file())
    assert files_a == files_b

    for fname in files_a:
        assert _sha256(dir_a / fname) == _sha256(dir_b / fname), f"Mismatch in {fname}"

    assert _sha256(dir_a / "sales.json") != _sha256(dir_c / "sales.json")


def test_master_table_counts_and_human_business_ids(generated_truth):
    _, tables, _ = generated_truth
    assert list(tables["suppliers"]["supplier_id"]) == [f"sup_0{i}" for i in range(1, 5)]
    assert list(tables["suppliers"]["supplier_code"]) == ["ZBP-IND", "ANAT-TR", "BALT-DE", "KRK-SI"]
    assert list(tables["carriers"]["carrier_id"]) == [f"car_0{i}" for i in range(1, 5)]
    assert list(tables["routes"]["route_id"]) == [f"rte_0{i}" for i in range(1, 5)]
    assert list(tables["routes"]["route_code"]) == ["KZ-AUTO-01", "TM-AUTO-02", "CHUK-RAIL-03", "TAS-AIR-04"]

    skus = tables["skus"]
    assert len(skus) == 7
    assert {"nomenclature_code", "reg_cert_no", "ean13"}.issubset(skus.columns)
    assert skus.loc[skus["sku_id"] == "SKU_CARDIO_01", "nomenclature_code"].iloc[0] == "00-000142"

    reg = tables["registry_events"]
    assert len(reg) == 3
    assert list(reg["event_id"]) == ["reg_0001", "reg_0002", "reg_0003"]
    assert list(reg["registry_record_no"]) == ["RE-3735-2025-01", "RE-3735-2025-07", "RE-3735-2025-11"]

    assert len(tables["quotes"]) == 24
    assert tables["quotes"]["quote_number"].str.startswith("KP-20").all()
    assert len(tables["contracts"]) == 10
    assert len(tables["shipments"]) == 44
    assert tables["shipments"]["invoice_number"].str.startswith("INV-20").all()
    assert len(tables["lots"]) == 50
    assert tables["lots"]["waybill_number"].str.startswith("РН-20").all()
    assert len(tables["customs"]) == 44
    assert tables["customs"]["gtd_number"].str.startswith("26004/20").all()
    assert len(tables["credit"]) == 22
    assert tables["credit"]["payment_doc_no"].str.startswith("ПП-20").all()
    assert tables["costs"]["cost_doc_no"].str.startswith("АВР-20").all()

    sales = tables["sales"]
    expected_sale_cols = {
        "doc_number",
        "counterparty_name",
        "region_name_ru",
        "region_name_uz_lat",
        "region_name_uz_cyr",
        "trade_name",
        "nomenclature_code",
        "lot_code",
    }
    assert expected_sale_cols.issubset(sales.columns)
    assert sales["doc_number"].str.startswith("РД-20").all()
    assert sales["counterparty_name"].str.contains("SYNTHETIC").all()


def test_registry_para19_and_non_smooth_landed_costs(generated_truth):
    _, tables, _ = generated_truth
    reg = tables["registry_events"]
    for _, row in reg.iterrows():
        assert row["bx_uzs"] == pytest.approx(round(0.02 * row["rn_uzs"], 2), abs=0.05)
        expected_base = round(row["rn_uzs"] + row["b_uzs"] + row["bx_uzs"], 2)
        assert row["calc_base_ex_vat_uzs"] == pytest.approx(expected_base, abs=0.05)

    reg3 = reg[reg["event_id"] == "reg_0003"].iloc[0]
    assert reg3["rn_uzs"] == pytest.approx(93317.0, abs=0.01)
    assert reg3["bx_uzs"] == pytest.approx(1866.34, abs=0.01)
    assert reg3["b_uzs"] == pytest.approx(1866.35, abs=0.01)
    assert reg3["chchn_with_vat_uzs"] == pytest.approx(150000.0, abs=0.01)
    assert reg3["chun_with_vat_uzs"] == pytest.approx(125000.0, abs=0.01)
    assert reg3["wholesale_ex_vat_uzs"] == pytest.approx(111607.14, abs=0.5)
    assert reg3["calc_base_ex_vat_uzs"] == pytest.approx(97049.69, abs=0.05)

    lots = tables["lots"]
    focus_lots = lots[lots["sku_id"] == "SKU_CARDIO_01"]
    focus_shipments = set(focus_lots["shipment_id"])
    customs_focus = tables["customs"][tables["customs"]["shipment_id"].isin(focus_shipments)]
    assert len(customs_focus) == 22

    # Non-smooth per-shipment landed cost driven by real CBU FX and shipment variance
    unit_std = float(customs_focus["landed_unit_cost_uzs"].std(ddof=0))
    unit_mean = float(customs_focus["landed_unit_cost_uzs"].mean())
    assert unit_std > 800.0
    assert 91000.0 <= unit_mean <= 102000.0
    assert abs(unit_mean - 97049.69) > 100.0

    # Every focus lot inherits its exact shipment landed_unit_cost_uzs
    cust_unit_map = customs_focus.set_index("shipment_id")["landed_unit_cost_uzs"].to_dict()
    for _, lot_row in focus_lots.iterrows():
        assert lot_row["unit_cost_uzs"] == pytest.approx(cust_unit_map[lot_row["shipment_id"]], abs=0.01)


def test_causal_invariants_shipments_lots_sales_inventory_writeoffs(generated_truth):
    _, tables, _ = generated_truth
    shipments = tables["shipments"].copy()
    lots = tables["lots"].copy()
    sales = tables["sales"].copy()
    inv = tables["inventory_monthly"].copy()
    writeoffs = tables["writeoffs"].copy()

    disp = pd.to_datetime(shipments["dispatch_date"])
    arr = pd.to_datetime(shipments["arrival_date"])
    assert (shipments["transit_days"] == shipments["planned_transit_days"] + shipments["delay_days"]).all()
    assert ((arr - disp).dt.days == shipments["transit_days"]).all()

    mfg = pd.to_datetime(lots["manufacture_date"])
    lot_disp = pd.to_datetime(lots["dispatch_date"])
    lot_arr = pd.to_datetime(lots["arrival_date"])
    exp = pd.to_datetime(lots["expiry_date"])
    assert (mfg < lot_disp).all()
    assert (lot_disp <= lot_arr).all()
    assert (lot_arr < exp).all()
    assert (lots["remaining_qty"] >= 0).all()
    assert (lots["initial_qty"] - lots["sold_qty"] - lots["writeoff_qty"] == lots["remaining_qty"]).all()

    lot_arr_month = lots.set_index("lot_id")["arrival_date"].str.slice(0, 7).to_dict()
    sale_lot_arr = sales["lot_id"].map(lot_arr_month)
    assert sale_lot_arr.notna().all()
    assert (sale_lot_arr <= sales["month"]).all()

    sold_by_lot = sales.groupby("lot_id")["qty_packs"].sum().reindex(lots["lot_id"], fill_value=0)
    assert (sold_by_lot.values == lots["sold_qty"].values).all()

    assert (inv["opening_stock"] + inv["received_qty"] == inv["available_stock"]).all()
    assert (inv["sold_qty"] <= inv["available_stock"]).all()
    assert (inv["closing_stock"] == inv["available_stock"] - inv["sold_qty"] - inv["writeoff_qty"]).all()
    assert (inv["closing_stock"] >= 0).all()

    assert len(writeoffs) > 0
    lot_exp_map = pd.to_datetime(lots.set_index("lot_id")["expiry_date"]).to_dict()
    lot_arr_map = pd.to_datetime(lots.set_index("lot_id")["arrival_date"]).to_dict()
    wo_date = pd.to_datetime(writeoffs["writeoff_date"])
    wo_exp = writeoffs["lot_id"].map(lot_exp_map)
    wo_arr = writeoffs["lot_id"].map(lot_arr_map)
    assert (wo_date >= wo_arr).all()
    assert ((wo_exp - wo_date).dt.days <= 90).all()

    wo_by_lot = writeoffs.groupby("lot_id")["qty_packs"].sum().reindex(lots["lot_id"], fill_value=0)
    assert (wo_by_lot.values == lots["writeoff_qty"].values).all()


def test_calibration_invariants_and_validation_failures(generated_truth, world_config: dict):
    truth_dir, _, report = generated_truth
    assert (truth_dir / "calibration_report.json").exists()

    validate_calibration(report, world_config)

    assert 145500 <= report["annual_sales_packs"] <= 154500
    assert report["chchn_with_vat_uzs"] == pytest.approx(150000.0, abs=0.01)
    assert report["chun_with_vat_uzs"] == pytest.approx(125000.0, abs=0.01)
    assert report["wholesale_ex_vat_uzs"] == pytest.approx(111607.14, abs=1.0)
    assert report["calc_base_ex_vat_uzs"] == pytest.approx(97049.69, abs=0.05)
    assert report["rn_uzs"] == pytest.approx(93317.0, abs=0.01)
    assert report["b_uzs"] == pytest.approx(1866.35, abs=0.01)
    assert report["bx_uzs"] == pytest.approx(1866.34, abs=0.01)
    assert report["landed_unit_cost_std_uzs"] > 800.0
    assert report["mean_landed_unit_cost_uzs"] == pytest.approx(97655.87, abs=50.0)
    assert abs(report["mean_landed_unit_cost_uzs"] - 97049.69) > 100.0
    assert 0.02 < report["net_margin_ex_vat"] < 0.03
    assert report["net_margin_ex_vat"] != pytest.approx(0.025, abs=1e-5)
    assert 0.25 < report["pre_reform_net_margin_ex_vat"] < 0.30
    assert report["pre_reform_net_margin_ex_vat"] != pytest.approx(0.275, abs=1e-5)

    bad_cases = [
        {"annual_sales_packs": 120000},
        {"chchn_with_vat_uzs": 160000.0},
        {"chun_with_vat_uzs": 130000.0},
        {"wholesale_ex_vat_uzs": 115000.0},
        {"calc_base_ex_vat_uzs": 90000.0},
        {"bx_uzs": 1200.0},
        {"landed_unit_cost_std_uzs": 0.0},
        {"net_margin_ex_vat": 0.05},
        {"net_margin_ex_vat": 0.01},
        {"pre_reform_net_margin_ex_vat": 0.15},
    ]
    for overrides in bad_cases:
        broken = {**report, **overrides}
        with pytest.raises(CalibrationError):
            validate_calibration(broken, world_config)


def test_evaluate_plan_baseline_and_regret(generated_truth):
    truth_dir, _, report = generated_truth

    baseline_plan = get_baseline_plan(truth_dir)
    assert isinstance(baseline_plan, dict)
    assert len(baseline_plan["monthly_orders"]) == 12

    res_none = evaluate_plan_on_truth(None, truth_dir=truth_dir)
    res_base = evaluate_plan_on_truth(baseline_plan, truth_dir=truth_dir)

    assert isinstance(res_base, PlanProfit)
    assert isinstance(res_base, float)
    assert float(res_base) == pytest.approx(res_base.net_profit_uzs, rel=1e-9)
    assert float(res_none) == pytest.approx(float(res_base), rel=1e-9)
    assert res_base["net_margin_ex_vat"] == pytest.approx(res_base.net_margin_ex_vat)
    assert res_base.get("missing_key", -1) == -1
    assert isinstance(res_base.to_dict(), dict)
    with pytest.raises(AttributeError):
        _ = res_base.nonexistent_metric

    # Baseline evaluator matches truth calibration report exactly
    assert 0.02 <= res_base.net_margin_ex_vat <= 0.03
    assert res_base.net_margin_ex_vat == pytest.approx(report["net_margin_ex_vat"], abs=1e-5)
    assert res_base.annual_sales_packs == report["annual_sales_packs"]
    assert res_base.stockout_packs == 0
    assert res_base.writeoff_packs == 400
    assert res_base.revenue_ex_vat_uzs == pytest.approx(report["revenue_ex_vat_uzs"], abs=100.0)
    assert res_base.cogs_ex_vat_uzs == pytest.approx(report["cogs_ex_vat_uzs"], abs=100.0)
    assert res_base.operating_costs_uzs == pytest.approx(report["operating_costs_uzs"], abs=100.0)
    assert res_base.net_profit_uzs == pytest.approx(report["net_profit_uzs"], abs=100.0)
    assert res_base.regret_vs_baseline_uzs == pytest.approx(0.0, abs=1.0)

    # Zero-order month produces stockouts & positive regret
    zero_plan = json.loads(json.dumps(baseline_plan))
    for order in zero_plan["monthly_orders"]:
        order["qty_packs"] = 0
    res_zero = evaluate_plan_on_truth(zero_plan, truth_dir=truth_dir)
    assert res_zero.stockout_packs > 100000
    assert res_zero.regret_vs_baseline_uzs > 0

    # 1. Under-ordering causes stockouts & positive regret
    under_plan = json.loads(json.dumps(baseline_plan))
    for order in under_plan["monthly_orders"]:
        order["qty_packs"] = int(order["qty_packs"] * 0.5)
    res_under = evaluate_plan_on_truth(under_plan, truth_dir=truth_dir)
    assert res_under.stockout_packs == 67850
    assert res_under.annual_sales_packs == 81400
    assert res_under.operating_costs_uzs == pytest.approx(1928645146.83, abs=100.0)
    assert res_under < res_base
    assert res_under.regret_vs_baseline_uzs == pytest.approx(944991097.5, abs=100.0)

    # 2. Over-ordering causes excess writeoffs, holding/financing costs, & positive regret
    over_plan = json.loads(json.dumps(baseline_plan))
    for order in over_plan["monthly_orders"]:
        order["qty_packs"] = int(order["qty_packs"] * 2.2)
    res_over = evaluate_plan_on_truth(over_plan, truth_dir=truth_dir)
    assert res_over.writeoff_packs == 108702
    assert res_over.operating_costs_uzs == pytest.approx(13538683244.38, abs=100.0)
    assert res_over < res_base
    assert res_over.regret_vs_baseline_uzs == pytest.approx(11442326747.16, abs=100.0)

    # 3. Expensive air route (rte_04) increases COGS freight delta & reduces profit
    air_plan = json.loads(json.dumps(baseline_plan))
    for order in air_plan["monthly_orders"]:
        order["route_id"] = "rte_04"
    res_air = evaluate_plan_on_truth(air_plan, truth_dir=truth_dir)
    assert res_air.cogs_ex_vat_uzs == pytest.approx(15419147250.79, abs=500.0)
    assert res_air < res_base
    assert res_air.regret_vs_baseline_uzs == pytest.approx(1318960129.39, abs=500.0)

    # 4. Price cap violation (> 111607.14 ex VAT) incurs regulatory penalty & regret
    cap_plan = json.loads(json.dumps(baseline_plan))
    cap_plan["selling_price_ex_vat_uzs"] = 135000.0
    res_cap = evaluate_plan_on_truth(cap_plan, truth_dir=truth_dir)
    expected_penalty = res_base.annual_sales_packs * (135000.0 - 111607.14) * 1.5 + 12 * 2_000_000.0
    assert res_cap.revenue_ex_vat_uzs == pytest.approx(res_base.revenue_ex_vat_uzs, abs=1.0)
    assert res_cap.operating_costs_uzs - res_base.operating_costs_uzs == pytest.approx(expected_penalty, abs=100.0)
    assert res_cap < res_base
    assert res_cap.regret_vs_baseline_uzs == pytest.approx(expected_penalty, abs=100.0)


def test_no_lagrange_rx_imports_in_truth_modules():
    for fname in ("truth_gen.py", "calibration.py", "evaluate.py"):
        src_path = SIM_DIR / "generator" / fname
        tree = ast.parse(src_path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "lagrange_rx" not in alias.name
                    assert not alias.name.startswith("src")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "lagrange_rx" not in mod
                assert not mod.startswith("src")
