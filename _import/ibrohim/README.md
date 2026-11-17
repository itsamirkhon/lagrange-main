# LAGRANGE Rx

**A runnable, evidence-first pharmaceutical profitability decision lab for National AI Hackathon 2026, Challenge #16.**

The core is a mixed-integer linear optimization model (MILP), not a prompt or a mock dashboard. Users can change FX/freight shocks, hypothetical wholesale price-cap revisions, working-capital budgets and route availability, then observe a mathematically re-optimized allocation.

> **Data disclaimer:** All supplier, cost, contract, capacity, scenario probability and wholesale price figures in this demo are **invented**. Only the challenge's 150,000 packs/year and 150,000 UZS retail ceiling are from the supplied problem statement. Regulatory constraints are simplified *modeling assumptions*, **not a legal-compliance certification**. Never present modeled profit uplift as a real-world result.

## Quick start

Python 3.11 or newer is required.

```bash
python -m venv .venv
# Linux / macOS: source .venv/bin/activate
# Windows: .venv\Scripts\activate
pip install -e ".[dev]"
python -m uvicorn lagrange_rx.api:app --host 127.0.0.1 --port 8000
```

Open **http://127.0.0.1:8000/** for the interactive decision lab or **http://127.0.0.1:8000/docs** for Swagger/OpenAPI.

The frontend is plain HTML/CSS/JavaScript, served by FastAPI. **No Node build step, cloud account, secrets, or API key required.** On Windows MSYS2, use the Python interpreter where `scipy` and `fastapi` install correctly; native MSYS2 Python and Windows Python may have separate environments.

For a solver-only run:

```bash
python -m lagrange_rx.cli --risk 0.35 --output evidence/local_run.json
python -m pytest -q
```

Optional Chromium interaction checks (requires `pip install playwright` and installed Chromium):

```bash
python scripts/smoke_browser.py
```

In the provided container, Chrome administrator policy blocks normal browser navigation to localhost, so this script runs the real frontend in Chromium and bridges API requests to a real local FastAPI server. It verifies the UI-to-backend logic but **does not** prove production browser networking/deployment.

## What's implemented

| Capability | Status |
|---|---|
| Deterministic synthetic 150,000-pack case | Built |
| MILP with routes, integer shipments and activation choices | Built |
| Common wholesale price with assumed cap and markup constraints | Built, simplified regulation |
| Profit objective: expected versus worst-case scenario | Built |
| Feasibility checks for demand, capacity, shipment size and worst-case cash | Built |
| Independently recomputed post-solve allocation audit | Built |
| Baseline, scenario profit, optimality gap, diagnostics | Built |
| Cost-plus markup paradox thought experiment | Built, illustrative |
| Interactive desktop/mobile decision lab | Built |
| Export run inputs and results as JSON | Built |
| Predictive demand model / causal marketing ML | Not built |
| Verified regulatory registry integration or invoice ingestion | Not built |
| Two-stage recourse, CVaR, localization real options, expiry planning | Not built |
| LLM copilot, multilingual extraction or Telegram bot | Not built |
| Authentication, multi-tenant storage and public deployment | Not built |

Optimization/operations research is not a trained machine-learning model. To claim a separately learned AI component, build and test one; do not pass off deterministic explanations as an LLM feature.

## Run a judge challenge

1. Start with the default synthetic case: five eligible source/route configurations and three scenarios.
2. Click **Run optimization**. Compare the optimized route mix against the feasible status-quo route.
3. Increase UZS depreciation to +18% and re-run; the profit and risk outputs change because the solver recalculates costs.
4. Disable Regional pooling; the solver replaces the route with other feasible suppliers.
5. Click **Test infeasibility**. The reduced working-capital constraint makes every allocation infeasible; the app reports this rather than fabricating a strategy.
6. Click **Reset case**, demonstrate the 5% synthetic supplier discount experiment, and **Export JSON**.

For a prepared three-minute demonstration and Q&A, read [docs/DEMO.md](docs/DEMO.md).

## Mathematical transparency

See [docs/MATH.md](docs/MATH.md) for variables, constraints, scenario economics, tax-basis assumptions and non-goals. The core implementation is in `src/lagrange_rx/solver.py`; `economics.py` independently audits every result.

The model is **single-stage robust optimization**, not a multi-period supply chain scheduler. The objective is

`maximize (1 - lambda) * E[profit] + lambda * min_s profit_s`.

This is not CVaR, not two-stage stochastic programming, and does not imply a particular risk distribution is calibrated.

## API

| Endpoint | Meaning |
|---|---|
| `GET /api/v1/health` | Readiness / version |
| `GET /api/v1/demo-case` | Synthetic structured input |
| `GET /api/v1/assumptions` | Data and modeling limitations |
| `GET /api/v1/experiments/cost-plus?discount_pct=5` | Deterministic markup-bound thought experiment |
| `POST /api/v1/optimize` | Solve requested case; empty JSON uses demo case |

Example:

```bash
curl -X POST http://127.0.0.1:8000/api/v1/optimize \
  -H 'Content-Type: application/json' \
  -d '{"risk_aversion":0.35}'
```

Invalid input returns HTTP 422; a proven infeasible model returns HTTP 200 with `status=infeasible`. Solver failures return HTTP 503, not a fabricated optimum.

## Team workflow

- `src/lagrange_rx/`: backend and solver
- `web/`: dependency-free frontend
- `tests/`: pricing, invariants, API, independent brute-force benchmark
- `evidence/`: reproducible machine output, optional local screenshots
- `docs/`: math, demo, regulatory caveats, roadmap, handoff
- `AGENTS.md`: instructions for coding agents

**Before publishing:** Agree on an open-source license, remove temporary/real client data, configure CI and rate limiting if exposing the API, and have a pharma domain expert review legal and commercial assumptions. The current app is a **local hackathon demonstration**, not a production financial or healthcare product.
