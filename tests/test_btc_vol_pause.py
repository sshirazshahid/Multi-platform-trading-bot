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


def test_elevated_band_cannot_pin_the_pause_indefinitely(gate):
    """A cooldown must not outlive its trigger.

    Sustained elevated-but-not-spiking vol re-armed ``pause_until`` on EVERY
    evaluation, so a clear_minutes=30 cooldown became open-ended. Measured live
    2026-08-23: the bot had been paused 10.3h -- 20x its configured cooldown --
    with the last genuine spike 10.3h earlier and BTC 1h ATR at 0.68% = 1.89x
    its 30d median, i.e. BELOW the 2.0x bar the gate itself uses to define
    dangerous vol. A bot with empty state would have traded at that same ATR;
    only the pause history held it. The baseline here stays calm-dominated
    (30 calm vs 12 elevated appends) so the median cannot drift and mask this.
    """
    t = _warm(gate, atr=1.0, n=30)
    spike_t = t + 60
    assert gate.update_and_evaluate(_cache(2.5), now=spike_t)[0] is True

    # 12 hours of elevated-but-never-spiking vol, evaluated every 5 minutes.
    last = None
    for step in range(1, 145):
        last = gate.update_and_evaluate(_cache(1.6), now=spike_t + step * 300)

    paused, reason, info = last
    assert info["median"] == pytest.approx(1.0), "median drifted; test is invalid"
    assert paused is False, (
        f"still paused 12h after a 30m cooldown with no new spike: {reason}"
    )


def test_bounded_cooldown_still_honours_a_fresh_spike(gate):
    """Bounding the wait must not let a genuine spike through."""
    t = _warm(gate, atr=1.0, n=30)
    spike_t = t + 60
    gate.update_and_evaluate(_cache(2.5), now=spike_t)
    for step in range(1, 145):
        gate.update_and_evaluate(_cache(1.6), now=spike_t + step * 300)
    # released above; a NEW spike must pause again immediately
    paused, reason, _ = gate.update_and_evaluate(_cache(2.5), now=spike_t + 145 * 300)
    assert paused is True
    assert "spike" in reason


def test_spike_at_is_recovered_from_the_buffer_on_restart(gate, tmp_path):
    """A restart must not clear a legitimate pause, nor re-pin a stale one.

    State written before spike_at existed has no record of when the spike was.
    Guessing "no spike" would release on every restart; the buffer already
    holds the answer, so recover it from there.
    """
    import json

    t = _warm(gate, atr=1.0, n=30)
    spike_t = t + 60
    gate.update_and_evaluate(_cache(2.5), now=spike_t)          # real spike
    for step in range(1, 13):                                    # 1h elevated
        gate.update_and_evaluate(_cache(1.6), now=spike_t + step * 300)

    # Simulate legacy state: drop spike_at, keep buf + an active pause.
    raw = json.loads((tmp_path / "data" / "btc_vol_state.json").read_text())
    raw.pop("spike_at", None)
    (tmp_path / "data" / "btc_vol_state.json").write_text(json.dumps(raw))

    revived = BtcVolPause()
    assert revived._spike_at == 0.0, "fixture did not actually drop spike_at"

    # 1h after a genuine spike is INSIDE the 240m cap -> must stay paused.
    paused, reason, _ = revived.update_and_evaluate(
        _cache(1.6), now=spike_t + 13 * 300)
    assert paused is True, f"restart cleared a legitimate pause: {reason}"
    assert revived._spike_at == pytest.approx(spike_t), "spike time not recovered"


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


# ── 2026-08-20: the Aug-17 window bug, still live in the GATE itself ──────────

def test_spike_baseline_uses_the_30d_spec_window_not_the_whole_buffer(gate):
    """The pause gate must median the 30-DAY window, like current_ratio() does.

    2026-08-17 postmortem fixed `current_ratio()` (band-lane veto) to window the
    baseline at 30 days -- config/gates.py pre-registers "BTC 1h ATR / 30d median".
    `update_and_evaluate()` reads the SAME buffer and was never fixed:

        current_ratio      :190  samples = [a for (ts, a) in self._buf if ts >= cutoff]
        update_and_evaluate:129  samples = [a for (_, a) in self._buf]   <- no cutoff

    buffer_max=1000 hourly samples is ~42d, and the LIVE buffer on 2026-08-20 held
    700 samples spanning 60.8 days. In a decaying-vol regime the older readings sit
    HIGHER, so the un-windowed median is inflated and the spike threshold with it --
    the gate is more PERMISSIVE than the screen authorised. Measured on the live
    buffer that day: whole-buffer median 0.43% (threshold 0.86%) vs 30d-spec median
    0.36% (threshold 0.72%). ATR 0.92% tripped both, so the verdict was unchanged
    that day -- but any ATR in [0.72, 0.86) is a silent unauthorised entry.
    """
    now = 1_000_000_000.0
    day = 86400.0
    # 30 OLD readings at 1.00%, 40-69 days back: OUTSIDE the 30d spec window.
    gate._buf = [[now - (40 + i) * day, 1.00] for i in range(30)]
    # 30 RECENT readings at 0.30%, inside the window (0.5-day spacing).
    gate._buf += [[now - (i * 0.5) * day, 0.30] for i in range(1, 31)]
    gate._buf.sort(key=lambda row: row[0])

    # 30d median = 0.30 -> spec spike = 0.60. Whole-buffer median ~0.65 -> ~1.30.
    # ATR 0.70 sits BETWEEN them: spec says PAUSE, un-windowed says allow.
    paused, reason, info = gate.update_and_evaluate(_cache(0.70), now=now)

    assert paused is True, (
        f"30d-spec baseline must pause at ATR 0.70% (spike=0.60%); "
        f"got allow -- gate is medianing the whole buffer. reason={reason} info={info}"
    )
    assert info.get("median") == pytest.approx(0.30, abs=0.02), (
        f"baseline median must come from the 30d window (0.30), got {info.get('median')}"
    )


def test_gate_and_current_ratio_share_one_baseline_window(gate):
    """Two readings of the same buffer must not disagree about the window.

    A gate with two implementations of its own baseline is the 2026-08-17 failure
    mode: one can be wrong while the other looks right, and nothing detects it.
    """
    now = 1_000_000_000.0
    day = 86400.0
    gate._buf = [[now - (45 + i) * day, 1.00] for i in range(30)]
    gate._buf += [[now - (i * 0.5) * day, 0.30] for i in range(1, 31)]
    gate._buf.sort(key=lambda row: row[0])

    ratio = gate.current_ratio(_cache(0.60), now=now)
    _, _, info = gate.update_and_evaluate(_cache(0.60), now=now)

    assert ratio is not None and info.get("median")
    implied = 0.60 / float(info["median"])
    assert implied == pytest.approx(ratio, rel=0.05), (
        f"current_ratio implies median {0.60 / ratio:.3f} but the gate used "
        f"{info['median']:.3f} -- the two paths are windowing differently"
    )
