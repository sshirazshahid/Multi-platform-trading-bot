"""
core/prediction_prompt.py — Prompt templates for the prediction sub-agent.

Design principles:
  1. Structured data presentation optimized for LLM pattern recognition
  2. Historical context (last 10 trades, WR, avg outcome) for calibration
  3. Market regime context (trending/ranging/volatile)
  4. Explicit "pre-mortem" section forcing failure-mode analysis
  5. Strict JSON output with confidence calibration instructions
  6. Token-efficient: ~2000-3000 tokens input for a typical prediction

The prompt is split into SYSTEM (role + output format) and USER (data + task).
This matches the existing convention in prompts/claude/*.md.
"""

from __future__ import annotations

import json
from typing import Any


def build_prediction_prompt(context: dict) -> tuple[str, str]:
    """Build (system_prompt, user_prompt) for a prediction call.

    Args:
        context: The structured context dict from prediction_agent._build_context

    Returns:
        Tuple of (system_prompt, user_prompt) strings.
    """
    system_prompt = _SYSTEM_PROMPT
    user_prompt = _build_user_prompt(context)
    return system_prompt, user_prompt


# ---------------------------------------------------------------------------
# System prompt — role definition + output schema
# ---------------------------------------------------------------------------

_SYSTEM_PROMPT = """\
You are a crypto trade prediction engine. You receive a candidate trade from a \
rules-based scoring system and rich market context. Your job: predict whether \
this trade will be profitable.

You are PREDICTION ONLY. You cannot place, modify, or cancel orders. You cannot \
override stops or change leverage. You analyze data and return a structured prediction.

RESPOND WITH STRICT JSON MATCHING EXACTLY THIS SHAPE:
{
  "prediction": "CONFIRM" | "VETO" | "WEAK_CONFIRM",
  "confidence": 0.0 to 1.0,
  "reasoning": "2-3 sentences explaining your prediction",
  "predicted_direction": "long" | "short" | "neutral",
  "predicted_move_pct": 1.5,
  "predicted_hold_minutes": 45,
  "risk_flags": ["flag1", "flag2"],
  "recommended_adjustments": {
    "sl_pct": 1.8,
    "tp_pct": 3.5,
    "size_multiplier": 0.7
  }
}

PREDICTION MEANINGS:
- CONFIRM: High conviction this trade will be profitable. Take it at full size.
- VETO: High conviction this trade will lose. Do NOT take it.
- WEAK_CONFIRM: Marginal signal. Trade is acceptable but reduce size.

CONFIDENCE CALIBRATION:
- 0.9+: You are very sure. Historically, 90%+ of your 0.9 calls should be correct.
- 0.7-0.9: Reasonable conviction. ~75% correct.
- 0.5-0.7: Coin flip territory. Default to WEAK_CONFIRM.
- Below 0.5: You are guessing. Default to WEAK_CONFIRM with size_multiplier 0.5.

RECOMMENDED_ADJUSTMENTS:
- sl_pct: Suggested stop-loss percent (1.0-5.0). Omit if default is fine.
- tp_pct: Suggested take-profit percent (1.0-10.0). Omit if default is fine.
- size_multiplier: 0.3-1.5. Below 1.0 = reduce size. Above 1.0 = increase size.

RISK_FLAGS — use these standard tags:
- "high_funding_rate" — funding rate > 0.03% against the trade direction
- "correlated_with_open" — same symbol or sector already in portfolio
- "extreme_volatility" — ATR or recent range unusually large
- "thin_orderbook" — orderbook imbalance > 70% against trade
- "news_risk" — recent headlines suggest adverse event
- "regime_hostile" — market regime (chop/squeeze) opposes directional trade
- "overextended" — price far from mean, likely to revert
- "low_volume" — volume below normal, poor liquidity
- "counter_trend" — trade opposes the dominant 4h trend
- "time_risk" — historically bad hour/day for this setup

No extra keys. No markdown. No code fences. Return ONLY the JSON object."""


# ---------------------------------------------------------------------------
# User prompt builder
# ---------------------------------------------------------------------------

