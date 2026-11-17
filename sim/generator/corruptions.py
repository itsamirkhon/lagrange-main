"""Deterministic corruption operators and level planner for LAGRANGE Rx."""

from __future__ import annotations

from datetime import date
from typing import Any

import numpy as np

OPERATORS_CATALOG: dict[str, dict[str, Any]] = {
    "N1": {"id": "N1", "name": "Number format", "category": "format", "default_recoverable": True, "description": "Non-standard number separators or million/thousand shorthands"},
    "N2": {"id": "N2", "name": "Date format", "category": "format", "default_recoverable": True, "description": "Mixed DD.MM.YYYY, MM/DD/YYYY, abbreviated or verbal dates"},
    "C1": {"id": "C1", "name": "Currency omitted", "category": "currency", "default_recoverable": True, "description": "Monetary amount shown without currency code or symbol"},
    "C2": {"id": "C2", "name": "Wrong currency", "category": "currency", "default_recoverable": True, "description": "USD value placed under UZS label or column"},
    "C3": {"id": "C3", "name": "Wrong FX date", "category": "currency", "default_recoverable": True, "description": "Converted using contract date FX rate instead of customs GTD date"},
    "V1": {"id": "V1", "name": "VAT omitted or swapped", "category": "tax", "default_recoverable": True, "description": "VAT inclusion label missing or inverted"},
    "U1": {"id": "U1", "name": "Unit mismatch", "category": "unit", "default_recoverable": True, "description": "Packs confused with blisters or boxes"},
    "L1": {"id": "L1", "name": "Language and script mix", "category": "text", "default_recoverable": True, "description": "Uzbek Latin/Cyrillic mix, transliteration, or SKU name typo"},
    "X1": {"id": "X1", "name": "Cross-document contradiction", "category": "consistency", "default_recoverable": True, "description": "Conflicting MOQ or lead time between quote and contract"},
    "X2": {"id": "X2", "name": "Multiple versions", "category": "consistency", "default_recoverable": True, "description": "Two conflicting versions of the same contract"},
    "S1": {"id": "S1", "name": "Stale value", "category": "staleness", "default_recoverable": True, "description": "Pre-revision registry price or expired supplier quote"},
    "M1": {"id": "M1", "name": "Missing data", "category": "completeness", "default_recoverable": True, "description": "Missing sales months, blank cells, or omitted pages"},
    "D1x": {"id": "D1x", "name": "Duplicate document", "category": "duplication", "default_recoverable": True, "description": "Same warehouse waybill issued twice under distinct numbers"},
    "A1": {"id": "A1", "name": "Arithmetic mismatch", "category": "arithmetic", "default_recoverable": True, "description": "Row sum disagrees with stated document total"},
    "R1": {"id": "R1", "name": "Scan and photo degradation", "category": "visual", "default_recoverable": True, "description": "Tilt, perspective warp, blur, low contrast, shadow, JPEG noise"},
    "R2": {"id": "R2", "name": "Stamp over text", "category": "visual", "default_recoverable": True, "description": "Abstract synthetic seal overlapping numeric cells"},
    "R3": {"id": "R3", "name": "Handwritten correction", "category": "visual", "default_recoverable": True, "description": "Strikethrough number with handwritten replacement"},
    "R4": {"id": "R4", "name": "Messenger screenshot crop", "category": "visual", "default_recoverable": True, "description": "Chat quote screenshot with clipped right or bottom edge"},
    "J1": {"id": "J1", "name": "Junk file", "category": "noise", "default_recoverable": True, "description": "Irrelevant promo flyer, unrelated SKU catalog, or blank scan"},
}

ALL_OPERATORS: tuple[str, ...] = tuple(OPERATORS_CATALOG.keys())

_RU_MONTHS_GEN: tuple[str, ...] = (
    "января", "февраля", "марта", "апреля", "мая", "июня",
    "июля", "августа", "сентября", "октября", "ноября", "декабря",
)
_RU_MONTHS_SHORT: tuple[str, ...] = (
    "янв.", "фев.", "мар.", "апр.", "май", "июн.",
    "июл.", "авг.", "сен.", "окт.", "ноя.", "дек.",
)

_LANG_MIX_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("Kardiozan", "Кардиозан"),
    ("Kardiozan", "Kardiozann"),
    ("Toshkent", "Тошкент"),
    ("viloyati", "вилояти"),
    ("Farg'ona", "Фарғона"),
    ("Samarqand", "Самарқанд"),
    ("O'zbekiston", "Узбекистон"),
    ("упаковка", "upakovka"),
)

