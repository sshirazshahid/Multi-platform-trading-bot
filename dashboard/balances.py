"""Exchange balance extractors for the dashboard package."""
from __future__ import annotations

def extract_usdt(bal: dict, exchange_name: str = "") -> float:
    if not bal:
        return 0.0
    ex = exchange_name.lower()

    if ex == "bybit":
        # Bybit Unified Account has several balance fields:
        #   totalEquity           = wallet + unrealized PnL + USD value of every
        #                           non-USDT spot holding (BTC/ETH/etc counted as
        #                           collateral). Display-friendly "Total Equity".
        #   totalWalletBalance    = USDT free + locked margin (no unrealized PnL,
        #                           no spot-coin collateral). Stable across
        #                           position open/close shuffles.
        #   totalAvailableBalance = free USDT margin for new orders only.
        #
        # 2026-05-02 fix: this is a USER-FACING DISPLAY. Use totalWalletBalance
        # (USDT free + locked) so the dashboard shows the user's actual USDT,
        # not just spendable margin. With the previous priority
        # (totalAvailableBalance first), Bybit displayed ~$50 when actual
        # wallet was ~$207 — looked like balance had crashed every time a
        # position opened and locked margin.
        # Spot-coin holdings are shown separately in the SPOT panel — using
        # totalEquity here would double-count them.
        # bot_engine's `_extract_usdt` keeps the old priority for SIZING
        # (free-margin-conservative); dashboard uses the new priority for
        # DISPLAY (wallet-accurate).
        try:
            result_list = bal.get("info", {}).get("result", {}).get("list", [])
            if result_list and isinstance(result_list, list):
                acct = result_list[0] if result_list else {}
                # Display priority: wallet > margin > available
                for field in ("totalWalletBalance",
                              "totalMarginBalance",
                              "totalAvailableBalance"):
                    val = acct.get(field)
                    if val is not None:
                        try:
                            v = float(val)
                            if v > 0:
                                return v
                        except (TypeError, ValueError):
                            pass
                # Per-coin USDT walletBalance fallback
                coins = acct.get("coin", [])
                if isinstance(coins, list):
                    for c in coins:
                        if c.get("coin") == "USDT":
                            for f2 in ("walletBalance",
                                       "availableBalance",
                                       "availableToWithdraw"):
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
