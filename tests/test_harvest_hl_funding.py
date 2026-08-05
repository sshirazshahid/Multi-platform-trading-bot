"""T6 — HL funding harvest exit codes + dedup (autoplan 2026-07-30)."""
from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "harvest_hl_funding.py"


def _load_mod():
    spec = importlib.util.spec_from_file_location("harvest_hl_funding", SCRIPT)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture(scope="module")
def hl():
    return _load_mod()


def _payload(coins_funding: dict[str, float]):
    universe = [{"name": c} for c in coins_funding]
    ctxs = [
        {"funding": str(v), "markPx": "1", "openInterest": "2", "premium": "0"}
        for v in coins_funding.values()
    ]
    return [{"universe": universe}, ctxs]


def test_harvest_rows_filters_want_set(hl):
    rows = hl.harvest_rows(
        _payload({"BTC": 0.0001, "ETH": -0.0002, "DOGE": 0.01}),
        {"BTC", "ETH"},
        now=1000.0,
    )
    assert {r["coin"] for r in rows} == {"BTC", "ETH"}
    assert rows[0]["venue"] == "hyperliquid"


def test_harvest_rows_malformed_payload_raises(hl):
    with pytest.raises(ValueError, match="malformed"):
        hl.harvest_rows({"nope": 1}, {"BTC"}, now=1.0)


def test_main_exits_1_on_zero_matching_coins(hl, tmp_path, monkeypatch):
    monkeypatch.setattr(hl, "fetch_meta_and_ctxs", lambda: _payload({"DOGE": 0.1}))
    rc = hl.main(
        ["--coins", "BTC", "--out", str(tmp_path / "hl.jsonl"), "--dedup-sec", "0"]
    )
    assert rc == 1
    assert not (tmp_path / "hl.jsonl").exists()


def test_main_exits_1_on_fetch_failure(hl, tmp_path, monkeypatch):
    def _boom():
        raise TimeoutError("hl down")

    monkeypatch.setattr(hl, "fetch_meta_and_ctxs", _boom)
    rc = hl.main(["--coins", "BTC", "--out", str(tmp_path / "hl.jsonl")])
    assert rc == 1


def test_main_appends_and_dedups(hl, tmp_path, monkeypatch):
    out = tmp_path / "hl.jsonl"
    monkeypatch.setattr(hl, "fetch_meta_and_ctxs", lambda: _payload({"BTC": 0.0001}))
    assert hl.main(["--coins", "BTC", "--out", str(out), "--dedup-sec", "3600"]) == 0
    assert out.exists()
    assert sum(1 for _ in out.open()) == 1
    # Second fire within dedup window: success with zero new rows
    assert hl.main(["--coins", "BTC", "--out", str(out), "--dedup-sec", "3600"]) == 0
    assert sum(1 for _ in out.open()) == 1


def test_recent_coins_reads_window(hl, tmp_path):
    p = tmp_path / "hl.jsonl"
    now = time.time()
    # Append-ordered: older first, newer last (matches production writer).
    p.write_text(
        json.dumps({"ts": now - 10_000, "coin": "ETH"})
        + "\n"
        + json.dumps({"ts": now - 10, "coin": "BTC"})
        + "\n",
        encoding="utf-8",
    )
    recent = hl._recent_coins(p, now=now, dedup_sec=60)
    assert recent == {"BTC"}
