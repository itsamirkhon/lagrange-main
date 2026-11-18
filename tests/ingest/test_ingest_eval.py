import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('ingest_eval', Path(__file__).parents[2] / 'eval' / 'ingest_eval.py')
eval_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eval_module)


def test_report_counts_by_type_and_does_not_claim_unmeasured_metrics():
    results = [
        {'doc_type': 'D1', 'prediction': 'D1', 'questions': 2, 'issues': 1, 'elapsed_seconds': 1.0},
        {'doc_type': 'D1', 'prediction': 'unknown', 'questions': 0, 'issues': 1, 'elapsed_seconds': 2.0},
        {'doc_type': 'D2', 'prediction': 'unmapped_label', 'questions': 1, 'issues': 0, 'elapsed_seconds': 3.0},
    ]
    report = eval_module.summarize('L0', results)
    assert report['by_document_type']['D1']['classification'] == {'correct': 1, 'total': 2, 'unmapped_predictions': 0}
    assert report['by_document_type']['D2']['classification']['unmapped_predictions'] == 1
    assert report['questions'] == 3
    assert report['documents'] == 3
    assert report['unavailable']['field_accuracy']


def test_manifest_paths_cannot_escape_simulation_root(tmp_path):
    import pytest
    with pytest.raises(ValueError):
        eval_module.document_path(tmp_path, '../outside.txt')


def test_evaluate_level_runs_ingest_on_manifest_documents(tmp_path):
    import asyncio
    import json
    sim = tmp_path / 'sim'
    world = sim / 'worlds' / 'L0'
    (world / 'documents').mkdir(parents=True)
    (world / 'documents' / 'sample.csv').write_text('Месяц,Количество\n2026-01,10\n', encoding='utf-8')
    (world / 'manifest.json').write_text(json.dumps({'level': 'L0', 'documents': [{'document': 'worlds/L0/documents/sample.csv', 'doc_type': 'D1'}]}), encoding='utf-8')
    report = asyncio.run(eval_module.evaluate_level(sim, 'L0', tmp_path / 'state', {}))
    assert report['documents'] == 1
    assert report['errors'] == 0
    assert report['by_document_type']['D1']['classification']['total'] == 1


def test_corruption_scoring_requires_specific_document_and_field():
    entries = [{'document': 'a.csv', 'corruptions': [{'operator': 'M1', 'field': 'monthly_sales_2026-05'}, {'operator': 'V1', 'field': 'vat_status'}]}]
    issues = [
        {'issue_type': 'missing', 'affected': ['hash-a'], 'detail': 'Missing period 2026-05'},
        {'issue_type': 'classification_required', 'affected': ['hash-a']},
        {'issue_type': 'contradiction', 'affected': ['hash-b'], 'detail': 'vat_status'},
    ]
    score = eval_module.score_corruptions(entries, {'a.csv': 'hash-a'}, issues)
    assert score['total'] == 2
    assert score['detected'] == 1
    assert score['by_operator']['M1']['detected'] == 1
    assert score['by_operator']['V1']['detected'] == 0
    assert score['unmatched_issues'] == 1
    assert score['excluded_technical_issues'] == 1


def test_corruption_scoring_does_not_match_generic_or_reuse_issue():
    entries = [{'document': 'a', 'corruptions': [{'operator': 'X1', 'field': 'price'}, {'operator': 'X2', 'field': 'price'}]}]
    issues = [{'issue_type': 'contradiction', 'affected': ['hash-a'], 'detail': 'price'}]
    score = eval_module.score_corruptions(entries, {'a': 'hash-a'}, issues)
    assert score['detected'] == 1
    assert score['total'] == 2


def test_evaluate_uploads_world_in_one_batch(tmp_path, monkeypatch):
    import asyncio
    import json
    import httpx
    sim = tmp_path / 'sim'
    world = sim / 'worlds' / 'L0'
    world.mkdir(parents=True)
    docs = []
    for name, content in [('a.csv', 'x\n1\n'), ('b.csv', 'x\n2\n')]:
        (world / name).write_text(content)
        docs.append({'document': f'worlds/L0/{name}', 'doc_type': 'D1', 'corruptions': []})
    (world / 'manifest.json').write_text(json.dumps({'level': 'L0', 'documents': docs}))
    calls = []
    original = httpx.AsyncClient.post
    async def post(client, url, **kwargs):
        calls.append(len(kwargs['files']))
        return await original(client, url, **kwargs)
    monkeypatch.setattr(httpx.AsyncClient, 'post', post)
    report = asyncio.run(eval_module.evaluate_level(sim, 'L0', tmp_path / 'state', {}))
    assert calls == [2]
    assert report['documents'] == 2
    assert report['errors'] == 0


def test_duplicate_issue_matches_document_without_field_name():
    entries = [{'document': 'duplicate.pdf', 'corruptions': [{'operator': 'D1x', 'field': 'shipment_id'}]}]
    issues = [{'issue_type': 'duplicate', 'affected': ['hash-a', 'hash-b'], 'detail': 'Possible duplicate'}]
    score = eval_module.score_corruptions(entries, {'duplicate.pdf': 'hash-b'}, issues)
    assert score['detected'] == 1


def test_ambiguous_vat_and_units_count_when_document_and_field_match():
    entries = [{'document': 'a', 'corruptions': [{'operator': 'V1', 'field': 'vat_status'}, {'operator': 'U1', 'field': 'initial_qty'}]}]
    issues = [{'issue_type': 'vat_ambiguous', 'field': 'vat_status', 'affected': ['x']}, {'issue_type': 'unit_ambiguous', 'field': 'initial_qty', 'affected': ['x']}]
    result = eval_module.score_corruptions(entries, {'a': 'x'}, issues)
    assert result['detected'] == 2


def test_summary_separates_technical_warnings_from_quality_findings():
    report = {'documents': 2, 'questions': 3, 'issues': 5, 'errors': 0, 'elapsed_seconds': 1, 'by_document_type': {'D1': {'classification': {'correct': 1, 'total': 2, 'unmapped_predictions': 0}}}, 'corruption_detection': {'detected': 1, 'total': 2, 'excluded_technical_issues': 4}}
    row = eval_module.summary_row('L3', report)
    assert row['technical_warnings'] == 4
    assert row['quality_findings'] == 1
    assert row['corruptions_detected'] == 1


def test_money_and_moq_semantics_align_only_on_same_document():
    entries = [{'document': 'a', 'corruptions': [{'operator': 'X1', 'field': 'min_order_qty'}, {'operator': 'C1', 'field': 'unit_price'}, {'operator': 'S1', 'field': 'quote_status'}]}]
    issues = [{'issue_type': 'contradiction', 'field': 'moq', 'affected': ['x']}, {'issue_type': 'currency_ambiguous', 'field': 'currency', 'affected': ['x']}, {'issue_type': 'stale', 'field': 'valid_until', 'affected': ['x']}]
    result = eval_module.score_corruptions(entries, {'a': 'x'}, issues)
    assert result['detected'] == 3
    assert eval_module.score_corruptions(entries, {'a': 'different'}, issues)['detected'] == 0
