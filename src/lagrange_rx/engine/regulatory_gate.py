"""Deterministic, fail-closed preflight for pharmaceutical procurement offers.

This module performs OFFLINE checks against explicitly supplied records. It does
NOT fetch government data, prove records genuine, or issue regulatory approval.
User-supplied evidence is advisory until independently authenticated/reviewed.
"""
from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

Decision = Literal["eligible", "hold", "blocked"]
Mode = Literal["synthetic", "operator_snapshot"]
Status = Literal["pass", "hold", "block"]
Scope = Literal["wholesale", "retail"]

# Trusted hostname *allowlist*, not a claim that a URL proves record authenticity.
SOURCES = {
    "MED_REG": frozenset({"uzpharm-control.uz", "old.uzpharm-control.uz"}),
    "LICENSE": frozenset({"license.gov.uz"}),
    "BATCH": frozenset({"uzpharm-control.uz", "old.uzpharm-control.uz"}),
    "ALERTS": frozenset({"uzpharm-control.uz", "old.uzpharm-control.uz"}),
    "TRACE": frozenset({"aslbelgisi.uz", "crpt-turon.uz", "help.crpt-turon.uz"}),
    "REF_PRICE": frozenset({"mpa.uz", "uzpharm-control.uz"}),
    "CONTROL": frozenset({"license.gov.uz", "uzpharm-control.uz", "old.uzpharm-control.uz"}),
    "PUBLIC": frozenset({"pharma.gov.uz", "lex.uz"}),
}
MAX_SNAPSHOT_AGE_DAYS = 30  # conservative product policy, NOT a statutory interval
WHOLESALE_MARKUP = Decimal("1.15")
RETAIL_MARKUP = Decimal("1.20")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class Source(StrictModel):
    system: str = Field(min_length=2, max_length=80)
    record_id: str = Field(min_length=2, max_length=120)
    url: str = Field(min_length=8, max_length=500)
    retrieved_on: date
    valid_from: date | None = None
    valid_to: date | None = None
    # Official/in-house review are declarations, NOT an agency endorsement.
    kind: Literal["synthetic", "reviewed_official", "reviewed_internal"] = "synthetic"
    reviewed_by: str | None = None
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @model_validator(mode="after")
    def interval(self):
        if self.valid_from and self.valid_to and self.valid_from > self.valid_to:
            raise ValueError("source valid_from exceeds valid_to")
        if self.kind != "synthetic" and not (self.reviewed_by and self.reviewed_by.strip() and self.sha256):
            raise ValueError("reviewed snapshots require a reviewer and content SHA-256")
        return self


def evidence_usable(source: Source | None, on: date, mode: Mode, system: str) -> bool:
    if source is None or source.retrieved_on > on or (on - source.retrieved_on).days > MAX_SNAPSHOT_AGE_DAYS:
        return False
    if source.valid_from and on < source.valid_from:
        return False
    if source.valid_to and on > source.valid_to:
        return False
    if mode == "synthetic":
        return source.kind == "synthetic"
    if source.system != system or source.kind == "synthetic" or not (source.reviewed_by and source.sha256):
        return False
    if system in ("PRICE_BASIS", "TEMP"):
        return source.kind == "reviewed_internal"  # operator documents, not government registers
    u = urlsplit(source.url)
    return (source.kind == "reviewed_official" and u.scheme == "https"
            and u.hostname in SOURCES.get(system, frozenset())
            and not u.username and not u.password)


class Medicine(StrictModel):
    registration_no: str
    trade_name: str
    strength: str
    dosage_form: str
    manufacturer: str
    pack_size: int = Field(gt=0)
    rx: bool
    control: Literal["none", "narcotic", "psychotropic", "strong_acting", "prohibited", "unknown"]
    registration_state: Literal["active", "suspended", "revoked", "unknown"]
    valid_from: date
    valid_until: date
    marking_required: bool | None = None
    minimum_shelf_days: int | None = Field(default=None, ge=0)
    storage_type: Literal["ambient", "controlled", "unknown"] = "unknown"
    min_temp_c: Decimal | None = None
    max_temp_c: Decimal | None = None
    source: Source

    @model_validator(mode="after")
    def validate_profile(self):
        if self.valid_from > self.valid_until:
            raise ValueError("medicine registration interval inverted")
        if self.storage_type == "controlled" and (self.min_temp_c is None or self.max_temp_c is None):
            raise ValueError("controlled storage requires two temperature limits")
        if self.min_temp_c is not None and self.max_temp_c is not None and self.min_temp_c > self.max_temp_c:
            raise ValueError("storage temperature interval inverted")
        return self


