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

PEAKS_FILE = Path("data/trailing_peaks.json")


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
        self.activation = RISK.get("trailing_activation", 0.008)
        self.enabled = RISK.get("trailing_stop", True)
        if self._tracking:
            logger.info(f"[Trail] Loaded {len(self._tracking)} peaks from disk")

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

    def update(self, position, current_price: float) -> tuple:
        if not self.enabled:
            return False, "", position.stop_loss
        pid = position.id
        side = position.side.lower()
        sl = position.stop_loss
        ep = position.entry_price
        be = self._breakeven_price(position, side)
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
            if peak_pnl >= self.activation and not t["active"]:
                t["active"] = True; dirty = True
                logger.info(
                    f"[Trail] {position.symbol} BUY activated: "
                    f"profit={peak_pnl*100:.1f}% — SL locked to breakeven {be:.6g}")
            if t["active"]:
                trail_pct = self._trail_distance(peak_pnl)
                trail_sl = round(t["peak"] * (1 - trail_pct), 8)
                lock_sl = ep * (1 + peak_pnl * 0.70)
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
            if peak_pnl >= self.activation and not t["active"]:
                t["active"] = True; dirty = True
                logger.info(
                    f"[Trail] {position.symbol} SELL activated: "
                    f"profit={peak_pnl*100:.1f}% — SL locked to breakeven {be:.6g}")
            if t["active"]:
                trail_pct = self._trail_distance(peak_pnl)
                trail_sl = round(t["trough"] * (1 + trail_pct), 8)
                lock_sl = ep * (1 - peak_pnl * 0.70)
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

    def _trail_distance(self, peak_pnl: float) -> float:
        """Adaptive trail distance — wider to survive normal retracements.
        Old values (0.4-0.8%) were within normal candle noise and exited winners prematurely.
        New values give positions room to breathe while still locking significant profit."""
        base = RISK.get("trailing_distance", 0.012)
        if peak_pnl < 0.03:   return base          # 1.2% — just activated, standard trail
        elif peak_pnl < 0.05: return base * 0.85    # 1.0% — moderate profit, slightly tighter
        elif peak_pnl < 0.08: return base * 0.75    # 0.9% — good profit, lock more
        elif peak_pnl < 0.12: return base           # 1.2% — let winners breathe
        elif peak_pnl < 0.20: return base * 1.25    # 1.5% — big winner, give room
        else:                 return base * 1.5     # 1.8% — exceptional winner, let it ride

    def remove(self, position_id: str):
        self._tracking.pop(position_id, None); self._save_peaks()

    def status(self) -> dict:
        return {pid: dict(t) for pid, t in self._tracking.items()}

    def _save_peaks(self):
        try:
            PEAKS_FILE.parent.mkdir(parents=True, exist_ok=True)
            PEAKS_FILE.write_text(json.dumps(self._tracking, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Trail] Save: {e}")

    def _load_peaks(self) -> dict:
        try:
            if PEAKS_FILE.exists():
                data = json.loads(PEAKS_FILE.read_text(encoding="utf-8"))
                if isinstance(data, dict): return data
        except Exception: pass
        return {}
