from __future__ import annotations

import numpy as np
import pandas as pd


def _df_from_close(closes: list[float]) -> pd.DataFrame:
    close = np.asarray(closes, dtype=float)
    open_ = np.r_[close[0], close[:-1]]
    return pd.DataFrame(
        {
            "ts": 1_700_000_000 + np.arange(len(close)) * 60,
            "open": open_,
            "high": np.maximum(open_, close) + 0.4,
            "low": np.minimum(open_, close) - 0.4,
            "close": close,
            "volume": np.full(len(close), 1_000.0),
        }
    )


def test_ema_rsi_detects_bullish_trend_pullback_reclaim():
    from core.signals.ema_rsi import signal

    closes = list(np.linspace(100.0, 120.0, 70)) + [119.0, 118.0, 117.0, 118.0, 119.5, 121.0]
    sig = signal(_df_from_close(closes), hold_bars=2)

    assert sig.iloc[-2:].tolist() == [1, 1]


def test_ema_rsi_detects_bearish_trend_pullback_rejection():
    from core.signals.ema_rsi import signal

    closes = list(np.linspace(120.0, 100.0, 70)) + [101.0, 102.0, 103.0, 102.0, 100.5, 99.0]
    sig = signal(_df_from_close(closes), hold_bars=2)

    assert sig.iloc[-2:].tolist() == [-1, -1]


def test_ema_rsi_stays_flat_on_noisy_flat_market():
    from core.signals.ema_rsi import signal

    noise = np.sin(np.linspace(0.0, 20.0, 120)) * 0.15
    sig = signal(_df_from_close((100.0 + noise).tolist()))

    assert int(sig.abs().sum()) == 0
