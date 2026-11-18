"""Engine view of the single rule catalog (rules/uz_pharma_rules.yaml).

Every legal number is read from the catalog by rule_id; this module only reshapes them into the flat
`Rules` record the engine uses. There is no second catalog.
"""
from __future__ import annotations

import hashlib
import sys
from dataclasses import dataclass
from pathlib import Path

import yaml

_REPO_RULES = Path(__file__).resolve().parents[3] / "rules" / "uz_pharma_rules.yaml"
DEFAULT_RULES_PATH = _REPO_RULES if _REPO_RULES.is_file() else Path(sys.prefix) / "rules" / "uz_pharma_rules.yaml"

REQUIRED_RULES = (
    "UZ-PKM185-LEGAL-BASE",
    "UZ-INV-LEGAL-VS-ECONOMIC-BASE",
    "UZ-PF13-WHOLESALE-MARKUP",
    "UZ-3735-P19-RETAIL-CEILING",
    "UZ-3735-P19-CEILING-FORMULA",
    "UZ-VAT-NORMALIZATION",
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
    hedge_forward_premium: float
    rule_ids: tuple[str, ...]
    needs_legal_review: tuple[str, ...] = ()  # catalog status of the rules the engine applies


def load_rules(path: Path | str | None = None) -> Rules:
    p = Path(path) if path else DEFAULT_RULES_PATH
    raw = p.read_bytes()
    doc = yaml.safe_load(raw) or {}
    items = doc.get("rules")
    if not isinstance(items, list):
        raise RulesError("rules file must contain a `rules:` list")
    rules = {r["rule_id"]: r for r in items}
    missing = [r for r in REQUIRED_RULES if r not in rules]
    if missing:
        raise RulesError(f"rules file lacks required rule ids: {missing}")

    def par(rule: str, key: str):
        try:
            return rules[rule]["parameters"][key]
        except KeyError as exc:
            raise RulesError(f"missing param {rule}.{key}") from exc

    return Rules(
        version=str(doc["version"]),
        sha256=hashlib.sha256(raw).hexdigest(),
        max_markup=round(float(par("UZ-PF13-WHOLESALE-MARKUP", "multiplier")) - 1.0, 12),
        vat=float(par("UZ-VAT-NORMALIZATION", "vat_rate")),
        retail_markup=round(float(par("UZ-3735-P19-RETAIL-CEILING", "retail_multiplier")) - 1.0, 12),
        formula_tolerance=float(par("UZ-3735-P19-CEILING-FORMULA", "discrepancy_threshold")),
        formula_wholesale_markup=round(float(par("UZ-3735-P19-CEILING-FORMULA", "wholesale_multiplier")) - 1.0, 12),
        threshold_scheduled=float(par("UZ-3735-P20-INDEXATION", "scheduled_threshold")),
        threshold_immediate=float(par("UZ-3735-P20-INDEXATION", "immediate_threshold")),
        index_dates=tuple((int(m), 1) for m in par("UZ-3735-P20-INDEXATION", "scheduled_months")),
        lag_days=int(par("UZ-3735-P20-INDEXATION", "lag_days")),
        p_cut=float(par("UZ-3735-P22-REVISION", "p_cut")),
        depth_min=float(par("UZ-3735-P22-REVISION", "depth_min")),
        depth_max=float(par("UZ-3735-P22-REVISION", "depth_max")),
        hedge_forward_premium=float(par("UZ-INV-LEGAL-VS-ECONOMIC-BASE", "hedge_forward_premium")),
        rule_ids=tuple(sorted(rules)),
        needs_legal_review=tuple(sorted(r for r in REQUIRED_RULES if rules[r].get("status") == "needs_legal_review")),
    )