_OPERATOR_BLUEPRINTS: dict[str, dict[str, str]] = {
    "N1": {"doc_type": "D2", "table": "sales", "field": "revenue_ex_vat_uzs", "hint": "Normalize number separators in D2 regional XLSX against D1 1C CSV"},
    "N2": {"doc_type": "D6", "table": "shipments", "field": "dispatch_date", "hint": "Cross-check shipment dispatch date against D7 customs summary"},
    "C1": {"doc_type": "D3", "table": "quotes", "field": "unit_price", "hint": "Currency USD stated in D5 contract and D6 commercial invoice"},
    "C2": {"doc_type": "D7", "table": "customs", "field": "cip_uzs", "hint": "Convert USD CIP value using CBU rate on declaration date in D5/D6"},
    "C3": {"doc_type": "D7", "table": "customs", "field": "landed_cost_ex_vat_uzs", "hint": "Recompute UZS landed cost using CBU FX rate on GTD date"},
    "V1": {"doc_type": "D4", "table": "quotes", "field": "unit_price", "hint": "Verify VAT status (12%) against D5 contract terms"},
    "U1": {"doc_type": "D8", "table": "lots", "field": "initial_qty", "hint": "Convert blisters/boxes to packs using SKU pack_blisters=3, box_packs=10"},
    "L1": {"doc_type": "D2", "table": "sales", "field": "region_name", "hint": "Match mixed Latin/Cyrillic region and SKU spelling to registry"},
    "X1": {"doc_type": "D3", "table": "contracts", "field": "min_order_qty", "hint": "Signed D5 contract MOQ supersedes preliminary D3 supplier quote"},
    "X2": {"doc_type": "D5", "table": "contracts", "field": "signed_date", "hint": "Use latest dated D5 addendum v2 matching D6 invoice terms"},
    "S1": {"doc_type": "D13", "table": "registry_events", "field": "chchn_with_vat_uzs", "hint": "Use post-revision Order #3735 price effective 2025-11 in D13 CSV"},
    "M1": {"doc_type": "D2", "table": "sales", "field": "qty_packs", "hint": "Recover missing regional slice from D1 1C export and D9 stock delta"},
    "D1x": {"doc_type": "D8", "table": "shipments", "field": "shipment_id", "hint": "Deduplicate repeated waybill by matching lot_id and dispatch_date in D9"},
    "A1": {"doc_type": "D10", "table": "credit", "field": "monthly_interest_uzs", "hint": "Recompute column total from individual line items in D10"},
    "R1": {"doc_type": "D8", "table": "shipments", "field": "qty_packs", "hint": "Verify scanned waybill quantities against D9 warehouse lot ledger"},
    "R2": {"doc_type": "D8", "table": "lots", "field": "initial_qty", "hint": "Confirm stamped waybill lot quantity in D9 stock XLSX"},
    "R3": {"doc_type": "D11", "table": "costs", "field": "amount_uzs", "hint": "Reconcile handwritten memo adjustment with D10 payments registry"},
    "R4": {"doc_type": "D4", "table": "quotes", "field": "unit_price", "hint": "Verify clipped chat quote against D4 carrier rates XLSX"},
    "J1": {"doc_type": "J1", "table": "quotes", "field": "irrelevant_file", "hint": "Ignore unrelated promotional/blank file not referencing SKU_CARDIO_01"},
}


def corrupt_number(val: float | int, rng: np.random.Generator) -> str:
    """Format a number with realistic Central Asian formatting noise."""
    num = float(val)
    choice = int(rng.integers(0, 4))
    if choice == 0:
        return f"{num:,.2f}".replace(",", " ").replace(".", ",")
    if choice == 1:
        return f"{int(round(num)):,}".replace(",", ".")
    if choice == 2 and abs(num) >= 1_000_000:
        return f"{num / 1_000_000:.2f}".replace(".", ",") + " млн"
    if abs(num) >= 1_000:
        return f"{num / 1_000:g}".replace(".", ",") + "т"
    return f"{num:.2f}".replace(".", ",")


def _parse_iso_date(iso_date: str) -> tuple[int, int, int | None]:
    """Parse YYYY-MM-DD or YYYY-MM string into (year, month, day_or_none)."""
    parts = str(iso_date).strip().split("-")
    if len(parts) == 2:
        parsed = date.fromisoformat(f"{parts[0]}-{parts[1]}-01")
        return parsed.year, parsed.month, None
    if len(parts) == 3:
        parsed = date.fromisoformat(str(iso_date).strip())
        return parsed.year, parsed.month, parsed.day
    raise ValueError(f"Invalid ISO date: {iso_date!r}")


