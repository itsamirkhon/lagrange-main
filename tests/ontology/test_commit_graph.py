from lagrange_rx.ingest.commit import commit_batch
from lagrange_rx.ontology import ClientGraph


def doc():
    return {'document_id': 'd1', 'filename': 'sales.csv', 'media_type': 'text/csv', 'pages': 1, 'uploaded_at': '2026-01-01T00:00:00Z', 'doc_class': 'sales_export'}


def proposal(field, value, status='confirmed', row=1):
    return {'id': field, 'type': 'Region', 'source_row': row, 'status': status, 'fields': {'Region.' + field: {'raw': value, 'evidence': {'document_id': 'd1', 'page': 1, 'quote': value}}}}


def test_combines_confirmed_row_and_is_idempotent():
    batch = {'documents': [doc()], 'proposed_instances': [proposal('region_id', 'r1'), proposal('name', 'Tashkent')]}
    graph, incomplete = commit_batch(batch, ClientGraph())
    assert not incomplete
    assert graph.regions[0].name == 'Tashkent'
    assert graph.regions[0].provenance.source_document == 'd1'
    again, incomplete = commit_batch(batch, graph)
    assert not incomplete
    assert len(again.regions) == 1
    assert len(again.source_documents) == 1


def test_missing_or_unconfirmed_values_are_not_committed():
    batch = {'documents': [doc()], 'proposed_instances': [proposal('region_id', 'r1'), proposal('name', 'Tashkent', 'extracted_unconfirmed')]}
    graph, incomplete = commit_batch(batch, ClientGraph())
    assert not graph.regions
    assert incomplete


def test_missing_references_are_reported_and_graph_is_unchanged():
    batch = {'documents': [doc()], 'proposed_instances': [
        {'id': '1', 'type': 'Warehouse', 'source_row': 1, 'status': 'confirmed', 'fields': {
            'Warehouse.warehouse_id': {'raw': 'w', 'evidence': {'document_id': 'd1', 'page': 1}},
            'Warehouse.region': {'raw': 'missing', 'evidence': {'document_id': 'd1', 'page': 1}},
            'Warehouse.capacity_packs': {'raw': '10', 'evidence': {'document_id': 'd1', 'page': 1}},
            'Warehouse.is_commingled': {'raw': 'false', 'evidence': {'document_id': 'd1', 'page': 1}},
        }}]}
    graph, incomplete = commit_batch(batch, ClientGraph())
    assert not graph.warehouses
    assert any('missing' in str(issue) for issue in incomplete)


def test_changed_review_does_not_silently_skip_previously_committed_row():
    batch = {'documents': [doc()], 'proposed_instances': [proposal('region_id', 'r1'), proposal('name', 'Tashkent')]}
    graph, _ = commit_batch(batch, ClientGraph())
    batch['proposed_instances'][1]['fields']['Region.name']['corrected_value'] = 'Samarkand'
    result, incomplete = commit_batch(batch, graph)
    assert result.regions[0].name == 'Tashkent'
    assert any('changed' in issue['reason'] for issue in incomplete)
