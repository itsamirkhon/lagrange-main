"""Stage 6d end-to-end chain on any world L0..L4:
documents -> Ingest (offline builders) -> simulated human -> ClientGraph -> twin calibration -> optimizer
-> legality -> truth evaluation (simulation truth through eval/truth_eval.SimTruth).

Usage: python -m eval.e2e [--worlds L0 L1 L2 L3 L4] [--n-truth 200] [--out-dir eval]

Product code sees only the documents. The simulated human, the extraction-vs-books comparison, the corruption
manifest and the truth evaluation read the simulation truth: this is eval code, which is allowed. One engine
configuration only: `lagrange_rx.engine.adapter.default_engine_config` (calibrated from the graph's own documents).

Failures are data. Every stage runs under its own guard: the report records the stage that stopped the chain, the
exact exception, and what Ingest lost (extraction vs the books, human questions, rejected groups, corruptions of the
manifest). Nothing is filled in for a stage that did not run.
"""
from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import traceback
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from eval.sim_human import SimHuman, TRUTH
from lagrange_rx.engine.adapter import default_engine_config, to_calculation_graph
from lagrange_rx.engine.baseline import baseline_plan
from lagrange_rx.engine.decision_policy import DEFAULT_SERVICE_LEVEL, has_supply
from lagrange_rx.engine.optimize import OptimizerConfig
from lagrange_rx.engine.rules import load_rules
from lagrange_rx.engine.scenarios import ScenarioConfig, load_fx_history, out_of_sample
from lagrange_rx.engine.service import run_optimize
from lagrange_rx.engine.twin import last_12_months, legal_base, twin_baseline
from lagrange_rx.engine.types import Plan, Scenario
from lagrange_rx.ingest.offline.build import build_draft
from lagrange_rx.ingest.offline.confirm import commit

ROOT = Path(__file__).resolve().parents[1]
ACCEPT_PP = 0.5  # task acceptance; eval/thresholds.json pre-registers a stricter 0.1 pp for the final evaluation
NOW = datetime(2026, 11, 18, tzinfo=timezone.utc)
LEVELS = ("L0", "L1", "L2", "L3", "L4")


def build_graph(documents: Path):
    draft = build_draft(documents, now=NOW)
    human = SimHuman()
    graph, report = commit(draft, human.answer_all(draft.questions), now=NOW)
    return draft, human, graph, report


def _failure(stage: str, exc: BaseException) -> dict:
    return {"stage": stage, "error_type": type(exc).__name__, "message": str(exc)[:600],
            "where": traceback.format_exception(exc)[-2].strip()[:300]}


def _graph_hash(graph) -> str:
    return hashlib.sha256(json.dumps(graph.model_dump(mode="json"), sort_keys=True, ensure_ascii=False, default=str)
                          .encode()).hexdigest()[:16]


# --------------------------------------------------------------------------- Ingest side

def _books_counts() -> dict:
    t = {p.stem: json.loads(p.read_text(encoding="utf-8")) for p in TRUTH.glob("*.json") if p.stem != "calibration_report"}
    return {"sales_records": len(t["sales"]), "shipments": len(t["shipments"]), "customs_entries": len(t["customs"]),
            "lots": len(t["lots"]), "quotes": len(t["quotes"]), "contracts": len(t["contracts"]),
            "registry_records": len(t["registry_events"])}


def _corruptions(level: str) -> dict:
    m = json.loads((ROOT / "sim" / "worlds" / level / "manifest.json").read_text(encoding="utf-8"))
    ops, rec = Counter(), Counter()
    by_doc_type: Counter = Counter()
    for d in m["documents"]:
        for c in d.get("corruptions", []):
            ops[c["operator"]] += 1
            rec["recoverable" if c.get("recoverable") else "not_recoverable"] += 1
            by_doc_type[d["doc_type"]] += 1
    return {"total": sum(ops.values()), "by_operator": dict(sorted(ops.items())), "recoverability": dict(rec),
            "by_document_type": dict(sorted(by_doc_type.items())), "omitted_documents": m.get("omitted_documents", [])}


