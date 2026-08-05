"""Fee structure and paper slippage realism."""
import os

DRY_RUN_BALANCE = float(os.getenv("DRY_RUN_BALANCE", "100.0"))

# ==============================================================
# EXCHANGE FEE STRUCTURE
# ==============================================================
FEE = {
    # Binance (also generic fallback)
    "spot_taker": 0.001,
    "spot_maker": 0.001,
    "futures_taker": 0.0005,
    "futures_maker": 0.0002,
    # Binance explicit keys (arb engine looks up "{exchange}_spot_taker")
    "binance_spot_taker": 0.001,
    "binance_futures_taker": 0.0005,
    # Bybit
    "bybit_spot_taker": 0.001,
    "bybit_spot_maker": 0.001,
    "bybit_futures_taker": 0.0006,
    "bybit_futures_maker": 0.0001,
    # Bitget
    "bitget_spot_taker": 0.001,
    "bitget_spot_maker": 0.001,
    "bitget_futures_taker": 0.0006,
    "bitget_futures_maker": 0.0002,
}

# ==============================================================
# SIM-LIVE REALISM  (2026-04-11)
# ==============================================================
# After 2 weeks of DRY_RUN profits turned into LIVE losses, we diagnosed
# three execution-path gaps:
#
#   1. Paper fills used `ticker.last` (a midpoint-ish price). Real market
#      orders cross the spread and slip through the book.
#   2. Paper SL/TP compared `ticker.last` at poll time — an intrabar wick
#      that dipped below SL and recovered was invisible to paper, so paper
#      kept losers that LIVE would have stopped out (survivorship bias).
#   3. Paper futures never paid funding.
#
# These knobs make DRY_RUN's execution match LIVE closely enough that a
# paper P&L curve should predict a live P&L curve within ~1 sigma.
SLIPPAGE = {
    "enabled": True,  # Master switch for the slippage model.
    "pct_open": 0.0005,  # 5 bps on open — cross spread + walk book
    "pct_close": 0.0005,  # 5 bps on close
    "pct_stop_loss": 0.0010,  # 10 bps on stop-out — SL fills worse in fast moves
    "wick_sl_tp": True,  # Use last 1m candle high/low for SL/TP trigger
    "funding": True,  # Charge funding every 8h on paper futures
    "prefer_book": True,  # Prefer ticker bid/ask over ticker last when available
}
