"""HTTP API of the engine (section 9 of docs/DECISION_ENGINE.md). In-memory stores; run with
`uvicorn lagrange_rx.api.engine:app`."""
from __future__ import annotations

import math
from dataclasses import asdict, is_dataclass
from typing import Any, Literal

from pathlib import Path

from fastapi import APIRouter, FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from lagrange_rx.engine.explain import apply_change  # noqa: F401  (validates change keys via what_if)
from lagrange_rx.engine.optimize import OptimizerConfig
from lagrange_rx.engine.scenarios import ScenarioConfig, load_fx_history, mean_scenario
from lagrange_rx.engine.service import (
    CalibrationRequired, EngineResult, LegalityError, calculation_graph, make_scenarios, run_optimize, run_what_if,
)
from lagrange_rx.engine.twin import TwinError, twin_baseline, unit_economics
from lagrange_rx.engine._legacy_ontology import ClientGraph
from lagrange_rx.engine.rules import load_rules
from lagrange_rx.api.seam import GraphNotUsable, graph_for_engine
from lagrange_rx.api.store import GraphStore


def jsonable(obj: Any) -> Any:
    if isinstance(obj, BaseModel):
        return jsonable(obj.model_dump(mode="python", by_alias=True))
    if is_dataclass(obj) and not isinstance(obj, type):
        return jsonable({k: v for k, v in asdict(obj).items() if k != "context"})
    if isinstance(obj, dict):
        return {str(k): jsonable(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple, set, frozenset)):
        return [jsonable(v) for v in obj]
    if isinstance(obj, float) and not math.isfinite(obj):
        return None
    return obj


class GraphRef(BaseModel):
    model_config = ConfigDict(extra="forbid")
    graph_id: str | None = None
    graph: ClientGraph | None = None


class ScenariosRequest(GraphRef):
    seed: int = 20261117
    n: int = Field(default=1000, ge=10, le=20000)
    k: int = Field(default=40, ge=1, le=500)
    stress: bool = True


class OptimizeRequest(GraphRef):
    risk_mode: Literal["expected", "cvar", "worst_case"] = "cvar"
    alpha: float = Field(default=0.95, gt=0.0, lt=1.0)
    lam: float = Field(default=0.5, ge=0.0, le=1.0)
    hedge_enabled: bool = False
    emergency_enabled: bool = True
    service_level: float | None = Field(default=None, gt=0.0, le=1.0)
    min_expected_profit_uzs: float | None = None
    budget_uzs: float | None = None
    time_limit_s: float = Field(default=20.0, gt=0.0, le=120.0)
    seed: int = 20261117
    n: int = Field(default=1000, ge=10, le=20000)
    k: int = Field(default=40, ge=1, le=500)
    demo_mode: bool = False


class WhatIfRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str
    change: dict[str, Any]


