# LAGRANGE Rx — Simulated Pharma Importer Environment (`sim/`)

Independent synthetic world and multi-level messy-document benchmark for **LAGRANGE Rx**, modeling an Uzbekistan prescription-drug importer and wholesale distributor (**ООО «CardioImport Distribution»**, Ташкент, ИНН `000000000 (SYNTHETIC)`).

> **Independence rule (Section 1 of `docs/SIMULATION.md`):**
> `sim/` never imports `src/` or `lagrange_rx`. Product code only consumes `sim/worlds/L{0..4}/documents/`. Ground truth (`sim/truth/`) and corruption manifests (`sim/worlds/L{0..4}/manifest.json`) are hidden from the product and read only by the evaluation layer.

---

## 1. Quickstart

Run all commands from the repository root (`/home/sukhrob/Desktop/lagrange-rx`):

```bash
# Generate ground truth (sim/truth/) and all five worlds (sim/worlds/L0..L4/) with seed=42
python3 -m sim.generator --seed 42

# Optional: generate into a custom directory
python3 -m sim.generator --seed 42 --sim-dir /tmp/sim_copy

# Run the full test suite (foundation, truth/eval, corruptions, documents, DoD)
python3 -m pytest -v

# Run the /ub-mode quality gate (Crapper < 10, Dryer < 0.8, Mutator)
python3 -m sim.tools.ub_quality_gate

# Launch the interactive Web UI (http://127.0.0.1:8080)
python3 -m sim.ui --host 127.0.0.1 --port 8080
```

### Interactive Web UI (`sim/ui/`)

Start the local dashboard server (`python3 -m sim.ui --host 127.0.0.1 --port 8080`, optional `--sim-dir sim`) to explore calibration KPIs, inspect `L0`–`L4` documents and `manifest.json` corruptions, browse `truth/` tables, and run live `evaluate_plan_on_truth` scenarios:

- `GET /` and `GET /static/{filename}` — Web UI frontend (`index.html`, `styles.css`, `app.js`)
- `GET /api/overview` — World config, `calibration_report.json` KPIs, and `L0`–`L4` summary counts
- `GET /api/worlds/{level}` — `manifest.json` enriched with file sizes, `preview_kind`, and `download_url`
- `GET /api/worlds/{level}/document/{filename}` — Raw document stream (PDF, PNG, JPG, XLSX, CSV, EML)
- `GET /api/worlds/{level}/preview/{filename}` — Structured preview payload (multi-sheet XLSX, decoded CSV, EML, PDF/image metadata)
- `GET /api/truth` and `GET /api/truth/{table}?limit=200&sku_id=...` — Ground-truth table list and filtered rows
- `GET /api/evaluate/baseline` and `POST /api/evaluate` — Baseline and custom scenario profit/regret evaluation
- `POST /api/regenerate` — Re-run deterministic world generation for `{"seed": <int>}`

### Evaluation Hook (`evaluate_plan_on_truth`)

```python
from sim import evaluate_plan_on_truth, get_baseline_plan

baseline = get_baseline_plan()
result = evaluate_plan_on_truth(baseline)
print(float(result), result.net_margin_ex_vat, result.regret_vs_baseline_uzs)
```

`evaluate_plan_on_truth(plan)` returns a `PlanProfit` (`float` subclass equal to `net_profit_uzs`) exposing `.to_dict()` and attributes:
`net_profit_uzs`, `revenue_ex_vat_uzs`, `cogs_ex_vat_uzs`, `operating_costs_uzs`, `net_margin_ex_vat`, `annual_sales_packs`, `stockout_packs`, `writeoff_packs`, `baseline_profit_uzs`, and `regret_vs_baseline_uzs`.

---

## 2. Directory Structure

