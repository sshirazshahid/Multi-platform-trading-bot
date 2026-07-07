"""
core/trailing_stop_manager.py — PEAK CAPTURE with BREAKEVEN GUARANTEE

Key principles:
  1. Once trailing activates (profit >= 0.8%), SL moves to breakeven MINIMUM
  2. Breakeven = entry price + total estimated fees (never close at net loss)
  3. As profit grows, trailing locks 70% of peak profit (above breakeven floor)
  4. Peaks persisted to data/trailing_peaks.json — survives restarts

Result: After activation, every trailing stop close is guaranteed net-positive.
"""
import json
from pathlib import Path

from loguru import logger

from config import RISK
from utils.atomic_io import atomic_write_json

PEAKS_FILE = Path("data/trailing_peaks.json")
PARAMS_FILE = Path("data/trailing_params.json")


def _load_fitted_params() -> tuple[float | None, float | None]:
    """Load (activation_pct, lock_fraction) from data/trailing_params.json
    if it exists. Falls back to (None, None) so callers can use defaults."""
    try:
        if PARAMS_FILE.exists():
            data = json.loads(PARAMS_FILE.read_text(encoding="utf-8"))
            a = data.get("activation_pct")
            L = data.get("lock_fraction")
            if a is not None and L is not None:
                return float(a), float(L)
    except Exception:
        pass
    return None, None


def _fee_rate(market_type: str) -> float:
    """Estimate round-trip fee rate (entry + exit)."""
    try:
        from config import FEE
        if market_type == "futures":
            return FEE.get("futures_maker", 0.0002) + FEE.get("futures_taker", 0.0005)
        return FEE.get("spot_maker", 0.001) + FEE.get("spot_taker", 0.001)
    except Exception:
        return 0.0007 if market_type == "futures" else 0.002


