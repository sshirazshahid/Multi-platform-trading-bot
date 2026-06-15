"""
core/prediction_tracker.py — Accuracy tracking for the prediction sub-agent.

Reads prediction_agent_log.jsonl and correlates predictions with trade outcomes
from the warehouse. Computes:
  1. Directional accuracy (did CONFIRM trades win more than VETO'd trades?)
  2. Calibration curve (does 80% confidence actually win 80% of the time?)
  3. Value-add metric (PnL of CONFIRM trades vs PnL of all MCP-approved trades)
  4. Auto-disable signal (accuracy < 50% over 50+ resolved trades)

The tracker is read-only — it never modifies the warehouse or prediction log.
It is called by:
  - PredictionAgent._check_auto_disable() on every prediction call
  - dashboard.py for display
  - learning_engine.py for periodic reports
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path

from loguru import logger

PREDICTION_LOG = Path("data/prediction_agent_log.jsonl")
ACCURACY_CACHE = Path("data/prediction_accuracy.json")

# Calibration bucket boundaries
_CALIBRATION_BUCKETS = [0.0, 0.2, 0.4, 0.5, 0.6, 0.7, 0.8, 0.9, 1.01]


@dataclass
class AccuracyStats:
    """Summary statistics for prediction accuracy."""
    total_predictions: int = 0
    total_resolved: int = 0  # predictions where trade outcome is known
    confirm_count: int = 0
    veto_count: int = 0
    weak_confirm_count: int = 0
    # Directional accuracy
    directional_accuracy: float = 0.0  # fraction of correct direction calls
    # Outcome by prediction type
    confirm_win_rate: float = 0.0
    confirm_avg_pnl: float = 0.0
    veto_would_have_won_rate: float = 0.0  # of VETOed, how many would have won
    veto_avg_avoided_pnl: float = 0.0      # avg PnL of trades we VETOed (counterfactual)
    # Value-add
    value_add_pnl: float = 0.0  # PnL of CONFIRM - PnL of all (estimation of agent value)
    # Calibration
    calibration_buckets: list[dict] | None = None
    # Cost
    total_cost_usd: float = 0.0
    avg_cost_per_prediction: float = 0.0
    # Meta
    computed_at: float = 0.0
    window_days: int = 30


class PredictionTracker:
    """Reads prediction log + warehouse to compute accuracy metrics."""

    def __init__(self, log_path: Path | None = None):
        self._log_path = log_path or PREDICTION_LOG

    def load_predictions(self, since_days: int = 30) -> list[dict]:
        """Load prediction entries from the JSONL log."""
        if not self._log_path.exists():
            return []

        cutoff = time.time() - since_days * 86400
        predictions = []
        try:
            with self._log_path.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        entry = json.loads(line)
                        if entry.get("ts", 0) >= cutoff:
                            predictions.append(entry)
                    except json.JSONDecodeError:
                        continue
        except Exception as e:
            logger.debug(f"[PredTracker] load error: {e}")

        return predictions

    def _load_trade_outcomes(self, symbols: set[str], since_days: int = 30) -> dict:
        """Load closed trade outcomes from warehouse for the given symbols.

        Returns dict keyed by (symbol, side, approx_ts) -> outcome dict.
        """
        try:
            from core.warehouse import get_warehouse
            wh = get_warehouse()
        except Exception:
            return {}

        cutoff = time.time() - since_days * 86400
        outcomes = {}
        try:
            trades = wh.query(
                "SELECT symbol, side, ts_entry, realized_pnl, r_multiple, "
                "hold_sec, exit_reason, mcp_score "
                "FROM trades WHERE status='CLOSED' AND ts_entry >= ?",
                (cutoff,),
            )
            for t in trades:
                sym = t.get("symbol", "")
                side = t.get("side", "")
                ts = t.get("ts_entry", 0)
                outcomes[(sym, side, ts)] = {
                    "realized_pnl": t.get("realized_pnl"),
                    "r_multiple": t.get("r_multiple"),
                    "hold_sec": t.get("hold_sec"),
                    "exit_reason": t.get("exit_reason"),
                    "mcp_score": t.get("mcp_score"),
                    "won": (t.get("realized_pnl") or 0) > 0,
                }
        except Exception as e:
            logger.debug(f"[PredTracker] warehouse query error: {e}")

        return outcomes

    def _match_prediction_to_outcome(
        self,
        pred: dict,
        outcomes: dict,
        tolerance_sec: float = 600,  # 10 min window
    ) -> dict | None:
        """Find the trade outcome that matches a prediction entry.

        Matches on (symbol, side) within a time tolerance of the prediction timestamp.
        """
        pred_symbol = pred.get("symbol", "")
        pred_side = pred.get("side", "")
        pred_ts = pred.get("ts", 0)

        # Normalize symbol format
        # Prediction uses "BTC/USDT", warehouse might use "BTC/USDT:USDT"
        sym_variants = {pred_symbol, f"{pred_symbol}:USDT"}

        best_match = None
        best_dt = float("inf")

        for (sym, side, ts), outcome in outcomes.items():
            if sym not in sym_variants and pred_symbol not in {sym, sym.split(":")[0]}:
                continue
            if side != pred_side:
                continue
            dt = abs(ts - pred_ts)
            if dt < tolerance_sec and dt < best_dt:
                best_dt = dt
                best_match = outcome

        return best_match

    def accuracy_stats(self, since_days: int = 30) -> dict | None:
        """Compute accuracy statistics for predictions in the window.

        Returns a dict (not AccuracyStats dataclass) for JSON serialization.
        Returns None if no predictions exist.
        """
        predictions = self.load_predictions(since_days)
        if not predictions:
            return None

        # Collect all symbols for the warehouse query
        symbols = {p.get("symbol", "") for p in predictions}
        outcomes = self._load_trade_outcomes(symbols, since_days)

        # Match predictions to outcomes
        resolved = []
        for pred in predictions:
            outcome = self._match_prediction_to_outcome(pred, outcomes)
            if outcome is not None:
                resolved.append({**pred, "_outcome": outcome})

        if not resolved:
            return {
                "total_predictions": len(predictions),
                "total_resolved": 0,
                "directional_accuracy": 0.5,  # unknown
                "computed_at": time.time(),
                "window_days": since_days,
            }

        # Compute directional accuracy
        correct_direction = 0
        confirm_trades = []
        veto_trades = []
        weak_trades = []

        for r in resolved:
            prediction = r.get("prediction", "WEAK_CONFIRM")
            won = r["_outcome"]["won"]

            if prediction == "CONFIRM":
                confirm_trades.append(r)
                if won:
                    correct_direction += 1
            elif prediction == "VETO":
                veto_trades.append(r)
                if not won:
                    correct_direction += 1  # correctly predicted loss
            elif prediction == "WEAK_CONFIRM":
                weak_trades.append(r)
                # WEAK_CONFIRM is directionally neutral; count as half-correct
                correct_direction += 0.5

        directional_acc = correct_direction / len(resolved) if resolved else 0.5

        # Confirm win rate
        confirm_wins = sum(1 for t in confirm_trades if t["_outcome"]["won"])
        confirm_wr = confirm_wins / len(confirm_trades) if confirm_trades else 0.0
        confirm_avg_pnl = (
            sum(t["_outcome"].get("realized_pnl", 0) or 0 for t in confirm_trades)
            / len(confirm_trades)
        ) if confirm_trades else 0.0

        # Veto counterfactual: of the trades we veto'd, how many would have won?
        # These are trades that the MCP approved but the agent blocked.
        # In ADVISOR mode, the trades still executed so we can measure directly.
        # In VETO mode, the trades were blocked — we only know the counterfactual
        # if the same setup was also present in candidates (future: sim).
        veto_would_win = sum(1 for t in veto_trades if t["_outcome"]["won"])
        veto_wr = veto_would_win / len(veto_trades) if veto_trades else 0.0
        veto_avg_pnl = (
            sum(t["_outcome"].get("realized_pnl", 0) or 0 for t in veto_trades)
            / len(veto_trades)
        ) if veto_trades else 0.0

        # Value-add: avg PnL of CONFIRM vs avg PnL of all resolved
        all_avg_pnl = (
            sum(t["_outcome"].get("realized_pnl", 0) or 0 for t in resolved)
            / len(resolved)
        ) if resolved else 0.0
        value_add = confirm_avg_pnl - all_avg_pnl if confirm_trades else 0.0

        # Calibration curve
        calibration = self._compute_calibration(resolved)

        # Cost
        total_cost = sum(p.get("cost_usd", 0) for p in predictions)
        avg_cost = total_cost / len(predictions) if predictions else 0.0

        stats = {
            "total_predictions": len(predictions),
            "total_resolved": len(resolved),
            "confirm_count": len(confirm_trades),
            "veto_count": len(veto_trades),
            "weak_confirm_count": len(weak_trades),
            "directional_accuracy": round(directional_acc, 4),
            "confirm_win_rate": round(confirm_wr, 4),
            "confirm_avg_pnl": round(confirm_avg_pnl, 4),
            "veto_would_have_won_rate": round(veto_wr, 4),
            "veto_avg_avoided_pnl": round(veto_avg_pnl, 4),
            "value_add_pnl": round(value_add, 4),
            "calibration_buckets": calibration,
            "total_cost_usd": round(total_cost, 4),
            "avg_cost_per_prediction": round(avg_cost, 6),
            "computed_at": time.time(),
            "window_days": since_days,
        }

        # Cache to disk
        self._save_cache(stats)

        return stats

    def _compute_calibration(self, resolved: list[dict]) -> list[dict]:
        """Compute calibration curve: predicted confidence vs actual win rate.

        Buckets predictions by confidence level and computes actual win rate
        in each bucket. Perfect calibration: 80% confidence bucket has 80% wins.
        """
        buckets = []
        for i in range(len(_CALIBRATION_BUCKETS) - 1):
            lo = _CALIBRATION_BUCKETS[i]
            hi = _CALIBRATION_BUCKETS[i + 1]

            in_bucket = [
                r for r in resolved
                if r.get("prediction") in ("CONFIRM", "WEAK_CONFIRM")
                and lo <= (r.get("confidence") or 0) < hi
            ]

            if not in_bucket:
                buckets.append({
                    "confidence_range": f"{lo:.1f}-{hi:.1f}",
                    "n": 0,
                    "actual_win_rate": None,
                    "predicted_confidence_avg": None,
                })
                continue

            wins = sum(1 for r in in_bucket if r["_outcome"]["won"])
            avg_conf = sum(r.get("confidence", 0) for r in in_bucket) / len(in_bucket)

            buckets.append({
                "confidence_range": f"{lo:.1f}-{hi:.1f}",
                "n": len(in_bucket),
                "actual_win_rate": round(wins / len(in_bucket), 4),
                "predicted_confidence_avg": round(avg_conf, 4),
            })

        return buckets

    def _save_cache(self, stats: dict) -> None:
        """Save accuracy stats to disk for dashboard/reporting."""
        try:
            ACCURACY_CACHE.parent.mkdir(parents=True, exist_ok=True)
            with ACCURACY_CACHE.open("w", encoding="utf-8") as f:
                json.dump(stats, f, indent=2, default=str)
        except Exception as e:
            logger.debug(f"[PredTracker] cache save error: {e}")

    def load_cached_stats(self) -> dict | None:
        """Load cached accuracy stats from disk (fast, no recomputation)."""
        if not ACCURACY_CACHE.exists():
            return None
        try:
            with ACCURACY_CACHE.open("r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def summary_report(self, since_days: int = 30) -> str:
        """Human-readable accuracy report for logging/dashboard."""
        stats = self.accuracy_stats(since_days)
        if stats is None:
            return "[PredTracker] No predictions to analyze."

        lines = [
            "=== Prediction Agent Accuracy Report ===",
            f"Window: {stats['window_days']} days",
            f"Total predictions: {stats['total_predictions']}",
            f"Resolved (matched to trade): {stats['total_resolved']}",
            "",
            f"Directional accuracy: {stats['directional_accuracy']:.1%}",
            f"  CONFIRM trades: {stats['confirm_count']} "
            f"(WR={stats['confirm_win_rate']:.0%}, "
            f"avg=${stats['confirm_avg_pnl']:+.2f})",
            f"  VETO'd trades: {stats['veto_count']} "
            f"(would have won {stats['veto_would_have_won_rate']:.0%}, "
            f"avg PnL avoided=${stats['veto_avg_avoided_pnl']:+.2f})",
            f"  WEAK_CONFIRM: {stats['weak_confirm_count']}",
            "",
            f"Value-add (CONFIRM avg - all avg): ${stats['value_add_pnl']:+.2f}/trade",
            "",
            f"Total API cost: ${stats['total_cost_usd']:.2f}",
            f"Avg cost/prediction: ${stats['avg_cost_per_prediction']:.4f}",
        ]

        # Calibration
        cal = stats.get("calibration_buckets", [])
        if cal:
            lines.append("")
            lines.append("Calibration (predicted conf vs actual WR):")
            for bucket in cal:
                n = bucket.get("n", 0)
                if n == 0:
                    continue
                lines.append(
                    f"  {bucket['confidence_range']}: "
                    f"n={n}, actual WR={bucket['actual_win_rate']:.0%}, "
                    f"avg conf={bucket['predicted_confidence_avg']:.2f}"
                )

        # Auto-disable warning
        if stats["total_resolved"] >= 50 and stats["directional_accuracy"] < 0.50:
            lines.append("")
            lines.append(
                "WARNING: Directional accuracy below 50% over 50+ trades. "
                "Agent should be auto-disabled."
            )

        return "\n".join(lines)
