"""SKU attribution of expenses (CostItem.product / CostItem.allocation)."""
from decimal import Decimal

import pytest
from pydantic import ValidationError

from lagrange_rx.ontology import (
    Carrier, Channel, ClientGraph, CustomsEntry, LeadTime, Lot, Manufacturer, PaymentTerms,
    Product, ProductionSite, Provenance, Region, RegistrationCertificate, Route, SalesRecord,
    Shipment, Supplier,
)
from lagrange_rx.ontology.finance import CostItem
from lagrange_rx.pnl import calculate_pnl

P = Provenance(kind='user_provided', source='source')


def cost(amount, **kw):
    return CostItem(category='distribution', layer='below_line', amount_uzs=Decimal(amount),
                    period='2026-05', driver='fixed', provenance=P, **kw)


def graph_with(costs, other_sku_sales=0):
    """sku: 10 packs × 10 UZS revenue, legal base 9 UZS/pack (cost basis 90).

    Optionally a second SKU `other` sold `other_sku_sales` packs at 30 UZS without lots
    (it is only a denominator for shared allocations).
    """
    terms = PaymentTerms(supplier_days=0, customer_days=0, prepayment_share=0, provenance=P)
    products = [Product(sku_id='sku', trade_name='Drug', inn='x', dosage_form='tablet', strength='10mg',
                        pack_size=30, rx=True, manufacturer='m', registration_certificate='c', provenance=P)]
    sales = [SalesRecord(period='2026-05', product='sku', region='r', channel='distributors',
                         qty_packs=10, price_ex_vat=10, lot='l', provenance=P)]
    if other_sku_sales:
        products.append(Product(sku_id='other', trade_name='Other', inn='y', dosage_form='tablet',
                                strength='5mg', pack_size=30, rx=True, manufacturer='m',
                                registration_certificate='c', provenance=P))
        sales.append(SalesRecord(period='2026-05', product='other', region='r', channel='distributors',
                                 qty_packs=other_sku_sales, price_ex_vat=30, provenance=P))
    return ClientGraph(
        manufacturers=[Manufacturer(name='m', country='UZ', provenance=P)],
        registration_certificates=[RegistrationCertificate(number='c', valid_from='2025-01-01',
                                                           valid_to='2027-01-01', holder='h', provenance=P)],
        products=products,
        regions=[Region(region_id='r', name='r', provenance=P)],
        channels=[Channel(name='distributors', provenance=P)],
        suppliers=[Supplier(supplier_id='s', name='s', country='UZ', payment_terms=terms, provenance=P)],
        production_sites=[ProductionSite(site_id='p', supplier='s', country='UZ', approved_for=['sku'], provenance=P)],
        carriers=[Carrier(carrier_id='c', name='c', modes=['road'], provenance=P)],
        routes=[Route(route_id='r', production_site='p', carrier='c', transport_mode='road', incoterms='CIP',
                      customs_post='x', lead_time_days=LeadTime(p50=1, p90=1), freight_per_shipment_uzs=0,
                      freight_per_pack_uzs=0, min_shipment_packs=1, max_shipment_packs=100,
                      max_shipments_per_year=1, capacity_packs_per_year=100, activation_cost_uzs=0,
                      is_eligible=True, provenance=P)],
        customs_entries=[CustomsEntry(entry_ref='e', entry_date='2026-05-01', cip_value_fx=90, currency='UZS',
                                      fx_rate_on_entry=1, duty_uzs=0, fees_uzs=0, vat_uzs=12, packs=10,
                                      provenance=P)],
        shipments=[Shipment(shipment_id='s', route='r', dispatch_date='2026-05-01', arrival_date='2026-05-01',
                            packs=10, actual_freight_uzs=0, lots=['l'], customs_entry='e', provenance=P)],
        lots=[Lot(lot_number='l', product='sku', production_date='2026-01-01', expiry_date='2027-01-01',
                  qty_packs=10, shipment='s', remaining_packs=0, provenance=P)],
        sales_records=sales,
        cost_items=costs,
    )


def test_direct_allocation_requires_product_and_vice_versa():
    with pytest.raises(ValidationError, match='requires product'):
        cost(1, allocation='direct')
    with pytest.raises(ValidationError, match='direct allocation'):
        cost(1, product='sku', allocation='shared_by_volume')


def test_cost_product_must_exist_in_graph():
    with pytest.raises(ValueError, match='products'):
        graph_with([cost('7.5', product='missing', allocation='direct')]).for_calculation()


def test_direct_costs_of_this_sku_count_and_other_skus_are_ignored():
    g = graph_with([cost('7.5', product='sku', allocation='direct'),
                    cost('1000', product='other', allocation='direct')], other_sku_sales=5)
    result = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert not result.missing
    assert result.cost == Decimal('97.5')
    assert result.margin == Decimal('0.025')


def test_shared_cost_split_by_volume_across_all_skus_in_period():
    # sku 10 packs, other 30 packs -> sku carries 25% of a shared 30 UZS cost = 7.5
    g = graph_with([cost('30', allocation='shared_by_volume')], other_sku_sales=30)
    result = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert not result.missing
    assert result.cost == Decimal('97.5')


def test_shared_cost_split_by_revenue_across_all_skus_in_period():
    # revenue: sku 100, other 10 × 30 = 300 -> sku carries 25% of 30 = 7.5
    g = graph_with([cost('30', allocation='shared_by_revenue')], other_sku_sales=10)
    result = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert not result.missing
    assert result.cost == Decimal('97.5')


def test_unallocated_cost_blocks_margin_unless_attested():
    g = graph_with([cost('7.5')], other_sku_sales=5)
    blocked = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert blocked.margin is None
    assert any('распределен' in item for item in blocked.missing)
    attested = calculate_pnl(graph_with([cost('7.5')]), 'sku', '2026-05', {'distribution'},
                             expenses_attributed_to_product=True)
    assert attested.margin == Decimal('0.025')


def test_required_category_counted_only_from_relevant_costs():
    # A distribution cost of another SKU does not satisfy this SKU's requirement.
    g = graph_with([cost('5', product='other', allocation='direct')], other_sku_sales=5)
    result = calculate_pnl(g, 'sku', '2026-05', {'distribution'})
    assert any('distribution' in item for item in result.missing)
