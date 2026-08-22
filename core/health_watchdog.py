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
     positions.json shows zero OPENs in the last MODEL_STARVE_HOURS hours
     while RiskManager.daily_pnl > -2%. INFO — unexplained idleness.
     Suppressed when EconGate=strict OR ENTRY_POLICY is SHADOW_ONLY /
     PROTECT_ONLY / OBSERVATION (zero OPENs is the intended latch).
     Deliberate measured vetoes (band_regime_filter, daily open budget)
     are silent only for DELIBERATE_BLOCK_MAX_HOURS.

  7. stuck_open_positions
     Warehouse trades stuck at status='OPEN' older than STUCK_OPEN_HOURS
     that are NOT still open in positions.json (true orphans after a
     trade_id close miss). Live holds past 24h (tier-geometry) are silent.
"""

from __future__ import annotations

import json
import re
import sqlite3
import statistics
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
# Bot logs, scanned by the starvation check to name the ACTUAL blocker before
# alerting. A deliberate measured veto is not a malfunction (2026-08-15).
LOG_DIR                  = Path("logs")
# Entry blocks that are the system WORKING AS DESIGNED: a measured filter
# refusing conditions it was calibrated to refuse. Idleness attributable to
# these is expected, not starvation, and must not page the operator hourly.
DELIBERATE_ENTRY_BLOCKS  = (
    "band_regime_filter",           # ADX>30 / btc_vol<0.7 toxic-regime veto
    "accband_research_daily_open_budget",  # the 12-opens/UTC-day research cap
    # 2026-08-19: the universe chop veto (Kaufman ER < floor). Added AFTER its
    # ER was made venue-independent (UTC-day grouping — bitget's shifted "1d"
    # buckets had produced hours-stale false chop). A correctly computed chop
    # refusal is the system working; the 24h cap below still bounds it.
    "chop",
)
# ...but only for so long. A "deliberate" rail blocking CONTINUOUSLY for days is
# a symptom, not a rail. The line above was added 2026-08-15 so a WORKING veto
# would stop paging hourly; it also silenced a BROKEN one for 80h (the veto was
# computing its baseline over 58 days instead of the spec's 30). Past this many
# hours since the last OPEN, sustained single-reason idleness alerts regardless
# of classification. Idle duration comes from positions.json, so the bound
# survives a restart.
DELIBERATE_BLOCK_MAX_HOURS = 24
# ── Gate-value verification (2026-08-17) ────────────────────────────────────
# The 80h outage was invisible because only ONE computation of the band-regime
# ratio existed, so a wrong one looked exactly like a right one. The watchdog
# now recomputes the SPEC quantity independently and alerts when the two
# disagree. The duplication is the point: a second implementation is the only
# thing that can catch the first one drifting from its pre-registration.
GATE_VALUE_TOLERANCE     = 0.02          # abs ratio delta before it's drift
BTC_VOL_SPEC_WINDOW_SEC  = 30 * 24 * 3600   # config/gates.py: "30d median"
BTC_VOL_APPEND_SEC       = 3600             # BTC_VOL_PAUSE append interval
# Persists per-check last-alert timestamps so cooldowns survive a restart
# (otherwise every sticky WARN re-emails on each bounce). Runtime state.
COOLDOWN_STATE_PATH      = Path("data/watchdog_cooldown_state.json")

# Per-check cooldowns (seconds) — re-fire once after the cooldown elapses
# Sentinel for "idle, and NO typed record says why". This is not a block reason;
# it is the ABSENCE of one, and it is the most serious of the three states the
# starvation check can report. Until 2026-08-20 this condition returned None and
# rendered as the INFO line "no identifiable entry block in the logs" -- the same
# severity as a healthy deliberate block, which is precisely how an unexplained
# idle stayed invisible. It now alerts at WARNING under its own key.
ENTRY_BLOCK_INSTRUMENTATION_GAP = "instrumentation_gap"

COOLDOWN_SEC = {
    # Slower than the 1h model_gate_starving cadence: this fires while something
    # is genuinely unexplained, and paging hourly re-trains the operator to
    # ignore the channel (the 2026-08-15 numbness this file exists to prevent).
    "model_gate_instrumentation_gap": 6 * 60 * 60,
    "heartbeat_stale":       30 * 60,
    "carry_heartbeat_stale": 60 * 60,
    "carry_recovery_active": 60 * 60,
    "carry_sample_milestone": 60,  # milestones dedupe via _announced_milestones
    "spec12_review_required": 60 * 60,
    "exchange_halted":       30 * 60,
    "sl_placement_failed":   30 * 60,
    "loss_streak":           60 * 60,
    "model_gate_starving":   60 * 60,
    # Both are edge-triggered (once per episode); these bound the re-fire
    # after a condition clears and returns, and keep the key off the
    # rsplit family-fallback, which would otherwise resolve
    # gate_value_btc_vol_stale to a "gate_value_btc_vol" that has no entry
    # either and silently take the 30-min generic default.
    "gate_value_btc_vol":       60 * 60,
    "gate_value_btc_vol_stale": 60 * 60,
    # Idle past DELIBERATE_BLOCK_MAX_HOURS nudges 4x/day, not hourly — an
    # hourly WARNING for a multi-day deliberate idle re-trains the operator
    # to ignore the channel (the 2026-08-15 numbness, other direction).
    "model_gate_starving_capped": 6 * 60 * 60,
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


def expected_idle_no_new_exposure() -> bool:
    """True when new directional OPENs are latched off by design.

    SHADOW_ONLY / PROTECT_ONLY / OBSERVATION are owner cash/safety
    postures. Zero OPENs is success, not model_gate_starving. Unlike
    DELIBERATE_ENTRY_BLOCKS this is NOT 24h-capped — the latch can
    stay on for weeks. Import failure does not suppress (alert).
    """
    try:
        from config import ENTRY_POLICY, OPERATING_MODE
    except Exception:
        return False
    if str(OPERATING_MODE or "").strip().upper() == "OBSERVATION":
        return True
    return str(ENTRY_POLICY or "").strip().upper() in (
        "SHADOW_ONLY",
        "PROTECT_ONLY",
    )


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
            self._check_gate_value_drift,
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

    @staticmethod
    def _expected_idle_no_new_exposure() -> bool:
        """True when new directional OPENs are latched off by design.

        SHADOW_ONLY / PROTECT_ONLY / OBSERVATION are owner cash/safety
        postures. Zero OPENs is success, not model_gate_starving. Unlike
        DELIBERATE_ENTRY_BLOCKS this is NOT 24h-capped — the latch can
        stay on for weeks. Import failure does not suppress (alert).
        """
        return expected_idle_no_new_exposure()

    def _entry_block_from_warehouse(self) -> tuple:
        """(reason, hits) from the TYPED decision_events store, or (None, 0).

        Authoritative, tried first. The log scan below can only see gates that
        emit the literal token ``BLOCKED``, and most do not:

          * ``BtcVolPause`` logs ``[BtcVolPause] WAIT -- ...`` at INFO
            (core/engine/entry_exec.py:301)
          * ``SoftStale`` logs NOTHING — the taken branch at
            core/engine/entry_exec.py:92-95 has no logger call at all
          * an upstream drought emits no per-candidate line by construction

        Measured 2026-08-20: 139 OPEN proposals, 136 vetoed by btc_vol_pause,
        3 silent, 0 executed -- while the log scan matched 0 of the 70 lines
        containing "BLOCKED" (all 70 from an unrelated SelfHeal job). The
        warehouse had the answer the whole time: 11/11 rows in the window named
        ``btc_vol_pause``. Reuses the purpose-built reader rather than
        re-implementing the SQL, so the ISO cutoff format stays consistent.
        """
        try:
            from mcp_server.warehouse_reader import open_funnel_status
            status = open_funnel_status(
                lookback_hours=float(MODEL_STARVE_HOURS))
        except Exception:
            return None, 0
        if not isinstance(status, dict) or not status.get("open_attempts"):
            return None, 0
        # Key names are "top_reject_reasons"/"drought_status" -- NOT "top"/
        # "drought". Guessing them cost a silent (None, 0) on 2026-08-20: the
        # funnel reported 18 open_attempts while this function returned "no
        # answer", which would have rendered as an instrumentation_gap while the
        # blocker was in fact known. Pinned by test below.
        top = status.get("top_reject_reasons") or []
        if not top:
            return None, 0
        best = top[0]
        reason = str(best.get("reason") or "").strip()
        if not reason:
            return None, 0
        # decision_events holds one row per terminal decision, so this is a true
        # per-candidate count, not the polling-inflated log-line count.
        self._dominant_block_symbols = 0
        return reason, int(best.get("count") or 0)

    def _entry_block_from_candidates(self) -> tuple:
        """(reason_family, hits) from the UPSTREAM `candidates` table, or (None, 0).

        `decision_events` only holds TERMINAL OPEN attempts. When the funnel dies
        at SCORING -- before any candidate ever reaches entry_exec -- that table
        is legitimately empty and `_entry_block_from_warehouse` correctly returns
        nothing. The answer is one table away.

        Measured 2026-08-21, the morning this was written: 14 cycles x 55 coins,
        "No actions this cycle" every time, watchdog reporting "no identifiable
        entry block" -- while `candidates` held 6,884 rows for that window:
            regime_toxic_trend(4h_adx=36..47>30)   4,444  (64.6%)
            analysis_only_accband_scope            2,055  (29.9%)
            ALLOW                                    121   (1.8%)
        ADX 36-47 against a pre-registered ceiling of 30: the market was in a
        hard trend and the filter was refusing entries exactly as designed.

        TWO TRAPS, both hit for real during that diagnosis:

        1. `candidates.ts` is REAL (unix epoch), NOT an ISO string. Comparing it
           against an ISO cutoff in SQLite matches ZERO rows and DOES NOT RAISE
           (numeric affinity sorts below text). That produced a false "0 rows in
           6h" reading on a table holding 6,884. Always compare epoch-to-epoch.
        2. Reasons must be grouped into FAMILIES. Ungrouped, those 4,444 rows
           split across ~12 distinct ADX values, so the true 64.6% dominant
           block renders as a 434-row also-ran beneath a 2,055-row constant.
        """
        try:
            import sqlite3
            from mcp_server.warehouse_reader import DEFAULT_DB_PATH
            cutoff = time.time() - MODEL_STARVE_HOURS * 3600  # EPOCH, see trap 1
            conn = sqlite3.connect(str(DEFAULT_DB_PATH))
            try:
                rows = conn.execute(
                    "SELECT skip_reason, COUNT(*) FROM candidates "
                    "WHERE ts >= ? AND COALESCE(decision,'') != 'ALLOW' "
                    "AND COALESCE(skip_reason,'') != '' "
                    "GROUP BY skip_reason",
                    (cutoff,),
                ).fetchall()
            finally:
                conn.close()
        except Exception:
            return None, 0
        if not rows:
            return None, 0
        fam: dict = {}
        for reason, count in rows:
            key = re.sub(r"\d+", "N", str(reason))  # see trap 2
            fam[key] = fam.get(key, 0) + int(count or 0)
        if not fam:
            return None, 0
        best = max(fam.items(), key=lambda kv: kv[1])
        self._dominant_block_symbols = 0
        return best[0], best[1]

    def _dominant_entry_block_reason(self) -> tuple:
        """(reason, hits) for the block that stopped the most entries.

        Order: typed warehouse rows, then the legacy log scan, then the
        ``ENTRY_BLOCK_INSTRUMENTATION_GAP`` sentinel. NEVER returns None -- an
        unexplained idle is its own named state that alerts at WARNING, because
        "nothing was blocked" and "we cannot tell what blocked it" are opposite
        conditions and must not collapse into one INFO message. They did until
        2026-08-20, which is how this class of failure stayed quiet.
        """
        # UPSTREAM first, and ALWAYS stashed even when a downstream reason wins.
        # The two counts are DIFFERENT UNITS and must not be compared as if they
        # were: `candidates` counts per-coin-per-cycle evaluations, while
        # decision_events counts terminal OPEN attempts. Measured 2026-08-21:
        # 4,459 upstream (regime_toxic_trend, ADX 36-47 vs a ceiling of 30) vs
        # 119 downstream (btc_vol_pause). Reporting only the terminal reason
        # hides that ~97% never reached the gate at all; reporting only the
        # larger number would compare apples to oranges. So the terminal reason
        # stays primary (it is the last thing that stopped a real candidate) and
        # the upstream reason travels with it in the alert context.
        up_reason, up_hits = self._entry_block_from_candidates()
        self._upstream_block = (up_reason, up_hits) if up_reason else None

        reason, hits = self._entry_block_from_warehouse()
        if reason:
            return reason, hits
        if up_reason:
            return up_reason, up_hits
        reason, hits = self._entry_block_from_logs()
        if reason:
            return reason, hits
        self._dominant_block_symbols = 0
        return ENTRY_BLOCK_INSTRUMENTATION_GAP, 0

    def _entry_block_from_logs(self) -> tuple:
        """(reason, hits) by scanning today's bot log, or (None, 0).

        SECONDARY source, kept as a fallback for gates that write a log line but
        no warehouse row. Structurally fragile (see the failure class documented
        on _entry_block_from_warehouse), so it must never be the only source and
        its (None, 0) must never reach the caller as an all-clear.
        """
        try:
            log = LOG_DIR / f"bot_{datetime.now(timezone.utc).strftime('%Y-%m-%d')}.log"
            if not log.exists():                       # fall back to the newest
                logs = sorted(LOG_DIR.glob("bot_*.log"))
                if not logs:
                    return None, 0
                log = logs[-1]
            counts: dict = {}
            symbols: dict = {}
            cutoff = time.time() - MODEL_STARVE_HOURS * 3600
            with log.open("r", encoding="utf-8", errors="ignore") as fh:
                for line in fh:
                    if "BLOCKED" not in line:
                        continue
                    stamp = _decision_ts_epoch(line[:19].strip().replace(" ", "T"))
                    if stamp is not None and stamp < cutoff:
                        continue
                    # [EntryPolicy] BLOCKED venue:symbol id: reason  — colon+space,
                    # not an emdash. The emdash regex below would miss it and
                    # report leftover universe_filter thin_book as dominant.
                    m = re.search(
                        r"\[EntryPolicy\] BLOCKED\b.+:\s+([a-z][a-z0-9_]*)\s*$",
                        line.rstrip(),
                    )
                    if m:
                        counts[m.group(1)] = counts.get(m.group(1), 0) + 1
                        continue
                    m = re.search(r"BLOCKED[^\n]*?[—-]\s*([a-z_]+)", line)
                    if m:
                        reason = m.group(1)
                        counts[reason] = counts.get(reason, 0) + 1
                        ms = re.search(r"BLOCKED[^:]*:\s+([A-Z0-9/]+)", line)
                        if ms:
                            symbols.setdefault(reason, set()).add(ms.group(1))
            if not counts:
                self._dominant_block_symbols = 0
                return None, 0
            best = max(counts.items(), key=lambda kv: kv[1])
            # Hits count per-cycle RE-CHECKS of the same symbols ("polling
            # inflation": 2026-08-19's "315 hits" was 3 symbols). Expose the
            # distinct-symbol count so alerts cannot dramatise it.
            self._dominant_block_symbols = len(symbols.get(best[0], ()))
            return best[0], best[1]
        except Exception:
            return None, 0

    def _verify_btc_vol_ratio(self, reported=None, buf=None, atr=None) -> bool:
        """Independently recompute the band-regime veto's ratio. True = agrees.

        `reported` is what BtcVolPause.current_ratio() returned, `buf` its
        baseline ([[epoch_sec, atr_pct], ...]) and `atr` the live BTC 1h ATR%.
        This recomputes ATR / median(last 30d) — the quantity config/gates.py
        and screen 13 actually specify — and alerts if the gate's own answer
        differs, or if the baseline has gone stale.

        Fail-OPEN and quiet on missing data: an empty buffer is warmup, not a
        defect. Verification only ever alerts; it never changes the gate.
        """
        try:
            from config import BTC_VOL_PAUSE as _spec
        except Exception:
            return True                   # no spec to verify against

        now = time.time()
        cutoff = now - BTC_VOL_SPEC_WINDOW_SEC
        newest, dropped, rows = None, 0, []
        for row in (buf or []):
            try:
                ts, val = float(row[0]), float(row[1])
            except (TypeError, ValueError, IndexError, KeyError):
                dropped += 1
                continue
            rows.append((ts, val))
            if newest is None or ts > newest:
                newest = ts
        if newest is None or dropped:
            # An empty buffer is warmup. Unparseable rows mean we cannot
            # faithfully reproduce what the gate saw, and a verifier that
            # guesses is worse than one that abstains. Neither is a defect.
            self._edge_alert("gate_value_btc_vol", False, "INFO", "")
            self._edge_alert("gate_value_btc_vol_stale", False, "INFO", "")
            return True

        age_min = (now - newest) / 60.0
        append_sec = float(_spec.get("append_min_interval_sec", BTC_VOL_APPEND_SEC))
        is_stale = (now - newest) > append_sec * 2
        # Edge-triggered, NOT _alert: 13.2% of this buffer's historical gaps
        # exceed the threshold and the worst is ~4 days. On a plain cooldown
        # that one gap alone is ~190 emails — reintroducing exactly the
        # numbness the 2026-08-15 suppression was meant to prevent.
        self._edge_alert(
            "gate_value_btc_vol_stale", is_stale, "WARNING",
            f"BTC vol baseline is stale: newest sample {age_min:.0f} min old "
            f"against a {append_sec / 60:.0f} min append interval. The "
            f"band-regime veto is gating entries on a numerator that may not "
            f"reflect the live tape.",
            {"newest_age_min": round(age_min, 1), "samples": len(rows)},
        )
        if is_stale:
            return False

        # Mirror current_ratio() EXACTLY — same window filter (no extra
        # positivity test), same min_samples floor, same non-positive-median
        # guard. A verifier that computes a slightly different function
        # reports drift that isn't there, and a noisy verification channel
        # gets muted like any other.
        samples = [a for (ts, a) in rows if ts >= cutoff]
        expected = None
        if len(samples) >= int(_spec.get("min_samples", 24)) and atr is not None:
            med = statistics.median(samples)
            if med > 0:
                expected = float(atr) / med

        # Both sides agree there is no computable ratio -> nothing to verify.
        if expected is None and reported is None:
            self._edge_alert("gate_value_btc_vol", False, "INFO", "")
            return True
        if expected is None or reported is None:
            self._edge_alert(
                "gate_value_btc_vol", True, "WARNING",
                f"Band-regime ratio existence mismatch: gate reported "
                f"{reported!r} but the 30d spec recomputation gives {expected!r} "
                f"({len(samples)} in-window samples).",
                {"reported": reported, "expected": expected,
                 "samples_30d": len(samples)},
            )
            return False

        delta = abs(float(reported) - expected)
        if delta > GATE_VALUE_TOLERANCE:
            self._edge_alert(
                "gate_value_btc_vol", True, "WARNING",
                f"Band-regime ratio DRIFT: the gate reports {float(reported):.4f} "
                f"but the 30d spec (config/gates.py) recomputes to {expected:.4f} "
                f"(delta {delta:.4f}). One of the two is wrong — the veto's "
                f"0.70 threshold sits between them often enough to idle the bot.",
                {"reported": round(float(reported), 4),
                 "expected": round(expected, 4), "delta": round(delta, 4),
                 "samples_30d": len(samples), "atr": atr},
            )
            return False

        self._edge_alert("gate_value_btc_vol", False, "INFO", "")
        return True

    def _check_gate_value_drift(self) -> None:
        """Cross-check the band-regime veto against its own specification."""
        from config import BTC_VOL_PAUSE as _spec
        from core.btc_vol_pause import extract_btc_atr_pct

        bvp = getattr(self._engine, "_btc_vol_pause", None)
        if bvp is None:
            return
        cache = getattr(getattr(self._engine, "mcp_brain", None),
                        "_indicator_cache", None)
        self._verify_btc_vol_ratio(
            reported=bvp.current_ratio(cache),
            buf=list(getattr(bvp, "_buf", None) or []),
            # Read the timeframe from the spec, never hardcode it: a config
            # change would otherwise have the two sides comparing different
            # series and alerting forever.
            atr=extract_btc_atr_pct(cache, _spec.get("timeframe", "1h")),
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
        if (self._expected_idle_under_strict_econ_gate()
                or self._expected_idle_no_new_exposure()):
            # Re-arm so a later flip to paper_fallback / APPROVED_PAPER
            # can alert again.
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
        newest_open = 0.0
        for e in entries:
            if not isinstance(e, dict):
                continue
            try:
                opened = float(e.get("open_time"))
            except (TypeError, ValueError):
                continue          # no usable open_time -> cannot count as recent
            if opened > newest_open:
                newest_open = opened
            if opened >= cutoff:
                opens_recent += 1
        if opens_recent == 0:
            # 2026-08-15: zero opens is not self-evidently a malfunction. When a
            # measured filter is deliberately refusing the tape, alerting hourly
            # trains the operator to ignore this channel — the same numbness that
            # made a 48h latch starvation expensive to notice. Only page when the
            # idleness is UNEXPLAINED.
            reason, hits = self._dominant_entry_block_reason()
            idle_h = ((time.time() - newest_open) / 3600.0
                      if newest_open > 0 else None)
            if reason in DELIBERATE_ENTRY_BLOCKS:
                # 2026-08-17: suppression is now time-bounded. Silencing a rail
                # that had been blocking for 80h is what let a MISCOMPUTED
                # baseline pass for a working one. We can only justify staying
                # quiet while the idleness is short enough to be ordinary — and
                # only when positions.json lets us bound it at all.
                if idle_h is not None and idle_h < DELIBERATE_BLOCK_MAX_HOURS:
                    self._edge_alert("model_gate_starving", False, "INFO", "")
                    return
                detail = (
                    f"deliberate block '{reason}' ({hits} hits) has now held for "
                    f"{idle_h:.0f}h, past the {DELIBERATE_BLOCK_MAX_HOURS}h "
                    f"suppression cap — verify the gate is computing its "
                    f"threshold correctly, not just that it is firing"
                    if idle_h is not None else
                    f"deliberate block '{reason}' ({hits} hits) and no OPEN has "
                    f"ever been recorded, so the idle duration cannot be bounded"
                )
                self._alert(
                    "model_gate_starving_capped", "WARNING",
                    f"No OPEN actions in the last {MODEL_STARVE_HOURS}h despite "
                    f"non-drawdown state — {detail}.",
                    {"opens_recent": opens_recent,
                     "window_hours": MODEL_STARVE_HOURS,
                     "dominant_block": reason, "block_hits": hits,
                     "block_symbols": getattr(self, "_dominant_block_symbols", None),
                     "idle_hours": round(idle_h, 1) if idle_h is not None else None,
                     "suppression_cap_hours": DELIBERATE_BLOCK_MAX_HOURS},
                )
                return
            if reason == ENTRY_BLOCK_INSTRUMENTATION_GAP:
                # Neither decision_events NOR the log can name a blocker. That is
                # NOT an all-clear -- it means the bot is idle and the system
                # cannot say why. Escalate; never let this share the INFO
                # cadence of a healthy, well-understood deliberate block.
                self._alert(
                    "model_gate_instrumentation_gap", "WARNING",
                    f"No OPEN actions in the last {MODEL_STARVE_HOURS}h and NO "
                    f"typed record of why — decision_events has no terminal OPEN "
                    f"row in the window and the log names no block. The bot is "
                    f"idle for an UNEXPLAINED reason: either the funnel died "
                    f"upstream of the entry gate (no candidate was ever "
                    f"proposed) or the instrumentation is broken. This is an "
                    f"observability failure, not a clean idle.",
                    {"opens_recent": opens_recent,
                     "window_hours": MODEL_STARVE_HOURS,
                     "dominant_block": None,
                     "state": ENTRY_BLOCK_INSTRUMENTATION_GAP},
                )
                return
            _syms = getattr(self, "_dominant_block_symbols", None)
            detail = (
                f"dominant block: {reason} ({hits} hits across {_syms} "
                f"symbol(s); hits count per-cycle re-checks)"
                if reason and _syms
                else f"dominant block: {reason} ({hits} hits)")
            self._alert(
                "model_gate_starving", "INFO",
                f"No OPEN actions in the last {MODEL_STARVE_HOURS}h despite "
                f"non-drawdown state — {detail}.",
                {"opens_recent": opens_recent,
                 "window_hours": MODEL_STARVE_HOURS,
                 "dominant_block": reason, "block_hits": hits,
                 # Different unit from block_hits: per-coin-per-cycle scoring
                 # eliminations, vs terminal OPEN attempts. Carried so the
                 # operator can see the funnel died upstream (2026-08-21:
                 # 4,459 upstream vs 119 terminal -- ~97% never reached the gate).
                 "upstream_block": getattr(self, "_upstream_block", None)},
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
            from core.feed_health import (
                entry_gating_feeds,
                read_forward_feed_status,
                unhealthy_forward_feeds,
            )
        except Exception:
            return
        records = read_forward_feed_status(Path("."))
        bad = unhealthy_forward_feeds(records)
        # 2026-08-22: ALERT on any unhealthy feed (unchanged), but only BLOCK
        # entries on feeds the live path depends on. `skew` is research-only and
        # has never connected -- it alone refused 219 of 465 entry decisions in
        # a day. Alerting stays loud, so ungating is not silencing.
        gating_bad = entry_gating_feeds(records)
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
        # 2026-08-22: a SECOND alert keyed on the gating subset. `_edge_alert`
        # is a membership test on last_alert, so it fires once per episode --
        # meaning a `forward_feeds_stale` alert already latched by a
        # non-gating feed (skew) would SHADOW a later l2/tv/liquidations death:
        # entries would be correctly blocked while no alert ever named the feed
        # responsible. Purely additive; the alert above is unchanged.
        self._edge_alert(
            "entry_gating_feeds_stale", bool(gating_bad), "WARN",
            f"entry-gating feed unhealthy — NEW ENTRIES BLOCKED: "
            f"{', '.join(sorted(gating_bad))}",
            {"gating_feeds": sorted(gating_bad)},
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

            if gating_bad:
                # 2026-08-22: do NOT clobber a latch another subsystem owns.
                # Observed live: an `exchange_outage:binance` latch (fails=9)
                # was overwritten by `forward_feeds_stale/[skew]` 4 minutes
                # later. Because the clear branch below matches
                # startswith("forward_feeds"), the next skew recovery would
                # then DELETE a block that a live exchange outage was holding,
                # and re-arming it costs several more consecutive failures.
                # Feed staleness must not be able to overwrite -- or launder --
                # a different subsystem's reason for refusing entries.
                _owner = ""
                try:
                    if soft_stale_entries_blocked():
                        _owner = str(
                            json.loads(
                                SOFT_STALE_LATCH_PATH.read_text(encoding="utf-8")
                            ).get("reason")
                            or ""
                        )
                except Exception:
                    _owner = ""
                if _owner and not _owner.startswith("forward_feeds"):
                    logger.debug(
                        f"[Watchdog] soft-stale already held by {_owner!r}; "
                        f"not overwriting with forward_feeds_stale"
                    )
                else:
                    set_soft_stale_latch(
                        reason="forward_feeds_stale",
                        detail={"feeds": list(sorted(gating_bad))},
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
