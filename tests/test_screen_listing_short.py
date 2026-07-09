"""Unit tests for the post-listing perp-short screen core math.

Pure-function tests on SYNTHETIC inputs only (no market data is synthesized for
the verdict — these exercise the deterministic calculations the screen relies on).
Run: venv/Scripts/python.exe -m pytest tests/test_screen_listing_short.py -v
"""
from __future__ import annotations

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import research.screen_listing_short as ls  # noqa: E402
from research.screen_listing_short import (  # noqa: E402
    apply_concurrency_cap,
    find_backfill_clusters,
    funding_pnl_short,
    funding_sum_in_window,
    is_crypto_base,
    load_funding_history,
    price_at,
    short_net_return,
    window_funding_covered,
)


def test_find_backfill_clusters_flags_shared_timestamps():
    firsts = {"A": 100, "B": 100, "C": 100, "D": 200, "E": 300, "F": 200}
    # 100 shared by 3 → cluster; 200 shared by 2 (< 3) → not; 300 unique → not
    clusters = find_backfill_clusters(firsts, min_shared=3)
    assert clusters == {100}


def test_find_backfill_clusters_empty_when_all_unique():
    firsts = {"A": 1, "B": 2, "C": 3}
    assert find_backfill_clusters(firsts, min_shared=3) == set()


def test_short_net_return_profit_on_decline():
    # short 100 -> cover 90; funding 0; fee 5bps + slip 5bps per side, 2 sides
    r = short_net_return(
        entry_price=100.0, exit_price=90.0, funding_sum=0.0,
        fee_per_side=0.0005, slip_per_side=0.0005,
    )
    # gross short = (100-90)/100 = 0.10 ; round-trip cost = 2*(0.0005+0.0005)=0.002
    assert r == pytest.approx(0.10 - 0.002, abs=1e-12)


def test_short_net_return_loss_on_rally():
    r = short_net_return(
        entry_price=100.0, exit_price=110.0, funding_sum=0.0,
        fee_per_side=0.0005, slip_per_side=0.0005,
    )
    # gross short = (100-110)/100 = -0.10 ; minus 0.002 cost
    assert r == pytest.approx(-0.10 - 0.002, abs=1e-12)


def test_negative_funding_is_a_cost_to_the_short():
    # flat price, no fees: a negative funding sum must REDUCE the short return
    # (short pays on negative-funding tokens — the pre-declared killer).
    r = short_net_return(
        entry_price=100.0, exit_price=100.0, funding_sum=-0.02,
        fee_per_side=0.0, slip_per_side=0.0,
    )
    assert r == pytest.approx(-0.02, abs=1e-12)


def test_positive_funding_is_income_to_the_short():
    r = short_net_return(
        entry_price=100.0, exit_price=100.0, funding_sum=+0.015,
        fee_per_side=0.0, slip_per_side=0.0,
    )
    assert r == pytest.approx(+0.015, abs=1e-12)


def test_funding_pnl_short_sums_rates_with_short_sign():
    # short receives +rate each settlement; net of a negative-dominant series is negative
    assert funding_pnl_short([0.0001, -0.0002, 0.0001]) == pytest.approx(0.0, abs=1e-12)
    assert funding_pnl_short([-0.001, -0.001]) == pytest.approx(-0.002, abs=1e-12)
    assert funding_pnl_short([]) == 0.0


def test_price_at_returns_first_bar_at_or_after_target():
    df = pd.DataFrame(
        {"ts": [1000, 2000, 3000, 4000], "close": [10.0, 20.0, 30.0, 40.0]}
    )
    # target between bars -> next bar
    price, actual = price_at(df, 2500)
    assert price == 30.0 and actual == 3000
    # exact match -> that bar
    price, actual = price_at(df, 2000)
    assert price == 20.0 and actual == 2000


def test_price_at_returns_none_past_end():
    df = pd.DataFrame({"ts": [1000, 2000], "close": [10.0, 20.0]})
    assert price_at(df, 5000) == (None, None)


# ── rev2: funding_history reader + window coverage (synthetic tmp fixtures) ───
def _write_funding(d, venue, base, ts, rates):
    p = d / f"{venue}_{base}.csv"
    pd.DataFrame({"ts": ts, "funding_rate": rates, "venue": venue,
                  "symbol": f"{base}/USDT:USDT"}).to_csv(p, index=False)


