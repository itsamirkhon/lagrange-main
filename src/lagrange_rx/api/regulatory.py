"""Reviewable pharmaceutical screening, separated from government integrations.

Demo records are deliberately invented. /pharma/compliance/evaluate accepts
operator-provided snapshots; its result NEVER means a government API was used.
"""
from __future__ import annotations

from datetime import date, timedelta
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict

from lagrange_rx.engine.regulatory_gate import (
    Batch, License, Marking, Medicine, Offer, Packet, PriceBasis, PriceReference,
    QualityScreen, Source, TemperatureLog, evaluate,
)

# Stable fixture date; intentionally not populated from any government registry.
DEMO_DAY = date(2026, 11, 18)
CaseId = Literal[
    "eligible", "overprice", "unlicensed", "recalled", "controlled",
    "cold_chain", "missing_record", "public_procurement", "wrong_spec", "bad_marking",
]


class DemoRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    case: CaseId = "eligible"


class SnapshotRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    on: date
    packet: Packet


SOURCES = (
    {"id": "MED_REG", "name": "Госреестр лекарственных средств", "url": "https://old.uzpharm-control.uz/ru/pages/state-register-of-medicines-and-medical-products", "status": "manual_import_required"},
    {"id": "REF_PRICE", "name": "Реестр референтных цен", "url": "https://mpa.uz/en/reference-prices", "status": "manual_import_required"},
    {"id": "LICENSE", "name": "Реестр лицензий", "url": "https://license.gov.uz/", "status": "manual_verification_required"},
    {"id": "BATCH", "name": "Реестр сертифицированных препаратов", "url": "https://old.uzpharm-control.uz/ru/registries/certified-medicines", "status": "manual_import_required"},
    {"id": "ALERTS", "name": "Сообщения о фальсифицированных/некачественных препаратах", "url": "https://old.uzpharm-control.uz/ru/pages/information-on-identified-falsified-and-substandard-unsuitable-drugs-and-medical-products", "status": "manual_review_required"},
    {"id": "TRACE", "name": "Asl Belgisi (маркировка)", "url": "https://aslbelgisi.uz/", "status": "authorized_api_access_required"},
    {"id": "LAW", "name": "Lex.uz (нормативная база)", "url": "https://lex.uz/uz/acts/3155770", "status": "rules_manual_review_required"},
)


