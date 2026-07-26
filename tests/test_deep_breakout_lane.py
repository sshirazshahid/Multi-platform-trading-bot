"""TDD spec for the ACTIVE PAPER deep-breakout lane (core/deep_breakout_lane.py).

Owner directive 2026-07-11: "start trading aggressively" — this lane places
ACTIVE PAPER orders (sim fills via sim_execution) for the Codex deep_breakout
strategy, unlike the log-only core/agents/breakout_probe_agent.py which stays
untouched and keeps collecting frozen-gate evidence in parallel.

Every test pins a BINDING rail:
1. PAPER-ONLY hard gate — the lane refuses to construct AND to tick unless
   config.DRY_RUN is True. Going live requires an owner decision + code change.
2. Cohort separation — every order carries strategy_family='deep_breakout';
   the goal/band reporting (report_goal_progress, dashboard THIS-BOOT) excludes
   the family so the 65-67% WR goal metric is not contaminated by this
   ~33%-WR-by-design 3R lane.
3. Sizing — researched 1% equity risk / 2x-equity notional cap, clamped by
   max 4 concurrent lane positions, lane gross <= 6% equity, correlation
   sizing, and the charter §2 12% portfolio exposure check ON TOP.
4. Venues binance+bybit only (bitget excluded: Codex confidence 44.7 on ~100
   days of usable 4h history); one position per BASE across venues.
5. Existing safety rails unmodified — entries via order_manager.open_position
   (SL/TP through the normal path), BTC vol pause honored fail-closed.
6. Signal timing — CLOSED 4h bars only, one evaluation per bar, re-entry
   earliest one bar after the exit bar; signal math IS the tested probe's.

Run: venv/Scripts/python.exe -m pytest tests/test_deep_breakout_lane.py -v
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

H4 = 4 * 3600
# 4h-grid-aligned start so bar opens land on the real exchange epoch grid
START = (1_000_000_000 // H4 + 1) * H4


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


def _breakout_tape(n_flat=400, base=100.0, last_close=110.0):
    return [base] * n_flat + [last_close]


# ── Test doubles ─────────────────────────────────────────────────────────
class _FakePos:
    _n = 0

    def __init__(self, strategy, symbol="BTC/USDT:USDT", exchange="binance",
                 size=1.0, entry_price=100.0, open_time=0.0, side="buy"):
        _FakePos._n += 1
        self.id = f"fp{_FakePos._n}"
        self.strategy = strategy
        self.symbol = symbol
        self.exchange = exchange
        self.size = size
        self.entry_price = entry_price
        self.open_time = open_time
        self.side = side
        self.market_type = "futures"


class _FakeTracker:
    def __init__(self, positions=None):
        self.positions = list(positions or [])

    def get_open(self, exchange=None, symbol=None):
        return list(self.positions)


class _FakeRisk:
    def __init__(self, can_add=True, size_multiplier=1.0):
        self.can_add = can_add
        self.size_multiplier = size_multiplier

    def check_correlation(self, symbol, open_positions, balance):
        return {"can_add": self.can_add, "size_multiplier": self.size_multiplier}


class _FakeOM:
    """Captures open/close calls; never touches an exchange."""

    def __init__(self, open_result="pos"):
        self.opens = []
        self.closes = []
        self.open_result = open_result
        self.last_open_reject = None

    def open_position(self, exchange, symbol, side, market_type, **kw):
        self.opens.append({"exchange": exchange, "symbol": symbol, "side": side,
                           "market_type": market_type, **kw})
        return self.open_result

    def close_position(self, exchange, position, reason, price=None, **kw):
        self.closes.append({"exchange": exchange, "position": position,
                            "reason": reason, "price": price})
        return True


class _FakeWh:
    """Answers only the lane's MAX(ts_exit) re-entry query."""

    def __init__(self):
        self.last_exit = {}  # base -> ts_exit (epoch s)

    def query(self, sql, params=()):
        base = str(params[1]).split("/")[0] if len(params) > 1 else ""
        return [{"m": self.last_exit.get(base)}]


