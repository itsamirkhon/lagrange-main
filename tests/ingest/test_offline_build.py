"""Headless Ingest of the real L0 folder: draft entities, links, questions, commit."""
from collections import Counter
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

import pytest

from lagrange_rx.ingest.offline.build import build_draft
from lagrange_rx.ingest.offline.confirm import commit
from lagrange_rx.pnl import calculate_pnl

L0 = Path(__file__).resolve().parents[2] / 'sim' / 'worlds' / 'L0' / 'documents'
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def draft():
    return build_draft(L0, now=NOW)


def auto_answers(draft):
    """Test double for a human: confirms groups; attributes a cost label to the SKU of the same name."""
    out = []
    for q in draft.questions:
        if q['kind'] == 'confirm_group':
            answer = {'decision': 'confirm'}
        elif q['kind'] == 'cost_attribution':
            match = q['facts']['name_matches']
            answer = {'allocation': 'direct', 'product': match[0]} if match else {'allocation': 'unallocated'}
        else:
            answer = {'valid_from': q['facts']['signed_on']}
        out.append({'question_id': q['question_id'], 'answer': answer, 'answered_by': 'test'})
    return out


def test_entity_counts(draft):
    counts = Counter(e.collection for e in draft.entries)
    assert counts['products'] == 7
    assert counts['shipments'] == 44 and counts['customs_entries'] == 44 and counts['lots'] == 50
    assert counts['sales_records'] == 3694
    assert counts['cost_items'] == 264
    assert counts['registry_records'] == 3
    assert counts['routes'] == 5 + 1  # observed (corridor, supplier) pairs + the air corridor without shipments
    assert counts['quotes'] == 16 + 8
    assert len(draft.source_documents) == 72


def test_links_by_document_numbers_are_complete(draft):
    assert draft.links['invoices'] == draft.links['invoices_with_customs'] == 44
    assert draft.links['sales_linked_to_lot'] == draft.links['sales_rows'] == 3694
    assert draft.links['regional_reconciliation']['mismatches'] == 0
    assert not [i for i in draft.issues if i['severity'] == 'blocking']


def test_nothing_is_confirmed_by_ingest(draft):
    assert {e.entity.provenance.kind for e in draft.entries} == {'extracted_unconfirmed'}


def test_customs_legal_base_includes_all_landed_costs(draft):
    first = next(c for c in draft.of('customs_entries') if c.entry_ref == '26004/2025-0001')
    assert first.packs == 16500 and first.currency == 'USD'
    landed = Decimal('1686494791.68')
    assert abs(first.legal_base_c_per_pack * first.packs - landed) < Decimal('0.01')
    assert 'fees_uzs' in first.field_provenance  # derived, documented as such


def test_questions_cover_the_decisions_ingest_must_not_guess(draft):
    kinds = Counter(q['kind'] for q in draft.questions)
    assert kinds['cost_attribution'] == 2
    assert kinds['contract_effective_from'] >= 1
    assert kinds['confirm_group'] == 9
    labels = {q['facts']['label']: q['facts']['name_matches'] for q in draft.questions if q['kind'] == 'cost_attribution'}
    assert labels['Общий портфель препаратов'] == [] and len(labels['Kardiozan 10 мг №30']) == 1
    assert all(q['status'] == 'pending' for q in draft.questions)


def test_unextractable_facts_are_listed_not_invented(draft):
    text = ' '.join(x['what'] for x in draft.not_extracted)
    for needle in ('image documents', 'CreditLine', 'inbound freight', 'Product: INN'):
        assert needle in text


def test_commit_gives_a_graph_that_passes_for_calculation(draft):
    graph, report = commit(draft, auto_answers(draft), now=NOW)
    graph.for_calculation()
    assert len(graph.shipments) == 44 and len(graph.sales_records) == 3694
    assert report['dropped'] == []
    kinds = {i.provenance.kind for i in graph.cost_items}
    assert kinds == {'extracted_confirmed'}
    assert {c.allocation for c in graph.cost_items} == {'direct', 'unallocated'}


def test_unanswered_group_and_dependents_are_dropped(draft):
    answers = [a for a in auto_answers(draft) if a['question_id'] != 'confirm_group:invoices']
    graph, report = commit(draft, answers, now=NOW)
    assert not graph.shipments and not graph.lots
    assert not graph.sales_records  # sales reference lots
    assert any('dangling' in d['reason'] for d in report['dropped'])
    graph.for_calculation()


def test_focus_pnl_for_a_month_is_computable_after_confirmation(draft):
    graph, _ = commit(draft, auto_answers(draft), now=NOW)
    kardiozan = next(p.sku_id for p in graph.products if p.trade_name.startswith('Kardiozan'))
    # the portfolio costs stay unallocated, so they are outside this SKU's direct view
    result = calculate_pnl(graph, kardiozan, '2026-03', {'distribution', 'marketing', 'warehousing', 'financing', 'write_off', 'g_and_a'},
                           expenses_attributed_to_product=False)
    assert result.missing == () or all('распределен' in m for m in result.missing), result.missing
