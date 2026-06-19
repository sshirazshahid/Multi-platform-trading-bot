"""Pure-python reference implementation of the LIVE discrete TSMOM rule.

Faithful re-derivation of ``core/tsmom_signal.py`` (Strategy A), used ONLY as the
parity oracle in ``nt/tests/test_tsmom_port_parity.py``. The NautilusTrader port
(``nt/strategies/tsmom_strategy.py``) must produce the SAME OPEN/CLOSE decision
bars as ``discrete_events`` on the same close series — bar-for-bar.

Constants copied verbatim from core/tsmom_signal.py so the two cannot drift.
"""
from __future__ import annotations

import math

_ANN = 365.0
_TARGET_ANN_VOL = 0.60
_VOL_WINDOW = 20
_BASE_SIZE_PCT = 5.0


def min_bars(lookback: int) -> int:
    """Bars of history required before the signal evaluates at all.

    Mirrors ``TSMOMSignal._min_bars = lookback + _VOL_WINDOW + 2``.
    """
    return int(lookback) + _VOL_WINDOW + 2


def vol_scaled_size_pct(closes_upto: list[float]) -> float:
    """Verbatim port of ``TSMOMSignal._vol_scaled_size_pct``.

    ``closes_upto`` is the close series up to and INCLUDING the current bar.
    """
    window = closes_upto[-(_VOL_WINDOW + 1):]
    rets = [window[i] / window[i - 1] - 1.0 for i in range(1, len(window))]
    if len(rets) < 2:
        return round(_BASE_SIZE_PCT * 0.5, 2)
    mean = sum(rets) / len(rets)
    var = sum((r - mean) ** 2 for r in rets) / (len(rets) - 1)
    realized_ann = math.sqrt(var) * math.sqrt(_ANN)
    if realized_ann <= 0:
        weight = 1.0
    else:
        weight = min(1.0, _TARGET_ANN_VOL / realized_ann)
    return round(max(1.0, _BASE_SIZE_PCT * weight), 2)


def discrete_events(closes: list[float], lookback: int) -> list[tuple[int, str]]:
    """Return the ordered list of (bar_index, "OPEN"|"CLOSE") decisions.

    Replicates ``TSMOMSignal.analyze_portfolio`` called once per daily bar with
    the open-position state carried forward:
      * evaluate only once >= min_bars closes are available,
      * mom = close[i] / close[i - lookback] - 1,
      * mom > 0 and flat   -> OPEN,
      * mom <= 0 and long  -> CLOSE,
      * else hold/flat (no event).
    Long-only; never opens twice without an intervening close.
    """
    lb = int(lookback)
    need = min_bars(lb)
    in_pos = False
    events: list[tuple[int, str]] = []
    for i in range(len(closes)):
        if (i + 1) < need:            # closes[0..i] -> count = i+1
            continue
        mom = closes[i] / closes[i - lb] - 1.0
        mom_positive = mom > 0
        if mom_positive and not in_pos:
            events.append((i, "OPEN"))
            in_pos = True
        elif (not mom_positive) and in_pos:
            events.append((i, "CLOSE"))
            in_pos = False
    return events
