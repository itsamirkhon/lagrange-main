"""Conservative comparisons of explicitly labelled fields in parsed documents.

These are possible problems for review, never automatic corrections. The
input contains parsed pages, not simulation labels or ground truth.
"""
from decimal import Decimal
from itertools import combinations
import re


def _find(document, pattern):
    for page, text in document['pages'].items():
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            return match, {'document_id': document['document_id'], 'page': page, 'quote': match.group(0)}
    return None, None


def _contract(document):
    number, _ = _find(document, r'(?:КОНТРАКТ\s+ПОСТАВКИ|ДОГОВОР)\s*№\s*([\w-]+)')
    price, evidence = _find(document, r'Цена(?:\s+CIP)?\s*:\s*(\d+(?:[.,]\d+)?)\s*(USD|EUR|UZS)\b')
    if not number or not price:
        return None
    return number.group(1).casefold(), Decimal(price.group(1).replace(',', '.')), price.group(2).upper(), evidence


def _tabular_waybill(document):
    heading, _ = _find(document, r'СКЛАДСКАЯ\s+НАКЛАДНАЯ\s*№')
    date_invoice, first = _find(document, r'Дата\s*:\s*(\d{4}-\d{2}-\d{2})\s*\|\s*Инвойс\s*:\s*([\w-]+)')
    lines, second = _find(document, r'Позиции\s*\nСерия\s*\nПрепарат\s*\nКоличество\s*\nСрок\s+годн\.\s*\n([\s\S]*?)(?=\nНакладная\s|\Z)')
    if not heading or not date_invoice or not lines:
        return None
    body = lines.group(1).strip()
    if not re.search(r'\d{4}-\d{2}-\d{2}', body) or not re.search(r'\d+(?:[.,]\d+)?\s*\(?(?:упак|блистер|короб)', body, re.I):
        return None
    key = ('tabular', date_invoice.group(1), date_invoice.group(2).casefold(), re.sub(r'\s+', ' ', body).casefold())
    return key, [first, second], None


def _waybill(document):
    heading, _ = _find(document, r'НАКЛАДНАЯ\s+СКЛАДА\s*№\s*([\w-]+)')
    if not heading:
        return _tabular_waybill(document)
    patterns = (r'Дата\s+при[её]мки\s*:\s*(\d{4}-\d{2}-\d{2})',
                r'Партия\s*:\s*[^\n]*?\(([A-ZА-Я0-9][\w-]+)\)',
                r'Срок\s+годности\s*:\s*(\d{4}-\d{2}-\d{2})',
                r'Количество\s*:\s*(\d+(?:[.,]\d+)?\s*\(?[А-Яа-яA-Za-z]+\)?)')
    values = [_find(document, pattern) for pattern in patterns]
    if any(match is None for match, _ in values):
        return None
    warehouse, warehouse_evidence = _find(document, r'Склад\s*:\s*([^\n|]+)')
    key = tuple(re.sub(r'\s+', ' ', match.group(1).strip()).casefold() for match, _ in values)
    return key, [evidence for _, evidence in values] + ([warehouse_evidence] if warehouse else []), (warehouse.group(1).strip().casefold() if warehouse else None)



def _issue(kind, field, left, right, evidence, detail):
    return {'issue_type': kind, 'severity': 'warning', 'field': field,
            'affected': [left['document_id'], right['document_id']],
            'evidence': evidence, 'detail': detail, 'resolution': 'unresolved'}


def analyze_observed_documents(documents):
    """Find candidate conflicts and duplicates using only explicit page text."""
    records = [(document, _contract(document), _waybill(document)) for document in documents]
    issues = []
    for (left, lc, lw), (right, rc, rw) in combinations(records, 2):
        if left['document_id'] == right['document_id']:
            continue
        if lc and rc and lc[0] == rc[0] and lc[2] == rc[2] and lc[1] != rc[1]:
            issues.append(_issue('contradiction', 'unit_price', left, right, [lc[3], rc[3]],
                                 'Conflicting unit_price for the same contract number and CIP currency; verify versions and effective dates.'))
        if lw and rw and lw[0] == rw[0] and (not lw[2] or not rw[2] or lw[2] == rw[2]):
            issues.append(_issue('duplicate', 'lot_code', left, right, lw[1] + rw[1],
                                 'Possible duplicate warehouse receipt: external lot code, receipt date, expiry and quantity match.'))
    return issues