```text
sim/
  config/
    world.yaml              # Company, period (22 months), focus & background SKUs, calibration targets, 14 UZ regions, channels
    levels.yaml             # Chaos levels L0..L4, intensity ranges, allow_unrecoverable, active operators
  inputs/
    fx_cbu.csv              # CBU Uzbekistan FX rates (USD, EUR, RUB -> UZS) for 2025-01-01..2026-10-31
    registry_sku.json       # Educational Order #3735 reference price registry record for SKU_CARDIO_01
  truth/                    # HIDDEN from product: *.parquet + *.json truth tables + calibration_report.json
  worlds/
    L0/..L4/
      documents/            # Product-facing messy/clean documents (D1..D13, plus J1 junk on L4)
      manifest.json         # HIDDEN from product: truth_refs, omitted_documents, and every applied corruption
  generator/                # Pure-Python/NumPy/Pandas/ReportLab/Pillow generator package
    cli.py / __main__.py    # Idempotent CLI entry point (`python -m sim.generator --seed 42`)
    config_loader.py        # YAML/CSV/JSON input loaders & schema validators
    domain.py               # Domain constants, dataclasses, deterministic synthetic entity generators
    xlsx_io.py              # Deterministic pure-Python OpenXML XLSX reader/writer (merged cells, fixed ZIP timestamps)
    truth_gen.py            # Causally consistent ground-truth simulator (suppliers..costs)
    calibration.py          # Section 3.3 calibration checker (`CalibrationError` on breach)
    evaluate.py             # Independent `evaluate_plan_on_truth` & `get_baseline_plan`
    corruptions.py          # All 19 corruption operators (N1..J1) & level manifest planner
    image_corruptions.py    # Pillow+NumPy visual degradations (R1..R4) & watermark enforcement
    doc_renderers.py        # Deterministic PDF (invariant=1), PNG/JPG, neutral chat UI, cp1251 CSV, EML renderers
    doc_builders.py         # D1..D13 (+J1) multi-level document builder & manifest writer
  tools/
    ub_quality_gate.py      # /ub-mode Crapper, Dryer, and AST Mutator runner
  ui/
    __main__.py             # Web UI CLI (`python3 -m sim.ui --host 127.0.0.1 --port 8080`)
    api.py                  # Pure inspection, preview, scenario evaluation, and regeneration APIs
    server.py               # Stdlib ThreadingHTTPServer and route dispatcher
    static/                 # Accessible HTML/CSS/JS frontend (tabs, native modal dialog, SVG chart)
  README.md                 # This documentation
```

---

## 3. Data Sources: Real vs. Synthetic

