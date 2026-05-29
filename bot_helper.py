"""
bot_helper.py — Helper script called by TradingBot.bat for all Python tasks.
"""

import sys
import re
import json
from pathlib import Path
from datetime import date, datetime


def _merge_positions():
    all_open   = []
    all_closed = []
    seen_ids   = set()

    def _ingest(path_str):
        try:
            p = Path(path_str)
            if not p.exists():
                return
            d = json.loads(p.read_text(encoding="utf-8"))
            for pos in d.get("open", []):
                pid = pos.get("id", "")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    all_open.append(pos)
            for pos in d.get("closed", []):
                pid = pos.get("id", "")
                if pid not in seen_ids:
                    seen_ids.add(pid)
                    all_closed.append(pos)
        except Exception:
            pass

    _ingest("data/positions.json")
    _ingest("data/profiles/conservative/positions.json")
    _ingest("data/profiles/moderate/positions.json")
    _ingest("data/profiles/aggressive/positions.json")

    return all_open, all_closed


def snapshot():
    all_open, all_closed = _merge_positions()

    if not all_closed and not all_open:
        print("  No trades recorded yet.")
        return

    try:
        today     = date.today().isoformat()
        today_c   = [
            t for t in all_closed
            if t.get("close_time") and
            datetime.fromtimestamp(t["close_time"]).date().isoformat() == today
        ]
        today_pnl = sum(t.get("pnl", 0) or 0 for t in today_c)
        total_pnl = sum(t.get("pnl", 0) or 0 for t in all_closed)
        wins      = sum(1 for t in all_closed if (t.get("pnl", 0) or 0) > 0)
        total     = len(all_closed)
        wr        = (wins / total * 100) if total > 0 else 0
        ts        = "+" if today_pnl >= 0 else ""
        tt        = "+" if total_pnl >= 0 else ""
        print(f"  Today   : {ts}{today_pnl:.4f} USDT  ({len(today_c)} trades today)")
        print(f"  Total   : {tt}{total_pnl:.4f} USDT  |  Win Rate: {wr:.0f}%  |  Trades: {total}")
        print(f"  Open    : {len(all_open)} position(s) active")
        tm = _read_env_value("TRADING_MODE", "usdt_only")
        print(f"  Mode    : {tm.upper()}")

        # Show wallet replication status if available
        snap_file = Path("data/wallet_snapshot.json")
        if snap_file.exists():
            snap = json.loads(snap_file.read_text(encoding="utf-8"))
            ts_r = snap.get("snapshot_at", "")[:16]
            tot  = snap.get("grand_total", 0)
            print(f"  Wallet  : Replicated ${tot:.2f} USDT from live  ({ts_r})")
    except Exception as e:
        print(f"  (Could not load stats: {e})")


def replicate_wallet():
    """Snapshot live exchange balances and write them to all 3 DRY RUN profile wallets."""
    try:
        from core.wallet_replicator import replicate_live_to_dry
        result = replicate_live_to_dry(verbose=True)
        if not result:
            print("\n  No balances found. Check your API keys and exchange connections.")
    except Exception as e:
        print(f"\n  Replication failed: {e}")
        import traceback
        traceback.print_exc()


def show_wallet_snapshot():
    """Print the last wallet snapshot without running a new scan."""
    try:
        from core.wallet_replicator import show_snapshot
        show_snapshot()
    except Exception as e:
        print(f"\n  Error: {e}")


def _read_env_value(key: str, default: str = "") -> str:
    env = Path(".env")
    if not env.exists():
        return default
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line.startswith(f"{key}="):
            return line.split("=", 1)[1].strip()
    return default


def update_env(key, value):
    env = Path(".env")
    if env.exists():
        content = env.read_text(encoding="utf-8")
        if re.search(rf"^{re.escape(key)}=", content, re.M):
            content = re.sub(rf"^{re.escape(key)}=.*", f"{key}={value}",
                             content, flags=re.M)
        else:
            content += f"\n{key}={value}"
    else:
        content = f"{key}={value}\n"
    env.write_text(content, encoding="utf-8")
    print(f"  [OK] {key} updated.")


def set_env_var(key: str, value: str):
    update_env(key, value)


def set_dry(value: str):
    update_env("DRY_RUN", value.lower())
    mode = "DRY RUN (paper trading)" if value.lower() == "true" else "LIVE TRADING"
    print(f"  [OK] Mode set to: {mode}")


def toggle_dry():
    current = _read_env_value("DRY_RUN", "true").lower()
    new_val = "false" if current == "true" else "true"
    update_env("DRY_RUN", new_val)
    mode = "DRY RUN" if new_val == "true" else "LIVE TRADING"
    print(f"  [OK] Mode switched to: {mode}")


