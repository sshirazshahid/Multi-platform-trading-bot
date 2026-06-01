"""
core/prediction_agent.py — AI Sub-Agent Prediction Layer (Phase 50).

STATUS (2026-05-30 audit): DORMANT SCAFFOLD — NOT wired into bot_engine or
mcp_brain. No live entry is gated, confirmed, or vetoed by this agent; the
only importers are tests/ and scripts/. The "integration modes" and "used as
a gate" language below describes the INTENDED design, not current behaviour.
Treat this as an unactivated component (mirrors the honest dormancy notes in
core/kronos_forecaster.py and core/data_sources/derivs.py). Do not assume any
live trade is AI-vetoed. Wire-up would require an explicit call site in
bot_engine._execute_open / mcp_brain and its own falsification-first gate.

A SECOND prediction layer intended to sit between the MCP Brain's rule-based
scoring and the execution path. The MCP Brain proposes entries; this agent
would confirm or veto them using richer context analyzed by Claude.

Integration modes (DESIGN INTENT — not active):
  VETO     — (default) Can only BLOCK entries. MCP Brain proposes, agent disposes.
  ADVISOR  — Predictions logged but not acted on (shadow validation).
  FULL     — Can confirm/veto entries AND suggest exits on open positions.

Architecture:
  1. Receives the MCP Brain's OPEN action + full market context
  2. Assembles a structured prompt with price action, funding, OI, orderbook,
     news, historical performance, and current portfolio state
  3. Calls Claude via utils.claude_client (CLI, uses Max subscription)
  4. Parses + validates the structured JSON prediction
  5. Returns a PredictionResult that the bot_engine uses as a gate

Cost management:
  - 15-minute per-symbol cache (don't re-predict same setup)
  - Batch mode: multiple symbols in one call when queue depth > 1
  - Model selection: haiku for fast/cheap predictions, sonnet for high-stakes

Safety:
  - The prediction agent CANNOT place, modify, or cancel orders
  - It CANNOT override stop losses or change leverage
  - It can only return CONFIRM / VETO / WEAK_CONFIRM
  - Auto-disables if directional accuracy < 50% over 50+ trades
"""

from __future__ import annotations

import hashlib
import json
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

from loguru import logger

# ---------------------------------------------------------------------------
# Result types
# ---------------------------------------------------------------------------

PredictionVerdict = Literal["CONFIRM", "VETO", "WEAK_CONFIRM"]
AgentMode = Literal["VETO", "ADVISOR", "FULL"]


@dataclass
class PredictionResult:
    """Structured output from the prediction sub-agent."""
    prediction: PredictionVerdict
    confidence: float                  # 0.0 - 1.0
    reasoning: str
    predicted_direction: str           # "long" | "short" | "neutral"
    predicted_move_pct: float          # expected move magnitude
    predicted_hold_minutes: int        # expected optimal hold time
    risk_flags: list[str] = field(default_factory=list)
    recommended_adjustments: dict = field(default_factory=dict)
    # Metadata
    latency_sec: float = 0.0
    model_used: str = ""
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    cache_hit: bool = False
    prompt_hash: str = ""

    def as_dict(self) -> dict:
        return asdict(self)


# Default result when agent is unavailable or errors out
_FALLBACK = PredictionResult(
    prediction="WEAK_CONFIRM",
    confidence=0.5,
    reasoning="prediction_agent_unavailable_or_error",
    predicted_direction="neutral",
    predicted_move_pct=0.0,
    predicted_hold_minutes=0,
    risk_flags=["agent_fallback"],
)


# ---------------------------------------------------------------------------
# Schema for validating Claude's response
# ---------------------------------------------------------------------------

_REQUIRED_KEYS = {
    "prediction", "confidence", "reasoning",
    "predicted_direction", "predicted_move_pct",
    "predicted_hold_minutes", "risk_flags",
    "recommended_adjustments",
}

