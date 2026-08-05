"""Tests for AccuracyTracker.resolve_outcomes.

Regression suite for the all-flat bug:
  - Old behaviour: 5-min resolution window + 1% threshold → every entry
    resolved as "flat" because crypto moves ~0.1% in 5 min.
  - Fix: 30-min minimum age, 4-h expiry, 0.3% win/loss threshold.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

# Root is already on sys.path via conftest.py
from core.mcp_brain import AccuracyTracker

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_tracker(tmp_path: Path) -> AccuracyTracker:
    """Return a fresh AccuracyTracker whose _save writes to tmp_path."""
    acc_file = tmp_path / "mcp_accuracy.json"
    with patch("core.scoring.accuracy_tracker.ACCURACY_FILE", acc_file):
        tracker = AccuracyTracker()
    tracker._acc_file = acc_file          # store for inspection
    # Monkey-patch _save/_load to use tmp file
    def _save():
        acc_file.parent.mkdir(parents=True, exist_ok=True)
        acc_file.write_text(
            json.dumps(tracker._records[-200:], default=str), encoding="utf-8"
        )
    tracker._save = _save
    return tracker


def _inject_record(tracker: AccuracyTracker, coin: str, action: str,
                   price: float, age_seconds: float, confidence: float = 0.7):
    """Insert a synthetic record with a controlled timestamp."""
    rec = {
        "ts": time.time() - age_seconds,
        "coin": coin,
        "action": action,
        "price": price,
        "confidence": confidence,
        "resolved": False,
        "outcome": None,
    }
    tracker._records.append(rec)
    return rec


# ---------------------------------------------------------------------------
# Window boundary tests
# ---------------------------------------------------------------------------

class TestResolutionWindow:
    def test_too_young_record_not_resolved(self, tmp_path):
        """Records younger than 30 min must remain unresolved."""
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "BNB", "BUY", 660.0, age_seconds=1700)
        tracker.resolve_outcomes({"BNB": 670.0})  # +1.5% — would be a win
        assert tracker._records[-1]["resolved"] is False
        assert tracker._records[-1]["outcome"] is None

    def test_record_at_30min_is_resolved(self, tmp_path):
        """Records exactly 30 min old must be resolved."""
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "BNB", "BUY", 660.0, age_seconds=1800)
        tracker.resolve_outcomes({"BNB": 664.0})  # +0.6% → win
        assert tracker._records[-1]["resolved"] is True
        assert tracker._records[-1]["outcome"] == "win"

    def test_record_beyond_4h_expires(self, tmp_path):
        """Records older than 4 hours must be marked expired, not flat."""
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "ETH", "BUY", 2000.0, age_seconds=14401)
        tracker.resolve_outcomes({"ETH": 2010.0})
        rec = tracker._records[-1]
        assert rec["resolved"] is True
        assert rec["outcome"] == "expired"

    def test_old_5min_window_would_have_been_flat(self, tmp_path):
        """Demonstrate the pre-fix behaviour: a 5-min old record with +0.12%
        move would have resolved as flat under the old 1% threshold."""
        tracker = _make_tracker(tmp_path)
        # BNB moved from 670.25 to 671.05 in 5 min (+0.12%) — old code: flat
        _inject_record(tracker, "BNB", "BUY", 670.25, age_seconds=310)
        tracker.resolve_outcomes({"BNB": 671.05})
        # With the fix: age < 1800 s → still unresolved
        assert tracker._records[-1]["resolved"] is False


# ---------------------------------------------------------------------------
# Win / loss / flat outcome tests
# ---------------------------------------------------------------------------

class TestOutcomeClassification:
    def test_buy_win_above_threshold(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "SOL", "BUY", 80.0, age_seconds=2000)
        # +0.5% move → win (above 0.3% threshold)
        tracker.resolve_outcomes({"SOL": 80.4})
        assert tracker._records[-1]["outcome"] == "win"

    def test_buy_loss_below_threshold(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "SOL", "BUY", 80.0, age_seconds=2000)
        # -0.5% move → loss
        tracker.resolve_outcomes({"SOL": 79.6})
        assert tracker._records[-1]["outcome"] == "loss"

    def test_buy_flat_inside_threshold(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "SOL", "BUY", 80.0, age_seconds=2000)
        # +0.2% move → flat (below 0.3% threshold)
        tracker.resolve_outcomes({"SOL": 80.16})
        assert tracker._records[-1]["outcome"] == "flat"

    def test_sell_win_price_drops(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "XRP", "SELL", 1.34, age_seconds=2000)
        # -0.5% move → win for a SELL
        tracker.resolve_outcomes({"XRP": 1.333})
        assert tracker._records[-1]["outcome"] == "win"

    def test_sell_loss_price_rises(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "XRP", "SELL", 1.33, age_seconds=2000)
        # +0.5% move → loss for a SELL
        tracker.resolve_outcomes({"XRP": 1.337})
        assert tracker._records[-1]["outcome"] == "loss"

    def test_unknown_action_is_flat(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "BTC", "HOLD", 75000.0, age_seconds=2000)
        tracker.resolve_outcomes({"BTC": 76000.0})
        assert tracker._records[-1]["outcome"] == "flat"

    def test_zero_entry_price_skipped(self, tmp_path):
        """Records with price=0 must not be resolved (avoid division by zero)."""
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "BTC", "BUY", 0.0, age_seconds=2000)
        tracker.resolve_outcomes({"BTC": 76000.0})
        assert tracker._records[-1]["resolved"] is False

    def test_coin_not_in_prices_skipped(self, tmp_path):
        """Records for coins absent from current_prices must remain pending."""
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "PEPE", "BUY", 0.00001, age_seconds=2000)
        tracker.resolve_outcomes({"BTC": 75000.0})  # PEPE not present
        assert tracker._records[-1]["resolved"] is False


# ---------------------------------------------------------------------------
# Stats integrity test
# ---------------------------------------------------------------------------

class TestStats:
    def test_stats_counts_wins_and_losses(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        # Inject 3 wins, 2 losses, 1 flat — all pre-resolved
        for _ in range(3):
            r = _inject_record(tracker, "BNB", "BUY", 660.0, age_seconds=2000)
            r["resolved"] = True
            r["outcome"] = "win"
        for _ in range(2):
            r = _inject_record(tracker, "BNB", "BUY", 660.0, age_seconds=2000)
            r["resolved"] = True
            r["outcome"] = "loss"
        r = _inject_record(tracker, "BNB", "BUY", 660.0, age_seconds=2000)
        r["resolved"] = True
        r["outcome"] = "flat"

        s = tracker.stats()
        assert s["wins"] == 3
        assert s["losses"] == 2
        assert s["flat"] == 1
        assert s["total"] == 6
        assert abs(s["win_rate"] - 0.5) < 0.01

    def test_expired_records_excluded_from_stats(self, tmp_path):
        tracker = _make_tracker(tmp_path)
        r = _inject_record(tracker, "ETH", "BUY", 2000.0, age_seconds=14500)
        r["resolved"] = True
        r["outcome"] = "expired"
        s = tracker.stats()
        assert s["total"] == 0

    def test_xrp_rounded_price_resolved_correctly(self, tmp_path):
        """XRP oscillating between 1.33 and 1.34 is a 0.75% move.
        With the new 0.3% threshold this must register as a win for SELL."""
        tracker = _make_tracker(tmp_path)
        _inject_record(tracker, "XRP", "SELL", 1.34, age_seconds=2000)
        tracker.resolve_outcomes({"XRP": 1.33})
        rec = tracker._records[-1]
        assert rec["resolved"] is True
        # 1.33/1.34 - 1 = -0.746% → well above 0.3% threshold for a SELL win
        assert rec["outcome"] == "win"
