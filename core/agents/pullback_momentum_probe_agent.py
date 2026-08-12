"""PullbackMomentumProbeAgent — LOG-ONLY forward paper test of the owner's
stated pullback-momentum rules (pullback_ma20_rsi14_4h_v1).

THIS IS NOT A PIPELINE GO — READ THIS BEFORE READING ANY RESULT.

Textbook pullback-momentum sits inside the REFUTED trend/momentum families on
the refuted-families-ledger (textbook trend 0/40 OOS on an independent
toolchain, 2026-06-13; long-only TSMOM no-edge, 2026-06-15). No screen has
confirmed this configuration; the probe exists ONLY because (a) the owner
explicitly directed a forward paper test of THEIR OWN stated rules and (b) a
log-only forward test is the honest instrument that could someday meet the
ledger's reopen bar — the exact TsmomProbeAgent / BreakoutProbeAgent
precedent (2026-07-11). Expectation: NO-PROMOTE unless forward evidence
surprises. Promotion — if ever — is assessed via the frozen
core/promotion_gate.py thresholds on >= 30 RESOLVED forward events plus an
explicit owner sign-off. Never here.

STRATEGY (frozen from the owner's stated rules; auxiliary conventions frozen
NOW, pre-outcome, and never re-tuned after outcomes accrue):
- long-only, bybit USDT linear perps, 4h bars; universe = the SAME
  spec-derived universe the bundle-MR probes use (wired at boot via
  bot_engine._bundle_probe_symbols -> bundle_mr_probe_agent.resolve_universe;
  class default = the frozen 5-major fail-closed basket)
- indicators (the in-repo bundle-MR reference math, generalized): SMA20/50/200
  on closes; ATR14 = SIMPLE mean of true ranges (NOT Wilder, bundle
  sma_atr_last); RSI14 = the bundle RSI construction (Wilder-ewm over clipped
  diffs, dn==0 -> 50) with period 14; signal valid only with >= 210 closed
  bars (the bundle warm-up gate)
- ENTRY (an EVENT, not a state) at the bar close where ALL hold: SMA50 >
  SMA200 (trend gate), close > SMA20, and RSI14 CROSSES above 55 (previous
  bar RSI14 <= 55 AND current bar RSI14 > 55). Fill = signal-bar close (fleet
  convention). One position per symbol, no re-entry while open.
- EXIT — first of: (a) bar close with RSI14 > 70; (b) bar close with close <
  SMA20; (c) intrabar stop low <= entry - 1.5 x ATR14(at entry), filled AT
  the stop (conservative); (d) 42-bar time stop (7 days).

CONDITION EXITS AND THE RESOLVER: the vetted keystone resolver
(core/shadow_resolver.py) replays SL/TP price barriers plus a horizon_bars
time exit — it cannot see an RSI or SMA condition. The decision row is
written AT ENTRY (pre-outcome: entry/sl/score frozen then) with sl_px = the
1.5xATR stop, tp_px = 0.0 (no TP barrier) and horizon_bars = 42; when exit
(a) or (b) fires at a later bar close, the monitor TIGHTENS the
still-PENDING row's horizon_bars to that realized exit bar, so the
resolver's time exit lands exactly at the strategy's exit bar close. The
update is mechanical (the frozen exit rule's own bar index), PENDING-guarded,
and never derived from PnL. Known residual race, documented not solved: if
the bot is down past the 42-bar cap while the external resolver task runs,
a condition exit can resolve as the 42-bar time exit instead.

SIZING IS NOTATIONAL ONLY: 1% equity risk per trade with the 2x max-notional
cap — the exact TsmomProbeAgent codex model (probe_common
codex_position_units) — recorded so logged PnL is interpretable. No sizing
hook, no leverage, no order exists anywhere in this module.

FROZEN pre-outcome discriminating score (the AUC promotion gate is
un-computable without one): score = tanh((rsi14_at_entry - 55) / 15), frozen
NOW before any outcome exists. NEVER re-tune it after outcomes accumulate —
a new score is a new pre-registration.

LOG-ONLY (charter, non-negotiable): this class holds ONLY read-only providers
(OHLCV / market data / balance) plus the warehouse. It has no reference to
any order path and is structurally incapable of placing an order.

Realized after-cost economics are produced ONLY by the vetted resolver off
the shadow_decisions rows this probe writes. This module performs NO PnL
math on outcomes — custom PnL math in a probe is a bug. closed_hint fields
are OCCUPANCY BOOKKEEPING; shadow_outcomes stays the only readable outcome.
Per the TP-probe precedent: never read a win- or hit-rate here without the
resolved shadow_outcomes.net_pnl next to it. Funding provider key
``shadow_pullback_probe`` is wired in scripts/resolve_shadow_outcomes.py;
resolved funding is charged when the history series covers the hold.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Callable, Optional

from loguru import logger

from core.agents.bundle_mr_probe_agent import (
    SYMBOLS,  # frozen 5-major fail-closed basket (bundle-MR universe default)
    bundle_rsi_last,
    sma_atr_last,
)
from core.agents.probe_common import (
    ATR_LEN,
    RISK_PCT,
    accrue_funding,
    closed_candles,
    codex_position_units,
    ensure_schema,
    eval_gate,
    insert_row,  # standalone MTM rows (an ENTRY uses write_entry_pair)
    probe_tick,
    set_hint,
    write_entry_pair,
)

# ── Frozen owner-stated constants ────────────────────────────────────────────
# Editing any of these is a NEW pre-registration, not a tweak. Pinned by
# tests/test_pullback_momentum_probe.py::test_frozen_constants.
TIMEFRAME = "4h"
BAR_S = 4 * 3600
SMA_FAST = 20  # close > SMA20 entry gate; close < SMA20 exit
SMA_MID = 50  # trend gate: SMA50 > SMA200
SMA_SLOW = 200
RSI_PERIOD = 14  # bundle RSI construction, period 14
RSI_ENTRY = 55.0  # entry EVENT: RSI14 crosses above 55
RSI_EXIT = 70.0  # exit (a): bar close with RSI14 > 70
STOP_ATR = 1.5  # exit (c): intrabar stop at entry - 1.5 x ATR14(entry)
MAX_HOLD_BARS = 42  # exit (d): 42-bar time stop (7 days on 4h)
MIN_BARS = 210  # bundle warm-up gate
FETCH_BARS = 260  # bundle history window (indicator parity entry vs monitor)
SCORE_RSI_OFFSET = 55.0  # FROZEN pre-outcome; never re-tune post-outcome
SCORE_RSI_SCALE = 15.0  # FROZEN pre-outcome; never re-tune post-outcome

# shadow_decisions.model_version — importable so a probe rename can never
# silently desync a reporter.
PULLBACK_MODEL_VERSION = "pullback_ma20_rsi14_4h_v1"


# ── Pure signal math ─────────────────────────────────────────────────────────
def sma_last(closes, n: int) -> Optional[float]:
    """Simple moving average of the last ``n`` closes; None on short history."""
    if n <= 0 or len(closes) < n:
        return None
    return sum(float(c) for c in closes[-n:]) / float(n)


def pullback_entry_event(*, close, sma20, sma50, sma200, rsi_prev, rsi_cur) -> bool:
    """The frozen entry EVENT: trend gate (SMA50 > SMA200), close > SMA20,
    and RSI14 crossing above 55 (prev <= 55 AND cur > 55). Any missing
    indicator -> False (a signal is never guessed)."""
    vals = (close, sma20, sma50, sma200, rsi_prev, rsi_cur)
    if any(v is None for v in vals):
        return False
    return (
        float(sma50) > float(sma200)
        and float(close) > float(sma20)
        and float(rsi_prev) <= RSI_ENTRY
        and float(rsi_cur) > RSI_ENTRY
    )


def pullback_score(rsi) -> float:
    """FROZEN pre-outcome discriminating score: tanh((rsi14 - 55) / 15)."""
    try:
        return math.tanh((float(rsi) - SCORE_RSI_OFFSET) / SCORE_RSI_SCALE)
    except (TypeError, ValueError):
        return 0.0


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_pullback_probe (
    proposal_id                TEXT PRIMARY KEY,
    symbol                     TEXT,
    venue                      TEXT,
    arm                        TEXT,    -- 'pullback_ma20_rsi14_4h'
    side                       TEXT,    -- 'buy' (long-only)
    signal_bar_ts              INTEGER, -- open ts of the signal bar (= decision ts)
    entry_px                   REAL,    -- signal-bar close (frozen spec fill)
    rsi_entry                  REAL,    -- ┐ as-of-entry signal inputs:
    sma20_entry                REAL,    -- │ RSI14, SMA20/50/200,
    sma50_entry                REAL,    -- │ SMA-ATR(14); written once,
    sma200_entry               REAL,    -- │ never re-derived
    atr_entry                  REAL,    -- ┘
    sl_px                      REAL,    -- entry - 1.5 x ATR (intrabar stop)
    max_hold_bars              INTEGER, -- 42 (the 7d time stop)
    risk_frac                  REAL,    -- 0.01 (codex risk model, notational)
    notional_usd               REAL,
    units                      REAL,
    score                      REAL,    -- FROZEN tanh((rsi14 - 55) / 15)
    realized_funding_rate_sum  REAL,
    last_funding_bucket        INTEGER,
    closed_hint_ts             INTEGER, -- occupancy bookkeeping ONLY (see header)
    closed_hint_reason         TEXT,    -- rsi_overbought | close_below_sma20 |
                                        --   stop_loss | time
    created_ts                 INTEGER
);
CREATE INDEX IF NOT EXISTS idx_pullback_probe_symbol
    ON shadow_pullback_probe(symbol);

CREATE TABLE IF NOT EXISTS shadow_pullback_mtm (
    proposal_id     TEXT,
    bar_ts          INTEGER,
    mark_px         REAL,
    unrealized_ret  REAL,     -- long-only: (mark - entry) / entry
    PRIMARY KEY (proposal_id, bar_ts)
);
"""


