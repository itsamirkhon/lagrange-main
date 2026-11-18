"""LAGRANGE Rx synthetic world generator package."""

from __future__ import annotations

from typing import Any

from sim.generator.config_loader import (
    load_fx_table,
    load_levels_config,
    load_registry_sku,
    load_world_config,
)
from sim.generator.domain import (
    DOC_TYPES,
    VALID_OPERATORS,
    WATERMARK_TEXT,
    CorruptionRecord,
    DocumentArtifact,
    SheetSpec,
    generate_address,
    generate_carrier_name,
    generate_person_name,
    generate_supplier_name,
    generate_synthetic_inn,
)
from sim.generator.xlsx_io import read_xlsx, write_xlsx

__all__ = [
    "DOC_TYPES",
    "VALID_OPERATORS",
    "WATERMARK_TEXT",
    "CorruptionRecord",
    "DocumentArtifact",
    "SheetSpec",
    "evaluate_plan_on_truth",
    "generate_address",
    "generate_carrier_name",
    "generate_person_name",
    "generate_supplier_name",
    "generate_synthetic_inn",
    "get_baseline_plan",
    "load_fx_table",
    "load_levels_config",
    "load_registry_sku",
    "load_world_config",
    "read_xlsx",
    "write_xlsx",
]


def __getattr__(name: str) -> Any:
    if name in ("evaluate_plan_on_truth", "get_baseline_plan"):
        from sim.generator import evaluate

        return getattr(evaluate, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
