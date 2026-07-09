"""Item 1 (data-integrity backlog 2026-07-07): shadow resolver aging + budget + funding.

Covers three gaps flagged in tasks/todo.md "Open backlog":
  1a. No UNRESOLVABLE aging — a delisted/vanished symbol whose candles never
      return was retried on EVERY run forever. Rows aged past the horizon are
      now marked terminal (UNRESOLVABLE) and stop being refetched.
  1b. Widening refetches bypassed the max_group_fetches budget — one symbol
      could starve the rest. The widening path now counts against the budget.
  1c. funding=0 for 8h+ probe horizons is an optimistic bound. A resolved probe
      row carrying a realized funding figure now folds it into the resolved net;
      rows without one keep 0 (documented, never fabricated).
"""
from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

HOUR_MS = 3_600_000


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_aging", ROOT / "core" / "warehouse.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_aging"] = mod
    spec.loader.exec_module(mod)
    return mod


def _load_runner():
    spec = importlib.util.spec_from_file_location(
        "resolve_shadow_outcomes_af", ROOT / "scripts" / "resolve_shadow_outcomes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _insert_pending(wh, *, proposal_id, ts, symbol="GONE/USDT:USDT", side="buy",
                    entry_px=100.0, sl_px=95.0, tp_px=110.0, venue="binance",
                    timeframe="1h", horizon_bars=5):
    wh._conn().execute(
        "INSERT INTO shadow_decisions (ts, symbol, side, decision, entry_px, "
        "sl_px, tp_px, venue, timeframe, horizon_bars, proposal_id, label_status) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?, 'PENDING')",
        (ts, symbol, side, "ALLOW", entry_px, sl_px, tp_px, venue, timeframe,
         horizon_bars, proposal_id),
    )
    wh._conn().commit()


def _status(wh, pid):
    return wh.query(
        "SELECT label_status FROM shadow_decisions WHERE proposal_id=?", (pid,)
    )[0]["label_status"]


# ── 1a: UNRESOLVABLE aging ──────────────────────────────────────────────────
def test_aged_out_delisted_row_marked_unresolvable(wh):
    from core.shadow_resolver import resolve_pending
    now = int(time.time())
    # entry way in the past: 5 * 1h horizon; 2x-horizon slack elapsed long ago.
    _insert_pending(wh, proposal_id="old-1", ts=now - 100 * 3600, horizon_bars=5)
    # symbol vanished -> candle source returns nothing, forever.
    summary = resolve_pending(wh, fetch_candles=lambda r: [], now=now)
    assert _status(wh, "old-1") == "UNRESOLVABLE"
    assert summary["unresolvable"] == 1
    # and it is no longer selected on the next run (PENDING-only SELECT)
    summary2 = resolve_pending(wh, fetch_candles=lambda r: [], now=now)
    assert summary2["n_pending"] == 0


def test_recent_row_with_no_candles_stays_pending(wh):
    from core.shadow_resolver import resolve_pending
    now = int(time.time())
    _insert_pending(wh, proposal_id="fresh-1", ts=now - 3600, horizon_bars=5)
    summary = resolve_pending(wh, fetch_candles=lambda r: [], now=now)
    assert _status(wh, "fresh-1") == "PENDING"
    assert summary["unresolvable"] == 0
    assert summary["skipped"] == 1


def test_budget_deferred_aged_row_not_aged_out(wh):
    """A row past its horizon that returned [] only because the per-run fetch
    budget was spent must NOT be terminated — it had no fetch attempt."""
    from core.shadow_resolver import resolve_pending
    now = int(time.time())
    _insert_pending(wh, proposal_id="deferred-1", ts=now - 100 * 3600, horizon_bars=5)

    def budget_spent(row):
        return []
    budget_spent.budget_deferred = True

    summary = resolve_pending(wh, fetch_candles=budget_spent, now=now)
    assert _status(wh, "deferred-1") == "PENDING"
    assert summary["unresolvable"] == 0


def test_aged_out_censored_row_marked_unresolvable(wh):
    """Candles come back but never fill the horizon window (data gap); once aged
    past 2x the horizon the missing bars will never arrive -> terminal."""
    from core.shadow_resolver import resolve_pending
    now = int(time.time())
    entry = now - 100 * 3600
    _insert_pending(wh, proposal_id="gap-1", ts=entry, horizon_bars=5)
    # only 2 quiet closed bars ever exist (far short of the 5-bar horizon)
    entry_ms = entry * 1000
    candles = [[entry_ms + i * HOUR_MS, 100, 101, 99, 100, 1.0] for i in (1, 2)]
    summary = resolve_pending(wh, fetch_candles=lambda r: candles, now=now)
    assert _status(wh, "gap-1") == "UNRESOLVABLE"
    assert summary["unresolvable"] == 1


