from lagrange_rx.ingest.observed_document_checks import analyze_observed_documents


def doc(id, text):
    return {'document_id': id, 'pages': {1: text}}


def test_contract_versions_with_same_number_and_different_cip_price():
    left = doc('a', 'КОНТРАКТ ПОСТАВКИ № CNT-123\nВалюта:\nUSD\nЦена CIP:\n7.10 USD')
    right = doc('b', 'КОНТРАКТ ПОСТАВКИ № CNT-123\nВалюта:\nUSD\nЦена CIP:\n7.60 USD')
    issue, = analyze_observed_documents([left, right])
    assert issue['issue_type'] == 'contradiction'
    assert issue['field'] == 'unit_price'
    assert set(issue['affected']) == {'a', 'b'}
    assert {e['document_id'] for e in issue['evidence']} == {'a', 'b'}
    assert all(e['quote'] and e['page'] == 1 for e in issue['evidence'])
    assert issue['resolution'] == 'unresolved'


def test_contract_comparison_requires_same_identifier_and_currency():
    base = doc('a', 'КОНТРАКТ ПОСТАВКИ № A-1\nЦена CIP: 7.10 USD')
    other = doc('b', 'КОНТРАКТ ПОСТАВКИ № B-1\nЦена CIP: 7.60 USD')
    currency = doc('c', 'КОНТРАКТ ПОСТАВКИ № A-1\nЦена CIP: 7.60 EUR')
    assert analyze_observed_documents([base, other, currency]) == []


def test_waybills_with_matching_shipment_lot_sku_date_are_possible_duplicates():
    text = ('НАКЛАДНАЯ СКЛАДА № {number}\nДата приёмки: 2025-01-20 | Отгрузка: shp_1\n'
            'Партия: lot_1 (KZ-202501-01)\nSKU: SKU_1\nКоличество: 1650 коробка\nСрок годности: 2026-10-22')
    issue, = analyze_observed_documents([doc('a', text.format(number='WB-1')), doc('b', text.format(number='WB-2'))])
    assert issue['issue_type'] == 'duplicate'
    assert set(issue['affected']) == {'a', 'b'}
    assert len(issue['evidence']) >= 2
    assert issue['resolution'] == 'unresolved'


def test_waybill_does_not_match_only_on_shipment():
    a = doc('a', 'НАКЛАДНАЯ СКЛАДА № A\nДата приёмки: 2025-01-20 | Отгрузка: s1\nПартия: p1 (KZ-1)\nSKU: sku1\nКоличество: 2 шт\nСрок годности: 2026-10-22')
    b = doc('b', 'НАКЛАДНАЯ СКЛАДА № B\nДата приёмки: 2025-01-20 | Отгрузка: s1\nПартия: p2 (KZ-2)\nSKU: sku1\nКоличество: 2 шт\nСрок годности: 2026-10-22')
    assert analyze_observed_documents([a, b]) == []


def test_waybill_uses_external_details_even_if_internal_ids_differ():
    template = ('НАКЛАДНАЯ СКЛАДА № {number}\nДата приёмки: 2025-01-20 | Отгрузка: {shipment}\n'
                'Партия: {lot} (KZ-202501-01)\nSKU: {sku}\nКоличество: 1650.0 (коробка)\nСрок годности: 2026-10-22')
    a = doc('a', template.format(number='A', shipment='s1', lot='l1', sku='sku1'))
    b = doc('b', template.format(number='B', shipment='s2', lot='l2', sku='sku2'))
    assert analyze_observed_documents([a, b])[0]['issue_type'] == 'duplicate'


def test_waybill_rejects_incompatible_external_warehouse():
    text = ('НАКЛАДНАЯ СКЛАДА № A\nСклад: {warehouse}\nДата приёмки: 2025-01-20\n'
            'Партия: lot1 (KZ-1)\nКоличество: 3 (коробка)\nСрок годности: 2026-10-22')
    assert analyze_observed_documents([doc('a', text.format(warehouse='Первый')), doc('b', text.format(warehouse='Второй'))]) == []


def test_waybill_reads_multiline_pdf_labels():
    text = ('НАКЛАДНАЯ СКЛАДА № A\nДата приёмки: 2025-01-20\n'
            'Партия:\ninternal (KZ-1)\nКоличество:\n3 (коробка)\nСрок годности:\n2026-10-22')
    assert len(analyze_observed_documents([doc('a', text), doc('b', text)])) == 1


def test_contracts_with_dogovor_heading_and_price_are_compared():
    from lagrange_rx.ingest.observed_document_checks import analyze_observed_documents
    docs = [
        {'document_id': 'a', 'pages': {1: 'ДОГОВОР № ABC-1\nЦена:\n7.48 USD\nMOQ:\n5000 уп.'}},
        {'document_id': 'b', 'pages': {1: 'ДОГОВОР № ABC-1 (ПРОЕКТ v2)\nЦена:\n8.00 USD\nMOQ:\n7000 уп.'}},
    ]
    issues = analyze_observed_documents(docs)
    assert any(i['field'] == 'unit_price' and i['issue_type'] == 'contradiction' for i in issues)


def test_waybill_duplicate_uses_invoice_date_and_line_items():
    from lagrange_rx.ingest.observed_document_checks import analyze_observed_documents
    body = 'Дата: 2025-01-20 | Инвойс: INV-1\nПозиции\nСерия\nПрепарат\nКоличество\nСрок годн.\nKZ-1\nDrug A\n500 упак.\n2027-01-01\nНакладная РН-1'
    a = {'document_id': 'a', 'pages': {1: 'СКЛАДСКАЯ НАКЛАДНАЯ № РН-1\n' + body}}
    b = {'document_id': 'b', 'pages': {1: 'СКЛАДСКАЯ НАКЛАДНАЯ № РН-2\n' + body}}
    assert any(i['issue_type'] == 'duplicate' for i in analyze_observed_documents([a, b]))
