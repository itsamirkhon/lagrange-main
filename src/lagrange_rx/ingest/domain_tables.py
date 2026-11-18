"""Conservative extraction from classified stock and customs tables.

Values are unconfirmed proposals with exact cell evidence; source system keys
are not promoted to ontology identities or relations.
"""
from collections import Counter

from .tables import _key

_SCHEMAS = {
    'stock': {
        'type': 'Lot',
        'headers': {'lot_code': 'Lot.lot_number', 'expiry_date': 'Lot.expiry_date',
                    'initial_qty': 'Lot.qty_packs', 'remaining_qty': 'Lot.remaining_packs'},
        'required': {'Lot.lot_number', 'Lot.expiry_date'},
    },
    'customs': {
        'type': 'CustomsEntry',
        'headers': {'clearance_date': 'CustomsEntry.entry_date', 'fx_rate_uzs': 'CustomsEntry.fx_rate_on_entry'},
        'required': {'CustomsEntry.entry_date', 'CustomsEntry.fx_rate_on_entry'},
    },
}


def extract_domain_table(table, document_id, doc_class):
    schema = _SCHEMAS.get(doc_class)
    if schema is None:
        return {'header_row': None, 'rows': [], 'unmapped_columns': []}
    for header_index, headers in enumerate(table.rows):
        fields = [schema['headers'].get(_key(cell)) for cell in headers]
        if not schema['required'].issubset(set(fields)):
            continue
        counts = Counter(field for field in fields if field)
        valid = {i: field for i, field in enumerate(fields) if field and counts[field] == 1}
        unmapped = [{'index': i, 'header': str(header), 'reason': 'duplicate_mapping' if field else 'unknown'}
                    for i, (header, field) in enumerate(zip(headers, fields))
                    if header is not None and str(header).strip() and i not in valid]
        rows = []
        for row_index, row in enumerate(table.rows[header_index + 1:], header_index + 1):
            values = {}
            for i, field in valid.items():
                if i >= len(row) or row[i] is None or not str(row[i]).strip():
                    continue
                raw = str(row[i])
                values[field] = {'raw': raw, 'evidence': {'document_id': document_id, 'page': table.page, 'quote': raw}}
            if values:
                rows.append({'row': row_index, 'type': schema['type'], 'fields': values})
        return {'header_row': header_index, 'rows': rows, 'unmapped_columns': unmapped}
    return {'header_row': None, 'rows': [], 'unmapped_columns': []}
