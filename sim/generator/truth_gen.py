"""Deterministic, causally consistent ground-truth generator for LAGRANGE Rx."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from sim.generator.config_loader import load_fx_table, load_registry_sku
from sim.generator.domain import (
    generate_address,
    generate_carrier_name,
    generate_counterparty_name,
    generate_person_name,
    generate_supplier_name,
    generate_synthetic_inn,
)

TRUTH_TABLE_NAMES: tuple[str, ...] = (
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
)

_SUPPLIER_SPECS: tuple[dict[str, Any], ...] = (
    {
        "supplier_id": "sup_01",
        "supplier_code": "ZBP-IND",
        "country": "Индия",
        "currency": "USD",
        "payment_terms": "60 дней отсрочка",
        "min_order_qty": 5000,
        "monthly_capacity": 35000,
        "lead_time_days": 22,
        "reliability_prob": 0.93,
    },
    {
        "supplier_id": "sup_02",
        "supplier_code": "ANAT-TR",
        "country": "Турция",
        "currency": "USD",
        "payment_terms": "30% предоплата / 70% по прибытии",
        "min_order_qty": 3000,
        "monthly_capacity": 22000,
        "lead_time_days": 18,
        "reliability_prob": 0.95,
    },
    {
        "supplier_id": "sup_03",
        "supplier_code": "BALT-DE",
        "country": "Германия",
        "currency": "EUR",
        "payment_terms": "45 дней отсрочка",
        "min_order_qty": 4000,
        "monthly_capacity": 25000,
        "lead_time_days": 24,
        "reliability_prob": 0.97,
    },
    {
        "supplier_id": "sup_04",
        "supplier_code": "KRK-SI",
        "country": "Словения",
        "currency": "EUR",
        "payment_terms": "100% аккредитив",
        "min_order_qty": 2500,
        "monthly_capacity": 18000,
        "lead_time_days": 21,
        "reliability_prob": 0.94,
    },
)

_CARRIER_SPECS: tuple[dict[str, Any], ...] = (
    {"carrier_id": "car_01", "mode": "Auto via Kazakhstan", "mode_ru": "Авто через Казахстан", "reliability_prob": 0.92},
    {"carrier_id": "car_02", "mode": "Auto via Turkmenistan", "mode_ru": "Авто через Туркменистан", "reliability_prob": 0.90},
    {"carrier_id": "car_03", "mode": "Rail", "mode_ru": "Ж/Д", "reliability_prob": 0.95},
    {"carrier_id": "car_04", "mode": "Air", "mode_ru": "Авиа", "reliability_prob": 0.98},
)

_ROUTE_SPECS: tuple[dict[str, Any], ...] = (
    {
        "route_id": "rte_01",
        "route_code": "KZ-AUTO-01",
        "carrier_id": "car_01",
        "mode": "Auto via Kazakhstan",
        "corridor": "Индия/ЕАЭС -> Казахстан -> Ташкент",
        "freight_per_shipment_usd": 1800.0,
        "freight_per_pack_usd": 0.12,
        "transit_days": 18,
        "incoterms": "CIP Ташкент",
        "customs_post": "Т/п Яллама (27012)",
    },
    {
        "route_id": "rte_02",
        "route_code": "TM-AUTO-02",
        "carrier_id": "car_02",
        "mode": "Auto via Turkmenistan",
        "corridor": "Турция -> Туркменистан -> Ташкент",
        "freight_per_shipment_usd": 1650.0,
        "freight_per_pack_usd": 0.14,
        "transit_days": 16,
        "incoterms": "CIP Ташкент",
        "customs_post": "Т/п Алат (20005)",
    },
    {
        "route_id": "rte_03",
        "route_code": "CHUK-RAIL-03",
        "carrier_id": "car_03",
        "mode": "Rail",
        "corridor": "ЕС/РФ -> Казахстан -> Ж/Д Чукурсай",
        "freight_per_shipment_usd": 2200.0,
        "freight_per_pack_usd": 0.09,
        "transit_days": 22,
        "incoterms": "DAP Чукурсай",
        "customs_post": "Т/п Чукурсай (26003)",
    },
    {
        "route_id": "rte_04",
        "route_code": "TAS-AIR-04",
        "carrier_id": "car_04",
        "mode": "Air",
        "corridor": "Международный хаб -> Аэропорт Ташкент",
        "freight_per_shipment_usd": 4800.0,
        "freight_per_pack_usd": 0.65,
        "transit_days": 5,
        "incoterms": "CIP Аэропорт Ташкент",
        "customs_post": "Т/п Аэропорт Ташкент (26001)",
    },
)

# Monthly focus-SKU sales volumes across 22 months (2025-01..2026-10).
# Last 12 months (index 10..21: 2025-11..2026-10) sum to 149,250 packs (within 150,000 ±3%).
_FOCUS_MONTHLY_SALES: tuple[int, ...] = (
    11200, 11400, 11800, 11600, 11200, 10800, 10500, 10700, 11300, 11800,
    12740, 13320, 13510, 12840, 12520, 12040, 11530, 11140, 11430, 12140, 12770, 13270,
)

# Monthly focus-SKU shipment quantities (one focus shipment per month).
_FOCUS_MONTHLY_ORDERS: tuple[int, ...] = (
    16500, 11500, 12000, 11500, 11500, 11000, 10500, 11000, 11500, 12000,
    13000, 13500, 13500, 13000, 12500, 12000, 11500, 11500, 11500, 12500, 13000, 13500,
)

_BG_MONTHLY_BASE: dict[str, int] = {
    "SKU_BG_01": 4200,
    "SKU_BG_02": 3600,
    "SKU_BG_03": 3100,
    "SKU_BG_04": 4800,
    "SKU_BG_05": 2400,
    "SKU_BG_06": 3900,
}


def _month_sequence(start_month: str, count: int) -> list[str]:
    year, month = (int(p) for p in start_month.split("-"))
    out: list[str] = []
    for offset in range(count):
        total_m = (year * 12 + (month - 1)) + offset
        out.append(f"{total_m // 12:04d}-{(total_m % 12) + 1:02d}")
    return out


def _month_start_date(month_str: str) -> date:
    year, month = (int(p) for p in month_str.split("-"))
    return date(year, month, 1)


def _lookup_fx_rate(fx_df: pd.DataFrame, target_date: str, currency: str) -> float:
    sub = fx_df[fx_df["currency"] == currency]
    prior = sub[sub["date"] <= target_date]
    row = prior.iloc[-1] if not prior.empty else sub.iloc[0]
    return float(row["rate_uzs"])


def _allocate_integer_weights(total: int, weights: np.ndarray) -> list[int]:
    norm = weights / weights.sum()
    raw = norm * total
    floors = np.floor(raw).astype(int)
    remainder = int(total - floors.sum())
    if remainder > 0:
        order = np.argsort(-(raw - floors), kind="mergesort")
        for idx in order[:remainder]:
            floors[idx] += 1
    return [int(v) for v in floors]


def _build_suppliers(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for spec in _SUPPLIER_SPECS:
        rows.append(
            {
                **spec,
                "name": generate_supplier_name(rng, spec["country"]),
                "contact_person": generate_person_name(rng),
                "address": generate_address(rng, spec["country"]),
                "inn": generate_synthetic_inn(rng),
            }
        )
    return pd.DataFrame(rows)


def _build_carriers(rng: np.random.Generator) -> pd.DataFrame:
    rows = []
    for spec in _CARRIER_SPECS:
        rows.append(
            {
                **spec,
                "name": generate_carrier_name(rng),
                "contact_person": generate_person_name(rng),
                "inn": generate_synthetic_inn(rng),
            }
        )
    return pd.DataFrame(rows)


def _build_routes() -> pd.DataFrame:
    return pd.DataFrame(list(_ROUTE_SPECS))


def _build_skus(config: dict[str, Any]) -> pd.DataFrame:
    focus = config["focus_sku"]
    targets = config["calibration_targets"]
    rows: list[dict[str, Any]] = [
        {
            "sku_id": focus["sku_id"],
            "trade_name": focus["trade_name"],
            "inn_name": focus["inn_name"],
            "dosage_form": focus["dosage_form"],
            "nomenclature_code": focus.get("nomenclature_code", "00-000142"),
            "reg_cert_no": focus.get("reg_cert_no", "DV/X 04812/01/25"),
            "ean13": focus.get("ean13", "4780099120014"),
            "is_focus": True,
            "pack_blisters": int(focus["pack_blisters"]),
            "blister_tabs": int(focus["blister_tabs"]),
            "box_packs": int(focus["box_packs"]),
            "shelf_life_months": int(focus["shelf_life_months"]),
            "base_cip_usd": float(focus["base_cip_usd"]),
            "wholesale_ex_vat_uzs": float(targets["wholesale_ex_vat_uzs"]),
            "retail_uzs": float(targets["chchn_with_vat_uzs"]),
        }
    ]
    vat = float(targets["vat_rate"])
    for idx, bg in enumerate(config["background_skus"], start=1):
        retail = float(bg["retail_uzs"])
        rows.append(
            {
                "sku_id": bg["sku_id"],
                "trade_name": bg["trade_name"],
                "inn_name": bg["inn_name"],
                "dosage_form": "таблетки, покрытые оболочкой (SYNTHETIC)",
                "nomenclature_code": bg.get("nomenclature_code", f"00-00020{idx}"),
                "reg_cert_no": bg.get("reg_cert_no", f"DV/X 0481{2 + idx}/01/25"),
                "ean13": bg.get("ean13", f"47800991200{14 + idx}"),
                "is_focus": False,
                "pack_blisters": 3,
                "blister_tabs": 10,
                "box_packs": 10,
                "shelf_life_months": int(bg["shelf_life_months"]),
                "base_cip_usd": float(bg["base_cip_usd"]),
                "wholesale_ex_vat_uzs": round(retail / (1.0 + float(targets["retail_markup_limit"])) / (1.0 + vat), 2),
                "retail_uzs": retail,
            }
        )
    return pd.DataFrame(rows)


def _build_registry_events(config: dict[str, Any], registry_sku: dict[str, Any]) -> pd.DataFrame:
    t = config["calibration_targets"]
    vat = float(t["vat_rate"])
    sku_id = config["focus_sku"]["sku_id"]
    rn_1, b_1 = 127000.0, 2633.91
    bx_1 = round(0.02 * rn_1, 2)
    rn_2, b_2 = 132000.0, 2751.30
    bx_2 = round(0.02 * rn_2, 2)
    rn_3 = float(t["rn_uzs"])
    b_3 = float(t["b_uzs"])
    bx_3 = round(0.02 * rn_3, 2)
    rows = [
        {
            "event_id": "reg_0001",
            "registry_record_no": "RE-3735-2025-01",
            "sku_id": sku_id,
            "event_date": registry_sku.get("initial_registration_date", "2025-01-15"),
            "effective_from": "2025-01-01",
            "confirmed_date": "2025-01-15",
            "event_type": "initial_registration",
            "rn_uzs": rn_1,
            "b_uzs": b_1,
            "bx_uzs": bx_1,
            "calc_base_ex_vat_uzs": round(rn_1 + b_1 + bx_1, 2),
            "wholesale_ex_vat_uzs": 152000.0,
            "chun_with_vat_uzs": round(152000.0 * (1.0 + vat), 2),
            "chchn_with_vat_uzs": round(152000.0 * (1.0 + vat) * 1.2, 2),
            "vat_rate": vat,
            "order_reference": "Приказ МЗ РУз №3735 (учебная запись 2025-01)",
        },
        {
            "event_id": "reg_0002",
            "registry_record_no": "RE-3735-2025-07",
            "sku_id": sku_id,
            "event_date": registry_sku.get("indexation_date", "2025-07-01"),
            "effective_from": "2025-07-01",
            "confirmed_date": "2025-07-01",
            "event_type": "para_20_indexation",
            "rn_uzs": rn_2,
            "b_uzs": b_2,
            "bx_uzs": bx_2,
            "calc_base_ex_vat_uzs": round(rn_2 + b_2 + bx_2, 2),
            "wholesale_ex_vat_uzs": 158000.0,
            "chun_with_vat_uzs": round(158000.0 * (1.0 + vat), 2),
            "chchn_with_vat_uzs": round(158000.0 * (1.0 + vat) * 1.2, 2),
            "vat_rate": vat,
            "order_reference": "Приказ МЗ РУз №3735, п.20 (индексация 2025-07)",
        },
        {
            "event_id": "reg_0003",
            "registry_record_no": "RE-3735-2025-11",
            "sku_id": sku_id,
            "event_date": registry_sku.get("effective_from", "2025-11-01"),
            "effective_from": registry_sku.get("effective_from", "2025-11-01"),
            "confirmed_date": registry_sku.get("revision_date", "2026-07-01"),
            "event_type": "downward_price_revision",
            "rn_uzs": rn_3,
            "b_uzs": b_3,
            "bx_uzs": bx_3,
            "calc_base_ex_vat_uzs": round(rn_3 + b_3 + bx_3, 2),
            "wholesale_ex_vat_uzs": float(t["wholesale_ex_vat_uzs"]),
            "chun_with_vat_uzs": float(t["chun_with_vat_uzs"]),
            "chchn_with_vat_uzs": float(t["chchn_with_vat_uzs"]),
            "vat_rate": vat,
            "order_reference": registry_sku.get("order_reference", "Приказ МЗ РУз №3735"),
        },
    ]
    return pd.DataFrame(rows)


def _build_supplier_quotes(
    rng: np.random.Generator,
    suppliers: pd.DataFrame,
    skus: pd.DataFrame,
    dates: list[str],
    focus_cip: float,
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(16):
        sup = suppliers.iloc[idx % 4]
        q_date = date.fromisoformat(dates[idx % len(dates)])
        valid_until = q_date + timedelta(days=90 if idx % 3 else 45)
        sku_row = skus.iloc[0 if idx < 10 else (idx % len(skus))]
        raw_price = focus_cip if sku_row["sku_id"] == "SKU_CARDIO_01" else float(sku_row["base_cip_usd"])
        base_price = round(raw_price * 0.92, 2) if sup["currency"] == "EUR" else raw_price
        rows.append(
            {
                "quote_id": f"q_{idx + 1:04d}",
                "quote_number": f"KP-{q_date.year}-{idx + 1:03d}",
                "quote_type": "supplier",
                "entity_id": sup["supplier_id"],
                "sku_id": sku_row["sku_id"],
                "route_id": "",
                "quote_date": q_date.isoformat(),
                "valid_until": valid_until.isoformat(),
                "unit_price": round(base_price * float(rng.uniform(0.985, 1.035)), 2),
                "currency": sup["currency"],
                "min_order_qty": int(sup["min_order_qty"]),
                "lead_time_days": int(sup["lead_time_days"]),
                "terms": sup["payment_terms"],
                "status": "expired" if valid_until < date(2026, 10, 1) else "active",
            }
        )
    return rows


def _build_carrier_quotes(
    rng: np.random.Generator,
    routes: pd.DataFrame,
    dates: list[str],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for idx in range(8):
        rte = routes.iloc[idx % 4]
        q_date = date.fromisoformat(dates[(idx + 1) % len(dates)])
        valid_until = q_date + timedelta(days=120)
        freight_pack = round(float(rte["freight_per_pack_usd"]) * float(rng.uniform(0.95, 1.08)), 3)
        rows.append(
            {
                "quote_id": f"q_{17 + idx:04d}",
                "quote_number": f"KP-{q_date.year}-{17 + idx:03d}",
                "quote_type": "carrier",
                "entity_id": rte["carrier_id"],
                "sku_id": "SKU_CARDIO_01",
                "route_id": rte["route_id"],
                "quote_date": q_date.isoformat(),
                "valid_until": valid_until.isoformat(),
                "unit_price": freight_pack,
                "currency": "USD",
                "min_order_qty": 3000,
                "lead_time_days": int(rte["transit_days"]),
                "terms": f"{rte['incoterms']}; фикс {rte['freight_per_shipment_usd']:.0f} USD/отгрузка",
                "status": "expired" if valid_until < date(2026, 10, 1) else "active",
            }
        )
    return rows


def _build_quotes(
    rng: np.random.Generator,
    suppliers: pd.DataFrame,
    routes: pd.DataFrame,
    skus: pd.DataFrame,
) -> pd.DataFrame:
    dates = ["2025-01-05", "2025-07-10", "2026-01-12", "2026-05-15", "2026-08-10"]
    sup_rows = _build_supplier_quotes(rng, suppliers, skus, dates, 7.32)
    car_rows = _build_carrier_quotes(rng, routes, dates)
    return pd.DataFrame(sup_rows + car_rows)


def _build_contracts(suppliers: pd.DataFrame, carriers: pd.DataFrame, quotes: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    sup_configs = [
        ("cnt_0001", "", "sup_01", "CNT-SUP-2025-01", "2025-01-05", "2025-01-05", "2025-12-31", 7.48, 5000, "expired", "Первичный договор поставки"),
        ("cnt_0002", "cnt_0001", "sup_01", "CNT-SUP-2025-01-A1", "2025-10-20", "2025-11-01", "2026-12-31", 7.22, 5000, "active", "Доп. соглашение №1: снижение CIP после пересмотра реестра"),
        ("cnt_0003", "", "sup_02", "CNT-SUP-2025-02", "2025-01-10", "2025-01-10", "2026-12-31", 7.42, 3000, "active", "Резервный поставщик Турция"),
        ("cnt_0004", "cnt_0003", "sup_02", "CNT-SUP-2025-02-A1", "2026-06-15", "2026-07-01", "2027-06-30", 7.15, 3000, "active", "Продление и фиксация цены"),
        ("cnt_0005", "", "sup_03", "CNT-SUP-2025-03", "2025-02-01", "2025-02-01", "2026-12-31", 6.65, 4000, "active", "Поставка фоновых и кардио позиций из ЕС"),
        ("cnt_0006", "", "sup_04", "CNT-SUP-2025-04", "2025-03-01", "2025-03-01", "2026-03-01", 6.80, 2500, "expired", "Рамочный контракт Словения"),
    ]
    for cid, parent, sid, num, signed, eff, valid, price, moq, status, note in sup_configs:
        sup = suppliers.loc[suppliers["supplier_id"] == sid].iloc[0]
        rows.append(
            {
                "contract_id": cid,
                "parent_contract_id": parent,
                "party_type": "supplier",
                "party_id": sid,
                "contract_number": num,
                "signed_date": signed,
                "effective_from": eff,
                "valid_until": valid,
                "currency": sup["currency"],
                "unit_price": price,
                "min_order_qty": moq,
                "payment_terms": sup["payment_terms"],
                "incoterms": "CIP Ташкент",
                "status": status,
                "amendment_note": note,
            }
        )

    # 4 carrier contracts (cnt_0007..cnt_0010)
    for idx in range(4):
        car = carriers.iloc[idx]
        q_match = quotes[quotes["entity_id"] == car["carrier_id"]].iloc[0]
        rows.append(
            {
                "contract_id": f"cnt_{7 + idx:04d}",
                "parent_contract_id": "",
                "party_type": "carrier",
                "party_id": car["carrier_id"],
                "contract_number": f"CNT-CAR-2025-0{idx + 1}",
                "signed_date": "2025-01-08",
                "effective_from": "2025-01-08",
                "valid_until": "2026-12-31",
                "currency": "USD",
                "unit_price": float(q_match["unit_price"]),
                "min_order_qty": 3000,
                "payment_terms": "14 дней после растаможки",
                "incoterms": "CIP Ташкент",
                "status": "active",
                "amendment_note": f"Базовый договор перевозки ({car['mode_ru']})",
            }
        )
    return pd.DataFrame(rows)


def _make_shipment_row(
    shp_idx: int,
    month_str: str,
    sup_id: str,
    cnt_id: str,
    rte: pd.Series,
    disp_d: date,
    delay: int,
    qty: int,
    sku_group: str,
) -> tuple[dict[str, Any], date]:
    planned = int(rte["transit_days"])
    transit = planned + delay
    arr_d = disp_d + timedelta(days=transit)
    freight = round(float(rte["freight_per_shipment_usd"]) + qty * float(rte["freight_per_pack_usd"]), 2)
    row = {
        "shipment_id": f"shp_{shp_idx:04d}",
        "invoice_number": f"INV-{month_str[:4]}-{shp_idx:03d}",
        "month": month_str,
        "supplier_id": sup_id,
        "contract_id": cnt_id,
        "route_id": rte["route_id"],
        "carrier_id": rte["carrier_id"],
        "dispatch_date": disp_d.isoformat(),
        "planned_transit_days": planned,
        "delay_days": delay,
        "transit_days": transit,
        "arrival_date": arr_d.isoformat(),
        "total_packs": qty,
        "freight_usd": freight,
        "sku_group": sku_group,
    }
    return row, arr_d


def _make_customs_row(
    cus_idx: int,
    shp_id: str,
    arr_d: date,
    customs_post: str,
    fx_usd: float,
    cip_uzs: float,
    duty_uzs: float,
    fee_uzs: float,
    inbound_uzs: float,
    total_qty: int,
    vat_rate: float,
) -> dict[str, Any]:
    landed = round(cip_uzs + duty_uzs + fee_uzs + inbound_uzs, 2)
    return {
        "customs_id": f"cus_{cus_idx:04d}",
        "gtd_number": f"26004/{arr_d.year}-{cus_idx:04d}",
        "shipment_id": shp_id,
        "clearance_date": arr_d.isoformat(),
        "customs_post": customs_post,
        "currency": "USD",
        "fx_rate_uzs": fx_usd,
        "cip_usd": round(cip_uzs / fx_usd, 2),
        "cip_uzs": cip_uzs,
        "duty_uzs": duty_uzs,
        "clearance_fee_uzs": fee_uzs,
        "inbound_freight_uzs": inbound_uzs,
        "vat_uzs": round((cip_uzs + duty_uzs + fee_uzs) * vat_rate, 2),
        "landed_cost_ex_vat_uzs": landed,
        "landed_unit_cost_uzs": round(landed / total_qty, 2),
    }


def _make_lot_row(
    lot_seq: int,
    prefix: str,
    month_str: str,
    shp_id: str,
    sku_id: str,
    mfg_d: date,
    disp_d: date,
    arr_d: date,
    exp_d: date,
    qty: int,
    unit_cost: float,
) -> dict[str, Any]:
    return {
        "lot_id": f"lot_{lot_seq:04d}",
        "lot_code": f"{prefix}-{month_str.replace('-', '')}-{lot_seq:02d}",
        "waybill_number": f"РН-{month_str[:4]}-{lot_seq:04d}",
        "shipment_id": shp_id,
        "sku_id": sku_id,
        "manufacture_date": mfg_d.isoformat(),
        "dispatch_date": disp_d.isoformat(),
        "arrival_date": arr_d.isoformat(),
        "expiry_date": exp_d.isoformat(),
        "initial_qty": int(qty),
        "sold_qty": 0,
        "writeoff_qty": 0,
        "remaining_qty": int(qty),
        "unit_cost_uzs": unit_cost,
    }


def _assemble_shipment_bundle(
    shp_idx: int,
    month_str: str,
    sup_id: str,
    cnt_id: str,
    rte: pd.Series,
    disp_d: date,
    delay: int,
    arr_d: date,
    fx_usd: float,
    lot_specs: list[dict[str, Any]],
    cip_uzs: float,
    duty_uzs: float,
    fee_uzs: float,
    inbound_uzs: float,
    vat_rate: float,
    sku_group: str,
    lot_seq: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], int]:
    shp_id = f"shp_{shp_idx:04d}"
    qty_tot = sum(int(spec["qty"]) for spec in lot_specs)
    ship_row, _ = _make_shipment_row(shp_idx, month_str, sup_id, cnt_id, rte, disp_d, delay, qty_tot, sku_group)
    cust_row = _make_customs_row(
        shp_idx, shp_id, arr_d, str(rte["customs_post"]), fx_usd,
        cip_uzs, duty_uzs, fee_uzs, inbound_uzs, qty_tot, vat_rate,
    )
    lots: list[dict[str, Any]] = []
    for spec in lot_specs:
        unit_cost = float(spec.get("unit_cost") or cust_row["landed_unit_cost_uzs"])
        lots.append(
            _make_lot_row(
                lot_seq, spec["prefix"], month_str, shp_id, spec["sku_id"],
                spec["mfg_d"], disp_d, arr_d, spec["exp_d"], int(spec["qty"]), unit_cost,
            )
        )
        lot_seq += 1
    return ship_row, lots, cust_row, lot_seq


def _pick_focus_supplier_and_contract(m_idx: int) -> tuple[str, str]:
    if m_idx % 3 == 2:
        return "sup_02", ("cnt_0003" if m_idx < 18 else "cnt_0004")
    return "sup_01", ("cnt_0001" if m_idx < 10 else "cnt_0002")


def _focus_lot_specs(
    rng: np.random.Generator,
    m_idx: int,
    qty: int,
    disp_d: date,
) -> list[dict[str, Any]]:
    splits = [qty - 400, 400] if m_idx in (1, 15) else [qty]
    specs: list[dict[str, Any]] = []
    for s_idx, l_qty in enumerate(splits):
        mfg_d = disp_d - timedelta(days=655 if s_idx == 1 else int(rng.integers(45, 85)))
        specs.append(
            {
                "prefix": "KZ",
                "sku_id": "SKU_CARDIO_01",
                "qty": l_qty,
                "mfg_d": mfg_d,
                "exp_d": mfg_d + timedelta(days=730),
                "unit_cost": None,
            }
        )
    return specs


def _focus_shipment_for_month(
    rng: np.random.Generator,
    m_idx: int,
    month_str: str,
    routes: pd.DataFrame,
    contract_prices: dict[str, float],
    fx_df: pd.DataFrame,
    targets: dict[str, Any],
    lot_seq: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], int]:
    rte = routes.iloc[0 if m_idx % 3 != 2 else 1]
    disp_d = _month_start_date(month_str) + timedelta(days=1)
    delay = int(rng.integers(0, 3))
    arr_d = disp_d + timedelta(days=int(rte["transit_days"]) + delay)
    qty = int(_FOCUS_MONTHLY_ORDERS[m_idx])
    sup, cnt = _pick_focus_supplier_and_contract(m_idx)

    fx_usd = _lookup_fx_rate(fx_df, arr_d.isoformat(), "USD")
    cip_usd = round(qty * contract_prices[cnt] * float(rng.uniform(0.985, 1.025)), 2)
    cip_uzs = round(cip_usd * fx_usd, 2)
    duty_uzs = round(cip_uzs * 0.02, 2)
    clearance_uzs = round(float(rng.uniform(800_000.0, 1_400_000.0)), 2)
    freight_usd = float(rte["freight_per_shipment_usd"]) + qty * float(rte["freight_per_pack_usd"])
    inbound_uzs = round(freight_usd * fx_usd + qty * float(rng.uniform(1150.0, 1450.0)), 2)
    lot_specs = _focus_lot_specs(rng, m_idx, qty, disp_d)

    return _assemble_shipment_bundle(
        m_idx * 2 + 1, month_str, sup, cnt, rte, disp_d, delay, arr_d,
        fx_usd, lot_specs, cip_uzs, duty_uzs, clearance_uzs, inbound_uzs,
        float(targets["vat_rate"]), "focus", lot_seq,
    )


def _build_bg_lot_specs(
    rng: np.random.Generator,
    m_idx: int,
    skus: pd.DataFrame,
    bg_ids: list[str],
    disp_d: date,
    fx_usd: float,
) -> tuple[list[dict[str, Any]], int, float]:
    has_two = m_idx in (0, 6, 12, 18)
    bg_indices = [(m_idx * 2) % 6, (m_idx * 2 + 1) % 6] if has_two else [m_idx % 6]
    lot_specs: list[dict[str, Any]] = []
    cip_tot = 0.0
    qty_tot = 0
    for pos, bg_i in enumerate(bg_indices):
        sku_row = skus[skus["sku_id"] == bg_ids[bg_i]].iloc[0]
        l_qty = int(_BG_MONTHLY_BASE[sku_row["sku_id"]] * (1.5 if has_two else 2.2))
        l_qty += 8000 if (m_idx == 0 and pos == 0) else 0
        mfg_d = disp_d - timedelta(days=560 if (m_idx == 6 and pos == 1) else int(rng.integers(45, 80)))
        base_cip = float(sku_row["base_cip_usd"]) * fx_usd
        lot_specs.append(
            {
                "prefix": "BG",
                "sku_id": str(sku_row["sku_id"]),
                "qty": l_qty,
                "mfg_d": mfg_d,
                "exp_d": mfg_d + timedelta(days=int(sku_row["shelf_life_months"]) * 30),
                "unit_cost": round(base_cip * 1.045, 2),
            }
        )
        qty_tot += l_qty
        cip_tot += round(base_cip * l_qty, 2)
    return lot_specs, qty_tot, cip_tot


def _bg_shipment_for_month(
    rng: np.random.Generator,
    m_idx: int,
    month_str: str,
    routes: pd.DataFrame,
    skus: pd.DataFrame,
    bg_ids: list[str],
    fx_df: pd.DataFrame,
    vat_rate: float,
    lot_seq: int,
) -> tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any], int]:
    rte = routes.iloc[2 if m_idx % 4 != 3 else 0]
    disp_d = _month_start_date(month_str) + timedelta(days=2)
    delay = int(rng.integers(0, 3))
    arr_d = disp_d + timedelta(days=int(rte["transit_days"]) + delay)
    fx_usd = _lookup_fx_rate(fx_df, arr_d.isoformat(), "USD")
    lot_specs, qty_tot, cip_tot = _build_bg_lot_specs(rng, m_idx, skus, bg_ids, disp_d, fx_usd)

    return _assemble_shipment_bundle(
        m_idx * 2 + 2, month_str,
        "sup_03" if m_idx % 2 == 0 else "sup_04",
        "cnt_0005" if m_idx % 2 == 0 else "cnt_0006",
        rte, disp_d, delay, arr_d, fx_usd, lot_specs,
        cip_tot, round(cip_tot * 0.025, 2), round(qty_tot * 1100.0, 2), 0.0,
        vat_rate, "background", lot_seq,
    )


def _build_shipments_lots_customs(
    rng: np.random.Generator,
    months: list[str],
    routes: pd.DataFrame,
    contracts: pd.DataFrame,
    skus: pd.DataFrame,
    fx_df: pd.DataFrame,
    config: dict[str, Any],
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Generate 44 shipments, 50 lots, and 44 1-to-1 customs records."""
    targets = config["calibration_targets"]
    vat_rate = float(targets["vat_rate"])
    bg_ids = [s for s in skus["sku_id"] if s != "SKU_CARDIO_01"]
    contract_prices = {str(r["contract_id"]): float(r["unit_price"]) for _, r in contracts.iterrows()}

    ship_rows: list[dict[str, Any]] = []
    lot_rows: list[dict[str, Any]] = []
    cust_rows: list[dict[str, Any]] = []
    lot_seq = 1

    for m_idx, month_str in enumerate(months):
        s_a, l_a, c_a, lot_seq = _focus_shipment_for_month(
            rng, m_idx, month_str, routes, contract_prices, fx_df, targets, lot_seq
        )
        s_b, l_b, c_b, lot_seq = _bg_shipment_for_month(
            rng, m_idx, month_str, routes, skus, bg_ids, fx_df, vat_rate, lot_seq
        )
        ship_rows.extend([s_a, s_b])
        lot_rows.extend(l_a + l_b)
        cust_rows.extend([c_a, c_b])

    return pd.DataFrame(ship_rows), pd.DataFrame(lot_rows), pd.DataFrame(cust_rows)


