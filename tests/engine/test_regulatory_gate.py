"""Regulatory safety invariants, including negative and stale evidence paths."""
from datetime import date, timedelta
from decimal import Decimal

import pytest
from pydantic import ValidationError
from lagrange_rx.engine.regulatory_gate import (
    Batch, License, Marking, Medicine, Offer, Packet, Permit, PriceBasis, PriceReference,
    ProcurementAuthorization, QualityScreen, Source, TemperatureLog, evaluate,
)

TODAY = date(2026, 11, 18)


def source(system='state_register', kind='synthetic', days=0, **kw):
    real = kind != 'synthetic'
    domains = {'MED_REG':'https://old.uzpharm-control.uz/ru/pages/state-register-of-medicines-and-medical-products',
               'REF_PRICE':'https://mpa.uz/en/reference-prices',
               'BATCH':'https://old.uzpharm-control.uz/ru/registries/certified-medicines',
               'ALERTS':'https://old.uzpharm-control.uz/ru/pages/information-on-identified-falsified-and-substandard-unsuitable-drugs-and-medical-products',
               'TRACE':'https://aslbelgisi.uz/',
               'LICENSE':'https://license.gov.uz/', 'CONTROL':'https://license.gov.uz/',
               'PUBLIC':'https://lex.uz/uz/acts/-7851804',
               'TEMP':'urn:internal:temperature-log', 'PRICE_BASIS':'urn:internal:invoice'}
    return Source(system=system, record_id='DEMO-RECORD',
                  url=domains.get(system, 'https://example.org/synthetic') if real else 'https://example.org/synthetic',
                  retrieved_on=TODAY - timedelta(days=days), kind=kind,
                  reviewed_by='Pharmacist Reviewer' if real else None,
                  sha256='a' * 64 if real else None, **kw)


def good_packet(*, mode='synthetic'):
    kind = 'reviewed_official' if mode == 'operator_snapshot' else 'synthetic'
    src = lambda system: source(system=system, kind=('reviewed_internal' if system in ('PRICE_BASIS','TEMP') and mode == 'operator_snapshot' else kind))
    offer = Offer(route_id='ROUTE-A', product_id='SKU-RX', supplier_id='S1', supplier_tin='123456789',
                  registration_no='REG-DEMO-1', trade_name='DemoMedicine', strength='10 mg',
                  dosage_form='tablet', manufacturer='Demo Manufacturer', pack_size=30,
                  lot_number='LOT-DEMO-1', expiry_date=TODAY + timedelta(days=200),
                  required_until=TODAY + timedelta(days=100), units=50, channel='wholesale',
                  selling_price_excl_vat=Decimal('114'), selling_price_incl_vat=Decimal('127.68'), vat_rate=Decimal('0.12'))
    med = Medicine(registration_no=offer.registration_no, trade_name=offer.trade_name,
                   strength=offer.strength, dosage_form=offer.dosage_form, manufacturer=offer.manufacturer,
                   pack_size=offer.pack_size, rx=True, control='none', registration_state='active',
                   valid_from=TODAY - timedelta(days=300), valid_until=TODAY + timedelta(days=300),
                   marking_required=True, minimum_shelf_days=30, storage_type='controlled',
                   min_temp_c=2, max_temp_c=8, source=src('MED_REG'))
    return Packet(offer=offer, medicine=med,
                  license=License(supplier_tin=offer.supplier_tin, activity='wholesale',
                                  status='active', valid_until=TODAY + timedelta(days=300), source=src('LICENSE')),
                  batch=Batch(registration_no=offer.registration_no, lot_number=offer.lot_number,
                              certification='valid', quality='released', source=src('BATCH')),
                  marking=Marking(registration_no=offer.registration_no, lot_number=offer.lot_number,
                                  status='active', source=src('TRACE')),
                  quality_screen=QualityScreen(registration_no=offer.registration_no, lot_number=offer.lot_number,
                                               status='clear', source=src('ALERTS')),
                  reference_price=PriceReference(registration_no=offer.registration_no, channel='wholesale',
                                                 maximum_incl_vat=Decimal('130'), source=src('REF_PRICE')),
                  price_basis=PriceBasis(cost_excl_vat=Decimal('100'), source=src('PRICE_BASIS')),
                  temperature_log=TemperatureLog(minimum_c=Decimal('2.5'), maximum_c=Decimal('7.7'),
                                                 device_document_id='TEMP-001', source=src('TEMP')))


