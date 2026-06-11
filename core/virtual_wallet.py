"""
core/virtual_wallet.py — Paper trading wallet for DRY RUN mode.

Simulates a real exchange account starting with a configurable USDT balance
(default $1000). Tracks every simulated trade exactly as a real exchange would:

  - Deducts notional + entry fee when a position is opened
  - Returns notional + PnL - exit fee when a position is closed
  - Maintains a running balance that the risk manager uses for position sizing
  - Persists balance to data/virtual_wallet.json so it survives restarts
  - Separate balances per exchange (binance / bybit / bitget) if multi-exchange is used
  - Prints a clear balance statement on every open and close

Fee rates mirror the real exchanges:
  Spot    0.10%  per side  (Binance standard; 0.075% with BNB discount)
  Futures 0.05%  per side  (Binance taker)

Only active in DRY_RUN mode. In LIVE mode this class is a no-op.
"""

import json
import os
import threading
import time
from pathlib import Path

from loguru import logger

from config import DRY_RUN

WALLET_FILE         = Path("data/virtual_wallet.json")
DEFAULT_START_USDT  = 1000.0   # overridden by config.py DRY_RUN_BALANCE


def _start_balance() -> float:
    try:
        from config import DRY_RUN_BALANCE
        return float(DRY_RUN_BALANCE)
    except (ImportError, AttributeError):
        return DEFAULT_START_USDT


