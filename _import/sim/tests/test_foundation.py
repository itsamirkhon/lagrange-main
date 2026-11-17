"""Unit tests for the LAGRANGE Rx simulation foundation layer."""

from __future__ import annotations

import ast
import hashlib
import json
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET

import numpy as np
import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
SIM_DIR = ROOT / "sim"
CONFIG_DIR = SIM_DIR / "config"
INPUTS_DIR = SIM_DIR / "inputs"


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_no_src_or_lagrange_rx_imports_in_sim() -> None:
    for py_file in SIM_DIR.rglob("*.py"):
        tree = ast.parse(py_file.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module:
                names = [node.module]
            else:
                names = []
            for mod in names:
                assert not mod.startswith("src"), f"Forbidden import {mod} in {py_file}"
                assert "lagrange_rx" not in mod, f"Forbidden import {mod} in {py_file}"


def test_world_yaml_valid_and_complete() -> None:
    from sim.generator.config_loader import load_world_config

    cfg = load_world_config(CONFIG_DIR / "world.yaml")
    assert cfg["seed"] == 42
    assert cfg["company"]["name"] == "ООО «CardioImport Distribution»"
    assert cfg["company"]["watermark"] == "УЧЕБНЫЙ ОБРАЗЕЦ / SYNTHETIC"
    assert cfg["period"]["start_month"] == "2025-01"
    assert cfg["period"]["end_month"] == "2026-10"
    assert cfg["period"]["months_count"] == 22
    assert cfg["period"]["calibration_window_months"] == 12
    assert cfg["focus_sku"]["sku_id"] == "SKU_CARDIO_01"
    assert cfg["focus_sku"]["trade_name"] == "Kardiozan 10 мг №30"
    assert cfg["focus_sku"]["nomenclature_code"] == "00-000142"
    assert cfg["focus_sku"]["ean13"] == "4780099120014"
    assert cfg["focus_sku"]["reg_cert_no"] == "DV/X 04812/01/25"
    assert len(cfg["background_skus"]) == 6
    for idx, bg in enumerate(cfg["background_skus"], start=1):
        assert bg["nomenclature_code"] == f"00-00020{idx}"
        assert bg["ean13"] == f"47800991200{14 + idx}"
        assert bg["reg_cert_no"] == f"DV/X {4812 + idx:05d}/01/25"

    targets = cfg["calibration_targets"]
    assert targets["annual_sales_packs_target"] == 150000
    assert targets["chchn_with_vat_uzs"] == 150000.0
    assert targets["chun_with_vat_uzs"] == 125000.0
    assert targets["wholesale_ex_vat_uzs"] == 111607.14
    assert targets["calc_base_ex_vat_uzs"] == 97049.69
    assert targets["rn_uzs"] == 93317.0
    assert targets["bx_share"] == 0.02
    assert targets["bx_uzs"] == 1866.34
    assert targets["b_uzs"] == 1866.35
    assert pytest.approx(targets["bx_uzs"], abs=0.05) == 0.02 * targets["rn_uzs"]
    assert pytest.approx(targets["rn_uzs"] + targets["b_uzs"] + targets["bx_uzs"], abs=1e-4) == 97049.69

    assert len(cfg["regions"]) == 14
    assert pytest.approx(sum(r["demand_weight"] for r in cfg["regions"]), abs=1e-6) == 1.0
    assert pytest.approx(sum(cfg["channels"].values()), abs=1e-6) == 1.0


def test_world_yaml_validation_errors(tmp_path: Path) -> None:
    from sim.generator.config_loader import load_world_config

    base = yaml.safe_load((CONFIG_DIR / "world.yaml").read_text(encoding="utf-8"))

    # Missing top-level key
    bad1 = dict(base)
    bad1.pop("regions")
    p1 = tmp_path / "bad_missing.yaml"
    p1.write_text(yaml.safe_dump(bad1), encoding="utf-8")
    with pytest.raises(ValueError, match="Missing"):
        load_world_config(p1)

    # Bad region weights
    bad2 = yaml.safe_load((CONFIG_DIR / "world.yaml").read_text(encoding="utf-8"))
    bad2["regions"][0]["demand_weight"] += 0.25
    p2 = tmp_path / "bad_weights.yaml"
    p2.write_text(yaml.safe_dump(bad2), encoding="utf-8")
    with pytest.raises(ValueError, match="demand_weight"):
        load_world_config(p2)

    # Bad channel weights
    bad3 = yaml.safe_load((CONFIG_DIR / "world.yaml").read_text(encoding="utf-8"))
    bad3["channels"]["distributors"] = 0.90
    p3 = tmp_path / "bad_channels.yaml"
    p3.write_text(yaml.safe_dump(bad3), encoding="utf-8")
    with pytest.raises(ValueError, match="channels"):
        load_world_config(p3)

    # Bad bx_share / bx_uzs formula (Order #3735 para. 19: Bx = 2% of Rn)
    bad4 = yaml.safe_load((CONFIG_DIR / "world.yaml").read_text(encoding="utf-8"))
    bad4["calibration_targets"]["bx_uzs"] = 1200.0
    p4 = tmp_path / "bad_bx.yaml"
    p4.write_text(yaml.safe_dump(bad4), encoding="utf-8")
    with pytest.raises(ValueError, match="bx_uzs"):
        load_world_config(p4)


def test_levels_yaml_valid_and_errors(tmp_path: Path) -> None:
    from sim.generator.config_loader import load_levels_config

    levels = load_levels_config(CONFIG_DIR / "levels.yaml")
    assert list(levels.keys()) == ["L0", "L1", "L2", "L3", "L4"]
    assert levels["L0"]["operators"] == []
    assert levels["L0"]["target_intensity"] == 0.0
    assert levels["L4"]["allow_unrecoverable"] is True
    assert "J1" in levels["L4"]["operators"]
    for lvl in ["L0", "L1", "L2", "L3"]:
        assert levels[lvl]["allow_unrecoverable"] is False

    # Invalid operator
    raw = yaml.safe_load((CONFIG_DIR / "levels.yaml").read_text(encoding="utf-8"))
    raw["levels"]["L1"]["operators"].append("UNKNOWN_OP")
    bad_op = tmp_path / "bad_op.yaml"
    bad_op.write_text(yaml.safe_dump(raw), encoding="utf-8")
    with pytest.raises(ValueError, match="operator"):
        load_levels_config(bad_op)

    # Unrecoverable disallowed on L2
    raw2 = yaml.safe_load((CONFIG_DIR / "levels.yaml").read_text(encoding="utf-8"))
    raw2["levels"]["L2"]["allow_unrecoverable"] = True
    bad_unrec = tmp_path / "bad_unrec.yaml"
    bad_unrec.write_text(yaml.safe_dump(raw2), encoding="utf-8")
    with pytest.raises(ValueError, match="allow_unrecoverable"):
        load_levels_config(bad_unrec)


def test_fx_cbu_csv_valid_and_errors(tmp_path: Path) -> None:
    from sim.generator.config_loader import load_fx_table

    df = load_fx_table(INPUTS_DIR / "fx_cbu.csv")
    assert len(df) == 405
    assert set(df.columns) >= {"date", "currency", "rate_uzs", "source"}
    assert set(df["currency"].unique()) == {"USD", "EUR", "RUB"}
    assert df["date"].min() == "2025-01-01"
    assert df["date"].max() == "2026-10-31"
    expected_prefix = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/all/"
    assert (df["source"] == expected_prefix + df["date"] + "/").all()

    usd_by_date = df[df["currency"] == "USD"].set_index("date")["rate_uzs"]
    assert usd_by_date["2025-01-01"] == pytest.approx(12920.48)
    assert usd_by_date["2025-01-23"] == pytest.approx(12990.0)
    assert usd_by_date["2025-01-31"] == pytest.approx(12957.01)
    assert usd_by_date["2025-12-30"] == pytest.approx(12025.33)

    eur = df[df["currency"] == "EUR"]["rate_uzs"]
    rub = df[df["currency"] == "RUB"]["rate_uzs"]
    assert usd_by_date.min() >= 11500 and usd_by_date.max() <= 13500
    assert eur.min() >= 13000 and eur.max() <= 15500
    assert rub.min() >= 115 and rub.max() <= 180

    bad_csv = tmp_path / "bad_fx.csv"
    bad_csv.write_text("date,currency,rate_uzs,source\n2025-01-01,USD,-10,https://cbu.uz/\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_fx_table(bad_csv)

    bad_date = tmp_path / "bad_fx_date.csv"
    bad_date.write_text(
        "date,currency,rate_uzs,source\n"
        "not-a-date,USD,12900,https://cbu.uz/\n"
        "2025-01-01,EUR,13500,https://cbu.uz/\n"
        "2025-01-01,RUB,130,https://cbu.uz/\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="date"):
        load_fx_table(bad_date)


def test_registry_sku_json_valid_and_errors(tmp_path: Path) -> None:
    from sim.generator.config_loader import load_registry_sku

    rec = load_registry_sku(INPUTS_DIR / "registry_sku.json")
    assert rec["sku_id"] == "SKU_CARDIO_01"
    assert rec["rn_uzs"] == 93317.0
    assert rec["bx_share"] == 0.02
    assert rec["bx_uzs"] == 1866.34
    assert rec["b_uzs"] == 1866.35
    assert rec["calc_base_ex_vat_uzs"] == 97049.69
    assert rec["wholesale_ex_vat_uzs"] == 111607.14
    assert rec["chun_with_vat_uzs"] == 125000.0
    assert rec["chchn_with_vat_uzs"] == 150000.0

    bad = dict(rec)
    bad["calc_base_ex_vat_uzs"] = 50000.0
    bad_path = tmp_path / "bad_reg.json"
    bad_path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="calc_base"):
        load_registry_sku(bad_path)

    bad_bx = dict(rec)
    bad_bx["bx_uzs"] = 1200.0
    bad_bx["b_uzs"] = 2532.69
    bad_bx_path = tmp_path / "bad_reg_bx.json"
    bad_bx_path.write_text(json.dumps(bad_bx), encoding="utf-8")
    with pytest.raises(ValueError, match="bx_uzs"):
        load_registry_sku(bad_bx_path)


def test_domain_dataclasses_and_seeded_generators() -> None:
    from sim.generator.domain import (
        DOC_TYPES,
        VALID_OPERATORS,
        WATERMARK_TEXT,
        CorruptionRecord,
        DocumentArtifact,
        SheetSpec,
        generate_address,
        generate_carrier_name,
        generate_counterparty_name,
        generate_person_name,
        generate_supplier_name,
        generate_synthetic_inn,
    )

    assert WATERMARK_TEXT == "УЧЕБНЫЙ ОБРАЗЕЦ / SYNTHETIC"
    assert len(VALID_OPERATORS) == 19
    assert len(DOC_TYPES) == 13

    rec = CorruptionRecord(
        operator="C1",
        field="unit_price",
        location={"page": 1, "bbox": [10, 20, 30, 40]},
        true_value={"amount": 4.85, "currency": "USD"},
        shown_value="4,85",
        recoverable=True,
        recover_hint="See contract D5",
    )
    art = DocumentArtifact(
        document="worlds/L2/documents/D3_quote.pdf",
        doc_type="D3",
        truth_refs=["quotes/q_0001"],
        corruptions=[rec],
    )
    art_dict = art.to_dict()
    assert art_dict["doc_type"] == "D3"
    assert art_dict["corruptions"][0]["operator"] == "C1"

    spec = SheetSpec(name="Sales", headers=["Month", "Qty"], rows=[["2025-01", 100]])
    assert spec.name == "Sales"

    rng1 = np.random.default_rng(42)
    rng2 = np.random.default_rng(42)
    seq1 = [
        generate_supplier_name(rng1),
        generate_carrier_name(rng1),
        generate_person_name(rng1),
        generate_address(rng1, "г. Ташкент"),
        generate_synthetic_inn(rng1),
    ]
    seq2 = [
        generate_supplier_name(rng2),
        generate_carrier_name(rng2),
        generate_person_name(rng2),
        generate_address(rng2, "г. Ташкент"),
        generate_synthetic_inn(rng2),
    ]
    assert seq1 == seq2
    assert "SYNTHETIC" in seq1[4]

    cp_rng1 = np.random.default_rng(7)
    cp_rng2 = np.random.default_rng(7)
    for ch in ("distributors", "pharmacy_chains", "hospital_tenders"):
        n1 = generate_counterparty_name(cp_rng1, ch, "Самаркандская область")
        n2 = generate_counterparty_name(cp_rng2, ch, "Самаркандская область")
        assert n1 == n2
        assert "(SYNTHETIC)" in n1
    with pytest.raises(ValueError, match="channel"):
        generate_counterparty_name(cp_rng1, "unknown_channel", "г. Ташкент")


def test_xlsx_io_roundtrip_styles_merges_and_determinism(tmp_path: Path) -> None:
    from sim.generator.domain import SheetSpec
    from sim.generator.xlsx_io import read_xlsx, write_xlsx

    sheets = [
        SheetSpec(
            name="Региональные продажи",
            pre_header_rows=[["Отчёт по продажам — УЧЕБНЫЙ ОБРАЗЕЦ / SYNTHETIC", None, None]],
            headers=["Регион", "Месяц", "Выручка"],
            rows=[
                ["г. Ташкент", "2025-01", 1250000.5],
                ["Самаркандская область", "2025-01", 420000],
                ["Бухарская область", "2025-02", None],
            ],
            col_widths=[28.0, 14.0, 18.0],
            merged_cells=["A1:C1"],
        ),
        SheetSpec(
            name="Summary",
            headers=["Metric", "Value"],
            rows=[["Margin", 0.025]],
            col_widths=[20.0, 12.0],
        ),
    ]

    p1 = tmp_path / "out1.xlsx"
    p2 = tmp_path / "out2.xlsx"
    write_xlsx(p1, sheets)
    write_xlsx(p2, sheets)

    assert _sha256(p1) == _sha256(p2)

    # Verify ZIP timestamps and OpenXML structure
    with zipfile.ZipFile(p1, "r") as zf:
        for info in zf.infolist():
            assert info.date_time == (2026, 1, 1, 0, 0, 0)
        sheet1_xml = zf.read("xl/worksheets/sheet1.xml").decode("utf-8")
        root = ET.fromstring(sheet1_xml)
        ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
        merges = root.findall(".//s:mergeCell", ns)
        assert [m.attrib["ref"] for m in merges] == ["A1:C1"]
        cols = root.findall(".//s:col", ns)
        assert len(cols) == 3
        header_cells = root.findall(".//s:row[@r='2']/s:c", ns)
        assert all(c.attrib.get("s") == "1" for c in header_cells)

    parsed = read_xlsx(p1)
    assert list(parsed.keys()) == ["Региональные продажи", "Summary"]
    s1 = parsed["Региональные продажи"]
    assert s1["merged_cells"] == ["A1:C1"]
    assert s1["all_rows"][0][0] == "Отчёт по продажам — УЧЕБНЫЙ ОБРАЗЕЦ / SYNTHETIC"
    assert s1["all_rows"][1] == ["Регион", "Месяц", "Выручка"]
    assert s1["all_rows"][2] == ["г. Ташкент", "2025-01", 1250000.5]
    assert s1["all_rows"][3] == ["Самаркандская область", "2025-01", 420000]
    assert s1["all_rows"][4][2] is None

    s2 = parsed["Summary"]
    assert s2["headers"] == ["Metric", "Value"]
    assert s2["rows"] == [["Margin", 0.025]]


def test_xlsx_io_rejects_empty_sheets(tmp_path: Path) -> None:
    from sim.generator.xlsx_io import write_xlsx

    with pytest.raises(ValueError, match="empty"):
        write_xlsx(tmp_path / "empty.xlsx", [])


def test_package_exports() -> None:
    import sim
    import sim.generator

    assert hasattr(sim, "__all__")
    assert hasattr(sim.generator, "SheetSpec")
    assert hasattr(sim.generator, "load_world_config")
    assert hasattr(sim.generator, "write_xlsx")
    with pytest.raises(AttributeError):
        _ = sim.non_existent_attr
    with pytest.raises(AttributeError):
        _ = sim.generator.non_existent_attr


def test_xlsx_bool_and_wide_columns_and_edge_errors(tmp_path: Path) -> None:
    from sim.generator.config_loader import load_fx_table, load_levels_config, load_world_config
    from sim.generator.domain import SheetSpec
    from sim.generator.xlsx_io import read_xlsx, write_xlsx

    wide_headers = [f"C{i}" for i in range(28)]
    wide_row = [True, False] + list(range(26))
    p = tmp_path / "wide.xlsx"
    write_xlsx(p, [SheetSpec(name="Wide", headers=wide_headers, rows=[wide_row, []])])
    parsed = read_xlsx(p)["Wide"]
    assert parsed["headers"][27] == "C27"
    assert parsed["rows"][0][:2] == [True, False]
    assert parsed["rows"][1] == []

    # Config edge validations
    not_dict = tmp_path / "scalar.yaml"
    not_dict.write_text("- item1\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_world_config(not_dict)
    with pytest.raises(ValueError):
        load_levels_config(not_dict)

    empty_lvl = tmp_path / "empty_lvl.yaml"
    empty_lvl.write_text("levels:\n  L0: {}\n", encoding="utf-8")
    with pytest.raises(ValueError, match="Missing keys in L0"):
        load_levels_config(empty_lvl)

    missing_lvl = tmp_path / "missing_lvl.yaml"
    missing_lvl.write_text(
        "levels:\n  L0:\n    name: x\n    intensity_range: [0,0]\n"
        "    target_intensity: 0.0\n    allow_unrecoverable: false\n    operators: []\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Missing level"):
        load_levels_config(missing_lvl)

    bad_fx_cols = tmp_path / "bad_fx_cols.csv"
    bad_fx_cols.write_text("date,currency\n2025-01-01,USD\n", encoding="utf-8")
    with pytest.raises(ValueError, match="missing columns"):
        load_fx_table(bad_fx_cols)

    bad_fx_curr = tmp_path / "bad_fx_curr.csv"
    bad_fx_curr.write_text("date,currency,rate_uzs,source\n2025-01-01,USD,12900,cbu\n", encoding="utf-8")
    with pytest.raises(ValueError, match="RUB"):
        load_fx_table(bad_fx_curr)

    # Wrong region count
    cfg = yaml.safe_load((CONFIG_DIR / "world.yaml").read_text(encoding="utf-8"))
    cfg["regions"] = cfg["regions"][:3]
    bad_reg_cnt = tmp_path / "bad_regions.yaml"
    bad_reg_cnt.write_text(yaml.safe_dump(cfg), encoding="utf-8")
    with pytest.raises(ValueError, match="14"):
        load_world_config(bad_reg_cnt)

    # Inline string & str cell parsing without sharedStrings.xml
    inline_xlsx = tmp_path / "inline.xlsx"
    with zipfile.ZipFile(inline_xlsx, "w") as zf:
        zf.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            '<sheets><sheet name="S1" sheetId="1" r:id="rId1"/></sheets></workbook>',
        )
        zf.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId1" Target="/xl/worksheets/sheet1.xml"/></Relationships>',
        )
        zf.writestr(
            "xl/worksheets/sheet1.xml",
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            '<sheetData><row r="1">'
            '<c r="A1" t="inlineStr"><is><t>Hello</t></is></c>'
            '<c r="B1" t="str"><v>FormulaStr</v></c>'
            "</row></sheetData></worksheet>",
        )
    parsed_inline = read_xlsx(inline_xlsx)["S1"]
    assert parsed_inline["headers"] == ["Hello", "FormulaStr"]
