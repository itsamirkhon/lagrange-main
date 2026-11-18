# Scope and staged product roadmap

2026-11-17: This delivery implements **LAGRANGE Rx**, the integrated quantitative decision lab for hackathon Problem #16. It uses the team's MARJA document as **future product context**, not as a claim that six modules have been built.

## What is built today

- Single-period, common-price, risk-adjusted MILP using synthetic five-route data.
- Three scenario outcomes, independent post-solve auditing, baseline comparisons.
- Scenario sliders, infeasibility demonstration, cost-plus model experiment, evidence JSON export.
- API contract, pricing/economics tests, brute-force oracle, browser interaction test.

## Deliberately not built

- **Twin / rules as code:** Formal jurisdiction-specific pricing validation and real medicine registry ingestion. First verify No. 3735 plus PF-13 and have qualified regulatory reviewers sign off. Store source, effective date, price tax basis and exact SKU identity.
- **Supply:** Seasonal demand forecasting and two-stage inventory/transport optimization with lead times, expiry and capital financing. Requires genuine historic SKU sales, stockout records and delivery timelines. Test holdout forecasts and risk calibrations.
- **Pool:** Multi-importer consolidated transport; requires freight contracts, real route compatibility and organizational consent. A simple 3PL route is only hypothetical in this demo.
- **Reach:** Causal uplift for marketing. It requires credible randomized/quasi-experimental exposure data; fabricated causal labels do not validate effectiveness.
- **Adhere:** Patient reminders and repeat-prescription access. Requires clinical/privacy/legal review, patient consent and the exclusion of prohibited prescription advertising. Not a hackathon shortcut.
- **Localize:** Manufacturing/packaging options and real-options timing. Requires validated CMO contracts, GMP/regulatory pathways, capital costs, and uncertainty calibration.
- **Copilot:** Bilingual Uzbek/Russian LLM tool caller. Build only after validating structured tool invocation, citations, and numerical agreement with solver response. The deterministic UI today is not an LLM copilot.
- **Business validation:** A participating distributor's permissioned cost data and prospective pilot, plus clear counterfactual measurement before any success-fee claim.

## Suggested next stage, in order

1. Replace the invented 125,000 UZS cap with an actual verified registered SKU/price, recording the official source and VAT basis.
2. Obtain non-sensitive, authorized supplier quote samples and implement evidence-backed document extraction with a labeled benchmark.
3. Extend to dated inventory and shipment arrival decisions; first add cases with known tiny-instance optimal solutions.
4. Add demand scenarios calibrated on actual demand/stockout history; measure holdout forecast and risk diagnostics.
5. Only then consider broad MARJA product modules and commercial pilot.
