# LAGRANGE Rx — Agent Instructions

1. Keep data classification explicit: 150,000 packs and 150,000 UZS retail ceiling originate in challenge #16; everything else in the sample case is synthetic. Do not present financial gains as real.
2. Read `docs/MATH.md` and inspect `src/lagrange_rx/models.py`, `economics.py`, `solver.py` before changing the mathematical formulation.
3. Preserve hard constraints (demand, eligibility, integer shipment, maximum capacity, scenario cash, assumed wholesale cap and markup). Fail closed when the model is infeasible. Never substitute LLM-generated allocations for solver results.
4. Tests must include positive and negative examples. Compare changed small models to the independent brute-force oracle. Run `python -m pytest -q` and `python scripts/smoke_browser.py` where supported; report what actually ran.
5. The cap is **assumed**, not fetched from an official medicine registry. Regulation No. 3735 replaced No. 3242 on 28 March 2026; do not cite No. 3242 as operative. Legal pricing treatment, registered SKU and VAT need expert review.
6. Single-stage means decisions are made before scenarios and fixed across them. Do not claim CVaR, recourse optimization, calibrated demand forecasting, marketing causality, real supplier authorization or genuine AI document extraction unless implemented and measured.
7. Do not add real patient information, credentials or sensitive business data to the demo. The stateless API has no authentication or rate limiting and must not be exposed to the public without appropriate controls.
8. Use typed API models, bounded inputs, and reproducible output. Treat inbound names/descriptions as untrusted text in the browser.
9. Commit only verified, intentional changes. Distinguish local implementation, tests, commit, push and deployment.
