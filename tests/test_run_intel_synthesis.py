"""Offline unit tests for scripts/run_intel_synthesis.py.

No network/LLM: only the pure assembly/parse helpers and the quarantine
invariant (the synthesis layer must never import the order path) are tested.
The live fetches and the Claude call are exercised by running the script.
"""

import csv
import importlib.util
from pathlib import Path

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "run_intel_synthesis.py"
_spec = importlib.util.spec_from_file_location("_run_intel_synthesis", _SCRIPT)
mod = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(mod)


def test_no_order_path_imports():
    """Gate-guard: advisory layer must not import anything from the order path."""
    import_lines = [
        ln
        for ln in _SCRIPT.read_text(encoding="utf-8").splitlines()
        if ln.strip().startswith(("import ", "from "))
    ]
    joined = "\n".join(import_lines)
    for forbidden in ("order_manager", "risk_manager", "live_gate", "bot_engine", "exchanges"):
        assert forbidden not in joined, f"intel synthesis must not import {forbidden}"


def test_mvrv_context_zone_and_percentile():
    series = {"2020-01-01": -0.5, "2021-01-01": 7.0, "2026-06-28": 3.0}
    ctx = mod.mvrv_context(series)
    assert ctx["date"] == "2026-06-28"
    assert ctx["z"] == 3.0
    assert ctx["zone"] == "mid / fair valuation"
    assert round(ctx["pctl"]) == 67  # 2 of 3 values <= 3.0


def test_mvrv_context_empty_is_none():
    assert mod.mvrv_context({}) is None


def test_build_facts_md_includes_all_sources():
    intel = {
        "fng_value": 55,
        "fng_class": "Greed",
        "btc_dom": 54.3,
        "eth_dom": 12.1,
        "breadth_green": 120,
        "breadth_total": 300,
        "top_movers": ["BTC", "ETH"],
        "pos_fund": ["SOL"],
        "neg_fund": ["XRP"],
    }
    mvrv = {"date": "2026-06-28", "z": 3.0, "pctl": 67.0, "zone": "mid / fair valuation"}
    derivs = {"BTC": {"lsr": 1.2, "taker_ls": 0.9, "oi_chg_pct": 1.5, "funding": 0.0001}}
    fundoi = {"BTC": {"funding": 0.0001, "oi_chg_24h_pct": 3.2}}
    md = mod.build_facts_md(intel, mvrv, derivs, fundoi)
    assert "Fear & Greed: 55" in md
    assert "BTC dominance: 54.3%" in md
    assert "MVRV-Z = 3.00" in md
    assert "long/short ratio=1.2" in md
    assert "OI 24h chg=+3.2%" in md


def test_build_facts_md_empty():
    assert "No facts" in mod.build_facts_md(None, None, None, None)


def test_funding_oi_summary_reads_csvs(tmp_path):
    fpath = tmp_path / "BTC_funding.csv"
    with fpath.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "datetime", "funding_rate"])
        w.writerow([1, "d1", "0.0001"])
    opath = tmp_path / "BTC_oi.csv"
    with opath.open("w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["timestamp", "datetime", "open_interest_base", "open_interest_usd"])
        for i in range(7):
            w.writerow([i, f"d{i}", i, 100 + i * 10])  # 100..160
    out = mod.funding_oi_summary(["BTC", "ETH"], directory=tmp_path)
    assert "ETH" not in out  # no files -> skipped
    assert out["BTC"]["funding"] == 0.0001
    assert round(out["BTC"]["oi_chg_24h_pct"]) == 60  # 160/100 - 1


def test_latest_market_intel_skips_corrupt_last_line(tmp_path):
    p = tmp_path / "h.jsonl"
    p.write_text('{"fng_value": 40}\n{ not valid json\n', encoding="utf-8")
    assert mod.latest_market_intel(p) == {"fng_value": 40}


def test_latest_market_intel_absent_is_none(tmp_path):
    assert mod.latest_market_intel(tmp_path / "nope.jsonl") is None


def test_synthesize_no_llm_returns_facts_unused():
    note, used = mod.synthesize("### facts\n- x", "2026-06-29 00:00 UTC", no_llm=True)
    assert used is False
    assert "facts" in note
