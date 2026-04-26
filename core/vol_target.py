"""Volatility-targeting position sizer (Phase 4.2).

Pure-function library that maps a single-asset 1-bar volatility forecast
(e.g. from a GARCH(1,1) or realized-vol estimator — see Phase 2.3) to a
notional USD position size such that, in expectation, the position
contributes a fixed annualized volatility to the portfolio.

Math
----
    annual_vol = vol_forecast * sqrt(bars_per_year)
    notional   = balance * (target_annual_vol / annual_vol)

Then clamp `notional` to `[notional_floor_pct, notional_ceiling_pct] * balance`
to avoid micro-positions on quiet assets and runaway leverage on near-zero
forecasts.

This is a pure-function library only — caller wiring (replacing the
fixed `15% × 3x` rule in `risk_manager.calculate_position_size`) is a
sibling task.

Reference: compiled-pondering-key.md Phase 4.2.
"""
from __future__ import annotations

import math

try:
    from loguru import logger
except Exception:  # pragma: no cover - loguru optional
    import logging

    logger = logging.getLogger(__name__)


# Defaults --------------------------------------------------------------
DEFAULT_BARS_PER_YEAR = 8760.0          # hourly bars: 24 * 365
DEFAULT_TARGET_ANNUAL_VOL = 0.10        # 10% annualized portfolio vol
DEFAULT_NOTIONAL_FLOOR_PCT = 0.05       # min 5% of balance
DEFAULT_NOTIONAL_CEILING_PCT = 2.00     # max 200% of balance (with leverage)


class VolTarget:
    """Stateless volatility-targeting sizer.

    Use the `size()` static method directly — no instance state needed.
    """

    @staticmethod
    def size(
        balance_usd: float,
        vol_forecast: float,
        bars_per_year: float = DEFAULT_BARS_PER_YEAR,
        target_annual_vol: float = DEFAULT_TARGET_ANNUAL_VOL,
        notional_floor_pct: float = DEFAULT_NOTIONAL_FLOOR_PCT,
        notional_ceiling_pct: float = DEFAULT_NOTIONAL_CEILING_PCT,
    ) -> float:
        """Return notional USD to allocate to a single position.

        Parameters
        ----------
        balance_usd
            Account balance / equity in USD.
        vol_forecast
            1-bar volatility forecast as a decimal (e.g. 0.012 = 1.2% per
            bar). Typically the output of a GARCH or realized-vol estimator
            on the same bar timeframe as `bars_per_year`.
        bars_per_year
            Number of bars per year for sqrt-time annualization.
            Defaults to 8760 (hourly bars: 24 * 365). For 4h bars use 2190.
            For daily bars use 365.
        target_annual_vol
            Desired annualized portfolio volatility contribution from this
            single position, as a decimal. Default 0.10 (10%/yr).
        notional_floor_pct
            Minimum notional as a fraction of balance. Default 0.05 (5%).
        notional_ceiling_pct
            Maximum notional as a fraction of balance. Default 2.00 (200%,
            assumes leverage is permitted at the venue level).

        Returns
        -------
        Notional USD, clamped to
        ``[notional_floor_pct, notional_ceiling_pct] * balance_usd``.

        Notes
        -----
        Degenerate input (``vol_forecast <= 0``, NaN, non-finite balance)
        returns the floor and logs a warning — the function never raises
        on bad input so the caller does not need defensive wrapping.
        """
        # Coerce / validate ------------------------------------------------
        try:
            balance_usd = float(balance_usd)
        except (TypeError, ValueError):
            balance_usd = 0.0
        if not math.isfinite(balance_usd) or balance_usd <= 0:
            return 0.0

        floor_notional = balance_usd * notional_floor_pct
        ceiling_notional = balance_usd * notional_ceiling_pct

        try:
            vf = float(vol_forecast)
        except (TypeError, ValueError):
            vf = 0.0

        if not math.isfinite(vf) or vf <= 0:
            logger.warning(
                "VolTarget: degenerate vol_forecast={!r} — returning floor "
                "({:.4f} = {:.1%} of balance)",
                vol_forecast, floor_notional, notional_floor_pct,
            )
            return floor_notional

        if not math.isfinite(bars_per_year) or bars_per_year <= 0:
            raise ValueError(f"bars_per_year must be > 0, got {bars_per_year}")
        if not math.isfinite(target_annual_vol) or target_annual_vol <= 0:
            raise ValueError(
                f"target_annual_vol must be > 0, got {target_annual_vol}"
            )

        # Core sizing ------------------------------------------------------
        annual_vol = vf * math.sqrt(bars_per_year)
        notional = balance_usd * (target_annual_vol / annual_vol)

        # Clamp -----------------------------------------------------------
        if notional < floor_notional:
            return floor_notional
        if notional > ceiling_notional:
            return ceiling_notional
        return notional