_VALID_PREDICTIONS = {"CONFIRM", "VETO", "WEAK_CONFIRM"}
_VALID_DIRECTIONS = {"long", "short", "neutral"}


def _validate_prediction(raw: dict) -> PredictionResult | None:
    """Validate and coerce Claude's raw JSON into a PredictionResult.

    Returns None on any validation failure (strict — mirrors claude_schemas.py
    philosophy of rejecting malformed output rather than guessing).
    """
    if not isinstance(raw, dict):
        return None

    # Check required keys
    missing = _REQUIRED_KEYS - set(raw.keys())
    if missing:
        logger.warning(f"[PredAgent] missing keys: {sorted(missing)}")
        return None

    # Validate enums
    if raw["prediction"] not in _VALID_PREDICTIONS:
        logger.warning(f"[PredAgent] invalid prediction: {raw['prediction']}")
        return None
    if raw["predicted_direction"] not in _VALID_DIRECTIONS:
        logger.warning(f"[PredAgent] invalid direction: {raw['predicted_direction']}")
        return None

    # Coerce numeric types
    try:
        confidence = float(raw["confidence"])
        if not 0.0 <= confidence <= 1.0:
            logger.warning(f"[PredAgent] confidence {confidence} outside [0, 1]")
            return None
    except (TypeError, ValueError):
        return None

    try:
        move_pct = float(raw["predicted_move_pct"])
    except (TypeError, ValueError):
        move_pct = 0.0

    try:
        hold_min = int(raw["predicted_hold_minutes"])
    except (TypeError, ValueError):
        hold_min = 0

    # Validate risk_flags is a list of strings
    flags = raw.get("risk_flags", [])
    if not isinstance(flags, list):
        flags = []
    flags = [str(f) for f in flags]

    # Validate recommended_adjustments is a dict with known keys
    adj = raw.get("recommended_adjustments", {})
    if not isinstance(adj, dict):
        adj = {}
    # Only allow known adjustment keys — no 'override_stop', no 'leverage', etc.
    _ALLOWED_ADJ = {"sl_pct", "tp_pct", "size_multiplier"}
    adj = {k: v for k, v in adj.items() if k in _ALLOWED_ADJ}
    # Clamp size_multiplier to [0.3, 1.5] — agent should not zero or double
    if "size_multiplier" in adj:
        try:
            adj["size_multiplier"] = max(0.3, min(1.5, float(adj["size_multiplier"])))
        except (TypeError, ValueError):
            del adj["size_multiplier"]

    return PredictionResult(
        prediction=raw["prediction"],
        confidence=confidence,
        reasoning=str(raw.get("reasoning", ""))[:2000],
        predicted_direction=raw["predicted_direction"],
        predicted_move_pct=move_pct,
        predicted_hold_minutes=hold_min,
        risk_flags=flags[:16],  # cap at 16 flags
        recommended_adjustments=adj,
    )


# ---------------------------------------------------------------------------
# Cache
# ---------------------------------------------------------------------------

class _PredictionCache:
    """Simple in-memory TTL cache keyed by (symbol, side, prompt_hash)."""

    def __init__(self, ttl_sec: int = 900):  # 15 min default
        self._store: dict[str, tuple[float, PredictionResult]] = {}
        self._ttl = ttl_sec

    def get(self, key: str) -> PredictionResult | None:
        entry = self._store.get(key)
        if entry is None:
            return None
        ts, result = entry
        if time.time() - ts > self._ttl:
            del self._store[key]
            return None
        return result

    def put(self, key: str, result: PredictionResult) -> None:
        self._store[key] = (time.time(), result)
        self._evict()

    def _evict(self) -> None:
        """Remove expired entries (lazy cleanup)."""
        now = time.time()
        expired = [k for k, (ts, _) in self._store.items() if now - ts > self._ttl]
        for k in expired:
            del self._store[k]

    def invalidate(self, symbol: str | None = None) -> None:
        """Clear cache. If symbol given, only clear that symbol's entries."""
        if symbol is None:
            self._store.clear()
        else:
            to_del = [k for k in self._store if k.startswith(f"{symbol}:")]
            for k in to_del:
                del self._store[k]