def _registry_price_for_month(month_str: str, registry_events: pd.DataFrame) -> float:
    active = registry_events[registry_events["effective_from"] <= f"{month_str}-01"]
    return float(active.iloc[-1]["wholesale_ex_vat_uzs"])


def _consume_from_lots(
    lots_state: list[dict[str, Any]],
    sku_id: str,
    month_str: str,
    qty_needed: int,
) -> list[tuple[str, int, float]]:
    """Consume qty_needed FIFO from arrived lots of sku_id up to month_str."""
    allocations: list[tuple[str, int, float]] = []
    rem = qty_needed
    eligible = [
        lot for lot in lots_state
        if lot["sku_id"] == sku_id and lot["arrival_date"][:7] <= month_str and lot["remaining_qty"] > 0
    ]
    for lot in eligible:
        if rem <= 0:
            break
        take = min(rem, lot["remaining_qty"])
        lot["sold_qty"] += take
        lot["remaining_qty"] -= take
        rem -= take
        allocations.append((lot["lot_id"], take, float(lot["unit_cost_uzs"])))
    return allocations


def _process_expiring_writeoffs(
    lots_state: list[dict[str, Any]],
    sku_id: str,
    month_str: str,
    wo_counter: list[int],
    writeoff_rows: list[dict[str, Any]],
) -> int:
    """Write off remaining stock whose expiry_date is within 90 days of month-end."""
    wo_date = _month_start_date(month_str) + timedelta(days=27)
    total_wo = 0
    for lot in lots_state:
        if lot["sku_id"] != sku_id or lot["remaining_qty"] <= 0:
            continue
        if date.fromisoformat(lot["arrival_date"]) > wo_date:
            continue
        days_left = (date.fromisoformat(lot["expiry_date"]) - wo_date).days
        if days_left <= 90:
            qty = int(lot["remaining_qty"])
            lot["writeoff_qty"] += qty
            lot["remaining_qty"] = 0
            total_wo += qty
            writeoff_rows.append(
                {
                    "writeoff_id": f"wo_{wo_counter[0]:04d}",
                    "month": month_str,
                    "writeoff_date": wo_date.isoformat(),
                    "lot_id": lot["lot_id"],
                    "sku_id": sku_id,
                    "qty_packs": qty,
                    "unit_cost_uzs": float(lot["unit_cost_uzs"]),
                    "writeoff_cost_uzs": round(qty * float(lot["unit_cost_uzs"]), 2),
                    "days_to_expiry": days_left,
                    "reason": "Истечение допустимого остаточного срока годности (<= 90 дней)",
                }
            )
            wo_counter[0] += 1
    return total_wo


