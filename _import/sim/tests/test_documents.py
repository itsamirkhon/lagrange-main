"""TDD tests for document renderers and multi-level world document builders."""

from __future__ import annotations

import ast
import email
import email.policy
import hashlib
import json
import re
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

from sim.generator.domain import DOC_TYPES, WATERMARK_TEXT
from sim.generator.image_corruptions import apply_messenger_crop, apply_stamp_overlay
from sim.generator.xlsx_io import read_xlsx

_INTERNAL_ID_RE = re.compile(
    r"\b("
    r"SKU_CARDIO_01|SKU_BG_0[1-6]|PORTFOLIO_BG|"
    r"sup_0[1-4]|car_0[1-4]|rte_0[1-4]|"
    r"REG_[A-Z_]+|"
    r"q_\d{4}|cnt_\d{4}|shp_\d{4}|lot_\d{4}|cus_\d{4}|sal_\d{5}|cst_\d{4}|crd_\d{4}|reg_\d{4}|wo_\d{4}"
    r")\b"
)

_EXPECTED_1C_HEADERS = [
    "Период",
    "Документ реализации",
    "Контрагент",
    "Регион",
    "Канал сбыта",
    "Номенклатура",
    "Код 1С",
    "Серия",
    "Ед. изм.",
    "Количество",
    "Цена без НДС",
    "Сумма без НДС",
    "НДС 12%",
    "Сумма с НДС",
]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_register_fonts_reportlab_and_pillow():
    from sim.generator.doc_renderers import load_pil_font, register_reportlab_fonts

    registered = register_reportlab_fonts()
    names = set(pdfmetrics.getRegisteredFontNames())
    for font_name in ("DejaVuSans", "DejaVuSans-Bold", "DejaVuSans-Oblique", "DejaVuSansMono"):
        assert font_name in registered
        assert font_name in names

    assert load_pil_font("regular", 18) is not None
    assert load_pil_font("bold", 18) is not None
    assert load_pil_font("mono", 14) is not None


def test_render_pdf_multipage_watermark_and_determinism(tmp_path: Path):
    from sim.generator.doc_renderers import render_pdf

    pages = [
        {
            "header": {
                "title": "Коммерческое предложение № KP-2026-005",
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
                    "paragraphs": ["Цена CIP Ташкент составляет 7.25 USD за упаковку."],
                },
                {
                    "heading": "Позиции КП",
                    "table": {
                        "headers": ["Код", "Наименование", "MOQ", "Цена CIP USD"],
                        "rows": [["00-000142", "Kardiozan 10 мг №30", "5 000", "7.25"]],
                    },
                },
            ],
            "footer": "ООО «CardioImport Distribution» | Стр. 1 из 2",
        },
        {
            "header": {"title": "Спецификация (продолжение)"},
            "sections": [{"heading": "Примечания", "paragraphs": ["Срок действия: 30 дней."]}],
            "footer": "ООО «CardioImport Distribution» | Стр. 2 из 2",
        },
    ]

    pdf_a = tmp_path / "doc_a.pdf"
    pdf_b = tmp_path / "doc_b.pdf"
    render_pdf(pdf_a, pages, watermark=WATERMARK_TEXT)
    render_pdf(pdf_b, pages, watermark=WATERMARK_TEXT)

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
        {"text": "Служебная записка № М-01", "style": "heading"},
        {"text": "Дата: 2026-07-14 | Склад: Ташкент", "style": "bold"},
        {"text": "Kardiozan 10 мг №30: 12 000 уп.", "style": "body"},
        {"text": "Партия: KZ-202501-01 | Годен до: 2028-05-01", "style": "mono"},
    ]
    clean_png = tmp_path / "clean.png"
    clean_jpg_a = tmp_path / "doc_a.jpg"
    clean_jpg_b = tmp_path / "doc_b.jpg"
    stamped_jpg = tmp_path / "stamped.jpg"

    render_document_image(clean_png, "Внутренний документ", lines, watermark=WATERMARK_TEXT)
    render_document_image(clean_jpg_a, "Внутренний документ", lines, watermark=WATERMARK_TEXT)
    render_document_image(clean_jpg_b, "Внутренний документ", lines, watermark=WATERMARK_TEXT)

    rng = np.random.default_rng(123)
    render_document_image(
        stamped_jpg,
        "Внутренний документ",
        lines,
        watermark=WATERMARK_TEXT,
        postprocess_fn=lambda im: apply_stamp_overlay(im, (300, 320, 520, 380), rng),
    )

    assert _sha256(clean_jpg_a) == _sha256(clean_jpg_b)
    assert _sha256(clean_jpg_a) != _sha256(stamped_jpg)
    with Image.open(clean_png) as img:
        assert img.size == (1200, 1600)


