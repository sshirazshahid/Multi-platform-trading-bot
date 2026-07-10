"""Offline tests for scripts/tv_client.py (TradingView chart-websocket client).

No network: only the framing / message-parsing / frame-assembly layer is tested.
The thin asyncio fetch wrapper is exercised by scripts/backfill_tv_cache.py at
harvest time, not here.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from scripts import tv_client as tvc


def _frame_of(obj) -> str:
    s = json.dumps(obj, separators=(",", ":"))
    return f"~m~{len(s)}~m~{s}"


def test_frame_roundtrip():
    msg = {"m": "set_auth_token", "p": ["unauthorized_user_token"]}
    raw = tvc.frame_msg(msg)
    parts = tvc.split_frames(raw)
    assert len(parts) == 1
    assert json.loads(parts[0]) == msg


def test_split_frames_multiple_and_heartbeat():
    a = _frame_of({"m": "x", "p": []})
    hb = "~m~4~m~~h~7"
    parts = tvc.split_frames(a + hb + a)
    assert len(parts) == 3
    assert tvc.is_heartbeat(parts[1])
    assert not tvc.is_heartbeat(parts[0])
    assert parts[1] == "~h~7"


def test_collector_timescale_update_and_completion():
    c = tvc.SeriesCollector()
    update = {
        "m": "timescale_update",
        "p": [
            "cs_x",
            {
                "sds_1": {
                    "s": [
                        {"i": 0, "v": [1781100000, 1.0, 2.0, 0.5, 1.5, 10.0]},
                        {"i": 1, "v": [1781186400, 1.5, 2.5, 1.0, 2.0, 20.0]},
                    ]
                }
            },
        ],
    }
    c.feed(json.dumps(update))
    assert len(c.bars) == 2
    assert not c.done
    c.feed(json.dumps({"m": "series_completed", "p": ["cs_x", "sds_1"]}))
    assert c.done
    assert c.error is None


def test_collector_du_update_collected():
    c = tvc.SeriesCollector()
    du = {
        "m": "du",
        "p": ["cs_x", {"sds_1": {"s": [{"i": 0, "v": [1781100000, 1, 2, 0.5, 1.5, 3.0]}]}}],
    }
    c.feed(json.dumps(du))
    assert len(c.bars) == 1


def test_collector_error_sets_error_and_done():
    c = tvc.SeriesCollector()
    c.feed(json.dumps({"m": "symbol_error", "p": ["cs_x", "sds_sym_1", "invalid symbol"]}))
    assert c.done
    assert c.error is not None


def test_collector_five_field_bar_gets_zero_volume():
    c = tvc.SeriesCollector()
    update = {
        "m": "timescale_update",
        "p": ["cs_x", {"sds_1": {"s": [{"i": 0, "v": [1781100000, 1.0, 2.0, 0.5, 1.5]}]}}],
    }
    c.feed(json.dumps(update))
    assert len(c.bars) == 1
    assert c.bars[0][5] == 0.0


def test_collector_ignores_junk():
    c = tvc.SeriesCollector()
    c.feed("not json at all")
    c.feed(json.dumps({"m": "quote_completed", "p": []}))
    assert c.bars == [] and not c.done


def test_to_frame_sorts_dedupes_and_types():
    bars = [
        [1781186400, 1.5, 2.5, 1.0, 2.0, 20.0],
        [1781100000, 1.0, 2.0, 0.5, 1.5, 10.0],
        [1781186400, 1.5, 2.5, 1.0, 2.0, 20.0],  # dup ts
    ]
    df = tvc.to_frame(bars)
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume"]
    assert df["ts"].tolist() == [1781100000, 1781186400]
    assert str(df["ts"].dtype) == "int64"
    assert df["close"].tolist() == [1.5, 2.0]


def test_to_frame_keeps_last_received_duplicate():
    # du updates supersede the initial snapshot of the same (developing) bar
    bars = [
        [1781100000, 1.0, 2.0, 0.5, 1.5, 10.0],
        [1781100000, 1.0, 2.2, 0.5, 1.9, 12.0],  # later update, same ts
    ]
    df = tvc.to_frame(bars)
    assert len(df) == 1
    assert df["close"].iloc[0] == 1.9


def test_collector_null_price_field_skipped():
    c = tvc.SeriesCollector()
    update = {
        "m": "timescale_update",
        "p": ["cs_x", {"sds_1": {"s": [{"i": 0, "v": [1781100000, 1.0, None, 0.5, 1.5, 3.0]}]}}],
    }
    c.feed(json.dumps(update))
    assert c.bars == []  # a null price must never become a 0.0 OHLC value


def test_drop_partial_last():
    df = tvc.to_frame([[1781049600, 1, 2, 0.5, 1.5, 1.0], [1781136000, 1, 2, 0.5, 1.6, 1.0]])
    # now = mid-period of the last daily bar -> developing bar dropped
    out = tvc.drop_partial_last(df, "1D", now_ts=1781136000 + 3600)
    assert out["ts"].tolist() == [1781049600]
    # now = after the bar completed -> kept
    out2 = tvc.drop_partial_last(df, "1D", now_ts=1781136000 + 86400)
    assert len(out2) == 2
    # unknown resolution -> no-op
    out3 = tvc.drop_partial_last(df, "1M", now_ts=1781136000 + 3600)
    assert len(out3) == 2


def test_to_frame_empty():
    df = tvc.to_frame([])
    assert len(df) == 0
    assert list(df.columns) == ["ts", "open", "high", "low", "close", "volume"]


def test_cache_path_naming():
    p = tvc.cache_path("CRYPTOCAP:USDT.D", "1D")
    assert p.name == "CRYPTOCAP_USDT.D_1D.parquet"
    assert p.parent.name == "tv_cache"
    p2 = tvc.cache_path("BINANCE:BTCUSDT", "1h")
    assert p2.name == "BINANCE_BTCUSDT_1h.parquet"


def test_save_and_load_cache_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setattr(tvc, "CACHE_DIR", tmp_path)
    df = tvc.to_frame([[1781100000, 1.0, 2.0, 0.5, 1.5, 10.0]])
    out = tvc.save_cache(df, "CRYPTOCAP:USDT.D", "1D")
    assert out.exists()
    back = pd.read_parquet(out)
    assert back["ts"].tolist() == [1781100000]


def test_tv_resolution_mapping():
    # intraday resolutions go to TV as bare minutes; daily+ pass through
    assert tvc.tv_resolution("1h") == "60"
    assert tvc.tv_resolution("4h") == "240"
    assert tvc.tv_resolution("15m") == "15"
    assert tvc.tv_resolution("1D") == "1D"
    assert tvc.tv_resolution("1W") == "1W"


def test_fetch_ohlcv_sync_rejects_bad_args():
    with pytest.raises(ValueError):
        tvc.fetch_ohlcv("NOCOLON", "1D", 10)
    with pytest.raises(ValueError):
        tvc.fetch_ohlcv("CRYPTOCAP:USDT.D", "1D", 0)
