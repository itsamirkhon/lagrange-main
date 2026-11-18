"""Prevent missing, mismatched and unreviewed offers from entering MILP routes."""
from datetime import date

import pytest
from pydantic import BaseModel, ConfigDict
from lagrange_rx.engine.regulatory_filter import PreflightError, screen_routes
from tests.engine.test_regulatory_gate import good_packet

TODAY = date(2026, 11, 18)


class _M(BaseModel):
    model_config = ConfigDict(frozen=True)


class Product(_M):
    id: str = 'SKU-RX'
    chun_uzs: float = 130
    chchn_uzs: float = 150


class Route(_M):
    id: str = 'ROUTE-A'
    supplier_id: str = 'S1'
    is_eligible: bool = True


class Graph(_M):
    product: Product = Product()
    routes: tuple[Route, ...] = (Route(),)
    notes: tuple[str, ...] = ()


def test_valid_snapshot_route_can_be_screened_but_never_certified():
    graph, reports = screen_routes(Graph(), (good_packet(mode='operator_snapshot'),), TODAY)
    assert graph.routes[0].is_eligible is True
    assert reports[0]['decision'] == 'eligible'
    assert any('not a live government API' in n for n in graph.notes)


def test_no_packet_does_not_enter_optimizer():
    graph, reports = screen_routes(Graph(), (), TODAY)
    assert graph.routes[0].is_eligible is False
    assert reports[0]['decision'] == 'hold'


def test_synthetic_fake_is_held_in_real_preflight():
    graph, reports = screen_routes(Graph(), (good_packet(),), TODAY)
    assert graph.routes[0].is_eligible is False and reports[0]['decision'] == 'hold'


def test_price_snapshot_mismatch_holds_even_with_clean_packet():
    graph, reports = screen_routes(Graph(product=Product(chun_uzs=129)),
                                   (good_packet(mode='operator_snapshot'),), TODAY)
    assert graph.routes[0].is_eligible is False
    assert reports[0]['decision'] == 'hold'
    assert any(x['code'] == 'PRICE_SYNC' for x in reports[0]['findings'])


def test_original_ineligibility_cannot_be_reversed():
    graph, reports = screen_routes(Graph(routes=(Route(is_eligible=False),)),
                                   (good_packet(mode='operator_snapshot'),), TODAY)
    assert graph.routes[0].is_eligible is False
    assert reports[0]['decision'] == 'eligible'  # evidence passed, route independently unavailable


def test_inconsistent_packet_identity_rejected():
    p = good_packet(mode='operator_snapshot')
    for change in ({'route_id': 'OTHER'}, {'product_id': 'OTHER'}, {'supplier_id': 'OTHER'}):
        wrong = p.model_copy(update={'offer': p.offer.model_copy(update=change)})
        with pytest.raises(PreflightError):
            screen_routes(Graph(), (wrong,), TODAY)
    with pytest.raises(PreflightError, match='duplicate'):
        screen_routes(Graph(), (p, p), TODAY)
