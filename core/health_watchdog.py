"""
core/health_watchdog.py — monitor-of-the-monitor.

Runs once per minute via the bot_engine scheduler. Checks observable
state on disk and in the live RiskManager / BotEngine attributes,
and sends a notifier alert when one of the trigger conditions fires.

Each check is independent and rate-limited so a persistent fault doesn't
flood the notifier. Cooldowns are kept in-process; a process restart
re-arms every check.

Triggers (see WatchdogConfig for thresholds):

  1. heartbeat_stale
     `data/heartbeat.json` has not been touched for HEARTBEAT_STALE_SEC.
     Indicates the main scheduler hung. WARN.

  2. spec12_review_required
     `data/review_required.json` is on disk. ALERT (loud) — bot has
     hit spec §12 halt and operator should look.

  3. exchange_halted
     `bot_engine._exchange_halted` is non-empty for > EXCHANGE_HALT_SEC.
     WARN.

  4. sl_placement_failed
     Last post_mortem.json entry has close_reason='sl_placement_failed'
     and pnl_pct <= SL_FAIL_PNL_PCT. ALERT — points at unfixed
     placement bug.

  5. loss_streak
     Warehouse `trades` table closed >= LOSS_STREAK_N losers in the
     last LOSS_STREAK_WINDOW_MIN minutes. WARN.

  6. model_gate_starving
     mcp_decisions.jsonl tail shows zero OPENs in the last
     MODEL_STARVE_HOURS hours while RiskManager.daily_pnl > -2%.
     INFO — the model gate has been blocking everything; not an
     emergency, but operator should know.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

HEARTBEAT_PATH        = Path("data/heartbeat.json")
REVIEW_FLAG_PATH      = Path("data/review_required.json")
POST_MORTEM_PATH      = Path("data/post_mortem.json")
DECISIONS_PATH        = Path("data/mcp_decisions.jsonl")
WAREHOUSE_PATH        = Path("data/warehouse.sqlite")

# Phase 48 (2026-05-10): bumped 5min → 10min. Bot's portfolio cycle runs
# every 5min and a single Claude API call can take 60-90s; with order
# execution on top, a healthy cycle can briefly exceed 300s. The 5-min
# threshold fired alert emails on TRANSIENT slow cycles, not real
# stalls. 10min gives 2 cycles of buffer before alarming on a genuine hang.
HEARTBEAT_STALE_SEC      = 10 * 60        # 10 min
EXCHANGE_HALT_SEC        = 10 * 60        # 10 min
SL_FAIL_PNL_PCT          = -3.0
LOSS_STREAK_N            = 3
LOSS_STREAK_WINDOW_MIN   = 60
MODEL_STARVE_HOURS       = 6
MODEL_STARVE_DAILY_PNL_FLOOR_PCT = -2.0   # only nag when bot is NOT in drawdown

# Per-check cooldowns (seconds) — re-fire once after the cooldown elapses
COOLDOWN_SEC = {
    "heartbeat_stale":       30 * 60,
    "spec12_review_required": 60 * 60,
    "exchange_halted":       30 * 60,
    "sl_placement_failed":   30 * 60,
    "loss_streak":           60 * 60,
    "model_gate_starving":   60 * 60,
}


@dataclass
class WatchdogState:
    last_alert: dict[str, float] = field(default_factory=dict)


class HealthWatchdog:

    def __init__(self, bot_engine, notifier=None, risk_manager=None,
                 warehouse_path: Path = WAREHOUSE_PATH):
        self._engine = bot_engine
        self._notifier = notifier
        self._risk = risk_manager
        self._warehouse_path = warehouse_path
        self._state = WatchdogState()
        # Track when a check first observed a sticky condition so we can
        # only alert after it persists past its threshold.
        self._first_seen: dict[str, float] = {}

    # ── Public API ──────────────────────────────────────────────────────

    def tick(self) -> None:
        """Run every check. Safe to call from a scheduler — never raises."""
        for check in (
            self._check_heartbeat,
            self._check_review_flag,
            self._check_exchange_halted,
            self._check_sl_placement_failed,
            self._check_loss_streak,
            self._check_model_gate_starving,
        ):
            try:
                check()
            except Exception as e:
                logger.debug(f"[Watchdog] {check.__name__} skipped: {e}")

    # ── Internals ───────────────────────────────────────────────────────

    def _alert(self, key: str, level: str, message: str,
               context: Optional[dict] = None) -> None:
        now = time.time()
        cooldown = COOLDOWN_SEC.get(key, 30 * 60)
        if (now - self._state.last_alert.get(key, 0)) < cooldown:
            return
        self._state.last_alert[key] = now
        title = f"[Watchdog/{level.upper()}] {key}"
        logger.warning(f"{title} — {message}")
        if self._notifier is not None:
            try:
                self._notifier.alert(message, title=title, context=context or {})
            except Exception as e:
                logger.debug(f"[Watchdog] notifier failed: {e}")

    def _check_heartbeat(self) -> None:
        if not HEARTBEAT_PATH.exists():
            return  # bot may not have written one yet
        age = time.time() - HEARTBEAT_PATH.stat().st_mtime
        if age > HEARTBEAT_STALE_SEC:
            self._alert(
                "heartbeat_stale", "WARN",
                f"heartbeat.json is {int(age)}s old (> {HEARTBEAT_STALE_SEC}s threshold)",
                {"age_sec": int(age), "path": str(HEARTBEAT_PATH)},
            )

    def _check_review_flag(self) -> None:
        if not REVIEW_FLAG_PATH.exists():
            # Re-arm: clear stale alert so we'll fire again if it reappears.
            self._state.last_alert.pop("spec12_review_required", None)
            return
        try:
            data = json.loads(REVIEW_FLAG_PATH.read_text(encoding="utf-8"))
        except Exception:
            data = {}
        self._alert(
            "spec12_review_required", "ALERT",
            "Spec §12 review_required.json is present — bot is in a halt window.",
            {
                "reason": str(data.get("reason", "?")),
                "action": str(data.get("action", "?")),
                "ts":     str(data.get("ts", "?")),
            },
        )

    def _check_exchange_halted(self) -> None:
        halted = getattr(self._engine, "_exchange_halted", None)
        if not halted:
            self._first_seen.pop("exchange_halted", None)
            return
        first = self._first_seen.setdefault("exchange_halted", time.time())
        if (time.time() - first) >= EXCHANGE_HALT_SEC:
            self._alert(
                "exchange_halted", "WARN",
                f"Exchange halt persisted > {EXCHANGE_HALT_SEC}s: {sorted(halted)}",
                {"halted": sorted(halted)},
            )

    def _check_sl_placement_failed(self) -> None:
        if not POST_MORTEM_PATH.exists():
            return
        try:
            pm = json.loads(POST_MORTEM_PATH.read_text(encoding="utf-8"))
        except Exception:
            return
        analyses = pm.get("analyses") or []
        if not analyses:
            return
        last = analyses[-1]
        # Only alert on entries newer than last alert — `timestamp` is unix seconds.
        ts = float(last.get("timestamp") or 0)
        last_alert_ts = self._state.last_alert.get("sl_placement_failed", 0)
        if ts <= last_alert_ts:
            return
        if (last.get("close_reason") == "sl_placement_failed"
                and float(last.get("pnl_pct") or 0) <= SL_FAIL_PNL_PCT):
            self._alert(
                "sl_placement_failed", "ALERT",
                f"{last.get('symbol')} closed at {last.get('pnl_pct'):.2f}% with"
                f" sl_placement_failed — exchange-side SL was never set.",
                {
                    "symbol":   str(last.get("symbol")),
                    "exchange": str(last.get("exchange")),
                    "side":     str(last.get("side")),
                    "pnl_pct":  last.get("pnl_pct"),
                    "leverage": last.get("leverage"),
                },
            )

    def _check_loss_streak(self) -> None:
        if not self._warehouse_path.exists():
            return
        cutoff = time.time() - LOSS_STREAK_WINDOW_MIN * 60
        try:
            conn = sqlite3.connect(str(self._warehouse_path))
            try:
                cur = conn.execute(
                    "SELECT realized_pnl FROM trades "
                    "WHERE status='CLOSED' AND ts_exit >= ? "
                    "ORDER BY ts_exit DESC LIMIT 10",
                    (cutoff,),
                )
                pnls = [r[0] for r in cur.fetchall()]
            finally:
                conn.close()
        except Exception:
            return
        if len(pnls) < LOSS_STREAK_N:
            return
        consec_losses = 0
        for p in pnls:
            if (p or 0) < 0:
                consec_losses += 1
            else:
                break
        if consec_losses >= LOSS_STREAK_N:
            self._alert(
                "loss_streak", "WARN",
                f"{consec_losses} consecutive losses in the last "
                f"{LOSS_STREAK_WINDOW_MIN} min.",
                {"consec_losses": consec_losses,
                 "recent_pnls": [round(float(p or 0), 4) for p in pnls[:consec_losses]]},
            )

    def _check_model_gate_starving(self) -> None:
        # Only nag when the bot isn't already in real drawdown — drawdown
        # makes the gate's caution rational and we don't want to encourage
        # operators to override it.
        if self._risk is not None:
            try:
                if float(self._risk.daily_pnl) <= MODEL_STARVE_DAILY_PNL_FLOOR_PCT:
                    return
            except Exception:
                pass
        if not DECISIONS_PATH.exists():
            return
        cutoff = time.time() - MODEL_STARVE_HOURS * 3600
        opens_recent = 0
        try:
            with DECISIONS_PATH.open("r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                    except Exception:
                        continue
                    ts = float(rec.get("ts") or 0)
                    if ts < cutoff:
                        continue
                    if (rec.get("type") or rec.get("action") or "").upper() == "OPEN":
                        opens_recent += 1
        except Exception:
            return
        if opens_recent == 0:
            self._alert(
                "model_gate_starving", "INFO",
                f"No OPEN actions in the last {MODEL_STARVE_HOURS}h despite "
                "non-drawdown state — model gate may be starving for signal.",
                {"opens_recent": opens_recent,
                 "window_hours": MODEL_STARVE_HOURS},
            )
