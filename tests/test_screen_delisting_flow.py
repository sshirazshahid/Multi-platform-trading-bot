"""Tests for research/screen_delisting_flow.py pure functions (TDD — written first).

Screen pre-registered in _workspace/strategy_pipeline/15a_screen_delisting.md.
2026-07-18: added build_events dedup tests + 404-vs-transient cache-semantics
pins (post-review cache-poisoning fix).
"""

import io
import os
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

import research.screen_delisting_flow as m
from research.screen_delisting_flow import (
    DEDUP_LOOKBACK_D,
    build_events,
    classify_title,
    concurrent_mtm_max_dd,
    event_mean,
    long_net_return,
    months_spanning,
    parse_bitget_candles,
    parse_delist_title,
    parse_vision_funding_csv,
    parse_vision_klines_csv,
    removal_ts_from_date,
    ts_to_seconds,
)


# ── title classification ────────────────────────────────────────────────────
@pytest.mark.parametrize(
    "title,expected",
    [
        ("Binance Will Delist ALCX, ARDR, NFP, POND on 2026-07-10", "spot_delist"),
        ("Binance Will Delist WAVES & OMG on 2025-01-15", "spot_delist"),
        ("Notice of Removal of Spot Trading Pairs - 2026-07-17", "pair_removal"),
        ("Notice of Removal of Margin Trading Pairs - 2026-07-17", "pair_removal"),
        (
            "Binance Futures Will Delist USDⓈ-Margined IPUSDT and IPUSDC "
            "Perpetual Contracts (2026-06-28)",
            "futures_delist",
        ),
        ("Binance Alpha Will Remove TTD, OIK (2026-06-30)", "alpha_removal"),
        ("Binance Margin And Loan Will Delist TST & IOTX on 2026-07-10", "margin_removal"),
        (
            "Binance Will Extend the Monitoring Tag to Include ACT, BLUR on 2026-06-18",
            "monitoring",
        ),
        ("Introducing the New Fan Token", "other"),
    ],
)
def test_classify_title(title, expected):
    assert classify_title(title) == expected


# ── delist title parsing ────────────────────────────────────────────────────
def test_parse_delist_title_batch():
    bases, date = parse_delist_title("Binance Will Delist ALCX, ARDR, NFP, POND on 2026-07-10")
    assert bases == ["ALCX", "ARDR", "NFP", "POND"]
    assert date == "2026-07-10"


def test_parse_delist_title_ampersand_and_digits():
    bases, date = parse_delist_title("Binance Will Delist WAVES & 1000SATS on 2025-01-15")
    assert bases == ["WAVES", "1000SATS"]
    assert date == "2025-01-15"


def test_parse_delist_title_no_date():
    bases, date = parse_delist_title("Binance Will Delist All FTX Leveraged Tokens")
    assert date is None


def test_removal_ts_from_date_is_0300_utc():
    ts = removal_ts_from_date("2026-07-10")
    expected = int(datetime(2026, 7, 10, 3, 0, tzinfo=timezone.utc).timestamp())
    assert ts == expected


# ── timestamp sniffing (vision switched spot dumps ms → μs in 2025) ─────────
@pytest.mark.parametrize(
    "raw,expected",
    [
        (1783998000, 1783998000),  # already seconds
        (1783998000000, 1783998000),  # milliseconds
        (1783998000000000, 1783998000),  # microseconds
    ],
)
def test_ts_to_seconds(raw, expected):
    assert ts_to_seconds(raw) == expected


# ── vision kline CSV parsing (with/without header, ms/μs) ───────────────────
def test_parse_vision_klines_headerless_ms():
    csv = (
        "1704067200000,1.0,1.2,0.9,1.1,1000,1704070799999,1100,10,500,550,0\n"
        "1704070800000,1.1,1.3,1.0,1.2,2000,1704074399999,2400,20,900,1080,0\n"
    )
    df = parse_vision_klines_csv(csv)
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert df["ts"].tolist() == [1704067200, 1704070800]
    assert df["close"].tolist() == [1.1, 1.2]


