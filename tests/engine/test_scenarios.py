import time

import numpy as np
import pytest

from lagrange_rx.engine.scenarios import (
    ScenarioConfig, bootstrap_indices, generate_scenarios, mean_scenario, out_of_sample, stress_scenarios,
)
from tests.engine.conftest import make_history


def cfg(**kw):
    return ScenarioConfig(**{"n": 400, "k": 20, **kw})


@pytest.fixture(scope="module")
def sset(graph, fx, rules):
    return generate_scenarios(graph, fx, rules, cfg())


def test_deterministic_per_seed(graph, fx, rules, sset):
    again = generate_scenarios(graph, fx, rules, cfg())
    assert again.full == sset.full and again.reduced == sset.reduced
    assert again.config_hash == sset.config_hash


def test_different_seeds_differ(graph, fx, rules, sset):
    other = generate_scenarios(graph, fx, rules, cfg(seed=7))
    assert other.full != sset.full and other.config_hash != sset.config_hash
    shifted = generate_scenarios(graph, fx, rules, cfg(), seed_offset=5)
    assert shifted.full != sset.full and shifted.seed == sset.seed + 5


def test_weights_and_sizes(sset):
    assert len(sset.full) == 400 and len(sset.reduced) == 20
    assert sum(s.weight for s in sset.full) == pytest.approx(1.0)
    assert sum(s.weight for s in sset.reduced) == pytest.approx(1.0)
    assert {s.kind for s in sset.full + sset.reduced} == {"sample"}
    assert len({s.id for s in sset.full}) == 400 and sset.full[0].id == "s0001"


def test_k_ge_n_returns_all(graph, fx, rules):
    s = generate_scenarios(graph, fx, rules, cfg(n=10, k=50))
    assert len(s.reduced) == 10 and sum(x.weight for x in s.reduced) == pytest.approx(1.0)


def test_supplier_failure_frequency(graph, fx, rules):
    n = 4000
    full = generate_scenarios(graph, fx, rules, cfg(n=n, k=5)).full
    for sup, routes, rel in (("SA", ("ra_sea", "ra_air"), 0.8), ("SB", ("rb_rail", "rb_road"), 0.95)):
        fails = [all(not s.available(r) for r in routes) for s in full]
        both = [all(not s.available(r) for r in routes) or all(s.available(r) for r in routes) for s in full]
        assert all(both)  # all routes of a supplier fail together
        p = 1 - rel
        assert abs(np.mean(fails) - p) < 3 * np.sqrt(p * (1 - p) / n)
    assert all(v is False for s in full for v in s.route_available.values())


def test_cut_frequency_and_range(graph, fx, rules):
    n = 4000
    full = generate_scenarios(graph, fx, rules, cfg(n=n, k=5)).full
    cuts = [s for s in full if s.cut_month is not None]
    p = rules.p_cut
    assert abs(len(cuts) / n - p) < 3 * np.sqrt(p * (1 - p) / n)
    assert all(rules.depth_min <= s.cut_depth <= rules.depth_max and 1 <= s.cut_month <= 12 for s in cuts)
    assert {s.cut_month for s in cuts} == set(range(1, 13))
    assert all(s.cut_depth == 0.0 for s in full if s.cut_month is None)


def test_cut_params_overridable(graph, fx, rules):
    full = generate_scenarios(graph, fx, rules, cfg(p_cut=1.0, depth_min=0.2, depth_max=0.2)).full
    assert all(s.cut_month is not None and s.cut_depth == pytest.approx(0.2) for s in full)


def test_demand_quantiles(graph, fx, rules):
    full = generate_scenarios(graph, fx, rules, cfg(n=6000, k=5)).full
    d = np.array([s.demand for s in full])
    q = np.quantile(d, [0.1, 0.5, 0.9])
    assert q == pytest.approx([800_000, 1_000_000, 1_300_000], rel=0.03)
    assert d.min() >= 0


def test_freight_mean_one_and_modes(graph, fx, rules):
    full = generate_scenarios(graph, fx, rules, cfg(n=4000, k=5)).full
    assert set(full[0].freight_mult) == {"sea", "air", "rail", "road"}
    for m in ("sea", "air", "rail", "road"):
        assert np.mean([s.freight(m) for s in full]) == pytest.approx(1.0, abs=0.02)
    assert np.std([s.freight("air") for s in full]) > np.std([s.freight("rail") for s in full])


