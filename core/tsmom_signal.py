"""Long-only time-series momentum (TSMOM) signal — capital-preservation variant.

Drop-in alternative to ``mcp_brain.analyze_portfolio`` selected by ``config.SIGNAL_SOURCE=tsmom``.
Emits the SAME action-dict contract ``bot_engine`` consumes, so entry/exit execution, risk,
sim-fill, and provenance all work unchanged. PAPER-only research path.

Thesis (validated 2026-06-15, ``reports/tsmom_validation_2026-06-15.md``): on liquid majors,
a long-only 28-day momentum rule — be long only while the daily trend is up, otherwise flat —
roughly HALVED drawdown vs buy-and-hold and beat it risk-adjusted on 4 of 5 majors. It is NOT
a profit engine (only 2/5 had positive out-of-sample Sharpe in a bearish window); it IS a
capital preserver. The objective here is "don't lose", per the research's own realistic verdict.

Decision rule, per major coin, on DAILY bars::

    momentum_positive = close[today] > close[today - LOOKBACK]
      positive  + no open long   -> OPEN  (buy / long)
      not-positive + open long   -> CLOSE (exit on trend flip)
      else                       -> no action (hold the long, or stay in cash)

INVARIANTS (capital preservation — pinned by tests/test_tsmom_signal.py):
  * LONG-ONLY. Never emits a short. Shorting is the anti-pattern the research flagged and the
    reason the current net-short bot is edgeless.
  * Universe restricted to a small set of validated liquid majors.
  * Daily-cadence trend signal (daily closes) — no intraday churn; an open long is held until
    the daily momentum flips, never re-opened while already long.
  * leverage = 1 (unleveraged long).

⚠ EXIT-MACHINERY TENSION (KNOWN, must be resolved before PAPER numbers mean anything):
  ``bot_engine``'s exit stack is scalp-tuned — a deterministic ATR stop set at entry, a trailing
  manager, ``mcp_take_profit`` at ~+0.5% net, and ``entry_invalidated`` at 30 min. Those will
  CLIP a trend position long before this module's momentum-flip CLOSE can fire, which defeats
  the downside-capture thesis. Swapping the signal source changes ENTRY selection only — it does
  NOT change EXIT behaviour. A follow-up must gate/relax those scalp exits when
  ``SIGNAL_SOURCE=tsmom`` (hold until the signal flips). Until then this path measures
  "TSMOM entries run through a scalp exit harness", which is NOT the validated strategy.
"""
from __future__ import annotations

import math
import time
import uuid

# Validated liquid majors from reports/tsmom_validation_2026-06-15.md. Kept small on purpose:
# edge that survives costs concentrates in big/liquid coins (CTREND finding), and a tiny
# universe keeps cost-as-% and minimum-notional manageable at retail size.
DEFAULT_UNIVERSE = {"BTC", "ETH", "SOL", "BNB", "XRP"}

_ANN = 365.0          # crypto trades every day
_TARGET_ANN_VOL = 0.60  # vol-target for position weighting (matches the validation script)
_VOL_WINDOW = 20      # daily bars for realized-vol estimate
_BASE_SIZE_PCT = 5.0  # max % of account a single long uses before vol-scaling