def test_parse_vision_klines_with_header_us():
    csv = (
        "open_time,open,high,low,close,volume,close_time,quote_volume,count,"
        "taker_buy_volume,taker_buy_quote_volume,ignore\n"
        "1740787200000000,2.0,2.2,1.9,2.1,500,1740790799999999,1050,5,250,525,0\n"
    )
    df = parse_vision_klines_csv(csv)
    assert df["ts"].tolist() == [1740787200]
    assert df["open"].tolist() == [2.0]


# ── vision fundingRate CSV parsing ──────────────────────────────────────────
def test_parse_vision_funding_with_header():
    csv = (
        "calc_time,funding_interval_hours,last_funding_rate\n"
        "1704067200000,8,-0.0005\n"
        "1704096000000,8,0.0001\n"
    )
    ser = parse_vision_funding_csv(csv)
    assert list(ser.index) == [1704067200, 1704096000]
    assert ser.iloc[0] == pytest.approx(-0.0005)


def test_parse_vision_funding_headerless_two_col():
    ser = parse_vision_funding_csv("1704067200000,-0.0003\n1704096000000,0.0002\n")
    assert list(ser.index) == [1704067200, 1704096000]
    assert ser.iloc[1] == pytest.approx(0.0002)


# ── bitget history-candles parsing ──────────────────────────────────────────
def test_parse_bitget_candles_sorted_dedup():
    rows = [
        ["1782892800000", "0.0024", "0.0025", "0.0023", "0.0024", "100", "0.24", "0.24"],
        ["1782889200000", "0.0023", "0.0024", "0.0022", "0.0024", "50", "0.12", "0.12"],
        ["1782892800000", "0.0024", "0.0025", "0.0023", "0.0024", "100", "0.24", "0.24"],
    ]
    df = parse_bitget_candles(rows)
    assert df["ts"].tolist() == [1782889200, 1782892800]
    assert df["volume"].tolist() == [50.0, 100.0]


# ── long-side after-cost return (2b) ────────────────────────────────────────
def test_long_net_return_charges_all_legs():
    # +10% gross, fee 10bps + slip 5bps + half-spread 25bps per side = 80bps RT
    r = long_net_return(1.0, 1.10, fee_per_side=0.001, slip_per_side=0.0005,
                        half_spread=0.0025)
    assert r == pytest.approx(0.10 - 0.008)


def test_long_net_return_negative_when_flat():
    r = long_net_return(1.0, 1.0, fee_per_side=0.001, slip_per_side=0.0005,
                        half_spread=0.0025)
    assert r == pytest.approx(-0.008)


# ── concurrent-MTM account equity max drawdown ──────────────────────────────
def _series(ts0, closes):
    ts = [ts0 + i * 3600 for i in range(len(closes))]
    return pd.DataFrame({"ts": ts, "close": closes})


def test_concurrent_mtm_single_short_win_no_dd():
    # short from 100 -> 80, monotone decline: equity only rises, dd ~ 0
    pos = [{
        "side": -1, "stake": 0.03, "entry_ts": 0, "exit_ts": 3 * 3600,
        "entry_px": 100.0, "series": _series(0, [100.0, 90.0, 85.0, 80.0]),
        "terminal_extra": 0.0,
    }]
    dd = concurrent_mtm_max_dd(pos)
    assert dd == pytest.approx(0.0, abs=1e-9)


def test_concurrent_mtm_overlapping_adverse_moves_stack():
    # two concurrent shorts both pump +50% mid-hold: MTM dd must reflect BOTH
    s = _series(0, [100.0, 150.0, 100.0, 90.0])
    pos = [
        {"side": -1, "stake": 0.03, "entry_ts": 0, "exit_ts": 3 * 3600,
         "entry_px": 100.0, "series": s, "terminal_extra": 0.0},
        {"side": -1, "stake": 0.03, "entry_ts": 0, "exit_ts": 3 * 3600,
         "entry_px": 100.0, "series": s, "terminal_extra": 0.0},
    ]
    dd = concurrent_mtm_max_dd(pos)
    # each short is -0.5 * 0.03 = -1.5% at the spike; two of them = -3%
    assert dd == pytest.approx(0.03, abs=1e-9)


