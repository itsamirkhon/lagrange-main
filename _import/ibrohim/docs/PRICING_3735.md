# Price rules under Order No. 3735 — what they mean for the model

Status: **research note, 2026-11-17.** Source: Minister of Health order No. 23 of 10 Dec 2025, registered by the Ministry of Justice 25 Dec 2025 as **No. 3735**, effective **28 Mar 2026** (replaces No. 3242). Official Uzbek text: https://lex.uz/docs/7952162 (metadata: https://lex.uz/docs/7952213).

> **Verification caveat.** The points below were extracted from the Uzbek text through machine translation. Before any number goes on a slide, a native Uzbek reader must check points 11, 12, 19, 20 and 22 against the original. Items marked **VERIFY** are the ones most likely to be misread.

## 1. What the order says (summary by point)

**p.11 — reference price `Rn` of an imported Rx drug.** The registry system computes three candidates:
1. average price across reference countries;
2. official price in the country of origin plus insurance and transport, capped at 15% of the product price;
3. 12-month average **CIP import price** from Customs Committee data.

The lowest candidate is recommended as `Rn`. **VERIFY:** one extraction said "lowest of these values (other than the first)", so check which candidates the minimum is taken over. The applicant files in USD, EUR or RUB at the CBU rate on the filing date, and the registry records prices in UZS.

**Appendix 2 — reference countries (10):** Hungary, Russia, Ukraine, Poland, Turkey, Romania, Kazakhstan, Bulgaria, Moldova, Egypt. A country price is excluded if it is more than 50% above the prices in two or more other reference countries.

**p.12:**
- If one manufacturer sells the same composition under different trade names, the lowest reference price applies to all of them.
- A generic is capped at ≤ 80% of the originator's reference price.
- A biosimilar is capped at ≤ the originator's reference price.

**p.19 — maximum prices (registry ceilings, VAT included):**

```
Imported:  ChUN  = (Rn + B + Bx + QQS) * UU        # max wholesale
           ChChN = ChUN * ChU                      # max retail (the order prints "CНU", a typo for ChU)
Domestic:  ChUN  = (Rn + QQS) * UU
           ChChN = ChUN * ChU
B   = customs payments in UZS, excluding VAT
Bx  = other expenses = 2% of Rn                    # a fixed allowance, NOT actual logistics cost
QQS = VAT 12%  (applied to Rn + B + Bx; VERIFY)
UU  = 1.15 (wholesale markup), ChU = 1.20 (retail markup), regardless of the number of intermediaries
```

**p.20 — indexation of imported drugs.** Automatic indexation by the CBU rate on **1 January and 1 July**, triggered only if the UZS value of the foreign-currency `Rn` has risen by **≥ 3%**. If the rise is **> 5%**, indexation happens **immediately**, outside the schedule. **VERIFY:** the actual lag of "immediately" in practice.

**p.22 — downward review.**
- The registry is reviewed every 6 months.
- If a drug's price has fallen by **≥ 15%** against its registered price, the applicant must file for re-registration within **10 working days**.
- If the applicant does not file, the authority re-registers the price itself. The retail ceiling follows **3 months** later.

## 2. The case, reconciled with VAT

The case gives only the 150,000 UZS retail ceiling. If that ceiling is the registry `ChChN` and both markups are at their maximum:

| Quantity | UZS / pack | Derivation |
|---|---|---|
| Max retail `ChChN` (incl. VAT) | 150,000 | case |
| Max wholesale `ChUN` (incl. VAT) | 125,000 | 150,000 / 1.20 |
| `(Rn + B + Bx) × 1.12` | 108,696 | 125,000 / 1.15 |
| **`Rn + B + Bx` (excl. VAT)** | **97,050** | / 1.12 |
| Max wholesale **excl. VAT** | **111,607** | 125,000 / 1.12 |
| Gross pot excl. VAT, if actual landed cost = ceiling base | **14,557** (13.0% of net revenue) | 111,607 − 97,050 |
| Illustrative split, assuming B ≈ 2% duty | Rn ≈ 93,300; B ≈ 1,870; Bx ≈ 1,870 | assumption |

