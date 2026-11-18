"""Evidence-backed money records read from parsed quote, contract, customs and registry documents.

Feeds ``check_money`` (C1, S1) and adds document-internal findings (C2, C3, registry S1).
Everything is a review proposal: nothing is corrected and no value is guessed. A value
is only emitted when its exact cell text is on the page.
"""
from __future__ import annotations

from datetime import date
from statistics import median
import re

from .normalize import normalize_date, normalize_number

_CURRENCY = re.compile(r'\b(USD|EUR|UZS|RUB|GBP|CNY|KZT)\b|([$€£₽])', re.I)
_SYMBOLS = {'$': 'USD', '€': 'EUR', '£': 'GBP', '₽': 'RUB'}
_QUOTE_COLUMNS = {'quote_number': r'номер кп|quote n|kp n|taklif', 'product_name': r'препарат|product|dori|наименование',
                  'moq': r'\bmoq\b', 'lead_time': r'срок|lead', 'unit_price': r'цена|price|narx', 'valid_until': r'действует до|valid'}
_QUOTE_NUMBER = re.compile(r'[A-ZА-Я]{2,4}-\d[\w-]*')
_DRAFT = re.compile(r'проект|draft|loyiha', re.I)


def _cells(text):
    return [c.strip() for line in text.splitlines() for c in line.split('\t') if c.strip()]


def _pairs(cells):
    """'Label:' value cells and inline 'A: x | B: y' pairs; keys are case-folded labels."""
    result = {}
    for i, cell in enumerate(cells):
        for part in (p.strip() for p in cell.split('|')):
            if part.endswith(':'):
                nxt = cells[i + 1] if i + 1 < len(cells) else ''
                if nxt and not nxt.endswith(':') and '|' not in nxt:
                    result.setdefault(part[:-1].strip().casefold(), nxt)
            elif ':' in part:
                label, value = part.split(':', 1)
                if value.strip():
                    result.setdefault(label.strip().casefold(), value.strip())
    return result


def _field(doc_id, page, raw, quote=None):
    return {'raw': raw, 'evidence': {'document_id': doc_id, 'page': page, 'quote': quote or raw}}


def _currency_token(text):
    match = _CURRENCY.search(text or '')
    return (match.group(1) or _SYMBOLS[match.group(2)]).upper() if match else None


def _number(text):
    match = re.search(r'\d[\d\s.,]*', text or '')
    if not match:
        return None
    try:
        return normalize_number(match.group().strip())
    except ValueError:
        return None


def _record(doc, fields, **extra):
    return {'document_id': doc['document_id'], 'doc_class': doc['doc_class'], 'fields': fields, 'ambiguous_fields': [], **extra}


def _quote_records(doc):
    records = []
    for page in doc['pages']:
        cells = _cells(page['text'])
        pairs = _pairs(cells)
        anchor = next((i for i, c in enumerate(cells) if re.fullmatch(_QUOTE_COLUMNS['quote_number'], c, re.I) or re.match(r'номер кп', c, re.I)), None)
        if anchor is None:
            continue
        end = next((i for i in range(anchor + 1, len(cells)) if re.fullmatch(r'[A-ZА-Я]{2,4}-\d[\w-]*', cells[i])), None)
        if end is None:
            continue
        header = cells[anchor:end]
        header_currency = next((_currency_token(c) for c in header if _currency_token(c)), None)
        stated = pairs.get('currency') or pairs.get('валюта')
        starts = [i for i in range(end, len(cells)) if _QUOTE_NUMBER.fullmatch(cells[i])]
        for n, start in enumerate(starts):
            # Cells can be dropped (blank) or fused by PDF extraction, so values are recognised by shape, not column index.
            stop = next((i for i in range(start + 1, len(cells)) if i in starts or '|' in cells[i]), len(cells))
            row = cells[start:stop]
            fields = {'quote_number': _field(doc['document_id'], page['number'], row[0])}
            rest = row[1:]
            if rest and not re.fullmatch(r'[\d\s.,]+|\d+\s*(?:дн|day)\S*', rest[0], re.I):
                fields['product_name'] = _field(doc['document_id'], page['number'], rest[0])
            lead = next((c for c in rest if re.fullmatch(r'\d+\s*(?:дн|day)\S*', c, re.I)), None)
            price_at = next((i for i, c in enumerate(rest) if re.fullmatch(r'\d{1,5}[.,]\d{1,3}(?:\s*(?:USD|EUR|UZS|RUB))?', c, re.I)), None)
            moq = next((c for c in rest if re.fullmatch(r'\d{3,6}', c)), None)
            if moq is None and 'product_name' in fields:
                fused = re.search(r'№\s*(\d{4,6})$', fields['product_name']['raw'])
                moq = fused.group(1) if fused else None
            if lead:
                fields['lead_time'] = _field(doc['document_id'], page['number'], lead)
            if moq:
                fields['moq'] = _field(doc['document_id'], page['number'], moq, fields['product_name']['raw'] if moq not in rest else moq)
            if price_at is not None:
                price = rest[price_at]
                fields['unit_price'] = _field(doc['document_id'], page['number'], price)
                if price_at + 1 < len(rest):
                    fields['valid_until'] = _field(doc['document_id'], page['number'], rest[price_at + 1])
                currency = _currency_token(price) or _currency_token(stated) or header_currency
                if currency:
                    quote = price if _currency_token(price) else (stated if _currency_token(stated) else next(c for c in header if _currency_token(c)))
                    fields['currency'] = _field(doc['document_id'], page['number'], currency, quote)
            if pairs.get('date'):
                fields['issue_date'] = _field(doc['document_id'], page['number'], pairs['date'])
            records.append(_record(doc, fields))
    return records


