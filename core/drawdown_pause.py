"""core/drawdown_pause.py — path-dependent drawdown circuit-breaker for NEW entries.

WHY (2026-06-28 deep-research risk finding): per-PERIOD loss caps (the soft
daily-loss breaker, per-period CVaR/ES) are blind to slow-bleed CLUSTERING — a
position losing 2%/day for 30 days compounds to ~45% drawdown that never trips a
5% *daily* limit. The literature (López de Prado / Jansen ML4Trading ch.19;
QuantOracle VaR-vs-CVaR-vs-MaxDD) is explicit that per-period risk metrics do NOT
replace a PATH-dependent max-drawdown rail. This module is that rail.

It pauses NEW entries (any side) when equity has fallen more than ``max_drawdown_pct``
below its running peak, and auto-resumes once equity has recovered to within a
``hysteresis_pct`` deadband of the trip level (so it does not flap at the boundary).

DESIGN — mirrors core/btc_vol_pause.py:
  * Opt-in via env vars (default OFF); NON-kernel (no config.py / risk_manager edit).
  * NEW ENTRIES ONLY — never touches open positions or their stop-losses (their
    fail-closed per-trade ATR SLs remain the hard rail).
  * Fail-OPEN: a missing/zero peak or equity can never wedge the bot paused.
  * Hysteresis deadband: trip at ``max_drawdown_pct``, resume only once drawdown
    recovers to <= (max_drawdown_pct - hysteresis_pct), optionally after
    ``clear_minutes``. State persists to data/drawdown_pause_state.json.

HONEST SCOPE: this is capital-PRESERVATION, not alpha. It bounds path risk and
cuts trade count during a sustained bleed; it adds no predictive edge.

Env knobs (read live each call):
  DRAWDOWN_PAUSE_ENABLED        bool   default "false"  (opt-in)
  DRAWDOWN_MAX_PCT              float  default "0.15"   trip at 15% below peak
  DRAWDOWN_HYSTERESIS_PCT       float  default "0.05"   resume-deadband width
  DRAWDOWN_CLEAR_MIN            float  default "0"      min minutes paused before resume
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path

from loguru import logger

_STATE = Path("data/drawdown_pause_state.json")


def _cfg() -> dict:
    """Read the breaker config from env each call (live-tunable, opt-in)."""
    def _f(name: str, default: str) -> float:
        try:
            return float(os.getenv(name, default))
        except (TypeError, ValueError):
            return float(default)

    return {
        "enabled": os.getenv("DRAWDOWN_PAUSE_ENABLED", "false").strip().lower() == "true",
        "max_drawdown_pct": max(0.0, _f("DRAWDOWN_MAX_PCT", "0.15")),
        "hysteresis_pct": max(0.0, _f("DRAWDOWN_HYSTERESIS_PCT", "0.05")),
        "clear_minutes": max(0.0, _f("DRAWDOWN_CLEAR_MIN", "0")),
    }


class DrawdownPause:
    """Stateful drawdown-from-peak pause. One instance per engine."""

    def __init__(self):
        self._paused: bool = False
        self._pause_since: float = 0.0
        self._load()

    # ── persistence ────────────────────────────────────────────────────
    def _load(self):
        try:
            if _STATE.exists():
                d = json.loads(_STATE.read_text(encoding="utf-8"))
                self._paused = bool(d.get("paused", False))
                self._pause_since = float(d.get("pause_since", 0.0))
        except Exception as e:
            logger.debug(f"[DrawdownPause] load skipped: {e}")

    def _save(self):
        try:
            _STATE.parent.mkdir(parents=True, exist_ok=True)
            tmp = _STATE.with_name(_STATE.name + ".tmp")
            tmp.write_text(
                json.dumps({"paused": self._paused, "pause_since": self._pause_since}),
                encoding="utf-8",
            )
            os.replace(tmp, _STATE)
        except Exception as e:
            logger.debug(f"[DrawdownPause] save skipped: {e}")

    # ── core ───────────────────────────────────────────────────────────
    def update_and_evaluate(self, peak_balance, current_equity, now: float = None):
        """Return (paused: bool, reason: str, info: dict). Fail-OPEN on any gap.

        Trips when drawdown-from-peak >= max_drawdown_pct; resumes when drawdown
        recovers to <= (max_drawdown_pct - hysteresis_pct) and clear_minutes elapsed.
        """
        cfg = _cfg()
        if not cfg["enabled"]:
            return False, "disabled", {}
        if now is None:
            now = time.time()
        try:
            peak = float(peak_balance)
            equity = float(current_equity)
        except (TypeError, ValueError):
            return False, "no_data", {}
        if peak <= 0 or equity <= 0:
            return False, "no_data", {}  # fail open — can't compute a drawdown

        dd = max(0.0, (peak - equity) / peak)
        trip = cfg["max_drawdown_pct"]
        resume_dd = max(0.0, trip - cfg["hysteresis_pct"])
        info = {
            "drawdown_pct": round(dd * 100, 2),
            "trip_pct": round(trip * 100, 2),
            "resume_pct": round(resume_dd * 100, 2),
            "peak": round(peak, 2),
            "equity": round(equity, 2),
        }
        if trip <= 0:
            return False, "disabled", info  # 0% trip == off

        if not self._paused:
            if dd >= trip:
                self._paused = True
                self._pause_since = now
                self._save()
                return (True,
                        f"drawdown {dd * 100:.1f}% >= {trip * 100:.1f}% below peak ${peak:,.0f}",
                        info)
            return False, "ok", info

        # currently paused — resume only after recovery past the deadband + timer
        recovered = dd <= resume_dd
        mins_paused = (now - self._pause_since) / 60.0
        if recovered and mins_paused >= cfg["clear_minutes"]:
            self._paused = False
            self._pause_since = 0.0
            self._save()
            return False, f"recovered: drawdown {dd * 100:.1f}% <= {resume_dd * 100:.1f}%", info
        wait = max(0.0, cfg["clear_minutes"] - mins_paused)
        suffix = f" (calm; {wait:.0f}m timer)" if recovered else ""
        return (True,
                f"drawdown breaker active: {dd * 100:.1f}% below peak, "
                f"resume <= {resume_dd * 100:.1f}%{suffix}",
                info)
