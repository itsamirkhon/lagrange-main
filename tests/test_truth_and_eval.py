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


def test_master_table_counts_and_ids(generated_truth):
    _, tables, _ = generated_truth
    assert list(tables["suppliers"]["supplier_id"]) == [f"sup_0{i}" for i in range(1, 5)]
    assert list(tables["carriers"]["carrier_id"]) == [f"car_0{i}" for i in range(1, 5)]
    assert list(tables["routes"]["route_id"]) == [f"rte_0{i}" for i in range(1, 5)]
    assert len(tables["skus"]) == 7
    assert "SKU_CARDIO_01" in set(tables["skus"]["sku_id"])
    for i in range(1, 7):
        assert f"SKU_BG_0{i}" in set(tables["skus"]["sku_id"])

    assert len(tables["registry_events"]) == 3
    assert list(tables["registry_events"]["event_id"]) == ["reg_0001", "reg_0002", "reg_0003"]
    assert len(tables["quotes"]) == 24
    assert len(tables["contracts"]) == 10
    assert len(tables["shipments"]) == 44
    assert len(tables["lots"]) == 50
    assert len(tables["customs"]) == 44
    assert len(tables["credit"]) == 22


def test_registry_and_customs_order_3735_values(generated_truth):
    _, tables, _ = generated_truth
    reg = tables["registry_events"]
    reg3 = reg[reg["event_id"] == "reg_0003"].iloc[0]
    assert reg3["chchn_with_vat_uzs"] == pytest.approx(150000.0, abs=0.01)
    assert reg3["chun_with_vat_uzs"] == pytest.approx(125000.0, abs=0.01)
    assert reg3["wholesale_ex_vat_uzs"] == pytest.approx(111607.14, abs=0.5)
    assert reg3["calc_base_ex_vat_uzs"] == pytest.approx(97049.69, abs=1.0)

    lots = tables["lots"]
    focus_shipments = set(lots[lots["sku_id"] == "SKU_CARDIO_01"]["shipment_id"])
    customs_focus = tables["customs"][tables["customs"]["shipment_id"].isin(focus_shipments)]
    assert len(customs_focus) > 0
    mean_unit_cost = float(customs_focus["landed_unit_cost_uzs"].mean())
    assert mean_unit_cost == pytest.approx(97049.69, abs=10.0)


def test_causal_invariants_shipments_lots_sales_inventory_writeoffs(generated_truth):
    _, tables, _ = generated_truth
    shipments = tables["shipments"].copy()
    lots = tables["lots"].copy()
    sales = tables["sales"].copy()
    inv = tables["inventory_monthly"].copy()
    writeoffs = tables["writeoffs"].copy()

    # 1. Shipments transit and arrival date invariant
    disp = pd.to_datetime(shipments["dispatch_date"])
    arr = pd.to_datetime(shipments["arrival_date"])
    assert (shipments["transit_days"] == shipments["planned_transit_days"] + shipments["delay_days"]).all()
    assert ((arr - disp).dt.days == shipments["transit_days"]).all()

    # 2. Lots date ordering and balance invariant
    mfg = pd.to_datetime(lots["manufacture_date"])
    lot_disp = pd.to_datetime(lots["dispatch_date"])
    lot_arr = pd.to_datetime(lots["arrival_date"])
    exp = pd.to_datetime(lots["expiry_date"])
    assert (mfg < lot_disp).all()
    assert (lot_disp <= lot_arr).all()
    assert (lot_arr < exp).all()
    assert (lots["remaining_qty"] >= 0).all()
    assert (
        lots["initial_qty"] - lots["sold_qty"] - lots["writeoff_qty"] == lots["remaining_qty"]
    ).all()

    # 3. Sales reference arrived lots and sum up to lot sold_qty
    lot_arr_month = lots.set_index("lot_id")["arrival_date"].str.slice(0, 7).to_dict()
    sale_lot_arr = sales["lot_id"].map(lot_arr_month)
    assert sale_lot_arr.notna().all()
    assert (sale_lot_arr <= sales["month"]).all()

    sold_by_lot = sales.groupby("lot_id")["qty_packs"].sum().reindex(lots["lot_id"], fill_value=0)
    assert (sold_by_lot.values == lots["sold_qty"].values).all()

    # 4. Monthly inventory balance invariant
    assert (inv["opening_stock"] + inv["received_qty"] == inv["available_stock"]).all()
    assert (inv["sold_qty"] <= inv["available_stock"]).all()
    assert (
        inv["closing_stock"] == inv["available_stock"] - inv["sold_qty"] - inv["writeoff_qty"]
    ).all()
    assert (inv["closing_stock"] >= 0).all()

    # 5. Writeoffs invariant: only from expiring stock (<= 90 days) and within lot qty
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
    truth_dir, tables, report = generated_truth
    assert (truth_dir / "calibration_report.json").exists()

    # Valid report passes
    validate_calibration(report, world_config)

    assert 145500 <= report["annual_sales_packs"] <= 154500
    assert report["annual_sales_packs"] == 150000
    assert report["chchn_with_vat_uzs"] == pytest.approx(150000.0, abs=0.01)
    assert report["chun_with_vat_uzs"] == pytest.approx(125000.0, abs=0.01)
    assert report["wholesale_ex_vat_uzs"] == pytest.approx(111607.14, abs=1.0)
    assert report["calc_base_ex_vat_uzs"] == pytest.approx(97049.69, abs=10.0)
    assert report["mean_landed_unit_cost_uzs"] == pytest.approx(97049.69, abs=1.0)
    assert 0.02 <= report["net_margin_ex_vat"] <= 0.03
    assert 0.25 <= report["pre_reform_net_margin_ex_vat"] <= 0.30

    # Failure cases
    bad_cases = [
        {"annual_sales_packs": 120000},
        {"chchn_with_vat_uzs": 160000.0},
        {"chun_with_vat_uzs": 130000.0},
        {"wholesale_ex_vat_uzs": 115000.0},
        {"calc_base_ex_vat_uzs": 90000.0},
        {"net_margin_ex_vat": 0.05},
        {"net_margin_ex_vat": 0.01},
        {"pre_reform_net_margin_ex_vat": 0.15},
    ]
    for overrides in bad_cases:
        broken = {**report, **overrides}
        with pytest.raises(CalibrationError):
            validate_calibration(broken, world_config)


