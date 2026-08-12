"""BreakoutProbeAgent — LOG-ONLY forward paper test of Codex's breakout_60d winner.

THIS IS NOT A PIPELINE GO — READ THIS BEFORE READING ANY RESULT.

Textbook trend/breakout is a REFUTED family on the refuted-families-ledger
(.claude/skills/refuted-families-ledger/SKILL.md): 8 strategies x 5 majors on
an independent toolchain went 0/40 OOS (2026-06-13), and donchian_breakout
scored F in the Codex project's own first 6-month sweep. The Codex deep run
(research/deep_futures_research.py, reports/DEEP_FUTURES_RESEARCH.md) is the
strongest EXTERNAL evidence yet for the family — 5-6 years x 10 markets,
survives 2x costs (100% cost survival), and 9/9 parameter-neighborhood cells
stable — BUT it does NOT meet our reopen bar:
  (a) the winner was selected ON holdout metrics across 20 candidates — the
      holdout is burned, and a flat 6-point trial penalty is not DSR/PBO
      multiplicity control;
  (b) Codex's OWN block-bootstrap Monte Carlo fails our frozen
      capital-preservation gates: P(positive) = 91.5% < the 0.95 floor and
      maxDD p95 = 42.5% > the 0.25 cap.
Expected outcome: NO-PROMOTE at the frozen gate. The probe exists because
Codex's own creation gate requires forward paper trading before any live
execution and the owner directed implementing that recommendation.

WIN-RATE WARNING: this is a 3:1 R:R trend system — its backtested win rate is
~30-35% BY DESIGN (Codex full-history per-market WR 23-41%). That conflicts
with the owner's standing >=65% WR-floor preference: even a gate-passing
result could never be promoted into the accuracy-band lane and would need its
own explicit owner decision.

STRATEGY (extracted verbatim from the Codex sources — this probe forward
tests THEIR configuration, not our reinterpretation):
  deep_futures_research.py::breakout_signal(60) + candidates() breakout_60d,
  execution semantics from research/futures_backtest.py::backtest().
- channel: prior 60-day rolling max of HIGH / min of LOW, SHIFTED one bar
  (the signal bar's own extremes are excluded) = 360 bars at 4h
- LONG when close > prior-360-bar max high; SHORT when close < min low
- no volume filter (that is the separate breakout_20d_volume candidate)
- stop = entry -/+ 2.2 x ATR(14, Wilder, taken at the signal bar);
  target = entry +/- 3R (rewardRisk = 3.0)
- max hold 504h = 126 4h-bars; the reference enters at the bar AFTER the
  signal bar and checks barriers on that entry bar through the time exit at
  the close of entry+126 — an entry-bar-INCLUSIVE scan of 127 bars, so the
  decision rows carry horizon_bars = 127
- fill: the reference fills at the next bar's open with slippage; the probe
  logs entry at the SIGNAL BAR CLOSE — the observable real-time equivalent —
  and the resolver's open-slippage stands in for the reference's entry slip
- markets: the 10 Codex majors (Bybit USDT linear perps), 4h bars
- one position per symbol, no overlap ("no overlapping trades per
  strategy/market"), re-entry earliest one bar after the exit bar
Spec provenance: candidates() was edited after the 15:58 run; the 2026-07-11
16:19 --finalize-only re-run confirms (stop_atr=2.2, reward_risk=3.0) as the
real winner config — 9/9 neighborhood cells stable around it, and the chosen
center is NOT the neighborhood's best cell (1.7/3.0 shows PF 1.21), i.e. not
perched on an overfit peak.

SIZING IS NOTATIONAL ONLY: 1% equity risk per trade with the 2x max-notional
cap (the shared Codex risk model, imported from tsmom_probe_agent). No sizing
hook, no leverage, no order exists anywhere in this module.

FROZEN pre-outcome discriminating score (the AUC promotion gate is
un-computable without one): score = tanh(penetration / SCORE_PEN_SCALE) where
penetration = |close - channel_edge| / channel_edge and SCORE_PEN_SCALE =
0.02, frozen NOW from the Codex cache distribution (n=1,604 signal bars:
median 0.0137, mean 0.0211, p75 0.028) BEFORE any outcome exists. NEVER
re-tune it after outcomes accumulate — a new score is a new pre-registration.

LOG-ONLY (charter, non-negotiable): this class holds ONLY read-only providers
(OHLCV / market data / balance) plus the warehouse. It has no reference to
any order path and is structurally incapable of placing an order. Promotion —
if ever — only via the frozen core/promotion_gate.py thresholds on >= 30
RESOLVED forward events plus an explicit owner sign-off. Never here.

Realized after-cost economics are produced by the vetted keystone resolver
(core/shadow_resolver.py) off the shadow_decisions rows this probe writes.
This module performs NO PnL math on outcomes — custom PnL math in a probe is
a bug. closed_hint fields are OCCUPANCY BOOKKEEPING mirroring the resolver's
SL-first barrier logic; shadow_outcomes stays the only readable outcome. Per
the TP-probe precedent (core/agents/tp_probe_agent.py): never read a win- or
hit-rate here without the resolved shadow_outcomes.net_pnl next to it.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Callable, Optional, Tuple

from core.agents.probe_common import (
    ATR_LEN,
    RISK_PCT,
    accrue_funding,
    codex_position_units,
    ensure_schema,
    eval_gate,
    monitor_open_barriers,
    probe_tick,
    wilder_atr_last,
    write_entry_pair,
)

# ── Frozen Codex constants (deep_futures_research.py, confirmed 16:19 re-run) ─
# Editing any of these is a NEW pre-registration, not a tweak. Pinned by
# tests/test_breakout_probe.py::test_frozen_codex_constants.
SYMBOLS = (
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
)
TIMEFRAME = "4h"
BAR_S = 4 * 3600
WINDOW_BARS = 360  # 60 days of 4h bars (bars_for_days(df, 60))
STOP_ATR = 2.2  # stop = entry -/+ 2.2 x ATR(14) at the signal bar
REWARD_RISK = 3.0  # target = entry +/- 3R
MAX_HOLD_BARS = 126  # 504h at 4h (StrategySpec max_hold_hours=504)
# Reference scan is entry-bar-INCLUSIVE: barriers are checked on the entry bar
# (the bar after the signal bar) and the time exit lands at the close of
# entry+126 — 127 forward bars after the signal bar. See module header.
RESOLVER_HORIZON_BARS = MAX_HOLD_BARS + 1
SCORE_PEN_SCALE = 0.02  # FROZEN pre-outcome (cache median 0.0137, n=1,604)

# shadow_decisions.model_version for this probe — importable (e.g. by
# scripts/gate_status.py) so a probe rename can never silently desync a reporter.
BREAKOUT_60D_MODEL_VERSION = "breakout_60d_4h_v1"


# ── Pure signal math ─────────────────────────────────────────────────────────
def breakout_signal_last(candles, window: int) -> Tuple[int, Optional[float]]:
    """(signal, channel_edge) on the LAST candle — the exact Codex rule.

    +1 when close > max HIGH of the prior ``window`` bars (signal bar
    excluded — the reference shifts the channel by one bar); -1 when close <
    min LOW of them; else 0. edge is the broken channel boundary (the frozen
    score's input), None when flat or history is short."""
    if window <= 0 or len(candles) < window + 1:
        return 0, None
    prior = candles[-(window + 1) : -1]
    try:
        upper = max(float(c[2]) for c in prior)
        lower = min(float(c[3]) for c in prior)
        close = float(candles[-1][4])
    except (IndexError, TypeError, ValueError):
        return 0, None
    if close > upper:
        return 1, upper
    if close < lower:
        return -1, lower
    return 0, None


def breakout_score(penetration) -> float:
    """FROZEN pre-outcome discriminating score: tanh(penetration / 0.02).
    Pre-specified before any outcome exists so the AUC >= 0.60 promotion gate
    is computable; an honest AUC FAIL is a legitimate NO-PROMOTE outcome."""
    try:
        return math.tanh(abs(float(penetration)) / SCORE_PEN_SCALE)
    except (TypeError, ValueError):
        return 0.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_breakout_probe (
    proposal_id                TEXT PRIMARY KEY,
    symbol                     TEXT,
    venue                      TEXT,
    side                       TEXT,    -- 'buy' | 'sell'
    signal_bar_ts              INTEGER, -- open ts of the signal bar (= decision ts)
    entry_px                   REAL,    -- signal-bar close (see fill note in header)
    channel_edge               REAL,    -- ┐ as-of-entry signal inputs:
    penetration                REAL,    -- │ written once, never re-derived
    atr_entry                  REAL,    -- ┘
    sl_px                      REAL,
    tp_px                      REAL,
    horizon_bars               INTEGER, -- 127 (entry-bar-inclusive scan)
    risk_frac                  REAL,    -- 0.01 (Codex risk model, notational)
    notional_usd               REAL,
    units                      REAL,
    score                      REAL,    -- FROZEN tanh(penetration / 0.02)
    realized_funding_rate_sum  REAL,
    last_funding_bucket        INTEGER,
    closed_hint_ts             INTEGER, -- occupancy bookkeeping ONLY (see header)
    closed_hint_reason         TEXT,    -- stop_loss | take_profit | time
    created_ts                 INTEGER
);
CREATE INDEX IF NOT EXISTS idx_breakout_probe_symbol
    ON shadow_breakout_probe(symbol);

CREATE TABLE IF NOT EXISTS shadow_breakout_mtm (
    proposal_id     TEXT,
    bar_ts          INTEGER,
    mark_px         REAL,
    unrealized_ret  REAL,     -- side-signed: >0 = position in profit
    PRIMARY KEY (proposal_id, bar_ts)
);
"""


class BreakoutProbeAgent:
    """Per-symbol 60d-channel breakout evaluator + log-only proposer. See header."""

    name = "BreakoutProbeAgent"
    model_version = BREAKOUT_60D_MODEL_VERSION

    def __init__(
        self,
        *,
        warehouse,
        ohlcv_provider: Callable[[str, str, str, int], list],
        market_data_provider: Callable[[str, str], dict],
        account_balance_provider: Callable[[], float],
        now_fn: Callable[[], float] = time.time,
        venue: str = "bybit",
        symbols=SYMBOLS,
    ):
        self._wh = warehouse
        self._ohlcv = ohlcv_provider
        self._market_data = market_data_provider
        self._balance = account_balance_provider
        self._now = now_fn
        self._venue = venue
        self._symbols = tuple(symbols)
        # expected-bar no-op guards (probe_common eval_gate / monitor):
        # (symbol, tf) -> open ts of the last bar already evaluated/fetched.
        self._bar_seen: dict = {}
        self._mon_seen: dict = {}
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        ensure_schema(self._wh, _SCHEMA)

    # ── public tick ──────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One probe cycle: update MTM/hints on open holds FIRST, then
        evaluate the latest closed 4h bar per symbol. NEVER raises upward."""
        units = [(symbol, (symbol,)) for symbol in self._symbols]
        return probe_tick(self, units, log_tag="BreakoutProbe")

    # ── entry evaluation ─────────────────────────────────────────────────
    def _eval_entry(self, symbol: str, now: int) -> int:
        gate = eval_gate(
            self, symbol=symbol, tf=TIMEFRAME, bar_s=BAR_S,
            fetch_bars=WINDOW_BARS + ATR_LEN + 8,
            min_bars=WINDOW_BARS + 1,
            last_sql="SELECT signal_bar_ts, closed_hint_ts, horizon_bars "
                     "FROM shadow_breakout_probe WHERE symbol=? "
                     "ORDER BY signal_bar_ts DESC LIMIT 1",
            last_params=(symbol,),
            hold_col="horizon_bars",
            now=now,
        )
        if gate is None:
            return 0
        candles, latest_ts = gate

        sig, edge = breakout_signal_last(candles, WINDOW_BARS)
        atrv = wilder_atr_last(candles, ATR_LEN)
        if sig == 0 or edge is None or edge <= 0 or atrv is None or atrv <= 0:
            return 0
        entry_px = float(candles[-1][4])
        risk_distance = STOP_ATR * atrv
        sl_px = entry_px - sig * risk_distance
        tp_px = entry_px + sig * risk_distance * REWARD_RISK
        if sl_px <= 0 or tp_px <= 0:
            return 0
        penetration = abs(entry_px - edge) / edge
        try:
            equity = float(self._balance() or 0.0)
        except Exception:
            equity = 0.0
        units = codex_position_units(equity, entry_px, risk_distance)
        notional = units * entry_px
        side = "buy" if sig > 0 else "sell"
        pid = f"bk-{uuid.uuid4().hex[:10]}"

        self._write_entry(
            proposal_id=pid,
            symbol=symbol,
            side=side,
            signal_bar_ts=latest_ts,
            entry_px=entry_px,
            channel_edge=float(edge),
            penetration=penetration,
            atr_entry=float(atrv),
            sl_px=sl_px,
            tp_px=tp_px,
            notional_usd=notional,
            units=units,
            score=breakout_score(penetration),
            now=now,
        )
        return 1

    # ── monitoring: per-bar MTM + occupancy hints + funding ──────────────
    def _monitor_open(self, now: int) -> int:
        return monitor_open_barriers(
            self, now,
            probe_table="shadow_breakout_probe",
            mtm_table="shadow_breakout_mtm",
            rows_sql=(
                "SELECT proposal_id, symbol, side, signal_bar_ts, entry_px, sl_px, "
                "tp_px, horizon_bars, last_funding_bucket, realized_funding_rate_sum "
                "FROM shadow_breakout_probe WHERE closed_hint_ts IS NULL"
            ),
            frame_of=lambda r: (TIMEFRAME, BAR_S, int(r["horizon_bars"])),
        )

    def _accrue_funding(self, row: dict, now: int) -> None:
        """Book the current funding print once per 8h settlement bucket while
        the position is open. Missing funding is never guessed."""
        accrue_funding(self._wh, table="shadow_breakout_probe",
                       market_data=self._market_data, venue=self._venue,
                       row=row, now=now)

    # ── warehouse writes ─────────────────────────────────────────────────
    def _write_entry(
        self,
        *,
        proposal_id,
        symbol,
        side,
        signal_bar_ts,
        entry_px,
        channel_edge,
        penetration,
        atr_entry,
        sl_px,
        tp_px,
        notional_usd,
        units,
        score,
        now,
    ) -> None:
        """The entry's TWO rows, written atomically (probe_common.
        write_entry_pair): the shadow_decisions row the keystone resolver
        replays into shadow_outcomes (SL-first, fees + slippage,
        censoring-guarded time exit; sim_pnl stays NULL — the resolver owns
        the after-cost net), and the probe row holding the occupancy slot.
        Split writes could orphan the decision — see write_entry_pair."""
        row = {
            "proposal_id": proposal_id,
            "symbol": symbol,
            "venue": self._venue,
            "side": side,
            "signal_bar_ts": int(signal_bar_ts),
            "entry_px": float(entry_px),
            "channel_edge": float(channel_edge),
            "penetration": float(penetration),
            "atr_entry": float(atr_entry),
            "sl_px": float(sl_px),
            "tp_px": float(tp_px),
            "horizon_bars": int(RESOLVER_HORIZON_BARS),
            "risk_frac": float(RISK_PCT),
            "notional_usd": float(notional_usd),
            "units": float(units),
            "score": float(score),
            "realized_funding_rate_sum": 0.0,
            "last_funding_bucket": None,
            "closed_hint_ts": None,
            "closed_hint_reason": None,
            "created_ts": int(now),
        }
        # Plain INSERT inside ONE transaction: an entry row is written exactly
        # once, never replaced, and never without its decision row.
        write_entry_pair(
            self._wh,
            decision=dict(
                ts=signal_bar_ts, model_version=self.model_version,
                symbol=symbol, side=side, agent_id=self.name,
                proposal_id=proposal_id, notional=notional_usd,
                entry_px=entry_px, sl_px=sl_px, tp_px=tp_px,
                venue=self._venue, timeframe=TIMEFRAME,
                horizon_bars=RESOLVER_HORIZON_BARS,
            ),
            probe_table="shadow_breakout_probe",
            probe_row=row,
        )
