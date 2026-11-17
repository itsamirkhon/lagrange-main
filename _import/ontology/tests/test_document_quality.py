from lagrange_rx.ingest.document_quality import analyze_document_quality


def page(doc, kind, text):
    return {'document_id': doc, 'doc_class': kind, 'pages': [{'number': 1, 'text': text}], 'tables': []}


def test_vat_omission_is_detected_from_explicit_quote_and_evidence():
    docs = [page('quote', 'carrier_quote', 'Ставка/уп.:\n0.12 USD\nНДС:\nНДС не указан\nСрок: 18 дней')]
    issues = analyze_document_quality(docs)
    assert len(issues) == 1
    assert issues[0]['issue_type'] == 'vat_ambiguous'
    assert issues[0]['evidence'][0]['document_id'] == 'quote'
    assert 'НДС не указан' in issues[0]['evidence'][0]['text_span']


def test_vat_known_and_unrelated_document_are_not_flagged():
    docs = [page('a', 'carrier_quote', 'НДС: включен'), page('b', 'memo', 'НДС не указан')]
    assert analyze_document_quality(docs) == []


def test_factor_ten_across_linked_batch_and_stock_is_ambiguous_units():
    waybill = page('waybill', 'waybill', 'Партия:\nABC-202501-01\nSKU:\nDrug A\nКоличество:\n1650.0 (коробка)\nСрок годности:')
    stock = page('stock', 'stock', '')
    stock['tables'] = [{'page': 1, 'rows': [['lot_code', 'initial_qty'], ['ABC-202501-01', '16500']]}]
    issues = analyze_document_quality([waybill, stock])
    assert len(issues) == 1
    assert issues[0]['issue_type'] == 'unit_ambiguous'
    assert set(issues[0]['affected']) == {'waybill', 'stock'}
    assert {e['document_id'] for e in issues[0]['evidence']} == {'waybill', 'stock'}


def test_unlinked_or_matching_quantities_are_not_flagged():
    waybill = page('waybill', 'waybill', 'Партия:\nABC-202501-01\nКоличество:\n1650 (коробка)')
    stock = page('stock', 'stock', '')
    stock['tables'] = [{'page': 1, 'rows': [['lot_code', 'initial_qty'], ['other', '16500'], ['ABC-202501-01', '1650']]}]
    assert analyze_document_quality([waybill, stock]) == []
