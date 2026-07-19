"""TDD spec for the bundle-MR probes (ZfadeProbeAgent cfg365 / Rsi2TrackerProbeAgent cfg226).

HONESTY FRAMING (binding — mirrors the module docstring): NOT a pipeline GO.
The 2026-07-19 cloud bundle test's cfg365 (MR-C 4h z-fade) FAILED gate G2 (OOS
WR 70-71% ABOVE the 63-67 band) and is a 1-of-432 sweep survivor —
plausible-unconfirmed. cfg226 (MR-B 4h RSI-2) was in/near band but net
NEGATIVE — a tracker for the band-vs-profit tension. Owner-directed log-only
forward paper test, mirroring the TsmomProbeAgent/BreakoutProbeAgent
precedent. Expectation: NO-PROMOTE.

Every test pins a binding condition from the owner directive or a charter
boundary:
- frozen cloud-deliverable constants (z20 +/-1.5 fade, RSI(2) 10/90, EMA200
  trend-side gate, TP 1.0/0.8 x ATR14, SL 2.4/2.0 x ATR14, 12-bar time-stop)
- indicator math matches the reference harness paper_trader.py EXACTLY:
  SMA-ATR(14) (NOT Wilder), ewm-span EMA200 with no min_periods (210-bar
  gate), Wilder-ewm RSI with dn==0 -> 50, rolling-20 zscore (ddof=1)
- frozen pre-outcome scores: tanh(|z|/3.0) and tanh((10-RSI2)/10) — never
  re-tuned
- notational 3%-stake sizing (the bundle's own model), no leverage
- two arms logged under DISTINCT agent_ids (funnel lanes key on
  agent_id+timeframe; both arms are 4h)
- one position per (symbol, arm); entry at the signal-bar close
- structural LOG-ONLY: no order path reachable
- resolver funding provider can read the probe's realized funding sum

Run: venv/Scripts/python.exe -m pytest tests/test_bundle_mr_probes.py -v
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
        "warehouse_bundle_mr_probe_test",
        ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_bundle_mr_probe_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


H4 = 4 * 3600
START = 1_000_000_000


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


def _make(wh, providers, cls_name, symbols=("BTC/USDT:USDT",)):
    import core.agents.bundle_mr_probe_agent as m

    cls = getattr(m, cls_name)
    return cls(
        warehouse=wh,
        ohlcv_provider=providers.ohlcv,
        market_data_provider=providers.market_data,
        account_balance_provider=providers.balance,
        now_fn=providers.now_fn,
        venue="bybit",
        symbols=symbols,
    )


def _dip_series(n_up=255, start=100.0, slope=0.1, dips=(1.5, 3.0, 4.5)):
    """Uptrend then a sharp 3-bar dip: z20 << -1.5 and RSI(2) << 10 while the
    close stays above EMA200 — a with-trend long setup for BOTH arms."""
    base = [start + slope * i for i in range(n_up)]
    top = base[-1]
    return base + [top - d for d in dips]


def _pop_series(n_dn=255, start=150.0, slope=0.1, pops=(1.5, 3.0, 4.5)):
    """Downtrend then a sharp 3-bar pop: mirror short setup for both arms."""
    base = [start - slope * i for i in range(n_dn)]
    bottom = base[-1]
    return base + [bottom + p for p in pops]


def _entered(wh, closes, cls_name, n_extra_ticks=0):
    p = _Providers(
        ohlcv={("BTC/USDT:USDT", "4h"): _candles(START, closes)},
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0001}},
        now=START + len(closes) * H4,  # all bars closed
    )
    probe = _make(wh, p, cls_name)
    probe.tick()
    for _ in range(n_extra_ticks):
        probe.tick()
    return probe, p


# ── Frozen cloud-deliverable constants ───────────────────────────────────
def test_frozen_bundle_constants():
    """The probes forward-test THE BUNDLE's shipped configuration (shortlist
    cfg365 / cfg226). Editing any of these is a new pre-registration."""
    from core.agents import bundle_mr_probe_agent as m

    assert m.TIMEFRAME == "4h" and m.BAR_S == 4 * 3600
    assert m.EMA_TREND_BARS == 200  # EMA200 trend-side gate
    assert m.Z_WINDOW == 20 and m.Z_ENTRY == 1.5  # cfg365: z20 beyond +/-1.5
    assert m.RSI_LEN == 2 and m.RSI_THR == 10.0  # cfg226: RSI(2) 10/90
    assert m.TSTOP_BARS == 12  # both arms: 12-bar time-stop
    assert m.ATR_LEN == 14  # SMA-ATR(14), the bundle's flavor
    assert m.MIN_BARS == 210 and m.FETCH_BARS == 260  # paper_trader gates
    assert m.STAKE_FRAC == 0.03  # bundle sizing: 3% notional (notational)
    assert m.SCORE_Z_SCALE == 3.0  # FROZEN pre-outcome
    assert m.SCORE_RSI_SCALE == 10.0  # FROZEN pre-outcome
    assert m.ZfadeProbeAgent.TP_ATR == 1.0 and m.ZfadeProbeAgent.SL_ATR == 2.4
    assert m.Rsi2TrackerProbeAgent.TP_ATR == 0.8 and m.Rsi2TrackerProbeAgent.SL_ATR == 2.0
    assert m.ZFADE_MODEL_VERSION == "zfade_4h_cfg365_v1"
    assert m.RSI2_MODEL_VERSION == "rsi2_4h_cfg226_v1"
    assert m.ZfadeProbeAgent.name == "ZfadeProbeAgent"
    assert m.Rsi2TrackerProbeAgent.name == "Rsi2TrackerProbeAgent"
    assert set(m.SYMBOLS) == {
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
        "BNB/USDT:USDT",
        "XRP/USDT:USDT",
    }


# ── Indicator math mirrors paper_trader.py exactly ───────────────────────
def test_rsi_matches_paper_trader_pandas_reference():
    import numpy as np
    import pandas as pd

    from core.agents.bundle_mr_probe_agent import bundle_rsi_last

    closes = [100 + 4 * math.sin(i / 3.0) + 0.02 * i for i in range(120)]
    s = pd.Series(closes)
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1 / 2, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1 / 2, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    ref = (100 - 100 / (1 + rs)).fillna(50).iloc[-1]
    assert bundle_rsi_last(closes, 2) == pytest.approx(float(ref), rel=1e-9)


def test_rsi_pure_uptrend_is_50_per_reference_fillna():
    """paper_trader: dn ewm == 0 -> rs NaN -> fillna(50). Mirror it exactly
    (a classic RSI would say 100 — fidelity beats textbook here)."""
    from core.agents.bundle_mr_probe_agent import bundle_rsi_last

    assert bundle_rsi_last([100.0 + i for i in range(50)], 2) == pytest.approx(50.0)


def test_zscore_matches_pandas_rolling_reference():
    import pandas as pd

    from core.agents.bundle_mr_probe_agent import zscore_last

    closes = [100 + 5 * math.sin(i / 4.0) for i in range(60)]
    s = pd.Series(closes)
    ref = ((s - s.rolling(20).mean()) / s.rolling(20).std()).iloc[-1]
    assert zscore_last(closes, 20) == pytest.approx(float(ref), rel=1e-9)
    assert zscore_last([100.0] * 60, 20) is None  # zero std -> no signal


def test_sma_atr_matches_paper_trader_pandas_reference():
    import pandas as pd

    from core.agents.bundle_mr_probe_agent import sma_atr_last

    closes = [100 + 6 * math.sin(i / 5.0) for i in range(80)]
    candles = _candles(0, closes)
    df = pd.DataFrame(
        {
            "high": [c[2] for c in candles],
            "low": [c[3] for c in candles],
            "close": [c[4] for c in candles],
        }
    )
    tr = pd.concat(
        [
            df.high - df.low,
            (df.high - df.close.shift()).abs(),
            (df.low - df.close.shift()).abs(),
        ],
        axis=1,
    ).max(axis=1)
    ref = tr.rolling(14).mean().iloc[-1]  # SIMPLE mean — NOT Wilder
    assert sma_atr_last(candles, 14) == pytest.approx(float(ref), rel=1e-9)
    assert sma_atr_last(candles[:10], 14) is None


def test_ema200_matches_pandas_no_min_periods():
    """paper_trader's ema() has NO min_periods — warm-up is its len>=210 gate.
    The probe must reproduce that seed-at-first-bar semantics."""
    import pandas as pd

    from core.agents.bundle_mr_probe_agent import bundle_ema_last

    closes = [100 + 3 * math.sin(i / 7.0) + 0.05 * i for i in range(258)]
    ref = pd.Series(closes).ewm(span=200, adjust=False).mean().iloc[-1]
    assert bundle_ema_last(closes, 200) == pytest.approx(float(ref), rel=1e-9)


# ── Signal rules: trend-side gating, both arms, both directions ──────────
def test_zfade_signal_requires_trend_side_agreement():
    from core.agents.bundle_mr_probe_agent import zfade_signal

    assert zfade_signal(-2.0, close=105.0, trend=100.0) == 1  # dip above EMA200
    assert zfade_signal(2.0, close=95.0, trend=100.0) == -1  # pop below EMA200
    assert zfade_signal(-2.0, close=95.0, trend=100.0) == 0  # counter-trend fade blocked
    assert zfade_signal(2.0, close=105.0, trend=100.0) == 0  # counter-trend fade blocked
    assert zfade_signal(-1.4, close=105.0, trend=100.0) == 0  # inside the +/-1.5 band
    assert zfade_signal(None, close=105.0, trend=100.0) == 0


def test_rsi2_signal_requires_trend_side_agreement():
    from core.agents.bundle_mr_probe_agent import rsi2_signal

    assert rsi2_signal(5.0, close=105.0, trend=100.0) == 1  # oversold above EMA200
    assert rsi2_signal(95.0, close=95.0, trend=100.0) == -1  # overbought below EMA200
    assert rsi2_signal(5.0, close=95.0, trend=100.0) == 0  # counter-trend blocked
    assert rsi2_signal(95.0, close=105.0, trend=100.0) == 0  # counter-trend blocked
    assert rsi2_signal(50.0, close=105.0, trend=100.0) == 0  # neutral RSI
    assert rsi2_signal(None, close=105.0, trend=100.0) == 0


def test_scores_frozen_formulas():
    from core.agents.bundle_mr_probe_agent import rsi2_score, zfade_score

    assert zfade_score(-2.1) == pytest.approx(math.tanh(2.1 / 3.0))
    assert zfade_score(2.1) == zfade_score(-2.1)  # magnitude, not sign
    assert zfade_score(3.0) > zfade_score(1.6)  # monotone in |z|
    assert rsi2_score(4.0, 1) == pytest.approx(math.tanh((10.0 - 4.0) / 10.0))
    assert rsi2_score(96.0, -1) == pytest.approx(math.tanh((96.0 - 90.0) / 10.0))
    assert rsi2_score(1.0, 1) > rsi2_score(9.0, 1)  # deeper oversold scores higher


# ── Lifecycle: entries ───────────────────────────────────────────────────
def test_zfade_long_entry_writes_decision_and_probe_rows(wh):
    from core.agents.bundle_mr_probe_agent import (
        bundle_ema_last,
        zscore_last,
    )

    closes = _dip_series()
    # fixture sanity: with-trend dip really is a cfg365 long setup
    assert zscore_last(closes, 20) < -1.5
    assert closes[-1] > bundle_ema_last(closes, 200)
    _entered(wh, closes, "ZfadeProbeAgent")

    rows = wh.query("SELECT * FROM shadow_decisions WHERE model_version='zfade_4h_cfg365_v1'")
    assert len(rows) == 1
    r = rows[0]
    assert r["agent_id"] == "ZfadeProbeAgent"
    assert r["side"] == "buy"
    assert r["decision"] == "ALLOW"
    assert r["label_status"] == "PENDING"
    assert r["timeframe"] == "4h"
    assert r["horizon_bars"] == 12  # the 12-bar time-stop
    assert r["venue"] == "bybit"
    entry, sl, tp = r["entry_px"], r["sl_px"], r["tp_px"]
    assert entry == pytest.approx(closes[-1])  # signal-bar close fill
    assert 0 < sl < entry < tp

    prows = wh.query("SELECT * FROM shadow_bundle_mr_probe WHERE arm='zfade_cfg365'")
    assert len(prows) == 1
    pr = prows[0]
    atr = pr["atr_entry"]
    assert atr is not None and atr > 1.3  # default-candle TR floor sanity
    # cfg365 bracket geometry: TP 1.0 x ATR, SL 2.4 x ATR
    assert tp - entry == pytest.approx(1.0 * atr, rel=1e-9)
    assert entry - sl == pytest.approx(2.4 * atr, rel=1e-9)
    assert pr["signal_value"] == pytest.approx(zscore_last(closes, 20))
    assert pr["ema_trend"] == pytest.approx(bundle_ema_last(closes, 200))
    assert pr["score"] == pytest.approx(math.tanh(abs(pr["signal_value"]) / 3.0))
    # bundle sizing: 3% of equity as notional (notational only)
    assert pr["stake_frac"] == pytest.approx(0.03)
    assert pr["notional_usd"] == pytest.approx(0.03 * 10_000.0)
    assert pr["units"] == pytest.approx(0.03 * 10_000.0 / entry)


def test_zfade_short_entry_mirrors_geometry(wh):
    closes = _pop_series()
    _entered(wh, closes, "ZfadeProbeAgent")

    rows = wh.query("SELECT * FROM shadow_decisions WHERE model_version='zfade_4h_cfg365_v1'")
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "sell"
    entry, sl, tp = r["entry_px"], r["sl_px"], r["tp_px"]
    assert tp < entry < sl  # short: stop above, target below
    pr = wh.query("SELECT * FROM shadow_bundle_mr_probe")[0]
    assert entry - tp == pytest.approx(1.0 * pr["atr_entry"], rel=1e-9)
    assert sl - entry == pytest.approx(2.4 * pr["atr_entry"], rel=1e-9)
    assert pr["signal_value"] > 1.5  # z at entry, written as-of


def test_rsi2_long_entry_writes_rows(wh):
    from core.agents.bundle_mr_probe_agent import bundle_rsi_last

    closes = _dip_series()
    assert bundle_rsi_last(closes, 2) < 10.0  # fixture sanity
    _entered(wh, closes, "Rsi2TrackerProbeAgent")

    rows = wh.query("SELECT * FROM shadow_decisions WHERE model_version='rsi2_4h_cfg226_v1'")
    assert len(rows) == 1
    r = rows[0]
    assert r["agent_id"] == "Rsi2TrackerProbeAgent"
    assert r["side"] == "buy"
    assert r["horizon_bars"] == 12
    entry, sl, tp = r["entry_px"], r["sl_px"], r["tp_px"]
    pr = wh.query("SELECT * FROM shadow_bundle_mr_probe WHERE arm='rsi2_cfg226'")[0]
    # cfg226 bracket geometry: TP 0.8 x ATR, SL 2.0 x ATR
    assert tp - entry == pytest.approx(0.8 * pr["atr_entry"], rel=1e-9)
    assert entry - sl == pytest.approx(2.0 * pr["atr_entry"], rel=1e-9)
    rsi = pr["signal_value"]
    assert rsi == pytest.approx(bundle_rsi_last(closes, 2))
    assert pr["score"] == pytest.approx(math.tanh((10.0 - rsi) / 10.0))


def test_rsi2_short_entry_mirrors(wh):
    from core.agents.bundle_mr_probe_agent import bundle_rsi_last

    closes = _pop_series()
    assert bundle_rsi_last(closes, 2) > 90.0  # fixture sanity
    _entered(wh, closes, "Rsi2TrackerProbeAgent")

    r = wh.query("SELECT * FROM shadow_decisions WHERE model_version='rsi2_4h_cfg226_v1'")[0]
    assert r["side"] == "sell"
    pr = wh.query("SELECT * FROM shadow_bundle_mr_probe")[0]
    assert pr["score"] == pytest.approx(math.tanh((pr["signal_value"] - 90.0) / 10.0))


def test_both_arms_log_under_distinct_agent_ids(wh):
    """The dip fires BOTH arms; each logs under its own agent_id so the
    promotion funnel (agent_id+timeframe) tracks them as separate lanes."""
    closes = _dip_series()
    p = _Providers(
        ohlcv={("BTC/USDT:USDT", "4h"): _candles(START, closes)},
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0001}},
        now=START + len(closes) * H4,
    )
    _make(wh, p, "ZfadeProbeAgent").tick()
    _make(wh, p, "Rsi2TrackerProbeAgent").tick()

    rows = wh.query("SELECT agent_id, model_version FROM shadow_decisions")
    assert {(r["agent_id"], r["model_version"]) for r in rows} == {
        ("ZfadeProbeAgent", "zfade_4h_cfg365_v1"),
        ("Rsi2TrackerProbeAgent", "rsi2_4h_cfg226_v1"),
    }
    arms = {r["arm"] for r in wh.query("SELECT arm FROM shadow_bundle_mr_probe")}
    assert arms == {"zfade_cfg365", "rsi2_cfg226"}


def test_flat_tape_no_entry(wh):
    _entered(wh, [100.0] * 258, "ZfadeProbeAgent")
    _entered(wh, [100.0] * 258, "Rsi2TrackerProbeAgent")
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_insufficient_history_no_entry(wh):
    closes = _dip_series(n_up=180)  # 183 < MIN_BARS=210 (paper_trader gate)
    _entered(wh, closes, "ZfadeProbeAgent")
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_no_double_entry_while_open(wh):
    closes = _dip_series()
    probe, p = _entered(wh, closes, "ZfadeProbeAgent")

    # next closed bar continues the dip -> signal still true, but occupied
    ext = closes + [closes[-1] - 1.5]
    p._ohlcv[("BTC/USDT:USDT", "4h")] = _candles(START, ext)
    p.now = START + len(ext) * H4
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 1


def test_same_bar_never_entered_twice(wh):
    closes = _dip_series()
    _entered(wh, closes, "ZfadeProbeAgent", n_extra_ticks=3)  # no new bars
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 1


# ── Monitoring: time-stop, stop hint, funding ────────────────────────────
def test_time_stop_hint_after_12_bars(wh):
    closes = _dip_series()
    probe, p = _entered(wh, closes, "ZfadeProbeAgent")
    row = wh.query("SELECT * FROM shadow_bundle_mr_probe")[0]
    entry_px = row["entry_px"]

    # 13 flat bars (inside both brackets), then time is up
    ext = closes + [entry_px + 0.2 * ((i % 3) - 1) for i in range(13)]
    p._ohlcv[("BTC/USDT:USDT", "4h")] = _candles(START, ext)
    p.now = START + len(ext) * H4
    probe.tick()

    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_bundle_mr_probe WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]
    assert hint["closed_hint_reason"] == "time"
    # 12 bars after the signal bar: opens at signal_bar_ts + 12 x 4h
    assert hint["closed_hint_ts"] == row["signal_bar_ts"] + 12 * H4
    n_mtm = wh.query(
        "SELECT COUNT(*) n FROM shadow_bundle_mr_mtm WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]["n"]
    assert n_mtm == 12  # per-bar intra-hold MTM covers the full hold


def test_stop_hint_frees_slot(wh):
    closes = _dip_series()
    probe, p = _entered(wh, closes, "ZfadeProbeAgent")
    row = wh.query("SELECT * FROM shadow_bundle_mr_probe")[0]

    # next bar wicks through the stop but closes back near entry
    ext = closes + [row["entry_px"] + 0.05]
    candles = _candles(START, ext)
    candles[-1][3] = row["sl_px"] - 0.5  # low pierces the stop
    p._ohlcv[("BTC/USDT:USDT", "4h")] = candles
    p.now = START + len(ext) * H4
    probe.tick()

    hint = wh.query(
        "SELECT closed_hint_reason FROM shadow_bundle_mr_probe WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]
    assert hint["closed_hint_reason"] == "stop_loss"


def test_funding_accrues_once_per_8h_bucket(wh):
    closes = _dip_series()
    probe, p = _entered(wh, closes, "ZfadeProbeAgent")
    pid = wh.query("SELECT proposal_id FROM shadow_bundle_mr_probe")[0]["proposal_id"]

    p.now = START + len(closes) * H4 + 60
    probe.tick()  # first bucket booked
    probe.tick()  # same bucket -> no double booking
    frs1 = wh.query(
        "SELECT realized_funding_rate_sum s FROM shadow_bundle_mr_probe WHERE proposal_id=?",
        (pid,),
    )[0]["s"]
    assert frs1 == pytest.approx(0.0001)

    p.now += 8 * 3600
    probe.tick()  # next settlement bucket
    frs2 = wh.query(
        "SELECT realized_funding_rate_sum s FROM shadow_bundle_mr_probe WHERE proposal_id=?",
        (pid,),
    )[0]["s"]
    assert frs2 == pytest.approx(0.0002)


def test_resolver_funding_provider_reads_bundle_probe(wh):
    closes = _dip_series()
    _entered(wh, closes, "ZfadeProbeAgent")
    pid = wh.query("SELECT proposal_id FROM shadow_bundle_mr_probe")[0]["proposal_id"]
    wh._conn().execute(
        "UPDATE shadow_bundle_mr_probe SET realized_funding_rate_sum=0.0042 WHERE proposal_id=?",
        (pid,),
    )
    wh._conn().commit()

    spec = importlib.util.spec_from_file_location(
        "resolve_shadow_outcomes_bundle_test", ROOT / "scripts" / "resolve_shadow_outcomes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    provider = mod._build_probe_funding_provider(wh)
    assert provider({"proposal_id": pid}) == pytest.approx(0.0042)


# ── Structure ────────────────────────────────────────────────────────────
def test_structural_log_only_no_order_path():
    """The probe module must contain no reference to any order/write path."""
    src = (ROOT / "core" / "agents" / "bundle_mr_probe_agent.py").read_text(encoding="utf-8")
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
    _make(wh, _Providers(), "ZfadeProbeAgent")
    tables = {r["name"] for r in wh.query("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shadow_bundle_mr_probe", "shadow_bundle_mr_mtm"} <= tables


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
    from core.agents.bundle_mr_probe_agent import Rsi2TrackerProbeAgent, ZfadeProbeAgent

    for cls in (ZfadeProbeAgent, Rsi2TrackerProbeAgent):
        probe = cls(
            warehouse=wh,
            ohlcv_provider=b.ohlcv,
            market_data_provider=b.market_data,
            account_balance_provider=b.balance,
            now_fn=b.now_fn,
        )
        stats = probe.tick()  # must not raise into the shadow lane
        assert stats["entered"] == 0
