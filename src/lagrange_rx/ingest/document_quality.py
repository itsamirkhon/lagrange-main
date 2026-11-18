"""Document-backed VAT and unit questions; never infer conversion factors.

Input is parsed page text and tabular cells from the current batch, not a
simulation manifest. Lot comparison requires an exact explicit batch code.
"""
from decimal import Decimal, InvalidOperation
import re

_VAT_MISSING = re.compile(r'(?:ндс\s+не\s+указан(?:а|о)?|vat\s+(?:not\s+specified|unknown))', re.I)
_LOT = re.compile(r'(?:партия|batch|lot)\s*:\s*([^\n]+)', re.I)
_QUANTITY = re.compile(r'(?:количество|quantity)\s*:\s*\n?\s*([\d.,]+)\s*(?:\(([^)]+)\))?', re.I)


def _evidence(document_id, page, quote):
    return {'document_id': document_id, 'page': page, 'quote': quote, 'text_span': quote}


def _issue(kind, affected, evidence, detail):
    return {'issue_type': kind, 'severity': 'warning', 'affected': affected,
            'evidence': evidence, 'detail': detail, 'resolution': 'unresolved',
            'field': {'vat_ambiguous': 'vat_status', 'unit_ambiguous': 'initial_qty'}[kind]}


def _decimal(value):
    try:
        number = Decimal(str(value).strip().replace(',', '.'))
        return number if number.is_finite() and number > 0 else None
    except InvalidOperation:
        return None


def _stock_rows(documents):
    for document in documents:
        if document.get('doc_class') != 'stock':
            continue
        for table in document.get('tables', []):
            rows = table.get('rows', [])
            if not rows:
                continue
            columns = [str(value).strip().casefold() for value in rows[0]]
            if columns.count('lot_code') != 1 or columns.count('initial_qty') != 1:
                continue
            code_index, qty_index = columns.index('lot_code'), columns.index('initial_qty')
            for row in rows[1:]:
                if max(code_index, qty_index) < len(row):
                    code, raw = str(row[code_index]).strip(), str(row[qty_index]).strip()
                    value = _decimal(raw)
                    if code and value is not None:
                        yield code.casefold(), value, document['document_id'], table.get('page', 1), '\t'.join(str(cell) for cell in row)


def analyze_document_quality(documents):
    issues = []
    stock = list(_stock_rows(documents))
    for document in documents:
        doc_id, kind = document['document_id'], document.get('doc_class')
        for page in document.get('pages', []):
            text, page_number = page.get('text', ''), page.get('number', 1)
            if kind in ('supplier_quote', 'carrier_quote', 'contract', 'invoice'):
                match = _VAT_MISSING.search(text)
                if match:
                    issues.append(_issue('vat_ambiguous', [doc_id], [_evidence(doc_id, page_number, match.group())],
                                         'Статус НДС явно не указан; необходимо подтверждение.'))
            if kind != 'waybill':
                continue
            lot, qty = _LOT.search(text), _QUANTITY.search(text)
            if not lot or not qty or not qty.group(2):
                continue
            label = lot.group(1).strip()
            code_match = re.search(r'\(([^()]+)\)', label)
            code = code_match.group(1).strip() if code_match else label
            value = _decimal(qty.group(1))
            matches = [row for row in stock if row[0] == code.casefold()]
            if value is None or len(matches) != 1:
                continue
            _, stock_value, stock_id, stock_page, stock_quote = matches[0]
            if max(value, stock_value) != min(value, stock_value) * 10:
                continue
            issues.append(_issue('unit_ambiguous', [doc_id, stock_id],
                [_evidence(doc_id, page_number, lot.group()), _evidence(doc_id, page_number, qty.group()),
                 _evidence(stock_id, stock_page, stock_quote)],
                'Количество одной партии различается в 10 раз; проверьте единицы и коэффициент упаковки.'))
    return issues
