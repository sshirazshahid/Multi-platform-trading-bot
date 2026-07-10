"""Dashboard truth + LIVE/PAPER scoping regressions (2026-06-11).

User report: "Live dashboard isn't showing correct data ... make sure it
shows correct values when switching between LIVE/PAPER."

Root causes found and fixed (4-agent audit, adversarially verified):

1. tests/test_partial_tp_accounting.py constructed a real VirtualWallet()
   with NO file isolation — every repo-root pytest run clobbered the
   production data/virtual_wallet.json with start=1000, and the next bot
   restart silently re-seeded all paper balances to 5000/exchange (fired
   4x Jun 10-11), erasing accumulated paper losses. Worse, the 8 positions
   open across the final re-seed credited +913.52 USDT of unmatched margin
   at close (losing trades RAISED the balance).

2. Dashboard scoping gaps: the EXCHANGE BREAKDOWN 'bal:' column printed
   REAL account balances inside a PAPER dashboard; the warehouse panels
   (PER-SYMBOL EDGE / LOSS-CLUSTER / SLIPPAGE) mixed PAPER and
   CONTROLLED_LIVE rows; performance bucketing used LOCAL dates while the
   risk manager uses UTC (made "Today 117" vs "Trades Today: 191" look
   contradictory); partial-TP profits were excluded from every PnL sum;
   "All Time (since X)" was actually a 500-row ring buffer.

These tests pin the fixes. Dashboard pins are source-level (house style —
dashboard.py is not imported by tests); wallet fixes are behavioral.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

DASH = Path("dashboard.py")


# ─── VirtualWallet: production-file protection ────────────────────────


def test_partial_tp_test_is_isolated():
    """The offending test must keep its tmp_path/chdir isolation — without
    it, every pytest run rewrites the production paper wallet."""
    src = Path("tests/test_partial_tp_accounting.py").read_text(encoding="utf-8")
    assert "monkeypatch.chdir(tmp_path)" in src, (
        "test_partial_tp_accounting must isolate VirtualWallet's _save "
        "(it clobbered data/virtual_wallet.json with start=1000, causing "
        "the bot to re-seed paper balances on every restart)")


def test_save_refuses_production_wallet_under_pytest():
    """Inside a pytest run, _save targeting the REAL data/virtual_wallet.json
    must be a no-op. (PYTEST_CURRENT_TEST is set by pytest itself.)

    Sentinel-based check, NOT a byte diff: the live bot may legitimately
    save the file between our reads, but our sentinel start value can only
    appear if the guard failed and OUR _save landed."""
    from core.virtual_wallet import WALLET_FILE, VirtualWallet
    prod = Path("data/virtual_wallet.json")
    sentinel = 123456.789
    w = VirtualWallet.__new__(VirtualWallet)  # skip _load
    w._enabled = True
    w._start = sentinel
    w._balances = {"binance": sentinel}
    w._lock = threading.RLock()
    # cwd is the repo root under pytest, so WALLET_FILE resolves to prod
    assert WALLET_FILE.resolve() == (Path.cwd() / "data" / "virtual_wallet.json").resolve()
    w._save()
    if prod.exists():
        data = json.loads(prod.read_text(encoding="utf-8"))
        assert data.get("start") != sentinel, (
            "_save wrote the production wallet file from inside pytest — "
            "the guard in VirtualWallet._save is broken")


def test_save_still_writes_isolated_file(tmp_path, monkeypatch):
    """The guard must NOT break properly-isolated tests: a chdir'd wallet
    resolves elsewhere and must still persist."""
    from core.virtual_wallet import VirtualWallet
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    w = VirtualWallet()
    w._enabled = True
    w._start = 1000.0
    w._balances = {"binance": 1000.0}
    w._save()
    assert (tmp_path / "data" / "virtual_wallet.json").exists()


def test_reset_redebits_open_paper_margin(tmp_path, monkeypatch):
    """A start-balance re-seed with OPEN paper positions must re-debit their
    margin (+ est. entry fee) so the eventual on_close credits are matched.
    Without this, the Jun-11 re-seed booked +913.52 of phantom margin."""
    from core.virtual_wallet import VirtualWallet
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    # open paper futures position: notional 1000, lev 2 -> margin 500
    (tmp_path / "data" / "positions.json").write_text(json.dumps({
        "open": [{
            "exchange": "Binance", "symbol": "BTC/USDT:USDT", "side": "buy",
            "size": 100.0, "entry_price": 10.0, "leverage": 2,
            "market_type": "futures", "paper_trade": True,
        }],
        "closed": [],
    }), encoding="utf-8")
    # wallet file with a DIFFERENT start -> triggers the reset path in _load
    (tmp_path / "data" / "virtual_wallet.json").write_text(json.dumps({
        "start": 1.0, "balances": {"binance": 1.0},
    }), encoding="utf-8")
    w = VirtualWallet()
    if not w._enabled:  # LIVE-mode env: wallet is a no-op, nothing to assert
        return
    start = w.start_balance
    bal = w.balance("binance")
    assert bal < start - 499.0, (
        f"reset must re-debit the open position's ~500 margin: "
        f"start={start}, balance={bal}")
    # and the eventual close credits margin+pnl back, landing near start+pnl
    w.on_close("binance", "BTC/USDT:USDT", "buy", 100.0, 11.0, 10.0,
               0.55, (11.0 - 10.0) * 100.0, "futures", 2)
    assert abs(w.balance("binance") - (start + 100.0)) < 2.0, (
        "after re-debit, close should land near start + gross_pnl (fees ~1)")


def test_reset_without_open_positions_is_clean(tmp_path, monkeypatch):
    """No open paper positions -> reset behaves exactly as before."""
    from core.virtual_wallet import VirtualWallet
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    (tmp_path / "data" / "virtual_wallet.json").write_text(json.dumps({
        "start": 1.0, "balances": {"binance": 1.0},
    }), encoding="utf-8")
    w = VirtualWallet()
    if not w._enabled:
        return
    assert w.balance("binance") == w.start_balance


# ─── Dashboard: LIVE/PAPER scoping pins (source-level) ────────────────


def test_breakdown_bal_column_is_mode_scoped():
    """PAPER dashboards must show the paper wallet balance, never the
    user's real exchange balance (the 220.73/0.01/78.25 leak)."""
    src = DASH.read_text(encoding="utf-8")
    assert "wallet_bal.get(ex_name) if dry_run else live_bals.get(ex_name)" in src


