"""Market-wide BTC volatility pause for NEW entries (2026-06-04 owner directive).

Pauses NEW entries (any side) while BTC 1h ATR% spikes >= vol_spike_mult x its own
trailing-median, auto-resuming after clear_minutes of calm. Adaptive (vs BTC's own
median, not a fixed % tuned to this week), direction-agnostic (no beta bias),
fail-OPEN on missing data/warmup, NEW ENTRIES ONLY. See core/btc_vol_pause.py.
"""
from __future__ import annotations

from tests.bot_engine_source import bot_engine_source_for_grep

from pathlib import Path

import pytest

import core.btc_vol_pause as bvp_mod
from core.btc_vol_pause import BtcVolPause, extract_btc_atr_pct

TEST_CFG = {
    "enabled": True, "timeframe": "1h",
    "vol_spike_mult": 2.0, "hysteresis_mult": 1.5, "clear_minutes": 30,
    "min_samples": 5, "append_min_interval_sec": 3600, "buffer_max": 1000,
}


@pytest.fixture
def gate(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(bvp_mod, "_cfg", lambda: dict(TEST_CFG))
    return BtcVolPause()


def _cache(atr):
    return {"BTC": {"1h": {"atr_pct": atr}}}


def _warm(gate, atr=1.0, n=5, t0=0.0):
    """Fill the trailing baseline with `n` calm readings spaced hourly."""
    t = t0
    for _ in range(n):
        gate.update_and_evaluate(_cache(atr), now=t)
        t += 3600
    return t


def test_extract_btc_atr_pct():
    assert extract_btc_atr_pct(_cache(2.4)) == 2.4
    assert extract_btc_atr_pct({"BTC": {"1h": {}}}) is None
    assert extract_btc_atr_pct({}) is None
    assert extract_btc_atr_pct(None) is None
    assert extract_btc_atr_pct(_cache(0)) is None  # non-positive rejected


def test_warmup_fails_open(gate):
    # below min_samples -> never pause, even on a huge reading
    paused, reason, _ = gate.update_and_evaluate(_cache(99.0), now=0.0)
    assert paused is False
    assert "warmup" in reason


def test_calm_allows(gate):
    t = _warm(gate, atr=1.0, n=5)
    paused, reason, info = gate.update_and_evaluate(_cache(1.1), now=t + 60)
    assert paused is False
    assert reason == "calm"
    assert info["median"] == pytest.approx(1.0, abs=0.2)


def test_spike_pauses(gate):
    t = _warm(gate, atr=1.0, n=5)
    # 2.5% >= 2.0x median(1.0) => pause
    paused, reason, info = gate.update_and_evaluate(_cache(2.5), now=t + 60)
    assert paused is True
    assert "spike" in reason
    assert info["spike"] == pytest.approx(2.0, abs=0.2)


def test_hysteresis_cooldown_holds_after_calm(gate):
    t = _warm(gate, atr=1.0, n=5)
    spike_t = t + 60
    assert gate.update_and_evaluate(_cache(2.5), now=spike_t)[0] is True  # pause set, until=+30m
    # 5 min later vol is calm again, but we must WAIT out the cooldown
    paused, reason, _ = gate.update_and_evaluate(_cache(1.0), now=spike_t + 300)
    assert paused is True
    assert "cooldown" in reason
    # after the 30-min window with calm vol -> resume
    paused2, reason2, _ = gate.update_and_evaluate(_cache(1.0), now=spike_t + 1801)
    assert paused2 is False
    assert reason2 == "calm"


def test_elevated_extends_the_wait(gate):
    t = _warm(gate, atr=1.0, n=5)
    spike_t = t + 60
    gate.update_and_evaluate(_cache(2.5), now=spike_t)            # pause until +30m
    # still elevated (1.6 is > clear=1.5x1.0 but < spike=2.0) just before window ends
    paused, reason, _ = gate.update_and_evaluate(_cache(1.6), now=spike_t + 1700)
    assert paused is True
    assert "elevated" in reason
    # the wait was extended past the original window
    assert gate.update_and_evaluate(_cache(1.0), now=spike_t + 1900)[0] is True


def test_missing_btc_fails_open(gate):
    _warm(gate, atr=1.0, n=5)
    assert gate.update_and_evaluate({"ETH": {"1h": {"atr_pct": 9.0}}}, now=99999)[0] is False
    assert gate.update_and_evaluate(None, now=99999)[0] is False


def test_disabled_never_pauses(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(bvp_mod, "_cfg", lambda: {"enabled": False})
    g = BtcVolPause()
    assert g.update_and_evaluate(_cache(99.0), now=0.0) == (False, "disabled", {})


def test_state_persists_across_instances(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir(exist_ok=True)
    monkeypatch.setattr(bvp_mod, "_cfg", lambda: dict(TEST_CFG))
    g1 = BtcVolPause()
    t = _warm(g1, atr=1.0, n=5)
    g1.update_and_evaluate(_cache(2.5), now=t + 60)   # sets pause_until, persists
    g2 = BtcVolPause()                                 # fresh instance reloads state
    paused, reason, _ = g2.update_and_evaluate(_cache(1.0), now=t + 120)
    assert paused is True and "cooldown" in reason


# ── 2026-08-17: current_ratio must use a 30-DAY baseline ────────────────────
# BUG (80h of zero trades): config/gates.py:121 defines the band-regime veto as
# "BTC 1h ATR / 30d median < 0.7" — the pre-registered screen-13 semantics. But
# current_ratio took the median of the WHOLE buffer, and buffer_max=1000 hourly
# samples is ~42 days; the live buffer had grown to 675 samples spanning 1,384h
# (58 DAYS). Measured on the live state file:
#     median over all 675 samples (58d) = 0.4300  -> ratio 0.628  BLOCKS
#     median over the intended 30d      = 0.3700  -> ratio 0.730  PASSES
# A longer window in a decaying-vol regime carries higher old readings, inflating
# the median and depressing the ratio — so the veto fired on a window the screen
# never authorised. A spec/implementation mismatch, not a market state.

def test_current_ratio_uses_only_the_last_30_days(gate):
    """Old samples beyond the 30d window must not inflate the baseline."""
    now = 1_800_000_000
    # 20 stale-but-high readings from 41-60 days ago + 20 recent low ones.
    gate._buf = ([[now - d * 86400, 1.00] for d in range(60, 40, -1)]
                 + [[now - d * 86400, 0.40] for d in range(20, 0, -1)])
    r = gate.current_ratio(_cache(0.40), now=now)
    assert r == pytest.approx(1.0), (
        f"baseline must be the 30d median (0.40), got ratio {r} — "
        "stale >30d samples are leaking into the median"
    )


def test_current_ratio_ignores_samples_older_than_the_window(gate):
    """A buffer that is ENTIRELY stale must fail OPEN, not compute a bogus ratio."""
    now = 1_800_000_000
    gate._buf = [[now - d * 86400, 0.90] for d in range(90, 60, -1)]
    assert gate.current_ratio(_cache(0.40), now=now) is None, (
        "no in-window samples must return None (fail open), never use stale data"
    )


def test_current_ratio_still_computes_on_a_healthy_recent_buffer(gate):
    now = 1_800_000_000
    gate._buf = [[now - h * 3600, 0.50] for h in range(200, 0, -1)]
    assert gate.current_ratio(_cache(0.25), now=now) == pytest.approx(0.5)


def test_wired_into_execute_open_new_entries_only():
    """Source pin: the gate lives in _execute_open (new-entry path), is config-gated,
    fail-OPEN, and blocks via `return False` — never touches exits/position mgmt."""
    src = bot_engine_source_for_grep()
    assert "from core.btc_vol_pause import BtcVolPause" in src
    i = src.index("def _execute_open")
    j = src.find("\n    def ", i + 1)
    body = src[i:j if j != -1 else len(src)]
    assert "BtcVolPause" in body, "BTC vol pause must be gated inside _execute_open"
    assert "update_and_evaluate" in body
    assert "defaulting to ALLOW" in body  # fail-open idiom
