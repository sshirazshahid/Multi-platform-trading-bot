"""TsmomProbeAgent — LOG-ONLY forward paper test of the Codex TSMOM-20d rules.

THIS IS NOT A PIPELINE GO — READ THIS BEFORE READING ANY RESULT.

Time-series momentum is a REFUTED family on the refuted-families-ledger
(.claude/skills/refuted-families-ledger/SKILL.md): long-only TSMOM showed no
profit edge on our own data (2026-06-15) and textbook trend/breakout went 0/40
OOS on an independent toolchain (2026-06-13). The external Codex project's
backtest that recommended "regime-watch — monitor in paper mode, do NOT
automate" (bot_weight 0.0) does NOT meet the ledger's reopen bar: ~30 OOS
trades on a ~1.8-month single-regime window, the winner of a ~90-run sweep
with no multiplicity control, and the PRECEDING period was -17.4% with 0%
profitable runs. Regime fragility is the KNOWN failure mode: if this probe
bleeds when the tape turns, that is the documented expectation playing out,
not a surprise. This probe exists ONLY because (a) the owner explicitly
directed the forward paper test and (b) a log-only forward test is the honest
instrument for collecting the evidence that could someday meet the reopen bar.
The expectation is NO-PROMOTE unless forward evidence surprises.

STRATEGY (extracted verbatim from the Codex Pine sources — this probe forward
tests THEIR configuration, not our reinterpretation):
  pine_strategies/17_time_series_momentum_20d_futures.pine          (1h arm)
  pine_strategies/18_time_series_momentum_20d_4h_full_history.pine  (4h arm)
- momentum = close / close[N] - 1, N = 20 days of bars (480 @1h, 120 @4h)
- trend    = EMA(close, 5 days of bars)            (120 @1h, 30 @4h)
- LONG  when momentum > 0 and close > trend
- SHORT when momentum < 0 and close < trend
- stop = entry -/+ 2.0 x ATR(14); target = entry +/- 2R (rewardRisk = 2.0)
- max hold 7 days (168 bars @1h, 42 bars @4h), then close at the bar close
- fill at the SIGNAL BAR CLOSE (Pine process_orders_on_close=true)
- markets: BTC/ETH/SOL USDT linear perps (the Codex backtest used Bybit)
Pine-vs-reference divergence, resolved in favour of the reference backtest
(research/futures_backtest.py) that actually produced the regime-watch
verdict: the Pine allows a reversal flip while in a position; the reference
enforces "no overlapping trades per strategy/market" and only enters flat.
This probe does the same — ONE position per (symbol, arm), no flips.

TWO ARMS, logged and scored separately (owner directive): decision rows carry
model_version tsmom_20d_1h_v1 / tsmom_20d_4h_v1. Promotion — if ever — is
assessed PER ARM via the frozen core/promotion_gate.py thresholds on >= 30
RESOLVED forward events per arm PLUS an explicit owner sign-off. Never here.

SIZING IS NOTATIONAL ONLY: 1% equity risk per trade with a 2x max-notional
cap (the Codex risk model) is recorded so logged PnL is interpretable. No
sizing hook, no leverage, no order exists anywhere in this module.

FROZEN pre-outcome discriminating score (the AUC promotion gate is
un-computable without one): score = tanh(|mom_20d| / SCORE_MOM_SCALE) with
SCORE_MOM_SCALE = 0.10 frozen NOW, before any outcome exists. NEVER re-tune
it after outcomes accumulate — a new score requires a new pre-registration.

LOG-ONLY (charter, non-negotiable): this class holds ONLY read-only providers
(OHLCV / market data / balance) plus the warehouse. It has no reference to
any order path and is structurally incapable of placing an order.

Realized after-cost economics are produced by the vetted keystone resolver
(core/shadow_resolver.py) off the shadow_decisions rows this probe writes
(entry at the signal-bar close; SL-first tie-break; fees + slippage; realized
funding via scripts/resolve_shadow_outcomes.py's probe funding provider).
This module performs NO PnL math on outcomes — custom PnL math in a probe is
a bug. The closed_hint fields below are OCCUPANCY BOOKKEEPING (they free the
one-position-per-symbol/arm slot, mirroring the resolver's barrier logic);
the resolver's shadow_outcomes row stays the only readable outcome. And per
the TP-probe precedent (core/agents/tp_probe_agent.py): never read a win- or
hit-rate here without the resolved shadow_outcomes.net_pnl next to it.
"""