def corrupt_date(iso_date: str, rng: np.random.Generator) -> str:
    """Format an ISO date into ambiguous or localized Central Asian date strings."""
    year, month, day = _parse_iso_date(iso_date)
    if day is None:
        return f"{_RU_MONTHS_SHORT[month - 1]} {year % 100:02d}"
    choice = int(rng.integers(0, 4))
    if choice == 0:
        return f"{day:02d}.{month:02d}.{year}"
    if choice == 1:
        return f"{month:02d}/{day:02d}/{year}"
    if choice == 2:
        return f"{_RU_MONTHS_SHORT[month - 1]} {year % 100:02d}"
    return f"{day} {_RU_MONTHS_GEN[month - 1]} {year} г."


def corrupt_currency_omit(val: float | int, curr: str = "USD") -> str:
    """Render monetary amount without currency symbol or ISO code (C1)."""
    _ = curr  # Preserved for signature parity; value formatted without currency label.
    return f"{float(val):.2f}".replace(".", ",")


def corrupt_currency_wrong(val_usd: float, rng: np.random.Generator) -> dict[str, Any]:
    """Place a USD amount under a UZS currency label without conversion (C2)."""
    amt = round(float(val_usd), 2)
    suffix = "UZS" if int(rng.integers(0, 2)) == 0 else "сум"
    return {
        "amount": amt,
        "true_currency": "USD",
        "shown_currency": "UZS",
        "display": f"{amt:.2f} {suffix}",
    }


def corrupt_fx_wrong_date(
    amount_fx: float,
    true_rate: float,
    wrong_rate: float,
    wrong_date: str,
) -> dict[str, Any]:
    """Convert foreign amount using an FX rate from the wrong date (C3)."""
    if true_rate <= 0 or wrong_rate <= 0:
        raise ValueError("FX rates must be strictly positive")
    true_uzs = round(float(amount_fx) * float(true_rate), 2)
    shown_uzs = round(float(amount_fx) * float(wrong_rate), 2)
    return {
        "amount_fx": float(amount_fx),
        "true_rate": float(true_rate),
        "wrong_rate": float(wrong_rate),
        "wrong_date": str(wrong_date),
        "true_uzs": true_uzs,
        "shown_uzs": shown_uzs,
        "delta_uzs": round(shown_uzs - true_uzs, 2),
    }


def corrupt_vat_label(
    price: float,
    includes_vat: bool,
    rng: np.random.Generator,
    vat_rate: float = 0.12,
) -> dict[str, Any]:
    """Omit or invert VAT inclusion label on a price (V1)."""
    true_label = "с НДС" if includes_vat else "без НДС"
    mode = "omitted" if int(rng.integers(0, 2)) == 0 else "swapped"
    if mode == "omitted":
        shown_label = ""
        shown_price = round(float(price), 2)
    else:
        shown_label = "без НДС" if includes_vat else "с НДС"
        factor = (1.0 + vat_rate) if not includes_vat else (1.0 / (1.0 + vat_rate))
        shown_price = round(float(price) * factor, 2)
    return {
        "mode": mode,
        "true_label": true_label,
        "shown_label": shown_label,
        "true_price": round(float(price), 2),
        "shown_price": shown_price,
    }


def corrupt_unit(
    qty_packs: int | float,
    unit_price_pack: float,
    rng: np.random.Generator,
    pack_blisters: int = 3,
    box_packs: int = 10,
) -> dict[str, Any]:
    """Convert pack quantities/prices into blisters or boxes (U1)."""
    if int(rng.integers(0, 2)) == 0:
        return {
            "unit": "блистер",
            "factor": pack_blisters,
            "qty": round(float(qty_packs) * pack_blisters, 4),
            "unit_price": round(float(unit_price_pack) / pack_blisters, 4),
        }
    return {
        "unit": "коробка",
        "factor": box_packs,
        "qty": round(float(qty_packs) / box_packs, 4),
        "unit_price": round(float(unit_price_pack) * box_packs, 4),
    }


def corrupt_language_mix(text: str, rng: np.random.Generator) -> str:
    """Apply Uzbek Latin/Cyrillic mixing, transliteration, or typo (L1)."""
    mutated = str(text)
    candidates = [pair for pair in _LANG_MIX_REPLACEMENTS if pair[0] in mutated]
    if candidates:
        src, dst = candidates[int(rng.integers(0, len(candidates)))]
        mutated = mutated.replace(src, dst, 1)
    if mutated == text:
        pos = int(rng.integers(0, max(1, len(mutated) - 1)))
        mutated = f"{mutated[:pos + 1]}{mutated[pos]}{mutated[pos + 1:]}"
    return mutated


