from datetime import date
from decimal import Decimal

import pytest

from lagrange_rx.ingest.money_checks import check_money, parse_cbu_rate


def field(raw, doc):
    return {'raw': raw, 'evidence': {'document_id': doc, 'page': 1, 'quote': raw}}


def record(doc, kind, **fields):
    return {'document_id': doc, 'doc_class': kind, 'fields': {k: field(v, doc) for k, v in fields.items()}}


def test_missing_currency_and_expired_quote():
    docs = [record('q', 'supplier_quote', unit_price='10', valid_until='2025-02-01')]
    issues = check_money(docs, as_of=date(2025, 2, 2))
    assert {i['field'] for i in issues} == {'currency', 'valid_until'}
    assert all(i['evidence'] for i in issues)


def test_currency_mismatch_only_with_explicit_reference():
    q = record('q', 'supplier_quote', quote_number='Q1', currency='USD')
    c = record('c', 'contract', quote_ref='Q1', currency='EUR')
    assert [i['field'] for i in check_money([q, c])] == ['currency']
    c['fields']['quote_ref'] = field('Q2', 'c')
    assert check_money([q, c]) == []


def test_fx_compares_official_rate_for_customs_date_and_currency():
    customs = record('g', 'customs_entry', entry_date='2025-01-02', currency='USD', fx_rate_on_entry='12500')
    rate = parse_cbu_rate([{'Ccy': 'USD', 'Date': '01.01.2025', 'Rate': '12600', 'Nominal': '1'}], 'USD', date(2025, 1, 2))
    issue, = check_money([customs], rates=[rate])
    assert issue['field'] == 'fx_rate_on_entry'
    assert issue['evidence'] and issue['reference']['source'].startswith('https://cbu.uz/')
    assert check_money([customs], rates=[]) == []


def test_rate_nominal_and_future_rate_rejected():
    rate = parse_cbu_rate([{'Ccy': 'JPY', 'Date': '01.01.2025', 'Rate': '8000', 'Nominal': '100'}], 'JPY', date(2025, 1, 2))
    assert rate.rate == Decimal('80')
    with pytest.raises(ValueError):
        parse_cbu_rate([{'Ccy': 'USD', 'Date': '03.01.2025', 'Rate': '1', 'Nominal': '1'}], 'USD', date(2025, 1, 2))


def test_registry_age_requires_explicit_policy_and_no_future_date():
    registry = record('r', 'registry_record', document_date='2025-01-01')
    assert check_money([registry], as_of=date(2025, 2, 1)) == []
    assert check_money([registry], as_of=date(2025, 2, 1), registry_max_age_days=30)[0]['field'] == 'document_date'
    assert check_money([registry], as_of=date(2024, 12, 1), registry_max_age_days=30) == []


def test_batch_adapter_does_not_pick_ambiguous_values():
    from lagrange_rx.ingest.money_checks import money_records_from_proposals
    documents = [{'document_id': 'q', 'doc_class': 'supplier_quote'}]
    proposals = [{'fields': {'Quote.unit_price': field('10', 'q'), 'Quote.currency': field('USD', 'q')}},
                 {'fields': {'Quote.currency': field('EUR', 'q')}}]
    result = money_records_from_proposals(documents, proposals)
    assert 'currency' not in result[0]['fields']
    assert result[0]['fields']['unit_price']['raw'] == '10'


def test_batch_customs_field_names_and_missing_rate_are_explicit():
    from lagrange_rx.ingest.money_checks import money_records_from_proposals
    documents = [{'document_id': 'g', 'doc_class': 'customs'}]
    proposals = [{'fields': {'CustomsEntry.date': field('2025-01-02', 'g'),
                             'CustomsEntry.currency': field('USD', 'g'),
                             'CustomsEntry.fx_rate_used': field('12500', 'g')}}]
    records = money_records_from_proposals(documents, proposals)
    issue, = check_money(records, require_official_rate=True)
    assert issue['issue_type'] == 'missing'
    assert issue['field'] == 'fx_rate_on_entry'
    assert check_money(records) == []


def test_currency_embedded_in_price_is_not_reported_missing():
    quote = record('q', 'supplier_quote', unit_price='10 USD')
    assert check_money([quote]) == []


def test_document_dates_are_normalized_without_guessing():
    quote = record('q', 'supplier_quote', valid_until='01.02.2025')
    assert check_money([quote], as_of=date(2025, 2, 2))[0]['issue_type'] == 'stale'
    quote['fields']['valid_until'] = field('01/02/2025', 'q')
    assert check_money([quote], as_of=date(2025, 2, 2)) == []
    quote['fields']['valid_until'] = field('фев 2025', 'q')
    assert check_money([quote], as_of=date(2025, 2, 2)) == []


def test_rate_requests_are_unique_and_require_verified_unambiguous_fields():
    from lagrange_rx.ingest.money_checks import cbu_rate_requests
    valid = record('g', 'customs', entry_date='02.01.2025', currency='USD', fx_rate_on_entry='12500')
    duplicate = record('h', 'customs', entry_date='2025-01-02', currency='usd', fx_rate_on_entry='12000')
    ambiguous = record('i', 'customs', entry_date='01/02/2025', currency='EUR', fx_rate_on_entry='12')
    malformed = record('j', 'customs', entry_date='2025-01-02', currency='../../USD', fx_rate_on_entry='12')
    assert cbu_rate_requests([valid, duplicate, ambiguous, malformed]) == [('USD', date(2025, 1, 2))]


def test_fx_check_accepts_unambiguous_dotted_customs_date():
    customs = record('g', 'customs', entry_date='02.01.2025', currency='USD', fx_rate_on_entry='12500')
    official = parse_cbu_rate([{'Ccy': 'USD', 'Date': '01.01.2025', 'Rate': '12600', 'Nominal': '1'}], 'USD', date(2025, 1, 2))
    assert check_money([customs], rates=[official])[0]['field'] == 'fx_rate_on_entry'