def make_router(data_root: str | Path | None = None) -> APIRouter:
    """/engine/* router. Graphs committed by /ingest under `data_root` resolve by graph_id."""
    app = APIRouter()
    store = GraphStore(Path(data_root)) if data_root is not None else None
    rules = load_rules()
    graphs: dict[str, ClientGraph] = {}
    plans: dict[str, EngineResult] = {}
    fx = load_fx_history()

    def resolve(ref: GraphRef) -> ClientGraph:
        if ref.graph is not None:
            return ref.graph
        if ref.graph_id and ref.graph_id in graphs:
            return graphs[ref.graph_id]
        if ref.graph_id and store is not None:
            try:
                committed = store.get(ref.graph_id)
            except ValueError as exc:
                raise HTTPException(500, f"stored graph invalid: {exc}") from exc
            if committed is not None:
                try:
                    return graph_for_engine(committed)
                except GraphNotUsable as exc:
                    raise HTTPException(422, {"error": "graph_not_usable_by_engine", "reason": str(exc)}) from exc
        raise HTTPException(404, "graph not found: pass `graph` or a registered `graph_id`")

    @app.post("/graphs")
    def register_graph(graph: ClientGraph):
        graphs[graph.client_id] = graph
        return {"graph_id": graph.client_id}

    @app.post("/engine/twin")
    def twin(ref: GraphRef):
        g = calculation_graph(resolve(ref))
        cal = twin_baseline(g)
        out: dict[str, Any] = {"calibration": jsonable(cal)}
        try:
            sset = make_scenarios(g, rules, fx, ScenarioConfig(n=200, k=20))
            ue = unit_economics(g, mean_scenario(sset.full, g), rules)
            out["unit_economics"] = jsonable(ue)
        except TwinError as exc:
            raise HTTPException(422, str(exc)) from exc
        return out

    @app.post("/engine/scenarios")
    def scenarios(req: ScenariosRequest):
        g = calculation_graph(resolve(req))
        sset = make_scenarios(g, rules, fx, ScenarioConfig(seed=req.seed, n=req.n, k=req.k))
        return jsonable({
            "seed": sset.seed, "config_hash": sset.config_hash, "fx_source": sset.fx_source,
            "full_count": len(sset.full), "reduced": sset.optimisation_set() if req.stress else sset.reduced,
            "stress": sset.stress, "distance_report": sset.distance_report,
        })

    @app.post("/engine/optimize")
    def optimize_endpoint(req: OptimizeRequest):
        g = resolve(req)
        cfg = OptimizerConfig(risk_mode=req.risk_mode, alpha=req.alpha, lam=req.lam, hedge_enabled=req.hedge_enabled,
                              emergency_enabled=req.emergency_enabled, service_level=req.service_level,
                              min_expected_profit_uzs=req.min_expected_profit_uzs, budget_uzs=req.budget_uzs,
                              time_limit_s=req.time_limit_s)
        try:
            res = run_optimize(g, rules=rules, fx=fx, scen_cfg=ScenarioConfig(seed=req.seed, n=req.n, k=req.k),
                               opt_cfg=cfg, demo_mode=req.demo_mode)
        except CalibrationRequired as exc:
            raise HTTPException(409, {"error": "twin_not_calibrated", "calibration": jsonable(exc.calibration)}) from exc
        except TwinError as exc:
            raise HTTPException(422, str(exc)) from exc
        except LegalityError as exc:
            raise HTTPException(500, {"error": "legality_check_failed", "failed": jsonable(exc.failed[:20])}) from exc
        key = res.plan.plan_id if res.plan else f"impossible-{res.snapshot.snapshot_id[:12]}"
        plans[key] = res
        return {"id": key, **_result_json(res)}

    @app.post("/engine/what-if")
    def what_if_endpoint(req: WhatIfRequest):
        res = plans.get(req.plan_id)
        if res is None or res.plan is None:
            raise HTTPException(404, "plan not found")
        try:
            return jsonable(run_what_if(res, req.change))
        except (ValueError, KeyError) as exc:
            raise HTTPException(422, str(exc)) from exc

    @app.get("/engine/plans/{plan_id}")
    def get_plan(plan_id: str):
        res = plans.get(plan_id)
        if res is None:
            raise HTTPException(404, "plan not found")
        return {"id": plan_id, **_result_json(res)}

    return app


def create_app() -> FastAPI:
    """Standalone engine app (kept for old callers); the unified app is api.app.create_app."""
    app = FastAPI(title="LAGRANGE Rx decision engine", version="0.1.0")
    app.include_router(make_router())
    return app


def _result_json(res: EngineResult) -> dict[str, Any]:
    failed = [c for c in res.checks if not c.passed]
    return jsonable({
        "plan": res.plan, "snapshot": res.snapshot, "calibration": res.calibration, "solve": res.solve,
        "metrics": res.metrics, "legal": not failed and bool(res.checks) or res.plan is None,
        "checks_total": len(res.checks), "checks_failed": len(failed), "checks": res.checks,
        "constraints": res.constraints, "waterfall": res.waterfall, "warnings": res.warnings,
        "impossible": res.impossible, "seconds": res.seconds,
    })


app = create_app()
