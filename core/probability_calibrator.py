"""
core/probability_calibrator.py — True Probability Calibration

Tracks predicted confidence vs actual win/loss outcomes.
Adjusts confidence scores so predictions match reality.

Example: If signals at 70% confidence only win 50% of the time,
the calibrator maps 70% → 50% so the bot doesn't overtrade.

Only trade when CALIBRATED confidence > threshold.
"""

import json
import math
from pathlib import Path
from collections import defaultdict
from loguru import logger

SAVE_PATH = Path("data/calibration.json")
MIN_SAMPLES = 15   # Need 15+ trades per bucket for calibration


class ProbabilityCalibrator:

    # Confidence buckets: [0.40-0.50), [0.50-0.60), ..., [0.90-1.00]
    BUCKET_SIZE = 0.10
    BUCKETS = [0.40, 0.50, 0.55, 0.60, 0.65, 0.70, 0.75, 0.80, 0.85, 0.90]

    def __init__(self):
        # bucket_key → {wins, total}
        self._data: dict[str, dict] = {}
        self._load()

    def record(self, predicted_conf: float, actual_win: bool,
               strategy: str = "all"):
        """Record a trade outcome for calibration."""
        bucket = self._bucket_key(predicted_conf)
        key = f"{strategy}:{bucket}"
        all_key = f"all:{bucket}"

        for k in (key, all_key):
            if k not in self._data:
                self._data[k] = {"wins": 0, "total": 0}
            self._data[k]["total"] += 1
            if actual_win:
                self._data[k]["wins"] += 1

        self._save()

    def calibrate(self, raw_confidence: float,
                  strategy: str = "all") -> float:
        """Return calibrated confidence based on historical accuracy.

        If we have enough data, maps raw confidence to actual win rate.
        If not enough data, returns raw confidence (no adjustment).
        """
        bucket = self._bucket_key(raw_confidence)

        # Try strategy-specific calibration first
        key = f"{strategy}:{bucket}"
        data = self._data.get(key)
        if data and data["total"] >= MIN_SAMPLES:
            actual_wr = data["wins"] / data["total"]
            logger.debug(
                f"[Calibrator] {strategy} {bucket}: "
                f"predicted={raw_confidence:.0%} actual={actual_wr:.0%} "
                f"(n={data['total']})")
            return round(actual_wr, 3)

        # Fall back to global calibration
        all_key = f"all:{bucket}"
        data = self._data.get(all_key)
        if data and data["total"] >= MIN_SAMPLES:
            actual_wr = data["wins"] / data["total"]
            return round(actual_wr, 3)

        # Not enough data — return raw
        return raw_confidence

    def should_trade(self, raw_confidence: float, threshold: float,
                     strategy: str = "all") -> tuple:
        """Return (should_trade: bool, calibrated_conf: float, reason: str)."""
        calibrated = self.calibrate(raw_confidence, strategy)

        if calibrated >= threshold:
            return True, calibrated, f"calibrated {calibrated:.0%} >= {threshold:.0%}"

        # Check if we're being too cautious (raw is high but calibrated is low)
        if raw_confidence >= 0.80 and calibrated < threshold:
            bucket = self._bucket_key(raw_confidence)
            data = self._data.get(f"all:{bucket}", {})
            n = data.get("total", 0)
            if n < MIN_SAMPLES:
                # Not enough data to override high raw confidence
                return True, raw_confidence, f"raw {raw_confidence:.0%} (insufficient calibration data)"

        return False, calibrated, f"calibrated {calibrated:.0%} < {threshold:.0%}"

    def get_calibration_table(self) -> dict:
        """Return calibration table for dashboard."""
        table = {}
        for bucket in self.BUCKETS:
            bk = self._bucket_key(bucket + 0.01)
            all_key = f"all:{bk}"
            data = self._data.get(all_key, {"wins": 0, "total": 0})
            n = data["total"]
            actual = data["wins"] / n if n > 0 else 0
            table[bk] = {
                "predicted_range": f"{bucket:.0%}-{bucket+self.BUCKET_SIZE:.0%}",
                "actual_win_rate": round(actual * 100, 1),
                "samples": n,
                "reliable": n >= MIN_SAMPLES,
                "gap": round((bucket + self.BUCKET_SIZE/2 - actual) * 100, 1) if n > 0 else 0,
            }
        return table

    def get_summary(self) -> dict:
        """Summary stats."""
        total_trades = sum(d["total"] for k, d in self._data.items()
                          if k.startswith("all:"))
        total_wins = sum(d["wins"] for k, d in self._data.items()
                        if k.startswith("all:"))
        overall_wr = total_wins / total_trades * 100 if total_trades > 0 else 0

        # Calibration accuracy (how close predictions match reality)
        calibration_error = 0.0
        calibrated_buckets = 0
        for bucket in self.BUCKETS:
            bk = self._bucket_key(bucket + 0.01)
            data = self._data.get(f"all:{bk}", {"wins": 0, "total": 0})
            if data["total"] >= MIN_SAMPLES:
                predicted = bucket + self.BUCKET_SIZE / 2
                actual = data["wins"] / data["total"]
                calibration_error += abs(predicted - actual)
                calibrated_buckets += 1

        avg_error = calibration_error / max(calibrated_buckets, 1)

        return {
            "total_recorded": total_trades,
            "overall_win_rate": round(overall_wr, 1),
            "calibrated_buckets": calibrated_buckets,
            "avg_calibration_error": round(avg_error * 100, 1),
            "is_calibrated": calibrated_buckets >= 3,
        }

    def _bucket_key(self, conf: float) -> str:
        """Map confidence to bucket key string using bisect for correct boundaries."""
        # Find the rightmost bucket whose lower bound <= conf
        import bisect
        idx = bisect.bisect_right(self.BUCKETS, conf) - 1
        if idx < 0:
            idx = 0
        return f"{self.BUCKETS[idx]:.2f}"

    def _save(self):
        try:
            SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            SAVE_PATH.write_text(
                json.dumps(self._data, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load(self):
        try:
            if SAVE_PATH.exists():
                self._data = json.loads(
                    SAVE_PATH.read_text(encoding="utf-8"))
        except Exception:
            self._data = {}
