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

from loguru import logger

from core.agents.tsmom_probe_agent import (
    ATR_LEN,
    FUNDING_SETTLE_S,
    RISK_PCT,
    codex_position_units,
    wilder_atr_last,
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
    model_version = "breakout_60d_4h_v1"

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
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        conn = self._wh._conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    # ── public tick ──────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One probe cycle: update MTM/hints on open holds FIRST, then
        evaluate the latest closed 4h bar per symbol. NEVER raises upward."""
        stats = {"evaluated": 0, "entered": 0, "mtm_rows": 0}
        now = int(self._now())
        try:
            stats["mtm_rows"] = self._monitor_open(now)
        except Exception as e:
            logger.debug(f"[BreakoutProbe] monitor error: {e}")
        for symbol in self._symbols:
            try:
                stats["entered"] += self._eval_entry(symbol, now)
                stats["evaluated"] += 1
            except Exception as e:
                logger.debug(f"[BreakoutProbe] {symbol} eval error: {e}")
        return stats

    # ── entry evaluation ─────────────────────────────────────────────────
    def _eval_entry(self, symbol: str, now: int) -> int:
        candles = self._closed_candles(symbol, now)
        if len(candles) < WINDOW_BARS + 1:
            return 0  # insufficient history — never guess a signal
        latest_ts = int(candles[-1][0]) // 1000

        last = self._wh.query(
            "SELECT signal_bar_ts, closed_hint_ts, horizon_bars "
            "FROM shadow_breakout_probe WHERE symbol=? "
            "ORDER BY signal_bar_ts DESC LIMIT 1",
            (symbol,),
        )
        if last:
            r = last[0]
            hint_ts = r["closed_hint_ts"]
            # ONE position per symbol — the reference's no-overlap rule. The
            # position time-exits at the close of the bar opening at
            # signal_bar_ts + horizon_bars x 4h.
            time_free = int(r["signal_bar_ts"]) + (int(r["horizon_bars"]) + 1) * BAR_S
            if hint_ts is None and now < time_free:
                return 0  # occupied
            if latest_ts <= int(r["signal_bar_ts"]):
                return 0  # this bar was already acted on — never re-signal it
            if hint_ts is not None and latest_ts <= int(hint_ts):
                return 0  # re-entry earliest one bar AFTER the exit-hint bar

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

        self._write_decision_row(pid, symbol, side, latest_ts, entry_px, sl_px, tp_px, notional)
        self._write_probe_row(
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

    def _closed_candles(self, symbol: str, now: int) -> list:
        """CLOSED 4h bars, oldest-first; the forming bar is dropped so a
        signal never repaints."""
        need = WINDOW_BARS + ATR_LEN + 8
        since_ms = (now - need * BAR_S) * 1000
        raw = self._ohlcv(self._venue, symbol, TIMEFRAME, since_ms) or []
        out = []
        for c in raw:
            try:
                bar_ts = int(c[0]) // 1000
                close = float(c[4])
            except (IndexError, TypeError, ValueError):
                continue
            if bar_ts + BAR_S <= now and close > 0:
                out.append(c)
        out.sort(key=lambda c: c[0])
        return out

    # ── monitoring: per-bar MTM + occupancy hints + funding ──────────────
    def _monitor_open(self, now: int) -> int:
        rows = self._wh.query(
            "SELECT proposal_id, symbol, side, signal_bar_ts, entry_px, sl_px, "
            "tp_px, horizon_bars, last_funding_bucket, realized_funding_rate_sum "
            "FROM shadow_breakout_probe WHERE closed_hint_ts IS NULL"
        )
        written = 0
        conn = self._wh._conn()
        for r in rows:
            entry_ts = int(r["signal_bar_ts"])
            entry_px = float(r["entry_px"])
            is_buy = r["side"] == "buy"
            sl, tp = float(r["sl_px"]), float(r["tp_px"])
            cap_open = entry_ts + int(r["horizon_bars"]) * BAR_S
            hinted = False

            last = self._wh.query(
                "SELECT MAX(bar_ts) AS m FROM shadow_breakout_mtm WHERE proposal_id=?",
                (r["proposal_id"],),
            )
            since = (int(last[0]["m"]) + 1) if last and last[0]["m"] is not None else entry_ts + 1
            if since <= min(now, cap_open):
                candles = self._ohlcv(self._venue, r["symbol"], TIMEFRAME, since * 1000) or []
                for c in sorted(candles, key=lambda c: c[0]):
                    try:
                        bar_ts = int(c[0]) // 1000
                        high, low, mark = float(c[2]), float(c[3]), float(c[4])
                    except (IndexError, TypeError, ValueError):
                        continue
                    if bar_ts <= entry_ts or bar_ts > cap_open:
                        continue
                    if bar_ts + BAR_S > now:
                        continue  # forming/unclosed bar — no repaint
                    ur = (mark - entry_px) / entry_px if is_buy else (entry_px - mark) / entry_px
                    conn.execute(
                        "INSERT OR REPLACE INTO shadow_breakout_mtm "
                        "(proposal_id, bar_ts, mark_px, unrealized_ret) "
                        "VALUES (?,?,?,?)",
                        (r["proposal_id"], bar_ts, mark, ur),
                    )
                    written += 1
                    # Occupancy hint, SL-FIRST on a both-barrier bar — the same
                    # conservative tie-break the resolver applies. The resolved
                    # outcome still comes ONLY from core/shadow_resolver.py.
                    hit_sl = (low <= sl) if is_buy else (high >= sl)
                    hit_tp = (high >= tp) if is_buy else (low <= tp)
                    if hit_sl or hit_tp:
                        self._set_hint(
                            r["proposal_id"], bar_ts, "stop_loss" if hit_sl else "take_profit"
                        )
                        hinted = True
                        break
            if not hinted and now >= cap_open + BAR_S:
                # last scannable bar has closed: the reference's time exit
                self._set_hint(r["proposal_id"], cap_open, "time")
                hinted = True
            if not hinted:
                self._accrue_funding(r, now)
        if written:
            conn.commit()
        return written

    def _set_hint(self, pid: str, bar_ts: int, reason: str) -> None:
        conn = self._wh._conn()
        conn.execute(
            "UPDATE shadow_breakout_probe SET closed_hint_ts=?, closed_hint_reason=? "
            "WHERE proposal_id=? AND closed_hint_ts IS NULL",
            (int(bar_ts), reason, pid),
        )
        conn.commit()

    def _accrue_funding(self, row: dict, now: int) -> None:
        """Book the current funding print once per 8h settlement bucket while
        the position is open. Missing funding is never guessed."""
        try:
            md = self._market_data(self._venue, row["symbol"]) or {}
        except Exception:
            return
        fr = md.get("funding_rate")
        if fr is None:
            return
        bucket = now // FUNDING_SETTLE_S
        if row["last_funding_bucket"] is not None and int(row["last_funding_bucket"]) == bucket:
            return  # this settlement already booked
        new_sum = float(row["realized_funding_rate_sum"] or 0.0) + float(fr)
        conn = self._wh._conn()
        conn.execute(
            "UPDATE shadow_breakout_probe SET realized_funding_rate_sum=?, "
            "last_funding_bucket=? WHERE proposal_id=?",
            (new_sum, int(bucket), row["proposal_id"]),
        )
        conn.commit()

    # ── warehouse writes ─────────────────────────────────────────────────
    def _write_decision_row(
        self, pid, symbol, side, entry_ts, entry_px, sl_px, tp_px, notional
    ) -> None:
        """One shadow_decisions row the keystone resolver replays into
        shadow_outcomes: SL-first, fees + slippage, censoring-guarded time
        exit. sim_pnl stays NULL — the resolver owns the after-cost net."""
        row = {
            "ts": int(entry_ts),
            "model_version": self.model_version,
            "symbol": symbol,
            "side": side,
            "decision": "ALLOW",
            "p_win": None,
            "sim_pnl": None,
            "sim_r_multiple": None,
            "agent_id": self.name,
            "proposal_id": pid,
            "proposed_at": int(entry_ts),
            "vetoed_by": None,
            "veto_reason": None,
            "projected_notional_current": float(notional),
            "projected_notional_alt": float(notional),
            "projected_pnl": None,
            "projected_fee": None,
            "entry_px": float(entry_px),
            "sl_px": float(sl_px),
            "tp_px": float(tp_px),
            "venue": self._venue,
            "timeframe": TIMEFRAME,
            "horizon_bars": int(RESOLVER_HORIZON_BARS),
            "label_status": "PENDING",
        }
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        conn = self._wh._conn()
        conn.execute(f"INSERT INTO shadow_decisions ({cols}) VALUES ({ph})", tuple(row.values()))
        conn.commit()

    def _write_probe_row(
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
        cols = ", ".join(row.keys())
        ph = ", ".join("?" * len(row))
        conn = self._wh._conn()
        # Plain INSERT: an entry row is written exactly once, never replaced.
        conn.execute(
            f"INSERT INTO shadow_breakout_probe ({cols}) VALUES ({ph})", tuple(row.values())
        )
        conn.commit()
