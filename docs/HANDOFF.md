# Engineering handoff

**Date:** 2026-11-17 (Asia/Tashkent). **Project:** LAGRANGE Rx prototype for National AI Hackathon #16.

## Decision record

- Solved synthetic one-SKU pharmaceutical importer case; no real drug registered price or supplier data claimed.
- Scenarios represent FX/freight changes and explicitly hypothetical price-cap revisions.
- Core optimizer SciPy/HiGHS MILP; API FastAPI/Pydantic; browser frontend static HTML/CSS/JS.
- Objective: risk mixture of expected annual profit and minimum scenario profit; **not CVaR**.
- Decision: common price across all active routes and scenarios; simplifies legality but can be conservative and may not match a real wholesale contract.
- Current reference-price framework: No. 3735 replaces No. 3242, effective 28 March 2026. The model remains non-certified.

## Constraints and trust boundaries

- Input data is bounded via Pydantic strict validation and only synthetic in the shipped demo.
- Server is stateless; there are no secrets, accounts, database, live pharmacy systems or patient records.
- Avoid exposing this local API publicly without authentication/rate limits and CPU-concurrency controls.
- An infeasible solver status is not interpreted as an optimal answer. Solver failures are HTTP 503.

## Files and verification

- `src/lagrange_rx/{models,economics,solver,demo_data,api,cli}.py` implement the math/API.
- `web/{index.html,styles.css,app.js}` implement the interactive decision lab.
- `tests/` contains independent, negative path and API tests.
- `evidence/` contains computed outputs and screenshots from an isolated browser run.

Run `python -m pytest -q`, `python scripts/smoke_browser.py` (optional local Chrome), and `python scripts/generate_evidence.py`. Record their actual outputs in the final handoff; do not pretend the existing log proves a future build.

## Remaining blockers

- Public GitHub repository URL has not been supplied. **Nothing has been pushed or deployed.**
- Actual registration/pricing and 15% markup application to a real SKU are **UNVERIFIED**.
- True business uplift is **UNVERIFIED** because data are synthetic.
- Managed Chromium policy in this container blocks localhost navigation; browser test uses a local Python API bridge and cannot certify production navigation.
- Team has not chosen an open-source license.
