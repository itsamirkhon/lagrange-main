"""Suggest cross-document relations from explicitly observed identifiers.

Input proposals must already have passed evidence validation. Nothing returned
by this module confirms a relation or authorizes calculation.
"""
from collections import defaultdict
from decimal import Decimal, InvalidOperation

from .checks import Observation, check_contradictions
from .resolve import Entity, resolve


def _fields(proposals):
    grouped = defaultdict(lambda: defaultdict(list))
    for proposal in proposals:
        for field, value in proposal.get('fields', {}).items():
            if not isinstance(value, dict) or not isinstance(value.get('evidence'), dict):
                continue
            evidence = value['evidence']
            document_id, raw = evidence.get('document_id'), value.get('raw')
            if document_id and isinstance(raw, str) and raw.strip():
                grouped[document_id][field.rsplit('.', 1)[-1]].append((raw.strip(), evidence, proposal['id']))
    # Ambiguous extracted fields cannot support a relation or comparison.
    return {doc: {name: values[0] for name, values in fields.items() if len({v[0] for v in values}) == 1}
            for doc, fields in grouped.items()}


def _get(fields, name):
    return fields.get(name, (None, None, None))[0]


def _number(fields, field):
    price = _get(fields, field)
    if price is None:
        return None
    try:
        result = Decimal(price.replace(' ', '').replace(',', '.'))
    except InvalidOperation:
        return None
    return result if result.is_finite() else None


def analyze_cross_documents(documents, proposals):
    """Return candidate links and issues for invoice/contract and quote/contract.

    Documents use ``document_id`` and ``doc_class``; proposals use the ingest
    ``fields`` structure with raw text and document-scoped evidence. Only exact
    explicit references permit comparisons, and monetary comparisons require
    the same explicit currency and VAT basis.
    """
    fields = _fields(proposals)
    classes = {doc['document_id']: doc.get('doc_class') for doc in documents}
    links, issues = [], []
    for source_class, target_class, source_key, target_key in (
        ('invoice', 'contract', 'contract_ref', 'contract_number'),
        ('contract', 'supplier_quote', 'quote_ref', 'quote_number'),
    ):
        targets = [Entity(doc_id, contract_number=_get(fields.get(doc_id, {}), target_key))
                   for doc_id, kind in classes.items() if kind == target_class and _get(fields.get(doc_id, {}), target_key)]
        for source, kind in classes.items():
            source_fields = fields.get(source, {})
            ref = _get(source_fields, source_key)
            if kind != source_class or not ref:
                continue
            resolution = resolve(Entity(source, contract_number=ref), targets)
            for candidate in resolution.candidates:
                links.append({'source': source, 'target': candidate.id, 'features': list(candidate.features),
                              'confidence': candidate.confidence, 'status': 'extracted_unconfirmed'})
            if resolution.match and source_class == 'contract':
                other = fields.get(resolution.match.id, {})
                a, b = _number(source_fields, 'moq'), _number(other, 'moq')
                if (a is not None and b is not None and a >= 0 and b >= 0
                        and a == a.to_integral_value() and b == b.to_integral_value()):
                    av, bv = source_fields['moq'], other['moq']
                    issues.extend(check_contradictions({'moq': Observation(av[2], a, [av[1]])},
                                                       {'moq': Observation(bv[2], b, [bv[1]])}))
                # Prices are comparable only with explicitly identical currency and VAT basis.
                comparable = all(
                    (left := _get(source_fields, key))
                    and left.casefold() == (_get(other, key) or '').casefold()
                    for key in ('currency', 'vat_note', 'price_unit')
                )
                if not comparable:
                    continue
                a, b = _number(source_fields, 'unit_price'), _number(other, 'unit_price')
                if a is not None and b is not None:
                    av, bv = source_fields['unit_price'], other['unit_price']
                    issues.extend(check_contradictions({'unit_price': Observation(av[2], a, [av[1]])},
                                                       {'unit_price': Observation(bv[2], b, [bv[1]])}))
    return {'links': links, 'issues': issues}


def _amount(record, name):
    field = record['fields'].get(name)
    if not field:
        return None
    from .money_fields import _number
    return _number(field['raw'])


def check_quote_contract_moq(records, *, price_tolerance=0.05):
    """X1 that survives ambiguous links.

    A quote row may match several contract versions (no shared reference, supplier
    names often unreadable). Plausible contracts are non-draft ones whose price is
    within ``price_tolerance``; the quote MOQ is flagged only when it agrees with
    none of them, so the finding holds whichever link is right. Review proposals only.
    """
    quotes = [r for r in records if r['doc_class'] == 'supplier_quote']
    contracts = [r for r in records if r['doc_class'] == 'contract' and r.get('status') != 'draft']
    issues = []
    for quote in quotes:
        moq, price = _amount(quote, 'moq'), _amount(quote, 'unit_price')
        if moq is None or not price:
            continue
        candidates = [c for c in contracts if _amount(c, 'moq') is not None and _amount(c, 'unit_price')
                      and abs(_amount(c, 'unit_price') - price) <= price_tolerance * price]
        if not candidates or any(_amount(c, 'moq') == moq for c in candidates):
            continue
        number = quote['fields'].get('quote_number', {}).get('raw', '?')
        listed = ', '.join(f"{c['fields']['contract_number']['raw']} MOQ {c['fields']['moq']['raw']}" for c in candidates)
        certainty = 'unique link' if len(candidates) == 1 else f'{len(candidates)} possible contracts'
        affected = [quote['document_id']] + [c['document_id'] for c in candidates]
        issues.append({'issue_type': 'contradiction', 'severity': 'warning', 'field': 'moq', 'affected': list(dict.fromkeys(affected)),
                       'evidence': [quote['fields']['moq']['evidence']] + [c['fields']['moq']['evidence'] for c in candidates],
                       'detail': f"Quote {number} MOQ {quote['fields']['moq']['raw']} matches no contract of similar price ({certainty}: {listed}).",
                       'resolution': 'unresolved', 'check_source': 'cross_document'})
    return issues