def ingest_block(level: str, draft, human, graph, commit_report) -> dict:
    counts = {k: v for k, v in commit_report["counts"].items() if v}
    books = _books_counts()
    extracted = {k: counts.get(k, 0) for k in books}
    rejected = [{"question_id": e["question_id"], "why": e["basis"][:400]} for e in human.log
                if e["answer"].get("decision") == "reject"]
    return {
        "documents": len(draft.source_documents),
        "questions_to_human": {"total": len(draft.questions), "answered": human.load,
                               "by_kind": dict(Counter(e["kind"] for e in human.log)),
                               "rejected_groups": [r["question_id"] for r in rejected]},
        "rejected_group_reasons": rejected,
        "dropped_by_collection": dict(Counter(x["collection"] for x in commit_report["dropped"])),
        "issues": dict(Counter(f"{i['issue_type']}/{i['severity']}" for i in draft.issues)),
        "not_extracted": [x["what"] for x in draft.not_extracted],
        "graph_counts": counts,
        "extraction_vs_books": {k: {"extracted": extracted[k], "books": books[k],
                                    "share": None if not books[k] else round(extracted[k] / books[k], 4)} for k in books},
        "corruptions_in_manifest": _corruptions(level),
    }


# --------------------------------------------------------------------------- truth evaluation

def _stats(profits) -> dict:
    from eval.engine_eval import _stats as st
    return st(list(profits))


def nominal_scenario(cg) -> Scenario:
    return Scenario(id="nominal", name="nominal", fx=dict(cg.nominal_fx), ref_fx_start=1.0, ref_fx_path=(1.0,) * 12,
                    demand=cg.demand.p50)


def truth_evaluation(cg, onto_graph, plans: dict[str, Plan | None], n: int, rules, fx) -> dict:
    """Profit of each plan on the simulation truth: nominal world (exact truth call) and `n` out-of-sample scenarios
    (overlay on a copy of the truth tables, eval/truth_eval.py). Mapping engine -> truth is by features."""
    from eval.truth_eval import MappingError, SimTruth
    try:
        st = SimTruth(cg, onto_graph)
    except MappingError as exc:
        return {"status": "mapping failed: the engine's routes/suppliers/SKU cannot be tied to truth entities",
                "error": str(exc)[:500]}
    with st:
        oos = out_of_sample(cg, fx, rules, ScenarioConfig(n=n))
        nominal = nominal_scenario(cg)
        out = {"status": "evaluated", "oos_scenarios": n, "truth_calls_unit": "one simulation call per plan x scenario",
               "mapping": st.mapping_table(), "truth_native_baseline": st.baseline_on_truth(), "plans": {}}
        for name, plan in plans.items():
            if plan is None:
                out["plans"][name] = {"status": "no plan"}
                continue
            try:
                prof = st.evaluate_plan_on_truth(plan, oos)
                out["plans"][name] = {"packs_by_route": plan.packs_by_route(), "nominal_profit_uzs": st.profit(plan, nominal),
                                      "oos": _stats(prof), "oos_mean_profit_uzs": sum(prof) / len(prof)}
            except Exception as exc:  # noqa: BLE001 - recorded, never hidden
                out["plans"][name] = {"status": "truth evaluation failed", "error": f"{type(exc).__name__}: {str(exc)[:300]}"}
        out["truth_calls"] = st.calls
    return out


def historical_markup_check(cg_split, cg_unsplit, rules) -> dict:
    """Does the client's own history satisfy the 15% rule as the engine models it (price <= 1.15 x legal base of the
    packs sold)? Computed from documents only (sales prices, customs entries), no truth."""
    sales = last_12_months(cg_split, cg_split.sales)
    packs = sum(s.packs for s in sales)
    price = sum(s.packs * s.price_uzs for s in sales) / packs

    def avg_base(cg):
        ships = [s for s in last_12_months(cg, cg.shipments)]
        tot = sum(s.packs for s in ships)
        return sum(legal_base(cg.route(s.route_id).model_copy(update={"kc": s.kc}), s.fx_uzs) * s.packs for s in ships) / tot

    bs, bu = avg_base(cg_split), avg_base(cg_unsplit)
    cap = 1.0 + rules.max_markup
    return {"avg_sale_price_ex_vat_uzs": price, "avg_legal_base_freight_split_uzs": bs, "avg_legal_base_incl_freight_uzs": bu,
            "implied_markup_on_split_base": price / bs - 1.0, "implied_markup_on_base_incl_freight": price / bu - 1.0,
            "rule_max_markup": rules.max_markup, "history_within_rule_split_base": price <= cap * bs,
            "history_within_rule_base_incl_freight": price <= cap * bu,
            "note": "the client's recorded sales price vs 1.15 x the legal base of the purchases; if the history is above "
                    "the cap, the engine's model of UZ-PF13 (status needs_legal_review) contradicts the client's own books"}


# --------------------------------------------------------------------------- the chain

