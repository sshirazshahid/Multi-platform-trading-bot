"""Live exchange data fetcher for the dashboard package."""
from __future__ import annotations

import json
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from loguru import logger

from dashboard.balances import extract_all_coins, extract_usdt
from dashboard.health import _load_core_module
from dashboard.state import PROJECT_ROOT, _UNIFIED_EXCHANGES

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
            sys.path.insert(0, str(PROJECT_ROOT))
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
