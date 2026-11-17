from lagrange_rx.ingest.parse import Table
from lagrange_rx.ingest.tables import extract_table


def test_finds_header_and_preserves_rows_and_evidence():
    table = Table(1, [['Отчёт регионального менеджера'], ['Компания', 'Компания А'],
                      ['Месяц', 'Регион', 'Канал', 'SKU', 'Упаковок', 'Выручка без НДС'],
                      ['2025-01', 'Джизакская область', 'distributors', 'Product A', '172', '26144000']])
    result = extract_table(table, 'doc')
    assert result['header_row'] == 2
    assert len(result['rows']) == 1
    fields = result['rows'][0]['fields']
    assert fields['SalesRecord.qty_packs']['raw'] == '172'
    assert fields['SalesRecord.region']['raw'] == 'Джизакская область'
    assert fields['SalesRecord.revenue_ex_vat_uzs']['evidence'] == {'document_id': 'doc', 'page': 1, 'quote': '26144000'}


def test_unknown_and_duplicate_columns_are_not_guessed():
    table = Table(1, [['Period', 'Qty Packs', 'qty_packs', 'mystery'], ['', '5', '9', 'X']])
    result = extract_table(table, 'doc')
    assert result['rows'] == []
    assert [c['header'] for c in result['unmapped_columns']] == ['Qty Packs', 'qty_packs', 'mystery']


def test_uzbek_and_mixed_case_mapping():
    table = Table(2, [['Oy', 'Hudud / Ҳудуд', 'SKU', 'Kanal', 'Sotuv (up.)', "Summa (so'm)"],
                      ['2025-01', 'Andijon', 'A', 'retail', '489', '74328000']])
    result = extract_table(table, 'doc')
    assert result['rows'][0]['fields']['SalesRecord.qty_packs']['raw'] == '489'
    assert result['rows'][0]['fields']['SalesRecord.region']['raw'] == 'Andijon'
    assert result['rows'][0]['fields']['SalesRecord.revenue_ex_vat_uzs']['raw'] == '74328000'


def test_no_header_does_not_invent_data():
    assert extract_table(Table(1, [['hello', 'world'], ['a', 'b']]), 'doc')['rows'] == []


def test_real_sales_headings_extract_lot_and_product_for_cost_tracing():
    table = Table(1, [['Период', 'Регион', 'Канал сбыта', 'Номенклатура', 'Серия', 'Количество', 'Цена без НДС'],
                      ['2026-01', 'Ташкент', 'Дистрибьютор', 'Kardiozan', 'KZ-202512-26', '1903', '111607,14']])
    fields = extract_table(table, 'doc')['rows'][0]['fields']
    assert fields['SalesRecord.product']['raw'] == 'Kardiozan'
    assert fields['SalesRecord.lot']['raw'] == 'KZ-202512-26'
    assert fields['SalesRecord.channel']['raw'] == 'Дистрибьютор'
    assert fields['SalesRecord.lot']['evidence']['quote'] == 'KZ-202512-26'