def test_render_chat_screenshot_neutral_ui_and_crop(tmp_path: Path):
    from sim.generator.doc_renderers import render_chat_screenshot

    messages = [
        {"sender": "SilkWay Logistics", "side": "left", "text": "Добрый день! Авто фрахт через КЗ: 4 200 USD.", "time": "10:12"},
        {"sender": "Логист CardioImport", "side": "right", "text": "Принято, по ставке за упаковку 0.125 USD?", "time": "10:15"},
    ]
    full_a = tmp_path / "chat_a.png"
    full_b = tmp_path / "chat_b.png"
    cropped = tmp_path / "chat_cropped.png"

    render_chat_screenshot(full_a, messages, watermark=WATERMARK_TEXT)
    render_chat_screenshot(full_b, messages, watermark=WATERMARK_TEXT)
    rng = np.random.default_rng(42)
    render_chat_screenshot(cropped, messages, watermark=WATERMARK_TEXT, crop_fn=lambda im: apply_messenger_crop(im, rng))

    assert _sha256(full_a) == _sha256(full_b)
    with Image.open(full_a) as im_full, Image.open(cropped) as im_crop:
        assert im_full.width >= 800 and im_full.height >= 600
        assert im_crop.width < im_full.width or im_crop.height < im_full.height


def test_compute_chat_token_bbox_and_targeted_crop(tmp_path: Path):
    from sim.generator.doc_renderers import compute_chat_token_bbox, render_chat_screenshot

    messages = [
        {
            "sender": "SilkWay Forwarding",
            "side": "left",
            "wide": True,
            "text": "Assalomu alaykum! Auto KZ orqali CIP Toshkent yetkazib berish stavkasi: 0.125 USD",
            "time": "10:12",
        },
        {"sender": "Логист CardioImport", "side": "right", "text": "Rahmat, qabul qilindi.", "time": "10:14"},
    ]
    bbox = compute_chat_token_bbox(messages, 0, "0.125 USD", width=960)
    x1, y1, x2, y2 = bbox
    assert 650 <= x1 < x2 <= 940
    assert 100 <= y1 < y2 <= 300

    out_png = tmp_path / "chat_targeted_crop.png"
    render_chat_screenshot(
        out_png,
        messages,
        watermark=WATERMARK_TEXT,
        crop_fn=lambda im: im.crop((0, 0, (x1 + x2) // 2, im.height)),
    )
    with Image.open(out_png) as im:
        assert x1 < im.width < x2


def test_render_table_document_image_waybill_determinism(tmp_path: Path):
    from sim.generator.doc_renderers import compute_table_image_bboxes, render_table_document_image

    headers = ["№", "Наименование", "Код", "Серия", "Ед.", "Кол-во", "Цена", "Сумма"]
    rows = [
        ["1", "Kardiozan 10 мг №30", "00-000142", "KZ-202501-01", "упак.", "12 500", "111 607,14", "1 395 089 250,00"],
        ["2", "Angiokor 5 мг №30", "00-000201", "BG-202501-02", "упак.", "4 200", "65 476,19", "275 000 000,00"],
        ["3", "Tensormin 50 мг №28", "00-000202", "BG-202502-03", "упак.", "3 100", "83 333,33", "258 333 323,00"],
        ["4", "Lipidostab 20 мг №30", "00-000203", "BG-202503-04", "упак.", "2 800", "102 678,57", "287 500 000,00"],
        ["5", "Vazopril 10 мг №20", "00-000204", "BG-202504-05", "упак.", "5 000", "55 059,52", "275 297 600,00"],
        ["6", "Ritmokard 200 мг №30", "00-000205", "BG-202505-06", "упак.", "1 900", "122 767,86", "233 258 934,00"],
    ]
    meta = ["Дата: 2025-01-20 | Склад: Ташкент-Центральный", "Получатель: ООО «ShifoMed Plus» (SYNTHETIC)"]
    footer = ["ИТОГО / JAMI: 2 724 479 107,00 UZS", "Отпустил / Topshirdi: Азиз Каримов ____________"]

    bboxes = compute_table_image_bboxes(headers, rows, len(meta), width=1200)
    assert "row_0_qty" in bboxes and "total" in bboxes

    jpg_a = tmp_path / "waybill_a.jpg"
    jpg_b = tmp_path / "waybill_b.jpg"
    render_table_document_image(jpg_a, "РАСХОДНАЯ НАКЛАДНАЯ № РН-2025-0001 / YUK XATI", meta, headers, rows, footer_lines=footer)
    render_table_document_image(jpg_b, "РАСХОДНАЯ НАКЛАДНАЯ № РН-2025-0001 / YUK XATI", meta, headers, rows, footer_lines=footer)
    assert _sha256(jpg_a) == _sha256(jpg_b)
    with Image.open(jpg_a) as im:
        assert im.size == (1200, 1600)


def test_write_csv_doc_cp1251_semicolon_decimal_comma(tmp_path: Path):
    from sim.generator.doc_renderers import write_csv_doc

    out_csv = tmp_path / "export_1c.csv"
    headers = ["Месяц", "Регион", "Наименование", "Количество", "Сумма_без_НДС"]
    rows = [["2026-01", "г. Ташкент", "Kardiozan 10 мг №30", 1250, 139508925.5]]
    write_csv_doc(out_csv, headers, rows, encoding="cp1251", delimiter=";", decimal_comma=True)

    lines = out_csv.read_bytes().decode("cp1251").strip().splitlines()
    assert lines[0] == "Месяц;Регион;Наименование;Количество;Сумма_без_НДС"
    assert "139508925,5" in lines[1]


def test_write_eml_doc_with_and_without_attachment_deterministic(tmp_path: Path):
    from sim.generator.doc_renderers import write_eml_doc

    att = tmp_path / "quote.txt"
    att.write_text("Unit price: 7.25 USD", encoding="utf-8")

    eml_a = tmp_path / "msg_a.eml"
    eml_b = tmp_path / "msg_b.eml"
    eml_plain = tmp_path / "msg_plain.eml"
    write_eml_doc(eml_a, "Re: КП Kardiozan 10 мг №30", "procurement@cardioimport.synthetic", "sales@medisynth.synthetic", "Mon, 11 May 2026 09:30:00 +0500", "Просим подтвердить партию 5 000 уп.", attachment_path=att)
    write_eml_doc(eml_b, "Re: КП Kardiozan 10 мг №30", "procurement@cardioimport.synthetic", "sales@medisynth.synthetic", "Mon, 11 May 2026 09:30:00 +0500", "Просим подтвердить партию 5 000 уп.", attachment_path=att)
    write_eml_doc(eml_plain, "Уведомление", "info@cardioimport.synthetic", "team@cardioimport.synthetic", "Tue, 12 May 2026 10:00:00 +0500", "Без вложения.")

    assert _sha256(eml_a) == _sha256(eml_b)
    parsed = email.message_from_bytes(eml_a.read_bytes(), policy=email.policy.default)
    assert parsed["Subject"] == "Re: КП Kardiozan 10 мг №30"
    assert [p.get_filename() for p in parsed.walk() if p.get_filename()] == ["quote.txt"]
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


@pytest.fixture(scope="module")
def built_worlds(tmp_path_factory, world_fixture):
    from sim.generator.doc_builders import build_world

    world_cfg, levels_cfg, truth_tables = world_fixture
    base = tmp_path_factory.mktemp("worlds")
    manifests = {}
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        out_dir = base / lvl
        manifests[lvl] = build_world(lvl, levels_cfg[lvl], world_cfg, truth_tables, out_dir, seed=42)
    return base, manifests


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
    return {tbl: set(truth_tables[tbl][col].astype(str)) for tbl, col in pk_map.items()}


def _extract_doc_text(path: Path) -> str:
    ext = path.suffix.lower()
    if ext == ".csv":
        raw = path.read_bytes()
        try:
            return raw.decode("cp1251")
        except UnicodeDecodeError:
            return raw.decode("utf-8", errors="replace")
    if ext == ".xlsx":
        wb = read_xlsx(path)
        return "\n".join(" ".join(str(c) for c in row) for sh in wb.values() for row in sh["all_rows"])
    if ext == ".pdf":
        return subprocess.check_output(["pdftotext", str(path), "-"], text=True)
    if ext == ".eml":
        return path.read_text(encoding="utf-8", errors="replace")
    return ""


def test_build_world_full_truth_coverage_and_zero_internal_ids(world_fixture, built_worlds):
    _, _, truth_tables = world_fixture
    base, manifests = built_worlds
    id_sets = _table_id_sets(truth_tables)

    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        manifest = manifests[lvl]
        docs = manifest["documents"]
        assert len(docs) >= 60, f"Expected >= 60 docs in {lvl}, got {len(docs)}"
        present_types = {d["doc_type"] for d in docs}
        for dtype in DOC_TYPES:
            assert dtype in present_types, f"{dtype} missing in {lvl}"

        refs_by_table: dict[str, set[str]] = {}
        for entry in docs:
            fname = Path(entry["document"]).name
            assert not _INTERNAL_ID_RE.search(fname), f"Internal ID in filename {fname}"
            doc_path = base / lvl / "documents" / fname
            assert doc_path.exists()
            text = _extract_doc_text(doc_path)
            if text:
                match = _INTERNAL_ID_RE.search(text)
                assert match is None, f"Internal ID {match.group(0)!r} leaked in {lvl}/{fname}"
            assert entry["truth_refs"]
            for ref in entry["truth_refs"]:
                tbl, rid = ref.split("/", 1)
                assert rid in id_sets[tbl], f"Invalid truth_ref {ref}"
                refs_by_table.setdefault(tbl, set()).add(rid)

        if lvl == "L0":
            for tbl in ("quotes", "contracts", "shipments", "customs", "lots", "credit", "costs", "registry_events"):
                assert refs_by_table.get(tbl) == id_sets[tbl], f"Incomplete {tbl} coverage in L0"


def test_build_world_d1_1c_headers_and_d2_six_templates(built_worlds):
    base, _ = built_worlds
    l0_docs = base / "L0" / "documents"
    l1_docs = base / "L1" / "documents"

    csv_2025_l0 = (l0_docs / "D1_1c_sales_2025.csv").read_bytes().decode("cp1251").splitlines()
    csv_2026_l0 = (l0_docs / "D1_1c_sales_2026.csv").read_bytes().decode("cp1251").splitlines()
    assert csv_2025_l0[0].split(";") == _EXPECTED_1C_HEADERS
    assert csv_2026_l0[0].split(";") == _EXPECTED_1C_HEADERS

    csv_2026_l1 = (l1_docs / "D1_1c_sales_2026.csv").read_bytes().decode("cp1251").splitlines()
    assert len(csv_2026_l1) < len(csv_2026_l0), "L1 1C export should omit selected slices"

    d2_files = sorted(l0_docs.glob("D2_*.xlsx"))
    assert len(d2_files) == 6
    headers = set()
    for xlsx_path in d2_files:
        wb = read_xlsx(xlsx_path)
        sh = next(iter(wb.values()))
        assert len(sh["merged_cells"]) >= 1
        headers.add(tuple(sh["headers"]))
    assert len(headers) >= 4


def test_build_world_corruption_density_and_l4_unrecoverable(world_fixture, built_worlds):
    _, levels_cfg, _ = world_fixture
    _, manifests = built_worlds

    counts = {}
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        allowed = set(levels_cfg[lvl]["operators"])
        corrs = [c for d in manifests[lvl]["documents"] for c in d["corruptions"]]
        counts[lvl] = len(corrs)
        op_counts: dict[str, int] = {}
        for c in corrs:
            assert c["operator"] in allowed
            assert c["location"]["page"] >= 1
            assert len(c["location"]["bbox"]) == 4
            assert c["recover_hint"].strip()
            assert not _INTERNAL_ID_RE.search(c["recover_hint"]), f"Internal ID in hint: {c['recover_hint']}"
            op_counts[c["operator"]] = op_counts.get(c["operator"], 0) + 1
            if lvl != "L4":
                assert c["recoverable"] is True

        if lvl == "L0":
            assert len(corrs) == 0
        else:
            min_per_op = 10 if lvl == "L1" else 6
            for op in allowed:
                assert op_counts.get(op, 0) >= min_per_op, f"{lvl} {op} count={op_counts.get(op, 0)} < {min_per_op}"

    assert 40 <= counts["L1"] <= 75
    assert 115 <= counts["L2"] <= 180
    assert counts["L3"] >= 200
    assert counts["L4"] >= 320
    l4_corrs = [c for d in manifests["L4"]["documents"] for c in d["corruptions"]]
    assert sum(1 for c in l4_corrs if not c["recoverable"]) >= 20
    assert sum(1 for d in manifests["L4"]["documents"] if d["doc_type"] == "J1") == 5


def test_build_world_r4_crop_alignment_and_watermarks(built_worlds):
    base, manifests = built_worlds
    for lvl in ("L2", "L3", "L4"):
        d4_chats = [d for d in manifests[lvl]["documents"] if d["doc_type"] == "D4" and d["document"].endswith(".png")]
        assert len(d4_chats) == 2
        for entry in d4_chats:
            img_path = base / lvl / "documents" / Path(entry["document"]).name
            with Image.open(img_path) as im:
                r4_corrs = [c for c in entry["corruptions"] if c["operator"] == "R4"]
                assert len(r4_corrs) >= 3
                for c in r4_corrs:
                    x1, _, x2, _ = c["location"]["bbox"]
                    assert x1 < im.width < x2, f"R4 bbox {c['location']['bbox']} not sliced by width {im.width}"

    for lvl in ("L0", "L4"):
        docs_dir = base / lvl / "documents"
        for pdf_path in docs_dir.glob("*.pdf"):
            txt = subprocess.check_output(["pdftotext", str(pdf_path), "-"], text=True)
            assert WATERMARK_TEXT in txt, f"Watermark missing in {pdf_path.name}"


def test_build_world_determinism_sha256(tmp_path: Path, world_fixture):
    from sim.generator.doc_builders import build_world

    world_cfg, levels_cfg, truth_tables = world_fixture
    dir_a = tmp_path / "L3_a"
    dir_b = tmp_path / "L3_b"
    build_world("L3", levels_cfg["L3"], world_cfg, truth_tables, dir_a, seed=42)
    build_world("L3", levels_cfg["L3"], world_cfg, truth_tables, dir_b, seed=42)

    files_a = sorted(p.relative_to(dir_a) for p in dir_a.rglob("*") if p.is_file())
    files_b = sorted(p.relative_to(dir_b) for p in dir_b.rglob("*") if p.is_file())
    assert files_a == files_b
    for rel in files_a:
        assert _sha256(dir_a / rel) == _sha256(dir_b / rel)


def test_no_lagrange_rx_imports_in_doc_modules():
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