def set_trading_mode(mode: str):
    mode = mode.lower().strip()
    if mode not in ("usdt_only", "portfolio", "all"):
        print(f"  ERROR: Unknown mode '{mode}'. Use: usdt_only, portfolio, or all")
        return
    update_env("TRADING_MODE", mode)
    if mode == "portfolio":
        print("  [OK] Trading mode set to: PORTFOLIO")
    elif mode == "all":
        print("  [OK] Trading mode set to: ALL")
        print("       Scans ALL available markets: Crypto + Gold + Silver + Oil + Stocks")
        print("       Uses every strategy: Scalping + Trend + Grid + Futures + Spot")
    else:
        print("  [OK] Trading mode set to: USDT ONLY")


def show_trading_mode():
    mode    = _read_env_value("TRADING_MODE", "usdt_only")
    dry_run = _read_env_value("DRY_RUN", "true")
    print()
    print(f"  Current trading mode : {mode.upper()}")
    print(f"  Current run mode     : {'DRY RUN' if dry_run == 'true' else 'LIVE TRADING'}")


def scan_portfolio():
    try:
        from exchanges import BinanceClient, BybitClient, BitgetClient
        import portfolio_scanner as ps

        exchanges = {}
        for name, cls in [
            ("binance", BinanceClient),
            ("bybit",   BybitClient),
            ("bitget",  BitgetClient),
        ]:
            ex = cls()
            if getattr(ex, "_connected", False):
                exchanges[name] = ex

        if not exchanges:
            print("  No exchanges connected. Check your API keys.")
            return

        print()
        print("  Scanning your portfolio...")
        print()
        pairs = ps.build_portfolio_pairs(exchanges)
        print()
        for ex_name, type_dict in pairs.items():
            print(f"  {ex_name.upper()}:")
            print(f"    Spot pairs    : {type_dict['spot']}")
            print(f"    Futures pairs : {type_dict['futures']}")
        print()
    except Exception as e:
        print(f"  Scan failed: {e}")


def apply_risk(pct, pos, dl, sl, tp, lev):
    cfg = Path("config.py")
    if not cfg.exists():
        print("  ERROR: config.py not found.")
        return
    c = cfg.read_text(encoding="utf-8")
    c = re.sub(r'"max_position_pct":\s*[\d.]+',   f'"max_position_pct":     {pct}', c)
    c = re.sub(r'"max_open_positions":\s*\d+',     f'"max_open_positions":   {pos}', c)
    c = re.sub(r'"max_daily_loss_pct":\s*[\d.]+',  f'"max_daily_loss_pct":   {dl}',  c)
    c = re.sub(r'"default_stop_loss":\s*[\d.]+',   f'"default_stop_loss":    {sl}',  c)
    c = re.sub(r'"default_take_profit":\s*[\d.]+', f'"default_take_profit":  {tp}',  c)
    c = re.sub(r'"default_leverage":\s*\d+',       f'"default_leverage":     {lev}', c)
    cfg.write_text(c, encoding="utf-8")
    print("  [OK] Risk profile applied to config.py")


def update_keys(exchange: str, api_key: str, secret: str, passphrase: str = ""):
    ex = exchange.upper()
    update_env(f"{ex}_API_KEY", api_key)
    update_env(f"{ex}_SECRET_KEY", secret)
    if passphrase:
        update_env(f"{ex}_PASSPHRASE", passphrase)
    print(f"  [OK] {ex} keys updated.")


def update_email(sender: str, password: str, recipient: str):
    update_env("GMAIL_SENDER", sender)
    update_env("GMAIL_APP_PASSWORD", password)
    update_env("GMAIL_RECIPIENT", recipient)
    print("  [OK] Email settings updated.")


def write_env(binance_key, binance_sec, binance_testnet,
              gmail_sender, gmail_pass, gmail_recv, dry_run):
    lines = [
        "# Trading Bot Configuration",
        "",
        "# Binance",
        f"BINANCE_API_KEY={binance_key}",
        f"BINANCE_SECRET_KEY={binance_sec}",
        f"BINANCE_TESTNET={binance_testnet}",
        "",
        "# Bybit",
        "BYBIT_API_KEY=your_bybit_api_key_here",
        "BYBIT_SECRET_KEY=your_bybit_secret_key_here",
        "",
        "# Bitget  (requires passphrase in addition to key+secret)",
        "BITGET_API_KEY=your_bitget_api_key_here",
        "BITGET_SECRET_KEY=your_bitget_secret_key_here",
        "BITGET_PASSPHRASE=your_bitget_passphrase_here",
        "",
        "# Email Notifications",
        f"GMAIL_SENDER={gmail_sender}",
        f"GMAIL_APP_PASSWORD={gmail_pass}",
        f"GMAIL_RECIPIENT={gmail_recv}",
        "EMAIL_SUBJECT_PREFIX=[TradingBot]",
        "",
        "# General",
        f"DRY_RUN={dry_run}",
        "LOG_LEVEL=INFO",
        "",
        "# Trading Mode: usdt_only or portfolio",
        "TRADING_MODE=usdt_only",
        "PORTFOLIO_MIN_VALUE_USD=2.0",
        "PORTFOLIO_RESCAN_MINUTES=60",
        "",
        "# Paper wallet: $100 per exchange per profile (or use Option L to replicate live)",
        "DRY_RUN_BALANCE=100.0",
        "",
    ]
    Path(".env").write_text("\n".join(lines), encoding="utf-8")
    print("  [OK] .env file written.")


