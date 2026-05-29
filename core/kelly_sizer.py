"""
core/kelly_sizer.py — Kelly Criterion Position Sizing

Calculates optimal bet size based on:
  - Historical win rate
  - Average win/loss ratio (R-multiple)
  - Available capital
  - Risk tolerance (fractional Kelly)

Kelly formula: f* = (p * b - q) / b
  where p = win probability, q = 1-p, b = avg_win / avg_loss

We use FRACTIONAL Kelly (25-50% of full Kelly) for safety.
"""

import json
from pathlib import Path

from loguru import logger


class KellySizer:

    KELLY_FRACTION = 0.25   # Use 25% of full Kelly (conservative)
    MIN_TRADES     = 10     # Need at least 10 trades for stats
    MIN_POSITION   = 0.01   # Minimum 1% of balance
    MAX_POSITION   = 0.10   # Maximum 10% of balance (safety cap)

    def __init__(self):
        self._stats = {}   # strategy → {wins, losses, avg_win, avg_loss}
        self._load()

    def update_from_trades(self, closed_trades: list):
        """Rebuild stats from closed trades.

        2026-04-16 (post-audit): previously skipped paper trades so Kelly
        would only block in live mode. But the bot runs in PAPER mode
        during the learning-first phase, so the block-on-negative-edge
        rule was structurally inert — SOL/XRP accumulated 16-22% WR
        without ever triggering a Kelly block.

        Now includes paper trades when running in PAPER/OBSERVATION —
        the SimExecution model already applies slippage, wick SL/TP,
        and funding, so paper stats are a reasonable proxy for live.
        In CONTROLLED_LIVE we still exclude paper noise.
        """
        try:
            from config import OPERATING_MODE
            controlled_live = OPERATING_MODE == "CONTROLLED_LIVE"
        except Exception:
            controlled_live = False

        self._stats = {}
        for t in closed_trades:
            if controlled_live and t.get("paper_trade", False):
                continue

            strat = t.get("strategy", "unknown")
            if "|" in strat:
                strat = strat.split("|")[0]  # Remove profile suffix

            if strat not in self._stats:
                self._stats[strat] = {
                    "wins": 0, "losses": 0,
                    "total_win": 0.0, "total_loss": 0.0,
                }

            pnl = t.get("pnl", 0) or 0
            # Bug 2D — pnl_pct is None marks a "reconciled_no_context" close
            # from position_tracker.reconcile_closed_pnl (Binance income event
            # with no entry/size to derive pct). Dollar PnL is trusted (income
            # is authoritative), so include it in total_win/total_loss for
            # ledger integrity (matches Spec §12 daily_pnl). Skip the row from
            # wins/losses counts since those drive WR / Kelly p, which would
            # be poisoned by no-context rows.
            no_context = t.get("pnl_pct") is None
            if pnl > 0:
                if not no_context:
                    self._stats[strat]["wins"] += 1
                self._stats[strat]["total_win"] += pnl
            elif pnl < 0:
                if not no_context:
                    self._stats[strat]["losses"] += 1
                self._stats[strat]["total_loss"] += abs(pnl)

        self._save()

    def optimal_position_pct(self, strategy: str, balance: float,
                              default_pct: float = 0.05) -> float:
        """
        Return optimal position size as fraction of balance.

        If insufficient data, returns default_pct.
        If Kelly says don't bet (negative edge), returns 0.
        """
        strat = strategy.split("|")[0] if "|" in strategy else strategy
        stats = self._stats.get(strat)

        if not stats:
            return default_pct

        total = stats["wins"] + stats["losses"]
        if total < self.MIN_TRADES:
            logger.debug(f"[Kelly] {strat}: only {total} trades, using default {default_pct:.1%}")
            return default_pct

        p = stats["wins"] / total   # Win probability
        q = 1 - p                   # Loss probability

        avg_win  = stats["total_win"]  / max(stats["wins"], 1)
        avg_loss = stats["total_loss"] / max(stats["losses"], 1)

        if avg_loss <= 0:
            return default_pct

        b = avg_win / avg_loss   # Win/loss ratio (R-multiple)

        # Kelly formula: f* = (p * b - q) / b
        kelly = (p * b - q) / b

        if kelly <= 0:
            if kelly < -0.25:
                logger.warning(
                    f"[Kelly] {strat}: SEVERE negative edge! "
                    f"WR={p:.0%} R={b:.2f} Kelly={kelly:.1%} — blocking trade")
                return 0.0
            # Mildly negative: trade at minimum size (aligned with should_block_trade threshold)
            logger.info(
                f"[Kelly] {strat}: mild negative edge "
                f"WR={p:.0%} R={b:.2f} Kelly={kelly:.1%} — using MIN_POSITION")
            return self.MIN_POSITION

        # Apply fractional Kelly
        position_pct = kelly * self.KELLY_FRACTION

        # Clamp to safety bounds
        position_pct = max(self.MIN_POSITION, min(self.MAX_POSITION, position_pct))

        logger.info(
            f"[Kelly] {strat}: WR={p:.0%} R={b:.2f} "
            f"fullKelly={kelly:.1%} fracKelly={position_pct:.1%} "
            f"(trades={total})")

        return round(position_pct, 4)

    def should_block_trade(self, strategy: str, mcp_approved: bool = False) -> tuple:
        """Return (block: bool, reason: str) if trade is too risky.

        2026-04-29: SHORT-CIRCUITED to always return (False, "") per user
        directive "clear kelly_block". The bot's claude_portfolio kelly_stats
        currently show 82W/104L (44% WR) with avg_loss > avg_win → kelly
        ≈ -56%, far below the -0.30 catastrophic threshold. The gate was
        therefore blocking EVERY MCP-approved trade in the active path,
        contradicting the user's "go all-in / don't block any trades"
        directive. Stats from pre-fix bot bugs (SL placement failures,
        ghost-sync close paths, BREAKEVEN fail-closed, L99) poisoned the
        avg_loss tail.

        update_from_trades() (called by learning_engine) still updates
        kelly_stats.json so the dashboard + reports keep showing the
        running expectancy — but the gate itself does not block.

        Restore the original logic by removing the early-return below.
        Kelly sizing math (calculate_position_pct) is untouched.
        """
        return False, ""

        # ── Original gate, preserved for reference / restoration ──────
        strat = strategy.split("|")[0] if "|" in strategy else strategy
        stats = self._best_stats_for(strat)
        if not stats:
            return False, ""
        total = stats["wins"] + stats["losses"]
        if total < self.MIN_TRADES:
            return False, ""
        wr = stats["wins"] / total
        avg_win  = stats["total_win"]  / max(stats["wins"], 1)
        avg_loss = stats["total_loss"] / max(stats["losses"], 1)
        b = avg_win / avg_loss if avg_loss > 0 else 999
        kelly = (wr * b - (1 - wr)) / b if b > 0 else -1
        if mcp_approved:
            if kelly < -0.30 and total >= 30:
                return True, (
                    f"{strat} catastrophic edge even for MCP: WR={wr:.0%} "
                    f"Kelly={kelly:.1%} over {total} trades")
            return False, ""
        if kelly < -0.25 and total >= 25:
            return True, (
                f"{strat} has negative edge: WR={wr:.0%} R={b:.2f} "
                f"Kelly={kelly:.1%} over {total} trades")
        if wr < 0.30 and total >= 20:
            return True, f"{strat} WR={wr:.0%} over {total} trades — too risky"
        return False, ""

    def _best_stats_for(self, strat: str) -> dict:
        """Find the best stats entry for a strategy, checking variant keys.
        Prefer underscore-format keys (supertrend_futures) over old
        CamelCase display names (Supertrend) which may have stale data."""
        # Check variant keys first: lowered, with _futures/_spot suffix
        variants = [
            strat.lower() + "_futures",
            strat.lower() + "_spot",
            strat + "_futures",
            strat + "_spot",
            strat.lower(),
            strat.lower().replace(" ", "_"),
        ]
        best = None
        best_total = 0
        for vk in variants:
            vs = self._stats.get(vk)
            if vs and (vs["wins"] + vs["losses"]) > best_total:
                best = vs
                best_total = vs["wins"] + vs["losses"]

        # Fall back to exact match only if no variant found
        if not best and strat in self._stats:
            best = self._stats[strat]

        return best

    def get_summary(self) -> dict:
        """Return Kelly stats for all strategies."""
        result = {}
        for strat, stats in self._stats.items():
            total = stats["wins"] + stats["losses"]
            if total == 0:
                continue
            wr = stats["wins"] / total
            avg_win  = stats["total_win"]  / max(stats["wins"], 1)
            avg_loss = stats["total_loss"] / max(stats["losses"], 1)
            b = avg_win / avg_loss if avg_loss > 0 else float('inf')
            kelly = 1.0 if b == float('inf') else (wr * b - (1 - wr)) / b
            result[strat] = {
                "trades": total, "win_rate": round(wr * 100, 1),
                "r_multiple": round(b, 2),
                "full_kelly": round(kelly * 100, 1),
                "frac_kelly": round(kelly * self.KELLY_FRACTION * 100, 1),
                "edge": "positive" if kelly > 0 else "negative",
            }
        return result

    def _save(self):
        try:
            p = Path("data/kelly_stats.json")
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(self._stats, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self):
        try:
            p = Path("data/kelly_stats.json")
            if p.exists():
                self._stats = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            self._stats = {}
