"""
dashboard.py -- LIVE Real-Time Trading Dashboard (Optimized)

Features:
  - Live prices + balances from all 3 exchanges (parallel fetching)
  - Unrealized PnL with live price tracking
  - Strategy performance breakdown
  - ROI%, profit factor, win/loss streaks
  - Hourly PnL heatmap
  - Virtual wallet display (DRY RUN)
  - Commodity/stock position highlighting
  - File-change caching (skip re-reads if file unchanged)
  - Trading mode display (USDT / PORTFOLIO / ALL)

Run from project root:
  python dashboard.py
  python dashboard.py --refresh 30 --width 100

Uses the same ``.env`` as the trading bot (``DRY_RUN`` / API keys). Refresh interval
defaults to 60s to match ``TradingBot.bat``; override with ``--refresh``. Exit with
Ctrl+C.

FIX: Bybit Unified Account balance now correctly parsed.
"""

import argparse
import json
import os
import sys
import time
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime    import datetime, timedelta, timezone
from pathlib     import Path

try:
    import shutil
except ImportError:
    shutil = None  # pragma: no cover

import importlib.util as _ilu
from loguru import logger


def _load_core_module(name: str):
    """Import a module from core/ directly, bypassing core/__init__.py
    (which pulls in BotEngine → schedule → rich, unnecessary for dashboard)."""
    p = Path(__file__).parent / "core" / "{}.py".format(name)
    if not p.exists():
        return None
    spec = _ilu.spec_from_file_location("core.{}".format(name), str(p))
    mod = _ilu.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod

# ── ANSI colours ──────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
CYAN   = "\033[96m"
WHITE  = "\033[97m"
DIM    = "\033[2m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
ORANGE = "\033[38;5;208m"
PURPLE = "\033[95m"
BLUE   = "\033[94m"
GOLD   = "\033[38;5;220m"
SILVER = "\033[38;5;250m"

# Defaults match TradingBot.bat (menu: "Dashboard 60s"); override via --refresh
REFRESH_SECONDS = 60
DASH_WIDTH = 80

# Background fetch thread: last error for footer (throttled display)
_BG_LAST_ERR = None

EX_COLOUR = {
    "binance": "\033[38;5;220m",
    "bybit":   "\033[38;5;214m",
    "bitget":  "\033[38;5;48m",
}

_UNIFIED_EXCHANGES = {"bybit"}

# Commodity base symbols for highlighting
_COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL"}
_STOCK_BASES = {
    "AAPL", "TSLA", "GOOG", "GOOGL", "AMZN", "MSFT", "META", "NVDA",
    "NFLX", "AMD", "COIN", "MSTR", "GME",
}

_ASSET_ICON = {
    "XAU": GOLD + "Au" + RESET,
    "XAG": SILVER + "Ag" + RESET,
    "WTI": ORANGE + "Oil" + RESET,
    "CL":  ORANGE + "Oil" + RESET,
}

_START_TIME = time.time()


def enable_ansi():
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
    # Force UTF-8 output to support ═ ✓ ✗ ★ ● characters on Windows
    if os.name == "nt":
        os.system("chcp 65001 >nul 2>&1")
    try:
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass


def clr():
    os.system("cls" if os.name == "nt" else "clear")


def col(text, code):
    return code + str(text) + RESET


# ── ANSI-aware width helpers ──────────────────────────────────────────
# `col()` wraps text in ANSI escape codes which Python's str.format counts
# toward field width (e.g. "{:<11}".format(col("Period", DIM)) sees a
# 14-char string for a 6-char visible word and skips padding entirely).
# Use vlen / vljust / vrjust to align colored cells visibly.
import re as _re
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*m")
def vlen(s):
    """Visible length of a string after stripping ANSI escape sequences."""
    return len(_ANSI_RE.sub("", s)) if s else 0
def vljust(s, width):
    """Left-justify `s` to `width` visible columns, ignoring ANSI codes."""
    pad = max(0, width - vlen(s))
    return (s if s is not None else "") + " " * pad
def vrjust(s, width):
    """Right-justify `s` to `width` visible columns, ignoring ANSI codes."""
    pad = max(0, width - vlen(s))
    return " " * pad + (s if s is not None else "")


def pnl_str(val, suffix=" USDT"):
    sign = "+" if val >= 0 else ""
    c    = GREEN if val >= 0 else RED
    return col("{}{:.4f}{}".format(sign, val, suffix), c)


def pnl_str_short(val):
    sign = "+" if val >= 0 else ""
    c    = GREEN if val >= 0 else RED
    return col("{}{:.4f}".format(sign, val), c)


def wr_col(wr):
    return GREEN if wr >= 55 else (YELLOW if wr >= 45 else RED)


def fg_str(value):
    v = int(value)
    if v >= 75:  return col("{} Extreme Greed".format(v), GREEN)
    if v >= 55:  return col("{} Greed".format(v),         GREEN)
    if v >= 45:  return col("{} Neutral".format(v),       YELLOW)
    if v >= 25:  return col("{} Fear".format(v),          ORANGE)
    return col("{} Extreme Fear".format(v), RED)


def _asset_tag(symbol: str) -> str:
    """Return a colored tag for commodities/stocks, or empty string."""
    base = symbol.split("/")[0].upper()
    if base in _ASSET_ICON:
        return " " + _ASSET_ICON[base]
    if base in _STOCK_BASES:
        return " " + col("$", BLUE)
    return ""


def _uptime_str() -> str:
    elapsed = time.time() - _START_TIME
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return "{}h {}m".format(h, m)
    return "{}m {}s".format(m, s)


# ══════════════════════════════════════════════════════════════════════
# File-change caching — skip re-reads if file hasn't been modified
# ══════════════════════════════════════════════════════════════════════

class FileCache:
    """Cache JSON file reads — only re-read when mtime changes."""

    def __init__(self):
        self._cache: dict[str, tuple] = {}   # path -> (mtime, data)

    def load(self, path_str: str):
        try:
            p = Path(path_str)
            if not p.exists():
                return None
            mt = p.stat().st_mtime
            cached = self._cache.get(path_str)
            if cached and cached[0] == mt:
                return cached[1]
            data = json.loads(p.read_text(encoding="utf-8"))
            self._cache[path_str] = (mt, data)
            return data
        except Exception:
            return None


_file_cache = FileCache()


# ══════════════════════════════════════════════════════════════════════
# Balance extractor — handles all 3 exchange response formats
# ══════════════════════════════════════════════════════════════════════

