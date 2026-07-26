"""Thin pytest shim for research/screen_stablecoin_depeg.py (15c screen).

Runs the module's embedded synthetic-bar selftest (7 cases: REPEG/STOP/TIMEOUT
exits, de-dup re-arm, fill-gap exclusion, unresolved-at-end, premium side)
under pytest so CI exercises the frozen event engine.
"""

import research.screen_stablecoin_depeg as m


def test_selftest_passes():
    m.selftest()
