"""TDD tests for document renderers and multi-level world document builders."""

from __future__ import annotations

import email
import email.policy
import hashlib
import json
import subprocess
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image
from reportlab.pdfbase import pdfmetrics

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from sim.generator.domain import WATERMARK_TEXT
from sim.generator.image_corruptions import apply_messenger_crop, apply_stamp_overlay


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_register_fonts_reportlab_and_pillow():
    from sim.generator.doc_renderers import load_pil_font, register_reportlab_fonts

    registered = register_reportlab_fonts()
    names = set(pdfmetrics.getRegisteredFontNames())
    for font_name in ("DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique", "DejaVuSansMono"):
        assert font_name in registered
        assert font_name in names

    pil_regular = load_pil_font("regular", 18)
    pil_bold = load_pil_font("bold", 18)
    pil_mono = load_pil_font("mono", 14)
    assert pil_regular is not None
    assert pil_bold is not None
    assert pil_mono is not None


def test_render_pdf_multipage_watermark_and_determinism(tmp_path: Path):
    from sim.generator.doc_renderers import render_pdf

    pages = [
        {
            "header": {
                "title": "Коммерческое предложение № Q-2026-05",
                "subtitle": "Поставщик: Medisynth Pvt. Ltd. (SYNTHETIC)",
                "meta": "Дата: 2026-05-10 | Валюта: USD",
            },
            "sections": [
                {
                    "heading": "Условия поставки / Delivery Terms",
                    "kv_rows": [
                        ("Препарат", "Kardiozan 10 мг №30 (Kardiozartan)"),
                        ("Регион", "Fargʻona viloyati / Тошкент шаҳри"),
                    ],
                    "paragraphs": [
                        "Цена CIP Ташкент составляет 7.25 USD за упаковку при минимальной партии 5 000 уп.",
                    ],
                },
                {
                    "heading": "Позиции КП",
                    "table": {
                        "headers": ["SKU", "Наименование", "MOQ", "Цена CIP USD"],
                        "rows": [
                            ["SKU_CARDIO_01", "Kardiozan 10 мг №30", "5 000", "7.25"],
                            ["SKU_BG_01", "Angiokor 5 мг №30", "3 000", "4.10"],
                        ],
                    },
                },
            ],
            "footer": "ООО «CardioImport Distribution» | Стр. 1 из 2",
        },
        {
            "header": {"title": "Спецификация (продолжение)"},
            "sections": [
                {
                    "heading": "Примечания",
                    "paragraphs": ["Срок действия предложения: 30 календарных дней."],
                }
            ],
            "footer": "ООО «CardioImport Distribution» | Стр. 2 из 2",
        },
    ]

    pdf_a = tmp_path / "doc_a.pdf"
    pdf_b = tmp_path / "doc_b.pdf"
    render_pdf(pdf_a, pages, watermark=WATERMARK_TEXT)
    render_pdf(pdf_b, pages, watermark=WATERMARK_TEXT)

    assert pdf_a.exists()
    assert pdf_a.read_bytes().startswith(b"%PDF")
    assert _sha256(pdf_a) == _sha256(pdf_b)

    extracted = subprocess.check_output(["pdftotext", str(pdf_a), "-"], text=True)
    page_texts = [p for p in extracted.split("\x0c") if p.strip()]
    assert len(page_texts) == 2
    for page_txt in page_texts:
        assert WATERMARK_TEXT in page_txt
    assert "Kardiozan 10 мг №30" in extracted
    assert "Fargʻona viloyati" in extracted


