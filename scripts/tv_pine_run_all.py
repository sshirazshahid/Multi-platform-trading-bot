#!/usr/bin/env python3
"""Run all bot-mirror Pine scripts via Brave/Edge CDP + parse Strategy Tester text.

Writes data/tv_pine_sim_latest.json (gitignored). Does not authorize live trades.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PINES = ROOT / "research" / "pine_scripts"
OUT = ROOT / "data" / "tv_pine_sim_latest.json"

METRIC_PATTERNS = {
    "net_profit": re.compile(r"Net profit[^\d\-+]*([+\-]?[\d,]+\.?\d*)", re.I),
    "profit_factor": re.compile(r"Profit factor[^\d]*([\d.]+)", re.I),
    "total_trades": re.compile(r"Total (?:closed )?trades[^\d]*([\d,]+)", re.I),
    "percent_profitable": re.compile(r"Percent profitable[^\d]*([\d.]+)", re.I),
    "max_drawdown": re.compile(r"Max(?:imum)?(?: equity)? drawdown[^\d]*([\d.]+)", re.I),
}


def _parse_metrics(text: str) -> dict:
    out: dict = {}
    for key, pat in METRIC_PATTERNS.items():
        m = pat.search(text)
        if m:
            raw = m.group(1).replace(",", "")
            try:
                out[key] = float(raw)
            except ValueError:
                out[key] = raw
    out["signed_in"] = "sign in" not in text.lower() and "look first" not in text.lower()
    out["has_strategy_tester"] = "strategy tester" in text.lower() or "net profit" in text.lower()
    out["compile_error"] = bool(re.search(r"\b(error|line \d+)", text, re.I))
    return out


def run_one(page, pine_path: Path, *, symbol: str) -> dict:
    source = pine_path.read_text(encoding="utf-8")
    stem = pine_path.stem
    report: dict = {"script": pine_path.name, "stem": stem, "symbol": symbol}

    try:
        if symbol and "tradingview.com" in page.url:
            page.goto(
                f"https://www.tradingview.com/chart/?symbol={symbol}",
                wait_until="domcontentloaded",
                timeout=30_000,
            )
            time.sleep(3)
    except Exception as exc:
        report["nav_error"] = str(exc)

    try:
        page.locator("text=Pine Editor").first.click(timeout=5000)
    except Exception:
        page.keyboard.press("Alt+E")
    time.sleep(2)
    # Bottom panel tabs vary — force editor tab
    for sel in ("text=Pine Editor", "[data-name='pine-editor']", "button:has-text('Pine Editor')"):
        try:
            page.locator(sel).first.click(timeout=2000)
            break
        except Exception:
            continue
    time.sleep(1)

    injected = page.evaluate(
        """(text) => {
            if (window.monaco && monaco.editor.getModels().length) {
                monaco.editor.getModels()[0].setValue(text);
                return 'monaco';
            }
            return null;
        }""",
        source,
    )
    report["inject"] = injected
    time.sleep(1)

    added = False
    for sel in (
        "button:has-text('Add to chart')",
        "[data-name='add-to-chart']",
        "text=Add to chart",
    ):
        try:
            page.locator(sel).first.click(timeout=3000)
            added = True
            break
        except Exception:
            continue
    if not added:
        page.keyboard.press("Control+Enter")
    time.sleep(5)

    page.keyboard.press("Escape")
    time.sleep(0.5)
    try:
        page.locator("[data-name='resolution']").first.click(timeout=3000)
        time.sleep(0.5)
        page.get_by_text("4 hours", exact=False).first.click(timeout=3000)
        report["timeframe"] = "4h"
    except Exception:
        report["timeframe"] = "unknown"

    time.sleep(3)
    try:
        page.locator("text=Strategy Tester").first.click(timeout=5000)
    except Exception:
        pass
    time.sleep(4)

    text = page.evaluate("() => document.body.innerText || ''")
    ui_path = PINES / f"_tv_{stem}_ui.txt"
    ui_path.write_text(text[:12000], encoding="utf-8", errors="replace")
    report["metrics"] = _parse_metrics(text)
    report["ui_excerpt"] = text[:1500]
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    ap.add_argument("--symbol", default="BYBIT:BTCUSDT.P")
    ap.add_argument("--scripts", nargs="*", default=None, help="Pine filenames (default: all *.pine)")
    args = ap.parse_args()

    scripts = args.scripts or sorted(p.name for p in PINES.glob("*.pine"))
    payload = {
        "ts_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "cdp": args.cdp,
        "symbol": args.symbol,
        "live_trade_authorized": False,
        "honesty": "TV cross-check only. Not a pipeline GO. Funding not modeled.",
        "results": [],
    }

    try:
        with sync_playwright() as p:
            browser = p.chromium.connect_over_cdp(args.cdp)
            ctx = browser.contexts[0]
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.set_default_timeout(20_000)
            for name in scripts:
                path = PINES / name
                if not path.is_file():
                    payload["results"].append({"script": name, "error": "missing"})
                    continue
                print(f"running {name}...", flush=True)
                payload["results"].append(run_one(page, path, symbol=args.symbol))
    except Exception as exc:
        payload["connect_error"] = str(exc)

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(payload, indent=2))
    return 0 if not payload.get("connect_error") else 1


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
