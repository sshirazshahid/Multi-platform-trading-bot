"""Regression tests for the three probe-lane defects (2026-08-12 deep audit).

All three corrupt PROMOTION EVIDENCE, not capital: the shadow probes are
log-only and structurally incapable of placing an order. But the frozen
promotion gate reads shadow_decisions/shadow_outcomes, so a probe that
silently skips bars, half-writes a decision, or mis-counts its exit bar
produces a gate verdict computed on rows that never described reality.

D1 (probe_common.eval_gate) — a transient venue error returns [] from the
OHLCV provider, `len(candles) < min_bars` is trivially true, and the
(symbol, tf) boundary is marked evaluated. The bar is then NEVER evaluated:
the expected-bar guard skips it until the next boundary. One blipped REST
call = one silently dropped signal bar, with no log line. The fix marks the
boundary only when the venue actually served candles — the same convention
monitor_open_barriers already uses.

D2 (all six paired entry writes) — a probe entry is TWO writes to TWO tables
(shadow_decisions for the resolver, the probe table for occupancy), each
self-committing via insert_row. A crash/lock/IntegrityError between them
leaves an ORPHAN decision row: the resolver still replays it into
shadow_outcomes (so it counts toward the >=30-resolved gate) while the probe
table has no row holding the slot, so the probe re-enters the same signal.
The current order is the fail-UNSAFE one. The fix writes both rows in ONE
transaction.

D3 (pullback_momentum_probe_agent._monitor_open) — the condition-exit path
tightened horizon_bars with the CALENDAR delta ``(bar_ts - entry_ts) // BAR_S``
while the resolver counts POSITIONAL bars over the forward-candle list it is
handed. Any missing bar (venue gap / maintenance) makes calendar > positional,
so the tightened horizon exceeds the bars that will ever exist. resolve_one's
censoring guard (`len(scan) < horizon` -> return None) then holds the row
PENDING FOREVER — the exit is never recorded and the lane silently stalls
one row short. The fix counts the probe's own forward positional index.

Run: venv/Scripts/python.exe -m pytest tests/test_probe_lane_defects.py -v
"""

from __future__ import annotations

import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agents.bundle_mr_probe_agent import bundle_rsi_last  # noqa: E402
from core.agents.probe_common import eval_gate, write_entry_pair  # noqa: E402

H4 = 4 * 3600
START = 1_000_000_000
RSI_N = 14


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_probe_lane_defects_test", ROOT / "core" / "warehouse.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_probe_lane_defects_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _candles(start_ts_s: int, closes, bar_s: int = H4, highs=None, lows=None):
    """[ts_ms, o, h, l, c, v] candles from a close list (h/l default c +/- 1)."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        ts_ms = (start_ts_s + i * bar_s) * 1000
        h = highs[i] if highs else c + 1.0
        low = lows[i] if lows else c - 1.0
        out.append([ts_ms, prev, h, low, c, 1000.0])
        prev = c
    return out


# ══════════════════════════════════════════════════════════════════════════
# D1 — eval_gate must not consume a bar on a transient venue error
# ══════════════════════════════════════════════════════════════════════════
class _GateAgent:
    """Minimal eval_gate host: the helper only touches _wh/_ohlcv/_venue/
    _bar_seen. Deliberately has no order path (log-only shape)."""

    def __init__(self, wh, ohlcv):
        self._wh = wh
        self._ohlcv = ohlcv
        self._venue = "bybit"
        self._bar_seen: dict = {}


_GATE_SQL = (
    "SELECT signal_bar_ts, closed_hint_ts, max_hold_bars "
    "FROM shadow_pullback_probe WHERE symbol=? ORDER BY signal_bar_ts DESC LIMIT 1"
)


def _gate(agent, now, *, min_bars=10):
    return eval_gate(
        agent, symbol="BTC/USDT:USDT", tf="4h", bar_s=H4, fetch_bars=40,
        min_bars=min_bars, last_sql=_GATE_SQL, last_params=("BTC/USDT:USDT",),
        hold_col="max_hold_bars", now=now,
    )


def _pullback_schema(wh):
    """The probe owns its schema; construct it so the gate's last_sql runs."""
    import core.agents.pullback_momentum_probe_agent as m
    from core.agents.probe_common import ensure_schema

    ensure_schema(wh, m._SCHEMA)