def test_render_document_image_determinism_watermark_and_postprocess(tmp_path: Path):
    from sim.generator.doc_renderers import render_document_image

    lines = [
        {"text": "Накладная на отгрузку № WB-0102", "style": "heading"},
        {"text": "Дата: 2026-07-14 | Склад: Ташкент", "style": "bold"},
        {"text": "SKU_CARDIO_01 — Kardiozan 10 мг №30: 12 000 уп.", "style": "body"},
        {"text": "Партия: lot_0015 | Срок годности: 2028-05-01", "style": "mono"},
    ]

    clean_png = tmp_path / "clean.png"
    clean_jpg_a = tmp_path / "doc_a.jpg"
    clean_jpg_b = tmp_path / "doc_b.jpg"
    stamped_jpg = tmp_path / "stamped.jpg"

    render_document_image(clean_png, "Складская накладная", lines, watermark=WATERMARK_TEXT)
    render_document_image(clean_jpg_a, "Складская накладная", lines, watermark=WATERMARK_TEXT)
    render_document_image(clean_jpg_b, "Складская накладная", lines, watermark=WATERMARK_TEXT)

    rng = np.random.default_rng(123)
    render_document_image(
        stamped_jpg,
        "Складская накладная",
        lines,
        watermark=WATERMARK_TEXT,
        postprocess_fn=lambda im: apply_stamp_overlay(im, (300, 320, 520, 380), rng),
    )

    assert clean_png.exists() and clean_jpg_a.exists() and stamped_jpg.exists()
    assert _sha256(clean_jpg_a) == _sha256(clean_jpg_b)
    assert _sha256(clean_jpg_a) != _sha256(stamped_jpg)

    with Image.open(clean_png) as img:
        assert img.size == (1200, 1600)
        # Footer watermark strip at bottom differs from pure white background
        footer_strip = np.asarray(img.crop((0, 1572, 1200, 1600)))
        assert footer_strip.std() > 5.0


def test_render_chat_screenshot_neutral_ui_and_crop(tmp_path: Path):
    from sim.generator.doc_renderers import render_chat_screenshot

    messages = [
        {"sender": "SilkWay Logistics", "side": "left", "text": "Добрый день! Авто фрахт через КЗ: 4 200 USD.", "time": "10:12"},
        {"sender": "Логист CardioImport", "side": "right", "text": "Принято, по ставке за упаковку 0.34 USD/pack?", "time": "10:15"},
        {"sender": "SilkWay Logistics", "side": "left", "text": "Да, срок 12 дней, без учёта страховки.", "time": "10:18"},
    ]

    full_a = tmp_path / "chat_a.png"
    full_b = tmp_path / "chat_b.png"
    cropped = tmp_path / "chat_cropped.png"

    render_chat_screenshot(full_a, messages, watermark=WATERMARK_TEXT)
    render_chat_screenshot(full_b, messages, watermark=WATERMARK_TEXT)
    rng = np.random.default_rng(42)
    render_chat_screenshot(
        cropped,
        messages,
        watermark=WATERMARK_TEXT,
        crop_fn=lambda im: apply_messenger_crop(im, rng),
    )

    assert _sha256(full_a) == _sha256(full_b)
    with Image.open(full_a) as im_full, Image.open(cropped) as im_crop:
        assert im_full.width >= 800 and im_full.height >= 600
        assert im_crop.width < im_full.width or im_crop.height < im_full.height


def test_write_csv_doc_cp1251_semicolon_decimal_comma(tmp_path: Path):
    from sim.generator.doc_renderers import write_csv_doc

    out_csv = tmp_path / "export_1c.csv"
    headers = ["Месяц", "Регион", "SKU", "Количество", "Сумма_без_НДС"]
    rows = [
        ["2026-01", "г. Ташкент", "SKU_CARDIO_01", 1250, 139508925.5],
        ["2026-01", "Самаркандская область", "SKU_CARDIO_01", 600, 66964284.0],
    ]
    write_csv_doc(out_csv, headers, rows, encoding="cp1251", delimiter=";", decimal_comma=True)

    raw = out_csv.read_bytes()
    text = raw.decode("cp1251")
    lines = text.strip().splitlines()
    assert lines[0] == "Месяц;Регион;SKU;Количество;Сумма_без_НДС"
    assert "139508925,5" in lines[1] or "139508925,50" in lines[1]
    assert "г. Ташкент" in lines[1]


