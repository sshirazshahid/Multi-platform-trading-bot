"""TDD spec for the TsmomProbeAgent (Codex TSMOM-20d regime-watch, owner-directed).

HONESTY FRAMING (binding — mirrors the module docstring): this probe is NOT a
pipeline GO. Time-series momentum is a REFUTED family on the
refuted-families-ledger (long-only TSMOM: no profit edge, 2026-06-15; textbook
trend 0/40 OOS, 2026-06-13), and the external Codex backtest did NOT meet the
reopen bar. The probe exists ONLY because (a) the owner directed it and (b) a
log-only forward paper test is the honest instrument for collecting the forward
evidence that could someday meet that bar. Expectation: NO-PROMOTE.

Every test pins a binding condition from the owner directive or a charter
boundary:
- Pine-extracted rules frozen as constants (480/120/168 @1h, 120/30/42 @4h,
  ATR 14, 2x ATR stop, 2R target, momentum-sign + EMA-side entry)
- indicator math matches the Codex reference backtest (pandas ewm formulas)
- frozen pre-outcome score tanh(|mom_20d| / 0.10) — never re-tuned
- Codex 1% equity-risk sizing (notational only), 2x notional cap
- two arms logged and scored separately (tsmom_20d_1h_v1 / tsmom_20d_4h_v1)
- per-bar intra-hold MTM logged; entry rows carry the signal inputs
- one position per (symbol, arm) — the reference backtest's no-overlap rule
- structural LOG-ONLY: no order path reachable
- resolver funding provider can read the probe's realized funding sum

Run: venv/Scripts/python.exe -m pytest tests/test_tsmom_probe.py -v
"""

from __future__ import annotations

import importlib.util
import math
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_tsmom_probe_test",
        ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_tsmom_probe_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


HOUR = 3600
H4 = 4 * HOUR
DAY = 24 * HOUR


