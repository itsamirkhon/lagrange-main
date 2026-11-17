import asyncio
import httpx
from lagrange_rx.api import create_app


def test_upload_review_and_commit(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            response = await client.post('/ingest/batches', files=[('files', ('sales.csv', 'Месяц,Количество\n2026-01,10\n'.encode(), 'text/csv'))])
            assert response.status_code == 200
            batch = (await client.get('/ingest/batches/' + response.json()['batch_id'])).json()
            assert batch['documents'][0]['document_id']
            assert batch['proposed_instances']
            assert all(item['status'] == 'extracted_unconfirmed' for item in batch['proposed_instances'])
            assert (await client.post('/ingest/batches/' + batch['batch_id'] + '/commit')).json()['committed'] == 0
            question = batch['review_queue'][0]
            assert (await client.post('/ingest/review/' + question['question_id'], json={'action': 'confirm'})).status_code == 200
            result = (await client.post('/ingest/batches/' + batch['batch_id'] + '/commit')).json()
            assert result['committed'] == 0
            assert result['incomplete']
            assert result['target'] == 'ClientGraph'
    asyncio.run(run())


def test_unknown_file_rejected(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            response = await client.post('/ingest/batches', files=[('files', ('bad.exe', b'abc', 'application/octet-stream'))])
            assert response.status_code == 400
    asyncio.run(run())


def test_zip_upload_exposes_documents_and_page(tmp_path):
    import io, zipfile
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, 'w') as z:
        z.writestr('sales.csv', 'Месяц,Количество\n2026-01,10\n')
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            response = await client.post('/ingest/batches', files=[('files', ('batch.zip', archive.getvalue(), 'application/zip'))])
            assert response.status_code == 200
            batch = (await client.get('/ingest/batches/' + response.json()['batch_id'])).json()
            assert len(batch['documents']) == 1
            page = await client.get('/ingest/documents/' + batch['documents'][0]['document_id'] + '/page/1.png')
            assert page.status_code == 200 and page.headers['content-type'] == 'image/png'
    asyncio.run(run())


def test_pipeline_classifies_and_rejects_unevidenced_model_output(tmp_path):
    from types import SimpleNamespace
    class Scorer:
        def decide(self, text, questions):
            return [SimpleNamespace(top='supplier_quote', confidence=.91)]
    class Extractor:
        def extract(self, doc_class, pages, **kwargs):
            assert doc_class == 'supplier_quote'
            return ({'unit_price': {'raw': '123', 'evidence': {'page': 1, 'quote': 'Price 123'}},
                     'moq': {'raw': '999', 'evidence': {'page': 1, 'quote': 'MOQ 999'}}}, [], None)
    async def run():
        app = create_app(tmp_path, scorer=Scorer(), extractor=Extractor())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            response = await client.post('/ingest/batches', files=[('files', ('quote.txt', b'Price 123', 'text/plain'))])
            result = (await client.get('/ingest/batches/' + response.json()['batch_id'])).json()
            assert result['documents'][0]['doc_class'] == 'supplier_quote'
            assert any('Quote.unit_price' in i['fields'] for i in result['proposed_instances'])
            assert not any('Quote.moq' in i['fields'] for i in result['proposed_instances'])
            assert any(i['issue_type'] == 'unverified_evidence' for i in result['issues'])
    asyncio.run(run())


def test_duplicate_is_flagged_and_irrelevant_is_not_extracted(tmp_path):
    from types import SimpleNamespace
    class Scorer:
        def decide(self, text, questions):
            return [SimpleNamespace(top='irrelevant', confidence=.99)]
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path, scorer=Scorer())), base_url='http://test') as client:
            files = [('files', ('one.csv', b'Month,Qty\n2026-01,3')), ('files', ('two.csv', b'Month,Qty\n2026-01,3'))]
            response = await client.post('/ingest/batches', files=files)
            result = (await client.get('/ingest/batches/' + response.json()['batch_id'])).json()
            assert not result['proposed_instances']
            assert any(issue['issue_type'] == 'duplicate' for issue in result['issues'])
    asyncio.run(run())


