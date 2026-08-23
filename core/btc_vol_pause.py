"""core/btc_vol_pause.py — market-wide BTC volatility circuit-breaker for NEW entries.

Owner directive (2026-06-04): "Over the past few days BTC is not respecting any
zone ... pure one-line-down sell event. Before taking any new trades wait for
sometime and react when it is worth the risk."

This pauses NEW entries (any side) while BTC's short-term realized volatility is
spiking well above its OWN recent normal, then auto-resumes once it calms — the
"wait during the violent move, react when worth the risk" behaviour.

WHY THIS, AND ONLY THIS (per the 2026-06-04 TP-accuracy diagnosis + review):
  * The per-symbol regime gate (core/market_regime.py, bot_engine Phase 9) already
    blocks per-symbol counter-trend entries and soft-throttles per-symbol VOLATILE.
    The ONE missing surface is a MARKET-WIDE veto: a symbol that reads calm on its
    OWN OHLCV can still be entered in the middle of a BTC crash. This gate fills
    exactly that gap, driven by BTC.
  * It is direction-AGNOSTIC. We deliberately do NOT add a "go short in a dump"
    bias — that is market beta (the diagnosis showed the bot's dump-period "wins"
    were net-short beta, not edge), and the bot is already ~93% short anyway.

HONEST SCOPE: extreme-vol trades are the most expensive (wide spreads / slippage)
and the most random; pausing them lowers cost + variance and cuts trade count
(the cost lever the diagnosis identified). It does NOT add predictive alpha.
"React when worth the risk" reduces to "resume when BTC vol normalises" — there is
no setup-quality signal beyond regime normalisation.

DESIGN:
  * Adaptive threshold: pause when BTC 1h ATR% >= vol_spike_mult x the trailing
    MEDIAN of BTC's own 1h ATR%. Relative-to-own-median (not a fixed % tuned to
    this week) so it generalises across calm and volatile epochs.
  * Hysteresis cooldown: after a spike, stay paused for clear_minutes; only resume
    once vol has been calm (<= hysteresis_mult x median) and the timer elapsed.
  * Fail-OPEN: missing/stale BTC data or warmup => NOT paused (a data gap can never
    wedge the bot paused indefinitely). Mirrors short_side_filter's None->allow.
  * NEW ENTRIES ONLY — never position management or stop-losses.
  * State persists to data/btc_vol_state.json (local file, not an exchange write).
"""
from __future__ import annotations

import json
import os
import statistics
import time

# The band-regime veto's baseline window. config/gates.py specifies
# "BTC 1h ATR / 30d median" — the window screen 13 pre-registered — so this is
# spec, not a tunable. buffer_max (1000 hourly samples ~= 42d) is a memory cap
# and must never be mistaken for the statistical window (fixed 2026-08-17).
_BASELINE_WINDOW_SEC = 30 * 24 * 3600
from pathlib import Path

from loguru import logger

_STATE = Path("data/btc_vol_state.json")


def _cfg() -> dict:
    try:
        from config import BTC_VOL_PAUSE
        return dict(BTC_VOL_PAUSE)
    except Exception:
        return {"enabled": False}


def extract_btc_atr_pct(indicator_cache, tf: str = "1h"):
    """BTC ATR% (e.g. 2.45 == 2.45%) at `tf` from the MCP indicator cache, or None."""
    if not isinstance(indicator_cache, dict):
        return None
    btc = indicator_cache.get("BTC") or {}
    v = (btc.get(tf) or {}).get("atr_pct")
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if v > 0 else None