def test_eval_gate_retries_the_bar_after_a_transient_venue_error(wh):
    """A blipped fetch ([] from the provider) must NOT consume the boundary."""
    _pullback_schema(wh)
    closes = [100.0 + i for i in range(30)]
    full = _candles(START, closes)
    now = START + len(closes) * H4
    calls = {"n": 0}

    def flaky_ohlcv(venue, symbol, tf, since_ms):
        calls["n"] += 1
        if calls["n"] == 1:
            return []  # transient venue error / empty response
        return [c for c in full if c[0] >= int(since_ms)]

    agent = _GateAgent(wh, flaky_ohlcv)

    assert _gate(agent, now) is None  # the blip yields no signal, as before
    assert agent._bar_seen == {}, "a transient error must not consume the bar"

    # SAME tick boundary, venue recovered: the bar must now be evaluated.
    got = _gate(agent, now)
    assert got is not None, "the recovered fetch must still evaluate this bar"
    candles, latest_ts = got
    assert latest_ts == START + (len(closes) - 1) * H4


def test_eval_gate_still_consumes_the_bar_on_genuinely_short_history(wh):
    """Cost control preserved: a real (non-empty) short history marks the
    boundary — it cannot lengthen within this bar, so re-fetching is waste."""
    _pullback_schema(wh)
    closes = [100.0 + i for i in range(4)]  # < min_bars, but REAL data
    full = _candles(START, closes)
    now = START + len(closes) * H4
    calls = {"n": 0}

    def short_ohlcv(venue, symbol, tf, since_ms):
        calls["n"] += 1
        return [c for c in full if c[0] >= int(since_ms)]

    agent = _GateAgent(wh, short_ohlcv)

    assert _gate(agent, now, min_bars=10) is None
    assert agent._bar_seen[("BTC/USDT:USDT", "4h")] == (now // H4 - 1) * H4
    assert _gate(agent, now, min_bars=10) is None
    assert calls["n"] == 1, "short-but-real history must not re-fetch this bar"


# ══════════════════════════════════════════════════════════════════════════
# D2 — the paired entry write must be atomic
# ══════════════════════════════════════════════════════════════════════════
def _decision_kwargs(pid="p-1"):
    return dict(
        ts=START, model_version="mv_test", symbol="BTC/USDT:USDT", side="buy",
        agent_id="TestProbe", proposal_id=pid, notional=100.0, entry_px=100.0,
        sl_px=95.0, tp_px=110.0, venue="bybit", timeframe="4h", horizon_bars=42,
    )


def _probe_row(pid="p-1"):
    return {
        "proposal_id": pid, "symbol": "BTC/USDT:USDT", "venue": "bybit",
        "arm": "pullback_ma20_rsi14_4h", "side": "buy", "signal_bar_ts": START,
        "entry_px": 100.0, "rsi_entry": 56.0, "sma20_entry": 99.0,
        "sma50_entry": 98.0, "sma200_entry": 97.0, "atr_entry": 3.0,
        "sl_px": 95.0, "max_hold_bars": 42, "risk_frac": 0.01,
        "notional_usd": 100.0, "units": 1.0, "score": 0.1,
        "realized_funding_rate_sum": 0.0, "last_funding_bucket": None,
        "closed_hint_ts": None, "closed_hint_reason": None, "created_ts": START,
    }


def test_entry_pair_writes_both_rows_on_success(wh):
    _pullback_schema(wh)
    write_entry_pair(
        wh, decision=_decision_kwargs(),
        probe_table="shadow_pullback_probe", probe_row=_probe_row(),
    )
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 1
    assert wh.query("SELECT COUNT(*) n FROM shadow_pullback_probe")[0]["n"] == 1


def test_entry_pair_rolls_back_the_decision_when_the_probe_row_fails(wh):
    """THE defect: a failed second write must leave NO orphan decision row.

    An orphan resolves into shadow_outcomes (counting toward the >=30-resolved
    promotion gate) while no probe row holds the occupancy slot — so the probe
    re-enters the same signal and the gate is computed on a doubled event."""
    _pullback_schema(wh)
    # Pre-claim the PK so the probe-table insert raises IntegrityError.
    conn = wh._conn()
    conn.execute(
        "INSERT INTO shadow_pullback_probe (proposal_id) VALUES (?)", ("p-1",)
    )
    conn.commit()

    with pytest.raises(sqlite3.IntegrityError):
        write_entry_pair(
            wh, decision=_decision_kwargs("p-1"),
            probe_table="shadow_pullback_probe", probe_row=_probe_row("p-1"),
        )

    orphans = wh.query(
        "SELECT COUNT(*) n FROM shadow_decisions WHERE proposal_id='p-1'"
    )[0]["n"]
    assert orphans == 0, "orphan decision row survived a failed paired write"
    assert wh._conn().in_transaction is False, "transaction left open"


def test_entry_pair_leaves_the_connection_usable_after_a_rollback(wh):
    """A rolled-back pair must not poison the shared thread-local connection."""
    _pullback_schema(wh)
    conn = wh._conn()
    conn.execute(
        "INSERT INTO shadow_pullback_probe (proposal_id) VALUES (?)", ("p-1",)
    )
    conn.commit()
    with pytest.raises(sqlite3.IntegrityError):
        write_entry_pair(
            wh, decision=_decision_kwargs("p-1"),
            probe_table="shadow_pullback_probe", probe_row=_probe_row("p-1"),
        )
    # A subsequent, valid pair still lands in full.
    write_entry_pair(
        wh, decision=_decision_kwargs("p-2"),
        probe_table="shadow_pullback_probe", probe_row=_probe_row("p-2"),
    )
    assert wh.query(
        "SELECT COUNT(*) n FROM shadow_decisions WHERE proposal_id='p-2'"
    )[0]["n"] == 1


def test_entry_pair_refuses_to_write_a_probe_row_with_no_decision(wh):
    """A mis-typed kwarg must NOT silently degrade into a bare insert_row: a
    probe row with no decision row takes the occupancy slot while leaving
    nothing for the resolver — the very corruption this helper prevents."""
    _pullback_schema(wh)
    with pytest.raises(ValueError, match="no decision row"):
        write_entry_pair(
            wh, probe_table="shadow_pullback_probe", probe_row=_probe_row()
        )
    assert wh.query("SELECT COUNT(*) n FROM shadow_pullback_probe")[0]["n"] == 0


@pytest.mark.parametrize(
    "module_name",
    [
        "core.agents.breakout_probe_agent",
        "core.agents.tsmom_probe_agent",
        "core.agents.bundle_mr_probe_agent",
        "core.agents.pullback_momentum_probe_agent",
        "core.agents.listing_short_probe_agent",
        "core.agents.unlock_short_probe_agent",
    ],
)
def test_every_probe_routes_its_entry_through_the_atomic_pair(module_name):
    """All six paired-write sites use the transactional helper. bundle_mr was
    the audit finding; the other five are the identical pattern."""
    import importlib
    import inspect

    src = inspect.getsource(importlib.import_module(module_name))
    assert "write_entry_pair" in src, f"{module_name} still writes the pair non-atomically"


# ══════════════════════════════════════════════════════════════════════════
# D3 — pullback horizon tightening must count POSITIONAL bars
# ══════════════════════════════════════════════════════════════════════════
class _Providers:
    def __init__(self, *, ohlcv=None, market_data=None, balance=10_000.0, now=0):
        self._ohlcv = dict(ohlcv or {})
        self._market_data = dict(market_data or {})
        self._balance = balance
        self.now = now

    def ohlcv(self, venue, symbol, timeframe, since_ms):
        return [r for r in self._ohlcv.get((symbol, timeframe), []) if r[0] >= int(since_ms)]

    def market_data(self, venue, symbol):
        return dict(self._market_data.get(symbol, {}))

    def balance(self):
        return self._balance

    def now_fn(self):
        return self.now


def _cross_series():
    """Uptrend -> 3-bar dip -> recovery; LAST bar is the RSI14 cross above 55."""
    closes = [100.0 + 0.1 * i for i in range(240)]
    top = closes[-1]
    closes += [top - 1.2, top - 2.4, top - 3.6]
    while True:
        r = bundle_rsi_last(closes, RSI_N)
        if r is not None and r > 55.0:
            break
        closes.append(closes[-1] + 0.8)
        assert len(closes) < 320, "fixture failed to produce an RSI14 cross"
    return closes


def _make(wh, providers):
    import core.agents.pullback_momentum_probe_agent as m

    return m.PullbackMomentumProbeAgent(
        warehouse=wh, ohlcv_provider=providers.ohlcv,
        market_data_provider=providers.market_data,
        account_balance_provider=providers.balance, now_fn=providers.now_fn,
        venue="bybit", symbols=("BTC/USDT:USDT",),
    )


def test_condition_exit_horizon_is_positional_across_a_venue_gap(wh):
    """A gap in the forward tape must not inflate the tightened horizon.

    The gap is present in BOTH the probe's view and the resolver's view (the
    same candle list), so calendar != positional and the assertion actually
    discriminates. Under the calendar formula the horizon exceeds the bars
    that exist and resolve_one returns None (permanent PENDING) — the harm.
    """
    from core.shadow_resolver import resolve_one

    closes = _cross_series()
    p = _Providers(
        ohlcv={("BTC/USDT:USDT", "4h"): _candles(START, closes)},
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0}},
        now=START + len(closes) * H4,
    )
    probe = _make(wh, p)
    probe.tick()
    row = wh.query("SELECT * FROM shadow_pullback_probe")[0]
    entry_ts = int(row["signal_bar_ts"])

    # Forward tape: a GENTLE climb to the RSI>70 exit (a steep one exits in
    # ~2 bars, leaving no room for a gap before it), then DROP a middle bar so
    # the venue's calendar span is one bar wider than the bars actually served.
    work = list(closes)
    ext = []
    while True:
        work.append(work[-1] + 0.15)
        ext.append(work[-1])
        win = work[-260:]
        assert work[-1] > sum(win[-20:]) / 20.0  # SMA20 exit must not fire first
        assert work[-1] - 1.0 > float(row["sl_px"])  # stop must not fire first
        if bundle_rsi_last(win, RSI_N) > 70.0:
            break
        assert len(ext) < 40

    full = _candles(START, closes + ext)
    n_entry = len(closes)  # first forward bar's index (entry bar is n_entry-1)
    assert len(ext) >= 3, "fixture needs >=3 forward bars to hold a gap"
    # Drop a forward bar BEFORE the exit bar so the hole shifts the exit's
    # positional index away from its calendar offset.
    gap_idx = n_entry + 1
    gapped = full[:gap_idx] + full[gap_idx + 1:]

    p._ohlcv[("BTC/USDT:USDT", "4h")] = gapped
    p.now = START + len(closes + ext) * H4
    probe.tick()

    d = wh.query(
        "SELECT * FROM shadow_decisions WHERE model_version='pullback_ma20_rsi14_4h_v1'"
    )[0]
    hint = wh.query("SELECT closed_hint_ts FROM shadow_pullback_probe")[0]
    exit_ts = int(hint["closed_hint_ts"])

    forward = [c for c in gapped if c[0] // 1000 > entry_ts]
    positional = sum(1 for c in forward if c[0] // 1000 <= exit_ts)
    calendar = (exit_ts - entry_ts) // H4
    assert calendar == positional + 1, "fixture failed to create a real gap"
    assert d["horizon_bars"] == positional, "horizon must count served bars"

    # THE harm the fix removes: with the calendar count the resolver's
    # censoring guard holds this row PENDING forever.
    out = resolve_one(dict(d), forward)
    assert out is not None, "gapped row must still resolve (not permanent PENDING)"
    assert out["bars_held"] == d["horizon_bars"]