def _record_sku_month_sales(
    lots_state: list[dict[str, Any]],
    sku_row: pd.Series,
    month_str: str,
    reg_chan_pairs: list[tuple[str, str]],
    cell_qtys: list[int],
    unit_price_ex_vat: float,
    vat_rate: float,
    sale_seq: int,
    sale_rows: list[dict[str, Any]],
    meta_ctx: dict[str, Any],
) -> int:
    sku_id = str(sku_row["sku_id"])
    trade_name = str(sku_row["trade_name"])
    nomenclature_code = str(sku_row["nomenclature_code"])
    unit_price_with_vat = round(unit_price_ex_vat * (1.0 + vat_rate), 2)
    m_compact = month_str.replace("-", "")
    reg_map = meta_ctx["region_map"]
    cp_map = meta_ctx["counterparty_map"]
    lot_codes = meta_ctx["lot_code_map"]

    for (reg_id, ch_name), c_qty in zip(reg_chan_pairs, cell_qtys):
        if c_qty <= 0:
            continue
        reg_info = reg_map[reg_id]
        cp_name = cp_map[(reg_id, ch_name)]
        for lot_id, take_qty, unit_cost in _consume_from_lots(lots_state, sku_id, month_str, c_qty):
            sale_rows.append(
                {
                    "sale_id": f"sal_{sale_seq:05d}",
                    "doc_number": f"РД-{m_compact}-{sale_seq:05d}",
                    "month": month_str,
                    "region_id": reg_id,
                    "region_name_ru": reg_info["name_ru"],
                    "region_name_uz_lat": reg_info["name_uz_lat"],
                    "region_name_uz_cyr": reg_info["name_uz_cyr"],
                    "channel": ch_name,
                    "counterparty_name": cp_name,
                    "sku_id": sku_id,
                    "trade_name": trade_name,
                    "nomenclature_code": nomenclature_code,
                    "lot_id": lot_id,
                    "lot_code": lot_codes[lot_id],
                    "qty_packs": int(take_qty),
                    "unit_price_ex_vat_uzs": round(unit_price_ex_vat, 2),
                    "unit_price_with_vat_uzs": unit_price_with_vat,
                    "revenue_ex_vat_uzs": round(take_qty * unit_price_ex_vat, 2),
                    "cogs_ex_vat_uzs": round(take_qty * unit_cost, 2),
                }
            )
            sale_seq += 1
    return sale_seq