def _mk_lane(tmp_path, monkeypatch, *, ohlcv, om=None, tracker=None, risk=None,
             equity=10_000.0, wh=None, pause=None, cfg=None,
             now=START + 401 * H4, exchanges=None, entry_authorizer=None):
    import config
    import core.kill_switch as kill_switch

    monkeypatch.setattr(config, "DRY_RUN", True, raising=False)
    if kill_switch.KILL_SWITCH_PATH == Path("data/KILL_SWITCH"):
        monkeypatch.setattr(
            kill_switch, "KILL_SWITCH_PATH", tmp_path / "test_KILL_SWITCH"
        )
        monkeypatch.setattr(kill_switch._default, "_last", None)
        monkeypatch.setattr(kill_switch._default, "_path", None)
    from core.deep_breakout_lane import DeepBreakoutLane
    from core.entry_policy import EntryAuthorization

    base_cfg = {
        "enabled": True,
        "venues": ("binance", "bybit"),
        "symbols": ("BTC/USDT:USDT",),
        "risk_pct": 1.0,
        "max_notional_multiple": 2.0,
        "max_concurrent": 4,
        "lane_exposure_cap_pct": 6.0,
        "hard_max_loss_pct": 8.0,
        "tick_sec": 300,
    }
    base_cfg.update(cfg or {})
    state = {"now": now}
    lane = DeepBreakoutLane(
        exchanges=exchanges if exchanges is not None else {"binance": "EX_BINANCE", "bybit": "EX_BYBIT"},
        order_manager=om if om is not None else _FakeOM(),
        tracker=tracker if tracker is not None else _FakeTracker(),
        risk=risk if risk is not None else _FakeRisk(),
        equity_provider=lambda: equity,
        ohlcv_provider=ohlcv,
        warehouse=wh if wh is not None else _FakeWh(),
        entry_pause_check=pause,
        entry_authorizer=entry_authorizer or (
            lambda: EntryAuthorization(True, "test_approved", "APPROVED_PAPER", "deep_breakout")
        ),
        cfg=base_cfg,
        now_fn=lambda: state["now"],
        state_path=tmp_path / "lane_state.json",
    )
    return lane, state


def _ohlcv_map(data):
    """Provider closing over {(venue, symbol): candles}; venue-keyed."""

    def provider(venue, symbol, timeframe, since_ms):
        rows = data.get((venue, symbol), [])
        return [r for r in rows if r[0] >= int(since_ms)]

    return provider


# ── Rail 1: PAPER-ONLY hard gate ─────────────────────────────────────────
def test_paper_gate_blocks_construction_off_paper(tmp_path, monkeypatch):
    import config
    from core.deep_breakout_lane import DeepBreakoutLane, PaperOnlyViolation

    monkeypatch.setattr(config, "DRY_RUN", False, raising=False)
    with pytest.raises(PaperOnlyViolation):
        DeepBreakoutLane(
            exchanges={}, order_manager=_FakeOM(), tracker=_FakeTracker(),
            risk=_FakeRisk(), equity_provider=lambda: 0.0,
            ohlcv_provider=lambda *a: [], warehouse=_FakeWh(),
            cfg={}, state_path=tmp_path / "s.json",
        )


def test_paper_gate_blocks_tick_after_mode_flip(tmp_path, monkeypatch):
    """Even a lane constructed in PAPER refuses to tick once DRY_RUN is False
    — the gate is re-checked at the entry point EVERY tick."""
    import config
    from core.deep_breakout_lane import PaperOnlyViolation

    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om)
    monkeypatch.setattr(config, "DRY_RUN", False, raising=False)
    with pytest.raises(PaperOnlyViolation):
        lane.tick()
    assert om.opens == []  # no order ever reached the order path


# ── Rail 6: signal math IS the tested probe implementation ───────────────
def test_signal_math_delegates_to_probe():
    from core import deep_breakout_lane as lane_mod
    from core.agents import breakout_probe_agent as probe_mod
    from core.agents import tsmom_probe_agent as tsmom_mod

    assert lane_mod.breakout_signal_last is probe_mod.breakout_signal_last
    assert lane_mod.wilder_atr_last is tsmom_mod.wilder_atr_last
    assert lane_mod.WINDOW_BARS == probe_mod.WINDOW_BARS == 360
    assert lane_mod.STOP_ATR == probe_mod.STOP_ATR == 2.2
    assert lane_mod.REWARD_RISK == probe_mod.REWARD_RISK == 3.0
    assert lane_mod.MAX_HOLD_BARS == probe_mod.MAX_HOLD_BARS == 126
    assert lane_mod.MAX_HOLD_SEC == 126 * H4  # 504h = 21d


