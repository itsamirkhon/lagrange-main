"""Domain models for product."""
from __future__ import annotations

from datetime import date
from typing import Literal
from pydantic import Field, model_validator
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
    inn: str | None = None
    dosage_form: str | None = None
    strength: str
    pack_size: int = Field(gt=0)
    rx: Literal[True]
    manufacturer: str | None = None
    registration_certificate: str | None = None
    is_generic: bool | None = None
    originator_sku: str | None = None
    # Optional pharmaceutical safety profile; absent means NOT ASSESSED, never "safe".
    storage_temperature_min_c: float | None = None
    storage_temperature_max_c: float | None = None
    min_remaining_shelf_life_days: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def validate_storage_range(self):
        low, high = self.storage_temperature_min_c, self.storage_temperature_max_c
        if (low is None) != (high is None):
            raise ValueError("both storage temperature bounds are required together")
        if low is not None and low > high:
            raise ValueError("minimum storage temperature exceeds maximum")
        return self