def test_evaluate_plan_baseline_and_regret(generated_truth):
    truth_dir, _, _ = generated_truth

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

    # Zero-order month produces zero sales/shipments without error and high regret
    zero_plan = json.loads(json.dumps(baseline_plan))
    for order in zero_plan["monthly_orders"]:
        order["qty_packs"] = 0
    res_zero = evaluate_plan_on_truth(zero_plan, truth_dir=truth_dir)
    assert res_zero.stockout_packs > 0
    assert res_zero.regret_vs_baseline_uzs > 0

    assert 0.02 <= res_base.net_margin_ex_vat <= 0.03
    assert res_base.annual_sales_packs == 150000
    assert res_base.stockout_packs == 0
    assert res_base.writeoff_packs == 400
    assert res_base.revenue_ex_vat_uzs == pytest.approx(16741071000.0, abs=100.0)
    assert res_base.cogs_ex_vat_uzs == pytest.approx(14557453500.0, abs=100.0)
    assert res_base.operating_costs_uzs == pytest.approx(1765090725.0, abs=100.0)
    assert res_base.net_profit_uzs == pytest.approx(418526775.0, abs=100.0)
    assert res_base.regret_vs_baseline_uzs == pytest.approx(0.0, abs=1.0)

    # 1. Under-ordering causes stockouts, lower variable distribution cost, & positive regret
    under_plan = json.loads(json.dumps(baseline_plan))
    for order in under_plan["monthly_orders"]:
        order["qty_packs"] = int(order["qty_packs"] * 0.5)
    res_under = evaluate_plan_on_truth(under_plan, truth_dir=truth_dir)
    assert res_under.stockout_packs == 68200
    assert res_under.annual_sales_packs == 81800
    assert res_under.operating_costs_uzs == pytest.approx(1509945849.0, abs=100.0)
    assert res_under < res_base
    assert res_under.regret_vs_baseline_uzs == pytest.approx(737673214.0, abs=100.0)

    # 2. Over-ordering causes excess writeoffs, holding/financing costs, & positive regret
    over_plan = json.loads(json.dumps(baseline_plan))
    for order in over_plan["monthly_orders"]:
        order["qty_packs"] = int(order["qty_packs"] * 2.2)
    res_over = evaluate_plan_on_truth(over_plan, truth_dir=truth_dir)
    assert res_over.writeoff_packs == 108043
    assert res_over.writeoff_packs > res_base.writeoff_packs
    assert res_over.operating_costs_uzs == pytest.approx(13404606056.58, abs=100.0)
    assert res_over < res_base
    assert res_over.regret_vs_baseline_uzs == pytest.approx(11896272769.56, abs=100.0)

    # 3. Expensive air route (rte_04) increases COGS freight delta & reduces profit
    air_plan = json.loads(json.dumps(baseline_plan))
    for order in air_plan["monthly_orders"]:
        order["route_id"] = "rte_04"
    res_air = evaluate_plan_on_truth(air_plan, truth_dir=truth_dir)
    assert res_air.cogs_ex_vat_uzs == pytest.approx(16062618215.7, abs=500.0)
    assert res_air < res_base
    assert res_air.regret_vs_baseline_uzs == pytest.approx(1505164715.7, abs=500.0)

    # 4. Price cap violation (> 111607.14 ex VAT) incurs regulatory penalty & regret
    cap_plan = json.loads(json.dumps(baseline_plan))
    cap_plan["selling_price_ex_vat_uzs"] = 135000.0
    res_cap = evaluate_plan_on_truth(cap_plan, truth_dir=truth_dir)
    assert res_cap.revenue_ex_vat_uzs == pytest.approx(res_base.revenue_ex_vat_uzs, abs=1.0)
    assert res_cap.operating_costs_uzs == pytest.approx(7052484225.0, abs=100.0)
    assert res_cap < res_base
    assert res_cap.regret_vs_baseline_uzs == pytest.approx(5287393500.0, abs=100.0)


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
