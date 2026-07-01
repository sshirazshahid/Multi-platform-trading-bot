"""Causal trading-signal generators.

Each module exposes ``def signal(df, **params) -> pandas.Series`` that obeys the
project signal-interface contract:

* ``df`` has columns ``['ts','open','high','low','close','volume']`` with
  ``ts`` in unix epoch SECONDS, ascending, integer index.
* The returned Series is the position to HOLD during the NEXT bar, values in
  ``{-1, 0, +1}``. Some named breakout strategies are long-biased; newer
  machine detectors can be side-aware.
* STRICTLY CAUSAL: the value at row ``i`` is a function of rows ``0..i`` only.
"""

from core.signals.ema_rsi import signal as ema_rsi_signal
from core.signals.harmonic_patterns import signal as harmonic_pattern_signal
from core.signals.ict_variation import signal as ict_variation_signal
from core.signals.session_breakout import signal as session_breakout_signal
from core.signals.squeeze_breakout import signal as squeeze_breakout_signal
from core.signals.stochastic_crossover import signal as stochastic_crossover_signal
from core.signals.swing_structure import signal as swing_structure_signal
from core.signals.valuation_model import signal as valuation_model_signal

__all__ = [
    "harmonic_pattern_signal",
    "ict_variation_signal",
    "session_breakout_signal",
    "squeeze_breakout_signal",
    "stochastic_crossover_signal",
    "swing_structure_signal",
    "valuation_model_signal",
    "ema_rsi_signal",
]
