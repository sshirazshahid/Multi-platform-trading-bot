"""Execution overrides, maker-only, SL/TP triggers, scalp mode."""
import os

# ==============================================================
# PER-PAIR SL/TP OVERRIDES (Phase 15, 2026-05-03)
# ==============================================================
# Empirically-calibrated SL/TP per symbol from 30-day realized warehouse
# data. Overrides the default ATR×1.5 SL + 2:1 R:R for symbols where the
# realized R:R distribution clearly favors a different ratio.
#
# Methodology: per-symbol futures stats (n>=8 trades, 30d):
#   ARB:  WR 44%, R:R 2.23 → push TP to 3.5x SL (proven follow-through)
#   ORDI: WR 50%, R:R 3.67 → push TP to 4.5x SL (fastest pair, 8min holds)
#   ATOM: WR 42%, R:R 1.76 → tighten TP to 2.5x SL to fit Phase 15 (75min)
#   ETH:  WR 75%, R:R 1.69 → tighten TP to 1.5x SL to capture in 75min
#   DOT:  WR 29%, R:R 2.51 → keep wider R:R, low WR
#   DOGE: WR 62%, R:R 0.82 → tighten TP to fix R:R asymmetry
#
# Floor: SL minimum 1.5% per RISK clamp (zone-tightening invariant).
# Symbols not in this map fall through to default ATR-based + STAR
# extender + S/D zone tightening.
#
# To disable per-pair calibration entirely, set PAIR_OVERRIDES = {}.
PAIR_OVERRIDES = {
    # Two schemas supported (per Phase 15.3, 2026-05-03):
    #   FLAT  : {"sl_pct": X, "tp_pct": Y}          — applies symmetrically
    #   SIDED : {"long":  {"sl_pct": X, "tp_pct": Y},
    #            "short": {"sl_pct": X, "tp_pct": Y}}
    # Memory feedback_short_side_filter / 2026-04-30 attribution: 126 shorts
    # net -$54 vs 210 longs net -$4 — shorts harder to capture at this fee
    # tier. Per-side schema lets us tighten short TPs without touching longs.
    #
    # Proven winners — push R:R higher
    "ARB/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 3.5},  # R:R 2.33, ARB is STAR
    "ORDI/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 4.5},  # R:R 3.00, fastest pair
    "ATOM/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.5},  # R:R 1.67, ATOM is STAR
    "DOT/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 3.0},  # R:R 2.00
    # High-WR pairs — tighten TP to reach within Phase 15 horizon
    "ETH/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.2},  # 75% WR, fast capture
    "FET/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.2},  # 57% WR
    "ALGO/USDT:USDT": {"sl_pct": 1.5, "tp_pct": 2.0},  # 56% WR
    # Asymmetric R:R + per-side tuning: shorts tighter than longs
    "BNB/USDT:USDT": {
        "long": {"sl_pct": 1.5, "tp_pct": 2.0},
        "short": {"sl_pct": 1.5, "tp_pct": 1.6},  # tighter R:R for the harder side
    },
    "DOGE/USDT:USDT": {
        "long": {"sl_pct": 1.5, "tp_pct": 2.0},
        "short": {"sl_pct": 1.5, "tp_pct": 1.6},
    },
    # Note: SOL/XRP/AAVE/AVAX/LINK left to fall through to ATR defaults.
}

# ==============================================================
# MAKER-ONLY EXECUTION (Phase 15, 2026-05-03)
# ==============================================================
# Bybit futures maker fee = 0.01% (vs 0.06% taker). Round-trip drops from
# 0.12% to 0.02% — making 5m strategies that were marginal at taker fees
# (60% fee burden vs typical move) viable (10% fee burden).
#
# Trade-off: postOnly limit orders can be REJECTED if they would cross
# the spread, and may NOT fill on fast-moving setups. When enabled, the
# bot skips the trade after `max_wait_sec` instead of falling back to
# market — preserving fee savings at the cost of missed entries.
#
# Default OFF for safety. Flip to True (or set MAKER_ONLY_ENABLED=true)
# to take the fee saving once convinced the missed-fill cost is less
# than the fee saving on the trades that DO fill.
MAKER_ONLY = {
    # Default ON as of 2026-05-29, per owner directive, for a LIVE maker-fee
    # soak test. Safely integrated 2026-05-29 (tests/test_maker_only_integration.py):
    # the executor reports a partial fill as 'partial_maker' (never a silent skip),
    # and order_manager._interpret_execution_result opens NO position on
    # skip/uncertain (no phantom — old code keyed only on order["id"]) and sizes a
    # partial to the actual fill (SL/TP cover the real position).
    # HONEST CAVEAT (unchanged): maker entries are adversely selected (filled on
    # losers, skipped on the bounce), so maker-only does NOT improve EV — the
    # scalp-edge study found it negative across 3yr folds. The soak measures the
    # realized maker-fill RATE and FEE saving, NOT profitability (known-negative).
    # Also: paper bypasses the executor, so judge ONLY on LIVE fills. Revert with
    # MAKER_ONLY_ENABLED=false.
    "enabled": os.getenv("MAKER_ONLY_ENABLED", "true").lower() == "true",
    "max_wait_sec": int(os.getenv("MAKER_ONLY_MAX_WAIT_SEC", "120")),
}

