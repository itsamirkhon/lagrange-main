"""Deterministic invented economic inputs; no real drug, supplier, invoice or registry data."""

from .models import PharmaCase


SYNTHETIC_CASE = {
    "id": "synthetic_cardio_2026",
    "title": "Illustrative imported cardiovascular SKU",
    "data_classification": "synthetic",
    "currency": "UZS",
    "annual_demand_packs": 150_000,
    "lot_size_packs": 500,
    "brief_retail_ceiling_uzs": 150_000,
    "assumed_registered_wholesale_cap_uzs": 125_000,
    "assumed_max_contract_wholesale_uzs": 125_000,
    "illustrative_markup_limit": 0.15,
    "working_capital_budget_uzs": 18_500_000_000,
    "baseline_option_id": "standard_road",
    "options": [
        {
            "id": "standard_road", "name": "Status quo: road transport",
            "description": "Invented reference corridor; allows 100% of demand as a feasible baseline.",
            "factory_cost_uzs": 97_000, "import_fees_uzs": 7_200, "freight_uzs": 4_496,
            "below_line_cost_uzs": 13_200, "fx_exposed_fraction": 0.9,
            "capacity_packs": 150_000, "min_shipment_packs": 5_000,
            "max_shipment_packs": 20_000, "max_shipments": 16,
            "shipment_fixed_uzs": 300_000, "activation_fixed_uzs": 0,
        },
        {
            "id": "consolidated_rail", "name": "Consolidated rail",
            "description": "Lower freight, longer lead time (not explicitly scheduled in this MVP).",
            "factory_cost_uzs": 98_000, "import_fees_uzs": 7_000, "freight_uzs": 3_000,
            "below_line_cost_uzs": 9_500, "fx_exposed_fraction": 0.9,
            "capacity_packs": 75_000, "min_shipment_packs": 10_000,
            "max_shipment_packs": 25_000, "max_shipments": 4,
            "shipment_fixed_uzs": 600_000, "activation_fixed_uzs": 6_000_000,
        },
        {
            "id": "air_priority", "name": "Priority air freight",
            "description": "Fast but higher variable freight; capacity constrained.",
            "factory_cost_uzs": 95_500, "import_fees_uzs": 7_100, "freight_uzs": 7_500,
            "below_line_cost_uzs": 9_300, "fx_exposed_fraction": 0.9,
            "capacity_packs": 35_000, "min_shipment_packs": 5_000,
            "max_shipment_packs": 10_000, "max_shipments": 5,
            "shipment_fixed_uzs": 2_000_000, "activation_fixed_uzs": 1_000_000,
        },
        {
            "id": "regional_pool", "name": "Regional pooling / 3PL",
            "description": "Shared logistics arrangement; eligible and operationally ready is an assumption.",
            "factory_cost_uzs": 98_800, "import_fees_uzs": 6_700, "freight_uzs": 3_000,
            "below_line_cost_uzs": 7_800, "fx_exposed_fraction": 0.75,
            "capacity_packs": 80_000, "min_shipment_packs": 10_000,
            "max_shipment_packs": 20_000, "max_shipments": 5,
            "shipment_fixed_uzs": 700_000, "activation_fixed_uzs": 12_000_000,
        },
        {
            "id": "credit_terms", "name": "Extended payment terms",
            "description": "Illustrates working-capital economics with an assumed higher invoice cost.",
            "factory_cost_uzs": 100_000, "import_fees_uzs": 6_000, "freight_uzs": 4_000,
            "below_line_cost_uzs": 7_300, "fx_exposed_fraction": 0.6,
            "capacity_packs": 60_000, "min_shipment_packs": 5_000,
            "max_shipment_packs": 15_000, "max_shipments": 5,
            "shipment_fixed_uzs": 450_000, "activation_fixed_uzs": 3_500_000,
        },
    ],
    "scenarios": [
        {
            "id": "base", "name": "Base case", "probability": 0.65,
            "fx_change_pct": 0, "freight_change_pct": 0,
            "wholesale_ceiling_change_pct": 0,
        },
        {
            "id": "fx_rise", "name": "UZS depreciation", "probability": 0.20,
            "fx_change_pct": 12, "freight_change_pct": 0,
            "wholesale_ceiling_change_pct": 0,
        },
        {
            "id": "freight_rise", "name": "Freight disruption", "probability": 0.15,
            "fx_change_pct": 0, "freight_change_pct": 35,
            "wholesale_ceiling_change_pct": 0,
        },
    ],
}


def demo_case() -> PharmaCase:
    return PharmaCase.model_validate(SYNTHETIC_CASE)
