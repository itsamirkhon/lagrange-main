"""Boundary between regulatory preflight and the existing numerical optimizer.

All routes start unapproved. Only operator-reviewed, fully eligible offers can
enter the *regulated* optimization endpoint; the legacy research endpoint stays
explicitly non-certified. This file does not mutate the caller's graph.
"""
from __future__ import annotations

from datetime import date
from typing import TYPE_CHECKING

from lagrange_rx.engine.regulatory_gate import Packet, Result, evaluate

if TYPE_CHECKING:
    from lagrange_rx.engine.inputs import EngineGraph


class PreflightError(ValueError):
    pass


def screen_routes(graph: 'EngineGraph', packets: tuple[Packet, ...], on: date):
    """Return (copy of graph, result per route), never elevating an ineligible route.

    Requires an exact one-to-one route/supplier/product identity. An omitted
    route is held, not assumed compliant. Multiple batches per route are not
    currently aggregated; duplicate route IDs are rejected for review.
    """
    known = {r.id: r for r in graph.routes}
    submitted: dict[str, Packet] = {}
    for packet in packets:
        offer = packet.offer
        if offer.route_id not in known:
            raise PreflightError(f'unknown route_id: {offer.route_id}')
        if offer.route_id in submitted:
            raise PreflightError(f'duplicate route_id: {offer.route_id}; aggregate approved batches separately')
        if offer.product_id != graph.product.id or offer.supplier_id != known[offer.route_id].supplier_id:
            raise PreflightError(f'offer identity does not match graph: {offer.route_id}')
        submitted[offer.route_id] = packet

    results: list[dict] = []
    safe_routes = []
    for route in graph.routes:
        packet = submitted.get(route.id)
        if packet is None:
            decision = 'hold'
            reason = 'No regulatory evidence packet submitted for this route'
            details = []
        else:
            report: Result = evaluate(packet, on, mode='operator_snapshot')
            decision = report.decision
            reason = 'Operator-reviewed snapshot; no government live verification' if decision == 'eligible' else 'Ineligible or unverified regulatory evidence'
            details = [f.model_dump(mode='json') for f in report.findings]
            # The financial optimizer's published wholesale ceiling must match
            # the one in the compliance snapshot, otherwise hold the route.
            if decision == 'eligible' and packet.medicine and packet.medicine.rx:
                expected = graph.product.chun_uzs if packet.offer.channel == 'wholesale' else graph.product.chchn_uzs
                observed = packet.reference_price.maximum_incl_vat if packet.reference_price else None
                if observed is None or abs(float(observed) - float(expected)) > max(0.01, abs(expected) * 1e-6):
                    decision = 'hold'
                    reason = 'Mismatch between engine and reviewed reference-price ceiling'
                    details.append({'code': 'PRICE_SYNC', 'status': 'hold', 'detail': reason})
        safe_routes.append(route.model_copy(update={'is_eligible': route.is_eligible and decision == 'eligible'}))
        results.append({'route_id': route.id, 'decision': decision, 'reason': reason, 'findings': details})

    copied = graph.model_copy(update={
        'routes': tuple(safe_routes),
        'notes': tuple(graph.notes) + ('regulated preflight uses operator-reviewed snapshots, not a live government API',),
    })
    return copied, results