def test_write_eml_doc_with_and_without_attachment_deterministic(tmp_path: Path):
    from sim.generator.doc_renderers import write_eml_doc

    att = tmp_path / "quote.txt"
    att.write_text("Unit price: 7.25 USD", encoding="utf-8")

    eml_a = tmp_path / "msg_a.eml"
    eml_b = tmp_path / "msg_b.eml"
    eml_plain = tmp_path / "msg_plain.eml"

    write_eml_doc(
        eml_a,
        subject="Re: КП Kardiozan 10 мг №30",
        sender="procurement@cardioimport.synthetic",
        recipient="sales@medisynth.synthetic",
        date_str="Mon, 11 May 2026 09:30:00 +0500",
        body="Просим подтвердить партию 5 000 упаковок.",
        attachment_path=att,
    )
    write_eml_doc(
        eml_b,
        subject="Re: КП Kardiozan 10 мг №30",
        sender="procurement@cardioimport.synthetic",
        recipient="sales@medisynth.synthetic",
        date_str="Mon, 11 May 2026 09:30:00 +0500",
        body="Просим подтвердить партию 5 000 упаковок.",
        attachment_path=att,
    )
    write_eml_doc(
        eml_plain,
        subject="Уведомление",
        sender="info@cardioimport.synthetic",
        recipient="team@cardioimport.synthetic",
        date_str="Tue, 12 May 2026 10:00:00 +0500",
        body="Без вложения.",
        attachment_path=None,
    )

    assert _sha256(eml_a) == _sha256(eml_b)
    parsed = email.message_from_bytes(eml_a.read_bytes(), policy=email.policy.default)
    assert parsed["Subject"] == "Re: КП Kardiozan 10 мг №30"
    assert parsed["Date"] == "Mon, 11 May 2026 09:30:00 +0500"
    assert parsed.is_multipart()
    filenames = [part.get_filename() for part in parsed.walk() if part.get_filename()]
    assert "quote.txt" in filenames
    assert not email.message_from_bytes(eml_plain.read_bytes(), policy=email.policy.default).is_multipart()


@pytest.fixture(scope="module")
def world_fixture(tmp_path_factory):
    from sim.generator.config_loader import load_levels_config, load_world_config
    from sim.generator.truth_gen import generate_truth

    world_cfg = load_world_config(ROOT / "sim" / "config" / "world.yaml")
    levels_cfg = load_levels_config(ROOT / "sim" / "config" / "levels.yaml")
    truth_dir = tmp_path_factory.mktemp("doc_truth")
    truth_tables = generate_truth(world_cfg, seed=42, truth_dir=truth_dir, inputs_dir=ROOT / "sim" / "inputs")
    return world_cfg, levels_cfg, truth_tables