def test_concurrent_mtm_terminal_extra_applied_at_exit():
    # flat price, terminal_extra (funding+costs) = -0.10 on the stake at exit
    pos = [{
        "side": -1, "stake": 0.03, "entry_ts": 0, "exit_ts": 2 * 3600,
        "entry_px": 100.0, "series": _series(0, [100.0, 100.0, 100.0]),
        "terminal_extra": -0.10,
    }]
    dd = concurrent_mtm_max_dd(pos)
    assert dd == pytest.approx(0.003, abs=1e-9)


# ── month spanning helper ───────────────────────────────────────────────────
def test_months_spanning():
    # 2025-12-20 .. 2026-02-03
    lo = 1766188800  # 2025-12-20 00:00 UTC
    hi = 1770076800  # 2026-02-03 00:00 UTC
    assert months_spanning(lo, hi) == ["2025-12", "2026-01", "2026-02"]


def test_gate_thresholds_frozen():
    assert m.MIN_DSR == 0.10
    assert m.MAX_PBO == 0.5
    assert m.MIN_OOS_WR == 0.55
    assert m.MC_MIN_TRADES == 30
    assert m.N_TRIALS_DSR == 6
    assert m.STAKE_FRAC == 0.03
    assert m.MAX_CONCURRENT == 4
    assert m.HALF_SPREAD_2B == 0.0025


def test_event_mean_is_equal_weighted():
    assert event_mean({"AAA": 0.10, "BBB": -0.02}) == pytest.approx(0.04)
    assert np.isnan(event_mean({}))


# ── build_events (dedup + counting) ─────────────────────────────────────────
T0 = datetime(2025, 5, 1, 4, 0, tzinfo=timezone.utc)


def _art(title, dt):
    return {"id": 1, "title": title, "releaseDate": int(dt.timestamp() * 1000)}


def test_build_events_full_subset_dropped_within_21d():
    arts = [
        _art("Binance Will Delist AAA, BBB on 2025-05-08", T0),
        _art("Binance Will Delist AAA on 2025-05-15", T0 + timedelta(days=7)),
    ]
    events, counts = build_events(arts)
    assert len(events) == 1
    assert events[0]["bases"] == ["AAA", "BBB"]
    assert counts["duplicate_events_dropped"] == 1


def test_build_events_partial_overlap_kept():
    arts = [
        _art("Binance Will Delist AAA, BBB on 2025-05-08", T0),
        _art("Binance Will Delist AAA, CCC on 2025-05-15", T0 + timedelta(days=7)),
    ]
    events, counts = build_events(arts)
    assert len(events) == 2
    assert counts["duplicate_events_dropped"] == 0


def test_build_events_dedup_boundary_exactly_lookback():
    # at exactly DEDUP_LOOKBACK_D the prior bases still apply (<=) -> dropped
    arts = [
        _art("Binance Will Delist AAA on 2025-05-08", T0),
        _art("Binance Will Delist AAA on 2025-05-29",
             T0 + timedelta(days=DEDUP_LOOKBACK_D)),
    ]
    events, counts = build_events(arts)
    assert len(events) == 1
    assert counts["duplicate_events_dropped"] == 1
    # one second past the lookback -> a fresh event
    arts2 = [
        _art("Binance Will Delist AAA on 2025-05-08", T0),
        _art("Binance Will Delist AAA on 2025-05-29",
             T0 + timedelta(days=DEDUP_LOOKBACK_D, seconds=1)),
    ]
    events2, counts2 = build_events(arts2)
    assert len(events2) == 2
    assert counts2["duplicate_events_dropped"] == 0


def test_build_events_unparseable_title_counted():
    arts = [_art("Binance Will Delist All FTX Leveraged Tokens", T0)]
    events, counts = build_events(arts)
    assert events == []
    assert counts["spot_delist_unparseable"] == 1


# ── 404-vs-transient cache semantics (cache-poisoning fix, 2026-07-18) ──────
class _Resp:
    def __init__(self, content=b"", status_code=200, payload=None):
        self.content = content
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


