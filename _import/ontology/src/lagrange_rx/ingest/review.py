"""Build reviewable groups without turning extracted cells into facts.

Confidence attached to extracted data is not calibrated, so grouping never
confirms data automatically. Each cell and its evidence remains inspectable.
"""
from collections import defaultdict
from copy import deepcopy
from hashlib import sha256
import json
import math


_OPTIONS = ['confirm', 'correct', 'irrelevant', 'need_document']


def _group_key(proposal, field, value):
    evidence = value.get('evidence') or {}
    document = evidence.get('document_id')
    row = proposal.get('source_row')
    # Only actual table cells with a source document can share a decision.
    if not document or not evidence.get('quote') or not isinstance(row, int) or isinstance(row, bool):
        return ('individual', proposal['id'], field)
    product = proposal.get('source_product') or {}
    return ('table', str(document), evidence.get('page'), field, product.get('raw'))


def _impact(proposal):
    value = proposal.get('money_impact_uzs')
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def _question(group):
    items = sorted(group, key=lambda item: (str(item['source_row']).zfill(20), item['instance_id']))
    ids = [item['instance_id'] for item in items]
    field = items[0]['field']
    digest = sha256(json.dumps([field, ids], ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()
    impacts = [item.pop('_impact') for item in items]
    total = sum(impacts) if all(value is not None for value in impacts) else None
    affects_money = any(token in field.casefold() for token in ('price', 'currency', 'vat', 'unit', 'qty', 'moq', 'amount', 'cost', 'rate', 'version'))
    return {'question_id': digest, 'instance_id': ids[0], 'instance_ids': ids, 'affects_money': affects_money,
            'field': field, 'items': items, 'money_impact_uzs': total,
            'options': list(_OPTIONS), 'status': 'pending'}


def build_review_queue(proposals):
    """Return grouped pending questions for unconfirmed single-field proposals.

    Grouping is limited to the same document and field. Unknown monetary impact
    stays unknown; known impacts are summed only if every member supplies one.
    """
    groups = defaultdict(list)
    for proposal in proposals:
        if proposal.get('status') != 'extracted_unconfirmed':
            continue
        for field, value in proposal['fields'].items():
            groups[_group_key(proposal, field, value)].append({
                'instance_id': proposal['id'], 'field': field,
                'source_row': proposal.get('source_row'), 'source_product': deepcopy(proposal.get('source_product')), 'value': deepcopy(value),
                '_impact': _impact(proposal),
            })
    questions = [_question(group) for group in groups.values()]
    return sorted(questions, key=lambda q: (q['money_impact_uzs'] is None,
                                            -(q['money_impact_uzs'] or 0), not q['affects_money'], q['question_id']))
