"""Thin pytest shim for research/screen_wrapper_discount.py (15d screen).

Runs the module's embedded selftest (7 groups: cost floor arithmetic, funding
credit sign, OLS drift recovery, trailing-median drift correction, discount
percentiles, episode detection incl. NaN breaks, graceful no-discount input)
under pytest so CI exercises the frozen descriptive-screen math.
"""

import research.screen_wrapper_discount as m


def test_selftest_passes():
    m.selftest()
