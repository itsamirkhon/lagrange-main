from decimal import Decimal

import pytest

from lagrange_rx.ontology import ClientGraph, Provenance
from lagrange_rx.ontology.finance import CostItem
from lagrange_rx.pnl import calculate_pnl

P = Provenance(kind='user_provided', source='source')


def test_empty_graph_reports_missing_documents_instead_of_margin():
    result = calculate_pnl(ClientGraph(), 'sku', '2026-05', {'distribution'})
    assert result.margin is None
    assert any('продаж' in item for item in result.missing)
    assert any('distribution' in item for item in result.missing)


def test_unconfirmed_provenance_is_never_used():
    item = CostItem(category='distribution', layer='below_line', amount_uzs=100,
                    period='2026-05', driver='fixed',
                    provenance=Provenance(kind='extracted_unconfirmed', source='source'))
    with pytest.raises(ValueError, match='unconfirmed'):
        calculate_pnl(ClientGraph(cost_items=[item]), 'sku', '2026-05', {'distribution'})


def test_missing_lot_and_non_fixed_cost_are_not_guessed():
    # The graph is complete enough to validate references, but does not identify
    # the cost basis for this sale or the units of this expense driver.
    from lagrange_rx.ontology import Manufacturer, RegistrationCertificate, Product, Region, Channel, SalesRecord
    graph = ClientGraph(
        manufacturers=[Manufacturer(name='m', country='UZ', provenance=P)],
        registration_certificates=[RegistrationCertificate(number='c', valid_from='2025-01-01', valid_to='2027-01-01', holder='h', provenance=P)],
        products=[Product(sku_id='sku', trade_name='Drug', inn='x', dosage_form='tablet', strength='10mg', pack_size=30, rx=True, manufacturer='m', registration_certificate='c', provenance=P)],
        regions=[Region(region_id='r', name='r', provenance=P)], channels=[Channel(name='distributors', provenance=P)],
        sales_records=[SalesRecord(period='2026-05', product='sku', region='r', channel='distributors', qty_packs=10, price_ex_vat=Decimal('100'), provenance=P)],
        cost_items=[CostItem(category='distribution', layer='below_line', amount_uzs=1, period='2026-05', driver='per_pack', provenance=P)],
    )
    result = calculate_pnl(graph, 'sku', '2026-05', {'distribution'})
    assert result.margin is None
    assert any('парт' in x for x in result.missing)
    assert any('driver' in x for x in result.missing)


def test_calculates_margin_from_confirmed_sales_lot_customs_and_expenses():
    from lagrange_rx.ontology import (Manufacturer, RegistrationCertificate, Product, Region, Channel, SalesRecord,
        Supplier, PaymentTerms, ProductionSite, Carrier, Route, LeadTime, Shipment, Lot, CustomsEntry)
    terms = PaymentTerms(supplier_days=0, customer_days=0, prepayment_share=0, provenance=P)
    graph = ClientGraph(
        manufacturers=[Manufacturer(name='m', country='UZ', provenance=P)],
        registration_certificates=[RegistrationCertificate(number='c', valid_from='2025-01-01', valid_to='2027-01-01', holder='h', provenance=P)],
        products=[Product(sku_id='sku', trade_name='Drug', inn='x', dosage_form='tablet', strength='10mg', pack_size=30, rx=True, manufacturer='m', registration_certificate='c', provenance=P)],
        regions=[Region(region_id='r', name='r', provenance=P)], channels=[Channel(name='distributors', provenance=P)],
        suppliers=[Supplier(supplier_id='s', name='s', country='UZ', payment_terms=terms, provenance=P)],
        production_sites=[ProductionSite(site_id='p', supplier='s', country='UZ', approved_for=['sku'], provenance=P)],
        carriers=[Carrier(carrier_id='c', name='c', modes=['road'], provenance=P)],
        routes=[Route(route_id='r', production_site='p', carrier='c', transport_mode='road', incoterms='CIP', customs_post='x', lead_time_days=LeadTime(p50=1, p90=1), freight_per_shipment_uzs=0, freight_per_pack_uzs=0, min_shipment_packs=1, max_shipment_packs=100, max_shipments_per_year=1, capacity_packs_per_year=100, activation_cost_uzs=0, is_eligible=True, provenance=P)],
        customs_entries=[CustomsEntry(entry_ref='e', entry_date='2026-05-01', cip_value_fx=90, currency='UZS', fx_rate_on_entry=1, duty_uzs=0, fees_uzs=0, vat_uzs=12, packs=10, provenance=P)],
        shipments=[Shipment(shipment_id='s', route='r', dispatch_date='2026-05-01', arrival_date='2026-05-01', packs=10, actual_freight_uzs=0, lots=['l'], customs_entry='e', provenance=P)],
        lots=[Lot(lot_number='l', product='sku', production_date='2026-01-01', expiry_date='2027-01-01', qty_packs=10, shipment='s', remaining_packs=0, provenance=P)],
        sales_records=[SalesRecord(period='2026-05', product='sku', region='r', channel='distributors', qty_packs=10, price_ex_vat=10, lot='l', provenance=P)],
        cost_items=[CostItem(category='distribution', layer='below_line', amount_uzs=Decimal('7.5'), period='2026-05', driver='fixed', provenance=P)],
    )
    result = calculate_pnl(graph, 'sku', '2026-05', {'distribution'}, expenses_attributed_to_product=True)
    assert not result.missing
    assert result.revenue == 100
    assert result.cost == Decimal('97.5')
    assert result.margin == Decimal('0.025')


def test_cost_allocation_must_be_explicitly_attested():
    cost = CostItem(category='distribution', layer='below_line', amount_uzs=100,
                    period='2026-05', driver='fixed', provenance=P)
    result = calculate_pnl(ClientGraph(cost_items=[cost]), 'sku', '2026-05', {'distribution'})
    assert any('распределен' in item for item in result.missing)
