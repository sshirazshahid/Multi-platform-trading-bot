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

        samples = [a for (_, a) in self._buf]
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

    def current_ratio(self, indicator_cache):
        """BTC 1h ATR% divided by its trailing-median baseline, or None.

        Read-only (no buffer append, no pause-state change) — the C.4 gate's
        update_and_evaluate() owns and maintains the baseline. Returns None on
        missing BTC data, warmup (< min_samples), or a non-positive median;
        callers MUST fail OPEN on None. Added 2026-07-12 for the band-lane
        regime filter (bot_engine._band_regime_veto, screen 13_band_conditional).
        """
        cfg = _cfg()
        atr = extract_btc_atr_pct(indicator_cache, cfg.get("timeframe", "1h"))
        if atr is None:
            return None
        samples = [a for (_, a) in self._buf]
        if len(samples) < int(cfg.get("min_samples", 24)):
            return None
        median = statistics.median(samples)
        if median <= 0:
            return None
        return atr / median
