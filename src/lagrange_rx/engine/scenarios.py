"""Scenario generator (docs/DECISION_ENGINE.md section 3): sampling, k-medoids reduction, stress cases.

All randomness comes from one numpy Generator(PCG64(seed)); everything is deterministic per seed.
"""
from __future__ import annotations

import csv
import dataclasses
import hashlib
import json
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from lagrange_rx.engine.types import Scenario
from lagrange_rx.engine.inputs import CalculationGraph
from lagrange_rx.engine.rules import Rules

HORIZON = 12
MIN_HISTORY_MONTHS = 24
DATA_DIR = Path(__file__).resolve().parents[3] / "data"
DEFAULT_SIGMA = 0.15
_EPS = 1e-12


# --------------------------------------------------------------------------- FX history

@dataclass(frozen=True)
class FxHistory:
    source: str  # "cbu" | "synthetic-sample"
    months: tuple[str, ...]
    rates: dict[str, tuple[float, ...]]  # UZS per 1 unit, monthly, aligned with months

    def log_returns(self, currencies: Sequence[str]) -> np.ndarray:
        """Array (T-1, len(currencies)) of monthly log-returns."""
        missing = [c for c in currencies if c not in self.rates]
        if missing:
            raise ValueError(f"FX history has no data for currencies {missing}")
        return np.diff(np.log(np.array([self.rates[c] for c in currencies])), axis=1).T


def _default_fx_path() -> Path:
    real = DATA_DIR / "cbu_fx_monthly.csv"
    return real if real.exists() else DATA_DIR / "fx_sample_SYNTHETIC.csv"


def _read_csv(path: Path) -> tuple[str, list[dict[str, str]]]:
    if not path.exists():
        raise FileNotFoundError(f"FX history file not found: {path}")
    source, body = "", []
    for line in path.read_text().splitlines():
        if line.startswith("#"):
            if line[1:].strip().startswith("source:"):
                source = line.split(":", 1)[1].strip()
        elif line.strip():
            body.append(line)
    if source not in ("cbu", "synthetic-sample"):
        raise ValueError(f"{path}: header must contain '# source: cbu' or '# source: synthetic-sample'")
    return source, list(csv.DictReader(body))


