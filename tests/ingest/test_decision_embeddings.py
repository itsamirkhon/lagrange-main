"""Decisions API scorer, Q0 pass, Q2 link choice, column suggestions and embeddings: fake transports, no network."""
import io
from urllib.error import HTTPError

import pytest

from lagrange_rx.ai.factory import Embedder, build_client, build_decision_scorer, build_embedder
from lagrange_rx.ai.openrouter import BudgetExceeded, OpenRouterClient, ReplayMiss
from lagrange_rx.ingest.decisions import DecisionQuestion, FallbackScorer, GoogleDecisionScorer
from lagrange_rx.ingest.q0 import Q0Runner
from lagrange_rx.ingest.resolve import Entity, resolve


def decisions_post(handler, calls):
    def post(url, payload, timeout, key):
        calls.append((url, payload))
        if url.endswith('/embeddings'):
            return {'data': [{'index': i, 'embedding': handler(t)} for i, t in enumerate(payload['input'])], 'usage': {'cost': 1e-6}}
        return {'model': 'google/gemini-decision-1-20261111', 'usage': {'cost': 1e-5},
                'answers': {key: handler(key.replace('_', '.', 1), q) for key, q in payload['questions'].items()}}
    return post


def q0_answers(qid, question):
    if question['type'] == 'score':
        return {'type': 'score', 'probabilities': {'0': .7, '1': .2, '2': .05, '3': .05}}
    top = {'ALL.doc_class': 'invoice', 'ALL.language': 'ru', 'ALL.is_photo_or_scan': 'born_digital',
           'ALL.has_handwritten_edits': 'yes', 'ALL.stamp_over_numbers': 'no'}[qid]
    names = list(question['criteria'])
    return {'type': 'choice', 'probabilities': {n: (.92 if n == top else .08 / (len(names) - 1)) for n in names}}


def client(tmp_path, post, **kw):
    return OpenRouterClient(tmp_path, api_key='k', budget_usd=1, max_request_cost_usd=.1, post=post, **kw)


def test_decisions_scorer_sends_criteria_and_returns_normalised_probabilities(tmp_path):
    calls = []
    scorer = GoogleDecisionScorer(client(tmp_path, decisions_post(q0_answers, calls)))
    runner = Q0Runner(scorer)
    answers = runner.run('Invoice No 5')
    url, payload = calls[0]
    assert url.endswith('/api/alpha/decisions') and payload['model'] == 'google/gemini-decision-1'
    assert payload['questions']['ALL_readability']['criteria'] == ['unreadable', 'poor', 'fair', 'clean']
    assert 'invoice' in payload['questions']['ALL_doc_class']['criteria'] and 'ALL.doc_class' not in payload['questions']
    assert answers['ALL.doc_class'].top == 'invoice' and sum(answers['ALL.doc_class'].distribution.values()) == pytest.approx(1)
    runner.run('Invoice No 5')
    assert len(calls) == 1  # one request per document


def test_q0_classification_is_capped_and_issues_are_proposals(tmp_path):
    runner = Q0Runner(GoogleDecisionScorer(client(tmp_path, decisions_post(q0_answers, []))))
    result = runner.classify('scan.pdf', 'text')
    assert (result.doc_class, result.method) == ('invoice', 'decision') and result.confidence <= .9
    kinds = {i['issue_type']: i for i in runner.issues('d1', 'text')}
    assert set(kinds) == {'unreadable', 'handwritten_edits'}  # stamp answered 'no'
    assert kinds['unreadable']['status'] == 'extracted_unconfirmed' and kinds['unreadable']['decision']['top'] == '0'


def test_replay_serves_recording_and_misses_loudly(tmp_path):
    Q0Runner(GoogleDecisionScorer(client(tmp_path, decisions_post(q0_answers, [])))).run('doc')
    replay = Q0Runner(GoogleDecisionScorer(OpenRouterClient(tmp_path, replay=True)))
    assert replay.run('doc')['ALL.doc_class'].top == 'invoice'
    with pytest.raises(ReplayMiss):
        replay.run('another document')