def _candles(start_ts_s: int, closes, bar_s: int = HOUR, highs=None, lows=None):
    """[ts_ms, o, h, l, c, v] candles from a close list (default h/l = c +/- 1)."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        ts_ms = (start_ts_s + i * bar_s) * 1000
        h = highs[i] if highs else c + 1.0
        low = lows[i] if lows else c - 1.0
        out.append([ts_ms, prev, h, low, c, 1000.0])
        prev = c
    return out


class _Providers:
    """Stub read-only providers. No write endpoint exists on this object —
    that is the structural LOG-ONLY guarantee the probe relies on."""

    def __init__(self, *, ohlcv=None, market_data=None, balance=10_000.0, now=0):
        self._ohlcv = dict(ohlcv or {})  # {(symbol, tf): [candles...]}
        self._market_data = dict(market_data or {})  # {symbol: {...}}
        self._balance = balance
        self.now = now

    def ohlcv(self, venue, symbol, timeframe, since_ms):
        rows = self._ohlcv.get((symbol, timeframe), [])
        return [r for r in rows if r[0] >= int(since_ms)]

    def market_data(self, venue, symbol):
        return dict(self._market_data.get(symbol, {}))

    def balance(self):
        return self._balance

    def now_fn(self):
        return self.now


def _make_probe(wh, providers, symbols=("BTC/USDT:USDT",)):
    from core.agents.tsmom_probe_agent import TsmomProbeAgent

    return TsmomProbeAgent(
        warehouse=wh,
        ohlcv_provider=providers.ohlcv,
        market_data_provider=providers.market_data,
        account_balance_provider=providers.balance,
        now_fn=providers.now_fn,
        venue="bybit",
        symbols=symbols,
    )


def _uptrend(n, start=100.0, slope=0.05):
    return [start + slope * i for i in range(n)]


def _downtrend(n, start=130.0, slope=0.05):
    return [start - slope * i for i in range(n)]


# ── Frozen Codex/Pine constants ──────────────────────────────────────────
def test_frozen_pine_constants():
    """The probe forward-tests THEIR configuration — the exact Pine 17/18 rules.
    Editing any of these is a new pre-registration, not a tweak."""
    from core.agents import tsmom_probe_agent as m

    assert m.ARMS["1h"]["mom_bars"] == 480  # 20d lookback on 1h (Pine 17)
    assert m.ARMS["1h"]["ema_bars"] == 120  # 5d EMA on 1h
    assert m.ARMS["1h"]["max_hold_bars"] == 168  # 7d max hold
    assert m.ARMS["1h"]["tf"] == "1h"
    assert m.ARMS["4h"]["mom_bars"] == 120  # 20d lookback on 4h (Pine 18)
    assert m.ARMS["4h"]["ema_bars"] == 30  # 5d EMA on 4h
    assert m.ARMS["4h"]["max_hold_bars"] == 42  # 7d max hold
    assert m.ARMS["4h"]["tf"] == "4h"
    assert m.ATR_LEN == 14
    assert m.STOP_ATR == 2.0
    assert m.REWARD_RISK == 2.0
    assert m.RISK_PCT == 0.01  # Codex risk model (notational)
    assert m.MAX_NOTIONAL_MULTIPLE == 2.0
    assert m.SCORE_MOM_SCALE == 0.10  # FROZEN pre-outcome
    assert set(m.SYMBOLS) == {"BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT"}


# ── Indicator math mirrors the Codex reference backtest ──────────────────
def test_ema_matches_pandas_reference():
    import pandas as pd

    from core.agents.tsmom_probe_agent import ema_last

    closes = [100 + 3 * math.sin(i / 7.0) + 0.01 * i for i in range(200)]
    ref = pd.Series(closes).ewm(span=30, adjust=False, min_periods=30).mean().iloc[-1]
    assert ema_last(closes, 30) == pytest.approx(float(ref), rel=1e-9)
    assert ema_last(closes[:10], 30) is None  # min_periods not met


def test_wilder_atr_matches_pandas_reference():
    import pandas as pd

    from core.agents.tsmom_probe_agent import wilder_atr_last

    closes = [100 + 5 * math.sin(i / 5.0) for i in range(80)]
    candles = _candles(0, closes)
    df = pd.DataFrame(
        {
            "high": [c[2] for c in candles],
            "low": [c[3] for c in candles],
            "close": [c[4] for c in candles],
        }
    )
    prev_close = df["close"].shift(1)
    tr = pd.concat(
        [df["high"] - df["low"], (df["high"] - prev_close).abs(), (df["low"] - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    ref = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean().iloc[-1]
    assert wilder_atr_last(candles, 14) == pytest.approx(float(ref), rel=1e-9)
    assert wilder_atr_last(candles[:10], 14) is None


def test_momentum_exact_lookback():
    from core.agents.tsmom_probe_agent import momentum_lookback

    closes = [100.0] * 5 + [110.0]  # close[-1]=110, close[-1-5]=100
    assert momentum_lookback(closes, 5) == pytest.approx(0.10)
    assert momentum_lookback(closes, 6) is None  # needs mom_bars+1 closes


def test_signal_rules_require_momentum_and_trend_agreement():
    from core.agents.tsmom_probe_agent import tsmom_signal

    assert tsmom_signal(0.05, close=101.0, trend=100.0) == 1  # long
    assert tsmom_signal(-0.05, close=99.0, trend=100.0) == -1  # short
    assert tsmom_signal(0.05, close=99.0, trend=100.0) == 0  # mom up, below EMA
    assert tsmom_signal(-0.05, close=101.0, trend=100.0) == 0  # mom down, above EMA
    assert tsmom_signal(0.0, close=101.0, trend=100.0) == 0  # zero momentum
    assert tsmom_signal(None, close=101.0, trend=100.0) == 0


def test_score_frozen_symmetric_monotone_varies():
    from core.agents.tsmom_probe_agent import tsmom_score

    assert tsmom_score(0.05) == pytest.approx(math.tanh(0.5))
    assert tsmom_score(-0.05) == tsmom_score(0.05)  # magnitude, not sign
    assert tsmom_score(0.20) > tsmom_score(0.05)  # monotone in |mom|
    assert len({tsmom_score(m) for m in (0.01, 0.08, 0.30)}) == 3


def test_codex_sizing_risk_and_leverage_cap():
    from core.agents.tsmom_probe_agent import codex_position_units

    # risk branch: 1% of 10k = $100 risk / $2 distance = 50 units ($5k < 2x cap)
    assert codex_position_units(10_000.0, 100.0, 2.0) == pytest.approx(50.0)
    # leverage branch: $100 / $0.4 = 250 units = $25k > 2x cap -> 200 units
    assert codex_position_units(10_000.0, 100.0, 0.4) == pytest.approx(200.0)
    assert codex_position_units(0.0, 100.0, 2.0) == 0.0


# ── Lifecycle: entries ───────────────────────────────────────────────────
def _entered_probe(wh, closes, *, bar_s=HOUR, tf="1h", n_extra_ticks=0):
    start = 1_000_000_000
    p = _Providers(
        ohlcv={("BTC/USDT:USDT", tf): _candles(start, closes, bar_s=bar_s)},
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0001}},
        now=start + len(closes) * bar_s,  # all bars closed
    )
    probe = _make_probe(wh, p)
    probe.tick()
    for _ in range(n_extra_ticks):
        probe.tick()
    return probe, p, start


def test_long_entry_writes_decision_and_probe_rows(wh):
    closes = _uptrend(600)
    _entered_probe(wh, closes)

    rows = wh.query("SELECT * FROM shadow_decisions WHERE model_version='tsmom_20d_1h_v1'")
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "buy"
    assert r["decision"] == "ALLOW"
    assert r["label_status"] == "PENDING"
    assert r["timeframe"] == "1h"
    assert r["horizon_bars"] == 168
    assert r["venue"] == "bybit"
    entry, sl, tp = r["entry_px"], r["sl_px"], r["tp_px"]
    assert entry == pytest.approx(closes[-1])  # signal-bar close fill
    assert 0 < sl < entry < tp
    # Pine geometry: tp - entry == 2 x (entry - sl)  (2R on a 2xATR stop)
    assert tp - entry == pytest.approx(2.0 * (entry - sl), rel=1e-9)

    prows = wh.query("SELECT * FROM shadow_tsmom_probe WHERE arm='1h'")
    assert len(prows) == 1
    pr = prows[0]
    assert pr["side"] == "buy"
    assert pr["mom_20d"] is not None and pr["mom_20d"] > 0
    assert pr["ema_trend"] is not None and pr["ema_trend"] < entry
    assert pr["atr_entry"] is not None and pr["atr_entry"] > 0
    assert pr["sl_px"] == pytest.approx(entry - 2.0 * pr["atr_entry"])
    assert pr["score"] == pytest.approx(math.tanh(abs(pr["mom_20d"]) / 0.10))
    assert pr["risk_frac"] == pytest.approx(0.01)
    # Codex sizing: units = 1% equity risk / risk distance (below the 2x cap)
    assert pr["units"] == pytest.approx(0.01 * 10_000.0 / (2.0 * pr["atr_entry"]))
    assert pr["notional_usd"] == pytest.approx(pr["units"] * entry)


def test_short_entry_mirrors_geometry(wh):
    closes = _downtrend(600)
    _entered_probe(wh, closes)

    rows = wh.query("SELECT * FROM shadow_decisions WHERE model_version='tsmom_20d_1h_v1'")
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "sell"
    entry, sl, tp = r["entry_px"], r["sl_px"], r["tp_px"]
    assert tp < entry < sl  # short: stop above, target below
    assert entry - tp == pytest.approx(2.0 * (sl - entry), rel=1e-9)


def test_arms_logged_separately(wh):
    start = 1_000_000_000
    n1h, n4h = 800, 200  # both series end at the same wall clock (start + 800h)
    p = _Providers(
        ohlcv={
            ("BTC/USDT:USDT", "1h"): _candles(start, _uptrend(n1h), bar_s=HOUR),
            ("BTC/USDT:USDT", "4h"): _candles(start, _uptrend(n4h), bar_s=H4),
        },
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0001}},
        now=start + n4h * H4,
    )
    probe = _make_probe(wh, p)
    probe.tick()

    versions = {
        r["model_version"]: r
        for r in wh.query("SELECT * FROM shadow_decisions WHERE agent_id='TsmomProbeAgent'")
    }
    assert set(versions) == {"tsmom_20d_1h_v1", "tsmom_20d_4h_v1"}
    assert versions["tsmom_20d_1h_v1"]["horizon_bars"] == 168
    assert versions["tsmom_20d_4h_v1"]["horizon_bars"] == 42
    assert versions["tsmom_20d_4h_v1"]["timeframe"] == "4h"
    arms = {r["arm"] for r in wh.query("SELECT arm FROM shadow_tsmom_probe")}
    assert arms == {"1h", "4h"}


def test_flat_tape_no_entry(wh):
    # zero momentum + close hugging the EMA -> no setup, nothing logged
    closes = [100.0] * 600
    _entered_probe(wh, closes)
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0
    assert wh.query("SELECT COUNT(*) n FROM shadow_tsmom_probe")[0]["n"] == 0


def test_insufficient_history_no_entry(wh):
    closes = _uptrend(100)  # < mom_bars+1 for the 1h arm
    _entered_probe(wh, closes)
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_no_double_entry_while_open(wh):
    closes = _uptrend(600)
    probe, p, start = _entered_probe(wh, closes)

    # next closed bar continues the trend -> signal still true, but occupied
    p._ohlcv[("BTC/USDT:USDT", "1h")] = _candles(start, _uptrend(601))
    p.now = start + 601 * HOUR
    probe.tick()
    assert (
        wh.query("SELECT COUNT(*) n FROM shadow_decisions WHERE model_version='tsmom_20d_1h_v1'")[
            0
        ]["n"]
        == 1
    )


def test_same_bar_never_entered_twice(wh):
    closes = _uptrend(600)
    probe, p, _ = _entered_probe(wh, closes, n_extra_ticks=3)  # no new bars
    assert (
        wh.query("SELECT COUNT(*) n FROM shadow_decisions WHERE model_version='tsmom_20d_1h_v1'")[
            0
        ]["n"]
        == 1
    )


# ── Monitoring: MTM path, barrier hints, re-entry, funding ───────────────
def test_mtm_path_logged_with_signed_returns(wh):
    closes = _uptrend(600)
    probe, p, start = _entered_probe(wh, closes)
    entry_px = wh.query("SELECT entry_px FROM shadow_tsmom_probe")[0]["entry_px"]

    # 5 more closed bars: dip below entry then recover above it
    ext = closes + [entry_px - 2.0, entry_px - 1.0, entry_px + 0.5, entry_px + 1.0, entry_px + 1.5]
    p._ohlcv[("BTC/USDT:USDT", "1h")] = _candles(start, ext)
    p.now = start + len(ext) * HOUR
    probe.tick()

    mtm = wh.query("SELECT * FROM shadow_tsmom_mtm ORDER BY bar_ts")
    assert len(mtm) == 5
    # long: below entry -> negative unrealized, above -> positive
    assert mtm[0]["unrealized_ret"] < 0
    assert mtm[-1]["unrealized_ret"] > 0
    assert mtm[-1]["unrealized_ret"] == pytest.approx((mtm[-1]["mark_px"] - entry_px) / entry_px)


def test_stop_hint_frees_slot_and_reentry_next_bar(wh):
    closes = _uptrend(600)
    probe, p, start = _entered_probe(wh, closes)
    row = wh.query("SELECT * FROM shadow_tsmom_probe")[0]
    sl = row["sl_px"]

    # bar 600 wicks through the stop but closes back on trend
    ext = list(closes) + [closes[-1] + 0.05]
    candles = _candles(start, ext)
    candles[-1][3] = sl - 0.5  # low pierces the stop
    p._ohlcv[("BTC/USDT:USDT", "1h")] = candles
    p.now = start + len(ext) * HOUR
    probe.tick()

    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_tsmom_probe WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]
    assert hint["closed_hint_reason"] == "stop_loss"
    assert hint["closed_hint_ts"] == start + 600 * HOUR

    # a LATER bar with the setup still true -> a NEW position may open
    ext.append(closes[-1] + 0.10)
    p._ohlcv[("BTC/USDT:USDT", "1h")] = _candles(start, ext)
    p.now = start + len(ext) * HOUR
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_tsmom_probe WHERE arm='1h'")[0]["n"] == 2


def test_sl_first_tie_break_on_same_bar(wh):
    """A bar spanning BOTH barriers hints stop_loss — the resolver's AFML
    conservative tie-break; the probe's occupancy hint must agree with it."""
    closes = _uptrend(600)
    probe, p, start = _entered_probe(wh, closes)
    row = wh.query("SELECT * FROM shadow_tsmom_probe")[0]

    ext = list(closes) + [closes[-1]]
    candles = _candles(start, ext)
    candles[-1][2] = row["tp_px"] + 1.0  # high through the target
    candles[-1][3] = row["sl_px"] - 1.0  # AND low through the stop
    p._ohlcv[("BTC/USDT:USDT", "1h")] = candles
    p.now = start + len(ext) * HOUR
    probe.tick()

    hint = wh.query(
        "SELECT closed_hint_reason FROM shadow_tsmom_probe WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]
    assert hint["closed_hint_reason"] == "stop_loss"


def test_time_exit_hint_after_max_hold(wh):
    # 4h arm (42-bar hold keeps the test light): flat continuation, no barriers
    start = 1_000_000_000
    closes = _uptrend(200, slope=0.05)
    p = _Providers(
        ohlcv={("BTC/USDT:USDT", "4h"): _candles(start, closes, bar_s=H4)},
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0001}},
        now=start + 200 * H4,
    )
    probe = _make_probe(wh, p)
    probe.tick()
    row = wh.query("SELECT * FROM shadow_tsmom_probe WHERE arm='4h'")[0]
    entry_px = row["entry_px"]

    # 43 flat bars (inside both barriers), then time is up
    ext = closes + [entry_px + 0.1 * ((i % 3) - 1) for i in range(43)]
    p._ohlcv[("BTC/USDT:USDT", "4h")] = _candles(start, ext, bar_s=H4)
    p.now = start + len(ext) * H4
    probe.tick()

    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_tsmom_probe WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]
    assert hint["closed_hint_reason"] == "time"
    # 42 bars after the signal bar: opens at signal_bar_ts + 42 x 4h
    assert hint["closed_hint_ts"] == row["signal_bar_ts"] + 42 * H4
    # MTM path covers exactly the 42 intra-hold bars
    n_mtm = wh.query(
        "SELECT COUNT(*) n FROM shadow_tsmom_mtm WHERE proposal_id=?", (row["proposal_id"],)
    )[0]["n"]
    assert n_mtm == 42


