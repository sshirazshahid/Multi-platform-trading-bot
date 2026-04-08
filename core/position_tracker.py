"""
core/position_tracker.py — Track positions with fee-adjusted PnL.

Every trade is tagged paper_trade=True (DRY RUN) or False (LIVE).
This tag is used by the learning engine to separate dry run knowledge
from live knowledge and blend them correctly.

Fee structure:
  entry_fee  = size * entry_price * fee_rate
  exit_fee   = size * exit_price  * fee_rate
  gross_pnl  = price movement only
  net_pnl    = gross_pnl - entry_fee - exit_fee   ← real money kept/lost
"""

import json
import time
import threading
from pathlib     import Path
from dataclasses import dataclass, field, asdict
from typing      import Optional
from loguru      import logger


_DEFAULT_SPOT_FEE    = 0.001
_DEFAULT_FUTURES_FEE = 0.0005


def _fee_rate(market_type: str) -> float:
    try:
        from config import FEE
        if market_type == "futures":
            return FEE.get("futures_taker", _DEFAULT_FUTURES_FEE)
        return FEE.get("spot_taker", _DEFAULT_SPOT_FEE)
    except (ImportError, AttributeError):
        return _DEFAULT_FUTURES_FEE if market_type == "futures" \
               else _DEFAULT_SPOT_FEE


def _is_dry_run() -> bool:
    try:
        from config import DRY_RUN
        return DRY_RUN
    except (ImportError, AttributeError):
        return True


@dataclass
class Position:
    id:           str
    exchange:     str
    symbol:       str
    side:         str
    market_type:  str
    strategy:     str
    entry_price:  float
    size:         float
    stop_loss:    float
    take_profit:  float
    leverage:     int             = 1
    open_time:    float           = field(default_factory=time.time)
    close_time:   Optional[float] = None
    exit_price:   Optional[float] = None
    entry_fee:    float           = 0.0
    exit_fee:     float           = 0.0
    total_fees:   float           = 0.0
    gross_pnl:    Optional[float] = None
    pnl:          Optional[float] = None
    pnl_pct:      Optional[float] = None
    close_reason: Optional[str]   = None
    order_id:     Optional[str]   = None
    # ── Mode tag — this is the key field for learning separation ──────
    paper_trade:  bool            = field(default_factory=_is_dry_run)

    def __post_init__(self):
        rate           = _fee_rate(self.market_type)
        self.entry_fee = self.size * self.entry_price * rate
        self.total_fees = self.entry_fee

    def close(self, exit_price: float, reason: str):
        self.exit_price   = exit_price
        self.close_time   = time.time()
        self.close_reason = reason
        rate              = _fee_rate(self.market_type)
        self.exit_fee     = self.size * exit_price * rate
        self.total_fees   = self.entry_fee + self.exit_fee

        if self.side == "buy":
            self.gross_pnl = (exit_price - self.entry_price) * self.size * self.leverage
        else:
            self.gross_pnl = (self.entry_price - exit_price) * self.size * self.leverage

        self.pnl     = self.gross_pnl - self.total_fees
        notional     = self.entry_price * self.size
        self.pnl_pct = (self.pnl / notional * 100) if notional > 0 else 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def duration_minutes(self) -> float:
        end = self.close_time or time.time()
        return (end - self.open_time) / 60

    @property
    def notional(self) -> float:
        return self.size * self.entry_price


