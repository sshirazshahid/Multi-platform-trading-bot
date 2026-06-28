from __future__ import annotations

import numpy as np
import pandas as pd


def _df_from_close(closes: list[float], *, start_ts: int = 1_700_000_000) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    return pd.DataFrame({
        "ts": start_ts + np.arange(len(close)) * 60,
        "open": close,
        "high": close + 0.5,
        "low": close - 0.5,
        "close": close,
        "volume": np.full(len(close), 1_000.0),
    })


def test_valuation_model_flags_material_undervaluation():
    from core.signals.valuation_model import signal

    df = _df_from_close([100.0] * 80 + [88.0, 87.0, 86.0, 85.0, 84.0])
    sig = signal(df, lookback=30, ema_span=20, z_entry=0.8, hold_bars=1)
    assert sig.iloc[-1] == 1


def test_ict_variation_detects_liquidity_sweep_reclaim():
    from core.signals.ict_variation import signal

    df = _df_from_close([100.0] * 20)
    df.loc[len(df) - 1, ["open", "high", "low", "close"]] = [96.0, 102.0, 94.0, 101.0]
    sig = signal(df, liquidity_lookback=8, require_imbalance=False, hold_bars=1)
    assert sig.iloc[-1] == 1


def test_stochastic_crossover_emits_bullish_recovery():
    from core.signals.stochastic_crossover import signal

    df = _df_from_close([100, 99, 98, 97, 96, 95, 94, 93, 94, 95, 96, 97])
    sig = signal(
        df,
        k_period=5,
        d_period=2,
        smooth_k=1,
        oversold_recover=100,
        hold_bars=1,
    )
    assert (sig == 1).any()


def test_harmonic_detector_finds_bullish_ratio_pattern():
    from core.signals.harmonic_patterns import detect_last_pattern, signal

    # Pivot sequence: L(100), H(120), L(108), H(115), L(104), then confirmation.
    df = _df_from_close([110, 100, 110, 120, 112, 108, 112, 115, 110, 104, 108, 109])
    pat = detect_last_pattern(df, left=1, right=1)
    assert pat is not None
    assert pat.direction == 1
    sig = signal(df, left=1, right=1, hold_bars=1)
    assert (sig == 1).any()


def test_harmonic_signal_matches_prefix_semantics():
    from core.signals.harmonic_patterns import detect_last_pattern, signal

    closes = [110, 100, 110, 120, 112, 108, 112, 115, 110, 104, 108, 109, 111, 107, 113]
    df = _df_from_close(closes)
    fast = signal(df, left=1, right=1, hold_bars=2)
    slow = pd.Series(0, index=df.index, dtype="int8")
    for i in range(len(df)):
        pat = detect_last_pattern(df.iloc[: i + 1], left=1, right=1)
        if pat is not None and pat.points[-1].confirmed_at == i:
            slow.iloc[i : min(len(df), i + 2)] = pat.direction

    assert fast.tolist() == slow.tolist()


def test_machine_strategy_engine_produces_action_shape_on_mechanical_signal():
    from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine

    df = _df_from_close([100.0] * 50)
    df.loc[len(df) - 1, ["open", "high", "low", "close"]] = [96.0, 102.0, 94.0, 101.0]
    eng = MachineStrategyEngine(MachineStrategyConfig(min_score=0.10))
    decision = eng.score_frame("BTC/USDT:USDT", df, market_type="futures")

    assert decision.action == "OPEN"
    assert decision.side == "buy"
    assert decision.stop_loss_pct > 0
    assert decision.take_profit_pct > decision.stop_loss_pct
    action = decision.as_action_dict()
    assert action["type"] == "OPEN"
    assert action["source"] == "machine_strategy_engine"


