"""Dashboard CLI entry point and background fetch thread."""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
from pathlib import Path

try:
    import shutil
except ImportError:
    shutil = None  # pragma: no cover

from loguru import logger

from dashboard.loaders import load_mode, load_positions
from dashboard.render import render
from dashboard import state
from dashboard.term import clr, enable_ansi

def background_fetch(fetcher, stop_event: threading.Event,
                     ready_event: threading.Event = None):
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

            state._BG_LAST_ERR = None
        except Exception as e:
            msg = str(e).strip().replace("\n", " ")[:200]
            state._BG_LAST_ERR = msg
            now = time.time()
            if msg != _last_logged_err or (now - _last_log_ts) >= 60.0:
                logger.warning("[Dashboard] Background fetch failed: {}", msg)
                _last_logged_err = msg
                _last_log_ts = now
        if ready_event and not ready_event.is_set():
            ready_event.set()  # Signal that first fetch is done
        stop_event.wait(state.REFRESH_SECONDS)


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
    p.add_argument(
        "--status-json",
        action="store_true",
        help="Print local machine-readable health/readiness status and exit.",
    )
    return p.parse_args()


def main():
    import dashboard as dash

    args = dash.parse_args()
    if getattr(args, "status_json", False):
        print(json.dumps(dash.build_health_status_payload(), sort_keys=True))
        return
    state.REFRESH_SECONDS = max(3, min(int(args.refresh), 3600))
    if args.width is not None:
        state.DASH_WIDTH = max(60, min(int(args.width), 200))
    else:
        try:
            if shutil:
                cols = shutil.get_terminal_size((100, 24)).columns
                state.DASH_WIDTH = max(60, min(cols, 120))
            else:
                state.DASH_WIDTH = 80
        except Exception:
            state.DASH_WIDTH = 80

    enable_ansi()
    tick       = 0
    fetcher    = dash.LiveFetcher()
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
            time.sleep(state.REFRESH_SECONDS)
        except KeyboardInterrupt:
            print("\n  Stopping dashboard...")
            stop_event.set()
            break
        except Exception as e:
            import traceback
            print("\n  Dashboard Error: " + str(e))
            traceback.print_exc()
            print("\n  Retrying in {}s...".format(state.REFRESH_SECONDS))
            time.sleep(state.REFRESH_SECONDS)