def test_config_block_matches_researched_spec():
    import config
    from core.agents import breakout_probe_agent as probe_mod

    cfg = config.DEEP_BREAKOUT_LANE
    assert set(cfg["symbols"]) == set(probe_mod.SYMBOLS)  # the 10 validated majors
    assert tuple(cfg["venues"]) == ("binance", "bybit")
    assert "bitget" not in cfg["venues"]  # 44.7 confidence, ~100d usable history
    assert cfg["risk_pct"] == 1.0
    assert cfg["max_notional_multiple"] == 2.0  # < 2.5x charter clamp
    assert cfg["max_concurrent"] == 4
    assert cfg["lane_exposure_cap_pct"] == 6.0  # half the §2 12% portfolio cap
    assert cfg["hard_max_loss_pct"] == 8.0  # charter §2 Stop-Loss Guardian
    assert isinstance(cfg["enabled"], bool)


# ── Rails 2+3: entry geometry, cohort tag, researched sizing ─────────────
def test_long_entry_cohort_tag_and_geometry(tmp_path, monkeypatch):
    import config
    from core.agents.tsmom_probe_agent import wilder_atr_last

    closes = _breakout_tape()
    data = {("binance", "BTC/USDT:USDT"): _candles(START, closes)}
    om = _FakeOM()
    # unbind lane/notional/§2 caps to isolate the researched risk formula
    monkeypatch.setattr(config, "MAX_PORTFOLIO_EXPOSURE_PCT", 1000.0, raising=False)
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                       cfg={"lane_exposure_cap_pct": 1000.0,
                            "max_notional_multiple": 1000.0})
    stats = lane.tick()
    assert stats["entered"] == 1
    assert len(om.opens) == 1
    o = om.opens[0]
    assert o["strategy"] == "deep_breakout"  # cohort tag -> trades.strategy_family
    assert o["model_version"] == "deep_breakout_4h_v1"
    assert o["side"] == "buy"
    assert o["market_type"] == "futures"
    assert o["leverage"] == 1
    assert o["exchange"] == "EX_BINANCE"  # binance-first (confidence 78.8)
    atrv = wilder_atr_last(_candles(START, closes), 14)
    rd = 2.2 * atrv
    assert o["sl"] == pytest.approx(110.0 - rd)  # stop 2.2 x ATR(14)
    assert o["tp"] - 110.0 == pytest.approx(3.0 * rd, rel=1e-9)  # target 3R
    assert o["size"] == pytest.approx(10_000.0 * 0.01 / rd)  # 1% equity risk


def test_short_entry_mirrors_geometry(tmp_path, monkeypatch):
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape(last_close=88.0))}
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om)
    lane.tick()
    o = om.opens[0]
    assert o["side"] == "sell"
    assert o["tp"] < 88.0 < o["sl"]
    assert 88.0 - o["tp"] == pytest.approx(3.0 * (o["sl"] - 88.0), rel=1e-9)


def test_lane_exposure_cap_clamps_and_blocks(tmp_path, monkeypatch):
    closes = _breakout_tape()
    data = {("binance", "BTC/USDT:USDT"): _candles(START, closes)}

    # default caps: 6% of 10k = $600 lane gross; risk-sized notional (~$1.9k)
    # must clamp down to the $600 headroom
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om)
    lane.tick()
    assert om.opens[0]["size"] * 110.0 == pytest.approx(600.0)

    # an existing lane position consuming the full cap blocks the next entry
    held = _FakePos("deep_breakout", symbol="ETH/USDT:USDT", size=6.0,
                    entry_price=100.0)  # gross $600
    om2 = _FakeOM()
    lane2, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om2,
                        tracker=_FakeTracker([held]))
    lane2.tick()
    assert om2.opens == []


def test_max_concurrent_blocks_fifth_position(tmp_path, monkeypatch):
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    held = [_FakePos("deep_breakout", symbol=f"{b}/USDT:USDT", size=0.01,
                     entry_price=100.0) for b in ("ETH", "SOL", "XRP", "ADA")]
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                       tracker=_FakeTracker(held))
    lane.tick()
    assert om.opens == []


def test_portfolio_exposure_cap_applies_on_top(tmp_path, monkeypatch):
    """Charter §2: lane entries must respect the shared 12% gross-notional cap
    counting NON-lane positions too (risk_manager.exposure_breached)."""
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    mcp_pos = _FakePos("claude_portfolio", symbol="ETH/USDT:USDT", size=11.5,
                       entry_price=100.0)  # $1,150 = 11.5% of 10k equity
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                       tracker=_FakeTracker([mcp_pos]))
    lane.tick()
    assert om.opens == []  # +$600 would push gross to 17.5% > 12%


def test_correlation_sizing_applies(tmp_path, monkeypatch):
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                       risk=_FakeRisk(size_multiplier=0.5))
    lane.tick()
    assert om.opens[0]["size"] * 110.0 == pytest.approx(300.0)  # 600 * 0.5

    om2 = _FakeOM()
    lane2, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om2,
                        risk=_FakeRisk(can_add=False))
    lane2.tick()
    assert om2.opens == []


