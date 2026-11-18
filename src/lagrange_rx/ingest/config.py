"""Configure the optional extraction provider without contacting it at startup."""
from pathlib import Path
import sys
import yaml
from ..ai.openrouter import OpenRouterClient
from .extract.openrouter import OpenRouterExtractor
from .extract.replay import ReplayExtractor

FIELDS = {
    'supplier_quote': 'supplier_name quote_number issue_date valid_until product_name unit_price currency incoterms moq lead_time shelf_life payment_terms vat_note price_unit',
    'carrier_quote': 'carrier_name route_description mode price_per_shipment price_per_pack currency transit_days moq valid_until',
    'contract': 'contract_number version_or_amendment supplier_name sign_date valid_until unit_price currency incoterms moq payment_terms quote_ref quote_number vat_note price_unit',
    'invoice': 'invoice_number date supplier contract_ref totals',
    'customs': 'entry_ref date customs_post cip_value currency fx_rate_used duty fees vat',
    'waybill': 'number date product lot qty unit from to',
    'registry_extract': 'product registration_number rn b bx chun chchn date',
}


def _schema(fields):
    evidence = {'type': 'object', 'properties': {'page': {'type': 'integer'}, 'quote': {'type': 'string'}}, 'required': ['page', 'quote'], 'additionalProperties': False}
    value = {'anyOf': [{'type': 'object', 'properties': {'raw': {'type': 'string'}, 'evidence': evidence}, 'required': ['raw', 'evidence'], 'additionalProperties': False}, {'type': 'null'}]}
    return {'type': 'object', 'properties': {name: value for name in fields.split()}, 'required': fields.split(), 'additionalProperties': False}


def configured_extractor(cache_dir, *, mode='replay', budget_usd=0, max_request_cost_usd=0.05, config_path=None):
    if mode not in ('replay', 'openrouter'):
        raise ValueError('Invalid extraction mode')
    if mode == 'openrouter' and (budget_usd <= 0 or max_request_cost_usd <= 0):
        raise ValueError('Live mode requires a positive budget')
    if config_path is None:
        source = Path(__file__).resolve().parents[3] / 'config/ai.yaml'
        config_path = source if source.is_file() else Path(sys.prefix) / 'config/ai.yaml'
    config_path = Path(config_path)
    settings = yaml.safe_load(config_path.read_text(encoding='utf-8'))
    model = settings['extractor']
    client = OpenRouterClient(cache_dir, replay=mode == 'replay', budget_usd=budget_usd, max_request_cost_usd=max_request_cost_usd)
    return (ReplayExtractor if mode == 'replay' else OpenRouterExtractor)(client, model, {kind: _schema(fields) for kind, fields in FIELDS.items()})
