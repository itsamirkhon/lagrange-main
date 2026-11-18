"""Q2 link choice and Q2.column suggestions are proposals attached to links/issues."""
from types import SimpleNamespace

from lagrange_rx.ingest.column_suggest import suggest_columns
from lagrange_rx.ingest.cross_document import analyze_cross_documents
from lagrange_rx.ingest.decisions import DecisionAnswer


class Scorer:
    def __init__(self, pick):
        self.pick, self.seen = pick, []

    def decide(self, document, questions):
        self.seen.append((document, questions))
        out = []
        for q in questions:
            options = q.options
            top = self.pick(q, options)
            rest = [o for o in options if o != top]
            distribution = {top: .8, **{o: .2 / len(rest) for o in rest}}
            out.append(DecisionAnswer(qid=q.qid, distribution=distribution, top=top, confidence=.8, model_id='fake-decision', latency_ms=1))
        return out


def field(doc, name, raw):
    return {'id': f'{doc}-{name}', 'fields': {f'X.{name}': {'raw': raw, 'evidence': {'document_id': doc, 'page': 1, 'quote': raw}}}}


def ambiguous_contracts():
    documents = [{'document_id': 'inv', 'doc_class': 'invoice'}, {'document_id': 'c1', 'doc_class': 'contract'}, {'document_id': 'c2', 'doc_class': 'contract'}]
    proposals = [field('inv', 'contract_ref', 'CNT-7'), field('c1', 'contract_number', 'CNT-7'), field('c2', 'contract_number', 'CNT-7')]
    return documents, proposals


def test_q2_chooses_among_candidates_and_never_confirms():
    documents, proposals = ambiguous_contracts()
    scorer = Scorer(lambda q, options: 'c2')
    result = analyze_cross_documents(documents, proposals, scorer=scorer)
    state, (question,) = scorer.seen[0]
    assert question.options == ['c1', 'c2', 'none_of_these'] and 'CNT-7' in state
    assert {l['target']: l['decision']['probability'] for l in result['links']} == {'c1': .1, 'c2': .8}
    assert all(l['status'] == 'extracted_unconfirmed' and l['decision']['chosen'] == 'c2' for l in result['links'])


def test_q2_none_of_these_raises_an_issue():
    documents, proposals = ambiguous_contracts()
    result = analyze_cross_documents(documents, proposals, scorer=Scorer(lambda q, options: 'none_of_these'))
    assert [i['issue_type'] for i in result['issues']] == ['ambiguous_link']


def test_unambiguous_links_do_not_spend_a_decision_call():
    documents = [{'document_id': 'inv', 'doc_class': 'invoice'}, {'document_id': 'c1', 'doc_class': 'contract'}]
    proposals = [field('inv', 'contract_ref', 'CNT-7'), field('c1', 'contract_number', 'CNT-7')]
    scorer = Scorer(lambda q, options: 'c1')
    result = analyze_cross_documents(documents, proposals, scorer=scorer)
    assert scorer.seen == [] and 'decision' not in result['links'][0]


def test_column_suggestion_is_attached_per_unknown_column_only():
    table = SimpleNamespace(rows=[['Месяц', 'Кол-во', 'Остаток склада'], ['2025-01', '10', '7'], ['2025-02', '12', '8']])
    unmapped = [{'index': 2, 'header': 'Остаток склада', 'reason': 'unknown'}, {'index': 1, 'header': 'x', 'reason': 'duplicate_mapping'}]
    scorer = Scorer(lambda q, options: 'ignore')
    found = suggest_columns(scorer, table, 0, unmapped)
    assert list(found) == [2] and found[2]['field'] == 'ignore'
    state, questions = scorer.seen[0]
    assert len(questions) == 1 and "sample values=['7', '8']" in state and 'SalesRecord.qty_packs' in questions[0].options


def test_column_suggestion_survives_a_failing_scorer():
    class Broken:
        def decide(self, *a):
            raise RuntimeError('down')
    table = SimpleNamespace(rows=[['Месяц', 'Z'], ['1', '2']])
    assert suggest_columns(Broken(), table, 0, [{'index': 1, 'header': 'Z', 'reason': 'unknown'}]) == {}
