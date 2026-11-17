"""HTTP API. Stateless demo; deliberately no multi-user, persistence or authentication."""

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from .demo_data import demo_case
from .economics import ASSUMPTIONS
from .models import OptimizeRequest, OptimizeResponse, PharmaCase
from .solver import SolverFailure, optimize

app = FastAPI(
    title="LAGRANGE Rx -- Regulatory-Aware Profitability Lab",
    version="0.1.0",
    description="Hackathon prototype. All demo company data are synthetic, and regulatory rules require expert review.",
)

WEB_DIR = Path(__file__).resolve().parents[2] / "web"
if WEB_DIR.is_dir():
    app.mount("/assets", StaticFiles(directory=WEB_DIR), name="assets")


@app.get("/", include_in_schema=False)
def homepage():
    if not (WEB_DIR / "index.html").exists():
        raise HTTPException(404, "Frontend is not installed")
    return FileResponse(WEB_DIR / "index.html")


@app.get("/api/v1/health")
def health() -> dict[str, str]:
    return {"status": "ok", "version": "0.1.0", "data": "synthetic"}


@app.get("/api/v1/demo-case", response_model=PharmaCase)
def get_demo_case() -> PharmaCase:
    return demo_case()


@app.get("/api/v1/assumptions")
def assumptions() -> dict[str, object]:
    return {"data_classification": "synthetic", "limitations": ASSUMPTIONS,
            "primary_rules": ["https://lex.uz/uz/acts/7952162", "https://lex.uz/en/acts/-7345588"]}


@app.post("/api/v1/optimize", response_model=OptimizeResponse)
def run_optimization(request: OptimizeRequest) -> OptimizeResponse:
    try:
        return optimize(request.case or demo_case(), risk_aversion=request.risk_aversion)
    except SolverFailure as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc


@app.get('/api/v1/experiments/cost-plus')
def cost_plus_experiment(discount_pct: float = 5.0) -> dict[str, object]:
    """Demonstrate the possible inverse cost/profit effect in the toy markup model.

    Not a legal conclusion: the underlying invoice base and selling-price rules are
    explicitly illustrative. This endpoint intentionally uses one base scenario.
    """
    if not 0 <= discount_pct <= 25:
        raise HTTPException(status_code=422, detail='discount_pct must be from 0 to 25')
    import math
    from .economics import unit_economics

    case = demo_case()
    source = next(o for o in case.options if o.id == case.baseline_option_id)
    scenario = next(s for s in case.scenarios if s.id == 'base')
    discounted = source.model_copy(update={
        'factory_cost_uzs': round(source.factory_cost_uzs * (1 - discount_pct / 100))
    })

    def economics_of(option):
        e = unit_economics(case, option, scenario)
        selling_price = min(
            case.assumed_max_contract_wholesale_uzs,
            e.registered_wholesale_cap_uzs,
            e.max_markup_wholesale_uzs,
        )
        fixed = option.activation_fixed_uzs + math.ceil(
            case.annual_demand_packs / option.max_shipment_packs
        ) * option.shipment_fixed_uzs
        annual_profit = case.annual_demand_packs * (
            selling_price - e.acquisition_base_uzs - e.below_line_uzs
        ) - fixed
        return {
            'factory_cost_uzs': option.factory_cost_uzs,
            'acquisition_base_uzs': round(e.acquisition_base_uzs, 2),
            'maximum_sale_price_uzs': round(selling_price, 2),
            'net_unit_contribution_uzs': round(selling_price - e.acquisition_base_uzs - e.below_line_uzs, 2),
            'annual_profit_uzs': round(annual_profit, 2),
        }

    before = economics_of(source)
    after = economics_of(discounted)
    return {
        'data_classification': 'synthetic',
        'experiment': 'cost_plus_markup_paradox',
        'discount_pct': discount_pct,
        'before': before,
        'after': after,
        'profit_delta_uzs': round(after['annual_profit_uzs'] - before['annual_profit_uzs'], 2),
        'caveat': 'Mathematical illustration only; not a determination of legal pricing or realized savings.',
    }
