"""Causal, long-biased trading-signal generators.

Each module exposes ``def signal(df, **params) -> pandas.Series`` that obeys the
project signal-interface contract:

* ``df`` has columns ``['ts','open','high','low','close','volume']`` with
  ``ts`` in unix epoch SECONDS, ascending, integer index.
* The returned Series is the position to HOLD during the NEXT bar, values in
  ``{-1, 0, +1}`` (these strategies are long-biased -> ``{0, +1}``).
* STRICTLY CAUSAL: the value at row ``i`` is a function of rows ``0..i`` only.
"""

from core.signals.session_breakout import signal as session_breakout_signal
from core.signals.squeeze_breakout import signal as squeeze_breakout_signal
from core.signals.swing_structure import signal as swing_structure_signal

__all__ = [
    "session_breakout_signal",
    "squeeze_breakout_signal",
    "swing_structure_signal",
]
