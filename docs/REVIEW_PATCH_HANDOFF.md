# LAGRANGE Rx code-review repair handoff

Date: 2026-11-18 (Asia/Tashkent). Target repository: `itsamirkhon/lagrange-main`.
Base commit: `59539deb57489d07831aad3c1d7973f73f7b4f63`.

## What changed and why

This is an incremental repair to the existing monorepo, **not a second implementation**. It preserves the team's ontology, ingestion, two-stage optimizer, simulation evaluation, 17-rule catalog, and seven-screen browser journey.

1. **No more vacuous winning decisions.** The public `/engine/optimize` endpoint explicitly requires a minimum expected fill rate, default **0.90**. This is an illustrative business policy, not a clinical/legal target. The raw mathematical optimizer can still run unconstrained when explicitly invoked by research/mini-oracle tests. The UI exposes the policy and returns a named infeasibility instead of silently accepting an empty purchasing plan.
2. **No more zero-supply legal pass.** On no plan or zero ordered packs, `legal: null`, `legal_status: not_evaluated`. A non-empty plan is checked against encoded constraints, with no implied regulatory certification. The full evaluation's 10,000-case legality gate explicitly fails as *not evaluated* for zero supply.
3. **Comparability in evaluation.** All business-level evaluation runs use the same specified 90% minimum expected supply, including EVPI/VSS/regret comparisons. Existing thresholds in `eval/thresholds.json` remain unchanged. If a constrained plan is infeasible, the evaluation returns a failing report instead of throwing or inventing a plan.
4. **Bounded uploads.** The ingest endpoint caps files and raw byte counts; compressed archives are bounded in member count and expanded bytes. This reduces but does not eliminate parser resource risk.
5. **OCR runtime.** Docker installs Tesseract data for English/Russian/Uzbek Latin. The offline **evaluation** parser intentionally remains no-OCR; the additional languages help the production ingest route, not magically fix L1-L4 evaluations.
6. **Decision-focused UI.** Minimum-service control, clear synthetic provenance, truthful no-supply states, model-verification vs legal-certification wording, presentation mode, skip link, responsive/keyboard-focused CSS. Existing design tokens and Russian seven-screen IA are preserved; no framework migration.
7. **Automated readiness.** New regression tests, real Chromium smoke script, synthetic demo-graph setup, release report consistency gate, and GitHub Actions jobs (not previously present).

## Apply without overwriting team work

Use the ZIP root `tools/apply_fixes.py`. It checks the exact base commit, dirty targeted files, unique source anchors, Python AST, and JavaScript syntax (when Node is installed). It is intentionally **not** a generalized patch that guesses how to edit an evolved repository.

```powershell
# From the unpacked fixpack folder (Windows / PowerShell):
py tools/apply_fixes.py --repo C:\path\to\lagrange-main --check
py tools/apply_fixes.py --repo C:\path\to\lagrange-main
cd C:\path\to\lagrange-main
py -m pip install -e ".[ingest,sim,dev]"
py -m pytest --ignore=tests/sim -q
node --check web/app.js
py -m eval.engine_eval --out eval/engine_report.json
py scripts/verify_release.py --strict
```

If the team advanced past the base commit, stop and port the patch deliberately. **Do not reset or force-push** their work. To take a diff, run `git diff`; review and commit the changes on a feature branch, then push/merge yourself.

## Preparation and presentation

1. Create a synthetic, already-human-reviewed L0 graph: `python scripts/prepare_demo.py`. This script uses simulation-truth answers **only to prepare a synthetic demo fixture**; it is not a real AI/human approval process.
2. Set `LAGRANGE_BUILD_SHA` to `git rev-parse HEAD` before launch to enable evidence-freshness badges. Start the local server with `lagrange-rx serve` and `LAGRANGE_SAMPLES_DIR=sim/worlds` if you want the sample picker (PowerShell: `$env:LAGRANGE_SAMPLES_DIR="sim/worlds"`).
3. In Documents, choose the generated `demo...` graph from the confirmed graph list. Run `Twin`, then `Plan` (starts in expected-profit mode with an explicit 0.90 minimum expected fill). Change the risk mode to CVaR and demonstrate the tradeoff. A low profitability or infeasible output is a **real modeled result**, not a presentation failure to conceal.
4. Under Shock, change FX by 15%, drop the ceiling or exclude a supplier. Show recomputation, not precomputed claims.
5. Use the new `Presentation mode` control for larger displays.
6. Run `python scripts/browser_smoke.py` on the exact machine (requires `pip install playwright` and `python -m playwright install chromium`). Inspect desktop and mobile screenshots and keyboard interaction; this smoke script does not validate screen-reader experience or every data state.
7. Keep the live API on loopback. Do not upload patient data or real confidential client quotations to an unauthenticated demo.

## Known blockers: do not conceal these

- **Legal:** The interpretation of Rule 3735 / 15% markup and VAT application is UNVERIFIED. A native-reader/pharma accountant must review legal formulas. Neither this patch nor existing 10,000-case tests certify compliance with Uzbek law.
- **Commercial:** The committed pre-patch report showed `all_pass=false`, approximately -554.4M UZS/year CVaR uplift, VSS below zero, and a calibration miss of 0.1725pp vs a 0.1pp threshold. With a 0.9 service floor, all metrics must be recomputed and honestly reported. No guarantee they become favorable.
- **Corrupted data:** L1-L4 offline end-to-end simulation presently fails due to unreadable sales records (`no SalesRecord`). Installing Docker OCR languages alone does not fix corrupted export formats or irrecoverable omissions. The team must supply a real documented recovery path and benchmark it. It is not valid to label them solved based on the L0 fixture.
- **Live upload to optimizer:** The existing tiny HTTP upload test creates a graph that the engine cannot use; the full L0 test injects a separately prepared verified graph. `prepare_demo.py` makes this explicit. A truly general 72-file HTTP ingest->review->solve path and live LLM extraction are UNVERIFIED until implemented and run on the presentation server.
- **Model scope:** The MILP remains an annual first-stage procurement with scenario-dependent recourse, not a full monthly FEFO schedule and forward-looking dynamic reference pricing. Do not claim those extensions are implemented.
- **Security:** No authentication/multitenancy, encrypted data handling, queue/worker isolation, or deployment recovery. Local-only, non-sensitive demonstration.

## Acceptance and release gates

The new `scripts/verify_release.py --strict` refuses to call the submission ready unless the report describes the exact current code commit, has `all_pass=true`, a positive purchased quantity, a non-vacuous full legality check, calibration pass and uplift pass. This gate cannot prove real-world correctness; it prevents known false-positive presentation claims.

The GitHub Actions workflow adds two jobs: fast/unit/integration and real browser smoke. Evidence regeneration is deliberate, not an automatic step that rewrites tracked results after a merge.

## Status at packaging

- Files implemented in patch bundle; Python syntax check on new files and scripts: yes.
- Patch anchors checked against connected GitHub source: yes, source SHA identified above.
- Full private repository `pytest` / Docker build / L1-L4 OCR test: **not executable** in this isolated workspace because the private repo cannot be cloned here.
- GitHub merged/pushed/deployed: **no**. Team retains ownership of merge and rollout.
