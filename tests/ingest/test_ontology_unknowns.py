"""Facts that documents never state may be left unknown (None); they are never guessed."""
from datetime import date
from decimal import Decimal

import pytest
from pydantic import ValidationError

from lagrange_rx.ontology import (
    CostItem, CustomsEntry, LeadTime, PaymentTerms, Product, Provenance, Quote, Route, Shipment,
)

P = Provenance(kind='user_provided', source='s')


def test_product_without_master_data_is_valid():
    p = Product(sku_id='a', trade_name='A 10 мг №30', strength='10 мг', pack_size=30, rx=True, provenance=P)
    assert p.inn is None and p.manufacturer is None and p.registration_certificate is None


def test_route_with_only_documented_rates():
    r = Route(route_id='r', carrier='c', transport_mode='road', customs_post='post',
              lead_time_days=LeadTime(p50=18), freight_per_shipment_usd=Decimal(1800),
              freight_per_pack_usd=Decimal('0.12'), provenance=P)
    assert r.production_site is None and r.is_eligible is None and r.freight_per_pack_uzs is None


def test_quote_of_a_carrier_without_product_or_dates():
    q = Quote(quote_id='KP', carrier='c', unit_price=Decimal('0.121'), currency='USD',
              lead_time_days=18, vat_status='excl', route_code='KZ-AUTO-01', provenance=P)
    assert q.product is None and q.issued_on is None


def test_shipment_and_customs_without_undocumented_amounts():
    s = Shipment(shipment_id='i', route='r', dispatch_date=date(2025, 1, 1), arrival_date=date(2025, 1, 2),
                 packs=1, customs_entry='e', provenance=P)
    c = CustomsEntry(entry_ref='e', entry_date=date(2025, 1, 2), cip_value_fx=Decimal(10), currency='USD',
                     fx_rate_on_entry=Decimal(2), duty_uzs=Decimal(1), fees_uzs=Decimal(1), packs=2, provenance=P)
    assert s.actual_freight_uzs is None and c.vat_uzs is None
    assert c.legal_base_c_per_pack == Decimal(11)


def test_payment_terms_customer_days_unknown():
    assert PaymentTerms(supplier_days=60, prepayment_share=0, provenance=P).customer_days is None


def test_cost_item_scope_only_for_shared_allocation():
    ok = CostItem(category='marketing', layer='below_line', amount_uzs=1, period='2025-01', driver='fixed',
                  allocation='shared_by_volume', scope=['a', 'b'], provenance=P)
    assert ok.scope == ['a', 'b']
    with pytest.raises(ValidationError):
        CostItem(category='marketing', layer='below_line', amount_uzs=1, period='2025-01', driver='fixed',
                 allocation='unallocated', scope=['a'], provenance=P)