def run_world(level: str, output: Path | None = None, *, n_truth: int = 200, diagnostics: bool = True) -> dict:
    documents = ROOT / "sim" / "worlds" / level / "documents"
    rep: dict = {"world": level, "stage_reached": "start", "failure": None, "truth_evaluation": None}

    def fail(stage, exc):
        rep["failure"] = _failure(stage, exc)

    try:
        draft, human, graph, commit_report = build_graph(documents)
    except Exception as exc:  # noqa: BLE001
        fail("ingest", exc)
        return _finish(rep, output, f"ingest-{level}-failed")
    rep["stage_reached"] = "ingest"
    rep["ingest"] = ingest_block(level, draft, human, graph, commit_report)
    ingest_snapshot = f"ingest-{level}-{_graph_hash(graph)}"
    rep["ingest_snapshot_id"] = ingest_snapshot
    try:
        graph.for_calculation()
    except Exception as exc:  # noqa: BLE001
        fail("graph_validation", exc)
        return _finish(rep, output, ingest_snapshot)

    try:
        cfg = default_engine_config(graph)
        cg = to_calculation_graph(graph, cfg)
    except Exception as exc:  # noqa: BLE001
        fail("engine_config", exc)
        return _finish(rep, output, ingest_snapshot)
    rep["stage_reached"] = "engine_config"
    rep["graph"] = {"focus_sku": cfg.sku_id, "horizon_start": cfg.horizon_start, "routes_in_engine": [r.id for r in cg.routes]}
    rep["engine_config"] = cfg.model_dump(mode="json")
    rep["engine_notes"] = list(cg.notes)

    truth = json.loads((TRUTH / "calibration_report.json").read_text(encoding="utf-8"))
    cal = twin_baseline(cg)
    gap = None if cal.twin_margin is None else (cal.twin_margin - truth["net_margin_ex_vat"]) * 100
    volume = sum(s.packs for s in last_12_months(cg, cg.sales))
    missing = list(cal.missing_data)
    if gap is not None and abs(gap) > ACCEPT_PP:
        missing.append("see calibration_gap: data not captured by the twin")
    rep["twin_margin"] = cal.twin_margin
    rep["calibration_gap"] = {
        "twin_margin": cal.twin_margin, "document_pnl_margin": cal.actual_margin, "calibrated_vs_documents": cal.calibrated,
        "truth_margin": truth["net_margin_ex_vat"], "gap_vs_truth_pp": gap, "twin_volume_packs": volume,
        "truth_volume_packs": truth["annual_sales_packs"], "accepted_within_0.5pp": gap is not None and abs(gap) <= ACCEPT_PP,
        "preregistered_threshold_pp": json.loads((ROOT / "eval" / "thresholds.json").read_text())["calibration_max_gap_pp"],
        "missing_data": missing}
    rep["stage_reached"] = "calibration"

    rules = load_rules()
    fx = load_fx_history()
    scen = ScenarioConfig()
    plans: dict[str, Plan | None] = {}
    try:
        res = run_optimize(cg, scen_cfg=scen, opt_cfg=OptimizerConfig(risk_mode="cvar", service_level=DEFAULT_SERVICE_LEVEL), rules=rules, fx=fx)
    except Exception as exc:  # noqa: BLE001 - e.g. CalibrationRequired: the product refuses to optimise
        fail("optimization", exc)
        return _finish(rep, output, ingest_snapshot)
    rep["stage_reached"] = "optimization"
    plan = None
    if res.plan is not None:
        plan = {"plan_id": res.plan.plan_id, "horizon": res.plan.horizon, "fx_hedge_share": res.plan.fx_hedge_share,
                "packs_total": sum(l.packs for l in res.plan.lines), "packs_by_route": res.plan.packs_by_route(),
                "shipments": sum(l.shipments for l in res.plan.lines),
                "solve": {"status": res.solve.status, "proven_optimal": res.solve.proven_optimal, "seconds": res.solve.seconds},
                "metrics_uzs": None if res.metrics is None else {
                    "expected": res.metrics.expected_uzs, "cvar95": res.metrics.cvar_uzs, "worst": res.metrics.worst_uzs,
                    "p_loss": res.metrics.p_loss}}
    failed = [c for c in res.checks if not c.passed]
    rep["plan"] = plan
    rep["supply_policy"] = {"minimum_expected_fill_rate": DEFAULT_SERVICE_LEVEL,
                             "basis": "explicit synthetic business policy"}
    if not has_supply(res.plan):
        fail("optimization", ValueError("No supply plan under the required service level"))
        rep["plan_withheld_reason"] = "no_supply_plan"
        return _finish(rep, output, res.snapshot.snapshot_id)
    rep["plan_note"] = ("cvar optimum is to import nothing: loss = fixed below-line costs only"
                        if plan and plan["packs_total"] == 0 else None)
    rep["plan_withheld_reason"] = None if res.plan is not None else "no feasible plan"
    rep["legality"] = {"checks_total": len(res.checks), "checks_failed": len(failed), "legal": bool(res.checks) and not failed}
    rep["warnings"] = list(res.warnings)
    snapshot_id = res.snapshot.snapshot_id
    plans["engine_cvar"] = res.plan
    alt = run_optimize(cg, scen_cfg=scen, opt_cfg=OptimizerConfig(risk_mode="expected", service_level=DEFAULT_SERVICE_LEVEL), rules=rules, fx=fx)
    plans["engine_expected"] = alt.plan
    rep["plan_expected_mode"] = None if alt.plan is None else {
        "packs_by_route": alt.plan.packs_by_route(), "metrics_uzs": {
            "expected": alt.metrics.expected_uzs, "cvar95": alt.metrics.cvar_uzs, "worst": alt.metrics.worst_uzs,
            "p_loss": alt.metrics.p_loss}, "legal": bool(alt.checks) and all(c.passed for c in alt.checks)}
    plans["client_baseline"] = baseline_plan(cg)
    try:
        nosplit = to_calculation_graph(graph, cfg.model_copy(update={"split_freight_from_fees": False}))
        rep["historical_markup_check"] = historical_markup_check(cg, nosplit, rules)
    except Exception as exc:  # noqa: BLE001
        rep["historical_markup_check"] = {"status": f"not computed: {type(exc).__name__}: {str(exc)[:200]}"}

    if diagnostics:
        rep["diagnostic_markup_rule_off"] = _diagnostic_markup_off(cg, graph, rules, fx, scen, n_truth)
    try:
        rep["truth_evaluation"] = truth_evaluation(cg, graph, plans, n_truth, rules, fx)
    except Exception as exc:  # noqa: BLE001
        fail("truth_evaluation", exc)
    rep["stage_reached"] = "truth_evaluation" if rep["truth_evaluation"] else rep["stage_reached"]
    return _finish(rep, output, snapshot_id)


