# Data contract v2 — end-to-end flow (proposal for stage 0)

Status: **proposal, 2026-11-17.** It extends the existing `models.py` contract without breaking `SupplyOption` / `PharmaCase` / `OptimizeResponse`. All streams (math, AI ingest, frontend, eval) build against these shapes. Changes after hour 2 go through one owner.

Target flow:

```
upload quote → extract fields with evidence → human confirms → solve → explain + verify → shock → re-solve or infeasible
```

## 1. Provenance — attached to every number

```jsonc
"provenance": {
  "kind": "official" | "user_provided" | "extracted_confirmed" | "synthetic" | "assumption",
  "source": "lex.uz/docs/7952162 p.19" | "quote_2026-11-18_carrierA.pdf p.2" | "demo_data.py",
  "retrieved_at": "2026-11-17T19:40:00+05:00",
  "note": "optional free text"
}
```

The UI shows a badge per number: official / user / AI-extracted & confirmed / synthetic / assumption. The solver **rejects** any input whose kind is `extracted_unconfirmed`.

## 2. RegistryRecord — the regulated price for one SKU

```jsonc
{
  "sku_id": "string",
  "trade_name": "string", "inn": "string", "manufacturer": "string", "country": "string",
  "registration_number": "string | null",
  "rx": true,
  "rn_fx": 0.0, "rn_currency": "USD|EUR|RUB", "filing_fx_rate_uzs": 0.0,
  "customs_b_uzs": 0.0,
  "bx_share": 0.02,           // order 3735 p.19
  "vat_rate": 0.12,
  "wholesale_markup": 0.15, "retail_markup": 0.20,
  "chun_uzs_incl_vat": 0.0,   // as published; cross-checked against the p.19 formula
  "chchn_uzs_incl_vat": 0.0,
  "registry_date": "YYYY-MM-DD",
  "provenance": { ... }
}
```

Derived (never typed in by hand): `chun_formula = (Rn·rate + B + 0.02·Rn·rate)·1.12·1.15`. If `|chun_formula − chun_published| / chun_published > 1%`, show a warning.

## 3. QuoteExtraction — Ingest output, one per uploaded document

```jsonc
{
  "document_id": "sha256",
  "file_name": "string", "pages": 0,
  "kind": "supplier_quote" | "freight_quote" | "unknown",
  "fields": [
    {
      "name": "unit_price" | "currency" | "incoterms" | "moq_packs" | "lead_time_days"
              | "capacity_packs" | "shelf_life_months" | "freight_per_shipment" | "payment_terms_days",
      "value": "number | string",
      "unit": "UZS|USD|EUR|RUB|packs|days|months|null",
      "evidence": { "page": 1, "text": "exact span from document", "bbox": [0,0,0,0] },
      "confidence": 0.0,
      "checks": ["currency_present", "price_in_market_range", "integer_moq"],
      "status": "extracted_unconfirmed" | "confirmed" | "edited_by_user" | "rejected"
    }
  ],
  "maps_to_option_id": "string | null",
  "warnings": ["string"]
}
```

Rules:
- A confirmed extraction becomes a `SupplyOption` (existing model) with `provenance.kind = "extracted_confirmed"`.
- An edited value keeps the original extraction for audit.

## 4. Scenario — extended for order 3735 p.20 / p.22

New optional fields on the existing `Scenario` (defaults preserve current behaviour):

```jsonc
"cap_rule": "fixed" | "index_p20" | "official_revision",
"index_lag_days": 0,
"official_revision_pct": 0.0      // e.g. -37 for the July 2026 cardio cut
```

## 5. OptimizeResponse — additions for the "explain + verify" step

```jsonc
"binding_constraints": [{ "name": "registry_ceiling|markup_15pct|capacity:route|budget|demand", "scenario_id": "..." }],
"checks": [{ "name": "string", "passed": true, "detail": "string" }],      // independent auditor output
"infeasibility": { "conflicting_constraints": ["..."], "human_explanation": "..." } | null,
"snapshot_id": "sha256(inputs + scenarios + solver version + code commit)",
"what_if": [{ "change": "budget +1bn UZS", "method": "re-solve", "objective_delta_uzs": 0.0 }]
```

`what_if` values come only from re-solving. MILP dual values are never presented as marginal prices.

## 6. Ownership

| Stream | Owns | Consumes |
|---|---|---|
| Math | `economics.py`, `solver.py`, sections 2, 4, 5 | RegistryRecord, SupplyOption |
| AI ingest | QuoteExtraction (section 3), extraction benchmark | — |
| Frontend | `web/*` | all response shapes |
| Eval | `tests/*`, `evidence/*` | everything |
| Business | the RegistryRecord for a real SKU, PRICING_3735 open questions | — |