# ── Rail 4: venues + one position per base across venues ────────────────
def test_bybit_fallback_when_binance_has_no_data(tmp_path, monkeypatch):
    data = {("bybit", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om)
    lane.tick()
    assert om.opens[0]["exchange"] == "EX_BYBIT"


def test_one_position_per_base_across_venues(tmp_path, monkeypatch):
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    held = _FakePos("deep_breakout", symbol="BTC/USDT:USDT", exchange="bybit")
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                       tracker=_FakeTracker([held]))
    lane.tick()
    assert om.opens == []  # BTC already held on bybit -> no binance duplicate


def test_non_lane_position_on_base_does_not_block(tmp_path, monkeypatch):
    """One-per-base is a LANE rule; an mcp position on the base doesn't own
    the lane slot (the shared §2 cap still applies and is far from binding)."""
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    held = _FakePos("claude_portfolio", symbol="BTC/USDT:USDT", size=0.001,
                    entry_price=100.0)
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                       tracker=_FakeTracker([held]))
    lane.tick()
    assert len(om.opens) == 1


# ── Rail 6: closed bars only, one eval per bar, re-entry discipline ──────
def test_closed_bar_discipline_no_forming_bar_signal(tmp_path, monkeypatch):
    """The breakout close on the LAST bar must not act until that bar CLOSES."""
    closes = _breakout_tape()
    data = {("binance", "BTC/USDT:USDT"): _candles(START, closes)}
    om = _FakeOM()
    forming_now = START + 401 * H4 - 60  # final bar still forming
    lane, state = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                           now=forming_now)
    lane.tick()
    assert om.opens == []  # forming-bar look-ahead forbidden
    state["now"] = START + 401 * H4  # the signal bar has now closed
    lane.tick()
    assert len(om.opens) == 1


def test_one_evaluation_per_bar(tmp_path, monkeypatch):
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    om = _FakeOM(open_result=None)  # order path rejects (e.g. min-notional)
    lane, state = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om)
    lane.tick()
    lane.tick()  # same bar -> no second attempt even after a reject
    state["now"] += 300
    lane.tick()
    assert len(om.opens) == 1


def test_reentry_earliest_one_bar_after_exit_bar(tmp_path, monkeypatch):
    # bar 401 closes at 112 > prior-360-bar max high 111 -> fresh signal
    closes = _breakout_tape() + [112.0]
    wh = _FakeWh()
    om = _FakeOM()
    data = {("binance", "BTC/USDT:USDT"): _candles(START, closes)}
    # prior lane trade exited DURING bar 401 (the latest closed bar)
    wh.last_exit["BTC"] = START + 401 * H4 + 100
    lane, state = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                           wh=wh, now=START + 402 * H4)
    lane.tick()
    assert om.opens == []  # signal bar == exit bar -> blocked

    # one full bar later: 115 > new channel high 113 -> re-entry allowed
    closes.append(115.0)
    data[("binance", "BTC/USDT:USDT")] = _candles(START, closes)
    state["now"] = START + 403 * H4
    lane.tick()
    assert len(om.opens) == 1


# ── Rail 5: BTC vol pause honored (fail-closed), retry within the bar ────
def test_btc_vol_pause_blocks_then_retries_same_bar(tmp_path, monkeypatch):
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    om = _FakeOM()
    gate = {"paused": True}
    lane, state = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                           pause=lambda: (gate["paused"], "btc_vol"))
    lane.tick()
    assert om.opens == []
    gate["paused"] = False  # pause lifts while the signal bar is still current
    state["now"] += 300
    lane.tick()
    assert len(om.opens) == 1


def test_broken_pause_check_fails_closed(tmp_path, monkeypatch):
    data = {("binance", "BTC/USDT:USDT"): _candles(START, _breakout_tape())}
    om = _FakeOM()

    def _broken():
        raise RuntimeError("indicator cache down")

    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=_ohlcv_map(data), om=om,
                       pause=_broken)
    lane.tick()
    assert om.opens == []  # A2 convention: broken gate blocks the entry