def test_budget_is_enforced_for_decisions(tmp_path):
    poor = OpenRouterClient(tmp_path, api_key='k', budget_usd=.01, max_request_cost_usd=.1, post=decisions_post(q0_answers, []))
    with pytest.raises(BudgetExceeded):
        Q0Runner(GoogleDecisionScorer(poor)).run('doc')


def test_fallback_only_on_provider_rejection(tmp_path):
    class Rejecting:
        def decide(self, *a):
            raise HTTPError('u', 404, 'nf', None, None)

    class BadRequest:
        def decide(self, *a):
            raise HTTPError('u', 400, 'bad', None, io.BytesIO(b'{"error":{"message":"question key invalid"}}'))

    class Chat:
        def decide(self, document, questions):
            from lagrange_rx.ingest.decisions import DecisionAnswer
            return [DecisionAnswer(qid=q.qid, distribution={o: (.8 if o == q.options[0] else .2 / (len(q.options) - 1)) for o in q.options},
                                   top=q.options[0], confidence=.8, model_id='chat/x', latency_ms=1) for q in questions]

    class Missing:
        def decide(self, *a):
            raise ReplayMiss('no')

    question = DecisionQuestion(qid='s', kind='score', instructions='x', rubric=['bad', 'ok'])
    scorer = FallbackScorer(Rejecting(), Chat())
    answer = scorer.decide('d', [question])[0]
    assert scorer.used_fallback and set(answer.distribution) == {'0', '1'}
    with pytest.raises(ReplayMiss):
        FallbackScorer(Missing(), Chat()).decide('d', [question])
    with pytest.raises(HTTPError):  # our own malformed request is never hidden behind plan B
        FallbackScorer(BadRequest(), Chat()).decide('d', [question])


def test_factory_off_returns_nothing_and_live_needs_budget(tmp_path):
    assert build_decision_scorer('off', tmp_path) is None and build_embedder('off', tmp_path) is None
    with pytest.raises(ValueError):
        build_client('openrouter', tmp_path, 0, .02)


def test_embeddings_are_cached_per_text_and_batched(tmp_path):
    calls = []
    post = decisions_post(lambda t: [float(len(t)), 1.0], calls)
    embedder = Embedder(client(tmp_path, post), 'm/e')
    first = embedder.vectors(['a', 'bb'])
    again = embedder.vectors(['bb', 'ccc'])
    assert first[1] == again[0] and [p['input'] for _, p in calls] == [['a', 'bb'], ['ccc']]
    assert Embedder(OpenRouterClient(tmp_path, replay=True), 'm/e').vectors(['a']) == [first[0]]
    with pytest.raises(ReplayMiss):
        Embedder(OpenRouterClient(tmp_path, replay=True), 'm/e').vectors(['unseen'])


class FakeEmbedder:
    def __init__(self, table):
        self.table = table

    def similarities(self, query, choices):
        return [self.table.get((query, c), 0.0) for c in choices]


def test_embedding_adds_a_name_feature_but_never_overrides_numbers():
    source = Entity('s', name='Андижанская область')
    embedder = FakeEmbedder({('Андижанская область', 'Andijon viloyati'): .91})
    assert resolve(source, [Entity('a', name='Andijon viloyati')]).candidates == []  # the n-gram baseline misses it
    found = resolve(source, [Entity('a', name='Andijon viloyati')], embedder).candidates
    assert [(c.id, c.features) for c in found] == [('a', ('name~embedding',))]
    dose = Entity('d', name='Кардиозан 10 мг №30')
    twin = Entity('t', name='Zzzz 20 mg N30')
    vetoed = resolve(dose, [twin], FakeEmbedder({('Кардиозан 10 мг №30', 'Zzzz 20 mg N30'): .99}))
    assert vetoed.candidates == []  # a different dose is vetoed even at cosine .99


def test_broken_embedder_leaves_the_deterministic_result():
    class Broken:
        def similarities(self, *a):
            raise ReplayMiss('x')
    target = Entity('a', invoice_number='INV-1')
    assert resolve(Entity('s', invoice_number='INV-1'), [target], Broken()).match.id == 'a'
