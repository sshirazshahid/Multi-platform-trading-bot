"""
core/bot_engine.py — Smart Multi-Timeframe Bot Engine with Learning + News
Exchanges: Binance | MEXC | Bybit | Bitget

FIX: _extract_usdt now handles Bybit Unified Account correctly.
     Bybit returns totalEquity in bal["total"]["USDT"], not bal["free"]["USDT"].
     Also: _log_balances now fetches Bybit balance ONCE (not spot+futures twice).
"""

import time
import signal
import atexit
import threading
import schedule
from loguru import logger
from rich.console import Console
from rich.table   import Table
from rich         import box
from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime, timezone
from config import (
    TRADING_PAIRS, DRY_RUN, RISK,
    TRADING_MODE, PORTFOLIO_RESCAN_MINUTES, PORTFOLIO_MIN_VALUE_USD,
)
try:
    from config import CLAUDE_PORTFOLIO
except ImportError:
    CLAUDE_PORTFOLIO = {"enabled": True, "scan_interval_min": 15, "max_actions_per_cycle": 4}
from exchanges import (
    BinanceClient, MEXCClient,
    BybitClient,   BitgetClient,
)
from exchanges.base         import BaseExchange
from core.risk_manager      import RiskManager
from core.order_manager     import OrderManager
from core.position_tracker  import PositionTracker
from core.learning_engine       import LearningEngine
from core.news_scanner          import NewsScanner
try:
    from core.pair_discovery    import discover_all
except ImportError:
    discover_all = None
try:
    from core.mcp_brain         import MCPBrain
except ImportError:
    MCPBrain = None
from utils import TelegramNotifier

console = Console()

MAX_PER_EXCHANGE    = 6       # Max 6 per exchange (4 exchanges × 6 = 24 slots)
MAX_TOTAL_POSITIONS = 8       # Max 8 total across all exchanges
NEWS_INTERVAL       = 60 * 30
LEARN_INTERVAL      = 60 * 60
PORTFOLIO_CYCLE_SEC = CLAUDE_PORTFOLIO.get("scan_interval_min", 15) * 60
MAX_ACTIONS_PER_CYCLE = CLAUDE_PORTFOLIO.get("max_actions_per_cycle", 4)

# Exchanges that use a single Unified Account (fetch balance once only)
_UNIFIED_EXCHANGES = {"bybit"}


