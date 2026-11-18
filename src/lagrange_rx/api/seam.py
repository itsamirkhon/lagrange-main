"""The single seam between the ontology graph (ingest/commit) and the engine's calculation input.

A graph committed through /ingest is an `ontology.ClientGraph`; the engine consumes the flat `CalculationGraph`
built by `engine.adapter.to_calculation_graph`. The engine-only parameters (`EngineConfig`) are either passed in by
the caller or derived from the graph by `engine.adapter.default_engine_config` (documented assumptions, each
substitution listed in `CalculationGraph.notes`, which the API surfaces as warnings).
"""
from __future__ import annotations

from pydantic import ValidationError

from lagrange_rx.engine.adapter import AdapterError, EngineConfig, default_engine_config, to_calculation_graph
from lagrange_rx.engine.inputs import CalculationGraph, EngineGraph
from lagrange_rx.ontology import ClientGraph as OntologyGraph


class GraphNotUsable(Exception):
    pass


def graph_for_engine(graph: OntologyGraph | EngineGraph, config: EngineConfig | None = None) -> EngineGraph | CalculationGraph:
    if isinstance(graph, EngineGraph):
        return graph
    try:
        return to_calculation_graph(graph, config or default_engine_config(graph))
    except (AdapterError, ValidationError, ValueError) as exc:
        raise GraphNotUsable(f'graph cannot be used by the engine: {exc}') from exc
