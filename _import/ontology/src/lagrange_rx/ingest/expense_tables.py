"""Read expense rows without assuming their allocation, layer or cost driver."""
from collections import Counter

from .tables import _key

_HEADERS = {
    'месяц': 'period', 'период': 'period', 'period': 'period',
    'статья': 'category', 'category': 'category',
    'сумма uzs': 'amount_uzs', 'amount_uzs': 'amount_uzs',
    'номенклатура': 'product', 'препарат': 'product', 'product': 'product',
}
_REQUIRED = {'period', 'category', 'amount_uzs'}


def extract_expenses(table, document_id):
    """Return unconfirmed rows with exact cell evidence and optional product context."""
    for header_row, headers in enumerate(table.rows):
        mapped = [_HEADERS.get(_key(value)) for value in headers]
        counts = Counter(value for value in mapped if value)
        if not all(counts[field] == 1 for field in _REQUIRED):
            continue
        indices = {field: index for index, field in enumerate(mapped) if field and counts[field] == 1}
        rows = []
        for index, row in enumerate(table.rows[header_row + 1:], header_row + 1):
            values = {}
            for field, column in indices.items():
                if column < len(row) and row[column] is not None and str(row[column]).strip():
                    raw = str(row[column])
                    values[field] = {'raw': raw, 'evidence': {'document_id': document_id, 'page': table.page, 'quote': raw}}
            if not _REQUIRED.issubset(values):
                continue
            item = {'row': index, 'type': 'CostItem',
                    'fields': {f'CostItem.{field}': values[field] for field in _REQUIRED}}
            if 'product' in values:
                item['product'] = values['product']
            rows.append(item)
        return {'header_row': header_row, 'rows': rows}
    return {'header_row': None, 'rows': []}
