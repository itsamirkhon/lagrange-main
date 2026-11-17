"""Conservative money checks over evidence-backed fields, with optional CBU rates.

The caller supplies the calculation date and any registry freshness policy. A
missing official rate means no FX conclusion, not a clean bill of health.
"""
from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
import json
import re
from urllib.request import urlopen

from .normalize import normalize_date


@dataclass(frozen=True)
class OfficialRate:
    currency: str
    requested_date: date
    effective_date: date
    rate: Decimal  # UZS per one unit of foreign currency
    source: str


def parse_cbu_rate(payload, currency, requested_date):
    """Validate the CBU JSON response for an explicitly requested date."""
    currency = currency.upper()
    url = f'https://cbu.uz/ru/arkhiv-kursov-valyut/json/{currency}/{requested_date.isoformat()}/'
    if not re.fullmatch(r'[A-Z]{3}', currency) or not isinstance(payload, list) or len(payload) != 1:
        raise ValueError('Invalid CBU response or currency')
    item = payload[0]
    try:
        effective = (date.fromisoformat(item['Date']) if '-' in item['Date']
                     else datetime.strptime(item['Date'], '%d.%m.%Y').date())
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError('Invalid CBU effective date') from exc
    try:
        rate = Decimal(str(item['Rate'])) / Decimal(str(item['Nominal']))
    except (InvalidOperation, ZeroDivisionError, KeyError, TypeError) as exc:
        raise ValueError('Invalid CBU rate') from exc
    if item.get('Ccy') != currency or effective > requested_date or not rate.is_finite() or rate <= 0:
        raise ValueError('CBU rate does not match request')
    return OfficialRate(currency, requested_date, effective, rate, url)


def fetch_cbu_rate(currency, requested_date, timeout=10):
    """Fetch a date-specific official rate; raises on network or invalid data."""
    currency = currency.upper()
    if not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Invalid currency')
    url = f'https://cbu.uz/ru/arkhiv-kursov-valyut/json/{currency}/{requested_date.isoformat()}/'
    with urlopen(url, timeout=timeout) as response:
        payload = json.load(response)
    return parse_cbu_rate(payload, currency, requested_date)


def _field(doc, name):
    value = doc.get('fields', {}).get(name)
    if not isinstance(value, dict) or not isinstance(value.get('raw'), str):
        return None
    evidence = value.get('evidence')
    if not isinstance(evidence, dict) or evidence.get('document_id') != doc.get('document_id') or not evidence.get('quote'):
        return None
    return value if value['raw'].strip() else None


def _date(value):
    try:
        return date.fromisoformat(normalize_date(value['raw'])) if value else None
    except ValueError:
        return None


def _issue(field, docs, evidence, detail, reference=None, kind='contradiction'):
    result = {'issue_type': kind, 'severity': 'warning', 'field': field,
              'affected': [doc['document_id'] for doc in docs], 'evidence': evidence,
              'detail': detail, 'resolution': 'unresolved'}
    if reference:
        result['reference'] = reference
    return result


def check_money(documents, *, as_of=None, rates=(), registry_max_age_days=None, fx_tolerance=Decimal('0.01'), require_official_rate=False):
    """Return review issues for explicit fields, never auto-correct values.

    Documents have document_id, doc_class, fields[name]={raw,evidence}. Links
    require unique exact quote/contract references. Dates are ISO 8601.
    """
    issues = []
    for doc in documents:
        kind = doc.get('doc_class')
        price, currency = _field(doc, 'unit_price'), _field(doc, 'currency')
        if (kind == 'supplier_quote' and price and not currency
                and 'currency' not in doc.get('ambiguous_fields', ())
                and not re.search(r'\b(?:USD|EUR|UZS|RUB|GBP|CNY|KZT)\b|[$€£₽]', price['raw'], re.I)):
            issues.append(_issue('currency', [doc], [price['evidence']], 'Price has no evidence-backed currency; review the source.', kind='currency_ambiguous'))
        validity = _field(doc, 'valid_until')
        if kind == 'supplier_quote' and as_of and _date(validity) and _date(validity) < as_of:
            issues.append(_issue('valid_until', [doc], [validity['evidence']], f'Quote expired before calculation date {as_of}.', kind='stale'))
        issued = _field(doc, 'document_date')
        if kind in ('registry_record', 'registry_extract') and as_of and registry_max_age_days is not None and _date(issued):
            if (as_of - _date(issued)).days > registry_max_age_days:
                issues.append(_issue('document_date', [doc], [issued['evidence']], f'Registry extract exceeds the configured {registry_max_age_days}-day age.', kind='stale'))
        if kind in ('customs_entry', 'customs'):
            _check_rate(doc, rates, fx_tolerance, issues, require_official_rate)
    for source_kind, target_kind, ref_name, id_name in [('contract', 'supplier_quote', 'quote_ref', 'quote_number'), ('invoice', 'contract', 'contract_ref', 'contract_number')]:
        targets = [doc for doc in documents if doc.get('doc_class') == target_kind]
        for source in (doc for doc in documents if doc.get('doc_class') == source_kind):
            ref, current = _field(source, ref_name), _field(source, 'currency')
            if not ref or not current:
                continue
            matches = [doc for doc in targets if (ident := _field(doc, id_name)) and ident['raw'].strip().casefold() == ref['raw'].strip().casefold()]
            if len(matches) != 1:
                continue
            target = matches[0]
            previous = _field(target, 'currency')
            if previous and previous['raw'].strip().casefold() != current['raw'].strip().casefold():
                issues.append(_issue('currency', [source, target], [ref['evidence'], _field(target, id_name)['evidence'], current['evidence'], previous['evidence']], 'Explicitly linked documents state different currencies; confirm terms.'))
    return issues


