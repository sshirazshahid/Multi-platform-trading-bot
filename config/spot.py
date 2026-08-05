"""Spot portfolio, partial TP, capital allocation."""
import os

NEWS = {
    "scan_interval_min": 30,  # Default scan interval (minutes)
    "fast_scan_interval_min": 10,  # When volatility is high (F&G < 20 or > 80)
    "max_headlines": 50,  # Max headlines to keep per scan
    "breaking_news_alert": True,  # Log breaking news at WARNING level
    "sentiment_history_days": 7,  # Days of per-coin sentiment history to keep
    # X/Twitter curated accounts (2026-07-28): advisory headlines only.
    # Official path needs X_BEARER_TOKEN or TWITTER_BEARER_TOKEN; RSSHub
    # fallback is best-effort when TWITTER_RSS_FALLBACK=true (default).
    "twitter_enabled": os.getenv("TWITTER_NEWS_ENABLED", "false").lower() == "true",  # De-Emotion
    "twitter_rss_fallback": os.getenv("TWITTER_RSS_FALLBACK", "true").lower() == "true",
    "twitter_max_results": int(os.getenv("TWITTER_MAX_RESULTS", "20")),
    "twitter_rsshub_base": os.getenv("TWITTER_RSSHUB_BASE", "https://rsshub.app"),
    # Empty → module DEFAULT_TWITTER_ACCOUNTS; override with comma handles.
    "twitter_accounts": tuple(
        a.strip().lstrip("@")
        for a in os.getenv("TWITTER_ACCOUNTS", "").split(",")
        if a.strip()
    ),
}

# ==============================================================
# STRATEGY ENABLE FLAGS — only Trend Pullback + Range Mean Reversion active
# ==============================================================
ENABLE_DCA = False
ENABLE_REBALANCE = False

# ==============================================================
# PARTIAL TAKE PROFIT
# ==============================================================
# 2026-05-19 sweet-spot retune. Captures more wins in the empirically
# proven 30-60min hold-time cell (67% WR, +$10.05 / 33 trades / 30d).
# Lowered first_take_at_pct from 0.5 to 0.35 (fires earlier) and raised
# first_take_size from 0.5 to 0.6 (books larger chunk early), so more
# positions book a small win before deteriorating into the
# 120-240min bleed band (-$23.63 / 30d).
# Rollback: revert both values to 0.5, restart bot. Sub-1-minute reversal.
# Spec: docs/superpowers/specs/2026-05-19-sweet-spot-partial-tp-retune-design.md
#
# 2026-05-25 — first_take_size 0.6 → 0.3. The 5-agent forensic swarm
# (memory: no-edge-forensics-2026-05-25) traced the realized R:R of 0.63
# (vs configured 1.2) to THIS line: booking 60% of every winner at
# first_take_at_pct (= +0.63% on a 1.8% TP = 0.42 R) then breakevening
# the rest mechanically caps blended R at ~0.63 under any realistic
# continuation. avg_win was $0.41 vs avg_loss $0.79 (win/loss ratio
# 0.526). Lowering the early book to 30% lets 70% of each winner ride to
# the full TP, lifting avg_win toward ~$0.65 (expectancy -$0.24 → -$0.13
# per trade). first_take_at_pct stays at 0.35 so the early-win capture in
# the 30-60min cell is preserved, just smaller. Rollback: set back to 0.6.
PARTIAL_TP = {
    "enabled": True,
    "first_take_at_pct": 0.35,
    "first_take_size": 0.3,
    "move_sl_to_breakeven": True,
}

