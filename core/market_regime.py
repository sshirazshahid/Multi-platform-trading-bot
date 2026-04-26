"""
core/market_regime.py — Advanced Market Regime Detection

Detects the current market regime using multiple methods:
  1. ADX — trend strength (>25 trending, <20 ranging)
  2. Hurst Exponent — persistence (>0.55 trending, <0.45 mean-reverting)
  3. Volatility Regime — ATR percentile (low/normal/high/extreme)
  4. EMA slope — trend direction

Returns which strategies are appropriate for each regime.
The bot engine uses this to skip strategies that don't fit.
"""

import time as _time
import numpy as np
import pandas as pd
from loguru import logger


# ── Regime labels ────────────────────────────────────────────────────

REGIME_TRENDING_UP   = "trending_up"
REGIME_TRENDING_DOWN = "trending_down"
REGIME_RANGING       = "ranging"
REGIME_VOLATILE      = "volatile"
REGIME_UNKNOWN       = "unknown"

# Sub-labels for volatility
VOL_LOW    = "vol_low"
VOL_NORMAL = "vol_normal"
VOL_HIGH   = "vol_high"
VOL_EXTREME = "vol_extreme"

# Which strategies run in each regime
REGIME_STRATEGY_MAP = {
    REGIME_TRENDING_UP: [
        "supertrend_spot", "supertrend_futures",
        "multitf_futures", "trend_spot", "trend_futures",
        "funding_arb_futures",      # funding arb works in all regimes
        "dca_spot",                 # DCA buys regardless of regime
    ],
    REGIME_TRENDING_DOWN: [
        "supertrend_futures",       # shorts only in downtrend
        "multitf_futures",
        "trend_futures",
        "funding_arb_futures",
        "dca_spot",                 # DCA buys regardless of regime
    ],
    REGIME_RANGING: [
        "mean_reversion_spot",
        "grid_spot",
        "dca_spot",
        "scalping_spot",
        "funding_arb_futures",
    ],
    REGIME_VOLATILE: [
        "funding_arb_futures",      # funding arb thrives in volatile markets
        "dca_spot",                 # DCA buys regardless of regime
    ],                              # (extreme funding = big carry)
    REGIME_UNKNOWN: None,           # None = use all (no filter)
}


# ── Indicator helpers ────────────────────────────────────────────────

def _ema(s: pd.Series, p: int) -> pd.Series:
    return s.ewm(span=p, adjust=False).mean()


def _atr(high, low, close, p=14) -> pd.Series:
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(com=p-1, adjust=False).mean()


def _adx(high, low, close, p=14) -> pd.Series:
    tr = _atr(high, low, close, p)
    up = high.diff()
    down = -low.diff()
    pdm = up.where((up > down) & (up > 0), 0.0)
    mdm = down.where((down > up) & (down > 0), 0.0)
    pdi = 100 * pdm.ewm(com=p-1, adjust=False).mean() / tr.replace(0, np.nan)
    mdi = 100 * mdm.ewm(com=p-1, adjust=False).mean() / tr.replace(0, np.nan)
    dx = (100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan))
    return dx.ewm(com=p-1, adjust=False).mean()


def _hurst_exponent(series: pd.Series, max_lag: int = 20) -> float:
    """
    Estimate Hurst exponent using Rescaled Range (R/S) analysis on RETURNS.

    H > 0.55 → persistent (trending) — use trend strategies
    H < 0.45 → anti-persistent (mean-reverting) — use MR strategies
    H ~ 0.50 → random walk — no edge

    We compute on log-returns (not raw prices) since R/S analysis
    requires a stationary series.
    """
    prices = series.dropna().values
    if len(prices) < max_lag * 4:
        return 0.5  # not enough data

    # Convert to log-returns for stationarity
    ts = np.diff(np.log(prices[prices > 0]))
    if len(ts) < max_lag * 3:
        return 0.5

    lags = range(2, max_lag + 1)
    rs_values = []

    for lag in lags:
        rs_lag = []
        for start in range(0, len(ts) - lag, lag):
            chunk = ts[start:start + lag]
            mean_chunk = np.mean(chunk)
            deviations = chunk - mean_chunk
            cumulative = np.cumsum(deviations)
            r = np.max(cumulative) - np.min(cumulative)
            s = np.std(chunk, ddof=1)
            if s > 1e-12:
                rs_lag.append(r / s)
        if rs_lag:
            rs_values.append((np.log(lag), np.log(np.mean(rs_lag))))

    if len(rs_values) < 3:
        return 0.5

    x = np.array([v[0] for v in rs_values])
    y = np.array([v[1] for v in rs_values])

    # Linear regression: H = slope
    n = len(x)
    denom = n * np.sum(x**2) - np.sum(x)**2
    if abs(denom) < 1e-12:
        return 0.5
    slope = (n * np.sum(x * y) - np.sum(x) * np.sum(y)) / denom

    return float(np.clip(slope, 0.0, 1.0))


def _volatility_regime(atr_series: pd.Series, close_series: pd.Series) -> str:
    """
    Classify current volatility into low/normal/high/extreme
    based on ATR percentile over the lookback window.
    """
    atr_pct = atr_series / close_series
    atr_pct = atr_pct.dropna()
    if len(atr_pct) < 20:
        return VOL_NORMAL

    current = float(atr_pct.iloc[-1])
    p25 = float(atr_pct.quantile(0.25))
    p75 = float(atr_pct.quantile(0.75))
    p95 = float(atr_pct.quantile(0.95))

    if current >= p95 or current > 0.08:
        return VOL_EXTREME
    elif current >= p75:
        return VOL_HIGH
    elif current <= p25:
        return VOL_LOW
    return VOL_NORMAL


