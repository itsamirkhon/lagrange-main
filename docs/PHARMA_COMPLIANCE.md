# LAGRANGE Rx — pharmaceutical regulatory preflight (18 Nov 2026)

## Scope and status

**Implemented in the feature module:** deterministic screening of an exact medicine, supplier, batch and proposed sale price; three-way **eligible / hold / blocked** decision; independent source metadata; release/matching checks; mandatory source freshness; exact VAT price normalization; controlled substance permits; temperature evidence; recall/quality alerts; trace marking; public procurement screening; a safe screen-to-optimizer boundary; synthetic interactive judge examples.

**Not implemented / UNVERIFIED:** live Lex.uz/Asl Belgisi/license/price/medicine API access; official registry bulk imports tailored to the actual external schema; signed regulator responses; authenticated reviewer workflow and permissions; clinical validation; automatic ordering; production legal sign-off. No affiliation, certification, API approval or Ministry of Justice application is implied.

The legacy `POST /engine/optimize` still verifies the repository's *numerical* model; it is **not** a complete regulatory check. The new `POST /engine/optimize/regulated` is the only endpoint that screens *every offered route* through operator-reviewed evidence before solving. It is still decision support, not authorization to buy or sell pharmaceuticals.

## Regulatory sources and legal basis to review with Uzbek counsel