class License(StrictModel):
    supplier_tin: str = Field(min_length=5, max_length=32)
    activity: Scope
    status: Literal["active", "suspended", "revoked", "unknown"]
    valid_until: date
    source: Source


class Permit(StrictModel):
    supplier_tin: str
    category: str
    status: Literal["active", "suspended", "revoked", "unknown"]
    valid_until: date
    source: Source


class Batch(StrictModel):
    registration_no: str
    lot_number: str
    certification: Literal["valid", "revoked", "missing", "unknown"]
    quality: Literal["released", "recalled", "quarantined", "unknown"]
    marking: Literal["active", "invalid", "missing", "unknown"] = "unknown"
    source: Source


class Marking(StrictModel):
    registration_no: str
    lot_number: str
    status: Literal["active", "invalid", "unknown"]
    source: Source


class QualityScreen(StrictModel):
    registration_no: str
    lot_number: str
    status: Literal["clear", "affected", "unknown"]
    source: Source


class PriceReference(StrictModel):
    registration_no: str
    channel: Scope
    maximum_incl_vat: Decimal = Field(gt=0)
    source: Source


class PriceBasis(StrictModel):
    cost_excl_vat: Decimal = Field(gt=0)
    source: Source


class TemperatureLog(StrictModel):
    minimum_c: Decimal
    maximum_c: Decimal
    device_document_id: str = Field(min_length=2)
    source: Source

    @model_validator(mode="after")
    def bounds(self):
        if self.minimum_c > self.maximum_c:
            raise ValueError("temperature log inverted")
        return self


class ProcurementAuthorization(StrictModel):
    supplier_tin: str
    status: Literal["valid", "invalid", "unknown"]
    # Demonstrates public purchasing channel and supplier GxP review; confirm law case-by-case.
    authorized_channel: bool
    gxp_confirmed: bool
    source: Source


class Offer(StrictModel):
    route_id: str = Field(min_length=1)
    product_id: str = Field(min_length=1)
    supplier_id: str = Field(min_length=1)
    supplier_tin: str = Field(min_length=5)
    registration_no: str
    trade_name: str
    strength: str
    dosage_form: str
    manufacturer: str
    pack_size: int = Field(gt=0)
    lot_number: str
    expiry_date: date
    required_until: date
    channel: Scope = "wholesale"
    units: int = Field(gt=0)
    selling_price_excl_vat: Decimal = Field(gt=0)
    selling_price_incl_vat: Decimal = Field(gt=0)
    vat_rate: Decimal = Field(ge=0, le=1, description="Explicit reviewed transaction VAT rate, not a default")
    public_procurement: bool = False

    @model_validator(mode="after")
    def validate_vat_consistency(self):
        expected = self.selling_price_excl_vat * (1 + self.vat_rate)
        if abs(self.selling_price_incl_vat - expected) > Decimal("0.02"):
            raise ValueError("inconsistent VAT-inclusive/exclusive sale prices")
        return self


class Packet(StrictModel):
    offer: Offer
    medicine: Medicine | None = None
    license: License | None = None
    batch: Batch | None = None
    marking: Marking | None = None
    quality_screen: QualityScreen | None = None
    reference_price: PriceReference | None = None
    price_basis: PriceBasis | None = None
    temperature_log: TemperatureLog | None = None
    controlled_permit: Permit | None = None
    public_authorization: ProcurementAuthorization | None = None


class Finding(StrictModel):
    code: str
    status: Status
    detail: str
    record_id: str | None = None
    source_url: str | None = None


class Result(StrictModel):
    route_id: str
    decision: Decision
    simulation: bool
    findings: tuple[Finding, ...]
    evidence_checked: int
    evidence_held: int
    note: str = "Decision support only; no government certification or authorization"

    @property
    def may_automatically_procure(self) -> bool:
        # This system NEVER executes purchases. Human and regulated workflow required.
        return False


