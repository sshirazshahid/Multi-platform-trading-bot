"""
core/knowledge_model.py — Persistent Learning Knowledge Base

This is the bridge between DRY RUN learning and LIVE trading.

The problem it solves:
  - Without this: switching from DRY → LIVE mode loses all learned knowledge
    because the strategy selector starts with no data about which strategies
    work best for which coins and regimes.
  - With this: everything learned in paper trading is preserved in a structured
    model file (data/knowledge_model.json) and immediately applied on day 1 of
    live trading.

How it works:
  1. During DRY RUN: every trade is analyzed and scores are written to the model
  2. Model stores per-strategy confidence multipliers, per-symbol best strategies,
     best trading hours, regime→strategy mappings, blacklist candidates etc.
  3. When you switch to LIVE: bot loads the model at startup, uses dry run scores
     as starting priors for all decisions
  4. As live trades accumulate, live data is weighted progressively higher:
       0-9  live trades  → dry run scores  100% weight
      10-29 live trades  → dry run 60%  + live 40%
      30-49 live trades  → dry run 30%  + live 70%
      50+   live trades  → live scores  100% weight (fully graduated)
  5. Per-strategy scores that perform differently in live vs dry are individually
     overridden as soon as enough live data exists for that strategy (≥5 trades)

Model file: data/knowledge_model.json
Backup:     data/knowledge_model.bak.json   (before each write)
"""

import json
import shutil
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from utils.atomic_io import atomic_write_json

MODEL_FILE  = Path("data/knowledge_model.json")
BACKUP_FILE = Path("data/knowledge_model.bak.json")

# How many live trades before we start blending live data into decisions
LIVE_BLEND_START    = 10
# How many live trades before live data fully replaces dry run
LIVE_BLEND_COMPLETE = 50
# Minimum trades per strategy to trust its score
MIN_STRATEGY_SAMPLE = 5