**Consequence for the repo:** `demo_data.py` prices at 125,000 against a 108,696 base with no VAT. That overstates the importer's pot: 16,304 vs ≈ 14,557 UZS per pack excl. VAT. Below-line costs then need re-calibrating if the demo should still land on "2–3%".

## 3. Three implications for the optimizer

1. **The ceiling is built from reference values, not from the importer's invoice.**
   - `ChUN` depends on `Rn`, `B` and a flat `Bx = 2% Rn`. Real freight, insurance and other costs above that 2% allowance are not passed into the ceiling, so logistics savings fall straight to margin.
   - The separate **15%-of-actual-purchase-price** markup cap (Decree PF-13 / CM Resolution 185, enforced through the base price on e-invoices) is a second, independent bound. The cost-plus paradox holds **only through that second bound**: price ≤ min(ChUN, 1.15 × actual base). **VERIFY with a pharma accountant** that the 15% is checked against the importer's actual base price per invoice.

2. **FX shocks are passed into the ceiling, with a lag.**
   - Under p.20, an FX move above 5% triggers immediate indexation; moves of 3–5% are indexed on 1 Jan / 1 Jul; moves under 3% are absorbed.
   - Holding the cap fixed in a +15% FX scenario is therefore **wrong under No. 3735**. Illustratively, +15% FX re-indexes `ChUN` from 125,000 to ≈ 143,750.
   - The real FX risk is **timing plus the < 3% band plus stock bought at the old rate**. Stock bought before the shock can even gain once the cap is indexed. Model the cap as `index(Rn, fx, rule p.20, lag)` instead of a constant.

3. **There is regulatory feedback from your own import price.**
   - The 12-month average customs CIP is one of the `Rn` candidates.
   - Buying cheaper lowers that average and can **lower the future ceiling** at the 6-month review, which forces re-registration if the drop is ≥ 15%. That is a ratchet.
   - The optimizer should treat today's contract price as affecting tomorrow's cap whenever the CIP candidate is the binding minimum. **VERIFY** whether the CIP average is product-wide (all importers) or per applicant.

Also relevant: since 1 July 2026, cardiovascular reference prices were cut by up to 37% (Kun.uz, 6 Jul 2026). Downward revisions are a real, observed scenario.

## 4. Proposed model changes (for the build team; not yet implemented)

- Add `vat_rate = 0.12`. Compute all profit excl. VAT and display ceilings incl. VAT, matching the registry.
- Replace `assumed_registered_wholesale_cap_uzs` with inputs `{Rn_fx, fx_currency, B_uzs, filing_fx_rate}`. Derive `ChUN` and `ChChN` by the p.19 formula and keep the registry value as a cross-check.
- Scenario FX: index the cap per p.20 (thresholds 3% / 5%, a `lag_days` parameter). Keep a separate "official downward revision" scenario (p.22, observed −37% for cardio in July 2026).
- Keep `price ≤ 1.15 × actual_base` as an explicitly labelled **assumption to verify**, with a UI toggle so judges can see the paradox appear and disappear.
- Optional extension: a multi-period model where `Rn_{t+1}` depends on the trailing average CIP (the ratchet).

## 5. Open questions (owner: business/pitch stream)

1. Native-reader check of p.11 (which candidates the minimum is taken over), the VAT base in p.19, and the lag in p.20.
2. Pharma accountant: is the 15% wholesale markup checked against the actual invoice base (e-invoice "base price") for Rx?
3. A real cardiovascular SKU with retail ceiling ≈ 150,000 UZS: trade name, INN, manufacturer, country, registration number, ChUN, ChChN, registry date. Sources: mpa.uz registry, or the Pharmaceutical Safety Center Telegram price lists.