def fixture(case: CaseId) -> Packet:
    """Reproducible synthetic offers; none of these identifiers exist in official registries."""
    source = Source(system="DEMO-NOT-GOVERNMENT", record_id="DEMO-SOURCE-001",
                    url="https://example.org/lagrange-rx/synthetic", retrieved_on=DEMO_DAY,
                    kind="synthetic")
    offer = Offer(route_id="DEMO-ROUTE-1", product_id="DEMO-SKU-1", supplier_id="DEMO-SUP-1",
                  supplier_tin="000000000", registration_no="DEMO-REG-001",
                  trade_name="Cardio Rx (fictional)", strength="10 mg", dosage_form="tablets",
                  manufacturer="Example Laboratory (fictional)", pack_size=30,
                  lot_number="DEMO-LOT-2026", expiry_date=DEMO_DAY + timedelta(days=120),
                  required_until=DEMO_DAY + timedelta(days=60), channel="wholesale", units=100,
                  selling_price_excl_vat=Decimal("114000"),
                  selling_price_incl_vat=Decimal("127680"), vat_rate=Decimal("0.12"))
    medicine = Medicine(registration_no=offer.registration_no, trade_name=offer.trade_name,
                        strength=offer.strength, dosage_form=offer.dosage_form,
                        manufacturer=offer.manufacturer, pack_size=offer.pack_size,
                        rx=True, control="none", registration_state="active",
                        valid_from=DEMO_DAY - timedelta(days=300),
                        valid_until=DEMO_DAY + timedelta(days=300), marking_required=True,
                        minimum_shelf_days=30, storage_type="controlled", min_temp_c=2,
                        max_temp_c=8, source=source)
    packet = Packet(offer=offer, medicine=medicine,
                    license=License(supplier_tin=offer.supplier_tin, activity="wholesale",
                                    status="active", valid_until=DEMO_DAY + timedelta(days=300), source=source),
                    batch=Batch(registration_no=offer.registration_no,
                                lot_number=offer.lot_number, certification="valid",
                                quality="released", source=source),
                    marking=Marking(registration_no=offer.registration_no, lot_number=offer.lot_number,
                                    status="active", source=source),
                    quality_screen=QualityScreen(registration_no=offer.registration_no,
                                                 lot_number=offer.lot_number, status="clear", source=source),
                    reference_price=PriceReference(registration_no=offer.registration_no,
                                                   channel="wholesale", maximum_incl_vat=Decimal("130000"),
                                                   source=source),
                    price_basis=PriceBasis(cost_excl_vat=Decimal("100000"), source=source),
                    temperature_log=TemperatureLog(minimum_c=Decimal("2.5"), maximum_c=Decimal("7.5"),
                                                   device_document_id="DEMO-LOGGER-001", source=source))
    if case == "overprice":
        packet = packet.model_copy(update={"offer": offer.model_copy(update={
            "selling_price_excl_vat": Decimal("120000"), "selling_price_incl_vat": Decimal("134400")})})
    elif case == "unlicensed":
        packet = packet.model_copy(update={"license": packet.license.model_copy(update={"status": "revoked"})})
    elif case == "recalled":
        packet = packet.model_copy(update={"batch": packet.batch.model_copy(update={"quality": "recalled"})})
    elif case == "controlled":
        packet = packet.model_copy(update={"medicine": medicine.model_copy(update={"control": "psychotropic"})})
    elif case == "cold_chain":
        packet = packet.model_copy(update={"temperature_log": packet.temperature_log.model_copy(update={"maximum_c": Decimal("11")})})
    elif case == "missing_record":
        packet = packet.model_copy(update={"medicine": None})
    elif case == "public_procurement":
        packet = packet.model_copy(update={"offer": offer.model_copy(update={"public_procurement": True})})
    elif case == "wrong_spec":
        packet = packet.model_copy(update={"offer": offer.model_copy(update={"strength": "20 mg"})})
    elif case == "bad_marking":
        packet = packet.model_copy(update={"marking": packet.marking.model_copy(update={"status": "invalid"})})
    return packet


def make_router() -> APIRouter:
    router = APIRouter()

    @router.get("/pharma/compliance/sources")
    def sources():
        return {"live_government_integrations": False, "sources": SOURCES,
                "note": "Official URLs are research sources, not active connected feeds."}

    @router.post("/pharma/demo/compliance")
    def demonstrate(body: DemoRequest):
        packet = fixture(body.case)
        result = evaluate(packet, DEMO_DAY, mode="synthetic")
        return {
            "synthetic": True,
            "live_government_checks": False,
            "as_of": DEMO_DAY.isoformat(),
            "case": body.case,
            "decision": result.decision,
            "checks": [item.model_dump(mode="json") for item in result.findings],
            "summary": {"passed": sum(x.status == "pass" for x in result.findings),
                        "held": sum(x.status == "hold" for x in result.findings),
                        "blocked": sum(x.status == "block" for x in result.findings)},
            "offer": packet.offer.model_dump(mode="json"),
            "warnings": ["Данные вымышлены: это демонстрация алгоритма, а не проверка реальной партии.",
                         "Нет подключения к государственным API; для внедрения требуются согласованные источники и экспертная проверка."],
        }

    @router.post("/pharma/compliance/evaluate")
    def evaluate_snapshot(body: SnapshotRequest):
        result = evaluate(body.packet, body.on, mode="operator_snapshot")
        return {
            **result.model_dump(mode="json"),
            "assurance": "operator_supplied_snapshots_only",
            "live_government_checks": False,
            "can_auto_order": False,
            "caution": "Caller-supplied assertions are NOT independently checked against government systems; operator attestation must be independently authenticated before production use.",
        }

    return router