def extract_usdt(bal: dict, exchange_name: str = "") -> float:
    if not bal:
        return 0.0
    ex = exchange_name.lower()

    if ex == "bybit":
        # Bybit Unified Account has several balance fields with very different meanings:
        #   totalEquity           = wallet + unrealized PnL + USD value of every non-USDT
        #                           spot holding (BTC/ETH/DOGE/... all counted as collateral).
        #                           Matches the Bybit app's top-line "Total Equity" number
        #                           but over-states spendable USDT by the value of non-USDT
        #                           spot coins — for the dashboard we want to report actual
        #                           USDT, not total equity.
        #   totalAvailableBalance = free USDT-equivalent margin for new orders
        #   availableToWithdraw   = free USDT (excludes locked margin)
        # Priority order below matches core/bot_engine.py._extract_usdt (2026-04-12 fix)
        # so dashboard and sizing agree on the same number.
        try:
            result_list = bal.get("info", {}).get("result", {}).get("list", [])
            if result_list and isinstance(result_list, list):
                acct = result_list[0] if result_list else {}
                for field in ("totalAvailableBalance",
                              "totalMarginBalance",
                              "totalWalletBalance"):
                    val = acct.get(field)
                    if val is not None:
                        try:
                            v = float(val)
                            if v > 0:
                                return v
                        except (TypeError, ValueError):
                            pass
                # Per-coin USDT free balance
                coins = acct.get("coin", [])
                if isinstance(coins, list):
                    for c in coins:
                        if c.get("coin") == "USDT":
                            for f2 in ("availableToWithdraw",
                                       "availableBalance",
                                       "walletBalance"):
                                val2 = c.get(f2)
                                if val2 is not None:
                                    try:
                                        v2 = float(val2)
                                        if v2 > 0:
                                            return v2
                                    except (TypeError, ValueError):
                                        pass
                # Last-resort: totalEquity. Over-states by spot-coin value but
                # better than reporting 0 when the preferred fields are missing.
                te = acct.get("totalEquity")
                if te is not None:
                    try:
                        v = float(te)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
        except Exception:
            pass
        # Fallback 2: ccxt parsed fields — total.USDT or USDT.total
        usdt = bal.get("USDT") or {}
        if isinstance(usdt, dict):
            for key in ("total", "free"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0: return v
                    except (TypeError, ValueError):
                        pass
        total_dict = bal.get("total") or {}
        if isinstance(total_dict, dict):
            val = total_dict.get("USDT")
            if val is not None:
                try:
                    v = float(val)
                    if v > 0: return v
                except (TypeError, ValueError):
                    pass
        return 0.0

    if ex == "bitget":
        usdt = bal.get("USDT") or {}
        if isinstance(usdt, dict):
            for key in ("total", "free"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0: return v
                    except (TypeError, ValueError):
                        pass
        total_dict = bal.get("total") or {}
        if isinstance(total_dict, dict):
            val = total_dict.get("USDT")
            if val is not None:
                try:
                    v = float(val)
                    if v > 0: return v
                except (TypeError, ValueError):
                    pass
        try:
            data = bal.get("info", {}).get("data", [{}])
            if data:
                for field in ("usdtEquity", "available", "balance"):
                    val = data[0].get(field)
                    if val: return float(val)
        except Exception:
            pass
        free_dict = bal.get("free") or {}
        if isinstance(free_dict, dict):
            val = free_dict.get("USDT")
            if val:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return 0.0

    # Standard ccxt (Binance)
    usdt = bal.get("USDT")
    if isinstance(usdt, dict):
        for key in ("total", "free"):
            val = usdt.get(key)
            if val is not None:
                try:
                    v = float(val)
                    if v > 0: return v
                except (TypeError, ValueError):
                    pass
    free = bal.get("free") or {}
    if isinstance(free, dict) and free.get("USDT"):
        try:
            return float(free["USDT"])
        except (TypeError, ValueError):
            pass
    total = bal.get("total") or {}
    if isinstance(total, dict) and total.get("USDT"):
        try:
            return float(total["USDT"])
        except (TypeError, ValueError):
            pass
    return 0.0


def extract_all_coins(bal: dict) -> dict:
    """Extract all non-zero coin balances from ccxt balance response.
    Returns {asset: {"free": x, "total": y}} for every coin with total > 0.
    Filters out Binance Earn/Savings wrapped tokens (LD-prefix, BETH, etc.)."""
    coins = {}
    # Binance Earn wrapped tokens & other non-tradeable assets
    _SKIP_ASSETS = {"BETH", "WBETH"}
    total_dict = bal.get("total")
    free_dict = bal.get("free")
    if isinstance(total_dict, dict) and isinstance(free_dict, dict):
        for asset, amt in total_dict.items():
            try:
                t = float(amt or 0)
            except (TypeError, ValueError):
                continue
            if t <= 0:
                continue
            # Skip Binance Earn/Savings wrapped tokens
            if asset.startswith("LD") or asset in _SKIP_ASSETS:
                continue
            f = 0.0
            try:
                f = float(free_dict.get(asset, 0) or 0)
            except (TypeError, ValueError):
                pass
            coins[asset] = {"free": f, "total": t}
    return coins


# ══════════════════════════════════════════════════════════════════════
# Live data fetcher — parallel price fetching
# ══════════════════════════════════════════════════════════════════════

class LiveFetcher:

    def __init__(self):
        self._lock       = threading.Lock()
        self._prices     = {}
        self._balances   = {}
        self._balance_detail = {}   # {exchange: {"spot": x, "futures": y, "unified": bool}}
        self._coin_balances  = {}   # {exchange: {asset: {"free": x, "total": y, "mtype": "spot"/"futures"}}}
        self._ex_status  = {}
        self._last_fetch = 0.0
        self._exchanges  = {}
        self._dry_run    = True
        self._wallet_bal = {}       # virtual wallet balances (DRY RUN)
        self._trading_mode = "usdt_only"
        self._pair_counts  = {}     # exchange -> {"spot": n, "futures": n}
        self._regime_data    = {}   # {symbol: {regime, adx, hurst, volatility, atr_pct}}
        self._funding_rates  = {}   # {exchange: {symbol: rate}}
        self._init_exchanges()

    def _init_exchanges(self):
        try:
            sys.path.insert(0, str(Path(__file__).parent))
            from exchanges import BinanceClient, BybitClient, BitgetClient
            from config import DRY_RUN, TRADING_MODE
            self._dry_run = DRY_RUN
            self._trading_mode = TRADING_MODE
            candidates = {
                "binance": BinanceClient,
                "bybit":   BybitClient,
                "bitget":  BitgetClient,
            }
            for name, cls in candidates.items():
                try:
                    ex = cls()
                    if getattr(ex, "_connected", False):
                        self._exchanges[name] = ex
                        self._ex_status[name] = "connecting..."
                except Exception as e:
                    self._ex_status[name] = "init error: {}".format(str(e)[:40])

            # Load virtual wallet balances if DRY RUN
            if DRY_RUN:
                try:
                    wf = Path("data/virtual_wallet.json")
                    if wf.exists():
                        wd = json.loads(wf.read_text(encoding="utf-8"))
                        self._wallet_bal = wd.get("balances", {})
                except Exception:
                    pass
        except Exception:
            pass

    def _fetch_balance(self, name, ex):
        """Fetch balance for one exchange. Returns (name, usdt_total).
        Also stores per-market-type breakdown in _balance_detail and coin holdings."""
        try:
            all_coins = {}  # {asset: {"free": x, "total": y, "mtype": "spot"/"futures"}}
            if name in _UNIFIED_EXCHANGES:
                bal = ex.fetch_balance("spot")
                total = extract_usdt(bal, name)
                coins = extract_all_coins(bal)
                for asset, info in coins.items():
                    all_coins[asset] = {**info, "mtype": "unified"}
                with self._lock:
                    self._balance_detail[name] = {"spot": total, "futures": total, "unified": True}
                    self._coin_balances[name] = all_coins
                return (name, total)
            else:
                total = 0.0
                detail = {"spot": 0.0, "futures": 0.0, "unified": False}
                for mtype in ("spot", "futures"):
                    try:
                        bal = ex.fetch_balance(mtype)
                        amt = extract_usdt(bal, name)
                        detail[mtype] = amt
                        total += amt
                        coins = extract_all_coins(bal)
                        for asset, info in coins.items():
                            if asset in all_coins:
                                all_coins[asset]["total"] += info["total"]
                                all_coins[asset]["free"] += info["free"]
                            else:
                                all_coins[asset] = {**info, "mtype": mtype}
                    except Exception:
                        pass
                with self._lock:
                    # Don't overwrite a known good balance with 0 (intermittent API failure)
                    if total > 0 or name not in self._balances or self._balances[name] == 0.0:
                        self._balance_detail[name] = detail
                        self._coin_balances[name] = all_coins
                    elif total == 0.0 and self._balances.get(name, 0) > 0:
                        # Keep previous balance detail, just return old value
                        return (name, self._balances[name])
                return (name, total)
        except Exception as e:
            with self._lock:
                self._ex_status[name] = str(e)[:50]
                # Return last known balance instead of 0
                prev = self._balances.get(name, 0.0)
            return (name, prev)

    def _fetch_price(self, ex, name, sym):
        """Fetch price for one symbol on one exchange."""
        try:
            mtype = "futures" if ":" in sym else "spot"
            t = ex.fetch_ticker(sym, mtype)
            price = float(t.get("last") or t.get("close") or 0)
            return (name, sym, price) if price > 0 else None
        except Exception:
            return None

    def fetch(self, symbols_needed: set):
        """Fetch prices + balances using parallel threads."""
        # Parallel balance fetch
        with ThreadPoolExecutor(max_workers=4) as pool:
            bal_futures = {
                pool.submit(self._fetch_balance, name, ex): name
                for name, ex in self._exchanges.items()
            }
            for f in as_completed(bal_futures):
                try:
                    name, usdt = f.result()
                    with self._lock:
                        self._balances[name] = usdt
                        self._ex_status[name] = "OK"
                except Exception:
                    pass

        # Parallel price fetch (all exchanges × all symbols)
        with ThreadPoolExecutor(max_workers=8) as pool:
            price_futures = []
            for name, ex in self._exchanges.items():
                for sym in symbols_needed:
                    price_futures.append(
                        pool.submit(self._fetch_price, ex, name, sym))
            for f in as_completed(price_futures):
                try:
                    result = f.result()
                    if result:
                        name, sym, price = result
                        with self._lock:
                            self._prices["{}:{}".format(name, sym)] = price
                            if sym not in self._prices:
                                self._prices[sym] = price
                                self._prices[f"{sym}:count"] = 1
                            else:
                                cnt = self._prices.get(f"{sym}:count", 1)
                                self._prices[sym] = (self._prices[sym] * cnt + price) / (cnt + 1)
                                self._prices[f"{sym}:count"] = cnt + 1
                except Exception:
                    pass

        # Refresh virtual wallet
        if self._dry_run:
            try:
                wf = Path("data/virtual_wallet.json")
                if wf.exists():
                    wd = json.loads(wf.read_text(encoding="utf-8"))
                    with self._lock:
                        self._wallet_bal = wd.get("balances", {})
            except Exception:
                pass

        self._last_fetch = time.time()

    def get_price(self, exchange: str, symbol: str) -> float:
        with self._lock:
            return self._prices.get("{}:{}".format(exchange.lower(), symbol)) or \
                   self._prices.get(symbol) or 0.0

    def get_balance(self, exchange: str) -> float:
        with self._lock:
            return self._balances.get(exchange.lower(), 0.0)

    def get_status(self, exchange: str) -> str:
        with self._lock:
            return self._ex_status.get(exchange.lower(), "not connected")

    def all_balances(self) -> dict:
        with self._lock:
            return dict(self._balances)

    def all_statuses(self) -> dict:
        with self._lock:
            return dict(self._ex_status)

    def wallet_balances(self) -> dict:
        with self._lock:
            return dict(self._wallet_bal)

    def balance_detail(self) -> dict:
        """Per-exchange spot/futures breakdown."""
        with self._lock:
            return dict(self._balance_detail)

    def coin_balances(self) -> dict:
        """Per-exchange coin holdings: {exchange: {asset: {"free", "total", "mtype"}}}."""
        with self._lock:
            return {k: dict(v) for k, v in self._coin_balances.items()}

    def seconds_since_fetch(self) -> float:
        return time.time() - self._last_fetch if self._last_fetch > 0 else 999

    def is_dry_run(self) -> bool:
        return self._dry_run

    def trading_mode(self) -> str:
        return self._trading_mode

    def fetch_regime_data(self, symbols: set):
        """Fetch market regime for top symbols using the regime detector."""
        try:
            _mod = _load_core_module("market_regime")
            if not _mod:
                return
            MarketRegimeDetector = _mod.MarketRegimeDetector
            detector = MarketRegimeDetector()
            # Only check top 8 symbols to avoid excessive API calls
            top_syms = sorted(symbols)[:8]
            for sym in top_syms:
                # Use the first connected exchange
                for name, ex in self._exchanges.items():
                    try:
                        info = detector.detect_detailed(ex, sym, "1h", 200)
                        if info.get("regime") != "unknown":
                            with self._lock:
                                self._regime_data[sym] = info
                            break
                    except Exception:
                        continue
        except Exception:
            pass

    def fetch_funding_rates(self):
        """Fetch funding rates for top futures symbols."""
        top_futures = [
            "BTC/USDT:USDT", "ETH/USDT:USDT", "SOL/USDT:USDT",
            "XRP/USDT:USDT", "DOGE/USDT:USDT", "BNB/USDT:USDT",
        ]
        for name, ex in self._exchanges.items():
            rates = {}
            for sym in top_futures:
                try:
                    if not hasattr(ex, 'exchange') or not ex.exchange:
                        continue
                    funding = ex.exchange.fetch_funding_rate(sym)
                    if funding and 'fundingRate' in funding:
                        rates[sym] = float(funding['fundingRate'])
                except Exception:
                    continue
            if rates:
                with self._lock:
                    self._funding_rates[name] = rates

    def regime_data(self) -> dict:
        with self._lock:
            return dict(self._regime_data)

    def funding_rates(self) -> dict:
        with self._lock:
            return {k: dict(v) for k, v in self._funding_rates.items()}

    def fetch_live_positions(self) -> list:
        """Fetch actual open positions from all connected exchanges.
        Includes FUTURES positions + SPOT holdings (non-USDT balances).
        Returns list of dicts compatible with the dashboard position format."""
        if self._dry_run:
            return []
        live = []

        def _fetch_futures(name, ex):
            """Fetch open futures positions."""
            try:
                positions = ex.fetch_positions()
                results = []
                for p in positions:
                    size = float(p.get("contracts") or p.get("contractSize") or 0)
                    if size == 0:
                        continue
                    side_raw = (p.get("side") or "").lower()
                    if side_raw not in ("long", "short"):
                        continue
                    side = "buy" if side_raw == "long" else "sell"
                    entry = float(p.get("entryPrice") or p.get("info", {}).get("avgPrice") or 0)
                    symbol = p.get("symbol", "")
                    upnl = float(p.get("unrealizedPnl") or 0)
                    lev = int(float(p.get("leverage") or 1))
                    liq = float(p.get("liquidationPrice") or 0)
                    results.append({
                        "id": "LIVE-{}-{}-{}".format(name, symbol, side),
                        "exchange": name.capitalize(),
                        "symbol": symbol,
                        "side": side,
                        "market_type": "futures",
                        "strategy": "exchange",
                        "entry_price": entry,
                        "size": size,
                        "stop_loss": liq if liq > 0 else 0,
                        "take_profit": 0,
                        "leverage": lev,
                        "open_time": time.time(),
                        "paper_trade": False,
                        "_live_upnl": upnl,
                        "_from_exchange": True,
                    })
                return results
            except Exception as e:
                # 2026-04-16: Was silently swallowed — now logged at warning
                # so a dead exchange client (e.g. Bybit init failure) is visible.
                logger.warning(f"[Dashboard] {name} futures fetch failed: {str(e)[:150]}")
                return []

        def _avg_cost_from_trades(ex, symbol, held_qty):
            """Try to compute average buy cost from recent trade history."""
            try:
                trades = ex.exchange.fetch_my_trades(symbol, limit=50)
                if not trades:
                    return 0.0
                # Walk backwards through buys to find cost basis for held_qty
                total_cost = 0.0
                total_qty = 0.0
                for t in reversed(trades):
                    if t.get("side") != "buy":
                        continue
                    qty = float(t.get("amount", 0))
                    px = float(t.get("price", 0))
                    if qty <= 0 or px <= 0:
                        continue
                    need = held_qty - total_qty
                    take = min(qty, need)
                    total_cost += take * px
                    total_qty += take
                    if total_qty >= held_qty * 0.99:  # close enough
                        break
                if total_qty > 0:
                    return total_cost / total_qty
            except Exception:
                pass
            return 0.0

        def _fetch_spot_holdings(name, ex):
            """Fetch non-USDT spot holdings as open SPOT positions."""
            try:
                bal = ex.fetch_balance("spot")
                results = []
                # Only use "total" for holdings display — includes assets in open orders
                asset_dict = bal.get("total", {})
                if isinstance(asset_dict, dict):
                    for asset, amount in asset_dict.items():
                        if asset in ("USDT", "USD", "BUSD", "USDC"):
                            continue
                        # Skip Binance Earn/Savings wrapped tokens
                        if asset.startswith("LD") or asset in ("BETH", "WBETH", "BETH"):
                            continue
                        try:
                            amt = float(amount)
                        except (TypeError, ValueError):
                            continue
                        if amt <= 0:
                            continue
                        symbol = "{}/USDT".format(asset)
                        # Estimate value — skip dust (<$1)
                        price = self.get_price(name, symbol)
                        if price and amt * price < 1.0:
                            continue
                        # Try to get actual average buy cost from trade history
                        avg_cost = _avg_cost_from_trades(ex, symbol, amt)
                        pid = "SPOT-{}-{}-buy".format(name, symbol)
                        results.append({
                            "id": pid,
                            "exchange": name.capitalize(),
                            "symbol": symbol,
                            "side": "buy",
                            "market_type": "spot",
                            "strategy": "holding",
                            "entry_price": avg_cost,  # 0 if trade history unavailable
                            "current_price": price or 0,
                            "size": amt,
                            "stop_loss": 0,
                            "take_profit": 0,
                            "leverage": 1,
                            "open_time": time.time(),
                            "paper_trade": False,
                            "_from_exchange": True,
                        })
                return results
            except Exception as e:
                logger.warning(f"[Dashboard] {name} spot holdings fetch failed: {str(e)[:150]}")
                return []

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = {}
            for n, e in self._exchanges.items():
                futs[pool.submit(_fetch_futures, n, e)] = n
                futs[pool.submit(_fetch_spot_holdings, n, e)] = n
            for f in as_completed(futs):
                try:
                    live.extend(f.result())
                except Exception:
                    pass
        with self._lock:
            self._live_positions = live
        return live

    def get_live_positions(self) -> list:
        with self._lock:
            return list(getattr(self, '_live_positions', []))


# ══════════════════════════════════════════════════════════════════════
# JSON data loaders (with file-change caching)
# ══════════════════════════════════════════════════════════════════════

def load_positions(fetcher: "LiveFetcher" = None):
    all_open   = []
    all_closed = []
    seen_ids   = set()
    is_live    = not load_mode()  # True when DRY_RUN=false (LIVE mode)

    def _ingest(data):
        if not data:
            return
        for pos in data.get("open", []):
            pid = pos.get("id", "")
            if pid in seen_ids:
                continue
            # LIVE mode: skip paper trades — only show real positions
            # Default False: positions missing the key are assumed real (not paper)
            if is_live and pos.get("paper_trade", False):
                continue
            seen_ids.add(pid)
            all_open.append(pos)
        for pos in data.get("closed", []):
            pid = pos.get("id", "")
            if pid in seen_ids:
                continue
            # LIVE mode: skip paper trades in history too
            if is_live and pos.get("paper_trade", False):
                continue
            seen_ids.add(pid)
            all_closed.append(pos)

    _ingest(_file_cache.load("data/positions.json"))
    for profile in ("conservative", "moderate", "aggressive"):
        _ingest(_file_cache.load("data/profiles/{}/positions.json".format(profile)))

    tracked_syms = set()
    for p in all_open:
        ex = (p.get("exchange") or "").lower()
        sym = p.get("symbol", "")
        side = p.get("side", "")
        tracked_syms.add((ex, sym, side))

    # 2026-04-16: Merge bot engine's universal exchange snapshot.
    # This is the authoritative cross-exchange view written by
    # bot_engine._fetch_all_exchange_positions(). It surfaces manual
    # positions even when the dashboard's own LiveFetcher failed to
    # initialize (e.g. Bybit API error at startup).
    try:
        ex_snap = _file_cache.load("data/exchange_positions.json") or {}
        snap_ts = float(ex_snap.get("ts") or 0)
        # Treat as fresh if written within last 5 minutes
        if snap_ts and (time.time() - snap_ts) < 300:
            for ep in ex_snap.get("positions", []):
                ex = (ep.get("exchange") or "").lower()
                sym = ep.get("symbol", "")
                side = ep.get("side", "")
                if not ex or not sym or not side:
                    continue
                if (ex, sym, side) in tracked_syms:
                    continue
                # Normalize to dashboard position shape
                mapped = {
                    "id": ep.get("id") or "ENG-{}-{}-{}".format(ex, sym, side),
                    "exchange": ex.capitalize(),
                    "symbol": sym,
                    "side": side,
                    "market_type": ep.get("market_type", "futures"),
                    "strategy": "manual",
                    "entry_price": float(ep.get("entry_price") or 0),
                    "current_price": float(ep.get("current_price") or 0),
                    "size": float(ep.get("size") or 0),
                    "stop_loss": float(ep.get("stop_loss") or ep.get("liquidation_price") or 0),
                    "take_profit": float(ep.get("take_profit") or 0),
                    "leverage": int(ep.get("leverage") or 1),
                    "open_time": time.time(),
                    "paper_trade": False,
                    "_live_upnl": float(ep.get("pnl") or 0),
                    "_from_exchange": True,
                }
                all_open.append(mapped)
                tracked_syms.add((ex, sym, side))
    except Exception as _e:
        logger.debug(f"[Dashboard] exchange_positions snapshot merge: {_e}")

    # Merge live exchange positions not already tracked locally (LiveFetcher path)
    if fetcher:
        live_pos = fetcher.get_live_positions()
        for lp in live_pos:
            ex = (lp.get("exchange") or "").lower()
            sym = lp.get("symbol", "")
            side = lp.get("side", "")
            if (ex, sym, side) not in tracked_syms:
                all_open.append(lp)
                tracked_syms.add((ex, sym, side))

    all_closed.sort(key=lambda x: x.get("close_time", 0), reverse=True)
    return all_open, all_closed


def load_news():       return _file_cache.load("data/news_cache.json")
def load_auto_mut():   return _file_cache.load("data/auto_mutations.json")
def load_post_mortem():return _file_cache.load("data/post_mortem.json")
def load_risk_state(): return _file_cache.load("data/risk_state.json")


def load_warehouse_stats() -> dict:
    """Warehouse-backed stats for the dashboard.

    Reads data/warehouse.sqlite directly (no ORM, no core/__init__).
    Returns {} if the DB doesn't exist or an error occurs.
    """
    out: dict = {"per_symbol": [], "per_family": [], "slippage": {}}
    db_path = Path("data/warehouse.sqlite")
    if not db_path.exists():
        return out
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Per-symbol expectancy + PF (closed trades only)
        rows = conn.execute(
            """SELECT symbol,
                      COUNT(*) AS n,
                      SUM(realized_pnl) AS net,
                      SUM(CASE WHEN realized_pnl > 0 THEN realized_pnl ELSE 0 END) AS gw,
                      SUM(CASE WHEN realized_pnl < 0 THEN -realized_pnl ELSE 0 END) AS gl,
                      SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
                 FROM trades
                WHERE status='CLOSED'
                GROUP BY symbol
                ORDER BY n DESC
                LIMIT 8"""
        ).fetchall()
        out["per_symbol"] = [dict(r) for r in rows]

        # Per-strategy-family net PnL + count
        fam = conn.execute(
            """SELECT COALESCE(strategy_family,'unknown') AS fam,
                      COUNT(*) AS n,
                      SUM(realized_pnl) AS net,
                      SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) AS wins
                 FROM trades
                WHERE status='CLOSED'
                GROUP BY fam
                ORDER BY n DESC
                LIMIT 6"""
        ).fetchall()
        out["per_family"] = [dict(r) for r in fam]

        # Slippage vs fee ratio — only rows where both are non-null
        slip = conn.execute(
            """SELECT AVG(slippage) AS avg_slip,
                      AVG(fee)      AS avg_fee,
                      COUNT(*)      AS n
                 FROM trades
                WHERE status='CLOSED' AND slippage IS NOT NULL AND fee IS NOT NULL"""
        ).fetchone()
        if slip:
            out["slippage"] = dict(slip)

        # Counts for header line
        tot = conn.execute(
            "SELECT COUNT(*) AS c FROM trades WHERE status='CLOSED'"
        ).fetchone()
        cnd = conn.execute(
            "SELECT COUNT(*) AS c FROM candidates"
        ).fetchone()
        out["total_trades"] = int(tot["c"]) if tot else 0
        out["total_candidates"] = int(cnd["c"]) if cnd else 0
        conn.close()
    except Exception as e:
        out["_error"] = str(e)
    return out


def load_block_reasons(tail_lines: int = 800) -> dict:
    """Parse today's bot log tail to count recent block reasons.

    Returns {"BLACKLIST": N, "DYN_BL": N, "UNIVERSE": N, "RISK": N,
             "HOUR": N, "TIER": N, "RR": N, "OTHER": N, "total": N,
             "claude_empty": N, "algo_zero": N}

    Used by the dashboard "WHY NO TRADES" panel to expose why the bot
    is idle so the user doesn't have to tail the log manually.
    """
    out = {
        "BLACKLIST": 0, "DYN_BL": 0, "UNIVERSE": 0, "RISK": 0,
        "HOUR": 0, "TIER": 0, "RR": 0, "SHORTS": 0, "LEV": 0,
        "OTHER": 0, "total": 0,
        "claude_empty": 0, "algo_zero": 0, "actions": 0,
    }
    try:
        log_path = Path("logs") / ("bot_" + datetime.now().strftime("%Y-%m-%d") + ".log")
        if not log_path.exists():
            return out
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            # Read last ~tail_lines lines cheaply
            try:
                f.seek(0, 2)
                size = f.tell()
                # ~200 chars per line avg → read that many bytes
                back = min(size, tail_lines * 260)
                f.seek(max(0, size - back))
                f.readline()  # discard partial line
                lines = f.readlines()[-tail_lines:]
            except Exception:
                f.seek(0)
                lines = f.readlines()[-tail_lines:]
        for ln in lines:
            if "BLOCKED by" in ln or "BLOCKED:" in ln or "blocked by" in ln.lower():
                out["total"] += 1
                lnl = ln.lower()
                if "blacklist_hard" in lnl or "hard blacklist" in lnl:
                    out["BLACKLIST"] += 1
                elif "dyn" in lnl and "blacklist" in lnl:
                    out["DYN_BL"] += 1
                elif "universe" in lnl or "spread" in lnl or "atr" in lnl or "depth" in lnl:
                    out["UNIVERSE"] += 1
                elif "risk" in lnl or "halt" in lnl or "daily loss" in lnl:
                    out["RISK"] += 1
                elif "hour" in lnl or "blocked hour" in lnl:
                    out["HOUR"] += 1
                elif "tier" in lnl or "leverage tier" in lnl:
                    out["TIER"] += 1
                elif "r:r" in lnl or "risk/reward" in lnl or "rr<" in lnl:
                    out["RR"] += 1
                elif "short" in lnl:
                    out["SHORTS"] += 1
                elif "leverage cap" in lnl:
                    out["LEV"] += 1
                else:
                    out["OTHER"] += 1
            if "Claude returned empty" in ln or "No trades:" in ln or '"actions":[]' in ln:
                out["claude_empty"] += 1
            if "Algorithmic fallback: 0 actions" in ln:
                out["algo_zero"] += 1
            if "Algorithmic fallback:" in ln and " actions" in ln:
                try:
                    n = int(ln.split("Algorithmic fallback:")[1].split(" actions")[0].strip())
                    out["actions"] += n
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _hour_class(hour: int) -> tuple:
    """Classify a UTC hour against config trading gates.
    Returns (label, ansi color)."""
    try:
        from config import (
            ALLOWED_HOURS_UTC, PEAK_HOURS_UTC,
            WARMUP_HOURS_UTC, BLOCKED_HOURS_UTC,
        )
    except Exception:
        return ("?", DIM)
    if hour in BLOCKED_HOURS_UTC:
        return ("BLOCKED", RED)
    if hour in PEAK_HOURS_UTC:
        return ("PEAK",    GOLD + BOLD)
    if hour in WARMUP_HOURS_UTC:
        return ("WARMUP",  YELLOW)
    # Default: allowed (any hour not explicitly blocked/warmup/peak)
    return ("ALLOWED", GREEN)


def load_mode():
    try:
        from config import DRY_RUN
        return DRY_RUN
    except Exception:
        return True


# ══════════════════════════════════════════════════════════════════════
# Stats helpers
# ══════════════════════════════════════════════════════════════════════

def calc_unrealized(open_pos: list, fetcher: LiveFetcher) -> dict:
    result = {}
    for pos in open_pos:
        pid    = pos.get("id", "")
        sym    = pos.get("symbol", "")
        side   = pos.get("side", "buy")
        entry  = float(pos.get("entry_price", 0) or 0)
        size   = float(pos.get("size", 0) or 0)
        lev    = int(pos.get("leverage", 1) or 1)
        mtype  = pos.get("market_type", "spot")
        exname = (pos.get("exchange") or "").lower()

        live_price = fetcher.get_price(exname, sym)
        if not live_price or not size:
            # Still check for exchange-provided uPnL (e.g., Bybit/Binance futures)
            live_upnl = pos.get("_live_upnl")
            if live_upnl is not None and live_upnl != 0:
                result[pid] = {"price": 0.0, "upnl": float(live_upnl),
                               "upnl_pct": None, "move_pct": 0.0}
            else:
                result[pid] = {"price": 0.0, "upnl": None, "upnl_pct": None, "move_pct": 0.0}
            continue

        # Exchange-provided uPnL is authoritative for live futures positions
        live_upnl = pos.get("_live_upnl")
        if live_upnl is not None and pos.get("_from_exchange") and mtype == "futures":
            upnl = float(live_upnl)
            move_pct = (live_price - entry) / entry * 100 if entry > 0 else 0
            margin = (size * entry) / max(lev, 1) if entry > 0 else 1
            upnl_pct = (upnl / max(margin, 0.0001)) * 100 if margin > 0 else 0
            result[pid] = {"price": live_price, "upnl": upnl, "upnl_pct": upnl_pct,
                           "move_pct": move_pct}
            continue

        # Holdings with unknown entry: estimate value but no PnL
        if not entry:
            value = live_price * size
            result[pid] = {"price": live_price, "upnl": None, "upnl_pct": None,
                           "move_pct": 0.0, "value": value}
            continue

        move_pct = (live_price - entry) / entry * 100
        if side == "buy":
            upnl = (live_price - entry) * size
        else:
            upnl = (entry - live_price) * size

        # NOTE: entry_fee is already deducted from exchange balance at open time.
        # Unrealized PnL shows raw price movement only — fees are NOT subtracted
        # because entry fee is already paid (double-counting) and exit fee is
        # not yet incurred.  Net PnL after fees is shown on close.

        margin   = (size * entry) / max(lev, 1)
        upnl_pct = (upnl / max(margin, 0.0001)) * 100
        result[pid] = {"price": live_price, "upnl": upnl, "upnl_pct": upnl_pct, "move_pct": move_pct}
    return result


def calc_stats(closed):
    today     = datetime.now().date()
    yesterday = today - timedelta(days=1)
    t_pnl = t_gross = t_fees = 0.0
    t_n   = t_wins  = 0
    y_pnl = y_gross = y_fees = 0.0
    y_n   = y_wins  = 0
    a_pnl = a_gross = a_fees = 0.0
    a_wins = 0; a_best = 0.0; a_worst = 0.0
    win_amounts = []; loss_amounts = []

    for t in closed:
        pnl   = t.get("pnl",       0) or 0
        gross = t.get("gross_pnl", pnl) or pnl
        fees  = t.get("total_fees", 0) or 0
        ct    = t.get("close_time", 0) or 0
        a_pnl += pnl; a_gross += gross; a_fees += fees
        a_best  = max(a_best,  pnl)
        a_worst = min(a_worst, pnl)
        if pnl > 0:
            a_wins += 1
            win_amounts.append(pnl)
        elif pnl < 0:
            loss_amounts.append(abs(pnl))
        if ct:
            try:
                d = datetime.fromtimestamp(ct).date()
            except (OSError, ValueError, OverflowError):
                d = None
            if d == today:
                t_pnl += pnl; t_gross += gross; t_fees += fees; t_n += 1
                if pnl > 0: t_wins += 1
            elif d == yesterday:
                y_pnl += pnl; y_gross += gross; y_fees += fees; y_n += 1
                if pnl > 0: y_wins += 1

    total_n = len(closed)

    # Profit factor: gross wins / gross losses
    total_wins  = sum(win_amounts) if win_amounts else 0
    total_losses = sum(loss_amounts) if loss_amounts else 0
    pf = total_wins / total_losses if total_losses > 0 else (999.0 if total_wins > 0 else 0.0)

    # Current streak
    streak = 0
    streak_type = ""
    for t in closed:  # already sorted by close_time desc
        p = t.get("pnl", 0) or 0
        if p > 0:
            if streak_type == "" or streak_type == "W":
                streak += 1; streak_type = "W"
            else:
                break
        elif p < 0:
            if streak_type == "" or streak_type == "L":
                streak += 1; streak_type = "L"
            else:
                break
        else:
            break

    return {
        "today_pnl": t_pnl, "today_n": t_n, "today_wins": t_wins,
        "today_wr":  (t_wins / t_n * 100) if t_n else 0,
        "yesterday_pnl":  y_pnl, "yesterday_n": y_n, "yesterday_wins": y_wins,
        "yesterday_wr":   (y_wins / y_n * 100) if y_n else 0,
        "all_pnl":   a_pnl, "all_gross": a_gross, "all_fees": a_fees,
        "total_n":   total_n, "all_wins": a_wins,
        "all_wr":    (a_wins / total_n * 100) if total_n else 0,
        "all_best":  a_best, "all_worst": a_worst,
        "profit_factor": pf,
        "avg_win":   (total_wins / len(win_amounts)) if win_amounts else 0,
        "avg_loss":  (total_losses / len(loss_amounts)) if loss_amounts else 0,
        "streak":    streak, "streak_type": streak_type,
    }


def calc_exchange_stats(closed, open_pos):
    ex_stats = defaultdict(lambda: {
        "pnl": 0.0, "n": 0, "wins": 0, "fees": 0.0, "open": 0,
        "spot_pnl": 0.0, "spot_n": 0, "spot_wins": 0,
        "futures_pnl": 0.0, "futures_n": 0, "futures_wins": 0,
    })
    for t in closed:
        ex  = (t.get("exchange") or "unknown").lower()
        pnl = t.get("pnl", 0) or 0
        fee = t.get("total_fees", 0) or 0
        mtype = t.get("market_type", "spot")
        ex_stats[ex]["pnl"]  += pnl
        ex_stats[ex]["fees"] += fee
        ex_stats[ex]["n"]    += 1
        if pnl > 0: ex_stats[ex]["wins"] += 1
        # Per market type
        key_pnl  = "{}_pnl".format(mtype)
        key_n    = "{}_n".format(mtype)
        key_wins = "{}_wins".format(mtype)
        ex_stats[ex][key_pnl]  = ex_stats[ex].get(key_pnl, 0.0) + pnl
        ex_stats[ex][key_n]    = ex_stats[ex].get(key_n, 0) + 1
        if pnl > 0:
            ex_stats[ex][key_wins] = ex_stats[ex].get(key_wins, 0) + 1
    for p in open_pos:
        ex = (p.get("exchange") or "unknown").lower()
        ex_stats[ex]["open"] += 1
    return ex_stats


def calc_strategy_stats(closed):
    """Breakdown PnL and win rate by strategy."""
    strat_stats = defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0})
    for t in closed:
        raw  = t.get("strategy", "unknown") or "unknown"
        name = raw.split("|")[0].strip()
        pnl  = t.get("pnl", 0) or 0
        strat_stats[name]["pnl"] += pnl
        strat_stats[name]["n"]   += 1
        if pnl > 0:
            strat_stats[name]["wins"] += 1
    return strat_stats


