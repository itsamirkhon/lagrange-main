"""Pure backend API functions for the LAGRANGE Rx Web UI."""

from __future__ import annotations

import copy
import csv
import email
import email.policy
import io
import json
import math
import re
from collections import Counter
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pandas as pd

from sim.generator.cli import run_generation
from sim.generator.config_loader import load_levels_config, load_world_config
from sim.generator.domain import DOC_TYPES, VALID_OPERATORS
from sim.generator.evaluate import evaluate_plan_on_truth, get_baseline_plan
from sim.generator.xlsx_io import read_xlsx

VALID_LEVELS: tuple[str, ...] = ("L0", "L1", "L2", "L3", "L4")
VALID_ROUTES: tuple[str, ...] = ("rte_01", "rte_02", "rte_03", "rte_04")
MAX_PREVIEW_ROWS = 200
MAX_TRUTH_LIMIT = 1000

TRUTH_TABLES: tuple[str, ...] = (
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

TRUTH_PK_MAP: dict[str, str] = {
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

PREVIEW_KIND_BY_EXT: dict[str, str] = {
    ".pdf": "pdf",
    ".png": "image",
    ".jpg": "image",
    ".jpeg": "image",
    ".xlsx": "xlsx",
    ".csv": "csv",
    ".eml": "eml",
}

_SAFE_NAME_RE = re.compile(r"^[A-Za-z0-9_]+$")


def _validate_level(level: str) -> str:
    if level not in VALID_LEVELS:
        raise ValueError(f"Unknown world level: {level!r}")
    return level


def _validate_filename(filename: str) -> str:
    if not filename or filename in (".", "..") or "/" in filename or "\\" in filename:
        raise ValueError(f"Invalid filename: {filename!r}")
    if Path(filename).name != filename:
        raise ValueError(f"Invalid filename: {filename!r}")
    return filename


def _read_json(path: Path) -> Any:
    if not path.is_file():
        raise FileNotFoundError(f"Missing JSON file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _sanitize_scalar(val: Any) -> Any:
    if hasattr(val, "item"):
        val = val.item()
    if val is None or isinstance(val, (bool, str, int)):
        return val
    if isinstance(val, float):
        return val if math.isfinite(val) else None
    return None if pd.isna(val) else str(val)


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    records = df.to_dict(orient="records")
    return [{str(k): _sanitize_scalar(v) for k, v in row.items()} for row in records]


def _load_truth_df(truth_dir: Path, table_name: str) -> pd.DataFrame:
    parquet_path = truth_dir / f"{table_name}.parquet"
    if parquet_path.is_file():
        return pd.read_parquet(parquet_path)
    json_path = truth_dir / f"{table_name}.json"
    if json_path.is_file():
        return pd.read_json(json_path)
    raise FileNotFoundError(f"Truth table not found: {table_name!r}")


def _summarize_level_manifest(
    sim_path: Path,
    level: str,
    level_cfg: dict[str, Any],
) -> dict[str, Any]:
    manifest = _read_json(sim_path / "worlds" / level / "manifest.json")
    docs = manifest.get("documents", [])
    op_counts: Counter[str] = Counter({op: 0 for op in VALID_OPERATORS})
    dtype_counts: Counter[str] = Counter({dt: 0 for dt in (*DOC_TYPES, "J1")})
    recoverable = 0
    unrecoverable = 0

    for doc in docs:
        dtype_counts[str(doc.get("doc_type", ""))] += 1
        for corr in doc.get("corruptions", []):
            op_counts[str(corr.get("operator", ""))] += 1
            if corr.get("recoverable"):
                recoverable += 1
            else:
                unrecoverable += 1

    return {
        "level": level,
        "name": level_cfg.get("name", level),
        "description": level_cfg.get("description", ""),
        "seed": manifest.get("seed"),
        "total_docs": len(docs),
        "total_corruptions": recoverable + unrecoverable,
        "recoverable_count": recoverable,
        "unrecoverable_count": unrecoverable,
        "omitted_documents_count": len(manifest.get("omitted_documents", [])),
        "operator_counts": dict(op_counts),
        "doc_type_counts": dict(dtype_counts),
    }


def get_overview(sim_dir: Path) -> dict[str, Any]:
    """Return high-level config, calibration metrics, and L0-L4 world summaries."""
    sim_path = Path(sim_dir)
    world_cfg = load_world_config(sim_path / "config" / "world.yaml")
    levels_cfg = load_levels_config(sim_path / "config" / "levels.yaml")
    calibration = _read_json(sim_path / "truth" / "calibration_report.json")
    worlds_summary = {
        lvl: _summarize_level_manifest(sim_path, lvl, levels_cfg[lvl])
        for lvl in VALID_LEVELS
    }
    seed = worlds_summary["L0"].get("seed") or world_cfg["seed"]
    return {
        "seed": int(seed),
        "company": world_cfg["company"],
        "period": world_cfg["period"],
        "focus_sku": world_cfg["focus_sku"],
        "calibration_targets": world_cfg["calibration_targets"],
        "calibration_report": calibration,
        "levels": levels_cfg,
        "worlds_summary": worlds_summary,
    }


def _enrich_doc_entry(docs_dir: Path, level: str, doc: dict[str, Any]) -> dict[str, Any]:
    filename = Path(str(doc.get("document", ""))).name
    file_path = docs_dir / filename
    ext = file_path.suffix.lower()
    size_bytes = file_path.stat().st_size if file_path.is_file() else 0
    return {
        **doc,
        "filename": filename,
        "size_bytes": int(size_bytes),
        "ext": ext,
        "preview_kind": PREVIEW_KIND_BY_EXT.get(ext, "unknown"),
        "download_url": f"/api/worlds/{level}/document/{filename}",
    }


def get_world_manifest(sim_dir: Path, level: str) -> dict[str, Any]:
    """Load and enrich a world's manifest.json with file sizes and preview URLs."""
    lvl = _validate_level(level)
    sim_path = Path(sim_dir)
    manifest = _read_json(sim_path / "worlds" / lvl / "manifest.json")
    docs_dir = sim_path / "worlds" / lvl / "documents"
    enriched = [_enrich_doc_entry(docs_dir, lvl, doc) for doc in manifest.get("documents", [])]
    return {**manifest, "documents": enriched}


def resolve_document_path(sim_dir: Path, level: str, filename: str) -> Path:
    """Resolve a world document safely, preventing path traversal."""
    lvl = _validate_level(level)
    safe_name = _validate_filename(filename)
    docs_dir = (Path(sim_dir) / "worlds" / lvl / "documents").resolve()
    candidate = (docs_dir / safe_name).resolve()
    if candidate.parent != docs_dir:
        raise ValueError(f"Path traversal rejected: {filename!r}")
    if not candidate.is_file():
        raise FileNotFoundError(f"Document not found: {lvl}/{safe_name}")
    return candidate


def _decode_csv_bytes(raw: bytes) -> tuple[str, str]:
    try:
        return raw.decode("utf-8-sig"), "utf-8"
    except UnicodeDecodeError:
        return raw.decode("cp1251", errors="replace"), "cp1251"


def _detect_delimiter(text: str) -> str:
    first_line = next((ln for ln in text.splitlines() if ln.strip()), "")
    if ";" in first_line and first_line.count(";") >= first_line.count(","):
        return ";"
    return ","


def _preview_csv(doc_path: Path) -> dict[str, Any]:
    text, encoding = _decode_csv_bytes(doc_path.read_bytes())
    delimiter = _detect_delimiter(text)
    all_rows = list(csv.reader(io.StringIO(text), delimiter=delimiter))
    headers = all_rows[0] if all_rows else []
    data_rows = all_rows[1:] if len(all_rows) > 1 else []
    return {
        "kind": "csv",
        "encoding": encoding,
        "delimiter": delimiter,
        "headers": headers,
        "rows": data_rows[:MAX_PREVIEW_ROWS],
        "total_rows": len(data_rows),
    }


def _preview_xlsx(doc_path: Path) -> dict[str, Any]:
    workbook = read_xlsx(doc_path)
    sheets: dict[str, Any] = {}
    for sheet_name, sheet_data in workbook.items():
        raw_rows = sheet_data.get("rows", [])
        sanitized_rows = [[_sanitize_scalar(c) for c in r] for r in raw_rows[:MAX_PREVIEW_ROWS]]
        sheets[sheet_name] = {
            "headers": [_sanitize_scalar(h) for h in sheet_data.get("headers", [])],
            "rows": sanitized_rows,
            "merged_cells": list(sheet_data.get("merged_cells", [])),
            "total_rows": len(raw_rows),
        }
    return {"kind": "xlsx", "sheets": sheets}


def _extract_eml_body(msg: EmailMessage) -> str:
    body_part = msg.get_body(preferencelist=("plain", "html"))
    if body_part is not None:
        return str(body_part.get_content())
    if not msg.is_multipart():
        return str(msg.get_content())
    return ""


def _extract_eml_attachments(msg: EmailMessage) -> list[dict[str, Any]]:
    attachments: list[dict[str, Any]] = []
    for part in msg.iter_attachments():
        payload = part.get_payload(decode=True) or b""
        attachments.append(
            {
                "filename": part.get_filename() or "attachment",
                "content_type": part.get_content_type(),
                "size_bytes": len(payload),
            }
        )
    return attachments


def _preview_eml(doc_path: Path) -> dict[str, Any]:
    msg = email.parser.BytesParser(policy=email.policy.default).parsebytes(doc_path.read_bytes())
    return {
        "kind": "eml",
        "subject": str(msg.get("Subject", "")),
        "from": str(msg.get("From", "")),
        "to": str(msg.get("To", "")),
        "date": str(msg.get("Date", "")),
        "body": _extract_eml_body(msg),
        "attachments": _extract_eml_attachments(msg),
    }


def _resolve_truth_refs(truth_dir: Path, refs: list[str]) -> list[dict[str, Any]]:
    grouped: dict[str, list[str]] = {}
    for ref in refs:
        if "/" not in ref:
            continue
        tbl, rid = ref.split("/", 1)
        grouped.setdefault(tbl, []).append(rid)

    items: list[dict[str, Any]] = []
    for tbl, rids in grouped.items():
        pk_col = TRUTH_PK_MAP.get(tbl)
        if not pk_col:
            continue
        df = _load_truth_df(truth_dir, tbl)
        sub = df[df[pk_col].astype(str).isin(set(rids))].head(25)
        for rec in _df_to_records(sub):
            rid_val = str(rec.get(pk_col, ""))
            items.append({"ref": f"{tbl}/{rid_val}", "table": tbl, "id": rid_val, "row": rec})
    return items


def get_document_preview(sim_dir: Path, level: str, filename: str) -> dict[str, Any]:
    """Return structured preview payload plus manifest entry and truth_refs for a document."""
    doc_path = resolve_document_path(sim_dir, level, filename)
    manifest = get_world_manifest(sim_dir, level)
    entry = next((d for d in manifest["documents"] if d["filename"] == doc_path.name), {})
    truth_preview = _resolve_truth_refs(Path(sim_dir) / "truth", entry.get("truth_refs", []))
    ext = doc_path.suffix.lower()

    if ext == ".xlsx":
        payload = _preview_xlsx(doc_path)
    elif ext == ".csv":
        payload = _preview_csv(doc_path)
    elif ext == ".eml":
        payload = _preview_eml(doc_path)
    else:
        kind = PREVIEW_KIND_BY_EXT.get(ext, "unknown")
        payload = {"kind": kind, "media_url": f"/api/worlds/{level}/document/{doc_path.name}"}

    return {**payload, "manifest_entry": entry, "truth_preview": truth_preview}


def list_truth_tables(sim_dir: Path) -> list[dict[str, Any]]:
    """List available ground-truth tables with row counts and column names."""
    truth_dir = Path(sim_dir) / "truth"
    if not truth_dir.is_dir():
        raise FileNotFoundError(f"Truth directory not found: {truth_dir}")

    discovered = {
        p.stem
        for p in truth_dir.iterdir()
        if p.is_file() and p.suffix in (".parquet", ".json") and p.stem != "calibration_report"
    }
    ordered = [t for t in TRUTH_TABLES if t in discovered] + sorted(discovered - set(TRUTH_TABLES))
    tables: list[dict[str, Any]] = []
    for name in ordered:
        df = _load_truth_df(truth_dir, name)
        tables.append({"name": name, "row_count": int(len(df)), "columns": [str(c) for c in df.columns]})
    return tables


def get_truth_table(
    sim_dir: Path,
    table_name: str,
    limit: int = 200,
    sku_id: str | None = None,
) -> dict[str, Any]:
    """Return rows and schema for a ground-truth table with optional sku_id filtering."""
    if not _SAFE_NAME_RE.match(table_name) or table_name == "calibration_report":
        raise ValueError(f"Invalid truth table name: {table_name!r}")

    df = _load_truth_df(Path(sim_dir) / "truth", table_name)
    if sku_id and "sku_id" in df.columns:
        df = df[df["sku_id"].astype(str) == sku_id]

    clamped_limit = max(1, min(int(limit), MAX_TRUTH_LIMIT))
    return {
        "table": table_name,
        "columns": [str(c) for c in df.columns],
        "total_rows": int(len(df)),
        "rows": _df_to_records(df.head(clamped_limit)),
    }


def _parse_positive_float(val: Any, name: str, allow_zero: bool = False) -> float:
    num = float(val)
    if math.isnan(num) or math.isinf(num):
        raise ValueError(f"{name} must be finite")
    if (allow_zero and num < 0.0) or (not allow_zero and num <= 0.0):
        raise ValueError(f"{name} must be {'non-negative' if allow_zero else 'positive'}")
    return num


def _build_scenario_plan(
    base_plan: dict[str, Any],
    payload: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if isinstance(payload.get("plan"), dict):
        plan = copy.deepcopy(payload["plan"])
        orders = plan.get("monthly_orders", [])
        summary = {
            "sku_id": plan.get("sku_id", base_plan["sku_id"]),
            "selling_price_ex_vat_uzs": float(plan.get("selling_price_ex_vat_uzs", base_plan["selling_price_ex_vat_uzs"])),
            "total_order_packs": sum(int(o.get("qty_packs", 0)) for o in orders),
            "total_marketing_uzs": round(sum(float(v) for v in plan.get("monthly_marketing_uzs", {}).values()), 2),
            "route_ids": sorted({str(o.get("route_id", "")) for o in orders}),
        }
        return plan, summary

    qty_mult = _parse_positive_float(payload.get("order_qty_multiplier", 1.0), "order_qty_multiplier")
    mkt_mult = _parse_positive_float(payload.get("marketing_multiplier", 1.0), "marketing_multiplier", allow_zero=True)
    price = _parse_positive_float(
        payload.get("selling_price_ex_vat_uzs", base_plan["selling_price_ex_vat_uzs"]),
        "selling_price_ex_vat_uzs",
    )
    route_id = payload.get("route_id")
    if route_id is not None and route_id not in VALID_ROUTES:
        raise ValueError(f"Invalid route_id: {route_id!r}")

    plan = copy.deepcopy(base_plan)
    plan["selling_price_ex_vat_uzs"] = round(price, 2)
    for order in plan["monthly_orders"]:
        order["qty_packs"] = max(0, int(round(order["qty_packs"] * qty_mult)))
        if route_id is not None:
            order["route_id"] = route_id
    plan["monthly_marketing_uzs"] = {
        m: round(float(v) * mkt_mult, 2)
        for m, v in plan["monthly_marketing_uzs"].items()
    }
    summary = {
        "sku_id": plan["sku_id"],
        "selling_price_ex_vat_uzs": plan["selling_price_ex_vat_uzs"],
        "order_qty_multiplier": qty_mult,
        "marketing_multiplier": mkt_mult,
        "route_id": route_id,
        "total_order_packs": sum(int(o["qty_packs"]) for o in plan["monthly_orders"]),
        "total_marketing_uzs": round(sum(plan["monthly_marketing_uzs"].values()), 2),
    }
    return plan, summary


def evaluate_scenario(sim_dir: Path, payload: dict[str, Any]) -> dict[str, Any]:
    """Evaluate a baseline or modified procurement/pricing scenario on ground truth."""
    if not isinstance(payload, dict):
        raise ValueError("Evaluation payload must be a JSON object")
    truth_dir = Path(sim_dir) / "truth"
    base_plan = get_baseline_plan(truth_dir)
    plan, summary = _build_scenario_plan(base_plan, payload)
    result = evaluate_plan_on_truth(plan, truth_dir)
    return {"plan_summary": summary, "metrics": result.to_dict()}


def regenerate_world_action(sim_dir: Path, seed: int) -> dict[str, Any]:
    """Regenerate truth tables and L0-L4 worlds deterministically for a new seed."""
    if isinstance(seed, bool) or not isinstance(seed, int) or seed < 0:
        raise ValueError(f"Seed must be a non-negative integer, got {seed!r}")
    sim_path = Path(sim_dir)
    run_generation(sim_dir=sim_path, seed=seed)
    return get_overview(sim_path)