def test_fx_paths_use_historical_returns(graph, fx, rules, sset):
    hist = np.diff(np.log(fx.rates["USD"]))
    start = graph.nominal_fx["USD"]
    for s in sset.full[:50]:
        assert len(s.ref_fx_path) == 12 and s.ref_fx_start == start
        steps = np.diff(np.log((start,) + s.ref_fx_path))
        assert all(np.isclose(hist, st).any() for st in steps)
        assert s.fx["USD"] == pytest.approx(np.mean(s.ref_fx_path))


def test_block_structure():
    rng = np.random.default_rng(1)
    idx = bootstrap_indices(rng, 20, 12, 3)
    assert len(idx) == 12 and all(0 <= i < 20 for i in idx)
    for b in range(0, 12, 3):
        assert [(idx[b + j] - idx[b]) % 20 for j in range(3)] == [0, 1, 2]


def test_cross_currency_same_indices(graph, rules):
    h = make_history(usd_equals_eur=True)
    # EUR returns identical to USD returns -> identical relative moves iff same indices are used
    s = generate_scenarios(graph, h, rules, cfg(n=30, k=5)).full
    for x in s:
        assert x.fx["EUR"] / 13800.0 == pytest.approx(x.fx["USD"] / 12700.0)


def test_mean_scenario(graph, sset):
    m = mean_scenario(sset.full, graph)
    assert m.kind == "mean" and m.id == "mean" and m.route_available == {}
    assert m.fx["USD"] == pytest.approx(np.mean([s.fx["USD"] for s in sset.full]))
    assert m.demand == pytest.approx(np.mean([s.demand for s in sset.full]))
    assert m.freight("sea") == pytest.approx(np.mean([s.freight("sea") for s in sset.full]))
    assert len(m.ref_fx_path) == 12
    exp = np.mean([(13 - s.cut_month) / 12 * s.cut_depth if s.cut_month else 0.0 for s in sset.full])
    assert m.cut_month == 1 and m.cut_depth == pytest.approx(exp)


def test_mean_scenario_no_cut(graph, fx, rules):
    s = generate_scenarios(graph, fx, rules, cfg(p_cut=0.0, n=20, k=5)).full
    m = mean_scenario(s, graph)
    assert m.cut_month is None and m.cut_depth == 0.0


def test_stress_scenarios(graph, sset):
    base = mean_scenario(sset.full, graph)
    st = {s.id: s for s in stress_scenarios(base, graph)}
    assert list(st) == ["stress_fx_up15", "stress_cut_37", "stress_supplier_A_down", "stress_freight_up35"]
    assert all(s.kind == "stress" and s.weight == 0.01 for s in st.values())
    up = st["stress_fx_up15"]
    assert up.fx["USD"] == pytest.approx(base.fx["USD"] * 1.15) and up.ref_fx_start == base.ref_fx_start
    assert up.ref_fx_path == pytest.approx(tuple(v * 1.15 for v in base.ref_fx_path))
    assert st["stress_cut_37"].cut_month == 1 and st["stress_cut_37"].cut_depth == 0.37
    # SA has most shipped packs (1000 > 500)
    down = st["stress_supplier_A_down"]
    assert not down.available("ra_sea") and not down.available("ra_air")
    assert down.available("rb_rail") and down.available("rb_road")
    fr = st["stress_freight_up35"]
    assert all(fr.freight(m) == pytest.approx(base.freight(m) * 1.35) for m in ("sea", "air", "rail", "road"))


def test_supplier_down_without_history_picks_first(graph, sset):
    g = graph.model_copy(update={"shipments": ()})
    down = [s for s in stress_scenarios(mean_scenario(sset.full, g), g) if s.id.endswith("A_down")][0]
    assert not down.available("ra_sea") and down.available("rb_rail")  # "SA" < "SB"


def test_optimisation_set(sset):
    opt = sset.optimisation_set()
    assert sum(s.weight for s in opt) == pytest.approx(1.0)
    assert len(opt) == 24 and [s.kind for s in opt[-4:]] == ["stress"] * 4
    assert all(s.weight == pytest.approx(0.01) for s in opt[-4:])
    assert sum(s.weight for s in opt[:-4]) == pytest.approx(0.96)
    assert len(sset.stress) == 4


def test_out_of_sample(graph, fx, rules, sset):
    oos = out_of_sample(graph, fx, rules, cfg())
    assert len(oos) == 400 and oos != sset.full and oos == out_of_sample(graph, fx, rules, cfg())
    assert all(s.kind == "sample" and s.weight == pytest.approx(1 / 400) for s in oos)


def test_speed_n1000_k40(graph, fx, rules):
    t = time.perf_counter()
    s = generate_scenarios(graph, fx, rules, ScenarioConfig())
    assert time.perf_counter() - t < 5.0
    assert len(s.full) == 1000 and len(s.reduced) == 40
