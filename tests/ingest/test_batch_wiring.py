"""Classification and money checks run inside batch processing and surface as review issues."""
from datetime import date
import asyncio

import httpx

from lagrange_rx.api.ingest import create_app

QUOTE_LINES = ['COMMERCIAL QUOTATION KP-2025-001', 'Date: 2025-01-05 | Incoterms: CIP Tashkent', 'Items', 'Номер КП', 'Препарат', 'MOQ', 'Срок',
               'Цена CIP', 'Действует до', 'KP-2025-001', 'Kardiozan 10 мг', '10000', '22 дн.', '7,50', '2025-02-19']
CONTRACT_LINES = ['ДОГОВОР № CNT-SUP-2025-01', 'Подписан: 2025-01-05 | Действует до: 2025-12-31', 'Валюта:', 'USD', 'Цена:', '7.48 USD', 'MOQ:', '5000 уп.']


def text(lines):
    return '\n'.join(lines).encode('utf-8')


def run(tmp_path, files, **kwargs):
    async def go():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path, **kwargs)), base_url='http://test') as client:
            response = await client.post('/ingest/batches', files=[('files', f) for f in files])
            response.raise_for_status()
            return (await client.get('/ingest/batches/' + response.json()['batch_id'])).json()
    return asyncio.run(go())


def test_batch_classifies_deterministically_and_runs_money_checks(tmp_path):
    batch = run(tmp_path, [('KP-2025-001.txt', text(QUOTE_LINES)), ('CNT-1.txt', text(CONTRACT_LINES))], as_of=date(2026, 6, 1))
    classes = {d['filename']: d for d in batch['documents']}
    assert classes['KP-2025-001.txt']['doc_class'] == 'supplier_quote' and classes['KP-2025-001.txt']['class_method'] == 'deterministic'
    assert classes['CNT-1.txt']['doc_class'] == 'contract' and classes['CNT-1.txt']['class_confidence'] >= .9
    assert batch['as_of'] == '2026-06-01'
    kinds = {i['issue_type'] for i in batch['issues']}
    assert {'currency_ambiguous', 'stale', 'contradiction'} <= kinds  # C1, S1, X1 (quote 10000 vs contract 5000)
    x1 = [i for i in batch['issues'] if i['issue_type'] == 'contradiction' and i.get('field') == 'moq']
    assert len(x1) == 1 and len(x1[0]['affected']) == 2 and x1[0]['resolution'] == 'unresolved'


def test_unreadable_document_stays_unknown_and_replay_miss_is_not_fatal(tmp_path, monkeypatch):
    monkeypatch.setenv('LAGRANGE_AI_MODE', 'replay')
    batch = run(tmp_path, [('notes.txt', b'hello there')])
    assert batch['documents'][0]['doc_class'] == 'unknown'
    assert 'llm_unavailable' in batch['documents'][0]['class_signals']
    assert any(i['issue_type'] == 'classification_required' for i in batch['issues'])