class TrailingStopManager:
    def __init__(self):
        self._tracking = self._load_peaks()
        # self.activation retained for backwards compat, but _adaptive_activation
        # reads RISK live on every call so config edits take immediate effect.
        self.activation = RISK.get("trailing_activation", 0.008)
        # Phase 2 / Task D: prefer fitted params from data/trailing_params.json
        # when present; fall back to RISK defaults. _lock_override applies
        # only to the BASE lock fraction; the graduated _lock_fraction(peak_pnl)
        # tiers still scale on top of it.
        fitted_a, fitted_L = _load_fitted_params()
        if fitted_a is not None:
            self.activation = fitted_a
        self._lock_override = fitted_L  # None when not fitted
        self.enabled = RISK.get("trailing_stop", True)
        if self._tracking:
            logger.info(f"[Trail] Loaded {len(self._tracking)} peaks from disk")
        if fitted_a is not None or fitted_L is not None:
            logger.info(
                f"[Trail] Fitted params loaded: activation={self.activation:.3%} "
                f"lock_override={self._lock_override}")

    def _breakeven_price(self, position, side: str) -> float:
        """Calculate breakeven price including round-trip fees.
        This is the MINIMUM trailing SL — never go below this."""
        ep = position.entry_price
        fee_pct = _fee_rate(position.market_type)
        # Breakeven = entry + fees (buy) or entry - fees (sell)
        # Add small buffer (0.05%) to ensure net-positive after slippage
        buffer = 0.0005
        if side == "buy":
            return ep * (1 + fee_pct + buffer)
        else:
            return ep * (1 - fee_pct - buffer)

    def _adaptive_activation(self, position) -> float:
        """
        Volatility-adaptive trailing activation threshold.
        High-vol assets need more room before trailing activates;
        low-vol assets can activate sooner.

        Reads RISK["trailing_activation"] live on every call so config
        reloads (or runtime edits to RISK) take effect without bot restart.

        2026-07-07: the Phase 15.3 age-aware overrides (drop to 0.5% at 50min,
        to ~0 at 65min) were REMOVED. They were calibrated against a 75-min
        AGE_LIMIT force-close that no longer exists (AGE_LIMIT has been 4h,
        loss-only, since Phase 14), and the 65-min tier had become an
        insta-closer: any position that ever ticked above entry activated on a
        ~0 threshold, the breakeven floor placed the SL above the current
        price, and the SAME update closed the position at market. 30d
        warehouse: 161 of 258 trailing exits (6% win rate, -$52) were this
        mechanism, pre-empting both TP and SL on near-flat positions.
        """
        base = RISK.get("trailing_activation", 0.008)  # default 0.8%
        # Use ATR if available on the position
        atr_pct = getattr(position, 'atr_pct', 0)
        if atr_pct and atr_pct > 0:
            adaptive = atr_pct * 1.1
            base = max(base * 0.5, min(adaptive, base * 2.0))
        return base

    def update(self, position, current_price: float) -> tuple:
        if not self.enabled:
            return False, "", position.stop_loss
        pid = position.id
        side = position.side.lower()
        sl = position.stop_loss
        ep = position.entry_price
        be = self._breakeven_price(position, side)
        activation_threshold = self._adaptive_activation(position)
        if pid not in self._tracking:
            self._tracking[pid] = {
                "peak": current_price, "trough": current_price,
                "active": False, "peak_pnl": 0.0, "symbol": position.symbol,
                "breakeven": be,
            }
        t = self._tracking[pid]
        t["breakeven"] = be  # Update in case fees changed
        dirty = False

        if side == "buy":
            if current_price > t["peak"]:
                t["peak"] = current_price
                t["peak_pnl"] = (current_price - ep) / ep
                dirty = True
            peak_pnl = t["peak_pnl"]
            # Guard (2026-07-07): only activate while the price is actually
            # past breakeven. Activating on a HISTORICAL peak (persisted peaks
            # after a restart, or a live threshold edit) with the price below
            # breakeven lets the be-floor place the SL above the market and
            # close the position on the very same update.
            if peak_pnl >= activation_threshold and not t["active"] and current_price > be:
                t["active"] = True; dirty = True
                logger.info(
                    f"[Trail] {position.symbol} BUY activated: "
                    f"profit={peak_pnl*100:.1f}% (thresh={activation_threshold*100:.1f}%) "
                    f"— SL locked to breakeven {be:.6g}")
            if t["active"]:
                trail_pct = self._trail_distance(peak_pnl)
                trail_sl = round(t["peak"] * (1 - trail_pct), 8)
                lock_frac = self._lock_fraction(peak_pnl, position)
                lock_sl = ep * (1 + peak_pnl * lock_frac)
                # Breakeven floor: NEVER let trailing SL go below breakeven
                new_sl = max(trail_sl, lock_sl, be)
                if new_sl > sl:
                    sl = new_sl
            if current_price <= sl and t["active"]:
                locked = (sl - ep) / ep * 100; peak = peak_pnl * 100
                net_pct = (sl - ep) / ep * 100 - _fee_rate(position.market_type) * 100
                logger.info(
                    f"[Trail] {position.symbol} BUY CLOSE: peak={peak:.1f}% "
                    f"locked={locked:.1f}% net~{net_pct:+.1f}%")
                self._tracking.pop(pid, None); self._save_peaks()
                return True, "trailing_stop", sl
        else:
            if current_price < t["trough"]:
                t["trough"] = current_price
                t["peak_pnl"] = (ep - current_price) / ep
                dirty = True
            peak_pnl = t["peak_pnl"]
            # Guard (2026-07-07): mirror of the buy branch — no activation
            # while the price sits above breakeven (shorts profit downward).
            if peak_pnl >= activation_threshold and not t["active"] and current_price < be:
                t["active"] = True; dirty = True
                logger.info(
                    f"[Trail] {position.symbol} SELL activated: "
                    f"profit={peak_pnl*100:.1f}% (thresh={activation_threshold*100:.1f}%) "
                    f"— SL locked to breakeven {be:.6g}")
            if t["active"]:
                trail_pct = self._trail_distance(peak_pnl)
                trail_sl = round(t["trough"] * (1 + trail_pct), 8)
                lock_frac = self._lock_fraction(peak_pnl, position)
                lock_sl = ep * (1 - peak_pnl * lock_frac)
                # Breakeven floor: NEVER let trailing SL go above breakeven (for shorts)
                new_sl = min(trail_sl, lock_sl, be)
                if new_sl < sl:
                    sl = new_sl
            if current_price >= sl and t["active"]:
                locked = (ep - sl) / ep * 100; peak = peak_pnl * 100
                net_pct = (ep - sl) / ep * 100 - _fee_rate(position.market_type) * 100
                logger.info(
                    f"[Trail] {position.symbol} SELL CLOSE: peak={peak:.1f}% "
                    f"locked={locked:.1f}% net~{net_pct:+.1f}%")
                self._tracking.pop(pid, None); self._save_peaks()
                return True, "trailing_stop", sl
        if dirty: self._save_peaks()
        return False, "", sl

    def _lock_fraction(self, peak_pnl: float, position=None) -> float:
        """Graduated profit lock — protect bigger winners more aggressively.

        When `data/trailing_params.json` provided a fitted base lock fraction
        we honour it for the small/medium-win tiers (where the fit dominates)
        and let the high-tier protections take over for exceptional winners
        (those tiers are sample-poor — only 4 historical TP hits at the time
        of writing — so the fit cannot speak to them).

        Phase 26A (2026-05-05) — winner age-lock. Audit of 267 closed
        trades found the 4h+ hold bucket bled $-41.71 (63% of total
        bleed): winners profitable at hour 1-2 reversed by hour 4 because
        the default 65% lock left too much room. When a position has
        been open >2h AND has any unrealized profit, ratchet lock to
        95% so reversal locks in near-peak instead of giving back the
        whole move. Targets the structural "MCP monitor exits on
        weakness with worse price" pattern (31 losses for $-21 in
        mcp_brain_close).
        """
        if position is not None:
            try:
                import time as _t
                age_min = (_t.time() - getattr(position, "open_time", _t.time())) / 60.0
            except Exception:
                age_min = 0.0
            if age_min >= 120 and peak_pnl > 0:
                return 0.95
        if getattr(self, "_lock_override", None) is not None and peak_pnl < 0.05:
            return float(self._lock_override)
        return self.__class__._lock_fraction_default(peak_pnl)

    @staticmethod
    def _lock_fraction_default(peak_pnl: float) -> float:
        """Hardcoded graduated lock table — used when no fit is available.

        2026-04-24 retune: lowered low-peak locks so winners have room to reach
        target TP instead of exiting at ~0.6% clipped profit. Prior config
        (0.60/0.70/0.75/0.80) produced 55W trailing @ $0.09 avg vs 4 full TPs
        @ $2.84 — trailing was intercepting wins before they matured.

        2026-04-28 (Phase 11) re-retune: 30-day data showed the 04-24 numbers
        UNDER-locked. Trailing exits clustered at +0.6% gross / +0.4% net,
        right at the cost-floor boundary. mcp_take_profit (which has a 0.5%
        net-PnL filter at bot_engine.py:2535) lands at +1.4% net — 47% better.
        Raising the small-win lock 0.40→0.55 converges the trailing exit
        distribution on mcp_take_profit's, without copying the LLM-in-loop
        decision path.
            At peak 2.0%, lock 0.55 → SL = +1.1% gross = +0.9% net
            At peak 3.0%, lock 0.55 → SL = +1.65% gross = +1.45% net
                                       (matches mcp_take_profit median 1.47%)

        2026-05-03 (Phase 15): with AGE_LIMIT cut to 75min, trailing must
        lock faster or positions force-close before lock takes hold.
        Small-win tier 0.55 → 0.65 (lock more under shorter horizon).
        Medium tier 0.55 → 0.65 to match.
        Good/Exceptional left alone — those rare events still need room.

        Low profit  (< 3%):  lock 65% — clears cost floor + faster lock-in
        Medium      (3-5%):  lock 65% — match small-win for consistency
        Good        (5-8%):  lock 70% — tighter lock
        Exceptional (> 8%):  lock 80% — protect the big win"""
        if peak_pnl < 0.03:
            return 0.65
        elif peak_pnl < 0.05:
            return 0.65
        elif peak_pnl < 0.08:
            return 0.70
        else:
            return 0.80

    def _trail_distance(self, peak_pnl: float) -> float:
        """Adaptive trail distance — wider to survive normal retracements.
        Old values (0.4-0.8%) were within normal candle noise and exited winners prematurely.
        New values give positions room to breathe while still locking significant profit."""
        base = RISK.get("trailing_distance", 0.012)
        if peak_pnl < 0.03:   return base          # 1.2% — just activated, standard trail
        elif peak_pnl < 0.05: return base * 0.85    # 1.0% — moderate profit, slightly tighter
        elif peak_pnl < 0.08: return base * 0.75    # 0.9% — good profit, lock more
        elif peak_pnl < 0.12: return base * 0.70    # 0.84% — great profit, continue tightening
        elif peak_pnl < 0.20: return base * 0.65    # 0.78% — big winner, protect gains
        else:                 return base * 0.60    # 0.72% — exceptional winner, tight protection

    def remove(self, position_id: str):
        self._tracking.pop(position_id, None); self._save_peaks()

    def status(self) -> dict:
        return {pid: dict(t) for pid, t in self._tracking.items()}

    def _save_peaks(self):
        try:
            PEAKS_FILE.parent.mkdir(parents=True, exist_ok=True)
            # Atomic write (tmp + os.replace) — a torn trailing_peaks.json loses
            # every live peak on restart (TD-2, plan 2026-06-12; mirrors risk_manager).
            atomic_write_json(PEAKS_FILE, self._tracking, indent=2, default=str)
        except Exception as e:
            logger.debug(f"[Trail] Save: {e}")

    def _load_peaks(self) -> dict:
        try:
            if PEAKS_FILE.exists():
                data = json.loads(PEAKS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict): return data
        except Exception: pass
        return {}