def _settlement(ts, rate):
    from core.funding_history import FundingSettlement

    return FundingSettlement(settlement_ts=float(ts), rate=float(rate))


def _stub_settlements(monkeypatch, entry_ts):
    """Stub the realized-settlement source: two settlements just after entry."""
    from core.agents import probe_common

    book = [_settlement(entry_ts + 1200.0, 0.0001),
            _settlement(entry_ts + 2400.0, 0.0001)]
    monkeypatch.setattr(
        probe_common, "load_realized_settlements",
        lambda venue, coin, *, start_ts, end_ts, **kw: tuple(
            s for s in book if start_ts <= s.settlement_ts <= end_ts),
    )
    return book


def test_funding_books_every_realized_settlement_exactly_once(wh, monkeypatch):
    """Replaces the old 8h-wall-clock-bucket contract: the funding interval is
    time-varying per symbol, so accrual replays the venue's REALIZED settlements
    and never books the live print. Hermetic: the source is stubbed, not data/."""
    closes = _uptrend(600)
    probe, p, start = _entered_probe(wh, closes)
    row = wh.query("SELECT proposal_id, signal_bar_ts FROM shadow_tsmom_probe")[0]
    pid, entry = row["proposal_id"], float(row["signal_bar_ts"])
    book = _stub_settlements(monkeypatch, entry)

    def _sum():
        return wh.query(
            "SELECT realized_funding_rate_sum s FROM shadow_tsmom_probe "
            "WHERE proposal_id=?", (pid,)
        )[0]["s"]

    p.now = start + 600 * HOUR + 60
    probe.tick()
    probe.tick()  # replaying the same settlements must not double-book
    assert _sum() == pytest.approx(0.0002)

    book.append(_settlement(entry + 4 * HOUR, 0.0003))
    p.now += 8 * HOUR
    probe.tick()
    assert _sum() == pytest.approx(0.0005)