def test_load_funding_history_reads_ts_rate(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "FUNDING_HISTORY_DIR", str(tmp_path))
    _write_funding(tmp_path, "binance", "ASTER", [100, 108, 116], [-0.001, -0.0005, 0.0002])
    s = load_funding_history("ASTER", "binance")
    assert list(s.index) == [100, 108, 116]
    assert s.loc[116] == pytest.approx(0.0002)


def test_load_funding_history_absent_returns_empty(tmp_path, monkeypatch):
    monkeypatch.setattr(ls, "FUNDING_HISTORY_DIR", str(tmp_path))
    assert load_funding_history("NOPE", "binance").empty


def test_funding_sum_in_window_half_open_interval():
    import pandas as pd
    s = pd.Series([0.001, 0.002, 0.004, 0.008], index=[100, 200, 300, 400])
    # [entry=200, exit=400) -> settlements at 200 and 300 only
    assert funding_sum_in_window(s, 200, 400) == pytest.approx(0.002 + 0.004)


def test_funding_sum_negative_dominant_is_a_short_cost():
    import pandas as pd
    s = pd.Series([-0.002, -0.003, 0.001], index=[100, 200, 300])
    # short receives +sum; a negative-dominant window is a NET COST to the short
    assert funding_sum_in_window(s, 100, 400) == pytest.approx(-0.004)


def test_window_funding_covered_true_when_spanning():
    import pandas as pd
    ts = list(range(1000, 1000 + 8 * 3600 * 12, 8 * 3600))  # 12 settlements
    s = pd.Series([0.0001] * len(ts), index=ts)
    entry, exit_ = ts[1], ts[-2]
    assert window_funding_covered(s, entry, exit_) is True


def test_window_funding_covered_false_when_gap_at_end():
    import pandas as pd
    ts = [1000, 1000 + 8 * 3600, 1000 + 16 * 3600]
    s = pd.Series([0.0001] * 3, index=ts)
    # exit far beyond last funding ts -> not covered (would require guessing)
    assert window_funding_covered(s, 1000, 1000 + 40 * 24 * 3600) is False


def test_window_funding_covered_false_when_empty():
    import pandas as pd
    assert window_funding_covered(pd.Series(dtype=float), 100, 200) is False


# ── rev3: crypto-only filter + concurrency cap (pure functions) ───────────────
def test_is_crypto_base_keeps_crypto():
    assert is_crypto_base("BTC")
    assert is_crypto_base("ASTER")
    assert is_crypto_base("SOMI")


def test_is_crypto_base_excludes_equity_commodity_and_junk():
    for b in ("AAPL", "AMZN", "CL", "COIN", "COPPER", "MSFT", "MSTR", "TSLA", "XAG", "XAU"):
        assert not is_crypto_base(b), b
    assert not is_crypto_base("币安人生")  # non-ASCII scrape junk
    assert not is_crypto_base("")


def test_concurrency_cap_skips_beyond_cap_in_entry_order():
    # 5 positions all still open together; cap=4 -> the LAST entrant is skipped.
    ee = [(0, 100, 200), (1, 110, 200), (2, 120, 200), (3, 130, 200), (4, 140, 200)]
    acc, capped, peak = apply_concurrency_cap(ee, max_concurrent=4)
    assert peak == 4
    assert acc == [0, 1, 2, 3]
    assert capped == [4]


def test_concurrency_cap_releases_closed_positions():
    # sequential, non-overlapping -> all accepted, peak concurrency 1.
    ee = [(0, 100, 150), (1, 160, 200), (2, 210, 260)]
    acc, capped, peak = apply_concurrency_cap(ee, max_concurrent=4)
    assert acc == [0, 1, 2]
    assert capped == []
    assert peak == 1


def test_concurrency_cap_no_cherry_picking_is_purely_chronological():
    # provide out-of-order input; acceptance follows ENTRY time, not list order
    # or return. The latest-entering overlapping position is the one skipped.
    ee = [(4, 104, 300), (0, 100, 300), (3, 103, 300), (1, 101, 300), (2, 102, 300)]
    acc, capped, peak = apply_concurrency_cap(ee, max_concurrent=4)
    assert acc == [0, 1, 2, 3]  # entry-time order
    assert capped == [4]        # the last to enter, skipped regardless of outcome
    assert peak == 4