# ==============================================================
# SPOT PORTFOLIO MANAGEMENT
# 2026-04-14 learning-first pivot: recommendation-only. SpotManager still
# evaluates holdings each cycle but writes HOLD/TRIM/EXIT/ROTATE_TO_USDT
# suggestions to data/spot_recommendations.jsonl instead of executing them.
# Spec §13: "No autonomous futures funding from spot during the rebuild
# phase." Hedging is disabled until futures proves positive expectancy.
# ==============================================================
# SPOT-PROTECT-V2 (May 2026): model-driven early SCALE_OUT.
# When the promoted spot ensemble outputs p_win_ensemble < p_win_floor
# AND the holding is already past drawdown_pct from peak, take half off.
# Complements the deterministic SPOT-PROTECT-V1 25%/40% peak-DD rules
# with model-aware risk reduction. No-op when no spot model is promoted.
SPOT_PROTECT_V2 = {
    "enabled": True,
    "drawdown_pct": 0.15,
    "p_win_floor": 0.40,
}

# Rev 5 Phase 5 — Spot S1 defensive allocation (risk control, PAPER-only).
# Opt-in: default OFF so runtime behavior is unchanged until the owner flips it.
# When enabled, SpotPortfolioManager.run_s1_cycle emits REBALANCE
# recommendations to data/spot_recommendations.jsonl — never orders.
SPOT_S1 = {
    "enabled": os.getenv("SPOT_S1_ENABLED", "false").lower() == "true",
}

SPOT_PORTFOLIO = {
    "enabled": True,
    # 2026-05-01 (under-supervision readiness pass): flip to False so
    # SPOT-PROTECT-V1 (peak-DD half/full exits at -25%/-40%) actually
    # places SELL orders. Was True since 2026-04-14 learning-first pivot.
    # User explicitly authorized autonomous defensive sells. The
    # threshold is deep (-25% from peak) so triggers are rare; bot
    # only sells what's already deeply drawn down — not hair-trigger.
    # Hedge_via_futures STAYS off (no perp overlay on spot DD).
    # Sell_on_structure_break STAYS off (we only act on peak-DD).
    "recommendation_only": False,
    "scan_interval_min": 30,
    "cost_basis_method": "average",
    "scale_out_threshold_pct": 0.20,
    "sell_on_structure_break": False,  # disabled — only peak-DD triggers exits
    "hedge_via_futures": False,  # disabled — no derivatives overlay
    "hedge_drawdown_pct": 0.10,
}

# ==============================================================
# CAPITAL ALLOCATION (Spot ↔ Futures)
# 2026-04-14 learning-first pivot: disabled. CapitalAllocator still runs
# to *evaluate* transfer opportunities and writes them to
# data/allocation_recommendations.jsonl, but never calls exchange.transfer().
# Re-enable only after paper trading proves positive expectancy AND owner
# signs docs/CONTROLLED_LIVE_CHECKLIST.md.
# ==============================================================
CAPITAL_ALLOCATION = {
    # Learning-first / safety-first: keep the evaluator enabled so it can
    # write transfer opportunities for review, but never call wallet-transfer
    # APIs unless CONTROLLED_LIVE has been explicitly armed and this flag is
    # deliberately changed.
    "enabled": True,
    "recommendation_only": True,  # recommendations only; no real transfers
    "cycle_interval_min": 15,
    "accumulation_threshold_usd": 10.0,
    "spot_targets": {"BTC": 0.50, "ETH": 0.50},
    "hedge_drawdown_pct": 0.10,
    "max_transfer_pct": 0.20,
    "min_transfer_usdt": 5.0,
    "rebalance_threshold_pct": 0.60,
}

# ==============================================================
# STRATEGY GATE — auto-disable underperformers
# ==============================================================
STRATEGY_GATE = {
    "min_win_rate": 0.50,  # disable below 50% WR
    "min_sample_size": 15,  # need 15+ trades before judging
    "fee_alert_ratio": 0.20,  # alert if fees > 20% of gross profit
    "auto_disable_fee_heavy": True,
}

# ==============================================================
# SCALING CONDITIONS — scale risk only after proving edge
# ==============================================================
SCALING = {
    "min_live_trades": 200,
    "min_win_rate": 0.60,
    "max_drawdown_for_scale": 0.10,
    "scale_factor": 1.5,
}