from __future__ import annotations

import math
import time
import uuid
from typing import Callable, Optional

from loguru import logger

# ── Frozen Codex/Pine constants (Pine 17/18 + research/futures_backtest.py) ──
# Editing any of these is a NEW pre-registration, not a tweak. Pinned by
# tests/test_tsmom_probe.py::test_frozen_pine_constants.
SYMBOLS = ("BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT")
ATR_LEN = 14  # ta.atr(14) — Wilder RMA
STOP_ATR = 2.0  # stop = entry -/+ 2 x ATR
REWARD_RISK = 2.0  # target = entry +/- 2R
RISK_PCT = 0.01  # Codex risk model: 1% equity risk (NOTATIONAL)
MAX_NOTIONAL_MULTIPLE = 2.0  # Codex cap: notional <= 2x equity (NOTATIONAL)
SCORE_MOM_SCALE = 0.10  # FROZEN pre-outcome; never re-tune post-outcome

ARMS = {
    "1h": {"tf": "1h", "bar_s": 3600, "mom_bars": 480, "ema_bars": 120, "max_hold_bars": 168},
    "4h": {"tf": "4h", "bar_s": 14400, "mom_bars": 120, "ema_bars": 30, "max_hold_bars": 42},
}

FUNDING_SETTLE_S = 8 * 3600  # perp funding settles every 8h


# ── Pure indicator math (mirrors research/futures_backtest.py pandas ewm) ────
def ema_last(closes, n: int) -> Optional[float]:
    """Last value of EMA(n) — pandas ewm(span=n, adjust=False, min_periods=n).
    None until min_periods is met (the reference's warm-up guard)."""
    if n <= 0 or len(closes) < n:
        return None
    alpha = 2.0 / (n + 1.0)
    e = float(closes[0])
    for c in closes[1:]:
        e = alpha * float(c) + (1.0 - alpha) * e
    return e


def wilder_atr_last(candles, n: int = ATR_LEN) -> Optional[float]:
    """Last Wilder ATR(n) — TR(prev-close) smoothed by pandas
    ewm(alpha=1/n, adjust=False, min_periods=n). None until warm-up."""
    if n <= 0 or len(candles) < n:
        return None
    prev_close: Optional[float] = None
    rma: Optional[float] = None
    for c in candles:
        try:
            high, low, close = float(c[2]), float(c[3]), float(c[4])
        except (IndexError, TypeError, ValueError):
            return None
        tr = high - low
        if prev_close is not None:
            tr = max(tr, abs(high - prev_close), abs(low - prev_close))
        rma = tr if rma is None else rma + (tr - rma) / float(n)
        prev_close = close
    return rma


def momentum_lookback(closes, mom_bars: int) -> Optional[float]:
    """Pine: close / close[mom_bars] - 1. None without mom_bars+1 closes."""
    if mom_bars <= 0 or len(closes) < mom_bars + 1:
        return None
    past = float(closes[-1 - mom_bars])
    if past <= 0:
        return None
    return float(closes[-1]) / past - 1.0


def tsmom_signal(mom, *, close: float, trend) -> int:
    """+1 long / -1 short / 0 flat — the exact Pine setup conditions."""
    if mom is None or trend is None:
        return 0
    if mom > 0 and close > trend:
        return 1
    if mom < 0 and close < trend:
        return -1
    return 0


def tsmom_score(mom) -> float:
    """FROZEN pre-outcome discriminating score: tanh(|mom_20d| / 0.10).
    Pre-specified before any outcome exists so the AUC >= 0.60 promotion gate
    is computable; an honest AUC FAIL is a legitimate NO-PROMOTE outcome."""
    try:
        return math.tanh(abs(float(mom)) / SCORE_MOM_SCALE)
    except (TypeError, ValueError):
        return 0.0