class KnowledgeModel:
    """
    Persistent strategy knowledge base.
    Survives mode switches (DRY ↔ LIVE) and bot restarts.
    """

    def __init__(self):
        self._model: dict = self._default_model()
        self._load()
        self._log_status()

    # ── Default empty model ───────────────────────────────────────────

    def _default_model(self) -> dict:
        return {
            "version":          3,
            "created_at":       datetime.now().isoformat(),
            "last_updated":     datetime.now().isoformat(),
            # Trade counts
            "dry_run_trades":   0,
            "live_trades":      0,
            # Per-strategy scores — each entry has "dry" and "live" sub-dicts
            # {strategy_name: {dry: {win_rate, avg_pnl, trades, confidence_mult},
            #                   live: {win_rate, avg_pnl, trades, confidence_mult}}}
            "strategy_scores":  {},
            # Per-symbol performance
            # {symbol: {dry: {...}, live: {...}, best_strategy: str}}
            "symbol_scores":    {},
            # Regime → best strategy mapping learned from data
            # {regime: {dry: strategy, live: strategy}}
            "regime_map":       {},
            # Best hours to trade (UTC hour → {win_rate, trades})
            "hour_scores":      {},
            # Symbols to be cautious with (many SLs)
            "caution_symbols":  [],
            # Strategies to be cautious with (low WR)
            "caution_strategies": [],
            # Star performers
            "star_strategies":  [],
            "star_symbols":     [],
            # Graduated: True = live data has fully replaced dry run
            "graduated":        False,
            # Human-readable insights log
            "insights_history": [],
        }

    # ── Public API ────────────────────────────────────────────────────

    def update_from_trades(self, trades: list, mode: str = "dry"):
        """
        Rebuild strategy/symbol/regime scores from a list of closed trades.
        mode: "dry" or "live"
        """
        if not trades:
            return

        # Exclude bug-driven exits from score computation. These closes are
        # not market-driven losses — they are the bot closing itself out:
        #   sl_placement_failed: Binance -4120 / Bybit 110072 rejected the
        #     SL order, fail-closed policy market-closed the position.
        #   systematic_close:    legacy stale-age rule (removed 2026-04-20)
        #     that closed positions after N hours regardless of market state.
        # Including these inflated WR<35% against otherwise-healthy symbols
        # and caused auto-symptom-blacklists. See feedback_no_symptom_blacklists.
        _BUG_EXITS = {"sl_placement_failed", "systematic_close"}
        _total_in = len(trades)
        trades = [
            t for t in trades
            if (t.get("close_reason") or t.get("exit_reason") or "") not in _BUG_EXITS
        ]
        _dropped = _total_in - len(trades)
        if _dropped:
            logger.info(
                f"[Knowledge] Excluded {_dropped}/{_total_in} bug-driven exits "
                f"({', '.join(sorted(_BUG_EXITS))}) from {mode} score rebuild")

        if not trades:
            return

        mode_key = "dry" if mode == "dry" else "live"
        count    = len(trades)

        if mode_key == "dry":
            self._model["dry_run_trades"] = count
        else:
            self._model["live_trades"] = count

        # ── Strategy scores ───────────────────────────────────────────
        # Bug 2D — t.get("pnl_pct") is None marks a "reconciled_no_context"
        # close (Binance income event with no entry/size to derive pct). Skip
        # such rows from count-based WR/trades aggregations, but still include
        # the dollar pnl in net_pnl for ledger integrity. Sibling fix to
        # position_tracker.reconcile_closed_pnl (Bug 2A).
        strat_buckets = defaultdict(lambda: {
            "trades": 0, "wins": 0, "net_pnl": 0.0, "total_fees": 0.0
        })
        for t in trades:
            s = t.get("strategy", "unknown")
            d = strat_buckets[s]
            d["net_pnl"]    += t.get("pnl") or 0.0           # dollar ledger
            d["total_fees"] += t.get("total_fees") or 0.0
            if t.get("pnl_pct") is None:
                continue  # reconciled_no_context: skip from WR/trades count
            d["trades"]     += 1
            if (t.get("pnl") or 0) > 0:
                d["wins"] += 1

        for strat, d in strat_buckets.items():
            n  = d["trades"]
            wr = (d["wins"] / n * 100) if n > 0 else 0
            if strat not in self._model["strategy_scores"]:
                self._model["strategy_scores"][strat] = {"dry": {}, "live": {}}
            self._model["strategy_scores"][strat][mode_key] = {
                "trades":            n,
                "wins":              d["wins"],
                "win_rate":          round(wr, 1),
                "avg_pnl":           round(d["net_pnl"] / max(n, 1), 4),
                "total_fees":        round(d["total_fees"], 4),
                "confidence_mult":   self._wr_to_multiplier(wr, n),
                "updated_at":        datetime.now().isoformat(),
            }

        # ── Symbol scores ─────────────────────────────────────────────
        sym_buckets = defaultdict(lambda: {
            "trades": 0, "wins": 0, "net_pnl": 0.0,
            "strategy_wins": defaultdict(int),
            "strategy_trades": defaultdict(int),
        })
        for t in trades:
            sym   = t.get("symbol", "unknown")
            strat = t.get("strategy", "unknown")
            d     = sym_buckets[sym]
            d["net_pnl"] += t.get("pnl") or 0.0           # dollar ledger always
            if t.get("pnl_pct") is None:
                continue  # reconciled_no_context: skip from WR/trades count (Bug 2D)
            d["trades"]  += 1
            d["strategy_trades"][strat] += 1
            if (t.get("pnl") or 0) > 0:
                d["wins"] += 1
                d["strategy_wins"][strat] += 1

        for sym, d in sym_buckets.items():
            n  = d["trades"]
            wr = (d["wins"] / n * 100) if n > 0 else 0
            # Best strategy for this symbol
            best_strat = max(
                d["strategy_wins"].items(),
                key=lambda x: x[1] / max(d["strategy_trades"][x[0]], 1),
                default=(None, 0)
            )[0]
            if sym not in self._model["symbol_scores"]:
                self._model["symbol_scores"][sym] = {"dry": {}, "live": {}}
            self._model["symbol_scores"][sym][mode_key] = {
                "trades":        n,
                "win_rate":      round(wr, 1),
                "avg_pnl":       round(d["net_pnl"] / max(n, 1), 4),
                "best_strategy": best_strat,
            }

        # ── Regime → best strategy ────────────────────────────────────
        def _regime(s: str) -> str:
            if "grid" in s or "mean_reversion" in s:  return "ranging"
            if "dca" in s:                             return "weak"
            return "trending"

        regime_buckets = defaultdict(lambda: defaultdict(lambda: {
            "trades": 0, "wins": 0
        }))
        for t in trades:
            if t.get("pnl_pct") is None:
                continue  # reconciled_no_context: skip from WR sample (Bug 2D)
            s = t.get("strategy", "unknown")
            r = _regime(s)
            regime_buckets[r][s]["trades"] += 1
            if (t.get("pnl") or 0) > 0:
                regime_buckets[r][s]["wins"] += 1

        for regime, strats in regime_buckets.items():
            qualified = [
                (s, d) for s, d in strats.items()
                if d["trades"] >= MIN_STRATEGY_SAMPLE
            ]
            if qualified:
                best = max(
                    qualified,
                    key=lambda x: x[1]["wins"] / max(x[1]["trades"], 1)
                )[0]
                if regime not in self._model["regime_map"]:
                    self._model["regime_map"][regime] = {"dry": None, "live": None}
                self._model["regime_map"][regime][mode_key] = best

        # ── Hour scores ───────────────────────────────────────────────
        hour_buckets = defaultdict(lambda: {
            "trades": 0, "wins": 0, "net_pnl": 0.0, "total_fees": 0.0,
        })
        for t in trades:
            ot = t.get("open_time", 0)
            if ot:
                h = datetime.fromtimestamp(ot, tz=timezone.utc).hour
                hour_buckets[h]["net_pnl"]    += t.get("pnl") or 0.0   # dollar ledger
                hour_buckets[h]["total_fees"] += t.get("total_fees") or 0.0
                if t.get("pnl_pct") is None:
                    continue  # reconciled_no_context: skip WR (Bug 2D)
                hour_buckets[h]["trades"]     += 1
                if (t.get("pnl") or 0) > 0:
                    hour_buckets[h]["wins"] += 1
        for h, d in hour_buckets.items():
            n  = d["trades"]
            wr = (d["wins"] / n * 100) if n > 0 else 0
            self._model["hour_scores"][str(h)] = {
                "trades":     n,
                "wins":       d["wins"],
                "win_rate":   round(wr, 1),
                "net_pnl":    round(d["net_pnl"], 4),
                "avg_pnl":    round(d["net_pnl"] / max(n, 1), 4),
                "total_fees": round(d["total_fees"], 4),
            }

        # ── Flags ─────────────────────────────────────────────────────
        caution_strats  = []
        star_strats     = []
        caution_symbols = []
        star_symbols    = []

        # Phase 39 (2026-05-09): claude_portfolio is the PRIMARY entry strategy —
        # never add it to caution_strats regardless of WR. The caution gate was
        # suppressing all new entries (bot opened 0 trades in 65h). The WR
        # benchmark is computed over the full mixed trade history including
        # reconciled/ghost closes which dilute the signal. Hard-exempt it so
        # the entry engine is never auto-disabled.
        _EXEMPT_FROM_CAUTION = {"claude_portfolio"}

        for strat, scores in self._model["strategy_scores"].items():
            data = scores.get(mode_key, {})
            if data.get("trades", 0) >= MIN_STRATEGY_SAMPLE:
                wr = data.get("win_rate", 50)
                if wr < 50 and strat not in _EXEMPT_FROM_CAUTION:
                    caution_strats.append(strat)
                if wr >= 65:  star_strats.append(strat)

        for sym, scores in self._model["symbol_scores"].items():
            data = scores.get(mode_key, {})
            if data.get("trades", 0) >= MIN_STRATEGY_SAMPLE:
                wr = data.get("win_rate", 50)
                if wr < 35:   caution_symbols.append(sym)
                if wr >= 65:  star_symbols.append(sym)

        self._model["caution_strategies"] = caution_strats
        self._model["star_strategies"]    = star_strats
        self._model["caution_symbols"]    = caution_symbols
        self._model["star_symbols"]       = star_symbols

        # Mark as graduated if live trades exceed threshold
        self._model["graduated"] = (
            self._model["live_trades"] >= LIVE_BLEND_COMPLETE
        )

        self._model["last_updated"] = datetime.now().isoformat()

        # Append to insights history
        insight = (
            f"{datetime.now().strftime('%Y-%m-%d %H:%M')} | "
            f"{mode.upper()} update | "
            f"{count} trades | "
            f"dry={self._model['dry_run_trades']} "
            f"live={self._model['live_trades']}"
        )
        self._model["insights_history"].append(insight)
        self._model["insights_history"] = self._model["insights_history"][-50:]

        self._save()
        logger.info(
            f"[Knowledge] Model updated from {count} {mode.upper()} trades | "
            f"dry={self._model['dry_run_trades']} "
            f"live={self._model['live_trades']} | "
            f"stars={star_strats} | caution={caution_strats}"
        )

    def confidence_multiplier(self, strategy: str) -> float:
        """
        Return confidence multiplier for a strategy.
        Blends dry run and live data based on how much live data we have.
        """
        scores = self._model["strategy_scores"].get(strategy, {})
        dry    = scores.get("dry",  {})
        live   = scores.get("live", {})

        dry_mult  = dry.get("confidence_mult", 1.0)
        dry_n     = dry.get("trades", 0)
        live_mult = live.get("confidence_mult", 1.0)
        live_n    = live.get("trades", 0)
        total_live = self._model.get("live_trades", 0)

        # No dry data and no live data — neutral
        if dry_n < MIN_STRATEGY_SAMPLE and live_n < MIN_STRATEGY_SAMPLE:
            return 1.0

        # If live data for this specific strategy is sufficient — use live only
        if live_n >= MIN_STRATEGY_SAMPLE:
            if total_live >= LIVE_BLEND_COMPLETE:
                return live_mult   # fully graduated
            # Blend
            live_weight = min(1.0, total_live / LIVE_BLEND_COMPLETE)
            dry_weight  = 1.0 - live_weight
            return round(dry_weight * dry_mult + live_weight * live_mult, 3)

        # Only dry data — use that
        if dry_n >= MIN_STRATEGY_SAMPLE:
            return dry_mult

        return 1.0

    def best_strategy_for(self, symbol: str, regime: str) -> str | None:
        """
        Return the best known strategy for a symbol+regime.
        Prefers live data; falls back to dry run; falls back to None.
        """
        total_live = self._model.get("live_trades", 0)

        # Symbol-level override (live takes priority)
        sym_scores = self._model["symbol_scores"].get(symbol, {})
        if total_live >= MIN_STRATEGY_SAMPLE:
            live_best = sym_scores.get("live", {}).get("best_strategy")
            if live_best:
                return live_best
        dry_best = sym_scores.get("dry", {}).get("best_strategy")
        if dry_best:
            return dry_best

        # Regime-level fallback
        regime_map = self._model["regime_map"].get(regime, {})
        if total_live >= MIN_STRATEGY_SAMPLE:
            live_regime = regime_map.get("live")
            if live_regime:
                return live_regime
        return regime_map.get("dry")

    def best_hours(self) -> list[int]:
        """Return list of UTC hours with best win rates (top 5)."""
        scored = [
            (int(h), d["win_rate"])
            for h, d in self._model["hour_scores"].items()
            if d.get("trades", 0) >= 3
        ]
        return [h for h, _ in sorted(scored, key=lambda x: x[1], reverse=True)[:5]]

    def is_star_strategy(self, strategy: str) -> bool:
        return strategy in self._model.get("star_strategies", [])

    def is_caution_symbol(self, symbol: str) -> bool:
        return symbol in self._model.get("caution_symbols", [])

    def summary(self) -> str:
        """Return a human-readable model summary."""
        dry_n   = self._model.get("dry_run_trades", 0)
        live_n  = self._model.get("live_trades", 0)
        graded  = self._model.get("graduated", False)
        stars   = self._model.get("star_strategies", [])
        caution = self._model.get("caution_strategies", [])
        updated = self._model.get("last_updated", "never")[:16]
        hours   = self.best_hours()
        lines   = [
            f"Knowledge Model  (last updated {updated})",
            f"  Dry Run trades : {dry_n}",
            f"  Live trades    : {live_n}",
            f"  Status         : {'GRADUATED to LIVE' if graded else 'Using DRY RUN priors'}",
            f"  Star strategies: {stars or 'none yet'}",
            f"  Caution strats : {caution or 'none'}",
            f"  Best hours UTC : {hours or 'not enough data'}",
        ]
        if live_n > 0 and not graded:
            pct = min(100, live_n / LIVE_BLEND_COMPLETE * 100)
            lines.append(
                f"  Live blending  : {pct:.0f}% "
                f"({live_n}/{LIVE_BLEND_COMPLETE} trades to full graduation)"
            )
        return "\n".join(lines)

    def export_status(self) -> dict:
        """Return key metrics for the dashboard."""
        return {
            "dry_run_trades":     self._model.get("dry_run_trades", 0),
            "live_trades":        self._model.get("live_trades", 0),
            "graduated":          self._model.get("graduated", False),
            "star_strategies":    self._model.get("star_strategies", []),
            "caution_strategies": self._model.get("caution_strategies", []),
            "star_symbols":       self._model.get("star_symbols", []),
            "caution_symbols":    self._model.get("caution_symbols", []),
            "best_hours":         self.best_hours(),
            "last_updated":       self._model.get("last_updated", ""),
            "strategy_count":     len(self._model.get("strategy_scores", {})),
            "blend_pct":          min(100, self._model.get("live_trades", 0)
                                     / LIVE_BLEND_COMPLETE * 100),
        }

    # ── Internal helpers ──────────────────────────────────────────────

    def _wr_to_multiplier(self, win_rate: float, n: int) -> float:
        """Convert win rate to a confidence multiplier (0.5–1.5).
        50% WR → 1.0 (neutral), 30% → 0.6, 70% → 1.4, 80% → 1.5 (capped)."""
        if n < MIN_STRATEGY_SAMPLE:
            return 1.0
        mult = (win_rate / 100) * 2.0
        return round(max(0.5, min(1.5, mult)), 3)

    # ── Persistence ───────────────────────────────────────────────────

    def _save(self):
        try:
            MODEL_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Backup before write
            if MODEL_FILE.exists():
                shutil.copy2(MODEL_FILE, BACKUP_FILE)
            # Atomic write (tmp + os.replace) — a torn knowledge_model.json
            # sends _load() down the "starting fresh" path on the next boot
            # (TD-2, plan 2026-06-12; mirrors risk_manager._save_state).
            atomic_write_json(MODEL_FILE, self._model, indent=2, default=str)
        except Exception as e:
            logger.error(f"[Knowledge] Save error: {e}")

    def _load(self):
        try:
            if MODEL_FILE.exists():
                loaded = json.loads(MODEL_FILE.read_text(encoding="utf-8"))
                # Merge with defaults to handle new fields added in updates
                merged = self._default_model()
                merged.update(loaded)
                self._model = merged
                logger.info(
                    f"[Knowledge] Loaded model: "
                    f"dry={self._model['dry_run_trades']} trades, "
                    f"live={self._model['live_trades']} trades"
                )
            else:
                logger.info(
                    "[Knowledge] No model file found — starting fresh. "
                    "Knowledge will build as dry run trades accumulate."
                )
        except Exception as e:
            logger.warning(f"[Knowledge] Load error ({e}) — using fresh model")
            self._model = self._default_model()

    def is_caution_strategy(self, strategy: str) -> bool:
        """Returns True if strategy is flagged as underperforming (WR < 50%)."""
        return strategy in self._model.get("caution_strategies", [])

    def flag_fee_heavy(self, strategy: str, ratio: float):
        """Flag a strategy as fee-heavy (fees > 20% of gross profit)."""
        fee_heavy = self._model.setdefault("fee_heavy_strategies", {})
        fee_heavy[strategy] = {"ratio": round(ratio, 3),
                               "flagged_at": __import__("datetime").datetime.now().isoformat()}
        self._save()
        logger.warning(f"[Knowledge] Fee-heavy: '{strategy}' fees={ratio:.0%} of gross profit")

    def get_fee_heavy_strategies(self) -> set:
        """Return set of strategy names flagged as fee-heavy."""
        return set(self._model.get("fee_heavy_strategies", {}).keys())

    def _log_status(self):
        dry_n  = self._model.get("dry_run_trades", 0)
        live_n = self._model.get("live_trades", 0)
        graded = self._model.get("graduated", False)
        if dry_n == 0 and live_n == 0:
            logger.info(
                "[Knowledge] No trade history yet. "
                "Run in DRY RUN mode to build strategy knowledge."
            )
        elif live_n == 0:
            logger.info(
                f"[Knowledge] {dry_n} dry run trades in model. "
                "This knowledge will be applied immediately when you go LIVE."
            )
        elif not graded:
            pct = min(100, live_n / LIVE_BLEND_COMPLETE * 100)
            logger.info(
                f"[Knowledge] Blending: {dry_n} dry + {live_n} live trades "
                f"({pct:.0f}% graduated to live data)"
            )
        else:
            logger.info(
                f"[Knowledge] FULLY GRADUATED: {live_n} live trades — "
                "live data drives all decisions."
            )