def _build_user_prompt(ctx: dict) -> str:
    """Assemble the data-rich user prompt from context.

    Token budget target: ~2000-3000 tokens. Each section is independently
    optional — missing data is clearly labeled so the model doesn't hallucinate.
    """
    sections = []

    # ── Section 1: Trade Candidate ────────────────────────────────────
    sections.append(_section_candidate(ctx))

    # ── Section 2: MCP Brain Breakdown ────────────────────────────────
    sections.append(_section_mcp_breakdown(ctx))

    # ── Section 3: Price Action ───────────────────────────────────────
    sections.append(_section_price_action(ctx))

    # ── Section 4: Funding & OI ───────────────────────────────────────
    sections.append(_section_funding_oi(ctx))

    # ── Section 5: Order Book ─────────────────────────────────────────
    sections.append(_section_orderbook(ctx))

    # ── Section 6: News ───────────────────────────────────────────────
    sections.append(_section_news(ctx))

    # ── Section 7: Historical Performance ─────────────────────────────
    sections.append(_section_history(ctx))

    # ── Section 8: Portfolio State ────────────────────────────────────
    sections.append(_section_portfolio(ctx))

    # ── Section 9: Pre-mortem ─────────────────────────────────────────
    sections.append(_section_premortem(ctx))

    # ── Task ──────────────────────────────────────────────────────────
    sections.append(_section_task(ctx))

    return "\n\n".join(s for s in sections if s)


def _section_candidate(ctx: dict) -> str:
    return f"""## TRADE CANDIDATE
- Symbol: {ctx.get('symbol', 'N/A')}
- Side: {ctx.get('side', 'N/A').upper()}
- MCP Score: {ctx.get('mcp_score', 'N/A')}/100
- Confidence: {_fmt_pct(ctx.get('confidence'))}
- Stop Loss: {_fmt_pct(ctx.get('sl_pct'), suffix='%')}
- Take Profit: {_fmt_pct(ctx.get('tp_pct'), suffix='%')}
- Leverage: {ctx.get('leverage', 'N/A')}x
- R:R Ratio: {_compute_rr(ctx)}"""


def _section_mcp_breakdown(ctx: dict) -> str:
    bd = ctx.get("mcp_breakdown")
    if not bd:
        reason = ctx.get("mcp_reason", "")
        if reason:
            return f"## MCP BRAIN ANALYSIS\n{reason}"
        return "## MCP BRAIN ANALYSIS\nNo breakdown available."

    lines = ["## MCP BRAIN ANALYSIS"]

    # Required conditions
    req = bd.get("required_conditions", {})
    if req:
        lines.append("Required conditions (all must pass):")
        for name, passed in req.items():
            status = "PASS" if passed else "FAIL"
            lines.append(f"  - {name}: {status}")

    # Bonus conditions
    bonus = bd.get("bonus_conditions", {})
    if bonus:
        lines.append("Bonus conditions:")
        for name, info in bonus.items():
            if isinstance(info, dict):
                lines.append(f"  - {name}: {info.get('status', '?')} (+{info.get('points', 0)})")
            else:
                lines.append(f"  - {name}: {info}")

    return "\n".join(lines)