class TSMOMSignal:
    """Stateless-per-call long-only trend signal. One instance is reused across cycles;
    all state lives in the exchange feeds + the open-position list passed in each call."""

    def __init__(self, exchanges: dict, *, universe=None,
                 lookback_days: int = 28, timeframe: str = "1d"):
        self.exchanges = exchanges or {}
        self.universe = {b.upper() for b in (universe or DEFAULT_UNIVERSE)}
        self.lookback = int(lookback_days)
        self.timeframe = timeframe
        # need lookback + a vol window + a little slack
        self._min_bars = self.lookback + _VOL_WINDOW + 2
        # observability: per-major hold/trade state from the latest cycle (so a
        # quiet bot in a downtrend can explain itself). Populated by analyze_portfolio.
        self.last_status: list = []

    # ── public contract ──────────────────────────────────────────────────────
    def analyze_portfolio(self, coins: list, open_positions: list,
                          exchange_balances: dict, risk_envelope: dict,
                          recent_trades: list, news_context: dict = None) -> list:
        """Return a list of OPEN/CLOSE action dicts matching the mcp_brain contract.

        Only ``coins`` that fall in the validated majors universe are considered; everything
        else is ignored (the bot may pass 40+ symbols). Signature mirrors
        ``mcp_brain.analyze_portfolio`` exactly so the call site is a clean swap.
        """
        open_long_bases = self._open_long_bases(open_positions)
        actions: list = []
        status: list = []

        for base in self._candidates(coins):
            closes, ex_name = self._daily_closes(base)
            if len(closes) < self._min_bars:
                status.append({"base": base, "state": "no_data", "mom_pct": None})
                continue  # insufficient history — skip, never guess

            mom_pct = (closes[-1] / closes[-1 - self.lookback] - 1.0) * 100.0
            mom_positive = mom_pct > 0          # close today > close `lookback` bars ago
            pos = open_long_bases.get(base)

            if mom_positive and pos is None:
                actions.append(self._open_action(base, ex_name, closes))
                state = "open_long"
            elif (not mom_positive) and pos is not None:
                actions.append(self._close_action(pos))
                state = "close_flip"
            elif mom_positive and pos is not None:
                state = "hold_long"             # already long + still trending up
            else:
                state = "flat"                  # negative momentum, no position -> stay in cash
            status.append({"base": base, "state": state,
                           "mom_pct": round(mom_pct, 1), "positive": mom_positive})

        self.last_status = status
        return actions

    def watch_summary(self) -> str:
        """One-line human summary of the latest cycle's per-major state — explains
        WHY tsmom is quiet (long-only: it stays in cash until a major's momentum
        over the configured lookback turns positive). Empty until analyze_portfolio
        has run once."""
        st = self.last_status
        if not st:
            return "no majors evaluated yet"
        tag = {"open_long": "OPEN", "close_flip": "CLOSE", "hold_long": "HOLD", "flat": "flat"}
        parts = []
        for s in st:
            if s.get("state") == "no_data":
                parts.append(f"{s['base']}=no_data")
            else:
                parts.append(f"{s['base']} {s['mom_pct']:+.0f}% [{tag.get(s['state'], s['state'])}]")
        n_up = sum(1 for s in st if s.get("positive"))
        return (f"{n_up}/{len(st)} majors in {self.lookback}d uptrend | " + " ".join(parts)
                + f" | long-only: opens when {self.lookback}d momentum > 0")

    # ── helpers ──────────────────────────────────────────────────────────────
    def _candidates(self, coins: list) -> list:
        """Coins from the caller that are in the validated majors universe (dedup, ordered)."""
        seen, out = set(), []
        for c in coins or []:
            base = str(c).split("/")[0].split(":")[0].upper()
            if base in self.universe and base not in seen:
                seen.add(base)
                out.append(base)
        return out

    @staticmethod
    def _open_long_bases(open_positions: list) -> dict:
        """Map base -> position dict for currently-open LONG positions (side buy)."""
        out: dict = {}
        for p in open_positions or []:
            if str(p.get("side", "")).lower() != "buy":
                continue  # only longs are ours to manage; ignore any legacy shorts
            base = str(p.get("symbol", "")).split("/")[0].split(":")[0].upper()
            if base:
                out.setdefault(base, p)
        return out

    def _daily_closes(self, base: str):
        """Fetch daily closes for a base, trying spot then futures across exchanges.

        Returns ``(closes, exchange_name)`` or ``([], None)`` on no data. Mirrors the
        spot-then-futures probe mcp_brain uses so it works on the same venues.
        """
        for market_type, sym in (("spot", f"{base}/USDT"),
                                 ("futures", f"{base}/USDT:USDT")):
            for ex_name, ex in self.exchanges.items():
                try:
                    raw = ex.fetch_ohlcv(sym, self.timeframe,
                                         limit=self._min_bars + 10,
                                         market_type=market_type)
                except Exception:
                    raw = None
                if raw:
                    raw = self._closed_ohlcv_rows(raw)
                if raw and len(raw) >= self._min_bars:
                    closes = [float(r[4]) for r in raw]
                    return closes, ex_name
        return [], None

    def _closed_ohlcv_rows(self, rows: list) -> list:
        """Return only fully closed candles.

        CCXT OHLCV timestamps are candle-open times. For a daily momentum
        signal, including the still-forming current candle leaks intraday price
        into a daily-close rule and can flip a trade too early.
        """
        tf_ms = self._timeframe_ms(self.timeframe)
        if tf_ms <= 0:
            return rows
        now_ms = int(time.time() * 1000)
        closed = []
        for r in rows or []:
            try:
                ts = float(r[0])
                if ts < 10_000_000_000:  # seconds, not milliseconds
                    ts *= 1000
                if ts + tf_ms <= now_ms:
                    closed.append(r)
            except Exception:
                continue
        return closed

    @staticmethod
    def _timeframe_ms(timeframe: str) -> int:
        tf = str(timeframe or "").strip().lower()
        if not tf:
            return 0
        units = {
            "m": 60_000,
            "h": 3_600_000,
            "d": 86_400_000,
            "w": 7 * 86_400_000,
        }
        try:
            unit = tf[-1]
            n = int(tf[:-1] or "1")
            return n * units.get(unit, 0)
        except Exception:
            return 0

    def _vol_scaled_size_pct(self, closes: list) -> float:
        """Vol-targeted size: smaller position when realized vol is high. Clamped to a
        conservative band; the risk manager still applies its own sizing downstream."""
        window = closes[-(_VOL_WINDOW + 1):]
        rets = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window))]
        if len(rets) < 2:
            return round(_BASE_SIZE_PCT * 0.5, 2)
        mean = sum(rets) / len(rets)
        var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
        realized_ann = math.sqrt(var) * math.sqrt(_ANN)
        if realized_ann <= 0:
            weight = 1.0
        else:
            weight = min(1.0, _TARGET_ANN_VOL / realized_ann)
        return round(max(1.0, _BASE_SIZE_PCT * weight), 2)

    def _open_action(self, base: str, ex_name: str, closes: list) -> dict:
        c_now, c_past = closes[-1], closes[-1 - self.lookback]
        return {
            "type": "OPEN",
            "symbol": f"{base}/USDT:USDT",
            "exchange": (ex_name or "binance").lower(),
            "market_type": "futures",
            "side": "buy",               # LONG-ONLY invariant
            "leverage": 1,               # unleveraged — capital preservation
            "size_pct": self._vol_scaled_size_pct(closes),
            # SL/TP are advisory: the engine computes its own ATR stop and the real exit is
            # the momentum-flip CLOSE. Kept wide so they don't pretend to be a scalp bracket.
            "sl_pct": 8.0,
            "tp_pct": 0.0,
            "confidence": 0.60,
            "reason": (f"TSMOM long: {base} {self.lookback}d momentum positive "
                       f"(close {c_now:.4f} > {c_past:.4f})"),
            "position_id": "",
            "decision_id": str(uuid.uuid4()),
            "source": "tsmom",
        }

    @staticmethod
    def _close_action(pos: dict) -> dict:
        return {
            "type": "CLOSE",
            "symbol": pos.get("symbol", ""),
            "exchange": str(pos.get("exchange", "")).lower(),
            "market_type": pos.get("market_type", "futures"),
            "side": pos.get("side", "buy"),
            "leverage": 1,
            "size_pct": 0.0,
            "sl_pct": 0.0,
            "tp_pct": 0.0,
            "confidence": 0.70,
            "reason": "tsmom_momentum_flip",
            "rationale": "daily momentum flipped non-positive",
            "position_id": pos.get("id", ""),
            "decision_id": str(uuid.uuid4()),
            "source": "tsmom",
        }


