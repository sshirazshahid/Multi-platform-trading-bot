"""Research-integrity regression tests for the offline machine replay."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd
import pytest

from scripts import machine_strategy_replay as replay


def _args(cache_dir: Path, market_type: str, **overrides):
    values = {
        "cache_dir": str(cache_dir),
        "timeframe": "15m",
        "market_type": market_type,
        "max_files": 0,
        "max_bars": 0,
        "min_bars": 40,
        "minimum_symbols": 1,
        "minimum_history_bars": 80,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def _write_frame(path: Path, rows: int = 120) -> None:
    close = [100.0 + i * 0.01 for i in range(rows)]
    pd.DataFrame(
        {
            "ts": list(range(1_700_000_000, 1_700_000_000 + rows)),
            "open": close,
            "high": [value + 0.2 for value in close],
            "low": [value - 0.2 for value in close],
            "close": close,
            "volume": [1000.0] * rows,
        }
    ).to_csv(path, index=False)


def test_market_type_selects_distinct_spot_and_futures_cache_keys(tmp_path):
    spot = tmp_path / "BTC-USDT_15m.csv"
    futures = tmp_path / "BTC-USDTUSDT_15m.parquet"
    spot.touch()
    futures.touch()

    spot_selection = replay.select_datasets(_args(tmp_path, "spot"))
    futures_selection = replay.select_datasets(_args(tmp_path, "futures"))

    assert spot_selection.files == [str(spot)]
    assert futures_selection.files == [str(futures)]
    assert spot_selection.datasets[0].symbol == "BTC/USDT"
    assert futures_selection.datasets[0].symbol == "BTC/USDT:USDT"


def test_explicit_metadata_can_classify_noncanonical_file(tmp_path):
    path = tmp_path / "legacy_eth_15m.csv"
    path.touch()
    Path(str(path) + ".meta.json").write_text(
        json.dumps({"market_type": "futures", "symbol": "ETH/USDT:USDT"}),
        encoding="utf-8",
    )

    dataset = replay.classify_ohlcv_path(path, "15m")

    assert dataset.market_type == "futures"
    assert dataset.symbol == "ETH/USDT:USDT"
    assert dataset.classification == "metadata"


def test_conflicting_metadata_and_futures_cache_key_is_rejected(tmp_path):
    path = tmp_path / "BTC-USDTUSDT_15m.csv"
    path.touch()
    Path(str(path) + ".meta.json").write_text(
        json.dumps({"market_type": "spot", "symbol": "BTC/USDT"}),
        encoding="utf-8",
    )

    with pytest.raises(replay.DatasetSelectionError, match="conflicting market identity"):
        replay.classify_ohlcv_path(path, "15m")


def test_metadata_market_must_match_its_unified_symbol(tmp_path):
    path = tmp_path / "legacy_btc_15m.csv"
    path.touch()
    Path(str(path) + ".meta.json").write_text(
        json.dumps({"market_type": "futures", "symbol": "BTC/USDT"}),
        encoding="utf-8",
    )

    with pytest.raises(replay.DatasetSelectionError, match="market_type conflicts"):
        replay.classify_ohlcv_path(path, "15m")


def test_same_market_duplicate_base_is_excluded_not_double_counted(tmp_path):
    (tmp_path / "BTC-USDTUSDT_15m.csv").touch()
    (tmp_path / "BTC-USDTUSDT_15m.parquet").touch()
    eth = tmp_path / "ETH-USDTUSDT_15m.csv"
    eth.touch()

    selection = replay.select_datasets(_args(tmp_path, "futures"))

    assert selection.files == [str(eth)]
    assert any("duplicate futures base BTC" in issue for issue in selection.issues)


def test_bounded_selection_is_stable_and_not_first_alphabetical_symbols(tmp_path):
    all_paths = []
    for base in ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ"):
        path = tmp_path / f"{base}-USDTUSDT_15m.csv"
        path.touch()
        all_paths.append(path)
    args = _args(tmp_path, "futures", max_files=4)

    first = replay.select_datasets(args)
    second = replay.select_datasets(args)
    selected_names = [Path(path).name for path in first.files]

    assert first.files == second.files
    assert selected_names != [path.name for path in sorted(all_paths)[:4]]
    assert first.as_dict()["selection_method"] == "sha256_symbol_sample_v1"


def test_headline_drawdown_uses_global_chronological_trade_order():
    # File-concatenation order would be +20%, -15%, -10% => 25% drawdown.
    trades = [
        {"ts": 2, "symbol": "B/USDT:USDT", "side": "buy", "ret": 0.20, "score": 1},
        {"ts": 3, "symbol": "C/USDT:USDT", "side": "buy", "ret": -0.15, "score": 1},
        {"ts": 1, "symbol": "A/USDT:USDT", "side": "buy", "ret": -0.10, "score": 1},
    ]

    summary = replay.summarize(trades)

    assert summary["ts_start"] == 1
    assert summary["ts_end"] == 3
    assert summary["max_drawdown"] == pytest.approx(0.15)


def test_futures_panel_fails_closed_on_spot_only_cache(tmp_path):
    _write_frame(tmp_path / "BTC-USDT_15m.csv")

    datasets, audit = replay.prepare_replay_datasets(_args(tmp_path, "futures", minimum_symbols=1))

    assert datasets == []
    assert audit["eligible"] is False
    assert audit["selection"]["classified_counts"]["spot"] == 1
    assert any("no explicitly classified futures" in reason for reason in audit["reasons"])


def test_futures_panel_reports_insufficient_breadth_and_history(tmp_path):
    _write_frame(tmp_path / "BTC-USDTUSDT_15m.csv", rows=120)
    _write_frame(tmp_path / "ETH-USDTUSDT_15m.csv", rows=50)

    datasets, audit = replay.prepare_replay_datasets(
        _args(
            tmp_path,
            "futures",
            minimum_symbols=2,
            minimum_history_bars=100,
        )
    )

    assert [dataset.base for dataset, _ in datasets] == ["BTC"]
    assert audit["eligible"] is False
    assert "eligible_symbols 1 < 2" in audit["reasons"]
    assert "1 selected files failed schema/history validation" in audit["warnings"]


def test_holdout_has_embargo_and_filters_validation_entries(monkeypatch):
    frame_path = Path("unused")
    del frame_path
    frame = pd.DataFrame(
        {
            "ts": list(range(200)),
            "open": [100.0] * 200,
            "high": [101.0] * 200,
            "low": [99.0] * 200,
            "close": [100.0] * 200,
            "volume": [1.0] * 200,
        }
    )

    def fake_run_one(window, symbol, engine, args):
        return [
            {
                "ts": int(ts),
                "symbol": symbol,
                "side": "buy",
                "ret": 0.001,
                "score": 1.0,
            }
            for ts in window["ts"]
        ]

    monkeypatch.setattr(replay, "run_one", fake_run_one)
    args = argparse.Namespace(
        validation_fraction=0.30,
        embargo_bars=24,
        time_stop_bars=12,
        min_bars=40,
    )

    selection, validation, protocol = replay.replay_chronological_holdout(
        frame, "BTC/USDT:USDT", object(), args
    )

    assert protocol["eligible"] is True
    assert protocol["selection_stop_index_exclusive"] == 116
    assert protocol["validation_start_index"] == 140
    assert max(trade["ts"] for trade in selection) == 115
    assert min(trade["ts"] for trade in validation) == 140


def test_grid_readiness_blocks_validation_reused_across_variants(monkeypatch):
    dataset = replay.OHLCVDataset(
        path="BTC-USDTUSDT_15m.csv",
        market_type="futures",
        symbol="BTC/USDT:USDT",
        base="BTC",
        classification="cache_key",
    )
    audit = {
        "eligible": True,
        "reasons": [],
        "market_type": "futures",
        "selection": {"eligible": True},
        "files": [],
    }
    monkeypatch.setattr(
        replay, "prepare_replay_datasets", lambda args: ([(dataset, object())], audit)
    )
    monkeypatch.setattr(replay, "_detector_params", lambda args: {})
    monkeypatch.setattr(replay, "MachineStrategyEngine", lambda config: object())

    def fake_holdout(frame, symbol, engine, args):
        trades = [
            {
                "ts": index,
                "symbol": symbol,
                "side": "buy",
                "ret": 0.01 if index % 2 else -0.005,
                "score": 0.8,
                "reasons": [],
                "detectors": {},
            }
            for index in range(1, 121)
        ]
        return trades[:60], trades[60:], {"eligible": True, "reason": None}

    monkeypatch.setattr(replay, "replay_chronological_holdout", fake_holdout)
    args = argparse.Namespace(
        min_score_grid="0.34,0.45",
        rr_grid="2.0",
        atr_stop_mult_grid="2.0",
        min_stop_pct=0.0035,
        max_stop_pct=0.025,
        time_stop_bars=12,
        confirmation_detectors="ema_rsi",
        unconfirmed_min_score=0.45,
        promotion_min_trades=30,
        promotion_min_profit_factor=1.2,
        exact=False,
        market_type="futures",
    )

    report = replay.run_grid(args)

    assert report["evaluation_protocol"]["multi_variant_readiness_blocked"] is True
    for result in report["results"].values():
        assert result["headline_sample"] == "untouched_validation"
        assert result["readiness"]["evidence_eligible"] is False
        assert any(
            "inspected by multiple parameter variants" in reason
            for reason in result["readiness"]["reasons"]
        )
