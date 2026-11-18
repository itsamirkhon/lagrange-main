"""Local ingest HTTP API. State is persisted under the configured data directory."""
from __future__ import annotations

import json
import hashlib
import os
from pathlib import Path
from datetime import date, datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel

from lagrange_rx.ingest.intake import IntakeError, IntakeStore
from lagrange_rx.ingest.parse import parse_document
from lagrange_rx.ingest.review import build_review_queue
from lagrange_rx.ingest.period_gaps import check_period_gaps
from lagrange_rx.ingest.document_quality import analyze_document_quality
from lagrange_rx.ingest.observed_document_checks import analyze_observed_documents
from lagrange_rx.ingest.table_quality import check_table_quality
from lagrange_rx.ingest.money_checks import check_money, money_records_from_proposals, cbu_rate_requests, fetch_cbu_rate
from lagrange_rx.ingest.expense_tables import extract_expenses
from lagrange_rx.ingest.tables import extract_table
from lagrange_rx.ingest.domain_tables import extract_domain_table
from lagrange_rx.ingest.checks import check_duplicates
from lagrange_rx.ingest.config import configured_extractor, FIELDS
from lagrange_rx.ingest.commit import commit_batch
from lagrange_rx.ingest.cross_document import analyze_cross_documents, check_quote_contract_moq
from lagrange_rx.ingest.money_fields import money_records
from lagrange_rx.pnl import calculate_pnl
from lagrange_rx.ontology import ClientGraph
from lagrange_rx.ingest.decisions import DecisionQuestion, HeuristicScorer
from lagrange_rx.ontology.rules import load_rules
from lagrange_rx.ingest.classification import DocumentClassifier
from lagrange_rx.ai.factory import build_client, build_decision_scorer, build_embedder
from lagrange_rx.ai.openrouter import ReplayMiss
from lagrange_rx.ingest.q0 import Q0Runner
from lagrange_rx.ingest.column_suggest import suggest_columns
from lagrange_rx.ingest.extract.extractor import validate_evidence


class ReviewAnswer(BaseModel):
    action: str
    value: str | None = None
    instance_id: str | None = None


class MoneyCheckRequest(BaseModel):
    as_of: date
    registry_max_age_days: int | None = None
    require_official_rate: bool = False
    fetch_official_rates: bool = False


class PnLRequest(BaseModel):
    product: str
    period: str
    required_expenses: set[str]
    expenses_attributed_to_product: bool = False