def _table_id_sets(truth_tables: dict) -> dict[str, set[str]]:
    pk_map = {
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
    return {
        tbl: set(truth_tables[tbl][col].astype(str))
        for tbl, col in pk_map.items()
        if tbl in truth_tables and col in truth_tables[tbl].columns
    }


def test_build_world_all_levels_doc_types_and_manifest_schema(tmp_path: Path, world_fixture):
    from sim.generator.doc_builders import build_world
    from sim.generator.domain import DOC_TYPES

    world_cfg, levels_cfg, truth_tables = world_fixture
    id_sets = _table_id_sets(truth_tables)

    for level_name in ("L0", "L1", "L2", "L3", "L4"):
        out_dir = tmp_path / level_name
        manifest = build_world(level_name, levels_cfg[level_name], world_cfg, truth_tables, out_dir, seed=42)
        manifest_path = out_dir / "manifest.json"
        assert manifest_path.exists()
        loaded = json.loads(manifest_path.read_text(encoding="utf-8"))
        assert loaded == manifest
        assert loaded["level"] == level_name
        assert loaded["seed"] == 42

        present_types = {entry["doc_type"] for entry in loaded["documents"]}
        for dtype in DOC_TYPES:
            assert dtype in present_types, f"{dtype} missing in {level_name}"

        allowed_ops = set(levels_cfg[level_name]["operators"])
        all_corruptions = []
        for entry in loaded["documents"]:
            doc_file = out_dir / "documents" / Path(entry["document"]).name
            assert doc_file.exists(), f"Missing document file: {doc_file}"
            assert entry["truth_refs"], f"Empty truth_refs on {entry['document']}"
            for ref in entry["truth_refs"]:
                tbl, rid = ref.split("/", 1)
                assert rid in id_sets[tbl], f"Invalid truth_ref {ref}"
            for corr in entry["corruptions"]:
                all_corruptions.append(corr)
                assert corr["operator"] in allowed_ops
                assert corr["location"]["page"] >= 1
                assert len(corr["location"]["bbox"]) == 4
                assert corr["recover_hint"].strip()
                if level_name != "L4":
                    assert corr["recoverable"] is True

        if level_name == "L0":
            assert len(all_corruptions) == 0
        else:
            assert len(all_corruptions) > 0
            seen_ops = {c["operator"] for c in all_corruptions}
            assert seen_ops == allowed_ops, f"Mismatch ops in {level_name}: {allowed_ops - seen_ops}"
        if level_name == "L4":
            assert "J1" in present_types
            assert any(not c["recoverable"] for c in all_corruptions)
            assert loaded.get("omitted_documents")


def test_build_world_d1_d2_xlsx_templates_and_determinism(tmp_path: Path, world_fixture):
    from sim.generator.doc_builders import build_world
    from sim.generator.xlsx_io import read_xlsx

    world_cfg, levels_cfg, truth_tables = world_fixture
    dir_a = tmp_path / "L3_a"
    dir_b = tmp_path / "L3_b"
    build_world("L3", levels_cfg["L3"], world_cfg, truth_tables, dir_a, seed=42)
    build_world("L3", levels_cfg["L3"], world_cfg, truth_tables, dir_b, seed=42)

    files_a = sorted(p.relative_to(dir_a) for p in dir_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(dir_b) for p in dir_b.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert _sha256(dir_a / rel) == _sha256(dir_b / rel), f"Non-deterministic {rel}"

    csv_text = (dir_a / "documents" / "D1_1c_sales_export.csv").read_bytes().decode("cp1251")
    assert ";" in csv_text.splitlines()[0]

    d2_headers = []
    for fname in (
        "D2_regional_sales_central.xlsx",
        "D2_regional_sales_fergana.xlsx",
        "D2_regional_sales_west_south.xlsx",
    ):
        wb = read_xlsx(dir_a / "documents" / fname)
        sheet = next(iter(wb.values()))
        assert len(sheet["merged_cells"]) >= 1
        d2_headers.append(tuple(sheet["all_rows"][0]))
    assert len(set(d2_headers)) == 3


def test_build_world_watermarks_on_all_pdfs_and_images(tmp_path: Path, world_fixture):
    from sim.generator.doc_builders import build_world

    world_cfg, levels_cfg, truth_tables = world_fixture
    for level_name in ("L0", "L4"):
        out_dir = tmp_path / f"wm_{level_name}"
        build_world(level_name, levels_cfg[level_name], world_cfg, truth_tables, out_dir, seed=42)
        docs_dir = out_dir / "documents"
        for pdf_path in docs_dir.glob("*.pdf"):
            txt = subprocess.check_output(["pdftotext", str(pdf_path), "-"], text=True)
            assert WATERMARK_TEXT in txt, f"Watermark missing in {pdf_path.name}"
        for img_path in list(docs_dir.glob("*.png")) + list(docs_dir.glob("*.jpg")):
            with Image.open(img_path) as im:
                assert im.width > 100 and im.height > 100


def test_no_lagrange_rx_imports_in_doc_modules():
    import ast

    for fname in ("doc_renderers.py", "doc_builders.py"):
        tree = ast.parse((ROOT / "sim" / "generator" / fname).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    assert "lagrange_rx" not in alias.name
                    assert not alias.name.startswith("src")
            elif isinstance(node, ast.ImportFrom):
                mod = node.module or ""
                assert "lagrange_rx" not in mod
                assert not mod.startswith("src")
