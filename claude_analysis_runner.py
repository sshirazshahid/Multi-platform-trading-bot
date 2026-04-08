"""
claude_analysis_runner.py — Run / View Claude's real-time market analysis

Usage:
    python claude_analysis_runner.py           # Run analysis right now
    python claude_analysis_runner.py --show    # Show last saved analysis
    python claude_analysis_runner.py --history # Show last 5 analyses
    python claude_analysis_runner.py --test    # Test API key (no real analysis)
"""

import sys
import json
from pathlib import Path


def test_key():
    """Quick test that the API key works."""
    import os
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        # Try reading .env manually
        try:
            for line in Path(".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY"):
                    parts = line.split("=", 1)
                    if len(parts) == 2:
                        key = parts[1].strip().strip('"').strip("'")
                        break
        except Exception:
            pass

    if not key:
        print("\n  ERROR: ANTHROPIC_API_KEY not found")
        print("  Add this line to your .env file:")
        print("    ANTHROPIC_API_KEY=sk-ant-...your key...")
        print("  Get a key at: https://console.anthropic.com/\n")
        return

    print("\n  Key found: {}...{}".format(key[:12], key[-4:]))
    print("  Testing API call...")

    import urllib.request, urllib.error
    try:
        payload = json.dumps({
            "model":      "claude-sonnet-4-20250514",
            "max_tokens": 50,
            "messages":   [{"role": "user", "content": "Reply with: OK"}],
        }).encode()

        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={
                "Content-Type":      "application/json",
                "x-api-key":         key,
                "anthropic-version": "2023-06-01",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        text = "".join(
            b.get("text","") for b in body.get("content",[])
            if b.get("type") == "text"
        )
        print("  API response: {}\n".format(text.strip()))
        print("  SUCCESS — API key is valid and working!\n")

    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print("  FAILED — HTTP {}: {}".format(e.code, body[:200]))
        if e.code == 401:
            print("  The key is invalid or expired.")
    except Exception as e:
        print("  FAILED — {}\n".format(e))


def show_current():
    p = Path("data/claude_analysis.json")
    if not p.exists():
        print("\n  No analysis yet. Run without --show to generate one.\n")
        return

    a      = json.loads(p.read_text(encoding="utf-8"))
    ts     = a.get("generated_at","")[:16]
    bias   = a.get("market_bias","?").upper()
    rm     = a.get("risk_multiplier", 1.0)
    note   = a.get("market_note","")
    best   = a.get("best_trade","?")
    avoid  = ", ".join(a.get("avoid",[]))
    advice = a.get("advice","")
    call_n = a.get("call_number", 0)

    print()
    print("  ====================================================")
    print("  CLAUDE REAL-TIME ANALYSIS  —  Call #{}  {}".format(call_n, ts))
    print("  ====================================================")
    print()
    print("  Bias     : {}".format(bias))
    print("  Risk Mult: {:.2f}x".format(rm))
    print("  Market   : {}".format(note))
    print("  Best Now : {}".format(best))
    print("  Avoid    : {}".format(avoid or "none"))
    print("  Advice   : {}".format(advice))
    print()
    print("  {:<8}  {:<18}  {:>7}  {}".format(
        "Coin", "Action", "ConfAdj", "Reason"))
    print("  " + "-" * 70)
    for coin, rec in a.get("coins",{}).items():
        action = rec.get("action","skip")
        cadj   = rec.get("confidence_adj",1.0)
        reason = rec.get("reason","")
        print("  {:<8}  {:<18}  {:>6.2f}x  {}".format(
            coin, action.replace("_"," ").upper(), cadj, reason[:50]))
    print()


def show_history():
    p = Path("data/claude_analysis_history.json")
    if not p.exists():
        print("\n  No history yet.\n")
        return
    history = json.loads(p.read_text(encoding="utf-8"))
    print("\n  Last {} analyses:".format(len(history[-5:])))
    for a in history[-5:]:
        ts   = a.get("generated_at","")[:16]
        bias = a.get("market_bias","?").upper()
        rm   = a.get("risk_multiplier",1.0)
        n    = a.get("call_number", 0)
        best = a.get("best_trade","?")
        print("  #{:3d}  {}  {}  rm={:.2f}x  best={}".format(
            n, ts, bias, rm, best))
    print()


def run_now():
    """Run a standalone Claude analysis with minimal market context."""
    from utils.logger import setup_logger
    setup_logger()
    from loguru import logger
    from core.claude_analyst import ClaudeAnalyst

    analyst = ClaudeAnalyst()

    if not analyst.is_configured():
        print("\n  Cannot run — ANTHROPIC_API_KEY not set in .env\n")
        print("  Run: python claude_analysis_runner.py --test")
        print("  to diagnose the key issue.\n")
        return

    print("\n  Calling Claude API for standalone analysis...")
    print("  (No live market data — Claude will use general knowledge)\n")

    analyst.analyze_now({}, {}, {}, [], )

    print("  Done!")
    show_current()
    html = Path("data/claude_analysis.html")
    if html.exists():
        print("  HTML report: {}\n".format(html.resolve()))


if __name__ == "__main__":
    args = sys.argv[1:]
    if "--test"    in args: test_key()
    elif "--show"    in args: show_current()
    elif "--history" in args: show_history()
    else:                     run_now()