# ── Main detector ────────────────────────────────────────────────────

class MarketRegimeDetector:

    def __init__(self):
        self._cache: dict = {}
        self._cache_ttl = 900  # 15 min

    def detect(self, exchange, symbol: str,
               timeframe: str = "1h", lookback: int = 200) -> str:
        """
        Detect market regime using ADX + Hurst + volatility.
        Returns one of the REGIME_* constants.
        """
        cached = self._cache.get(symbol)
        if cached and (_time.time() - cached[1]) < self._cache_ttl:
            return cached[0]

        try:
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit=lookback)
            if not raw or len(raw) < 60:
                return REGIME_UNKNOWN

            df = pd.DataFrame(
                raw, columns=["ts", "open", "high", "low", "close", "volume"]
            )

            # Compute indicators
            adx_series = _adx(df["high"], df["low"], df["close"], 14)
            ema50 = _ema(df["close"], 50)
            atr_series = _atr(df["high"], df["low"], df["close"], 14)
            hurst = _hurst_exponent(df["close"], max_lag=20)
            vol_regime = _volatility_regime(atr_series, df["close"])

            df.dropna(inplace=True)
            if df.empty:
                return REGIME_UNKNOWN

            adx_val = float(adx_series.iloc[-1])
            price = float(df["close"].iloc[-1])
            ema_val = float(ema50.iloc[-1])
            atr_pct = float(atr_series.iloc[-1]) / price

            # Decision logic:
            # 1. Extreme volatility → pause
            if vol_regime == VOL_EXTREME:
                regime = REGIME_VOLATILE

            # 2. Strong trend (ADX > 25 AND Hurst > 0.50)
            elif adx_val >= 25 and hurst > 0.50:
                regime = REGIME_TRENDING_UP if price > ema_val else REGIME_TRENDING_DOWN

            # 3. ADX trending but Hurst says mean-reverting → conflicting, be cautious
            elif adx_val >= 25 and hurst < 0.45:
                # ADX says trending but Hurst says MR — likely a choppy breakout
                # Default to ranging (safer)
                regime = REGIME_RANGING

            # 4. ADX low + Hurst mean-reverting → strong ranging signal
            elif adx_val < 20 and hurst < 0.50:
                regime = REGIME_RANGING

            # 5. ADX low but Hurst trending → early trend forming
            elif adx_val < 20 and hurst > 0.55:
                regime = REGIME_TRENDING_UP if price > ema_val else REGIME_TRENDING_DOWN

            # 6. Middle ground
            elif adx_val < 20:
                regime = REGIME_RANGING
            else:
                regime = REGIME_UNKNOWN

            self._cache[symbol] = (regime, _time.time())
            logger.debug(
                f"[Regime] {symbol}: {regime} "
                f"(ADX={adx_val:.1f} Hurst={hurst:.2f} "
                f"Vol={vol_regime} ATR%={atr_pct*100:.2f}%)"
            )
            return regime

        except Exception as e:
            logger.debug(f"[Regime] Detection failed for {symbol}: {e}")
            return REGIME_UNKNOWN

    def detect_detailed(self, exchange, symbol: str,
                        timeframe: str = "1h", lookback: int = 200) -> dict:
        """
        Return full regime analysis dict (for dashboard/logging).
        Calls detect() first (populates cache), then computes extra indicators
        from a single OHLCV fetch — avoids double API call.
        """
        try:
            # detect() fetches OHLCV and caches the regime
            regime = self.detect(exchange, symbol, timeframe, lookback)

            # Fetch OHLCV once for the detailed indicators
            raw = exchange.fetch_ohlcv(symbol, timeframe, limit=lookback)
            if not raw or len(raw) < 60:
                return {"regime": regime}

            df = pd.DataFrame(
                raw, columns=["ts", "open", "high", "low", "close", "volume"]
            )

            adx_series = _adx(df["high"], df["low"], df["close"], 14)
            atr_series = _atr(df["high"], df["low"], df["close"], 14)
            hurst = _hurst_exponent(df["close"], max_lag=20)
            vol_regime = _volatility_regime(atr_series, df["close"])

            adx_val = float(adx_series.dropna().iloc[-1]) if not adx_series.dropna().empty else 0
            atr_pct = float(atr_series.iloc[-1] / df["close"].iloc[-1]) if len(df) > 0 else 0

            return {
                "regime": regime,
                "adx": round(adx_val, 1),
                "hurst": round(hurst, 3),
                "volatility": vol_regime,
                "atr_pct": round(atr_pct * 100, 2),
                "recommendation": self.allowed_strategies(regime),
            }
        except Exception as e:
            return {"regime": REGIME_UNKNOWN, "error": str(e)}

    def allowed_strategies(self, regime: str) -> list | None:
        """
        Returns the list of strategy keys that should run for this regime,
        or None if all configured strategies should run.
        """
        return REGIME_STRATEGY_MAP.get(regime)

    def should_run(self, strategy_key: str, regime: str) -> bool:
        """Returns True if a strategy should run in the given regime."""
        allowed = self.allowed_strategies(regime)
        if allowed is None:
            return True
        if not allowed:
            return False
        return strategy_key in allowed

    def clear_cache(self):
        """Clear the regime cache (useful after major events)."""
        self._cache.clear()
