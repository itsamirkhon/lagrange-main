# LAGRANGE Rx — decision engine (stage 1)

Implementation of `docs/DECISION_ENGINE.md`: from a **confirmed** ClientGraph to a **legal, provably best** procurement
and supply plan for one Rx drug under price ceilings — explained, reproducible and honestly measured.
All numbers are computed by code. There is no LLM anywhere in the core.

```
ClientGraph.for_calculation() → twin → scenarios → MILP (HiGHS) → independent legality check → explanation → evaluation
```

## Quick start

```bash
pip install -e ".[dev]"
python -m pytest                      # ~2 min; add  -m slow  for the 10 000-case legality run
python -m lagrange_rx.eval.engine_eval   # writes eval/engine_report.json (≈1 min)
uvicorn lagrange_rx.api.app:app       # API of section 9
```

```python
from lagrange_rx.fixtures.l0 import build_l0_graph
from lagrange_rx.engine.service import run_optimize

res = run_optimize(build_l0_graph())          # calibrate → scenarios → optimise → check → explain
res.plan, res.solve, res.metrics, res.waterfall, res.constraints, res.snapshot
```

## Layout

| Path | What |
|---|---|
| `rules/uz_pharma_rules.yaml` | the only source of legal numbers (15%, VAT 12%, ×1.20, P20 thresholds 3%/5%, P22 cut) |
| `src/lagrange_rx/ontology.py` | minimal ClientGraph; engine accepts only `for_calculation()` output |
| `engine/twin.py` | A. unit economics, ceilings, plan evaluation with recourse, calibration (`twin_baseline`) |
| `engine/scenarios.py`, `fx_fetch.py` | B. block-bootstrap FX, P20/P22, freight, supplier failure, demand quantiles, k-medoids reduction, 4 stress scenarios |
| `engine/optimize.py` | C. two-stage stochastic MILP; modes `expected` / `cvar` / `worst_case`; status, bound, gap |
| `engine/compliance.py` | D. independent legality check (imports no twin/optimizer; AST test; 33 mutants all killed) |
| `engine/explain.py` | E. binding constraints with `rule_id`, waterfall vs baseline, what-if by re-solving, "impossible and why" via elastic MILP |
| `eval/oracle.py`, `eval/engine_eval.py` | F. brute-force oracle; uplift, VSS, EVPI, legality, optimality, speed, regret by level |
| `engine/service.py`, `api/app.py` | orchestration and HTTP API (`/engine/twin`, `/scenarios`, `/optimize`, `/what-if`, `/plans/{id}`) |
| `eval/thresholds.json` | thresholds pre-registered before the first run (hash and commit recorded in the report) |

## Modelling decisions worth knowing

* **15% rule on packs actually sold.** `R_s ≤ 1.15 · Σ base_i · q_is`. The doc's literal form (base of *ordered* lots) lets an
  optimizer pad purchases to loosen the cap, so the engine uses sold packs by route; `ScenarioOutcome.sold_by_route` carries it
  and `compliance.py` verifies it. Assumption to confirm with a lawyer: the legal base is that of the packs sold (specific identification).
* **Hedge** is a discrete share (0/25/50/75/100%), exactly linearised; it changes economic cost, never the legal base.
* **Annual ceiling** = mean of 12 monthly factors (P20 indexation with 3%/5% thresholds and 1 Jan/1 Jul dates, P22 cut), i.e. uniform sales over months.
* **Service level** (optional) = expected fill rate over scenarios; **profit target** (optional) = expected profit. Both can be reported "impossible".
* The twin refuses to run on `unallocated` below-line costs, and the solver refuses an uncalibrated twin unless `demo_mode=True`.

## What is NOT real yet (read before showing numbers)

1. **FX data is synthetic.** The sandbox could not reach cbu.uz. `data/fx_sample_SYNTHETIC.csv` is generated; reports say
   `fx_source: synthetic-sample`. Run `python scripts/fetch_cbu_fx.py` to write `data/cbu_fx_monthly.csv`; it is then picked up automatically.
2. **The L0 graph is a hand-built fixture** (`fixtures/l0.py`); its "actual" result is constructed to give 2.5% margin, so calibration there tests plumbing.
3. **The simulation is not connected**: `eval/engine_eval.py` uses `LocalTruth` (twin on a truth graph) behind the `TruthEvaluator` protocol; plug in the real `evaluate_plan_on_truth`. Regret L0–L4 needs the Ingest graphs and reports "not available" until given.
4. **Assumptions in the YAML** (`assumption: true`): P19 formula markup used only for a warning, P22 cut parameters (p=0.3, 10–37%), hedge forward premium 2%.
5. Stage 2 (monthly model, FEFO, CIP ratchet) and the `lagrange-rx` merge (section 10) are not done.
