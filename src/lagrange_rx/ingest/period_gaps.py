"""Report gaps between observed months without inferring document coverage."""
import re

from .tables import _key, _SALES

_MONTH = re.compile(r'\d{4}-(?:0[1-9]|1[0-2])\Z')
_HEADERS = {_key(value) for value in _SALES['period']}


def _index(month):
    year, number = map(int, month.split('-'))
    return year * 12 + number - 1


def _label(index):
    return f'{index // 12:04d}-{index % 12 + 1:02d}'


def check_period_gaps(table, document_id):
    """Flag internal gaps as review issues, not proof of corrupted source data.

    An invalid period, ambiguous header or absence of two distinct months
    prevents inference. Values outside the observed span are never guessed.
    """
    for start, row in enumerate(table.rows):
        columns = [i for i, value in enumerate(row) if _key(value) in _HEADERS]
        if not columns:
            continue
        if len(columns) != 1:
            return []
        column = columns[0]
        periods = set()
        for data in table.rows[start + 1:]:
            if not any(cell is not None and str(cell).strip() for cell in data):
                continue
            if any(_key(cell) in {'total', 'итого', 'жами'} for cell in data):
                continue
            value = str(data[column]).strip() if column < len(data) and data[column] is not None else ''
            if not _MONTH.fullmatch(value):
                return []
            periods.add(_index(value))
        ordered = sorted(periods)
        issues = []
        for left, right in zip(ordered, ordered[1:]):
            for missing in range(left + 1, right):
                quotes = [_label(left), _label(right)]
                issues.append({'issue_type': 'missing', 'severity': 'medium',
                               'affected': [document_id],
                               'detail': f'Missing period {_label(missing)} between observed months; verify coverage',
                               'evidence': [{'document_id': document_id, 'page': table.page, 'quote': quote} for quote in quotes]})
        return issues
    return []