def _diagnostic_markup_off(cg, graph, rules, fx, scen, n_truth) -> dict:
    """NOT a product result and NOT legal: the same chain with the wholesale 15% markup rule switched off in the twin
    (registry / retail ceilings stay). It isolates how much of the engine-vs-truth gap is the markup rule, because the
    simulation's evaluator prices at the registry ceiling and never applies the 15% rule."""
    from eval.truth_eval import MappingError, SimTruth
    off = dataclasses.replace(rules, max_markup=9.0)
    out: dict = {"status": "diagnostic only: legality checks are against the relaxed rules, so they prove nothing",
                 "max_markup_used": off.max_markup}
    try:
        plans = {}
        for mode in ("cvar", "expected"):
            r = run_optimize(cg, scen_cfg=scen, opt_cfg=OptimizerConfig(risk_mode=mode), rules=off, fx=fx)
            plans[f"diag_{mode}"] = r.plan
            out[f"twin_{mode}"] = None if r.plan is None else {
                "packs_by_route": r.plan.packs_by_route(), "expected_uzs": r.metrics.expected_uzs, "cvar95": r.metrics.cvar_uzs,
                "p_loss": r.metrics.p_loss}
        out["truth"] = truth_evaluation(cg, graph, plans, n_truth, off, fx)
    except (MappingError, Exception) as exc:  # noqa: BLE001
        out["error"] = f"{type(exc).__name__}: {str(exc)[:300]}"
    return out


def _finish(rep: dict, output: Path | None, snapshot_id: str) -> dict:
    rep["snapshot_id"] = snapshot_id
    rep["report_sha256"] = hashlib.sha256(json.dumps(rep, sort_keys=True, default=str).encode()).hexdigest()[:16]
    if output is not None:
        output.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return rep


# --------------------------------------------------------------------------- regret by level

