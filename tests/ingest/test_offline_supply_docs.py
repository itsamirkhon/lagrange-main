"""Deterministic parsers on the real L0 supply documents."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from lagrange_rx.ingest.offline import supply_docs as sd
from lagrange_rx.ingest.offline.readers import read_folder

L0 = Path(__file__).resolve().parents[2] / 'sim' / 'worlds' / 'L0' / 'documents'


@pytest.fixture(scope='module')
def by_class():
    out = {}
    for d in read_folder(L0):
        out.setdefault(d.doc_class, []).append(d)
    return out


def test_invoices_have_packing_list_rows_that_add_up(by_class):
    invoices = [sd.parse_invoice_page(p) for d in by_class['invoice'] for p in d.pages]
    assert len(invoices) == 44
    assert all(sum(l['qty'] for l in i['lots']) == i['total_packs'] for i in invoices)
    first = next(i for i in invoices if i['invoice'] == 'INV-2025-001')
    assert first['supplier'] == 'Medisynth S.A. (Индия, SYNTHETIC)' and first['contract'] == 'CNT-SUP-2025-01'
    assert first['dispatch'] == date(2025, 1, 2) and first['arrival'] == date(2025, 1, 20)
    assert first['lots'] == [{'lot': 'KZ-202501-01', 'product': 'Kardiozan 10 мг №30', 'qty': 16500,
                              'produced': date(2024, 10, 22), 'expiry': date(2026, 10, 22)}]
    assert sum(len(i['lots']) for i in invoices) == 50
    assert all(i['supplier'] and i['contract'] for i in invoices)


def test_customs_pdf_and_table_agree_on_the_same_entries(by_class):
    pdf = [r for d in by_class['customs'] if d.kind == 'pdf' for p in d.pages for r in sd.parse_customs_pdf(p)]
    table = [r for d in by_class['customs'] if d.kind == 'xlsx' for t in d.tables for r in sd.parse_customs_table(t)]
    assert len(pdf) == len(table) == 44
    by_gtd = {r['gtd']: r for r in table}
    for row in pdf:
        other = by_gtd[row['gtd']]
        assert other['invoice'] == row['invoice'] and other['rate'] == row['rate'] and other['landed_uzs'] == row['landed_uzs']
        assert abs(row['cip_fx'] * row['rate'] - other['cip_uzs']) < 1
    first = by_gtd['26004/2025-0001']
    assert first['post'] == 'Т/п Яллама (27012)' and first['duty_uzs'] == Decimal('31716333.79')


def test_stock_lots(by_class):
    lots = [r for d in by_class['stock'] for name, t in zip(d.sheets, d.tables) if name == 'Партии' for r in sd.parse_stock_lots(t)]
    assert len(lots) == 50
    first = next(l for l in lots if l['lot'] == 'KZ-202501-01')
    assert first['waybill'] == 'РН-2025-0001' and first['initial'] == 16500 and first['remaining'] == 0


def test_carrier_rates_and_quotes(by_class):
    rates = [r for d in by_class['carrier_quote'] if d.kind == 'xlsx' for r in sd.parse_rates(d.tables[0])]
    assert {r['code'] for r in rates} == {'KZ-AUTO-01', 'TM-AUTO-02', 'CHUK-RAIL-03', 'TAS-AIR-04'}
    quotes = [q for d in by_class['carrier_quote'] if d.kind == 'pdf' for q in sd.parse_carrier_quotes(d.pages[0])]
    assert len(quotes) == 8 and quotes[0]['rate_per_pack'] == Decimal('0.121') and quotes[0]['vat'] == 'excl'


def test_supplier_quotes(by_class):
    quotes = [sd.parse_supplier_quote(d.pages[0]) for d in by_class['supplier_quote']]
    assert len(quotes) == 8
    first = next(q for q in quotes if q['quote'] == 'KP-2025-001')
    assert first['supplier_code'] == 'ZBP-IND' and first['date'] == date(2025, 1, 5)
    assert first['items'][0]['price'] == Decimal('7.50') and first['items'][0]['currency'] == 'USD'
    assert first['items'][0]['product'] == 'Kardiozan 10 мг №'  # truncated in the document
    assert first['payment_terms'] == '60 дней отсрочка'


def test_contracts(by_class):
    pages = [sd.parse_contract_page(p) for d in by_class['contract'] for p in d.pages]
    by_no = {c['contract']: c for c in pages}
    assert {'CNT-SUP-2025-01', 'CNT-SUP-2025-01-A1', 'CNT-CAR-2025-04'} <= set(by_no)
    assert by_no['CNT-SUP-2025-01-A1']['price'] == Decimal('7.22')
    assert by_no['CNT-SUP-2025-01-A1']['signed'] == date(2025, 10, 20)


@pytest.mark.parametrize('text,expected', [
    ('60 дней отсрочка', (60, Decimal(0))),
    ('30% предоплата / 70% по прибытии', (0, Decimal('0.3'))),
    ('100% аккредитив', (0, Decimal(1))),
    ('как договоримся', None),
])
def test_payment_terms(text, expected):
    assert sd.parse_payment_terms(text) == expected
