"""
multi_profile_main.py  --  Entry point for Multi-Profile + Claude AI trading

Usage:
    python multi_profile_main.py            # Run all 3 profiles
    python multi_profile_main.py --report   # Print comparison report
    python multi_profile_main.py --test     # Test Claude API key
"""
import sys
import argparse


def print_report():
    import json
    from pathlib import Path
    p = Path("data/profiles/comparison.json")
    if not p.exists():
        print("\n  No data yet. Run: python multi_profile_main.py\n")
        return
    comp = json.loads(p.read_text(encoding="utf-8"))
    ranked = comp.get("ranked", [])
    leader = comp.get("leader", "?")
    print()
    print("  MULTI-PROFILE COMPARISON  --  Scan #{}  {}".format(
        comp.get("scan_count", 0), comp.get("updated_at", "")[:16]))
    print()
    print("  {:<14} {:>8} {:>8} {:>6} {:>9} {:>6} {:>6} {:>6} {:>6}".format(
        "Profile", "Balance", "Return", "WR%", "PnL", "DD%",
        "Longs", "Shorts", "Score"))
    print("  " + "-" * 78)
    for name in ranked:
        d = comp["profiles"].get(name, {})
        flag = " << LEADER" if name == leader else ""
        print("  {:<14} {:>8.2f} {:>+7.2f}% {:>5.1f}% {:>+9.4f} {:>5.1f}% "
              "{:>5} {:>6} {:>6.1f}{}".format(
            name.upper(),
            d.get("balance", 0), d.get("return_pct", 0),
            d.get("win_rate", 0), d.get("net_pnl", 0),
            d.get("max_drawdown", 0),
            d.get("longs", 0), d.get("shorts", 0),
            d.get("score", 0), flag))
    print()
    print("  " + comp.get("recommendation", ""))
    print()
    claude = comp.get("claude_summary", "")
    if claude:
        print("  Claude: " + claude)
    print()


def test_claude_key():
    import os, urllib.request, urllib.error, json
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass

    key = os.getenv("ANTHROPIC_API_KEY", "").strip()
    if not key:
        try:
            from pathlib import Path
            for line in Path(".env").read_text(encoding="utf-8").splitlines():
                if line.strip().startswith("ANTHROPIC_API_KEY="):
                    key = line.split("=", 1)[1].strip().strip('"').strip("'")
                    break
        except Exception:
            pass

    if not key:
        print("\n  ERROR: ANTHROPIC_API_KEY not set in .env")
        print("  Add: ANTHROPIC_API_KEY=sk-ant-...  to your .env file")
        print("  Get a key at: https://console.anthropic.com\n")
        return

    print("\n  Key: {}...{}".format(key[:14], key[-4:]))
    print("  Testing...")
    try:
        payload = json.dumps({
            "model": "claude-sonnet-4-20250514",
            "max_tokens": 20,
            "messages": [{"role": "user", "content": "Reply: OK"}],
        }).encode()
        req = urllib.request.Request(
            "https://api.anthropic.com/v1/messages",
            data=payload,
            headers={"Content-Type": "application/json",
                     "x-api-key": key,
                     "anthropic-version": "2023-06-01"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = json.loads(resp.read())
        text = "".join(b.get("text","") for b in body.get("content",[])
                       if b.get("type")=="text").strip()
        print("  Response: {}".format(text))
        print("  SUCCESS -- API key works!\n")
    except urllib.error.HTTPError as e:
        msg = e.read().decode("utf-8", errors="replace")
        print("  FAILED -- HTTP {}: {}".format(e.code, msg[:200]))
        if e.code == 401:
            print("  Key is invalid or expired. Get a new one at console.anthropic.com")
    except Exception as e:
        print("  FAILED -- {}".format(e))
    print()


def main():
    from utils.logger import setup_logger
    setup_logger()

    parser = argparse.ArgumentParser(description="Multi-Profile + Claude AI")
    parser.add_argument("--report", action="store_true", help="Print comparison report")
    parser.add_argument("--test",   action="store_true", help="Test Claude API key")
    args = parser.parse_args()

    if args.report:
        print_report()
        return
    if args.test:
        test_claude_key()
        return

    # Normal run -- import runner and start
    from core.multi_profile_runner import MultiProfileRunner
    runner = MultiProfileRunner()
    runner.run()


if __name__ == "__main__":
    main()
