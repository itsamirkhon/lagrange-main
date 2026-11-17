"""Domain constants, data contracts, and deterministic synthetic entity generators."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np

WATERMARK_TEXT = "УЧЕБНЫЙ ОБРАЗЕЦ / SYNTHETIC"

VALID_OPERATORS: tuple[str, ...] = (
    "N1",
    "N2",
    "C1",
    "C2",
    "C3",
    "V1",
    "U1",
    "L1",
    "X1",
    "X2",
    "S1",
    "M1",
    "D1x",
    "A1",
    "R1",
    "R2",
    "R3",
    "R4",
    "J1",
)

DOC_TYPES: tuple[str, ...] = (
    "D1",
    "D2",
    "D3",
    "D4",
    "D5",
    "D6",
    "D7",
    "D8",
    "D9",
    "D10",
    "D11",
    "D12",
    "D13",
)

_SUPPLIER_ROOTS: tuple[str, ...] = (
    "Medisynth",
    "AnatoliaBio",
    "BalticGalen",
    "KardiaLabs",
    "VektorPharm",
    "NoorRemedies",
)

_SUPPLIER_SUFFIXES: tuple[str, ...] = (
    "Pvt. Ltd.",
    "A.S.",
    "GmbH",
    "S.A.",
)

_CARRIER_PREFIXES: tuple[str, ...] = (
    "SilkWay",
    "SteppeTrans",
    "TuranCargo",
    "NavoiFreight",
    "AralRail",
    "SkyBridge",
)

_CARRIER_SUFFIXES: tuple[str, ...] = (
    "Logistics",
    "Forwarding",
    "Express",
    "Lines",
)

_FIRST_NAMES_RU_UZ: tuple[str, ...] = (
    "Азиз",
    "Дилноза",
    "Тимур",
    "Мадина",
    "Фарход",
    "Нодира",
    "Сардор",
    "Гульнора",
    "Рустам",
    "Шахноза",
)

_LAST_NAMES_RU_UZ: tuple[str, ...] = (
    "Каримов",
    "Рахимова",
    "Турсунов",
    "Алимова",
    "Исмаилов",
    "Юлдашева",
    "Ходжаев",
    "Мирзаева",
    "Назаров",
    "Садыкова",
)

_STREETS: tuple[str, ...] = (
    "ул. Синтетическая",
    "пр. Учебный",
    "ул. Логистическая",
    "ул. Фармацевтов",
    "ул. Навоийская (учебн.)",
    "ул. Индустриальная",
)

_COUNTERPARTY_TEMPLATES: dict[str, tuple[str, ...]] = {
    "distributors": (
        "ООО «ShifoMed Plus»",
        "ООО «O'rtaOsiyo Farm»",
        "ООО «Sog'lom Savdo Opt»",
        "ООО «Turan Med Trade»",
        "ООО «BaxtFarm Distribution»",
    ),
    "pharmacy_chains": (
        "Сеть аптек «Dorixona 24»",
        "Сеть аптек «Salomatlik Pharm»",
        "Сеть аптек «NurDorixona»",
        "Сеть аптек «MedLine Apteka»",
        "Сеть аптек «Avicenna Plus»",
    ),
    "hospital_tenders": (
        "Кардиологический центр ({city})",
        "Многопрофильная клиника ({city})",
        "Областной медцентр ({city})",
        "Госпитальный центр ({city})",
    ),
}


@dataclass
class SheetSpec:
    """Declarative specification for one Excel worksheet."""

    name: str
    headers: list[str] = field(default_factory=list)
    rows: list[list[Any]] = field(default_factory=list)
    col_widths: list[float] | None = None
    merged_cells: list[str] = field(default_factory=list)
    pre_header_rows: list[list[Any]] = field(default_factory=list)


@dataclass
class CorruptionRecord:
    """Single logged corruption event matching SIMULATION.md Section 7."""

    operator: str
    field: str
    location: dict[str, Any]
    true_value: Any
    shown_value: Any
    recoverable: bool
    recover_hint: str

    def to_dict(self) -> dict[str, Any]:
        """Serialize corruption record to plain dict."""
        return asdict(self)


@dataclass
class DocumentArtifact:
    """Manifest entry for one generated document artifact."""

    document: str
    doc_type: str
    truth_refs: list[str]
    corruptions: list[CorruptionRecord] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialize artifact and its corruptions to plain dict."""
        return {
            "document": self.document,
            "doc_type": self.doc_type,
            "truth_refs": list(self.truth_refs),
            "corruptions": [c.to_dict() for c in self.corruptions],
        }


def _pick(rng: np.random.Generator, items: tuple[str, ...]) -> str:
    return items[int(rng.integers(0, len(items)))]


def generate_supplier_name(rng: np.random.Generator, country: str | None = None) -> str:
    """Generate a fictional pharmaceutical supplier name."""
    root = _pick(rng, _SUPPLIER_ROOTS)
    suffix = _pick(rng, _SUPPLIER_SUFFIXES)
    tag = f" ({country}, SYNTHETIC)" if country else " (SYNTHETIC)"
    return f"{root} {suffix}{tag}"


def generate_carrier_name(rng: np.random.Generator) -> str:
    """Generate a fictional logistics carrier name."""
    return f"{_pick(rng, _CARRIER_PREFIXES)} {_pick(rng, _CARRIER_SUFFIXES)} (SYNTHETIC)"


def generate_person_name(rng: np.random.Generator, locale: str = "ru_uz") -> str:
    """Generate a fictional employee or contact person name."""
    _ = locale
    return f"{_pick(rng, _FIRST_NAMES_RU_UZ)} {_pick(rng, _LAST_NAMES_RU_UZ)}"


def generate_address(rng: np.random.Generator, region_name: str = "Ташкент") -> str:
    """Generate a clearly synthetic Uzbekistan business address."""
    building = int(rng.integers(1, 150))
    return f"Узбекистан, {region_name}, {_pick(rng, _STREETS)}, д. {building} (SYNTHETIC)"


def generate_synthetic_inn(rng: np.random.Generator) -> str:
    """Generate a synthetic tax identifier outside real company patterns."""
    digits = int(rng.integers(100000, 999999))
    return f"000{digits} (SYNTHETIC)"


def generate_counterparty_name(
    rng: np.random.Generator,
    channel: str,
    region_name_ru: str = "г. Ташкент",
) -> str:
    """Generate a deterministic synthetic Uzbek pharmacy, distributor, or clinic name."""
    if channel not in _COUNTERPARTY_TEMPLATES:
        raise ValueError(f"Unknown sales channel: {channel}")
    template = _pick(rng, _COUNTERPARTY_TEMPLATES[channel])
    city = region_name_ru.replace("г. ", "").replace("Республика ", "")
    return f"{template.format(city=city)} (SYNTHETIC)"
