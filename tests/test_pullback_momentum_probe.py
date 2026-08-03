"""TDD spec for PullbackMomentumProbeAgent (pullback_ma20_rsi14_4h_v1).

HONESTY FRAMING (binding — mirrors the module docstring): NOT a pipeline GO.
Textbook pullback-momentum sits inside the refuted trend/momentum families
(0/40 OOS 2026-06-13). This is an owner-directed LOG-ONLY forward paper test
of the owner's own stated rules, mirroring the TsmomProbeAgent /
BreakoutProbeAgent precedent. Expectation: NO-PROMOTE.

Every test pins a frozen-spec condition:
- frozen constants (SMA20/50/200, RSI14, cross>55 entry, RSI>70 exit,
  close<SMA20 exit, 1.5xATR intrabar stop, 42-bar time stop, 210-bar warmup)
- indicator conventions = the in-repo bundle-MR reference math generalized
  (SMA-ATR14 NOT Wilder; bundle RSI construction with period 14, dn==0 -> 50)
- ENTRY is an EVENT (RSI14 crosses above 55), not a state
- frozen pre-outcome score tanh((rsi14_entry - 55) / 15) — never re-tuned
- notational 1%-risk sizing via the TsmomProbeAgent codex model
- one position per symbol; entry at the signal-bar close; long-only
- condition exits are encoded for the vetted resolver by tightening the
  still-PENDING decision row's horizon_bars to the realized exit bar
- structural LOG-ONLY: no order path reachable
- universe: reuse of the bundle-MR spec-derived resolver (frozen-5 default)

Run: venv/Scripts/python.exe -m pytest tests/test_pullback_momentum_probe.py -v
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

from core.agents.bundle_mr_probe_agent import SYMBOLS as BUNDLE_SYMBOLS  # noqa: E402
from core.agents.bundle_mr_probe_agent import bundle_rsi_last  # noqa: E402


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_pullback_probe_test",
        ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_pullback_probe_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


H4 = 4 * 3600
START = 1_000_000_000
RSI_N = 14


def _candles(start_ts_s: int, closes, bar_s: int = H4, highs=None, lows=None):
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


def _make(wh, providers, symbols=("BTC/USDT:USDT",)):
    import core.agents.pullback_momentum_probe_agent as m

    return m.PullbackMomentumProbeAgent(
        warehouse=wh,
        ohlcv_provider=providers.ohlcv,
        market_data_provider=providers.market_data,
        account_balance_provider=providers.balance,
        now_fn=providers.now_fn,
        venue="bybit",
        symbols=symbols,
    )


def _sma(vals, n):
    return sum(vals[-n:]) / n


def _cross_series():
    """Uptrend -> 3-bar dip (RSI14 well below 55) -> recovery; the LAST bar is
    the bar on which RSI14 first crosses back above 55 (the entry EVENT)."""
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


def _entered(wh, closes):
    p = _Providers(
        ohlcv={("BTC/USDT:USDT", "4h"): _candles(START, closes)},
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0001}},
        now=START + len(closes) * H4,  # all bars closed
    )
    probe = _make(wh, p)
    probe.tick()
    return probe, p


def _decision(wh):
    rows = wh.query(
        "SELECT * FROM shadow_decisions WHERE model_version='pullback_ma20_rsi14_4h_v1'"
    )
    assert len(rows) == 1
    return rows[0]


def _probe_row(wh):
    rows = wh.query("SELECT * FROM shadow_pullback_probe")
    assert len(rows) == 1
    return rows[0]


# ── Frozen constants ─────────────────────────────────────────────────────
def test_frozen_constants():
    """The probe forward-tests the owner's stated configuration. Editing any
    of these is a new pre-registration, not a tweak."""
    from core.agents import pullback_momentum_probe_agent as m

    assert m.TIMEFRAME == "4h" and m.BAR_S == 4 * 3600
    assert m.SMA_FAST == 20 and m.SMA_MID == 50 and m.SMA_SLOW == 200
    assert m.RSI_PERIOD == 14
    assert m.RSI_ENTRY == 55.0 and m.RSI_EXIT == 70.0
    assert m.STOP_ATR == 1.5
    assert m.MAX_HOLD_BARS == 42  # 7 days on 4h
    assert m.MIN_BARS == 210 and m.FETCH_BARS == 260
    assert m.ATR_LEN == 14  # SMA-ATR(14), the bundle-MR flavor
    assert m.SCORE_RSI_OFFSET == 55.0 and m.SCORE_RSI_SCALE == 15.0
    assert m.PULLBACK_MODEL_VERSION == "pullback_ma20_rsi14_4h_v1"
    assert m.PullbackMomentumProbeAgent.name == "PullbackMomentumProbeAgent"
    assert m.PullbackMomentumProbeAgent.model_version == "pullback_ma20_rsi14_4h_v1"


def test_frozen_score_formula():
    from core.agents.pullback_momentum_probe_agent import pullback_score

    assert pullback_score(58.3) == pytest.approx(math.tanh((58.3 - 55.0) / 15.0))
    assert pullback_score(70.0) > pullback_score(56.0)  # monotone in RSI
    assert pullback_score(None) == 0.0


# ── Entry event: pure gate decomposition ─────────────────────────────────
def test_entry_event_requires_cross_not_state():
    from core.agents.pullback_momentum_probe_agent import pullback_entry_event

    ok = dict(close=105.0, sma20=104.0, sma50=103.0, sma200=100.0)
    # the cross: prev <= 55 AND cur > 55
    assert pullback_entry_event(rsi_prev=54.0, rsi_cur=56.0, **ok) is True
    assert pullback_entry_event(rsi_prev=55.0, rsi_cur=55.1, **ok) is True  # boundary
    # state-true-but-no-cross must NOT enter
    assert pullback_entry_event(rsi_prev=56.0, rsi_cur=57.0, **ok) is False
    assert pullback_entry_event(rsi_prev=54.0, rsi_cur=55.0, **ok) is False  # not above
    # trend gate: SMA50 > SMA200
    assert (
        pullback_entry_event(
            rsi_prev=54.0, rsi_cur=56.0, close=105.0, sma20=104.0, sma50=99.0, sma200=100.0
        )
        is False
    )
    # close above SMA20
    assert (
        pullback_entry_event(
            rsi_prev=54.0, rsi_cur=56.0, close=103.0, sma20=104.0, sma50=103.0, sma200=100.0
        )
        is False
    )
    # missing indicator -> no signal, never a guess
    assert (
        pullback_entry_event(
            rsi_prev=None, rsi_cur=56.0, close=105.0, sma20=104.0, sma50=103.0, sma200=100.0
        )
        is False
    )


# ── Lifecycle: entry ─────────────────────────────────────────────────────
def test_cross_entry_writes_decision_and_probe_rows(wh):
    from core.agents.pullback_momentum_probe_agent import sma_atr_last

    closes = _cross_series()
    # fixture sanity: the frozen entry event really holds at the last bar
    assert bundle_rsi_last(closes[:-1], RSI_N) <= 55.0 < bundle_rsi_last(closes, RSI_N)
    assert _sma(closes, 50) > _sma(closes, 200)
    assert closes[-1] > _sma(closes, 20)
    _entered(wh, closes)

    r = _decision(wh)
    assert r["agent_id"] == "PullbackMomentumProbeAgent"
    assert r["side"] == "buy"  # long-only
    assert r["decision"] == "ALLOW"
    assert r["label_status"] == "PENDING"
    assert r["timeframe"] == "4h"
    assert r["horizon_bars"] == 42  # 42-bar (7d) time stop
    assert r["venue"] == "bybit"
    assert r["tp_px"] == 0.0  # NO TP barrier — exits are condition-based
    assert r["entry_px"] == pytest.approx(closes[-1])  # signal-bar close fill

    pr = _probe_row(wh)
    atr = sma_atr_last(_candles(START, closes), 14)
    assert pr["atr_entry"] == pytest.approx(atr)
    # conservative intrabar stop: entry - 1.5 x ATR14(at entry)
    assert r["sl_px"] == pytest.approx(closes[-1] - 1.5 * atr, rel=1e-9)
    assert pr["rsi_entry"] == pytest.approx(bundle_rsi_last(closes, RSI_N))
    assert pr["score"] == pytest.approx(math.tanh((pr["rsi_entry"] - 55.0) / 15.0))
    assert pr["max_hold_bars"] == 42
    assert pr["side"] == "buy"


def test_state_true_but_no_cross_does_not_enter(wh):
    closes = _cross_series()
    closes = closes + [closes[-1] + 0.8]  # latest bar: RSI still > 55, no cross
    assert bundle_rsi_last(closes[:-1], RSI_N) > 55.0  # fixture sanity
    assert bundle_rsi_last(closes, RSI_N) > 55.0
    _entered(wh, closes)
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_trend_gate_vetoes_cross_in_downtrend(wh):
    closes = [200.0 - 0.25 * i for i in range(240)]
    while True:
        r = bundle_rsi_last(closes, RSI_N)
        if r is not None and r > 55.0:
            break
        closes.append(closes[-1] + 1.0)
        assert len(closes) < 320
    # fixture sanity: cross true, but SMA50 below SMA200
    assert bundle_rsi_last(closes[:-1], RSI_N) <= 55.0 < bundle_rsi_last(closes, RSI_N)
    assert _sma(closes, 50) < _sma(closes, 200)
    _entered(wh, closes)
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_warmup_gate_no_entry_below_210_bars(wh):
    closes = _cross_series()
    short = closes[-200:]  # < MIN_BARS=210
    _entered(wh, short)
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_sizing_mirrors_tsmom_codex_notational_model(wh):
    from core.agents.probe_common import codex_position_units

    closes = _cross_series()
    _entered(wh, closes)
    r = _decision(wh)
    pr = _probe_row(wh)
    stop_distance = r["entry_px"] - r["sl_px"]
    units = codex_position_units(10_000.0, r["entry_px"], stop_distance)
    assert units > 0
    assert pr["units"] == pytest.approx(units)
    assert pr["notional_usd"] == pytest.approx(units * r["entry_px"])
    assert pr["risk_frac"] == pytest.approx(0.01)  # notational 1%-risk


def test_no_double_entry_while_open(wh):
    closes = _cross_series()
    probe, p = _entered(wh, closes)
    # next closed bar: RSI crosses back below then above? No — just extend;
    # occupied slot must block regardless of the new bar's signal state.
    ext = closes + [closes[-1] + 0.8]
    p._ohlcv[("BTC/USDT:USDT", "4h")] = _candles(START, ext)
    p.now = START + len(ext) * H4
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 1


def test_same_bar_never_entered_twice(wh):
    closes = _cross_series()
    probe, p = _entered(wh, closes)
    probe.tick()
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 1


# ── Exit paths ───────────────────────────────────────────────────────────
def _extend(wh, closes, build_ext):
    """Enter on _cross_series, then extend the tape with build_ext(sl) bars
    and tick once. Returns (probe row before ticks, extended closes)."""
    probe, p = _entered(wh, closes)
    row = _probe_row(wh)
    ext = build_ext(row)
    full = closes + ext
    p._ohlcv[("BTC/USDT:USDT", "4h")] = _candles(START, full)
    p.now = START + len(full) * H4
    probe.tick()
    return row, full


def test_rsi_overbought_exit_tightens_horizon(wh):
    closes = _cross_series()

    def build(row):
        ext = []
        work = list(closes)
        while True:
            work.append(work[-1] + 1.2)
            ext.append(work[-1])
            win = work[-260:]
            r = bundle_rsi_last(win, RSI_N)
            assert work[-1] > _sma(win, 20)  # fixture: SMA20 exit must not fire first
            assert work[-1] - 1.0 > row["sl_px"]  # fixture: stop must not fire first
            if r > 70.0:
                return ext
            assert len(ext) < 30

    row, full = _extend(wh, closes, build)
    k = len(full) - len(closes)
    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_pullback_probe"
    )[0]
    assert hint["closed_hint_reason"] == "rsi_overbought"
    assert hint["closed_hint_ts"] == row["signal_bar_ts"] + k * H4
    # the resolver lever: horizon tightened to the realized exit bar
    assert _decision(wh)["horizon_bars"] == k


def test_rsi_exit_resolves_as_time_exit_at_exit_bar_close(wh):
    """End-to-end honesty: the vetted resolver, replaying the tightened row,
    exits AT the strategy's exit bar close (no custom PnL math in the probe)."""
    from core.shadow_resolver import resolve_one

    closes = _cross_series()

    def build(row):
        ext = []
        work = list(closes)
        while True:
            work.append(work[-1] + 1.2)
            ext.append(work[-1])
            if bundle_rsi_last(work[-260:], RSI_N) > 70.0:
                return ext
            assert len(ext) < 30

    row, full = _extend(wh, closes, build)
    d = _decision(wh)
    forward = _candles(START, full)[len(closes):]  # bars AFTER entry
    out = resolve_one(dict(d), forward)
    assert out is not None
    assert out["exit_reason"] == "time"
    assert out["bars_held"] == d["horizon_bars"]
    # time exit marks out at the exit bar close (minus exit slippage)
    assert out["exit_px"] == pytest.approx(full[-1] * (1 - 5.0 / 10_000.0), rel=1e-9)