def codex_position_units(equity: float, entry_px: float, risk_distance: float) -> float:
    """Codex sizing (NOTATIONAL): min(1%-risk units, 2x-notional cap units)."""
    if equity <= 0 or entry_px <= 0 or risk_distance <= 0:
        return 0.0
    return min(equity * RISK_PCT / risk_distance, equity * MAX_NOTIONAL_MULTIPLE / entry_px)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS shadow_tsmom_probe (
    proposal_id                TEXT PRIMARY KEY,
    symbol                     TEXT,
    venue                      TEXT,
    arm                        TEXT,    -- '1h' | '4h'
    side                       TEXT,    -- 'buy' | 'sell'
    signal_bar_ts              INTEGER, -- open ts of the signal bar (= decision ts)
    entry_px                   REAL,    -- signal-bar close (process_orders_on_close)
    mom_20d                    REAL,    -- ┐ as-of-entry signal inputs:
    ema_trend                  REAL,    -- │ written once, never re-derived
    atr_entry                  REAL,    -- ┘
    sl_px                      REAL,
    tp_px                      REAL,
    max_hold_bars              INTEGER,
    risk_frac                  REAL,    -- 0.01 (Codex risk model, notational)
    notional_usd               REAL,
    units                      REAL,
    score                      REAL,    -- FROZEN tanh(|mom_20d| / 0.10)
    realized_funding_rate_sum  REAL,
    last_funding_bucket        INTEGER,
    closed_hint_ts             INTEGER, -- occupancy bookkeeping ONLY (see header)
    closed_hint_reason         TEXT,    -- stop_loss | take_profit | time
    created_ts                 INTEGER
);
CREATE INDEX IF NOT EXISTS idx_tsmom_probe_arm
    ON shadow_tsmom_probe(symbol, arm);

