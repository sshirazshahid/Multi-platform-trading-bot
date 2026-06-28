"""Deployable-balance aggregation shared across the decision pipeline.

Single source of truth for "how much USDT can the bot deploy across all
exchanges". The subtlety it encapsulates: a balances dict stores per-exchange
``{"spot": x, "futures": y}``, but spot and futures are the SAME underlying
wallet when

  (a) running in PAPER mode — the virtual wallet mirrors one balance into both
      slots (see ``BotEngine._log_balances``), or
  (b) the exchange uses a Unified Trading Account (Bybit).

In those cases the two slots are the same money and must be counted ONCE. Only a
live, non-unified exchange (Binance/Bitget) has independent spot + futures
wallets that should be summed.

Prior to this helper, four call sites in ``bot_engine`` and one in ``mcp_brain``
each summed ``spot + futures`` for non-unified exchanges. In PAPER mode that
double-counted Binance and Bitget (e.g. 5340*2 + 5227*2 + 5220 = $26,355 instead
of the true $15,787), inflating the equity base used for position sizing, the
§2 exposure cap, and the correlation denominator.
"""
from __future__ import annotations

from config import DRY_RUN

# Exchanges that use a single Unified Account (spot wallet == futures wallet).
UNIFIED_EXCHANGES = {"bybit"}


def deployable_total(balances: dict | None, *, dry_run: bool | None = None) -> float:
    """Sum deployable USDT across exchanges, counting each wallet once.

    Args:
        balances: mapping of exchange name -> {"spot": float, "futures": float}.
        dry_run: override the PAPER/live discriminator (defaults to config.DRY_RUN).
            In PAPER mode every exchange's two slots mirror one wallet, so each
            is counted once regardless of unified status.
    """
    paper = DRY_RUN if dry_run is None else dry_run
    total = 0.0
    for ex, bal in (balances or {}).items():
        spot = float(bal.get("spot", 0) or 0)
        futures = float(bal.get("futures", 0) or 0)
        if paper or ex in UNIFIED_EXCHANGES:
            total += spot or futures  # same wallet — count once
        else:
            total += spot + futures
    return total