def test_close_below_sma20_exit_tightens_horizon(wh):
    closes = _cross_series()

    def build(row):
        ext = []
        work = list(closes)
        while True:
            work.append(work[-1] - 0.6)
            ext.append(work[-1])
            win = work[-260:]
            assert bundle_rsi_last(win, RSI_N) <= 70.0  # fixture guards
            assert work[-1] - 1.0 > row["sl_px"]
            if work[-1] < _sma(win, 20):
                return ext
            assert len(ext) < 30

    row, full = _extend(wh, closes, build)
    k = len(full) - len(closes)
    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_pullback_probe"
    )[0]
    assert hint["closed_hint_reason"] == "close_below_sma20"
    assert hint["closed_hint_ts"] == row["signal_bar_ts"] + k * H4
    assert _decision(wh)["horizon_bars"] == k


def test_intrabar_stop_has_priority_over_close_conditions(wh):
    """A bar whose low pierces the stop AND whose close is below SMA20 must
    hint stop_loss (intrabar precedes bar-close conditions), keep horizon=42,
    and resolve conservatively AT the stop."""
    from core.shadow_resolver import resolve_one

    closes = _cross_series()
    probe, p = _entered(wh, closes)
    row = _probe_row(wh)
    sl = row["sl_px"]

    exit_close = row["entry_px"] - 4.0
    full = closes + [exit_close]
    candles = _candles(START, full)
    candles[-1][3] = sl - 0.5  # low pierces the stop intrabar
    assert exit_close < _sma(full[-260:], 20)  # fixture: close condition ALSO true
    p._ohlcv[("BTC/USDT:USDT", "4h")] = candles
    p.now = START + len(full) * H4
    probe.tick()

    hint = wh.query("SELECT closed_hint_reason FROM shadow_pullback_probe")[0]
    assert hint["closed_hint_reason"] == "stop_loss"
    d = _decision(wh)
    assert d["horizon_bars"] == 42  # untouched — the resolver hits the SL itself

    out = resolve_one(dict(d), candles[len(closes):])
    assert out is not None
    assert out["exit_reason"] == "stop_loss"
    # conservative fill AT the stop (with SL slippage), never the better close
    assert out["exit_px"] == pytest.approx(sl * (1 - 10.0 / 10_000.0), rel=1e-9)


