from lagrange_rx.ingest.review import build_review_queue


def item(i, field, value, *, doc='doc', confidence=None, impact=None, row=1):
    data = {'raw': value, 'evidence': {'document_id': doc, 'page': 1, 'quote': value}}
    if confidence is not None:
        data['confidence'] = confidence
    return {'id': str(i), 'type': field.split('.')[0], 'source_row': row,
            'fields': {field: data}, 'status': 'extracted_unconfirmed',
            **({'money_impact_uzs': impact} if impact is not None else {})}


def test_group_cells_by_document_and_field_preserving_values_and_evidence():
    proposals = [item(i, 'SalesRecord.qty', str(i), row=i) for i in range(100)]
    queue = build_review_queue(proposals)
    assert len(queue) == 1
    question = queue[0]
    assert len(question['items']) == 100
    assert question['items'][41]['instance_id'] == '41'
    assert question['items'][41]['value']['evidence']['quote'] == '41'
    assert question['instance_ids'] == [str(i) for i in range(100)]
    assert all(p['status'] == 'extracted_unconfirmed' for p in proposals)


def test_distinct_documents_fields_and_model_entities_do_not_mix():
    proposals = [item(1, 'Sale.qty', '1'), item(2, 'Sale.qty', '2', doc='other'),
                 item(3, 'Sale.price', '3'), item(4, 'Sale.qty', '4', row='model_entity'),
                 item(5, 'Sale.qty', '5', row='model_entity')]
    assert len(build_review_queue(proposals)) == 5


def test_missing_evidence_is_never_grouped_with_other_cells():
    a, b = item(1, 'Sale.qty', '1'), item(2, 'Sale.qty', '2')
    del a['fields']['Sale.qty']['evidence']
    del b['fields']['Sale.qty']['evidence']
    assert len(build_review_queue([a, b])) == 2


def test_known_money_impact_sorted_before_unknown_and_summed_per_group():
    queue = build_review_queue([item(1, 'Sale.qty', '1'), item(2, 'Sale.price', '3', impact=5),
                                item(3, 'Sale.price', '4', impact=10)])
    assert queue[0]['field'] == 'Sale.price'
    assert queue[0]['money_impact_uzs'] == 15
    assert queue[1]['money_impact_uzs'] is None


def test_confidence_does_not_auto_confirm_money_or_uncalibrated_extraction():
    p = item(1, 'Sale.price', '1', confidence=.99)
    q = item(2, 'Sale.name', 'abc', confidence=.99)
    queue = build_review_queue([p, q])
    assert len(queue) == 2
    assert p['status'] == q['status'] == 'extracted_unconfirmed'
    assert all(question['status'] == 'pending' for question in queue)


def test_group_ids_stable_under_input_reordering():
    proposals = [item(1, 'Sale.qty', '1'), item(2, 'Sale.qty', '2')]
    assert build_review_queue(proposals)[0]['question_id'] == build_review_queue(proposals[::-1])[0]['question_id']


def test_missing_quote_is_kept_individual():
    a, b = item(1, 'Sale.qty', '1'), item(2, 'Sale.qty', '2')
    del a['fields']['Sale.qty']['evidence']['quote']
    del b['fields']['Sale.qty']['evidence']['quote']
    assert len(build_review_queue([a, b])) == 2


def test_money_sensitive_questions_prioritized_when_impact_unknown():
    from lagrange_rx.ingest.review import build_review_queue
    proposals = [
        {'id': 'a', 'status': 'extracted_unconfirmed', 'fields': {'SalesRecord.region': {'raw': 'X', 'evidence': {'document_id': 'd', 'page': 1, 'quote': 'X'}}}, 'source_row': 1},
        {'id': 'b', 'status': 'extracted_unconfirmed', 'fields': {'SalesRecord.price_ex_vat': {'raw': '1', 'evidence': {'document_id': 'd', 'page': 1, 'quote': '1'}}}, 'source_row': 1},
    ]
    questions = build_review_queue(proposals)
    assert questions[0]['field'] == 'SalesRecord.price_ex_vat'
    assert questions[0]['affects_money'] is True
    assert questions[0]['money_impact_uzs'] is None


def test_expense_questions_keep_product_evidence_and_do_not_mix_products():
    from lagrange_rx.ingest.review import build_review_queue
    def proposal(ident, product):
        return {'id': ident, 'type': 'CostItem', 'source_row': 1, 'status': 'extracted_unconfirmed', 'source_product': {'raw': product, 'evidence': {'document_id': 'd', 'page': 1, 'quote': product}}, 'fields': {'CostItem.amount_uzs': {'raw': '100', 'evidence': {'document_id': 'd', 'page': 1, 'quote': '100'}}}}
    questions = build_review_queue([proposal('a', 'A'), proposal('b', 'B')])
    assert len(questions) == 2
    assert {q['items'][0]['source_product']['raw'] for q in questions} == {'A', 'B'}