def _section_price_action(ctx: dict) -> str:
    lines = ["## PRICE ACTION (last 20 candles)"]

    for label, key in [("1h", "candles_1h"), ("4h", "candles_4h")]:
        candles = ctx.get(key, [])
        if not candles:
            lines.append(f"\n{label} timeframe: No data available.")
            continue

        lines.append(f"\n{label} timeframe ({len(candles)} candles):")

        # Summary stats instead of raw candles (token-efficient)
        closes = [c.get("c", 0) for c in candles]
        highs = [c.get("h", 0) for c in candles]
        lows = [c.get("l", 0) for c in candles]
        volumes = [c.get("v", 0) for c in candles]

        if closes:
            latest = closes[-1]
            first = closes[0]
            change_pct = ((latest - first) / first * 100) if first > 0 else 0
            high = max(highs) if highs else 0
            low = min(lows) if lows else 0
            range_pct = ((high - low) / low * 100) if low > 0 else 0
            avg_vol = sum(volumes) / len(volumes) if volumes else 0
            recent_vol = sum(volumes[-3:]) / 3 if len(volumes) >= 3 else avg_vol
            vol_ratio = recent_vol / avg_vol if avg_vol > 0 else 1.0

            lines.append(f"  Current: {latest:.6g}")
            lines.append(f"  Period change: {change_pct:+.2f}%")
            lines.append(f"  High/Low range: {range_pct:.2f}%")
            lines.append(f"  Volume ratio (recent/avg): {vol_ratio:.2f}")

            # Last 5 candles for pattern recognition
            lines.append("  Last 5 candles [O,H,L,C]:")
            for c in candles[-5:]:
                body = "green" if c.get("c", 0) >= c.get("o", 0) else "red"
                lines.append(
                    f"    [{c.get('o',0):.6g}, {c.get('h',0):.6g}, "
                    f"{c.get('l',0):.6g}, {c.get('c',0):.6g}] {body}")

    return "\n".join(lines)


def _section_funding_oi(ctx: dict) -> str:
    funding = ctx.get("funding")
    oi = ctx.get("open_interest")

    if not funding and not oi:
        return "## FUNDING & OPEN INTEREST\nNo data available."

    lines = ["## FUNDING & OPEN INTEREST"]

    if funding:
        rate = funding.get("rate")
        if rate is not None:
            direction = "longs pay" if rate > 0 else "shorts pay"
            lines.append(f"- Funding rate: {rate:.4%} ({direction})")
            # Flag if funding is high
            if abs(rate) > 0.0003:
                lines.append(f"  WARNING: Elevated funding rate (>{0.03}%)")
        pred = funding.get("predicted_rate")
        if pred is not None:
            lines.append(f"- Predicted next: {pred:.4%}")

    if oi:
        oi_val = oi.get("oi_value")
        if oi_val is not None:
            lines.append(f"- Open Interest: ${oi_val:,.0f}")
        change_1h = oi.get("oi_change_pct_1h")
        if change_1h is not None:
            lines.append(f"- OI change 1h: {change_1h:+.2f}%")
        change_4h = oi.get("oi_change_pct_4h")
        if change_4h is not None:
            lines.append(f"- OI change 4h: {change_4h:+.2f}%")

    return "\n".join(lines)


def _section_orderbook(ctx: dict) -> str:
    ob = ctx.get("orderbook")
    if not ob:
        return "## ORDER BOOK\nNo data available."

    lines = ["## ORDER BOOK"]
    bid = ob.get("bid_depth_10")
    ask = ob.get("ask_depth_10")
    if bid is not None and ask is not None:
        total = bid + ask
        bid_pct = (bid / total * 100) if total > 0 else 50
        lines.append(f"- Bid depth (10 levels): ${bid:,.0f} ({bid_pct:.0f}%)")
        lines.append(f"- Ask depth (10 levels): ${ask:,.0f} ({100-bid_pct:.0f}%)")

    imb = ob.get("imbalance")
    if imb is not None:
        direction = "bid-heavy (bullish)" if imb > 0 else "ask-heavy (bearish)"
        lines.append(f"- Imbalance: {imb:+.2f} ({direction})")

    spread = ob.get("spread_pct")
    if spread is not None:
        lines.append(f"- Spread: {spread:.4f}%")

    return "\n".join(lines)


def _section_news(ctx: dict) -> str:
    headlines = ctx.get("news", [])
    if not headlines:
        return "## RECENT NEWS\nNo relevant headlines."

    lines = ["## RECENT NEWS"]
    for h in headlines[:10]:
        lines.append(f"- {h}")
    return "\n".join(lines)


