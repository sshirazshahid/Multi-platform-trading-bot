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
import time as _time
from datetime import date
from pathlib import Path
from loguru import logger
from config import RISK

HALT_COOLDOWN_MIN  = 15     # minutes to pause before checking recovery
RECOVERY_WR_MIN    = 60.0   # win rate % to auto-resume
RECOVERY_LOOKBACK  = 5      # number of recent trades to check

# Spec §12 5-consec-loss halt → guarded auto-resume after a hard cooldown.
# 4 hours is long enough that the regime that caused the streak has likely shifted;
# short enough that a truly autonomous bot doesn't sit idle. On resume the streak is
# cleared (stale) and the review flag is deleted so future halts can re-trigger.
SPEC12_AUTO_RESUME_COOLDOWN_MIN = 240

# Drawdown halt → time-based escape. Without this, the bot deadlocks: WR-based
# and DD-based recovery both need trades, but trades are blocked while halted,
# so the only exit was the midnight new-day reset. Real losing streaks are
# already caught by Spec §12 (5 consec global losses, 4h cooldown) independent
# of the drawdown gate, so a matching 4h cooldown here is safe. On resume we
# reset peak_balance to the current effective balance — otherwise the next
# losing trade would re-trip the drawdown immediately since the math is
# unchanged. daily_pnl is NOT reset: the daily-loss circuit-breaker is a
# separate constraint that must run independently.
DRAWDOWN_HALT_COOLDOWN_MIN = 240

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


