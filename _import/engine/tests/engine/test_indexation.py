import pytest

from lagrange_rx.engine.indexation import annual_ceiling_factor, ceiling_factors, indexation_decision
from lagrange_rx.rules import load_rules

R = load_rules()


@pytest.mark.parametrize(
    "g,on_date,expected",
    [
        (0.029, True, "none"), (0.030, True, "scheduled"), (0.030, False, "none"),
        (0.050, True, "scheduled"), (0.050, False, "none"), (0.051, False, "immediate"),
        (0.051, True, "immediate"), (-0.10, True, "none"),
    ],
)
def test_thresholds(g, on_date, expected):
    assert indexation_decision(g, on_date, R) == expected


def path(g):  # constant step at horizon start 2026-11 (offset 2 = January)
    return tuple(100.0 * (1 + g) for _ in range(12))


@pytest.mark.parametrize("g,m_idx,jump", [(0.029, 2, 1.0), (0.030, 2, 1.030), (0.050, 2, 1.050)])
def test_scheduled_indexation_waits_for_january(g, m_idx, jump):
    f = ceiling_factors(100.0, path(g), None, 0.0, "2026-11", R)
    assert f[0] == f[1] == 1.0
    assert f[m_idx] == pytest.approx(jump)


def test_immediate_indexation_has_lag():
    f = ceiling_factors(100.0, path(0.051), None, 0.0, "2026-11", R)
    assert f[0] == 1.0 and f[1] == pytest.approx(1.051)


def test_cut_applies_from_month():
    f = ceiling_factors(100.0, tuple([100.0] * 12), 7, 0.37, "2026-11", R)
    assert f[5] == 1.0 and f[6] == pytest.approx(0.63)
    assert annual_ceiling_factor(100.0, tuple([100.0] * 12), 7, 0.37, "2026-11", R) == pytest.approx((6 + 6 * 0.63) / 12)


def test_bad_path_length():
    with pytest.raises(ValueError):
        ceiling_factors(100.0, (100.0,), None, 0.0, "2026-11", R)