# ---------------------------------------------------------------------------
# Data assembly
# ---------------------------------------------------------------------------

def _build_context(
    *,
    action: dict,
    candles_1h: list[dict],
    candles_4h: list[dict],
    funding_data: dict | None,
    oi_data: dict | None,
    orderbook: dict | None,
    news_headlines: list[str],
    symbol_history: dict | None,
    open_positions: list[dict],
    mcp_breakdown: dict | None,
) -> dict:
    """Assemble the structured context payload for the prediction prompt.

    Each section is independently nullable — the prompt template handles
    missing sections gracefully (tells Claude "data unavailable").
    """
    symbol = action.get("symbol", "")
    side = action.get("side", "")

    # Price action summary: last 20 candles, condensed
    def _condense_candles(candles: list[dict], label: str) -> list[dict]:
        result = []
        for c in (candles or [])[-20:]:
            result.append({
                "ts": c.get("timestamp") or c.get("ts"),
                "o": round(float(c.get("open", 0)), 6),
                "h": round(float(c.get("high", 0)), 6),
                "l": round(float(c.get("low", 0)), 6),
                "c": round(float(c.get("close", 0)), 6),
                "v": round(float(c.get("volume", 0)), 2),
            })
        return result

    # Correlation check: which open positions share the same base or sector
    _coin = symbol.split("/")[0].split(":")[0]
    correlated_positions = []
    for p in (open_positions or []):
        p_coin = (p.get("symbol") or "").split("/")[0].split(":")[0]
        if p_coin == _coin:
            correlated_positions.append({
                "symbol": p.get("symbol"),
                "side": p.get("side"),
                "pnl_pct": p.get("pnl_pct"),
                "exchange": p.get("exchange"),
            })

    # Concentration: how much of equity is in open positions
    total_open = len(open_positions or [])

    return {
        "symbol": symbol,
        "side": side,
        "mcp_score": action.get("mcp_score"),
        "mcp_reason": action.get("reason"),
        "confidence": action.get("confidence"),
        "sl_pct": action.get("sl_pct"),
        "tp_pct": action.get("tp_pct"),
        "leverage": action.get("leverage"),
        "mcp_breakdown": mcp_breakdown,
        "candles_1h": _condense_candles(candles_1h, "1h"),
        "candles_4h": _condense_candles(candles_4h, "4h"),
        "funding": {
            "rate": (funding_data or {}).get("funding_rate"),
            "next_funding_ts": (funding_data or {}).get("next_funding_time"),
            "predicted_rate": (funding_data or {}).get("predicted_rate"),
        } if funding_data else None,
        "open_interest": {
            "oi_value": (oi_data or {}).get("oi_value"),
            "oi_change_pct_1h": (oi_data or {}).get("oi_change_pct_1h"),
            "oi_change_pct_4h": (oi_data or {}).get("oi_change_pct_4h"),
        } if oi_data else None,
        "orderbook": {
            "bid_depth_10": (orderbook or {}).get("bid_depth_10"),
            "ask_depth_10": (orderbook or {}).get("ask_depth_10"),
            "imbalance": (orderbook or {}).get("imbalance"),
            "spread_pct": (orderbook or {}).get("spread_pct"),
        } if orderbook else None,
        "news": news_headlines[:10] if news_headlines else [],
        "symbol_history": symbol_history,  # WR, avg_R, avg_hold, n from warehouse
        "portfolio_state": {
            "total_open_positions": total_open,
            "correlated_positions": correlated_positions,
            "same_symbol_open": len(correlated_positions) > 0,
        },
    }


