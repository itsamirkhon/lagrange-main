"""Tests for LAGRANGE Rx corruption operators, level planner, and image corruptions."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest
from PIL import Image, ImageDraw

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from sim.generator.config_loader import load_levels_config
from sim.generator.corruptions import (
    ALL_OPERATORS,
    OPERATORS_CATALOG,
    corrupt_arithmetic,
    corrupt_currency_omit,
    corrupt_currency_wrong,
    corrupt_date,
    corrupt_fx_wrong_date,
    corrupt_language_mix,
    corrupt_number,
    corrupt_unit,
    corrupt_vat_label,
    make_corruption_record,
    plan_level_corruptions,
)
from sim.generator.image_corruptions import (
    apply_handwritten_correction,
    apply_messenger_crop,
    apply_scan_photo_effects,
    apply_stamp_overlay,
    ensure_watermark,
)


def _sample_truth_tables() -> dict[str, pd.DataFrame]:
    """Build minimal deterministic truth tables matching truth_gen schema."""
    return {
        "quotes": pd.DataFrame(
            [
                {"quote_id": "q_0001", "supplier_id": "sup_01", "unit_price": 4.85, "currency": "USD", "min_order_qty": 5000, "lead_time_days": 21, "valid_until": "2026-06-30"},
                {"quote_id": "q_0002", "supplier_id": "sup_02", "unit_price": 5.10, "currency": "USD", "min_order_qty": 3000, "lead_time_days": 14, "valid_until": "2026-09-30"},
                {"quote_id": "q_0003", "supplier_id": "sup_03", "unit_price": 4.95, "currency": "EUR", "min_order_qty": 4000, "lead_time_days": 18, "valid_until": "2026-08-31"},
            ]
        ),
        "contracts": pd.DataFrame(
            [
                {"contract_id": "cnt_0001", "supplier_id": "sup_01", "min_order_qty": 10000, "lead_time_days": 30, "signed_date": "2025-12-01"},
                {"contract_id": "cnt_0002", "supplier_id": "sup_02", "min_order_qty": 6000, "lead_time_days": 20, "signed_date": "2026-01-15"},
            ]
        ),
        "shipments": pd.DataFrame(
            [
                {"shipment_id": "shp_0001", "dispatch_date": "2026-03-04", "arrival_date": "2026-03-25", "qty_packs": 12000, "freight_uzs": 18500000.0},
                {"shipment_id": "shp_0002", "dispatch_date": "2026-06-10", "arrival_date": "2026-06-28", "qty_packs": 15000, "freight_uzs": 21200000.0},
            ]
        ),
        "lots": pd.DataFrame(
            [
                {"lot_id": "lot_0001", "shipment_id": "shp_0001", "sku_id": "SKU_CARDIO_01", "initial_qty": 12000, "remaining_qty": 4500},
                {"lot_id": "lot_0002", "shipment_id": "shp_0002", "sku_id": "SKU_CARDIO_01", "initial_qty": 15000, "remaining_qty": 11000},
            ]
        ),
        "customs": pd.DataFrame(
            [
                {"customs_id": "cus_0001", "shipment_id": "shp_0001", "declaration_date": "2026-03-26", "cip_uzs": 900000000.0, "vat_uzs": 108000000.0},
                {"customs_id": "cus_0002", "shipment_id": "shp_0002", "declaration_date": "2026-06-29", "cip_uzs": 1125000000.0, "vat_uzs": 135000000.0},
            ]
        ),
        "sales": pd.DataFrame(
            [
                {"sale_id": "sale_0001", "month": "2026-06", "region_id": "REG_TASHKENT_CITY", "sku_id": "SKU_CARDIO_01", "qty_packs": 3200, "revenue_ex_vat_uzs": 357142848.0},
                {"sale_id": "sale_0002", "month": "2026-07", "region_id": "REG_SAMARKAND", "sku_id": "SKU_CARDIO_01", "qty_packs": 1500, "revenue_ex_vat_uzs": 167410710.0},
                {"sale_id": "sale_0003", "month": "2026-08", "region_id": "REG_FERGANA", "sku_id": "SKU_CARDIO_01", "qty_packs": 1800, "revenue_ex_vat_uzs": 200892852.0},
            ]
        ),
        "registry_events": pd.DataFrame(
            [
                {"reg_id": "reg_0001", "effective_date": "2025-01-10", "chchn_with_vat_uzs": 195000.0, "chun_with_vat_uzs": 162500.0},
                {"reg_id": "reg_0002", "effective_date": "2025-11-01", "chchn_with_vat_uzs": 150000.0, "chun_with_vat_uzs": 125000.0},
            ]
        ),
        "fx": pd.DataFrame(
            [
                {"date": "2025-12-01", "currency": "USD", "rate_uzs": 12850.0},
                {"date": "2026-03-26", "currency": "USD", "rate_uzs": 12990.0},
                {"date": "2026-06-29", "currency": "USD", "rate_uzs": 13060.0},
            ]
        ),
        "credit": pd.DataFrame(
            [
                {"credit_id": "crd_0001", "month": "2026-07", "closing_balance_uzs": 420000000.0, "monthly_interest_uzs": 7700000.0},
            ]
        ),
        "costs": pd.DataFrame(
            [
                {"cost_id": "cst_0001", "month": "2026-07", "category": "distribution", "amount_uzs": 45000000.0},
            ]
        ),
    }


def _sample_level_configs() -> dict[str, dict]:
    """Return level configs matching SIMULATION.md Section 5-6 and world/levels spec."""
    return {
        "L0": {"name": "Эталон", "target_intensity": 0.0, "allow_unrecoverable": False, "operators": []},
        "L1": {"name": "Нормальный бизнес", "target_intensity": 0.08, "allow_unrecoverable": False, "operators": ["N1", "N2", "M1"]},
        "L2": {"name": "Типичная ЦА", "target_intensity": 0.20, "allow_unrecoverable": False, "operators": ["N1", "N2", "M1", "R1", "R4", "L1", "C1", "S1", "D1x"]},
        "L3": {
            "name": "Беспорядок",
            "target_intensity": 0.38,
            "allow_unrecoverable": False,
            "operators": ["N1", "N2", "M1", "R1", "R4", "L1", "C1", "S1", "D1x", "R2", "R3", "X1", "X2", "C2", "C3", "V1", "U1", "A1"],
        },
        "L4": {
            "name": "Хаос",
            "target_intensity": 0.60,
            "allow_unrecoverable": True,
            "operators": ["N1", "N2", "M1", "R1", "R4", "L1", "C1", "S1", "D1x", "R2", "R3", "X1", "X2", "C2", "C3", "V1", "U1", "A1", "J1"],
        },
    }


def _make_sample_doc_image(width: int = 800, height: int = 600) -> Image.Image:
    """Create a clean white synthetic test image with black text lines."""
    img = Image.new("RGB", (width, height), (255, 255, 255))
    draw = ImageDraw.Draw(img)
    draw.rectangle([40, 40, width - 40, height - 40], outline=(30, 30, 30), width=2)
    draw.text((80, 100), "CardioImport Distribution - Invoice", fill=(10, 10, 10))
    draw.text((80, 160), "Unit Price: 4.85 USD", fill=(10, 10, 10))
    draw.text((80, 220), "Total: 125 000.00 UZS", fill=(10, 10, 10))
    return img


def test_all_19_operators_registered():
    expected = {"N1", "N2", "C1", "C2", "C3", "V1", "U1", "L1", "X1", "X2", "S1", "M1", "D1x", "A1", "R1", "R2", "R3", "R4", "J1"}
    assert set(ALL_OPERATORS) == expected
    assert len(ALL_OPERATORS) == 19
    for op_id in expected:
        entry = OPERATORS_CATALOG[op_id]
        assert entry["id"] == op_id
        assert entry["name"]
        assert entry["category"]
        assert isinstance(entry["default_recoverable"], bool)


def test_corrupt_number_deterministic_and_varied():
    val = 1_250_000.0
    out_a = corrupt_number(val, np.random.default_rng(42))
    out_b = corrupt_number(val, np.random.default_rng(42))
    assert out_a == out_b
    variants = {corrupt_number(val, np.random.default_rng(s)) for s in range(30)}
    assert "1 250 000,00" in variants
    assert "1.250.000" in variants
    assert "1,25 млн" in variants
    assert "1250т" in variants
    assert corrupt_number(42.5, np.random.default_rng(2)) == "42,50"
    assert corrupt_number(2500.0, np.random.default_rng(2)) == "2,5т"
    assert corrupt_number(1_000_000.0, np.random.default_rng(4)) == "1,00 млн"


def test_corrupt_date_deterministic_and_invalid_raises():
    d1 = corrupt_date("2026-04-03", np.random.default_rng(10))
    d2 = corrupt_date("2026-04-03", np.random.default_rng(10))
    assert d1 == d2
    assert corrupt_date("2026-04-03", np.random.default_rng(0)) == "3 апреля 2026 г."
    variants = {corrupt_date("2026-04-03", np.random.default_rng(s)) for s in range(35)}
    assert "03.04.2026" in variants
    assert "04/03/2026" in variants
    assert "апр. 26" in variants
    assert "3 апреля 2026 г." in variants
    assert corrupt_date("2026-04", np.random.default_rng(5)) == "апр. 26"
    with pytest.raises(ValueError):
        corrupt_date("not-a-date", np.random.default_rng(1))


def test_corrupt_currency_omit_and_wrong():
    omitted = corrupt_currency_omit(4.85, "USD")
    assert omitted == "4,85"
    assert "USD" not in omitted and "$" not in omitted
    wrong1 = corrupt_currency_wrong(4.85, np.random.default_rng(7))
    wrong2 = corrupt_currency_wrong(4.85, np.random.default_rng(7))
    assert wrong1 == wrong2
    assert wrong1["display"] == "4.85 сум"
    assert wrong1["amount"] == pytest.approx(4.85)
    assert wrong1["true_currency"] == "USD"
    assert wrong1["shown_currency"] == "UZS"
    displays = {corrupt_currency_wrong(4.85, np.random.default_rng(s))["display"] for s in range(10)}
    assert "4.85 UZS" in displays
    assert "4.85 сум" in displays


def test_corrupt_fx_wrong_date():
    res = corrupt_fx_wrong_date(1000.0, true_rate=13060.0, wrong_rate=12850.0, wrong_date="2025-12-01")
    assert res["true_uzs"] == pytest.approx(13_060_000.0)
    assert res["shown_uzs"] == pytest.approx(12_850_000.0)
    assert res["delta_uzs"] == pytest.approx(-210_000.0)
    assert res["wrong_date"] == "2025-12-01"
    with pytest.raises(ValueError):
        corrupt_fx_wrong_date(1000.0, true_rate=0.0, wrong_rate=12850.0, wrong_date="2025-12-01")
    with pytest.raises(ValueError):
        corrupt_fx_wrong_date(1000.0, true_rate=13060.0, wrong_rate=-1.0, wrong_date="2025-12-01")


def test_corrupt_vat_label():
    assert corrupt_vat_label(125_000.0, True, np.random.default_rng(0))["mode"] == "swapped"
    assert corrupt_vat_label(125_000.0, True, np.random.default_rng(1))["mode"] == "omitted"
    modes = set()
    for seed in range(20):
        res = corrupt_vat_label(125_000.0, includes_vat=True, rng=np.random.default_rng(seed), vat_rate=0.12)
        modes.add(res["mode"])
        assert res["true_label"] == "с НДС"
        assert res["shown_label"] != res["true_label"]
        assert res["shown_price"] > 0
        if res["mode"] == "swapped":
            assert res["shown_label"] == "без НДС"
            assert res["shown_price"] == pytest.approx(125_000.0 / 1.12, rel=1e-3)
    assert modes == {"omitted", "swapped"}
    ex_modes = {
        corrupt_vat_label(100_000.0, includes_vat=False, rng=np.random.default_rng(s), vat_rate=0.12)["shown_price"]
        for s in range(10)
    }
    assert any(abs(p - 112_000.0) < 1e-2 for p in ex_modes)


def test_corrupt_unit():
    assert corrupt_unit(1000, 125_000.0, np.random.default_rng(0))["unit"] == "коробка"
    assert corrupt_unit(1000, 125_000.0, np.random.default_rng(1))["unit"] == "блистер"
    units_seen = set()
    for seed in range(20):
        res = corrupt_unit(1000, 125_000.0, np.random.default_rng(seed), pack_blisters=3, box_packs=10)
        units_seen.add(res["unit"])
        assert res["qty"] * res["unit_price"] == pytest.approx(1000 * 125_000.0, rel=1e-4)
    assert "блистер" in units_seen
    assert "коробка" in units_seen


def test_corrupt_language_mix():
    src = "Kardiozan Toshkent viloyati"
    outputs = {corrupt_language_mix(src, np.random.default_rng(s)) for s in range(25)}
    assert all(out != src for out in outputs)
    assert len(outputs) >= 3
    assert corrupt_language_mix(src, np.random.default_rng(3)) == corrupt_language_mix(src, np.random.default_rng(3))
    bilingual = "Счёт-фактура Договор Kardiozan г. Ташкент упаковок без НДС"
    mixed = {corrupt_language_mix(bilingual, np.random.default_rng(s)) for s in range(25)}
    assert any("Hisobvaraq-faktura" in m or "Shartnoma" in m or "Toshkent shahri" in m for m in mixed)
    assert any("Қардиозан" in m or "qadoq" in m or "QQSsiz" in m for m in mixed)
    # Multiple pairs can be replaced in a single call (1-2 occurrences)
    assert any(sum(tok in m for tok in ("Hisobvaraq-faktura", "Shartnoma", "Қардиозан", "Kardiozann", "Toshkent shahri", "qadoq", "QQSsiz")) >= 2 for m in mixed)
    plain = "Angiokor"
    assert corrupt_language_mix(plain, np.random.default_rng(2)) == "Angiokkor"
    assert corrupt_language_mix("A", np.random.default_rng(0)) == "AA"


def test_corrupt_arithmetic():
    lines = [120_000.0, 250_000.0, 380_000.0]
    res1 = corrupt_arithmetic(lines, np.random.default_rng(11))
    res2 = corrupt_arithmetic(lines, np.random.default_rng(11))
    assert res1 == res2
    assert res1["true_total"] == pytest.approx(750_000.0)
    assert res1["shown_total"] != pytest.approx(750_000.0)
    assert res1["delta"] == pytest.approx(res1["shown_total"] - res1["true_total"])
    shown_set = {corrupt_arithmetic(lines, np.random.default_rng(s))["shown_total"] for s in range(20)}
    assert shown_set == {690_000.0, 787_500.0, 370_000.0}
    single = corrupt_arithmetic([100.0], np.random.default_rng(4))
    assert single["shown_total"] == pytest.approx(110.0)
    with pytest.raises(ValueError):
        corrupt_arithmetic([], np.random.default_rng(1))


def test_make_corruption_record_exact_schema():
    rec = make_corruption_record(
        operator="C1",
        field="unit_price",
        page=1,
        bbox=[120, 340, 260, 360],
        true_value={"amount": 4.85, "currency": "USD"},
        shown_value="4,85",
        recoverable=True,
        recover_hint="currency in contract D5_contract_supplier_A.pdf p.2",
    )
    assert rec == {
        "operator": "C1",
        "field": "unit_price",
        "location": {"page": 1, "bbox": [120, 340, 260, 360]},
        "true_value": {"amount": 4.85, "currency": "USD"},
        "shown_value": "4,85",
        "recoverable": True,
        "recover_hint": "currency in contract D5_contract_supplier_A.pdf p.2",
    }
    with pytest.raises(ValueError):
        make_corruption_record("UNKNOWN", "f", 1, [0, 0, 10, 10], 1, 2, True, "hint")
    with pytest.raises(ValueError):
        make_corruption_record("N1", "f", 1, [0, 0, 10], 1, 2, True, "hint")
    with pytest.raises(ValueError):
        make_corruption_record("N1", "f", 0, [0, 0, 10, 10], 1, 2, True, "hint")
    with pytest.raises(ValueError):
        make_corruption_record("N1", "f", 1, [0, 0, 10, 10], 1, 2, True, "   ")


def test_plan_level_corruptions_constraints_l0_to_l4():
    truth = _sample_truth_tables()
    levels = _sample_level_configs()

    # L0: 0 corruptions
    plan_l0 = plan_level_corruptions("L0", levels["L0"], truth, np.random.default_rng(42))
    assert plan_l0["summary"]["total_count"] == 0
    assert plan_l0["planned_corruptions"] == []
    assert plan_l0["junk_files"] == []
    assert plan_l0["omitted_documents"] == []

    # L1-L3: all recoverable=True, only allowed operators, deterministic
    for lvl in ("L1", "L2", "L3"):
        p1 = plan_level_corruptions(lvl, levels[lvl], truth, np.random.default_rng(42))
        p2 = plan_level_corruptions(lvl, levels[lvl], truth, np.random.default_rng(42))
        assert p1 == p2
        items = p1["planned_corruptions"]
        assert len(items) > 0
        allowed = set(levels[lvl]["operators"])
        assert all(item["operator"] in allowed for item in items)
        assert all(item["recoverable"] is True for item in items)
        assert all(bool(item["recover_hint"]) for item in items)
        assert p1["summary"]["unrecoverable_count"] == 0
        assert p1["junk_files"] == []

    # L4: high intensity >= 50%, J1 junk files, explicit recoverable=False entries
    plan_l3 = plan_level_corruptions("L3", levels["L3"], truth, np.random.default_rng(42))
    assert plan_l3["summary"]["total_count"] >= 180
    assert all(cnt >= 10 for cnt in plan_l3["summary"]["by_operator"].values())

    plan_l4 = plan_level_corruptions("L4", levels["L4"], truth, np.random.default_rng(42))
    assert plan_l4["target_intensity"] >= 0.50
    assert len(plan_l4["junk_files"]) >= 5
    assert any(item["operator"] == "J1" for item in plan_l4["planned_corruptions"])
    unrec = [item for item in plan_l4["planned_corruptions"] if item["recoverable"] is False]
    assert len(unrec) >= 15
    assert all("client" in item["recover_hint"].lower() or "original" in item["recover_hint"].lower() or "клиент" in item["recover_hint"].lower() for item in unrec)
    assert len(plan_l4["omitted_documents"]) >= 1
    assert plan_l4["summary"]["total_count"] >= 280
    assert plan_l4["summary"]["unrecoverable_count"] == len(unrec)
    assert plan_l4["summary"]["recoverable_count"] == plan_l4["summary"]["total_count"] - len(unrec)
    assert plan_l4["summary"]["by_operator"]["N1"] >= 15
    assert sum(plan_l4["summary"]["by_operator"].values()) == plan_l4["summary"]["total_count"]


def test_no_internal_truth_ids_in_hints_or_fields():
    truth = _sample_truth_tables()
    levels = _sample_level_configs()
    forbidden = ("SKU_CARDIO_01", "SKU_BG_", "lot_id", "shipment_id", "reg_0002", "reg_0003", "quote_id", "contract_id")
    for lvl in ("L1", "L2", "L3", "L4"):
        plan = plan_level_corruptions(lvl, levels[lvl], truth, np.random.default_rng(42))
        for item in plan["planned_corruptions"]:
            for tok in forbidden:
                assert tok not in item["recover_hint"], f"Internal token {tok} leaked in hint: {item['recover_hint']}"
                assert tok not in item["field"], f"Internal token {tok} leaked in field: {item['field']}"


def test_plan_level_corruptions_intensity_increases_monotonically():
    truth = _sample_truth_tables()
    levels = _sample_level_configs()
    counts = [
        plan_level_corruptions(lvl, levels[lvl], truth, np.random.default_rng(42))["summary"]["total_count"]
        for lvl in ("L0", "L1", "L2", "L3", "L4")
    ]
    assert counts[0] == 0
    assert counts[0] < counts[1] < counts[2] < counts[3] < counts[4]


def test_plan_level_corruptions_edge_cases():
    levels = _sample_level_configs()
    list_truth = {
        "quotes": [{"other": 1}],
        "sales": [],
    }
    plan = plan_level_corruptions("L1", levels["L1"], list_truth, np.random.default_rng(7))
    assert any(item["truth_ref"] == "sales/row_0001" for item in plan["planned_corruptions"])

    # Empty truth_tables on L4 uses explicit fallback IDs for unrecoverable/junk items
    l4_empty = plan_level_corruptions("L4", levels["L4"], {}, np.random.default_rng(42))
    refs = {item["truth_ref"] for item in l4_empty["planned_corruptions"]}
    assert {"sales/sale_0001", "lots/lot_0001", "costs/cst_0001"}.issubset(refs)

    # Boolean boundary checks on allow_unrecoverable and empty/zero intensity
    l3_forced = plan_level_corruptions("L3", {**levels["L3"], "allow_unrecoverable": True}, list_truth, np.random.default_rng(42))
    assert l3_forced["allow_unrecoverable"] is False
    assert l3_forced["summary"]["unrecoverable_count"] == 0

    l4_disabled = plan_level_corruptions("L4", {**levels["L4"], "allow_unrecoverable": False}, list_truth, np.random.default_rng(42))
    assert l4_disabled["allow_unrecoverable"] is False
    assert l4_disabled["summary"]["unrecoverable_count"] == 0

    zero_intensity = plan_level_corruptions("L1", {"target_intensity": 0.0, "operators": ["N1"]}, list_truth, np.random.default_rng(7))
    assert zero_intensity["summary"]["total_count"] == 0

    empty_ops = plan_level_corruptions("L1", {"target_intensity": 0.1, "operators": []}, list_truth, np.random.default_rng(7))
    assert empty_ops["summary"]["total_count"] == 0

    bad_cfg = {"target_intensity": 0.1, "allow_unrecoverable": False, "operators": ["BOGUS"]}
    with pytest.raises(ValueError):
        plan_level_corruptions("L1", bad_cfg, list_truth, np.random.default_rng(7))


def test_image_scan_photo_effects_deterministic_and_severe():
    base = _make_sample_doc_image()
    mild1 = apply_scan_photo_effects(base, np.random.default_rng(99), severe=False)
    mild2 = apply_scan_photo_effects(base, np.random.default_rng(99), severe=False)
    severe = apply_scan_photo_effects(base, np.random.default_rng(99), severe=True)

    assert mild1.size == base.size
    assert mild1.mode == "RGB"
    assert np.array_equal(np.asarray(mild1), np.asarray(mild2))

    arr_base = np.asarray(base, dtype=np.float32)
    arr_mild = np.asarray(mild1, dtype=np.float32)
    arr_sev = np.asarray(severe, dtype=np.float32)
    mae_mild = float(np.mean(np.abs(arr_base - arr_mild)))
    mae_sev = float(np.mean(np.abs(arr_base - arr_sev)))
    assert mae_mild > 2.0
    assert mae_sev > mae_mild


def test_image_stamp_overlay_modifies_bbox_deterministically():
    base = _make_sample_doc_image()
    bbox = (120, 150, 280, 190)
    s1 = apply_stamp_overlay(base, bbox, np.random.default_rng(12))
    s2 = apply_stamp_overlay(base, bbox, np.random.default_rng(12))
    assert np.array_equal(np.asarray(s1), np.asarray(s2))
    assert s1.size == base.size
    crop_base = np.asarray(base.crop(bbox), dtype=np.int16)
    crop_stamp = np.asarray(s1.crop(bbox), dtype=np.int16)
    assert np.max(np.abs(crop_base - crop_stamp)) > 20


def test_image_handwritten_correction_ascii_and_cyrillic():
    base = _make_sample_doc_image()
    bbox = (180, 155, 290, 185)
    h1 = apply_handwritten_correction(base, bbox, "5.20 USD", np.random.default_rng(5))
    h2 = apply_handwritten_correction(base, bbox, "5.20 USD", np.random.default_rng(5))
    h_cyr = apply_handwritten_correction(base, bbox, "испр. 5,20", np.random.default_rng(5))
    h_edge = apply_handwritten_correction(base, (680, 155, 760, 185), "4.90", np.random.default_rng(5))
    assert np.array_equal(np.asarray(h1), np.asarray(h2))
    assert h_cyr.size == base.size
    assert h_edge.size == base.size
    diff = np.abs(np.asarray(base, dtype=np.int16) - np.asarray(h1, dtype=np.int16))
    assert int(np.sum(diff > 15)) > 100


def test_image_messenger_crop_clips_right_or_bottom():
    base = _make_sample_doc_image(800, 1000)
    c1 = apply_messenger_crop(base, np.random.default_rng(21))
    c2 = apply_messenger_crop(base, np.random.default_rng(21))
    assert c1.size == c2.size
    assert c1.width < base.width or c1.height < base.height
    assert c1.width >= int(base.width * 0.75)
    assert c1.height >= int(base.height * 0.75)

    # Exact target_bbox alignment: right edge cuts strictly inside price bbox (x1 < new_w < x2)
    price_bbox = (610, 320, 740, 355)
    for seed in range(15):
        cropped = apply_messenger_crop(base, np.random.default_rng(seed), target_bbox=price_bbox)
        assert price_bbox[0] < cropped.width < price_bbox[2]
        assert cropped.height >= price_bbox[3]


def test_ensure_watermark_visible_and_deterministic():
    blank = Image.new("RGB", (600, 800), (255, 255, 255))
    w1 = ensure_watermark(blank)
    w2 = ensure_watermark(blank)
    assert np.array_equal(np.asarray(w1), np.asarray(w2))
    arr = np.asarray(w1)
    assert np.min(arr) < 235


def test_plan_level_corruptions_against_real_levels_yaml():
    levels = load_levels_config(REPO_ROOT / "sim/config/levels.yaml")
    truth = _sample_truth_tables()
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        plan = plan_level_corruptions(lvl, levels[lvl], truth, np.random.default_rng(42))
        assert plan["level"] == lvl
        if lvl == "L0":
            assert plan["summary"]["total_count"] == 0
        elif lvl in ("L1", "L2", "L3"):
            assert plan["summary"]["unrecoverable_count"] == 0
        else:
            assert plan["summary"]["unrecoverable_count"] >= 2
            assert len(plan["junk_files"]) >= 2


def test_no_src_or_lagrange_rx_imports_in_corruption_modules():
    for rel in ("sim/generator/corruptions.py", "sim/generator/image_corruptions.py"):
        tree = ast.parse((REPO_ROOT / rel).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "lagrange_rx" not in alias.name
                    assert not alias.name.startswith("src")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "lagrange_rx" not in mod
                assert not mod.startswith("src")
