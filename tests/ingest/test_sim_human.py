"""The simulated human answers from simulation truth and logs every answer (eval only)."""
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path

import pytest

from eval.sim_human import SimHuman
from lagrange_rx.ingest.offline.build import build_draft
from lagrange_rx.ingest.offline.confirm import commit

L0 = Path(__file__).resolve().parents[2] / 'sim' / 'worlds' / 'L0' / 'documents'
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)


@pytest.fixture(scope='module')
def draft():
    return build_draft(L0, now=NOW)


def test_answers_every_question_and_logs_each(draft):
    human = SimHuman()
    answers = human.answer_all(draft.questions)
    assert len(answers) == len(human.log) == human.load == len(draft.questions)
    assert all(entry['basis'] for entry in human.log)
    groups = {a['question_id']: a['answer'] for a in answers}
    assert all(v['decision'] == 'confirm' for k, v in groups.items() if k.startswith('confirm_group'))


def test_cost_attribution_comes_from_the_books(draft):
    answers = {a['question_id']: a['answer'] for a in SimHuman().answer_all(draft.questions)}
    own = answers['cost_attribution:Kardiozan 10 мг №30']
    pool = answers['cost_attribution:Общий портфель препаратов']
    assert own['allocation'] == 'direct' and own['product'].startswith('kardiozan')
    assert pool['allocation'] == 'shared_by_volume' and own['product'] not in pool['scope'] and len(pool['scope']) == 6


def test_summary_that_disagrees_with_the_books_is_rejected(draft):
    bad = deepcopy([q for q in draft.questions if q['question_id'] == 'confirm_group:sales'][0])
    bad['facts']['packs'] += 1
    answer = SimHuman().answer(bad)['answer']
    assert answer['decision'] == 'reject' and 'packs' in answer['problems'][0]


def test_committed_graph_with_simulated_human_is_valid(draft):
    graph, report = commit(draft, SimHuman().answer_all(draft.questions), now=NOW)
    graph.for_calculation()
    assert report['dropped'] == []
    assert {c.allocation for c in graph.cost_items} == {'direct', 'shared_by_volume'}
