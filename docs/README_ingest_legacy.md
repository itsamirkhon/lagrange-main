# LAGRANGE Rx ingest

Local document-ingest implementation and ontology models. The implementation remains incomplete relative to [docs/INGEST.md](docs/INGEST.md); the current limits below are material.

## Run locally

Python 3.11+, Tesseract with `rus`, `uzb`, `uzb_cyrl`, `eng` language data. Missing OCR languages produce warnings and no invented text.

```bash
python -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
uvicorn lagrange_rx.api:app --host 127.0.0.1 --port 8000
```

Open http://127.0.0.1:8000/docs. Upload files or ZIP via `POST /ingest/batches`, inspect results and review queue, answer questions and commit. The API has no authentication: use it only locally. Files, batch state and graphs are stored in `./data` or `LAGRANGE_DATA_DIR`. `GET /ontology/rules` displays the versioned catalog. A commit produces a validated `ClientGraph`; incomplete groups are returned with reasons and remain outside it.

The default mode makes no model calls. For optional model extraction, put `OPENROUTER_API_KEY` in a private `.env`, then:

```bash
set -a; source .env; set +a
export LAGRANGE_AI_MODE=openrouter
export LAGRANGE_AI_BUDGET_USD=1
uvicorn lagrange_rx.api:app --host 127.0.0.1 --port 8000
```

Models are configured in `config/ai.yaml`. The in-process budget uses a configured request-cost estimate and reported usage; it is not a provider-enforced hard spending cap. Configure a separate limit with the provider. `LAGRANGE_AI_MODE=replay` reads matching cached responses only, and a cache miss becomes an issue. The cache contains document-derived material and should be treated as private.

## Money checks

`POST /ingest/batches/{batch_id}/money-checks` accepts `as_of` (ISO date), optional `registry_max_age_days` and `fetch_official_rates`. Currency and expiry findings are review suggestions based on evidence-backed proposals. Set `fetch_official_rates` to `true` to request official CBU rates for identified customs dates and currencies; the response records coverage and network errors. Missing inputs or failed requests do not establish that a rate is correct. Monetary checks use extracted values and require human review.

Expense rows and their product context are retained as unconfirmed proposals. They do not by themselves establish allocation of shipment or other costs to a SKU. `POST /ingest/batches/{batch_id}/pnl` requires a validated committed graph and explicit expense-allocation choices; it reports missing inputs rather than inferring them.

## Verify

```bash
pytest -q
PYTHONPATH=src python eval/ingest_eval.py /path/to/lagrange-sim/sim --output eval --class-map eval/class_map.json
```

Evaluation outputs include counts, and explicitly mark metrics not yet measured. `eval/replay` contains a single synthetic extraction recording, not a complete demo run.

## Current limits

The full twin and solver are absent; the narrow P&L endpoint reports missing inputs when a validated graph is insufficient. Classified text extraction is optional; the extraction schemas and mappings are incomplete, invoice line items are not implemented, scans require OCR, and there is no end-to-end confirmed L0 margin. The API integrates selected arithmetic and cross-document checks; other checks are not all wired into the pipeline. Table review groups related values, while keeping each cell available for individual review. No value is auto-confirmed, and monetary impact is marked unknown unless supplied rather than fabricated. Money impact prioritization, a complete review UI, human simulation, full truth-aligned evaluation, and a complete L0–L4 offline replay are still outstanding. Legal rules are transcribed from the ontology specification; effective dates and original legal texts require verification. Do not use this prototype for business decisions.