def _section_history(ctx: dict) -> str:
    hist = ctx.get("symbol_history")
    if not hist:
        return "## HISTORICAL PERFORMANCE\nNo historical data for this symbol."

    lines = ["## HISTORICAL PERFORMANCE (last 30 days, this symbol)"]

    n = hist.get("n", 0)
    wr = hist.get("win_rate")
    avg_pnl = hist.get("mean")
    total_pnl = hist.get("sum")
    avg_r = hist.get("avg_r_multiple")
    avg_hold = hist.get("avg_hold_min")

    if n > 0:
        lines.append(f"- Trades: {n}")
        if wr is not None:
            lines.append(f"- Win rate: {wr:.0%}")
        if avg_pnl is not None:
            lines.append(f"- Avg PnL/trade: ${avg_pnl:+.2f}")
        if total_pnl is not None:
            lines.append(f"- Total PnL: ${total_pnl:+.2f}")
        if avg_r is not None:
            lines.append(f"- Avg R-multiple: {avg_r:.2f}")
        if avg_hold is not None:
            lines.append(f"- Avg hold time: {avg_hold:.0f} min")

        # Flag concerning patterns
        if wr is not None and wr < 0.40:
            lines.append(f"  WARNING: Win rate below 40% on this symbol")
        if avg_pnl is not None and avg_pnl < -0.20:
            lines.append(f"  WARNING: Negative historical expectancy")
    else:
        lines.append("- No closed trades for this symbol in the window.")

    return "\n".join(lines)


def _section_portfolio(ctx: dict) -> str:
    ps = ctx.get("portfolio_state", {})
    lines = ["## CURRENT PORTFOLIO STATE"]

    total = ps.get("total_open_positions", 0)
    lines.append(f"- Open positions: {total}")

    corr = ps.get("correlated_positions", [])
    if corr:
        lines.append(f"- WARNING: Same-symbol positions already open:")
        for p in corr:
            lines.append(
                f"  - {p.get('symbol')} {p.get('side')} on {p.get('exchange')} "
                f"(PnL: {p.get('pnl_pct', 0):+.1f}%)")
    else:
        lines.append("- No correlated positions.")

    if ps.get("same_symbol_open"):
        lines.append("  RISK: Adding to an existing position in same symbol.")

    return "\n".join(lines)


def _section_premortem(ctx: dict) -> str:
    """Force the model to think about failure modes before deciding."""
    side = ctx.get("side", "buy")
    symbol = ctx.get("symbol", "?")

    return f"""## PRE-MORTEM ANALYSIS
Before making your prediction, consider these failure scenarios for a \
{side.upper()} on {symbol}:

1. What specific price pattern would invalidate this trade within 1 hour?
2. Is there a funding/OI divergence that suggests crowded positioning?
3. Does the order book support or oppose this direction?
4. Is there a news catalyst that could cause a sudden reversal?
5. Has this symbol historically performed poorly at this time of day?

Factor these risks into your confidence score and risk_flags."""


def _section_task(ctx: dict) -> str:
    side = ctx.get("side", "buy").upper()
    symbol = ctx.get("symbol", "?")
    score = ctx.get("mcp_score", "?")

    return f"""## YOUR TASK
The rules-based scoring engine (MCP Brain) scored {symbol} {side} at \
{score}/100 and recommends entry. Analyze ALL the data above and predict:

1. Will this trade be profitable? (CONFIRM / VETO / WEAK_CONFIRM)
2. What is the expected price direction and magnitude?
3. What are the key risks?
4. Should the stop-loss, take-profit, or position size be adjusted?

Return ONLY the JSON object specified in your instructions. No other text."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _fmt_pct(val: Any, suffix: str = "%") -> str:
    if val is None:
        return "N/A"
    try:
        v = float(val)
        if v <= 1.0 and suffix == "%":
            return f"{v:.0%}"
        return f"{v:.2f}{suffix}"
    except (TypeError, ValueError):
        return str(val)


def _compute_rr(ctx: dict) -> str:
    sl = ctx.get("sl_pct")
    tp = ctx.get("tp_pct")
    if sl and tp:
        try:
            ratio = float(tp) / float(sl)
            return f"{ratio:.1f}:1"
        except (TypeError, ValueError, ZeroDivisionError):
            pass
    return "N/A"
