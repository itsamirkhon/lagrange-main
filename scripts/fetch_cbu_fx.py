"""Download monthly USD/EUR/RUB -> UZS rates from the official CBU API.

Usage:
    python scripts/fetch_cbu_fx.py [--out data/cbu_fx_monthly.csv] [--start 2020-01]

Takes the rate on the first day of each month from 2020-01 to the current month
(https://cbu.uz/ru/arkhiv-kursov-valyut/json/{CCY}/{YYYY-MM-DD}/) and writes a CSV with
header `# source: cbu`. `data/cbu_fx_monthly.csv` is the committed official history (a copy of the simulation's
CBU export, 2025-01..2026-10); this script can regenerate a longer one (needs network). `load_fx_history()` reads
that file by default and never falls back to the synthetic sample.
"""
from __future__ import annotations

import argparse
from pathlib import Path

from lagrange_rx.engine.fx_fetch import build_rows, urllib_opener, write_csv

ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--out", default=str(ROOT / "data" / "cbu_fx_monthly.csv"))
    ap.add_argument("--start", default="2020-01", help="first month, YYYY-MM")
    ap.add_argument("--currencies", nargs="+", default=["USD", "EUR", "RUB"])
    a = ap.parse_args(argv)
    year, month = (int(x) for x in a.start.split("-"))
    rows = build_rows(tuple(a.currencies), urllib_opener(), year, month)
    write_csv(rows, a.out)
    print(f"wrote {len(rows)} rows to {a.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
