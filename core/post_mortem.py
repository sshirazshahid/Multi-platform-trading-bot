"""
core/post_mortem.py — Post-Trade Analysis & Loss Prevention System

After every loss:
  1. Analyzes what went wrong (entry timing, SL too tight, regime mismatch, etc.)
  2. Saves findings to data/post_mortem.json
  3. Updates a "lessons learned" database
  4. Provides confidence adjustments to avoid repeating mistakes

After every win:
  1. Records what went right for reinforcement
"""

import json
import time
from pathlib import Path
from datetime import datetime
from loguru import logger

SAVE_PATH = Path("data/post_mortem.json")
MAX_ENTRIES = 500


class PostMortem:

    def __init__(self):
        self._analyses = []
        self._lessons = {}   # pattern → {count, last_seen, adjustment}
        self._load()

    # ── Public API ────────────────────────────────────────────────────

    def analyze_trade(self, trade: dict) -> dict:
        """Analyze a closed trade. Call after every close."""
        pnl = trade.get("pnl", 0) or 0
        is_loss = pnl < 0

        analysis = {
            "timestamp": time.time(),
            "symbol": trade.get("symbol", "?"),
            "exchange": trade.get("exchange", "?"),
            "side": trade.get("side", "?"),
            "market_type": trade.get("market_type", "spot"),
            "strategy": trade.get("strategy", "?"),
            "entry_price": trade.get("entry_price", 0),
            "close_price": trade.get("close_price", 0),
            "pnl": pnl,
            "pnl_pct": trade.get("pnl_pct", 0),
            "close_reason": trade.get("close_reason", "?"),
            "leverage": trade.get("leverage", 1),
            "duration_min": 0,
            "verdict": "WIN" if pnl > 0 else "LOSS",
            "mistakes": [],
            "lessons": [],
        }

        # Calculate duration
        open_t = trade.get("open_time", 0)
        close_t = trade.get("close_time", 0)
        if open_t and close_t:
            analysis["duration_min"] = round((close_t - open_t) / 60, 1)

        if is_loss:
            analysis["mistakes"] = self._identify_mistakes(trade, analysis)
            analysis["lessons"] = self._derive_lessons(analysis)
            self._update_lessons(analysis)

            for mistake in analysis["mistakes"]:
                logger.warning(f"[PostMortem] LOSS {trade.get('symbol')}: {mistake}")
            for lesson in analysis["lessons"]:
                logger.info(f"[PostMortem] LESSON: {lesson}")
        else:
            analysis["lessons"] = [f"Good entry on {trade.get('strategy', '?')}"]

        self._analyses.append(analysis)
        if len(self._analyses) > MAX_ENTRIES:
            self._analyses = self._analyses[-MAX_ENTRIES:]

        self._save()
        return analysis

    def confidence_adjustment(self, symbol: str, strategy: str,
                               side: str, market_type: str) -> float:
        """Return a multiplier (0.5 to 1.0) based on past mistakes.
        Lower = more caution needed."""
        adj = 1.0

        # Check if this exact pattern has repeated losses
        patterns = [
            f"symbol:{symbol}",
            f"strategy:{strategy}",
            f"side:{side}:{market_type}",
            f"{symbol}:{strategy}",
        ]

        for pattern in patterns:
            lesson = self._lessons.get(pattern)
            if lesson and lesson.get("count", 0) >= 2:
                # Each repeated mistake reduces confidence by 10%
                penalty = min(0.4, lesson["count"] * 0.10)
                adj *= (1.0 - penalty)

        return max(0.5, round(adj, 3))

    def get_summary(self) -> dict:
        """Return summary stats for dashboard/email."""
        if not self._analyses:
            return {"total": 0, "losses": 0, "top_mistakes": [], "lessons_count": 0}

        losses = [a for a in self._analyses if a["verdict"] == "LOSS"]
        all_mistakes = []
        for a in losses:
            all_mistakes.extend(a.get("mistakes", []))

        # Count mistake frequency
        from collections import Counter
        mistake_freq = Counter(all_mistakes).most_common(5)

        return {
            "total": len(self._analyses),
            "losses": len(losses),
            "wins": len(self._analyses) - len(losses),
            "top_mistakes": [{"mistake": m, "count": c} for m, c in mistake_freq],
            "lessons_count": len(self._lessons),
            "recent_losses": losses[-3:] if losses else [],
        }

    # ── Mistake identification ────────────────────────────────────────

    def _identify_mistakes(self, trade: dict, analysis: dict) -> list:
        mistakes = []
        reason = analysis["close_reason"]
        duration = analysis["duration_min"]
        pnl_pct = abs(analysis.get("pnl_pct", 0) or 0)
        side = analysis["side"]
        strategy = analysis["strategy"].split("|")[0] if "|" in analysis.get("strategy","") else analysis["strategy"]

        # 1. Stop loss hit too quickly (< 5 min)
        if reason == "stop_loss" and duration < 5:
            mistakes.append("SL hit in <5min — entry timing was poor")

        # 2. Stop loss hit after long hold (> 4 hours) — trend reversed
        if reason == "stop_loss" and duration > 240:
            mistakes.append("SL hit after 4+ hours — trend reversed, should have taken partial profit")

        # 3. Large loss (> 3% of position)
        if pnl_pct > 3.0:
            mistakes.append(f"Large loss ({pnl_pct:.1f}%) — SL too wide or leverage too high")

        # 4. Short in uptrend (or long in downtrend) — wrong direction
        if side == "sell" and reason == "stop_loss":
            mistakes.append("SHORT stopped out — possible counter-trend trade")
        elif side == "buy" and reason == "stop_loss":
            mistakes.append("LONG stopped out — possible counter-trend trade")

        # 5. Strategy-specific
        if "supertrend" in strategy and reason == "stop_loss" and duration < 30:
            mistakes.append("Supertrend whipsaw — trend was not established")
        if "mean_reversion" in strategy and pnl_pct > 2:
            mistakes.append("Mean reversion failed — market broke out of range")
        if "dca" in strategy and reason == "stop_loss":
            mistakes.append("DCA position hit SL — accumulation in wrong direction")

        # 6. Leverage too high for the volatility
        lev = analysis.get("leverage", 1) or 1
        if lev >= 5 and pnl_pct > 2:
            mistakes.append(f"High leverage ({lev}x) amplified loss")

        if not mistakes:
            mistakes.append("Unknown cause — needs manual review")

        return mistakes

    def _derive_lessons(self, analysis: dict) -> list:
        lessons = []
        for mistake in analysis["mistakes"]:
            if "entry timing" in mistake:
                lessons.append("Wait for confirmation candle before entering")
            elif "trend reversed" in mistake:
                lessons.append("Set trailing stop after 2h of profit")
            elif "SL too wide" in mistake:
                lessons.append("Reduce SL to 1.5x ATR or lower leverage")
            elif "counter-trend" in mistake:
                lessons.append("Only trade with the higher timeframe trend")
            elif "whipsaw" in mistake:
                lessons.append("Require ADX > 25 before supertrend entry")
            elif "broke out of range" in mistake:
                lessons.append("Check for volume breakout before mean reversion")
            elif "High leverage" in mistake:
                lessons.append("Cap leverage at 3x when ATR% > 3%")
            elif "DCA" in mistake:
                lessons.append("Only DCA when 4h trend is aligned")
        return lessons

    def _update_lessons(self, analysis: dict):
        """Track repeated mistake patterns."""
        sym = analysis["symbol"]
        strat = analysis["strategy"].split("|")[0] if "|" in analysis.get("strategy","") else analysis["strategy"]
        side = analysis["side"]
        mtype = analysis["market_type"]

        patterns = [
            f"symbol:{sym}",
            f"strategy:{strat}",
            f"side:{side}:{mtype}",
            f"{sym}:{strat}",
        ]

        for pattern in patterns:
            if pattern not in self._lessons:
                self._lessons[pattern] = {"count": 0, "last_seen": 0}
            self._lessons[pattern]["count"] += 1
            self._lessons[pattern]["last_seen"] = time.time()

    # ── Persistence ───────────────────────────────────────────────────

    def _save(self):
        try:
            SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "analyses": self._analyses[-MAX_ENTRIES:],
                "lessons": self._lessons,
                "saved_at": datetime.now().isoformat(),
            }
            SAVE_PATH.write_text(json.dumps(data, indent=2, default=str),
                                 encoding="utf-8")
        except Exception as e:
            logger.debug(f"[PostMortem] save: {e}")

    def _load(self):
        try:
            if SAVE_PATH.exists():
                data = json.loads(SAVE_PATH.read_text(encoding="utf-8"))
                self._analyses = data.get("analyses", [])
                self._lessons = data.get("lessons", {})
        except Exception:
            self._analyses = []
            self._lessons = {}