def _save(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix('.tmp')
    temp.write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    temp.replace(path)


def _load(path: Path) -> dict:
    if not path.is_file():
        raise HTTPException(404, 'Not found')
    return json.loads(path.read_text(encoding='utf-8'))


def make_router(data_dir: str | Path | None = None, *, scorer=None, extractor=None, fx_provider=None, as_of: date | None = None) -> tuple[APIRouter, Path]:
    """The /ingest/* router. Returns (router, data_root); state lives under the data root."""
    root = Path(data_dir or os.getenv('LAGRANGE_DATA_DIR', './data')).resolve()
    root.mkdir(parents=True, exist_ok=True)
    store = IntakeStore(root / 'documents')
    router = APIRouter()
    fx_provider = fx_provider or fetch_cbu_rate
    mode = os.getenv('LAGRANGE_AI_MODE', 'off')
    # Staleness (S1) is relative to an explicit calculation date; the default is today and is recorded in the batch.
    as_of = as_of or (date.fromisoformat(os.environ['LAGRANGE_AS_OF']) if os.getenv('LAGRANGE_AS_OF') else date.today())
    budget = float(os.getenv('LAGRANGE_AI_BUDGET_USD', '0'))
    # One client (one budget, one cache) serves the Decisions scorer and the embedder.
    ai_client = build_client(mode, root / 'cache' / 'ai', budget, 0.02) if mode in ('replay', 'openrouter') else None
    decision_scorer = None if scorer else build_decision_scorer(mode, None, client=ai_client)
    embedder = build_embedder(mode, None, client=ai_client)
    q0 = Q0Runner(decision_scorer) if decision_scorer else None
    classifier = DocumentClassifier(llm=q0, scorer=scorer)
    if extractor is None and mode != 'off':
        extractor = configured_extractor(root / 'cache', mode=mode, budget_usd=float(os.getenv('LAGRANGE_AI_BUDGET_USD', '0')))

    def batch_path(batch_id: str) -> Path:
        if not batch_id.isalnum():
            raise HTTPException(404, 'Not found')
        return root / 'batches' / (batch_id + '.json')

    @router.post('/ingest/batches')
    async def create_batch(files: list[UploadFile] = File(...)):
        if not files or len(files) > 100:
            raise HTTPException(413, "Batch must contain 1-100 files")
        total_bytes = 0
        batch_id = uuid4().hex
        batch = {'batch_id': batch_id, 'as_of': as_of.isoformat(), 'documents': [], 'proposed_instances': [], 'links': [], 'issues': [], 'review_queue': [], 'coverage': {'missing_for_twin': ['ClientGraph and P&L integration are not available']}}
        documents = []
        parsed_documents = []
        for upload in files:
            data = await upload.read(25 * 1024 * 1024 + 1)
            total_bytes += len(data)
            if len(data) > 25 * 1024 * 1024 or total_bytes > 40 * 1024 * 1024:
                raise HTTPException(413, "Batch exceeds file or total size limit")
            try:
                if (upload.filename or '').lower().endswith('.zip'):
                    documents.extend(store.add_archive(data))
                else:
                    documents.append(store.add(upload.filename or '', data))
            except IntakeError as exc:
                raise HTTPException(400, str(exc)) from exc
        for doc in documents:
            try:
                parsed = parse_document(doc, root / 'pages', store)
            except Exception as exc:
                batch['documents'].append({'document_id': doc.document_id, 'filename': doc.filename, 'doc_class': 'unknown', 'class_confidence': 0.0, 'status': 'needs_review', 'duplicate': doc.duplicate})
                batch['issues'].append({'issue_type': 'parse_error', 'severity': 'high', 'affected': [doc.document_id], 'evidence': [], 'detail': str(exc)})
                continue
            pages = {page.number: page.text for page in parsed.pages}
            page_texts = [page.text for page in parsed.pages]
            classification = classifier.classify(doc.filename, page_texts, doc.media_type)
            doc_class = classification.doc_class
            batch['documents'].append({'document_id': doc.document_id, 'filename': doc.filename, 'doc_class': doc_class, 'class_confidence': classification.confidence, 'class_method': classification.method, 'class_signals': list(classification.signals), 'status': 'irrelevant' if doc_class == 'irrelevant' else 'needs_review', 'duplicate': doc.duplicate, 'media_type': doc.media_type, 'pages': len(parsed.pages), 'uploaded_at': datetime.now(timezone.utc).isoformat()})
            parsed_documents.append({'document_id': doc.document_id, 'doc_class': doc_class, 'pages': [{'number': page.number, 'text': page.text} for page in parsed.pages], 'tables': [{'page': table.page, 'rows': table.rows} for table in parsed.tables]})
            if q0 and any(page_texts) and '\n'.join(page_texts).strip():
                try:
                    batch['issues'].extend(q0.issues(doc.document_id, '\n'.join(page_texts)))
                except ReplayMiss:
                    pass  # a replay run serves recordings only
                except Exception as exc:
                    batch['issues'].append({'issue_type': 'decision_error', 'severity': 'info', 'affected': [doc.document_id], 'evidence': [], 'detail': str(exc)})
            if extractor and doc_class in FIELDS:
                try:
                    result, model_issues, response = extractor.extract(doc_class, pages)
                    verified, evidence_issues = validate_evidence(result, pages)
                    batch['issues'].extend(model_issues + evidence_issues)
                    entity = {'supplier_quote': 'Quote', 'carrier_quote': 'Quote', 'contract': 'Contract', 'invoice': 'Shipment', 'customs': 'CustomsEntry', 'waybill': 'Shipment', 'registry_extract': 'RegistryRecord'}.get(doc_class)
                    if entity and isinstance(verified, dict):
                        for field, value in verified.items():
                            if not isinstance(value, dict) or 'raw' not in value:
                                continue
                            ident = uuid4().hex
                            evidence = dict(value['evidence'], document_id=doc.document_id)
                            if response is not None:
                                evidence.update(model_id=response.model_id, model_version=response.model_version, latency_ms=response.latency_ms)
                            value['evidence'] = evidence
                            name = entity + '.' + field
                            batch['proposed_instances'].append({'type': entity, 'id': ident, 'source_row': 'model_entity', 'fields': {name: value}, 'status': 'extracted_unconfirmed', 'evidence': [evidence]})
                            batch['review_queue'].append({'question_id': ident, 'instance_id': ident, 'field': name, 'money_impact_uzs': 0.0, 'options': ['confirm', 'correct', 'irrelevant', 'need_document'], 'status': 'pending'})
                except Exception as exc:
                    batch['issues'].append({'issue_type': 'extraction_error', 'severity': 'high', 'affected': [doc.document_id], 'evidence': [], 'detail': str(exc)})
            for warning in parsed.warnings:
                batch['issues'].append({'issue_type': 'parse_warning', 'severity': 'high', 'affected': [doc.document_id], 'evidence': [], 'detail': warning})
            if doc_class == 'unknown':
                batch['issues'].append({'issue_type': 'classification_required', 'severity': 'medium', 'affected': [doc.document_id], 'evidence': []})
            if doc_class == 'irrelevant':
                continue
            for table in parsed.tables:
                batch['issues'].extend(check_table_quality(table, doc.document_id))
                batch['issues'].extend(check_period_gaps(table, doc.document_id))
                sales_table = doc_class not in ('stock', 'customs')
                result = extract_domain_table(table, doc.document_id, doc_class) if not sales_table else extract_table(table, doc.document_id)
                if not result['rows']:
                    expense = extract_expenses(table, doc.document_id)
                    if expense['rows']:
                        result, sales_table = expense, False
                suggestions = suggest_columns(decision_scorer, table, result.get('header_row'), result.get('unmapped_columns', [])) if decision_scorer and sales_table else {}
                for column in result.get('unmapped_columns', []):
                    issue = {'issue_type': 'unmapped_column', 'severity': 'info', 'affected': [doc.document_id], 'evidence': [], 'detail': column}
                    if column.get('index') in suggestions:
                        issue['suggestion'] = suggestions[column['index']]
                    batch['issues'].append(issue)
                for row in result['rows']:
                    for field, value in row['fields'].items():
                        if field == 'SalesRecord.revenue_ex_vat_uzs':
                            continue
                        ident = uuid4().hex
                        evidence = value['evidence']
                        batch['proposed_instances'].append({'type': row['type'], 'id': ident, 'source_row': row['row'], 'source_product': row.get('product'), 'fields': {field: value}, 'status': 'extracted_unconfirmed', 'evidence': [evidence]})
                        batch['review_queue'].append({'question_id': ident, 'instance_id': ident, 'field': field, 'money_impact_uzs': 0.0, 'options': ['confirm', 'correct', 'irrelevant', 'need_document'], 'status': 'pending'})
        unique_documents = list({d['document_id']: d for d in reversed(parsed_documents)}.values())[::-1]
        extra_records, field_issues = money_records(unique_documents, as_of=as_of)
        batch['money_records'] = extra_records
        batch['issues'].extend(field_issues)
        initial_money_issues = check_money(money_records_from_proposals(batch['documents'], batch['proposed_instances']) + extra_records, as_of=as_of)
        for issue in initial_money_issues:
            issue['check_source'] = 'money_checks'
        batch['issues'].extend(initial_money_issues)
        batch['issues'].extend(check_quote_contract_moq(extra_records))
        batch['issues'].extend(analyze_document_quality(parsed_documents))
        batch['issues'].extend(analyze_observed_documents([{'document_id': d['document_id'], 'pages': {p['number']: p['text'] for p in d['pages']}} for d in parsed_documents]))
        batch['review_queue'] = build_review_queue(batch['proposed_instances'])
        cross = analyze_cross_documents(batch['documents'], batch['proposed_instances'], scorer=decision_scorer, embedder=embedder)
        batch['links'].extend(cross['links'])
        batch['issues'].extend(cross['issues'])
        batch['issues'].extend(check_duplicates([{'id': d['document_id'], 'hash': d['document_id']} for d in batch['documents']], []))
        _save(batch_path(batch_id), batch)
        return {'batch_id': batch_id}

    @router.get('/ingest/batches/{batch_id}')
    async def get_batch(batch_id: str):
        return _load(batch_path(batch_id))

    @router.post('/ingest/batches/{batch_id}/money-checks')
    async def money_checks(batch_id: str, request: MoneyCheckRequest):
        batch = _load(batch_path(batch_id))
        records = money_records_from_proposals(batch['documents'], batch['proposed_instances']) + batch.get('money_records', [])
        rates, errors = [], []
        queries = cbu_rate_requests(records)
        if request.fetch_official_rates:
            if len(queries) > 50:
                raise HTTPException(400, 'More than 50 official FX requests in one batch')
            for currency, day in queries:
                try:
                    rates.append(fx_provider(currency, day))
                except (OSError, ValueError, TimeoutError) as exc:
                    errors.append({'currency': currency, 'date': day.isoformat(), 'error': str(exc)})
        issues = check_money(records, as_of=request.as_of, rates=rates, registry_max_age_days=request.registry_max_age_days, require_official_rate=request.require_official_rate or request.fetch_official_rates)
        for issue in issues:
            issue['check_source'] = 'money_checks'
        batch['issues'] = [issue for issue in batch['issues'] if issue.get('check_source') != 'money_checks'] + issues
        _save(batch_path(batch_id), batch)
        customs = [r for r in records if r.get('doc_class') in ('customs', 'customs_entry')]
        eligible = sum(bool(cbu_rate_requests([r])) for r in customs)
        coverage = 'checked' if rates and len(rates) == len(queries) and eligible == len(customs) else ('partial' if rates else 'not_checked')
        return {'issues': issues, 'rate_fetch_errors': errors, 'fx_coverage': {'status': coverage, 'customs_documents': len(customs), 'eligible_documents': eligible, 'rates_obtained': len(rates)}}


    @router.get('/ingest/batches/{batch_id}/review')
    async def review_queue(batch_id: str):
        return sorted(_load(batch_path(batch_id))['review_queue'], key=lambda q: (q['money_impact_uzs'] is None, -(q['money_impact_uzs'] or 0), not q.get('affects_money', False)))

    @router.post('/ingest/review/{question_id}')
    async def answer_review(question_id: str, answer: ReviewAnswer):
        if answer.action not in {'confirm', 'correct', 'irrelevant', 'need_document'} or (answer.action == 'correct' and not answer.value):
            raise HTTPException(400, 'Invalid review answer')
        for path in (root / 'batches').glob('*.json'):
            batch = _load(path)
            for question in batch['review_queue']:
                if question['question_id'] != question_id:
                    continue
                if question['status'] != 'pending':
                    raise HTTPException(409, 'Question already answered')
                ids = question.get('instance_ids', [question['instance_id']])
                if answer.instance_id is not None and answer.instance_id not in ids:
                    raise HTTPException(400, 'Instance is not pending in this question')
                if answer.action == 'correct' and len(ids) > 1 and answer.instance_id is None:
                    raise HTTPException(400, 'Select one instance to correct')
                selected = [answer.instance_id] if answer.instance_id else ids[:]
                for ident in selected:
                    instance = next(i for i in batch['proposed_instances'] if i['id'] == ident)
                    if answer.action in {'confirm', 'correct'}:
                        instance['status'] = 'confirmed'
                        if answer.action == 'correct':
                            instance['fields'][question['field']]['corrected_value'] = answer.value
                    else:
                        instance['status'] = answer.action
                question.setdefault('decisions', []).append({'action': answer.action, 'instance_ids': selected, 'corrected_value': answer.value})
                remaining = [ident for ident in ids if ident not in selected]
                question['instance_ids'] = remaining
                question['items'] = [item for item in question.get('items', []) if item['instance_id'] in remaining]
                if remaining:
                    question['instance_id'] = remaining[0]
                else:
                    question.update(status='answered', action=answer.action, corrected_value=answer.value)
                _save(path, batch)
                return question
        raise HTTPException(404, 'Question not found')

    @router.post('/ingest/batches/{batch_id}/review/confirm-all')
    async def confirm_all(batch_id: str):
        """Confirm every pending question in one load/save (the per-question endpoint rewrites the whole batch each time)."""
        path = batch_path(batch_id)
        batch = _load(path)
        by_id = {i['id']: i for i in batch['proposed_instances']}
        done = 0
        for question in batch['review_queue']:
            if question['status'] != 'pending':
                continue
            ids = question.get('instance_ids', [question['instance_id']])
            for ident in ids:
                if ident in by_id:
                    by_id[ident]['status'] = 'confirmed'
            question.setdefault('decisions', []).append({'action': 'confirm', 'instance_ids': ids, 'corrected_value': None})
            question['instance_ids'] = []
            question['items'] = []
            question.update(status='answered', action='confirm', corrected_value=None)
            done += 1
        _save(path, batch)
        return {'confirmed': done}

    @router.post('/ingest/batches/{batch_id}/commit')
    async def commit(batch_id: str):
        batch = _load(batch_path(batch_id))
        graph_path = root / 'graphs' / (batch_id + '.json')
        existing = ClientGraph.model_validate(_load(graph_path)) if graph_path.exists() else ClientGraph()
        graph, incomplete = commit_batch(batch, existing)
        graph.for_calculation()
        payload = graph.model_dump(mode='json', exclude_computed_fields=True)
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode()
        snapshot_id = hashlib.sha256(encoded).hexdigest()
        _save(graph_path, payload)
        _save(root / 'snapshots' / (snapshot_id + '.json'), payload)
        count = sum(len(getattr(graph, name)) for name in ClientGraph.model_fields if name != 'source_documents')
        return {'snapshot_id': snapshot_id, 'committed': count, 'target': 'ClientGraph', 'incomplete': incomplete}


    @router.post('/ingest/batches/{batch_id}/pnl')
    async def pnl(batch_id: str, request: PnLRequest):
        batch_path(batch_id)
        graph = ClientGraph.model_validate(_load(root / 'graphs' / (batch_id + '.json')))
        return calculate_pnl(graph, request.product, request.period, request.required_expenses, expenses_attributed_to_product=request.expenses_attributed_to_product)

    @router.get('/ingest/documents/{document_id}/page/{number}.png')
    async def page(document_id: str, number: int):
        if len(document_id) != 64 or any(c not in '0123456789abcdef' for c in document_id) or number < 1:
            raise HTTPException(404, 'Not found')
        directory = root / 'pages' / document_id
        for pattern in (f'{number}.png', f'page_{number}.png', f'page-{number}.png'):
            path = directory / pattern
            if path.is_file():
                return Response(path.read_bytes(), media_type='image/png')
        raise HTTPException(404, 'Not found')

    return router, root


def create_app(data_dir: str | Path | None = None, *, scorer=None, extractor=None, fx_provider=None) -> FastAPI:
    """Standalone ingest app (kept for old callers); the unified app is api.app.create_app."""
    from lagrange_rx.api.ontology import make_router as ontology_router
    router, root = make_router(data_dir, scorer=scorer, extractor=extractor, fx_provider=fx_provider)
    app = FastAPI(title='LAGRANGE Rx ingest')
    app.include_router(router)
    app.include_router(ontology_router(root))
    return app


app = create_app()
