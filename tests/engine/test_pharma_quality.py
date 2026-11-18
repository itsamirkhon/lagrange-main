"""Pharmaceutical quality controls: unsafe stock/transport must not be optimized."""
from datetime import date, timedelta

import pytest
from pydantic import ValidationError

from lagrange_rx.engine.adapter import AdapterError, to_calculation_graph
from lagrange_rx.engine.pharma_quality import QualityBlocked, allocate_released_fefo
from lagrange_rx.fixtures.l0 import SKU, build_l0_config, build_l0_graph

G = build_l0_graph()
CFG = build_l0_config()


def _fefo_graph():
    p = next(p for p in G.products if p.sku_id == SKU)
    p = p.model_copy(update={"min_remaining_shelf_life_days": 30})
    base = date(2026, 11, 18)
    a, b, c, d = G.lots[:4]
    lots = [
        a.model_copy(update={"lot_number": "A", "product": SKU, "expiry_date": base + timedelta(days=70),
                             "remaining_packs": 40, "quality_status": "released"}),
        b.model_copy(update={"lot_number": "B", "product": SKU, "expiry_date": base + timedelta(days=50),
                             "remaining_packs": 20, "quality_status": "released"}),
        c.model_copy(update={"lot_number": "C", "product": SKU, "expiry_date": base + timedelta(days=20),
                             "remaining_packs": 100, "quality_status": "released"}),
        d.model_copy(update={"lot_number": "D", "product": SKU, "expiry_date": base + timedelta(days=100),
                             "remaining_packs": 100, "quality_status": "recalled"}),
    ]
    # Drop other references to the four replaced synthetic identifiers: FEFO only
    # needs product and release status, so retain original lots with new statuses
    # to keep referential integrity for shipment and sales history.
    by_id = {x.lot_number: x for x in G.lots}
    selected = G.lots[:4]
    for original, mutated in zip(selected, lots):
        by_id[original.lot_number] = mutated.model_copy(update={"lot_number": original.lot_number})
    return G.model_copy(update={"products": [p if x.sku_id == SKU else x for x in G.products],
                                "lots": [by_id[x.lot_number] for x in G.lots]}), tuple(x.lot_number for x in selected)


def test_fefo_released_only_and_nearest_eligible_expiry_first():
    graph, ids = _fefo_graph()
    got = allocate_released_fefo(graph, SKU, 50, date(2026, 11, 18))
    assert [x.lot_number for x in got.lines] == [ids[1], ids[0]]
    assert [x.packs for x in got.lines] == [20, 30]
    assert got.shortfall_packs == 0
    assert ids[2] in got.rejected_lots and ids[3] in got.rejected_lots


def test_fefo_expiry_and_recall_cannot_be_bypassed_by_demand():
    graph, ids = _fefo_graph()
    got = allocate_released_fefo(graph, SKU, 1000, date(2026, 11, 18))
    assert got.allocated_packs >= 60
    assert got.shortfall_packs > 0
    assert ids[2] not in [x.lot_number for x in got.lines]
    assert ids[3] not in [x.lot_number for x in got.lines]


def test_fefo_fails_when_minimum_shelf_life_unconfirmed():
    with pytest.raises(QualityBlocked, match="shelf life is unknown"):
        allocate_released_fefo(G, SKU, 20, date(2026, 11, 18))


def test_temperature_sensitive_product_excludes_unverified_routes():
    products = [p.model_copy(update={"storage_temperature_min_c": 2.0,
                                     "storage_temperature_max_c": 8.0}) if p.sku_id == SKU else p
                for p in G.products]
    graph = G.model_copy(update={"products": products})
    with pytest.raises(AdapterError, match="no eligible routes"):
        to_calculation_graph(graph, CFG)


def test_temperature_verified_route_can_enter_but_excursion_is_excluded():
    doc_id = G.source_documents[0].document_id
    products = [p.model_copy(update={"storage_temperature_min_c": 2.0,
                                     "storage_temperature_max_c": 8.0}) if p.sku_id == SKU else p
                for p in G.products]
    first = G.routes[0]
    allowed = first.model_copy(update={"temperature_record_status": "verified",
                                      "temperature_record_document": doc_id,
                                      "observed_min_c": 2.5, "observed_max_c": 7.8})
    routes = [allowed if r.route_id == first.route_id else r for r in G.routes]
    graph = G.model_copy(update={"products": products, "routes": routes})
    result = to_calculation_graph(graph, CFG)
    assert any(r.id == first.route_id for r in result.routes)
    bad = allowed.model_copy(update={"observed_max_c": 10.0})
    graph_bad = graph.model_copy(update={"routes": [bad if r.route_id == first.route_id else r for r in routes]})
    with pytest.raises(AdapterError, match="no eligible routes"):
        to_calculation_graph(graph_bad, CFG)


def test_temperature_range_is_consistent():
    with pytest.raises(ValidationError):
        G.products[0].model_copy(update={"storage_temperature_min_c": 8, "storage_temperature_max_c": 2})