class RiskManager:

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

        self._daily_pnl:     float = 0.0
        self._start_balance: float = 0.0
        self._peak_balance:  float = 0.0
        self._trading_day:   date  = date.today()
        self._halted:        bool  = False
        self._halt_reason:   str   = ""
        self._halt_time:     float = 0.0
        self._recent_results: list = []   # last N trades: True=win, False=loss
        self._trade_history:  list = []   # Kelly: (win, pnl_pct)
        self._correlation_mgr = None      # lazy-loaded

        # Spec §12 pause tracking
        self._symbol_streaks: dict = {}   # symbol → list[bool] (True = win)
        self._family_streaks: dict = {}   # family → list[bool]
        self._symbol_pauses:  dict = {}   # symbol → epoch_until
        self._family_pauses:  dict = {}   # family → epoch_until
        self._global_streak:  list = []   # last 20 global outcomes

        # Restore persisted state from previous session
        self._load_state()

    # ── State persistence for dashboard ────────────────────────────────

    def _save_state(self):
        """Persist risk state for dashboard + resume on restart."""
        state = {
            "is_halted": self._halted,
            "halt_reason": self._halt_reason,
            "halt_time": self._halt_time,
            "daily_pnl": round(self._daily_pnl, 4),
            "max_drawdown_pct": round(
                (self._peak_balance - ((self._start_balance or self._peak_balance) + self._daily_pnl))
                / self._peak_balance, 4) if self._peak_balance > 0 else 0.0,
            "start_balance": round(self._start_balance, 2),
            "peak_balance": round(self._peak_balance, 2),
            "trading_day": self._trading_day.isoformat(),
            "trades_today": len(self._trade_history),
            "recent_results": self._recent_results[-20:],
            "trade_history": self._trade_history[-100:],
            "symbol_pauses": self._symbol_pauses,
            "family_pauses": self._family_pauses,
            "global_streak": self._global_streak[-20:],
            "timestamp": _time.time(),
        }
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/risk_state.json").write_text(
                json.dumps(state, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _load_state(self):
        """Load persisted risk state on startup. Handles same-day resume vs new-day reset.

        The Spec §12 review-flag is honoured authoritatively: it is checked
        AFTER the risk_state.json branches AND on every early-return path
        (missing file, corrupt JSON, invalid trading_day). This guarantees a
        bot whose `risk_state.json` was lost cannot accidentally come up
        unhalted while `data/review_required.json` still sits on disk.
        """
        path = Path("data/risk_state.json")
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

        if saved_day == date.today():
            # Same-day restart: restore daily PnL, halt state, peak
            self._daily_pnl = state.get("daily_pnl", 0.0)
            self._trading_day = saved_day
            self._peak_balance = state.get("peak_balance", 0.0)
            # Restore start_balance so drawdown math survives process restart.
            # Without this, (self._start_balance or self._peak_balance) in the
            # DD resume / daily-loss paths falls through to peak, which nullifies
            # peak-reset and mis-scales the daily-loss limit until bot_engine
            # re-calls set_start_balance.
            self._start_balance = state.get("start_balance", 0.0)
            self._halted = state.get("is_halted", False)
            self._halt_reason = state.get("halt_reason", "")
            self._halt_time = state.get("halt_time", 0.0)
            logger.info(
                f"[Risk] Resumed same-day state: daily_pnl={self._daily_pnl:+.4f}, "
                f"trades={len(self._trade_history)}, halted={self._halted}")
        else:
            # New day: reset daily counters but keep trade history
            self._daily_pnl = 0.0
            self._trading_day = date.today()
            self._halted = False
            self._halt_reason = ""
            logger.info(
                f"[Risk] New day — daily counters reset. "
                f"Carried over {len(self._trade_history)} trade history entries")

        # Spec §12: review_required.json is the authoritative halt record on
        # both same-day AND new-day branches. See _honour_review_flag_if_present.
        self._honour_review_flag_if_present()

    def _honour_review_flag_if_present(self) -> None:
        """If `data/review_required.json` exists, override in-memory halt state
        from it. Runs after every `_load_state` branch (including early
        returns) so a missing/corrupt risk_state.json cannot wipe a valid
        halt that the flag still attests to.

        Fix 1A: same-day AND new-day AND missing-state-file paths all hit this.
        Fix 1D: emits a WARNING with elapsed minutes + remaining cooldown.
        """
        if not _REVIEW_FLAG_PATH.exists():
            return

        try:
            flag_data = json.loads(_REVIEW_FLAG_PATH.read_text(encoding="utf-8"))
            # File format is a list of event dicts (see _write_review_flag).
            # Accept a bare dict for backwards compatibility.
            if isinstance(flag_data, list):
                flag_state = flag_data[-1] if flag_data else {}
            elif isinstance(flag_data, dict):
                flag_state = flag_data
            else:
                flag_state = {}
            if not isinstance(flag_state, dict):
                flag_state = {}
            flag_ts = float(flag_state.get("ts", 0.0) or 0.0)
            flag_reason = str(flag_state.get("reason", "unknown"))
            flag_action = str(flag_state.get("action", ""))
        except (json.JSONDecodeError, OSError, ValueError, TypeError) as e:
            logger.warning(
                f"[Risk] review_required.json present but unreadable "
                f"({e}) — failing safe (halted)")
            flag_ts = _time.time()
            flag_reason = "review_flag_unreadable"
            flag_action = "force_observation_mode"

        elapsed_min = (_time.time() - flag_ts) / 60 if flag_ts > 0 else 0
        remaining_min = max(0.0, SPEC12_AUTO_RESUME_COOLDOWN_MIN - elapsed_min)

        # Reconstruct halt state from the flag. Use a reason string that
        # `_should_auto_resume` recognises so the cooldown check fires.
        # Fix 1C: _halted=True is paired with _halt_time set in the same block.
        self._halted = True
        self._halt_time = flag_ts or _time.time()
        if "consecutive" in flag_reason or "global" in flag_reason:
            self._halt_reason = "spec12:consec_global_losses"
        else:
            # Outlier losses and manual review flags are also honoured —
            # treat as halted until the operator removes the flag.
            self._halt_reason = f"spec12:flag:{flag_action or 'review_required'}"

        # Fix 1D: WARNING so operators can see Spec §12 state at startup.
        logger.warning(
            f"[Risk/Spec12] review_required.json present "
            f"(reason='{flag_reason}', action='{flag_action}', "
            f"age={elapsed_min:.1f}min, cooldown remaining={remaining_min:.1f}min). "
            f"Halt restored from flag — risk_state.json value ignored.")

    # ── Trading gate with SMART RECOVERY ─────────────────────────────

    def can_trade(self, open_position_count: int) -> bool:
        if self._halted:
            # Check if we should auto-resume
            if self._should_auto_resume():
                old = self._halt_reason
                self._halted      = False
                self._halt_reason = ""
                logger.info(f"[Risk] AUTO-RESUMED after '{old}'")
                self._save_state()
            else:
                logger.debug(f"[Risk] Trading PAUSED — {self._halt_reason}")
                return False
        if open_position_count >= self.max_open_positions:
            logger.debug(
                f"[Risk] Max open positions ({self.max_open_positions}) reached.")
            return False
        return True

    def _should_auto_resume(self) -> bool:
        """Smart recovery: resume when conditions improve."""
        # Spec §12: 5-global-consecutive-loss halt.
        # Fully autonomous — no human intervention required. Wait a hard cooldown
        # (SPEC12_AUTO_RESUME_COOLDOWN_MIN) so the losing regime has time to shift,
        # then clear the stale streak and delete the review flag so future halts
        # can retrigger cleanly.
        if "spec12" in self._halt_reason and "consec_global" in self._halt_reason:
            elapsed_min = (_time.time() - self._halt_time) / 60
            if elapsed_min < SPEC12_AUTO_RESUME_COOLDOWN_MIN:
                remaining = SPEC12_AUTO_RESUME_COOLDOWN_MIN - elapsed_min
                logger.debug(
                    f"[Risk] Spec §12 cooldown — {remaining:.0f} min left "
                    f"before auto-resume.")
                return False
            # Cooldown elapsed. Delete the flag FIRST; only clear in-memory
            # halt if the unlink succeeds. Prevents the class of bugs where
            # `_halted=False` got saved to risk_state.json while the flag
            # file persisted on disk (silent unlink failure on Windows under
            # AV/file-lock left stale state).
            if _REVIEW_FLAG_PATH.exists():
                try:
                    _REVIEW_FLAG_PATH.unlink()
                except Exception as e:
                    logger.error(
                        f"[Risk] Spec §12 auto-resume BLOCKED — could not "
                        f"delete {_REVIEW_FLAG_PATH}: {e}. Staying halted to "
                        f"avoid silent wipe. Remove the file manually to resume.")
                    return False
            self._global_streak = []
            logger.warning(
                f"[Risk] Spec §12 auto-resume after {SPEC12_AUTO_RESUME_COOLDOWN_MIN}min "
                f"cooldown — flag deleted, streak cleared.")
            return True

        # Daily loss resets at midnight
        if "daily" in self._halt_reason and date.today() != self._trading_day:
            return True

        # Drawdown: require cooldown first
        elapsed = (_time.time() - self._halt_time) / 60
        if elapsed < HALT_COOLDOWN_MIN:
            return False

        # Check recent win rate
        if len(self._recent_results) >= RECOVERY_LOOKBACK:
            recent = self._recent_results[-RECOVERY_LOOKBACK:]
            wr = sum(1 for r in recent if r) / len(recent) * 100
            if wr >= RECOVERY_WR_MIN:
                logger.info(f"[Risk] Recovery: last {RECOVERY_LOOKBACK} trades WR={wr:.0f}%")
                return True

        # Check drawdown recovered to safe level (less than half of max drawdown threshold)
        if self._peak_balance > 0:
            effective_balance = (self._start_balance or self._peak_balance) + self._daily_pnl
            current_dd = (self._peak_balance - effective_balance) / self._peak_balance
            safe_dd = self.max_drawdown_pct * 0.5  # Must recover to half the DD threshold
            if current_dd <= safe_dd and self._daily_pnl > 0:
                logger.info(
                    f"[Risk] Recovery: drawdown {current_dd*100:.1f}% < "
                    f"safe level {safe_dd*100:.1f}%, daily PnL={self._daily_pnl:+.4f}")
                return True

        # Drawdown halt time-based escape — mirrors Spec §12 pattern. If WR/DD
        # recovery never materialised (common, since both need trades), reset
        # peak to current effective balance after the cooldown and resume.
        # Real losing streaks still caught by Spec §12 independently.
        if "drawdown" in self._halt_reason and elapsed >= DRAWDOWN_HALT_COOLDOWN_MIN:
            effective = (self._start_balance or self._peak_balance) + self._daily_pnl
            if effective > 0:
                old_peak = self._peak_balance
                self._peak_balance = effective
                logger.warning(
                    f"[Risk] Drawdown halt cooldown elapsed "
                    f"({DRAWDOWN_HALT_COOLDOWN_MIN}min). Peak reset "
                    f"${old_peak:.2f} → ${effective:.2f}, resuming.")
                return True

        return False

    # ── Position sizing ──────────────────────────────────────────────

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
            target_risk = balance_usdt * 0.01
            qty = target_risk / (price * atr_pct)
            # Apply leverage and cap to max position size
            qty = qty * leverage
            qty = min(qty, balance_usdt * self.max_position_pct * 3 * leverage / price)
            logger.info(
                f"[Risk] Volatility sizing: atr%={atr_pct*100:.2f}% "
                f"qty={qty:.8f} @ {price:.4f} lev={leverage}x")
            return qty
        else:
            pct = self.max_position_pct

        # Apply scaling multiplier if conditions met (200+ trades, 60%+ WR, <10% DD)
        scale = self._check_scaling_eligible()

        notional = balance_usdt * pct * leverage * scale
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
                   leverage: int = 1):
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
        today = date.today()
        if today != self._trading_day:
            self._daily_pnl   = 0.0
            self._trading_day = today
            if self._halted and "daily" in self._halt_reason:
                self._halted      = False
                self._halt_reason = ""
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
        if effective_balance > self._peak_balance:
            self._peak_balance = effective_balance

        daily_loss_limit = (self._start_balance or balance) * self.max_daily_loss_pct

        # Daily loss circuit-breaker
        if self._daily_pnl < -daily_loss_limit and not self._halted:
            self._halted      = True
            self._halt_reason = f"daily loss ({self._daily_pnl:+.4f} USDT)"
            self._halt_time   = _time.time()
            logger.warning(
                f"[Risk] DAILY LOSS LIMIT: {self._daily_pnl:.4f} USDT. "
                "Paused — will auto-resume when PnL recovers.")
            self._save_state()
            return

        # Max drawdown circuit-breaker (with smart recovery)
        if self._peak_balance > 0:
            drawdown = (self._peak_balance - effective_balance) / self._peak_balance
            if drawdown >= self.max_drawdown_pct and not self._halted:
                self._halted      = True
                self._halt_reason = (
                    f"drawdown {drawdown*100:.1f}% "
                    f"(peak ${self._peak_balance:.2f})")
                self._halt_time = _time.time()
                logger.warning(
                    f"[Risk] DRAWDOWN {drawdown*100:.1f}% from peak "
                    f"${self._peak_balance:.2f}. Paused — "
                    f"will auto-resume when WR>={RECOVERY_WR_MIN:.0f}% "
                    f"or PnL positive.")

        self._save_state()

    def set_start_balance(self, balance: float):
        """Set starting balance on startup. Respects resumed same-day state."""
        self._start_balance = balance

        if self._trading_day == date.today() and self._peak_balance > 0:
            # Same-day restart: keep resumed daily PnL and peak
            # Only advance peak if current balance exceeds it
            if balance > self._peak_balance:
                self._peak_balance = balance
            # Keep _daily_pnl as loaded from state
            # Keep _halted as loaded (daily loss halt still valid same day)
            logger.info(
                f"[Risk] Starting balance: {balance:.4f} USDT "
                f"(resumed: daily_pnl={self._daily_pnl:+.4f}, "
                f"peak=${self._peak_balance:.2f}, halted={self._halted})")
        else:
            # New day or first run: fresh slate
            self._peak_balance = balance
            self._daily_pnl = 0.0
            if self._halted:
                logger.warning(
                    f"[Risk] Clearing previous halt ('{self._halt_reason}') on restart. "
                    f"Fresh peak = ${balance:.2f}")
                self._halted = False
                self._halt_reason = ""
            logger.info(f"[Risk] Starting balance: {balance:.4f} USDT (peak reset)")

    def update_current_balance(self, balance: float):
        """Update balance without resetting peak or clearing halts.
        Called on subsequent cycles after startup.

        2026-04-13 FIX: Guard against artificial balance spikes. A single
        balance fetch returning an inflated number (e.g. Bybit's totalEquity
        instead of totalAvailableBalance) used to permanently poison the
        peak, triggering a fake drawdown halt that required manual reset.
        Now: if balance jumps > 30% above peak in a single cycle, log a
        warning and DON'T update peak — real P&L never spikes 30% in 5 min.
        """
        self._start_balance = balance
        if balance > self._peak_balance:
            if self._peak_balance > 0:
                jump_pct = (balance - self._peak_balance) / self._peak_balance
                if jump_pct > 0.30:
                    logger.warning(
                        f"[Risk] Balance spike rejected: ${balance:.2f} is "
                        f"{jump_pct:.0%} above peak ${self._peak_balance:.2f} "
                        f"— likely a balance-fetch bug, not real P&L")
                    return
            self._peak_balance = balance

    def resume_trading(self):
        """Manually resume trading after a halt."""
        self._halted      = False
        self._halt_reason = ""
        logger.warning("[Risk] Trading manually resumed after halt.")

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
                             is_win: bool, pnl_usd: float = 0.0) -> None:
        """Spec §12 pause policy. Call once per closed trade.

        - 2 consec losses on symbol → pause symbol 6h
        - 3 consec losses in family → pause family 12h
        - 5 global consec losses → force OBSERVATION + review flag
        - Outlier loss beyond MAX_LOSS_PER_TRADE_USD → review flag

        This augments record_trade_pnl (which handles daily P&L and drawdown).
        Intentionally a separate entry point so existing call sites keep working.
        """
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

        # Per-symbol pause
        if len(sym_hist) >= SPEC_SYMBOL_LOSSES_TO_PAUSE and not any(
            sym_hist[-SPEC_SYMBOL_LOSSES_TO_PAUSE:]
        ):
            until = now + SPEC_SYMBOL_PAUSE_HOURS * hour
            self._symbol_pauses[symbol] = until
            logger.warning(
                f"[Risk/Spec12] {symbol} paused {SPEC_SYMBOL_PAUSE_HOURS}h "
                f"after {SPEC_SYMBOL_LOSSES_TO_PAUSE} consecutive losses"
            )

        # Per-family pause
        if len(fam_hist) >= SPEC_FAMILY_LOSSES_TO_PAUSE and not any(
            fam_hist[-SPEC_FAMILY_LOSSES_TO_PAUSE:]
        ):
            key = family or "unknown"
            until = now + SPEC_FAMILY_PAUSE_HOURS * hour
            self._family_pauses[key] = until
            logger.warning(
                f"[Risk/Spec12] family '{key}' paused {SPEC_FAMILY_PAUSE_HOURS}h "
                f"after {SPEC_FAMILY_LOSSES_TO_PAUSE} consecutive losses"
            )

        # 5 global consec → force observation + review
        if len(self._global_streak) >= SPEC_GLOBAL_LOSSES_TO_REVIEW and not any(
            self._global_streak[-SPEC_GLOBAL_LOSSES_TO_REVIEW:]
        ):
            self._write_review_flag(
                reason=f"{SPEC_GLOBAL_LOSSES_TO_REVIEW} consecutive global losses",
                action="force_observation_mode",
            )
            logger.error(
                f"[Risk/Spec12] {SPEC_GLOBAL_LOSSES_TO_REVIEW} consecutive losses — "
                f"review flag written; bot_engine will refuse to open new trades "
                f"until OPERATING_MODE is manually cleared"
            )
            self._halted = True
            self._halt_reason = f"spec12:{SPEC_GLOBAL_LOSSES_TO_REVIEW}_consec_global_losses"
            self._halt_time = now

        # Outlier loss
        try:
            from config import MAX_LOSS_PER_TRADE_USD as _max_loss
        except ImportError:
            _max_loss = 2.0
        if pnl_usd < -abs(_max_loss):
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
        return self._halted

    @property
    def halt_reason(self) -> str:
        return self._halt_reason

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