def regret_by_level(reports: dict[str, dict]) -> dict:
    """Regret of planning on the graph Ingest assembled at level Lk, measured on the simulation truth, relative to
    planning on the clean world L0 (same optimizer, same scenario seed, same truth evaluator). L0 is itself not the
    truth optimum: the reference is the best plan the pipeline finds on clean documents. Levels whose chain stopped
    before a plan exists have no regret; they are listed with the stopping stage, not imputed."""
    def value(rep, mode):
        te = rep.get("truth_evaluation") or {}
        p = (te.get("plans") or {}).get(mode) or {}
        return p.get("oos_mean_profit_uzs"), p.get("nominal_profit_uzs")

    ref = reports.get("L0")
    out = {"reference": "plan optimised on L0 (clean documents), evaluated on truth", "levels": {}}
    for level in LEVELS:
        rep = reports.get(level)
        if rep is None:
            continue
        entry = {"snapshot_id": rep["snapshot_id"], "stage_reached": rep["stage_reached"], "failure": rep["failure"],
                 "questions_to_human": (rep.get("ingest") or {}).get("questions_to_human", {}).get("total"),
                 "rejected_groups": (rep.get("ingest") or {}).get("questions_to_human", {}).get("rejected_groups"),
                 "modes": {}}
        for mode in ("engine_cvar", "engine_expected", "client_baseline"):
            v, nom = value(rep, mode)
            rv, rnom = value(ref, mode) if ref else (None, None)
            entry["modes"][mode] = {
                "oos_mean_profit_uzs": v, "nominal_profit_uzs": nom,
                "regret_vs_L0_oos_uzs": None if v is None or rv is None else rv - v,
                "regret_vs_L0_nominal_uzs": None if nom is None or rnom is None else rnom - nom}
        entry["has_plan"] = any(m["oos_mean_profit_uzs"] is not None for m in entry["modes"].values())
        out["levels"][level] = entry
    return out


def attach_stage5(out_dir: Path) -> bool:
    """Put the full Stage 5 result (eval/engine_report.json: 1 000 out-of-sample scenarios, pre-registered thresholds)
    into the `truth_evaluation` slot of e2e_report_L0.json. Only if both reports carry the same snapshot_id (same
    graph, scenarios, rules, solver settings); otherwise nothing is attached."""
    e2e_path, s5_path = out_dir / "e2e_report_L0.json", out_dir / "engine_report.json"
    rep, s5 = json.loads(e2e_path.read_text(encoding="utf-8")), json.loads(s5_path.read_text(encoding="utf-8"))
    if rep["snapshot_id"] != s5["snapshot_id"] or not isinstance(rep.get("truth_evaluation"), dict):
        return False
    m = s5["metrics"]
    rep["truth_evaluation"]["stage5_full"] = {
        "source": "eval/engine_report.json", "snapshot_id": s5["snapshot_id"], "thresholds_sha256": s5["thresholds_sha256"],
        "truth": s5["truth"], "truth_calls": s5["truth_calls"], "all_pass": s5["all_pass"],
        "metrics_pass": {k: v.get("pass") for k, v in m.items() if "pass" in v},
        "calibration_gap_pp": m["calibration"]["gap_pp"], "uplift_uzs_per_year": m["uplift"]["uplift_uzs_per_year"],
        "uplift_engine": m["uplift"]["engine"], "uplift_baseline": m["uplift"]["baseline"], "vss_uzs": m["vss"]["vss_uzs"],
        "evpi_uzs": m["evpi"]["evpi_uzs"], "legal_rate": m["legality"]["legal_rate"],
        "optimality_match_rate": m["optimality"]["match_rate"], "recompute_after_shock_seconds": m["speed"]["recompute_after_shock_seconds"],
        "plan_lines": s5["plan"]["lines"]}
    rep.pop("report_sha256", None)
    rep["report_sha256"] = hashlib.sha256(json.dumps(rep, sort_keys=True, default=str).encode()).hexdigest()[:16]
    e2e_path.write_text(json.dumps(rep, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return True


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--worlds", nargs="*", default=list(LEVELS))
    ap.add_argument("--n-truth", type=int, default=200)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "eval")
    ap.add_argument("--no-diagnostics", action="store_true")
    ap.add_argument("--attach-stage5", action="store_true", help="only merge eval/engine_report.json into e2e_report_L0.json")
    a = ap.parse_args()
    if a.attach_stage5:
        print("attached" if attach_stage5(a.out_dir) else "not attached: snapshot ids differ")
        return
    reports = {}
    for level in a.worlds:
        reports[level] = run_world(level, a.out_dir / f"e2e_report_{level}.json", n_truth=a.n_truth,
                                   diagnostics=not a.no_diagnostics and level == "L0")
        r = reports[level]
        print(level, r["stage_reached"], r["failure"], r["snapshot_id"], flush=True)
    reg = regret_by_level(reports)
    reg["snapshot_ids"] = {k: v["snapshot_id"] for k, v in reports.items()}
    (a.out_dir / "regret_by_level.json").write_text(json.dumps(reg, ensure_ascii=False, indent=1, default=str), encoding="utf-8")


if __name__ == "__main__":
    main()
