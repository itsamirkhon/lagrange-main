"""Fetch official historical FX rates from Central Bank of Uzbekistan (cbu.uz).

Usage:
    python3 -m sim.tools.fetch_cbu_fx [--out sim/inputs/fx_cbu.csv]
"""

from __future__ import annotations

import argparse
import concurrent.futures
import csv
import datetime as dt
import json
import urllib.request
from pathlib import Path

CURRENCIES: tuple[str, ...] = ("USD", "EUR", "RUB")
BASE_URL = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/all/{date_str}/"
DEFAULT_OUT = Path(__file__).resolve().parent.parent / "inputs" / "fx_cbu.csv"


def build_target_dates(start: dt.date = dt.date(2025, 1, 1), end: dt.date = dt.date(2026, 10, 31)) -> list[dt.date]:
    """Build deterministic schedule covering month starts/ends, weekly samples, and key dates."""
    dates: set[dt.date] = {
        dt.date(2025, 1, 1),
        dt.date(2025, 1, 23),
        dt.date(2025, 1, 31),
        dt.date(2025, 12, 30),
        start,
        end,
    }
    cur = start
    while cur <= end:
        dates.add(cur)
        cur += dt.timedelta(days=7)

    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        first_day = dt.date(year, month, 1)
        next_m = dt.date(year + (month // 12), (month % 12) + 1, 1)
        last_day = next_m - dt.timedelta(days=1)
        if start <= first_day <= end:
            dates.add(first_day)
        if start <= last_day <= end:
            dates.add(last_day)
        year, month = next_m.year, next_m.month
    return sorted(dates)


def _parse_cbu_payload(req_date: dt.date, raw_items: list[dict]) -> list[dict[str, str | float]]:
    """Extract USD/EUR/RUB rows and honest provenance source from CBU JSON response."""
    rows: list[dict[str, str | float]] = []
    by_ccy = {str(item.get("Ccy", "")): item for item in raw_items}
    for ccy in CURRENCIES:
        item = by_ccy.get(ccy)
        if not item:
            raise ValueError(f"Currency {ccy} missing in CBU response for {req_date.isoformat()}")
        nominal = float(item.get("Nominal", 1) or 1)
        rate = round(float(item["Rate"]) / nominal, 4)
        cbu_date_str = str(item.get("Date", "")).strip()
        cbu_date = dt.datetime.strptime(cbu_date_str, "%d.%m.%Y").date() if cbu_date_str else req_date
        lag_days = (req_date - cbu_date).days
        if lag_days > 14:
            source = f"cbu.uz_latest_carried_forward_from_{cbu_date.isoformat()}"
        else:
            source = BASE_URL.format(date_str=req_date.isoformat())
        rows.append(
            {
                "date": req_date.isoformat(),
                "currency": ccy,
                "rate_uzs": rate,
                "source": source,
            }
        )
    return rows


def fetch_cbu_for_date(req_date: dt.date, timeout: float = 15.0) -> list[dict[str, str | float]]:
    """Fetch official CBU Uzbekistan rates for one calendar date."""
    url = BASE_URL.format(date_str=req_date.isoformat())
    req = urllib.request.Request(url, headers={"User-Agent": "lagrange-sim-fx-fetcher/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    if not isinstance(payload, list) or not payload:
        raise ValueError(f"Unexpected CBU payload for {req_date.isoformat()}")
    return _parse_cbu_payload(req_date, payload)


def fetch_and_write_fx_csv(out_path: Path = DEFAULT_OUT, max_workers: int = 8) -> int:
    """Fetch CBU rates for 2025-01-01..2026-10-31 and write sorted CSV."""
    dates = build_target_dates()
    all_rows: list[dict[str, str | float]] = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as pool:
        for batch in pool.map(fetch_cbu_for_date, dates):
            all_rows.extend(batch)

    all_rows.sort(key=lambda r: (str(r["date"]), str(r["currency"])))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["date", "currency", "rate_uzs", "source"])
        writer.writeheader()
        for row in all_rows:
            writer.writerow(row)
    return len(all_rows)


def main(argv: list[str] | None = None) -> int:
    """CLI entry point for fetching real CBU FX data."""
    parser = argparse.ArgumentParser(description="Fetch real FX rates from cbu.uz API.")
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args(argv)
    count = fetch_and_write_fx_csv(args.out)
    print(f"Wrote {count} real FX rows to {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