def _compute_prompt_hash(context: dict) -> str:
    """Deterministic hash of the context for cache keying."""
    # Hash on the immutable parts: symbol, side, mcp_score, candle closes
    key_data = {
        "symbol": context.get("symbol"),
        "side": context.get("side"),
        "mcp_score": context.get("mcp_score"),
        "candle_closes_1h": [c.get("c") for c in (context.get("candles_1h") or [])[-5:]],
    }
    raw = json.dumps(key_data, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


# ---------------------------------------------------------------------------
# Main prediction engine
# ---------------------------------------------------------------------------

class PredictionAgent:
    """AI sub-agent that confirms or vetoes MCP Brain entries.

    Usage:
        agent = PredictionAgent(mode="VETO")
        result = agent.predict(action, context_kwargs...)
        if result.prediction == "VETO":
            # block the trade
    """

    def __init__(
        self,
        mode: AgentMode = "VETO",
        model: str = "haiku",
        cache_ttl_sec: int = 900,
        auto_disable_threshold: float = 0.50,
        auto_disable_min_trades: int = 50,
    ):
        self.mode: AgentMode = mode
        self.model = model
        self._cache = _PredictionCache(ttl_sec=cache_ttl_sec)
        self._auto_disable_threshold = auto_disable_threshold
        self._auto_disable_min_trades = auto_disable_min_trades
        self._disabled = False
        self._disabled_reason = ""

        # Import prompt builder lazily to avoid circular imports
        self._prompt_builder = None

    def _get_prompt_builder(self):
        if self._prompt_builder is None:
            from core.prediction_prompt import build_prediction_prompt
            self._prompt_builder = build_prediction_prompt
        return self._prompt_builder

    @property
    def is_active(self) -> bool:
        """True if the agent is enabled and not auto-disabled."""
        return not self._disabled

    def predict(
        self,
        action: dict,
        *,
        candles_1h: list[dict] | None = None,
        candles_4h: list[dict] | None = None,
        funding_data: dict | None = None,
        oi_data: dict | None = None,
        orderbook: dict | None = None,
        news_headlines: list[str] | None = None,
        symbol_history: dict | None = None,
        open_positions: list[dict] | None = None,
        mcp_breakdown: dict | None = None,
    ) -> PredictionResult:
        """Run prediction on a single MCP Brain OPEN action.

        Returns PredictionResult. On any failure, returns WEAK_CONFIRM
        (fail-open in VETO mode, fail-logged in ADVISOR mode).
        """
        symbol = action.get("symbol", "?")

        # Auto-disable check
        if self._disabled:
            logger.debug(
                f"[PredAgent] DISABLED ({self._disabled_reason}) — "
                f"returning fallback for {symbol}")
            return _FALLBACK

        # Accuracy gate check (lazy — checked every call, cheap query)
        try:
            self._check_auto_disable()
        except Exception:
            pass

        if self._disabled:
            return _FALLBACK

        # Build context
        context = _build_context(
            action=action,
            candles_1h=candles_1h or [],
            candles_4h=candles_4h or [],
            funding_data=funding_data,
            oi_data=oi_data,
            orderbook=orderbook,
            news_headlines=news_headlines or [],
            symbol_history=symbol_history,
            open_positions=open_positions or [],
            mcp_breakdown=mcp_breakdown,
        )

        # Cache check
        prompt_hash = _compute_prompt_hash(context)
        side = action.get("side", "")
        cache_key = f"{symbol}:{side}:{prompt_hash}"
        cached = self._cache.get(cache_key)
        if cached is not None:
            logger.info(f"[PredAgent] CACHE HIT for {symbol} {side}")
            cached.cache_hit = True
            return cached

        # Build prompt
        t0 = time.time()
        try:
            build_prompt = self._get_prompt_builder()
            system_prompt, user_prompt = build_prompt(context)
        except Exception as e:
            logger.error(f"[PredAgent] prompt build failed: {e}")
            return _FALLBACK

        # Call Claude
        try:
            from utils.claude_client import call_claude_cli
            from core.claude_advisor import _extract_json_object

            raw_text = call_claude_cli(
                prompt=user_prompt,
                system_prompt=system_prompt,
                model=self.model,
                timeout=60,      # predictions should be fast
                effort="low",    # structured classification, not deep analysis
            )
            latency = time.time() - t0

            if not raw_text:
                logger.info(f"[PredAgent] CLI returned nothing for {symbol}")
                return _FALLBACK

            parsed = _extract_json_object(raw_text)
            if parsed is None:
                logger.info(f"[PredAgent] no JSON in response for {symbol}")
                self._log_failure(symbol, side, "no_json", raw_text[:500])
                return _FALLBACK

            result = _validate_prediction(parsed)
            if result is None:
                logger.info(f"[PredAgent] validation failed for {symbol}")
                self._log_failure(symbol, side, "validation_fail", parsed)
                return _FALLBACK

            # Attach metadata
            result.latency_sec = round(latency, 2)
            result.model_used = self.model
            result.prompt_hash = prompt_hash
            # Token estimates (approximate — CLI doesn't return exact counts)
            result.tokens_in = len(user_prompt + system_prompt) // 4
            result.tokens_out = len(raw_text) // 4
            result.cost_usd = _estimate_cost(
                self.model, result.tokens_in, result.tokens_out)

            # Cache the result
            self._cache.put(cache_key, result)

            # Log the prediction
            self._log_prediction(symbol, side, action, result)

            logger.info(
                f"[PredAgent] {symbol} {side}: {result.prediction} "
                f"(conf={result.confidence:.2f}, "
                f"dir={result.predicted_direction}, "
                f"move={result.predicted_move_pct:+.1f}%, "
                f"latency={result.latency_sec:.1f}s, "
                f"cost=${result.cost_usd:.4f})")

            return result

        except Exception as e:
            logger.error(f"[PredAgent] prediction failed for {symbol}: {e}")
            return _FALLBACK

    def predict_batch(
        self,
        actions: list[dict],
        **shared_kwargs,
    ) -> list[PredictionResult]:
        """Predict multiple symbols. Currently sequential; future: batch prompt.

        For now, iterates and calls predict() per action. The cache prevents
        redundant calls within the TTL window. A true batch prompt (multiple
        symbols in one Claude call) is a future optimization.
        """
        results = []
        for action in actions:
            result = self.predict(action, **shared_kwargs)
            results.append(result)
        return results

    def should_veto(self, result: PredictionResult) -> bool:
        """Convenience: returns True if the trade should be blocked.

        In VETO mode: blocks on VETO.
        In ADVISOR mode: never blocks (log only).
        In FULL mode: blocks on VETO.
        """
        if self.mode == "ADVISOR":
            return False
        return result.prediction == "VETO"

    def get_size_multiplier(self, result: PredictionResult) -> float:
        """Extract recommended size multiplier from prediction.

        Returns 1.0 if no adjustment recommended.
        Clamps to [0.3, 1.5] for safety.
        """
        adj = result.recommended_adjustments or {}
        mult = adj.get("size_multiplier", 1.0)
        try:
            return max(0.3, min(1.5, float(mult)))
        except (TypeError, ValueError):
            return 1.0

    # ── Auto-disable logic ────────────────────────────────────────────

    def _check_auto_disable(self) -> None:
        """Check prediction accuracy and auto-disable if below random."""
        try:
            from core.prediction_tracker import PredictionTracker
            tracker = PredictionTracker()
            stats = tracker.accuracy_stats()
            if stats is None:
                return

            n = stats.get("total_resolved", 0)
            if n < self._auto_disable_min_trades:
                return

            directional_acc = stats.get("directional_accuracy", 0.5)
            if directional_acc < self._auto_disable_threshold:
                self._disabled = True
                self._disabled_reason = (
                    f"directional_accuracy={directional_acc:.2%} < "
                    f"{self._auto_disable_threshold:.2%} over {n} trades"
                )
                logger.warning(f"[PredAgent] AUTO-DISABLED: {self._disabled_reason}")
        except ImportError:
            pass  # tracker not yet available
        except Exception as e:
            logger.debug(f"[PredAgent] auto-disable check error: {e}")

    def force_enable(self) -> None:
        """Manually re-enable after auto-disable. Use with caution."""
        self._disabled = False
        self._disabled_reason = ""
        logger.info("[PredAgent] manually re-enabled")

    # ── Logging ───────────────────────────────────────────────────────

    def _log_prediction(
        self, symbol: str, side: str, action: dict, result: PredictionResult,
    ) -> None:
        """Append prediction to JSONL log for accuracy tracking."""
        entry = {
            "ts": time.time(),
            "symbol": symbol,
            "side": side,
            "mcp_score": action.get("mcp_score"),
            "mode": self.mode,
            "prediction": result.prediction,
            "confidence": result.confidence,
            "predicted_direction": result.predicted_direction,
            "predicted_move_pct": result.predicted_move_pct,
            "predicted_hold_minutes": result.predicted_hold_minutes,
            "risk_flags": result.risk_flags,
            "recommended_adjustments": result.recommended_adjustments,
            "reasoning": result.reasoning,
            "model_used": result.model_used,
            "latency_sec": result.latency_sec,
            "tokens_in": result.tokens_in,
            "tokens_out": result.tokens_out,
            "cost_usd": result.cost_usd,
            "prompt_hash": result.prompt_hash,
            "cache_hit": result.cache_hit,
        }
        try:
            log_path = Path("data/prediction_agent_log.jsonl")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception as e:
            logger.debug(f"[PredAgent] log write error: {e}")

    def _log_failure(self, symbol: str, side: str, reason: str, data: Any) -> None:
        """Log prediction failures for debugging prompt issues."""
        entry = {
            "ts": time.time(),
            "symbol": symbol,
            "side": side,
            "failure_reason": reason,
            "raw_data": data if isinstance(data, (str, dict, list)) else str(data)[:500],
        }
        try:
            log_path = Path("data/prediction_agent_failures.jsonl")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry, default=str) + "\n")
        except Exception:
            pass