CREATE TABLE IF NOT EXISTS shadow_tsmom_mtm (
    proposal_id     TEXT,
    bar_ts          INTEGER,
    mark_px         REAL,
    unrealized_ret  REAL,     -- side-signed: >0 = position in profit
    PRIMARY KEY (proposal_id, bar_ts)
);
"""


class TsmomProbeAgent:
    """Per-(symbol, arm) TSMOM-20d signal evaluator + log-only proposer. See header."""

    name = "TsmomProbeAgent"
    model_version = "tsmom_20d_probe_v1"  # per-arm versions on decision rows

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

    # ── schema ───────────────────────────────────────────────────────────
    def _ensure_schema(self) -> None:
        conn = self._wh._conn()
        conn.executescript(_SCHEMA)
        conn.commit()

    # ── public tick ──────────────────────────────────────────────────────
    def tick(self) -> dict:
        """One probe cycle: update MTM/hints on open holds FIRST (a barrier
        hit this bar frees the slot honestly), then evaluate the latest closed
        bar per (symbol, arm). Returns a stats dict. NEVER raises upward."""
        stats = {"evaluated": 0, "entered": 0, "mtm_rows": 0}
        now = int(self._now())
        try:
            stats["mtm_rows"] = self._monitor_open(now)
        except Exception as e:
            logger.debug(f"[TsmomProbe] monitor error: {e}")
        for symbol in self._symbols:
            for arm_key in ARMS:
                try:
                    stats["entered"] += self._eval_entry(symbol, arm_key, now)
                    stats["evaluated"] += 1
                except Exception as e:
                    logger.debug(f"[TsmomProbe] {symbol} {arm_key} eval error: {e}")
        return stats

    # ── entry evaluation ─────────────────────────────────────────────────
    def _eval_entry(self, symbol: str, arm_key: str, now: int) -> int:
        spec = ARMS[arm_key]
        bar_s = int(spec["bar_s"])

        candles = self._closed_candles(symbol, spec, now)
        if len(candles) < spec["mom_bars"] + 1:
            return 0  # insufficient history — never guess a signal
        latest_ts = int(candles[-1][0]) // 1000

        last = self._wh.query(
            "SELECT signal_bar_ts, closed_hint_ts, max_hold_bars "
            "FROM shadow_tsmom_probe WHERE symbol=? AND arm=? "
            "ORDER BY signal_bar_ts DESC LIMIT 1",
            (symbol, arm_key),
        )
        if last:
            r = last[0]
            hint_ts = r["closed_hint_ts"]
            # ONE position per (symbol, arm) — the reference backtest's
            # no-overlap rule. Time bound: the position exits at the close of
            # bar entry+max_hold, i.e. signal_bar_ts + (max_hold+1) bars.
            time_free = int(r["signal_bar_ts"]) + (int(r["max_hold_bars"]) + 1) * bar_s
            if hint_ts is None and now < time_free:
                return 0  # occupied
            if latest_ts <= int(r["signal_bar_ts"]):
                return 0  # this bar was already acted on — never re-signal it
            if hint_ts is not None and latest_ts <= int(hint_ts):
                return 0  # re-entry earliest one bar AFTER the exit-hint bar

        closes = [float(c[4]) for c in candles]
        mom = momentum_lookback(closes, int(spec["mom_bars"]))
        trend = ema_last(closes, int(spec["ema_bars"]))
        atrv = wilder_atr_last(candles, ATR_LEN)
        sig = tsmom_signal(mom, close=closes[-1], trend=trend)
        if sig == 0 or atrv is None or atrv <= 0:
            return 0  # flat state — a state signal is not logged bar-by-bar

        entry_px = closes[-1]  # signal-bar close (Pine)
        risk_distance = STOP_ATR * atrv
        sl_px = entry_px - sig * risk_distance
        tp_px = entry_px + sig * risk_distance * REWARD_RISK
        if sl_px <= 0 or tp_px <= 0:
            return 0
        try:
            equity = float(self._balance() or 0.0)
        except Exception:
            equity = 0.0
        units = codex_position_units(equity, entry_px, risk_distance)
        notional = units * entry_px
        side = "buy" if sig > 0 else "sell"
        pid = f"tm-{uuid.uuid4().hex[:10]}"

        self._write_decision_row(
            pid,
            symbol,
            side,
            latest_ts,
            entry_px,
            sl_px,
            tp_px,
            int(spec["max_hold_bars"]),
            str(spec["tf"]),
            notional,
            model_version=f"tsmom_20d_{arm_key}_v1",
        )
        self._write_probe_row(
            proposal_id=pid,
            symbol=symbol,
            arm=arm_key,
            side=side,
            signal_bar_ts=latest_ts,
            entry_px=entry_px,
            mom_20d=float(mom),
            ema_trend=float(trend),
            atr_entry=float(atrv),
            sl_px=sl_px,
            tp_px=tp_px,
            max_hold_bars=int(spec["max_hold_bars"]),
            notional_usd=notional,
            units=units,
            score=tsmom_score(mom),
            now=now,
        )
        return 1

    def _closed_candles(self, symbol: str, spec: dict, now: int) -> list:
        """CLOSED bars for (symbol, arm timeframe), oldest-first. The forming
        bar is dropped so a signal never repaints."""
        bar_s = int(spec["bar_s"])
        need = int(spec["mom_bars"]) + int(spec["ema_bars"]) + ATR_LEN + 8
        since_ms = (now - need * bar_s) * 1000
        raw = self._ohlcv(self._venue, symbol, str(spec["tf"]), since_ms) or []
        out = []
        for c in raw:
            try:
                bar_ts = int(c[0]) // 1000
                close = float(c[4])
            except (IndexError, TypeError, ValueError):
                continue
            if bar_ts + bar_s <= now and close > 0:
                out.append(c)
        out.sort(key=lambda c: c[0])
        return out

    # ── monitoring: per-bar MTM + occupancy hints + funding ──────────────
    def _monitor_open(self, now: int) -> int:
        rows = self._wh.query(
            "SELECT proposal_id, symbol, arm, side, signal_bar_ts, entry_px, "
            "sl_px, tp_px, max_hold_bars, last_funding_bucket, "
            "realized_funding_rate_sum FROM shadow_tsmom_probe "
            "WHERE closed_hint_ts IS NULL"
        )
        written = 0
        conn = self._wh._conn()
        for r in rows:
            spec = ARMS.get(r["arm"])
            if spec is None:
                continue
            bar_s = int(spec["bar_s"])
            entry_ts = int(r["signal_bar_ts"])
            entry_px = float(r["entry_px"])
            is_buy = r["side"] == "buy"
            sl, tp = float(r["sl_px"]), float(r["tp_px"])
            cap_open = entry_ts + int(r["max_hold_bars"]) * bar_s
            hinted = False

            last = self._wh.query(
                "SELECT MAX(bar_ts) AS m FROM shadow_tsmom_mtm WHERE proposal_id=?",
                (r["proposal_id"],),
            )
            since = (int(last[0]["m"]) + 1) if last and last[0]["m"] is not None else entry_ts + 1
            if since <= min(now, cap_open):
                candles = self._ohlcv(self._venue, r["symbol"], str(spec["tf"]), since * 1000) or []
                for c in sorted(candles, key=lambda c: c[0]):
                    try:
                        bar_ts = int(c[0]) // 1000
                        high, low, mark = float(c[2]), float(c[3]), float(c[4])
                    except (IndexError, TypeError, ValueError):
                        continue
                    if bar_ts <= entry_ts or bar_ts > cap_open:
                        continue
                    if bar_ts + bar_s > now:
                        continue  # forming/unclosed bar — no repaint
                    ur = (mark - entry_px) / entry_px if is_buy else (entry_px - mark) / entry_px
                    conn.execute(
                        "INSERT OR REPLACE INTO shadow_tsmom_mtm "
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
            if not hinted and now >= cap_open + bar_s:
                # max-hold bar has closed: the Pine close_all time exit
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
            "UPDATE shadow_tsmom_probe SET closed_hint_ts=?, closed_hint_reason=? "
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
            "UPDATE shadow_tsmom_probe SET realized_funding_rate_sum=?, "
            "last_funding_bucket=? WHERE proposal_id=?",
            (new_sum, int(bucket), row["proposal_id"]),
        )
        conn.commit()

    # ── warehouse writes ─────────────────────────────────────────────────
    def _write_decision_row(
        self,
        pid,
        symbol,
        side,
        entry_ts,
        entry_px,
        sl_px,
        tp_px,
        horizon_bars,
        timeframe,
        notional,
        *,
        model_version,
    ) -> None:
        """One shadow_decisions row the keystone resolver replays into
        shadow_outcomes: SL-first, fees + slippage, censoring-guarded time exit
        at the max-hold bar. sim_pnl stays NULL — this probe does no PnL
        projection; the resolver owns the after-cost net."""
        row = {
            "ts": int(entry_ts),
            "model_version": model_version,
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
            "timeframe": str(timeframe),
            "horizon_bars": int(horizon_bars),
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
        arm,
        side,
        signal_bar_ts,
        entry_px,
        mom_20d,
        ema_trend,
        atr_entry,
        sl_px,
        tp_px,
        max_hold_bars,
        notional_usd,
        units,
        score,
        now,
    ) -> None:
        row = {
            "proposal_id": proposal_id,
            "symbol": symbol,
            "venue": self._venue,
            "arm": arm,
            "side": side,
            "signal_bar_ts": int(signal_bar_ts),
            "entry_px": float(entry_px),
            "mom_20d": float(mom_20d),
            "ema_trend": float(ema_trend),
            "atr_entry": float(atr_entry),
            "sl_px": float(sl_px),
            "tp_px": float(tp_px),
            "max_hold_bars": int(max_hold_bars),
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
        conn.execute(f"INSERT INTO shadow_tsmom_probe ({cols}) VALUES ({ph})", tuple(row.values()))
        conn.commit()