def _simulate_sku_month(
    rng: np.random.Generator,
    m_idx: int,
    month_str: str,
    sku_row: pd.Series,
    lots_state: list[dict[str, Any]],
    opening: int,
    registry_events: pd.DataFrame,
    base_weights: np.ndarray,
    reg_chan_pairs: list[tuple[str, str]],
    vat_rate: float,
    sale_seq: int,
    wo_counter: list[int],
    sale_rows: list[dict[str, Any]],
    inv_rows: list[dict[str, Any]],
    wo_rows: list[dict[str, Any]],
    meta_ctx: dict[str, Any],
) -> tuple[int, int]:
    sku_id = str(sku_row["sku_id"])
    received = sum(
        int(l["initial_qty"]) for l in lots_state
        if l["sku_id"] == sku_id and l["arrival_date"][:7] == month_str
    )
    available = opening + received
    if sku_id == "SKU_CARDIO_01":
        target_sales = int(_FOCUS_MONTHLY_SALES[m_idx])
        unit_price_ex_vat = _registry_price_for_month(month_str, registry_events)
    else:
        nominal = int(_BG_MONTHLY_BASE[sku_id] * float(rng.uniform(0.92, 1.08)))
        target_sales = min(nominal, max(0, available - 400))
        unit_price_ex_vat = float(sku_row["wholesale_ex_vat_uzs"])

    noisy_w = base_weights * rng.uniform(0.90, 1.10, size=len(base_weights))
    cell_qtys = _allocate_integer_weights(target_sales, noisy_w)
    sale_seq = _record_sku_month_sales(
        lots_state, sku_row, month_str, reg_chan_pairs, cell_qtys,
        unit_price_ex_vat, vat_rate, sale_seq, sale_rows, meta_ctx,
    )
    wo_qty = _process_expiring_writeoffs(lots_state, sku_id, month_str, wo_counter, wo_rows)
    closing = available - target_sales - wo_qty
    inv_rows.append(
        {
            "month": month_str,
            "sku_id": sku_id,
            "opening_stock": int(opening),
            "received_qty": int(received),
            "available_stock": int(available),
            "sold_qty": int(target_sales),
            "writeoff_qty": int(wo_qty),
            "closing_stock": int(closing),
        }
    )
    return int(closing), sale_seq


