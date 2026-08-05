"""External data feed configuration for MCP scoring."""
import os

from config.gates import SMART_MONEY_ENTRY_GATE

DATA_FEEDS = {
    # ── Master switches ────────────────────────────────────────────
    "funding_enabled": True,
    "oi_enabled": True,
    "orderbook_enabled": True,
    "news_enabled": False,  # De-Emotion: feed deleted
    "smart_money_enabled": True,
    # ── Cache TTLs (seconds) ───────────────────────────────────────
    # Each feed refreshes independently at its own cadence.
    # Staleness threshold = TTL * staleness_multiplier.
    "funding_ttl": 300,  # 5 min (FR settles 3x/day but predicted rate drifts)
    "oi_ttl": 180,  # 3 min (OI updates every ~10s on Binance)
    "orderbook_ttl": 60,  # 1 min (most volatile source)
    "news_ttl": 600,  # 10 min (news doesn't move faster than this)
    "smart_money_ttl": 900,  # 15 min (on-chain aggregate, slow-moving)
    "staleness_multiplier": 2.0,  # >2x TTL = data marked stale, use neutral defaults
    "max_workers": 5,  # ThreadPoolExecutor workers for parallel refresh
    # ── Scoring weights (MCP Brain bonus points) ───────────────────
    # B11: Funding rate alignment bonus
    #   FR z-score aligns with proposed side (extreme neg FR + long = bonus)
    # ⚠ DISABLED 2026-05-30: SCREENED at owner's request (run_edge_sweep.py, 31
    #   syms x 210 8h-bars through 2026-05-30) -> NO_EDGE, weakly ANTI-predictive.
    #   B11's exact construction is the per-symbol funding z-fade (B_ts_zfade):
    #   Sharpe -1.29; cross-sectional fade A_H1 IR +0.26/-0.04 (vs the 0.50 bar);
    #   time-series funding IC mean -0.055, t=-3.70 (significantly negative).
    #   A +7 bonus on an anti-predictive signal was inflating live entry scores.
    #   Mechanism kept; re-enable only if a funding signal clears IR>=0.50.
    "b11_funding_enabled": False,
    "b11_funding_points": 7,  # points awarded when FR aligns
    "b11_fr_zscore_threshold": 1.5,  # |z| must exceed this to qualify
    # B12: OI-price divergence confirmation bonus
    #   "continuation" signal when entering WITH the trend
    # ⚠ DISABLED 2026-05-30: OI-divergence (H5 = sign(dPrice)*dOI) was finally
    #   screened on 28 syms x 100 daily bars (scripts/run_oi_edge_screen.py) and
    #   FAILED stage-1 by a wide margin (IR -0.06 @1d, -0.07 @3d vs the 0.50 bar;
    #   DSR~0). NO_EDGE. Removing the +8 stops noise from inflating entry scores.
    "b12_oi_enabled": False,
    "b12_oi_points": 8,  # points for continuation signal
    "b12_oi_conviction_min": 0.3,  # minimum conviction to award
    # B13: Smart money alignment bonus
    #   Coin in top-20 smart money inflow AND proposed side = buy
    # 2026-05-30: disabled (unscreenable snapshot). 2026-07-24: owner Approach 1
    # hard entry gate — when SMART_MONEY_ENTRY_GATE is active, B13 bonus is
    # re-enabled by default (override with B13_SMART_MONEY_ENABLED=false).
    "b13_smart_money_enabled": (
        os.getenv(
            "B13_SMART_MONEY_ENABLED",
            "true" if SMART_MONEY_ENTRY_GATE.get("enabled") else "false",
        ).lower()
        == "true"
    ),
    "b13_smart_money_points": 5,  # points when smart money confirms
    # ── VETO gates (block entry, not just reduce score) ────────────
    # V1: OI exhaustion veto — price up + OI down with high conviction
    #   Blocks LONG entries when the move is short-covering, not new money
    # ⚠ DISABLED 2026-05-30: rests on the SAME OI-divergence signal B12 uses,
    #   which screened NO_EDGE (see B12). A veto firing on a falsified signal is
    #   noise, not protection — and blocking longs on noise contradicts the
    #   owner's UNBLOCK_ALL stance. Cost/risk vetoes V2/V3 stay ON. (Re-enabling
    #   trades fewer/more is a turnover knob, not an edge — owner's call.)
    "v1_oi_exhaustion_veto": False,
    "v1_oi_exhaustion_conviction_min": 0.5,
    # V3: Slippage veto — estimated slippage > threshold
    "v3_slippage_veto": True,
    "v3_slippage_max_bps": 30.0,  # max 30 bps slippage
    # ── B6 orderbook-imbalance bonus ───────────────────────────────
    # ⚠ DISABLED 2026-05-30 (owner directive): the B6 bonus (+7) rewards live
    #   L2 orderbook imbalance + funding direction. It is UNSCREENABLE — L2
    #   depth is a point-in-time snapshot (CoinDesk history paywalled/403, ccxt
    #   snapshot-only), so it can't be validated against forward returns; and
    #   its funding leg screened NO_EDGE/anti-predictive (B11). Top-of-book
    #   imbalance has no documented edge at 15-60min+ holds and is spoofable.
    #   `b6_orderbook_enabled` is the MASTER gate (off => no B6 bonus at all,
    #   enhanced OR legacy). The V3 slippage veto uses the same feed and stays
    #   ON. To revisit, log imbalance forward then screen.
    "b6_orderbook_enabled": False,
    # When B6 is on, `enhanced_b6_enabled` picks the enhanced orderbook feed
    # (imbalance momentum, depth ratio) over the basic Binance depth fetch.
    "enhanced_b6_enabled": True,
    "enhanced_b6_points": 7,  # was 5 in legacy B6
    # ── Short-side filter integration ──────────────────────────────
    # Short side uses STRICTER thresholds on data feed signals
    "short_side_stricter_feeds": True,
    "short_fr_zscore_threshold": 1.0,  # lower bar = more shorts filtered
}

# Convenience: per-feed env-var overrides for operational toggling
# without editing config.py. Set FEED_FUNDING_ENABLED=false etc.
for _feed_key in ("funding", "oi", "orderbook", "news", "smart_money"):
    _env_key = f"FEED_{_feed_key.upper()}_ENABLED"
    _env_val = os.getenv(_env_key, "").strip().lower()
    if _env_val == "false":
        DATA_FEEDS[f"{_feed_key}_enabled"] = False
    elif _env_val == "true":
        DATA_FEEDS[f"{_feed_key}_enabled"] = True
