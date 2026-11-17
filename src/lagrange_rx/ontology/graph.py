"""A client-scoped graph with explicit referential and calculation checks."""
from typing import ClassVar
from pydantic import BaseModel, Field
from .models import *

class ClientGraph(StrictModel):
    source_documents: list[SourceDocument] = Field(default_factory=list)
    regulations: list[Regulation] = Field(default_factory=list)
    rules: list[Rule] = Field(default_factory=list)
    registry_records: list[RegistryRecord] = Field(default_factory=list)
    price_indexation_events: list[PriceIndexationEvent] = Field(default_factory=list)
    products: list[Product] = Field(default_factory=list)
    manufacturers: list[Manufacturer] = Field(default_factory=list)
    registration_certificates: list[RegistrationCertificate] = Field(default_factory=list)
    suppliers: list[Supplier] = Field(default_factory=list)
    production_sites: list[ProductionSite] = Field(default_factory=list)
    carriers: list[Carrier] = Field(default_factory=list)
    routes: list[Route] = Field(default_factory=list)
    quotes: list[Quote] = Field(default_factory=list)
    contracts: list[Contract] = Field(default_factory=list)
    shipments: list[Shipment] = Field(default_factory=list)
    lots: list[Lot] = Field(default_factory=list)
    customs_entries: list[CustomsEntry] = Field(default_factory=list)
    currencies: list[Currency] = Field(default_factory=list)
    fx_rates: list[FxRate] = Field(default_factory=list)
    cost_items: list[CostItem] = Field(default_factory=list)
    credit_lines: list[CreditLine] = Field(default_factory=list)
    fx_hedges: list[FxHedge] = Field(default_factory=list)
    warehouses: list[Warehouse] = Field(default_factory=list)
    regions: list[Region] = Field(default_factory=list)
    channels: list[Channel] = Field(default_factory=list)
    customers: list[Customer] = Field(default_factory=list)
    sales_records: list[SalesRecord] = Field(default_factory=list)
    demand_forecasts: list[DemandForecast] = Field(default_factory=list)
    scenarios: list[Scenario] = Field(default_factory=list)
    plans: list[Plan] = Field(default_factory=list)
    constraints: list[Constraint] = Field(default_factory=list)
    check_results: list[CheckResult] = Field(default_factory=list)
    snapshots: list[Snapshot] = Field(default_factory=list)
    issues: list[DataQualityIssue] = Field(default_factory=list)

    _keys: ClassVar[dict[str, str]] = dict(source_documents='document_id', regulations='regulation_id', rules='rule_id', registry_records='registration_number', products='sku_id', manufacturers='name', registration_certificates='number', suppliers='supplier_id', production_sites='site_id', carriers='carrier_id', routes='route_id', quotes='quote_id', contracts='contract_id', shipments='shipment_id', lots='lot_number', customs_entries='entry_ref', currencies='code', warehouses='warehouse_id', regions='region_id', channels='name', customers='name', scenarios='scenario_id', plans='plan_id', snapshots='snapshot_id')
    _refs: ClassVar[dict[str, dict[str, str]]] = {
        'regulations': {'replaces':'regulations'}, 'rules': {'regulation':'regulations'},
        'registry_records': {'product':'products'}, 'price_indexation_events': {'registry_record':'registry_records'},
        'products': {'manufacturer':'manufacturers','registration_certificate':'registration_certificates','originator_sku':'products'},
        'manufacturers': {'same_composition_skus':'products'}, 'production_sites': {'supplier':'suppliers','approved_for':'products'},
        'routes': {'production_site':'production_sites','carrier':'carriers'}, 'quotes': {'supplier':'suppliers','carrier':'carriers','product':'products'},
        'contracts': {'supplier':'suppliers','supersedes':'contracts'}, 'shipments': {'route':'routes','lots':'lots','customs_entry':'customs_entries'},
        'lots': {'product':'products','shipment':'shipments'}, 'cost_items': {'shipment':'shipments'},
        'warehouses': {'region':'regions'}, 'customers': {'region':'regions','channel':'channels'},
        'sales_records': {'product':'products','region':'regions','channel':'channels','lot':'lots'},
        'demand_forecasts': {'product':'products'}, 'scenarios': {'supplier_disrupted':'routes'},
        'plans': {'snapshot':'snapshots'}, 'constraints': {'rule':'rules','scenario_id':'scenarios'}, 'check_results': {'rule':'rules'},
    }

    def indexes(self) -> dict[str, dict[str, Entity]]:
        indexes = {}
        for collection, key in self._keys.items():
            index = {}
            for item in getattr(self, collection):
                value = getattr(item, key)
                if value in index:
                    raise ValueError(f'duplicate {collection}.{key}: {value}')
                index[value] = item
            indexes[collection] = index
        return indexes

    def validate_references(self) -> None:
        indexes = self.indexes()
        for collection, references in self._refs.items():
            for item in getattr(self, collection):
                for field, target in references.items():
                    values = getattr(item, field)
                    if values is None:
                        continue
                    for value in values if isinstance(values, list) else [values]:
                        if value not in indexes[target]:
                            raise ValueError(f'{collection}.{field} references missing {target}: {value}')
        for plan in self.plans:
            for line in plan.lines:
                if line.route not in indexes['routes']:
                    raise ValueError(f'plans.lines.route references missing routes: {line.route}')
        self._check_sources(indexes)

    def _check_sources(self, indexes: dict) -> None:
        def visit(value):
            if isinstance(value, Provenance):
                if value.source_document is not None and value.source_document not in indexes['source_documents']:
                    raise ValueError(f'provenance.source_document references missing source_documents: {value.source_document}')
            elif isinstance(value, Evidence):
                if value.source_document not in indexes['source_documents']:
                    raise ValueError(f'evidence.source_document references missing source_documents: {value.source_document}')
                for child in value.__dict__.values(): visit(child)
            elif isinstance(value, BaseModel):
                for child in value.__dict__.values(): visit(child)
            elif isinstance(value, dict):
                for child in value.values(): visit(child)
            elif isinstance(value, (list, tuple)):
                for child in value: visit(child)
        visit(self)

    def for_calculation(self) -> 'ClientGraph':
        self.validate_references()
        def check(value):
            if isinstance(value, Provenance) and value.kind == 'extracted_unconfirmed':
                raise ValueError('unconfirmed provenance cannot enter calculations')
            if isinstance(value, BaseModel):
                for child in value.__dict__.values(): check(child)
            elif isinstance(value, dict):
                for child in value.values(): check(child)
            elif isinstance(value, (list, tuple)):
                for child in value: check(child)
        check(self)
        return self
