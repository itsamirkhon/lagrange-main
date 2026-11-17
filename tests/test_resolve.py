from datetime import date

import pytest

from lagrange_rx.ingest.resolve import Entity, resolve


def test_exact_identifiers_match_and_report_evidence():
    source = Entity('invoice', contract_number='К-123', counterparty='Acme', date=date(2026, 5, 3))
    choices = [Entity('contract', contract_number='К-123', counterparty='Acme', date=date(2026, 5, 1)),
               Entity('other', contract_number='К-999', counterparty='Other')]
    result = resolve(source, choices)
    assert result.match.id == 'contract'
    assert 'contract_number' in result.match.features
    assert result.needs_review is False


def test_conflicting_identifiers_prevent_a_link_even_if_other_features_match():
    source = Entity('s', invoice_number='INV-1', counterparty='Acme', quantity=100)
    result = resolve(source, [Entity('t', invoice_number='INV-2', counterparty='Acme', quantity=100)])
    assert result.match is None


def test_ambiguous_candidates_are_offered_with_none_option():
    source = Entity('s', lot_number='ABC')
    result = resolve(source, [Entity('a', lot_number='ABC'), Entity('b', lot_number='ABC')])
    assert result.match is None
    assert result.needs_review
    assert [x.id for x in result.candidates] == ['a', 'b']
    assert result.options == ['a', 'b', 'none_of_these']


def test_numeric_proximity_alone_cannot_create_a_candidate():
    result = resolve(Entity('s', quantity=100, date=date(2026, 1, 1)),
                     [Entity('t', quantity=102, date=date(2026, 1, 2))])
    assert result.candidates == []
    assert result.match is None


def test_date_and_quantity_thresholds_and_candidate_limit():
    source = Entity('s', counterparty='Acme', quantity=100, date=date(2026, 1, 1))
    choices = [Entity(str(i), counterparty='Acme', quantity=105, date=date(2026, 1, 8)) for i in range(12)]
    result = resolve(source, choices)
    assert len(result.candidates) == 10
    assert 'date±7d' in result.candidates[0].features
    assert 'quantity±5%' in result.candidates[0].features
    assert result.options[-1] == 'none_of_these'


def test_invalid_input_is_rejected():
    with pytest.raises(ValueError):
        Entity('s', quantity=-1)
    with pytest.raises(ValueError):
        resolve(Entity('s'), [Entity('a'), Entity('a')])


def test_entity_date_annotation_resolves():
    from datetime import date
    from typing import get_type_hints
    assert get_type_hints(Entity)['date'] == date | None
