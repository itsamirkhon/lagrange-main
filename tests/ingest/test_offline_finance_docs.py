"""D10 expenses (English articles in a Russian table), D13 registry and 1C sales rows."""
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from lagrange_rx.ingest.offline import finance_docs as fd
from lagrange_rx.ingest.offline import sales
from lagrange_rx.ingest.offline.readers import read_folder

L0 = Path(__file__).resolve().parents[2] / 'sim' / 'worlds' / 'L0' / 'documents'


@pytest.fixture(scope='module')
def by_name():
    return {d.name: d for d in read_folder(L0)}


def test_expense_rows_map_english_articles_to_categories(by_name):
    d = by_name['D10_payments_credit_2025.xlsx']
    rows = fd.parse_expenses(d.tables[d.sheets.index('Расходы')])
    assert len(rows) == 144
    first = rows[0]
    assert (first['doc'], first['period'], first['label'], first['article'], first['category']) == \
        ('АВР-2025-001', '2025-01', 'Kardiozan 10 мг №30', 'distribution', 'distribution')
    assert first['amount'] == Decimal('24640000.0')
    assert {r['article']: r['category'] for r in rows} == {
        'distribution': 'distribution', 'marketing': 'marketing', 'warehouse': 'warehousing',
        'financing': 'financing', 'writeoffs': 'write_off', 'general': 'g_and_a'}
    assert {r['label'] for r in rows} == {'Kardiozan 10 мг №30', 'Общий портфель препаратов'}


def test_unknown_article_has_no_category():
    rows = [['Акт/Док.', 'Месяц', 'Номенклатура', 'Статья', 'Сумма UZS'], ['A-1', '2025-01', 'X', 'bribes', 5]]
    assert fd.parse_expenses(rows)[0]['category'] is None


def test_registry_rows(by_name):
    rows = fd.parse_registry(by_name['D13_registry_extract_2026.csv'].tables[0])
    assert [r['number'] for r in rows] == ['RE-3735-2025-01', 'RE-3735-2025-07', 'RE-3735-2025-11']
    last = rows[-1]
    assert last['effective_from'] == date(2025, 11, 1) and last['rn'] == Decimal('93317.0')
    assert last['chun'] == Decimal('125000.0') and last['chchn'] == Decimal('150000.0')


def test_1c_sales_rows_have_unit_price(by_name):
    rows = sales.parse_1c(by_name['D1_1c_sales_2025.csv'].tables[0])
    assert len(rows) == 1992
    first = rows[0]
    assert first['doc'] == 'РД-202501-00001' and first['qty'] == 1582 and first['lot'] == 'KZ-202501-01'
    assert first['price'] == Decimal('152000.0') and first['amount'] == Decimal('240464000.0')
    assert first['region'] == 'г. Ташкент' and first['channel'] == 'Дистрибьюторы'


def test_regional_summary_in_uzbek_latin(by_name):
    d = by_name['D2_sales_tashkent_region_syrdarya.xlsx']
    rows = sales.parse_regional(d.tables[0], d.sheets[0])
    assert rows[0]['qty'] == 580 and rows[0]['lot'] == 'KZ-202501-01' and rows[0]['amount'] == Decimal('88160000.0')
