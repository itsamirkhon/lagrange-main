from lagrange_rx.ingest.cross_document import analyze_cross_documents


def proposal(document, kind, field, raw):
    evidence = {'document_id': document, 'page': 1, 'quote': raw}
    return {'id': f'{document}-{field}', 'type': kind, 'fields': {f'{kind}.{field}': {'raw': raw, 'evidence': evidence}}, 'status': 'extracted_unconfirmed'}


def test_exact_contract_reference_links_invoice_and_remains_unconfirmed():
    docs = [{'document_id': 'a', 'doc_class': 'contract'}, {'document_id': 'b', 'doc_class': 'invoice'}]
    props = [proposal('a', 'Contract', 'contract_number', 'K-1'), proposal('b', 'Shipment', 'contract_ref', 'K-1')]
    result = analyze_cross_documents(docs, props)
    assert len(result['links']) == 1
    assert result['links'][0]['status'] == 'extracted_unconfirmed'
    assert result['links'][0]['source'] == 'b' and result['links'][0]['target'] == 'a'
    assert result['links'][0]['features'] == ['contract_number']


def test_candidates_and_conflicts_do_not_confirm_or_compare_unrelated_documents():
    docs = [{'document_id': 'a', 'doc_class': 'contract'}, {'document_id': 'b', 'doc_class': 'invoice'}]
    props = [proposal('a', 'Contract', 'contract_number', 'K-1'), proposal('b', 'Shipment', 'contract_ref', 'K-2'),
             proposal('a', 'Contract', 'supplier_name', 'Acme'), proposal('b', 'Shipment', 'supplier', 'Acme')]
    assert analyze_cross_documents(docs, props) == {'links': [], 'issues': []}


def test_quote_contract_comparison_requires_matching_explicit_quote_ref_and_currency():
    docs = [{'document_id': 'q', 'doc_class': 'supplier_quote'}, {'document_id': 'c', 'doc_class': 'contract'}]
    props = [proposal('q', 'Quote', 'quote_number', 'Q-1'), proposal('c', 'Contract', 'quote_ref', 'Q-1'),
             proposal('q', 'Quote', 'unit_price', '10'), proposal('c', 'Contract', 'unit_price', '12'),
             proposal('q', 'Quote', 'currency', 'USD'), proposal('c', 'Contract', 'currency', 'USD'),
             proposal('q', 'Quote', 'vat_note', 'excl'), proposal('c', 'Contract', 'vat_note', 'excl'),
             proposal('q', 'Quote', 'price_unit', 'pack'), proposal('c', 'Contract', 'price_unit', 'pack')]
    result = analyze_cross_documents(docs, props)
    assert len(result['links']) == 1 and len(result['issues']) == 1
    assert result['issues'][0]['issue_type'] == 'contradiction'
    assert len(result['issues'][0]['evidence']) == 2
    props[5] = proposal('c', 'Contract', 'currency', 'EUR')
    assert analyze_cross_documents(docs, props)['issues'] == []


def test_ambiguous_identifiers_have_no_comparisons():
    docs = [{'document_id': x, 'doc_class': cls} for x, cls in [('a', 'contract'), ('b', 'contract'), ('i', 'invoice')]]
    props = [proposal(x, 'Contract', 'contract_number', '1') for x in ('a', 'b')] + [proposal('i', 'Shipment', 'contract_ref', '1')]
    result = analyze_cross_documents(docs, props)
    assert len(result['links']) == 2 and all(link['status'] == 'extracted_unconfirmed' for link in result['links'])
    assert result['issues'] == []


def test_price_comparison_requires_explicit_vat_and_unit():
    docs = [{'document_id': 'q', 'doc_class': 'supplier_quote'}, {'document_id': 'c', 'doc_class': 'contract'}]
    props = [proposal('q', 'Quote', 'quote_number', 'Q-1'), proposal('c', 'Contract', 'quote_ref', 'Q-1'),
             proposal('q', 'Quote', 'unit_price', '10'), proposal('c', 'Contract', 'unit_price', '12'),
             proposal('q', 'Quote', 'currency', 'USD'), proposal('c', 'Contract', 'currency', 'USD')]
    assert analyze_cross_documents(docs, props)['issues'] == []


def test_linked_quote_contract_moq_conflict_does_not_depend_on_price_basis():
    docs = [{'document_id': 'q', 'doc_class': 'supplier_quote'}, {'document_id': 'c', 'doc_class': 'contract'}]
    props = [proposal('q', 'Quote', 'quote_number', 'Q-1'), proposal('c', 'Contract', 'quote_ref', 'Q-1'),
             proposal('q', 'Quote', 'moq', '10000'), proposal('c', 'Contract', 'moq', '5000')]
    result = analyze_cross_documents(docs, props)
    assert len(result['issues']) == 1
    assert result['issues'][0]['issue_type'] == 'contradiction'
    assert result['issues'][0]['affected'] == ['c-moq', 'q-moq']
    assert len(result['issues'][0]['evidence']) == 2


def test_moq_without_unambiguous_reference_is_not_compared():
    docs = [{'document_id': 'q', 'doc_class': 'supplier_quote'}, {'document_id': 'c', 'doc_class': 'contract'}]
    props = [proposal('q', 'Quote', 'quote_number', 'Q-1'), proposal('c', 'Contract', 'quote_ref', 'Q-2'),
             proposal('q', 'Quote', 'moq', '10000'), proposal('c', 'Contract', 'moq', '5000')]
    assert analyze_cross_documents(docs, props)['issues'] == []