# ---------------------------------------------------------------------------
# Cost estimation
# ---------------------------------------------------------------------------

# Pricing per 1M tokens (as of May 2026, Anthropic published rates)
_PRICING = {
    "haiku": {"input": 0.25, "output": 1.25},      # $0.25/1M in, $1.25/1M out
    "sonnet": {"input": 3.00, "output": 15.00},     # $3/1M in, $15/1M out
    "opus": {"input": 15.00, "output": 75.00},      # $15/1M in, $75/1M out
}


def _estimate_cost(model: str, tokens_in: int, tokens_out: int) -> float:
    """Estimate USD cost for a prediction call.

    Note: when using Claude Code CLI with Max subscription, the actual cost
    is $0 (covered by subscription). This estimate is for budget tracking
    if the system switches to direct API calls.
    """
    rates = _PRICING.get(model, _PRICING["haiku"])
    cost = (tokens_in * rates["input"] + tokens_out * rates["output"]) / 1_000_000
    return round(cost, 6)


# ---------------------------------------------------------------------------
# Singleton convenience
# ---------------------------------------------------------------------------

_default_agent: PredictionAgent | None = None


def get_prediction_agent() -> PredictionAgent:
    """Get or create the default prediction agent.

    Reads config on first call to set mode and model.
    """
    global _default_agent
    if _default_agent is None:
        try:
            from config import PREDICTION_AGENT as _cfg
        except ImportError:
            _cfg = {}
        _default_agent = PredictionAgent(
            mode=_cfg.get("mode", "ADVISOR"),
            model=_cfg.get("model", "haiku"),
            cache_ttl_sec=int(_cfg.get("cache_ttl_sec", 900)),
            auto_disable_threshold=float(_cfg.get("auto_disable_threshold", 0.50)),
            auto_disable_min_trades=int(_cfg.get("auto_disable_min_trades", 50)),
        )
    return _default_agent