class PullbackMomentumProbeAgent:
    """Per-symbol 4h pullback-momentum evaluator + log-only proposer. See header."""

    name = "PullbackMomentumProbeAgent"
    model_version = PULLBACK_MODEL_VERSION
    ARM = "pullback_ma20_rsi14_4h"

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
        ensure_schema(self._wh, _SCHEMA)

    # ── public tick ──────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One probe cycle: update MTM/hints on open holds FIRST, then
        evaluate the latest closed 4h bar per symbol. NEVER raises upward."""
        units = [(symbol, (symbol,)) for symbol in self._symbols]
        return probe_tick(self, units, log_tag="PullbackProbe")

    # ── entry evaluation ─────────────────────────────────────────────────
    def _eval_entry(self, symbol: str, now: int) -> int:
        gate = eval_gate(
            self,
            symbol=symbol,
            tf=TIMEFRAME,
            bar_s=BAR_S,
            fetch_bars=FETCH_BARS,
            min_bars=MIN_BARS,
            last_sql="SELECT signal_bar_ts, closed_hint_ts, max_hold_bars "
            "FROM shadow_pullback_probe WHERE symbol=? "
            "ORDER BY signal_bar_ts DESC LIMIT 1",
            last_params=(symbol,),
            hold_col="max_hold_bars",
            now=now,
        )
        if gate is None:
            return 0
        candles, latest_ts = gate

        closes = [float(c[4]) for c in candles]
        atrv = sma_atr_last(candles, ATR_LEN)
        sma20 = sma_last(closes, SMA_FAST)
        sma50 = sma_last(closes, SMA_MID)
        sma200 = sma_last(closes, SMA_SLOW)
        rsi_cur = bundle_rsi_last(closes, RSI_PERIOD)
        rsi_prev = bundle_rsi_last(closes[:-1], RSI_PERIOD)
        if atrv is None or atrv <= 0:
            return 0
        if not pullback_entry_event(
            close=closes[-1], sma20=sma20, sma50=sma50, sma200=sma200,
            rsi_prev=rsi_prev, rsi_cur=rsi_cur,
        ):
            return 0  # no EVENT — a state signal is never logged bar-by-bar

        entry_px = closes[-1]  # signal-bar close (frozen spec)
        risk_distance = STOP_ATR * atrv
        sl_px = entry_px - risk_distance  # long-only
        if sl_px <= 0:
            return 0
        try:
            equity = float(self._balance() or 0.0)
        except Exception:
            equity = 0.0
        units = codex_position_units(equity, entry_px, risk_distance)
        notional = units * entry_px
        pid = f"pb-{uuid.uuid4().hex[:10]}"

        row = {
            "proposal_id": pid,
            "symbol": symbol,
            "venue": self._venue,
            "arm": self.ARM,
            "side": "buy",
            "signal_bar_ts": int(latest_ts),
            "entry_px": float(entry_px),
            "rsi_entry": float(rsi_cur),
            "sma20_entry": float(sma20),
            "sma50_entry": float(sma50),
            "sma200_entry": float(sma200),
            "atr_entry": float(atrv),
            "sl_px": float(sl_px),
            "max_hold_bars": int(MAX_HOLD_BARS),
            "risk_frac": float(RISK_PCT),
            "notional_usd": float(notional),
            "units": float(units),
            "score": float(pullback_score(rsi_cur)),
            "realized_funding_rate_sum": 0.0,
            "last_funding_bucket": None,
            "closed_hint_ts": None,
            "closed_hint_reason": None,
            "created_ts": int(now),
        }
        # The entry's TWO rows in ONE transaction: the shadow_decisions row
        # the keystone resolver replays into shadow_outcomes (tp_px=0.0 = NO
        # TP barrier — exits are condition-based, see header; horizon_bars
        # starts at the 42-bar time stop and is tightened by the monitor when
        # exit (a)/(b) fires), and this probe row holding the occupancy slot.
        # Written separately, a failure between them orphans the decision.
        write_entry_pair(
            self._wh,
            decision=dict(
                ts=latest_ts,
                model_version=self.model_version,
                symbol=symbol,
                side="buy",
                agent_id=self.name,
                proposal_id=pid,
                notional=notional,
                entry_px=entry_px,
                sl_px=sl_px,
                tp_px=0.0,
                venue=self._venue,
                timeframe=TIMEFRAME,
                horizon_bars=MAX_HOLD_BARS,
            ),
            probe_table="shadow_pullback_probe",
            probe_row=row,
        )
        return 1

    # ── monitoring: per-bar MTM + condition exits + funding ──────────────
    # probe_common.monitor_open_barriers is NOT reusable here: with tp_px=0.0
    # its unguarded ``high >= tp`` check would fire a spurious take_profit
    # hint on the first bar of every long, and it cannot evaluate the RSI /
    # SMA bar-close exit conditions. This custom monitor mirrors its shape
    # (expected-bar fetch gate, MTM per closed bar, first-exit hint, funding)
    # with the frozen exit priority: intrabar stop FIRST, then RSI > 70, then
    # close < SMA20 (both at the bar close), then the 42-bar time cap.
    def _monitor_open(self, now: int) -> int:
        rows = self._wh.query(
            "SELECT proposal_id, symbol, signal_bar_ts, entry_px, sl_px, "
            "max_hold_bars, last_funding_bucket, realized_funding_rate_sum "
            "FROM shadow_pullback_probe WHERE closed_hint_ts IS NULL"
        )
        written = 0
        for r in rows:
            entry_ts = int(r["signal_bar_ts"])
            entry_px = float(r["entry_px"])
            sl = float(r["sl_px"])
            hold_bars = int(r["max_hold_bars"])
            cap_open = entry_ts + hold_bars * BAR_S
            hinted = False

            last = self._wh.query(
                "SELECT MAX(bar_ts) AS m FROM shadow_pullback_mtm WHERE proposal_id=?",
                (r["proposal_id"],),
            )
            since = (int(last[0]["m"]) + 1) if last and last[0]["m"] is not None else entry_ts + 1
            expected_bar = (now // BAR_S - 1) * BAR_S
            gate_key = (r["symbol"], TIMEFRAME)
            if since <= min(now, cap_open) and self._mon_seen.get(gate_key, -1) < expected_bar:
                # Fetch WITH pre-entry context so RSI14/SMA20 at each forward
                # bar are computed on the same sliding FETCH_BARS window the
                # entry evaluation used (indicator parity).
                need = FETCH_BARS + max(0, (now - entry_ts) // BAR_S) + 2
                candles = closed_candles(
                    self._ohlcv, self._venue, r["symbol"], TIMEFRAME, BAR_S, need, now
                )
                if candles:
                    self._mon_seen[gate_key] = expected_bar
                closes = [float(c[4]) for c in candles]
                # Positional index of the ENTRY bar in this fetch. The
                # resolver counts POSITIONAL forward bars over the candles it
                # is handed, so a condition exit must be encoded the same way:
                # a venue gap makes the calendar delta LARGER than the bars
                # that will ever exist, and resolve_one's censoring guard
                # (len(scan) < horizon -> None) then holds the row PENDING
                # forever. None = the entry bar is not in this window; the
                # calendar fallback below is then the only available count.
                entry_idx = next(
                    (j for j, cc in enumerate(candles) if int(cc[0]) // 1000 == entry_ts),
                    None,
                )
                for i, c in enumerate(candles):
                    try:
                        bar_ts = int(c[0]) // 1000
                        low, mark = float(c[3]), float(c[4])
                    except (IndexError, TypeError, ValueError):
                        continue
                    if bar_ts <= entry_ts or bar_ts > cap_open or bar_ts < since:
                        continue
                    insert_row(
                        self._wh,
                        "shadow_pullback_mtm",
                        {
                            "proposal_id": r["proposal_id"],
                            "bar_ts": bar_ts,
                            "mark_px": mark,
                            "unrealized_ret": (mark - entry_px) / entry_px,
                        },
                        replace=True,
                    )
                    written += 1
                    # (c) intrabar stop FIRST — the resolver's own SL-first
                    # conservative tie-break; horizon stays 42 (the resolver
                    # hits the same sl_px itself).
                    if low <= sl:
                        set_hint(self._wh, "shadow_pullback_probe",
                                 r["proposal_id"], bar_ts, "stop_loss")
                        hinted = True
                        break
                    # (a)/(b) bar-close condition exits on the sliding window
                    # ending at this bar; encode for the resolver by
                    # tightening horizon_bars to this realized exit bar.
                    win = closes[max(0, i + 1 - FETCH_BARS): i + 1]
                    rsi = bundle_rsi_last(win, RSI_PERIOD)
                    sma20 = sma_last(win, SMA_FAST)
                    reason = None
                    if rsi is not None and rsi > RSI_EXIT:
                        reason = "rsi_overbought"
                    elif sma20 is not None and mark < sma20:
                        reason = "close_below_sma20"
                    if reason is not None:
                        set_hint(self._wh, "shadow_pullback_probe",
                                 r["proposal_id"], bar_ts, reason)
                        if entry_idx is not None:
                            exit_bars = i - entry_idx  # POSITIONAL, gap-safe
                        else:
                            exit_bars = (bar_ts - entry_ts) // BAR_S
                            logger.debug(
                                f"[PullbackProbe] {r['symbol']} "
                                f"{r['proposal_id']}: entry bar absent from the "
                                "monitor window — horizon tightened on the "
                                "calendar delta, which a venue gap inflates"
                            )
                        self._tighten_horizon(r["proposal_id"], exit_bars)
                        hinted = True
                        break
            if not hinted and now >= cap_open + BAR_S:
                # (d) max-hold bar has closed: the 42-bar time exit
                set_hint(self._wh, "shadow_pullback_probe",
                         r["proposal_id"], cap_open, "time")
                hinted = True
            if not hinted:
                self._accrue_funding(r, now)
        return written

    def _tighten_horizon(self, pid: str, exit_bars: int) -> None:
        """Tighten the still-PENDING decision row's horizon_bars to the
        realized condition-exit bar so the vetted resolver's time exit lands
        exactly at the strategy's exit bar close. Mechanical (the frozen exit
        rule's own bar index), never derived from PnL; the PENDING guard
        makes a resolved/terminal row immutable. Mirrors probe_common
        set_hint's style — kept local per the 2026-07-23 touch-only scope."""
        if exit_bars <= 0:
            return
        conn = self._wh._conn()
        conn.execute(
            "UPDATE shadow_decisions SET horizon_bars=? "
            "WHERE proposal_id=? AND label_status='PENDING'",
            (int(exit_bars), pid),
        )
        conn.commit()

    def _accrue_funding(self, row: dict, now: int) -> None:
        """Book the current funding print once per 8h settlement bucket while
        the position is open. Missing funding is never guessed."""
        accrue_funding(
            self._wh,
            table="shadow_pullback_probe",
            market_data=self._market_data,
            venue=self._venue,
            row=row,
            now=now,
        )