def findings(packet, mode='synthetic'):
    return evaluate(packet, TODAY, mode=mode)


def test_good_snapshot_in_both_modes():
    for mode in ('synthetic', 'operator_snapshot'):
        res = findings(good_packet(mode=mode), mode)
        assert res.decision == 'eligible', res.model_dump()
        assert res.simulation == (mode == 'synthetic')
        assert res.evidence_checked >= 6 and res.evidence_held == 0
        assert res.may_automatically_procure is False


@pytest.mark.parametrize('update,expected', [
    ({'license': None}, 'LICENSE'),
    ({'batch': None}, 'BATCH'),
    ({'reference_price': None}, 'REFERENCE_PRICE'),
    ({'price_basis': None}, 'MARKUP'),
    ({'temperature_log': None}, 'TEMPERATURE'),
    ({'medicine': None}, 'REGISTRATION'),
])
def test_missing_evidence_holds(update, expected):
    result = findings(good_packet().model_copy(update=update))
    assert result.decision == 'hold'
    assert any(x.code == expected and x.status == 'hold' for x in result.findings)


def test_unreviewed_operator_snapshot_never_passes():
    res = findings(good_packet(mode='synthetic'), mode='operator_snapshot')
    assert res.decision == 'hold' and res.evidence_held > 0


def test_stale_snapshot_never_passes():
    pkt = good_packet(mode='operator_snapshot')
    old = pkt.medicine.source.model_copy(update={'retrieved_on': TODAY - timedelta(days=31)})
    result = findings(pkt.model_copy(update={'medicine': pkt.medicine.model_copy(update={'source': old})}),
                      mode='operator_snapshot')
    assert result.decision == 'hold'
    assert any(x.code == 'REGISTRATION.EVIDENCE' for x in result.findings)


def test_fake_government_url_does_not_count_as_approved_evidence():
    pkt = good_packet(mode='operator_snapshot')
    tampered = pkt.license.source.model_copy(update={'url': 'https://license.gov.uz.evil.example/path'})
    result = findings(pkt.model_copy(update={'license': pkt.license.model_copy(update={'source': tampered})}),
                      mode='operator_snapshot')
    assert result.decision == 'hold'


@pytest.mark.parametrize('change,code', [
    ({'registration_state': 'revoked'}, 'REGISTRATION.STATUS'),
    ({'control': 'prohibited'}, 'CONTROL'),
    ({'minimum_shelf_days': 250}, 'SHELF_LIFE'),
])
def test_illegal_product_blocks(change, code):
    pkt = good_packet()
    r = findings(pkt.model_copy(update={'medicine': pkt.medicine.model_copy(update=change)}))
    assert r.decision == 'blocked'
    assert any(x.code == code and x.status == 'block' for x in r.findings)


def test_product_exact_identity_mismatch_blocks():
    pkt = good_packet()
    wrong = pkt.offer.model_copy(update={'strength': '20 mg'})
    r = findings(pkt.model_copy(update={'offer': wrong}))
    assert r.decision == 'blocked' and any(x.code == 'REGISTRATION.MATCH' for x in r.findings)


def test_supplier_license_wrong_scope_or_revoked_blocks():
    pkt = good_packet()
    for changes in ({'activity': 'retail'}, {'status': 'revoked'}, {'supplier_tin': '987654321'}):
        r = findings(pkt.model_copy(update={'license': pkt.license.model_copy(update=changes)}))
        assert r.decision == 'blocked' and any(x.code == 'LICENSE' and x.status == 'block' for x in r.findings)


