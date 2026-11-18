"""Engine-side input for the evaluation: the ontology graph produced by Ingest (eval/ingest_graph_L0.json) and the
single engine configuration of the product, `lagrange_rx.engine.adapter.default_engine_config`.

Stage 6d: there is no second configuration here any more (stage 5 used to enrich the graph and hand-write an
`EngineConfig`; that diverged from the documents-only path of stage 6c). Nothing in this module reads the simulation
truth: every engine parameter is derived by the adapter from the graph's own documents, and each substituted
default is listed in `CalculationGraph.notes` (copied into the reports).
"""
from __future__ import annotations

import json
from pathlib import Path

from lagrange_rx import ontology as onto
from lagrange_rx.engine.adapter import EngineConfig, default_engine_config

ROOT = Path(__file__).resolve().parents[1]
INGEST_GRAPH_PATH = ROOT / "eval" / "ingest_graph_L0.json"


def load_ingest_graph(path: Path = INGEST_GRAPH_PATH) -> tuple[onto.ClientGraph, str]:
    """Read an Ingest result. Derived fields that the ontology recomputes are dropped before validation."""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    raw = data["graph"]
    for r in raw["registry_records"]:
        r.pop("chun_formula_incl_vat", None)
    for c in raw["customs_entries"]:
        c.pop("legal_base_c_per_pack", None)
    return onto.ClientGraph.model_validate(raw), data["snapshot_id"]


def engine_config(graph: onto.ClientGraph) -> EngineConfig:
    """The one product path (documents-only client)."""
    return default_engine_config(graph)
