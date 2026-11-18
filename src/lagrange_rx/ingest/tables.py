"""Conservative table mapping: only explicit, unambiguous headings are accepted.

Rows retain their source evidence and are proposals, not confirmed graph objects.
The caller must classify the document and review values before using them.
"""
from collections import Counter
import re
import unicodedata


# A header alone does not prove a document's class; restrict generic labels to
# a sales-shaped table rather than treating every price or quantity as a sale.
_SALES = {
    'period': ('месяц', 'month', 'period', 'период', 'oy'),
    'region': ('регион', 'region', 'hudud / ҳудуд', 'hudud', 'ҳудуд', 'region_id'),
    'channel': ('канал', 'канал сбыта', 'channel', 'kanal'),
    'product': ('sku', 'препарат', 'наименование препарата', 'номенклатура', 'product', 'sku_id'),
    'lot': ('серия', 'lot', 'lot number', 'lot_number'),
    'qty_packs': ('количество', 'кол-во', 'упаковок', 'qty', 'qty, packs', 'qty packs', 'qty_packs', 'sotuv (up.)', 'сотув (уп.)'),
    'price_ex_vat': ('price_ex_vat_uzs', 'цена без ндс', 'price ex vat'),
    # Revenue is a source measure, not an asserted ontology property.
    'revenue_ex_vat_uzs': ('выручка без ндс', 'revenue ex vat', 'revenue_ex_vat_uzs', "summa (so'm)"),
}


def _key(value):
    return re.sub(r'\s+', ' ', unicodedata.normalize('NFKC', str(value)).casefold().strip())


_LOOKUP = {_key(alias): f'SalesRecord.{field}' for field, aliases in _SALES.items() for alias in aliases}


def extract_table(table, document_id):
    """Map a sales-shaped Table. Return header_row, row proposals and unmapped columns.

    header_row is zero-based, or None. Mapping is exact after whitespace/case
    normalization; duplicate headings resolving to the same field are rejected.
    Quotes are the exact nonempty cell strings from the parsed page text.
    """
    best_index, best_fields, best_score = None, [], 0
    for index, row in enumerate(table.rows):
        fields = [_LOOKUP.get(_key(cell)) for cell in row]
        unique = set(fields) - {None}
        # Require quantity and a contextual sales dimension; numeric data rows
        # cannot become headers just because they happen to contain common words.
        if 'SalesRecord.qty_packs' not in unique or len(unique & {'SalesRecord.period', 'SalesRecord.region', 'SalesRecord.channel', 'SalesRecord.product'}) < 1:
            continue
        if len(unique) > best_score:
            best_index, best_fields, best_score = index, fields, len(unique)
    if best_index is None:
        return {'header_row': None, 'rows': [], 'unmapped_columns': []}
    headers = table.rows[best_index]
    counts = Counter(field for field in best_fields if field)
    valid = {index: field for index, field in enumerate(best_fields) if field and counts[field] == 1}
    unmapped = [{'index': index, 'header': str(header), 'reason': 'duplicate_mapping' if field else 'unknown'}
                for index, (header, field) in enumerate(zip(headers, best_fields))
                if str(header).strip() and index not in valid]
    rows = []
    for row_index, row in enumerate(table.rows[best_index + 1:], best_index + 1):
        fields = {}
        for index, field in valid.items():
            if index >= len(row) or row[index] is None or not str(row[index]).strip():
                continue
            raw = str(row[index])
            fields[field] = {'raw': raw, 'evidence': {'document_id': document_id, 'page': table.page, 'quote': raw}}
        if fields:
            rows.append({'row': row_index, 'type': 'SalesRecord', 'fields': fields})
    return {'header_row': best_index, 'rows': rows, 'unmapped_columns': unmapped}
