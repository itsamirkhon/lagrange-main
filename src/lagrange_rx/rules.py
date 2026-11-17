"""Loader for rules/uz_pharma_rules.yaml: the single source of legal numbers."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

import yaml

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "rules" / "uz_pharma_rules.yaml"

REQUIRED_RULES = (
    "UZ-PKM185-LEGAL-BASE",
    "UZ-INV-LEGAL-VS-ECONOMIC-BASE",
    "UZ-PF13-WHOLESALE-MARKUP",
    "UZ-3735-P19-CEILING",
    "UZ-3735-P20-INDEXATION",
    "UZ-3735-P22-REVISION",
    "UZ-INV-UNIFORM-WAREHOUSE-PRICE",
)


class RulesError(ValueError):
    pass


@dataclass(frozen=True)
class Rules:
    version: str
    sha256: str
    max_markup: float
    vat: float
    retail_markup: float
    formula_tolerance: float
    formula_wholesale_markup: float
    threshold_scheduled: float
    threshold_immediate: float
    index_dates: tuple[tuple[int, int], ...]
    lag_days: int
    p_cut: float
    depth_min: float
    depth_max: float
    rule_ids: tuple[str, ...]


def load_rules(path: Path | str | None = None) -> Rules:
    p = Path(path) if path else DEFAULT_RULES_PATH
    raw = p.read_bytes()
    doc = yaml.safe_load(raw)
    rules = doc.get("rules") or {}
    missing = [r for r in REQUIRED_RULES if r not in rules]
    if missing:
        raise RulesError(f"rules file lacks required rule ids: {missing}")

    def par(rule: str, key: str):
        try:
            return rules[rule]["params"][key]
        except KeyError as exc:
            raise RulesError(f"missing param {rule}.{key}") from exc

    return Rules(
        version=str(doc["version"]),
        sha256=hashlib.sha256(raw).hexdigest(),
        max_markup=float(par("UZ-PF13-WHOLESALE-MARKUP", "max_markup")),
        vat=float(par("UZ-3735-P19-CEILING", "vat")),
        retail_markup=float(par("UZ-3735-P19-CEILING", "retail_markup")),
        formula_tolerance=float(par("UZ-3735-P19-CEILING", "formula_tolerance")),
        formula_wholesale_markup=float(par("UZ-3735-P19-CEILING", "formula_wholesale_markup")),
        threshold_scheduled=float(par("UZ-3735-P20-INDEXATION", "threshold_scheduled")),
        threshold_immediate=float(par("UZ-3735-P20-INDEXATION", "threshold_immediate")),
        index_dates=tuple((int(m), int(d)) for m, d in par("UZ-3735-P20-INDEXATION", "index_dates")),
        lag_days=int(par("UZ-3735-P20-INDEXATION", "lag_days")),
        p_cut=float(par("UZ-3735-P22-REVISION", "p_cut")),
        depth_min=float(par("UZ-3735-P22-REVISION", "depth_min")),
        depth_max=float(par("UZ-3735-P22-REVISION", "depth_max")),
        rule_ids=tuple(sorted(rules)),
    )