class PositionTracker:

    SAVE_PATH = Path("data/positions.json")
    BACKUP_PATH = Path("data/positions.json.bak")

    def __init__(self):
        self._open:   dict = {}
        self._closed: list = []
        self._lock = threading.Lock()
        self._load()

    def add(self, position: Position):
        with self._lock:
            self._open[position.id] = position
            tag = "[PAPER]" if position.paper_trade else "[LIVE]"
            logger.info(
                f"[Positions] {tag} OPENED {position.side.upper()} "
                f"{position.symbol} @ {position.entry_price:.4f} | "
                f"size={position.size:.6f} notional={position.notional:.2f} USDT | "
                f"entry_fee={position.entry_fee:.4f} USDT | id={position.id}"
            )
            self._save()

    def close(self, position_id: str, exit_price: float, reason: str):
        with self._lock:
            pos = self._open.pop(position_id, None)
            if not pos:
                logger.warning(f"[Positions] Unknown id: {position_id}")
                return None
            pos.close(exit_price, reason)
            self._closed.append(pos)
            # Trim closed list to prevent unbounded memory growth
            if len(self._closed) > 500:
                self._closed = self._closed[-500:]

            tag  = "[PAPER]" if pos.paper_trade else "[LIVE]"
            sign = "+" if pos.pnl >= 0 else ""
            logger.info(
                f"[Positions] {tag} CLOSED {pos.symbol} | "
                f"Gross: {pos.gross_pnl:+.4f} USDT | "
                f"Fees: -{pos.total_fees:.4f} USDT "
                f"(in={pos.entry_fee:.4f} out={pos.exit_fee:.4f}) | "
                f"Net PnL: {sign}{pos.pnl:.4f} USDT ({pos.pnl_pct:+.2f}%) | "
                f"reason={reason}"
            )
            self._save()
            return pos

    def get_open(self, exchange: str = None, symbol: str = None) -> list:
        positions = list(self._open.values())
        if exchange:
            positions = [p for p in positions
                         if p.exchange.lower() == exchange.lower()]
        if symbol:
            positions = [p for p in positions if p.symbol == symbol]
        return positions

    def count_open(self, exchange: str = None) -> int:
        if exchange:
            return sum(1 for p in self._open.values()
                       if p.exchange.lower() == exchange.lower())
        return len(self._open)

    def summary(self) -> dict:
        total      = len(self._closed)
        wins       = [p for p in self._closed if (p.pnl or 0) > 0]
        losses     = [p for p in self._closed if (p.pnl or 0) <= 0]
        total_pnl  = sum(p.pnl        or 0 for p in self._closed)
        gross_pnl  = sum(p.gross_pnl  or 0 for p in self._closed)
        total_fees = sum(p.total_fees  or 0 for p in self._closed)
        win_rate   = (len(wins) / total * 100) if total > 0 else 0
        avg_win    = (sum(p.pnl for p in wins)   / len(wins))   if wins   else 0
        avg_loss   = (sum(p.pnl for p in losses) / len(losses)) if losses else 0
        paper_n    = sum(1 for p in self._closed if p.paper_trade)
        live_n     = total - paper_n
        return {
            "total_trades":   total,
            "paper_trades":   paper_n,
            "live_trades":    live_n,
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       round(win_rate, 2),
            "gross_pnl":      round(gross_pnl, 4),
            "total_fees":     round(total_fees, 4),
            "total_pnl":      round(total_pnl, 4),
            "avg_win":        round(avg_win, 4),
            "avg_loss":       round(avg_loss, 4),
            "open_positions": self.count_open(),
        }

    def sync_with_exchanges(self, active_exchanges: dict):
        """Verify open LIVE positions still exist on the exchange.
        Auto-closes ghost positions that were closed/liquidated on the exchange
        but the tracker never got updated (network failure, crash, etc.)."""
        if not self._open:
            return

        live_open = [p for p in self._open.values() if not p.paper_trade]
        if not live_open:
            return

        # Collect all active positions from exchanges
        exchange_positions = {}  # (exchange_lower, symbol, side) -> True
        for ex_name, exchange in active_exchanges.items():
            try:
                positions = exchange.fetch_positions()
                for ep in positions:
                    size = float(ep.get("contracts") or ep.get("contractSize") or 0)
                    if size == 0:
                        continue
                    side_raw = (ep.get("side") or "").lower()
                    side = "buy" if side_raw == "long" else "sell"
                    sym = ep.get("symbol", "")
                    exchange_positions[(ex_name.lower(), sym, side)] = True
            except Exception as e:
                logger.debug(f"[Positions] Sync fetch {ex_name}: {e}")

        # Close any tracked LIVE positions not found on exchange
        ghosts = []
        for pos in live_open:
            key = (pos.exchange.lower(), pos.symbol, pos.side)
            if key not in exchange_positions:
                ghosts.append(pos)

        for pos in ghosts:
            logger.warning(
                f"[Positions] GHOST detected: {pos.symbol} {pos.side.upper()} "
                f"on {pos.exchange} — not found on exchange. Auto-closing.")
            # Try to get current price for accurate PnL
            ex = active_exchanges.get(pos.exchange.lower())
            close_price = pos.entry_price  # fallback
            if ex:
                try:
                    ticker = ex.fetch_ticker(pos.symbol, pos.market_type)
                    close_price = float(ticker.get("last") or ticker.get("close") or pos.entry_price)
                except Exception:
                    pass
            self.close(pos.id, close_price, "ghost_sync")

        if ghosts:
            logger.info(f"[Positions] Synced: closed {len(ghosts)} ghost position(s)")
        else:
            logger.debug("[Positions] Sync: all LIVE positions verified on exchange")

    def _save(self):
        """Atomic save: write to temp file, then rename over original."""
        self.SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "open":   [asdict(p) for p in self._open.values()],
            "closed": [asdict(p) for p in self._closed[-500:]],
        }
        tmp_path = self.SAVE_PATH.with_suffix(".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
            # Backup current file before replacing
            if self.SAVE_PATH.exists():
                try:
                    self.BACKUP_PATH.write_bytes(self.SAVE_PATH.read_bytes())
                except Exception:
                    pass
            tmp_path.replace(self.SAVE_PATH)
        except Exception as e:
            logger.error(f"[Positions] Save failed: {e}")
            # Clean up temp file on failure
            try:
                tmp_path.unlink(missing_ok=True)
            except Exception:
                pass

    def _load(self):
        if not self.SAVE_PATH.exists():
            # Try backup if main file missing
            if self.BACKUP_PATH.exists():
                logger.warning("[Positions] Main file missing, loading from backup")
                source = self.BACKUP_PATH
            else:
                return
        else:
            source = self.SAVE_PATH
        try:
            raw = source.read_text(encoding="utf-8")
            data = json.loads(raw)
            for d in data.get("open", []):
                d.setdefault("entry_fee",   0.0)
                d.setdefault("exit_fee",    0.0)
                d.setdefault("total_fees",  0.0)
                d.setdefault("gross_pnl",   None)
                d.setdefault("paper_trade", True)   # old records = paper
                p = Position(**d)
                self._open[p.id] = p
            for d in data.get("closed", []):
                d.setdefault("entry_fee",   0.0)
                d.setdefault("exit_fee",    0.0)
                d.setdefault("total_fees",  0.0)
                d.setdefault("gross_pnl",   None)
                d.setdefault("paper_trade", True)
                self._closed.append(Position(**d))
            paper_n = sum(1 for p in self._closed if p.paper_trade)
            live_n  = len(self._closed) - paper_n
            logger.info(
                f"[Positions] Loaded {len(self._open)} open, "
                f"{len(self._closed)} closed "
                f"({paper_n} paper / {live_n} live)"
            )
        except json.JSONDecodeError as e:
            logger.error(f"[Positions] Corrupt JSON in {source}: {e}")
            # Try backup if main was corrupt
            if source == self.SAVE_PATH and self.BACKUP_PATH.exists():
                logger.warning("[Positions] Trying backup file...")
                source_bak = self.BACKUP_PATH
                try:
                    data = json.loads(source_bak.read_text(encoding="utf-8"))
                    for d in data.get("open", []):
                        d.setdefault("entry_fee", 0.0)
                        d.setdefault("exit_fee", 0.0)
                        d.setdefault("total_fees", 0.0)
                        d.setdefault("gross_pnl", None)
                        d.setdefault("paper_trade", True)
                        p = Position(**d)
                        self._open[p.id] = p
                    for d in data.get("closed", []):
                        d.setdefault("entry_fee", 0.0)
                        d.setdefault("exit_fee", 0.0)
                        d.setdefault("total_fees", 0.0)
                        d.setdefault("gross_pnl", None)
                        d.setdefault("paper_trade", True)
                        self._closed.append(Position(**d))
                    logger.info(f"[Positions] Recovered from backup: {len(self._open)} open, {len(self._closed)} closed")
                except Exception as e2:
                    logger.error(f"[Positions] Backup also failed: {e2}")
        except Exception as e:
            logger.error(f"[Positions] Load error: {e}")
