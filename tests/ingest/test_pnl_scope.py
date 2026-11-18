"""A shared cost with `scope` is split only among the SKUs in its scope."""
from decimal import Decimal

from lagrange_rx.pnl import calculate_pnl

from .test_cost_allocation import cost, graph_with


def test_shared_cost_outside_scope_is_irrelevant_to_the_sku():
    g = graph_with([cost('1000', allocation='shared_by_volume', scope=['other']),
                    cost('7.5', product='sku', allocation='direct')], other_sku_sales=30)
    result = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert not result.missing
    assert result.cost == Decimal('97.5')


def test_shared_cost_denominator_is_limited_to_scope():
    # scope covers sku (10 packs) and other (30 packs): sku carries 25% of 40 = 10
    g = graph_with([cost('40', allocation='shared_by_volume', scope=['sku', 'other'])], other_sku_sales=30)
    result = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert not result.missing
    assert result.cost == Decimal('100')


def test_out_of_scope_cost_does_not_satisfy_required_category():
    g = graph_with([cost('5', allocation='shared_by_volume', scope=['other'])], other_sku_sales=5)
    result = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert any('distribution' in item for item in result.missing)