# ── 1b: widening refetch counts against the budget ──────────────────────────
def test_widening_refetch_respects_budget():
    mod = _load_runner()
    late_sec = 1_000_000_000
    early_sec = late_sec - 7200
    now_ms = (late_sec + 100 * 3600) * 1000

    calls = []

    def counting_fetch(venue, symbol, tf, since_ms):
        calls.append(since_ms)
        base = since_ms - (since_ms % HOUR_MS)
        return [[base + i * HOUR_MS, 100, 101, 99, 100, 1.0] for i in range(12)]

    # budget of exactly 1 new/widening fetch per run
    fc = mod.build_fetch_candles(counting_fetch, now_ms=now_ms, max_group_fetches=1)
    first = fc(_runner_row(late_sec))          # consumes the single fetch
    assert first
    second = fc(_runner_row(early_sec))        # SAME group, earlier entry -> widen
    assert second == [], "widening refetch must be blocked once budget is spent"
    assert fc.budget_deferred is True
    assert len(calls) == 1, "widening must count against max_group_fetches"


def _runner_row(ts_sec, venue="binance", symbol="BTC/USDT:USDT", tf="1h", horizon=5):
    return {"ts": ts_sec, "venue": venue, "symbol": symbol,
            "timeframe": tf, "horizon_bars": horizon}


# ── 1c: realized funding folded into the resolved net ───────────────────────
def test_resolve_one_short_receives_positive_funding():
    from core.shadow_resolver import resolve_one
    row = {"side": "sell", "entry_px": 100.0, "sl_px": 105.0, "tp_px": 90.0,
           "horizon_bars": 5, "projected_notional_current": 200.0, "timeframe": "1h"}
    candles = [[0, 100, 101, 88, 90, 1.0]]  # short TP hit
    base = resolve_one(row, candles)
    withf = resolve_one(row, candles, funding_rate_sum=0.001)  # +funding to a short
    assert withf["funding"] > 0
    assert withf["net_pnl"] > base["net_pnl"]
    assert withf["funding"] == pytest.approx(0.001 * 200.0)


def test_resolve_one_long_pays_positive_funding():
    from core.shadow_resolver import resolve_one
    row = {"side": "buy", "entry_px": 100.0, "sl_px": 95.0, "tp_px": 110.0,
           "horizon_bars": 5, "projected_notional_current": 200.0, "timeframe": "1h"}
    candles = [[0, 100, 112, 100, 111, 1.0]]  # long TP hit
    base = resolve_one(row, candles)
    withf = resolve_one(row, candles, funding_rate_sum=0.001)  # long pays +funding
    assert withf["funding"] < 0
    assert withf["net_pnl"] < base["net_pnl"]


def test_resolve_pending_wires_funding_provider(wh):
    from core.shadow_resolver import resolve_pending
    now = int(time.time())
    entry = now - 100 * 3600
    _insert_pending(wh, proposal_id="fund-1", ts=entry, side="sell",
                    entry_px=100.0, sl_px=105.0, tp_px=90.0, horizon_bars=5)
    entry_ms = entry * 1000
    candles = [[entry_ms + HOUR_MS, 100, 101, 88, 90, 1.0]]  # short TP hit
    resolve_pending(
        wh, fetch_candles=lambda r: candles, now=now,
        funding_rate_provider=lambda r: 0.002 if r["proposal_id"] == "fund-1" else 0.0,
    )
    out = wh.query("SELECT funding, net_pnl FROM shadow_outcomes WHERE proposal_id=?",
                   ("fund-1",))[0]
    assert out["funding"] == pytest.approx(0.002 * 200.0)  # notional falls back to $200 ref
    assert _status(wh, "fund-1") == "RESOLVED"


def test_listing_funding_provider_reads_probe_row(wh):
    """The runner's provider sources realized_funding_rate_sum from the listing
    probe's shadow_listing_probe table; a row without a probe record gets 0."""
    mod = _load_runner()
    wh._conn().executescript(
        """CREATE TABLE IF NOT EXISTS shadow_listing_probe (
            proposal_id TEXT PRIMARY KEY, decision TEXT,
            realized_funding_rate_sum REAL);"""
    )
    wh._conn().execute(
        "INSERT INTO shadow_listing_probe (proposal_id, decision, "
        "realized_funding_rate_sum) VALUES ('ls-1', 'ENTER', 0.0042)")
    wh._conn().commit()
    provider = mod._build_listing_funding_provider(wh)
    assert provider({"proposal_id": "ls-1"}) == pytest.approx(0.0042)
    assert provider({"proposal_id": "missing"}) == 0.0
    assert provider({}) == 0.0
