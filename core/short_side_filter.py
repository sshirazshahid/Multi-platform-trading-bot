"""
core/short_side_filter.py -- block low-quality SHORT entries against a BTC uptrend.

Why this exists. Warehouse evidence (336 closed trades, May 2026):
    longs   210 trades net  -$4
    shorts  126 trades net -$54
The single biggest negative driver is the bot shorting alts while BTC trends up
on both 4h and 1h. SHORTS_REQUIRE_BTC_BEAR (config.py) is False so the
existing tier path lets these through; this filter is the explicit gate.

Idiosyncratic catalyst escape hatch: when the news scanner has tagged the
symbol with a bearish-impact headline in the last 30 min, the filter ALLOWS
the short -- the BTC-trend signal is not predictive against a fresh negative
catalyst on the specific asset.

2026-05-27 (454-trade hardening): shorts are 2.5x worse than longs across
179 vs 275 trades (-$87 vs -$21). Two additional score/confidence gates are
enforced regardless of BTC trend alignment:
  - mcp_score    >= SHORT_SIDE_FILTER["min_mcp_score"]    (default 75)
  - confidence   >= SHORT_SIDE_FILTER["min_confidence"]   (default 0.70)
Both are bypassed by the news_bearish_catalyst escape hatch.
Missing score/confidence values (None / 0.0) trigger the gate -- fail-closed.

The filter is stateless and side-effect-free. Callers log the decision.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ShortFilterDecision:
    block:  bool
    reason: str


def evaluate(
    *,
    side: str,
    symbol: str,
    btc_4h_uptrend: bool | None,
    btc_1h_uptrend: bool | None,
    symbol_news_sentiment: int | None = None,
    mcp_score: float | None = None,
    confidence: float | None = None,
    min_mcp_score: float = 75.0,
    min_confidence: float = 0.70,
) -> ShortFilterDecision:
    """Return a decision for a single OPEN action.

    Parameters
    ----------
    side                  : 'buy' | 'sell' (case-insensitive)
    symbol                : 'ETH/USDT' or 'ETH/USDT:USDT' -- used in reason text
    btc_4h_uptrend        : True when BTC 4h EMA20 > EMA50
    btc_1h_uptrend        : True when BTC 1h EMA20 > EMA50
    symbol_news_sentiment : NewsScanner.symbol_sentiment(symbol) result.
                            < 0 means recent bearish headlines on this symbol.
                            None means scanner unavailable.
    mcp_score             : Algorithmic score (0-100). None treated as 0.
    confidence            : Trade confidence (0-1). None treated as 0.
    min_mcp_score         : Minimum mcp_score for shorts (default from config).
    min_confidence        : Minimum confidence for shorts (default from config).

    The filter only ever blocks SHORTS. Longs are passed through untouched.
    Missing BTC trend signals (None) default to "not aligned" -> no block,
    so an upstream data outage cannot silently start blocking trades.
    Missing score/confidence default to 0 -> gate triggers (fail-closed).
    """
    if (side or "").lower() != "sell":
        return ShortFilterDecision(False, "not_short")

    # Idiosyncratic bearish catalyst on this symbol -- let the short through.
    # This escape hatch overrides ALL gates below (BTC trend + score gates).
    if symbol_news_sentiment is not None and symbol_news_sentiment < 0:
        return ShortFilterDecision(
            False,
            f"news_bearish_catalyst({symbol_news_sentiment})",
        )

    # Score gate: shorts need a stronger signal than longs.
    # Fail-closed: missing score (None/0) fails the gate.
    _score = float(mcp_score) if mcp_score is not None else 0.0
    if _score < min_mcp_score:
        return ShortFilterDecision(
            True,
            f"short_score_too_low({_score:.0f}<{min_mcp_score:.0f})",
        )

    # Confidence gate: shorts need higher conviction than longs.
    _conf = float(confidence) if confidence is not None else 0.0
    if _conf < min_confidence:
        return ShortFilterDecision(
            True,
            f"short_conf_too_low({_conf:.2f}<{min_confidence:.2f})",
        )

    # BTC macro-trend gate: block shorting alts into a BTC uptrend.
    if btc_4h_uptrend and btc_1h_uptrend:
        return ShortFilterDecision(
            True,
            "btc_aligned_up_4h_and_1h",
        )

    return ShortFilterDecision(False, "btc_not_aligned_up")


def extract_btc_trends(exchange_indicators: dict | None) -> tuple[bool | None, bool | None]:
    """Pull BTC 4h/1h ema20>ema50 booleans from the standard MCP indicator dict.

    Returns (None, None) when the structure is missing -- caller can pass these
    straight to evaluate() and the filter will degrade to "not aligned".
    """
    if not isinstance(exchange_indicators, dict):
        return (None, None)
    btc = exchange_indicators.get("BTC") or {}
    h4 = btc.get("4h") or {}
    h1 = btc.get("1h") or {}
    e4 = h4.get("ema20_above_50")
    e1 = h1.get("ema20_above_50")
    return (
        bool(e4) if isinstance(e4, bool) else None,
        bool(e1) if isinstance(e1, bool) else None,
    )