def calc_hourly_heatmap(closed):
    """PnL by hour of day (UTC) — identifies best/worst trading hours."""
    hours = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    for t in closed:
        ct = t.get("close_time", 0)
        if not ct:
            continue
        hour = datetime.fromtimestamp(ct, tz=timezone.utc).hour
        pnl  = t.get("pnl", 0) or 0
        hours[hour]["pnl"] += pnl
        hours[hour]["n"]   += 1
    return hours


def calc_daily_pnl(closed, days=7):
    buckets = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    today   = datetime.now().date()
    for t in closed:
        ct = t.get("close_time", 0)
        if not ct: continue
        day = datetime.fromtimestamp(ct).date()
        if (today - day).days >= days: continue
        pnl = t.get("pnl", 0) or 0
        buckets[day]["pnl"]    += pnl
        buckets[day]["trades"] += 1
        if pnl > 0: buckets[day]["wins"] += 1
    result = []
    for i in range(days - 1, -1, -1):
        day  = today - timedelta(days=i)
        data = buckets.get(day, {"pnl": 0.0, "trades": 0, "wins": 0})
        result.append({"date": day, **data})
    return result


def _bucket_stats(trades):
    """Aggregate stats over an arbitrary slice of closed trades."""
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0, "pnl": 0.0,
                "pf": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "best": 0.0, "worst": 0.0, "fees": 0.0}
    wins = [t.get("pnl", 0) or 0 for t in trades if (t.get("pnl") or 0) > 0]
    losses = [abs(t.get("pnl", 0) or 0) for t in trades if (t.get("pnl") or 0) < 0]
    pnls = [t.get("pnl", 0) or 0 for t in trades]
    wsum = sum(wins); lsum = sum(losses)
    return {
        "n":       len(trades),
        "wins":    len(wins),
        "losses":  len(losses),
        "wr":      (len(wins) / len(trades) * 100) if trades else 0.0,
        "pnl":     sum(pnls),
        "pf":      (wsum / lsum) if lsum > 0 else (999.0 if wsum > 0 else 0.0),
        "avg_win": (wsum / len(wins)) if wins else 0.0,
        "avg_loss":(lsum / len(losses)) if losses else 0.0,
        "best":    max(pnls) if pnls else 0.0,
        "worst":   min(pnls) if pnls else 0.0,
        "fees":    sum(t.get("total_fees", 0) or 0 for t in trades),
    }


def calc_weekly_stats(closed):
    """This-week / last-week buckets + a per-day breakdown for the current week.

    Returns dict with:
      - this_week:     stats for trades closed in the last 7 days
      - last_week:     stats for trades closed 7-14 days ago
      - best_day:      (date, pnl, n) — best day in the current week
      - worst_day:     (date, pnl, n) — worst day in the current week
      - active_days:   number of days in the current week with at least 1 trade
    """
    now_ts = time.time()
    cutoff_this = now_ts - 7 * 86400
    cutoff_prev = now_ts - 14 * 86400

    this_trades = []
    prev_trades = []
    for t in closed:
        ct = t.get("close_time", 0) or 0
        if ct >= cutoff_this:
            this_trades.append(t)
        elif ct >= cutoff_prev:
            prev_trades.append(t)

    daily = calc_daily_pnl(closed, days=7)
    active = [d for d in daily if d["trades"] > 0]
    best_day = max(active, key=lambda d: d["pnl"]) if active else None
    worst_day = min(active, key=lambda d: d["pnl"]) if active else None

    return {
        "this_week":  _bucket_stats(this_trades),
        "last_week":  _bucket_stats(prev_trades),
        "best_day":   best_day,
        "worst_day":  worst_day,
        "active_days": len(active),
    }


