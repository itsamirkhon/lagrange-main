"""Multi-level world document builder (D1-D13 + J1) and manifest generator."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sim.generator.corruptions import (
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
)
from sim.generator.doc_renderers import (
    render_chat_screenshot,
    render_document_image,
    render_pdf,
    write_csv_doc,
    write_eml_doc,
)
from sim.generator.domain import DocumentArtifact, SheetSpec, WATERMARK_TEXT
from sim.generator.image_corruptions import (
    apply_handwritten_correction,
    apply_messenger_crop,
    apply_scan_photo_effects,
    apply_stamp_overlay,
)
from sim.generator.xlsx_io import write_xlsx

_LEVEL_SALT: dict[str, int] = {"L0": 100, "L1": 200, "L2": 300, "L3": 400, "L4": 500}


@dataclass
class _WorldCtx:
    """Shared deterministic build context for one chaos level."""

    level_name: str
    level_cfg: dict[str, Any]
    world_cfg: dict[str, Any]
    truth: dict[str, pd.DataFrame]
    docs_dir: Path
    rng: np.random.Generator
    active_ops: set[str]
    allow_unrec: bool
    watermark: str = WATERMARK_TEXT
    artifacts: list[dict[str, Any]] = field(default_factory=list)
    omitted_documents: list[dict[str, Any]] = field(default_factory=list)

    def has(self, op: str) -> bool:
        return op in self.active_ops

    def doc_rel_path(self, filename: str) -> str:
        return f"worlds/{self.level_name}/documents/{filename}"

    def add_artifact(
        self,
        filename: str,
        doc_type: str,
        truth_refs: list[str],
        corruptions: list[dict[str, Any]] | None = None,
    ) -> None:
        artifact = DocumentArtifact(
            document=self.doc_rel_path(filename),
            doc_type=doc_type,
            truth_refs=list(truth_refs),
            corruptions=[],
        ).to_dict()
        artifact["corruptions"] = list(corruptions or [])
        self.artifacts.append(artifact)

    def corr(
        self,
        op: str,
        field_name: str,
        page: int,
        bbox: list[int],
        true_val: Any,
        shown_val: Any,
        hint: str,
        recoverable: bool = True,
    ) -> dict[str, Any]:
        rec = make_corruption_record(op, field_name, page, bbox, true_val, shown_val, recoverable, hint)
        return rec


def _first_refs(df: pd.DataFrame, table: str, id_col: str, limit: int = 3) -> list[str]:
    if df.empty:
        return [f"{table}/empty"]
    return [f"{table}/{val}" for val in df[id_col].astype(str).head(limit).tolist()]


def _build_d1_csv(ctx: _WorldCtx) -> None:
    sales = ctx.truth["sales"]
    corrs: list[dict[str, Any]] = []
    filtered = sales
    if ctx.allow_unrec and ctx.has("M1"):
        mask = (sales["month"] == "2026-08") & (sales["region_id"] == "REG_FER")
        omitted_ids = sales.loc[mask, "sale_id"].astype(str).head(3).tolist()
        filtered = sales.loc[~mask]
        ref_id = omitted_ids[0] if omitted_ids else str(sales.iloc[0]["sale_id"])
        corrs.append(
            ctx.corr(
                "M1",
                "monthly_sales_2026_08_fergana",
                1,
                [40, 120, 520, 160],
                {"month": "2026-08", "region_id": "REG_FER", "rows": int(mask.sum())},
                "OMITTED",
                "Missing 2026-08 Fergana slice in both 1C export and regional workbook; request original 1C backup from client",
                recoverable=False,
            )
        )
        ctx.omitted_documents.append(
            {
                "doc_type": "D1",
                "scope": "2026-08_REG_FER",
                "truth_ref": f"sales/{ref_id}",
                "reason": "2026-08 Fergana sales omitted from 1C export and D2 Fergana report",
                "recoverable": False,
            }
        )

    headers = ["sale_id", "month", "region_id", "channel", "sku_id", "lot_id", "qty_packs", "price_ex_vat_uzs", "revenue_ex_vat_uzs"]
    rows = [
        [r["sale_id"], r["month"], r["region_id"], r["channel"], r["sku_id"], r["lot_id"], int(r["qty_packs"]), float(r["unit_price_ex_vat_uzs"]), float(r["revenue_ex_vat_uzs"])]
        for r in filtered.head(360).to_dict("records")
    ]
    write_csv_doc(ctx.docs_dir / "D1_1c_sales_export.csv", headers, rows, encoding="cp1251", delimiter=";", decimal_comma=True)
    ctx.add_artifact("D1_1c_sales_export.csv", "D1", _first_refs(sales, "sales", "sale_id", 5), corrs)


def _d2_template_spec(style: str) -> dict[str, Any]:
    templates = {
        "central": {
            "pre": [["Отчёт регионального менеджера: Центральный округ (SYNTHETIC)"], ["Компания:", "ООО «CardioImport Distribution»", "Валюта:", "UZS"]],
            "headers": ["Месяц", "Регион", "Канал", "SKU", "Упаковок", "Выручка без НДС"],
            "merges": ["A1:F1", "B2:C2"],
            "widths": [12.0, 26.0, 18.0, 16.0, 12.0, 18.0],
        },
        "fergana": {
            "pre": [["Fargʻona vodiysi boʻyicha savdo hisoboti (SYNTHETIC)"]],
            "headers": ["Oy", "Hudud / Ҳудуд", "SKU", "Kanal", "Sotuv (up.)", "Summa (so'm)"],
            "merges": ["A1:F1"],
            "widths": [12.0, 28.0, 16.0, 18.0, 12.0, 18.0],
        },
        "west_south": {
            "pre": [["Regional Sales Summary — West & South (SYNTHETIC)"], ["Prepared for Finance Dept", "", "Unit: packs"]],
            "headers": ["Region", "Period", "SKU", "Channel", "Qty Packs", "Revenue ex VAT"],
            "merges": ["A1:F1", "A2:B2"],
            "widths": [28.0, 12.0, 16.0, 18.0, 12.0, 18.0],
        },
    }
    return templates[style]


def _drop_d2_m1_month(
    ctx: _WorldCtx,
    grouped: pd.DataFrame,
    style: str,
    corrs: list[dict[str, Any]],
) -> pd.DataFrame:
    if not ctx.has("M1"):
        return grouped
    is_unrec = ctx.allow_unrec and style == "fergana"
    drop_month = "2026-08" if is_unrec else "2026-05"
    drop_mask = grouped["month"] == drop_month
    if not drop_mask.any():
        return grouped
    dropped = grouped.loc[drop_mask].iloc[0]
    hint = "Request missing 2026-08 regional report from client" if is_unrec else "Recover missing month from D1_1c_sales_export.csv"
    corrs.append(ctx.corr("M1", f"monthly_sales_{drop_month}", 1, [40, 200, 480, 240], {"month": drop_month, "qty_packs": int(dropped["qty_packs"])}, "MISSING_MONTH", hint, recoverable=not is_unrec))
    if is_unrec:
        ctx.omitted_documents.append({"doc_type": "D2", "scope": "2026-08_fergana", "truth_ref": f"sales/{dropped['sale_id']}", "reason": "Missing 2026-08 Fergana regional sales rows", "recoverable": False})
    return grouped.loc[~drop_mask]


def _format_d2_row(
    ctx: _WorldCtx,
    rec: dict[str, Any],
    idx: int,
    reg_map: dict[str, dict[str, Any]],
    style: str,
    corrs: list[dict[str, Any]],
) -> list[Any]:
    reg = reg_map[rec["region_id"]]
    name_key = {"fergana": "name_uz_lat", "central": "name_ru"}.get(style, "name_uz_cyr")
    rname = reg[name_key]
    if ctx.has("L1") and idx == 1:
        shown_rname = corrupt_language_mix(rname, ctx.rng)
        corrs.append(ctx.corr("L1", "region_name", 1, [120, 180, 260, 200], rname, shown_rname, "Match mixed Uzbek Latin/Cyrillic region name to world config"))
        rname = shown_rname
    rev_val: Any = round(float(rec["revenue"]), 2)
    if ctx.has("N1") and idx == 0:
        shown_num = corrupt_number(rev_val, ctx.rng)
        corrs.append(ctx.corr("N1", "revenue_ex_vat_uzs", 1, [360, 160, 460, 180], rev_val, shown_num, "Normalize number separators against D1_1c_sales_export.csv"))
        rev_val = shown_num
    row_layouts = {
        "west_south": [rname, rec["month"], "SKU_CARDIO_01", rec["channel"], int(rec["qty_packs"]), rev_val],
        "fergana": [rec["month"], rname, "SKU_CARDIO_01", rec["channel"], int(rec["qty_packs"]), rev_val],
        "central": [rec["month"], rname, rec["channel"], "SKU_CARDIO_01", int(rec["qty_packs"]), rev_val],
    }
    return row_layouts[style]


def _build_d2_workbook(ctx: _WorldCtx, filename: str, region_ids: list[str], style: str) -> None:
    sales = ctx.truth["sales"]
    reg_map = {r["region_id"]: r for r in ctx.world_cfg["regions"]}
    sub = sales[(sales["sku_id"] == "SKU_CARDIO_01") & (sales["region_id"].isin(region_ids))]
    grouped = sub.groupby(["month", "region_id", "channel"], as_index=False).agg(
        qty_packs=("qty_packs", "sum"),
        revenue=("revenue_ex_vat_uzs", "sum"),
        sale_id=("sale_id", "first"),
    )
    corrs: list[dict[str, Any]] = []
    grouped = _drop_d2_m1_month(ctx, grouped, style, corrs)
    tmpl = _d2_template_spec(style)
    rows = [_format_d2_row(ctx, rec, idx, reg_map, style, corrs) for idx, rec in enumerate(grouped.head(36).to_dict("records"))]
    sheet = SheetSpec(name=style.title(), headers=tmpl["headers"], rows=rows, col_widths=tmpl["widths"], merged_cells=tmpl["merges"], pre_header_rows=tmpl["pre"])
    write_xlsx(ctx.docs_dir / filename, [sheet])
    refs = [f"sales/{sid}" for sid in grouped["sale_id"].astype(str).head(4).tolist()] or _first_refs(sales, "sales", "sale_id", 2)
    ctx.add_artifact(filename, "D2", refs, corrs)


def _build_d2_all(ctx: _WorldCtx) -> None:
    _build_d2_workbook(ctx, "D2_regional_sales_central.xlsx", ["REG_TAS_C", "REG_TAS_R", "REG_SYR", "REG_JIZ"], "central")
    _build_d2_workbook(ctx, "D2_regional_sales_fergana.xlsx", ["REG_FER", "REG_AND", "REG_NAM"], "fergana")
    _build_d2_workbook(ctx, "D2_regional_sales_west_south.xlsx", ["REG_SAM", "REG_KAS", "REG_BUK", "REG_SUR", "REG_KHO", "REG_KAR", "REG_NAV"], "west_south")


def _apply_d3_corruptions(
    ctx: _WorldCtx,
    quote: dict[str, Any],
    lang: str,
    flags: tuple[bool, bool, bool],
) -> tuple[str, int, str, list[dict[str, Any]]]:
    apply_c1, apply_x1, apply_s1 = flags
    ops = {
        op
        for op, enabled in (("C1", apply_c1), ("X1", apply_x1), ("S1", apply_s1), ("L1", lang == "ru"))
        if enabled and ctx.has(op)
    }
    corrs: list[dict[str, Any]] = []
    price_str = f"{float(quote['unit_price']):.2f} {quote['currency']}"
    if "C1" in ops:
        price_str = corrupt_currency_omit(quote["unit_price"], quote["currency"])
        corrs.append(ctx.corr("C1", "unit_price", 1, [280, 340, 380, 360], {"amount": float(quote["unit_price"]), "currency": quote["currency"]}, price_str, "Currency stated in D5 supplier contract and D6 invoice"))

    moq_val = int(quote["min_order_qty"])
    if "X1" in ops:
        corrs.append(ctx.corr("X1", "min_order_qty", 1, [280, 360, 380, 380], moq_val, moq_val * 2, "Signed D5 contract MOQ supersedes preliminary D3 quote"))
        moq_val *= 2

    valid_str = str(quote["valid_until"])
    if "S1" in ops:
        corrs.append(ctx.corr("S1", "valid_until", 1, [160, 260, 300, 280], {"status": "expired", "valid_until": valid_str}, f"Valid quote ({valid_str})", "Quote expired before 2026-08; verify active D5 addendum"))

    sku_name = "Kardiozan 10 мг №30"
    if "L1" in ops:
        sku_name = corrupt_language_mix(sku_name, ctx.rng)
        corrs.append(ctx.corr("L1", "trade_name", 1, [120, 340, 260, 360], "Kardiozan 10 мг №30", sku_name, "Normalize SKU spelling to registry SKU_CARDIO_01"))
    return price_str, moq_val, sku_name, corrs


def _build_d3_quote(ctx: _WorldCtx, quote: dict[str, Any], filename: str, lang: str, flags: tuple[bool, bool, bool]) -> None:
    sup_df = ctx.truth["suppliers"]
    sup = sup_df[sup_df["supplier_id"] == quote["entity_id"]].iloc[0].to_dict()
    price_str, moq_val, sku_name, corrs = _apply_d3_corruptions(ctx, quote, lang, flags)
    title = "COMMERCIAL QUOTATION" if lang == "en" else "КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ"
    pages = [{
        "header": {"title": f"{title} #{quote['quote_id']}", "subtitle": f"Supplier: {sup['name']}", "meta": f"Date: {quote['quote_date']} | Valid until: {quote['valid_until']}"},
        "sections": [
            {"heading": "Terms & Conditions" if lang == "en" else "Условия поставки", "kv_rows": [("SKU", f"{quote['sku_id']} ({sku_name})"), ("Payment", quote["terms"]), ("Lead Time", f"{quote['lead_time_days']} days"), ("MOQ", f"{moq_val} packs")]},
            {"heading": "Pricing", "table": {"headers": ["SKU", "Description", "MOQ", "Unit Price CIP"], "rows": [[quote["sku_id"], sku_name, str(moq_val), price_str]]}},
        ],
        "footer": f"ООО «CardioImport Distribution» | {filename} | Page 1",
    }]
    render_pdf(ctx.docs_dir / filename, pages, watermark=ctx.watermark)
    ctx.add_artifact(filename, "D3", [f"quotes/{quote['quote_id']}"], corrs)


def _build_d3_all(ctx: _WorldCtx) -> None:
    sup_quotes = ctx.truth["quotes"][ctx.truth["quotes"]["quote_type"] == "supplier"]
    q_a = sup_quotes.iloc[0].to_dict()
    q_b = sup_quotes.iloc[1].to_dict() if len(sup_quotes) > 1 else q_a
    _build_d3_quote(ctx, q_a, "D3_quote_supplier_A_2026-05.pdf", "en", (False, True, False))
    _build_d3_quote(ctx, q_b, "D3_quote_supplier_B_2026-08.pdf", "ru", (True, False, True))


def _build_d4_all(ctx: _WorldCtx) -> None:
    car_quotes = ctx.truth["quotes"][ctx.truth["quotes"]["quote_type"] == "carrier"]
    q_car = car_quotes.iloc[0].to_dict()
    routes = ctx.truth["routes"]
    carriers = ctx.truth["carriers"].set_index("carrier_id")

    corrs_pdf: list[dict[str, Any]] = []
    vat_text = "без НДС (0% экспорт/транзит)"
    price_val = float(q_car["unit_price"])
    if ctx.has("V1"):
        v_info = corrupt_vat_label(price_val, includes_vat=False, rng=ctx.rng)
        vat_text = v_info["shown_label"] or "НДС не указан"
        price_val = float(v_info["shown_price"])
        corrs_pdf.append(ctx.corr("V1", "vat_status", 1, [200, 320, 380, 345], {"price": float(q_car["unit_price"]), "label": v_info["true_label"]}, {"price": price_val, "label": vat_text}, "Confirm VAT status against D5 carrier agreement and D4_carrier_rates.xlsx"))

    render_pdf(
        ctx.docs_dir / "D4_carrier_quote_auto.pdf",
        [{
            "header": {"title": f"КП перевозчика ({q_car['quote_id']})", "subtitle": "Автоперевозка CIP Ташкент", "meta": f"Дата: {q_car['quote_date']}"},
            "sections": [{"heading": "Ставки", "kv_rows": [("Маршрут", q_car["route_id"]), ("Ставка/уп.", f"{price_val} {q_car['currency']}"), ("НДС", vat_text), ("Срок", f"{q_car['lead_time_days']} дней")]}],
            "footer": "LAGRANGE Rx Carrier Quote",
        }],
        watermark=ctx.watermark,
    )
    ctx.add_artifact("D4_carrier_quote_auto.pdf", "D4", [f"quotes/{q_car['quote_id']}"], corrs_pdf)

    rate_rows = [
        [r["route_id"], carriers.loc[r["carrier_id"], "name"], r["mode"], r["corridor"], float(r["freight_per_shipment_usd"]), float(r["freight_per_pack_usd"]), int(r["transit_days"])]
        for r in routes.to_dict("records")
    ]
    write_xlsx(
        ctx.docs_dir / "D4_carrier_rates.xlsx",
        [SheetSpec(name="Carrier_Rates", headers=["route_id", "carrier", "mode", "corridor", "per_shipment_usd", "per_pack_usd", "transit_days"], rows=rate_rows, col_widths=[10.0, 26.0, 20.0, 30.0, 16.0, 14.0, 12.0])],
    )
    ctx.add_artifact("D4_carrier_rates.xlsx", "D4", _first_refs(routes, "routes", "route_id", 4), [])

    messages = [
        {"sender": str(carriers.loc[q_car["entity_id"], "name"]), "side": "left", "text": f"Assalomu alaykum! Auto KZ stavka: {q_car['unit_price']} USD/pack, 18 days.", "time": "09:42"},
        {"sender": "Логист CardioImport", "side": "right", "text": "Спасибо! Фиксируем тариф для отгрузки на май-июнь?", "time": "09:45"},
        {"sender": str(carriers.loc[q_car["entity_id"], "name"]), "side": "left", "text": f"Да, действует до {q_car['valid_until']}, MOQ {q_car['min_order_qty']} уп.", "time": "09:47"},
    ]
    corrs_chat: list[dict[str, Any]] = []
    crop_fn = None
    if ctx.has("R4"):
        crop_fn = lambda im: apply_messenger_crop(im, ctx.rng)
        corrs_chat.append(ctx.corr("R4", "unit_price", 1, [680, 140, 950, 240], float(q_car["unit_price"]), "partially_clipped_edge", "Verify exact carrier per-pack rate in D4_carrier_rates.xlsx"))
    render_chat_screenshot(ctx.docs_dir / "D4_carrier_chat_quote.png", messages, watermark=ctx.watermark, crop_fn=crop_fn)
    ctx.add_artifact("D4_carrier_chat_quote.png", "D4", [f"quotes/{q_car['quote_id']}"], corrs_chat)


def _build_d5_contract(ctx: _WorldCtx, cnt: dict[str, Any], filename: str, is_v2: bool = False) -> None:
    corrs: list[dict[str, Any]] = []
    price = float(cnt["unit_price"])
    if is_v2:
        price = round(price * 1.07, 2)
        corrs.append(ctx.corr("X2", "unit_price", 1, [200, 300, 360, 330], float(cnt["unit_price"]), price, "Conflicting contract draft v2; use signed D5_contract_supplier_A.pdf matching D6 invoice"))

    pages = [
        {
            "header": {"title": f"КОНТРАКТ ПОСТАВКИ № {cnt['contract_number']}", "subtitle": f"Поставщик: {cnt['party_id']} | Покупатель: ООО «CardioImport Distribution»", "meta": f"Дата подписания: {cnt['signed_date']} | Действует до: {cnt['valid_until']}"},
            "sections": [
                {"heading": "1. Предмет контракта и цены", "kv_rows": [("Валюта", cnt["currency"]), ("Цена CIP", f"{price:.2f} {cnt['currency']}"), ("MOQ", f"{cnt['min_order_qty']} упаковок"), ("Условия оплаты", cnt["payment_terms"])]},
                {"heading": "2. Базис поставки", "paragraphs": [f"Поставка осуществляется на условиях {cnt['incoterms']}. Примечание: {cnt['amendment_note']}."]},
            ],
            "footer": f"Контракт {cnt['contract_number']} | Стр. 1 из 2",
        },
        {
            "header": {"title": f"Контракт № {cnt['contract_number']} — Юридические условия"},
            "sections": [{"heading": "3. Реквизиты и подписи (SYNTHETIC)", "paragraphs": ["Стороны руководствуются законодательством РУз и требованиями Приказа МЗ РУз №3735. Документ является учебным образцом."]}],
            "footer": f"Контракт {cnt['contract_number']} | Стр. 2 из 2",
        },
    ]
    render_pdf(ctx.docs_dir / filename, pages, watermark=ctx.watermark)
    ctx.add_artifact(filename, "D5", [f"contracts/{cnt['contract_id']}"], corrs)


def _build_d5_all(ctx: _WorldCtx) -> None:
    contracts = ctx.truth["contracts"]
    cnt_a = contracts[contracts["contract_id"] == "cnt_0002"].iloc[0].to_dict()
    cnt_b = contracts[contracts["contract_id"] == "cnt_0003"].iloc[0].to_dict()
    _build_d5_contract(ctx, cnt_a, "D5_contract_supplier_A.pdf", is_v2=False)
    _build_d5_contract(ctx, cnt_b, "D5_contract_supplier_B.pdf", is_v2=False)
    if ctx.has("X2"):
        _build_d5_contract(ctx, cnt_a, "D5_contract_supplier_A_v2.pdf", is_v2=True)


def _build_d6_invoice(ctx: _WorldCtx, shipment_id: str, filename: str) -> None:
    shp = ctx.truth["shipments"][ctx.truth["shipments"]["shipment_id"] == shipment_id].iloc[0].to_dict()
    lots = ctx.truth["lots"][ctx.truth["lots"]["shipment_id"] == shipment_id]
    corrs: list[dict[str, Any]] = []
    disp_str = str(shp["dispatch_date"])
    if ctx.has("N2"):
        shown_date = corrupt_date(disp_str, ctx.rng)
        corrs.append(ctx.corr("N2", "dispatch_date", 1, [160, 120, 320, 145], disp_str, shown_date, "Cross-check ISO dispatch date against D7 customs summary"))
        disp_str = shown_date

    lot_rows = [[r["lot_id"], r["lot_code"], r["sku_id"], int(r["initial_qty"]), r["expiry_date"]] for r in lots.to_dict("records")]
    pages = [{
        "header": {"title": f"INVOICE & PACKING LIST — {shipment_id.upper()}", "subtitle": f"Supplier: {shp['supplier_id']} | Contract: {shp['contract_id']}", "meta": f"Dispatch: {disp_str} | Arrival: {shp['arrival_date']}"},
        "sections": [{"heading": "Packing List", "table": {"headers": ["Lot ID", "Batch Code", "SKU", "Qty Packs", "Expiry"], "rows": lot_rows}}],
        "footer": f"Total packs: {int(shp['total_packs'])} | {filename}",
    }]
    render_pdf(ctx.docs_dir / filename, pages, watermark=ctx.watermark)
    refs = [f"shipments/{shipment_id}"] + [f"lots/{lid}" for lid in lots["lot_id"].astype(str).tolist()]
    ctx.add_artifact(filename, "D6", refs, corrs)


def _build_d6_all(ctx: _WorldCtx) -> None:
    _build_d6_invoice(ctx, "shp_0035", "D6_invoice_packing_shp_0035.pdf")
    _build_d6_invoice(ctx, "shp_0040", "D6_invoice_packing_shp_0040.pdf")


def _build_d7_all(ctx: _WorldCtx) -> None:
    customs = ctx.truth["customs"]
    corrs_xlsx: list[dict[str, Any]] = []
    corrs_pdf: list[dict[str, Any]] = []
    rows_xlsx: list[list[Any]] = []
    rows_pdf: list[list[Any]] = []

    for idx, rec in enumerate(customs.head(15).to_dict("records")):
        cip_uzs: Any = round(float(rec["cip_uzs"]), 2)
        landed: Any = round(float(rec["landed_cost_ex_vat_uzs"]), 2)
        c_date = str(rec["clearance_date"])
        if ctx.has("C2") and idx == 0:
            c2 = corrupt_currency_wrong(float(rec["cip_usd"]), ctx.rng)
            corrs_xlsx.append(ctx.corr("C2", "cip_uzs", 1, [260, 140, 380, 165], cip_uzs, c2["display"], "USD CIP value placed in UZS column; multiply by CBU rate on clearance_date"))
            cip_uzs = c2["display"]
        if ctx.has("C3") and idx == 1:
            wrong_rate = float(rec["fx_rate_uzs"]) * 0.96
            c3 = corrupt_fx_wrong_date(float(rec["cip_usd"]), float(rec["fx_rate_uzs"]), wrong_rate, "2025-01-05")
            corrs_pdf.append(ctx.corr("C3", "landed_cost_ex_vat_uzs", 1, [340, 180, 480, 205], landed, c3["shown_uzs"], "Recompute customs UZS value using CBU rate on GTD clearance_date"))
            landed = c3["shown_uzs"]
        if ctx.has("N2") and idx == 2:
            shown_d = corrupt_date(c_date, ctx.rng)
            corrs_pdf.append(ctx.corr("N2", "clearance_date", 1, [140, 200, 260, 225], c_date, shown_d, "Normalize date format to ISO YYYY-MM-DD"))
            c_date = shown_d
        rows_xlsx.append([rec["customs_id"], rec["shipment_id"], rec["clearance_date"], rec["customs_post"], float(rec["fx_rate_uzs"]), cip_uzs, float(rec["landed_cost_ex_vat_uzs"])])
        rows_pdf.append([rec["customs_id"], rec["shipment_id"], c_date, str(rec["fx_rate_uzs"]), str(rec["cip_usd"]), str(landed)])

    write_xlsx(
        ctx.docs_dir / "D7_customs_summary.xlsx",
        [SheetSpec(name="Customs_Summary", headers=["customs_id", "shipment_id", "clearance_date", "customs_post", "fx_rate_uzs", "cip_uzs", "landed_cost_ex_vat_uzs"], rows=rows_xlsx, col_widths=[12.0, 12.0, 14.0, 22.0, 12.0, 20.0, 22.0])],
    )
    ctx.add_artifact("D7_customs_summary.xlsx", "D7", _first_refs(customs, "customs", "customs_id", 4), corrs_xlsx)

    render_pdf(
        ctx.docs_dir / "D7_customs_summary.pdf",
        [{"header": {"title": "УЧЕБНАЯ СВОДКА ПО РАСТАМОЖКЕ (D7)", "subtitle": "Customs Clearance Educational Summary (SYNTHETIC)"}, "sections": [{"heading": "Декларации", "table": {"headers": ["ID", "Отгрузка", "Дата ГТД", "Курс", "CIP USD", "Итого без НДС"], "rows": rows_pdf}}], "footer": "Не является бланком ГТД | SYNTHETIC"}],
        watermark=ctx.watermark,
    )
    ctx.add_artifact("D7_customs_summary.pdf", "D7", _first_refs(customs, "customs", "customs_id", 4), corrs_pdf)


def _build_d8_photo(ctx: _WorldCtx, lot_2: dict[str, Any]) -> None:
    lines = [
        {"text": "Складская накладная № WB-0102", "style": "heading"},
        {"text": f"Отгрузка: {lot_2['shipment_id']} | Дата: {lot_2['arrival_date']}", "style": "bold"},
        {"text": f"SKU: {lot_2['sku_id']} | Партия: {lot_2['lot_id']} ({lot_2['lot_code']})", "style": "body"},
        {"text": f"Количество: {int(lot_2['initial_qty'])} упак.", "style": "mono", "y": 360},
    ]
    corrs_jpg: list[dict[str, Any]] = []
    if ctx.has("R1"):
        is_severe = ctx.allow_unrec
        hint = "Scan severely occluded/blurred; request original paper waybill from client warehouse" if is_severe else "Verify noisy scan quantity against D9_warehouse_stock_lots.xlsx"
        corrs_jpg.append(ctx.corr("R1", "qty_packs", 1, [64, 350, 560, 405], int(lot_2["initial_qty"]), "severe_blur_occlusion" if is_severe else "scan_photo_noise", hint, recoverable=not is_severe))
    if ctx.has("R2"):
        corrs_jpg.append(ctx.corr("R2", "initial_qty", 1, [200, 345, 420, 410], int(lot_2["initial_qty"]), "stamp_over_digits", "Confirm stamped waybill lot quantity in D9_warehouse_stock_lots.xlsx"))

    def _post_d8(im):
        out = apply_stamp_overlay(im, (200, 345, 420, 410), ctx.rng) if ctx.has("R2") else im
        return apply_scan_photo_effects(out, ctx.rng, severe=ctx.allow_unrec) if ctx.has("R1") else out

    render_document_image(ctx.docs_dir / "D8_warehouse_waybill_0102_photo.jpg", "Складская накладная WB-0102", lines, watermark=ctx.watermark, postprocess_fn=_post_d8)
    ctx.add_artifact("D8_warehouse_waybill_0102_photo.jpg", "D8", [f"lots/{lot_2['lot_id']}", f"shipments/{lot_2['shipment_id']}"], corrs_jpg)


def _build_d8_all(ctx: _WorldCtx) -> None:
    lots = ctx.truth["lots"]
    lot_1 = lots.iloc[0].to_dict()
    lot_2 = lots.iloc[1].to_dict() if len(lots) > 1 else lot_1

    corrs_pdf: list[dict[str, Any]] = []
    qty_label = f"{int(lot_1['initial_qty'])} упаковок (packs)"
    if ctx.has("U1"):
        u_info = corrupt_unit(int(lot_1["initial_qty"]), float(lot_1["unit_cost_uzs"]), ctx.rng)
        qty_label = f"{u_info['qty']} ({u_info['unit']})"
        corrs_pdf.append(ctx.corr("U1", "initial_qty", 1, [240, 280, 420, 310], int(lot_1["initial_qty"]), qty_label, "Convert blisters (x3) or boxes (/10) to packs using focus_sku ratios"))

    wb_pages = [{
        "header": {"title": "НАКЛАДНАЯ СКЛАДА № WB-0101", "subtitle": "Склад: Ташкент-Центральный (SYNTHETIC)", "meta": f"Дата приёмки: {lot_1['arrival_date']} | Отгрузка: {lot_1['shipment_id']}"},
        "sections": [{"heading": "Позиции приёмки", "kv_rows": [("Партия", f"{lot_1['lot_id']} ({lot_1['lot_code']})"), ("SKU", lot_1["sku_id"]), ("Количество", qty_label), ("Срок годности", lot_1["expiry_date"])]}],
        "footer": "Ответственный кладовщик (SYNTHETIC)",
    }]
    render_pdf(ctx.docs_dir / "D8_warehouse_waybill_0101.pdf", wb_pages, watermark=ctx.watermark)
    ctx.add_artifact("D8_warehouse_waybill_0101.pdf", "D8", [f"lots/{lot_1['lot_id']}", f"shipments/{lot_1['shipment_id']}"], corrs_pdf)

    if ctx.has("D1x"):
        wb_pages[0]["header"]["title"] = "НАКЛАДНАЯ СКЛАДА № WB-0101-DUP"
        render_pdf(ctx.docs_dir / "D8_warehouse_waybill_0101_dup.pdf", wb_pages, watermark=ctx.watermark)
        dup_corr = ctx.corr("D1x", "shipment_id", 1, [80, 740, 400, 780], lot_1["shipment_id"], "WB-0101-DUP", "Duplicate waybill for same lot_id and arrival_date; deduplicate via D9 stock ledger")
        ctx.add_artifact("D8_warehouse_waybill_0101_dup.pdf", "D8", [f"lots/{lot_1['lot_id']}", f"shipments/{lot_1['shipment_id']}"], [dup_corr])

    _build_d8_photo(ctx, lot_2)


def _build_d9_d10_xlsx(ctx: _WorldCtx) -> None:
    lots = ctx.truth["lots"]
    inv = ctx.truth["inventory_monthly"]
    lot_rows = [[r["lot_id"], r["lot_code"], r["sku_id"], r["arrival_date"], r["expiry_date"], int(r["initial_qty"]), int(r["sold_qty"]), int(r["writeoff_qty"]), int(r["remaining_qty"])] for r in lots.to_dict("records")]
    inv_rows = [[r["month"], r["sku_id"], int(r["opening_stock"]), int(r["received_qty"]), int(r["sold_qty"]), int(r["writeoff_qty"]), int(r["closing_stock"])] for r in inv.head(42).to_dict("records")]
    write_xlsx(
        ctx.docs_dir / "D9_warehouse_stock_lots.xlsx",
        [
            SheetSpec(name="Lots", headers=["lot_id", "lot_code", "sku_id", "arrival_date", "expiry_date", "initial_qty", "sold_qty", "writeoff_qty", "remaining_qty"], rows=lot_rows),
            SheetSpec(name="Monthly_Stock", headers=["month", "sku_id", "opening_stock", "received_qty", "sold_qty", "writeoff_qty", "closing_stock"], rows=inv_rows),
        ],
    )
    ctx.add_artifact("D9_warehouse_stock_lots.xlsx", "D9", _first_refs(lots, "lots", "lot_id", 4), [])

    credit = ctx.truth["credit"]
    costs = ctx.truth["costs"]
    interests = [float(x) for x in credit["monthly_interest_uzs"].tolist()]
    total_interest = round(sum(interests), 2)
    corrs_d10: list[dict[str, Any]] = []
    if ctx.has("A1"):
        a1 = corrupt_arithmetic(interests, ctx.rng)
        total_interest = a1["shown_total"]
        corrs_d10.append(ctx.corr("A1", "total_interest_uzs", 1, [200, 480, 360, 510], a1["true_total"], a1["shown_total"], "Recompute total interest by summing monthly_interest_uzs column in D10"))

    cred_rows = [[r["credit_id"], r["month"], float(r["opening_balance_uzs"]), float(r["drawdown_uzs"]), float(r["repayment_uzs"]), float(r["closing_balance_uzs"]), float(r["monthly_interest_uzs"])] for r in credit.to_dict("records")]
    cred_rows.append(["TOTAL", "2025-01..2026-10", "", "", "", "", total_interest])
    cost_rows = [[r["cost_id"], r["month"], r["sku_id"], r["category"], float(r["amount_uzs"])] for r in costs.head(60).to_dict("records")]
    write_xlsx(
        ctx.docs_dir / "D10_payments_and_credit.xlsx",
        [
            SheetSpec(name="Credit_Schedule", headers=["credit_id", "month", "opening_uzs", "drawdown_uzs", "repayment_uzs", "closing_uzs", "monthly_interest_uzs"], rows=cred_rows),
            SheetSpec(name="Operating_Costs", headers=["cost_id", "month", "sku_id", "category", "amount_uzs"], rows=cost_rows),
        ],
    )
    ctx.add_artifact("D10_payments_and_credit.xlsx", "D10", _first_refs(credit, "credit", "credit_id", 4), corrs_d10)


def _build_d11_memo(ctx: _WorldCtx) -> None:
    costs = ctx.truth["costs"]
    row = costs.iloc[0].to_dict()
    amt = round(float(row["amount_uzs"]), 2)
    corrs: list[dict[str, Any]] = []
    lines = [
        {"text": "СЛУЖЕБНАЯ ЗАПИСКА / ICHKI XIZMAT XATI", "style": "heading"},
        {"text": f"Месяц: {row['month']} | Статья: {row['category']} | SKU: {row['sku_id']}", "style": "bold"},
        {"text": f"Плановая сумма расходов: {amt:,.2f} UZS", "style": "mono", "y": 310},
        {"text": "Примечание: корректировка по итогам сверки с фин. отделом.", "style": "body", "y": 400},
    ]
    if ctx.has("R3"):
        new_amt = round(amt * 1.12, 2)
        corrs.append(ctx.corr("R3", "amount_uzs", 1, [320, 305, 520, 340], amt, new_amt, "Reconcile handwritten memo adjustment against D10_payments_and_credit.xlsx"))
    if ctx.allow_unrec and ctx.has("M1"):
        corrs.append(ctx.corr("M1", "memo_page_2", 2, [0, 0, 1200, 1600], "page_2_cost_breakdown", "MISSING_PAGE_2", "Second page of handwritten memo missing; request original scan from client", recoverable=False))
        ctx.omitted_documents.append({"doc_type": "D11", "scope": "memo_page_2", "truth_ref": f"costs/{row['cost_id']}", "reason": "Page 2 of internal memo lost in L4 chaos", "recoverable": False})

    def _post_d11(im):
        if ctx.has("R3"):
            return apply_handwritten_correction(im, (320, 305, 520, 340), f"{round(amt * 1.12, 0):.0f}", ctx.rng)
        return im

    render_document_image(ctx.docs_dir / "D11_internal_memo_handwritten.jpg", "Служебная записка (SYNTHETIC)", lines, watermark=ctx.watermark, postprocess_fn=_post_d11)
    ctx.add_artifact("D11_internal_memo_handwritten.jpg", "D11", [f"costs/{row['cost_id']}"], corrs)


def _build_d12_email(ctx: _WorldCtx) -> None:
    q = ctx.truth["quotes"].iloc[0].to_dict()
    subject = f"Re: Commercial Quote {q['quote_id']} — Kardiozan 10 mg #30"
    sender = "procurement@cardioimport.synthetic"
    recipient = "export@medisynth.synthetic"
    date_str = "Mon, 11 May 2026 09:30:00 +0500"
    body = (
        f"Уважаемые коллеги / Dear Partners,\n\n"
        f"Подтверждаем получение КП {q['quote_id']} ({q['unit_price']} {q['currency']}, MOQ {q['min_order_qty']} packs).\n"
        f"Просим направить оригинал инвойса для Ташкента.\n\n"
        f"С уважением,\nОтдел закупок ООО «CardioImport Distribution» (SYNTHETIC)"
    )
    att = ctx.docs_dir / "D3_quote_supplier_A_2026-05.pdf"
    write_eml_doc(ctx.docs_dir / "D12_email_supplier_thread.eml", subject, sender, recipient, date_str, body, attachment_path=att)
    ctx.add_artifact("D12_email_supplier_thread.eml", "D12", [f"quotes/{q['quote_id']}"], [])

    render_pdf(
        ctx.docs_dir / "D12_email_printout.pdf",
        [{"header": {"title": "EMAIL THREAD PRINTOUT (D12)", "subtitle": f"From: {sender} -> To: {recipient}", "meta": f"Date: {date_str} | Subject: {subject}"}, "sections": [{"heading": "Message Body", "paragraphs": body.splitlines()}], "footer": "Attachment: D3_quote_supplier_A_2026-05.pdf"}],
        watermark=ctx.watermark,
    )
    ctx.add_artifact("D12_email_printout.pdf", "D12", [f"quotes/{q['quote_id']}"], [])


def _build_d13_registry(ctx: _WorldCtx) -> None:
    reg = ctx.truth["registry_events"]
    headers = ["event_id", "sku_id", "effective_from", "event_type", "rn_uzs", "b_uzs", "bx_uzs", "calc_base_ex_vat_uzs", "wholesale_ex_vat_uzs", "chun_with_vat_uzs", "chchn_with_vat_uzs"]
    csv_rows = [[r[col] for col in headers] for r in reg.to_dict("records")]
    write_csv_doc(ctx.docs_dir / "D13_price_registry_extract.csv", headers, csv_rows, encoding="utf-8", delimiter=";", decimal_comma=False)
    ctx.add_artifact("D13_price_registry_extract.csv", "D13", _first_refs(reg, "registry_events", "event_id", 3), [])

    latest = reg.iloc[-1].to_dict()
    shown_row = reg.iloc[1].to_dict() if (ctx.has("S1") and len(reg) > 1) else latest
    corrs_pdf: list[dict[str, Any]] = []
    if ctx.has("S1") and len(reg) > 1:
        corrs_pdf.append(
            ctx.corr(
                "S1",
                "chchn_with_vat_uzs",
                1,
                [260, 220, 420, 250],
                float(latest["chchn_with_vat_uzs"]),
                float(shown_row["chchn_with_vat_uzs"]),
                "PDF extract reflects pre-revision reg_0002; use latest reg_0003 in D13_price_registry_extract.csv",
            )
        )

    render_pdf(
        ctx.docs_dir / "D13_price_registry_extract.pdf",
        [{
            "header": {"title": "ВЫПИСКА ИЗ РЕЕСТРА ПРЕДЕЛЬНЫХ ЦЕН (УЧЕБНАЯ)", "subtitle": "Приказ МЗ РУз №3735 — SKU_CARDIO_01", "meta": f"Запись: {shown_row['event_id']} | Действует с: {shown_row['effective_from']}"},
            "sections": [{"heading": "Предельные цены", "kv_rows": [("Rn (база)", shown_row["rn_uzs"]), ("Calc Base без НДС", shown_row["calc_base_ex_vat_uzs"]), ("Опт без НДС", shown_row["wholesale_ex_vat_uzs"]), ("ChUN с НДС", shown_row["chun_with_vat_uzs"]), ("ChChN с НДС", shown_row["chchn_with_vat_uzs"])]}],
            "footer": "Учебная форма реестра цен | SYNTHETIC",
        }],
        watermark=ctx.watermark,
    )
    ctx.add_artifact("D13_price_registry_extract.pdf", "D13", [f"registry_events/{shown_row['event_id']}"], corrs_pdf)


def _build_j1_junk(ctx: _WorldCtx) -> None:
    if not ctx.has("J1"):
        return
    ref = _first_refs(ctx.truth["skus"], "skus", "sku_id", 1)
    render_pdf(
        ctx.docs_dir / "J1_junk_promo_flyer.pdf",
        [{"header": {"title": "РЕКЛАМНЫЙ БУКЛЕТ: ВИТАМИНЫ СЕРИИ BIO-VITA (SYNTHETIC)"}, "sections": [{"heading": "Акция (не относится к кардиопрепаратам)", "paragraphs": ["Скидка 15% на линейку витаминов Bio-Vita C 500 мг. Мусорный файл уровня L4."]}], "footer": "J1 Junk Promo Flyer"}],
        watermark=ctx.watermark,
    )
    ctx.add_artifact("J1_junk_promo_flyer.pdf", "J1", ref, [ctx.corr("J1", "irrelevant_file", 1, [0, 0, 595, 842], "unrelated", "J1_junk_promo_flyer.pdf", "Ignore unrelated promotional flyer", recoverable=True)])

    write_csv_doc(ctx.docs_dir / "J1_junk_unrelated_catalog.csv", ["sku_other", "name", "price_uzs"], [["OTC_901", "VitaMix Syrup 100ml", 42000.0]], encoding="utf-8", delimiter=";", decimal_comma=False)
    ctx.add_artifact("J1_junk_unrelated_catalog.csv", "J1", ref, [ctx.corr("J1", "irrelevant_file", 1, [0, 0, 400, 120], "unrelated", "J1_junk_unrelated_catalog.csv", "Ignore unrelated OTC SKU catalog", recoverable=True)])

    render_document_image(
        ctx.docs_dir / "J1_junk_blank_scan.jpg",
        "Пустой лист сканера (SYNTHETIC)",
        [{"text": "— Лист без данных / Blank scanner sheet —", "style": "body"}],
        watermark=ctx.watermark,
        postprocess_fn=lambda im: apply_scan_photo_effects(im, ctx.rng, severe=False),
    )
    ctx.add_artifact("J1_junk_blank_scan.jpg", "J1", ref, [ctx.corr("J1", "irrelevant_file", 1, [0, 0, 1200, 1600], "blank_scan", "J1_junk_blank_scan.jpg", "Ignore blank noisy scanner sheet", recoverable=True)])


def build_world(
    level_name: str,
    level_cfg: dict[str, Any],
    world_cfg: dict[str, Any],
    truth_tables: dict[str, pd.DataFrame],
    out_world_dir: Path | str,
    seed: int,
) -> dict[str, Any]:
    """Generate D1-D13 (+J1 on L4) documents and manifest.json for one chaos level."""
    out_dir = Path(out_world_dir)
    docs_dir = out_dir / "documents"
    if docs_dir.exists():
        for existing in docs_dir.iterdir():
            if existing.is_file():
                existing.unlink()
    docs_dir.mkdir(parents=True, exist_ok=True)

    salt = _LEVEL_SALT.get(level_name, 900)
    ctx = _WorldCtx(
        level_name=level_name,
        level_cfg=level_cfg,
        world_cfg=world_cfg,
        truth=truth_tables,
        docs_dir=docs_dir,
        rng=np.random.default_rng(int(seed) + salt),
        active_ops=set(level_cfg.get("operators", [])),
        allow_unrec=bool(level_cfg.get("allow_unrecoverable", False)) and (level_name == "L4"),
        watermark=str(world_cfg.get("company", {}).get("watermark", WATERMARK_TEXT)),
    )

    _build_d1_csv(ctx)
    _build_d2_all(ctx)
    _build_d3_all(ctx)
    _build_d4_all(ctx)
    _build_d5_all(ctx)
    _build_d6_all(ctx)
    _build_d7_all(ctx)
    _build_d8_all(ctx)
    _build_d9_d10_xlsx(ctx)
    _build_d11_memo(ctx)
    _build_d12_email(ctx)
    _build_d13_registry(ctx)
    _build_j1_junk(ctx)

    manifest = {
        "level": level_name,
        "seed": int(seed),
        "omitted_documents": ctx.omitted_documents,
        "documents": ctx.artifacts,
    }
    (out_dir / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False, sort_keys=False),
        encoding="utf-8",
    )
    return manifest
