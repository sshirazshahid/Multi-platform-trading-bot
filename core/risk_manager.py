"""
core/risk_manager.py — Risk Management with Smart Recovery + Correlation Awareness

Features:
  - Dynamic SL/TP based on ATR percentile (wider in high-vol, tighter in low-vol)
  - Correlation-aware position sizing (reduces size for correlated assets)
  - Smart drawdown recovery (auto-resume when conditions improve)
  - Regime-adaptive leverage (reduce leverage in volatile regimes)
  - Peak balance resets on new session to avoid stale peaks
"""

import json
import math
import os
import threading
import time as _time
from datetime import date, datetime, timezone
from pathlib import Path

from loguru import logger

from config import RISK


def _utc_today() -> date:
    """UTC calendar date. Daily rollover / loss-breaker / trade-cap boundaries align with
    exchange & funding settlement (UTC), matching this module's design comments
    (audit 2026-06-03: code had drifted to local date()). Single source of truth."""
    return datetime.now(timezone.utc).date()


def exposure_breached(open_positions, new_notional: float, equity: float,
                      max_pct: float) -> bool:
    """CLAUDE.md §2 (R2) helper: would adding ``new_notional`` (gross market notional,
    leverage included) push total open GROSS NOTIONAL over ``max_pct`` of ``equity``?

    Pure + side-effect-free so it's unit-testable without the engine.

    Fail-CLOSED on a bad EQUITY reading (2026 A2 audit): equity None or <= 0 means
    we cannot prove we are under the cap, so treat it as BREACHED (return True) — a
    hard risk rail must never open the gate on a glitch. A bad/disabled cap
    (max_pct None or <= 0) still fails OPEN (the cap is intentionally off).
    """
    if max_pct is None or max_pct <= 0:
        return False
    if equity is None or equity <= 0:
        return True
    gross = float(new_notional or 0.0)
    for p in open_positions or []:
        try:
            gross += abs(float(getattr(p, "size", 0) or 0) * float(getattr(p, "entry_price", 0) or 0))
        except (TypeError, ValueError):
            continue
    return (gross / equity * 100.0) > float(max_pct)


def aggregate_open_risk_breached(
    open_positions,
    new_notional: float,
    new_stop_frac: float,
    equity: float,
    max_risk_frac: float,
    stressed_exit_cost_frac: float = 0.0,
) -> bool:
    """Return whether stop loss plus stressed exit costs exceed the risk budget.

    ``new_notional`` and existing ``size * entry_price`` values are gross
    market notionals. Unknown equity or an unprotected futures position fails
    closed because the aggregate risk cannot be bounded.
    """
    try:
        cap = float(max_risk_frac)
        equity = float(equity)
        new_notional = abs(float(new_notional or 0.0))
        new_stop_frac = float(new_stop_frac or 0.0)
        exit_cost = max(0.0, float(stressed_exit_cost_frac or 0.0))
    except (TypeError, ValueError):
        return True
    if cap <= 0:
        return False
    if equity <= 0 or (new_notional > 0 and new_stop_frac <= 0):
        return True

    total_risk = new_notional * (new_stop_frac + exit_cost)
    for position in open_positions or ():
        getter = position.get if isinstance(position, dict) else None

        def field(name, default=None):
            return getter(name, default) if getter else getattr(position, name, default)

        market_type = str(field("market_type", "") or "").lower()
        if market_type and market_type != "futures":
            continue
        try:
            entry = float(field("entry_price", 0.0) or 0.0)
            size = abs(float(field("size", 0.0) or 0.0))
        except (TypeError, ValueError):
            return True
        if entry <= 0 or size <= 0:
            return True
        stop = None
        stop_fn = field("entry_risk_stop")
        if callable(stop_fn):
            try:
                stop = stop_fn()
            except Exception:
                return True
        if not stop:
            stop = field("stop_loss", 0.0)
        try:
            stop = float(stop or 0.0)
        except (TypeError, ValueError):
            return True
        if stop <= 0:
            return True
        total_risk += (size * entry) * (abs(stop - entry) / entry + exit_cost)
    return total_risk > equity * cap

# ------------------------------------------------------------------
# Spec §12 pause policy (learning-first rebuild, 2026-04-14)
# 2 consecutive losses on a symbol → pause that symbol 6h.
# 3 consecutive losses in a strategy family → pause that family 12h.
# 5 global consecutive losses → force OBSERVATION + review flag.
# Outlier loss above MAX_LOSS_PER_TRADE_USD → immediate review flag.
# ------------------------------------------------------------------
SPEC_SYMBOL_LOSSES_TO_PAUSE  = 2
SPEC_SYMBOL_PAUSE_HOURS      = 6
SPEC_FAMILY_LOSSES_TO_PAUSE  = 3
SPEC_FAMILY_PAUSE_HOURS      = 12
SPEC_GLOBAL_LOSSES_TO_REVIEW = 5
_REVIEW_FLAG_PATH = Path("data/review_required.json")
_INCIDENT_LATCH_PATH = Path("data/risk_incident_latch.json")
_INCIDENT_HISTORY_PATH = Path("data/risk_incident_history.jsonl")
RISK_STATE_PATH = Path("data/risk_state.json")

# 2026-04-27: §12 streaks should reflect strategy outcomes, not infrastructure
# artefacts. STALE / AGE_LIMIT / ghost_force_close / sl_placement_failed and
# the reconcile_closed_pnl import paths all close at near-breakeven or on
# operational triggers; counting them as "losses" caused 5 consecutive
# −$0.09 STALE exits to false-trip the global halt and pause claude_portfolio
# for 12h. Treat scratches and these reasons as null entries.
_SPEC12_NEUTRAL_REASONS = frozenset({
    "STALE",
    "AGE_LIMIT",
    "ghost_force_close",
    "sl_placement_failed",
    "reconciled_from_exchange",
    "reconciled_no_context",
    # 2026-05-24 — Infrastructure exits added after the audit found these
    # were extending the 5-consec global halt streak. Safe only because
    # Commit 1 (position_tracker._classify_conditional_fill) reclassifies
    # real exchange-side SL fills to "stop_loss" first. The residual
    # ghost_* are truly-unclassified events (no exchange-placed
    # conditional, or fill outside the 50bps SL/TP band).
    # sl_crossed_* fire when price moved between order placement and SL
    # placement — also infrastructure, not signal failure. Phase 29
    # cooldown is unaffected (wired through note_sl_hit on stop_loss).
    "ghost_reconciled",
    "ghost_sync",
    "sl_crossed_at_placement",
    "sl_crossed_during_placement",
})
_SPEC12_SCRATCH_PCT = 0.5


# ─────────────────────────────────────────────────────────────────────
# Patch #2 (2026-05-19) — refit-driven age-cutoff loader
# ─────────────────────────────────────────────────────────────────────
# scripts/refit_age_cutoffs.py writes data/models/age_cutoffs.json with per-tier
# (STANDARD/CONVICTION/AGGRESSIVE) max-age cutoffs in MINUTES when the 45d-fit
# / 15d-holdout split shows strict holdout improvement over current. If the JSON
# is absent or unreadable, callers fall back to RISK["max_position_age_hours"].
_AGE_CUTOFFS_DEFAULT_PATH = Path("data/models/age_cutoffs.json")


