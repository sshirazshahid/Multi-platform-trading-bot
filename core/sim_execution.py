"""
core/sim_execution.py — DRY_RUN execution realism (2026-04-11)

Paper trading drifted from LIVE reality in three ways that combined to turn
two weeks of profitable dry-run into live losses:

  1. Fill price: paper used `ticker.last` (a midpoint-ish reference).
     Real market orders cross the spread and then slip through the book
     for size; the paper fill was systematically ~5 bps better than
     reality on both sides of every trade — a ~10 bps round-trip drag
     the paper account never paid.

  2. SL/TP trigger: paper compared `ticker.last` at poll time. If an
     intrabar wick dipped below SL and recovered between polls, paper
     was oblivious while LIVE's exchange-side SL order had already
     stopped the position out. Result: paper kept winners that LIVE
     would have stopped — survivorship bias inflating paper WR.

  3. Funding: paper futures never paid the 8-hour funding, so any
     carry-heavy symbol looked free to hold.

This module is the bridge. `order_manager.py` consults it whenever
`self.dry_run` is True, so paper fills and paper SL/TP triggers behave
like LIVE. The cumulative slippage cost is also exposed so the dashboard
can show how much of the paper P&L was "borrowed" from LIVE reality.

Stateless except for a small slippage counter used for diagnostics.
"""
from typing import Optional, Tuple

from loguru import logger

try:
    from config import SLIPPAGE as _SLIP_CFG
except Exception:  # pragma: no cover — config should always import
    _SLIP_CFG = {}


class SimExecutionModel:
    """
    Realistic fill-price + wick SL/TP simulator for DRY_RUN.

    Safe to instantiate once per OrderManager; holds only a cumulative
    slippage-cost counter for diagnostics (not position state).
    """

    def __init__(self):
        self.enabled      = bool(_SLIP_CFG.get("enabled",      True))
        self.open_slip    = float(_SLIP_CFG.get("pct_open",      0.0005))
        self.close_slip   = float(_SLIP_CFG.get("pct_close",     0.0005))
        self.sl_slip      = float(_SLIP_CFG.get("pct_stop_loss", 0.0010))
        self.wick_enable  = bool(_SLIP_CFG.get("wick_sl_tp",  True))
        self.funding_on   = bool(_SLIP_CFG.get("funding",     True))
        self.prefer_book  = bool(_SLIP_CFG.get("prefer_book", True))
        # Cumulative slippage paid in USDT-equivalent, for the dashboard
        # "sim-live divergence" panel.
        self._cum_cost_usd = 0.0
        self._fills        = 0

    # ── Fill price ─────────────────────────────────────────────────────

    def paper_fill_price(
        self, exchange, symbol: str, side: str, market_type: str,
        base_price: Optional[float] = None, phase: str = "open",
        size: float = 0.0,
    ) -> float:
        """
        Compute a realistic paper fill price.

        Strategy:
          1. Fetch ticker (bid / ask / last) once.
          2. If bid/ask present and prefer_book is on: buy at ask, sell at bid.
          3. Apply directional slippage on top (crosses book further).
          4. If ticker has no book, fall back to last + slippage.

        `phase` picks the slippage constant:
          "open"  → self.open_slip
          "close" → self.close_slip
          "stop"  → self.sl_slip  (wider — fast-move fills are worse)
        """
        try:
            ticker = exchange.fetch_ticker(symbol, market_type) or {}
        except Exception:
            ticker = {}

        bid  = _safe_float(ticker.get("bid"))
        ask  = _safe_float(ticker.get("ask"))
        last = _safe_float(ticker.get("last") or ticker.get("close"))
        if not last and base_price:
            last = float(base_price)
        if not last or last <= 0:
            return float(base_price or 0.0)

        if not self.enabled:
            return float(last)

        slip = {
            "open":  self.open_slip,
            "close": self.close_slip,
            "stop":  self.sl_slip,
        }.get(phase, self.open_slip)

        if side == "buy":
            ref = ask if (self.prefer_book and ask > 0) else last
            fill = ref * (1.0 + slip)
        else:  # sell / short-open / long-close
            ref = bid if (self.prefer_book and bid > 0) else last
            fill = ref * (1.0 - slip)

        # Diagnostics: cost paid = (fill - last) * size, signed by direction
        try:
            cost_usd = abs(fill - last) * max(float(size), 0.0)
            if cost_usd > 0:
                self._cum_cost_usd += cost_usd
                self._fills += 1
        except Exception:
            pass

        return float(fill)

    # ── Wick-based SL/TP detection ─────────────────────────────────────

    def check_wick_trigger(
        self, exchange, symbol: str, market_type: str, side: str,
        sl: float, tp: float,
    ) -> Tuple[Optional[str], Optional[float]]:
        """
        Did the last 1m candle's high/low cross SL or TP?

        If both were touched in the same candle we pessimistically assume
        SL fired first — matches worst-case LIVE behavior for a losing
        direction and keeps paper accounting honest.

        Returns:
          ("stop_loss",   sl_price) — SL wicked
          ("take_profit", tp_price) — TP wicked
          (None,          None)    — neither
        """
        if not self.wick_enable:
            return (None, None)

        try:
            candles = exchange.fetch_ohlcv(
                symbol, "1m", limit=2, market_type=market_type)
        except Exception:
            return (None, None)
        if not candles:
            return (None, None)

        # Last closed candle: [timestamp, open, high, low, close, volume]
        c = candles[-1]
        try:
            high = float(c[2])
            low  = float(c[3])
        except (IndexError, TypeError, ValueError):
            return (None, None)

        if side == "buy":
            hit_sl = sl > 0 and low  <= sl
            hit_tp = tp > 0 and high >= tp
            if hit_sl:   # pessimistic
                return ("stop_loss",   sl)
            if hit_tp:
                return ("take_profit", tp)
        else:
            hit_sl = sl > 0 and high >= sl
            hit_tp = tp > 0 and low  <= tp
            if hit_sl:
                return ("stop_loss",   sl)
            if hit_tp:
                return ("take_profit", tp)
        return (None, None)

    # ── Funding cost ───────────────────────────────────────────────────

    def funding_payment(self, position, mark_price: float,
                        funding_rate: float) -> float:
        """
        Funding paid over one 8h interval for a held futures position.

          long  pays when rate > 0 (loss  to wallet)
          short pays when rate < 0 (gain  to wallet)

        Returns a signed USDT number — positive means wallet debit, negative
        means wallet credit. Call this once per 8h funding boundary.
        """
        if not self.funding_on:
            return 0.0
        if getattr(position, "market_type", "") != "futures":
            return 0.0
        notional = float(position.size) * float(mark_price)
        if notional <= 0:
            return 0.0
        rate = float(funding_rate or 0.0)
        if position.side == "buy":
            return notional * rate
        return -notional * rate

    # ── Diagnostics ────────────────────────────────────────────────────

    def cumulative_slippage_usd(self) -> float:
        """Total USDT-equivalent slippage that paper has paid since start."""
        return round(self._cum_cost_usd, 4)

    def fill_count(self) -> int:
        return self._fills


def _safe_float(x) -> float:
    try:
        v = float(x)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0