# ==============================================================
# SL/TP TRIGGER PRICE BASIS (C3, tpbot retrofit 2026-07-08)
# ==============================================================
# When True, exchange-side SL/TP conditionals trigger on MARK price instead
# of the venue's default LAST price (Binance workingType=MARK_PRICE, Bybit
# triggerBy/slTriggerBy/tpTriggerBy=MarkPrice, Bitget triggerType=mark_price).
# Mark-price triggering ignores single rogue last-price prints (no
# wick-stopouts from one bad trade) and stays synced with the venue's
# liquidation engine. NOTE: ccxt 4.5.54 already defaults Bitget tpsl orders
# to mark_price — this flag makes the basis explicit on ALL venues.
# ⚠ WR-relevant semantics change (flagged per the WR-floor rule): fewer
# last-price wick stop-outs is expected neutral-to-positive, but the trigger
# feed changes in CONTROLLED_LIVE. Revert with SLTP_TRIGGER_MARK_PRICE=false.
# PAPER is unaffected (sim triggers on 1m last-price candles either way —
# see the honest divergence note in CLAUDE.md gotchas).
SLTP_TRIGGER_MARK_PRICE = (
    os.getenv("SLTP_TRIGGER_MARK_PRICE", "true").lower() == "true")

# ==============================================================
# SL-FAIL EMERGENCY CLOSE (Codex order_router port, 2026-07-12)
# ==============================================================
# Charter §2 Stop-Loss Guardian alignment: when exchange-side SL placement
# GENUINELY fails after the full retry ladder (BaseExchange.create_order
# retries incl. Bybit-110072 clientOrderId regen / Binance -4120 routing),
# the position is closed immediately through the NORMAL close path
# (close_position → tracker.close → on_close hooks, reason=
# 'sl_placement_failed') and the alert then reports "closed fail-safe".
# Default TRUE. The flag exists ONLY as an operator escape hatch: false
# restores the old behavior (_sl_failed=True + EMERGENCY alert + local
# polled SL + per-minute exchange-SL reconcile — position stays open
# WITHOUT exchange-side protection). Identical in PAPER and
# CONTROLLED_LIVE (close_position sim-closes in paper).
SL_FAIL_EMERGENCY_CLOSE_ENABLED = (
    os.getenv("SL_FAIL_EMERGENCY_CLOSE_ENABLED", "true").lower() == "true")

# OHLCV integrity validation (Codex port, 2026-07-12): every
# BaseExchange.fetch_ohlcv result is checked for monotonic timestamps,
# finite values, OHLC range sanity and staleness (exchanges/base.py::
# validate_ohlcv). A defective series is replaced by [] (the existing
# no-data return) so callers skip the symbol for one cycle — fail-closed
# for the data, fail-open for the bot. Kill flag for emergencies only.
OHLCV_VALIDATION_ENABLED = (
    os.getenv("OHLCV_VALIDATION_ENABLED", "true").lower() == "true")

# C8 (tpbot retrofit 2026-07-08): venue clock-drift alert threshold (ms).
# The 60s health cycle samples an NTP-style offset per venue; sustained
# drift beyond this warns in-engine and edge-alerts via health_watchdog.
# Exchanges reject signed requests when drift approaches recvWindow — the
# repo's deployment notes call Windows drift "the #1 silent killer".
CLOCK_DRIFT_ALERT_MS = int(os.getenv("CLOCK_DRIFT_ALERT_MS", "500"))

# ==============================================================
# SCALP MODE — 15-60 minute VWAP-centric entries (2026-05-27)
# ==============================================================
# Evidence: 454-trade dataset shows 15-60m holds = 63.8% WR (+$4.41)
# vs 0-15m = 32.3% WR (-$41.10) and >60m = 35-38% WR.
# VWAP + Long = 61.9% WR — strongest signal in the dataset.
# B1 MACD (25% WR) and B3 15m timing (35% WR) are anti-predictive.
SCALP_MODE = {
    "enabled": os.getenv("SCALP_MODE_ENABLED", "true").lower() == "true",
    "entry_threshold": int(os.getenv("SCALP_ENTRY_THRESHOLD", "65")),
    # 2026-05-28: tightened SL/TP for $1-2/trade target at $130+ notional.
    # Old 1.0/1.8 produced $0.62/$1.12 per trade at $62 notional — too small.
    # New 0.8/1.3 at $136 notional (35% × 3x × $130 pocket):
    #   TP hit: $136 × 1.3% = $1.77   SL hit: $136 × 0.8% = $1.09
    #   R:R = 1.625:1, clears min_rr_ratio 1.2:1
    "sl_pct": float(os.getenv("SCALP_SL_PCT", "0.8")),
    "tp_pct": float(os.getenv("SCALP_TP_PCT", "1.3")),
    # 2026-05-28: raised for 15m-1h candle entries; 60min/45min was killing winners
    "time_wall_min": int(os.getenv("SCALP_TIME_WALL_MIN", "180")),
    "stale_close_min": int(os.getenv("SCALP_STALE_MIN", "120")),
    "stale_min_profit": float(os.getenv("SCALP_STALE_PROFIT", "0.3")),
    "trailing_enabled": os.getenv("SCALP_TRAILING", "false").lower() == "true",
    "longs_only": os.getenv("SCALP_LONGS_ONLY", "true").lower() == "true",
    "min_atr_pct": float(os.getenv("SCALP_MIN_ATR", "0.8")),
    "max_spread_bps": float(os.getenv("SCALP_MAX_SPREAD_BPS", "10")),
    "vwap_distance_max_pct": float(os.getenv("SCALP_VWAP_MAX", "0.3")),
    "peak_hours": {22, 23, 0},
    "partial_tp": {
        "enabled": True,
        "fraction": 0.5,
        "at_pct": 1.0,
        "move_sl_to_be": True,
    },
}