def _contract_records(doc):
    records = []
    for page in doc['pages']:
        cells = _cells(page['text'])
        title = next((c for c in cells if re.search(r'договор|contract|shartnoma', c, re.I) and '№' in c), None)
        if not title:
            continue
        number = re.search(r'№\s*([^\s(]+)', title)
        if not number:
            continue
        pairs = _pairs(cells)
        fields = {'contract_number': _field(doc['document_id'], page['number'], number.group(1))}
        for name, labels in (('sign_date', ('подписан', 'signed')), ('valid_until', ('действует до', 'valid until')),
                             ('unit_price', ('цена', 'price')), ('moq', ('moq',)), ('currency', ('валюта', 'currency'))):
            value = next((pairs[label] for label in labels if label in pairs), None)
            if value is None:
                continue
            if name == 'currency':
                token = _currency_token(value)
                if token and re.fullmatch(r'\s*(?:USD|EUR|UZS|RUB|GBP|CNY|KZT)\s*', value, re.I):
                    fields['currency'] = _field(doc['document_id'], page['number'], token, value)
            else:
                fields[name] = _field(doc['document_id'], page['number'], value)
        if 'currency' not in fields and 'unit_price' in fields and _currency_token(fields['unit_price']['raw']):
            price = fields['unit_price']
            fields['currency'] = _field(doc['document_id'], page['number'], _currency_token(price['raw']), price['raw'])
        records.append(_record(doc, fields, **({'status': 'draft'} if _DRAFT.search(title) else {})))
    return records


def _issue(kind, field, doc_id, evidence, detail):
    return {'issue_type': kind, 'severity': 'warning', 'field': field, 'affected': [doc_id], 'evidence': evidence,
            'detail': detail, 'resolution': 'unresolved', 'check_source': 'money_fields'}


def _unit_outliers(doc):
    issues = []
    for table in doc.get('tables', []):
        rows = table['rows']
        header_index = next((i for i, r in enumerate(rows[:5]) if sum(bool(str(c).strip()) for c in r) >= 3 and len({str(c) for c in r}) >= 3), None)
        if header_index is None:
            continue
        header = [str(c) for c in rows[header_index]]
        for column, title in enumerate(header):
            if 'uzs' not in title.casefold():
                continue
            values = [(r[column], _number(str(r[column]))) for r in rows[header_index + 1:] if column < len(r) and str(r[column]).strip()]
            numbers = [v for _, v in values if v and v > 0]
            if len(numbers) < 6 or median(numbers) < 10_000:
                continue
            middle = median(numbers)
            # A tiny value is only suspicious when its cell carries an explicit currency label that
            # most of the column's cells lack; small bare amounts (e.g. a minor write-off) are normal.
            labelled = [bool(_CURRENCY.search(str(cell))) for cell, _ in values]
            if sum(labelled) > len(values) / 2:
                continue
            for (cell, value), has_label in zip(values, labelled):
                if has_label and value and 0 < value < middle / 100:
                    issues.append(_issue('currency_ambiguous', title, doc['document_id'],
                                         [{'document_id': doc['document_id'], 'page': table['page'], 'quote': str(cell)}],
                                         f'Value {cell} in column "{title}" is over 100 times below the column median {middle:,.0f}; the amount may be in another currency.'))
    return issues