class BtcVolPause:
    """Stateful market-wide BTC volatility pause. One instance per engine."""

    def __init__(self):
        self._buf: list[list[float]] = []   # [[ts, atr_pct], ...] trailing baseline
        self._pause_until: float = 0.0
        self._load()

    # ── persistence ────────────────────────────────────────────────────
    def _load(self):
        try:
            if _STATE.exists():
                d = json.loads(_STATE.read_text(encoding="utf-8"))
                self._buf = [list(x) for x in d.get("buf", [])]
                self._pause_until = float(d.get("pause_until", 0.0))
        except Exception as e:
            logger.debug(f"[BtcVolPause] load skipped: {e}")

    def _save(self):
        try:
            _STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE.with_name(_STATE.name + ".tmp")
            tmp.write_text(json.dumps({"buf": self._buf, "pause_until": self._pause_until}),
                           encoding="utf-8")
            os.replace(tmp, _STATE)
        except Exception as e:
            logger.debug(f"[BtcVolPause] save skipped: {e}")

    # ── core ───────────────────────────────────────────────────────────
    def update_and_evaluate(self, indicator_cache, now: float = None):
        """Returns (paused: bool, reason: str, info: dict). Fail-OPEN on any gap."""
        cfg = _cfg()
        if not cfg.get("enabled", False):
            return False, "disabled", {}
        if now is None:
            now = time.time()
        atr = extract_btc_atr_pct(indicator_cache, cfg.get("timeframe", "1h"))
        if atr is None:
            return False, "no_btc_data", {}          # fail open

        dirty = False
        # Append to the trailing baseline at most ~hourly (dedup so per-candidate
        # calls within a cycle don't pollute the sample).
        interval = float(cfg.get("append_min_interval_sec", 3600))
        if not self._buf or (now - self._buf[-1][0]) >= interval:
            self._buf.append([now, atr])
            cap = int(cfg.get("buffer_max", 1000))
            if len(self._buf) > cap:
                self._buf = self._buf[-cap:]
            dirty = True

        # 30-DAY window, not the whole buffer. This is the SPEC (config/gates.py
        # pre-registers "BTC 1h ATR / 30d median"), and it is the same cutoff
        # current_ratio() applies below. Until 2026-08-20 this line read
        # `[a for (_, a) in self._buf]` -- un-windowed -- so the gate and
        # current_ratio disagreed about their own baseline while reading one buffer.
        # buffer_max=1000 hourly samples is ~42d and the live buffer had reached 700
        # samples spanning 60.8 days; in a decaying-vol regime the older readings sit
        # HIGHER, inflating the median and RAISING the spike threshold, i.e. the gate
        # was more PERMISSIVE than screen 13 authorised (measured 2026-08-20:
        # whole-buffer median 0.43% -> threshold 0.86% vs 30d-spec 0.36% -> 0.72%).
        # The 2026-08-17 postmortem fixed current_ratio() and missed this path.
        cutoff = now - _BASELINE_WINDOW_SEC
        samples = [a for (ts, a) in self._buf if ts >= cutoff]
        if len(samples) < int(cfg.get("min_samples", 24)):
            if dirty:
                self._save()
            return False, f"warmup({len(samples)})", {"atr": round(atr, 3)}   # fail open

        median = statistics.median(samples)
        spike = float(cfg.get("vol_spike_mult", 2.0)) * median
        clear = float(cfg.get("hysteresis_mult", 1.5)) * median
        clear_sec = float(cfg.get("clear_minutes", 30)) * 60.0
        info = {"atr": round(atr, 3), "median": round(median, 3),
                "spike": round(spike, 3), "clear": round(clear, 3)}

        if atr >= spike:
            self._pause_until = now + clear_sec
            self._save()
            return (True,
                    f"BTC vol spike: 1h ATR {atr:.2f}% >= {spike:.2f}% "
                    f"({cfg.get('vol_spike_mult', 2.0)}x median {median:.2f}%)", info)

        # within the post-spike cooldown window
        if now < self._pause_until:
            if atr > clear:
                # still elevated (between clear and spike) -> extend the wait
                self._pause_until = now + clear_sec
                self._save()
                return True, f"BTC vol still elevated: 1h ATR {atr:.2f}% > {clear:.2f}%", info
            # calm again, but honour the timed wait ("wait for sometime")
            mins = int((self._pause_until - now) / 60) + 1
            if dirty:
                self._save()
            return True, f"BTC vol cooldown: calm but waiting {mins}m before re-entry", info

        if dirty:
            self._save()
        return False, "calm", info

    def current_ratio(self, indicator_cache, now: float | None = None):
        """BTC 1h ATR% divided by its trailing 30-DAY median, or None.

        Read-only (no buffer append, no pause-state change) — the C.4 gate's
        update_and_evaluate() owns and maintains the baseline. Returns None on
        missing BTC data, warmup (< min_samples in-window), or a non-positive
        median; callers MUST fail OPEN on None. Added 2026-07-12 for the
        band-lane regime filter (screen 13_band_conditional).

        The 30-day window is the SPEC, not a tuning choice: config/gates.py
        defines the veto as "BTC 1h ATR / 30d median < 0.7", which is what
        screen 13 pre-registered. Until 2026-08-17 this took the median of the
        WHOLE buffer, and buffer_max=1000 hourly samples is ~42 days — the live
        buffer had reached 675 samples spanning 1,384h (58 days). In a
        decaying-vol regime the extra-old readings sit higher, inflating the
        median and depressing the ratio, so the veto fired on a window the
        screen never authorised: measured 0.628 (58d baseline, BLOCKS) vs
        0.730 (30d baseline, PASSES) — 80h of zero trades.
        """
        cfg = _cfg()
        atr = extract_btc_atr_pct(indicator_cache, cfg.get("timeframe", "1h"))
        if atr is None:
            return None
        cutoff = (time.time() if now is None else float(now)) - _BASELINE_WINDOW_SEC
        samples = [a for (ts, a) in self._buf if ts >= cutoff]
        if len(samples) < int(cfg.get("min_samples", 24)):
            return None
        median = statistics.median(samples)
        if median <= 0:
            return None
        return atr / median