def test_price_checks_independent_vat_and_base():
    pkt = good_packet()
    over_ref = pkt.offer.model_copy(update={'selling_price_incl_vat': Decimal('131')})
    over_markup = pkt.offer.model_copy(update={'selling_price_excl_vat': Decimal('115.01')})
    assert any(x.code == 'REFERENCE_PRICE' and x.status == 'block'
               for x in findings(pkt.model_copy(update={'offer': over_ref})).findings)
    assert any(x.code == 'MARKUP' and x.status == 'block'
               for x in findings(pkt.model_copy(update={'offer': over_markup})).findings)
    assert findings(pkt).decision == 'eligible'  # 114 / 100 = 14%


def test_otc_exempt_from_rx_markup_check_but_not_product_checks():
    pkt = good_packet()
    otc = pkt.medicine.model_copy(update={'rx': False})
    price = pkt.offer.model_copy(update={'selling_price_excl_vat': Decimal('1000')})
    result = findings(pkt.model_copy(update={'medicine': otc, 'offer': price,
                                           'reference_price': None, 'price_basis': None}))
    assert result.decision == 'eligible'
    assert any(x.code == 'PRICE_SCOPE' for x in result.findings)


def test_bad_batch_marking_or_temperature_blocks():
    pkt = good_packet()
    for change in ({'quality': 'recalled'}, {'certification': 'revoked'}):
        r = findings(pkt.model_copy(update={'batch': pkt.batch.model_copy(update=change)}))
        assert r.decision == 'blocked'
    r = findings(pkt.model_copy(update={'marking': pkt.marking.model_copy(update={'status': 'invalid'})}))
    assert r.decision == 'blocked'
    temp = pkt.temperature_log.model_copy(update={'maximum_c': Decimal('10')})
    assert findings(pkt.model_copy(update={'temperature_log': temp})).decision == 'blocked'


def test_controlled_product_needs_matching_permit():
    pkt = good_packet()
    med = pkt.medicine.model_copy(update={'control': 'psychotropic'})
    assert findings(pkt.model_copy(update={'medicine': med})).decision == 'hold'
    permit = Permit(supplier_tin=pkt.offer.supplier_tin, category='psychotropic', status='active',
                    valid_until=TODAY + timedelta(days=40), source=source('CONTROL'))
    assert findings(pkt.model_copy(update={'medicine': med, 'controlled_permit': permit})).decision == 'eligible'
    bad = permit.model_copy(update={'category': 'narcotic'})
    assert findings(pkt.model_copy(update={'medicine': med, 'controlled_permit': bad})).decision == 'blocked'


def test_public_procurement_needs_separate_workflow_evidence():
    pkt = good_packet()
    offer = pkt.offer.model_copy(update={'public_procurement': True})
    assert findings(pkt.model_copy(update={'offer': offer})).decision == 'hold'
    pa = ProcurementAuthorization(supplier_tin=offer.supplier_tin, status='valid',
                                   authorized_channel=True, gxp_confirmed=True, source=source('PUBLIC'))
    assert findings(pkt.model_copy(update={'offer': offer, 'public_authorization': pa})).decision == 'eligible'
    bad = pa.model_copy(update={'authorized_channel': False})
    assert findings(pkt.model_copy(update={'offer': offer, 'public_authorization': bad})).decision == 'blocked'


def test_invalid_input_rejected_before_evaluation():
    pkt = good_packet()
    with pytest.raises(ValidationError):
        Offer.model_validate({**pkt.offer.model_dump(), 'units': -1})
    with pytest.raises(ValidationError):
        Offer.model_validate({**pkt.offer.model_dump(), 'selling_price_incl_vat': '1'})
    with pytest.raises(ValidationError):
        TemperatureLog.model_validate({**pkt.temperature_log.model_dump(), 'minimum_c': '20'})
