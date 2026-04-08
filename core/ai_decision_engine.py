"""
core/ai_decision_engine.py — Claude AI Trade Decision Layer

Connects to the Anthropic API so Claude reviews every trade opportunity
before it is executed. Claude acts as an independent analyst that either
confirms, rejects, or adjusts the confidence of each signal.

What Claude sees per trade:
  - Symbol, market type, direction (long/short)
  - Multi-timeframe indicator snapshot (ADX, RSI, EMA, BB, Volume)
  - Strategy name and technical confidence score
  - Current Fear & Greed index
  - Market regime (trending / ranging / volatile)
  - Recent price action summary

What Claude returns:
  - decision: "execute" | "skip" | "reduce"
  - confidence_adjustment: multiplier (0.5 to 1.5)
  - reasoning: brief explanation
  - risk_note: any concerns

This runs as an optional layer. If the API call fails or times out,
the original signal passes through unchanged.

Usage in multi_profile_runner: set AI_DECISIONS=true in .env
"""

import json
import time
import os
from pathlib  import Path
from datetime import datetime
from loguru   import logger

try:
    import urllib.request
    import urllib.error
    _HAS_URLLIB = True
except ImportError:
    _HAS_URLLIB = False

AI_DECISION_LOG = Path("data/ai_decisions.jsonl")
AI_MODEL        = "claude-sonnet-4-20250514"
AI_TIMEOUT      = 20   # seconds — skip if Claude takes longer
AI_MAX_TOKENS   = 400

# Cache decisions to avoid calling the API on every 15-min cycle
# for the same symbol+direction when nothing has changed
_DECISION_CACHE: dict = {}   # key -> (decision_dict, timestamp)
_CACHE_TTL      = 900  # 15 minutes


def _build_prompt(opportunity, snapshots: dict, fear_greed: int) -> str:
    """Build a concise prompt for Claude with all relevant context."""
    sym      = opportunity.symbol
    mtype    = opportunity.market_type.upper()
    dirn     = "LONG" if opportunity.direction == "buy" else "SHORT"
    strategy = opportunity.strategy
    conf     = opportunity.confidence
    regime   = opportunity.regime
    adx      = opportunity.adx_1h
    rsi      = opportunity.rsi_1h
    reason   = opportunity.reason

    # Build TF snapshot table
    tf_lines = []
    for tf, snap in sorted(snapshots.items(),
                            key=lambda x: ["1m","15m","1h","4h","1d"].index(x[0])
                            if x[0] in ["1m","15m","1h","4h","1d"] else 99):
        tf_lines.append(
            "  {:<4} ADX={:.1f} RSI={:.1f} dir={:<7} regime={} atr={:.2f}% vol={:.1f}x".format(
                tf, snap.adx, snap.rsi, snap.direction,
                snap.regime, snap.atr_pct * 100, snap.vol_ratio
            )
        )

    tf_table = "\n".join(tf_lines) if tf_lines else "  (no snapshot data)"

    fg_label = (
        "Extreme Fear" if fear_greed < 20 else
        "Fear"         if fear_greed < 40 else
        "Neutral"      if fear_greed < 60 else
        "Greed"        if fear_greed < 80 else
        "Extreme Greed"
    )

    prompt = """You are a professional crypto trading analyst reviewing a trade signal.
Evaluate this opportunity and decide whether to execute, skip, or reduce position size.

TRADE SIGNAL
  Symbol     : {sym}
  Market     : {mtype}
  Direction  : {dirn}
  Strategy   : {strategy}
  Confidence : {conf:.0%}
  Regime     : {regime}
  ADX (1h)   : {adx:.1f}
  RSI (1h)   : {rsi:.1f}
  Reason     : {reason}

TIMEFRAME ANALYSIS
{tf_table}

MARKET CONTEXT
  Fear & Greed Index: {fear_greed} ({fg_label})

DECISION REQUIRED
Respond with ONLY valid JSON, no other text:
{{
  "decision": "execute" | "skip" | "reduce",
  "confidence_adjustment": <float 0.5-1.5>,
  "reasoning": "<one sentence>",
  "risk_note": "<one sentence or empty string>"
}}

Rules:
- "execute"  → signal is sound, proceed at full size (confidence_adjustment 0.9-1.3)
- "reduce"   → signal has merit but risk is elevated, trade at half size (0.5-0.8)
- "skip"     → signal is weak, contradictory, or market conditions are unfavourable (0.0)
- If Fear & Greed < 20 (Extreme Fear), be cautious about longs; shorts may be valid
- If Fear & Greed > 80 (Extreme Greed), be cautious about longs; consider reducing
- ADX > 30 with trending regime = strong confirmation
- RSI > 70 on a LONG = potential overbought, consider "reduce"
- RSI < 30 on a SHORT = potential oversold, consider "reduce"
""".format(
        sym=sym, mtype=mtype, dirn=dirn, strategy=strategy,
        conf=conf, regime=regime, adx=adx, rsi=rsi, reason=reason,
        tf_table=tf_table, fear_greed=fear_greed, fg_label=fg_label,
    )
    return prompt


