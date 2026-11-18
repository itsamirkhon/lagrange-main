"""The single seam between the ontology graph (ingest/commit) and the engine's input graph.

Today the engine still runs on its own minimal graph (engine._legacy_ontology.ClientGraph).
When the engine migrates to ontology.ClientGraph, `graph_for_engine` becomes the identity
and nothing else in api/ changes.
"""
from __future__ import annotations

from lagrange_rx.engine._legacy_ontology import ClientGraph as EngineGraph
from lagrange_rx.ontology import ClientGraph as OntologyGraph


class GraphNotUsable(Exception):
    pass


def graph_for_engine(graph: OntologyGraph | EngineGraph) -> EngineGraph:
    if isinstance(graph, EngineGraph):
        return graph
    raise GraphNotUsable(
        'graph committed by /ingest is an ontology.ClientGraph; the engine has not yet been migrated to it '
        '(no ontology -> engine adapter exists). The engine stage must provide it; only api/seam.py changes.')
