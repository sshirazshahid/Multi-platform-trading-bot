"""Guard: a gate that MISCOMPUTES its own threshold must not fail silently.

2026-08-17 postmortem. The band-regime veto is specified in config/gates.py as
"BTC 1h ATR / 30d median < 0.7". `current_ratio()` instead took the median of
the WHOLE buffer, which had grown to 58 days (buffer_max=1000 hourly samples
is ~42d). In a decaying-vol regime the older readings sit higher, inflating
the median and depressing the ratio:

    58d baseline -> ratio 0.698  BLOCK      <- what the bot used
    30d baseline -> ratio 0.811  PASS       <- what the spec says

The bot sat out of the market for 80 HOURS on a 0.002 margin, and nothing
alarmed. Two reasons it stayed invisible, both addressed here:

  1. NO INDEPENDENT CHECK. Only one computation of the ratio existed, so a
     wrong one looked exactly like a right one. The guard recomputes the spec
     quantity from the same buffer and alerts on divergence.

  2. THE WATCHDOG WAS TAUGHT TO TRUST IT. On 2026-08-15 `band_regime_filter`
     was added to DELIBERATE_ENTRY_BLOCKS so a *working* rail would stop
     paging hourly — which also silenced a *broken* one. Deliberate blocks now
     suppress only up to a duration cap; past it, sustained single-reason
     idleness alerts regardless of classification.

A third defect the postmortem surfaced: a stale numerator corrupts the ratio no
matter which window the median uses. The threshold is 2x the configured 3600s
append interval, i.e. 120 min — one full interval missed. Be precise about what
that does and does not catch: the 108-min and 89-min readings observed during
the postmortem are BELOW it and do not fire. What fires is the tail — measured
over the live buffer's 675 gaps, the median is 62 min (healthy, one append per
hour) but 13.2% exceed 120 min and the worst is 5,726 min (~4 days).

Because that tail is common, staleness is edge-triggered: one alert per episode,
re-armed when the feed recovers. On a plain cooldown the 4-day gap alone would
have sent ~190 emails — reintroducing the alert numbness this file exists to
prevent.

Run: venv/Scripts/python.exe -m pytest tests/test_gate_value_verification.py -v
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import core.health_watchdog as hw  # noqa: E402


class _Notifier:
    def __init__(self):
        self.sent = []

    def alert(self, message, title=None, context=None):
        self.sent.append((title, message))


def _wd(notifier):
    """A HealthWatchdog with only the attributes these checks touch.

    __init__ wants a live engine + warehouse; verification needs neither.
    """
    wd = object.__new__(hw.HealthWatchdog)
    wd._engine = None
    wd._notifier = notifier
    wd._risk = None
    wd._state = hw.WatchdogState()
    wd._first_seen = {}
    return wd


def _buf(hours, atr=0.40, now=None):
    """A dense hourly buffer of `hours` samples ending ~now."""
    now = now or time.time()
    return [[now - h * 3600, atr] for h in range(hours, 0, -1)]


# ── 1. spec-vs-implementation divergence ────────────────────────────────────

def test_divergent_gate_value_alerts(tmp_path, monkeypatch):
    """THE bug: implementation ratio disagrees with the spec recomputation."""
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    n = _Notifier()
    # 30d window gives 0.30/0.40 = 0.75; the gate reports the 58d value 0.698.
    ok = _wd(n)._verify_btc_vol_ratio(reported=0.698, buf=_buf(700), atr=0.30)
    assert ok is False
    assert n.sent, "a diverging gate value must alert"


def test_agreeing_gate_value_is_silent(tmp_path, monkeypatch):
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    n = _Notifier()
    # atr 0.20 / median 0.40 = 0.5 exactly
    ok = _wd(n)._verify_btc_vol_ratio(reported=0.5, buf=_buf(400), atr=0.20)
    assert ok is True and n.sent == []


def test_small_float_noise_is_tolerated(tmp_path, monkeypatch):
    """Float jitter must not page the operator."""
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    n = _Notifier()
    ok = _wd(n)._verify_btc_vol_ratio(reported=0.5001, buf=_buf(400), atr=0.20)
    assert ok is True and n.sent == []


# ── 2. buffer staleness (independent of the window bug) ─────────────────────

def test_stale_buffer_alerts(tmp_path, monkeypatch):
    """A stale newest sample corrupts the numerator whatever the window is.

    Live buffer at postmortem: newest sample 108 min old vs a 60 min append
    interval."""
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    n = _Notifier()
    stale = [[time.time() - 3 * 3600 - h * 3600, 0.40] for h in range(400, 0, -1)]
    _wd(n)._verify_btc_vol_ratio(reported=0.5, buf=stale, atr=0.20)
    blob = " ".join(m for _, m in n.sent).lower()
    assert n.sent and "stale" in blob, f"stale buffer must alert, got {n.sent}"


def test_stale_alert_is_edge_triggered(tmp_path, monkeypatch):
    """One alert per stale episode, not one per tick.

    13.2% of the live buffer's gaps exceed the threshold and the worst is
    ~4 days; on a plain cooldown that single gap is ~190 emails."""
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    n = _Notifier()
    wd = _wd(n)
    stale = [[time.time() - 3 * 3600 - h * 3600, 0.40] for h in range(400, 0, -1)]
    for _ in range(5):
        wd._verify_btc_vol_ratio(reported=0.5, buf=stale, atr=0.20)
        # Age every recorded cooldown past its window — what real elapsed time
        # does. Without this the plain-cooldown path would also send once and
        # the test would pass against the very code it is meant to reject.
        for k in list(wd._state.last_alert):
            wd._state.last_alert[k] -= 3601
    assert len(n.sent) == 1, f"expected 1 alert per episode, got {len(n.sent)}"


# ── 2b. the verifier must compute the SAME function as the gate ─────────────

def test_min_samples_floor_is_mirrored(tmp_path, monkeypatch):
    """current_ratio returns None below min_samples in-window; so must we.

    Otherwise a buffer that is mostly older than 30d makes the gate abstain
    while the verifier produces a number, and the guard cries drift at a
    correctly-behaving gate — a false positive in the verification channel
    is how verification channels get muted."""
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    n = _Notifier()
    now = time.time()
    # 10 recent samples (< min_samples=24) + 100 well outside the 30d window
    buf = ([[now - d * 86400, 0.90] for d in range(140, 40, -1)]
           + [[now - h * 3600, 0.40] for h in range(10, 0, -1)])
    ok = _wd(n)._verify_btc_vol_ratio(reported=None, buf=buf, atr=0.20)
    assert ok is True and n.sent == [], (
        f"verifier must abstain below min_samples like the gate, got {n.sent}"
    )


def test_empty_buffer_does_not_crash_or_alert(tmp_path, monkeypatch):
    """No data is a warmup state, not a defect — fail open, stay quiet."""
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    n = _Notifier()
    assert _wd(n)._verify_btc_vol_ratio(reported=None, buf=[], atr=None) is True
    assert n.sent == []


# ── 3. the suppression cap ──────────────────────────────────────────────────

def test_deliberate_block_suppression_is_time_capped():
    """A 'deliberate' rail blocking for days is a symptom, not a rail.

    2026-08-15 added band_regime_filter to DELIBERATE_ENTRY_BLOCKS so a working
    veto would stop paging hourly. That also silenced 80h of a BROKEN one."""
    assert hasattr(hw, "DELIBERATE_BLOCK_MAX_HOURS"), (
        "suppression must be time-bounded, not unconditional"
    )
    assert 0 < hw.DELIBERATE_BLOCK_MAX_HOURS <= 48


def test_suppression_cap_is_honoured_in_the_starvation_check():
    """Source pin: the cap must actually gate the suppression branch."""
    import inspect

    src = inspect.getsource(hw.HealthWatchdog._check_model_gate_starving)
    assert "DELIBERATE_BLOCK_MAX_HOURS" in src, (
        "the starvation check must consult the suppression cap"
    )


# ── 2026-08-19: the chop-alert incident (367 hits = 3 symbols) ──────────────

def test_chop_is_a_deliberate_block():
    """The universe chop veto is a designed measured filter. With its ER now
    computed on venue-independent UTC days, refusing chop IS the system
    working; idleness under it must not page hourly — only past the cap."""
    assert "chop" in hw.DELIBERATE_ENTRY_BLOCKS


def test_capped_starvation_has_its_own_slower_cadence():
    """Idle >24h is worth a nudge 4x/day, not hourly. The capped branch must
    alert under its own key with a >=6h cooldown, or a long deliberate idle
    re-trains the operator to ignore the channel (the 2026-08-15 numbness)."""
    import inspect

    assert hw.COOLDOWN_SEC.get("model_gate_starving_capped", 0) >= 6 * 3600
    src = inspect.getsource(hw.HealthWatchdog._check_model_gate_starving)
    assert "model_gate_starving_capped" in src


def test_dominant_block_reports_unique_symbols(tmp_path, monkeypatch):
    """'315 hits' was 3 symbols re-polled every 5-min cycle. The scan must
    count unique symbols so the alert cannot dramatise polling inflation."""
    import datetime as _dt

    monkeypatch.setattr(hw, "LOG_DIR", tmp_path)
    monkeypatch.setattr(hw, "COOLDOWN_STATE_PATH", tmp_path / "cd.json")
    now = _dt.datetime.now(_dt.timezone.utc)
    stamp = now.strftime("%Y-%m-%d %H:%M:%S")
    lines = []
    for sym in ("ATOM/USDT", "ATOM/USDT", "ATOM/USDT", "BTC/USDT", "BTC/USDT"):
        lines.append(
            f"{stamp} | INFO | core.engine.entry_exec:_execute_open:943 | "
            f"[Claude] BLOCKED by universe filter: {sym} — chop:ER=0.09<0.12")
    (tmp_path / f"bot_{now.strftime('%Y-%m-%d')}.log").write_text(
        "\n".join(lines), encoding="utf-8")
    wd = _wd(_Notifier())
    reason, hits = wd._dominant_entry_block_reason()
    assert (reason, hits) == ("chop", 5)
    assert wd._dominant_block_symbols == 2, (
        "the scan must expose how many DISTINCT symbols produced the hits"
    )


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
