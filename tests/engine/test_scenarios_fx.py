import importlib.util
import json
from pathlib import Path

import pytest

from lagrange_rx.engine.fx_fetch import build_rows, fetch_rate, month_starts, parse_rate, write_csv
from lagrange_rx.engine.scenarios import default_fx_path, load_fx_history

ROOT = Path(__file__).resolve().parents[2]


def write(tmp_path, text):
    p = tmp_path / "fx.csv"
    p.write_text(text)
    return p


def csv(source="cbu", months=30, drop=None, rate=100.0):
    rows = ["month,currency,rate"]
    for i in range(months):
        m = f"{2020 + i // 12}-{i % 12 + 1:02d}"
        for c in ("USD", "EUR"):
            if (c, i) != drop:
                rows.append(f"{m},{c},{rate + i}")
    return f"# source: {source}\n# comment\n" + "\n".join(rows) + "\n"


def test_load_ok(tmp_path):
    h = load_fx_history(write(tmp_path, csv()))
    assert h.source == "cbu" and len(h.months) == 30 and h.rates["USD"][0] == 100.0
    assert h.months[0] == "2020-01" and set(h.rates) == {"USD", "EUR"}


@pytest.mark.parametrize("text,msg", [
    (csv(months=12), "18"),
    (csv(rate=-1.0), "positive"),
    (csv(drop=("EUR", 5)), "aligned"),
    (csv(source="bogus"), "source"),
    ("month,currency,rate\n2020-01,USD,1\n", "source"),
])
def test_load_errors(tmp_path, text, msg):
    with pytest.raises(ValueError, match=msg):
        load_fx_history(write(tmp_path, text))


def test_load_missing(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_fx_history(tmp_path / "nope.csv")


def test_default_is_real_cbu_not_synthetic():
    h = load_fx_history()
    assert h.source == "cbu" and h.source_label == "cbu.uz official"
    assert len(h.months) >= 18 and set(h.rates) == {"USD", "EUR", "RUB"}
    assert (ROOT / "data" / "cbu_fx_monthly.csv").read_bytes() == (ROOT / "sim" / "inputs" / "fx_cbu.csv").read_bytes()
    assert default_fx_path().name == "cbu_fx_monthly.csv"


def test_default_never_falls_back_to_synthetic(monkeypatch, tmp_path):
    import lagrange_rx.engine.scenarios as sc
    monkeypatch.setattr(sc, "DATA_DIR", tmp_path)  # real file absent -> error, not the synthetic sample
    (tmp_path / "fx_sample_SYNTHETIC.csv").write_text((ROOT / "data" / "fx_sample_SYNTHETIC.csv").read_text())
    with pytest.raises(FileNotFoundError):
        sc.load_fx_history()


def test_scenarios_use_real_cbu_by_default(rules):
    from lagrange_rx.engine.scenarios import ScenarioConfig, generate_scenarios
    from lagrange_rx.fixtures.l0 import build_l0_calculation_graph
    s = generate_scenarios(build_l0_calculation_graph(), load_fx_history(), rules, ScenarioConfig(n=30, k=5))
    assert s.fx_source == "cbu"


def test_official_export_is_aggregated_to_first_observation_per_month(tmp_path):
    rows = ["date,currency,rate_uzs,source"]
    for i in range(20):
        m = f"{2025 + i // 12}-{i % 12 + 1:02d}"
        rows += [f"{m}-20,USD,{200 + i},https://cbu.uz/x", f"{m}-05,USD,{100 + i},https://cbu.uz/x"]
    h = load_fx_history(write(tmp_path, "\n".join(rows) + "\n"))
    assert h.source == "cbu" and len(h.months) == 20 and h.rates["USD"][:2] == (100.0, 101.0)
    with pytest.raises(ValueError, match="cbu.uz"):
        load_fx_history(write(tmp_path, "\n".join(rows[:-1] + ["2026-09-01,USD,5,https://example.com"]) + "\n"))


def test_parse_rate():
    assert parse_rate(json.dumps([{"Rate": "12 650.5", "Nominal": "1"}])) == 12650.5
    assert parse_rate(json.dumps([{"Rate": "100,0", "Nominal": "10"}])) == 10.0
    for bad in ("[]", "{}", json.dumps([{"Rate": "x", "Nominal": "1"}])):
        with pytest.raises(ValueError):
            parse_rate(bad)


def test_month_starts():
    assert month_starts(2020, 11, today=__import__("datetime").date(2021, 2, 17)) == [
        "2020-11-01", "2020-12-01", "2021-01-01", "2021-02-01"]


def test_fetch_retries():
    calls = []

    def opener(url):
        calls.append(url)
        if len(calls) < 3:
            raise OSError("boom")
        return b'[{"Rate":"5","Nominal":"1"}]'

    assert fetch_rate("RUB", "2020-01-01", opener, retries=3, sleep=lambda s: None) == 5.0
    assert calls[0] == "https://cbu.uz/ru/arkhiv-kursov-valyut/json/RUB/2020-01-01/"
    with pytest.raises(RuntimeError):
        fetch_rate("RUB", "2020-01-01", lambda u: (_ for _ in ()).throw(OSError("x")), retries=2, sleep=lambda s: None)


def test_build_and_write_roundtrip(tmp_path):
    def opener(url):
        ccy, date = url.rstrip("/").split("/")[-2:]
        return json.dumps([{"Rate": str(100 + int(date[5:7]) + len(ccy)), "Nominal": "1"}]).encode()

    import datetime
    rows = build_rows(("USD", "EUR"), opener, 2020, 1, today=datetime.date(2022, 6, 3), sleep=lambda s: None)
    assert len(rows) == 2 * 30
    p = tmp_path / "o.csv"
    write_csv(rows, p)
    assert p.read_text().startswith("# source: cbu\n")
    h = load_fx_history(p)
    assert h.source == "cbu" and len(h.months) == 30


def test_synthetic_script_deterministic_and_committed():
    spec = importlib.util.spec_from_file_location("mk", ROOT / "scripts" / "make_synthetic_fx.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    text = mod.render()
    assert text == mod.render()
    assert "# source: synthetic-sample" in text and "# SYNTHETIC DATA - NOT REAL CBU RATES" in text
    assert (ROOT / "data" / "fx_sample_SYNTHETIC.csv").read_text() == text
    h = load_fx_history(ROOT / "data" / "fx_sample_SYNTHETIC.csv")
    assert h.months[0] == "2020-01" and h.months[-1] == "2026-10" and len(h.months) == 82
    assert 9000 < h.rates["USD"][0] < 12000


def test_uzs_needs_no_history_and_never_moves():
    """The national currency is its own unit: zero returns, no 'missing history' error (registry Rn stated in UZS)."""
    h = load_fx_history()
    r = h.log_returns(["USD", "UZS"])
    assert r.shape[1] == 2 and (r[:, 1] == 0.0).all() and (r[:, 0] != 0.0).any()