def test_machine_precomputed_detector_path_matches_score_frame():
    from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine

    df = _df_from_close([100.0] * 50)
    df.loc[len(df) - 1, ["open", "high", "low", "close"]] = [96.0, 102.0, 94.0, 101.0]
    eng = MachineStrategyEngine(MachineStrategyConfig(min_score=0.10))

    direct = eng.score_frame("BTC/USDT:USDT", df, market_type="futures")
    signals = eng.detector_frame(df)
    atr_pct = eng.atr_pct_series(df)
    fast = eng.decision_from_detector_values(
        "BTC/USDT:USDT",
        {name: int(signals[name].iloc[-1]) for name in signals.columns},
        atr_pct=float(atr_pct.iloc[-1]),
        market_type="futures",
    )

    assert fast.action == direct.action
    assert fast.side == direct.side
    assert fast.score == direct.score
    assert fast.diagnostics["detectors"] == direct.diagnostics["detectors"]


def test_machine_strategy_engine_includes_ema_rsi_detector_frame():
    from core.machine_strategy_engine import MachineStrategyEngine

    df = _df_from_close(list(np.linspace(100.0, 120.0, 70)) + [119.0, 118.0, 117.0, 118.0, 119.5, 121.0])
    eng = MachineStrategyEngine()
    signals = eng.detector_frame(df)

    assert "ema_rsi" in signals.columns
    assert signals["ema_rsi"].iloc[-1] == 1


def test_machine_strategy_engine_can_score_ema_rsi_as_confirming_vote():
    from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine

    df = _df_from_close(list(np.linspace(100.0, 120.0, 70)) + [119.0, 118.0, 117.0, 118.0, 119.5, 121.0])
    weights = {
        "valuation": 0.0,
        "harmonic": 0.0,
        "stochastic": 0.0,
        "ict": 0.0,
        "asian_range": 0.0,
        "dow_swing": 0.0,
        "bb_squeeze": 0.0,
        "ema_rsi": 1.0,
    }
    eng = MachineStrategyEngine(MachineStrategyConfig(min_score=0.50, weights=weights))

    decision = eng.score_frame("BTC/USDT:USDT", df, market_type="futures")

    assert decision.action == "OPEN"
    assert decision.side == "buy"
    assert decision.score == 1.0
    assert "ema_rsi=bull" in decision.reasons


def test_machine_strategy_engine_confirmation_gate_blocks_weak_unconfirmed_votes():
    from core.machine_strategy_engine import MachineStrategyConfig, MachineStrategyEngine

    weights = {
        "valuation": 0.0,
        "harmonic": 0.0,
        "stochastic": 0.0,
        "ict": 0.28,
        "asian_range": 0.0,
        "dow_swing": 0.0,
        "bb_squeeze": 0.0,
        "ema_rsi": 0.16,
    }
    eng = MachineStrategyEngine(
        MachineStrategyConfig(
            min_score=0.25,
            unconfirmed_min_score=0.40,
            confirmation_detectors=("ema_rsi",),
            weights=weights,
        )
    )

    unconfirmed = eng.decision_from_detector_values(
        "BTC/USDT:USDT",
        {"ict": 1, "ema_rsi": 0},
        atr_pct=0.01,
        market_type="futures",
    )
    confirmed = eng.decision_from_detector_values(
        "BTC/USDT:USDT",
        {"ict": 1, "ema_rsi": 1},
        atr_pct=0.01,
        market_type="futures",
    )

    assert unconfirmed.action == "HOLD"
    assert unconfirmed.diagnostics["long_required_score"] == 0.40
    assert confirmed.action == "OPEN"
    assert confirmed.side == "buy"
    assert confirmed.diagnostics["required_score"] == 0.25
    assert confirmed.diagnostics["confirmed_by_detector"] is True


def test_rank_tradeable_universe_prefers_liquid_movers_not_illiquid_spikes():
    from core.machine_strategy_engine import rank_tradeable_universe

    tickers = {
        "MICRO/USDT:USDT": {"quoteVolume": 100_000, "percentage": 80.0, "spread_bps": 4},
        "BTC/USDT:USDT": {"quoteVolume": 500_000_000, "percentage": 1.2, "spread_bps": 1},
        "SOL/USDT:USDT": {"quoteVolume": 80_000_000, "percentage": 8.0, "spread_bps": 3},
        "WIDE/USDT:USDT": {"quoteVolume": 200_000_000, "percentage": 6.0, "spread_bps": 40},
    }
    ranked = rank_tradeable_universe(tickers, max_symbols=3)
    assert ranked == ["BTC/USDT:USDT", "SOL/USDT:USDT"]
