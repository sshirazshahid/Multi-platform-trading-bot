"""
core/health_watchdog.py — monitor-of-the-monitor.

Runs once per minute via the bot_engine scheduler. Checks observable
state on disk and in the live RiskManager / BotEngine attributes,
and sends a notifier alert when one of the trigger conditions fires.

Each check is independent and rate-limited so a persistent fault doesn't
flood the notifier. Cooldowns are persisted to
``data/watchdog_cooldown_state.json`` so a process restart does NOT re-arm
every check — a sticky condition (e.g. a stale model pointer or a
NOT_READY audit) that is still true after a bounce stays muted until its
cooldown genuinely elapses, instead of re-emailing on every restart.

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

  7. stuck_open_positions
     Warehouse trades stuck at status='OPEN' older than STUCK_OPEN_HOURS
     that are NOT still open in positions.json (true orphans after a
     trade_id close miss). Live holds past 24h (tier-geometry) are silent.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from loguru import logger


def _decision_ts_epoch(raw) -> Optional[float]:
    """Parse an mcp_decisions.jsonl ``ts`` value to epoch seconds.

    Accepts legacy float/int epochs AND the current ISO-8601 strings
    (e.g. ``2026-07-20T01:13:35.644077+00:00``); returns None when
    unparseable so one bad record cannot silently kill a whole check
    (F6, 2026-07-20 audit).

    NOTE (2026-07-28): its only consumer, _check_model_gate_starving, no
    longer reads that file — it reads positions.json, whose timestamps are
    plain epoch floats. Kept (not deleted) because it is the correct parser
    for that log and the Brain view's live-reasoning feed reads the same
    file; deleting it would only make the next consumer re-derive it."""
    if raw is None:
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        pass
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    except ValueError:
        return None

HEARTBEAT_PATH        = Path("data/heartbeat.json")
CARRY_HEARTBEAT_PATH  = Path("data/carry_heartbeat.json")
CARRY_STATE_PATH      = Path("data/carry_positions.json")
# Live-sample accuracy milestones: announce measured per-cycle WR when the
# resolved-cycle count first reaches each level (owner goal bar: 80%).
CARRY_SAMPLE_MILESTONES = (1, 10, 30, 60)
REVIEW_FLAG_PATH      = Path("data/review_required.json")
POST_MORTEM_PATH      = Path("data/post_mortem.json")
DECISIONS_PATH        = Path("data/mcp_decisions.jsonl")
# Authoritative record of "a position actually opened" — every entry carries
# open_time. Used by the starvation check instead of mcp_decisions.jsonl, whose
# record shapes cannot answer the question (see _check_model_gate_starving).
POSITIONS_PATH        = Path("data/positions.json")
# Anchored to the repo root (parents[1] == repo root from core/) so a standalone
# entrypoint that constructs HealthWatchdog from another cwd reads the canonical
# warehouse — cwd-relative broke ShadowResolver from System32 on 2026-07-05. (The
# other data/ constants here are consumed only by the in-process bot, which runs
# from ROOT; left cwd-relative to keep this diff surgical.)
WAREHOUSE_PATH        = Path(__file__).resolve().parents[1] / "data" / "warehouse.sqlite"

# Phase 48 (2026-05-10): bumped 5min → 10min. Bot's portfolio cycle runs
# every 5min and a single Claude API call can take 60-90s; with order
# execution on top, a healthy cycle can briefly exceed 300s. The 5-min
# threshold fired alert emails on TRANSIENT slow cycles, not real
# stalls. 10min gives 2 cycles of buffer before alarming on a genuine hang.
HEARTBEAT_STALE_SEC      = 10 * 60        # 10 min
# Carry runner is scheduled externally (~15 min); 1h stale = several missed
# passes. File existence is the opt-in — no file, no check.
CARRY_HEARTBEAT_STALE_SEC = 60 * 60       # 1 h
EXCHANGE_HALT_SEC        = 10 * 60        # 10 min
SL_FAIL_PNL_PCT          = -3.0
LOSS_STREAK_N            = 3
LOSS_STREAK_WINDOW_MIN   = 60
MODEL_STARVE_HOURS       = 6
MODEL_STARVE_DAILY_PNL_FLOOR_PCT = -2.0   # only nag when bot is NOT in drawdown
MODEL_POINTER_MARKETS    = ("futures", "spot")
NO_SCAN_PROGRESS_SEC     = 15 * 60
STUCK_OPEN_HOURS         = 24
REPORTS_DIR              = Path("reports")
# Persists per-check last-alert timestamps so cooldowns survive a restart
# (otherwise every sticky WARN re-emails on each bounce). Runtime state.
COOLDOWN_STATE_PATH      = Path("data/watchdog_cooldown_state.json")

# Per-check cooldowns (seconds) — re-fire once after the cooldown elapses
COOLDOWN_SEC = {
    "heartbeat_stale":       30 * 60,
    "carry_heartbeat_stale": 60 * 60,
    "carry_recovery_active": 60 * 60,
    "carry_sample_milestone": 60,  # milestones dedupe via _announced_milestones
    "spec12_review_required": 60 * 60,
    "exchange_halted":       30 * 60,
    "sl_placement_failed":   30 * 60,
    "loss_streak":           60 * 60,
    "model_gate_starving":   60 * 60,
    "model_pointer_invalid":  60 * 60,
    "no_scan_progress":      30 * 60,
    "stuck_open_positions":  60 * 60,
    "audit_not_ready":       6 * 60 * 60,
    "forward_feeds_stale":   30 * 60,
    # C8 (2026-07-08): per-venue keys are clock_drift_<venue>; this base
    # entry documents the family default (edge-triggered, so the cooldown
    # only matters across restarts of a still-true episode).
    "clock_drift":           60 * 60,
    # 2026-07-11: stale maker-first intents = resolver starvation class.
    "stale_maker_intents":   60 * 60,
}

# A pending maker intent older than this means the resolver is not running
# (timeout is 45s; 10 minutes = unambiguous starvation, not a slow tick).
STALE_MAKER_INTENT_SEC = 10 * 60

# A forward feed must stay unhealthy this long before we alert — absorbs the
# startup warmup window (status files from a previous run look stale until the
# just-launched harvester writes a fresh one) and brief poll flaps.
FEED_GRACE_SEC = 10 * 60


def live_open_position_keys(
    positions_path: Path = POSITIONS_PATH,
) -> Optional[set[tuple[str, str, str]]]:
    """Return {(exchange_lower, symbol, side_lower)} for tracked opens.

    ``None`` means positions state was unreadable — callers must skip the
    stuck/orphan check rather than treat every old warehouse OPEN as an orphan.
    """
    if not positions_path.exists():
        return set()
    try:
        doc = json.loads(positions_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    opens = []
    if isinstance(doc, dict):
        val = doc.get("open")
        if isinstance(val, list):
            opens = val
    elif isinstance(doc, list):
        opens = [e for e in doc if isinstance(e, dict) and not e.get("close_time")]
    keys: set[tuple[str, str, str]] = set()
    for e in opens:
        if not isinstance(e, dict):
            continue
        ex = str(e.get("exchange") or "").strip().lower()
        sym = str(e.get("symbol") or "").strip()
        side = str(e.get("side") or "").strip().lower()
        if ex and sym and side:
            keys.add((ex, sym, side))
    return keys


def orphan_open_trade_rows(
    warehouse_path: Path,
    *,
    older_than_hours: float = STUCK_OPEN_HOURS,
    positions_path: Path = POSITIONS_PATH,
    limit: int = 50,
) -> Optional[list]:
    """Warehouse OPEN rows older than threshold with no matching tracker open.

    Returns ``[]`` when none, or ``None`` when the check cannot run safely
    (missing warehouse / unreadable positions.json / query error).
    """
    if not warehouse_path.exists():
        return []
    live = live_open_position_keys(positions_path)
    if live is None:
        return None
    cutoff = time.time() - float(older_than_hours) * 3600
    try:
        conn = sqlite3.connect(str(warehouse_path))
        try:
            rows = conn.execute(
                """
                SELECT id, exchange, symbol, side, ts_entry FROM trades
                WHERE status='OPEN' AND ts_entry < ?
                ORDER BY ts_entry LIMIT ?
                """,
                (cutoff, int(limit)),
            ).fetchall()
        finally:
            conn.close()
    except Exception:
        return None
    orphans = []
    for r in rows:
        ex = str(r[1] or "").strip().lower()
        sym = str(r[2] or "").strip()
        side = str(r[3] or "").strip().lower()
        if (ex, sym, side) in live:
            continue
        orphans.append(r)
    return orphans


@dataclass
class WatchdogState:
    last_alert: dict[str, float] = field(default_factory=dict)
    last_cycle_count: int | None = None


class HealthWatchdog:

    def __init__(self, bot_engine, notifier=None, risk_manager=None,
                 warehouse_path: Path = WAREHOUSE_PATH):
        self._engine = bot_engine
        self._notifier = notifier
        self._risk = risk_manager
        self._warehouse_path = warehouse_path
        self._state = WatchdogState()
        # Restore persisted cooldowns so a restart doesn't re-fire every
        # still-true sticky alert (model_pointer_invalid, audit_not_ready, ...).
        self._load_cooldowns()
        # Track when a check first observed a sticky condition so we can
        # only alert after it persists past its threshold.
        self._first_seen: dict[str, float] = {}

    def _load_cooldowns(self) -> None:
        """Load persisted last-alert timestamps into the in-memory state."""
        try:
            if COOLDOWN_STATE_PATH.exists():
                raw = json.loads(COOLDOWN_STATE_PATH.read_text(encoding="utf-8"))
                if isinstance(raw, dict):
                    self._state.last_alert = {
                        str(k): float(v)
                        for k, v in raw.items()
                        if isinstance(v, (int, float))
                    }
        except Exception as e:  # corrupt/partial file must never block startup
            logger.debug(f"[Watchdog] cooldown state load skipped: {e}")

    def _persist_cooldowns(self) -> None:
        """Write last-alert timestamps to disk so cooldowns survive a restart."""
        try:
            COOLDOWN_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            COOLDOWN_STATE_PATH.write_text(
                json.dumps(self._state.last_alert), encoding="utf-8")
        except Exception as e:  # persistence is best-effort, never fatal
            logger.debug(f"[Watchdog] cooldown state persist skipped: {e}")

    # ── Public API ──────────────────────────────────────────────────────

    def tick(self) -> None:
        """Run every check. Safe to call from a scheduler — never raises."""
        for check in (
            self._check_heartbeat,
            self._check_carry_heartbeat,
            self._check_carry_recovery,
            self._check_carry_sample_milestones,
            self._check_review_flag,
            self._check_exchange_halted,
            self._check_sl_placement_failed,
            self._check_loss_streak,
            self._check_model_gate_starving,
            self._check_soft_stale_latch_stuck,
            self._check_model_pointer_valid,
            self._check_no_scan_progress,
            self._check_stuck_open_positions,
            self._check_forward_feeds,
            self._check_latest_audit,
            self._check_clock_drift,
            self._check_stale_maker_intents,
        ):
            try:
                check()
            except Exception as e:
                logger.debug(f"[Watchdog] {check.__name__} skipped: {e}")

    # ── Internals ───────────────────────────────────────────────────────

    def _alert(self, key: str, level: str, message: str,
               context: Optional[dict] = None) -> None:
        now = time.time()
        cooldown = COOLDOWN_SEC.get(key)
        if cooldown is None:
            # Per-venue keys (e.g. clock_drift_binance) inherit their family
            # base (clock_drift) instead of silently using the generic default.
            cooldown = COOLDOWN_SEC.get(key.rsplit("_", 1)[0], 30 * 60)
        if (now - self._state.last_alert.get(key, 0)) < cooldown:
            return
        title = f"[Watchdog/{level.upper()}] {key}"
        logger.warning(f"{title} — {message}")
        if self._notifier is not None:
            try:
                delivered = self._notifier.alert(
                    message,
                    title=title,
                    context=context or {},
                )
                # Legacy/test notifiers return None on success. The concrete
                # EmailNotifier returns False only when an enabled transport
                # actually failed. Do not mute that incident.
                if delivered is False and getattr(self._notifier, "enabled", True):
                    logger.debug("[Watchdog] notifier did not deliver; retry remains armed")
                    return
            except Exception as e:
                # Do NOT latch on a failed send (audit 2026-07-07): leaving
                # last_alert unset means the next tick retries this safety
                # alert instead of muting the whole episode on one SMTP hiccup.
                # The cooldown below applies only on the success path.
                logger.debug(f"[Watchdog] notifier failed: {e}")
                return
        self._state.last_alert[key] = now
        self._persist_cooldowns()

    def _edge_alert(self, key: str, is_bad: bool, level: str, message: str,
                    context: Optional[dict] = None, *, grace_sec: float = 0.0) -> None:
        """Edge-triggered alert for sticky/known conditions.

        Notify ONCE when a condition becomes true, stay silent while it
        persists, and re-arm when it clears — so the watchdog stops re-emailing
        a known, persistent fault on every cooldown tick. With ``grace_sec`` the
        condition must stay true that long before alerting (debounce for
        transient startup/flap conditions). Combined with the persisted
        last_alert state, a still-true condition also stays muted across a
        restart.
        """
        if not is_bad:
            self._first_seen.pop(key, None)
            if self._state.last_alert.pop(key, None) is not None:
                self._persist_cooldowns()  # re-arm survives restart
            return
        if grace_sec > 0:
            first = self._first_seen.setdefault(key, time.time())
            if (time.time() - first) < grace_sec:
                return  # not sustained long enough — likely transient
        if key in self._state.last_alert:
            return  # already alerted for this episode
        self._alert(key, level, message, context)

    def _check_clock_drift(self) -> None:
        """C8 (2026-07-08, watchdog check #15): venue clock drift.

        Reads the per-venue NTP-style offset map the engine's 60s health
        cycle maintains (engine._clock_drift_ms; None = no sample — never
        treated as drift). Edge-triggered per venue at
        config.CLOCK_DRIFT_ALERT_MS (default 500ms): one alert per episode,
        re-arms when the venue clock recovers. Alert-only, like every other
        check — the operator decides what to do (w32tm /resync etc.)."""
        drift_map = getattr(self._engine, "_clock_drift_ms", None)
        if not isinstance(drift_map, dict):
            return
        try:
            from config import CLOCK_DRIFT_ALERT_MS as _thr
        except ImportError:
            _thr = 500
        # 2026-07-27: each sample is (venue_clock - local_clock), so a wrong
        # LOCAL clock offsets every venue by the same amount. The SHAPE of the
        # map therefore says where to look, and blaming w32tm for a single
        # drifting venue sends the operator after something they cannot fix
        # (the 2026-07-22 and 2026-07-27 bitget alerts did exactly that).
        _sampled = [d for d in drift_map.values() if isinstance(d, (int, float))]
        _bad = [d for d in _sampled if abs(d) > _thr]
        if len(_sampled) < 2:
            _hint = "check venue status and local NTP/w32tm sync"
        elif _bad and all(abs(d) > 0.8 * _thr for d in _sampled):
            # Near-band attribution (2026-08-03): at the decaying tail of a
            # local-clock episode venues re-cross the threshold at slightly
            # different instants; a venue at 0.9x the line is still "drifting
            # together", not evidence against the local clock.
            _hint = "every sampled venue drifted together — check local NTP/w32tm sync"
        else:
            _hint = ("other venues are in sync — suspect this venue or its "
                     "network path, not the local clock")
        for ex_name, drift in drift_map.items():
            if not isinstance(drift, (int, float)):
                # Missing sample (slow-RTT discard / failed health check):
                # neither clears nor extends an episode — a sample gap must
                # not re-arm the edge alert mid-episode (2026-08-02 flap).
                continue
            if abs(drift) > _thr:
                is_bad = True
            elif abs(drift) < 0.8 * _thr:
                is_bad = False
            else:
                continue  # hysteresis band [0.8*thr, thr]: hold episode state
            self._edge_alert(
                f"clock_drift_{ex_name}", is_bad, "WARN",
                (f"{ex_name} clock drift {drift:+.0f}ms exceeds {_thr}ms — "
                 f"signed requests at risk; {_hint}"
                 if is_bad else ""),
                {"exchange": ex_name,
                 "drift_ms": round(drift, 1) if is_bad else None,
                 "threshold_ms": _thr} if is_bad else None,
            )

    def _check_stale_maker_intents(self) -> None:
        """2026-07-11 (watchdog check #16): stale maker-first intents.

        A pending virtual maker entry that outlives ~10x its 45s timeout means
        the resolver is not running — the starvation class that silently lost
        the INJ/ARB entries on 2026-07-11 (zero-open early-return). Runtime
        net: WARN the operator instead of losing entries quietly. Edge-
        triggered; re-arms when pending drains.
        """
        state_path = Path("data/pending_maker_entries.json")
        oldest_age = 0.0
        stale_syms: list = []
        try:
            if state_path.exists():
                state = json.loads(state_path.read_text(encoding="utf-8"))
                now = time.time()
                for key, intent in (state.get("pending") or {}).items():
                    age = now - float(intent.get("created_ts") or now)
                    if age > STALE_MAKER_INTENT_SEC:
                        stale_syms.append(f"{key}@{age / 60:.0f}min")
                    oldest_age = max(oldest_age, age)
        except (OSError, ValueError, TypeError):
            return  # unreadable state — not this check's business
        is_bad = bool(stale_syms)
        self._edge_alert(
            "stale_maker_intents", is_bad, "WARN",
            (f"{len(stale_syms)} maker-first intent(s) pending far past the "
             f"45s timeout ({', '.join(stale_syms[:4])}) — the resolver is "
             f"not running; entries are being LOST (starvation class, see "
             f"2026-07-11 fix)" if is_bad else ""),
            {"stale": stale_syms[:8],
             "oldest_age_min": round(oldest_age / 60, 1)} if is_bad else None,
        )

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

    def _check_carry_heartbeat(self) -> None:
        # NOT gated on SIGNAL_SOURCE — the heartbeat file's existence is the
        # opt-in ("carry never ran" is not an alert). Edge-triggered.
        if not CARRY_HEARTBEAT_PATH.exists():
            self._edge_alert("carry_heartbeat_stale", False, "WARN", "")
            return
        age = time.time() - CARRY_HEARTBEAT_PATH.stat().st_mtime
        self._edge_alert(
            "carry_heartbeat_stale", age > CARRY_HEARTBEAT_STALE_SEC, "WARN",
            f"carry heartbeat is {int(age)}s old "
            f"(> {CARRY_HEARTBEAT_STALE_SEC}s threshold)",
            {"age_sec": int(age), "path": str(CARRY_HEARTBEAT_PATH)},
        )

    def _check_carry_recovery(self) -> None:
        # Rev 5.2: the carry runner's heartbeat stores its pass summary;
        # summary.recovery_active true means the portfolio-wide reduce-only
        # latch is set. Missing file / missing key -> silent + re-arm.
        # Edge-triggered: one alert per episode, re-arms when the flag clears.
        active = False
        if CARRY_HEARTBEAT_PATH.exists():
            try:
                payload = json.loads(CARRY_HEARTBEAT_PATH.read_text(encoding="utf-8"))
                active = bool((payload.get("summary") or {}).get("recovery_active"))
            except Exception:
                active = False
        self._edge_alert(
            "carry_recovery_active", active, "ALERT",
            "carry reduce-only recovery latched — new carry entries halted; "
            "clear with scripts/run_f1_carry_paper.py --clear-recovery",
            {"path": str(CARRY_HEARTBEAT_PATH)},
        )

    def _check_carry_sample_milestones(self) -> None:
        """Announce the live carry sample's measured accuracy at milestones.

        The owner's goal (80% per-cycle accuracy) resolves in market time; this
        makes the sample self-announcing — at 1/10/30/60 resolved cycles the
        notifier reports the measured win rate instead of relying on anyone
        reading reports. Once per milestone per process; an engine restart may
        re-announce the highest reached milestone once (harmless reminder).
        """
        if not CARRY_STATE_PATH.exists():
            return
        cycles = json.loads(
            CARRY_STATE_PATH.read_text(encoding="utf-8")).get("cycles", [])
        resolved = [c for c in cycles if c.get("label_status") == "RESOLVED"]
        n = len(resolved)
        if n == 0:
            return
        announced = getattr(self, "_announced_milestones", set())
        due = [m for m in CARRY_SAMPLE_MILESTONES if n >= m and m not in announced]
        if not due:
            return
        announced.update(m for m in CARRY_SAMPLE_MILESTONES if n >= m)
        self._announced_milestones = announced
        wins = sum(1 for c in resolved if float(c.get("net_pnl", 0.0)) > 0)
        wr = 100.0 * wins / n
        self._alert(
            "carry_sample_milestone", "WARN",
            f"carry live sample milestone {max(due)}: {n} resolved cycles, "
            f"measured win rate {wr:.0f}% ({wins}W/{n - wins}L) — owner goal "
            f"bar is 80%; small samples overstate certainty",
            {"resolved": n, "wins": wins, "win_rate_pct": round(wr, 1)},
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

    @staticmethod
    def _expected_idle_under_strict_econ_gate() -> bool:
        """True when zero directional OPENs is the intended honesty state.

        Under EconGate=strict with no promoted model, AccBand/MCP directional
        flow is supposed to stay idle — alerting that as 'starvation' contradicts
        the owner premise (refuse −EV opens = success).
        """
        try:
            from config import MCP_DIRECTIONAL_ECONOMIC_GATE
            return str(
                MCP_DIRECTIONAL_ECONOMIC_GATE.get("mode", "")
            ).strip().lower() == "strict"
        except Exception:
            return False

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
        if self._expected_idle_under_strict_econ_gate():
            # Re-arm so a later flip to paper_fallback can alert again.
            self._edge_alert("model_gate_starving", False, "INFO", "")
            return
        # 2026-07-28: this counted mcp_decisions.jsonl records whose TOP-LEVEL
        # "type"/"action" == "OPEN". Measured against the live file, top-level
        # type is only ever portfolio / rejection / position_monitor — the real
        # OPEN actions sit two levels down at decisions.actions[].type. So the
        # count was structurally always 0 and this INFO alert emailed hourly
        # forever while the bot traded normally (16 entries that day). The F6
        # ISO-timestamp fix (2026-07-20) is what made the broken check start
        # firing; the shape mismatch predated it.
        #
        # Walking the nested actions is ALSO wrong: those are PROPOSED opens,
        # each of which additionally emits a "rejection" record — and that type
        # is a misnomer, its reasons include maker_first_maker_fill (a fill).
        # A decision_id join of proposals-minus-rejections measured 0 executed
        # opens on a day with 16 real entries.
        #
        # positions.json is the authoritative answer: every entry carries
        # open_time. Its closed list is a rolling 500 cap that keeps the NEWEST
        # entries, so recent opens are never truncated away; truncation could
        # only lower an old count, and this check alerts solely on zero.
        cutoff = time.time() - MODEL_STARVE_HOURS * 3600
        try:
            doc = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
        except Exception:
            # Absent or unreadable state is NOT evidence of starvation.
            return
        entries = []
        if isinstance(doc, dict):
            for key in ("open", "closed"):
                val = doc.get(key)
                if isinstance(val, list):
                    entries.extend(val)
        elif isinstance(doc, list):
            entries = doc
        opens_recent = 0
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                opened = float(e.get("open_time"))
            except (TypeError, ValueError):
                continue          # no usable open_time -> cannot count as recent
            if opened >= cutoff:
                opens_recent += 1
        if opens_recent == 0:
            self._alert(
                "model_gate_starving", "INFO",
                f"No OPEN actions in the last {MODEL_STARVE_HOURS}h despite "
                "non-drawdown state — model gate may be starving for signal.",
                {"opens_recent": opens_recent,
                 "window_hours": MODEL_STARVE_HOURS},
            )

    def _check_soft_stale_latch_stuck(self) -> None:
        """Warn if soft-stale entry block has been active for hours.

        Soft-stale correctly blocks NEW opens; a latch that never clears may
        mean feeds never recovered. Does not flatten positions.
        """
        path = Path("data/soft_stale_entry_latch.json")
        if not path.exists():
            self._edge_alert("soft_stale_latch_stuck", False, "WARN", "")
            return
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
            ts = float(doc.get("ts_unix") or 0.0)
        except Exception:
            self._alert(
                "soft_stale_latch_stuck", "WARN",
                "soft_stale_entry_latch.json present but unreadable — fail-closed "
                "entry block may be stuck; inspect feeds and clear latch when healthy.",
                {},
            )
            return
        age_h = (time.time() - ts) / 3600.0 if ts > 0 else 999.0
        if age_h >= 6.0:
            self._alert(
                "soft_stale_latch_stuck", "WARN",
                f"Soft-stale entry latch active for {age_h:.1f}h — NEW opens blocked. "
                "Open positions are held (no default loser-flatten). Check feeds.",
                {"age_hours": round(age_h, 2), "reason": (doc or {}).get("reason")},
            )
        else:
            self._edge_alert("soft_stale_latch_stuck", False, "WARN", "")

    def _check_model_pointer_valid(self) -> None:
        # The ML ensemble only drives live decisions on the MCP/Claude path.
        # Under SIGNAL_SOURCE=machine/tsmom it is shadow/log-only, so a stale
        # pointer is not operationally relevant — stay silent (and re-arm so it
        # fires again if the bot is switched back to mcp).
        try:
            from config import SIGNAL_SOURCE
        except Exception:
            SIGNAL_SOURCE = "mcp"
        if SIGNAL_SOURCE != "mcp":
            self._edge_alert("model_pointer_invalid", False, "WARN", "")
            return
        try:
            from core.promotion_gate import validate_model_pointer
        except Exception:
            return
        bad = None
        for market in MODEL_POINTER_MARKETS:
            path = Path("data/models") / f"ensemble_{market}_latest.json"
            if not path.exists():
                continue
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                ok, reason, diag = validate_model_pointer(payload, market_type=market)
            except Exception as exc:
                ok, reason, diag = False, str(exc), {}
            if not ok:
                bad = (market, reason, diag)
                break
        self._edge_alert(
            "model_pointer_invalid", bad is not None, "WARN",
            f"{bad[0]} latest model pointer rejected: {bad[1]}" if bad else "",
            {"market": bad[0], "reason": bad[1], "diag": bad[2]} if bad else None,
        )

    def _check_no_scan_progress(self) -> None:
        if not HEARTBEAT_PATH.exists():
            return
        try:
            payload = json.loads(HEARTBEAT_PATH.read_text(encoding="utf-8"))
            cycle_count = int(payload.get("cycle_count") or 0)
        except Exception:
            return
        if self._state.last_cycle_count is None:
            self._state.last_cycle_count = cycle_count
            return
        if cycle_count != self._state.last_cycle_count:
            self._state.last_cycle_count = cycle_count
            self._first_seen.pop("no_scan_progress", None)
            return
        first = self._first_seen.setdefault("no_scan_progress", time.time())
        if time.time() - first >= NO_SCAN_PROGRESS_SEC:
            self._alert(
                "no_scan_progress", "WARN",
                "heartbeat is fresh but cycle_count has not advanced for "
                f"> {NO_SCAN_PROGRESS_SEC}s",
                {"cycle_count": cycle_count},
            )

    @staticmethod
    def _live_open_keys(positions_path: Path = POSITIONS_PATH) -> Optional[set[tuple[str, str, str]]]:
        return live_open_position_keys(positions_path)

    def _check_stuck_open_positions(self) -> None:
        """Alert (+ optional auto-close) on orphan warehouse OPEN rows.

        Tier-geometry holds can keep real PAPER positions open past
        ``STUCK_OPEN_HOURS`` (up to ~72h). Those are intentional, not stuck.
        The leak this check exists for is a warehouse row left OPEN after the
        position closed in ``positions.json`` (trade_id lookup miss).

        Blueprint Phase 1: when ``WAREHOUSE_ORPHAN_AUTO_CLOSE`` is on (default
        under PAPER), close orphans with exit_reason=reconcile_flat (zero PnL,
        null exit_px) — learning book only, never exchange orders.
        """
        orphans = orphan_open_trade_rows(
            self._warehouse_path,
            older_than_hours=STUCK_OPEN_HOURS,
            positions_path=POSITIONS_PATH,
            limit=50,
        )
        if orphans is None:
            # Unreadable positions.json — skip rather than false-positive.
            return
        closed_n = 0
        try:
            from core.warehouse_reconcile import (
                reconcile_warehouse_orphans,
                warehouse_orphan_auto_close_enabled,
            )

            if orphans and warehouse_orphan_auto_close_enabled():
                result = reconcile_warehouse_orphans(
                    self._warehouse_path,
                    positions_path=POSITIONS_PATH,
                    older_than_hours=STUCK_OPEN_HOURS,
                    limit=50,
                )
                closed_n = len(result.get("closed") or [])
                # Re-read remaining orphans after auto-close for the alert.
                orphans = orphan_open_trade_rows(
                    self._warehouse_path,
                    older_than_hours=STUCK_OPEN_HOURS,
                    positions_path=POSITIONS_PATH,
                    limit=50,
                ) or []
        except Exception as _re:
            logger.debug(f"[Watchdog] orphan auto-close skipped: {_re}")
        sample = orphans[:10]
        msg = ""
        if closed_n:
            msg = (
                f"auto-closed {closed_n} orphan OPEN warehouse row(s) "
                f"(exit_reason=reconcile_flat)"
            )
        if orphans:
            extra = (
                f"{len(orphans)} orphan OPEN warehouse row(s) older than "
                f"{STUCK_OPEN_HOURS}h (not in positions.json) — learning "
                f"analytics under-count; run "
                f"`python scripts/backfill_warehouse_closes.py --commit`"
            )
            msg = f"{msg}; {extra}" if msg else extra
        self._edge_alert(
            "stuck_open_positions",
            bool(orphans) or closed_n > 0,
            "WARN",
            msg,
            {
                "orphan_count": len(orphans),
                "auto_closed": closed_n,
                "sample": [
                    {
                        "id": int(r[0]),
                        "exchange": str(r[1]),
                        "symbol": str(r[2]),
                        "side": str(r[3]),
                        "age_h": round((time.time() - float(r[4])) / 3600, 2),
                    }
                    for r in sample
                ],
            } if (orphans or closed_n) else None,
        )

    def _check_forward_feeds(self) -> None:
        try:
            from core.feed_health import read_forward_feed_status, unhealthy_forward_feeds
        except Exception:
            return
        records = read_forward_feed_status(Path("."))
        bad = unhealthy_forward_feeds(records)
        self._edge_alert(
            "forward_feeds_stale", bool(bad), "WARN",
            f"forward feed status unhealthy: {', '.join(sorted(bad))}",
            {
                "feeds": [
                    {
                        "name": r.get("name"),
                        "connected": r.get("connected"),
                        "age_sec": r.get("age_sec"),
                        "fresh": r.get("fresh"),
                        "error": r.get("error"),
                    }
                    for r in records
                ]
            },
            grace_sec=FEED_GRACE_SEC,
        )
        # Blueprint Phase 1: soft-stale latch blocks NEW entries only.
        try:
            from core.soft_stale_latch import (
                SOFT_STALE_LATCH_PATH,
                clear_soft_stale_latch,
                set_soft_stale_latch,
                soft_stale_entries_blocked,
            )

            if bad:
                set_soft_stale_latch(
                    reason="forward_feeds_stale",
                    detail={"feeds": list(sorted(bad))},
                )
            elif soft_stale_entries_blocked():
                try:
                    doc = json.loads(SOFT_STALE_LATCH_PATH.read_text(encoding="utf-8"))
                    if str(doc.get("reason") or "").startswith("forward_feeds"):
                        clear_soft_stale_latch()
                except Exception:
                    pass
        except Exception as _sse:
            logger.debug(f"[Watchdog] soft-stale latch skip: {_sse}")

    def _check_latest_audit(self) -> None:
        if not REPORTS_DIR.exists():
            return
        reports = sorted(
            REPORTS_DIR.glob("trading_system_audit_*.json"),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
        if not reports:
            return
        try:
            payload = json.loads(reports[0].read_text(encoding="utf-8"))
        except Exception:
            return
        verdict = str(payload.get("verdict") or "")
        # NOT_READY is the honest steady-state under NO_EDGE; alert only on the
        # transition into it (edge-triggered), not every cooldown thereafter.
        self._edge_alert(
            "audit_not_ready", verdict == "NOT_READY", "WARN",
            "latest trading system audit verdict is NOT_READY",
            {"report": str(reports[0]), "verdict": verdict},
        )
