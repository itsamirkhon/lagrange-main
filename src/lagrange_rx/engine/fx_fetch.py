"""Pure logic for downloading monthly CBU (Central Bank of Uzbekistan) FX rates.

The network is injected: an *opener* is any callable ``opener(url) -> bytes``.
`urllib_opener` is the real one; tests pass a fake.
"""
from __future__ import annotations

import datetime as dt
import json
import time
import urllib.request
from collections.abc import Callable
from pathlib import Path

CBU_URL = "https://cbu.uz/ru/arkhiv-kursov-valyut/json/{ccy}/{date}/"
Opener = Callable[[str], bytes]
Row = tuple[str, str, float]  # (month, currency, UZS per 1 unit)


def urllib_opener(timeout: float = 30.0) -> Opener:
    def _open(url: str) -> bytes:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310 (fixed https host)
            return resp.read()

    return _open


def parse_rate(payload: str | bytes) -> float:
    """UZS per 1 unit from the CBU JSON list: Rate / Nominal."""
    try:
        item = json.loads(payload)[0]
        rate = float(str(item["Rate"]).replace(" ", "").replace(",", "."))
        nominal = float(str(item["Nominal"]).replace(" ", "").replace(",", "."))
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise ValueError(f"unparseable CBU response: {exc!r}") from exc
    if rate <= 0 or nominal <= 0:
        raise ValueError("CBU rate and nominal must be positive")
    return rate / nominal


def month_starts(year: int, month: int, today: dt.date | None = None) -> list[str]:
    """ISO first-of-month dates from (year, month) up to the month of `today`."""
    end = today or dt.date.today()
    out, y, m = [], year, month
    while (y, m) <= (end.year, end.month):
        out.append(f"{y:04d}-{m:02d}-01")
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def fetch_rate(ccy: str, date: str, opener: Opener, retries: int = 3,
               sleep: Callable[[float], None] = time.sleep) -> float:
    url = CBU_URL.format(ccy=ccy, date=date)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            return parse_rate(opener(url))
        except (OSError, ValueError) as exc:
            last = exc
            if attempt < retries - 1:
                sleep(2.0 ** attempt)
    raise RuntimeError(f"failed to fetch {ccy} {date} after {retries} attempts: {last}")


def build_rows(currencies: tuple[str, ...], opener: Opener, start_year: int = 2020, start_month: int = 1,
               today: dt.date | None = None, retries: int = 3,
               sleep: Callable[[float], None] = time.sleep) -> list[Row]:
    return [
        (d[:7], c, fetch_rate(c, d, opener, retries, sleep))
        for d in month_starts(start_year, start_month, today)
        for c in currencies
    ]


def write_csv(rows: list[Row], path: Path | str) -> None:
    lines = ["# source: cbu", "# CBU official rates, UZS per 1 unit, first day of month", "month,currency,rate"]
    lines += [f"{m},{c},{r:.4f}" for m, c, r in rows]
    Path(path).write_text("\n".join(lines) + "\n")
