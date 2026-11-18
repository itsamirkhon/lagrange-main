"""Interactive Web UI package for LAGRANGE Rx simulation inspection and evaluation."""

from __future__ import annotations

from sim.ui.api import (
    evaluate_scenario,
    get_document_preview,
    get_overview,
    get_truth_table,
    get_world_manifest,
    list_truth_tables,
    regenerate_world_action,
    resolve_document_path,
)

__all__ = [
    "evaluate_scenario",
    "get_document_preview",
    "get_overview",
    "get_truth_table",
    "get_world_manifest",
    "list_truth_tables",
    "regenerate_world_action",
    "resolve_document_path",
]