def _zip_bytes(text: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("k.csv", text)
    return buf.getvalue()


def test_vision_csv_marks_missing_only_on_404(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_get", lambda url, timeout=30: m.NOT_FOUND)
    assert m._vision_csv("spot/monthly/klines/X/1h/x.zip",
                         "spot/X/2025-01.csv", offline=False) is None
    assert os.path.exists(tmp_path / "vision" / "spot" / "X" / "2025-01.csv.missing")


def test_vision_csv_transient_failure_not_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_get", lambda url, timeout=30: None)  # timeout/429/5xx
    assert m._vision_csv("spot/monthly/klines/X/1h/x.zip",
                         "spot/X/2025-01.csv", offline=False) is None
    vdir = tmp_path / "vision" / "spot" / "X"
    assert not os.path.exists(vdir / "2025-01.csv.missing")
    assert not os.path.exists(vdir / "2025-01.csv")


def test_vision_csv_success_cached_then_served_from_cache(tmp_path, monkeypatch):
    csv = "1704067200000,1.0,1.2,0.9,1.1,10,0,0,0,0,0,0\n"
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_get", lambda url, timeout=30: _Resp(content=_zip_bytes(csv)))
    text = m._vision_csv("spot/monthly/klines/X/1h/x.zip",
                         "spot/X/2025-01.csv", offline=False)
    assert text == csv
    assert (tmp_path / "vision" / "spot" / "X" / "2025-01.csv").exists()

    def _boom(url, timeout=30):
        raise AssertionError("cache hit must not re-fetch")

    monkeypatch.setattr(m, "_get", _boom)
    assert m._vision_csv("spot/monthly/klines/X/1h/x.zip",
                         "spot/X/2025-01.csv", offline=False) == csv


def test_bitget_transient_break_returns_uncached(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_get", lambda url, timeout=30, **kw: None)
    df = m.load_bitget_spot("XUSDT", 1_700_000_000, 1_700_100_000, offline=False)
    assert df is not None and len(df) == 0
    assert list((tmp_path / "bitget").glob("*")) == []  # nothing cached


def test_bitget_40034_notlisted_cached(tmp_path, monkeypatch):
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_get",
                        lambda url, timeout=30, **kw: _Resp(payload={"code": "40034"}))
    assert m.load_bitget_spot("XUSDT", 1_700_000_000, 1_700_100_000,
                              offline=False) is None
    assert (tmp_path / "bitget" / "XUSDT_1700000000_1700100000.notlisted").exists()


def test_bitget_40309_removed_marker_and_short_circuit(tmp_path, monkeypatch):
    # 40309 "The symbol has been removed" (was listed, later removed) -> a
    # distinct .removed marker, None returned, NO CSV cached.
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_get",
                        lambda url, timeout=30, **kw: _Resp(payload={"code": "40309"}))
    assert m.load_bitget_spot("XUSDT", 1_700_000_000, 1_700_100_000,
                              offline=False) is None
    assert (tmp_path / "bitget" / "XUSDT_1700000000_1700100000.removed").exists()
    assert not (tmp_path / "bitget" / "XUSDT_1700000000_1700100000.csv").exists()

    def _boom(url, timeout=30):
        raise AssertionError(".removed marker must short-circuit without network")

    monkeypatch.setattr(m, "_get", _boom)
    assert m.load_bitget_spot("XUSDT", 1_700_000_000, 1_700_100_000,
                              offline=False) is None


def test_bitget_complete_pagination_cached(tmp_path, monkeypatch):
    lo, hi = 1_700_000_000, 1_700_007_200
    rows = [[str(lo * 1000), "1", "1", "1", "1", "5"],
            [str((lo + 3600) * 1000), "1", "1", "1", "1", "6"]]
    monkeypatch.setattr(m, "CACHE_DIR", str(tmp_path))
    monkeypatch.setattr(m, "_get",
                        lambda url, timeout=30, **kw: _Resp(payload={"code": "00000",
                                                                     "data": rows}))
    df = m.load_bitget_spot("XUSDT", lo, hi, offline=False)
    assert len(df) == 2
    assert (tmp_path / "bitget" / f"XUSDT_{lo}_{hi}.csv").exists()