- Prescription price regulation: Presidential Decree [PF-13](https://lex.uz/uz/docs/7345614), Resolution [185](https://lex.uz/uz/acts/3155770) (including 2025/2026 amendments), and reference-price recording order [registration No. 3735](https://lex.uz/pdffile/7952162). The **15% wholesale** and **20% retail** markup limits and relevant reference price limits apply to *prescription medicines*, not all OTC sales. The price base and VAT treatment can differ by business flow; the runtime requires an explicit documented base, channel, declared VAT rate and independently checked published ceiling. This requires legal/financial review for each client.
- Registration/specifications: [state medicines register](https://old.uzpharm-control.uz/ru/pages/state-register-of-medicines-and-medical-products). Identity match is exact across registration number, name, strength, dosage, manufacturer and pack size. Unmatched or unverified -> hold/block.
- Published ceiling per exact medicine: [reference-price register](https://mpa.uz/en/reference-prices), with snapshot date; no claim of live API access.
- Supplier licensing: [license.gov.uz](https://license.gov.uz/); verify supplier TIN, activity, status, license scope and premises *as applicable* using legally authorized access. This prototype checks identity, activity and validity; facility-specific scope is **UNVERIFIED**.
- Product batches: [certification registry](https://old.uzpharm-control.uz/ru/registries/certified-medicines) and [quality/recall notices](https://old.uzpharm-control.uz/ru/pages/information-on-identified-falsified-and-substandard-unsuitable-drugs-and-medical-products). Certification and absence of relevant regulatory alerts require separate evidence in this implementation.
- Marking: [Asl Belgisi](https://aslbelgisi.uz/) and authorized trace data. A certificate alone does not establish a valid marking code; separate evidence is required when marking applies.
- Controlled substances: [strong-acting substances Resolution 818](https://lex.uz/acts/-4532164), plus current narcotic/psychotropic schedules and corresponding activity authorizations. The app never conflates *prohibited* with *controlled under license*.
- Public procurement: [Resolution 737 (21 Nov 2025)](https://lex.uz/uz/acts/-7851804), amended in 2026, and Farma.xarid.uz requirements. Public procurement requires a separate workflow/GxP check; LAGRANGE Rx does not replace the mandatory purchasing platform.

These URLs are references for human review, not upstream APIs called by the code. A responsible pharmacist and Uzbekistan-qualified lawyer must verify current effective versions, exceptions, applicable activities and the correct tax base before using the checks in business operations.

## Trust boundaries and data handling

1. **Government source ↔ trusted operator:** a qualified reviewer downloads an official export, records exact URL, record ID, retrieval/effective dates, name, and SHA-256 digest. This code does not independently prove that downloaded content was issued by the regulator. No invented record may be labeled official.
2. **Company records ↔ reviewer:** documented legal purchase/base cost and temperature logs are internal company evidence, not government records. Their sources are tagged `reviewed_internal`, distinct from official registers.
3. **Operator ↔ API caller:** arbitrary JSON passed to `/pharma/compliance/evaluate` is caller-supplied, even with `reviewed_by` and `sha256`; the response says `operator_supplied_snapshots_only`, `can_auto_order=false`. A reviewed_by string is **not** authentication. A production deployment needs server-side controlled evidence storage, reviewer identities, role-scoped authorization, tamper-evident audit logs, and scheduled source reconciliation.
4. **Regulatory preflight ↔ optimizer:** `screen_routes` accepts only an exact product ID + route ID + supplier ID match, rejects duplicated/unknown routes, defaults missing routes to **hold**, ensures the published reference price equals the engine ceiling, and only exposes fully eligible routes to the regulated optimization endpoint. The older research endpoint remains explicitly unverified.
5. **Human approval ↔ procurement execution:** the system never places orders. Even `eligible` means *passed the supplied checks*, not certified legal. No automatically executed purchases.

Source age limit: **30 days** (conservative product policy, not a legal interval). Expired, unreviewed, future-dated, mismatched or missing evidence -> hold; proven invalid -> block. A stopped external feed or stale registry cannot silently become 'passed'. `synthetic` evidence cannot pass an `operator_snapshot` screen. No HTTP calls or scraping are used by the checker (reduces SSRF/external dependency risk).

## Run the sample and evidence verification

```bash
python -m pytest tests/engine/test_regulatory_gate.py tests/engine/test_regulatory_filter.py tests/engine/test_regulatory_api.py tests/engine/test_regulatory_import.py -q
# start normal app; choose 'Юридическая проверка' in the menu; switch scenarios
lagrange-rx serve
# For each manually acquired official export, make a reviewed manifest with real SHA-256:
python scripts/verify_regulatory_snapshot.py --manifest /safe/manifest.json --root /safe/raw_exports --on 2026-11-18
```

Example manifest structure (illustrative ONLY; fill the real digest from an actual downloaded file, not this placeholder):

```json
{
  "version": 1,
  "rows": [{
    "path": "medicine_prices_2026-11-18.xlsx",
    "source": {
      "system": "REF_PRICE",
      "record_id": "EXACT-REGISTRY-RECORD-ID",
      "url": "https://mpa.uz/en/reference-prices",
      "retrieved_on": "2026-11-18",
      "kind": "reviewed_official",
      "reviewed_by": "ACTUAL-REVIEWER",
      "sha256": "REPLACE-WITH-64-CHARACTERS-OF-ACTUAL-FILE-HASH"
    }
  }]
}
```

`verify_regulatory_snapshot.py` verifies file bytes against metadata and rejects path escapes and duplicate records. It **does not** parse arbitrary regulator XLSX layouts or independently check the issuer. A separate controlled mapping and dual review are required to turn raw exports into `Packet` records. Do not commit confidential bids, staff credentials or supplier customer records to the public repository.

## Demo sequence for mentors

1. Open **Юридическая проверка** → **Корректная партия**. Output is `eligible` only in **synthetic** mode; the screen clearly says no live government integration.
2. Choose **Цена выше лимита**: both reference-price and markup violations are detected independently.
3. Choose **Отозвана серия**, **Лицензия отозвана**, **Нарушена температура** or **Недействительная маркировка**: a cheaper proposal is blocked before optimization.
4. Choose **Нет документа**, **Спецконтроль без разрешения**, or **Госзакупка без подтверждения**: uncertain evidence is held, not magically approved.
5. Use the existing **FEFO** screen to demonstrate selecting only suitable warehouse lots with earliest expiry. FEFO is existing-stock allocation; monthly integrated planning is NOT yet implemented.

## Before any real pilot

- Ministry/Adolat and each registry operator: request published export/API agreements, terms of use, refresh cadence and authorized use in commercial software. Do not say approval is pending until an application is actually submitted.
- Pharmacist/legal review: verify latest relevant statutes, exception cases, controlled-substance schedules, batch quality/marking requirements, reference pricing, VAT treatment, facility license scope, procurement and disposal procedures.
- Engineering: add authenticated server-side signed snapshot store, roles, data retention, immutable audit trail, freshness monitoring, source synchronization, live sandbox contract tests, supervised exception workflow, exact multi-batch route aggregation, and end-to-end optimize→approval→procurement handoff with explicit stop conditions.
- Test on genuine, permissioned records including adverse cases; rerun all CI, browser and negative authorization tests before any production data flows. A successful simulated demo is not production validation.
