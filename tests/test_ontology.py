import pytest
from pydantic import ValidationError
from lagrange_rx.ontology import ClientGraph, Product, Manufacturer, RegistrationCertificate, Provenance

P = Provenance(kind='user_provided', source='certificate.pdf')

def product(**kwargs):
    values = dict(sku_id='sku1', trade_name='Drug', inn='x', dosage_form='tablet', strength='10mg', pack_size=30, rx=True, manufacturer='m1', registration_certificate='cert1', provenance=P)
    values.update(kwargs)
    return Product(**values)

def test_graph_validates_references_and_trust():
    graph = ClientGraph(products=[product()], manufacturers=[Manufacturer(name='m1', country='UZ', provenance=P)], registration_certificates=[RegistrationCertificate(number='cert1', valid_from='2025-01-01', valid_to='2027-01-01', holder='holder', provenance=P)])
    graph.validate_references()
    assert graph.for_calculation() == graph
    with pytest.raises(ValueError, match='manufacturer'):
        ClientGraph(products=[product()]).validate_references()
    unconfirmed = product(provenance=Provenance(kind='extracted_unconfirmed', source='quote.pdf'))
    with pytest.raises(ValueError, match='unconfirmed'):
        ClientGraph(products=[unconfirmed], manufacturers=graph.manufacturers, registration_certificates=graph.registration_certificates).for_calculation()

def test_models_require_provenance_and_forbid_extra():
    with pytest.raises(ValidationError):
        Manufacturer(name='x', country='UZ')
    with pytest.raises(ValidationError):
        product(unexpected=True)

def test_unconfirmed_field_is_blocked_even_if_object_confirmed():
    graph = ClientGraph(manufacturers=[Manufacturer(name='m1', country='UZ', provenance=P, field_provenance={'country': Provenance(kind='extracted_unconfirmed', source='x')})])
    with pytest.raises(ValueError, match='unconfirmed'):
        graph.for_calculation()

def test_customs_legal_base_excludes_vat():
    from lagrange_rx.ontology import CustomsEntry
    item = CustomsEntry(entry_ref='e', entry_date='2026-01-01', cip_value_fx=100, currency='USD', fx_rate_on_entry=12000, duty_uzs=10000, fees_uzs=5000, vat_uzs=200000, packs=100, provenance=P)
    assert item.legal_base_c_per_pack == 12150


def test_domain_models_have_canonical_modules_and_compatible_exports():
    from lagrange_rx.ontology import models
    from lagrange_rx.ontology import trust, regulation, product, supply, finance, demand, decision
    for module, names in [
        (trust, ['Provenance', 'SourceDocument', 'Evidence', 'DataQualityIssue', 'Snapshot']),
        (regulation, ['Regulation', 'Rule', 'RegistryRecord', 'PriceIndexationEvent']),
        (product, ['Product', 'Manufacturer', 'RegistrationCertificate']),
        (supply, ['Supplier', 'Route', 'Quote', 'Shipment', 'CustomsEntry']),
        (finance, ['Currency', 'FxRate', 'CostItem', 'CreditLine']),
        (demand, ['Region', 'Warehouse', 'SalesRecord', 'DemandForecast']),
        (decision, ['Scenario', 'Plan', 'Constraint', 'CheckResult']),
    ]:
        for name in names:
            cls = getattr(module, name)
            assert cls is getattr(models, name)
            assert cls.__module__ == module.__name__