def corrupt_arithmetic(line_totals: list[float], rng: np.random.Generator) -> dict[str, Any]:
    """Produce a stated total that disagrees with the true sum of lines (A1)."""
    if not line_totals:
        raise ValueError("line_totals must not be empty")
    true_total = round(sum(float(x) for x in line_totals), 2)
    choice = int(rng.integers(0, 3))
    if choice == 0:
        shown_total = round(true_total * 0.92, 2)
    elif choice == 1:
        shown_total = round(true_total + max(100.0, round(true_total * 0.05, 2)), 2)
    else:
        shown_total = round(true_total - float(line_totals[-1]), 2) if len(line_totals) > 1 else round(true_total * 1.1, 2)
    return {
        "true_total": true_total,
        "shown_total": shown_total,
        "delta": round(shown_total - true_total, 2),
    }


def make_corruption_record(
    operator: str,
    field: str,
    page: int,
    bbox: list[int] | tuple[int, int, int, int],
    true_value: Any,
    shown_value: Any,
    recoverable: bool,
    recover_hint: str,
) -> dict[str, Any]:
    """Build a Section 7 manifest corruption record with schema validation."""
    if operator not in OPERATORS_CATALOG:
        raise ValueError(f"Unknown operator: {operator!r}")
    if len(bbox) != 4:
        raise ValueError(f"bbox must have 4 coordinates, got {bbox!r}")
    if int(page) < 1:
        raise ValueError(f"page must be >= 1, got {page!r}")
    if not str(recover_hint).strip():
        raise ValueError("recover_hint must be non-empty")
    return {
        "operator": operator,
        "field": str(field),
        "location": {"page": int(page), "bbox": [int(v) for v in bbox]},
        "true_value": true_value,
        "shown_value": shown_value,
        "recoverable": bool(recoverable),
        "recover_hint": str(recover_hint),
    }


def _extract_table_ids(truth_tables: dict[str, Any], table_name: str, id_cols: tuple[str, ...]) -> list[str]:
    """Extract string primary IDs from a truth DataFrame or list of dicts."""
    table = truth_tables.get(table_name)
    if table is None:
        return []
    rows = table.to_dict("records") if hasattr(table, "to_dict") else list(table)
    if not rows:
        return []
    key = next((col for col in id_cols if col in rows[0]), None)
    if key is None:
        return [f"{table_name}_{i:04d}" for i in range(len(rows))]
    return [str(row[key]) for row in rows]


def _collect_truth_refs(truth_tables: dict[str, Any]) -> dict[str, list[str]]:
    """Build table -> list['table/id'] lookup from truth tables."""
    id_candidates = ("quote_id", "contract_id", "shipment_id", "lot_id", "customs_id", "sale_id", "reg_id", "event_id", "credit_id", "cost_id")
    refs: dict[str, list[str]] = {}
    for table_name in ("quotes", "contracts", "shipments", "lots", "customs", "sales", "registry_events", "credit", "costs"):
        ids = _extract_table_ids(truth_tables, table_name, id_candidates)
        refs[table_name] = [f"{table_name}/{item_id}" for item_id in ids]
    return refs


def _build_operator_specs(
    active_ops: list[str],
    refs: dict[str, list[str]],
    intensity: float,
    rng: np.random.Generator,
) -> list[dict[str, Any]]:
    """Generate deterministic recoverable corruption specs for active operators."""
    reps = max(1, int(round(intensity * 6)))
    specs: list[dict[str, Any]] = []
    for op_id in active_ops:
        if op_id not in OPERATORS_CATALOG:
            raise ValueError(f"Unknown operator in level config: {op_id!r}")
        if op_id == "J1":
            continue
        bp = _OPERATOR_BLUEPRINTS[op_id]
        table_refs = refs.get(bp["table"]) or [f"{bp['table']}/row_0001"]
        for idx in range(reps):
            ref = table_refs[idx % len(table_refs)]
            specs.append({
                "operator": op_id,
                "doc_type": bp["doc_type"],
                "field": bp["field"],
                "truth_ref": ref,
                "recoverable": True,
                "recover_hint": bp["hint"],
                "variant_seed": int(rng.integers(0, 1_000_000)),
            })
    return specs


