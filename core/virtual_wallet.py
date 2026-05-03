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
    """
    Paper-trading wallet.  Thread-safe enough for single-threaded schedule loop.
    """

    def __init__(self):
        self._enabled  = DRY_RUN
        self._balances: dict = {}   # {exchange_name: float}
        self._start    = _start_balance()
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

    def reset(self, exchange: str = None):
        """Reset balance back to starting amount."""
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
        try:
            WALLET_FILE.parent.mkdir(parents=True, exist_ok=True)
            WALLET_FILE.write_text(
                json.dumps({
                    "start":    self._start,
                    "balances": self._balances,
                    "saved_at": time.time(),
                }, indent=2),
                encoding="utf-8",
            )
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
