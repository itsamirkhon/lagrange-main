"""Evidence-backed consistency checks for recognizable tables."""
from decimal import Decimal, InvalidOperation
import re

from .checks import Observation, check_sum


def check_table_quality(table, document_id):
    """Check a stated interest total only when every monthly value is present."""
    for index, header in enumerate(table.rows):
        headers = [str(x).strip().casefold() for x in header]
        month_cols = [i for i, value in enumerate(headers) if value in ('month', 'месяц')]
        amount_cols = [i for i, value in enumerate(headers) if value in ('monthly_interest_uzs', 'проценты uzs')]
        if len(month_cols) != 1 or len(amount_cols) != 1:
            continue
        month, amount = month_cols[0], amount_cols[0]
        observations, total = [], None
        for row in table.rows[index + 1:]:
            if max(month, amount) >= len(row):
                return []
            period, raw = str(row[month]).strip(), str(row[amount]).strip()
            if not period:
                continue
            if any(str(cell).strip().casefold() in ('total', 'итого') for cell in row[:amount]):
                if total is not None:
                    return []
                target = 'total'
            elif re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])', period):
                target = 'line'
            else:
                return []
            try:
                number = Decimal(raw)
                if not number.is_finite():
                    return []
            except InvalidOperation:
                return []
            obs = Observation(f'{document_id}:{period}', number, [{'document_id': document_id, 'page': table.page, 'quote': raw}])
            if target == 'total':
                total = obs
            else:
                observations.append(obs)
        if total and observations:
            issue = check_sum(total, observations)
            if issue:
                issue['field'] = 'total_interest_uzs'
                return [issue]
            return []
        return []
    return []