def load_age_cutoffs(path: Path | str | None = None) -> dict | None:
    """Load the refit-driven per-tier age cutoffs from JSON.

    Returns the JSON payload as a dict on success (keys typically include
    STANDARD/CONVICTION/AGGRESSIVE → int minutes, plus fitted_at,
    fit_sample_size). Returns None when the file is missing or unreadable
    so the caller can fall back to RISK config constants.
    """
    p = Path(path) if path is not None else _AGE_CUTOFFS_DEFAULT_PATH
    if not p.exists():
        return None
    try:
        with p.open("r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload, dict):
            return None
        return payload
    except (OSError, json.JSONDecodeError) as e:
        logger.warning(f"[risk] load_age_cutoffs: failed to read {p}: {e}")
        return None


class RiskManager:

    def _notify_halt(self, subject: str, body: str) -> None:
        """No-op — halts removed (2026-05-27). Kept for signature compat."""
        pass

    def __init__(self):
        self.max_position_pct   = RISK["max_position_pct"]
        self.max_open_positions = RISK["max_open_positions"]
        self.max_daily_loss_pct = RISK["max_daily_loss_pct"]
        self.default_sl         = RISK["default_stop_loss"]
        self.default_tp         = RISK["default_take_profit"]
        self.futures_max_lev    = RISK["futures_max_leverage"]
        self.default_leverage   = RISK["default_leverage"]
        self.sizing_mode        = RISK.get("position_sizing_mode", "fixed")
        self.max_drawdown_pct   = RISK.get("max_drawdown_pct", 0.15)  # 15%

        # 2026-06-04 (audit): serialize the PnL accumulators. record_trade_pnl runs
        # from the post-close hook, which PositionTracker fires OUTSIDE its own lock, on
        # whatever thread closed the position — the 10s SL/TP daemon's ThreadPoolExecutor
        # (fanned per exchange×{spot,futures}), the 5-min cycle, and the 90s monitor.
        # Without serialization two concurrent closes lost-update `self._daily_pnl += pnl`,
        # under-counting the day's loss so the LIVE-active daily-loss breaker trips late.
        # RLock so nested locked calls on the same thread cannot self-deadlock.
        self._lock = threading.RLock()

        self._daily_pnl:     float = 0.0
        self._start_balance: float = 0.0
        self._peak_balance:  float = 0.0
        # Phase 25 (2026-05-05): set to True after _load_state restores
        # peak from disk; first note_balance_update checks if the loaded
        # peak is stale (>10% above current effective) and resets if so.
        # Stops the "peak from a higher-equity prior session haunts
        # forever, drawdown halts every restart" anti-pattern.
        self._peak_stale_flag: bool = False
        # True only when _load_state restored today's on-disk ledger. This
        # distinguishes a legitimate same-day restart from a fresh instance,
        # whose default calendar date also happens to be today.
        self._loaded_same_day_state: bool = False
        self._trading_day:   date  = _utc_today()
        self._recent_results: list = []   # last N trades: True=win, False=loss
        self._trade_history:  list = []   # Kelly: (win, pnl_pct)
        self._last_halt_reason: str = ""
        self._incident_memory: dict | None = None
        # Tracks the last balance reading for the 30%-down-spike guard in
        # update_current_balance. Not persisted — re-seeded by the first
        # balance update after restart.
        self._last_balance_seen: float = 0.0
        # 2026-04-27: per-UTC-day open counter, enforced against
        # RISK["max_trades_per_day"]. _trade_history is the wrong source for
        # "trades today" because it caps at 100 and is the rolling Kelly
        # window, not a daily counter — the bot did 52 trades on 2026-04-27
        # and hit no cap because there was none.
        self._opens_today:    int  = 0
        self._correlation_mgr = None      # lazy-loaded

        # Spec §12 pause tracking
        self._symbol_streaks: dict = {}   # symbol → list[bool] (True = win)
        self._family_streaks: dict = {}   # family → list[bool]
        self._symbol_pauses:  dict = {}   # symbol → epoch_until
        self._family_pauses:  dict = {}   # family → epoch_until
        self._global_streak:  list = []   # last 20 global outcomes

        # Phase 29 (2026-05-05) — freqtrade-style post-SL CooldownPeriod.
        # Audit of 267 trades found 48 stop_loss hits totaling -$78.10
        # (largest active bleed source). Many of those are likely
        # immediate re-entries on a symbol that just stopped out.
        # Ledger of recent SL exits per (symbol, side):
        #   key = "<symbol>|<side>", value = list of close timestamps
        # Pruned on every check + record (>24h entries dropped).
        # Persisted to risk_state.json since 2026-06-11 — a 180-min
        # cooldown is too long to lose on a process restart.
        self._recent_sl_by_pair_side: dict = {}

        # Restore persisted state from previous session
        self._load_state()

    # ── State persistence for dashboard ────────────────────────────────

    def _incident_reason(self) -> str:
        payload = self._incident_memory
        if payload is None:
            try:
                if not _INCIDENT_LATCH_PATH.exists():
                    return ""
                payload = json.loads(_INCIDENT_LATCH_PATH.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError, TypeError):
                return "incident_latch_unreadable"
        category = str(payload.get("category") or "system")
        reason = str(payload.get("reason") or "unspecified")
        return f"{category}_incident:{reason}"

    def _daily_loss_reason(self) -> str:
        try:
            from config import DAILY_LOSS_BREAKER as breaker
        except Exception:
            breaker = {}
        if not breaker.get("enabled") or self._start_balance <= 0:
            return ""
        try:
            max_loss_pct = abs(float(breaker.get("max_loss_pct", 0.02)))
        except (TypeError, ValueError):
            return "daily_loss_breaker_config_invalid"
        budget = max_loss_pct * self._start_balance
        with self._lock:
            pnl = self._daily_pnl
        if budget > 0 and pnl <= -budget:
            return f"daily_loss:{pnl:+.2f}<=-{budget:.2f}"
        return ""

    def _persistent_breaker_reason(self, *, include_kill_switch: bool = True) -> str:
        incident = self._incident_reason()
        if incident:
            return incident
        if include_kill_switch:
            try:
                from core.kill_switch import entries_halted

                if entries_halted():
                    return "kill_switch_active"
            except Exception:
                return "kill_switch_status_unavailable"
        return self._daily_loss_reason()

    def latch_incident(self, reason: str, *, category: str = "execution",
                       metadata: dict | None = None) -> bool:
        """Persist a fail-closed data/execution incident until manual clearance."""
        reason = str(reason or "").strip()
        category = str(category or "").strip().lower()
        if not reason or category not in {"data", "execution", "reconciliation", "system"}:
            return False
        payload = {
            "category": category,
            "reason": reason,
            "metadata": dict(metadata or {}),
            "latched_at": datetime.now(timezone.utc).isoformat(),
        }
        self._incident_memory = payload
        self._last_halt_reason = f"{category}_incident:{reason}"
        try:
            _INCIDENT_LATCH_PATH.parent.mkdir(parents=True, exist_ok=True)
            tmp = _INCIDENT_LATCH_PATH.with_name(_INCIDENT_LATCH_PATH.name + ".tmp")
            tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
            os.replace(tmp, _INCIDENT_LATCH_PATH)
            self._save_state()
            return True
        except OSError as exc:
            logger.error(f"[Risk] incident latch persistence failed; memory halt remains: {exc}")
            return False

    def clear_incident(self, operator_note: str) -> bool:
        """Clear a manual incident latch only with an auditable operator note."""
        note = str(operator_note or "").strip()
        if not note:
            return False
        try:
            if self._incident_memory is not None:
                payload = dict(self._incident_memory)
            elif _INCIDENT_LATCH_PATH.exists():
                payload = json.loads(_INCIDENT_LATCH_PATH.read_text(encoding="utf-8"))
            else:
                return False
            archive = {
                **payload,
                "cleared_at": datetime.now(timezone.utc).isoformat(),
                "operator_note": note,
            }
            _INCIDENT_HISTORY_PATH.parent.mkdir(parents=True, exist_ok=True)
            with _INCIDENT_HISTORY_PATH.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(archive, sort_keys=True) + "\n")
            if _INCIDENT_LATCH_PATH.exists():
                _INCIDENT_LATCH_PATH.unlink()
            self._incident_memory = None
            self._last_halt_reason = ""
            self._save_state()
            return True
        except (OSError, json.JSONDecodeError, TypeError) as exc:
            logger.error(f"[Risk] incident clear failed; latch remains active: {exc}")
            return False

    def _save_state(self):
        """Persist risk state for dashboard + resume on restart."""
        breaker_reason = self._persistent_breaker_reason()
        state = {
            "is_halted": bool(breaker_reason),
            "halt_reason": breaker_reason,
            "daily_pnl": round(self._daily_pnl, 4),
            "max_drawdown_pct": round(
                max(0.0, (self._peak_balance - ((self._start_balance or self._peak_balance) + self._daily_pnl))
                    / self._peak_balance), 4) if self._peak_balance > 0 else 0.0,
            "start_balance": round(self._start_balance, 2),
            "peak_balance": round(self._peak_balance, 2),
            "trading_day": self._trading_day.isoformat(),
            "trades_today": self._opens_today,
            "recent_results": self._recent_results[-20:],
            "trade_history": self._trade_history[-100:],
            "symbol_pauses": self._symbol_pauses,
            "family_pauses": self._family_pauses,
            "global_streak": self._global_streak[-20:],
            # Phase 29 ledger — persisted since 2026-06-11; a 180-min
            # cooldown is too long to lose on a process restart. Shallow
            # snapshot: a concurrent note_sl_hit key-insert mid-dumps would
            # raise "dictionary changed size during iteration" and silently
            # drop the save (values are replaced wholesale, never mutated
            # in place, so dict() suffices).
            "recent_sl_by_pair_side": dict(self._recent_sl_by_pair_side),
            "timestamp": _time.time(),
        }
        try:
            RISK_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write (tmp + os.replace) — a torn risk_state.json sends
            # _load_state down the "starting fresh" path on the next boot,
            # losing daily_pnl, Spec §12 pauses AND the Phase-29 ledger
            # (2026-06-11; mirrors virtual_wallet._save).
            target = RISK_STATE_PATH
            tmp = target.with_name(target.name + ".tmp")
            tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
            os.replace(tmp, target)
        except Exception:
            pass

    def _load_state(self):
        """Load persisted risk state on startup. Handles same-day resume vs new-day reset."""
        path = RISK_STATE_PATH
        if not path.exists():
            self._honour_review_flag_if_present()
            return

        try:
            state = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            logger.warning(f"[Risk] Could not load risk state: {e}")
            self._honour_review_flag_if_present()
            return

        saved_day_str = state.get("trading_day", "")
        try:
            saved_day = date.fromisoformat(saved_day_str)
        except (ValueError, TypeError):
            logger.warning("[Risk] Invalid trading_day in saved state — starting fresh")
            self._honour_review_flag_if_present()
            return

        # Always restore trade history (used by Kelly sizing across days)
        self._recent_results = state.get("recent_results", [])
        self._trade_history = [
            tuple(t) if isinstance(t, list) else t
            for t in state.get("trade_history", [])
        ]
        # Spec §12 pause state — keep across restarts
        self._symbol_pauses = {k: float(v) for k, v in state.get("symbol_pauses", {}).items()}
        self._family_pauses = {k: float(v) for k, v in state.get("family_pauses", {}).items()}
        self._global_streak = list(state.get("global_streak", []))
        # Phase 29 SL-cooldown ledger — restore regardless of day
        # (entries self-prune at the 24h guard lookback).
        try:
            self._recent_sl_by_pair_side = {
                k: [float(t) for t in v]
                for k, v in (state.get("recent_sl_by_pair_side") or {}).items()}
        except (TypeError, ValueError):
            self._recent_sl_by_pair_side = {}

        if saved_day == _utc_today():
            # Same-day restart: restore daily PnL, peak
            self._loaded_same_day_state = True
            self._daily_pnl = state.get("daily_pnl", 0.0)
            self._trading_day = saved_day
            self._peak_balance = state.get("peak_balance", 0.0)
            # Phase 25: mark loaded peak as potentially stale. The first
            # note_balance_update will compare against actual exchange
            # equity and reset if the gap is >10%.
            if self._peak_balance > 0:
                self._peak_stale_flag = True
            # Restore start_balance so drawdown math survives process restart.
            self._start_balance = state.get("start_balance", 0.0)
            # Restore today's open count so the daily-trade cap survives a
            # mid-day process restart. Default 0 for files saved by older
            # builds that lacked this counter.
            self._opens_today = int(state.get("trades_today", 0) or 0)
            logger.info(
                f"[Risk] Resumed same-day state: daily_pnl={self._daily_pnl:+.4f}, "
                f"trades={len(self._trade_history)}")
        else:
            # New day: reset daily counters but keep trade history
            self._loaded_same_day_state = False
            self._daily_pnl = 0.0
            self._trading_day = _utc_today()
            self._opens_today = 0
            logger.info(
                f"[Risk] New day — daily counters reset. "
                f"Carried over {len(self._trade_history)} trade history entries")

        # Cleanup: delete stale review_required.json if present
        self._honour_review_flag_if_present()

    def _honour_review_flag_if_present(self) -> None:
        """Delete stale review_required.json on startup (halts removed)."""
        if _REVIEW_FLAG_PATH.exists():
            try:
                _REVIEW_FLAG_PATH.unlink()
                logger.info("[Risk] Deleted stale review_required.json (halts disabled)")
            except Exception as e:
                logger.debug(f"[Risk] Could not delete review flag: {e}")

    # Phase 29 (2026-05-05) — post-SL cooldown helpers ─────────────────
    # freqtrade-style CooldownPeriod + StoplossGuard (per-pair-side).
    # First SL on (symbol, side):  refuse re-entry for 180 min
    # 2+ SL on (symbol, side) in 24h: refuse for 6 h
    # The check runs at the top of bot_engine._execute_open. SL recording
    # happens in order_manager._finalize_close.
    # 2026-06-11 (owner-approved): 30 → 180 min. Jun-11 tape: ADA/DOT/BNB/
    # APT re-shorted 9-12x each into 70 stop-losses (~every 90 min) while
    # the 30-min cooldown was advisory-only. Time-based protection, NOT an
    # edge-opinion block — kept under UNBLOCK per owner.
    POST_SL_SHORT_COOLDOWN_MIN = 180     # after any SL on this pair-side
    POST_SL_GUARD_TRADE_LIMIT  = 2       # SL count to escalate
    POST_SL_GUARD_LOOKBACK_HRS = 24      # window for the SL-count
    POST_SL_GUARD_LOCK_HRS     = 6       # escalated lock duration

    @staticmethod
    def _pair_side_key(symbol: str, side: str) -> str:
        """Canonical Phase-29 ledger key: settle suffix stripped.

        Found live 2026-07-10: note_sl_hit received the tracker symbol
        (``ARB/USDT:USDT``) while _execute_open checked the action symbol
        (``ARB/USDT``) — keys never matched, so BOTH cooldown layers were
        dead for futures re-entries (ARB re-entered 14/5/3min after
        consecutive SLs with 8 hits in the ledger)."""
        return f"{str(symbol).split(':')[0]}|{side}"

    def note_sl_hit(self, symbol: str, side: str) -> None:
        """Record that a position closed via stop_loss. Called from
        order_manager._finalize_close on `reason == "stop_loss"` only.
        """
        if not symbol or not side:
            return
        # Lock: this runs on SL/TP daemon worker threads concurrently with
        # record_trade_pnl (which saves under the same lock) — unlocked
        # mutate+save raced the encoder (2026-06-11 review).
        with self._lock:
            key = self._pair_side_key(symbol, side)
            now = _time.time()
            lst = self._recent_sl_by_pair_side.get(key, [])
            # Prune entries older than guard lookback (default 24h)
            cutoff = now - self.POST_SL_GUARD_LOOKBACK_HRS * 3600
            lst = [t for t in lst if t >= cutoff]
            lst.append(now)
            self._recent_sl_by_pair_side[key] = lst
            # Flush so the cooldown survives a process restart (2026-06-11).
            self._save_state()

    def is_sl_cooldown_active(self, symbol: str, side: str) -> tuple:
        """Return (active: bool, reason: str). Checks both layers:
          1. 180min hard cooldown after the last SL on this pair-side
          2. 6h escalated lock if 2+ SL on this pair-side in last 24h
        Caller refuses the trade when active=True. No state mutation.

        2026-07-19 (max-flow band engine): SL_COOLDOWN_ENABLED=false makes
        this a no-op — (False, "sl_cooldown_disabled_by_profile") — while
        note_sl_hit keeps recording, so re-enabling restores protection.
        """
        try:
            from config import SL_COOLDOWN_ENABLED as _sl_cooldown_on
        except ImportError:
            _sl_cooldown_on = True
        if not _sl_cooldown_on:
            return False, "sl_cooldown_disabled_by_profile"
        if not symbol or not side:
            return False, ""
        key = self._pair_side_key(symbol, side)
        # Merge hits recorded under the canonical key AND any legacy
        # suffixed variant (pre-2026-07-10 state files + in-flight
        # processes) so existing ledger entries keep protecting.
        base = key.split("|")[0]
        lst = list(self._recent_sl_by_pair_side.get(key) or [])
        for k, v in list(self._recent_sl_by_pair_side.items()):
            if k != key and k.split("|")[-1] == side and \
                    k.split("|")[0].split(":")[0] == base:
                lst.extend(v)
        if not lst:
            return False, ""
        now = _time.time()
        # Prune for accurate window — caller guard
        cutoff = now - self.POST_SL_GUARD_LOOKBACK_HRS * 3600
        lst = sorted(t for t in set(lst) if t >= cutoff)
        self._recent_sl_by_pair_side[key] = lst
        if not lst:
            return False, ""
        last_sl = max(lst)
        elapsed_min = (now - last_sl) / 60.0
        # Layer 2: escalated guard (StoplossGuard equivalent)
        if (len(lst) >= self.POST_SL_GUARD_TRADE_LIMIT
                and elapsed_min < self.POST_SL_GUARD_LOCK_HRS * 60):
            remain = self.POST_SL_GUARD_LOCK_HRS * 60 - elapsed_min
            return True, (
                f"sl_guard:{len(lst)}_in_{self.POST_SL_GUARD_LOOKBACK_HRS}h "
                f"lock_remain={remain:.0f}min")
        # Layer 1: short post-SL cooldown
        if elapsed_min < self.POST_SL_SHORT_COOLDOWN_MIN:
            remain = self.POST_SL_SHORT_COOLDOWN_MIN - elapsed_min
            return True, (
                f"post_sl_cooldown:remain={remain:.0f}min")
        return False, ""

    # ── Trading gate with SMART RECOVERY ─────────────────────────────

    def roll_day_if_needed(self) -> bool:
        """Reset daily counters (daily_pnl, opens_today) on a UTC-day crossing.

        Idempotent and side-effect-free within a day, so it is safe to call
        every portfolio cycle. 2026-06-13: previously the rollover lived only
        inside can_trade()/note_trade_opened(), both reached only via the
        entry-execution path (bot_engine._execute_open). When entries were
        gated upstream for >1 day (CLAUDE_PORTFOLIO_MODE=off → algo-only SCALP
        path produced 0 ALLOW), can_trade() was never called, so the counters
        froze at their pre-gate values across UTC day boundaries and
        dashboards/notifiers reading risk_state.json reported a stale "today"
        PnL. Calling this once per cycle decouples the rollover from entries.
        Returns True if a rollover occurred.
        """
        today = _utc_today()
        if today != self._trading_day:
            self._daily_pnl = 0.0
            self._trading_day = today
            self._opens_today = 0
            self._save_state()
            return True
        return False

    def can_trade(self, open_position_count: int) -> bool:
        # New-day rollover: in long-running processes the load-state path
        # only fires at startup, so a midnight crossing must be picked up
        # here. Without this the daily counters never reset.
        self.roll_day_if_needed()

        # Manual incidents persist until an operator clears them; the UTC
        # daily-loss breaker is the only breaker that resets automatically.
        breaker_reason = self._persistent_breaker_reason()
        if breaker_reason:
            self._last_halt_reason = breaker_reason
            logger.warning(
                f"[Risk] refusing NEW entry: {breaker_reason}. "
                "Existing positions and reduce-only actions remain active."
            )
            return False

        if open_position_count >= self.max_open_positions:
            self._last_halt_reason = "max_open_positions"
            logger.debug(
                f"[Risk] Max open positions ({self.max_open_positions}) reached.")
            return False
        # 2026-04-27: per-day open cap. Caller must invoke note_trade_opened()
        # after a successful open so this counter advances.
        max_per_day = RISK.get("max_trades_per_day", 0) or 0
        if max_per_day > 0 and self._opens_today >= max_per_day:
            self._last_halt_reason = "daily_trade_cap"
            logger.info(
                f"[Risk] Daily trade cap reached: {self._opens_today}/{max_per_day} "
                f"opens today — refusing new entries until UTC midnight.")
            return False
        self._last_halt_reason = ""
        return True

    def note_trade_opened(self) -> None:
        """Increment today's open counter. Caller invokes after a successful
        position open so can_trade() can enforce RISK['max_trades_per_day'].
        """
        # Roll over the counter on a UTC-midnight crossing the same way
        # can_trade() does, so a long-running process that opens its first
        # post-midnight trade doesn't carry yesterday's count forward.
        self.roll_day_if_needed()
        self._opens_today += 1
        self._save_state()

    # ── Position sizing ──────────────────────────────────────────────

    def _adaptive_size_multiplier(self, lookback: int = 50) -> float:
        """Adaptive position-size multiplier from rolling EV (2026-05-03).

        Reads last N closed futures trades from the warehouse and returns
        a multiplier in [0.25, 1.0]. Closes the feedback loop the bot
        otherwise lacks — sizing tracks recent realized expectancy.

        Tiers:
          n < 30:           1.0   (insufficient sample, full size)
          EV >= +$0.10:     1.0   (proven positive — full size)
          EV in [-0.05, +0.10]: 0.75  (break-even zone — moderate size)
          EV in [-0.20, -0.05]: 0.50  (bleeding — half size)
          EV < -$0.20:      0.25  (deep negative — quarter size, slow bleed)
        """
        try:
            import sqlite3
            from pathlib import Path
            db = Path("data/warehouse.sqlite")
            if not db.exists():
                return 1.0
            con = sqlite3.connect(str(db))
            con.row_factory = sqlite3.Row
            rows = list(con.execute(
                "SELECT realized_pnl FROM trades WHERE status='CLOSED' "
                "AND market_type='futures' "
                "ORDER BY ts_exit DESC LIMIT ?",
                (lookback,)
            ))
            con.close()
        except Exception:
            return 1.0
        n = len(rows)
        if n < 30:
            return 1.0
        pnls = [r["realized_pnl"] or 0.0 for r in rows]
        ev = sum(pnls) / n
        if ev >= 0.10:
            return 1.0
        if ev >= -0.05:
            return 0.75
        if ev >= -0.20:
            return 0.50
        return 0.25

    def calculate_position_size(self, balance_usdt: float, price: float,
                                 leverage: int = 1,
                                 atr_pct: float = None) -> float:
        if balance_usdt <= 0:
            logger.warning("[Risk] Zero balance — cannot size position.")
            return 0.0

        if self.sizing_mode == "kelly":
            pct = self._kelly_fraction()
            if pct < 0:
                logger.info("[Risk] Kelly negative edge — blocking trade")
                return 0.0
        elif self.sizing_mode == "volatility" and atr_pct:
            # Phase 2 / Task C: replace the ad-hoc `1% target_risk / atr` with
            # a proper portfolio-vol-targeting sizer. VolTarget.size() is a
            # @staticmethod that returns a notional in USD already clamped
            # to [floor_pct, ceiling_pct] of balance — we feed
            # notional_ceiling_pct = max_position_pct * 3 * leverage to
            # match the prior envelope, then apply leverage to convert
            # notional → contract qty.
            from core.vol_target import VolTarget
            notional = VolTarget.size(
                balance_usd=balance_usdt,
                vol_forecast=atr_pct,
                notional_ceiling_pct=self.max_position_pct * 3 * leverage,
            )
            qty = (notional * leverage) / price if price > 0 else 0.0
            logger.info(
                f"[Risk] VolTarget: atr%={atr_pct*100:.2f}% "
                f"notional=${notional:.2f} qty={qty:.8f} lev={leverage}x")
            return qty
        else:
            pct = self.max_position_pct

        # Apply scaling multiplier if conditions met (200+ trades, 60%+ WR, <10% DD)
        scale = self._check_scaling_eligible()

        # 2026-05-03 (Phase 16): adaptive sizing from rolling 50-trade EV.
        # Closes the feedback loop — the bot now sizes itself based on recent
        # realized expectancy. EV positive → full size. EV negative → scale
        # down to limit bleed. EV deeply negative → quarter-size.
        # Falls back to 1.0× when warehouse is empty / inaccessible (fail-safe).
        try:
            ev_mult = self._adaptive_size_multiplier()
        except Exception:
            ev_mult = 1.0

        notional = balance_usdt * pct * leverage * scale * ev_mult
        qty      = notional / price

        # MIN NOTIONAL: exchanges reject < $5
        if notional < 5.50 and balance_usdt >= 10:
            notional = min(5.50, balance_usdt * 0.20 * leverage)
            qty = notional / price
            logger.debug(f"[Risk] Notional boosted to ${notional:.2f}")
        elif notional < 5.50:
            logger.debug(f"[Risk] Balance ${balance_usdt:.2f} too low")
            return 0.0

        scale_tag = f" scale={scale:.1f}x" if scale > 1.0 else ""
        logger.info(
            f"[Risk] [{self.sizing_mode}] balance={balance_usdt:.4f} "
            f"pct={pct*100:.2f}% notional={notional:.4f} USDT "
            f"qty={qty:.8f} lev={leverage}x{scale_tag}")
        return qty

    def _check_scaling_eligible(self) -> float:
        """Return position size multiplier. 1.0 normally, scale_factor if scaling conditions met."""
        try:
            from config import SCALING
        except ImportError:
            return 1.0
        if len(self._trade_history) < SCALING.get("min_live_trades", 200):
            return 1.0
        wins = sum(1 for t in self._trade_history if t[0])
        wr = wins / len(self._trade_history) if self._trade_history else 0
        if wr < SCALING.get("min_win_rate", 0.60):
            return 1.0
        effective = self._start_balance + self._daily_pnl
        dd = self.drawdown_pct(effective)
        if dd > SCALING.get("max_drawdown_for_scale", 0.10):
            return 1.0
        factor = SCALING.get("scale_factor", 1.5)
        logger.info(f"[Risk] Scaling eligible: {len(self._trade_history)} trades, "
                     f"WR={wr:.0%}, DD={dd:.1%} — applying {factor}x")
        return factor

    def _kelly_fraction(self) -> float:
        if len(self._trade_history) < 10:
            return self.max_position_pct
        # Filter out entries with pnl_pct=None (reconciled-from-exchange trades
        # without entry/size context — dollar pnl is known, percentage isn't).
        # Keep win/loss counts based on the full history; only the avg_win /
        # avg_loss means need a numeric pct. Fall back to full history for
        # win_rate so sample size stays representative.
        numeric = [t for t in self._trade_history if t[1] is not None]
        wins  = [t for t in self._trade_history if t[0]]
        loss  = [t for t in self._trade_history if not t[0]]
        if not wins or not loss:
            return self.max_position_pct
        num_wins = [t for t in numeric if t[0]]
        num_loss = [t for t in numeric if not t[0]]
        if not num_wins or not num_loss:
            # Not enough numeric samples to compute Kelly ratio safely
            return self.max_position_pct
        win_rate = len(wins) / len(self._trade_history)
        avg_win  = sum(t[1] for t in num_wins) / len(num_wins)
        avg_loss = abs(sum(t[1] for t in num_loss)) / len(num_loss)
        if avg_loss == 0:
            return self.max_position_pct
        kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        if kelly <= 0:
            logger.debug(f"[Risk] Kelly negative ({kelly:.4f}) — blocking trade (negative edge)")
            return -1.0  # Negative sentinel — caller must check and block trade
        fraction = min(kelly * 0.5, self.max_position_pct * 3)
        return max(0.005, fraction)  # minimum 0.5% if edge is positive

    # ── Correlation manager ─────────────────────────────────────────

    @property
    def correlation(self):
        if self._correlation_mgr is None:
            from core.correlation_manager import CorrelationManager
            self._correlation_mgr = CorrelationManager()
        return self._correlation_mgr

    def check_correlation(self, symbol: str, open_positions: list,
                          balance: float) -> dict:
        """Check if opening a position on symbol would over-concentrate."""
        return self.correlation.group_exposure(symbol, open_positions, balance)

    # ── Leverage ─────────────────────────────────────────────────────

    def validate_leverage(self, requested: int) -> int:
        safe = min(requested, self.futures_max_lev)
        return safe

    def regime_adjusted_leverage(self, requested: int,
                                  vol_regime: str = "vol_normal") -> int:
        """
        Reduce leverage in high-volatility regimes.
        Normal: use requested. High: 60%. Extreme: 40%.
        """
        safe = self.validate_leverage(requested)
        if vol_regime == "vol_extreme":
            safe = max(1, int(safe * 0.4))
            logger.debug(f"[Risk] Extreme vol: leverage reduced to {safe}x")
        elif vol_regime == "vol_high":
            safe = max(1, int(safe * 0.6))
            logger.debug(f"[Risk] High vol: leverage reduced to {safe}x")
        return safe

    # ── SL / TP calculation ───────────────────────────────────────────

    def get_sl_tp(self, entry: float, side: str, atr: float = None,
                   atr_sl_mult: float = None, atr_tp_mult: float = None,
                   leverage: int = 1, symbol: str = None,
                   regime: str = None):
        # ── Distribution-fitted SL/TP (Phase 2 / Task B) ───────────────
        # Fit SL to the realized MAE distribution per (symbol[, regime]).
        # When the cell has < MIN_FIT_TRADES warehouse rows, DistFitSL
        # returns an ATR×1.8 / ATR×4.5 fallback FitResult so callers see a
        # uniform interface. We still honor caller-supplied multiplier
        # overrides when present (preserves backwards compat for callers
        # tuning specific strategies).
        sl_dist = tp_dist = None
        if atr and atr > 0 and symbol and atr_sl_mult is None and atr_tp_mult is None:
            try:
                from core.dist_fit_sl import DistFitSL
                fit = DistFitSL().compute(
                    symbol=symbol, side=side, atr_pct=atr / entry if entry > 0 else atr,
                    regime=regime,
                )
                sl_dist = entry * fit.sl_pct
                tp_dist = entry * fit.tp_pct
                logger.debug(
                    f"[Risk] dist-fit SL/TP {symbol} ({fit.source}, n={fit.n}, "
                    f"R={fit.r_target:.2f}): sl={fit.sl_pct*100:.2f}% "
                    f"tp={fit.tp_pct*100:.2f}%")
            except Exception as e:
                logger.debug(f"[Risk] DistFitSL failed for {symbol}: {e}")
                sl_dist = tp_dist = None

        if sl_dist is None or tp_dist is None:
            if atr and atr > 0:
                from config import SUPERTREND as ST
                sl_mult = atr_sl_mult or ST.get("atr_sl_mult", 1.8)
                tp_mult = atr_tp_mult or ST.get("atr_tp_mult", 4.5)
                sl_dist = atr * sl_mult
                tp_dist = atr * tp_mult
            else:
                sl_dist = entry * self.default_sl
                tp_dist = entry * self.default_tp

        # DYNAMIC SL FLOOR: flat 1.0% across all leverages.
        # 2026-04-16 (post-audit): The old `0.015 + (lev-1)*0.003` formula
        # scaled the floor with leverage, producing:
        #   10x → 4.2%  15x → 5.7%  20x → 7.2%
        # Since the leverage tier system now caps SL at 0.8-1.2% at high tiers
        # (per LEVERAGE_TIERS in config), this floor was multiplying every
        # high-tier SL by 4-9×, collapsing realized R:R to 0.74:1 across
        # 177 trades.
        # ATR-based sizing already accounts for volatility; the floor is
        # only a safety net for cases where ATR is stale or miscalculated.
        base_floor = 0.010  # Flat 1% across all leverages.

        min_sl_dist = entry * base_floor
        if sl_dist < min_sl_dist:
            ratio = min_sl_dist / sl_dist if sl_dist > 0 else 1.5
            # Cap the TP expansion ratio to prevent unreachable TP targets
            ratio = min(ratio, 2.0)
            tp_dist = tp_dist * ratio
            original_pct = sl_dist / entry * 100 if entry > 0 else 0
            sl_dist = min_sl_dist
            # The 2.0x TP-expansion cap can leave R:R below the configured minimum when the
            # original ATR SL was tiny (e.g. 0.2% SL + 2:1 TP -> floored 1.0% SL + 0.8% TP = 0.8:1).
            # Re-assert min_rr_ratio so we never ship a sub-minimum-R:R bracket (audit 2026-06-06).
            min_rr = RISK.get("min_rr_ratio", 1.2)
            if tp_dist < sl_dist * min_rr:
                tp_dist = sl_dist * min_rr
            logger.debug(
                f"[Risk] SL floor applied: {sl_dist/entry*100:.2f}% "
                f"(original wanted {original_pct:.2f}%, lev={leverage}x)")

        if side.lower() == "buy":
            sl = entry - sl_dist
            tp = entry + tp_dist
        else:
            sl = entry + sl_dist
            tp = entry - tp_dist

        if side.lower() == "buy"  and sl >= entry:
            sl = entry * (1 - self.default_sl)
        if side.lower() == "sell" and sl <= entry:
            sl = entry * (1 + self.default_sl)

        return round(sl, 8), round(tp, 8)

    # ── PnL recording + drawdown ──────────────────────────────────────

    def record_trade_pnl(self, pnl: float, balance: float,
                          is_win: bool = None, pnl_pct=0.0):
        # pnl_pct may be None when the trade was reconciled from the exchange
        # ledger without entry/size context (see position_tracker.reconcile_
        # closed_pnl). The dollar `pnl` is still ground truth, so is_win and
        # daily_pnl stay correct. Only Kelly-history storage cares about the
        # percentage; store None there and let Kelly ignore unknowns.
        with self._lock:
            today = _utc_today()
            if today != self._trading_day:
                self._daily_pnl   = 0.0
                self._opens_today = 0  # A2: reset open-count too, else a post-
                self._trading_day = today  # midnight close carries yesterday's count
                logger.info("[Risk] Daily PnL reset (new day)")

            self._daily_pnl += pnl

            # Track recent results for smart recovery
            if is_win is not None:
                self._recent_results.append(is_win)
                if len(self._recent_results) > 20:
                    self._recent_results.pop(0)

            # Kelly history — store pnl_pct verbatim (None stays None).
            if is_win is not None:
                self._trade_history.append((is_win, pnl_pct))
                if len(self._trade_history) > 100:
                    self._trade_history.pop(0)

            # Effective balance = start balance + cumulative daily PnL
            # (The `balance` param is often just position notional — unreliable for DD calc)
            effective_balance = (self._start_balance or balance) + self._daily_pnl

            # Phase 25 (2026-05-05): stale-peak reset on first update post-restart.
            # If the peak loaded from disk is >10% above current effective, the
            # peak is from a richer prior session and should not gate today's
            # drawdown calc. Reset to current × 1.05 to give a small buffer for
            # normal volatility without losing all guard.
            if self._peak_stale_flag:
                self._peak_stale_flag = False
                if (effective_balance > 0
                        and self._peak_balance > effective_balance * 1.10):
                    old_peak = self._peak_balance
                    self._peak_balance = effective_balance * 1.05
                    logger.warning(
                        f"[Risk] Phase 25: stale peak ${old_peak:.2f} > "
                        f"${effective_balance:.2f} × 1.10 — reset to "
                        f"${self._peak_balance:.2f} (current × 1.05).")

            if effective_balance > self._peak_balance:
                self._peak_balance = effective_balance

            self._save_state()

    def set_start_balance(self, balance: float):
        """Set starting balance on startup. Respects resumed same-day state."""
        try:
            current_balance = float(balance)
            restored_start = float(self._start_balance)
            restored_peak = float(self._peak_balance)
            restored_pnl = float(self._daily_pnl)
        except (TypeError, ValueError):
            current_balance = restored_start = restored_peak = restored_pnl = float("nan")

        restored_valid = (
            math.isfinite(restored_start) and restored_start > 0
            and math.isfinite(restored_peak) and restored_peak > 0
            and math.isfinite(restored_pnl)
        )
        if self._loaded_same_day_state and not restored_valid:
            # A torn or test-polluted ledger must not leave start_balance=0:
            # the percentage daily-loss breaker is undefined and disabled
            # without a positive denominator. Reconstruct start-of-day equity
            # from current equity and the retained daily realized PnL.
            if math.isfinite(current_balance) and current_balance > 0:
                daily_pnl = restored_pnl if math.isfinite(restored_pnl) else 0.0
                reconstructed_start = current_balance - daily_pnl
                if not math.isfinite(reconstructed_start) or reconstructed_start <= 0:
                    logger.error(
                        "[Risk] Invalid persisted PnL prevents start-balance reconstruction; "
                        "resetting the corrupt daily PnL to zero.")
                    daily_pnl = 0.0
                    reconstructed_start = current_balance
                self._daily_pnl = daily_pnl
                self._start_balance = reconstructed_start
                self._peak_balance = max(reconstructed_start, current_balance)
                self._peak_stale_flag = False
                self._save_state()
                logger.warning(
                    "[Risk] Recovered invalid same-day risk ledger: "
                    f"start=${self._start_balance:.4f}, daily_pnl={self._daily_pnl:+.4f}, "
                    f"peak=${self._peak_balance:.4f}.")
            else:
                logger.error(
                    "[Risk] Invalid same-day risk ledger and no positive startup balance; "
                    "percentage daily-loss breaker remains unavailable.")
            return

        if self._loaded_same_day_state and self._peak_balance > 0:
            # Same-day restart: keep the disk-restored _start_balance, daily PnL and peak.
            # Audit 2026-06-03: do NOT overwrite _start_balance with `balance` here —
            # `balance` is CURRENT equity (already has today's realized PnL baked in), so
            # overwriting would double-count today's PnL in effective_balance
            # (_start_balance + _daily_pnl) and deflate the daily-loss-breaker budget.
            # Phase 34: peak only advances via realized record_trade_pnl, not startup equity.
            logger.info(
                f"[Risk] Starting balance: keeping resumed start=${self._start_balance:.4f} USDT "
                f"(daily_pnl={self._daily_pnl:+.4f}, peak=${self._peak_balance:.2f})")
        else:
            # New day or first run: fresh slate
            self._start_balance = balance
            self._peak_balance = balance
            self._daily_pnl = 0.0
            logger.info(f"[Risk] Starting balance: {balance:.4f} USDT (peak reset)")

    def update_current_balance(self, balance: float):
        """Update peak balance without resetting peak or clearing halts.
        Called on subsequent cycles after startup.

        2026-04-13 FIX: Guard against artificial balance spikes. A single
        balance fetch returning an inflated number (e.g. Bybit's totalEquity
        instead of totalAvailableBalance) used to permanently poison the
        peak, triggering a fake drawdown halt that required manual reset.
        Now: if balance jumps > 30% above peak in a single cycle, log a
        warning and DON'T update peak — real P&L never spikes 30% in 5 min.

        2026-04-30 FIX: do NOT overwrite `self._start_balance` on every
        cycle. start_balance is the SESSION START balance — set once via
        set_start_balance() and held constant — because the drawdown
        circuit-breaker math is:
            effective = start_balance + daily_pnl
            drawdown  = (peak - effective) / peak
        That formula assumes start_balance is constant. If we overwrite
        it with the current balance every cycle, then effective ≈ current
        balance + daily_pnl (double-counting), and the moment a single
        exchange stalls and returns a partial-balance read, start_balance
        drops to that low number → drawdown formula computes a phantom
        loss → halt fires for no real reason.

        Live evidence (2026-04-30): peak_balance=$364, balance reads
        fluctuated $108↔$221↔$357 across the day as one or another
        exchange momentarily stalled. The bug clobbered start_balance to
        $108, drawdown calc returned 70.2% (= (364-(108-0.65))/364), and
        the bot halted on a drawdown that never happened.

        Also added: a 30% DOWN-spike rejection mirroring the existing UP
        spike guard. If current reads >30% below the prior cycle's
        snapshot, treat it as a fetch flake (one exchange offline) and
        skip the peak update without poisoning state. The peak is only
        ever advanced by genuinely-higher cycles.
        """
        # Symmetric flake guard: reject 30% DROPS too (e.g. one exchange
        # API momentarily returns 0). Without this, every transient
        # fetch hiccup looks like a P&L disaster to the drawdown calc.
        prior = float(getattr(self, "_last_balance_seen", 0.0) or 0.0)
        if prior > 0 and balance > 0:
            drop_pct = (prior - balance) / prior
            if drop_pct > 0.30:
                logger.warning(
                    f"[Risk] Balance drop rejected: ${balance:.2f} is "
                    f"{drop_pct*100:.0f}% below last reading ${prior:.2f} "
                    f"— likely a partial-exchange-fetch flake, not real P&L. "
                    f"Skipping peak update for this cycle.")
                return
        self._last_balance_seen = balance
        # Phase 34 (2026-05-05): peak_balance is NO LONGER bumped here.
        # The `balance` parameter is wallet equity which includes UNREALIZED
        # P&L from open positions. A favorable intra-trade swing was
        # spiking peak above realized capital, then any reversal of that
        # same unrealized P&L tripped the 8% drawdown halt for "money"
        # that was never actually realized.
        #
        # Live evidence: 2026-05-05 17:13 — peak $713.64 set from
        # unrealized spike, equity drew down to $566 (still above
        # start_balance $568.32), drawdown computed 20.6% off the
        # phantom peak, halt fired. No realized capital was lost.
        #
        # Peak is now bumped ONLY in record_trade_pnl (after a close
        # books realized PnL into daily_pnl). Drawdown becomes
        # realized-vs-realized — the value that reflects actual money kept.
        # update_current_balance still runs the flake guards above
        # (drop/spike rejection on _last_balance_seen) for the
        # downstream 30%-drop guard, just doesn't advance peak.

    def resume_trading(self):
        """No-op — halts removed (2026-05-27). Kept for API compat."""
        pass

    # ── Spec §12 per-symbol / per-family pauses ──────────────────────

    def is_symbol_paused(self, symbol: str) -> bool:
        until = self._symbol_pauses.get(symbol, 0.0)
        if until and _time.time() >= until:
            del self._symbol_pauses[symbol]
            self._save_state()
            return False
        return bool(until)

    def is_family_paused(self, family: str) -> bool:
        until = self._family_pauses.get(family, 0.0)
        if until and _time.time() >= until:
            del self._family_pauses[family]
            self._save_state()
            return False
        return bool(until)

    def record_trade_result(self, symbol: str, family: str,
                             is_win: bool, pnl_usd: float = 0.0,
                             pnl_pct: float = None,
                             reason: str = None) -> None:
        """Spec §12 pause policy. Call once per closed trade.

        - 2 consec losses on symbol → pause symbol 6h
        - 3 consec losses in family → pause family 12h
        - 5 global consec losses → force OBSERVATION + review flag
        - Outlier loss beyond MAX_LOSS_PER_TRADE_USD → review flag

        This augments record_trade_pnl (which handles daily P&L and drawdown).
        Intentionally a separate entry point so existing call sites keep working.

        Neutrality (2026-04-27): scratches (|pnl_pct| < _SPEC12_SCRATCH_PCT)
        and infrastructure exits (_SPEC12_NEUTRAL_REASONS) do NOT extend or
        break any streak — they are recorded by record_trade_pnl for daily
        accounting but skipped here so a string of timeouts cannot trigger
        a §12 halt. record_trade_pnl is intentionally untouched: daily P&L
        and drawdown still see every close.
        """
        if reason in _SPEC12_NEUTRAL_REASONS:
            return
        if pnl_pct is not None and abs(pnl_pct) < _SPEC12_SCRATCH_PCT:
            return

        sym_hist = self._symbol_streaks.setdefault(symbol, [])
        sym_hist.append(is_win)
        if len(sym_hist) > 20:
            sym_hist.pop(0)

        fam_hist = self._family_streaks.setdefault(family or "unknown", [])
        fam_hist.append(is_win)
        if len(fam_hist) > 20:
            fam_hist.pop(0)

        self._global_streak.append(is_win)
        if len(self._global_streak) > 20:
            self._global_streak.pop(0)

        now = _time.time()
        hour = 3600

        # Phase 33 (2026-05-05): per-symbol / per-family Spec §12 pauses
        # gated behind config flags. User directive: "Remove any blocks."
        # GLOBAL Spec §12 halt (5 consec losses → 4h cooldown) below
        # remains ON as catastrophic safety rail. The per-symbol and
        # per-family layers are duplicated by Phase 27 (per-symbol-side
        # graduated EV) and Phase 29 (post-SL cooldown) in a per-trade
        # data-driven way, so the static-pause version is removable.
        try:
            from config import SPEC12_FAMILY_PAUSE_ENABLED as _F12F
            from config import SPEC12_SYMBOL_PAUSE_ENABLED as _F12S
        except ImportError:
            _F12S = True
            _F12F = True

        # 2026-05-27: HALT_MECHANISMS removed (all values were False = disabled).
        # Gate checks replaced with False to preserve disabled behavior.

        # Per-symbol pause (disabled — was gated by HALT_MECHANISMS["symbol_pause"])
        if (False  # symbol_pause disabled
                and _F12S and len(sym_hist) >= SPEC_SYMBOL_LOSSES_TO_PAUSE
                and not any(sym_hist[-SPEC_SYMBOL_LOSSES_TO_PAUSE:])
        ):
            until = now + SPEC_SYMBOL_PAUSE_HOURS * hour
            self._symbol_pauses[symbol] = until
            logger.warning(
                f"[Risk/Spec12] {symbol} paused {SPEC_SYMBOL_PAUSE_HOURS}h "
                f"after {SPEC_SYMBOL_LOSSES_TO_PAUSE} consecutive losses"
            )
            # 2026-05-24 — Clear the streak buffer so the next loss after
            # the pause expires doesn't immediately re-pause via the
            # stale [False, False] tail. Without this, the cooldown is
            # effectively meaningless on a losing tape.
            self._symbol_streaks[symbol] = []

        # Per-family pause (disabled — was gated by HALT_MECHANISMS["family_pause"])
        if (False  # family_pause disabled
                and _F12F and len(fam_hist) >= SPEC_FAMILY_LOSSES_TO_PAUSE
                and not any(fam_hist[-SPEC_FAMILY_LOSSES_TO_PAUSE:])
        ):
            key = family or "unknown"
            until = now + SPEC_FAMILY_PAUSE_HOURS * hour
            self._family_pauses[key] = until
            logger.warning(
                f"[Risk/Spec12] family '{key}' paused {SPEC_FAMILY_PAUSE_HOURS}h "
                f"after {SPEC_FAMILY_LOSSES_TO_PAUSE} consecutive losses"
            )
            # 2026-05-24 — Clear streak buffer (see symbol-pause block).
            self._family_streaks[key] = []

        # 5 global consec (disabled — was gated by HALT_MECHANISMS["spec12_streak_halt"])
        if (False  # spec12_streak_halt disabled
                and len(self._global_streak) >= SPEC_GLOBAL_LOSSES_TO_REVIEW
                and not any(self._global_streak[-SPEC_GLOBAL_LOSSES_TO_REVIEW:])
        ):
            logger.error(
                f"[Risk/Spec12] {SPEC_GLOBAL_LOSSES_TO_REVIEW} consecutive losses — "
                f"logged for audit (halts disabled)"
            )

        # Outlier loss
        try:
            from config import MAX_LOSS_PER_TRADE_USD as _max_loss
        except ImportError:
            _max_loss = 2.0
        # Outlier loss flag (disabled — was gated by HALT_MECHANISMS["outlier_loss_flag"])
        if False and pnl_usd < -abs(_max_loss):  # outlier_loss_flag disabled
            self._write_review_flag(
                reason=f"outlier_loss({pnl_usd:+.2f} USD beyond ${_max_loss:.2f} cap)",
                action="manual_review",
                symbol=symbol, family=family,
            )
            logger.error(
                f"[Risk/Spec12] Outlier loss {pnl_usd:+.2f} on {symbol} "
                f"exceeds cap ${_max_loss:.2f} — review flag written"
            )

        self._save_state()

    # ── Operational hazard pauses (spec §12 hardening) ───────────────
    #
    # These are distinct from the loss-streak pauses above. They fire on
    # *environmental* signals that mean executing anything new right now
    # is unsafe regardless of the setup quality.

    SPREAD_PCTL_HAZARD   = 0.98   # spread percentile above which we pause
    SPREAD_HAZARD_MIN    = 5       # minutes the hazard must persist
    SPREAD_PAUSE_HOURS   = 1
    STALE_TICK_SECONDS   = 60      # > this with no fresh tick → exchange pause
    REJECT_WINDOW_MIN    = 10      # rolling window for rejection count
    REJECT_COUNT_PAUSE   = 3
    REJECT_PAUSE_HOURS   = 2

    def note_spread_hazard(self, symbol: str, pctl: float) -> None:
        """Track elevated-spread observations. After SPREAD_HAZARD_MIN minutes
        of continuous above-threshold readings, pause the symbol for 1h."""
        now = _time.time()
        tracker = getattr(self, "_spread_hazard_since", None)
        if tracker is None:
            self._spread_hazard_since = {}
            tracker = self._spread_hazard_since

        if pctl is None or pctl < self.SPREAD_PCTL_HAZARD:
            tracker.pop(symbol, None)
            return

        started = tracker.get(symbol)
        if started is None:
            tracker[symbol] = now
            return

        if (now - started) >= self.SPREAD_HAZARD_MIN * 60:
            until = now + self.SPREAD_PAUSE_HOURS * 3600
            self._symbol_pauses[symbol] = max(
                until, self._symbol_pauses.get(symbol, 0.0)
            )
            logger.warning(
                f"[Risk/Hazard] {symbol} paused {self.SPREAD_PAUSE_HOURS}h "
                f"— spread pctl {pctl:.2f} above {self.SPREAD_PCTL_HAZARD} for "
                f"{self.SPREAD_HAZARD_MIN}min+"
            )
            tracker.pop(symbol, None)
            self._save_state()

    def note_stale_data(self, exchange: str, seconds_since_tick: float) -> bool:
        """Return True if exchange should be treated as stale and skipped.
        Caller does the filtering — we just emit a warning log + review flag
        on first detection in a cycle."""
        if seconds_since_tick < self.STALE_TICK_SECONDS:
            return False
        logger.warning(
            f"[Risk/Hazard] {exchange} stale data: {seconds_since_tick:.0f}s "
            f"since last tick (threshold {self.STALE_TICK_SECONDS}s)"
        )
        # We only flag once per hour per exchange to avoid log spam.
        tracker = getattr(self, "_stale_flagged", None)
        if tracker is None:
            self._stale_flagged = {}
            tracker = self._stale_flagged
        last = tracker.get(exchange, 0)
        now = _time.time()
        if now - last > 3600:
            self._write_review_flag(
                reason=f"stale_data({exchange}, {seconds_since_tick:.0f}s)",
                action="investigate_exchange_connectivity",
                exchange=exchange,
            )
            tracker[exchange] = now
        return True

    def note_order_rejection(self, symbol: str, reason: str) -> None:
        """Track recent order rejections per symbol. 3 in 10 minutes → pause."""
        now = _time.time()
        tracker = getattr(self, "_rejection_log", None)
        if tracker is None:
            self._rejection_log = {}
            tracker = self._rejection_log
        window = tracker.setdefault(symbol, [])
        window.append(now)
        # Drop entries outside the window
        cutoff = now - self.REJECT_WINDOW_MIN * 60
        tracker[symbol] = [t for t in window if t >= cutoff]

        if len(tracker[symbol]) >= self.REJECT_COUNT_PAUSE:
            until = now + self.REJECT_PAUSE_HOURS * 3600
            self._symbol_pauses[symbol] = max(
                until, self._symbol_pauses.get(symbol, 0.0)
            )
            logger.error(
                f"[Risk/Hazard] {symbol} paused {self.REJECT_PAUSE_HOURS}h "
                f"after {len(tracker[symbol])} rejections in "
                f"{self.REJECT_WINDOW_MIN}min (last: {reason[:80]})"
            )
            tracker[symbol] = []
            self._save_state()

    def _write_review_flag(self, reason: str, action: str, **meta) -> None:
        """Append an entry to data/review_required.json (list of events)."""
        try:
            existing = []
            if _REVIEW_FLAG_PATH.exists():
                try:
                    existing = json.loads(_REVIEW_FLAG_PATH.read_text(encoding="utf-8"))
                    if not isinstance(existing, list):
                        existing = []
                except json.JSONDecodeError:
                    existing = []
            existing.append({
                "ts": _time.time(),
                "reason": reason,
                "action": action,
                **meta,
            })
            _REVIEW_FLAG_PATH.parent.mkdir(parents=True, exist_ok=True)
            _REVIEW_FLAG_PATH.write_text(
                json.dumps(existing[-100:], indent=2), encoding="utf-8"
            )
        except Exception as e:
            logger.warning(f"[Risk] Could not write review flag: {e}")

    # ── Properties ───────────────────────────────────────────────────

    @property
    def is_halted(self) -> bool:
        self.roll_day_if_needed()
        reason = self._persistent_breaker_reason()
        if reason:
            self._last_halt_reason = reason
        else:
            self._last_halt_reason = ""
        return bool(reason)

    @property
    def halt_reason(self) -> str:
        self.roll_day_if_needed()
        reason = self._persistent_breaker_reason()
        if reason:
            self._last_halt_reason = reason
            return reason
        return self._last_halt_reason

    @property
    def daily_pnl(self) -> float:
        return self._daily_pnl

    @property
    def peak_balance(self) -> float:
        return self._peak_balance

    def dynamic_min_rr(self, regime: str = "trending", atr_pct: float = 0.0) -> float:
        """
        Regime-adaptive minimum R:R ratio.
        - Trending: standard 1.5:1 (reliable direction, normal SL width)
        - Ranging:  lower 1.2:1 (mean reversion has higher WR, shorter targets)
        - Volatile: higher 2.0:1 (wider SL needed, demand bigger reward)
        - Breakout: 1.8:1 (high reward potential but higher false-breakout risk)
        """
        base = RISK.get("min_rr_ratio", 1.8)
        if regime == "ranging":
            return max(1.2, base * 0.8)
        elif regime in ("volatile", "vol_extreme"):
            return max(2.0, base * 1.3)
        elif regime == "breakout":
            return max(1.8, base * 1.2)
        return base

    def drawdown_pct(self, current_balance: float) -> float:
        if self._peak_balance <= 0:
            return 0.0
        return max(0.0, (self._peak_balance - current_balance) / self._peak_balance)
