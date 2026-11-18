# Work log

- Read the ingest specification and inspected the workspace and neighboring simulation: the workspace has no application, ontology models or usable Git repository.
- Applied ub-mode and delegated parsing, normalization and AI extraction to separate agents; separated their files to avoid concurrent edits.
- Wrote behavioral tests before implementation for intake, parsing, normalization, evidence checking and the HTTP review flow; observed the missing modules fail.
- Implemented content-addressed intake, parsers, normalization, name similarity, an OpenRouter cache/replay client and citation validation.
- Parsed the 128 available L0–L4 documents without parser exceptions. Missing Tesseract language data yields warnings; this does not establish OCR or extraction accuracy.
- Added a local API and persistence for a limited table-field and manual-review prototype. Tested that unconfirmed proposals are omitted from the temporary snapshot.
- Identified and fixed a hang in the HTTP test flow by using asynchronous route handlers in this environment.
- Ran the full test suite: 34 passed. The commands `crapper`, `dryer`, and `mutator` were not available in PATH; their analyses were not run.
- No commit or push was possible: this workspace is not a Git repository and its `.git` directory is read-only. No worktrees were created or removed.

## Ontology follow-up

- Read ONTOLOGY.md and compared the domain and trust constraints with the prototype. Delegated ontology models, table mapping, and resolution/quality checks to separate agents.
- Added typed ontology models, reference and provenance validation, Mermaid export, conservative decision contracts, replay/heuristic scorers, a structured OpenRouter chat fallback, resolution and standalone quality checks.
- Added tests before implementing the decision and ZIP/API behavior. Connected sales table mapping and ZIP upload to the API; only individual reviewed fields enter the temporary snapshot.
- Examined OpenRouter documentation: Gemini Decision 1 uses its separate Decisions API, so it must not be sent to the chat-completions fallback. Live model access was not exercised.
- The twin, full ClientGraph commit integration, OCR language data, end-to-end evaluation and recorded replay remain outstanding.

## Review fixes

- Reproduced the date annotation name collision through `typing.get_type_hints` on Python 3.14, added a regression test and aliased datetime; Python 3.13 was not installed here.
- Added and validated the 17-rule catalog based on the available ontology specification; the referenced twin branch was not available locally, and legal dates were not invented.
- Split ontology models into their domain modules while preserving the prior import interface.
- Added keyword-based heuristic decisions, configurable OpenRouter extraction and replay, evidence revalidation, broader table mappings, links and selected quality checks in the API. Confirmed groups are validated and committed to ClientGraph; incomplete groups remain outside it.
- Evaluated classification and operational counts on the simulation with explicit document-class mapping; unimplemented metrics are recorded as unavailable.
- Executed one synthetic OpenRouter call ($0.0004256 reported) and verified that replay of its saved response reproduces the extraction; this is not a complete simulation replay.
- Final baseline classification counts with the explicit class map: L0 20/24, L1 20/24, L2 21/25, L3 22/26, L4 22/29; these are classification counts, not extraction accuracy or proof of the twin's margin. The reviewer must account for small sample sizes.
- Full offline replay, complete corruption/link/field evaluation, and the twin integration remain open. The referenced source branch was unavailable, so no claim of a direct rule transfer is made.

## Review: workload, corruption audit and L0 margin

- Added a legal-rule comment clarifying that order 3735 was checked against a machine translation and requires native-speaker review.
- Audited the previous L0 issue count: 12 unmapped columns, 4 classification requests, 3 OCR warnings; none were corruption findings. Changed evaluation to upload each world as one batch, separate technical warnings and conservatively match documented findings to corruption records.
- Added grouped, manually reviewable table-field questions, individual-cell corrections and honest unknown monetary impact; did not auto-confirm uncalibrated or monetary values.
- Added document-backed checks for arithmetic, VAT, units, contract versions, possible duplicate receipts and internal period gaps. No simulation truth or manifest is read in ingest.
- Checked L3 regional spreadsheets against L0: the alleged missing 2026-05 is outside the visible 2025-only ranges in both worlds, so it cannot be inferred from those tables alone.
- Added a narrow P&L over a confirmed ClientGraph and an API endpoint. It returns missing data instead of a guessed margin; 2.5% was tested on a constructed graph, not reproduced end-to-end from L0.
- Final full-world evaluation: L0 has 27 grouped questions, 19 technical warnings and 0 quality findings; L3 has 27 grouped questions, 19 technical warnings, 6 quality findings and 5/28 conservatively matched corruptions. X2, D1x, V1, U1 and A1 each matched one case. X1 and all three M1 cases remain unmatched. Full test suite: 151 passed.

## L0, monetary checks, X1 and simulation follow-up

- Observed concurrent uncommitted changes in the neighboring simulation checkout; did not modify it. Copied a fixed snapshot to `/tmp/lagrange-sim-ingest-snapshot` for reproducible analysis and wrote `docs/SIMULATION_HANDOFF.md` for the simulation owner. The newer generator guards M1 logging on the presence of rows to remove; no simulation-owner message was sent because no matching chat was identifiable.
- Added evidence-backed sales lot/product fields and expense proposals with their source-product context. Expense allocation is not inferred; the ontology's CostItem does not itself preserve SKU attribution.
- Added optional currency, dated exchange-rate and staleness checks, including a CBU fetch option, explicit request coverage and missing-rate results. A live official CBU request returned USD 2026-01-05 rate 12004.41 UZS per unit. No automatic legal conclusion is made from an absent rate.
- Fixed MOQ comparison for a uniquely referenced quote and contract, independently of currency/VAT. On the inspected L3 documents, common supplier alone is insufficient to identify the right quote row and contract version.
- L0 end-to-end margin remains unresolved: inspected multi-product invoices contain aggregate totals without per-product prices, and confirmed cost allocation and complete graph references are missing. No synthetic figure is claimed as an L0 result.
- On the fixed newer uncommitted simulation snapshot, full L3 evaluation with model extraction disabled loaded 78 documents and reported 43 grouped questions, 83 technical warnings, 8 quality findings and 8/253 conservatively matched corruption entries (A1 2/10, X2 3/10, D1x 3/6). The snapshot is not comparable to the former 26-document world. Supporting files and the source manifest hash are under `eval/snapshot_2026-11-17/`.
- Updated checks for localized credit table headers and the newer contract and warehouse receipt formats. Isolated source-document comparisons yielded zero candidates in L0 and 2 arithmetic, 3 contract price and 3 possible duplicate findings in L3; matching remains a review proposal.

## Decision model and embeddings (2026-11-18)

- Verified on OpenRouter: `google/gemini-decision-1` is rejected by chat completions and served by `/api/alpha/decisions`; probed the request schema (question keys `[A-Za-z0-9_-]`, `criteria` is a record for choice and an array for score, `instructions` required). A live 14-document L1 batch made 13 Decisions calls for about $0.002.
- Wired `GoogleDecisionScorer` into the batch path (Q0 per document, Q2 link choice, Q2.column suggestions) behind `LAGRANGE_AI_MODE`; with the mode off nothing changes. Added `ai/factory.py`, an `embed` method with a per-text cache, and the question bank in `ingest/questions/*.yaml`.
- Evaluated five embedding models; selected `google/gemini-embedding-2` and raised the cosine threshold from the first-guess 0.80 to 0.85 after a pair-level sweep showed 0.45 precision on regions at 0.80.
- A first live run exposed that dotted question ids are invalid API keys, and the chat fallback had masked it by silently switching models; the fallback now triggers only when the provider rejects the model.