def test_time_stop_after_42_bars(wh):
    closes = _cross_series()

    def build(row):
        ext = []
        work = list(closes)
        for i in range(43):
            work.append(work[-1] + (0.4 if i % 2 == 0 else -0.3))
            ext.append(work[-1])
            win = work[-260:]
            # fixture guards: no earlier exit may fire
            assert bundle_rsi_last(win, RSI_N) <= 70.0
            assert work[-1] > _sma(win, 20)
            assert work[-1] - 1.0 > row["sl_px"]
        return ext

    row, full = _extend(wh, closes, build)
    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_pullback_probe"
    )[0]
    assert hint["closed_hint_reason"] == "time"
    assert hint["closed_hint_ts"] == row["signal_bar_ts"] + 42 * H4
    assert _decision(wh)["horizon_bars"] == 42
    n_mtm = wh.query("SELECT COUNT(*) n FROM shadow_pullback_mtm")[0]["n"]
    assert n_mtm == 42  # per-bar intra-hold MTM covers the full hold


def test_horizon_tighten_respects_pending_guard(wh):
    """A decision already RESOLVED (or otherwise terminal) is never mutated."""
    closes = _cross_series()
    probe, p = _entered(wh, closes)
    wh._conn().execute("UPDATE shadow_decisions SET label_status='RESOLVED'")
    wh._conn().commit()

    work = list(closes)
    while bundle_rsi_last(work[-260:], RSI_N) <= 70.0:
        work.append(work[-1] + 1.2)
    p._ohlcv[("BTC/USDT:USDT", "4h")] = _candles(START, work)
    p.now = START + len(work) * H4
    probe.tick()

    d = wh.query("SELECT horizon_bars FROM shadow_decisions")[0]
    assert d["horizon_bars"] == 42  # guard held


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
    closes = _cross_series()
    probe, p = _entered(wh, closes)
    row = _probe_row(wh)
    pid, entry = row["proposal_id"], float(row["signal_bar_ts"])
    book = _stub_settlements(monkeypatch, entry)

    def _sum():
        return wh.query(
            "SELECT realized_funding_rate_sum s FROM shadow_pullback_probe "
            "WHERE proposal_id=?", (pid,),
        )[0]["s"]

    p.now = START + len(closes) * H4 + 60
    probe.tick()
    probe.tick()  # replaying the same settlements must not double-book
    assert _sum() == pytest.approx(0.0002)

    book.append(_settlement(entry + 4 * 3600.0, 0.0003))
    p.now += 8 * 3600
    probe.tick()
    assert _sum() == pytest.approx(0.0005)


