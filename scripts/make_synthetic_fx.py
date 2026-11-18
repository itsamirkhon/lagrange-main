"""Generate data/fx_sample_SYNTHETIC.csv deterministically (random walk, NOT real CBU rates).

Usage: python scripts/make_synthetic_fx.py [--out data/fx_sample_SYNTHETIC.csv]
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
SEED = 20201101
MONTHS = 82  # 2020-01 .. 2026-10
# currency: (start, monthly drift, monthly vol)
PARAMS = {"USD": (10500.0, 0.003, 0.012), "EUR": (11700.0, 0.0032, 0.016), "RUB": (140.0, 0.0005, 0.035)}
JUMP_INDEX, JUMP = 27, 0.09  # one devaluation jump in 2022-04 (all currencies, scaled by beta)
BETA = {"USD": 1.0, "EUR": 1.0, "RUB": 1.6}


def month_label(i: int) -> str:
    return f"{2020 + i // 12}-{i % 12 + 1:02d}"


def series() -> dict[str, np.ndarray]:
    rng = np.random.default_rng(SEED)
    common = rng.normal(0.0, 1.0, MONTHS - 1)
    out = {}
    for ccy, (start, drift, vol) in PARAMS.items():
        idio = rng.normal(0.0, 1.0, MONTHS - 1)
        r = drift + vol * (0.6 * common + 0.8 * idio)
        r[JUMP_INDEX - 1] += JUMP * BETA[ccy]
        out[ccy] = np.round(start * np.exp(np.concatenate([[0.0], np.cumsum(r)])), 2)
    return out


def render() -> str:
    s = series()
    lines = [
        "# source: synthetic-sample",
        "# SYNTHETIC DATA - NOT REAL CBU RATES - replace by running scripts/fetch_cbu_fx.py",
        "month,currency,rate",
    ]
    lines += [f"{month_label(i)},{c},{s[c][i]:.2f}" for i in range(MONTHS) for c in PARAMS]
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "data" / "fx_sample_SYNTHETIC.csv"))
    Path(ap.parse_args(argv).out).write_text(render())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
