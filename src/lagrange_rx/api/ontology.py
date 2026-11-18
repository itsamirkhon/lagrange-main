"""/ontology/* : rules catalog, committed graphs by id, graph validation."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException
from pydantic import ValidationError

from lagrange_rx.api.store import GraphStore
from lagrange_rx.ontology import ClientGraph
from lagrange_rx.ontology.rules import load_rules


def make_router(root: Path) -> APIRouter:
    router = APIRouter(prefix='/ontology')
    store = GraphStore(root)

    @router.get('/rules')
    async def rule_catalog():
        return load_rules().model_dump(mode='json')

    @router.get('/graphs')
    async def graphs():
        return {'graph_ids': store.ids()}

    @router.get('/graphs/{graph_id}')
    async def graph(graph_id: str):
        g = store.get(graph_id)
        if g is None:
            raise HTTPException(404, 'graph not found')
        return g.model_dump(mode='json', exclude_computed_fields=True)

    @router.post('/validate')
    async def validate(payload: dict[str, Any]):
        """Validate a ClientGraph payload; `ready_for_calculation` is False while unconfirmed data remains."""
        try:
            g = ClientGraph.model_validate(payload)
        except ValidationError as exc:
            return {'valid': False, 'ready_for_calculation': False,
                    'errors': [{'loc': list(e['loc']), 'msg': e['msg']} for e in exc.errors()]}
        try:
            g.for_calculation()
        except ValueError as exc:
            return {'valid': True, 'ready_for_calculation': False, 'errors': [{'loc': [], 'msg': str(exc)}]}
        return {'valid': True, 'ready_for_calculation': True, 'errors': []}

    return router