def _table(rows: list[dict[str, str]], path: Path) -> dict[str, dict[str, float]]:
    table: dict[str, dict[str, float]] = {}
    for r in rows:
        try:
            rate = float(r["rate"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"{path}: bad row {r}") from exc
        if not rate > 0:
            raise ValueError(f"{path}: rate must be positive, got {rate} for {r.get('currency')} {r.get('month')}")
        table.setdefault(r["currency"], {})[r["month"]] = rate
    return table


def load_fx_history(path: Path | str | None = None) -> FxHistory:
    p = Path(path) if path else _default_fx_path()
    source, rows = _read_csv(p)
    table = _table(rows, p)
    months = sorted({m for per in table.values() for m in per})
    if len(months) < MIN_HISTORY_MONTHS:
        raise ValueError(f"{p}: need at least {MIN_HISTORY_MONTHS} months of FX history, got {len(months)}")
    for c, per in table.items():
        if sorted(per) != months:
            raise ValueError(f"{p}: currency {c} is not aligned with the other currencies' months")
    return FxHistory(source, tuple(months), {c: tuple(per[m] for m in months) for c, per in sorted(table.items())})


# --------------------------------------------------------------------------- config / set

@dataclass(frozen=True)
class ScenarioConfig:
    seed: int = 20261117
    n: int = 1000
    k: int = 40
    block_len: int = 3
    freight_sigma: dict[str, float] = field(
        default_factory=lambda: {"sea": 0.20, "rail": 0.12, "road": 0.15, "air": 0.25})
    stress_weight_each: float = 0.01
    p_cut: float | None = None  # None -> Rules.p_cut
    depth_min: float | None = None
    depth_max: float | None = None
    oos_seed_offset: int = 1_000_003

    def cut_params(self, rules: Rules) -> tuple[float, float, float]:
        pick = lambda v, d: d if v is None else v  # noqa: E731
        return pick(self.p_cut, rules.p_cut), pick(self.depth_min, rules.depth_min), pick(self.depth_max, rules.depth_max)


@dataclass(frozen=True)
class ScenarioSet:
    full: tuple[Scenario, ...]
    reduced: tuple[Scenario, ...]
    stress: tuple[Scenario, ...]
    distance_report: dict
    seed: int
    config_hash: str
    fx_source: str
    stress_weight_each: float = 0.01

    def optimisation_set(self) -> tuple[Scenario, ...]:
        scale = 1.0 - len(self.stress) * self.stress_weight_each
        red = tuple(s.model_copy(update={"weight": s.weight * scale}) for s in self.reduced)
        st = tuple(s.model_copy(update={"weight": self.stress_weight_each}) for s in self.stress)
        return red + st


# --------------------------------------------------------------------------- sampling

def bootstrap_indices(rng: np.random.Generator, n_returns: int, horizon: int, block_len: int) -> np.ndarray:
    """Circular block bootstrap: consecutive blocks of `block_len` return indices, wrapping around."""
    n_blocks = -(-horizon // block_len)
    starts = rng.integers(0, n_returns, n_blocks)
    idx = (starts[:, None] + np.arange(block_len)[None, :]) % n_returns
    return idx.ravel()[:horizon]


def _fx_paths(rng, graph, returns, currencies, block_len) -> dict[str, np.ndarray]:
    idx = bootstrap_indices(rng, returns.shape[0], HORIZON, block_len)
    cum = np.cumsum(returns[idx], axis=0)
    return {c: graph.nominal_fx[c] * np.exp(cum[:, j]) for j, c in enumerate(currencies)}


def _draw_cut(rng, p_cut, dmin, dmax) -> tuple[int | None, float]:
    happens, month, depth = rng.random(), int(rng.integers(1, HORIZON + 1)), float(rng.uniform(dmin, dmax))
    return (month, depth) if happens < p_cut else (None, 0.0)


def _draw_freight(rng, modes, sigma) -> dict[str, float]:
    z = rng.standard_normal(len(modes))
    out = {}
    for m, zi in zip(modes, z):
        s = sigma.get(m, DEFAULT_SIGMA)
        out[m] = float(np.exp(-s * s / 2 + s * zi))
    return out


def _supplier_routes(graph: CalculationGraph) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {s.id: [] for s in sorted(graph.suppliers, key=lambda s: s.id)}
    for r in graph.routes:
        out[r.supplier_id].append(r.id)
    return {k: tuple(v) for k, v in out.items()}


def _draw_availability(rng, graph) -> dict[str, bool]:
    rel = {s.id: s.reliability for s in graph.suppliers}
    out: dict[str, bool] = {}
    for sid, routes in _supplier_routes(graph).items():
        if rng.random() >= rel[sid]:
            out.update({rid: False for rid in routes})
    return out


def demand_from_quantile(u: float, p10: float, p50: float, p90: float) -> float:
    """Inverse CDF: piecewise linear through (0.1,p10),(0.5,p50),(0.9,p90), linear extrapolation, >= 0."""
    if u < 0.5:
        lo, hi, ulo, uhi = p10, p50, 0.1, 0.5
    else:
        lo, hi, ulo, uhi = p50, p90, 0.5, 0.9
    return max(0.0, lo + (hi - lo) * (u - ulo) / (uhi - ulo))


def _one_sample(rng, i, n, graph, returns, currencies, modes, cut_cfg, cfg) -> Scenario:
    paths = _fx_paths(rng, graph, returns, currencies, cfg.block_len)
    cut_month, cut_depth = _draw_cut(rng, *cut_cfg)
    freight = _draw_freight(rng, modes, cfg.freight_sigma)
    avail = _draw_availability(rng, graph)
    d = graph.demand
    demand = demand_from_quantile(rng.random(), d.p10, d.p50, d.p90)
    ref = graph.product.reference_currency
    return Scenario(
        id=f"s{i + 1:04d}", name=f"sample {i + 1}", kind="sample", weight=1.0 / n,
        fx={c: float(p.mean()) for c, p in paths.items()},
        ref_fx_start=graph.nominal_fx[ref], ref_fx_path=tuple(float(v) for v in paths[ref]),
        cut_month=cut_month, cut_depth=cut_depth, freight_mult=freight, route_available=avail, demand=demand,
    )


def _sample(graph, fx, rules, cfg, seed) -> tuple[Scenario, ...]:
    currencies = sorted(graph.nominal_fx)
    returns = fx.log_returns(currencies)
    modes = sorted({r.mode for r in graph.routes})
    cut_cfg = cfg.cut_params(rules)
    rng = np.random.Generator(np.random.PCG64(seed))
    return tuple(_one_sample(rng, i, cfg.n, graph, returns, currencies, modes, cut_cfg, cfg) for i in range(cfg.n))


def out_of_sample(graph, fx, rules, cfg) -> tuple[Scenario, ...]:
    return _sample(graph, fx, rules, cfg, cfg.seed + cfg.oos_seed_offset)


# --------------------------------------------------------------------------- reduction

def _features(samples: Sequence[Scenario], graph: CalculationGraph) -> np.ndarray:
    ccys, modes = sorted(graph.nominal_fx), sorted({r.mode for r in graph.routes})
    rids = sorted(r.id for r in graph.routes)
    rows = []
    for s in samples:
        cut = 0.0 if s.cut_month is None else s.cut_depth * (13 - s.cut_month) / HORIZON
        rows.append(
            [np.log(s.fx[c] / graph.nominal_fx[c]) for c in ccys]
            + [np.log(s.ref_fx_path[-1] / s.ref_fx_start), cut]
            + [s.freight(m) for m in modes] + [float(s.available(r)) for r in rids] + [s.demand]
        )
    x = np.array(rows, dtype=float)
    sd = x.std(axis=0)
    return (x - x.mean(axis=0)) / np.where(sd < _EPS, 1.0, sd)


def _pairwise(x: np.ndarray) -> np.ndarray:
    sq = (x * x).sum(axis=1)
    d2 = sq[:, None] + sq[None, :] - 2.0 * x @ x.T
    return np.sqrt(np.maximum(d2, 0.0))


def _init_medoids(d: np.ndarray, k: int, rng) -> np.ndarray:
    """k-medoids++ seeding (D^2 weighting)."""
    n = d.shape[0]
    med = [int(rng.integers(n))]
    for _ in range(k - 1):
        near = d[:, med].min(axis=1) ** 2
        total = near.sum()
        p = near / total if total > _EPS else np.full(n, 1.0 / n)
        p[med] = 0.0
        p = p / p.sum() if p.sum() > _EPS else np.where(np.isin(np.arange(n), med), 0.0, 1.0) / (n - len(med))
        med.append(int(rng.choice(n, p=p)))
    return np.array(med)


def _assign(d: np.ndarray, med: np.ndarray) -> np.ndarray:
    labels = d[:, med].argmin(axis=1)
    labels[med] = np.arange(len(med))  # a medoid always owns itself (duplicate points)
    return labels


def _update(d: np.ndarray, med: np.ndarray, labels: np.ndarray) -> np.ndarray:
    new = med.copy()
    for j in range(len(med)):
        members = np.flatnonzero(labels == j)
        new[j] = members[d[np.ix_(members, members)].sum(axis=1).argmin()]
    return new


def kmedoids(d: np.ndarray, k: int, rng, max_iter: int = 100) -> tuple[np.ndarray, np.ndarray]:
    """Alternating k-medoids on a distance matrix. Returns (medoid indices, labels in 0..k-1)."""
    n = d.shape[0]
    if k >= n:
        return np.arange(n), np.arange(n)
    med = _init_medoids(d, k, rng)
    labels = _assign(d, med)
    for _ in range(max_iter):
        new = _update(d, med, labels)
        if np.array_equal(new, med):
            break
        med, labels = new, _assign(d, new)
    return med, labels


def _reduce(samples, graph, k, rng) -> tuple[tuple[Scenario, ...], dict]:
    x = _features(samples, graph)
    d = _pairwise(x)
    med, labels = kmedoids(d, k, rng)
    n = len(samples)
    share = np.bincount(labels, minlength=len(med)) / n
    reduced = tuple(samples[m].model_copy(update={"weight": float(w)}) for m, w in zip(med, share))
    dist = d[np.arange(n), med[labels]]
    return reduced, {"mean_dist_to_medoid": float(dist.mean()), "max_dist_to_medoid": float(dist.max())}


# --------------------------------------------------------------------------- distance report

def _wmean(scs: Sequence[Scenario], f) -> float:
    w = np.array([s.weight for s in scs])
    return float(np.dot(w, [f(s) for s in scs]) / w.sum())


def _gap(red: float, full: float) -> float:
    return abs(red - full) / max(abs(full), _EPS)


def _failure_freq(scs, supplier_routes) -> float:
    groups = [rs for rs in supplier_routes.values() if rs]
    return _wmean(scs, lambda s: sum(all(not s.available(r) for r in rs) for rs in groups) / max(len(groups), 1))


def _stat_gaps(full, reduced, graph) -> dict:
    gap = lambda f: _gap(_wmean(reduced, f), _wmean(full, f))  # noqa: E731
    sup = _supplier_routes(graph)
    return {
        "gap_demand_mean": gap(lambda s: s.demand),
        "gap_fx_mean": {c: gap(lambda s, c=c: s.fx[c]) for c in sorted(graph.nominal_fx)},
        "gap_freight_mean": {m: gap(lambda s, m=m: s.freight(m)) for m in sorted({r.mode for r in graph.routes})},
        "gap_cut_frequency": gap(lambda s: float(s.cut_month is not None)),
        "gap_supplier_failure_frequency": _gap(_failure_freq(reduced, sup), _failure_freq(full, sup)),
    }


# --------------------------------------------------------------------------- mean + stress

def _mean_cut(samples: Sequence[Scenario]) -> tuple[int | None, float]:
    eff = float(np.mean([0.0 if s.cut_month is None else (13 - s.cut_month) / HORIZON * s.cut_depth for s in samples]))
    return (1, eff) if eff > 0 else (None, 0.0)


def mean_scenario(samples: Sequence[Scenario], graph: CalculationGraph) -> Scenario:
    mean = lambda f: float(np.mean([f(s) for s in samples]))  # noqa: E731
    cut_month, cut_depth = _mean_cut(samples)
    return Scenario(
        id="mean", name="mean of samples", kind="mean", weight=1.0,
        fx={c: mean(lambda s, c=c: s.fx[c]) for c in sorted(graph.nominal_fx)},
        ref_fx_start=graph.nominal_fx[graph.product.reference_currency],
        ref_fx_path=tuple(float(v) for v in np.mean([s.ref_fx_path for s in samples], axis=0)),
        cut_month=cut_month, cut_depth=cut_depth,
        freight_mult={m: mean(lambda s, m=m: s.freight(m)) for m in sorted({r.mode for r in graph.routes})},
        route_available={}, demand=mean(lambda s: s.demand),
    )


def _largest_supplier(graph: CalculationGraph) -> str:
    owner = {r.id: r.supplier_id for r in graph.routes}
    packs = {sid: 0 for sid in _supplier_routes(graph)}
    for sh in graph.shipments:
        packs[owner[sh.route_id]] += sh.packs
    return min(packs, key=lambda sid: (-packs[sid], sid))


def stress_scenarios(base: Scenario, graph: CalculationGraph, weight: float | None = None) -> tuple[Scenario, ...]:
    w = ScenarioConfig().stress_weight_each if weight is None else weight
    mk = lambda sid, name, **kw: base.model_copy(  # noqa: E731
        update={"id": sid, "name": name, "kind": "stress", "weight": w, **kw})
    sup = _largest_supplier(graph)
    down = {**base.route_available, **{rid: False for rid in _supplier_routes(graph)[sup]}}
    modes = sorted({r.mode for r in graph.routes})
    return (
        mk("stress_fx_up15", "FX +15%", fx={c: v * 1.15 for c, v in base.fx.items()},
           ref_fx_path=tuple(v * 1.15 for v in base.ref_fx_path)),
        mk("stress_cut_37", "Ceiling cut -37% from month 1", cut_month=1, cut_depth=0.37),
        mk("stress_supplier_A_down", f"Largest supplier ({sup}) unavailable", route_available=down),
        mk("stress_freight_up35", "Freight +35%", freight_mult={m: base.freight(m) * 1.35 for m in modes}),
    )


# --------------------------------------------------------------------------- orchestration

def _config_hash(graph, fx, rules, cfg, seed) -> str:
    payload = {"cfg": dataclasses.asdict(cfg), "seed": seed, "rules": rules.sha256, "fx_source": fx.source,
               "fx_months": [fx.months[0], fx.months[-1], len(fx.months)], "client": graph.client_id}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def generate_scenarios(graph: CalculationGraph, fx: FxHistory, rules: Rules, cfg: ScenarioConfig,
                       *, seed_offset: int = 0) -> ScenarioSet:
    seed = cfg.seed + seed_offset
    full = _sample(graph, fx, rules, cfg, seed)
    rng = np.random.Generator(np.random.PCG64(seed + 17))  # separate stream for the reduction
    reduced, report = _reduce(full, graph, cfg.k, rng)
    report.update(_stat_gaps(full, reduced, graph))
    stress = stress_scenarios(mean_scenario(full, graph), graph, cfg.stress_weight_each)
    return ScenarioSet(full, reduced, stress, report, seed, _config_hash(graph, fx, rules, cfg, seed),
                       fx.source, cfg.stress_weight_each)