class VirtualWallet:
    """Paper-trading wallet.

    Balance mutations are serialized by self._lock (RLock). The bot hits ONE
    shared wallet from several threads — the 5-min portfolio cycle (on_open),
    the 10s SL/TP daemon whose ThreadPoolExecutor fans (exchange, market_type)
    so a spot close and a futures close on the SAME exchange key run in parallel
    (on_close + funding), and the shadow runner. Without the lock the
    read-modify-write on _balances[ex] lost updates and the paper balance
    drifted from true paper PnL — the data the learning-first phase depends on
    (audit 2026-06-04). LIVE is unaffected (the wallet is a no-op via _enabled).
    """

    def __init__(self):
        self._enabled  = DRY_RUN
        self._balances: dict = {}   # {exchange_name: float}
        self._start    = _start_balance()
        self._lock     = threading.RLock()  # serialize _balances RMW + _save
        self._load()

    # ── Public API ────────────────────────────────────────────────────

    @property
    def start_balance(self) -> float:
        """Return the configured starting balance per exchange."""
        return self._start

    def balance(self, exchange: str = "binance") -> float:
        """Return current available USDT balance for the exchange."""
        if not self._enabled:
            return 0.0
        return round(self._balances.get(exchange.lower(), self._start), 4)

    def total_balance(self) -> float:
        """Sum of all exchange balances."""
        if not self._enabled:
            return 0.0
        return round(sum(self._balances.values()), 4)

    def on_open(self, exchange: str, symbol: str, side: str,
                size: float, price: float, fee: float,
                market_type: str = "spot", leverage: int = 1) -> bool:
        """
        Simulate spending USDT to open a position.
        Returns False if insufficient balance.

        For leveraged futures: deduct margin (notional / leverage) + fee,
        NOT the full notional. This matches real exchange behavior where
        only the margin is reserved from the account balance.
        """
        if not self._enabled:
            return True

        with self._lock:
            ex  = exchange.lower()
            bal = self._balances.get(ex, self._start)

            notional = size * price
            if market_type == "futures" and leverage > 1:
                cost = notional / leverage + fee   # margin + entry fee
            else:
                cost = notional + fee   # spot: full notional + entry fee

            if cost > bal:
                logger.warning(
                    f"[VirtualWallet] Insufficient balance on {exchange}: "
                    f"need {cost:.4f} USDT, have {bal:.4f} USDT — order skipped."
                )
                return False

            self._balances[ex] = bal - cost
            self._save()

            logger.info(
                f"[VirtualWallet] {exchange} | OPEN {side.upper()} {size:.6f} "
                f"{symbol} @ {price:.4f} | "
                f"cost={cost:.4f} USDT (notional={size*price:.4f} + fee={fee:.4f}) | "
                f"balance: {bal:.4f} → {self._balances[ex]:.4f} USDT"
            )
            return True

    def on_close(self, exchange: str, symbol: str, side: str,
                 size: float, exit_price: float, entry_price: float,
                 exit_fee: float, gross_pnl: float,
                 market_type: str = "spot", leverage: int = 1):
        """
        Return USDT to wallet when position closes.

        Spot:    proceeds = size * exit_price - exit_fee
        Futures: proceeds = margin_returned + PnL - exit_fee
                 (on_open deducted margin = notional/leverage + entry_fee,
                  so we return margin + gross_pnl - exit_fee)
        """
        if not self._enabled:
            return

        with self._lock:
            ex       = exchange.lower()
            bal      = self._balances.get(ex, self._start)

            if market_type == "futures" and leverage > 1:
                # Return margin (what was originally deducted) + realized PnL
                margin = size * entry_price / leverage
                proceeds = margin + gross_pnl - exit_fee
            else:
                proceeds = size * exit_price - exit_fee   # spot: sell proceeds

            # Use config fee rates for accurate net PnL display
            try:
                from core.position_tracker import _fee_rate
                entry_fee_est = size * entry_price * _fee_rate(market_type)
            except Exception:
                fee_rate = 0.0005 if market_type == "futures" else 0.001
                entry_fee_est = size * entry_price * fee_rate
            net_pnl  = gross_pnl - entry_fee_est - exit_fee

            self._balances[ex] = bal + proceeds
            self._save()

            pnl_str = f"{net_pnl:+.4f}"
            logger.info(
                f"[VirtualWallet] {exchange} | CLOSE {symbol} @ {exit_price:.4f} | "
                f"proceeds={proceeds:.4f} USDT (exit_fee={exit_fee:.4f}) | "
                f"net pnl={pnl_str} USDT | "
                f"balance: {bal:.4f} → {self._balances[ex]:.4f} USDT"
            )

    def apply_funding(self, exchange: str, cost: float) -> float:
        """Locked balance adjustment for paper funding accrual: +cost debits
        (wallet shrinks), -cost credits. Returns the new balance.

        order_manager.accrue_paper_funding calls THIS instead of mutating
        self.wallet._balances directly — that external RMW (read prev / write
        prev-cost) raced the on_open/on_close RMW on the same per-exchange key
        every 10s funding tick (audit 2026-06-04)."""
        if not self._enabled:
            return 0.0
        ex = exchange.lower()
        with self._lock:
            bal = self._balances.get(ex, self._start)
            new_bal = bal - cost
            self._balances[ex] = new_bal
            self._save()
            return new_bal

    def reset(self, exchange: str = None):
        """Reset balance back to starting amount."""
        with self._lock:
            if exchange:
                self._balances[exchange.lower()] = self._start
                logger.warning(
                    f"[VirtualWallet] {exchange} balance reset to "
                    f"{self._start:.2f} USDT"
                )
            else:
                self._balances.clear()
                logger.warning(
                    f"[VirtualWallet] ALL balances reset to {self._start:.2f} USDT"
                )
            self._save()

    def _redebit_open_margin(self):
        """After a start-balance re-seed, re-debit margin (+ est. entry fee) of
        any OPEN paper positions from data/positions.json so their eventual
        on_close credits have a matching debit.

        2026-06-11: a re-seed fired with 8 paper positions open; their margin
        debits were erased and the later closes credited +913.52 USDT of
        unmatched margin — losing trades RAISED the balance. This replays the
        on_open debit (margin + entry fee) against the fresh seed."""
        try:
            pf = Path("data/positions.json")
            if not pf.exists():
                return
            rows = json.loads(pf.read_text(encoding="utf-8")).get("open", [])
            redebited = 0
            for d in rows:
                if not d.get("paper_trade"):
                    continue
                ex = str(d.get("exchange", "")).lower()
                size = float(d.get("size") or 0)
                entry = float(d.get("entry_price") or 0)
                lev = int(d.get("leverage") or 1)
                mtype = d.get("market_type", "futures")
                if not ex or size <= 0 or entry <= 0:
                    continue
                notional = size * entry
                margin = notional / lev if (mtype == "futures" and lev > 1) else notional
                try:
                    from core.position_tracker import _fee_rate
                    fee_est = notional * _fee_rate(mtype)
                except Exception:
                    fee_est = notional * (0.0005 if mtype == "futures" else 0.001)
                cost = margin + fee_est
                bal = self._balances.get(ex, self._start)
                self._balances[ex] = bal - cost
                redebited += 1
                logger.warning(
                    f"[VirtualWallet] re-debited {cost:.4f} USDT "
                    f"(margin {margin:.4f} + fee est {fee_est:.4f}) for open paper "
                    f"{d.get('symbol')} on {ex} after re-seed | "
                    f"balance: {bal:.4f} → {self._balances[ex]:.4f}"
                )
            if redebited:
                self._save()
        except Exception as e:
            logger.debug(f"[VirtualWallet] re-debit after reset failed: {e}")

    def statement(self) -> str:
        """Return a formatted balance statement string."""
        if not self._enabled:
            return "(VirtualWallet disabled — LIVE mode)"
        lines = [f"  Virtual Wallet (DRY RUN — starting ${self._start:.0f})"]
        for ex, bal in sorted(self._balances.items()):
            diff   = bal - self._start
            symbol = "+" if diff >= 0 else ""
            lines.append(
                f"    {ex.upper():<10}: {bal:>10.4f} USDT  "
                f"({symbol}{diff:.4f} vs start)"
            )
        if not self._balances:
            lines.append(f"    (no activity yet — balance will be {self._start:.2f} USDT)")
        return "\n".join(lines)

    # ── Persistence ───────────────────────────────────────────────────

    def _save(self):
        # Never write the PRODUCTION wallet file from inside a pytest run.
        # 2026-06-11: an unisolated test constructed VirtualWallet() from the
        # repo root and its _save() clobbered data/virtual_wallet.json with
        # start=1000 — on the next bot restart _load saw the start mismatch and
        # re-seeded every balance, erasing accumulated paper PnL (4x Jun 10-11).
        # Isolated tests (monkeypatch.chdir(tmp_path)) resolve elsewhere and
        # still save normally.
        if "PYTEST_CURRENT_TEST" in os.environ:
            try:
                _prod = Path(__file__).resolve().parents[1] / "data" / "virtual_wallet.json"
                if WALLET_FILE.resolve() == _prod:
                    logger.debug(
                        "[VirtualWallet] pytest targeting production wallet file — save skipped")
                    return
            except Exception:
                pass
        # Atomic write (tmp + os.replace) so two concurrent saves can never tear
        # data/virtual_wallet.json into invalid JSON (which _load would swallow,
        # resetting balances to start). Callers hold self._lock; os.replace is
        # atomic on the same filesystem. Audit 2026-06-04.
        try:
            WALLET_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp = WALLET_FILE.with_name(WALLET_FILE.name + ".tmp")
            tmp.write_text(
                json.dumps({
                    "start":    self._start,
                    "balances": self._balances,
                    "saved_at": time.time(),
                }, indent=2),
                encoding="utf-8",
            )
            os.replace(tmp, WALLET_FILE)
        except Exception as e:
            logger.debug(f"[VirtualWallet] Save error: {e}")

    def _load(self):
        if not self._enabled:
            return
        try:
            if WALLET_FILE.exists():
                data = json.loads(WALLET_FILE.read_text(encoding="utf-8"))
                saved_start = data.get("start", self._start)
                # If config start balance changed, reset
                if abs(saved_start - self._start) > 0.01:
                    logger.info(
                        f"[VirtualWallet] Start balance changed "
                        f"({saved_start} → {self._start}) — resetting."
                    )
                    self._balances = {}
                    self._redebit_open_margin()
                else:
                    self._balances = data.get("balances", {})
                    logger.info(
                        "[VirtualWallet] Loaded balances: "
                        + ", ".join(
                            f"{k.upper()}={v:.4f}" for k, v in self._balances.items()
                        )
                    )
            else:
                logger.info(
                    f"[VirtualWallet] New paper wallet — "
                    f"starting balance: ${self._start:.2f} USDT per exchange"
                )
        except Exception as e:
            logger.debug(f"[VirtualWallet] Load error: {e}")
            self._balances = {}