def sparkline(values):
    if not values: return ""
    max_abs = max(abs(v) for v in values) or 1
    bars = []
    for v in values:
        if v > 0:
            bars.append(col("▲" * max(1, int(v / max_abs * 4)), GREEN))
        elif v < 0:
            bars.append(col("▼" * max(1, int(abs(v) / max_abs * 4)), RED))
        else:
            bars.append(col("·", DIM))
    return "  ".join(bars)


# ══════════════════════════════════════════════════════════════════════
# Render
# ══════════════════════════════════════════════════════════════════════

def render(open_pos, closed, dry_run, tick, fetcher: LiveFetcher):
    now   = datetime.now().strftime("%Y-%m-%d  %H:%M:%S")
    try:
        from config import OPERATING_MODE as _OP_MODE
    except ImportError:
        _OP_MODE = "PAPER" if dry_run else "CONTROLLED_LIVE"
    _mode_labels = {
        "PAPER": ("PAPER", YELLOW),
        "OBSERVATION": ("OBSERVATION", BLUE),
        "CONTROLLED_LIVE": ("CONTROLLED_LIVE", RED + BOLD),
    }
    mode, mc = _mode_labels.get(_OP_MODE, ("PAPER", YELLOW))
    s     = calc_stats(closed)
    ex_s  = calc_exchange_stats(closed, open_pos)
    news  = load_news()
    W     = DASH_WIDTH

    upnl_map   = calc_unrealized(open_pos, fetcher)
    live_bals  = fetcher.all_balances()
    statuses   = fetcher.all_statuses()
    wallet_bal = fetcher.wallet_balances()
    coin_bals  = fetcher.coin_balances()
    bal_detail = fetcher.balance_detail()
    age_s      = int(fetcher.seconds_since_fetch())
    age_col    = GREEN if age_s < 15 else (YELLOW if age_s < 30 else RED)
    # Only count unrealized PnL from FUTURES positions — spot holdings are
    # already valued in coin_bals, counting them here would double-count.
    _futures_ids = {p.get("id", "") for p in open_pos if p.get("market_type") == "futures"}
    total_upnl = sum(v["upnl"] for pid, v in upnl_map.items()
                     if v.get("upnl") is not None and pid in _futures_ids)
    upnl_count = sum(1 for pid, v in upnl_map.items()
                     if v.get("upnl") is not None and pid in _futures_ids)
    upnl_total_count = len(_futures_ids)
    tm         = fetcher.trading_mode().upper()

    # ── Box-drawing helpers ───────────────────────────────────────────
    B_TL = "┌"; B_TR = "┐"; B_BL = "└"; B_BR = "┘"
    B_H  = "─"; B_V  = "│"; B_LT = "├"; B_RT = "┤"

    def box_top(title=""):
        if title:
            pad = W - 4 - len(title)
            print(col(B_TL + B_H + " ", CYAN) + col(title, BOLD + CYAN) +
                  col(" " + B_H * max(pad, 1) + B_TR, CYAN))
        else:
            print(col(B_TL + B_H * (W - 2) + B_TR, CYAN))

    def box_mid(title=""):
        if title:
            pad = W - 4 - len(title)
            print(col(B_LT + B_H + " ", DIM) + col(title, BOLD + CYAN) +
                  col(" " + B_H * max(pad, 1) + B_RT, DIM))
        else:
            print(col(B_LT + B_H * (W - 2) + B_RT, DIM))

    def box_bot():
        print(col(B_BL + B_H * (W - 2) + B_BR, CYAN))

    def row(text):
        print(col(B_V, DIM) + " " + text)

    # ══════════════════════════════════════════════════════════════════
    #  HEADER
    # ══════════════════════════════════════════════════════════════════
    print(col("╔" + "═" * (W - 2) + "╗", CYAN))
    title_txt = "TRADING BOT  --  LIVE DASHBOARD"
    pad_l = (W - 2 - len(title_txt)) // 2
    print(col("║", CYAN) + " " * pad_l + col(title_txt, BOLD + WHITE) +
          " " * (W - 2 - pad_l - len(title_txt)) + col("║", CYAN))
    print(col("╚" + "═" * (W - 2) + "╝", CYAN))

    # Status bar
    ex_icons = []
    for e in ["binance", "bybit", "bitget"]:
        if e not in statuses:
            continue
        ok = statuses.get(e) == "OK"
        ic = col("●", GREEN) if ok else col("●", RED)
        ex_icons.append("{} {}".format(ic, col(e.upper(), EX_COLOUR.get(e, WHITE))))
    tm_col = GREEN if tm == "ALL" else (YELLOW if tm == "PORTFOLIO" else WHITE)
    print("  {} {} {} {} {} {}  {}".format(
        col(now, DIM), col(B_V, DIM), col(mode, mc), col(B_V, DIM),
        col("Mode:", DIM), col(tm, tm_col),
        "  ".join(ex_icons) if ex_icons else col("No exchanges", DIM)))
    print("  {} {} {} {} {}".format(
        col("Uptime:", DIM), col(_uptime_str(), WHITE),
        col(B_V, DIM),
        col("Data:", DIM), col("{}s ago".format(age_s), age_col)))

    # ── Bot Heartbeat Status ──────────────────────────────────────────
    try:
        hb_path = Path("data/heartbeat.json")
        if hb_path.exists():
            hb = json.loads(hb_path.read_text(encoding="utf-8"))
            hb_uptime = hb.get("uptime_seconds", 0)
            hb_hours = hb_uptime // 3600
            hb_mins = (hb_uptime % 3600) // 60
            hb_cycle = hb.get("cycle_count", 0)
            hb_mem = hb.get("memory_mb", 0)
            hb_halted = hb.get("is_halted", False)
            hb_halt_reason = hb.get("halt_reason") or ""
            hb_ts = hb.get("timestamp", "")
            # Calculate age of heartbeat
            hb_age_s = "?"
            try:
                from datetime import datetime as _dt
                hb_dt = _dt.fromisoformat(hb_ts.replace("Z", "+00:00"))
                hb_age_s = str(int((datetime.now(timezone.utc) - hb_dt).total_seconds()))
            except Exception:
                pass
            # Status indicator
            if hb_halted:
                hb_status = col("HALTED", RED + BOLD)
                hb_reason = "  " + col(hb_halt_reason, YELLOW) if hb_halt_reason else ""
            else:
                hb_status = col("RUNNING", GREEN + BOLD)
                hb_reason = ""
            print("  {} {}  {} {}h {}m  {} {}  {} {:.0f}MB  {} {}s ago{}".format(
                col("Bot:", DIM), hb_status,
                col("Up:", DIM), hb_hours, hb_mins,
                col("Cycle:", DIM), col(str(hb_cycle), WHITE),
                col("Mem:", DIM), hb_mem,
                col("HB:", DIM), hb_age_s,
                hb_reason))
        else:
            print("  {} {}".format(
                col("Bot:", DIM),
                col("No heartbeat -- bot may not be running", YELLOW)))
    except Exception:
        pass
    print()

    # ══════════════════════════════════════════════════════════════════
    #  EXCHANGE BALANCES & PORTFOLIO
    # ══════════════════════════════════════════════════════════════════
    box_top("EXCHANGE BALANCES" + ("  (paper)" if dry_run else ""))
    total_live = 0.0
    start_bal  = 0.0
    _stables = {"USDT", "USD", "BUSD", "USDC"}

    if dry_run and wallet_bal:
        try:
            wf = Path("data/virtual_wallet.json")
            if wf.exists():
                wd = json.loads(wf.read_text(encoding="utf-8"))
                start_bal = wd.get("start", 100.0)
        except Exception:
            start_bal = 100.0
        for ex_name in ["binance", "bybit", "bitget"]:
            if ex_name not in statuses:
                continue
            ec  = EX_COLOUR.get(ex_name, WHITE)
            bal = wallet_bal.get(ex_name, start_bal)
            st  = statuses.get(ex_name, "—")
            diff = bal - start_bal
            total_live += bal
            st_s = col("OK", GREEN) if st == "OK" else col(st[:15], RED)
            row("{} {}  {:>10.2f} USDT  [{}]  {:>+.2f}".format(
                col("●", ec), vljust(col(ex_name.upper(), ec), 8), bal, st_s, diff))
        start_total = start_bal * max(len([e for e in statuses if statuses[e] == "OK"]), 1)
        roi_pct = ((total_live - start_total) / start_total * 100) if start_total > 0 else 0
        rc = GREEN if roi_pct >= 0 else RED
        box_mid()
        row("{}  {:>10.2f} USDT   {}".format(
            col("TOTAL", BOLD + WHITE), total_live,
            col("ROI: {:+.2f}%".format(roi_pct), rc)))
    elif live_bals:
        bal_det = fetcher.balance_detail()
        grand_coins = {}
        grand_usdt_vals = {}
        for ex_name in ["binance", "bybit", "bitget"]:
            if ex_name not in live_bals and ex_name not in statuses:
                continue
            ec   = EX_COLOUR.get(ex_name, WHITE)
            bal  = live_bals.get(ex_name, 0.0)
            st   = statuses.get(ex_name, "—")
            det  = bal_det.get(ex_name, {})
            total_live += bal
            st_s = col("OK", GREEN) if st == "OK" else col(st[:15], RED)
            if det.get("unified"):
                detail_s = col("Unified Account", DIM)
            else:
                s_bal = det.get("spot", 0.0)
                f_bal = det.get("futures", 0.0)
                detail_s = "{}:{:.2f}  {}:{:.2f}".format(
                    col("Spot", CYAN), s_bal, col("Fut", ORANGE), f_bal)
            row("{} {}  {:>10.2f} USDT  [{}]  {}".format(
                col("●", ec), vljust(col(ex_name.upper(), ec), 8), bal, st_s, detail_s))
            # Coin holdings for this exchange
            ex_coins = coin_bals.get(ex_name, {})
            is_unified = det.get("unified", False)
            coin_rows = []
            for asset, info in ex_coins.items():
                amt = info.get("total", 0)
                if amt <= 0:
                    continue
                if asset in _stables:
                    usdt_val = amt
                else:
                    sym = "{}/USDT".format(asset)
                    px = fetcher.get_price(ex_name, sym)
                    usdt_val = amt * px if px > 0 else 0.0
                if usdt_val < 0.01:
                    continue
                coin_rows.append((asset, amt, usdt_val))
                # Unified exchanges (Bybit): totalEquity in total_live already
                # includes all coin values, so skip adding to grand totals to
                # avoid double-counting in Est. Total.
                if not is_unified:
                    grand_coins[asset] = grand_coins.get(asset, 0.0) + amt
                    grand_usdt_vals[asset] = grand_usdt_vals.get(asset, 0.0) + usdt_val
            coin_rows.sort(key=lambda r: (0 if r[0] in _stables else 1, -r[2]))
            if coin_rows:
                parts = []
                for asset, amt, uval in coin_rows[:6]:
                    if asset in _stables:
                        parts.append("{} {:.2f}".format(col(asset, YELLOW), amt))
                    else:
                        parts.append("{} {:.6g} {}".format(
                            col(asset, CYAN), amt,
                            col("~{:.2f}$".format(uval), DIM)))
                row("  " + col("└─", DIM) + " " + "  ".join(parts))
                if len(coin_rows) > 6:
                    extra = len(coin_rows) - 6
                    row("    " + col("+{} more coins".format(extra), DIM))

        box_mid("PORTFOLIO TOTALS")
        row("  {} {:>10.2f} USDT".format(
            col("USDT Balance:", WHITE), total_live))
        if total_upnl != 0 or upnl_count > 0:
            uc = GREEN if total_upnl >= 0 else RED
            cnt_s = " ({} futures)".format(upnl_count) if upnl_count > 0 else ""
            row("  {} {}{}".format(
                col("Unrealized:  ", WHITE),
                col("{:>+10.4f} USDT".format(total_upnl), uc),
                col(cnt_s, DIM)))
        # Aggregated coin holdings (non-stable only)
        # Excludes unified exchange coins (Bybit) — already in USDT Balance
        if grand_coins:
            non_stable = []
            for asset, amt in grand_coins.items():
                if asset in _stables:
                    continue
                uval = grand_usdt_vals.get(asset, 0.0)
                if uval >= 0.01:
                    non_stable.append((asset, amt, uval))
            non_stable.sort(key=lambda r: -r[2])
            non_stable_val = sum(r[2] for r in non_stable)
            if non_stable:
                hld = "  ".join("{} {:.6g}".format(col(r[0], CYAN), r[1])
                               for r in non_stable[:10])
                row("  {} {} {}".format(
                    col("Coins:       ", WHITE), hld,
                    col("~{:.0f}$".format(non_stable_val), DIM)))
            # Est. Total = USDT balances + non-stable coin value + futures unrealized
            total_est = total_live + non_stable_val + total_upnl
            row("  {} {:>10.2f} USDT".format(
                col("Est. Total:  ", BOLD + WHITE), total_est))
        else:
            row("  {} {:>10.2f} USDT".format(
                col("Est. Total:  ", BOLD + WHITE), total_live + total_upnl))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  PERFORMANCE
    # ══════════════════════════════════════════════════════════════════
    box_top("PERFORMANCE")
    tl = s["today_n"]     - s["today_wins"]
    yl = s["yesterday_n"] - s["yesterday_wins"]
    al = s["total_n"]     - s["all_wins"]

    sk = s["streak"]; stype = s["streak_type"]
    streak_s = col("{}{}".format(sk, stype), GREEN if stype == "W" else RED) if sk > 0 else col("--", DIM)
    pf = s["profit_factor"]
    pf_c = GREEN if pf >= 1.5 else (YELLOW if pf >= 1.0 else RED)
    pf_s = col("{:.2f}".format(min(pf, 99.99)), pf_c)

    row("  {}  {}  trades:{}  W:{} L:{}  WR:{}".format(
        vljust(col("Today", WHITE), 11), pnl_str(s["today_pnl"]),
        s["today_n"], col(s["today_wins"], GREEN), col(tl, RED),
        col("{:.1f}%".format(s["today_wr"]), wr_col(s["today_wr"]))))
    if s["yesterday_n"]:
        row("  {}  {}  trades:{}  W:{} L:{}  WR:{}".format(
            vljust(col("Yesterday", WHITE), 11), pnl_str(s["yesterday_pnl"]),
            s["yesterday_n"], col(s["yesterday_wins"], GREEN), col(yl, RED),
            col("{:.1f}%".format(s["yesterday_wr"]), wr_col(s["yesterday_wr"]))))
    else:
        row("  {}  {}".format(vljust(col("Yesterday", WHITE), 11),
                              col("no closed trades", DIM)))
    row("  {}  {}  trades:{}  W:{} L:{}  WR:{}".format(
        vljust(col("All Time", WHITE), 11), pnl_str(s["all_pnl"]),
        s["total_n"], col(s["all_wins"], GREEN), col(al, RED),
        col("{:.1f}%".format(s["all_wr"]), wr_col(s["all_wr"]))))
    row("  Avg Win: {}  Avg Loss: {}  PF: {}  Streak: {}".format(
        pnl_str_short(s["avg_win"]),
        pnl_str_short(-s["avg_loss"]) if s["avg_loss"] else col("--", DIM),
        pf_s, streak_s))
    if s["all_best"] != 0 or s["all_worst"] != 0:
        row("  Best: {}  Worst: {}  Fees: {}".format(
            pnl_str_short(s["all_best"]), pnl_str_short(s["all_worst"]),
            col("-{:.4f}".format(s["all_fees"]), RED)))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  TRADING GATES — hour gate + whitelist/blacklist + BTC macro + tiers
    # ══════════════════════════════════════════════════════════════════
    try:
        from config import (
            WHITELIST_SYMBOLS, BLACKLIST_HARD, LEVERAGE_TIERS,
            MAX_LOSS_PER_TRADE_PCT, SHORTS_REQUIRE_BTC_BEAR,
            BTC_TREND_TIMEFRAME,
        )
        cur_utc = datetime.now(timezone.utc)
        cur_hour = cur_utc.hour
        hc_label, hc_color = _hour_class(cur_hour)

        box_top("TRADING GATES")
        # Line 1: UTC hour status
        row("  {} {}  hour {:02d} → {}   Loss clamp: {}".format(
            col("UTC:", DIM),
            col(cur_utc.strftime("%H:%M"), WHITE),
            cur_hour,
            col(hc_label, hc_color),
            col("{:.1f}%".format(MAX_LOSS_PER_TRADE_PCT * 100),
                YELLOW)))

        # Line 2: static whitelist / blacklist sizes
        row("  {} {} symbols   {} {} symbols".format(
            col("Whitelist:", DIM),
            col(str(len(WHITELIST_SYMBOLS)), GREEN),
            col("Blacklist:", DIM),
            col(str(len(BLACKLIST_HARD)), RED)))

        # Line 3: leverage tiers
        tier_parts = []
        for tname, tcfg in LEVERAGE_TIERS.items():
            tier_parts.append("{}: {}x/{:.1f}%SL/conf{:.0f}%".format(
                col(tname[:4], CYAN),
                tcfg.get("leverage", 1),
                tcfg.get("sl_pct", 0) * 100,
                tcfg.get("min_confidence", 0) * 100))
        row("  Tiers: " + "  ".join(tier_parts))

        # Line 4: BTC macro + shorts gate
        btc_trend = "?"
        btc_col = DIM
        try:
            # Try to read cached BTC trend if the engine has persisted it
            btc_state = _file_cache.load("data/btc_trend.json") or {}
            btc_slope = btc_state.get("ema200_slope", 0)
            if btc_slope > 0:
                btc_trend = "BULL"
                btc_col = GREEN
            elif btc_slope < 0:
                btc_trend = "BEAR"
                btc_col = RED
            else:
                btc_trend = "FLAT"
                btc_col = YELLOW
        except Exception:
            pass
        shorts_ok = (not SHORTS_REQUIRE_BTC_BEAR) or (btc_trend == "BEAR")
        shorts_s = col("ALLOWED", GREEN) if shorts_ok else col("BLOCKED (needs BTC bear)", RED)
        row("  BTC {}: {}   Shorts: {}".format(
            BTC_TREND_TIMEFRAME, col(btc_trend, btc_col), shorts_s))
        box_bot()
        print()
    except Exception as _e:
        # Config changed or not importable — skip silently
        pass

    # ══════════════════════════════════════════════════════════════════
    #  AUTO-MUTATIONS — closed-loop post-mortem learner state
    # ══════════════════════════════════════════════════════════════════
    amut = load_auto_mut()
    if amut:
        box_top("AUTO-MUTATIONS  (post-mortem learner)")
        # Dynamic blacklist
        dbl = amut.get("blacklist", {})
        now_ts = time.time()
        active_bl = [(sym, float(expires) - now_ts)
                     for sym, expires in dbl.items()
                     if float(expires) > now_ts]
        if active_bl:
            active_bl.sort(key=lambda x: -x[1])
            parts = ["{} ({:.0f}h)".format(
                col(sym.split("/")[0], RED), secs / 3600)
                for sym, secs in active_bl[:6]]
            row("  {} {}".format(
                col("Dyn-blacklist:", DIM), "  ".join(parts)))
            if len(active_bl) > 6:
                row("  " + col("  +{} more".format(len(active_bl) - 6), DIM))
        else:
            row("  {} {}".format(
                col("Dyn-blacklist:", DIM), col("(empty)", DIM)))

        # Leverage cap
        lev_cap = amut.get("leverage_cap")
        lev_until = float(amut.get("leverage_cap_until", 0) or 0)
        if lev_cap and lev_until > now_ts:
            hours_left = (lev_until - now_ts) / 3600
            row("  {} {}x for {:.1f}h".format(
                col("Leverage cap:", DIM),
                col(str(int(lev_cap)), YELLOW),
                hours_left))
        else:
            row("  {} {}".format(
                col("Leverage cap:", DIM), col("(none)", DIM)))

        # Shorts block
        sb_until = float(amut.get("shorts_blocked_until", 0) or 0)
        if sb_until > now_ts:
            hours_left = (sb_until - now_ts) / 3600
            row("  {} {} for {:.1f}h".format(
                col("Shorts:", DIM), col("BLOCKED", RED), hours_left))
        else:
            row("  {} {}".format(
                col("Shorts:", DIM), col("allowed", GREEN)))

        # Last scan
        last_scan = float(amut.get("last_scan_at", 0) or 0)
        if last_scan > 0:
            mins_ago = (now_ts - last_scan) / 60
            tail = amut.get("last_scan_loss_tail", 0)
            age_c = GREEN if mins_ago < 10 else (YELLOW if mins_ago < 30 else DIM)
            row("  {} {}m ago   lookback={} losses".format(
                col("Last scan:", DIM),
                col("{:.0f}".format(mins_ago), age_c),
                tail))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  WHY NO TRADES — block-reason telemetry from today's log tail
    # ══════════════════════════════════════════════════════════════════
    try:
        br = load_block_reasons()
        if br.get("total", 0) > 0 or br.get("claude_empty", 0) > 0:
            box_top("WHY NO TRADES  (last ~800 log lines)")
            parts = []
            for k, label, c in [
                ("BLACKLIST", "static-BL",  RED),
                ("DYN_BL",    "dyn-BL",     RED),
                ("UNIVERSE",  "universe",   YELLOW),
                ("RISK",      "risk-mgr",   YELLOW),
                ("HOUR",      "hour-gate",  YELLOW),
                ("TIER",      "tier",       CYAN),
                ("RR",        "r:r",        CYAN),
                ("SHORTS",    "shorts",     CYAN),
                ("LEV",       "lev-cap",    CYAN),
                ("OTHER",     "other",      DIM),
            ]:
                v = br.get(k, 0)
                if v:
                    parts.append("{}:{}".format(col(label, c), v))
            if parts:
                row("  Blocks: " + "  ".join(parts) +
                    "  " + col("total={}".format(br["total"]), BOLD + WHITE))
            else:
                row("  " + col("No blocks in recent tail", GREEN))
            ce = br.get("claude_empty", 0)
            az = br.get("algo_zero", 0)
            ap = br.get("actions", 0)
            row("  {} {}   {} {}   {} {}".format(
                col("Claude empty:", DIM),
                col(str(ce), RED if ce > 10 else YELLOW if ce else GREEN),
                col("Algo 0-action:", DIM),
                col(str(az), RED if az > 10 else YELLOW if az else GREEN),
                col("Algo-proposed:", DIM),
                col(str(ap), GREEN if ap else DIM)))
            box_bot()
            print()
    except Exception:
        pass

    # ══════════════════════════════════════════════════════════════════
    #  OPEN POSITIONS
    # ══════════════════════════════════════════════════════════════════
    n_fut = sum(1 for p in open_pos if p.get("market_type") == "futures")
    n_spt = len(open_pos) - n_fut
    box_top("OPEN POSITIONS  [Spot:{} Futures:{}  Total:{}]".format(n_spt, n_fut, len(open_pos)))
    if open_pos:
        by_ex = defaultdict(list)
        for p in open_pos:
            by_ex[(p.get("exchange") or "?").lower()].append(p)
        first_ex = True
        for ex_name, pos_list in sorted(by_ex.items()):
            if not first_ex:
                box_mid(ex_name.upper())
            else:
                first_ex = False
            ec = EX_COLOUR.get(ex_name, WHITE)
            row("{} {} {} positions".format(
                col("●", ec), col(ex_name.upper(), BOLD + ec), len(pos_list)))
            for pos in pos_list:
                pid    = pos.get("id", "")
                side   = (pos.get("side") or "?").upper()
                sym    = pos.get("symbol", "?")
                entry  = pos.get("entry_price", 0)
                sz     = float(pos.get("size", 0) or 0)
                sl     = pos.get("stop_loss", 0)
                tp     = pos.get("take_profit", 0)
                raw_s  = pos.get("strategy", "?") or "?"
                strat  = raw_s.split("|")[0]
                prof   = raw_s.split("|")[1] if "|" in raw_s else ""
                mtype  = pos.get("market_type", "spot")
                lev    = pos.get("leverage", 1)
                dur    = int((time.time() - pos.get("open_time", time.time())) / 60)
                paper  = pos.get("paper_trade", True)
                ud     = upnl_map.get(pid, {})
                live_px  = ud.get("price", 0.0)
                upnl     = ud.get("upnl")
                upnl_pct = ud.get("upnl_pct")
                move     = ud.get("move_pct", 0.0)
                sc     = GREEN if side == "BUY" else RED
                tag    = col("P", PURPLE) if paper else col("L", GREEN + BOLD)
                lev_s  = col(" {}x".format(lev), YELLOW) if lev > 1 else ""
                prof_s = col(" {}".format(prof[:4].upper()), YELLOW) if prof else ""
                at     = _asset_tag(sym)
                mt_tag = col("SPOT", CYAN) if mtype == "spot" else col("FUT ", ORANGE)
                if live_px > 0:
                    px_col = GREEN if move >= 0 else RED
                    live_s = col("{:.6g}".format(live_px), px_col)
                    move_s = col("{:+.2f}%".format(move), px_col)
                else:
                    live_s = col("--", DIM); move_s = col("--", DIM)
                if upnl is not None:
                    uc     = GREEN if upnl >= 0 else RED
                    upnl_s = col("{:+.4f}({:+.1f}%)".format(upnl, upnl_pct or 0), uc)
                else:
                    upnl_s = col("--", DIM)
                # Line 1: tag, side, market, symbol
                row("  [{}] {} {} {}{}{}{}".format(
                    tag, col(side, sc), mt_tag, col(sym, WHITE), lev_s, prof_s, at))
                # Line 2: entry, live, move, upnl, duration
                price_lbl = "Buy" if side == "BUY" else "Sell"
                if entry and entry > 0:
                    row("      {} @{:.6g}  Now:{}  {}  uPnL:{}  {}m".format(
                        price_lbl, entry, live_s, move_s, upnl_s, dur))
                else:
                    # Holdings with unknown entry price — show value instead
                    val = ud.get("value", sz * live_px if live_px else 0)
                    val_s = col("${:.2f}".format(val), WHITE) if val else col("--", DIM)
                    row("      Now:{}  Value:{}  Qty:{:.6g}  {}m".format(
                        live_s, val_s, sz, dur))
                # Line 3: SL/TP or value
                if sl and tp:
                    if entry > 0:
                        sl_pct = abs(sl - entry) / entry * 100
                        tp_pct = abs(tp - entry) / entry * 100
                        row("      SL:{:.6g}({:.1f}%)  TP:{:.6g}({:.1f}%)  [{}]".format(
                            sl, sl_pct, tp, tp_pct, col(strat, DIM)))
                    else:
                        row("      SL:{:.6g}  TP:{:.6g}  [{}]".format(
                            sl, tp, col(strat, DIM)))
                elif mtype == "spot" and sz > 0 and live_px > 0:
                    value = sz * live_px
                    row("      Qty:{:.6g}  Value:{:.2f} USDT  [{}]".format(
                        sz, value, col(strat, DIM)))
    else:
        row("  " + col("No open positions", DIM))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  EXCHANGE BREAKDOWN
    # ══════════════════════════════════════════════════════════════════
    if ex_s:
        box_top("EXCHANGE BREAKDOWN  (Spot vs Futures)")
        total_spot_pnl = 0.0
        total_futures_pnl = 0.0
        for ex_name in ["binance", "bybit", "bitget"]:
            if ex_name not in ex_s:
                continue
            d    = ex_s[ex_name]
            ec   = EX_COLOUR.get(ex_name, WHITE)
            pnl  = d["pnl"]; n = d["n"]; wins = d["wins"]
            open_n = d["open"]
            wr   = (wins / n * 100) if n > 0 else 0
            live_b = live_bals.get(ex_name)
            bal_s  = col("bal:{:.2f}".format(live_b), DIM) if live_b else ""
            row("{} {} trades:{:>3}  WR:{}  PnL:{}  open:{}  {}".format(
                col("●", ec), vljust(col(ex_name.upper(), ec), 8),
                n, col("{:.1f}%".format(wr), wr_col(wr)),
                col("{:+.4f}".format(pnl), GREEN if pnl >= 0 else RED),
                col(str(open_n), CYAN if open_n > 0 else DIM), bal_s))
            sp = d.get("spot_pnl", 0.0); sn = d.get("spot_n", 0)
            fp = d.get("futures_pnl", 0.0); fn = d.get("futures_n", 0)
            total_spot_pnl += sp
            total_futures_pnl += fp
            if sn > 0 or fn > 0:
                parts = []
                if sn > 0:
                    sw = d.get("spot_wins", 0)
                    swr = (sw / sn * 100) if sn > 0 else 0
                    parts.append("{}: {} ({} trd, {:.0f}%WR)".format(
                        col("SPOT", CYAN),
                        col("{:+.4f}".format(sp), GREEN if sp >= 0 else RED), sn, swr))
                if fn > 0:
                    fw = d.get("futures_wins", 0)
                    fwr = (fw / fn * 100) if fn > 0 else 0
                    parts.append("{}: {} ({} trd, {:.0f}%WR)".format(
                        col("FUT", ORANGE),
                        col("{:+.4f}".format(fp), GREEN if fp >= 0 else RED), fn, fwr))
                row("  " + col("└─", DIM) + " " + ("  " + col(B_V, DIM) + "  ").join(parts))
        if total_spot_pnl != 0 or total_futures_pnl != 0:
            box_mid("TOTAL PROFIT")
            row("  {} {}    {} {}".format(
                col("SPOT:", CYAN),
                col("{:+.4f} USDT".format(total_spot_pnl),
                    GREEN if total_spot_pnl >= 0 else RED),
                col("FUTURES:", ORANGE),
                col("{:+.4f} USDT".format(total_futures_pnl),
                    GREEN if total_futures_pnl >= 0 else RED)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  STRATEGY BREAKDOWN
    # ══════════════════════════════════════════════════════════════════
    strat_s = calc_strategy_stats(closed)
    if strat_s:
        box_top("STRATEGY BREAKDOWN")
        row("  {} {}  {}  {}".format(
            vljust(col("Strategy", DIM), 20),
            vrjust(col("Trades", DIM), 6),
            vrjust(col("WR", DIM), 7),
            vrjust(col("PnL", DIM), 12)))
        row("  " + col("─" * 52, DIM))
        sorted_strats = sorted(strat_s.items(), key=lambda x: x[1]["pnl"], reverse=True)
        for sname, sd in sorted_strats:
            wr = (sd["wins"] / sd["n"] * 100) if sd["n"] > 0 else 0
            pc = GREEN if sd["pnl"] >= 0 else RED
            wc = wr_col(wr)
            star = col(" ★", GOLD) if sd == sorted_strats[0][1] and sd["pnl"] > 0 else ""
            row("  {:<20} {:>6}  {}  {}{}".format(
                sname[:20], sd["n"],
                vrjust(col("{:.1f}%".format(wr), wc), 7),
                vrjust(col("{:+.4f}".format(sd["pnl"]), pc), 12),
                star))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  DAILY PnL
    # ══════════════════════════════════════════════════════════════════
    daily    = calc_daily_pnl(closed, days=7)
    has_data = any(d["trades"] > 0 for d in daily)
    if has_data:
        box_top("DAILY PnL  (7 days)")
        row("  " + sparkline([d["pnl"] for d in daily]))
        row("  {} {}  {}  {}  {}".format(
            vljust(col("Date", DIM), 12),
            vrjust(col("PnL", DIM), 9),
            vrjust(col("Trd", DIM), 5),
            vrjust(col("WR%", DIM), 6), ""))
        row("  " + col("─" * 50, DIM))
        max_abs = max(abs(v["pnl"]) for v in daily if v["trades"] > 0) or 1
        for d in daily:
            ds   = d["date"].strftime("%a %d %b")
            pnl  = d["pnl"]; n = d["trades"]; wins = d["wins"]
            wr   = (wins / n * 100) if n > 0 else 0
            pc   = GREEN if pnl > 0 else (RED if pnl < 0 else DIM)
            sign = "+" if pnl >= 0 else ""
            bar_l= int(abs(pnl) / max_abs * 14) if n > 0 else 0
            bar  = col("█" * bar_l, GREEN if pnl > 0 else RED) if bar_l else col("·", DIM)
            if n == 0:
                row("  {:<12} {}  {}  {}  {}".format(
                    ds,
                    vrjust(col("--", DIM), 9),
                    vrjust(col("0", DIM), 5),
                    vrjust(col("--", DIM), 6),
                    col("no trades", DIM)))
            else:
                row("  {:<12} {}  {}  {}  {}".format(
                    ds,
                    vrjust(col("{}{:.4f}".format(sign, pnl), pc), 9),
                    vrjust(col(str(n), WHITE), 5),
                    vrjust("{:.1f}%".format(wr), 6),
                    bar))
        wk_pnl = sum(d["pnl"] for d in daily); wk_t = sum(d["trades"] for d in daily)
        wk_w   = sum(d["wins"] for d in daily); wk_wr = (wk_w / wk_t * 100) if wk_t else 0
        wc     = GREEN if wk_pnl >= 0 else RED
        row("  " + col("─" * 50, DIM))
        row("  {}  {}  {}  {}".format(
            vljust(col("WEEK TOTAL", BOLD + WHITE), 12),
            vrjust(col("{}{:.4f}".format("+" if wk_pnl >= 0 else "", wk_pnl), wc), 9),
            vrjust(col(str(wk_t), WHITE), 5),
            vrjust("{:.1f}%".format(wk_wr), 6)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  WEEKLY PERFORMANCE  (rolling 7d vs prior 7d)
    # ══════════════════════════════════════════════════════════════════
    wk = calc_weekly_stats(closed)
    tw, lw = wk["this_week"], wk["last_week"]
    if tw["n"] > 0 or lw["n"] > 0:
        box_top("WEEKLY PERFORMANCE")
        # Column widths: label=11, trd=5, W:L=7, WR%=7, PnL=10, PF=6, AvgW=8, AvgL=8, Best=8, Worst=8
        row("  {} {} {} {} {} {} {} {} {} {}".format(
            vljust(col("Period", DIM), 11),
            vrjust(col("Trd",   DIM),  5),
            vrjust(col("W:L",   DIM),  7),
            vrjust(col("WR%",   DIM),  7),
            vrjust(col("PnL",   DIM), 10),
            vrjust(col("PF",    DIM),  6),
            vrjust(col("AvgW",  DIM),  8),
            vrjust(col("AvgL",  DIM),  8),
            vrjust(col("Best",  DIM),  8),
            vrjust(col("Worst", DIM),  8)))
        row("  " + col("─" * (DASH_WIDTH - 4), DIM))

        def _row(label, b, label_col=BOLD + WHITE):
            if b["n"] == 0:
                row("  {} {} {} {} {} {} {} {} {} {}".format(
                    vljust(col(label, label_col), 11),
                    vrjust(col("0",  DIM),  5),
                    vrjust(col("--", DIM),  7),
                    vrjust(col("--", DIM),  7),
                    vrjust(col("--", DIM), 10),
                    vrjust(col("--", DIM),  6),
                    vrjust(col("--", DIM),  8),
                    vrjust(col("--", DIM),  8),
                    vrjust(col("--", DIM),  8),
                    vrjust(col("--", DIM),  8)))
                return
            pc  = GREEN if b["pnl"] >= 0 else RED
            pfc = GREEN if b["pf"] >= 1.0 else RED
            wrc = wr_col(b["wr"])
            row("  {} {} {} {} {} {} {} {} {} {}".format(
                vljust(col(label, label_col), 11),
                vrjust(col(str(b["n"]), WHITE), 5),
                vrjust(col("{}:{}".format(b["wins"], b["losses"]), WHITE), 7),
                vrjust(col("{:.1f}%".format(b["wr"]), wrc), 7),
                vrjust(col("{}{:.4f}".format("+" if b["pnl"] >= 0 else "", b["pnl"]), pc), 10),
                vrjust(col("{:.2f}".format(b["pf"]) if b["pf"] < 999 else "∞", pfc), 6),
                vrjust(col("{:.3f}".format(b["avg_win"]),  GREEN), 8),
                vrjust(col("{:.3f}".format(b["avg_loss"]), RED),   8),
                vrjust(col("{:+.3f}".format(b["best"]),    GREEN), 8),
                vrjust(col("{:+.3f}".format(b["worst"]),   RED),   8)))

        _row("This week", tw, BOLD + WHITE)
        _row("Last week", lw, DIM)

        # Δ comparison row
        if lw["n"] > 0 and tw["n"] > 0:
            d_n   = tw["n"]   - lw["n"]
            d_pnl = tw["pnl"] - lw["pnl"]
            d_wr  = tw["wr"]  - lw["wr"]
            d_pf  = tw["pf"]  - lw["pf"] if (lw["pf"] < 999 and tw["pf"] < 999) else 0.0
            row("  " + col("─" * (DASH_WIDTH - 4), DIM))
            row("  {} {} {} {} {} {}".format(
                vljust(col("Δ vs prev", BOLD + CYAN), 11),
                vrjust(col("{:+d}".format(d_n), GREEN if d_n >= 0 else RED), 5),
                " " * 7,
                vrjust(col("{:+.1f}".format(d_wr), GREEN if d_wr >= 0 else RED), 7),
                vrjust(col("{:+.4f}".format(d_pnl), GREEN if d_pnl >= 0 else RED), 10),
                vrjust(col("{:+.2f}".format(d_pf) if d_pf else "--",
                           GREEN if d_pf >= 0 else RED), 6)))

        # Best / worst day this week
        if wk["best_day"] or wk["worst_day"]:
            row("  " + col("─" * (DASH_WIDTH - 4), DIM))
            if wk["best_day"]:
                bd = wk["best_day"]
                row("  {} {} on {} ({} trades)".format(
                    col("Best day :", DIM),
                    col("{:+.4f} USDT".format(bd["pnl"]), GREEN),
                    col(bd["date"].strftime("%a %d %b"), WHITE),
                    bd["trades"]))
            if wk["worst_day"]:
                wd = wk["worst_day"]
                row("  {} {} on {} ({} trades)".format(
                    col("Worst day:", DIM),
                    col("{:+.4f} USDT".format(wd["pnl"]), RED),
                    col(wd["date"].strftime("%a %d %b"), WHITE),
                    wd["trades"]))
            row("  {} {} / 7".format(
                col("Active   :", DIM),
                col(str(wk["active_days"]), WHITE)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  HOURLY HEATMAP
    # ══════════════════════════════════════════════════════════════════
    hourly = calc_hourly_heatmap(closed)
    if hourly:
        box_top("HOURLY HEATMAP  (UTC)")
        sorted_hours = sorted(hourly.items(), key=lambda x: x[1]["pnl"])
        worst_3 = [(h, d) for h, d in sorted_hours[:3] if d["pnl"] < 0]
        best_3  = [(h, d) for h, d in sorted_hours[-3:] if d["pnl"] > 0][::-1]
        if best_3:
            parts = ["{}h {:+.4f}({})".format(h, d["pnl"], d["n"]) for h, d in best_3]
            row("  Best  : " + col("  ".join(parts), GREEN))
        if worst_3:
            parts = ["{}h {:+.4f}({})".format(h, d["pnl"], d["n"]) for h, d in worst_3]
            row("  Worst : " + col("  ".join(parts), RED))
        vals = [hourly.get(h, {"pnl": 0})["pnl"] for h in range(24)]
        cells = []
        for h in range(24):
            v = vals[h]
            if v > 0:
                cells.append(col("█", GREEN))
            elif v < 0:
                cells.append(col("█", RED))
            else:
                cells.append(col("·", DIM))
        row("  " + col("0", DIM) + "".join(cells) + col("23", DIM))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  RECENT TRADES & MARKET INFO (side by side conceptually)
    # ══════════════════════════════════════════════════════════════════
    box_top("RECENT TRADES  (last 8)")
    recent = sorted(closed, key=lambda x: x.get("close_time", 0), reverse=True)[:8]
    if recent:
        for t in recent:
            sym    = t.get("symbol", "?"); pnl = t.get("pnl", 0) or 0
            side   = (t.get("side") or "?").upper()
            raw_s  = t.get("strategy", "?") or "?"
            strat  = raw_s.split("|")[0][:14]
            prof   = raw_s.split("|")[1] if "|" in raw_s else ""
            reason = t.get("close_reason", "?"); paper = t.get("paper_trade", True)
            ct     = t.get("close_time"); ex_n = (t.get("exchange") or "").lower()
            ts     = datetime.fromtimestamp(ct).strftime("%m/%d %H:%M") if ct else "--:--"
            sc     = GREEN if side == "BUY" else RED
            tag    = col("P", PURPLE) if paper else col("L", GREEN + BOLD)
            prof_s = col("[{}]".format(prof[:4].upper()), YELLOW) if prof else ""
            ec     = col(ex_n[:3].upper(), EX_COLOUR.get(ex_n, DIM))
            at     = _asset_tag(sym)
            row("  {} [{}] {} {} {} {:<12}{} {}  ({})".format(
                col(ts, DIM), tag, ec, prof_s, col(side, sc), sym, at,
                pnl_str_short(pnl), col(reason, DIM)))
    else:
        row("  " + col("No closed trades yet", DIM))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  MARKET INTELLIGENCE  (Fear&Greed, trending, news headlines)
    # ══════════════════════════════════════════════════════════════════
    # 2026-04-26: ARBITRAGE block removed (engine vestigial per CLAUDE.md,
    # opportunities.json file 28d stale). CLAUDE AI sub-block removed
    # (claude_analysis.json 23d stale; live decisions now route through
    # MCP-Brain, not the legacy analysis file).
    if news:
        box_top("MARKET INTELLIGENCE")
        fg   = news.get("fear_greed", {})
        glb  = news.get("global", {})
        chg  = glb.get("market_cap_change_24h", 0)
        row("  Fear & Greed: {}   MCap 24h: {}".format(
            fg_str(fg.get("value", 50)),
            col("{:+.2f}%".format(chg), GREEN if chg >= 0 else RED)))
        trend = news.get("trending", [])
        if trend:
            row("  Trending: " + "  ".join(
                col(c.get("symbol", "?"), YELLOW) for c in trend[:5]))
        for a in news.get("news", [])[:2]:
            sent = a.get("sentiment", 0)
            icon = col("▲", GREEN) if sent > 0 else (col("▼", RED) if sent < 0 else col("·", DIM))
            row("  {} {}  {}".format(icon, a.get("title", "")[:60],
                                     col(a.get("source", "")[:12], DIM)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  MCP BRAIN STATUS
    # ══════════════════════════════════════════════════════════════════
    mcp_state = _file_cache.load("data/mcp_state.json")
    mcp_acc   = _file_cache.load("data/mcp_accuracy.json")
    kelly_st  = _file_cache.load("data/kelly_stats.json")
    has_mcp   = mcp_state or mcp_acc or kelly_st
    if has_mcp:
        box_top("MCP BRAIN  &  KELLY CRITERION")
        # MCP Brain decisions summary
        if mcp_state:
            raw_decs = mcp_state.get("decisions", {})
            # Normalize: can be dict {coin: {...}} or list [{...}, ...]
            if isinstance(raw_decs, dict):
                decs = raw_decs
            elif isinstance(raw_decs, list):
                decs = {}
                for item in raw_decs:
                    if isinstance(item, dict):
                        coin = item.get("coin") or item.get("symbol") or "?"
                        decs[coin] = item
            else:
                decs = {}
            saved_at = mcp_state.get("saved_at", 0)
            age_min = (time.time() - saved_at) / 60 if saved_at else 999
            buys  = sum(1 for d in decs.values() if isinstance(d, dict) and d.get("action") == "BUY")
            sells = sum(1 for d in decs.values() if isinstance(d, dict) and d.get("action") == "SELL")
            holds = sum(1 for d in decs.values() if isinstance(d, dict) and d.get("action") == "HOLD")
            age_c = GREEN if age_min < 5 else (YELLOW if age_min < 15 else RED)
            row("  {} {} BUY  {} SELL  {} HOLD  {}".format(
                col("MCP Brain:", BOLD + PURPLE),
                col(str(buys), GREEN), col(str(sells), RED), col(str(holds), DIM),
                col("{:.0f}m ago".format(age_min), age_c)))
            # Show top BUY/SELL signals
            active = [(c, d) for c, d in decs.items()
                      if isinstance(d, dict) and d.get("action") in ("BUY", "SELL")]
            active.sort(key=lambda x: x[1].get("confidence", 0), reverse=True)
            if active:
                parts = []
                for coin, d in active[:6]:
                    ac = GREEN if d["action"] == "BUY" else RED
                    conf = d.get("confidence", 0)
                    parts.append("{} {} {:.0f}%".format(
                        col(coin, WHITE), col(d["action"], ac), conf * 100))
                row("  " + col("└─", DIM) + " " + "  ".join(parts))
        # MCP Accuracy
        if mcp_acc:
            # mcp_accuracy.json is a list of trade records — compute stats
            if isinstance(mcp_acc, list):
                resolved = [r for r in mcp_acc
                            if isinstance(r, dict) and r.get("resolved")
                            and r.get("outcome") != "expired"]
                recent   = resolved[-50:]
                total_a  = len(recent)
                wins_a   = sum(1 for r in recent if r.get("outcome") == "win")
                losses_a = sum(1 for r in recent if r.get("outcome") == "loss")
                flat_a   = sum(1 for r in recent if r.get("outcome") == "flat")
            elif isinstance(mcp_acc, dict):
                total_a  = mcp_acc.get("total", 0)
                wins_a   = mcp_acc.get("wins", mcp_acc.get("correct", 0))
                losses_a = mcp_acc.get("losses", 0)
                flat_a   = mcp_acc.get("flat", 0)
            else:
                total_a = wins_a = losses_a = flat_a = 0
            wr_a = (wins_a / total_a * 100) if total_a > 0 else 0
            ac = GREEN if wr_a >= 55 else (YELLOW if wr_a >= 45 else RED)
            row("  {} {}W/{}L/{}F ({})  WR:{}".format(
                col("MCP Accuracy:", WHITE), wins_a, losses_a, flat_a, total_a,
                col("{:.1f}%".format(wr_a), ac)))
        # Kelly criterion stats
        if kelly_st:
            box_mid("KELLY STATS")
            row("  {} {}  {}  {}  {}".format(
                vljust(col("Strategy", DIM), 18),
                vrjust(col("Trades", DIM), 6),
                vrjust(col("WR%",    DIM), 6),
                vrjust(col("R-Mult", DIM), 8),
                vrjust(col("Kelly%", DIM), 10)))
            row("  " + col("─" * 56, DIM))
            for strat_name, stats in sorted(kelly_st.items(),
                    key=lambda x: x[1].get("wins", 0) + x[1].get("losses", 0), reverse=True):
                if not isinstance(stats, dict):
                    continue
                w = stats.get("wins", 0); l = stats.get("losses", 0)
                total_k = w + l
                if total_k == 0:
                    continue
                wr_k = w / total_k * 100
                tw = stats.get("total_win", 0); tl = stats.get("total_loss", 0)
                avg_w = tw / max(w, 1); avg_l = tl / max(l, 1)
                r_mult = avg_w / avg_l if avg_l > 0 else 0
                p = w / total_k; q = 1 - p
                kelly_f = ((p * r_mult - q) / r_mult * 100) if r_mult > 0 else 0
                kc = GREEN if kelly_f > 0 else RED
                wrc = GREEN if wr_k >= 55 else (YELLOW if wr_k >= 45 else RED)
                row("  {:<18} {:>6}  {}  {:>8.2f}  {}".format(
                    strat_name[:18], total_k,
                    vrjust(col("{:.1f}%".format(wr_k), wrc), 6),
                    r_mult,
                    vrjust(col("{:+.1f}%".format(kelly_f), kc), 10)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  MARKET REGIME
    # ══════════════════════════════════════════════════════════════════
    regime_data = fetcher.regime_data()
    if regime_data:
        REGIME_ICON = {
            "trending_up":   (GREEN,  "TREND UP "),
            "trending_down": (RED,    "TREND DN "),
            "ranging":       (YELLOW, "RANGING  "),
            "volatile":      (RED + BOLD, "VOLATILE "),
            "unknown":       (DIM,    "UNKNOWN  "),
        }
        box_top("MARKET REGIME  (ADX + Hurst + Volatility)")
        row("  {} {} {}  {}  {}  {}".format(
            vljust(col("Symbol",     DIM), 12),
            vljust(col("Regime",     DIM), 14),
            vrjust(col("ADX",        DIM),  5),
            vrjust(col("Hurst",      DIM),  6),
            vrjust(col("Vol",        DIM),  9),
            col("Strategies", DIM)))
        row("  " + col("─" * 66, DIM))
        for sym in sorted(regime_data.keys()):
            rd = regime_data[sym]
            regime = rd.get("regime", "unknown")
            ic_c, ic_t = REGIME_ICON.get(regime, (DIM, "?        "))
            adx_v = rd.get("adx", 0)
            hurst_v = rd.get("hurst", 0.5)
            vol_r = rd.get("volatility", "vol_normal")
            atr_p = rd.get("atr_pct", 0)
            rec = rd.get("recommendation")

            # ADX color
            adx_c = GREEN if adx_v >= 25 else (YELLOW if adx_v >= 20 else DIM)
            # Hurst color: >0.55 green (trending), <0.45 blue (MR), else dim
            hurst_c = GREEN if hurst_v > 0.55 else (BLUE if hurst_v < 0.45 else DIM)
            # Vol color
            vol_icons = {"vol_low": (CYAN, "LOW"), "vol_normal": (DIM, "NORM"),
                         "vol_high": (ORANGE, "HIGH"), "vol_extreme": (RED + BOLD, "EXTR")}
            vc, vt = vol_icons.get(vol_r, (DIM, "?"))

            # Recommended strategies: show first 3
            rec_s = ""
            if rec:
                short_names = [r.replace("_futures","(F)").replace("_spot","(S)")[:14] for r in rec[:3]]
                rec_s = col(", ".join(short_names), DIM)
            elif rec is not None and not rec:
                rec_s = col("ALL PAUSED", RED)

            row("  {:<12} {} {}  {}  {}  {}".format(
                sym[:12],
                vljust(col(ic_t, ic_c), 14),
                vrjust(col("{:.1f}".format(adx_v),  adx_c),   5),
                vrjust(col("{:.3f}".format(hurst_v), hurst_c), 6),
                vrjust(col("{:>4} {:.2f}%".format(vt, atr_p), vc), 9),
                rec_s))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  CORRELATION EXPOSURE
    # ══════════════════════════════════════════════════════════════════
    if open_pos:
        try:
            _corr_mod = _load_core_module("correlation_manager")
            if not _corr_mod:
                raise ImportError("correlation_manager not found")
            CORRELATION_GROUPS = _corr_mod.CORRELATION_GROUPS
            group_usage = {}
            for gname, gdata in CORRELATION_GROUPS.items():
                count = 0
                notional = 0.0
                for p in open_pos:
                    base = p.get("symbol", "").split("/")[0].upper()
                    if base in gdata["assets"]:
                        count += 1
                        sz = float(p.get("size", 0) or 0)
                        ep = float(p.get("entry_price", 0) or 0)
                        notional += sz * ep
                if count > 0:
                    group_usage[gname] = {
                        "count": count, "notional": notional,
                        "max_pct": gdata["max_group_pct"],
                        "assets": gdata["assets"],
                    }

            if group_usage:
                box_top("CORRELATION EXPOSURE")
                total_notional = sum(
                    float(p.get("size", 0) or 0) * float(p.get("entry_price", 0) or 0)
                    for p in open_pos
                )
                if total_notional <= 0:
                    total_notional = total_live if total_live > 0 else 1.0

                row("  {} {}  {}  {}  {}  {}".format(
                    vljust(col("Group",    DIM), 14),
                    vrjust(col("Pos",      DIM),  5),
                    vrjust(col("Notional", DIM), 10),
                    vrjust(col("Used%",    DIM),  6),
                    vrjust(col("Max%",     DIM),  6),
                    col("Fill", DIM)))
                row("  " + col("─" * 62, DIM))
                for gname in sorted(group_usage.keys()):
                    gu = group_usage[gname]
                    used_pct = gu["notional"] / total_notional * 100 if total_notional > 0 else 0
                    max_pct = gu["max_pct"] * 100
                    fill_ratio = min(used_pct / max_pct, 1.0) if max_pct > 0 else 0
                    bar_len = int(fill_ratio * 16)
                    bar_rem = 16 - bar_len
                    if fill_ratio >= 0.9:
                        bar_c = RED
                    elif fill_ratio >= 0.6:
                        bar_c = YELLOW
                    else:
                        bar_c = GREEN
                    bar_s = col("█" * bar_len, bar_c) + col("░" * bar_rem, DIM)
                    row("  {:<14} {:>5}  {:>9.2f}$  {:>5.1f}%  {:>5.0f}%  {}".format(
                        gname, gu["count"], gu["notional"],
                        used_pct, max_pct, bar_s))
                box_bot()
                print()
        except Exception:
            pass

    # ══════════════════════════════════════════════════════════════════
    #  FUNDING RATES
    # ══════════════════════════════════════════════════════════════════
    funding = fetcher.funding_rates()
    if funding:
        box_top("FUNDING RATES  (per 8h)")
        # Collect all symbols across exchanges
        all_fsyms = set()
        for rates in funding.values():
            all_fsyms.update(rates.keys())
        all_fsyms = sorted(all_fsyms)

        # Header — pad text BEFORE applying ANSI colors to keep alignment
        ex_names = sorted(funding.keys())
        hdr_parts = col("{:<16}".format("Symbol"), DIM)
        for en in ex_names:
            ec = EX_COLOUR.get(en, WHITE)
            hdr_parts += " " + col("{:>10}".format(en[:8].upper()), ec)
        hdr_parts += "  {}".format(col("Signal", DIM))
        row("  " + hdr_parts)
        row("  " + col("─" * (16 + 12 * len(ex_names) + 10), DIM))

        for sym in all_fsyms[:8]:
            short_sym = sym.split("/")[0]
            parts = "{:<16}".format(short_sym)
            max_rate = 0
            min_rate = 0
            for en in ex_names:
                rate = funding.get(en, {}).get(sym)
                if rate is not None:
                    r_pct = rate * 100
                    max_rate = max(max_rate, rate)
                    min_rate = min(min_rate, rate)
                    if abs(r_pct) >= 0.1:
                        rc = RED + BOLD if r_pct > 0 else GREEN + BOLD
                    elif abs(r_pct) >= 0.05:
                        rc = RED if r_pct > 0 else GREEN
                    else:
                        rc = DIM
                    parts += " " + col("{:>10}".format("{:+.4f}%".format(r_pct)), rc)
                else:
                    parts += " " + col("{:>10}".format("--"), DIM)

            # Signal (check extreme thresholds first)
            if max_rate >= 0.001:
                sig = col("SHORT", RED + BOLD)
            elif min_rate <= -0.001:
                sig = col("LONG", GREEN + BOLD)
            elif max_rate >= 0.0005:
                sig = col("short", RED)
            elif min_rate <= -0.0005:
                sig = col("long", GREEN)
            else:
                sig = col("neutral", DIM)
            parts += "  {}".format(sig)
            row("  " + parts)
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  EQUITY CURVE  (ASCII)
    # ══════════════════════════════════════════════════════════════════
    if closed and len(closed) >= 3:
      try:
        import numpy as np
        # Build equity curve from closed trades
        sorted_closed = sorted(closed, key=lambda x: x.get("close_time", 0))
        equity = []
        bal_curve = 0.0
        for t in sorted_closed:
            bal_curve += t.get("pnl", 0) or 0
            equity.append(bal_curve)

        # Only show last 50 trades for readability
        eq_show = equity[-50:]
        if len(eq_show) >= 3:
            box_top("EQUITY CURVE  (last {} trades)".format(len(eq_show)))

            # Compute Sharpe/Sortino from PnL series
            pnl_series = [t.get("pnl", 0) or 0 for t in sorted_closed[-50:]]
            pnl_arr = np.array(pnl_series)
            mean_pnl = np.mean(pnl_arr)
            std_pnl = np.std(pnl_arr)
            trades_per_yr = 365 * 4
            sharpe = (mean_pnl / std_pnl * np.sqrt(trades_per_yr)) if std_pnl > 0 else 0
            downside = pnl_arr[pnl_arr < 0]
            ds_std = np.std(downside) if len(downside) > 0 else 0
            sortino = (mean_pnl / ds_std * np.sqrt(trades_per_yr)) if ds_std > 0 else 0

            # Max drawdown
            peak_eq = 0.0
            max_dd = 0.0
            for e in equity:
                peak_eq = max(peak_eq, e)
                dd = peak_eq - e
                max_dd = max(max_dd, dd)

            # ASCII chart: 8 rows height
            chart_h = 8
            chart_w = min(len(eq_show), W - 10)
            # Resample if needed
            if len(eq_show) > chart_w:
                step = len(eq_show) / chart_w
                sampled = [eq_show[int(i * step)] for i in range(chart_w)]
            else:
                sampled = eq_show

            mn = min(sampled)
            mx = max(sampled)
            rng = mx - mn if mx != mn else 1

            for r in range(chart_h):
                threshold = mx - (r / (chart_h - 1)) * rng
                line_chars = []
                for v in sampled:
                    if v >= threshold:
                        if v >= 0:
                            line_chars.append(col("█", GREEN))
                        else:
                            line_chars.append(col("█", RED))
                    else:
                        line_chars.append(" ")
                # Y-axis label
                if r == 0:
                    label = "{:>+8.2f}".format(mx)
                elif r == chart_h - 1:
                    label = "{:>+8.2f}".format(mn)
                elif r == chart_h // 2:
                    mid_val = (mx + mn) / 2
                    label = "{:>+8.2f}".format(mid_val)
                else:
                    label = "        "
                row("{}{}{}".format(col(label, DIM), col("│", DIM), "".join(line_chars)))

            # X-axis
            row("{}{}".format(" " * 8, col("└" + "─" * len(sampled), DIM)))

            # Stats below chart
            sc = GREEN if sharpe > 1 else (YELLOW if sharpe > 0 else RED)
            soc = GREEN if sortino > 1 else (YELLOW if sortino > 0 else RED)
            dd_c = GREEN if max_dd < 5 else (YELLOW if max_dd < 15 else RED)
            row("  Sharpe: {}  Sortino: {}  MaxDD: {}  Cumul: {}".format(
                col("{:.2f}".format(sharpe), sc),
                col("{:.2f}".format(sortino), soc),
                col("{:.2f} USDT".format(max_dd), dd_c),
                pnl_str_short(equity[-1]) if equity else col("--", DIM)))
            box_bot()
            print()
      except ImportError:
        pass  # numpy not available — skip equity curve panel

    # ══════════════════════════════════════════════════════════════════
    #  RISK DASHBOARD
    # ══════════════════════════════════════════════════════════════════
    risk_data = _file_cache.load("data/risk_state.json")
    # Also derive risk from positions and config
    try:
        from config import RISK as RISK_CFG
        risk_avail = True
    except Exception:
        risk_avail = False

    if risk_avail or risk_data:
        box_top("RISK DASHBOARD")

        # Drawdown bar
        if risk_data:
            # risk_manager writes "max_drawdown_pct" and "is_halted"; accept both old+new keys
            dd_pct = risk_data.get("max_drawdown_pct",
                                   risk_data.get("drawdown_pct", 0)) * 100
            peak = risk_data.get("peak_balance", 0)
            daily_pnl = risk_data.get("daily_pnl", 0)
            halted = risk_data.get("is_halted",
                                   risk_data.get("halted", False))
            halt_reason = risk_data.get("halt_reason", "")
            trades_today = risk_data.get("trades_today", 0)
        else:
            dd_pct = 0
            peak = total_live
            daily_pnl = s.get("today_pnl", 0)
            halted = False
            halt_reason = ""
            trades_today = s.get("today_n", 0)

        if risk_avail:
            max_dd_limit = RISK_CFG.get("max_drawdown_pct", 0.25) * 100
            daily_loss_limit = RISK_CFG.get("max_daily_loss_pct", 0.08) * 100
            max_positions = RISK_CFG.get("max_open_positions", 15)
            max_leverage = RISK_CFG.get("futures_max_leverage", 5)
        else:
            max_dd_limit = 25
            daily_loss_limit = 8
            max_positions = 15
            max_leverage = 5

        # Drawdown bar
        dd_fill = min(dd_pct / max_dd_limit, 1.0) if max_dd_limit > 0 else 0
        dd_bar_len = int(dd_fill * 20)
        dd_bar_rem = 20 - dd_bar_len
        dd_bar_c = GREEN if dd_fill < 0.5 else (YELLOW if dd_fill < 0.8 else RED)
        dd_bar = col("█" * dd_bar_len, dd_bar_c) + col("░" * dd_bar_rem, DIM)
        row("  Drawdown:    {} {:.1f}% / {:.0f}% max".format(
            dd_bar, dd_pct, max_dd_limit))

        # Daily loss bar
        if total_live > 0:
            daily_used = abs(min(daily_pnl, 0)) / total_live * 100
        else:
            daily_used = 0
        dl_fill = min(daily_used / daily_loss_limit, 1.0) if daily_loss_limit > 0 else 0
        dl_bar_len = int(dl_fill * 20)
        dl_bar_rem = 20 - dl_bar_len
        dl_bar_c = GREEN if dl_fill < 0.5 else (YELLOW if dl_fill < 0.8 else RED)
        dl_bar = col("█" * dl_bar_len, dl_bar_c) + col("░" * dl_bar_rem, DIM)
        row("  Daily Loss:  {} {:.2f}% / {:.0f}% max  ({})".format(
            dl_bar, daily_used, daily_loss_limit,
            pnl_str_short(daily_pnl)))

        # Position usage
        n_open = len(open_pos)
        pos_fill = min(n_open / max_positions, 1.0) if max_positions > 0 else 0
        pos_bar_len = int(pos_fill * 20)
        pos_bar_rem = 20 - pos_bar_len
        pos_bar_c = GREEN if pos_fill < 0.6 else (YELLOW if pos_fill < 0.9 else RED)
        pos_bar = col("█" * pos_bar_len, pos_bar_c) + col("░" * pos_bar_rem, DIM)
        row("  Positions:   {} {} / {} max".format(
            pos_bar, n_open, max_positions))

        # Halt status + trades today
        if halted:
            row("  {}  {}".format(
                col("!! TRADING HALTED !!", RED + BOLD),
                col(halt_reason, YELLOW)))
        else:
            row("  Status: {}  MaxLev: {}x  Trades Today: {}".format(
                col("ACTIVE", GREEN + BOLD), max_leverage,
                col(str(trades_today), WHITE)))

        # Live safety nets
        if _OP_MODE == "CONTROLLED_LIVE":
            try:
                from config import MAX_LOSS_PER_TRADE_USD, LEVERAGE_TIERS
                _max_pos = RISK_CFG.get("max_position_pct", 0.01) * 100
                _tiers = "  ".join(
                    "{}:{}x".format(t, v["leverage"])
                    for t, v in LEVERAGE_TIERS.items())
                row("  {}  Size: {}%  MaxLoss: ${:.2f}  Tiers: {}  Hold: 2h min".format(
                    col("LIVE SAFETY:", CYAN + BOLD), _max_pos,
                    MAX_LOSS_PER_TRADE_USD, _tiers))
            except Exception:
                pass

        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  WAREHOUSE: PER-SYMBOL EDGE  (Spec §15)
    # ══════════════════════════════════════════════════════════════════
    wh = load_warehouse_stats()
    if wh and wh.get("total_trades", 0) > 0:
        box_top("PER-SYMBOL EDGE (warehouse)")
        row("  {}  candidates: {}   closed trades: {}".format(
            col("Source:", DIM),
            col(str(wh.get("total_candidates", 0)), WHITE),
            col(str(wh.get("total_trades", 0)), WHITE)))
        row("  {:<14} {:>4} {:>9} {:>6} {:>7} {:>9}".format(
            "Symbol", "N", "Net", "WR", "PF", "Expect"))
        for r_ in wh["per_symbol"]:
            n = int(r_["n"] or 0)
            net = float(r_["net"] or 0.0)
            gw  = float(r_["gw"]  or 0.0)
            gl  = float(r_["gl"]  or 0.0)
            wins = int(r_["wins"] or 0)
            wr = (wins / n * 100) if n else 0.0
            pf = (gw / gl) if gl > 0 else (999.0 if gw > 0 else 0.0)
            exp_ = (net / n) if n else 0.0
            net_c = GREEN if net > 0 else (RED if net < 0 else DIM)
            pf_c  = GREEN if pf >= 1.0 else RED
            row("  {:<14} {:>4d} {} {:>5.1f}% {} {}".format(
                (r_["symbol"] or "?")[:14],
                n,
                vrjust(col("{:+.2f}".format(net), net_c), 9),
                wr,
                vrjust(col(("{:.2f}".format(pf) if pf < 99 else "inf"), pf_c), 7),
                vrjust(col("{:+.4f}".format(exp_), net_c), 9)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  LOSS-CLUSTER MONITOR  (Spec §12 pauses)
    # ══════════════════════════════════════════════════════════════════
    rstate = load_risk_state()
    if (wh and wh.get("per_family")) or rstate:
        box_top("LOSS-CLUSTER MONITOR")
        sym_pauses = (rstate or {}).get("symbol_pauses", {}) or {}
        fam_pauses = (rstate or {}).get("family_pauses", {}) or {}
        gstreak = (rstate or {}).get("global_streak", []) or []

        # Global streak marker (last 20)
        recent_losses = 0
        for ok in reversed(gstreak):
            if not ok:
                recent_losses += 1
            else:
                break
        glob_c = GREEN if recent_losses < 2 else (YELLOW if recent_losses < 5 else RED)
        row("  {} {}   {} {}".format(
            col("Global streak:", DIM),
            col("{} consecutive losses".format(recent_losses), glob_c),
            col("last 20:", DIM),
            "".join(col("W", GREEN) if ok else col("L", RED)
                    for ok in gstreak[-20:]) or col("(none)", DIM)))

        # Per-family rollup
        now_ts = time.time()
        if wh.get("per_family"):
            row("  {:<18} {:>4} {:>9} {:>6}   {}".format(
                "Family", "N", "Net", "WR", "Pause"))
            for r_ in wh["per_family"]:
                fam = (r_["fam"] or "unknown")[:18]
                n = int(r_["n"] or 0)
                net = float(r_["net"] or 0.0)
                wins = int(r_["wins"] or 0)
                wr = (wins / n * 100) if n else 0.0
                net_c = GREEN if net > 0 else (RED if net < 0 else DIM)
                until = float(fam_pauses.get(fam, 0.0) or 0.0)
                if until > now_ts:
                    mins = int((until - now_ts) / 60)
                    pause_txt = col("PAUSED {}m".format(mins), RED + BOLD)
                else:
                    pause_txt = col("active", DIM)
                row("  {:<18} {:>4d} {} {:>5.1f}%   {}".format(
                    fam, n,
                    vrjust(col("{:+.2f}".format(net), net_c), 9),
                    wr, pause_txt))

        # Paused symbols (if any)
        live_sym_pauses = [(k, v) for k, v in sym_pauses.items() if float(v) > now_ts]
        if live_sym_pauses:
            row("  {} {}".format(
                col("Paused symbols:", DIM),
                ", ".join(col("{} ({}m)".format(k, int((float(v)-now_ts)/60)),
                              YELLOW) for k, v in live_sym_pauses[:6])))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  SLIPPAGE vs FEE RATIO  (execution-quality decay)
    # ══════════════════════════════════════════════════════════════════
    if wh and wh.get("slippage", {}).get("n", 0):
        box_top("SLIPPAGE vs FEE RATIO")
        s = wh["slippage"]
        n = int(s.get("n") or 0)
        slip = float(s.get("avg_slip") or 0.0)
        fee  = float(s.get("avg_fee")  or 0.0)
        ratio = (slip / fee) if fee > 0 else 0.0
        # Healthy when slippage cost is < fee cost (ratio < 1).
        ratio_c = GREEN if ratio < 1.0 else (YELLOW if ratio < 2.0 else RED)
        row("  {} {}   {} {}   {} {}".format(
            col("Avg slippage:", DIM),
            col("{:.4f} USDT".format(slip), WHITE),
            col("Avg fee:", DIM),
            col("{:.4f} USDT".format(fee), WHITE),
            col("Ratio:", DIM),
            col("{:.2f}x".format(ratio), ratio_c)))
        hint = ("healthy" if ratio < 1.0 else
                ("watch" if ratio < 2.0 else "execution decay"))
        row("  {} {}   {} trades sampled".format(
            col("Status:", DIM), col(hint, ratio_c), col(str(n), WHITE)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  EXECUTION QUALITY
    # ══════════════════════════════════════════════════════════════════
    exec_data = _file_cache.load("data/execution_stats.json")
    if exec_data:
        box_top("EXECUTION QUALITY")
        total_orders = exec_data.get("total_orders", 0)
        limit_fills = exec_data.get("limit_fills", 0)
        market_falls = exec_data.get("market_fallbacks", 0)
        twap_used = exec_data.get("twap_orders", 0)
        avg_slippage = exec_data.get("avg_slippage_pct", 0)
        spread_rejects = exec_data.get("spread_rejects", 0)
        saved_fees = exec_data.get("estimated_fee_savings", 0)

        fill_rate = (limit_fills / total_orders * 100) if total_orders > 0 else 0
        fc = GREEN if fill_rate >= 60 else (YELLOW if fill_rate >= 30 else DIM)

        row("  Orders: {}  Limit Fills: {} ({})  Market Fallback: {}".format(
            col(str(total_orders), WHITE),
            col(str(limit_fills), GREEN),
            col("{:.0f}%".format(fill_rate), fc),
            col(str(market_falls), YELLOW)))
        row("  TWAP: {}  Spread Rejects: {}  Avg Slippage: {}".format(
            col(str(twap_used), CYAN),
            col(str(spread_rejects), RED if spread_rejects > 0 else DIM),
            col("{:.4f}%".format(avg_slippage), GREEN if avg_slippage < 0.05 else YELLOW)))
        if saved_fees > 0:
            row("  {} {}".format(
                col("Est. Fee Savings:", WHITE),
                col("{:.4f} USDT".format(saved_fees), GREEN)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  FOOTER
    # ══════════════════════════════════════════════════════════════════
    print(col("─" * W, DIM))
    if _BG_LAST_ERR:
        err_show = _BG_LAST_ERR if len(_BG_LAST_ERR) <= W - 8 else _BG_LAST_ERR[: W - 12] + "..."
        print("  {} {}".format(col("Fetch warning:", RED + BOLD), col(err_show, YELLOW)))

    # DRY_RUN sim-realism note: show slippage model applied to paper fills
    # so users know the paper curve already reflects LIVE execution costs.
    if dry_run:
        try:
            from config import SLIPPAGE as _SL_CFG
            if _SL_CFG.get("enabled", True):
                bp_open  = _SL_CFG.get("pct_open",      0.0005) * 10000
                bp_close = _SL_CFG.get("pct_close",     0.0005) * 10000
                bp_stop  = _SL_CFG.get("pct_stop_loss", 0.0010) * 10000
                wick_on  = "wick" if _SL_CFG.get("wick_sl_tp", True) else "poll"
                fund_on  = "fund" if _SL_CFG.get("funding",    True) else "-"
                print("  {} open {:.0f}bp close {:.0f}bp stop {:.0f}bp  [{} {}]  {}".format(
                    col("Sim-realism:", DIM),
                    bp_open, bp_close, bp_stop,
                    col(wick_on, CYAN), col(fund_on, CYAN),
                    col("(paper fills match LIVE book costs)", DIM)))
        except Exception:
            pass

    print("  {} {} Refresh:{}s {} tick #{} {} up {}".format(
        col("Ctrl+C to exit", YELLOW), col(B_V, DIM),
        REFRESH_SECONDS, col(B_V, DIM),
        tick, col(B_V, DIM), _uptime_str()))
    print(col("─" * W, DIM))


# ══════════════════════════════════════════════════════════════════════
# Background fetch thread
# ══════════════════════════════════════════════════════════════════════

def background_fetch(fetcher: LiveFetcher, stop_event: threading.Event,
                     ready_event: threading.Event = None):
    global _BG_LAST_ERR
    _last_logged_err = None
    _last_log_ts = 0.0
    _regime_tick = 0
    while not stop_event.is_set():
        try:
            # Fetch live exchange positions first (so they're available for merge)
            if not fetcher._dry_run:
                fetcher.fetch_live_positions()
            open_pos, _ = load_positions(fetcher)
            symbols = {p.get("symbol","") for p in open_pos if p.get("symbol")}
            # Add symbols for all coins in wallet so we can estimate USDT values
            stables = {"USDT", "USD", "BUSD", "USDC"}
            for ex_coins in fetcher.coin_balances().values():
                for asset in ex_coins:
                    if asset not in stables:
                        symbols.add("{}/USDT".format(asset))
            fetcher.fetch(symbols)

            # Regime + funding: every 3rd tick (expensive API calls)
            _regime_tick += 1
            if _regime_tick % 3 == 1:
                try:
                    regime_syms = {"BTC/USDT", "ETH/USDT", "SOL/USDT", "XRP/USDT"}
                    regime_syms.update(s for s in symbols if ":" not in s and s)
                    fetcher.fetch_regime_data(regime_syms)
                except Exception:
                    pass
                try:
                    fetcher.fetch_funding_rates()
                except Exception:
                    pass

            _BG_LAST_ERR = None
        except Exception as e:
            msg = str(e).strip().replace("\n", " ")[:200]
            _BG_LAST_ERR = msg
            now = time.time()
            if msg != _last_logged_err or (now - _last_log_ts) >= 60.0:
                logger.warning("[Dashboard] Background fetch failed: {}", msg)
                _last_logged_err = msg
                _last_log_ts = now
        if ready_event and not ready_event.is_set():
            ready_event.set()  # Signal that first fetch is done
        stop_event.wait(REFRESH_SECONDS)


# ══════════════════════════════════════════════════════════════════════
# Entry point
# ══════════════════════════════════════════════════════════════════════

def parse_args():
    p = argparse.ArgumentParser(
        description="LIVE terminal dashboard: balances, positions, PnL (same .env as the bot).",
    )
    p.add_argument(
        "--refresh",
        type=int,
        default=60,
        metavar="SEC",
        help="Seconds between data refresh and screen redraw (default: 60, min 3, max 3600).",
    )
    p.add_argument(
        "--width",
        type=int,
        default=None,
        metavar="COLS",
        help="Terminal width for layout (default: min(terminal width, 120) or 80).",
    )
    return p.parse_args()


def main():
    global REFRESH_SECONDS, DASH_WIDTH
    args = parse_args()
    REFRESH_SECONDS = max(3, min(int(args.refresh), 3600))
    if args.width is not None:
        DASH_WIDTH = max(60, min(int(args.width), 200))
    else:
        try:
            if shutil:
                cols = shutil.get_terminal_size((100, 24)).columns
                DASH_WIDTH = max(60, min(cols, 120))
            else:
                DASH_WIDTH = 80
        except Exception:
            DASH_WIDTH = 80

    enable_ansi()
    tick       = 0
    fetcher    = LiveFetcher()
    stop_event  = threading.Event()
    ready_event = threading.Event()
    fetch_thread = threading.Thread(
        target=background_fetch, args=(fetcher, stop_event, ready_event),
        daemon=True, name="live-fetcher")
    fetch_thread.start()

    print("\n  Starting live dashboard — connecting to exchanges...")
    print("  (Fetching live positions from all exchanges...)")
    # Wait up to 30s for first fetch to complete so positions show on first render
    ready_event.wait(timeout=30)

    while True:
        try:
            open_pos, closed = load_positions(fetcher)
            dry_run          = load_mode()
            clr()
            tick += 1
            render(open_pos, closed, dry_run, tick, fetcher)
            time.sleep(REFRESH_SECONDS)
        except KeyboardInterrupt:
            print("\n  Stopping dashboard...")
            stop_event.set()
            break
        except Exception as e:
            import traceback
            print("\n  Dashboard Error: " + str(e))
            traceback.print_exc()
            print("\n  Retrying in {}s...".format(REFRESH_SECONDS))
            time.sleep(REFRESH_SECONDS)


if __name__ == "__main__":
    main()