def _simulate_sales_inventory_writeoffs(
    rng: np.random.Generator,
    months: list[str],
    config: dict[str, Any],
    skus: pd.DataFrame,
    lots: pd.DataFrame,
    registry_events: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Run monthly causal simulation across SKUs, regions, channels, and FIFO lots."""
    lots_state = lots.to_dict(orient="records")
    regions = config["regions"]
    channels = list(config["channels"].items())
    base_weights = np.array(
        [float(r["demand_weight"]) * float(c_w) for r in regions for _, c_w in channels],
        dtype=float,
    )
    reg_chan_pairs = [(r["region_id"], c_name) for r in regions for c_name, _ in channels]
    vat_rate = float(config["calibration_targets"]["vat_rate"])
    meta_ctx = {
        "region_map": {r["region_id"]: r for r in regions},
        "lot_code_map": {l["lot_id"]: l["lot_code"] for l in lots_state},
        "counterparty_map": {
            (r["region_id"], c_name): generate_counterparty_name(rng, c_name, r["name_ru"])
            for r in regions
            for c_name, _ in channels
        },
    }

    sale_rows: list[dict[str, Any]] = []
    inv_rows: list[dict[str, Any]] = []
    wo_rows: list[dict[str, Any]] = []
    wo_counter = [1]
    sale_seq = 1
    opening_by_sku = {sku_id: 0 for sku_id in skus["sku_id"]}

    for m_idx, month_str in enumerate(months):
        for _, sku_row in skus.iterrows():
            closing, sale_seq = _simulate_sku_month(
                rng, m_idx, month_str, sku_row, lots_state,
                opening_by_sku[sku_row["sku_id"]], registry_events,
                base_weights, reg_chan_pairs, vat_rate, sale_seq, wo_counter,
                sale_rows, inv_rows, wo_rows, meta_ctx,
            )
            opening_by_sku[sku_row["sku_id"]] = closing

    return (
        pd.DataFrame(lots_state),
        pd.DataFrame(sale_rows),
        pd.DataFrame(inv_rows),
        pd.DataFrame(wo_rows),
    )


def _build_credit(
    months: list[str],
    customs: pd.DataFrame,
    shipments: pd.DataFrame,
    sales: pd.DataFrame,
) -> pd.DataFrame:
    ship_month = shipments.set_index("shipment_id")["month"].to_dict()
    cust = customs.copy()
    cust["month"] = cust["shipment_id"].map(ship_month)
    monthly_imports = cust.groupby("month")["landed_cost_ex_vat_uzs"].sum().to_dict()
    monthly_rev = sales.groupby("month")["revenue_ex_vat_uzs"].sum().to_dict()

    limit_uzs = 8_000_000_000.0
    annual_rate = 0.22
    balance = 1_800_000_000.0
    rows: list[dict[str, Any]] = []

    for idx, month_str in enumerate(months):
        opening = round(balance, 2)
        import_outflow = float(monthly_imports.get(month_str, 0.0))
        rev_inflow = float(monthly_rev.get(month_str, 0.0))
        drawdown = round(min(limit_uzs - opening, max(400_000_000.0, import_outflow * 0.55)), 2)
        repayment = round(min(opening + drawdown, max(350_000_000.0, rev_inflow * 0.48)), 2)
        closing = round(opening + drawdown - repayment, 2)
        interest = round(((opening + closing) / 2.0) * (annual_rate / 12.0), 2)
        rows.append(
            {
                "credit_id": f"crd_{idx + 1:04d}",
                "payment_doc_no": f"ПП-{month_str[:4]}-{idx + 1:02d}",
                "month": month_str,
                "credit_limit_uzs": limit_uzs,
                "opening_balance_uzs": opening,
                "drawdown_uzs": drawdown,
                "repayment_uzs": repayment,
                "closing_balance_uzs": closing,
                "annual_rate": annual_rate,
                "monthly_interest_uzs": interest,
            }
        )
        balance = closing
    return pd.DataFrame(rows)


def _build_window_focus_costs(
    months_window: list[str],
    sales: pd.DataFrame,
    inv: pd.DataFrame,
    writeoffs: pd.DataFrame,
    credit: pd.DataFrame,
    target_margin: float,
    cost_seq_start: int,
) -> tuple[list[dict[str, Any]], int]:
    """Build calibrated 6-category monthly costs for SKU_CARDIO_01 in one regime window."""
    sub_sales = sales[(sales["sku_id"] == "SKU_CARDIO_01") & (sales["month"].isin(months_window))]
    sub_inv = inv[(inv["sku_id"] == "SKU_CARDIO_01") & (inv["month"].isin(months_window))]
    sub_wo = writeoffs[(writeoffs["sku_id"] == "SKU_CARDIO_01") & (writeoffs["month"].isin(months_window))]

    total_rev = float(sub_sales["revenue_ex_vat_uzs"].sum())
    total_cogs = float(sub_sales["cogs_ex_vat_uzs"].sum())
    total_cost_budget = total_rev - total_cogs - (target_margin * total_rev)

    rev_by_m = sub_sales.groupby("month")["revenue_ex_vat_uzs"].sum().to_dict()
    qty_by_m = sub_sales.groupby("month")["qty_packs"].sum().to_dict()
    inv_by_m = sub_inv.set_index("month").to_dict(orient="index")
    wo_by_m = sub_wo.groupby("month")["writeoff_cost_uzs"].sum().to_dict()
    cred_by_m = credit.set_index("month")["monthly_interest_uzs"].to_dict()

    non_general_rows: list[tuple[str, str, float]] = []
    non_general_total = 0.0
    for m_str in months_window:
        m_qty = float(qty_by_m.get(m_str, 0.0))
        inv_m = inv_by_m[m_str]
        avg_stock = (float(inv_m["opening_stock"]) + float(inv_m["closing_stock"])) / 2.0
        cats = [
            ("distribution", round(m_qty * 2200.0, 2)),
            ("marketing", 45_000_000.0),
            ("warehouse", round(avg_stock * 900.0, 2)),
            ("financing", round(float(cred_by_m[m_str]) * 0.55, 2)),
            ("writeoffs", round(float(wo_by_m.get(m_str, 0.0)), 2)),
        ]
        for cat, amt in cats:
            non_general_rows.append((m_str, cat, amt))
            non_general_total += amt

    general_pool = max(0.0, total_cost_budget - non_general_total)
    rows: list[dict[str, Any]] = []
    seq = cost_seq_start
    for m_str, cat, amt in non_general_rows:
        rows.append(
            {
                "cost_id": f"cst_{seq:04d}",
                "cost_doc_no": f"АВР-{m_str[:4]}-{seq:03d}",
                "month": m_str,
                "sku_id": "SKU_CARDIO_01",
                "category": cat,
                "amount_uzs": amt,
            }
        )
        seq += 1

    allocated_gen = 0.0
    for idx, m_str in enumerate(months_window):
        if idx == len(months_window) - 1:
            gen_amt = round(general_pool - allocated_gen, 2)
        else:
            share = float(rev_by_m.get(m_str, 0.0)) / total_rev
            gen_amt = round(general_pool * share, 2)
            allocated_gen += gen_amt
        rows.append(
            {
                "cost_id": f"cst_{seq:04d}",
                "cost_doc_no": f"АВР-{m_str[:4]}-{seq:03d}",
                "month": m_str,
                "sku_id": "SKU_CARDIO_01",
                "category": "general",
                "amount_uzs": gen_amt,
            }
        )
        seq += 1
    return rows, seq


def _build_costs(
    months: list[str],
    config: dict[str, Any],
    sales: pd.DataFrame,
    inv: pd.DataFrame,
    writeoffs: pd.DataFrame,
    credit: pd.DataFrame,
) -> pd.DataFrame:
    _ = config
    pre_months = months[:10]
    post_months = months[10:]

    pre_rows, seq = _build_window_focus_costs(
        pre_months, sales, inv, writeoffs, credit, 0.2734, 1,
    )
    post_rows, seq = _build_window_focus_costs(
        post_months, sales, inv, writeoffs, credit, 0.0243, seq,
    )

    bg_sales = sales[sales["sku_id"] != "SKU_CARDIO_01"].groupby("month")["revenue_ex_vat_uzs"].sum().to_dict()
    bg_wo = writeoffs[writeoffs["sku_id"] != "SKU_CARDIO_01"].groupby("month")["writeoff_cost_uzs"].sum().to_dict()
    cred_by_m = credit.set_index("month")["monthly_interest_uzs"].to_dict()
    bg_rows: list[dict[str, Any]] = []
    for m_str in months:
        bg_rev = float(bg_sales.get(m_str, 0.0))
        cats = [
            ("distribution", round(bg_rev * 0.022, 2)),
            ("marketing", round(bg_rev * 0.030, 2)),
            ("warehouse", round(bg_rev * 0.012, 2)),
            ("financing", round(float(cred_by_m[m_str]) * 0.45, 2)),
            ("writeoffs", round(float(bg_wo.get(m_str, 0.0)), 2)),
            ("general", round(bg_rev * 0.024, 2)),
        ]
        for cat, amt in cats:
            bg_rows.append(
                {
                    "cost_id": f"cst_{seq:04d}",
                    "cost_doc_no": f"АВР-{m_str[:4]}-{seq:03d}",
                    "month": m_str,
                    "sku_id": "PORTFOLIO_BG",
                    "category": cat,
                    "amount_uzs": amt,
                }
            )
            seq += 1

    return pd.DataFrame(pre_rows + post_rows + bg_rows)


def _save_truth_tables(tables: dict[str, pd.DataFrame], truth_dir: Path) -> None:
    truth_dir.mkdir(parents=True, exist_ok=True)
    for name in TRUTH_TABLE_NAMES:
        df = tables[name]
        df.to_parquet(truth_dir / f"{name}.parquet", index=False, engine="pyarrow")
        records = df.to_dict(orient="records")
        json_text = json.dumps(records, sort_keys=True, indent=2, ensure_ascii=False)
        (truth_dir / f"{name}.json").write_text(json_text + "\n", encoding="utf-8")


def generate_truth(
    config: dict[str, Any],
    seed: int,
    truth_dir: Path | str,
    inputs_dir: Path | str,
) -> dict[str, pd.DataFrame]:
    """Generate and persist all deterministic ground-truth tables."""
    rng = np.random.default_rng(seed)
    truth_path = Path(truth_dir)
    inputs_path = Path(inputs_dir)

    fx_df = load_fx_table(inputs_path / "fx_cbu.csv")
    registry_sku = load_registry_sku(inputs_path / "registry_sku.json")
    months = _month_sequence(config["period"]["start_month"], int(config["period"]["months_count"]))

    suppliers = _build_suppliers(rng)
    carriers = _build_carriers(rng)
    routes = _build_routes()
    skus = _build_skus(config)
    registry_events = _build_registry_events(config, registry_sku)
    quotes = _build_quotes(rng, suppliers, routes, skus)
    contracts = _build_contracts(suppliers, carriers, quotes)
    shipments, lots_init, customs = _build_shipments_lots_customs(
        rng, months, routes, contracts, skus, fx_df, config
    )
    lots, sales, inv_monthly, writeoffs = _simulate_sales_inventory_writeoffs(
        rng, months, config, skus, lots_init, registry_events
    )
    credit = _build_credit(months, customs, shipments, sales)
    costs = _build_costs(months, config, sales, inv_monthly, writeoffs, credit)

    tables: dict[str, pd.DataFrame] = {
        "suppliers": suppliers,
        "carriers": carriers,
        "routes": routes,
        "skus": skus,
        "fx": fx_df,
        "registry_events": registry_events,
        "quotes": quotes,
        "contracts": contracts,
        "shipments": shipments,
        "lots": lots,
        "customs": customs,
        "sales": sales,
        "inventory_monthly": inv_monthly,
        "writeoffs": writeoffs,
        "credit": credit,
        "costs": costs,
    }
    _save_truth_tables(tables, truth_path)
    return tables
