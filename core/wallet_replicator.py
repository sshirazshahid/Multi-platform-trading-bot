"""
core/wallet_replicator.py — Replicate Live Exchange Wallets → Dry Run Paper Wallets

FIX: Bybit Unified Account balance now correctly read from bal["total"]["USDT"]
     or bal["info"]["result"]["list"][0]["totalEquity"].
     Bybit is fetched ONCE (not twice for spot+futures) — it's a single account.
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

PROFILE_NAMES  = ("conservative", "moderate", "aggressive")
SNAPSHOT_FILE  = Path("data/wallet_snapshot.json")

# Exchanges whose spot + futures share one account — fetch balance once only
_UNIFIED_EXCHANGES = {"bybit"}


# ══════════════════════════════════════════════════════════════════════
# Balance extractor — handles all 4 exchange response formats
# ══════════════════════════════════════════════════════════════════════

def extract_usdt(bal: dict, exchange_name: str = "") -> float:
    """
    Extract total USDT from a ccxt fetch_balance() response.

    Bybit Unified Account:
        bal["USDT"]["total"]   or   bal["total"]["USDT"]
        NOTE: bal["USDT"]["free"] is 0 on Bybit — funds are in the unified pool.

    Bitget futures:
        bal["USDT"]["total"]   (free may be lower than actual equity)

    Binance (standard spot):
        bal["USDT"]["free"]    or   bal["free"]["USDT"]
    """
    if not bal:
        return 0.0

    ex = exchange_name.lower()

    # ── Bybit: MUST use "total" not "free" ────────────────────────────
    if ex == "bybit":
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

        # Raw Bybit v5 API fallback
        try:
            lst = bal.get("info", {}).get("result", {}).get("list", [{}])
            if lst:
                eq = lst[0].get("totalEquity") or lst[0].get("totalWalletBalance")
                if eq:
                    return float(eq)
        except Exception:
            pass
        return 0.0

    # ── Bitget: also prefers "total" ──────────────────────────────────
    if ex == "bitget":
        usdt = bal.get("USDT") or {}
        if isinstance(usdt, dict):
            for key in ("total", "free"):
                val = usdt.get(key)
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

        # Raw Bitget fallback
        try:
            data = bal.get("info", {}).get("data", [{}])
            if data:
                for field in ("usdtEquity", "available", "balance"):
                    val = data[0].get(field)
                    if val:
                        return float(val)
        except Exception:
            pass

        free_d = bal.get("free") or {}
        if isinstance(free_d, dict):
            val = free_d.get("USDT")
            if val:
                try:
                    return float(val)
                except (TypeError, ValueError):
                    pass
        return 0.0

    # ── Standard ccxt (Binance) ──────────────────────────────────────
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

    free_d = bal.get("free") or {}
    if isinstance(free_d, dict):
        val = free_d.get("USDT")
        if val:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    total_d = bal.get("total") or {}
    if isinstance(total_d, dict):
        val = total_d.get("USDT")
        if val:
            try:
                return float(val)
            except (TypeError, ValueError):
                pass

    return 0.0


# ══════════════════════════════════════════════════════════════════════
# Main entry point
# ══════════════════════════════════════════════════════════════════════

def replicate_live_to_dry(verbose: bool = True) -> dict:
    from exchanges import BinanceClient, BitgetClient, BybitClient

    clients = {
        "binance": BinanceClient(),
        "bybit":   BybitClient(),
        "bitget":  BitgetClient(),
    }
    active = {n: ex for n, ex in clients.items()
              if getattr(ex, "_connected", False)}

    if not active:
        logger.error("[WalletReplicator] No exchanges connected. Check API keys in .env")
        return {}

    if verbose:
        print("\n  ┌─────────────────────────────────────────────────────┐")
        print("  │  WALLET REPLICATOR  —  Scanning live balances...    │")
        print("  └─────────────────────────────────────────────────────┘\n")

    snapshot: dict[str, dict] = {}

    for ex_name, exchange in active.items():
        if verbose:
            unified_note = " (Unified Account)" if ex_name in _UNIFIED_EXCHANGES else ""
            print(f"  [{ex_name.upper()}]{unified_note} Fetching balances...")
        ex_snapshot = _scan_exchange(exchange, ex_name, verbose)
        snapshot[ex_name] = ex_snapshot

    if verbose:
        print()
        print("  ┌─────────────────────────────────────────────────────┐")
        print("  │  SUMMARY                                            │")
        print("  ├─────────────────────────────────────────────────────┤")
        total_all = 0.0
        for ex_name, snap in snapshot.items():
            total = snap.get("total_usdt", 0)
            total_all += total
            print(f"  │  {ex_name.upper():<10} ${total:>10.2f} USDT".ljust(55) + "│")
        print("  │                                                     │")
        print(f"  │  GRAND TOTAL  ${total_all:>10.2f} USDT".ljust(55) + "│")
        print("  └─────────────────────────────────────────────────────┘\n")

    _write_profile_wallets(snapshot, verbose)
    _save_snapshot(snapshot)

    if verbose:
        print("  ✓ DRY RUN wallets updated — all 3 profiles now mirror your live balance.")
        print("  ✓ Snapshot saved to: data/wallet_snapshot.json")
        print("  ✓ Start Multi-Profile Learning (Option M) to trade with replicated amounts.\n")

    return snapshot


# ══════════════════════════════════════════════════════════════════════
# Per-exchange scanner
# ══════════════════════════════════════════════════════════════════════

def _scan_exchange(exchange, ex_name: str, verbose: bool) -> dict:
    result = {
        "exchange":   ex_name,
        "total_usdt": 0.0,
        "spot_usdt":  0.0,
        "spot_coins": {},
        "futures_usdt": 0.0,
        "commodity_positions": {},
        "snapshot_at": datetime.now().isoformat(),
    }

    if ex_name in _UNIFIED_EXCHANGES:
        # ── Bybit: single Unified Account covers everything ────────────
        try:
            bal        = exchange.fetch_balance("spot")  # accountType=UNIFIED set in client
            total_usdt = extract_usdt(bal, ex_name)
            result["spot_usdt"]   = round(total_usdt, 4)
            result["futures_usdt"] = 0.0   # already included in unified total
            if verbose:
                print(f"    Unified total USDT: ${total_usdt:.4f}")
        except Exception as e:
            logger.debug(f"[WalletReplicator] {ex_name} unified balance: {e}")
    else:
        # ── Spot balance ───────────────────────────────────────────────
        try:
            spot_bal  = exchange.fetch_balance("spot")
            usdt_free = extract_usdt(spot_bal, ex_name)
            result["spot_usdt"] = round(usdt_free, 4)

            # Coin holdings → convert to USDT
            total_b   = spot_bal.get("total", {}) or {}
            skip      = {"USDT","USDC","BUSD","TUSD","DAI","FDUSD"}
            for coin, amount_raw in total_b.items():
                if coin in skip: continue
                try:
                    amount = float(amount_raw or 0)
                except (TypeError, ValueError):
                    continue
                if amount < 1e-8: continue
                price = _get_price(exchange, coin, verbose)
                if price <= 0: continue
                usdt_val = round(amount * price, 4)
                if usdt_val < 0.50: continue
                result["spot_coins"][coin] = {
                    "amount":     round(amount, 8),
                    "price":      round(price, 6),
                    "usdt_value": usdt_val,
                }
                if verbose:
                    print(f"    Spot  {coin:<8} {amount:>12.6f}  @ ${price:>12.4f}  = ${usdt_val:>10.2f}")
        except Exception as e:
            logger.debug(f"[WalletReplicator] {ex_name} spot balance: {e}")

        # ── Futures balance ────────────────────────────────────────────
        try:
            futures_bal = exchange.fetch_balance("futures")
            usdt_f      = extract_usdt(futures_bal, ex_name)
            result["futures_usdt"] = round(usdt_f, 4)
            if verbose and usdt_f > 0:
                print(f"    Futures USDT margin: ${usdt_f:.4f}")
        except Exception as e:
            logger.debug(f"[WalletReplicator] {ex_name} futures balance: {e}")

    # ── Commodity positions (all exchanges) ───────────────────────────
    for com_sym, com_label in _COMMODITY_FUTURES.get(ex_name, {}).items():
        try:
            pos_list = exchange.fetch_positions([com_sym])
            for pos in (pos_list or []):
                margin = float(pos.get("initialMargin") or pos.get("margin") or 0)
                size   = float(pos.get("contracts") or pos.get("size") or 0)
                if abs(size) > 0 or margin > 0:
                    result["commodity_positions"][com_label] = {
                        "symbol":       com_sym,
                        "size":         size,
                        "margin_usdt":  round(margin, 4),
                    }
                    if verbose:
                        print(f"    {com_label:<10} futures: size={size}  margin=${margin:.2f}")
        except Exception:
            pass

    # ── Total ─────────────────────────────────────────────────────────
    coin_total = sum(v["usdt_value"] for v in result["spot_coins"].values())
    com_total  = sum(v["margin_usdt"] for v in result["commodity_positions"].values())
    result["total_usdt"] = round(
        result["spot_usdt"] + coin_total + result["futures_usdt"] + com_total, 4)

    if verbose:
        print(f"    ─── {ex_name.upper()} total: ${result['total_usdt']:.4f} USDT\n")

    return result


def _get_price(exchange, coin: str, verbose: bool) -> float:
    symbol = f"{coin}/USDT"
    try:
        tick  = exchange.fetch_ticker(symbol, "spot")
        price = float(tick.get("last") or tick.get("close") or 0)
        if price > 0:
            return price
    except Exception:
        pass
    return 0.0


_COMMODITY_FUTURES = {
    "binance": {"XAU/USDT:USDT": "Gold", "XAG/USDT:USDT": "Silver"},
    "bybit":   {"XAU/USDT:USDT": "Gold", "XAG/USDT:USDT": "Silver"},
    "bitget":  {"XAUUSDT:USDT": "Gold",  "XAGUSDT:USDT": "Silver", "WTIUSDT:USDT": "Oil"},
}


# ══════════════════════════════════════════════════════════════════════
# Write profile wallets
# ══════════════════════════════════════════════════════════════════════

def _write_profile_wallets(snapshot: dict, verbose: bool):
    now = time.time()
    for prof_name in PROFILE_NAMES:
        wallet_path = Path(f"data/profiles/{prof_name}/wallet.json")
        wallet_path.parent.mkdir(parents=True, exist_ok=True)
        balances = {}
        for ex_name, snap in snapshot.items():
            total = snap.get("total_usdt", 0)
            if total > 0:
                balances[ex_name] = round(total, 4)
        wallet_data = {
            "balances":      balances,
            "start":         round(sum(balances.values()), 4) if balances else 100.0,
            "saved_at":      now,
            "replicated_at": datetime.now().isoformat(),
            "source":        "live_wallet_replication",
        }
        wallet_path.write_text(json.dumps(wallet_data, indent=2), encoding="utf-8")
        if verbose:
            total_bal = sum(balances.values())
            print(f"  ✓ {prof_name.upper():<14} wallet set to ${total_bal:.2f} USDT")


def _save_snapshot(snapshot: dict):
    try:
        SNAPSHOT_FILE.parent.mkdir(parents=True, exist_ok=True)
        SNAPSHOT_FILE.write_text(
            json.dumps({
                "snapshot_at": datetime.now().isoformat(),
                "exchanges":   snapshot,
                "grand_total": round(sum(s.get("total_usdt",0) for s in snapshot.values()), 4),
            }, indent=2), encoding="utf-8")
    except Exception as e:
        logger.debug(f"[WalletReplicator] Save snapshot: {e}")


# ══════════════════════════════════════════════════════════════════════
# Show last snapshot
# ══════════════════════════════════════════════════════════════════════

def show_snapshot():
    if not SNAPSHOT_FILE.exists():
        print("\n  No wallet snapshot yet. Run Option [L] to replicate.\n")
        return
    try:
        data = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
        ts   = data.get("snapshot_at", "?")[:19]
        print(f"\n  Wallet Snapshot  ({ts})")
        print("  " + "─" * 52)
        for ex_name, snap in data.get("exchanges", {}).items():
            total = snap.get("total_usdt", 0)
            print(f"  {ex_name.upper():<12} ${total:>10.4f} USDT")
            for coin, cd in snap.get("spot_coins", {}).items():
                print(f"    {coin:<8} {cd['amount']:>12.6f}  = ${cd['usdt_value']:>8.2f}")
            for com, cmd in snap.get("commodity_positions", {}).items():
                print(f"    {com:<8} futures margin ${cmd['margin_usdt']:>8.2f}")
        print(f"\n  Grand Total: ${data.get('grand_total',0):>10.4f} USDT\n")
    except Exception as e:
        print(f"\n  Error reading snapshot: {e}\n")


if __name__ == "__main__":
    import sys
    if "--show" in sys.argv:
        show_snapshot()
    else:
        replicate_live_to_dry(verbose=True)