def test_warehouse_queries_are_mode_filtered():
    """All four load_warehouse_stats queries must scope to the current
    operating mode — the trades table mixes PAPER and CONTROLLED_LIVE."""
    src = DASH.read_text(encoding="utf-8")
    i = src.index("def load_warehouse_stats")
    # 4500->6000 (2026-07-10): the current-boot cohort block grew the function
    # head; it adds a 5th mode-filtered query, so the floor rises to 5.
    block = src[i:i + 6000]
    assert block.count("AND mode = ?") >= 5, (
        "per_symbol / current-boot / per_family / slippage / header-count "
        "queries must all filter by mode")
    assert '"PAPER" if _dr else "CONTROLLED_LIVE"' in block


def test_whole_trade_pnl_helper_used_everywhere():
    """Partial-TP legs (realized_partial_pnl) must be included in every PnL
    sum — the runner-only `pnl` field under-reported profit by +22.49 over
    the 500-row window at audit time."""
    src = DASH.read_text(encoding="utf-8")
    assert "def _whole_pnl(" in src
    assert "realized_partial_pnl" in src
    # the aggregators all route through the helper
    for fn in ("def calc_stats", "def calc_exchange_stats",
               "def calc_strategy_stats", "def calc_hourly_heatmap",
               "def calc_daily_pnl", "def _bucket_stats"):
        i = src.index(fn)
        assert "_whole_pnl(t)" in src[i:i + 2500], f"{fn} must use _whole_pnl"


def test_day_buckets_are_utc():
    """calc_stats and calc_daily_pnl must bucket by UTC date — the engine
    (risk manager, hour gates, heatmap) lives on UTC days; local-date
    bucketing made panels disagree (117 local closes vs 191 UTC opens)."""
    src = DASH.read_text(encoding="utf-8")
    for fn in ("def calc_stats", "def calc_daily_pnl"):
        i = src.index(fn)
        block = src[i:i + 2500]
        assert "datetime.now(timezone.utc).date()" in block, \
            f"{fn} day anchor must be UTC"
        assert "fromtimestamp(ct, tz=timezone.utc)" in block, \
            f"{fn} bucketing must be UTC"


def test_ring_buffer_label_is_honest():
    """positions.json closed history is capped at 500 rows — the panel must
    say 'Last 500 trades', not 'All Time', once the cap is hit."""
    src = DASH.read_text(encoding="utf-8")
    assert '"Last 500 trades" if s["total_n"] >= 500 else "All Time"' in src


def test_risk_panel_counter_labeled_as_opens():
    """risk_state's trades_today counts OPENS since UTC midnight — label it
    so it isn't compared 1:1 against the closed-trade count."""
    src = DASH.read_text(encoding="utf-8")
    assert "Opens Today (UTC):" in src


def test_equity_curve_filters_and_whole_pnl():
    src = DASH.read_text(encoding="utf-8")
    assert "sorted(_filter_real_trades(closed)" in src
    assert "pnl_series = [_whole_pnl(t) for t in sorted_closed[-50:]]" in src


def test_regime_panel_title_names_its_timeframe():
    """'BTC 4h: BEAR' (gates) vs 'TREND UP' (regime) read as a contradiction
    until the regime panel admits it is a 1h classifier."""
    src = DASH.read_text(encoding="utf-8")
    assert 'box_top("MARKET REGIME  (1h' in src


def test_balances_panel_shows_trade_history_truth():
    """PAPER balances panel must surface warehouse whole-trade net alongside
    the sim-wallet ROI (the wallet measures 'since last seed', not results)."""
    src = DASH.read_text(encoding="utf-8")
    assert "sim wallet vs seed" in src
    assert "Trade PnL ({}, all history" in src


def test_whole_pnl_contract_on_synthetic_rows():
    """Replicate the helper contract (dashboard.py is not imported)."""
    def whole(t):
        return (t.get("pnl", 0) or 0) + (t.get("realized_partial_pnl") or 0)
    assert abs(whole({"pnl": -0.5, "realized_partial_pnl": 0.8}) - 0.3) < 1e-9
    assert whole({"pnl": 1.0}) == 1.0
    assert whole({"pnl": None, "realized_partial_pnl": None}) == 0