def _add_l4_unrecoverable_and_junk(
    items: list[dict[str, Any]],
    refs: dict[str, list[str]],
    active_ops: list[str],
    rng: np.random.Generator,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Append L4 unrecoverable omissions/degradations and J1 junk files."""
    sales_ref = (refs.get("sales") or ["sales/sale_0001"])[-1]
    lot_ref = (refs.get("lots") or ["lots/lot_0001"])[0]
    cost_ref = (refs.get("costs") or ["costs/cst_0001"])[0]

    unrecoverable_specs = [
        {
            "operator": "M1",
            "doc_type": "D2",
            "field": "monthly_sales_2026_08",
            "truth_ref": sales_ref,
            "recoverable": False,
            "recover_hint": "Missing 2026-08 sales export; request original regional report from client",
            "variant_seed": int(rng.integers(0, 1_000_000)),
        },
        {
            "operator": "R1",
            "doc_type": "D8",
            "field": "qty_packs",
            "truth_ref": lot_ref,
            "recoverable": False,
            "recover_hint": "Scan severely blurred/occluded; request original paper waybill from client warehouse",
            "variant_seed": int(rng.integers(0, 1_000_000)),
        },
        {
            "operator": "M1",
            "doc_type": "D11",
            "field": "memo_page_2",
            "truth_ref": cost_ref,
            "recoverable": False,
            "recover_hint": "Second page of handwritten memo missing; request original document from client",
            "variant_seed": int(rng.integers(0, 1_000_000)),
        },
    ]
    items.extend(unrecoverable_specs)

    junk_files: list[dict[str, Any]] = []
    if "J1" in active_ops:
        junk_specs = [
            ("J1_junk_promo_flyer.pdf", "promo_flyer", "Irrelevant promotional flyer; ignore during ingest"),
            ("J1_junk_unrelated_catalog.csv", "unrelated_sku_catalog", "Unrelated OTC catalog; ignore during ingest"),
            ("J1_junk_blank_scan.jpg", "blank_scan", "Blank/noisy scanner sheet; ignore during ingest"),
        ]
        for filename, kind, hint in junk_specs:
            junk_files.append({"filename": filename, "kind": kind, "operator": "J1"})
            items.append({
                "operator": "J1",
                "doc_type": "J1",
                "field": filename,
                "truth_ref": sales_ref,
                "recoverable": True,
                "recover_hint": hint,
                "variant_seed": int(rng.integers(0, 1_000_000)),
            })

    omitted_docs = [
        {"doc_type": "D2", "scope": "2026-08_fergana", "reason": "Missing monthly regional workbook", "recoverable": False},
    ]
    return omitted_docs, junk_files


def _summarize_plan(items: list[dict[str, Any]]) -> dict[str, Any]:
    """Group planned corruptions by doc_type and compute summary counts."""
    by_doc: dict[str, list[dict[str, Any]]] = {}
    by_op: dict[str, int] = {}
    for item in items:
        by_doc.setdefault(item["doc_type"], []).append(item)
        by_op[item["operator"]] = by_op.get(item["operator"], 0) + 1
    unrec = sum(1 for item in items if not item["recoverable"])
    return {
        "by_doc": by_doc,
        "summary": {
            "total_count": len(items),
            "recoverable_count": len(items) - unrec,
            "unrecoverable_count": unrec,
            "by_operator": by_op,
        },
    }


def plan_level_corruptions(
    level_name: str,
    level_cfg: dict[str, Any],
    truth_tables: dict[str, Any],
    rng: np.random.Generator,
) -> dict[str, Any]:
    """Create a deterministic corruption plan for one chaos level (L0-L4)."""
    active_ops = list(level_cfg.get("operators", []))
    intensity = float(level_cfg.get("target_intensity", 0.0))
    allow_unrec = bool(level_cfg.get("allow_unrecoverable", False)) and (level_name == "L4")

    if level_name == "L0" or not active_ops or intensity <= 0.0:
        return {
            "level": level_name,
            "target_intensity": 0.0,
            "allow_unrecoverable": False,
            "active_operators": [],
            "corruptions_by_doc": {},
            "planned_corruptions": [],
            "omitted_documents": [],
            "junk_files": [],
            "summary": {"total_count": 0, "recoverable_count": 0, "unrecoverable_count": 0, "by_operator": {}},
        }

    refs = _collect_truth_refs(truth_tables)
    items = _build_operator_specs(active_ops, refs, intensity, rng)
    omitted_docs: list[dict[str, Any]] = []
    junk_files: list[dict[str, Any]] = []

    if allow_unrec:
        omitted_docs, junk_files = _add_l4_unrecoverable_and_junk(items, refs, active_ops, rng)

    grouped = _summarize_plan(items)
    return {
        "level": level_name,
        "target_intensity": intensity,
        "allow_unrecoverable": allow_unrec,
        "active_operators": active_ops,
        "corruptions_by_doc": grouped["by_doc"],
        "planned_corruptions": items,
        "omitted_documents": omitted_docs,
        "junk_files": junk_files,
        "summary": grouped["summary"],
    }