# ── Universe: reuse of the bundle-MR spec-derived resolver ───────────────
def test_default_symbols_are_the_frozen_bundle_basket():
    """The class default is the bundle-MR frozen 5-major fail-closed basket —
    widening happens ONLY through bot_engine._bundle_probe_symbols at wiring
    time (the SAME cached spec-derived resolution the bundle arms use)."""
    from core.agents import pullback_momentum_probe_agent as m

    assert m.SYMBOLS is BUNDLE_SYMBOLS


# ── Registration: bot_engine._PROBE_SPECS + funnel lane ──────────────────
def _pullback_spec():
    from core.bot_engine import BotEngine

    specs = [
        s
        for s in BotEngine._PROBE_SPECS
        if "pullback_momentum_probe_agent" in s["import_path"]
    ]
    assert len(specs) == 1
    return specs[0]


def test_probe_spec_registered_with_frozen_log_wording():
    spec = _pullback_spec()
    assert (
        spec["import_path"]
        == "core.agents.pullback_momentum_probe_agent:PullbackMomentumProbeAgent"
    )
    assert (
        "(log-only shadow probe; owner-directed pullback-momentum forward test, "
        "NOT a pipeline GO)" in spec["log"]
    )
    import config

    cfg = getattr(config, spec["config"])
    assert isinstance(cfg, dict)
    assert "enabled" in cfg and "venue" in cfg


