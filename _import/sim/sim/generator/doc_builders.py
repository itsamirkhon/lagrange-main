"""Multi-level world document builder (D1-D13 + J1) covering 100% of truth with zero internal IDs."""

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
    compute_chat_token_bbox,
    compute_table_image_bboxes,
    render_chat_screenshot,
    render_document_image,
    render_pdf,
    render_table_document_image,
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
_CHANNEL_RU: dict[str, str] = {
    "distributors": "Дистрибьюторы",
    "pharmacy_chains": "Аптечные сети",
    "hospital_tenders": "Госзакупки",
}
_1C_HEADERS = [
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
_D2_GROUPS: tuple[dict[str, Any], ...] = (
    {
        "file": "D2_sales_tashkent_city.xlsx",
        "sheet": "Ташкент_город",
        "regions": ["REG_TAS_C"],
        "lang": "ru",
        "pre": [["Региональный отчёт продаж: г. Ташкент (SYNTHETIC)"], ["Менеджер: Дилноза Рахимова", "Отдел продаж"]],
        "headers": ["Период", "Регион", "Контрагент", "Канал", "Препарат", "Серия", "Упаковок", "Выручка без НДС"],
        "merges": ["A1:H1", "A2:B2"],
    },
    {
        "file": "D2_sales_tashkent_region_syrdarya.xlsx",
        "sheet": "Toshkent_Sirdaryo",
        "regions": ["REG_TAS_R", "REG_SYR", "REG_JIZ"],
        "lang": "uz_lat",
        "pre": [["Hududiy savdo hisoboti: Toshkent vil., Sirdaryo, Jizzax (SYNTHETIC)"]],
        "headers": ["Oy", "Hudud", "Xaridor", "Kanal", "Dori nomi", "Seriya", "Miqdor (up.)", "Summa (QQSsiz)"],
        "merges": ["A1:H1"],
    },
    {
        "file": "D2_sales_fergana_valley.xlsx",
        "sheet": "Водий_сотув",
        "regions": ["REG_FER", "REG_AND", "REG_NAM"],
        "lang": "uz_cyr",
        "pre": [["Фарғона водийси бўйича сотув ҳисоботи (SYNTHETIC)"], ["Тайёрлади:", "Сардор Исмаилов", "Назорат:", "Финанс"]],
        "headers": ["Давр", "Ҳудуд", "Мижоз", "Канал", "Препарат", "Серия", "Қадоқ", "Сумма ҚҚСсиз"],
        "merges": ["A1:H1", "C2:D2"],
    },
    {
        "file": "D2_sales_samarkand_navoi.xlsx",
        "sheet": "Samarkand_Navoi",
        "regions": ["REG_SAM", "REG_NAV"],
        "lang": "en",
        "pre": [["Regional Sales Summary: Samarkand & Navoi (SYNTHETIC)"]],
        "headers": ["Period", "Region", "Counterparty", "Channel", "Product", "Batch", "Packs", "Net Revenue UZS"],
        "merges": ["A1:H1"],
    },
    {
        "file": "D2_sales_bukhara_kashkadarya_surkhan.xlsx",
        "sheet": "Юг_и_Бухара",
        "regions": ["REG_BUK", "REG_KAS", "REG_SUR"],
        "lang": "ru",
        "pre": [["Сводка продаж: Бухара, Кашкадарья, Сурхандарья (SYNTHETIC)"]],
        "headers": ["Месяц", "Область", "Покупатель", "Канал сбыта", "Номенклатура", "Серия", "Кол-во", "Сумма без НДС"],
        "merges": ["A1:H1"],
    },
    {
        "file": "D2_sales_khorezm_karakalpakstan.xlsx",
        "sheet": "Xorazm_QR",
        "regions": ["REG_KHO", "REG_KAR"],
        "lang": "uz_lat",
        "pre": [["Xorazm va Qoraqalpogʻiston savdo hisoboti (SYNTHETIC)"]],
        "headers": ["Davr", "Viloyat", "Mijoz", "Yoʻnalish", "Preparat", "Partiya", "Qadoq", "Tushum UZS"],
        "merges": ["A1:H1"],
    },
)

_SLOTS_1C_N2 = {"L1": (2, 18), "L2": (2, 18, 42, 75), "L3": (2, 18, 42, 75), "L4": (2, 18, 42, 75, 110, 145, 180, 215, 250, 285, 320)}
_SLOTS_1C_N1 = {"L1": (1, 14), "L2": (1, 14, 33, 61), "L3": (1, 14, 33, 61), "L4": (1, 14, 33, 61, 95, 130, 165, 200, 235, 270, 305)}
_SLOTS_1C_L1 = {"L2": (5, 29, 88), "L3": (5, 29, 88), "L4": (5, 29, 88, 120, 160, 210, 260)}
_SLOTS_D2_N2 = {"L1": (1,), "L2": (1,), "L3": (1, 19), "L4": (1, 19, 31, 43, 55)}
_SLOTS_D2_N1 = {"L1": (0,), "L2": (0,), "L3": (0, 8, 24), "L4": (0, 8, 24, 36, 48)}
_SLOTS_D2_L1 = {"L2": (2, 11), "L3": (2, 11), "L4": (2, 11, 21)}


@dataclass
class _WorldCtx:
    level_name: str
    level_cfg: dict[str, Any]
    world_cfg: dict[str, Any]
    truth: dict[str, pd.DataFrame]
    docs_dir: Path
    rng: np.random.Generator
    active_ops: set[str]
    allow_unrec: bool
    watermark: str = WATERMARK_TEXT
    sup_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    car_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    route_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    sku_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    cnt_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    shp_map: dict[str, dict[str, Any]] = field(default_factory=dict)
    lot_map: dict[str, dict[str, Any]] = field(default_factory=dict)
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
        art = DocumentArtifact(self.doc_rel_path(filename), doc_type, list(truth_refs), []).to_dict()
        art["corruptions"] = list(corruptions or [])
        self.artifacts.append(art)

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
        return make_corruption_record(op, field_name, page, bbox, true_val, shown_val, recoverable, hint)

    def sku_label(self, sku_id: str) -> str:
        if sku_id in self.sku_map:
            return str(self.sku_map[sku_id]["trade_name"])
        return "Общий портфель препаратов"


def _index_by(df: pd.DataFrame, col: str) -> dict[str, dict[str, Any]]:
    return {r[col]: r for r in df.to_dict("records")}


def _init_ctx(level_name: str, level_cfg: dict[str, Any], world_cfg: dict[str, Any], truth: dict[str, pd.DataFrame], docs_dir: Path, seed: int) -> _WorldCtx:
    salt = _LEVEL_SALT.get(level_name, 900)
    return _WorldCtx(
        level_name=level_name,
        level_cfg=level_cfg,
        world_cfg=world_cfg,
        truth=truth,
        docs_dir=docs_dir,
        rng=np.random.default_rng(int(seed) + salt),
        active_ops=set(level_cfg.get("operators", [])),
        allow_unrec=bool(level_cfg.get("allow_unrecoverable", False)) and (level_name == "L4"),
        watermark=str(world_cfg.get("company", {}).get("watermark", WATERMARK_TEXT)),
        sup_map=_index_by(truth["suppliers"], "supplier_id"),
        car_map=_index_by(truth["carriers"], "carrier_id"),
        route_map=_index_by(truth["routes"], "route_id"),
        sku_map=_index_by(truth["skus"], "sku_id"),
        cnt_map=_index_by(truth["contracts"], "contract_id"),
        shp_map=_index_by(truth["shipments"], "shipment_id"),
        lot_map=_index_by(truth["lots"], "lot_id"),
    )


def _corrupt_1c_fields(ctx: _WorldCtx, rec: dict[str, Any], row_idx: int, corrs: list[dict[str, Any]]) -> tuple[str, Any, str]:
    period = str(rec["month"])
    if ctx.has("N2") and row_idx in _SLOTS_1C_N2.get(ctx.level_name, ()):
        shown_p = corrupt_date(period, ctx.rng)
        corrs.append(ctx.corr("N2", "period", 1, [20, 40 + row_idx, 120, 58 + row_idx], period, shown_p, "Нормализовать формат периода YYYY-MM по данным реестра продаж"))
        period = shown_p

    rev_out: Any = float(rec["revenue_ex_vat_uzs"])
    if ctx.has("N1") and row_idx in _SLOTS_1C_N1.get(ctx.level_name, ()):
        shown_n = corrupt_number(rev_out, ctx.rng)
        corrs.append(ctx.corr("N1", "revenue_ex_vat_uzs", 1, [380, 40 + row_idx, 490, 58 + row_idx], rev_out, shown_n, "Привести разделители разрядов к стандартному числу по D2/D9"))
        rev_out = shown_n

    region_txt = str(rec["region_name_ru"])
    if ctx.has("L1") and row_idx in _SLOTS_1C_L1.get(ctx.level_name, ()):
        shown_reg = corrupt_language_mix(region_txt, ctx.rng)
        corrs.append(ctx.corr("L1", "region_name", 1, [160, 40 + row_idx, 280, 58 + row_idx], region_txt, shown_reg, "Сопоставить смешанное написание региона со справочником областей"))
        region_txt = shown_reg
    return period, rev_out, region_txt


def _format_1c_row(ctx: _WorldCtx, rec: dict[str, Any], row_idx: int, corrs: list[dict[str, Any]]) -> list[Any]:
    period, rev_out, region_txt = _corrupt_1c_fields(ctx, rec, row_idx, corrs)
    rev = float(rec["revenue_ex_vat_uzs"])
    vat_amt = round(rev * 0.12, 2)
    return [
        period,
        rec["doc_number"],
        rec["counterparty_name"],
        region_txt,
        _CHANNEL_RU.get(rec["channel"], rec["channel"]),
        rec["trade_name"],
        rec["nomenclature_code"],
        rec["lot_code"],
        "упак.",
        int(rec["qty_packs"]),
        float(rec["unit_price_ex_vat_uzs"]),
        rev_out,
        vat_amt,
        round(rev + vat_amt, 2),
    ]


def _drop_1c_slice(ctx: _WorldCtx, kept: pd.DataFrame, spec: tuple[str, str, str], corrs: list[dict[str, Any]]) -> pd.DataFrame:
    month, reg_id, ch = spec
    mask = (kept["month"] == month) & (kept["region_id"] == reg_id) & (kept["channel"] == ch)
    if not mask.any():
        return kept
    omitted = kept.loc[mask]
    is_unrec = ctx.allow_unrec and reg_id in ("REG_FER", "REG_AND", "REG_NAM")
    reg_ru = str(omitted.iloc[0]["region_name_ru"])
    hint = f"Отсутствует выгрузка 1С и региональный отчёт ({reg_ru}, {month}); запросите резервную копию у клиента" if is_unrec else f"Восстановите пропущенный срез ({reg_ru}, {month}) из регионального отчёта XLSX и остатков склада"
    corrs.append(ctx.corr("M1", f"sales_slice_{month}", 1, [40, 120, 620, 160], {"month": month, "region": reg_ru, "qty_packs": int(omitted["qty_packs"].sum())}, "OMITTED_SLICE", hint, recoverable=not is_unrec))
    if is_unrec:
        ctx.omitted_documents.append({"doc_type": "D1", "scope": f"{month}_{reg_ru}", "truth_ref": f"sales/{omitted.iloc[0]['sale_id']}", "reason": f"Пропуск продаж 1С и регионального отчёта за {month}", "recoverable": False})
    return kept.loc[~mask]


def _filter_1c_m1_slices(ctx: _WorldCtx, year_df: pd.DataFrame, year: int, corrs: list[dict[str, Any]]) -> pd.DataFrame:
    if not ctx.has("M1"):
        return year_df
    base_2025 = [("2025-04", "REG_TAS_R", "pharmacy_chains"), ("2025-07", "REG_SAM", "hospital_tenders"), ("2025-10", "REG_BUK", "pharmacy_chains")]
    base_2026 = [("2026-02", "REG_AND", "hospital_tenders"), ("2026-06", "REG_KAS", "pharmacy_chains"), ("2026-08", "REG_FER", "distributors")]
    extra_2025 = [("2025-09", "REG_FER", "pharmacy_chains"), ("2025-11", "REG_NAM", "hospital_tenders")]
    extra_2026 = [("2026-07", "REG_FER", "pharmacy_chains"), ("2026-08", "REG_AND", "distributors"), ("2026-09", "REG_FER", "hospital_tenders")]
    slices = (base_2025 if year == 2025 else base_2026) + ((extra_2025 if year == 2025 else extra_2026) if ctx.allow_unrec else [])
    kept = year_df
    for spec in slices:
        kept = _drop_1c_slice(ctx, kept, spec, corrs)
    return kept


def _build_d1_all(ctx: _WorldCtx) -> None:
    sales = ctx.truth["sales"]
    for year in (2025, 2026):
        sub = _filter_1c_m1_slices(ctx, sales[sales["month"].str.startswith(f"{year}-")].copy(), year, corrs := [])
        rows = [_format_1c_row(ctx, rec, idx, corrs) for idx, rec in enumerate(sub.to_dict("records"))]
        fname = f"D1_1c_sales_{year}.csv"
        write_csv_doc(ctx.docs_dir / fname, _1C_HEADERS, rows, encoding="cp1251", delimiter=";", decimal_comma=True)
        refs = [f"sales/{sid}" for sid in sub["sale_id"].astype(str).head(25).tolist()]
        ctx.add_artifact(fname, "D1", refs, corrs)


def _d2_region_name(reg: dict[str, Any], lang: str) -> str:
    key = {"uz_lat": "name_uz_lat", "uz_cyr": "name_uz_cyr"}.get(lang, "name_ru")
    return str(reg[key])


def _d2_apply_m1(ctx: _WorldCtx, sub: pd.DataFrame, grp: dict[str, Any], corrs: list[dict[str, Any]]) -> pd.DataFrame:
    if not ctx.has("M1"):
        return sub
    is_fergana_unrec = ctx.allow_unrec and grp["file"] == "D2_sales_fergana_valley.xlsx"
    months = ("2026-07", "2026-08", "2026-09") if is_fergana_unrec else ("2026-04",)
    kept = sub
    for m in months:
        mask = kept["month"] == m
        if not mask.any():
            continue
        dropped = kept.loc[mask]
        reg_ru = str(dropped.iloc[0]["region_name_ru"])
        hint = f"Региональный отчёт и выгрузка 1С за {m} по {reg_ru} отсутствуют; запросите оригинал у менеджера" if is_fergana_unrec else f"Восстановите пропуск за {m} из выгрузки 1С и складского движения"
        corrs.append(ctx.corr("M1", f"monthly_sales_{m}", 1, [40, 200, 520, 240], {"month": m, "qty_packs": int(dropped["qty_packs"].sum())}, "MISSING_MONTH", hint, recoverable=not is_fergana_unrec))
        if is_fergana_unrec:
            ctx.omitted_documents.append({"doc_type": "D2", "scope": f"{m}_{reg_ru}", "truth_ref": f"sales/{dropped.iloc[0]['sale_id']}", "reason": f"Отсутствуют данные за {m}", "recoverable": False})
        kept = kept.loc[~mask]
    return kept


def _d2_format_row(ctx: _WorldCtx, rec: dict[str, Any], idx: int, lang: str, reg_map: dict[str, dict[str, Any]], corrs: list[dict[str, Any]]) -> tuple[list[Any], float]:
    period = str(rec["month"])
    if ctx.has("N2") and idx in _SLOTS_D2_N2.get(ctx.level_name, ()):
        shown_d = corrupt_date(period, ctx.rng)
        corrs.append(ctx.corr("N2", "period", 1, [40, 120 + idx, 140, 140 + idx], period, shown_d, "Приведите период к ISO YYYY-MM"))
        period = shown_d
    rname = _d2_region_name(reg_map[rec["region_id"]], lang)
    if ctx.has("L1") and idx in _SLOTS_D2_L1.get(ctx.level_name, ()):
        shown_r = corrupt_language_mix(rname, ctx.rng)
        corrs.append(ctx.corr("L1", "region_name", 1, [140, 120 + idx, 280, 140 + idx], rname, shown_r, "Сопоставьте название области со справочником регионов"))
        rname = shown_r
    rev = round(float(rec["revenue_ex_vat_uzs"]), 2)
    rev_cell: Any = rev
    if ctx.has("N1") and idx in _SLOTS_D2_N1.get(ctx.level_name, ()):
        shown_n = corrupt_number(rev, ctx.rng)
        corrs.append(ctx.corr("N1", "revenue_ex_vat_uzs", 1, [540, 120 + idx, 680, 140 + idx], rev, shown_n, "Удалите текстовые сокращения и пробелы в сумме"))
        rev_cell = shown_n
    return [period, rname, rec["counterparty_name"], _CHANNEL_RU.get(rec["channel"], rec["channel"]), rec["trade_name"], rec["lot_code"], int(rec["qty_packs"]), rev_cell], rev


def _build_d2_one(ctx: _WorldCtx, grp: dict[str, Any], reg_map: dict[str, dict[str, Any]]) -> None:
    sales = ctx.truth["sales"]
    sub = _d2_apply_m1(ctx, sales[sales["region_id"].isin(grp["regions"])].copy(), grp, corrs := [])
    rows: list[list[Any]] = []
    rev_vals: list[float] = []
    for idx, rec in enumerate(sub.to_dict("records")):
        row, rev = _d2_format_row(ctx, rec, idx, grp["lang"], reg_map, corrs)
        rows.append(row)
        rev_vals.append(rev)
    total_rev = round(sum(rev_vals), 2)
    if ctx.has("A1") and rev_vals:
        a1 = corrupt_arithmetic(rev_vals, ctx.rng)
        corrs.append(ctx.corr("A1", "total_revenue_uzs", 1, [540, 480, 680, 510], a1["true_total"], a1["shown_total"], "Пересчитайте итог столбца суммированием строк"))
        total_rev = a1["shown_total"]
    rows.append(["ИТОГО", "", "", "", "", "", int(sub["qty_packs"].sum()), total_rev])
    write_xlsx(ctx.docs_dir / grp["file"], [SheetSpec(name=grp["sheet"], headers=grp["headers"], rows=rows, merged_cells=grp["merges"], pre_header_rows=grp["pre"])])
    refs = [f"sales/{sid}" for sid in sub["sale_id"].astype(str).head(25).tolist()]
    ctx.add_artifact(grp["file"], "D2", refs, corrs)


def _build_d2_all(ctx: _WorldCtx) -> None:
    reg_map = {r["region_id"]: r for r in ctx.world_cfg["regions"]}
    for grp in _D2_GROUPS:
        _build_d2_one(ctx, grp, reg_map)


def _d3_price_and_moq(ctx: _WorldCtx, q: dict[str, Any], idx: int, corrs: list[dict[str, Any]]) -> tuple[str, int]:
    price = float(q["unit_price"])
    curr = str(q["currency"])
    price_str = f"{price:.2f} {curr}"
    if ctx.has("C1"):
        price_str = corrupt_currency_omit(price, curr)
        corrs.append(ctx.corr("C1", "unit_price", 1, [340, 320 + idx * 24, 430, 342 + idx * 24], {"amount": price, "currency": curr}, price_str, "Валюта цены указана в действующем контракте поставки и инвойсе"))
    moq = int(q["min_order_qty"])
    if ctx.has("X1"):
        corrs.append(ctx.corr("X1", "min_order_qty", 1, [260, 320 + idx * 24, 330, 342 + idx * 24], moq, moq * 2, "Условия подписанного контракта имеют приоритет над предварительным КП"))
        moq *= 2
    return price_str, moq


def _format_d3_quote_row(ctx: _WorldCtx, q: dict[str, Any], idx: int, corrs: list[dict[str, Any]]) -> list[str]:
    price_str, moq = _d3_price_and_moq(ctx, q, idx, corrs)
    sku_name = ctx.sku_label(str(q["sku_id"]))
    if ctx.has("L1") and idx == 0:
        shown_sku = corrupt_language_mix(sku_name, ctx.rng)
        corrs.append(ctx.corr("L1", "trade_name", 1, [120, 320, 260, 342], sku_name, shown_sku, "Сверьте торговое наименование с карточкой препарата"))
        sku_name = shown_sku
    valid_str = str(q["valid_until"])
    if ctx.has("N2") and idx == 1:
        shown_v = corrupt_date(valid_str, ctx.rng)
        corrs.append(ctx.corr("N2", "valid_until", 1, [430, 344, 520, 366], valid_str, shown_v, "Приведите дату действия КП к формату YYYY-MM-DD"))
        valid_str = shown_v
    if ctx.has("S1") and q["status"] == "expired" and idx == 0:
        corrs.append(ctx.corr("S1", "quote_status", 1, [120, 260, 300, 282], {"status": "expired", "valid_until": q["valid_until"]}, f"Актуально ({valid_str})", "Срок действия КП истёк; проверьте актуальное доп. соглашение"))
    return [str(q["quote_number"]), sku_name, str(moq), f"{q['lead_time_days']} дн.", price_str, valid_str]


def _build_d3_all(ctx: _WorldCtx) -> None:
    sup_quotes = ctx.truth["quotes"][ctx.truth["quotes"]["quote_type"] == "supplier"].to_dict("records")
    for b_idx in range(0, len(sup_quotes), 2):
        pair = sup_quotes[b_idx : b_idx + 2]
        q_first = pair[0]
        sup = ctx.sup_map[q_first["entity_id"]]
        corrs: list[dict[str, Any]] = []
        rows = [_format_d3_quote_row(ctx, q, i, corrs) for i, q in enumerate(pair)]
        fname = f"D3_quote_{q_first['quote_number']}.pdf"
        lang_en = (b_idx // 2) % 2 == 0
        pages = [{
            "header": {"title": f"{'COMMERCIAL QUOTATION' if lang_en else 'КОММЕРЧЕСКОЕ ПРЕДЛОЖЕНИЕ'} {q_first['quote_number']}", "subtitle": f"Supplier: {sup['name']} ({sup['supplier_code']})", "meta": f"Date: {q_first['quote_date']} | Incoterms: CIP Tashkent"},
            "sections": [
                {"heading": "Terms" if lang_en else "Условия", "kv_rows": [("Поставщик", sup["name"]), ("Оплата", q_first["terms"]), ("Код поставщика", sup["supplier_code"])]},
                {"heading": "Items", "table": {"headers": ["Номер КП", "Препарат", "MOQ", "Срок", "Цена CIP", "Действует до"], "rows": rows}},
            ],
            "footer": f"ООО «CardioImport Distribution» | {fname}",
        }]
        render_pdf(ctx.docs_dir / fname, pages, watermark=ctx.watermark)
        ctx.add_artifact(fname, "D3", [f"quotes/{q['quote_id']}" for q in pair], corrs)


def _build_d4_chats(ctx: _WorldCtx, car_quotes: list[dict[str, Any]]) -> None:
    chat_configs = [
        ("D4_carrier_chat_auto_kz.png", car_quotes[0], "KZ-AUTO-01", "SilkWay Logistics (SYNTHETIC)"),
        ("D4_carrier_chat_air_tas.png", car_quotes[3] if len(car_quotes) > 3 else car_quotes[-1], "TAS-AIR-04", "SkyBridge Express (SYNTHETIC)"),
    ]
    for fname, q, rcode, sender in chat_configs:
        rate_tok = f"{float(q['unit_price']):.3f} {q['currency']}"
        moq_tok = f"{int(q['min_order_qty'])} pack"
        days_tok = f"{int(q['lead_time_days'])} days"
        messages = [
            {"sender": sender, "side": "left", "wide": True, "text": f"Assalomu alaykum! {rcode} boʻyicha CIP Toshkent stavkasi jami: {rate_tok}", "time": "09:41"},
            {"sender": "Логист CardioImport", "side": "right", "wide": True, "text": f"Минимальная партия отправки подтверждена на уровне: {moq_tok}", "time": "09:44"},
            {"sender": sender, "side": "left", "wide": True, "text": f"Ha, albatta. Chegaradan omborgacha toʻliq yetkazish muddati: {days_tok}", "time": "09:47"},
        ]
        corrs: list[dict[str, Any]] = []
        crop_fn = None
        if ctx.has("R4"):
            b0 = compute_chat_token_bbox(messages, 0, rate_tok)
            b1 = compute_chat_token_bbox(messages, 1, moq_tok)
            b2 = compute_chat_token_bbox(messages, 2, days_tok)
            shared_bbox = (max(b0[0], b1[0], b2[0]), min(b0[1], b1[1], b2[1]), min(b0[2], b1[2], b2[2]), max(b0[3], b1[3], b2[3]))
            crop_fn = lambda im, tb=shared_bbox: apply_messenger_crop(im, ctx.rng, target_bbox=tb)
            for field_n, tok, bbox in (("unit_price", rate_tok, b0), ("min_order_qty", moq_tok, b1), ("lead_time_days", days_tok, b2)):
                corrs.append(ctx.corr("R4", field_n, 1, list(bbox), tok, f"{tok[:3]}...", "Цифры обрезаны правым краем скриншота; сверьте с таблицей ставок перевозчиков"))
        render_chat_screenshot(ctx.docs_dir / fname, messages, watermark=ctx.watermark, crop_fn=crop_fn)
        ctx.add_artifact(fname, "D4", [f"quotes/{q['quote_id']}"], corrs)


def _d4_pdf_row(ctx: _WorldCtx, q: dict[str, Any], idx: int, corrs: list[dict[str, Any]]) -> list[str]:
    rte = ctx.route_map[q["route_id"]]
    price_val = float(q["unit_price"])
    vat_lbl = "без НДС"
    if ctx.has("V1"):
        v_info = corrupt_vat_label(price_val, includes_vat=False, rng=ctx.rng)
        vat_lbl = v_info["shown_label"] or "не указан"
        corrs.append(ctx.corr("V1", "vat_status", 1, [320, 260 + idx * 20, 460, 280 + idx * 20], {"price": price_val, "label": "без НДС"}, {"price": v_info["shown_price"], "label": vat_lbl}, "Уточните ставку НДС по договору перевозки"))
        price_val = float(v_info["shown_price"])
    return [q["quote_number"], rte["route_code"], rte["mode"], f"{price_val} {q['currency']}", vat_lbl, f"{q['lead_time_days']} дн."]


def _build_d4_all(ctx: _WorldCtx) -> None:
    car_quotes = ctx.truth["quotes"][ctx.truth["quotes"]["quote_type"] == "carrier"].to_dict("records")
    for p_idx, (fname, subset) in enumerate((("D4_carrier_quote_auto_rail.pdf", car_quotes[:4]), ("D4_carrier_quote_turkmen_air.pdf", car_quotes[4:]))):
        corrs: list[dict[str, Any]] = []
        rows = [_d4_pdf_row(ctx, q, idx, corrs) for idx, q in enumerate(subset)]
        render_pdf(ctx.docs_dir / fname, [{"header": {"title": f"КОММЕРЧЕСКИЕ ПРЕДЛОЖЕНИЯ ПЕРЕВОЗЧИКОВ — ЧАСТЬ {p_idx + 1}"}, "sections": [{"heading": "Тарифы доставки", "table": {"headers": ["Номер КП", "Маршрут", "Тип", "Ставка/уп.", "НДС", "Срок"], "rows": rows}}], "footer": fname}], watermark=ctx.watermark)
        ctx.add_artifact(fname, "D4", [f"quotes/{q['quote_id']}" for q in subset], corrs)

    routes_list = ctx.truth["routes"].to_dict("records")
    route_refs = [f"routes/{r['route_id']}" for r in routes_list]
    for yr in (2025, 2026):
        fname = f"D4_carrier_rates_{yr}.xlsx"
        rows_x = [[r["route_code"], ctx.car_map[r["carrier_id"]]["name"], r["mode"], r["corridor"], float(r["freight_per_shipment_usd"]), float(r["freight_per_pack_usd"]), int(r["transit_days"]), r["customs_post"]] for r in routes_list]
        write_xlsx(ctx.docs_dir / fname, [SheetSpec(name=f"Rates_{yr}", headers=["Код маршрута", "Перевозчик", "Вид транспорта", "Коридор", "За партию USD", "За упак. USD", "Срок дн.", "Пост"], rows=rows_x)])
        ctx.add_artifact(fname, "D4", route_refs, [])

    _build_d4_chats(ctx, car_quotes)


def _d5_page_for_contract(ctx: _WorldCtx, cnt: dict[str, Any], c_idx: int, is_v2: bool, corrs: list[dict[str, Any]]) -> dict[str, Any]:
    party = ctx.sup_map.get(cnt["party_id"]) or ctx.car_map.get(cnt["party_id"]) or {"name": "Контрагент (SYNTHETIC)"}
    price = float(cnt["unit_price"])
    moq = int(cnt["min_order_qty"])
    if is_v2:
        corrs.append(ctx.corr("X2", "unit_price", c_idx + 1, [200, 300, 360, 324], price, round(price * 1.07, 2), f"Противоречивый проект договора {cnt['contract_number']} v2; используйте подписанную редакцию"))
        corrs.append(ctx.corr("X2", "min_order_qty", c_idx + 1, [200, 326, 360, 350], moq, moq + 2000, f"Конфликт MOQ в версии v2 договора {cnt['contract_number']}"))
        price, moq = round(price * 1.07, 2), moq + 2000
    elif ctx.has("C1") and c_idx == 0:
        shown_p = corrupt_currency_omit(price, cnt["currency"])
        corrs.append(ctx.corr("C1", "unit_price", c_idx + 1, [200, 300, 340, 324], {"amount": price, "currency": cnt["currency"]}, shown_p, "Валюта договора указана в коммерческом инвойсе"))
    return {
        "header": {"title": f"ДОГОВОР № {cnt['contract_number']}{' (ПРОЕКТ v2)' if is_v2 else ''}", "subtitle": f"Контрагент: {party['name']}", "meta": f"Подписан: {cnt['signed_date']} | Действует до: {cnt['valid_until']}"},
        "sections": [
            {"heading": "Условия цены и поставки", "kv_rows": [("Валюта", cnt["currency"]), ("Цена", f"{price:.2f} {cnt['currency']}"), ("MOQ", f"{moq} уп."), ("Оплата", cnt["payment_terms"]), ("Базис", cnt["incoterms"])]},
            {"heading": "Примечание", "paragraphs": [str(cnt["amendment_note"])]},
        ],
        "footer": f"Договор {cnt['contract_number']} | Стр. {c_idx + 1}",
    }


def _build_d5_pdf(ctx: _WorldCtx, cnt_list: list[dict[str, Any]], fname: str, is_v2: bool = False) -> None:
    corrs: list[dict[str, Any]] = []
    pages = [_d5_page_for_contract(ctx, cnt, c_idx, is_v2, corrs) for c_idx, cnt in enumerate(cnt_list)]
    render_pdf(ctx.docs_dir / fname, pages, watermark=ctx.watermark)
    ctx.add_artifact(fname, "D5", [f"contracts/{c['contract_id']}" for c in cnt_list], corrs)


def _build_d5_all(ctx: _WorldCtx) -> None:
    contracts = ctx.truth["contracts"].to_dict("records")
    groups = (
        ("D5_contract_CNT-SUP-2025-01.pdf", contracts[0:2]),
        ("D5_contract_CNT-SUP-2025-02.pdf", contracts[2:4]),
        ("D5_contract_CNT-SUP-2025-03.pdf", contracts[4:5]),
        ("D5_contract_CNT-SUP-2025-04.pdf", contracts[5:6]),
        ("D5_contract_CNT-CAR-2025-01_02.pdf", contracts[6:8]),
        ("D5_contract_CNT-CAR-2025-03_04.pdf", contracts[8:10]),
    )
    for fname, grp in groups:
        _build_d5_pdf(ctx, grp, fname, is_v2=False)
    if ctx.has("X2"):
        for fname, grp in (("D5_contract_CNT-SUP-2025-01_v2.pdf", contracts[0:2]), ("D5_contract_CNT-SUP-2025-02_v2.pdf", contracts[2:4]), ("D5_contract_CNT-SUP-2025-03_v2.pdf", contracts[4:5])):
            _build_d5_pdf(ctx, grp, fname, is_v2=True)


def _d6_lot_row(ctx: _WorldCtx, lt: dict[str, Any], apply_u1: bool, page_num: int, corrs: list[dict[str, Any]]) -> list[Any]:
    qty_txt = f"{int(lt['initial_qty'])} упак."
    if apply_u1:
        u_inf = corrupt_unit(int(lt["initial_qty"]), float(lt["unit_cost_uzs"]), ctx.rng)
        qty_txt = f"{u_inf['qty']} ({u_inf['unit']})"
        corrs.append(ctx.corr("U1", "initial_qty", page_num, [300, 260, 440, 285], int(lt["initial_qty"]), qty_txt, "Пересчитайте блистеры/коробки в упаковки (3 блистера/уп., 10 уп./коробка)"))
    return [lt["lot_code"], ctx.sku_label(lt["sku_id"]), qty_txt, lt["manufacture_date"], lt["expiry_date"]]


def _d6_shipment_page(ctx: _WorldCtx, b_idx: int, p_i: int, shp: dict[str, Any], shp_lots: list[dict[str, Any]], corrs: list[dict[str, Any]]) -> dict[str, Any]:
    sup = ctx.sup_map[shp["supplier_id"]]
    cnt = ctx.cnt_map[shp["contract_id"]]
    disp = str(shp["dispatch_date"])
    if ctx.has("N2") and p_i == 0 and (b_idx < 5 or ctx.level_name != "L1"):
        shown_d = corrupt_date(disp, ctx.rng)
        corrs.append(ctx.corr("N2", "dispatch_date", p_i + 1, [160, 120, 300, 145], disp, shown_d, "Приведите дату отгрузки к ISO YYYY-MM-DD"))
        disp = shown_d
    use_u1 = ctx.has("U1") and b_idx in (0, 2, 5) and p_i == 0
    lot_rows = [_d6_lot_row(ctx, lt, use_u1 and l_i == 0, p_i + 1, corrs) for l_i, lt in enumerate(shp_lots)]
    return {
        "header": {"title": f"INVOICE & PACKING LIST {shp['invoice_number']}", "subtitle": f"Supplier: {sup['name']} | Contract: {cnt['contract_number']}", "meta": f"Dispatch: {disp} | Arrival: {shp['arrival_date']}"},
        "sections": [{"heading": "Packing List", "table": {"headers": ["Серия", "Препарат", "Количество", "Дата пр-ва", "Срок годн."], "rows": lot_rows}}],
        "footer": f"Invoice {shp['invoice_number']} | Total packs: {int(shp['total_packs'])}",
    }


def _build_d6_bundle(ctx: _WorldCtx, b_idx: int, shps: list[dict[str, Any]]) -> None:
    lots_df = ctx.truth["lots"]
    corrs: list[dict[str, Any]] = []
    pages: list[dict[str, Any]] = []
    refs: list[str] = []
    for p_i, shp in enumerate(shps):
        refs.append(f"shipments/{shp['shipment_id']}")
        shp_lots = lots_df[lots_df["shipment_id"] == shp["shipment_id"]].to_dict("records")
        for lt in shp_lots:
            refs.append(f"lots/{lt['lot_id']}")
        pages.append(_d6_shipment_page(ctx, b_idx, p_i, shp, shp_lots, corrs))
    fname = f"D6_invoices_bundle_{b_idx + 1:02d}.pdf"
    render_pdf(ctx.docs_dir / fname, pages, watermark=ctx.watermark)
    ctx.add_artifact(fname, "D6", refs, corrs)


def _build_d6_all(ctx: _WorldCtx) -> None:
    shps = ctx.truth["shipments"].to_dict("records")
    for b_idx in range(0, len(shps), 4):
        _build_d6_bundle(ctx, b_idx // 4, shps[b_idx : b_idx + 4])


_D7_C2_SLOTS = {"L3": (0, 5, 11), "L4": (0, 5, 11, 15, 19)}
_D7_C3_SLOTS = {"L3": (1, 6, 12, 17), "L4": (1, 6, 12, 17, 20)}
_D7_N2_SLOTS = {"L1": (2,), "L2": (2, 9), "L3": (2, 9), "L4": (2, 9, 14)}


def _d7_corrupt_row(ctx: _WorldCtx, yr: int, idx: int, rec: dict[str, Any], corrs_x: list[dict[str, Any]], corrs_p: list[dict[str, Any]]) -> tuple[Any, Any, str]:
    cip_uzs: Any = round(float(rec["cip_uzs"]), 2)
    landed: Any = round(float(rec["landed_cost_ex_vat_uzs"]), 2)
    cdate = str(rec["clearance_date"])
    if ctx.has("C2") and idx in _D7_C2_SLOTS.get(ctx.level_name, ()):
        c2 = corrupt_currency_wrong(float(rec["cip_usd"]), ctx.rng)
        corrs_x.append(ctx.corr("C2", "cip_uzs", 1, [320, 140 + idx * 18, 440, 160 + idx * 18], cip_uzs, c2["display"], "Сумма в USD записана в колонке UZS; пересчитайте по курсу ЦБ на дату ГТД"))
        cip_uzs = c2["display"]
    if ctx.has("C3") and idx in _D7_C3_SLOTS.get(ctx.level_name, ()):
        c3 = corrupt_fx_wrong_date(float(rec["cip_usd"]), float(rec["fx_rate_uzs"]), float(rec["fx_rate_uzs"]) * 0.95, f"{yr}-01-05")
        corrs_p.append(ctx.corr("C3", "landed_cost_ex_vat_uzs", 1, [380, 140 + idx * 18, 520, 160 + idx * 18], landed, c3["shown_uzs"], "Применён неверный курс валюты; пересчитайте по курсу ЦБ на дату оформления ГТД"))
        landed = c3["shown_uzs"]
    if ctx.has("N2") and idx in _D7_N2_SLOTS.get(ctx.level_name, ()):
        shown_d = corrupt_date(cdate, ctx.rng)
        corrs_p.append(ctx.corr("N2", "clearance_date", 1, [140, 140 + idx * 18, 240, 160 + idx * 18], cdate, shown_d, "Приведите дату ГТД к формату YYYY-MM-DD"))
        cdate = shown_d
    return cip_uzs, landed, cdate


def _build_d7_year(ctx: _WorldCtx, yr: int, recs: list[dict[str, Any]]) -> None:
    corrs_x: list[dict[str, Any]] = []
    corrs_p: list[dict[str, Any]] = []
    rows_x: list[list[Any]] = []
    rows_p: list[list[Any]] = []
    for idx, rec in enumerate(recs):
        shp = ctx.shp_map[rec["shipment_id"]]
        cip_uzs, landed, cdate = _d7_corrupt_row(ctx, yr, idx, rec, corrs_x, corrs_p)
        rows_x.append([rec["gtd_number"], shp["invoice_number"], rec["clearance_date"], rec["customs_post"], float(rec["fx_rate_uzs"]), cip_uzs, float(rec["duty_uzs"]), float(rec["landed_cost_ex_vat_uzs"])])
        rows_p.append([rec["gtd_number"], shp["invoice_number"], cdate, str(rec["fx_rate_uzs"]), str(rec["cip_usd"]), str(landed)])

    fx_name = f"D7_customs_summary_{yr}.xlsx"
    write_xlsx(ctx.docs_dir / fx_name, [SheetSpec(name=f"Customs_{yr}", headers=["Номер ГТД", "Инвойс", "Дата ГТД", "Таможенный пост", "Курс ЦБ", "CIP UZS", "Пошлина UZS", "Себестоимость без НДС"], rows=rows_x)])
    refs = [f"customs/{r['customs_id']}" for r in recs]
    ctx.add_artifact(fx_name, "D7", refs, corrs_x)

    fp_name = f"D7_customs_summary_{yr}.pdf"
    render_pdf(ctx.docs_dir / fp_name, [{"header": {"title": f"СВОДКА ТАМОЖЕННОЙ ОЧИСТКИ — {yr} (УЧЕБНАЯ)"}, "sections": [{"heading": "Реестр ГТД", "table": {"headers": ["Номер ГТД", "Инвойс", "Дата ГТД", "Курс", "CIP USD", "Себест. без НДС"], "rows": rows_p}}], "footer": f"{fp_name} | SYNTHETIC"}], watermark=ctx.watermark)
    ctx.add_artifact(fp_name, "D7", refs, corrs_p)


def _build_d7_all(ctx: _WorldCtx) -> None:
    customs = ctx.truth["customs"]
    _build_d7_year(ctx, 2025, customs[customs["clearance_date"].str.startswith("2025-")].to_dict("records"))
    _build_d7_year(ctx, 2026, customs[customs["clearance_date"].str.startswith("2026-")].to_dict("records"))


def _d8_photo_table_rows(ctx: _WorldCtx, p_idx: int, all_lots: list[dict[str, Any]], corrs: list[dict[str, Any]]) -> tuple[list[list[str]], float]:
    rows: list[list[str]] = []
    line_sums: list[float] = []
    for r_i, lt in enumerate(all_lots):
        qty = int(lt["initial_qty"])
        price = round(float(lt["unit_cost_uzs"]) * 1.15, 2)
        lsum = round(qty * price, 2)
        line_sums.append(lsum)
        qty_str = f"{qty:,}".replace(",", " ")
        if ctx.has("U1") and r_i == 0 and p_idx in (0, 2):
            u_inf = corrupt_unit(qty, price, ctx.rng)
            qty_str = f"{u_inf['qty']} {u_inf['unit']}"
            corrs.append(ctx.corr("U1", "initial_qty", 1, [660, 268, 790, 320], qty, qty_str, "Пересчитайте единицы измерения в упаковки"))
        sku = ctx.sku_map[lt["sku_id"]]
        rows.append([str(r_i + 1), sku["trade_name"], sku["nomenclature_code"], lt["lot_code"], "упак.", qty_str, f"{price:,.2f}", f"{lsum:,.2f}"])
    total_sum = round(sum(line_sums), 2)
    if ctx.has("A1") and p_idx in (1, 3):
        a1 = corrupt_arithmetic(line_sums, ctx.rng)
        corrs.append(ctx.corr("A1", "grand_total_uzs", 1, [56, 600, 1144, 644], a1["true_total"], a1["shown_total"], "Пересчитайте итог накладной по сумме строк"))
        total_sum = a1["shown_total"]
    return rows, total_sum


def _d8_record_r1(ctx: _WorldCtx, is_severe: bool, qty: int, bbox: tuple[int, int, int, int], corrs: list[dict[str, Any]]) -> None:
    if not ctx.has("R1"):
        return
    hint = "Фото накладной сильно размыто/засвечено; запросите бумажный оригинал со склада" if is_severe else "Сверьте скан накладной со складской книгой остатков XLSX"
    fields = ("qty_packs", "unit_price_uzs", "line_total_uzs") if is_severe else ("qty_packs",)
    for fld in fields:
        corrs.append(ctx.corr("R1", fld, 1, list(bbox), qty, "severe_occlusion" if is_severe else "scan_noise", hint, recoverable=not is_severe))


def _d8_photo_visual_corrs(ctx: _WorldCtx, p_idx: int, focus_lot: dict[str, Any], total_sum: float, bboxes: dict[str, tuple[int, int, int, int]], corrs: list[dict[str, Any]]) -> bool:
    is_severe = ctx.allow_unrec and p_idx in (0, 1)
    qty = int(focus_lot["initial_qty"])
    _d8_record_r1(ctx, is_severe, qty, bboxes["row_0_qty"], corrs)
    if ctx.has("R2"):
        corrs.append(ctx.corr("R2", "initial_qty", 1, list(bboxes["row_0_qty"]), qty, "stamp_over_digits", "Уточните количество под печатью по складскому реестру"))
        corrs.append(ctx.corr("R2", "grand_total_uzs", 1, list(bboxes["total"]), total_sum, "stamp_over_total", "Подтвердите сумму под печатью по складскому учёту"))
    if ctx.has("R3") and p_idx in (0, 2):
        corrs.append(ctx.corr("R3", "handwritten_qty", 1, list(bboxes["row_0_qty"]), qty, qty + 200, "Сверьте рукописную правку количества с учётной карточкой партии"))
    return is_severe


def _build_d8_photo_item(ctx: _WorldCtx, p_idx: int, focus_lot: dict[str, Any], bg_lots: list[dict[str, Any]]) -> None:
    all_lots = [focus_lot] + bg_lots[:5]
    headers = ["№", "Наименование", "Код", "Серия", "Ед.", "Кол-во", "Цена UZS", "Сумма UZS"]
    corrs: list[dict[str, Any]] = []
    rows, total_sum = _d8_photo_table_rows(ctx, p_idx, all_lots, corrs)
    meta = [
        f"Дата отгрузки: {focus_lot['arrival_date']} | Склад: Ташкент-Центральный",
        f"Грузополучатель: ООО «ShifoMed Distribution» (SYNTHETIC) | Основание: {ctx.shp_map[focus_lot['shipment_id']]['invoice_number']}",
    ]
    footer = [f"ИТОГО / JAMI: {total_sum:,.2f} UZS", "Отпустил / Topshirdi: Сардор Исмаилов ____________"]
    bboxes = compute_table_image_bboxes(headers, rows, len(meta), width=1200)
    is_severe = _d8_photo_visual_corrs(ctx, p_idx, focus_lot, total_sum, bboxes, corrs)

    def _post(im):
        out = apply_stamp_overlay(im, bboxes["row_0_qty"], ctx.rng) if ctx.has("R2") else im
        if ctx.has("R2"):
            out = apply_stamp_overlay(out, bboxes["total"], ctx.rng)
        if ctx.has("R3") and p_idx in (0, 2):
            out = apply_handwritten_correction(out, bboxes["row_0_qty"], str(int(focus_lot["initial_qty"]) + 200), ctx.rng)
        return apply_scan_photo_effects(out, ctx.rng, severe=is_severe) if ctx.has("R1") else out

    fname = f"D8_waybill_photo_{p_idx + 1:02d}.jpg"
    render_table_document_image(ctx.docs_dir / fname, f"РАСХОДНАЯ НАКЛАДНАЯ № {focus_lot['waybill_number']} / YUK XATI", meta, headers, rows, footer_lines=footer, watermark=ctx.watermark, postprocess_fn=_post)
    ctx.add_artifact(fname, "D8", [f"lots/{lt['lot_id']}" for lt in all_lots], corrs)


def _build_d8_pdf_item(ctx: _WorldCtx, idx: int, lt: dict[str, Any], bg: dict[str, Any]) -> None:
    corrs: list[dict[str, Any]] = []
    qty_txt = f"{int(lt['initial_qty'])} упак."
    if ctx.has("U1") and (idx in (0, 3) or ctx.level_name == "L4"):
        u_inf = corrupt_unit(int(lt["initial_qty"]), float(lt["unit_cost_uzs"]), ctx.rng)
        qty_txt = f"{u_inf['qty']} ({u_inf['unit']})"
        corrs.append(ctx.corr("U1", "initial_qty", 1, [240, 280, 400, 310], int(lt["initial_qty"]), qty_txt, "Пересчитайте блистеры/коробки в упаковки"))
    pages = [{
        "header": {"title": f"СКЛАДСКАЯ НАКЛАДНАЯ № {lt['waybill_number']}", "subtitle": "Склад: Ташкент-Центральный (SYNTHETIC)", "meta": f"Дата: {lt['arrival_date']} | Инвойс: {ctx.shp_map[lt['shipment_id']]['invoice_number']}"},
        "sections": [{"heading": "Позиции", "table": {"headers": ["Серия", "Препарат", "Количество", "Срок годн."], "rows": [[lt["lot_code"], ctx.sku_label(lt["sku_id"]), qty_txt, lt["expiry_date"]], [bg["lot_code"], ctx.sku_label(bg["sku_id"]), f"{int(bg['initial_qty'])} упак.", bg["expiry_date"]]]}}],
        "footer": f"Накладная {lt['waybill_number']}",
    }]
    fname = f"D8_waybill_{idx + 1:02d}.pdf"
    render_pdf(ctx.docs_dir / fname, pages, watermark=ctx.watermark)
    refs = [f"lots/{lt['lot_id']}", f"lots/{bg['lot_id']}"]
    ctx.add_artifact(fname, "D8", refs, corrs)
    if ctx.has("D1x") and idx < 3:
        dup_name = f"D8_waybill_{idx + 1:02d}_dup.pdf"
        pages[0]["header"]["title"] = f"СКЛАДСКАЯ НАКЛАДНАЯ № {lt['waybill_number']}-ПОВТ"
        render_pdf(ctx.docs_dir / dup_name, pages, watermark=ctx.watermark)
        dup_corrs = [
            ctx.corr("D1x", "waybill_number", 1, [80, 740, 420, 780], lt["waybill_number"], f"{lt['waybill_number']}-ПОВТ", f"Дубль накладной по серии {lt['lot_code']}; исключите повторный приход"),
            ctx.corr("D1x", "lot_code", 1, [80, 540, 260, 570], bg["lot_code"], f"{bg['lot_code']}-DUP", f"Дубль строки серии {bg['lot_code']} в повторной накладной"),
        ]
        ctx.add_artifact(dup_name, "D8", refs, dup_corrs)


def _build_d8_all(ctx: _WorldCtx) -> None:
    lots = ctx.truth["lots"].to_dict("records")
    focus_lots = [r for r in lots if r["sku_id"] == "SKU_CARDIO_01"]
    bg_lots = [r for r in lots if r["sku_id"] != "SKU_CARDIO_01"]
    for idx in range(6):
        _build_d8_pdf_item(ctx, idx, focus_lots[idx % len(focus_lots)], bg_lots[idx % len(bg_lots)])
    for p_idx in range(4):
        _build_d8_photo_item(ctx, p_idx, focus_lots[(p_idx * 3) % len(focus_lots)], bg_lots[p_idx * 5 : p_idx * 5 + 5] or bg_lots[:5])


def _build_d9_period(ctx: _WorldCtx, fname: str, sub_lots: pd.DataFrame, sub_inv: pd.DataFrame, sub_wo: pd.DataFrame) -> None:
    corrs: list[dict[str, Any]] = []
    lot_rows = []
    for idx, r in enumerate(sub_lots.to_dict("records")):
        qty_cell: Any = int(r["initial_qty"])
        if ctx.has("N1") and idx == 0:
            qty_cell = corrupt_number(qty_cell, ctx.rng)
            corrs.append(ctx.corr("N1", "initial_qty", 1, [300, 120, 400, 145], int(r["initial_qty"]), qty_cell, "Нормализуйте числовой формат прихода партии"))
        lot_rows.append([r["lot_code"], r["waybill_number"], ctx.sku_label(r["sku_id"]), r["arrival_date"], r["expiry_date"], qty_cell, int(r["sold_qty"]), int(r["writeoff_qty"]), int(r["remaining_qty"])])
    inv_rows = [[r["month"], ctx.sku_label(r["sku_id"]), int(r["opening_stock"]), int(r["received_qty"]), int(r["sold_qty"]), int(r["writeoff_qty"]), int(r["closing_stock"])] for r in sub_inv.to_dict("records")]
    wo_rows = [[f"СПС-{i + 1:03d}", r["writeoff_date"], ctx.lot_map[r["lot_id"]]["lot_code"], ctx.sku_label(r["sku_id"]), int(r["qty_packs"]), float(r["writeoff_cost_uzs"]), r["reason"]] for i, r in enumerate(sub_wo.to_dict("records"))]
    write_xlsx(
        ctx.docs_dir / fname,
        [
            SheetSpec(name="Партии", headers=["Серия", "Накладная", "Препарат", "Приход", "Срок годн.", "Нач. остаток", "Продано", "Списано", "Остаток"], rows=lot_rows),
            SheetSpec(name="Движение_месяц", headers=["Месяц", "Препарат", "Нач. остаток", "Приход", "Продажи", "Списание", "Кон. остаток"], rows=inv_rows),
            SheetSpec(name="Списания", headers=["Акт", "Дата", "Серия", "Препарат", "Упаковок", "Сумма UZS", "Причина"], rows=wo_rows),
        ],
    )
    refs = [f"lots/{lid}" for lid in sub_lots["lot_id"].astype(str).tolist()] + [f"writeoffs/{wid}" for wid in sub_wo["writeoff_id"].astype(str).tolist()]
    ctx.add_artifact(fname, "D9", refs, corrs)


def _build_d9_all(ctx: _WorldCtx) -> None:
    lots, inv, wo = ctx.truth["lots"], ctx.truth["inventory_monthly"], ctx.truth["writeoffs"]
    _build_d9_period(ctx, "D9_warehouse_stock_2025_H1.xlsx", lots[lots["arrival_date"] < "2025-07-01"], inv[inv["month"] <= "2025-06"], wo[wo["month"] <= "2025-06"])
    _build_d9_period(ctx, "D9_warehouse_stock_2025_H2.xlsx", lots[(lots["arrival_date"] >= "2025-07-01") & (lots["arrival_date"] < "2026-01-01")], inv[(inv["month"] >= "2025-07") & (inv["month"] <= "2025-12")], wo[(wo["month"] >= "2025-07") & (wo["month"] <= "2025-12")])
    _build_d9_period(ctx, "D9_warehouse_stock_2026.xlsx", lots[lots["arrival_date"] >= "2026-01-01"], inv[inv["month"] >= "2026-01"], wo[wo["month"] >= "2026-01"])


def _d10_cost_rows(ctx: _WorldCtx, sub_k: list[dict[str, Any]], corrs: list[dict[str, Any]]) -> list[list[Any]]:
    n1_slots = (0, 25, 45, 65, 85, 105) if ctx.level_name == "L4" else (0, 25)
    rows: list[list[Any]] = []
    for idx, r in enumerate(sub_k):
        amt: Any = float(r["amount_uzs"])
        if ctx.has("N1") and idx in n1_slots:
            amt = corrupt_number(amt, ctx.rng)
            corrs.append(ctx.corr("N1", "amount_uzs", 2, [340, 120 + idx, 460, 140 + idx], float(r["amount_uzs"]), amt, "Приведите сумму затрат к числовому формату"))
        elif ctx.has("C2") and idx == 10:
            c2 = corrupt_currency_wrong(round(float(amt) / 12800.0, 2), ctx.rng)
            corrs.append(ctx.corr("C2", "amount_uzs", 2, [340, 220, 460, 240], float(amt), c2["display"], "Валюта расхода указана в USD вместо UZS"))
            amt = c2["display"]
        rows.append([r["cost_doc_no"], r["month"], ctx.sku_label(r["sku_id"]), r["category"], amt])
    return rows


def _build_d10_all(ctx: _WorldCtx) -> None:
    credit, costs = ctx.truth["credit"], ctx.truth["costs"]
    for yr in (2025, 2026):
        sub_c = credit[credit["month"].str.startswith(f"{yr}-")].to_dict("records")
        sub_k = costs[costs["month"].str.startswith(f"{yr}-")].to_dict("records")
        corrs: list[dict[str, Any]] = []
        interests = [float(r["monthly_interest_uzs"]) for r in sub_c]
        tot_int = round(sum(interests), 2)
        if ctx.has("A1") and interests:
            a1 = corrupt_arithmetic(interests, ctx.rng)
            corrs.append(ctx.corr("A1", "total_interest_uzs", 1, [420, 360, 560, 390], a1["true_total"], a1["shown_total"], "Пересчитайте сумму процентов по кредитной линии построчно"))
            tot_int = a1["shown_total"]
        cred_rows = [[r["payment_doc_no"], r["month"], float(r["opening_balance_uzs"]), float(r["drawdown_uzs"]), float(r["repayment_uzs"]), float(r["closing_balance_uzs"]), float(r["monthly_interest_uzs"])] for r in sub_c]
        cred_rows.append(["ИТОГО", str(yr), "", "", "", "", tot_int])
        cost_rows = _d10_cost_rows(ctx, sub_k, corrs)
        fname = f"D10_payments_credit_{yr}.xlsx"
        write_xlsx(ctx.docs_dir / fname, [SheetSpec(name="Кредит", headers=["Плат. док.", "Месяц", "Вх. остаток", "Выборка", "Погашение", "Исх. остаток", "Проценты UZS"], rows=cred_rows), SheetSpec(name="Расходы", headers=["Акт/Док.", "Месяц", "Номенклатура", "Статья", "Сумма UZS"], rows=cost_rows)])
        refs = [f"credit/{r['credit_id']}" for r in sub_c] + [f"costs/{r['cost_id']}" for r in sub_k]
        ctx.add_artifact(fname, "D10", refs, corrs)


def _d11_memo_corruptions(ctx: _WorldCtx, m_idx: int, fname: str, row: dict[str, Any], amt: float, adj: float) -> list[dict[str, Any]]:
    corrs: list[dict[str, Any]] = []
    is_sev = ctx.allow_unrec and m_idx < 2
    if ctx.has("R1"):
        hint_r1 = "Скан служебной записки нечитаем из-за засветки; запросите оригинал" if is_sev else "Сверьте размытый скан служебной записки с реестром затрат XLSX"
        corrs.append(ctx.corr("R1", "memo_scan_quality", 1, [60, 150, 1140, 520], amt, "severe_blur" if is_sev else "scan_noise", hint_r1, recoverable=not is_sev))
    if ctx.has("R2") and m_idx < 2:
        corrs.append(ctx.corr("R2", "amount_uzs", 1, [320, 305, 520, 340], amt, "stamp_over_digits", "Уточните сумму под печатью по реестру расходов"))
    if ctx.has("R3"):
        corrs.append(ctx.corr("R3", "amount_uzs", 1, [320, 305, 520, 340], amt, adj, "Сверьте рукописную правку суммы с реестром расходов"))
        corrs.append(ctx.corr("R3", "approved_budget_uzs", 1, [320, 355, 520, 390], amt, round(amt * 1.05, 2), "Уточните исправленную сумму по бухгалтерскому реестру"))
    if ctx.allow_unrec:
        corrs.append(ctx.corr("M1", "memo_page_2", 2, [0, 0, 1200, 1600], "page_2_breakdown", "MISSING_PAGE_2", "Оборотная сторона служебной записки утеряна; запросите оригинал", recoverable=False))
        ctx.omitted_documents.append({"doc_type": "D11", "scope": f"{fname}_page_2", "truth_ref": f"costs/{row['cost_id']}", "reason": "Утеряна 2-я страница служебной записки", "recoverable": False})
    return corrs


def _build_d11_memo(ctx: _WorldCtx, m_idx: int, fname: str, row: dict[str, Any], title: str) -> None:
    amt = round(float(row["amount_uzs"]), 2)
    adj = round(amt * 1.12, 2)
    lines = [
        {"text": f"Документ: {row['cost_doc_no']} | Месяц: {row['month']}", "style": "heading"},
        {"text": f"Номенклатура: {ctx.sku_label(row['sku_id'])} | Статья: {row['category']}", "style": "bold"},
        {"text": f"Сумма по бюджету: {amt:,.2f} UZS", "style": "mono", "y": 310},
        {"text": "Ichki xizmat xati: tasdiqlash uchun moliya boʻlimiga topshirildi.", "style": "body", "y": 390},
    ]
    corrs = _d11_memo_corruptions(ctx, m_idx, fname, row, amt, adj)
    is_sev = ctx.allow_unrec and m_idx < 2
    use_r2 = ctx.has("R2") and m_idx < 2

    def _post_m(im):
        out = apply_stamp_overlay(im, (320, 305, 520, 340), ctx.rng) if use_r2 else im
        out = apply_handwritten_correction(out, (320, 305, 520, 340), f"{adj:.0f}", ctx.rng) if ctx.has("R3") else out
        return apply_scan_photo_effects(out, ctx.rng, severe=is_sev) if ctx.has("R1") else out

    render_document_image(ctx.docs_dir / fname, title, lines, watermark=ctx.watermark, postprocess_fn=_post_m)
    ctx.add_artifact(fname, "D11", [f"costs/{row['cost_id']}"], corrs)


def _build_d11_all(ctx: _WorldCtx) -> None:
    costs = ctx.truth["costs"].to_dict("records")
    memos = (
        ("D11_memo_marketing_2025.jpg", costs[1], "Служебная записка: маркетинг / Marketing xarajatlari"),
        ("D11_memo_logistics_2025.jpg", costs[0], "Служебная записка: дистрибуция / Logistika xati"),
        ("D11_memo_writeoffs_2026.jpg", costs[120], "Служебная записка: списания / Hisobdan chiqarish"),
        ("D11_memo_finance_2026.jpg", costs[180], "Служебная записка: финансы / Moliya boʻlimi"),
    )
    for m_idx, (fname, row, title) in enumerate(memos):
        _build_d11_memo(ctx, m_idx, fname, row, title)


def _build_d12_all(ctx: _WorldCtx) -> None:
    quotes = ctx.truth["quotes"].to_dict("records")
    contracts = ctx.truth["contracts"].to_dict("records")
    customs = ctx.truth["customs"].to_dict("records")
    emls = (
        ("D12_email_quotes_2025.eml", f"Re: КП {quotes[0]['quote_number']} — Kardiozan 10 мг №30", "procurement@cardioimport.synthetic", "export@medisynth.synthetic", "Mon, 10 Feb 2025 10:15:00 +0500", f"Подтверждаем получение {quotes[0]['quote_number']}.", f"quotes/{quotes[0]['quote_id']}", f"D3_quote_{quotes[0]['quote_number']}.pdf"),
        ("D12_email_contracts_2025.eml", f"Договор {contracts[1]['contract_number']}", "legal@cardioimport.synthetic", "sales@medisynth.synthetic", "Tue, 21 Oct 2025 14:20:00 +0500", f"Направляем подписанное доп. соглашение {contracts[1]['contract_number']}.", f"contracts/{contracts[1]['contract_id']}", "D5_contract_CNT-SUP-2025-01.pdf"),
        ("D12_email_customs_2026.eml", f"Справка ГТД {customs[-1]['gtd_number']}", "logistics@cardioimport.synthetic", "finance@cardioimport.synthetic", "Wed, 15 Jul 2026 11:05:00 +0500", f"Декларация {customs[-1]['gtd_number']} оформлена на посту {customs[-1]['customs_post']}.", f"customs/{customs[-1]['customs_id']}", "D7_customs_summary_2026.pdf"),
        ("D12_email_supplier_2026.eml", f"Обновление цен по {quotes[4]['quote_number']}", "procurement@cardioimport.synthetic", "trade@vektorpharm.synthetic", "Mon, 17 Aug 2026 16:40:00 +0500", f"Просим подтвердить актуальность ставки {quotes[4]['unit_price']} {quotes[4]['currency']}.", f"quotes/{quotes[4]['quote_id']}", None),
    )
    for fname, subj, snd, rcp, dstr, body, ref, att_name in emls:
        att_p = (ctx.docs_dir / att_name) if att_name and (ctx.docs_dir / att_name).exists() else None
        write_eml_doc(ctx.docs_dir / fname, subj, snd, rcp, dstr, body, attachment_path=att_p)
        ctx.add_artifact(fname, "D12", [ref], [])

    for p_fname, ref, title, txt in (
        ("D12_email_printout_contracts.pdf", f"contracts/{contracts[1]['contract_id']}", "РАСПЕЧАТКА ПЕРЕПИСКИ ПО ДОГОВОРАМ", f"Согласовано доп. соглашение {contracts[1]['contract_number']} по цене {contracts[1]['unit_price']} {contracts[1]['currency']}."),
        ("D12_email_printout_customs.pdf", f"customs/{customs[-1]['customs_id']}", "РАСПЕЧАТКА ПЕРЕПИСКИ ПО ТАМОЖНЕ", f"Подтверждён курс на дату выпуска ГТД {customs[-1]['gtd_number']}: {customs[-1]['fx_rate_uzs']} UZS/USD."),
    ):
        render_pdf(ctx.docs_dir / p_fname, [{"header": {"title": title}, "sections": [{"heading": "Текст сообщения", "paragraphs": [txt]}], "footer": p_fname}], watermark=ctx.watermark)
        ctx.add_artifact(p_fname, "D12", [ref], [])


def _build_d13_year(ctx: _WorldCtx, yr: int, recs: list[dict[str, Any]], sub: list[dict[str, Any]]) -> None:
    headers = ["Реестровая запись", "Препарат", "Действует с", "Тип события", "Rn UZS", "B UZS", "Bx UZS", "Расч. база без НДС", "Опт без НДС", "ChUN с НДС", "ChChN с НДС"]
    csv_rows = [[r["registry_record_no"], "Kardiozan 10 мг №30", r["effective_from"], r["event_type"], r["rn_uzs"], r["b_uzs"], r["bx_uzs"], r["calc_base_ex_vat_uzs"], r["wholesale_ex_vat_uzs"], r["chun_with_vat_uzs"], r["chchn_with_vat_uzs"]] for r in sub]
    c_name = f"D13_registry_extract_{yr}.csv"
    write_csv_doc(ctx.docs_dir / c_name, headers, csv_rows, encoding="utf-8", delimiter=";", decimal_comma=False)
    ctx.add_artifact(c_name, "D13", [f"registry_events/{r['event_id']}" for r in sub], [])

    is_stale = yr == 2026 and ctx.has("S1") and len(recs) > 2
    shown = recs[1] if is_stale else recs[-1]
    corrs_p: list[dict[str, Any]] = []
    if is_stale:
        for fld in ("chchn_with_vat_uzs", "chun_with_vat_uzs", "wholesale_ex_vat_uzs", "calc_base_ex_vat_uzs"):
            corrs_p.append(ctx.corr("S1", fld, 1, [260, 220, 420, 260], float(recs[-1][fld]), float(shown[fld]), "Выписка PDF содержит устаревшую запись RE-3735-2025-07; актуальная запись RE-3735-2025-11 в CSV"))
    p_name = f"D13_registry_extract_{yr}.pdf"
    render_pdf(
        ctx.docs_dir / p_name,
        [{"header": {"title": f"ВЫПИСКА ИЗ РЕЕСТРА ЦЕН № {shown['registry_record_no']} ({yr})", "subtitle": "Приказ МЗ РУз №3735 — Kardiozan 10 мг №30"}, "sections": [{"heading": "Показатели", "kv_rows": [("Rn", shown["rn_uzs"]), ("B", shown["b_uzs"]), ("Bx (2%)", shown["bx_uzs"]), ("Расч. база", shown["calc_base_ex_vat_uzs"]), ("Опт без НДС", shown["wholesale_ex_vat_uzs"]), ("ChUN с НДС", shown["chun_with_vat_uzs"]), ("ChChN с НДС", shown["chchn_with_vat_uzs"])]}], "footer": p_name}],
        watermark=ctx.watermark,
    )
    ctx.add_artifact(p_name, "D13", [f"registry_events/{shown['event_id']}"], corrs_p)


def _build_d13_all(ctx: _WorldCtx) -> None:
    recs = ctx.truth["registry_events"].to_dict("records")
    _build_d13_year(ctx, 2025, recs, recs[:2])
    _build_d13_year(ctx, 2026, recs, recs)


def _build_j1_all(ctx: _WorldCtx) -> None:
    if not ctx.has("J1"):
        return
    ref = [f"skus/{ctx.truth['skus'].iloc[0]['sku_id']}"]
    for fname, title, msg in (
        ("J1_junk_promo_biovita.pdf", "РЕКЛАМНЫЙ БУКЛЕТ: ВИТАМИНЫ BIO-VITA (SYNTHETIC)", "Спецпредложение на витамины Bio-Vita C 500 мг."),
        ("J1_junk_promo_cardio_mix.pdf", "КАТАЛОГ БАД И ЧАЁВ (SYNTHETIC)", "Не относится к рецептурному кардиопортфелю."),
    ):
        render_pdf(ctx.docs_dir / fname, [{"header": {"title": title}, "sections": [{"heading": "Реклама", "paragraphs": [msg]}], "footer": fname}], watermark=ctx.watermark)
        ctx.add_artifact(fname, "J1", ref, [ctx.corr("J1", "irrelevant_file", 1, [0, 0, 595, 842], "unrelated", fname, "Игнорируйте рекламный буклет, не относящийся к рецептурному портфелю")])

    write_csv_doc(ctx.docs_dir / "J1_junk_unrelated_otc_catalog.csv", ["Код", "Наименование", "Цена UZS"], [["OTC-901", "VitaMix Syrup 100ml", 42000.0], ["OTC-902", "HerbalTea №20", 18500.0]], encoding="utf-8", delimiter=";", decimal_comma=False)
    ctx.add_artifact("J1_junk_unrelated_otc_catalog.csv", "J1", ref, [
        ctx.corr("J1", "irrelevant_catalog_row_1", 1, [0, 0, 400, 80], "unrelated", "OTC-901", "Игнорируйте посторонний прайс ОТС"),
        ctx.corr("J1", "irrelevant_catalog_row_2", 1, [0, 80, 400, 140], "unrelated", "OTC-902", "Игнорируйте посторонний прайс ОТС"),
    ])

    for b_fname in ("J1_junk_blank_scan_01.jpg", "J1_junk_blank_scan_02.jpg"):
        render_document_image(ctx.docs_dir / b_fname, "Пустой лист сканера (SYNTHETIC)", [{"text": "— Лист без данных / Blank sheet —", "style": "body"}], watermark=ctx.watermark, postprocess_fn=lambda im: apply_scan_photo_effects(im, ctx.rng, severe=False))
        ctx.add_artifact(b_fname, "J1", ref, [ctx.corr("J1", "blank_scan", 1, [0, 0, 1200, 1600], "blank_scan", b_fname, "Игнорируйте пустой лист сканера")])


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

    ctx = _init_ctx(level_name, level_cfg, world_cfg, truth_tables, docs_dir, seed)
    _build_d1_all(ctx)
    _build_d2_all(ctx)
    _build_d3_all(ctx)
    _build_d4_all(ctx)
    _build_d5_all(ctx)
    _build_d6_all(ctx)
    _build_d7_all(ctx)
    _build_d8_all(ctx)
    _build_d9_all(ctx)
    _build_d10_all(ctx)
    _build_d11_all(ctx)
    _build_d12_all(ctx)
    _build_d13_all(ctx)
    _build_j1_all(ctx)

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
