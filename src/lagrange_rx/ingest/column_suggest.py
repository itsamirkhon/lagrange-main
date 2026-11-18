"""Q2.column: propose an ontology field for sales-table columns the synonym dictionary does not know.

The suggestion is attached to the unmapped-column issue for a human; the rows are not remapped.
"""
from __future__ import annotations

from .decisions import DecisionQuestion, review_policy
from .questions import load_templates
from .tables import _SALES

FIELDS = [f'SalesRecord.{name}' for name in _SALES]
SAMPLE_ROWS = 5


def suggest_columns(scorer, table, header_row, unmapped):
    """Return {column index: suggestion} for columns the dictionary left as 'unknown'; {} if the scorer fails."""
    columns = [c for c in unmapped if c.get('reason') == 'unknown' and header_row is not None]
    if not columns:
        return {}
    instructions = load_templates('q2')['Q2.column']
    questions, states = [], []
    for c in columns:
        samples = [str(row[c['index']]) for row in table.rows[header_row + 1:header_row + 1 + SAMPLE_ROWS]
                   if c['index'] < len(row) and row[c['index']] not in (None, '')]
        questions.append(DecisionQuestion(qid=f"col{c['index']}", kind='choice', instructions=instructions, options=FIELDS + ['ignore', 'unknown'],
                                          descriptions={f: f.split('.')[1].replace('_', ' ') for f in FIELDS}))
        states.append(f"column {c['index']}: header={c['header']!r}; sample values={samples}")
    other_headers = [str(h) for h in table.rows[header_row] if str(h).strip()]
    state = 'Sales spreadsheet with headers: ' + ', '.join(other_headers) + '\n' + '\n'.join(states)
    try:
        answers = scorer.decide(state, questions)
    except Exception:
        return {}
    return {c['index']: {'field': a.top, 'confidence': round(a.confidence, 4), 'model_id': a.model_id,
                         'policy': review_policy(a.confidence, a.top, money=False)}
            for c, a in zip(columns, answers)}