def test_rule_catalog_available_through_api(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            response = await client.get('/ontology/rules')
            assert response.status_code == 200
            assert any(r['rule_id'] == 'TRUST-NO-UNCONFIRMED' for r in response.json()['rules'])
    asyncio.run(run())


def test_batch_finds_arithmetic_in_credit_schedule(tmp_path):
    async def run():
        content = 'month,monthly_interest_uzs\n2026-01,10\n2026-02,20\nTOTAL,27\n'.encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            response = await client.post('/ingest/batches', files=[('files', ('credit.csv', content))])
            result = (await client.get('/ingest/batches/' + response.json()['batch_id'])).json()
            assert any(i['issue_type'] == 'arithmetic' for i in result['issues'])
    asyncio.run(run())


def test_table_review_groups_and_supports_single_cell_correction(tmp_path):
    async def run():
        rows = 'Месяц,Количество\n2026-01,10\n2026-02,11\n2026-03,12\n'.encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            created = (await client.post('/ingest/batches', files=[('files', ('sales.csv', rows))])).json()
            batch = (await client.get('/ingest/batches/' + created['batch_id'])).json()
            queue = batch['review_queue']
            assert len(queue) == 2
            question = next(q for q in queue if q['field'] == 'SalesRecord.qty_packs')
            assert len(question['items']) == 3
            target = question['items'][1]['instance_id']
            response = await client.post('/ingest/review/' + question['question_id'], json={'action': 'correct', 'value': '13', 'instance_id': target})
            assert response.status_code == 200
            updated = (await client.get('/ingest/batches/' + created['batch_id'])).json()
            values = {i['id']: i for i in updated['proposed_instances']}
            assert values[target]['fields']['SalesRecord.qty_packs']['corrected_value'] == '13'
            assert all(values[i['instance_id']]['status'] == 'extracted_unconfirmed' for i in question['items'] if i['instance_id'] != target)
    asyncio.run(run())


def test_pnl_reports_missing_information_instead_of_guessing(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            response = await client.post('/ingest/batches', files=[('files', ('a.txt', b'hello'))])
            batch_id = response.json()['batch_id']
            await client.post('/ingest/batches/' + batch_id + '/commit')
            result = await client.post('/ingest/batches/' + batch_id + '/pnl', json={'product': 'A', 'period': '2026-05', 'required_expenses': ['distribution'], 'expenses_attributed_to_product': False})
            assert result.status_code == 200
            assert result.json()['margin'] is None
            assert len(result.json()['missing']) >= 2
    asyncio.run(run())


def test_expense_rows_remain_proposals_with_product_context(tmp_path):
    async def run():
        content = 'Месяц,Статья,Сумма UZS,Номенклатура\n2026-01,distribution,1200,Drug A\n'.encode()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            batch_id = (await client.post('/ingest/batches', files=[('files', ('costs.csv', content))])).json()['batch_id']
            batch = (await client.get('/ingest/batches/' + batch_id)).json()
            cost = [p for p in batch['proposed_instances'] if p['type'] == 'CostItem']
            assert len(cost) == 3
            assert all(p['status'] == 'extracted_unconfirmed' for p in cost)
            assert all(p['source_product']['raw'] == 'Drug A' for p in cost)
            result = (await client.post('/ingest/batches/' + batch_id + '/commit')).json()
            assert result['committed'] == 0
    asyncio.run(run())


def test_money_check_api_returns_unconfirmed_currency_and_staleness(tmp_path):
    from types import SimpleNamespace
    class Scorer:
        def decide(self, text, questions):
            return [SimpleNamespace(top='supplier_quote', confidence=.9)]
    class Extractor:
        def extract(self, doc_class, pages, **kwargs):
            return ({'unit_price': {'raw': '123', 'evidence': {'page': 1, 'quote': 'Price 123'}},
                     'valid_until': {'raw': '2025-01-01', 'evidence': {'page': 1, 'quote': 'Valid 2025-01-01'}}}, [], None)
    async def run():
        app = create_app(tmp_path, scorer=Scorer(), extractor=Extractor())
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            batch = (await client.post('/ingest/batches', files=[('files', ('q.txt', b'Price 123\nValid 2025-01-01'))])).json()['batch_id']
            response = await client.post('/ingest/batches/' + batch + '/money-checks', json={'as_of': '2026-01-01'})
            assert response.status_code == 200
            assert {i['issue_type'] for i in response.json()['issues']} >= {'currency_ambiguous', 'stale'}
    asyncio.run(run())


def test_money_check_fetches_cbu_only_when_requested(tmp_path):
    from types import SimpleNamespace
    from datetime import date
    from decimal import Decimal
    from lagrange_rx.ingest.money_checks import OfficialRate
    called = []
    def provider(currency, day):
        called.append((currency, day))
        return OfficialRate(currency, day, day, Decimal('12000'), 'https://cbu.uz/')
    class Scorer:
        def decide(self, text, questions): return [SimpleNamespace(top='customs', confidence=.9)]
    class Extractor:
        def extract(self, doc_class, pages, **kwargs):
            return ({'date': {'raw': '2026-01-03', 'evidence': {'page': 1, 'quote': 'Date 2026-01-03'}}, 'currency': {'raw': 'USD', 'evidence': {'page': 1, 'quote': 'Currency USD'}}, 'fx_rate_used': {'raw': '13000', 'evidence': {'page': 1, 'quote': 'Rate 13000'}}}, [], None)
    async def run():
        app = create_app(tmp_path, scorer=Scorer(), extractor=Extractor(), fx_provider=provider)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
            batch = (await client.post('/ingest/batches', files=[('files', ('a.txt', b'Date 2026-01-03\nCurrency USD\nRate 13000'))])).json()['batch_id']
            response = await client.post('/ingest/batches/' + batch + '/money-checks', json={'as_of': '2026-02-01', 'fetch_official_rates': True})
            assert response.status_code == 200
            assert called == [('USD', date(2026, 1, 3))]
            assert any(i['field'] == 'fx_rate_on_entry' and i['issue_type'] == 'contradiction' for i in response.json()['issues'])
            saved = (await client.get('/ingest/batches/' + batch)).json()
            assert any(i.get('check_source') == 'money_checks' for i in saved['issues'])
    asyncio.run(run())


def test_money_checks_do_not_claim_unavailable_fx_was_checked(tmp_path):
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path)), base_url='http://test') as client:
            batch = (await client.post('/ingest/batches', files=[('files', ('a.txt', b'No customs data'))])).json()['batch_id']
            response = await client.post('/ingest/batches/' + batch + '/money-checks', json={'as_of': '2026-01-01'})
            assert response.json()['fx_coverage']['status'] == 'not_checked'
    asyncio.run(run())


def test_missing_currency_is_added_during_ingest(tmp_path):
    from types import SimpleNamespace
    class Scorer:
        def decide(self, text, questions): return [SimpleNamespace(top='supplier_quote', confidence=.9)]
    class Extractor:
        def extract(self, doc_class, pages, **kwargs):
            return ({'unit_price': {'raw': '123', 'evidence': {'page': 1, 'quote': 'Price 123'}}}, [], None)
    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=create_app(tmp_path, scorer=Scorer(), extractor=Extractor())), base_url='http://test') as client:
            batch_id = (await client.post('/ingest/batches', files=[('files', ('q.txt', b'Price 123'))])).json()['batch_id']
            batch = (await client.get('/ingest/batches/' + batch_id)).json()
            assert any(i['issue_type'] == 'currency_ambiguous' for i in batch['issues'])
    asyncio.run(run())
