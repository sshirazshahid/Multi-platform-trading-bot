"""One-shot: inject Pine into Brave TradingView, Add to chart, capture tester.

Does NOT close Brave (CDP disconnect only).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).resolve().parents[1]
PINES = ROOT / "research" / "pine_scripts"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--script", default="zfade_4h_cfg365_v1.pine")
    ap.add_argument("--cdp", default="http://127.0.0.1:9222")
    args = ap.parse_args()
    pine_path = PINES / args.script
    source = pine_path.read_text(encoding="utf-8")
    stem = pine_path.stem

    with sync_playwright() as p:
        browser = p.chromium.connect_over_cdp(args.cdp)
        ctx = browser.contexts[0]
        page = ctx.pages[0]
        page.set_default_timeout(15_000)
        time.sleep(3)

        # Open Pine Editor via bottom bar text
        try:
            page.locator("text=Pine Editor").first.click(timeout=5000)
        except Exception:
            page.keyboard.press("Alt+E")
        time.sleep(2)

        # Inject into Monaco
        page.evaluate(
            """(text) => {
                if (window.monaco && monaco.editor.getModels().length) {
                    monaco.editor.getModels()[0].setValue(text);
                    return 'monaco';
                }
                const ta = document.querySelector('.monaco-editor textarea');
                if (!ta) return null;
                ta.focus();
                const proto = window.HTMLTextAreaElement.prototype;
                Object.getOwnPropertyDescriptor(proto, 'value').set.call(ta, text);
                ta.dispatchEvent(new Event('input', { bubbles: true }));
                return 'textarea';
            }""",
            source,
        )
        time.sleep(1)
        page.screenshot(path=str(PINES / f"_tv_{stem}_editor.png"))

        # Add to chart — try several UI variants
        added = False
        for sel in (
            "button:has-text('Add to chart')",
            "[data-name='add-to-chart']",
            "div[role='button']:has-text('Add to chart')",
            "text=Add to chart",
        ):
            try:
                page.locator(sel).first.click(timeout=3000)
                added = True
                print(f"add via {sel}", flush=True)
                break
            except Exception:
                continue
        if not added:
            # Keyboard: Ctrl+Enter often adds/applies in Pine Editor
            page.keyboard.press("Control+Enter")
            print("add via Ctrl+Enter", flush=True)
        time.sleep(5)

        # Switch timeframe: open interval dialog with digit shortcuts
        page.keyboard.press("Escape")
        time.sleep(0.5)
        try:
            page.locator("[data-name='resolution']").first.click(timeout=3000)
            time.sleep(0.5)
            page.get_by_text("4 hours", exact=False).first.click(timeout=3000)
            print("tf 4h via menu", flush=True)
        except Exception:
            try:
                page.locator("text=1D").first.click(timeout=2000)
                page.get_by_text("4h", exact=True).first.click(timeout=2000)
                print("tf 4h via 1D", flush=True)
            except Exception as exc:
                print(f"tf fail {exc}", flush=True)

        time.sleep(3)
        try:
            page.locator("text=Strategy Tester").first.click(timeout=5000)
            print("opened Strategy Tester", flush=True)
        except Exception as exc:
            print(f"tester fail {exc}", flush=True)

        time.sleep(4)
        page.screenshot(path=str(PINES / f"_tv_{stem}_tester.png"))
        text = page.evaluate("() => document.body.innerText || ''")
        (PINES / f"_tv_{stem}_ui.txt").write_text(text[:8000], encoding="utf-8", errors="replace")
        # Grep key metrics
        for key in (
            "Net profit",
            "Profit factor",
            "Total trades",
            "Max equity drawdown",
            "Percent profitable",
            "BotMirror",
            "Zfade",
            "error",
            "Error",
        ):
            if key.lower() in text.lower():
                print(f"HIT:{key}", flush=True)
        print("done", flush=True)
        # Disconnect without killing Brave
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    raise SystemExit(main())