class BotEngine:

    def __init__(self):
        mode_label = "DRY RUN" if DRY_RUN else "LIVE TRADING"
        logger.info("=" * 60)
        logger.info("  TRADING BOT — Smart Scanner + Learning + News")
        logger.info(f"  Trade Mode : {TRADING_MODE.upper()}")
        logger.info(f"  Run Mode   : {mode_label}")
        logger.info("=" * 60)

        self.notifier  = TelegramNotifier()
        self.tracker   = PositionTracker()
        self.risk      = RiskManager()
        self.order_mgr = OrderManager(self.tracker, self.risk, self.notifier)
        self.learner   = LearningEngine()
        self.news      = NewsScanner()
        self.mcp_brain = MCPBrain() if MCPBrain else None
        self.order_mgr.mcp_brain = self.mcp_brain  # Wire MCP exit intelligence

        self.exchanges = {
            "binance": BinanceClient(),
            "mexc":    MEXCClient(),
            "bybit":   BybitClient(),
            "bitget":  BitgetClient(),
        }
        self.active_exchanges = {
            name: ex for name, ex in self.exchanges.items()
            if getattr(ex, "_connected", False)
        }

        self._start_time = time.time()
        self._cycle      = 0
        self._consecutive_api_fails = 0
        # Per-exchange, per-market-type USDT balances (populated by _log_balances)
        self._balances: dict[str, dict[str, float]] = {}
        # Transfer cooldown: prevent repeated transfer attempts (5 min per route)
        self._last_transfer: dict[tuple, float] = {}  # (ex, from, to) → timestamp
        # Exchange position cache — for universal position monitor (60s TTL)
        self._exchange_positions_cache: list[dict] = []
        self._exchange_positions_time: float = 0

        if not self.active_exchanges:
            logger.error("[Engine] No exchanges connected!")
        else:
            logger.info(f"[Engine] Connected: {list(self.active_exchanges.keys())}")
            self._log_balances()
            # Sync tracked positions with exchange — close ghosts on startup
            if not DRY_RUN:
                self.tracker.sync_with_exchanges(self.active_exchanges)

            # Wire exchange clients to MCP Brain for direct OHLCV fetching
            if self.mcp_brain:
                self.mcp_brain.set_exchanges(self.active_exchanges)

        self.current_pairs = self._resolve_pairs()

        # Dynamic pair discovery
        if discover_all:
            try:
                discovered = discover_all(self.active_exchanges)
                for ename, pairs in discovered.items():
                    existing = self.current_pairs.get(ename, {"spot": [], "futures": []})
                    for mtype in ("spot", "futures"):
                        for sym in pairs.get(mtype, []):
                            if sym not in existing.get(mtype, []):
                                existing.setdefault(mtype, []).append(sym)
                    self.current_pairs[ename] = existing
                total = sum(len(p.get("spot",[]))+len(p.get("futures",[]))
                            for p in self.current_pairs.values())
                logger.info(f"[Engine] Total pairs after discovery: {total}")
            except Exception as e:
                logger.debug(f"[Engine] Pair discovery: {e}")

        # Build DCA/rebalance strategy pool (lightweight — no scanner strategies)
        self._build_strategy_pool()

        try:
            self.news.scan()
        except Exception:
            pass

    # ── Strategy pool ─────────────────────────────────────────────────

    def _build_strategy_pool(self):
        """Build lightweight pool — only DCA + rebalance (scheduled independently).
        All entry/exit decisions now come from Claude portfolio cycle."""
        kwargs = dict(order_manager=self.order_mgr, risk_manager=self.risk)
        self.pool = {}
        for name, mod_cls, mtype in [
            ("dca_spot",  ("strategies.dca_strategy", "DCAStrategy"),         "spot"),
            ("rebalance", ("strategies.rebalancing",  "RebalancingStrategy"), "spot"),
        ]:
            try:
                mod = __import__(mod_cls[0], fromlist=[mod_cls[1]])
                self.pool[name] = getattr(mod, mod_cls[1])(**kwargs, market_type=mtype)
            except (ImportError, AttributeError):
                pass
        logger.info(f"[Engine] Strategy pool (DCA/rebal only): {list(self.pool.keys())}")

    # ── Pair resolution ───────────────────────────────────────────────

    def _resolve_pairs(self) -> dict:
        if TRADING_MODE == "portfolio":
            logger.info("[Engine] Portfolio mode — scanning wallets...")
            import portfolio_scanner as ps
            ps.MIN_VALUE_USD = PORTFOLIO_MIN_VALUE_USD
            pairs = ps.build_portfolio_pairs(self.active_exchanges)
            logger.info("[Engine] Scan complete.")
            return pairs

        if TRADING_MODE == "all":
            logger.info("[Engine] ALL mode — scanning everything available...")
            return self._resolve_all_mode_pairs()

        logger.info("[Engine] USDT-only mode — using configured pairs.")
        return TRADING_PAIRS

    def _resolve_all_mode_pairs(self) -> dict:
        """
        ALL mode: Aggressive discovery of every tradeable market.
        1. Start with configured TRADING_PAIRS as base
        2. Scan wallet holdings (like portfolio mode)
        3. Run expanded pair discovery (higher limits, all asset classes)
        4. Merge everything — maximum opportunity surface
        """
        # Start with configured base pairs
        pairs = {}
        for ex_name, type_dict in TRADING_PAIRS.items():
            pairs[ex_name] = {
                "spot":    list(type_dict.get("spot", [])),
                "futures": list(type_dict.get("futures", [])),
            }

        # Layer 1: Scan wallet holdings (portfolio scan)
        try:
            import portfolio_scanner as ps
            ps.MIN_VALUE_USD = 1.0  # Lower threshold — scan everything
            for ex_name, exchange in self.active_exchanges.items():
                if ex_name not in pairs:
                    pairs[ex_name] = {"spot": [], "futures": []}
                for pair in ps.scan_spot_holdings(exchange):
                    if pair not in pairs[ex_name]["spot"]:
                        pairs[ex_name]["spot"].append(pair)
                for pair in ps.scan_futures_holdings(exchange):
                    if pair not in pairs[ex_name]["futures"]:
                        pairs[ex_name]["futures"].append(pair)
        except Exception as e:
            logger.debug(f"[Engine] ALL mode wallet scan: {e}")

        # Layer 2: Aggressive pair discovery (expanded limits)
        try:
            from core.pair_discovery import discover_all_mode
            discovered = discover_all_mode(self.active_exchanges)
            for ex_name, disc_pairs in discovered.items():
                if ex_name not in pairs:
                    pairs[ex_name] = {"spot": [], "futures": []}
                for mtype in ("spot", "futures"):
                    for sym in disc_pairs.get(mtype, []):
                        if sym not in pairs[ex_name][mtype]:
                            pairs[ex_name][mtype].append(sym)
        except Exception as e:
            logger.debug(f"[Engine] ALL mode discovery: {e}")

        total = sum(
            len(p.get("spot", [])) + len(p.get("futures", []))
            for p in pairs.values()
        )
        logger.info(
            f"[Engine] ALL mode: {total} total pairs across "
            f"{len(pairs)} exchanges")
        for ex_name, type_dict in pairs.items():
            logger.info(
                f"[Engine]   {ex_name.upper()}: "
                f"{len(type_dict['spot'])} spot + "
                f"{len(type_dict['futures'])} futures")

        return pairs

    def _rescan_portfolio(self):
        if TRADING_MODE not in ("portfolio", "all"):
            return
        logger.info("[Engine] Re-scanning portfolio...")
        import portfolio_scanner as ps
        ps.MIN_VALUE_USD = PORTFOLIO_MIN_VALUE_USD
        new_pairs = ps.build_portfolio_pairs(self.active_exchanges)
        added = []
        for ex_name, type_dict in new_pairs.items():
            old = self.current_pairs.get(ex_name, {"spot": [], "futures": []})
            for mtype in ("spot", "futures"):
                for sym in type_dict.get(mtype, []):
                    if sym not in old.get(mtype, []):
                        added.append((ex_name, mtype, sym))
        if added:
            logger.info(f"[Engine] New holdings: {added}")
            self.current_pairs = new_pairs
        else:
            logger.info("[Engine] No new holdings.")

    # ── Balances ──────────────────────────────────────────────────────

    def _log_balances(self):
        logger.info("[Engine] Fetching balances...")
        total = 0.0
        balances = {}
        if DRY_RUN:
            wallet = self.order_mgr.wallet
            for ex_name in self.active_exchanges:
                bal = wallet.balance(ex_name)
                logger.info(
                    f"[Engine] {ex_name.upper()} virtual: {bal:.4f} USDT (paper)")
                total += bal
                balances[ex_name] = {"spot": bal, "futures": bal}
        else:
            for name, ex in self.active_exchanges.items():
                # Retain previous balance on fetch failure (don't default to 0 for 15 min)
                prev = self._balances.get(name, {"spot": 0.0, "futures": 0.0})
                balances[name] = {"spot": prev.get("spot", 0.0), "futures": prev.get("futures", 0.0)}
                if name in _UNIFIED_EXCHANGES:
                    # Bybit: single unified account — fetch once only
                    try:
                        bal  = ex.fetch_balance("spot")
                        usdt = self._extract_usdt(bal, name)
                        if usdt > 0:
                            logger.info(f"[Engine] {name.upper()} unified: {usdt:.4f} USDT")
                            total += usdt
                            balances[name] = {"spot": usdt, "futures": usdt}
                        else:
                            total += balances[name]["spot"]  # Use retained balance
                    except Exception as e:
                        logger.debug(f"[Engine] {name} balance: {e}")
                        total += balances[name]["spot"]  # Use retained balance
                else:
                    for mtype in ("spot", "futures"):
                        try:
                            bal  = ex.fetch_balance(mtype)
                            usdt = self._extract_usdt(bal, name)
                            if usdt > 0:
                                balances[name][mtype] = usdt
                                logger.info(
                                    f"[Engine] {name.upper()} {mtype}: {usdt:.4f} USDT")
                            total += balances[name][mtype]
                        except Exception as e:
                            logger.debug(f"[Engine] {name} balance: {e}")
                            total += balances[name][mtype]  # Use retained balance
        self._balances = balances
        if total > 0:
            self.risk.set_start_balance(total)
            logger.info(f"[Engine] Total USDT: {total:.4f}")
        else:
            logger.warning(
                "[Engine] Could not read balance — "
                "ensure 'Enable Reading' is on in your exchange API settings.")

    def _extract_usdt(self, bal: dict, exchange_name: str = "") -> float:
        """
        Extract USDT balance from a ccxt fetch_balance() response.

        FIX: Bybit Unified Account returns totalEquity in bal["total"]["USDT"]
             NOT in bal["free"]["USDT"] (which is 0 on Bybit Unified).
        """
        if not bal:
            return 0.0

        ex = exchange_name.lower() if exchange_name else ""

        # ── Bybit: Use totalEquity (matches Bybit app — includes unrealized PnL)
        if ex == "bybit":
            # Priority 1: Raw Bybit v5 API — totalEquity is what the app shows
            try:
                lst = bal.get("info", {}).get("result", {}).get("list", [{}])
                if lst:
                    eq = lst[0].get("totalEquity")
                    if eq:
                        v = float(eq)
                        if v > 0:
                            return v
                    wb = lst[0].get("totalWalletBalance")
                    if wb:
                        v = float(wb)
                        if v > 0:
                            return v
            except Exception:
                pass
            # Fallback: ccxt parsed fields
            usdt = bal.get("USDT") or {}
            if isinstance(usdt, dict):
                val = usdt.get("total")
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
            total_d = bal.get("total") or {}
            if isinstance(total_d, dict):
                val = total_d.get("USDT")
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
            return 0.0

        # ── Standard ccxt (Binance, MEXC, Bitget) ─────────────────────
        usdt = bal.get("USDT")
        if isinstance(usdt, dict):
            for key in ("free", "total"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
        free = bal.get("free", {})
        if isinstance(free, dict) and free.get("USDT"):
            try:
                return float(free["USDT"])
            except (TypeError, ValueError):
                pass
        total = bal.get("total", {})
        if isinstance(total, dict) and total.get("USDT"):
            try:
                return float(total["USDT"])
            except (TypeError, ValueError):
                pass
        return 0.0

    # ── Claude Portfolio Cycle (SOLE entry/exit authority) ──────────────

    def _collect_all_coins(self) -> list:
        """Collect all unique base assets from TRADING_PAIRS."""
        all_coins = set()
        for pairs in self.current_pairs.values():
            for sym in pairs.get("spot", []) + pairs.get("futures", []):
                base = sym.split("/")[0].split(":")[0]
                all_coins.add(base)
        # Also add coins from open positions
        for p in self.tracker.get_open():
            base = p.symbol.split("/")[0].split(":")[0]
            all_coins.add(base)
        # Prioritize major coins
        _priority = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE",
                     "XAU", "ADA", "AVAX", "LINK", "DOT"}
        priority = sorted(all_coins & _priority)
        others = sorted(all_coins - _priority)
        return (priority + others)[:40]

    def _build_position_snapshot(self) -> list:
        """Build snapshot of all open positions for Claude."""
        result = []
        for p in self.tracker.get_open():
            current_price = 0
            for ex_name, exchange in self.active_exchanges.items():
                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                    try:
                        ticker = exchange.fetch_ticker(p.symbol, p.market_type)
                        current_price = float(ticker.get("last", 0) or 0)
                    except Exception:
                        pass
                    break
            if not current_price:
                continue
            if p.side == "buy":
                pnl_pct = (current_price - p.entry_price) / p.entry_price * 100
            else:
                pnl_pct = (p.entry_price - current_price) / p.entry_price * 100
            result.append({
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side,
                "entry_price": p.entry_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct, 2),
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "age_min": p.duration_minutes,
                "exchange": p.exchange,
                "market_type": p.market_type,
                "leverage": p.leverage,
                "strategy": getattr(p, "strategy", ""),
            })
        return result

    def _build_risk_envelope(self) -> dict:
        """Build risk constraints for Claude."""
        total_open = self.tracker.count_open()
        max_new = max(0, MAX_TOTAL_POSITIONS - total_open)

        total_bal = sum(
            b.get("spot", 0) + b.get("futures", 0)
            for ex, b in self._balances.items()
            if ex not in _UNIFIED_EXCHANGES
        ) + sum(
            b.get("spot", 0)
            for ex, b in self._balances.items()
            if ex in _UNIFIED_EXCHANGES
        )

        daily_loss_pct = 0
        if total_bal > 0:
            daily_loss_pct = abs(min(0, self.risk.daily_pnl)) / total_bal

        dd_pct = RISK.get("max_drawdown_pct", 0.25)

        return {
            "max_new_positions": max_new,
            "total_open": total_open,
            "daily_loss_pct": round(daily_loss_pct, 4),
            "drawdown_headroom_pct": round((dd_pct - daily_loss_pct) * 100, 1),
            "total_balance": round(total_bal, 2),
        }

    def _get_recent_trades(self, n: int = 20) -> list:
        """Get last N closed trades with P&L for accuracy feedback."""
        closed = getattr(self.tracker, '_closed', [])[-n:]
        result = []
        for t in closed:
            result.append({
                "symbol": t.symbol,
                "side": t.side,
                "pnl": round(getattr(t, "pnl", 0) or 0, 4),
                "strategy": getattr(t, "strategy", ""),
                "exchange": t.exchange,
            })
        return result

    def _claude_portfolio_cycle(self):
        """Single unified cycle: gather data -> Claude decides -> execute.
        Replaces the old _scan_and_trade + _run_mcp_brain pipeline."""
        if not self.mcp_brain or not self.mcp_brain.is_enabled:
            logger.warning("[Claude] MCP Brain not available — skipping portfolio cycle")
            return

        self._log_balances()

        all_coins = self._collect_all_coins()
        open_positions = self._build_position_snapshot()
        risk_envelope = self._build_risk_envelope()
        recent_trades = self._get_recent_trades(20)

        logger.info(
            f"[Claude] Portfolio cycle: {len(all_coins)} coins, "
            f"{len(open_positions)} open positions, "
            f"balance=${risk_envelope.get('total_balance', 0):.0f}")

        actions = self.mcp_brain.analyze_portfolio(
            coins=all_coins,
            open_positions=open_positions,
            exchange_balances=dict(self._balances),
            risk_envelope=risk_envelope,
            recent_trades=recent_trades,
        )

        if not actions:
            logger.info("[Claude] No actions this cycle")
            self._cycle += 1
            return

        # Cap actions per cycle
        actions = actions[:MAX_ACTIONS_PER_CYCLE]

        executed = 0
        for action in actions:
            try:
                if action["type"] == "OPEN":
                    if self._execute_open(action):
                        executed += 1
                elif action["type"] == "CLOSE":
                    if self._execute_close(action):
                        executed += 1
            except Exception as e:
                logger.error(f"[Claude] Action execution error: {e}")

        # Fund ops (transfers between spot/futures)
        fund_ops = self.mcp_brain.last_fund_ops()
        if fund_ops:
            self._execute_fund_ops(fund_ops)

        self._cycle += 1
        logger.info(
            f"[Claude] Cycle complete: {executed}/{len(actions)} actions executed")

    def _execute_open(self, action: dict) -> bool:
        """Validate and execute an OPEN action from Claude. Returns True if executed."""
        symbol     = action.get("symbol", "")
        ex_name    = action.get("exchange", "").lower()
        market_type = action.get("market_type", "futures")
        side       = action.get("side", "").lower()
        leverage   = min(action.get("leverage", 5), RISK.get("futures_max_leverage", 5))
        size_pct   = min(action.get("size_pct", 3.0), RISK.get("max_position_pct", 0.05) * 100)
        sl_pct     = action.get("sl_pct", 4.0)
        tp_pct     = action.get("tp_pct", 10.0)
        confidence = action.get("confidence", 0)

        if not symbol or not ex_name or side not in ("buy", "sell"):
            logger.warning(f"[Claude] Invalid OPEN action: {action}")
            return False

        # ── Hard limits (non-negotiable) ──

        # MEXC futures blocked (geo-blocked from Pakistan)
        if market_type == "futures" and ex_name == "mexc":
            logger.warning(f"[Claude] BLOCKED: MEXC futures not available")
            return False

        # Spot can only be "buy" (no short on spot)
        if market_type == "spot" and side == "sell":
            logger.warning(f"[Claude] BLOCKED: Cannot short on spot")
            return False

        # Minimum SL/TP
        if market_type == "futures":
            sl_pct = max(sl_pct, 3.0)
            tp_pct = max(tp_pct, 8.0)
        else:
            sl_pct = max(sl_pct, 2.0)
            tp_pct = max(tp_pct, 5.0)

        exchange = self.active_exchanges.get(ex_name)
        if not exchange:
            logger.warning(f"[Claude] Exchange '{ex_name}' not connected")
            return False

        # Risk manager circuit breakers
        if not self.risk.can_trade(self.tracker.count_open()):
            logger.warning(f"[Claude] BLOCKED by risk manager: {self.risk.halt_reason}")
            return False

        # Per-exchange position limit
        ex_open = self.tracker.count_open(exchange=exchange.name)
        if ex_open >= MAX_PER_EXCHANGE:
            logger.info(f"[Claude] {ex_name}: {ex_open}/{MAX_PER_EXCHANGE} positions — full")
            return False

        # Total position limit
        total_open = self.tracker.count_open()
        if total_open >= MAX_TOTAL_POSITIONS:
            logger.info(f"[Claude] Total {total_open}/{MAX_TOTAL_POSITIONS} — full")
            return False

        # No duplicate base asset on same exchange
        base_asset = symbol.split("/")[0]
        all_open = self.tracker.get_open(exchange=exchange.name)
        already_has = any(
            p.symbol.split("/")[0] == base_asset for p in all_open
        )
        if already_has:
            logger.info(f"[Claude] {base_asset} already open on {ex_name} — skip")
            return False

        # Balance check
        ex_bals = self._balances.get(ex_name, {})
        mtype_bal = ex_bals.get(market_type, 0.0)
        min_trade_bal = 8.0 if market_type == "futures" else 3.0
        if mtype_bal < min_trade_bal:
            # Try auto-transfer
            other = "spot" if market_type == "futures" else "futures"
            other_bal = ex_bals.get(other, 0.0)
            xfer_key = (ex_name, other, market_type)
            xfer_cooldown = time.time() - self._last_transfer.get(xfer_key, 0) < 300
            can_xfer = (other_bal >= 6.0
                        and ex_name not in ("bybit",)
                        and not xfer_cooldown)
            if can_xfer:
                xfer = min(other_bal * 0.70, 200.0)
                self._last_transfer[xfer_key] = time.time()
                logger.info(f"[Claude] Auto-transfer ${xfer:.2f} {other}->{market_type} on {ex_name}")
                try:
                    if exchange.transfer(xfer, other, market_type):
                        ex_bals[other] -= xfer
                        ex_bals[market_type] = mtype_bal + xfer
                        mtype_bal += xfer
                except Exception as e:
                    logger.debug(f"[Claude] Auto-transfer failed: {e}")
            if mtype_bal < min_trade_bal:
                logger.info(f"[Claude] {ex_name} {market_type} balance ${mtype_bal:.2f} < ${min_trade_bal}")
                return False

        # Add :USDT suffix for futures
        trade_symbol = symbol
        if market_type == "futures" and ":" not in symbol:
            trade_symbol = symbol + ":USDT"

        # Compute position size from size_pct
        size_fraction = size_pct / 100.0
        notional = mtype_bal * size_fraction
        if notional < 5.0:
            logger.info(f"[Claude] Notional ${notional:.2f} < $5 minimum")
            return False

        # Get current price for sizing
        try:
            ticker = exchange.fetch_ticker(trade_symbol, market_type)
            price = float(ticker.get("last", 0) or 0)
        except Exception as e:
            logger.warning(f"[Claude] fetch_ticker {trade_symbol}: {e}")
            return False
        if price <= 0:
            return False

        # Compute SL/TP prices
        if side == "buy":
            stop_loss   = price * (1 - sl_pct / 100)
            take_profit = price * (1 + tp_pct / 100)
        else:
            stop_loss   = price * (1 + sl_pct / 100)
            take_profit = price * (1 - tp_pct / 100)

        # Compute size in base units
        if market_type == "futures":
            size = (notional * leverage) / price
        else:
            size = notional / price

        # Set leverage
        if market_type == "futures" and leverage > 1:
            try:
                exchange.set_leverage(trade_symbol, leverage)
            except Exception as e:
                logger.debug(f"[Claude] set_leverage: {e}")

        logger.info(
            f"[Claude] EXECUTING OPEN: {trade_symbol} {side.upper()} on {ex_name} "
            f"({market_type}) {leverage}x | size={size:.6g} notional=${notional:.2f} "
            f"SL={stop_loss:.6g} TP={take_profit:.6g} conf={confidence:.0%}")

        try:
            self.order_mgr.open_position(
                exchange, trade_symbol, side, market_type,
                strategy="claude_portfolio",
                size=size, price=price,
                sl=stop_loss, tp=take_profit,
                leverage=leverage,
            )
            return True
        except Exception as e:
            logger.error(f"[Claude] open_position failed: {e}")
            return False

    def _execute_close(self, action: dict) -> bool:
        """Find and close a position by ID. Returns True if closed."""
        position_id = action.get("position_id", "")
        reason = action.get("reason", "claude_portfolio_close")

        if not position_id:
            logger.warning("[Claude] CLOSE action missing position_id")
            return False

        # Find position by ID (full or prefix match)
        target = None
        for p in self.tracker.get_open():
            if p.id == position_id or p.id.startswith(position_id):
                target = p
                break

        if not target:
            logger.info(f"[Claude] Position {position_id[:8]} not found — may be closed already")
            return False

        # Find exchange client
        exchange = None
        for ex_name, ex in self.active_exchanges.items():
            if ex_name == target.exchange.lower() or ex_name in target.exchange.lower():
                exchange = ex
                break

        if not exchange:
            logger.warning(f"[Claude] Exchange for {target.exchange} not connected")
            return False

        logger.info(
            f"[Claude] EXECUTING CLOSE: {target.symbol} {target.side} "
            f"on {target.exchange} | {reason[:60]}")

        try:
            self.order_mgr.close_position(exchange, target, reason)
            return True
        except Exception as e:
            logger.error(f"[Claude] close_position failed: {e}")
            return False

    # ── Scheduled tasks ───────────────────────────────────────────────

    def _check_all_sl_tp(self):
        """Check SL/TP for all open positions — parallel across exchanges."""
        if self.tracker.count_open() == 0:
            return

        def _check_one(ex_name, exchange, mtype):
            try:
                self.order_mgr.check_sl_tp(exchange, mtype)
            except Exception as e:
                logger.debug(f"[Engine] SL/TP check {ex_name}/{mtype}: {e}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = []
            for ex_name, exchange in self.active_exchanges.items():
                for mtype in ("spot", "futures"):
                    futs.append(pool.submit(_check_one, ex_name, exchange, mtype))
            for f in as_completed(futs):
                f.result()  # Propagate any unhandled exception

    def _sltp_monitor_loop(self, stop_event: threading.Event):
        """Dedicated thread: monitors SL/TP every 10 seconds, never blocked by scans."""
        while not stop_event.is_set():
            try:
                self._check_all_sl_tp()
            except Exception as e:
                logger.debug(f"[Engine] SL/TP monitor: {e}")
            stop_event.wait(10)  # Check every 10 seconds

    def _check_exchange_health(self):
        """Verify exchanges are still reachable. Alert + reconnect if not."""
        for ex_name, exchange in list(self.active_exchanges.items()):
            try:
                ticker = exchange.fetch_ticker("BTC/USDT", "spot")
                if ticker and ticker.get("last"):
                    self._consecutive_api_fails = 0
                else:
                    raise Exception("Empty ticker")
            except Exception as e:
                self._consecutive_api_fails += 1
                logger.warning(
                    f"[Engine] {ex_name} health check FAILED "
                    f"(attempt {self._consecutive_api_fails}): {e}")
                if self._consecutive_api_fails >= 3:
                    open_on_ex = self.tracker.count_open(exchange=exchange.name)
                    self.notifier.error(
                        f"EXCHANGE DOWN: {ex_name.upper()} unreachable for "
                        f"{self._consecutive_api_fails} consecutive checks.\n"
                        f"Open positions on {ex_name}: {open_on_ex}\n"
                        f"SL/TP monitoring PAUSED for this exchange.\n"
                        f"Attempting auto-reconnect...")
                    # Try to reconnect
                    try:
                        exchange._init_exchange()
                        if getattr(exchange, '_connected', False):
                            logger.info(f"[Engine] {ex_name} reconnected")
                            self._consecutive_api_fails = 0
                            self.notifier.alert(
                                f"{ex_name.upper()} reconnected successfully after outage.")
                    except Exception:
                        pass

    def _sync_positions(self):
        """Periodically verify tracked LIVE positions still exist on exchange."""
        try:
            self.tracker.sync_with_exchanges(self.active_exchanges)
        except Exception as e:
            logger.debug(f"[Engine] Position sync: {e}")

    def _fetch_news(self):
        try:
            self.news.scan(force=True)
        except Exception as e:
            logger.debug(f"[Engine] News fetch error: {e}")

    def _run_dca(self):
        """Run DCA strategy on top coins across all exchanges.
        Respects MCP Brain: skips DCA if MCP says SELL for that coin."""
        dca = self.pool.get("dca_spot")
        if not dca:
            return
        dca_coins = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
        for ex_name, exchange in self.active_exchanges.items():
            for symbol in dca_coins:
                # MCP Brain gate: don't DCA-buy coins MCP says to SELL
                if self.mcp_brain and self.mcp_brain.is_enabled:
                    base = symbol.split("/")[0]
                    mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                    if mcp_dec.get("action") == "SELL":
                        logger.debug(
                            f"[DCA] {symbol}: MCP says SELL — skipping DCA buy")
                        continue
                try:
                    dca.run(exchange, symbol)
                except Exception as e:
                    logger.debug(f"[DCA] {symbol} on {ex_name}: {e}")

    def _run_rebalance(self):
        """Run portfolio rebalancing once per day."""
        rebal = self.pool.get("rebalance")
        if not rebal:
            return
        for ex_name, exchange in self.active_exchanges.items():
            try:
                rebal.run(exchange)
            except Exception as e:
                logger.debug(f"[Rebalance] {ex_name}: {e}")

    def _execute_fund_ops(self, fund_ops: list):
        """Execute MCP Brain fund management operations: TRANSFER, SELL_PORTFOLIO, BUY_PORTFOLIO."""
        for op in fund_ops:
            op_type = op.get("op", "")
            ex_name = op.get("exchange", "").lower()
            exchange = self.active_exchanges.get(ex_name)
            if not exchange:
                logger.warning(f"[MCP-FundOps] Exchange '{ex_name}' not active — skip {op_type}")
                continue

            try:
                if op_type == "TRANSFER":
                    from_acct = op.get("from", "spot")
                    to_acct = op.get("to", "futures")
                    amount = float(op.get("amount", 0))
                    if amount < 1:
                        continue
                    # Binance, Bitget, MEXC support transfers; Bybit is unified (no need)
                    if ex_name not in ("binance", "bitget", "mexc"):
                        logger.debug(f"[MCP-FundOps] {ex_name} unified or no transfer support")
                        continue
                    logger.info(
                        f"[MCP-FundOps] TRANSFER {ex_name.upper()}: "
                        f"${amount:.2f} USDT {from_acct}→{to_acct} | {op.get('reason','')[:50]}")
                    success = exchange.transfer(amount, from_acct, to_acct)
                    if success:
                        # Update cached balance
                        bals = self._balances.get(ex_name, {"spot": 0, "futures": 0})
                        bals[from_acct] = max(0, bals.get(from_acct, 0) - amount)
                        bals[to_acct] = bals.get(to_acct, 0) + amount
                        self._balances[ex_name] = bals
                        logger.info(f"[MCP-FundOps] Transfer OK: {ex_name} balances updated")
                    else:
                        logger.warning(f"[MCP-FundOps] Transfer FAILED on {ex_name}")

                elif op_type == "SELL_PORTFOLIO":
                    coin = op.get("coin", "")
                    if not coin or coin in ("USDT", "BUSD", "USDC"):
                        continue
                    symbol = f"{coin}/USDT"
                    reason = op.get("reason", "MCP fund rebalance")
                    # Get current holding
                    try:
                        bal = exchange.fetch_balance("spot")
                        held = float(bal.get("free", {}).get(coin, 0) or 0)
                    except Exception:
                        held = 0
                    if held <= 0:
                        logger.debug(f"[MCP-FundOps] No {coin} on {ex_name} to sell")
                        continue
                    # Check minimum notional
                    try:
                        t = exchange.fetch_ticker(symbol, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0 or held * px < 5.0:
                        logger.debug(f"[MCP-FundOps] {coin} on {ex_name}: ${held*px:.2f} < $5 min")
                        continue
                    logger.info(
                        f"[MCP-FundOps] SELL {coin} on {ex_name.upper()}: "
                        f"{held:.6g} ~${held*px:.2f} | {reason[:50]}")
                    try:
                        exchange.create_order(symbol, "market", "sell", held,
                                              None, {}, "spot")
                        logger.info(f"[MCP-FundOps] Sold {held:.6g} {coin} on {ex_name}")
                    except Exception as e:
                        logger.warning(f"[MCP-FundOps] Sell {coin} on {ex_name} failed: {e}")

                elif op_type == "BUY_PORTFOLIO":
                    coin = op.get("coin", "")
                    amount = float(op.get("amount", 0))
                    if not coin or amount < 5:
                        continue
                    symbol = f"{coin}/USDT"
                    try:
                        t = exchange.fetch_ticker(symbol, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0:
                        continue
                    qty = amount / px
                    logger.info(
                        f"[MCP-FundOps] BUY {coin} on {ex_name.upper()}: "
                        f"${amount:.2f} ({qty:.6g} {coin}) | {op.get('reason','')[:50]}")
                    try:
                        exchange.create_order(symbol, "market", "buy", qty,
                                              px, {}, "spot")
                        logger.info(f"[MCP-FundOps] Bought {qty:.6g} {coin} on {ex_name}")
                    except Exception as e:
                        logger.warning(f"[MCP-FundOps] Buy {coin} on {ex_name} failed: {e}")

            except Exception as e:
                logger.error(f"[MCP-FundOps] {op_type} on {ex_name} error: {e}")

    # ── Universal position scanner ──────────────────────────────────
    _STABLECOINS = {"USDT", "BUSD", "USDC", "FDUSD", "TUSD", "DAI", "USDD"}
    _COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL"}

    def _fetch_all_exchange_positions(self) -> list[dict]:
        """Fetch ALL open positions from ALL connected exchanges.
        Returns futures positions + spot holdings as a unified list.
        Cached for 60 seconds to avoid redundant API calls."""
        now = time.time()
        if now - self._exchange_positions_time < 60 and self._exchange_positions_cache:
            return list(self._exchange_positions_cache)

        results = []

        def _fetch_one_exchange(ex_name, exchange):
            positions = []
            # ── Futures positions ──
            is_mexc = ex_name.lower() == "mexc"
            if not is_mexc:
                try:
                    raw = exchange.fetch_positions()
                    for ep in (raw or []):
                        size = float(ep.get("contracts") or ep.get("contractSize") or 0)
                        if size <= 0:
                            continue
                        symbol = ep.get("symbol", "")
                        side_raw = (ep.get("side") or "").lower()
                        if side_raw not in ("long", "short"):
                            continue
                        side = "buy" if side_raw == "long" else "sell"
                        entry = float(ep.get("entryPrice") or 0)
                        mark = float(ep.get("markPrice") or ep.get("lastPrice") or 0)
                        upnl = float(ep.get("unrealizedPnl") or 0)
                        lev = int(ep.get("leverage") or 1)
                        liq = float(ep.get("liquidationPrice") or 0)
                        pnl_pct = 0
                        if entry > 0:
                            if side == "buy":
                                pnl_pct = (mark - entry) / entry * 100
                            else:
                                pnl_pct = (entry - mark) / entry * 100
                        base = symbol.split("/")[0].split(":")[0]
                        asset_class = "commodity" if base in self._COMMODITY_BASES else "crypto_futures"
                        positions.append({
                            "id": f"EX-{ex_name}-{symbol}-{side}",
                            "symbol": symbol,
                            "side": side,
                            "entry_price": entry,
                            "current_price": mark,
                            "pnl": round(upnl, 4),
                            "pnl_pct": round(pnl_pct, 2),
                            "stop_loss": 0,
                            "take_profit": 0,
                            "age_min": 0,
                            "exchange": ex_name,
                            "market_type": "futures",
                            "leverage": lev,
                            "liquidation_price": liq,
                            "source": "exchange",
                            "size": size,
                            "usdt_value": round(mark * size, 2) if mark else 0,
                            "asset_class": asset_class,
                        })
                except Exception as e:
                    logger.debug(f"[ExScan] {ex_name} futures fetch: {e}")

            # ── Spot holdings ──
            try:
                bal = exchange.fetch_balance("spot")
                total_bal = bal.get("total", {})
                for asset, amt in total_bal.items():
                    amt = float(amt or 0)
                    if amt <= 0 or asset in self._STABLECOINS:
                        continue
                    # Get current price
                    sym = f"{asset}/USDT"
                    try:
                        t = exchange.fetch_ticker(sym, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0:
                        continue
                    usdt_val = amt * px
                    if usdt_val < 5.0:
                        continue
                    asset_class = "commodity" if asset in self._COMMODITY_BASES else "crypto_spot"
                    positions.append({
                        "id": f"SPOT-{ex_name}-{asset}",
                        "symbol": sym,
                        "side": "buy",
                        "entry_price": 0,
                        "current_price": px,
                        "pnl": 0,
                        "pnl_pct": 0,
                        "stop_loss": 0,
                        "take_profit": 0,
                        "age_min": 0,
                        "exchange": ex_name,
                        "market_type": "spot",
                        "leverage": 1,
                        "liquidation_price": 0,
                        "source": "exchange",
                        "size": amt,
                        "usdt_value": round(usdt_val, 2),
                        "asset_class": asset_class,
                    })
            except Exception as e:
                logger.debug(f"[ExScan] {ex_name} spot balance: {e}")
            return positions

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one_exchange, name, ex): name
                for name, ex in self.active_exchanges.items()
            }
            for fut in as_completed(futures, timeout=30):
                try:
                    results.extend(fut.result())
                except Exception as e:
                    logger.debug(f"[ExScan] fetch error: {e}")

        self._exchange_positions_cache = results
        self._exchange_positions_time = time.time()
        return results

    def _run_mcp_position_monitor(self):
        """Run MCP Brain position monitor — scans ALL positions across ALL exchanges.
        Merges bot-tracked positions with exchange-discovered positions (futures + spot).
        MCP Brain is the SOLE authority on hold/close/take-profit for the entire portfolio."""
        if not self.mcp_brain or not self.mcp_brain.is_enabled:
            return
        try:
            # ── Phase 1: Bot-tracked positions ──
            open_positions = self.tracker.get_open()
            tracker_map = {}          # id -> Position object
            tracked_keys = set()      # (exchange, symbol_norm, side) for dedup
            pos_data = []

            for p in open_positions:
                current_price = 0
                for ex_name, exchange in self.active_exchanges.items():
                    if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                        try:
                            ticker = exchange.fetch_ticker(p.symbol, p.market_type)
                            current_price = float(ticker.get("last", 0) or 0)
                        except Exception:
                            pass
                        break
                if not current_price:
                    continue
                if p.side == "buy":
                    unrealized_pnl = (current_price - p.entry_price) * p.size * p.leverage
                    pnl_pct = (current_price - p.entry_price) / p.entry_price * 100
                else:
                    unrealized_pnl = (p.entry_price - current_price) * p.size * p.leverage
                    pnl_pct = (p.entry_price - current_price) / p.entry_price * 100

                base = p.symbol.split("/")[0].split(":")[0]
                asset_class = "commodity" if base in self._COMMODITY_BASES else (
                    "crypto_futures" if p.market_type == "futures" else "crypto_spot")

                pd = {
                    "id": p.id,
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "current_price": current_price,
                    "pnl": round(unrealized_pnl, 4),
                    "pnl_pct": round(pnl_pct, 2),
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "age_min": p.duration_minutes,
                    "exchange": p.exchange,
                    "market_type": p.market_type,
                    "leverage": p.leverage,
                    "liquidation_price": 0,
                    "source": "tracker",
                    "size": p.size,
                    "usdt_value": round(current_price * p.size, 2),
                    "asset_class": asset_class,
                }
                pos_data.append(pd)
                tracker_map[p.id] = p
                sym_norm = p.symbol.split(":")[0]
                tracked_keys.add((p.exchange.lower(), sym_norm, p.side))

            # ── Phase 2: Exchange-discovered positions (futures + spot) ──
            exchange_positions = self._fetch_all_exchange_positions()

            # ── Phase 3: Deduplicate and merge ──
            for ep in exchange_positions:
                sym_norm = ep["symbol"].split(":")[0]
                key = (ep["exchange"].lower(), sym_norm, ep["side"])
                if key in tracked_keys:
                    continue  # already tracked by bot — skip exchange duplicate
                pos_data.append(ep)

            if not pos_data:
                return

            # ── Priority cap: max 20 positions for MCP Brain ──
            # Tracked first, then external futures by value, then external spot by value
            tracked_data = [d for d in pos_data if d["source"] == "tracker"]
            ext_futures = sorted(
                [d for d in pos_data if d["source"] == "exchange" and d["market_type"] == "futures"],
                key=lambda x: x.get("usdt_value", 0), reverse=True)
            ext_spot = sorted(
                [d for d in pos_data if d["source"] == "exchange" and d["market_type"] == "spot"],
                key=lambda x: x.get("usdt_value", 0), reverse=True)
            capped = tracked_data[:20]
            remaining = 20 - len(capped)
            if remaining > 0:
                capped.extend(ext_futures[:remaining])
                remaining = 20 - len(capped)
            if remaining > 0:
                capped.extend(ext_spot[:remaining])
            pos_data = capped

            n_tracked = len(tracked_data)
            n_ext_fut = len(ext_futures)
            n_ext_spot = len(ext_spot)
            if n_ext_fut or n_ext_spot:
                logger.info(
                    f"[MCP-Monitor] {n_tracked} tracked + {n_ext_fut} ext-futures "
                    f"+ {n_ext_spot} ext-spot = {len(pos_data)} sent to MCP Brain")

            # ── Phase 4: Send to MCP Brain ──
            advice = self.mcp_brain.monitor_positions(pos_data)
            if not advice:
                return

            # ── Phase 5: Apply actions ──
            # Build quick lookup for pos_data by ID
            pd_map = {d["id"]: d for d in pos_data}

            for pd in pos_data:
                pid = pd["id"]
                adv = advice.get(pid, {})
                # Also try short ID (MCP Brain returns first 8 chars as keys)
                if not adv and len(pid) > 8:
                    adv = advice.get(pid[:8], {})
                action = adv.get("action", "")
                if not action or action == "HOLD":
                    continue

                source = pd["source"]
                conf = adv.get("confidence", 0)
                reason = adv.get("reason", "")[:60]
                _pnl_pct = pd.get("pnl_pct", 0)

                # ═══ TRACKER POSITIONS — use existing close_position path ═══
                if source == "tracker":
                    p = tracker_map.get(pid)
                    if not p:
                        continue

                    if action == "TAKE_PROFIT":
                        if _pnl_pct >= 1.0 and conf >= 0.60:
                            for ex_name, exchange in self.active_exchanges.items():
                                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                    logger.warning(
                                        f"[MCP-Brain] TAKE PROFIT: {p.symbol} {p.side} "
                                        f"pnl={_pnl_pct:+.1f}% conf={conf:.0%} — {reason}")
                                    self.order_mgr.close_position(
                                        exchange, p, "mcp_take_profit")
                                    break
                        else:
                            logger.debug(
                                f"[MCP-Brain] TAKE_PROFIT skipped: {p.symbol} "
                                f"pnl={_pnl_pct:+.1f}% conf={conf:.0%} "
                                f"(need pnl>=1% + conf>=60%)")

                    elif action == "CLOSE":
                        age_min = p.duration_minutes
                        if age_min < 60 and not (conf >= 0.90 and _pnl_pct < -2.0):
                            logger.info(
                                f"[MCP-Brain] CLOSE blocked: {p.symbol} only {age_min:.0f}min old "
                                f"(conf={conf:.0%} pnl={_pnl_pct:+.1f}%)")
                            continue
                        for ex_name, exchange in self.active_exchanges.items():
                            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                logger.warning(
                                    f"[MCP-Brain] CLOSE: {p.symbol} {p.side} — {reason}")
                                self.order_mgr.close_position(
                                    exchange, p, "mcp_brain_close")
                                break

                    elif action == "TIGHTEN":
                        for ex_name, exchange in self.active_exchanges.items():
                            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                try:
                                    t = exchange.fetch_ticker(p.symbol, p.market_type)
                                    cur = float(t.get("last", 0))
                                    if cur > 0 and p.stop_loss > 0:
                                        if p.side == "buy":
                                            new_sl = p.stop_loss + (cur - p.stop_loss) * 0.3
                                            if new_sl > p.stop_loss:
                                                logger.info(f"[MCP-Brain] TIGHTEN: {p.symbol} BUY SL {p.stop_loss:.6g} → {new_sl:.6g}")
                                                p.stop_loss = new_sl
                                        else:
                                            new_sl = p.stop_loss - (p.stop_loss - cur) * 0.3
                                            if new_sl < p.stop_loss:
                                                logger.info(f"[MCP-Brain] TIGHTEN: {p.symbol} SELL SL {p.stop_loss:.6g} → {new_sl:.6g}")
                                                p.stop_loss = new_sl
                                except Exception:
                                    pass
                                break

                    elif action == "BREAKEVEN":
                        from core.position_tracker import _fee_rate
                        rate = _fee_rate(p.market_type)
                        if p.side == "buy":
                            be = p.entry_price * (1 + rate * 2 + 0.0005)
                            if be > p.stop_loss:
                                logger.info(f"[MCP-Brain] BREAKEVEN: {p.symbol} BUY SL {p.stop_loss:.6g} → {be:.6g}")
                                p.stop_loss = be
                        else:
                            be = p.entry_price * (1 - rate * 2 - 0.0005)
                            if be < p.stop_loss:
                                logger.info(f"[MCP-Brain] BREAKEVEN: {p.symbol} SELL SL {p.stop_loss:.6g} → {be:.6g}")
                                p.stop_loss = be

                    elif action == "WIDEN":
                        if p.id not in self.order_mgr._sl_widened:
                            self.order_mgr._sl_widened.add(p.id)
                            if p.side == "buy":
                                new_sl = p.stop_loss * (1 - 0.005)
                                logger.info(f"[MCP-Brain] WIDEN: {p.symbol} BUY SL {p.stop_loss:.6g} → {new_sl:.6g}")
                                p.stop_loss = new_sl
                            else:
                                new_sl = p.stop_loss * (1 + 0.005)
                                logger.info(f"[MCP-Brain] WIDEN: {p.symbol} SELL SL {p.stop_loss:.6g} → {new_sl:.6g}")
                                p.stop_loss = new_sl

                # ═══ EXTERNAL POSITIONS — direct exchange execution ═══
                elif source == "exchange":
                    ex_name = pd["exchange"]
                    mtype = pd["market_type"]

                    if action in ("TAKE_PROFIT", "CLOSE"):
                        # Higher thresholds for external positions (unknown age/history)
                        if action == "CLOSE" and conf < 0.85:
                            logger.debug(
                                f"[MCP-Brain] EXT CLOSE skipped: {pd['symbol']} on {ex_name} "
                                f"conf={conf:.0%} < 85% threshold")
                            continue
                        if action == "TAKE_PROFIT":
                            if mtype == "futures" and (_pnl_pct < 1.0 or conf < 0.70):
                                logger.debug(
                                    f"[MCP-Brain] EXT TP skipped: {pd['symbol']} on {ex_name} "
                                    f"pnl={_pnl_pct:+.1f}% conf={conf:.0%}")
                                continue
                            if mtype == "spot" and conf < 0.80:
                                logger.debug(
                                    f"[MCP-Brain] EXT SPOT TP skipped: {pd['symbol']} on {ex_name} "
                                    f"conf={conf:.0%} < 80%")
                                continue

                        if DRY_RUN:
                            logger.info(
                                f"[MCP-Brain] [DRY] EXT {action}: {pd['symbol']} {pd['side']} "
                                f"on {ex_name} ({mtype}) — {reason}")
                        else:
                            self._close_external_position(ex_name, pd, reason)

                    elif action in ("TIGHTEN", "BREAKEVEN", "WIDEN"):
                        logger.debug(
                            f"[MCP-Brain] EXT {action} N/A: {pd['symbol']} on {ex_name} "
                            f"(no tracked SL for external positions)")

            # Persist SL changes for tracker positions
            self.tracker._save()

        except Exception as e:
            logger.debug(f"[Engine] MCP Position Monitor: {e}")

    def _close_external_position(self, ex_name: str, pos: dict, reason: str):
        """Close an exchange-discovered position NOT tracked internally.
        Futures: market close order. Spot: market sell coins."""
        exchange = self.active_exchanges.get(ex_name)
        if not exchange:
            return

        symbol = pos["symbol"]
        market_type = pos["market_type"]
        side = pos["side"]
        size = pos["size"]

        if market_type == "spot":
            coin = symbol.split("/")[0]
            try:
                bal = exchange.fetch_balance("spot")
                held = float(bal.get("free", {}).get(coin, 0) or 0)
            except Exception:
                held = 0
            if held <= 0:
                return
            try:
                t = exchange.fetch_ticker(symbol, "spot")
                px = float(t.get("last") or 0)
            except Exception:
                px = 0
            if px <= 0 or held * px < 5.0:
                return
            logger.warning(
                f"[MCP-Brain] SELL SPOT {coin} on {ex_name}: "
                f"{held:.6g} ~${held * px:.2f} | {reason}")
            try:
                exchange.create_order(symbol, "market", "sell", held, None, {}, "spot")
            except Exception as e:
                logger.error(f"[MCP-Brain] Spot sell {coin} on {ex_name} failed: {e}")

        elif market_type == "futures":
            close_side = "sell" if side == "buy" else "buy"
            ex_lower = ex_name.lower()
            params = {}
            if ex_lower in self.order_mgr._oneway_mode:
                if getattr(exchange, '_is_oneway', False):
                    params["reduceOnly"] = True
            else:
                params["positionSide"] = "LONG" if side == "buy" else "SHORT"
                params["reduceOnly"] = True

            logger.warning(
                f"[MCP-Brain] CLOSE EXT FUTURES {symbol} {side} on {ex_name}: "
                f"size={size} | {reason}")
            try:
                exchange.create_order(
                    symbol, "market", close_side, size, None, params, "futures")
            except Exception as e:
                err = str(e)
                if "reduceonly" in err.lower() or "-1106" in err:
                    self.order_mgr._oneway_mode.add(ex_lower)
                    try:
                        exchange.create_order(
                            symbol, "market", close_side, size, None, {}, "futures")
                    except Exception as e2:
                        logger.error(f"[MCP-Brain] Ext futures close retry: {e2}")
                elif "no position" in err.lower() or "does not exist" in err.lower():
                    logger.info(f"[MCP-Brain] Ext position already closed: {symbol}")
                elif any(s in err.lower() for s in (
                        "unilateral", "position mode", "positionside", "-4061", "40774")):
                    self.order_mgr._oneway_mode.add(ex_lower)
                    _ow = {"reduceOnly": True} if getattr(exchange, '_is_oneway', False) else {}
                    try:
                        exchange.create_order(
                            symbol, "market", close_side, size, None, _ow, "futures")
                    except Exception as e2:
                        logger.error(f"[MCP-Brain] Ext futures close (one-way): {e2}")
                else:
                    logger.error(f"[MCP-Brain] Ext futures close failed: {e}")

    def _run_learning(self):
        try:
            self.learner.learn(force=True)
        except Exception as e:
            logger.debug(f"[Engine] Learning error: {e}")

    def _run_optimizer(self):
        from core.auto_optimizer import AutoOptimizer
        logger.info("[Engine] Starting auto-optimization...")
        for ex in self.active_exchanges.values():
            try:
                AutoOptimizer(ex).run_all()
                break
            except Exception as e:
                logger.error(f"[Engine] Optimizer: {e}")

    # ── Main run ──────────────────────────────────────────────────────

    def run(self):
        logger.info("[Engine] Bot started — Ctrl+C to stop")
        self.notifier.alert(
            f"Bot started | {TRADING_MODE.upper()} | "
            f"{'DRY RUN $' + str(int(self.order_mgr.wallet.start_balance)) if DRY_RUN else 'LIVE'}"
        )

        # ── CLAUDE PORTFOLIO: single unified cycle (replaces per-exchange scans + MCP brain)
        schedule.every(PORTFOLIO_CYCLE_SEC).seconds.do(self._claude_portfolio_cycle)
        schedule.every(2).minutes.do(self._run_mcp_position_monitor)  # Position monitor (2 min)
        schedule.every(NEWS_INTERVAL).seconds.do(self._fetch_news)
        schedule.every(LEARN_INTERVAL).seconds.do(self._run_learning)
        schedule.every(4).hours.do(self._run_dca)
        schedule.every(24).hours.do(self._run_rebalance)
        schedule.every().day.at("00:00").do(self._daily_summary)
        # Balance refresh happens inside _claude_portfolio_cycle, but also on schedule
        schedule.every(15).minutes.do(self._log_balances)
        if not DRY_RUN:
            schedule.every(5).minutes.do(self._sync_positions)

        if TRADING_MODE in ("portfolio", "all"):
            schedule.every(PORTFOLIO_RESCAN_MINUTES).minutes.do(
                self._rescan_portfolio)

        logger.info("[Engine] Running initial Claude portfolio cycle + learn...")
        self._run_learning()
        self._claude_portfolio_cycle()   # Claude makes first decisions
        self._run_mcp_position_monitor() # Position monitor after first cycle

        # Start dedicated SL/TP monitor thread — runs every 10s, never blocked by scans
        self._stop_event = threading.Event()
        self._sltp_thread = threading.Thread(
            target=self._sltp_monitor_loop,
            args=(self._stop_event,),
            daemon=True, name="sltp-monitor")
        self._sltp_thread.start()
        logger.info("[Engine] SL/TP monitor thread started (10s interval)")

        # Register signal handlers for clean shutdown
        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else signum
            logger.info(f"[Engine] Signal {sig_name} received — shutting down")
            self._stop_event.set()
            self._shutdown()
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        atexit.register(self._shutdown)

        try:
            while True:
                schedule.run_pending()

                # Watchdog: restart SL/TP thread if it died
                if not self._sltp_thread.is_alive():
                    logger.warning("[Engine] SL/TP thread DIED — restarting")
                    self._stop_event.clear()
                    self._sltp_thread = threading.Thread(
                        target=self._sltp_monitor_loop,
                        args=(self._stop_event,),
                        daemon=True, name="sltp-monitor")
                    self._sltp_thread.start()
                    open_count = self.tracker.count_open()
                    self.notifier.error(
                        f"CRITICAL: SL/TP monitor thread crashed and was restarted.\n"
                        f"Open positions: {open_count}\n"
                        f"All positions were UNMONITORED until restart.\n"
                        f"Check logs for the crash cause.")

                # Watchdog: check exchange connectivity every 60s
                if self._cycle % 12 == 0 and self._cycle > 0:
                    self._check_exchange_health()

                self._print_live_status()
                time.sleep(5)
        except (KeyboardInterrupt, SystemExit):
            logger.info("[Engine] Shutdown received")
            self._stop_event.set()
            self._shutdown()
        except Exception as e:
            logger.critical(f"[Engine] FATAL ERROR in main loop: {e}", exc_info=True)
            self._stop_event.set()
            try:
                open_count = self.tracker.count_open()
                self.notifier.error(
                    f"FATAL: Main loop crashed!\n"
                    f"Error: {e}\n"
                    f"Open positions: {open_count}\n"
                    f"Bot will attempt restart in 30s...")
            except Exception:
                pass
            self._shutdown()
            # Exit with non-zero code so auto_restart.bat restarts the process
            # (avoids recursive self.run() which would overflow the stack on repeated crashes)
            logger.info("[Engine] Exiting for auto-restart in 10s...")
            import sys
            sys.exit(1)

    def _shutdown(self):
        if getattr(self, '_shutdown_done', False):
            return
        self._shutdown_done = True
        logger.info("[Engine] Shutting down...")
        s = self.tracker.summary()
        self.notifier.daily_summary(
            s["total_trades"], s["wins"], s["losses"], s["total_pnl"], 0.0)
        try:
            summary = self.order_mgr.compliance.export_summary()
            if summary:
                logger.info(
                    f"[Compliance] {summary.get('month')} — "
                    f"trades={summary.get('trades')} "
                    f"pnl={summary.get('total_pnl'):+.4f} USDT "
                    f"fees={summary.get('total_fees'):.4f} USDT")
        except Exception:
            pass
        self._run_learning()
        self._print_full_summary()
        logger.info("[Engine] Stopped.")

    def _daily_summary(self):
        s = self.tracker.summary()
        if DRY_RUN:
            balance = self.order_mgr.wallet.total_balance()
        else:
            balance = 0.0
            for name, ex in self.active_exchanges.items():
                if name in _UNIFIED_EXCHANGES:
                    try:
                        bal = ex.fetch_balance("spot")
                        balance += self._extract_usdt(bal, name)
                    except Exception:
                        pass
                else:
                    for mtype in ("spot", "futures"):
                        try:
                            bal = ex.fetch_balance(mtype)
                            balance += self._extract_usdt(bal, name)
                        except Exception:
                            pass
        self.notifier.daily_summary(
            s["total_trades"], s["wins"], s["losses"], s["total_pnl"], balance)

    def _print_live_status(self):
        s        = self.tracker.summary()
        uptime_m = int((time.time() - self._start_time) / 60)
        mode     = "[DRY]" if DRY_RUN else "[LIVE]"
        halted   = f" [HALTED: {self.risk.halt_reason}]" if self.risk.is_halted else ""
        fg       = self.news.fear_greed_value()
        logger.debug(
            f"{mode}{halted} up={uptime_m}m scans={self._cycle} "
            f"pnl={s['total_pnl']:+.4f} fees={s['total_fees']:.4f} "
            f"open={s['open_positions']} W={s['wins']} L={s['losses']} "
            f"F&G={fg}")

    def _print_full_summary(self):
        s      = self.tracker.summary()
        uptime = (time.time() - self._start_time) / 3600
        wallet = self.order_mgr.wallet.total_balance() if DRY_RUN else 0
        table  = Table(title="Trading Bot — Final Summary", box=box.ROUNDED)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value",  style="green")
        c = "green" if s["total_pnl"] >= 0 else "red"
        table.add_row("Uptime",        f"{uptime:.1f} hours")
        table.add_row("Mode",          f"{'DRY RUN ($' + str(int(self.order_mgr.wallet.start_balance)) + ' start)' if DRY_RUN else 'LIVE'}")
        table.add_row("Trade Mode",    TRADING_MODE.upper())
        table.add_row("Exchanges",     str(list(self.active_exchanges.keys())))
        table.add_row("Scan Cycles",   str(self._cycle))
        table.add_row("Total Trades",  str(s["total_trades"]))
        table.add_row("Wins / Losses", f"{s['wins']} / {s['losses']}")
        table.add_row("Win Rate",      f"{s['win_rate']:.1f}%")
        table.add_row("Gross PnL",     f"{s['gross_pnl']:+.4f} USDT")
        table.add_row("Fees Paid",     f"-{s['total_fees']:.4f} USDT")
        table.add_row("Net PnL",       f"[{c}]{s['total_pnl']:+.4f}[/{c}] USDT")
        table.add_row("Avg Win",       f"{s['avg_win']:+.4f} USDT")
        table.add_row("Avg Loss",      f"{s['avg_loss']:+.4f} USDT")
        if DRY_RUN:
            table.add_row("Paper Wallet", f"{wallet:.4f} USDT")
        table.add_row("Open Pos",      str(s["open_positions"]))
        table.add_row("Daily PnL",     f"[{c}]{self.risk.daily_pnl:+.4f}[/{c}] USDT")
        table.add_row("Fear & Greed",  f"{self.news.fear_greed_value()} — {self.news.fear_greed_label()}")
        table.add_row("Trending",      ", ".join(self.news.trending_coins()[:5]) or "—")
        if self.risk.is_halted:
            table.add_row("Halted", f"YES — {self.risk.halt_reason}")
        else:
            table.add_row("Halted", "No")
        table.add_row(
            "Blacklisted",
            str(list(self.order_mgr.blacklist.get_all().keys())) or "None")
        console.print(table)
