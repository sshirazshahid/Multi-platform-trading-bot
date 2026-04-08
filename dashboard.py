"""
dashboard.py -- LIVE Real-Time Trading Dashboard (Optimized)

Features:
  - Live prices + balances from all 4 exchanges (parallel fetching)
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
    "mexc":    "\033[38;5;45m",
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
# Balance extractor — handles all 4 exchange response formats
# ══════════════════════════════════════════════════════════════════════

def extract_usdt(bal: dict, exchange_name: str = "") -> float:
    if not bal:
        return 0.0
    ex = exchange_name.lower()

    if ex == "bybit":
        # Priority 1: Raw Bybit v5 API — totalEquity matches Bybit app display
        # (includes wallet balance + unrealized PnL from open positions)
        try:
            result_list = bal.get("info", {}).get("result", {}).get("list", [{}])
            if result_list:
                equity = result_list[0].get("totalEquity")
                if equity:
                    v = float(equity)
                    if v > 0: return v
                wb = result_list[0].get("totalWalletBalance")
                if wb:
                    v = float(wb)
                    if v > 0: return v
        except Exception:
            pass
        # Fallback: ccxt parsed fields
        usdt = bal.get("USDT") or {}
        if isinstance(usdt, dict):
            total = usdt.get("total")
            if total is not None:
                try:
                    v = float(total)
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

    # Standard ccxt (Binance, MEXC)
    usdt = bal.get("USDT")
    if isinstance(usdt, dict):
        for key in ("free", "total"):
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
            from exchanges import BinanceClient, MEXCClient, BybitClient, BitgetClient
            from config import DRY_RUN, TRADING_MODE
            self._dry_run = DRY_RUN
            self._trading_mode = TRADING_MODE
            candidates = {
                "binance": BinanceClient,
                "mexc":    MEXCClient,
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
                        # Only mark OK if we actually got balance data
                        if usdt > 0 or name not in self._ex_status:
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
                            else:
                                self._prices[sym] = (self._prices[sym] + price) / 2
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
            except Exception:
                return []

        def _fetch_spot_holdings(name, ex):
            """Fetch non-USDT spot holdings as open SPOT positions."""
            try:
                bal = ex.fetch_balance("spot")
                results = []
                # Parse ccxt balance format: {"BTC": {"free": 0.001, "total": 0.001}, ...}
                for asset_key in ("free", "total"):
                    asset_dict = bal.get(asset_key, {})
                    if not isinstance(asset_dict, dict):
                        continue
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
                        pid = "SPOT-{}-{}-buy".format(name, symbol)
                        results.append({
                            "id": pid,
                            "exchange": name.capitalize(),
                            "symbol": symbol,
                            "side": "buy",
                            "market_type": "spot",
                            "strategy": "holding",
                            "entry_price": price or 0,
                            "size": amt,
                            "stop_loss": 0,
                            "take_profit": 0,
                            "leverage": 1,
                            "open_time": time.time(),
                            "paper_trade": False,
                            "_from_exchange": True,
                        })
                    if results:
                        break  # "free" was enough, skip "total"
                return results
            except Exception:
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
            if is_live and pos.get("paper_trade", True):
                continue
            seen_ids.add(pid)
            all_open.append(pos)
        for pos in data.get("closed", []):
            pid = pos.get("id", "")
            if pid in seen_ids:
                continue
            # LIVE mode: skip paper trades in history too
            if is_live and pos.get("paper_trade", True):
                continue
            seen_ids.add(pid)
            all_closed.append(pos)

    _ingest(_file_cache.load("data/positions.json"))
    for profile in ("conservative", "moderate", "aggressive"):
        _ingest(_file_cache.load("data/profiles/{}/positions.json".format(profile)))

    # Merge live exchange positions not already tracked locally
    if fetcher:
        live_pos = fetcher.get_live_positions()
        tracked_syms = set()
        for p in all_open:
            ex = (p.get("exchange") or "").lower()
            sym = p.get("symbol", "")
            side = p.get("side", "")
            tracked_syms.add((ex, sym, side))
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
def load_comparison(): return _file_cache.load("data/profiles/comparison.json")
def load_claude():     return _file_cache.load("data/claude_analysis.json")
def load_arb():        return _file_cache.load("data/arbitrage/opportunities.json")


def load_mode():
    try:
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            if line.strip().lower().startswith("dry_run"):
                return "false" not in line.lower()
    except Exception:
        pass
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
        if not live_price or not entry or not size:
            result[pid] = {"price": 0.0, "upnl": None, "upnl_pct": None, "move_pct": 0.0}
            continue

        move_pct = (live_price - entry) / entry * 100
        if mtype == "futures":
            upnl = (live_price - entry) * size if side == "buy" else (entry - live_price) * size
        else:
            upnl = (live_price - entry) * size

        # Subtract fees: stored entry_fee + estimated exit_fee at current price
        fee_rate = 0.0005 if mtype == "futures" else 0.001
        entry_fee = float(pos.get("entry_fee", 0) or 0)
        if entry_fee == 0:
            entry_fee = size * entry * fee_rate
        exit_fee_est = size * live_price * fee_rate
        upnl -= (entry_fee + exit_fee_est)

        margin   = (size * entry) / max(lev, 1)
        upnl_pct = (upnl / max(margin, 0.0001)) * 100
        result[pid] = {"price": live_price, "upnl": upnl, "upnl_pct": upnl_pct, "move_pct": move_pct}
    return result


def calc_stats(closed):
    today   = datetime.now().date()
    t_pnl = t_gross = t_fees = 0.0
    t_n   = t_wins  = 0
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
        if ct and datetime.fromtimestamp(ct).date() == today:
            t_pnl += pnl; t_gross += gross; t_fees += fees; t_n += 1
            if pnl > 0: t_wins += 1

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
    mode  = "DRY RUN" if dry_run else "LIVE"
    mc    = YELLOW if dry_run else GREEN + BOLD
    s     = calc_stats(closed)
    ex_s  = calc_exchange_stats(closed, open_pos)
    news  = load_news()
    comp  = load_comparison()
    clai  = load_claude()
    arb   = load_arb()
    W     = DASH_WIDTH

    upnl_map   = calc_unrealized(open_pos, fetcher)
    live_bals  = fetcher.all_balances()
    statuses   = fetcher.all_statuses()
    wallet_bal = fetcher.wallet_balances()
    coin_bals  = fetcher.coin_balances()
    age_s      = int(fetcher.seconds_since_fetch())
    age_col    = GREEN if age_s < 15 else (YELLOW if age_s < 30 else RED)
    total_upnl = sum(v["upnl"] for v in upnl_map.values() if v.get("upnl") is not None)
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
    for e in ["binance", "mexc", "bybit", "bitget"]:
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
        for ex_name in ["binance", "mexc", "bybit", "bitget"]:
            if ex_name not in statuses:
                continue
            ec  = EX_COLOUR.get(ex_name, WHITE)
            bal = wallet_bal.get(ex_name, start_bal)
            st  = statuses.get(ex_name, "—")
            diff = bal - start_bal
            total_live += bal
            st_s = col("OK", GREEN) if st == "OK" else col(st[:15], RED)
            row("{} {:<8}  {:>10.2f} USDT  [{}]  {:>+.2f}".format(
                col("●", ec), col(ex_name.upper(), ec), bal, st_s, diff))
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
        for ex_name in ["binance", "mexc", "bybit", "bitget"]:
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
            row("{} {:<8}  {:>10.2f} USDT  [{}]  {}".format(
                col("●", ec), col(ex_name.upper(), ec), bal, st_s, detail_s))
            # Coin holdings for this exchange
            ex_coins = coin_bals.get(ex_name, {})
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
            col("Free Balance:", WHITE), total_live))
        if total_upnl != 0:
            uc = GREEN if total_upnl >= 0 else RED
            row("  {} {}".format(
                col("Unrealized:  ", WHITE),
                col("{:>+10.4f} USDT".format(total_upnl), uc)))
        # Aggregated holdings
        if grand_coins:
            agg_rows = []
            for asset, amt in grand_coins.items():
                uval = grand_usdt_vals.get(asset, 0.0)
                agg_rows.append((asset, amt, uval))
            agg_rows.sort(key=lambda r: (0 if r[0] in _stables else 1, -r[2]))
            total_est = sum(r[2] for r in agg_rows) + total_upnl
            non_stable = [r for r in agg_rows if r[0] not in _stables and r[2] >= 0.01]
            if non_stable:
                hld = "  ".join("{} {:.6g}".format(col(r[0], CYAN), r[1])
                               for r in non_stable[:10])
                row("  {} {}".format(col("Coins:       ", WHITE), hld))
            row("  {} {:>10.2f} USDT".format(
                col("Est. Total:  ", BOLD + WHITE), total_est))
    box_bot()
    print()

    # ══════════════════════════════════════════════════════════════════
    #  PERFORMANCE
    # ══════════════════════════════════════════════════════════════════
    box_top("PERFORMANCE")
    tl = s["today_n"] - s["today_wins"]; al = s["total_n"] - s["all_wins"]

    sk = s["streak"]; stype = s["streak_type"]
    streak_s = col("{}{}".format(sk, stype), GREEN if stype == "W" else RED) if sk > 0 else col("--", DIM)
    pf = s["profit_factor"]
    pf_c = GREEN if pf >= 1.5 else (YELLOW if pf >= 1.0 else RED)
    pf_s = col("{:.2f}".format(min(pf, 99.99)), pf_c)

    row("  {:<11} {}  trades:{}  W:{} L:{}  WR:{}".format(
        col("Today", WHITE), pnl_str(s["today_pnl"]),
        s["today_n"], col(s["today_wins"], GREEN), col(tl, RED),
        col("{:.1f}%".format(s["today_wr"]), wr_col(s["today_wr"]))))
    row("  {:<11} {}  trades:{}  W:{} L:{}  WR:{}".format(
        col("All Time", WHITE), pnl_str(s["all_pnl"]),
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
                row("      {} @{:.6g}  Now:{}  {}  uPnL:{}  {}m".format(
                    price_lbl, entry, live_s, move_s, upnl_s, dur))
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
        for ex_name in ["binance", "mexc", "bybit", "bitget"]:
            if ex_name not in ex_s:
                continue
            d    = ex_s[ex_name]
            ec   = EX_COLOUR.get(ex_name, WHITE)
            pnl  = d["pnl"]; n = d["n"]; wins = d["wins"]
            open_n = d["open"]
            wr   = (wins / n * 100) if n > 0 else 0
            live_b = live_bals.get(ex_name)
            bal_s  = col("bal:{:.2f}".format(live_b), DIM) if live_b else ""
            row("{} {:<8} trades:{:>3}  WR:{}  PnL:{}  open:{}  {}".format(
                col("●", ec), col(ex_name.upper(), ec),
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
        row("  {:<20} {:>6}  {:>7}  {:>12}".format(
            col("Strategy", DIM), col("Trades", DIM),
            col("WR", DIM), col("PnL", DIM)))
        row("  " + col("─" * 52, DIM))
        sorted_strats = sorted(strat_s.items(), key=lambda x: x[1]["pnl"], reverse=True)
        for sname, sd in sorted_strats:
            wr = (sd["wins"] / sd["n"] * 100) if sd["n"] > 0 else 0
            pc = GREEN if sd["pnl"] >= 0 else RED
            wc = wr_col(wr)
            star = col(" ★", GOLD) if sd == sorted_strats[0][1] and sd["pnl"] > 0 else ""
            row("  {:<20} {:>6}  {}  {}{}".format(
                sname, sd["n"],
                col("{:>6.1f}%".format(wr), wc),
                col("{:>+11.4f}".format(sd["pnl"]), pc), star))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  MULTI-PROFILE COMPARISON
    # ══════════════════════════════════════════════════════════════════
    if comp and comp.get("profiles"):
        profiles  = comp.get("profiles", {})
        ranked    = comp.get("ranked",   [])
        leader    = comp.get("leader",   "")
        scans     = comp.get("scan_count", 0)
        recommend = comp.get("recommendation", "")
        PROF_C    = {"conservative": GREEN, "moderate": YELLOW, "aggressive": RED}

        box_top("MULTI-PROFILE  --  Scan #{}".format(scans))
        row("  {:<14} {:>9} {:>9} {:>7} {:>10} {:>7}".format(
            "Profile", "Balance", "Return", "WR", "PnL", "DD%"))
        row("  " + col("─" * 60, DIM))
        for name in ranked:
            d    = profiles.get(name, {})
            pc   = PROF_C.get(name, WHITE)
            bal  = d.get("balance", 0); ret = d.get("return_pct", 0)
            wr   = d.get("win_rate",  0); pnl = d.get("net_pnl",   0)
            dd   = d.get("max_drawdown", 0)
            halt = d.get("is_halted", False)
            lf   = col(" << LEADER", GREEN + BOLD) if name == leader else ""
            hf   = col(" HALTED", RED) if halt else ""
            row("  {}{:<14}{} {:>9.2f} {}{:>+8.2f}%{} {}{:>5.1f}%{} "
                "{}{:>+9.4f}{} {:>6.1f}%{}{}".format(
                pc, name.upper(), RESET, bal,
                GREEN if ret >= 0 else RED, ret, RESET,
                GREEN if wr >= 55 else (YELLOW if wr >= 45 else RED), wr, RESET,
                GREEN if pnl >= 0 else RED, pnl, RESET,
                dd, lf, hf))
        if recommend:
            rc = GREEN if "READY" in recommend else (YELLOW if "leads" in recommend else DIM)
            row("  " + col(recommend[:72], rc))
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
        row("  {:<12} {:>9}  {:>5}  {:>6}  {}".format(
            col("Date", DIM), col("PnL", DIM), col("Trd", DIM), col("WR%", DIM), ""))
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
                row("  {:<12} {:>9}  {:>5}  {:>6}  {}".format(
                    ds, col("--", DIM), col("0", DIM), col("--", DIM), col("no trades", DIM)))
            else:
                row("  {:<12} {:>9}  {:>5}  {:>5.1f}%  {}".format(
                    ds, col("{}{:.4f}".format(sign, pnl), pc), col(str(n), WHITE), wr, bar))
        wk_pnl = sum(d["pnl"] for d in daily); wk_t = sum(d["trades"] for d in daily)
        wk_w   = sum(d["wins"] for d in daily); wk_wr = (wk_w / wk_t * 100) if wk_t else 0
        wc     = GREEN if wk_pnl >= 0 else RED
        row("  " + col("─" * 50, DIM))
        row("  {:<12} {:>9}  {:>5}  {:>5.1f}%".format(
            col("WEEK TOTAL", BOLD + WHITE),
            col("{}{:.4f}".format("+" if wk_pnl >= 0 else "", wk_pnl), wc),
            col(str(wk_t), WHITE), wk_wr))
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
    #  ARBITRAGE
    # ══════════════════════════════════════════════════════════════════
    if arb:
        box_top("ARBITRAGE")
        arb_pnl = arb.get("total_pnl", 0)
        row("  Scan #{} {} Open:{} {} PnL:{}".format(
            arb.get("scan_count", 0), col(B_V, DIM),
            arb.get("open_arbs", 0), col(B_V, DIM),
            col("{:+.4f} USDT".format(arb_pnl), GREEN if arb_pnl >= 0 else RED)))
        for o in arb.get("opportunities", [])[:3]:
            ns = o.get("net_spread", 0) * 100
            row("  {:<12} buy:{:<8} sell:{:<8} {}".format(
                o.get("symbol", "?"), o.get("buy_ex", "?")[:7], o.get("sell_ex", "?")[:7],
                col("{:.3f}%".format(ns), GREEN if ns >= 0.5 else YELLOW)))
        box_bot()
        print()

    # ══════════════════════════════════════════════════════════════════
    #  MARKET SENTIMENT & AI
    # ══════════════════════════════════════════════════════════════════
    has_sentiment = news or (clai and clai.get("coins"))
    if has_sentiment:
        box_top("MARKET INTELLIGENCE")
        if news:
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
        if clai and clai.get("coins"):
            if news:
                box_mid("CLAUDE AI")
            bias = clai.get("market_bias", "neutral"); rm = clai.get("risk_multiplier", 1.0)
            bc   = GREEN if bias == "bullish" else (RED if bias == "bearish" else YELLOW)
            note = clai.get("market_note", "")
            row("  Bias: {}  Risk: {}{}".format(
                col(bias.upper(), bc),
                col("{:.2f}x".format(rm), GREEN if rm <= 1.0 else YELLOW),
                "  " + col(note[:48], DIM) if note else ""))
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
            row("  {:<18} {:>6}  {:>6}  {:>8}  {:>10}".format(
                col("Strategy", DIM), col("Trades", DIM),
                col("WR%", DIM), col("R-Mult", DIM), col("Kelly%", DIM)))
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
                    col("{:>5.1f}%".format(wr_k), wrc),
                    r_mult,
                    col("{:>+9.1f}%".format(kelly_f), kc)))
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
        row("  {:<12} {:<14} {:>5}  {:>6}  {:>9}  {}".format(
            col("Symbol", DIM), col("Regime", DIM), col("ADX", DIM),
            col("Hurst", DIM), col("Vol", DIM), col("Strategies", DIM)))
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

            row("  {:<12} {}  {}  {}  {}  {}".format(
                sym[:12],
                col(ic_t, ic_c),
                col("{:>5.1f}".format(adx_v), adx_c),
                col("{:>5.3f}".format(hurst_v), hurst_c),
                col("{:>4} {:>4.2f}%".format(vt, atr_p), vc),
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
                        lev = int(p.get("leverage", 1) or 1)
                        notional += sz * ep * lev
                if count > 0:
                    group_usage[gname] = {
                        "count": count, "notional": notional,
                        "max_pct": gdata["max_group_pct"],
                        "assets": gdata["assets"],
                    }

            if group_usage:
                box_top("CORRELATION EXPOSURE")
                total_notional = sum(
                    float(p.get("size", 0) or 0) * float(p.get("entry_price", 0) or 0) *
                    int(p.get("leverage", 1) or 1) for p in open_pos
                )
                if total_notional <= 0:
                    total_notional = total_live if total_live > 0 else 1.0

                row("  {:<14} {:>5}  {:>10}  {:>6}  {:>6}  {}".format(
                    col("Group", DIM), col("Pos", DIM), col("Notional", DIM),
                    col("Used%", DIM), col("Max%", DIM), col("Fill", DIM)))
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
            dd_pct = risk_data.get("drawdown_pct", 0) * 100
            peak = risk_data.get("peak_balance", 0)
            daily_pnl = risk_data.get("daily_pnl", 0)
            halted = risk_data.get("halted", False)
            halt_reason = risk_data.get("halt_reason", "")
        else:
            dd_pct = 0
            peak = total_live
            daily_pnl = s.get("today_pnl", 0)
            halted = False
            halt_reason = ""

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

        # Halt status
        if halted:
            row("  {}  {}".format(
                col("!! TRADING HALTED !!", RED + BOLD),
                col(halt_reason, YELLOW)))
        else:
            row("  Status: {}  MaxLev: {}x".format(
                col("ACTIVE", GREEN + BOLD), max_leverage))

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
