"""TDD spec for the BreakoutProbeAgent (Codex deep-research breakout_60d winner).

HONESTY FRAMING (binding — mirrors the module docstring): NOT a pipeline GO.
Textbook trend/breakout is a REFUTED family on the refuted-families-ledger
(0/40 OOS, 2026-06-13; donchian_breakout scored F in Codex's own first
6-month sweep). The Codex deep run is the strongest external evidence yet for
the family (5-6yr x 10 markets, survives 2x costs, 9/9 parameter cells
stable) BUT the winner was selected ON holdout metrics across 20 candidates
(holdout burned; a flat 6-point penalty is not DSR/PBO) and Codex's OWN Monte
Carlo fails our frozen capital-preservation gates (P(positive)=91.5% < 0.95,
maxDD p95=42.5% > 0.25). Expected outcome: NO-PROMOTE at the frozen gate.

Every test pins a binding condition:
- Codex spec frozen as constants (360-bar channel, 2.2x ATR stop, 3R target,
  126-bar max hold -> 127-bar entry-inclusive resolver scan, 10 majors, 4h);
  spec confirmed by the 2026-07-11 16:19 --finalize-only re-run (9/9 cells
  stable around (2.2, 3.0); center is not the neighborhood's best cell)
- breakout signal: close beyond the SHIFTED prior-360-bar high/low channel
- frozen pre-outcome score tanh(penetration / 0.02) — scale picked from the
  Codex cache distribution (median 0.0137, n=1,604), never re-tuned
- 1% equity-risk notational sizing (shared Codex risk model)
- per-bar intra-hold MTM; one position per symbol; structural LOG-ONLY
- resolver funding provider reads the probe's realized funding sum

Run: venv/Scripts/python.exe -m pytest tests/test_breakout_probe.py -v
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
        "warehouse_breakout_probe_test",
        ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_breakout_probe_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


H4 = 4 * 3600


def _candles(start_ts_s: int, closes, highs=None, lows=None):
    """[ts_ms, o, h, l, c, v] 4h candles (default h/l = c +/- 1)."""
    out = []
    prev = closes[0]
    for i, c in enumerate(closes):
        ts_ms = (start_ts_s + i * H4) * 1000
        h = highs[i] if highs else c + 1.0
        low = lows[i] if lows else c - 1.0
        out.append([ts_ms, prev, h, low, c, 1000.0])
        prev = c
    return out


class _Providers:
    """Stub read-only providers — no write endpoint exists (LOG-ONLY)."""

    def __init__(self, *, ohlcv=None, market_data=None, balance=10_000.0, now=0):
        self._ohlcv = dict(ohlcv or {})  # {symbol: [candles...]}
        self._market_data = dict(market_data or {})
        self._balance = balance
        self.now = now

    def ohlcv(self, venue, symbol, timeframe, since_ms):
        rows = self._ohlcv.get(symbol, [])
        return [r for r in rows if r[0] >= int(since_ms)]

    def market_data(self, venue, symbol):
        return dict(self._market_data.get(symbol, {}))

    def balance(self):
        return self._balance

    def now_fn(self):
        return self.now


def _make_probe(wh, providers, symbols=("BTC/USDT:USDT",)):
    from core.agents.breakout_probe_agent import BreakoutProbeAgent

    return BreakoutProbeAgent(
        warehouse=wh,
        ohlcv_provider=providers.ohlcv,
        market_data_provider=providers.market_data,
        account_balance_provider=providers.balance,
        now_fn=providers.now_fn,
        venue="bybit",
        symbols=symbols,
    )


def _breakout_tape(n_flat=400, base=100.0, last_close=110.0):
    """Flat channel then a final bar closing above/below it."""
    return [base] * n_flat + [last_close]


# ── Frozen Codex constants ───────────────────────────────────────────────
def test_frozen_codex_constants():
    from core.agents import breakout_probe_agent as m

    assert m.WINDOW_BARS == 360  # 60 days of 4h bars
    assert m.STOP_ATR == 2.2  # confirmed by the 16:19 --finalize-only re-run
    assert m.REWARD_RISK == 3.0
    assert m.MAX_HOLD_BARS == 126  # 504h at 4h
    assert m.RESOLVER_HORIZON_BARS == 127  # entry-bar-inclusive reference scan
    assert m.SCORE_PEN_SCALE == 0.02  # FROZEN pre-outcome (cache median 0.0137)
    assert m.TIMEFRAME == "4h"
    assert set(m.SYMBOLS) == {
        "BTC/USDT:USDT",
        "ETH/USDT:USDT",
        "SOL/USDT:USDT",
        "XRP/USDT:USDT",
        "BNB/USDT:USDT",
        "ADA/USDT:USDT",
        "DOGE/USDT:USDT",
        "LINK/USDT:USDT",
        "AVAX/USDT:USDT",
        "DOT/USDT:USDT",
    }
    # the spec-confirmation provenance (16:19 --finalize-only re-run, 9/9
    # stable cells) must be recorded in the module — honesty is load-bearing
    src = (ROOT / "core" / "agents" / "breakout_probe_agent.py").read_text(encoding="utf-8")
    assert "finalize-only" in src and "9/9" in src


# ── Pure signal math ─────────────────────────────────────────────────────
def test_breakout_signal_long_uses_shifted_channel():
    from core.agents.breakout_probe_agent import breakout_signal_last

    candles = _candles(0, _breakout_tape())
    sig, edge = breakout_signal_last(candles, 360)
    assert sig == 1
    assert edge == pytest.approx(101.0)  # prior-360 max HIGH (100 + 1), signal bar excluded


def test_breakout_signal_short():
    from core.agents.breakout_probe_agent import breakout_signal_last

    candles = _candles(0, _breakout_tape(last_close=88.0))
    sig, edge = breakout_signal_last(candles, 360)
    assert sig == -1
    assert edge == pytest.approx(99.0)  # prior-360 min LOW (100 - 1)


def test_breakout_signal_inside_channel_or_short_history():
    from core.agents.breakout_probe_agent import breakout_signal_last

    inside = _candles(0, _breakout_tape(last_close=100.5))  # within [99, 101]
    assert breakout_signal_last(inside, 360) == (0, None)
    short_hist = _candles(0, [100.0] * 300 + [110.0])  # < 360 prior bars
    assert breakout_signal_last(short_hist, 360) == (0, None)


def test_score_frozen_monotone_varies():
    from core.agents.breakout_probe_agent import breakout_score

    assert breakout_score(0.01) == pytest.approx(math.tanh(0.01 / 0.02))
    assert breakout_score(0.05) > breakout_score(0.01)
    assert len({breakout_score(p) for p in (0.003, 0.0137, 0.05)}) == 3
    assert breakout_score(None) == 0.0


# ── Lifecycle ────────────────────────────────────────────────────────────
def _entered_probe(wh, closes, **prov_kw):
    start = 1_000_000_000
    p = _Providers(
        ohlcv={"BTC/USDT:USDT": _candles(start, closes)},
        market_data={"BTC/USDT:USDT": {"funding_rate": 0.0001}},
        now=start + len(closes) * H4,
        **prov_kw,
    )
    probe = _make_probe(wh, p)
    probe.tick()
    return probe, p, start


def test_long_entry_writes_decision_and_probe_rows(wh):
    from core.agents.tsmom_probe_agent import wilder_atr_last

    closes = _breakout_tape()
    _, p, start = _entered_probe(wh, closes)

    rows = wh.query("SELECT * FROM shadow_decisions WHERE model_version='breakout_60d_4h_v1'")
    assert len(rows) == 1
    r = rows[0]
    assert r["side"] == "buy"
    assert r["label_status"] == "PENDING"
    assert r["timeframe"] == "4h"
    assert r["horizon_bars"] == 127
    assert r["venue"] == "bybit"
    entry, sl, tp = r["entry_px"], r["sl_px"], r["tp_px"]
    assert entry == pytest.approx(110.0)
    atrv = wilder_atr_last(_candles(start, closes), 14)
    assert sl == pytest.approx(entry - 2.2 * atrv)  # 2.2x ATR stop
    assert tp - entry == pytest.approx(3.0 * (entry - sl), rel=1e-9)  # 3R target

    pr = wh.query("SELECT * FROM shadow_breakout_probe")[0]
    assert pr["side"] == "buy"
    assert pr["channel_edge"] == pytest.approx(101.0)
    assert pr["penetration"] == pytest.approx((110.0 - 101.0) / 101.0)
    assert pr["score"] == pytest.approx(math.tanh(pr["penetration"] / 0.02))
    assert pr["atr_entry"] == pytest.approx(atrv)
    assert pr["risk_frac"] == pytest.approx(0.01)  # Codex risk model
    assert pr["units"] == pytest.approx(0.01 * 10_000.0 / (2.2 * atrv))
    assert pr["notional_usd"] == pytest.approx(pr["units"] * entry)


def test_short_entry_mirrors_geometry(wh):
    closes = _breakout_tape(last_close=88.0)
    _entered_probe(wh, closes)

    r = wh.query("SELECT * FROM shadow_decisions WHERE model_version='breakout_60d_4h_v1'")[0]
    assert r["side"] == "sell"
    entry, sl, tp = r["entry_px"], r["sl_px"], r["tp_px"]
    assert tp < entry < sl
    assert entry - tp == pytest.approx(3.0 * (sl - entry), rel=1e-9)
    pr = wh.query("SELECT * FROM shadow_breakout_probe")[0]
    assert pr["channel_edge"] == pytest.approx(99.0)
    assert pr["penetration"] == pytest.approx((99.0 - 88.0) / 99.0)


def test_inside_channel_and_short_history_no_entry(wh):
    _entered_probe(wh, _breakout_tape(last_close=100.5))  # inside channel
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0
    assert wh.query("SELECT COUNT(*) n FROM shadow_breakout_probe")[0]["n"] == 0


def test_no_double_entry_while_open_then_reentry_after_stop(wh):
    closes = _breakout_tape()
    probe, p, start = _entered_probe(wh, closes)
    row = wh.query("SELECT * FROM shadow_breakout_probe")[0]

    # next bar continues higher -> still a breakout state, but occupied
    ext = list(closes) + [111.0]
    p._ohlcv["BTC/USDT:USDT"] = _candles(start, ext)
    p.now = start + len(ext) * H4
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_breakout_probe")[0]["n"] == 1
    # ... and the intra-hold MTM path was logged for that bar
    mtm = wh.query("SELECT * FROM shadow_breakout_mtm ORDER BY bar_ts")
    assert len(mtm) == 1
    assert mtm[0]["unrealized_ret"] == pytest.approx((111.0 - 110.0) / 110.0)

    # a bar wicking through the stop hints stop_loss and frees the slot
    ext.append(111.0)
    candles = _candles(start, ext)
    candles[-1][3] = row["sl_px"] - 0.5
    p._ohlcv["BTC/USDT:USDT"] = candles
    p.now = start + len(ext) * H4
    probe.tick()
    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_breakout_probe WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]
    assert hint["closed_hint_reason"] == "stop_loss"

    # a LATER bar breaking the (now higher) channel again -> new position
    ext.append(115.0)
    candles = _candles(start, ext)
    candles[-2][3] = row["sl_px"] - 0.5  # keep the historical wick
    p._ohlcv["BTC/USDT:USDT"] = candles
    p.now = start + len(ext) * H4
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_breakout_probe")[0]["n"] == 2


def test_time_exit_hint_after_127_bars(wh):
    closes = _breakout_tape()
    probe, p, start = _entered_probe(wh, closes)
    row = wh.query("SELECT * FROM shadow_breakout_probe")[0]
    entry = row["entry_px"]

    # 128 flat bars inside both barriers (sl ~ entry-6, tp ~ entry+18)
    ext = closes + [entry + 0.2 * ((i % 3) - 1) for i in range(128)]
    p._ohlcv["BTC/USDT:USDT"] = _candles(start, ext)
    p.now = start + len(ext) * H4
    probe.tick()

    hint = wh.query(
        "SELECT closed_hint_ts, closed_hint_reason FROM shadow_breakout_probe WHERE proposal_id=?",
        (row["proposal_id"],),
    )[0]
    assert hint["closed_hint_reason"] == "time"
    assert hint["closed_hint_ts"] == row["signal_bar_ts"] + 127 * H4
    n_mtm = wh.query(
        "SELECT COUNT(*) n FROM shadow_breakout_mtm WHERE proposal_id=?", (row["proposal_id"],)
    )[0]["n"]
    assert n_mtm == 127


def test_funding_accrues_once_per_8h_bucket(wh):
    closes = _breakout_tape()
    probe, p, start = _entered_probe(wh, closes)
    pid = wh.query("SELECT proposal_id FROM shadow_breakout_probe")[0]["proposal_id"]

    p.now = start + len(closes) * H4 + 60
    probe.tick()
    probe.tick()  # same bucket -> booked once
    frs = wh.query(
        "SELECT realized_funding_rate_sum s FROM shadow_breakout_probe WHERE proposal_id=?", (pid,)
    )[0]["s"]
    assert frs == pytest.approx(0.0001)
    p.now += 8 * 3600
    probe.tick()
    frs = wh.query(
        "SELECT realized_funding_rate_sum s FROM shadow_breakout_probe WHERE proposal_id=?", (pid,)
    )[0]["s"]
    assert frs == pytest.approx(0.0002)


def test_resolver_funding_provider_reads_breakout_probe(wh):
    closes = _breakout_tape()
    _entered_probe(wh, closes)
    pid = wh.query("SELECT proposal_id FROM shadow_breakout_probe")[0]["proposal_id"]
    wh._conn().execute(
        "UPDATE shadow_breakout_probe SET realized_funding_rate_sum=0.0077 WHERE proposal_id=?",
        (pid,),
    )
    wh._conn().commit()

    spec = importlib.util.spec_from_file_location(
        "resolve_shadow_outcomes_breakout_test", ROOT / "scripts" / "resolve_shadow_outcomes.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    provider = mod._build_probe_funding_provider(wh)
    assert provider({"proposal_id": pid}) == pytest.approx(0.0077)
    assert provider({"proposal_id": "bk-nonexistent"}) == 0.0


# ── Structure ────────────────────────────────────────────────────────────
def test_structural_log_only_no_order_path():
    src = (ROOT / "core" / "agents" / "breakout_probe_agent.py").read_text(encoding="utf-8")
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
    assert {"shadow_breakout_probe", "shadow_breakout_mtm"} <= tables


def test_tick_never_raises_on_provider_errors(wh):
    class _Broken:
        def ohlcv(self, *a):
            raise RuntimeError("venue down")

        def market_data(self, *a):
            raise RuntimeError("venue down")

        def balance(self):
            raise RuntimeError("venue down")

    b = _Broken()
    from core.agents.breakout_probe_agent import BreakoutProbeAgent

    probe = BreakoutProbeAgent(
        warehouse=wh,
        ohlcv_provider=b.ohlcv,
        market_data_provider=b.market_data,
        account_balance_provider=b.balance,
        now_fn=lambda: 1_000_000_000,
    )
    stats = probe.tick()
    assert stats["entered"] == 0
