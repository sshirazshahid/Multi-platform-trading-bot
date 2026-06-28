"""Regression tests for core.balance_utils.deployable_total.

Bug (2026-06-28): in PAPER mode the virtual wallet mirrors one balance into both
the "spot" and "futures" slots, so summing spot+futures double-counted every
non-unified exchange. The Machine cycle reported balance=$26,355 instead of the
true $15,787 (5340*2 + 5227*2 + 5220), inflating the equity base used for
sizing, the §2 exposure cap, and the correlation denominator.
"""
import pytest

from core.balance_utils import deployable_total

# The exact paper-wallet snapshot from the reported startup log.
_PAPER_SNAPSHOT = {
    "binance": {"spot": 5340.2041, "futures": 5340.2041},
    "bybit": {"spot": 5220.3984, "futures": 5220.3984},
    "bitget": {"spot": 5227.0356, "futures": 5227.0356},
}


def test_paper_counts_each_mirrored_wallet_once():
    # PAPER: spot == futures (one wallet) for every exchange -> count once.
    assert deployable_total(_PAPER_SNAPSHOT, dry_run=True) == pytest.approx(15787.6381)


def test_paper_does_not_double_count_regression():
    # The pre-fix value was the double-counted total; guard against regressing.
    double_counted = 5340.2041 * 2 + 5227.0356 * 2 + 5220.3984
    assert deployable_total(_PAPER_SNAPSHOT, dry_run=True) != pytest.approx(double_counted)
    assert double_counted == pytest.approx(26354.8778)  # ~ the buggy $26,355


def test_live_sums_independent_spot_and_futures():
    # LIVE non-unified (binance/bitget) have independent wallets -> sum both;
    # unified (bybit) shares one wallet -> count once.
    balances = {
        "binance": {"spot": 1000.0, "futures": 2000.0},
        "bybit": {"spot": 5000.0, "futures": 5000.0},
        "bitget": {"spot": 500.0, "futures": 250.0},
    }
    # binance 3000 + bitget 750 + bybit 5000 = 8750
    assert deployable_total(balances, dry_run=False) == pytest.approx(8750.0)


def test_live_unified_counted_once():
    balances = {"bybit": {"spot": 4200.0, "futures": 4200.0}}
    assert deployable_total(balances, dry_run=False) == pytest.approx(4200.0)


def test_empty_and_none_are_zero():
    assert deployable_total(None, dry_run=True) == 0.0
    assert deployable_total({}, dry_run=False) == 0.0