| Component | Real vs. Synthetic | Source / Basis |
|---|---|---|
| **Reference Pricing Rules (Order #3735)** | **Real rules** | Uzbekistan Ministry of Health Order #3735 (para. 19–20): calculation base (`Rn + B + Bx`), wholesale markup cap (`+15%`), retail markup cap (`+20%`), VAT (`12%`). |
| **Calibration Anchor Numbers** | **Real case targets** | `docs/SIMULATION.md` Section 3.3: `annual_sales ≈ 150,000 ±3%`, `ChChN = 150,000 UZS`, `ChUN = 125,000 UZS`, `wholesale_ex_vat = 111,607.14 UZS`, `calc_base = 97,049.69 UZS`, `net_margin ∈ [2%, 3%]`, `pre_reform_margin ∈ [25%, 30%]`. |
| **Uzbekistan Geography** | **Real** | All 14 administrative regions of Uzbekistan (13 regions + Tashkent city) in Russian, Uzbek Latin, and Uzbek Cyrillic (`sim/config/world.yaml`). |
| **FX Rates (`sim/inputs/fx_cbu.csv`)** | **Real-sourced schedule** | Central Bank of Uzbekistan (`https://cbu.uz/ru/arkhiv-kursov-valyut/`) series for `USD`, `EUR`, `RUB` → `UZS` across `2025-01-01`..`2026-10-31`. |
| **Company, SKUs, Counterparties, Documents** | **Synthetic** | Fictional importer (`ООО «CardioImport Distribution»`), fictional INN (`000000000 (SYNTHETIC)`), fictional focus SKU (`Kardiozan 10 мг №30`, `SKU_CARDIO_01`), 6 background SKUs (`SKU_BG_01..06`), fictional suppliers/carriers/people/addresses. Every PDF and image carries `"УЧЕБНЫЙ ОБРАЗЕЦ / SYNTHETIC"`. |

---

## 4. Document Types (`D1`–`D13`) & Chaos Levels (`L0`–`L4`)

### Document Types
- **D1**: 1C sales export CSV (`cp1251`, `;` delimiter, decimal comma)
- **D2**: Regional manager XLSX reports (3 distinct templates with merged cells)
- **D3**: Supplier commercial quotes (PDF, EN/RU)
- **D4**: Carrier quotes (PDF, XLSX, and neutral chat screenshot PNG)
- **D5**: Multi-page supplier contracts & amendments (PDF)
- **D6**: Commercial invoices & packing lists (PDF)
- **D7**: Educational customs clearance summaries (XLSX & PDF)
- **D8**: Warehouse dispatch waybills (PDF & photo JPG)
- **D9**: Warehouse stock & batch/lot balances (XLSX)
- **D10**: Payments registry & credit schedule (XLSX)
- **D11**: Internal memos / handwritten notes (photo JPG)
- **D12**: Email correspondence with attachments (`.eml` + PDF printout)
- **D13**: Price registry extracts (CSV & PDF)
- **J1** *(L4 only)*: Irrelevant junk files (promo flyer PDF, unrelated OTC catalog CSV, blank scan JPG)

### Chaos Levels (`sim/config/levels.yaml`)
- **L0 (Эталон, 0%)**: Clean, consistent `D1`–`D13` with zero corruptions.
- **L1 (Нормальный бизнес, 5–10%)**: Distinct regional XLSX templates; localized `N1`, `N2`, `M1` (`recoverable: true`).
- **L2 (Типичная ЦА, 15–25%)**: Adds scans/photos (`R1`), messenger crops (`R4`), language/script mixing (`L1`), omitted currency (`C1`), stale data (`S1`), duplicate waybills (`D1x`) (`recoverable: true`).
- **L3 (Беспорядок, 30–45%)**: Adds synthetic stamp overlays (`R2`), handwritten edits (`R3`), cross-document contradictions (`X1`), conflicting contract versions (`X2`), wrong currency (`C2`), wrong FX date (`C3`), VAT ambiguity (`V1`), unit mismatches (`U1`), arithmetic errors (`A1`) (`recoverable: true`).
- **L4 (Хаос, 50%+)**: Adds severe `M1` omissions, severely degraded `R1` scans (`recoverable: false`), and `J1` junk files.

---

## 5. Domain Glossary

- **Focus SKU (`SKU_CARDIO_01`)**: Primary prescription cardiology product (*Kardiozan 10 мг №30*, INN *Kardiozartan*) subject to Order #3735 price caps and margin calibration.
- **Rn (Референтная цена)**: Registered reference CIP price in UZS (`93,500.00 UZS`).
- **B (Таможенная пошлина и сборы)**: Customs duty and clearance charges per pack (`2,349.69 UZS`).
- **Bx (Прочие расходы до склада)**: Inbound logistics/other allowable expenses (`1,200.00 UZS`).
- **Расчётная база (`calc_base_ex_vat_uzs`)**: `Rn + B + Bx` per Order #3735 para. 19 (`97,049.69 UZS`).
- **ChUN (Чекланган улгуржи нарх)**: Maximum wholesale price **including 12% VAT** (`125,000.00 UZS`; ex-VAT ceiling `111,607.14 UZS` at `+15%` wholesale markup cap).
- **ChChN (Чекланган чакана нарх)**: Maximum retail price **including 12% VAT** (`150,000.00 UZS` at `+20%` retail markup cap).
- **Pre-reform period (`2025-01`..`2025-10`)**: Historical 10-month window prior to the price cap revision (`25–30%` net margin).
- **Calibration window (`2025-11`..`2026-10`)**: Post-reform 12-month window (`150,000 ±3%` packs, `2–3%` net margin ex-VAT).
- **Decision Regret (`regret_vs_baseline_uzs`)**: `baseline_profit_uzs - evaluated_profit_uzs` measuring the economic cost of poor data or suboptimal procurement/pricing decisions.
- **Recoverable corruption (`recoverable`)**: Manifest flag indicating whether ground truth can be deduced by cross-referencing other documents (`true` on L1–L3; `false` on L4 unrecoverable losses).