def _call_claude(prompt: str) -> dict | None:
    """Call Claude (CLI-first, API-fallback) and return parsed JSON response."""
    from utils.claude_client import call_claude, strip_markdown_fences

    text = call_claude(
        prompt,
        model="sonnet",
        api_model=AI_MODEL,
        max_tokens=AI_MAX_TOKENS,
        timeout=AI_TIMEOUT + 10,
    )
    if not text:
        return None

    try:
        text = strip_markdown_fences(text)
        return json.loads(text.strip())
    except json.JSONDecodeError as e:
        logger.debug("[AI] JSON parse error: {}".format(e))
        return None


class AIDecisionEngine:
    """
    Optional AI layer that uses Claude to validate each trade signal.
    Falls back gracefully if the API is unavailable.
    """

    def __init__(self):
        self._enabled = self._check_enabled()
        self._calls   = 0
        self._skips   = 0
        self._reduces = 0
        if self._enabled:
            logger.info("[AI] Decision engine enabled — Claude will review each signal")
        else:
            logger.info("[AI] Decision engine disabled (set AI_DECISIONS=true in .env to enable)")

    def _check_enabled(self) -> bool:
        val = os.environ.get("AI_DECISIONS", "").lower()
        if val in ("true", "1", "yes"):
            return True
        # Also check .env
        env_path = Path(".env")
        if env_path.exists():
            for line in env_path.read_text(encoding="utf-8").splitlines():
                if line.strip().lower().startswith("ai_decisions"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        return parts[1].strip().lower() in ("true", "1", "yes")
        return False

    def review(self, opportunity, fear_greed: int = 50) -> tuple[bool, float]:
        """
        Review a trade opportunity.
        Returns (should_execute: bool, confidence_multiplier: float).
        On any failure, returns (True, 1.0) — passthrough.
        """
        if not self._enabled:
            return True, 1.0

        sym  = opportunity.symbol
        dirn = opportunity.direction
        key  = "{}:{}:{}".format(sym, opportunity.market_type, dirn)

        # Check cache
        cached = _DECISION_CACHE.get(key)
        if cached and (time.time() - cached[1]) < _CACHE_TTL:
            decision = cached[0]
            logger.debug("[AI] Cache hit for {} — {}".format(
                key, decision.get("decision")))
            return self._parse_result(decision)

        # Get snapshots from opportunity if available
        snapshots = getattr(opportunity, "snapshots", {})
        if not isinstance(snapshots, dict):
            snapshots = {}

        prompt = _build_prompt(opportunity, snapshots, fear_greed)

        t0       = time.time()
        result   = _call_claude(prompt)
        elapsed  = time.time() - t0

        if not result:
            logger.debug("[AI] No response for {} — passing through".format(key))
            return True, 1.0

        # Cache the result
        _DECISION_CACHE[key] = (result, time.time())
        self._calls += 1

        decision = result.get("decision", "execute")
        adj      = float(result.get("confidence_adjustment", 1.0))
        reasoning= result.get("reasoning", "")
        risk_note= result.get("risk_note", "")

        if decision == "skip":
            self._skips += 1
        elif decision == "reduce":
            self._reduces += 1

        # Log to file
        self._log(opportunity, result, elapsed)

        logger.info(
            "[AI] {} {} → {} adj={:.2f} | {} {}".format(
                "LONG" if dirn == "buy" else "SHORT",
                sym, decision.upper(), adj,
                reasoning,
                "| RISK: " + risk_note if risk_note else ""
            )
        )

        return self._parse_result(result)

    def _parse_result(self, result: dict) -> tuple[bool, float]:
        decision = result.get("decision", "execute")
        adj      = float(result.get("confidence_adjustment", 1.0))
        if decision == "skip":
            return False, 0.0
        if decision == "reduce":
            return True, min(adj, 0.8)
        return True, max(adj, 0.9)

    def _log(self, opportunity, result: dict, elapsed: float):
        try:
            AI_DECISION_LOG.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "ts":          datetime.now().isoformat(),
                "symbol":      opportunity.symbol,
                "market_type": opportunity.market_type,
                "direction":   opportunity.direction,
                "strategy":    opportunity.strategy,
                "confidence":  opportunity.confidence,
                "regime":      opportunity.regime,
                "ai_decision": result.get("decision"),
                "adj":         result.get("confidence_adjustment"),
                "reasoning":   result.get("reasoning"),
                "risk_note":   result.get("risk_note"),
                "elapsed_s":   round(elapsed, 2),
            }
            with open(AI_DECISION_LOG, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.debug("[AI] Log error: {}".format(e))

    def stats(self) -> dict:
        return {
            "calls":   self._calls,
            "skips":   self._skips,
            "reduces": self._reduces,
            "enabled": self._enabled,
        }