def test_probe_spec_passes_bundle_universe_symbols():
    from core.bot_engine import BotEngine

    sentinel = ("AAA/USDT:USDT", "BBB/USDT:USDT")
    inst = BotEngine.__new__(BotEngine)
    inst._bundle_probe_symbols = lambda venue: sentinel
    inst._unlock_ohlcv = lambda *a: []
    inst._unlock_market_data = lambda *a: {}
    kwargs = _pullback_spec()["kwargs"](inst, {"venue": "bybit"})
    assert kwargs.get("symbols") == sentinel
    assert kwargs.get("venue") == "bybit"


def test_probe_spec_builds_agent_class():
    import importlib

    spec = _pullback_spec()
    mod_name, cls_name = spec["import_path"].split(":")
    cls = getattr(importlib.import_module(mod_name), cls_name)
    assert cls.name == "PullbackMomentumProbeAgent"


def test_promotion_funnel_lane_declared():
    from scripts.promotion_funnel import PROBE_LANES

    assert PROBE_LANES.get("pullback_ma20_4h") == ("PullbackMomentumProbeAgent", "4h")


# ── Structure ────────────────────────────────────────────────────────────
def test_structural_log_only_no_order_path():
    """The probe module must contain no reference to any order/write path."""
    src = (ROOT / "core" / "agents" / "pullback_momentum_probe_agent.py").read_text(
        encoding="utf-8"
    )
    for forbidden in (
        "order_manager",
        "OrderManager",
        "create_order",
        "open_position",
        "place_order",
        "cancel_order",
        "direct_executor",
        "smart_executor",
        "mcp_brain",
        "risk_manager",
        "_execute_open",
    ):
        assert forbidden not in src, f"probe references an order path: {forbidden}"


def test_companion_tables_exist(wh):
    _make(wh, _Providers())
    tables = {r["name"] for r in wh.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shadow_pullback_probe", "shadow_pullback_mtm"} <= tables


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
    import core.agents.pullback_momentum_probe_agent as m

    probe = m.PullbackMomentumProbeAgent(
        warehouse=wh,
        ohlcv_provider=b.ohlcv,
        market_data_provider=b.market_data,
        account_balance_provider=b.balance,
        now_fn=b.now_fn,
    )
    stats = probe.tick()  # must not raise into the shadow lane
    assert stats["entered"] == 0