def _check_rate(doc, rates, tolerance, issues, require_official_rate):
    entry, currency, stated = (_field(doc, name) for name in ('entry_date', 'currency', 'fx_rate_on_entry'))
    day = _date(entry)
    if not day or not currency or not stated:
        return
    matches = [rate for rate in rates if rate.requested_date == day and rate.currency == currency['raw'].strip().upper()]
    if len(matches) != 1:
        if require_official_rate:
            issues.append(_issue('fx_rate_on_entry', [doc], [entry['evidence'], currency['evidence'], stated['evidence']], 'Official CBU rate unavailable for this currency and customs date.', kind='missing'))
        return
    try:
        amount = Decimal(stated['raw'].replace(' ', '').replace(',', '.'))
    except InvalidOperation:
        return
    official = matches[0]
    if amount.is_finite() and abs(amount - official.rate) > tolerance:
        issues.append(_issue('fx_rate_on_entry', [doc], [entry['evidence'], currency['evidence'], stated['evidence']], 'Stated rate differs from the official CBU rate for the customs date.', {'source': official.source, 'requested_date': day.isoformat(), 'effective_date': official.effective_date.isoformat(), 'rate_per_unit': str(official.rate)}))


def money_records_from_proposals(documents, proposals):
    """Adapt validated ingest proposals, excluding ambiguous fields per document."""
    from collections import defaultdict
    grouped = defaultdict(lambda: defaultdict(list))
    for proposal in proposals:
        for name, value in proposal.get('fields', {}).items():
            if not isinstance(value, dict) or not isinstance(value.get('evidence'), dict):
                continue
            document_id = value['evidence'].get('document_id')
            if document_id and isinstance(value.get('raw'), str) and value['raw'].strip():
                grouped[document_id][name.rsplit('.', 1)[-1]].append(value)
    result = []
    for doc in documents:
        fields, ambiguous = {}, []
        for name, values in grouped[doc['document_id']].items():
            if len({value['raw'].strip().casefold() for value in values}) != 1:
                ambiguous.append(name)
                continue
            fields[name] = values[0]
        if doc.get('doc_class') == 'customs':
            for source, target in [('date', 'entry_date'), ('fx_rate_used', 'fx_rate_on_entry')]:
                if source in fields:
                    fields[target] = fields[source]
        if doc.get('doc_class') == 'registry_extract' and 'date' in fields:
            fields['document_date'] = fields['date']
        result.append({'document_id': doc['document_id'], 'doc_class': doc.get('doc_class'),
                       'fields': fields, 'ambiguous_fields': ambiguous})
    return result


def cbu_rate_requests(documents):
    """Unique (currency, date) pairs needed by the FX check, without I/O.

    Only evidence-backed customs fields with a full unambiguous date and a
    three-letter currency are eligible. The caller decides whether to fetch,
    handles failures, and passes the resulting rates to check_money.
    """
    requests = set()
    for doc in documents:
        if doc.get('doc_class') not in ('customs', 'customs_entry'):
            continue
        entry, currency, stated = (_field(doc, name) for name in ('entry_date', 'currency', 'fx_rate_on_entry'))
        day = _date(entry)
        if not day or not currency or not stated:
            continue
        code = currency['raw'].strip().upper()
        if re.fullmatch(r'[A-Z]{3}', code):
            requests.add((code, day))
    return sorted(requests)