# ── Phase 2b: tsmom identity + entry-shape helpers ───────────────────────────
# Shared by bot_engine (entry shaping + monitor guards) and order_manager
# (exit gating). A tsmom position is HELD until its own momentum-flip CLOSE, so
# every scalp early-exit is suppressed; only a wide disaster stop remains.

def is_tsmom_action(action) -> bool:
    """True if an OPEN/CLOSE action dict was produced by the TSMOM signal."""
    return str((action or {}).get("source", "")).lower() == "tsmom"


def is_tsmom_position(pos) -> bool:
    """True if a tracked Position was opened by the TSMOM signal.

    Keys on the persisted ``Position.strategy`` tag (threaded from
    ``action['source']`` at entry), so the exit policy follows how the position
    was OPENED — immune to a later ``SIGNAL_SOURCE`` flip with positions open.
    """
    return str(getattr(pos, "strategy", "") or "").lower() == "tsmom"


def tsmom_entry_shape(action, default_sl_pct: float = 8.0):
    """Entry shape for a tsmom OPEN: ``(sl_pct, tp_pct, leverage, bypass_rr)``.

    Wide disaster stop (the signal's ``sl_pct``, default 8%), NO take-profit
    (``tp_pct=0`` — the exit is the daily momentum flip, not a fixed target),
    unleveraged (leverage 1), and ``bypass_rr=True`` because a no-TP position has
    an undefined reward:risk ratio that the scalp R:R gate would otherwise reject.
    """
    try:
        sl = float(action.get("sl_pct", 0) or 0)
    except (TypeError, ValueError):
        sl = 0.0
    if sl <= 0:
        sl = default_sl_pct
    return sl, 0.0, 1, True
