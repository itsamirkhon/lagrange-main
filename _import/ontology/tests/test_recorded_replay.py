"""A real provider response to a synthetic document, replayed without network."""
from pathlib import Path

from lagrange_rx.ai.openrouter import OpenRouterClient
from lagrange_rx.ingest.config import FIELDS, _schema
from lagrange_rx.ingest.extract.replay import ReplayExtractor


def test_recorded_openrouter_extraction_works_offline():
    cache = Path(__file__).parents[1] / 'eval' / 'replay'
    replay = ReplayExtractor(OpenRouterClient(cache, replay=True), 'google/gemini-2.5-flash',
                             {'supplier_quote': _schema(FIELDS['supplier_quote'])})
    pages = {1: 'SYNTHETIC EXAMPLE ONLY\nSupplier quotation\nSupplier: Example Labs\nProduct: Test medicine\nUnit price: 12 USD'}
    fields, issues, response = replay.extract('supplier_quote', pages)
    assert not issues
    assert fields['supplier_name']['raw'] == 'Example Labs'
    assert fields['unit_price']['raw'] == '12'
    assert response.model_id == 'google/gemini-2.5-flash'