def test_resolver_funding_provider_reads_tsmom_probe(wh, tmp_path):
    closes = _uptrend(600)
    _entered_probe(wh, closes)
    pid = wh.query("SELECT proposal_id FROM shadow_tsmom_probe")[0]["proposal_id"]
    wh._conn().execute(
        "UPDATE shadow_tsmom_probe SET realized_funding_rate_sum=0.0042 WHERE proposal_id=?", (pid,)
    )
    wh._conn().commit()

    spec = importlib.util.spec_from_file_location(
        "resolve_shadow_outcomes_tsmom_test", ROOT / "scripts" / "resolve_shadow_outcomes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    provider = mod._build_probe_funding_provider(wh)
    assert provider({"proposal_id": pid}) == pytest.approx(0.0042)
    assert provider({"proposal_id": "tm-nonexistent"}) == 0.0


# ── Structure ────────────────────────────────────────────────────────────
def test_structural_log_only_no_order_path():
    """The probe module must contain no reference to any order/write path."""
    src = (ROOT / "core" / "agents" / "tsmom_probe_agent.py").read_text(encoding="utf-8")
    for forbidden in (
        "order_manager",
        "OrderManager",
        "create_order",
        "open_position",
        "place_order",
        "cancel_order",
        "mcp_brain",
        "risk_manager",
        "_execute_open",
    ):
        assert forbidden not in src, f"probe references an order path: {forbidden}"


def test_companion_tables_exist(wh):
    _make_probe(wh, _Providers())
    tables = {r["name"] for r in wh.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shadow_tsmom_probe", "shadow_tsmom_mtm"} <= tables


def test_tick_never_raises_on_provider_errors(wh):
    class _Broken:
        def ohlcv(self, *a):
            raise RuntimeError("venue down")

        def market_data(self, *a):
            raise RuntimeError("venue down")

        def balance(self):
            raise RuntimeError("venue down")

        def now_fn(self):
            return 1_000_000_000

    b = _Broken()
    from core.agents.tsmom_probe_agent import TsmomProbeAgent

    probe = TsmomProbeAgent(
        warehouse=wh,
        ohlcv_provider=b.ohlcv,
        market_data_provider=b.market_data,
        account_balance_provider=b.balance,
        now_fn=b.now_fn,
    )
    stats = probe.tick()  # must not raise into the shadow lane
    assert stats["entered"] == 0
