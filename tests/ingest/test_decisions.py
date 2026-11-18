import pytest
from lagrange_rx.ingest.decisions import DecisionQuestion, DecisionAnswer, HeuristicScorer, ReplayScorer, review_policy


def test_distribution_and_unknown_are_enforced():
    with pytest.raises(ValueError):
        DecisionQuestion(qid='x', kind='choice', instructions='choose', options=['yes', 'no'])
    with pytest.raises(ValueError):
        DecisionAnswer(qid='x', distribution={'yes': .8, 'no': .8}, top='yes', confidence=.8, model_id='x', latency_ms=0)


def test_heuristic_refuses_to_guess_and_replay_is_exact(tmp_path):
    q = DecisionQuestion(qid='unknown', kind='choice', instructions='choose', options=['yes', 'unknown'])
    answer = HeuristicScorer().decide('anything', [q])[0]
    assert answer.top == 'unknown'
    ReplayScorer.write(tmp_path, 'abc', [answer])
    assert ReplayScorer(tmp_path).decide('abc', [q]) == [answer]
    with pytest.raises(LookupError):
        ReplayScorer(tmp_path).decide('missing', [q])


def test_money_is_never_auto_confirmed():
    assert review_policy(.99, 'USD', money=True) == 'review'
    assert review_policy(.99, 'yes', money=False) == 'auto'
    assert review_policy(.99, 'unknown', money=False) == 'issue'


def test_openrouter_scorer_uses_shared_client_and_validates_choices():
    from types import SimpleNamespace
    from lagrange_rx.ingest.decisions import OpenRouterDecisionScorer
    class Client:
        def chat(self, model_id, prompt, input_data, schema):
            assert model_id == 'example/model'
            return SimpleNamespace(content={'answers': [{'qid': 'class', 'distribution': {'invoice': .8, 'unknown': .2}, 'top': 'invoice', 'confidence': .8}]}, model_id=model_id, latency_ms=5)
    question = DecisionQuestion(qid='class', kind='choice', instructions='classify', options=['invoice', 'unknown'])
    answer = OpenRouterDecisionScorer(Client(), 'example/model').decide('an invoice', [question])[0]
    assert answer.top == 'invoice' and answer.distribution['unknown'] == .2


@pytest.mark.parametrize(('text', 'expected'), [
    ('СЧЁТ-ФАКТУРА № 123', 'invoice'),
    ('Договор поставки № 32', 'contract'),
    ('Commercial invoice', 'invoice'),
    ('No recognizable document heading', 'unknown'),
    ('Договор и счёт-фактура', 'unknown'),
])
def test_heuristic_document_class(text, expected):
    q = DecisionQuestion(qid='ALL.doc_class', kind='choice', instructions='classify', options=['invoice', 'contract', 'unknown'])
    answer = HeuristicScorer().decide(text, [q])[0]
    assert answer.top == expected
    assert sum(answer.distribution.values()) == pytest.approx(1)
    assert answer.confidence < 1


@pytest.mark.parametrize(('text', 'expected'), [
    ('Цена 120 USD', 'USD'), ('Стоимость 100 сум', 'UZS'),
    ('100 EUR или 120 USD', 'unknown'), ('Цена договорная', 'unknown'),
])
def test_heuristic_currency(text, expected):
    q = DecisionQuestion(qid='Q.price_currency', kind='choice', instructions='currency', options=['USD', 'EUR', 'RUB', 'UZS', 'unknown'])
    assert HeuristicScorer().decide(text, [q])[0].top == expected


@pytest.mark.parametrize(('text', 'expected'), [
    ('Цена включает НДС 12%', 'incl_vat'), ('Цена без НДС', 'excl_vat'),
    ('Цена не включая НДС', 'excl_vat'),
    ('Цена с НДС; стоимость без НДС', 'not_stated'), ('Цена 100', 'not_stated'),
])
def test_heuristic_vat(text, expected):
    q = DecisionQuestion(qid='Q.vat_status', kind='choice', instructions='VAT', options=['incl_vat', 'excl_vat', 'not_stated'])
    assert HeuristicScorer().decide(text, [q])[0].top == expected


@pytest.mark.parametrize(('text', 'expected'), [
    ('INVOICE & PACKING LIST\nSupplier: ACME | Contract: X-1', 'invoice'),
    ('From: a@example.com\nTo: b@example.com\nSubject: invoice\nPlease send the contract', 'email'),
    ('COMMERCIAL QUOTATION\nMOQ 100 packs\nUnit Price CIP', 'supplier_quote'),
    ('КП перевозчика\nМаршрут: Ташкент\nСтавка: 100 USD', 'carrier_quote'),
    ('sale_id\tmonth\tregion_id\tchannel\tqty_packs\tprice_ex_vat_uzs', 'sales_export'),
    ('lot_id\tlot_code\texpiry_date\tinitial_qty\tremaining_qty', 'stock'),
    ('customs_id\tclearance_date\tcustoms_post\tfx_rate_uzs', 'customs'),
    ('ВЫПИСКА ИЗ РЕЕСТРА ПРЕДЕЛЬНЫХ ЦЕН', 'registry_extract'),
    ('Regional Sales Summary — West & South', 'regional_report'),
    ('route_id\tcarrier\tmode\tper_shipment_usd\ttransit_days', 'carrier_quote'),
    ('Contract and invoice are discussed in this document', 'unknown'),
    ('', 'unknown'),
])
def test_heuristic_document_structural_signals(text, expected):
    options = ['invoice', 'contract', 'supplier_quote', 'carrier_quote', 'email', 'sales_export', 'stock', 'customs', 'registry_extract', 'regional_report', 'unknown']
    q = DecisionQuestion(qid='ALL.doc_class', kind='choice', instructions='classify', options=options)
    assert HeuristicScorer().decide(text, [q])[0].top == expected
