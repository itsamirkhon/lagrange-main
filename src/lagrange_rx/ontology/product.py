"""Domain models for product."""
from __future__ import annotations

from datetime import date
from typing import Literal
from pydantic import Field
from .trust import Entity

class Manufacturer(Entity):
    name: str
    country: str
    same_composition_skus: list[str] = Field(default_factory=list)

class RegistrationCertificate(Entity):
    number: str
    valid_from: date
    valid_to: date
    holder: str

class Product(Entity):
    sku_id: str
    trade_name: str
    inn: str
    dosage_form: str
    strength: str
    pack_size: int = Field(gt=0)
    rx: Literal[True]
    manufacturer: str
    registration_certificate: str
    is_generic: bool | None = None
    originator_sku: str | None = None