def _customs_cost_issues(doc):
    issues = []
    for page in doc['pages']:
        cells = _cells(page['text'])
        anchor = next((i for i, c in enumerate(cells) if re.match(r'номер гтд', c, re.I)), None)
        if anchor is None:
            continue
        end = next((i for i in range(anchor + 1, len(cells)) if re.fullmatch(r'\d{3,6}/[\d-]+', cells[i])), None)
        if end is None:
            continue
        header = [c.casefold() for c in cells[anchor:end]]
        rate = next((i for i, h in enumerate(header) if h.startswith('курс')), None)
        cip = next((i for i, h in enumerate(header) if 'cip' in h and 'usd' in h), None)
        cost = next((i for i, h in enumerate(header) if 'себест' in h), None)
        if None in (rate, cip, cost):
            continue
        for start in range(end, len(cells) - len(header) + 1, len(header)):
            row = cells[start:start + len(header)]
            if not re.fullmatch(r'\d{3,6}/[\d-]+', row[0]):
                break
            r, usd, total = _number(row[rate]), _number(row[cip]), _number(row[cost])
            if r and usd and total and total < usd * r * 0.99:
                issues.append(_issue('contradiction', 'fx_rate_on_entry', doc['document_id'],
                                     [{'document_id': doc['document_id'], 'page': page['number'], 'quote': row[rate]},
                                      {'document_id': doc['document_id'], 'page': page['number'], 'quote': row[cost]}],
                                     f'Landed cost {row[cost]} ex VAT is below the customs value {usd:,.2f} USD x rate {row[rate]} = {usd * r:,.0f} UZS in entry {row[0]}; '
                                     'a wrong exchange rate was probably applied. Recalculate with the official rate on the customs date.'))
    return issues


def _registry_issues(docs, as_of):
    if as_of is None:
        return []
    records = []
    for doc in docs:
        for table in doc.get('tables', []):
            rows = table['rows']
            if not rows or not any(re.search(r'реестровая запись', str(c), re.I) for c in rows[0]):
                continue
            head = [str(c).casefold() for c in rows[0]]
            number = next(i for i, h in enumerate(head) if 'реестровая' in h)
            start = next((i for i, h in enumerate(head) if 'действует с' in h), None)
            product = next((i for i, h in enumerate(head) if 'препарат' in h), None)
            for row in rows[1:]:
                try:
                    effective = date.fromisoformat(normalize_date(str(row[start]))) if start is not None else None
                except (ValueError, IndexError):
                    continue
                if effective:
                    records.append((str(row[number]), str(row[product]) if product is not None else '', effective))
    issues = []
    for doc in docs:
        for page in doc['pages']:
            title = next(iter(_cells(page['text'])), '')
            match = re.search(r'№\s*(\S+)', title)
            if not match or not re.search(r'реестр|registry', title, re.I):
                continue
            ident = match.group(1)
            current = next((r for r in records if r[0] == ident), None)
            if not current:
                continue
            newer = [r for r in records if r[1] == current[1] and current[2] < r[2] <= as_of]
            if newer:
                latest = max(newer, key=lambda r: r[2])
                issues.append(_issue('stale', 'registry_record', doc['document_id'],
                                     [{'document_id': doc['document_id'], 'page': page['number'], 'quote': ident}],
                                     f'Registry extract {ident} was superseded by {latest[0]} effective {latest[2].isoformat()}; '
                                     'its chchn_with_vat_uzs and other prices may be out of date.'))
    return issues


def money_records(documents, *, as_of: date | None = None):
    """Return (records for check_money, additional issues). Documents: id, doc_class, pages, tables."""
    records, issues = [], []
    for doc in documents:
        if doc.get('doc_class') == 'supplier_quote':
            records.extend(_quote_records(doc))
        elif doc.get('doc_class') == 'contract':
            records.extend(_contract_records(doc))
        if doc.get('doc_class') not in (None, 'irrelevant', 'unknown'):
            issues.extend(_unit_outliers(doc))
        if doc.get('doc_class') == 'customs':
            issues.extend(_customs_cost_issues(doc))
    issues.extend(_registry_issues([d for d in documents if d.get('doc_class') == 'registry_extract'], as_of))
    return records, issues
