"""Question bank loader: questions live in YAML, not in code."""
from pathlib import Path

import yaml

from ..decisions import DecisionQuestion

_DIR = Path(__file__).resolve().parent


def load_questions(name: str) -> dict[str, DecisionQuestion]:
    data = yaml.safe_load((_DIR / f'{name}.yaml').read_text(encoding='utf-8'))
    questions = {}
    for item in data['questions']:
        item = dict(item)
        if 'options' not in item and item.get('descriptions'):
            item['options'] = list(item['descriptions'])
        questions[item['qid']] = DecisionQuestion(**item)
    return questions


def load_templates(name: str) -> dict[str, str]:
    return {qid: ' '.join(text.split()) for qid, text in yaml.safe_load((_DIR / f'{name}.yaml').read_text(encoding='utf-8'))['templates'].items()}
