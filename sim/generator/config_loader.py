"""Configuration and input data loaders with boundary validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd
import yaml

from sim.generator.domain import VALID_OPERATORS

_WORLD_REQUIRED_KEYS = (
    "seed",
    "company",
    "period",
    "focus_sku",
    "background_skus",
    "calibration_targets",
    "regions",
    "channels",
)
_FX_REQUIRED_COLS = {"date", "currency", "rate_uzs", "source"}
_REGISTRY_REQUIRED_KEYS = (
    "sku_id",
    "rn_uzs",
    "b_uzs",
    "bx_uzs",
    "calc_base_ex_vat_uzs",
    "wholesale_ex_vat_uzs",
    "chun_with_vat_uzs",
    "chchn_with_vat_uzs",
)


def _require_keys(data: dict[str, Any], keys: tuple[str, ...], ctx: str) -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise ValueError(f"Missing keys in {ctx}: {missing}")


def _validate_unit_sum(values: list[float], label: str) -> None:
    total = sum(float(v) for v in values)
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"{label} weights must sum to 1.0, got {total:.6f}")


def _validate_order_3735_base(data: dict[str, Any]) -> None:
    rn = float(data["rn_uzs"])
    if "bx_share" in data:
        expected_bx = round(float(data["bx_share"]) * rn, 2)
        if abs(float(data["bx_uzs"]) - expected_bx) > 0.05:
            raise ValueError(f"bx_uzs ({data['bx_uzs']}) != bx_share * rn_uzs ({expected_bx})")
    expected_base = round(rn + float(data["b_uzs"]) + float(data["bx_uzs"]), 2)
    if abs(float(data["calc_base_ex_vat_uzs"]) - expected_base) > 0.05:
        raise ValueError(
            f"calc_base_ex_vat_uzs ({data['calc_base_ex_vat_uzs']}) != rn+b+bx ({expected_base})"
        )


def load_world_config(path: Path | str) -> dict[str, Any]:
    """Load and validate sim/config/world.yaml."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("world.yaml must parse to a mapping")
    _require_keys(raw, _WORLD_REQUIRED_KEYS, "world.yaml")
    if len(raw["regions"]) != 14:
        raise ValueError(f"Expected 14 Uzbekistan regions, got {len(raw['regions'])}")
    _validate_unit_sum([r["demand_weight"] for r in raw["regions"]], "regions demand_weight")
    _validate_unit_sum(list(raw["channels"].values()), "channels")
    _validate_order_3735_base(raw["calibration_targets"])
    return raw


def _validate_single_level(name: str, cfg: dict[str, Any]) -> None:
    _require_keys(cfg, ("name", "intensity_range", "target_intensity", "allow_unrecoverable", "operators"), name)
    unknown = [op for op in cfg["operators"] if op not in VALID_OPERATORS]
    if unknown:
        raise ValueError(f"Unknown operator(s) in {name}: {unknown}")
    if name != "L4" and cfg["allow_unrecoverable"]:
        raise ValueError(f"allow_unrecoverable must be false on {name}")


def load_levels_config(path: Path | str) -> dict[str, Any]:
    """Load and validate sim/config/levels.yaml, returning the levels mapping."""
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, dict) or "levels" not in raw:
        raise ValueError("levels.yaml must contain top-level 'levels' mapping")
    levels = raw["levels"]
    for lvl in ("L0", "L1", "L2", "L3", "L4"):
        if lvl not in levels:
            raise ValueError(f"Missing level {lvl} in levels.yaml")
        _validate_single_level(lvl, levels[lvl])
    return levels


def load_fx_table(path: Path | str) -> pd.DataFrame:
    """Load and validate CBU Uzbekistan FX rates CSV."""
    df = pd.read_csv(Path(path))
    if not _FX_REQUIRED_COLS.issubset(df.columns):
        raise ValueError(f"FX table missing columns: {_FX_REQUIRED_COLS - set(df.columns)}")
    if df.empty or (df["rate_uzs"] <= 0).any():
        raise ValueError("FX table must be non-empty with positive rates")
    if pd.to_datetime(df["date"], format="%Y-%m-%d", errors="coerce").isna().any():
        raise ValueError("FX table contains invalid ISO date values")
    if not {"USD", "EUR", "RUB"}.issubset(set(df["currency"].unique())):
        raise ValueError("FX table must contain USD, EUR, and RUB")
    return df


def load_registry_sku(path: Path | str) -> dict[str, Any]:
    """Load and validate Order #3735 registry SKU record JSON."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    _require_keys(data, _REGISTRY_REQUIRED_KEYS, "registry_sku.json")
    _validate_order_3735_base(data)
    return data
