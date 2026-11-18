from lagrange_rx.ingest.domain_tables import extract_domain_table
from lagrange_rx.ingest.parse import Table


def test_stock_simulation_headers_preserve_values_and_evidence_without_internal_ids():
    table = Table(2, [
        ['lot_id', 'lot_code', 'sku_id', 'arrival_date', 'expiry_date', 'initial_qty', 'sold_qty', 'writeoff_qty', 'remaining_qty'],
        ['lot_0001', 'KZ-202501-01', 'SKU_CARDIO_01', '2025-01-20', '2026-10-22', '16500', '16500', '0', '0'],
    ])
    row = extract_domain_table(table, 'doc', 'stock')['rows'][0]
    assert row['type'] == 'Lot'
    assert row['fields']['Lot.lot_number']['raw'] == 'KZ-202501-01'
    assert row['fields']['Lot.remaining_packs']['evidence'] == {'document_id': 'doc', 'page': 2, 'quote': '0'}
    assert 'Lot.product' not in row['fields']
    assert all('lot_0001' != item['raw'] for item in row['fields'].values())


def test_customs_simulation_preserves_date_and_rate_without_treating_uzs_as_fx():
    table = Table(1, [['customs_id', 'shipment_id', 'clearance_date', 'customs_post', 'fx_rate_uzs', 'cip_uzs', 'landed_cost_ex_vat_uzs'],
                      ['cus_0001', 'shp_0001', '2025-01-20', 'Т/п Яллама', '12892.1', '1542750000', '1601319885']])
    fields = extract_domain_table(table, 'doc', 'customs')['rows'][0]['fields']
    assert fields['CustomsEntry.entry_date']['raw'] == '2025-01-20'
    assert fields['CustomsEntry.fx_rate_on_entry']['raw'] == '12892.1'
    assert 'CustomsEntry.cip_value_fx' not in fields
    assert 'CustomsEntry.entry_ref' not in fields


def test_class_and_duplicate_headers_fail_closed():
    table = Table(1, [['lot_code', 'expiry_date', 'remaining_qty', 'remaining_qty'], ['L1', '2026-01-01', '4', '5']])
    assert not extract_domain_table(table, 'doc', 'customs')['rows']
    result = extract_domain_table(table, 'doc', 'stock')
    assert 'Lot.remaining_packs' not in result['rows'][0]['fields']
    assert len([c for c in result['unmapped_columns'] if c['reason'] == 'duplicate_mapping']) == 2