# ── Max-hold: 126 bars, closed through the normal close path ────────────
def test_max_hold_close_after_126_bars(tmp_path, monkeypatch):
    now = START + 500 * H4
    expired = _FakePos("deep_breakout", symbol="ETH/USDT:USDT",
                       exchange="binance", open_time=now - 126 * H4 - 10)
    fresh = _FakePos("deep_breakout", symbol="SOL/USDT:USDT",
                     exchange="bybit", open_time=now - 10 * H4)
    not_ours = _FakePos("claude_portfolio", symbol="XRP/USDT:USDT",
                        exchange="binance", open_time=now - 1000 * H4)
    om = _FakeOM()
    lane, _ = _mk_lane(tmp_path, monkeypatch, ohlcv=lambda *a: [], om=om,
                       tracker=_FakeTracker([expired, fresh, not_ours]), now=now)
    stats = lane.tick()
    assert stats["max_hold_closed"] == 1
    assert len(om.closes) == 1
    assert om.closes[0]["position"] is expired
    assert om.closes[0]["reason"] == "max_hold"
    assert om.closes[0]["exchange"] == "EX_BINANCE"


# ── Identity predicate ───────────────────────────────────────────────────
def test_is_deep_breakout_position():
    from core.deep_breakout_lane import is_deep_breakout_position

    assert is_deep_breakout_position(_FakePos("deep_breakout"))
    assert is_deep_breakout_position(_FakePos("DEEP_BREAKOUT"))
    assert not is_deep_breakout_position(_FakePos("claude_portfolio"))
    assert not is_deep_breakout_position(_FakePos("tsmom"))
    assert not is_deep_breakout_position(_FakePos(None))
    assert not is_deep_breakout_position(object())


# ── Rail 2: cohort separation in the goal/band reporting ─────────────────
def _goal_report_mod():
    spec = importlib.util.spec_from_file_location(
        "report_goal_progress_db_lane_test", ROOT / "scripts" / "report_goal_progress.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _trades_conn(tmp_path):
    conn = sqlite3.connect(tmp_path / "wh.sqlite")
    conn.row_factory = sqlite3.Row
    conn.execute(
        """CREATE TABLE trades (
            id INTEGER PRIMARY KEY, ts_entry REAL, ts_exit REAL, realized_pnl REAL,
            partial_realized_pnl REAL, strategy_family TEXT,
            status TEXT, mode TEXT, market_type TEXT, decision_id TEXT)"""
    )
    import time as _t

    now = _t.time()
    rows = [
        (1, now - 3600, now - 60, 5.0, 0.0, "claude_portfolio", "CLOSED", "PAPER", "futures", "d1"),
        (2, now - 3600, now - 60, -7.0, 0.0, "deep_breakout", "CLOSED", "PAPER", "futures", "d2"),
    ]
    conn.executemany("INSERT INTO trades VALUES (?,?,?,?,?,?,?,?,?,?)", rows)
    conn.commit()
    return conn


def test_goal_report_excludes_deep_breakout_cohort(tmp_path):
    mod = _goal_report_mod()
    conn = _trades_conn(tmp_path)
    d = mod.directional_summary(conn)
    assert d["closed_trades"] == 1  # deep_breakout row NOT counted
    assert d["net_pnl"] == pytest.approx(5.0)
    cb = mod.current_boot_summary(conn, since_epoch=0)
    assert cb["closed_trades"] == 1
    assert cb["net_pnl"] == pytest.approx(5.0)


def test_dashboard_this_boot_excludes_deep_breakout():
    src = (ROOT / "dashboard.py").read_text(encoding="utf-8")
    # the THIS-BOOT cohort SQL must carry the family exclusion
    i = src.index("Current-boot cohort")
    block = src[i:i + 1500]
    assert "deep_breakout" in block


# ── Rail 5: wiring pins (order_manager + bot_engine choke points) ────────
def test_order_manager_wiring_pins():
    src = (ROOT / "core" / "order_manager.py").read_text(encoding="utf-8")
    # maker-first paper-entry interception excludes the lane (taker fidelity
    # to the researched fill model; waiting at the touch adverse-selects
    # breakout entries)
    assert '"deep_breakout" not in str(strategy or "").lower()' in src
    # monitor loop: lane positions keep SL/TP + the charter 8% guardian but
    # skip the scalp machinery (partial-TP/trailing/BE/staleness/age)
    assert "is_deep_breakout_position" in src
    assert "DEEP-BREAKOUT GUARDIAN" in src


def test_bot_engine_wiring_pins():
    src = (ROOT / "core" / "bot_engine.py").read_text(encoding="utf-8")
    # lane tick scheduled behind the flag, refused off-PAPER
    assert "_run_deep_breakout_lane" in src
    assert "PAPER-only hard gate" in src
    # position-monitor + portfolio-close guards: the lane owns its exit
    assert "_is_db_pos(p_local)" in src
    assert "_is_db_pos(p)" in src
    assert "_is_db_pos(target)" in src
