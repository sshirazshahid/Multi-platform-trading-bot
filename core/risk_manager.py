"""
core/risk_manager.py — Risk Management with Smart Recovery + Correlation Awareness

Features:
  - Dynamic SL/TP based on ATR percentile (wider in high-vol, tighter in low-vol)
  - Correlation-aware position sizing (reduces size for correlated assets)
  - Smart drawdown recovery (auto-resume when conditions improve)
  - Regime-adaptive leverage (reduce leverage in volatile regimes)
  - Peak balance resets on new session to avoid stale peaks
"""

import time as _time
from datetime import date
from loguru import logger
from config import RISK

HALT_COOLDOWN_MIN  = 15     # minutes to pause before checking recovery
RECOVERY_WR_MIN    = 60.0   # win rate % to auto-resume
RECOVERY_LOOKBACK  = 5      # number of recent trades to check


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

    # ── Trading gate with SMART RECOVERY ─────────────────────────────

    def can_trade(self, open_position_count: int) -> bool:
        if self._halted:
            # Check if we should auto-resume
            if self._should_auto_resume():
                old = self._halt_reason
                self._halted      = False
                self._halt_reason = ""
                logger.info(f"[Risk] AUTO-RESUMED after '{old}'")
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

        # Check daily PnL turned positive
        if self._daily_pnl > 0:
            logger.info(f"[Risk] Recovery: daily PnL={self._daily_pnl:+.4f} positive")
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
        elif self.sizing_mode == "volatility" and atr_pct:
            target_risk = balance_usdt * 0.01
            qty = target_risk / (price * atr_pct)
            qty = min(qty, balance_usdt * self.max_position_pct * 3 / price)
            logger.info(
                f"[Risk] Volatility sizing: atr%={atr_pct*100:.2f}% "
                f"qty={qty:.8f} @ {price:.4f}")
            return qty
        else:
            pct = self.max_position_pct

        notional = balance_usdt * pct * leverage
        qty      = notional / price

        # MIN NOTIONAL: exchanges reject < $5
        if notional < 5.50 and balance_usdt >= 10:
            notional = min(5.50, balance_usdt * 0.20 * leverage)
            qty = notional / price
            logger.debug(f"[Risk] Notional boosted to ${notional:.2f}")
        elif notional < 5.50:
            logger.debug(f"[Risk] Balance ${balance_usdt:.2f} too low")
            return 0.0

        logger.info(
            f"[Risk] [{self.sizing_mode}] balance={balance_usdt:.4f} "
            f"pct={pct*100:.2f}% notional={notional:.4f} USDT "
            f"qty={qty:.8f} lev={leverage}x")
        return qty

    def _kelly_fraction(self) -> float:
        if len(self._trade_history) < 10:
            return self.max_position_pct
        wins  = [t for t in self._trade_history if t[0]]
        loss  = [t for t in self._trade_history if not t[0]]
        if not wins or not loss:
            return self.max_position_pct
        win_rate = len(wins) / len(self._trade_history)
        avg_win  = sum(t[1] for t in wins)  / len(wins)
        avg_loss = abs(sum(t[1] for t in loss)) / len(loss)
        if avg_loss == 0:
            return self.max_position_pct
        kelly = (win_rate * avg_win - (1 - win_rate) * avg_loss) / avg_win
        if kelly <= 0:
            logger.debug(f"[Risk] Kelly negative ({kelly:.4f}) — blocking trade (negative edge)")
            return 0.0
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

        # DYNAMIC SL FLOOR: gentler scaling with leverage
        # Old formula: 3% + 0.8% per leverage step → at 5x = 6.2% floor
        # This DESTROYED ATR-based SL/TP by expanding SL 5-7x and making TPs unreachable
        # New formula: 1.5% base + 0.3% per leverage step → at 5x = 2.7% floor
        # ATR-based strategies already account for volatility; the floor is only
        # a safety net for cases where ATR data is stale or miscalculated
        base_floor = 0.015  # 1.5% minimum for spot (was 3% — too aggressive)
        if leverage > 1:
            base_floor = 0.015 + (leverage - 1) * 0.003  # 3x→2.1%, 5x→2.7% (was 6.2%)

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
                          is_win: bool = None, pnl_pct: float = 0.0):
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

        # Kelly history
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

    def set_start_balance(self, balance: float):
        """Set starting balance. Resets peak AND clears any halt state.
        Called once on startup — fresh session = clean slate."""
        self._start_balance = balance
        self._peak_balance  = balance
        self._daily_pnl     = 0.0
        # CRITICAL: Clear halt from previous session — previous DD is irrelevant
        # after restart because peak is reset to current real balance
        if self._halted:
            logger.warning(
                f"[Risk] Clearing previous halt ('{self._halt_reason}') on restart. "
                f"Fresh peak = ${balance:.2f}")
            self._halted      = False
            self._halt_reason = ""
        logger.info(f"[Risk] Starting balance: {balance:.4f} USDT (peak reset)")

    def resume_trading(self):
        """Manually resume trading after a halt."""
        self._halted      = False
        self._halt_reason = ""
        logger.warning("[Risk] Trading manually resumed after halt.")

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

    def drawdown_pct(self, current_balance: float) -> float:
        if self._peak_balance <= 0:
            return 0.0
        return max(0.0, (self._peak_balance - current_balance) / self._peak_balance)