def evaluate(packet: Packet, on: date, *, mode: Mode = "operator_snapshot") -> Result:
    """Check exact offer identity, official evidence coverage and hard constraints.

    `operator_snapshot` is only a human-reviewed data assertion, not an agency API
    integration. Synthetic mode is solely for fixtures and judge demonstrations.
    Neither mode approves transactions or executes orders.
    """
    o = packet.offer
    findings: list[Finding] = []
    verified = 0
    uncertain = 0

    def add(code: str, status: Status, detail: str, source: Source | None = None):
        findings.append(Finding(code=code, status=status, detail=detail,
                                record_id=source.record_id if source else None,
                                source_url=source.url if source else None))

    def evidence(code: str, source: Source | None, system: str) -> bool:
        nonlocal verified, uncertain
        if evidence_usable(source, on, mode, system):
            verified += 1
            return True
        uncertain += 1
        add(code + ".EVIDENCE", "hold", "Missing, stale, future-dated or unreviewed evidence", source)
        return False

    m = packet.medicine
    if m is None:
        add("REGISTRATION", "hold", "Official medicine registration and specification record missing")
    elif evidence("REGISTRATION", m.source, "MED_REG"):
        identity = (o.registration_no, o.trade_name, o.strength, o.dosage_form, o.manufacturer, o.pack_size)
        legal_identity = (m.registration_no, m.trade_name, m.strength, m.dosage_form, m.manufacturer, m.pack_size)
        if identity != legal_identity:
            add("REGISTRATION.MATCH", "block", "Exact registered medicine identity/specification mismatch", m.source)
        elif m.registration_state in ("suspended", "revoked") or not (m.valid_from <= on <= m.valid_until):
            add("REGISTRATION.STATUS", "block", "Registration suspended, revoked, not yet valid or expired", m.source)
        elif m.registration_state != "active":
            add("REGISTRATION.STATUS", "hold", "Registration status unknown", m.source)
        else:
            add("REGISTRATION", "pass", "Exact medicine specification and registration are current", m.source)
        if m.control == "prohibited":
            add("CONTROL", "block", "Product is classified as prohibited in supplied record", m.source)
        elif m.control == "unknown":
            add("CONTROL", "hold", "Controlled-substance classification missing", m.source)
        elif m.control == "none":
            add("CONTROL", "pass", "No special control indicated in supplied record", m.source)
        elif packet.controlled_permit is None:
            add("CONTROL.PERMIT", "hold", "Special controlled-substance authorization required", m.source)
        else:
            p = packet.controlled_permit
            if evidence("CONTROL.PERMIT", p.source, "CONTROL"):
                if p.supplier_tin != o.supplier_tin or p.category != m.control:
                    add("CONTROL.PERMIT", "block", "Special authorization does not cover supplier/category", p.source)
                elif p.status in ("suspended", "revoked") or p.valid_until < on:
                    add("CONTROL.PERMIT", "block", "Special authorization inactive or expired", p.source)
                elif p.status == "unknown":
                    add("CONTROL.PERMIT", "hold", "Special authorization not confirmed", p.source)
                else:
                    add("CONTROL.PERMIT", "pass", "Special authorization recorded", p.source)

        if m.minimum_shelf_days is None:
            add("SHELF_LIFE", "hold", "Confirmed required minimum remaining shelf life is missing", m.source)
        elif o.expiry_date < o.required_until or (o.expiry_date - on).days < m.minimum_shelf_days:
            add("SHELF_LIFE", "block", "Expired/short-dated for expected use or minimum shelf life", m.source)
        else:
            add("SHELF_LIFE", "pass", "Shelf-life requirement satisfied", m.source)

        if m.storage_type == "unknown":
            add("TEMPERATURE", "hold", "Official storage specification not confirmed", m.source)
        elif m.storage_type == "ambient":
            add("TEMPERATURE", "pass", "Ambient storage profile recorded; route monitoring still required by applicable SOP", m.source)
        elif packet.temperature_log is None:
            add("TEMPERATURE", "hold", "Temperature-sensitive product has no verified logger evidence", m.source)
        else:
            t = packet.temperature_log
            if evidence("TEMPERATURE", t.source, "TEMP"):
                if t.minimum_c < m.min_temp_c or t.maximum_c > m.max_temp_c:
                    add("TEMPERATURE", "block", "Observed storage/transport temperature excursion", t.source)
                else:
                    add("TEMPERATURE", "pass", "Recorded temperature inside labelled range", t.source)

        if m.rx:
            if packet.reference_price is None:
                add("REFERENCE_PRICE", "hold", "Prescription reference-price row missing", m.source)
            else:
                r = packet.reference_price
                if evidence("REFERENCE_PRICE", r.source, "REF_PRICE"):
                    if r.registration_no != o.registration_no or r.channel != o.channel:
                        add("REFERENCE_PRICE", "block", "Reference row is for another product/channel", r.source)
                    elif o.selling_price_incl_vat > r.maximum_incl_vat:
                        add("REFERENCE_PRICE", "block", "Sale price exceeds published reference ceiling with VAT", r.source)
                    else:
                        add("REFERENCE_PRICE", "pass", "Within reference ceiling (VAT-inclusive comparison)", r.source)
            if packet.price_basis is None:
                add("MARKUP", "hold", "Documented legal purchase/base cost missing", m.source)
            else:
                b = packet.price_basis
                if evidence("MARKUP", b.source, "PRICE_BASIS"):
                    multiplier = WHOLESALE_MARKUP if o.channel == "wholesale" else RETAIL_MARKUP
                    if o.selling_price_excl_vat > b.cost_excl_vat * multiplier:
                        add("MARKUP", "block", "15% wholesale / 20% retail ceiling exceeded (VAT-excl base)", b.source)
                    else:
                        add("MARKUP", "pass", "Within prescription markup limit for this channel", b.source)
        else:
            add("PRICE_SCOPE", "pass", "OTC: general Rx markup/reference ceilings not applied", m.source)

        if m.marking_required is None:
            add("MARKING", "hold", "Whether digital marking is mandatory for this product is unconfirmed", m.source)
    
    lic = packet.license
    if lic is None:
        add("LICENSE", "hold", "Supplier license missing")
    elif evidence("LICENSE", lic.source, "LICENSE"):
        if lic.supplier_tin != o.supplier_tin or lic.activity != o.channel:
            add("LICENSE", "block", "License does not cover supplier/channel", lic.source)
        elif lic.status in ("revoked", "suspended") or lic.valid_until < on:
            add("LICENSE", "block", "Supplier license invalid, suspended or expired", lic.source)
        elif lic.status == "unknown":
            add("LICENSE", "hold", "License status not confirmed", lic.source)
        else:
            add("LICENSE", "pass", "Supplier and permitted activity verified from supplied snapshot", lic.source)

    batch = packet.batch
    if batch is None:
        add("BATCH", "hold", "Batch certificate, release and recall status missing")
    elif evidence("BATCH", batch.source, "BATCH"):
        if batch.lot_number != o.lot_number or batch.registration_no != o.registration_no:
            add("BATCH", "block", "Batch record does not match exact product/lot", batch.source)
        elif batch.quality in ("recalled", "quarantined") or batch.certification == "revoked":
            add("BATCH", "block", "Batch recalled, quarantined or certification revoked", batch.source)
        elif batch.quality != "released" or batch.certification != "valid":
            add("BATCH", "hold", "Batch release/certification not confirmed", batch.source)
        else:
            add("BATCH", "pass", "Batch certified and released in supplied snapshot", batch.source)

    if m is not None and evidence_usable(m.source, on, mode, "MED_REG") and m.marking_required:
        marking = packet.marking
        if marking is None:
            add("MARKING", "hold", "Mandatory Asl Belgisi status evidence missing")
        elif evidence("MARKING", marking.source, "TRACE"):
            if marking.registration_no != o.registration_no or marking.lot_number != o.lot_number or marking.status == "invalid":
                add("MARKING", "block", "Digital marking not valid for exact product and lot", marking.source)
            elif marking.status == "unknown":
                add("MARKING", "hold", "Digital marking status unconfirmed", marking.source)
            else:
                add("MARKING", "pass", "Marking status matched to product and lot", marking.source)

    alert = packet.quality_screen
    if alert is None:
        add("QUALITY_ALERT", "hold", "Recent regulator alert/recall screening missing")
    elif evidence("QUALITY_ALERT", alert.source, "ALERTS"):
        if alert.registration_no != o.registration_no or alert.lot_number != o.lot_number:
            add("QUALITY_ALERT", "block", "Quality alert screening refers to another product/lot", alert.source)
        elif alert.status == "affected":
            add("QUALITY_ALERT", "block", "Batch affected by quality/recall alert", alert.source)
        elif alert.status == "unknown":
            add("QUALITY_ALERT", "hold", "Recall/quality alert status not confirmed", alert.source)
        else:
            add("QUALITY_ALERT", "pass", "No matching alert in operator-reviewed snapshot", alert.source)

    if o.public_procurement:
        pa = packet.public_authorization
        if pa is None:
            add("PUBLIC_PROCUREMENT", "hold", "Government procurement channel/GxP eligibility unverified")
        elif evidence("PUBLIC_PROCUREMENT", pa.source, "PUBLIC"):
            if pa.supplier_tin != o.supplier_tin or pa.status == "invalid" or not pa.authorized_channel or not pa.gxp_confirmed:
                add("PUBLIC_PROCUREMENT", "block", "Public channel or required supplier GxP evidence invalid", pa.source)
            elif pa.status == "unknown":
                add("PUBLIC_PROCUREMENT", "hold", "Government procurement permission uncertain", pa.source)
            else:
                add("PUBLIC_PROCUREMENT", "pass", "Operator-recorded public channel/GxP controls satisfied", pa.source)

    final: Decision = "blocked" if any(c.status == "block" for c in findings) else (
        "hold" if not findings or any(c.status == "hold" for c in findings) else "eligible"
    )
    return Result(route_id=o.route_id, decision=final, simulation=mode == "synthetic",
                  findings=tuple(findings), evidence_checked=verified, evidence_held=uncertain)