def fix_ghost_positions():
    """Remove ghost/phantom positions with invalid symbols from all position files.
    Handles both formats: {"open":[], "closed":[]} and flat list []."""
    bot_dir = Path(__file__).parent
    data_dir = bot_dir / "data"
    fixed = 0
    scanned = 0

    def _is_valid(pos):
        """A position is valid if it has a symbol and entry_price."""
        return (isinstance(pos, dict)
                and pos.get("symbol")
                and pos.get("entry_price"))

    def _clean_file(fpath: Path):
        nonlocal fixed, scanned
        if not fpath.exists():
            return
        scanned += 1
        try:
            data = json.loads(fpath.read_text(encoding="utf-8"))
        except Exception as e:
            print(f"  Error reading {fpath.name}: {e}")
            return

        changed = False
        if isinstance(data, dict):
            # Format: {"open": [...], "closed": [...]}
            for key in ("open", "closed"):
                items = data.get(key, [])
                if not isinstance(items, list):
                    continue
                before = len(items)
                clean = [p for p in items if _is_valid(p)]
                if len(clean) < before:
                    data[key] = clean
                    removed = before - len(clean)
                    fixed += removed
                    changed = True
                    print(f"  {fpath.name} [{key}]: removed {removed} ghost positions")
        elif isinstance(data, list):
            # Format: flat list of positions
            before = len(data)
            data = [p for p in data if _is_valid(p)]
            if len(data) < before:
                removed = before - len(data)
                fixed += removed
                changed = True
                print(f"  {fpath.name}: removed {removed} ghost positions")

        if changed:
            fpath.write_text(json.dumps(data, indent=2), encoding="utf-8")

    # Scan root positions.json
    _clean_file(data_dir / "positions.json")

    # Scan all profile positions
    profiles_dir = data_dir / "profiles"
    if profiles_dir.exists():
        for pos_file in profiles_dir.rglob("positions.json"):
            _clean_file(pos_file)

    print(f"\n  Scanned {scanned} position files.")
    if fixed == 0:
        print("  No ghost positions found. All clean!")
    else:
        print(f"  Removed {fixed} total ghost positions.")


def test_connection():
    try:
        import ccxt
    except ImportError:
        print("  ccxt not installed yet.")
        sys.exit(0)

    results = []
    for name, cls_name in [
        ("Binance",    "binance"),
        ("Bybit",      "bybit"),
        ("Bitget",     "bitget"),
    ]:
        try:
            ex    = getattr(ccxt, cls_name)({"enableRateLimit": True})
            price = ex.fetch_ticker("BTC/USDT").get("last", 0)
            print(f"  [OK]   {name:<10} BTC/USDT = {price:,.2f} USDT")
        except Exception as e:
            print(f"  [FAIL] {name:<10} {str(e)[:70]}")
            results.append(1)
    sys.exit(1 if results else 0)


# ── Dispatch ──────────────────────────────────────────────────────────
if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""

    if cmd == "snapshot":
        snapshot()
    elif cmd == "replicate_wallet":
        replicate_wallet()
    elif cmd == "show_wallet_snapshot":
        show_wallet_snapshot()
    elif cmd == "apply_risk":
        apply_risk(*sys.argv[2:8])
    elif cmd == "update_env":
        update_env(sys.argv[2], sys.argv[3])
    elif cmd == "set_dry":
        set_dry(sys.argv[2])
    elif cmd == "toggle_dry":
        toggle_dry()
    elif cmd == "set_trading_mode":
        set_trading_mode(sys.argv[2])
    elif cmd == "show_trading_mode":
        show_trading_mode()
    elif cmd == "scan_portfolio":
        scan_portfolio()
    elif cmd == "update_keys":
        args = sys.argv[2:]
        update_keys(*args[:4]) if len(args) >= 4 else update_keys(*args[:3])
    elif cmd == "update_email":
        update_email(sys.argv[2], sys.argv[3], sys.argv[4])
    elif cmd == "write_env":
        args_w = sys.argv[2:12]
        if len(args_w) < 10:
            print(f"  ERROR: write_env requires 10 arguments, got {len(args_w)}")
            sys.exit(1)
        write_env(*args_w)
    elif cmd == "fix_ghosts":
        fix_ghost_positions()
    elif cmd == "test_connection":
        test_connection()
    else:
        print(f"  Unknown command: {cmd}")
        sys.exit(1)
