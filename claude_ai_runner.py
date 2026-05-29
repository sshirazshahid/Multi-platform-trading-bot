"""
claude_ai_runner.py — Standalone Claude AI runner called by TradingBot.bat [E]

Uses live Claude API when ANTHROPIC_API_KEY is set and uncommented.
Falls back to embedded framework (free, zero cost) otherwise.
"""

import json
import sys
import os
from pathlib import Path
from datetime import datetime


def _call_live(news: dict, snapshots: dict) -> dict | None:
    """Call Claude (CLI-first, API-fallback). Returns parsed result or None."""
    from utils.claude_client import call_claude, strip_markdown_fences

    fg   = (news.get("fear_greed") or {}).get("value", 50)
    chg  = (news.get("global") or {}).get("market_cap_change_24h", 0)
    dom  = (news.get("global") or {}).get("btc_dominance", 50)
    news_heads = "; ".join(
        a.get("title","")[:60] for a in (news.get("news") or [])[:3])

    coin_list = ", ".join(snapshots.keys()) if snapshots else "BTC,ETH,BNB,SOL,XRP"

    prompt = (
        "You are a crypto trading risk manager. Analyse the market and give "
        "per-coin trading decisions in JSON format.\n\n"
        "Market data:\n"
        "  Fear & Greed: {fg}/100\n"
        "  Market cap change 24h: {chg:+.1f}%\n"
        "  BTC dominance: {dom:.1f}%\n"
        "  News: {news}\n\n"
        "Coins to analyse: {coins}\n\n"
        "Respond ONLY with a JSON object (no markdown, no extra text):\n"
        "{{\n"
        '  "market_bias": "bullish|bearish|neutral",\n'
        '  "market_note": "one sentence",\n'
        '  "risk_multiplier": 0.8..1.3,\n'
        '  "best_trade": "COIN ACTION",\n'
        '  "advice": "one sentence",\n'
        '  "coins": {{\n'
        '    "BTC": {{"action":"long_spot|long_futures|short_futures|skip|evaluate",'
        '"confidence_adj":0.5..1.5,"reason":"brief"}},\n'
        '    ...\n'
        '  }}\n'
        '}}'
    ).format(fg=fg, chg=chg, dom=dom, news=news_heads or "none", coins=coin_list)

    text = call_claude(
        prompt,
        model="sonnet",
        api_model="claude-opus-4-7",
        max_tokens=800,
        timeout=60,
    )
    if not text:
        return None

    try:
        text = strip_markdown_fences(text)
        return json.loads(text)
    except Exception as e:
        print("  Claude response parse error: {}".format(e))
        return None


def run_analysis():
    """Run Claude AI analysis and print formatted report to terminal."""
    # Try to enable ANSI colours on Windows
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass

    BOLD   = "\033[1m"
    CYAN   = "\033[96m"
    GREEN  = "\033[92m"
    RED    = "\033[91m"
    YELLOW = "\033[93m"
    DIM    = "\033[2m"
    RESET  = "\033[0m"

    print()
    print("  {}Running Claude AI analysis...{}".format(CYAN, RESET))
    print()

    # Load cached data
    news = {}
    try:
        p = Path("data/news_cache.json")
        if p.exists():
            news = json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        pass

    snapshots = {}
    try:
        p = Path("data/claude_analysis.json")
        if p.exists():
            old = json.loads(p.read_text(encoding="utf-8"))
            for coin in old.get("coins", {}):
                snapshots[coin + "/USDT"] = {}
    except Exception:
        pass

    # Try Claude Code CLI first, then API fallback
    from utils.claude_client import is_available
    result  = None
    source  = "embedded"

    if is_available():
        print("  Calling Claude (CLI-first, API-fallback)...")
        live = _call_live(news, snapshots)
        if live and live.get("coins"):
            live["source"]       = "claude"
            live["generated_at"] = datetime.now().isoformat()
            live["call_number"]  = (
                json.loads(Path("data/claude_analysis.json").read_text(
                    encoding="utf-8")).get("call_number", 0) + 1
                if Path("data/claude_analysis.json").exists() else 1)
            result = live
            source = "api"
            print("  {}Live Claude response received.{}".format(GREEN, RESET))
        else:
            print("  {}Claude call failed — using embedded framework.{}".format(
                YELLOW, RESET))

    if result is None:
        # Embedded fallback
        from core.claude_analyst import ClaudeAnalyst
        analyst = ClaudeAnalyst()
        result  = analyst.analyze_now(snapshots, news, {}, [])
        source  = result.get("source", "embedded")

    # Save result
    try:
        Path("data").mkdir(exist_ok=True)
        Path("data/claude_analysis.json").write_text(
            json.dumps(result, indent=2, default=str), encoding="utf-8")
    except Exception as e:
        print("  Warning: could not save analysis: {}".format(e))

    # Print formatted report
    bias   = result.get("market_bias", "neutral").upper()
    rm     = result.get("risk_multiplier", 1.0)
    note   = result.get("market_note", "")
    best   = result.get("best_trade", "none")
    avoid  = result.get("avoid", [])
    adv    = result.get("advice", "")
    ts     = result.get("generated_at", "")[:19]
    fg     = (news.get("fear_greed") or {}).get("value", "?")
    chg    = (news.get("global") or {}).get("market_cap_change_24h", 0)

    bc  = GREEN if bias == "BULLISH" else (RED if bias == "BEARISH" else YELLOW)
    rmc = GREEN if rm <= 1.0 else (YELLOW if rm <= 1.2 else RED)

    print()
    print("  " + "=" * 62)
    print("  {}CLAUDE AI ANALYSIS  —  {}{}".format(BOLD, source.upper(), RESET))
    print("  " + "=" * 62)
    print("  Time   : {}{}{}".format(DIM, ts, RESET))
    print("  Source : {}{}{}".format(
        GREEN if source == "claude" else DIM,
        "CLAUDE (CLI/API)" if source == "claude" else "EMBEDDED FRAMEWORK (free)",
        RESET))
    print("  Bias   : {}{}{}".format(bc, bias, RESET))
    print("  Risk x : {}{:.2f}{}".format(rmc, rm, RESET))
    print("  F&G    : {}  Mkt chg: {:+.1f}%".format(fg, chg))
    if note:
        print("  Market : {}{}{}".format(DIM, note, RESET))
    print()
    print("  Best   : {}{}{}".format(GREEN, best, RESET))
    if avoid:
        print("  Avoid  : {}".format(", ".join(avoid)))
    print()
    if adv:
        print("  Advice:")
        words = adv.split(); line = "    "
        for w in words:
            if len(line) + len(w) + 1 > 64:
                print("{}{}{}".format(DIM, line, RESET)); line = "    " + w + " "
            else:
                line += w + " "
        if line.strip():
            print("{}{}{}".format(DIM, line, RESET))
    print()

    print("  {:<8}  {:<16}  {:>6}  {}".format("Coin","Action","Adj","Reason"))
    print("  " + "-" * 58)

    ALC = {
        "long_spot":     GREEN,
        "long_futures":  GREEN,
        "short_futures": RED,
        "skip":          DIM,
        "evaluate":      YELLOW,
    }
    ALL = {
        "long_spot":     "BUY  SPOT     ",
        "long_futures":  "LONG FUTURES  ",
        "short_futures": "SHORT FUTURES ",
        "skip":          "SKIP          ",
        "evaluate":      "EVALUATE      ",
    }
    for coin, rec in (result.get("coins") or {}).items():
        action = rec.get("action", "skip")
        cadj   = rec.get("confidence_adj", 1.0)
        reason = str(rec.get("reason", ""))[:40]
        ac     = ALC.get(action, DIM)
        label  = ALL.get(action, action.upper()[:14])
        cc     = GREEN if cadj >= 1.1 else (RED if cadj <= 0.7 else "")
        print("  {:<8}  {}{}{}  {}{:.2f}x{}  {}{}{}".format(
            coin, ac, label, RESET,
            cc, cadj, RESET,
            DIM, reason, RESET))

    print()
    print("  " + "=" * 62)
    print("  Report saved: data/claude_analysis.json")
    if source == "embedded":
        print()
        print("  {}NOTE:{} Using embedded Claude framework (free, no API cost).".format(
            YELLOW, RESET))
        print("  To use live Claude: install Claude Code CLI, or set ANTHROPIC_API_KEY in .env")
    print()


def show_report():
    p = Path("data/claude_analysis.json")
    if not p.exists():
        print("\n  No analysis yet. Run Option [E] to generate one.\n")
        return
    try:
        a = json.loads(p.read_text(encoding="utf-8"))
        ts   = a.get("generated_at","?")[:19]
        bias = a.get("market_bias","?").upper()
        rm   = a.get("risk_multiplier", 1.0)
        note = a.get("market_note","")
        best = a.get("best_trade","none")
        src  = a.get("source","embedded").upper()
        print("\n  Last Claude Analysis  [{}]  {}".format(src, ts))
        print("  Bias: {}   Risk x{:.2f}".format(bias, rm))
        if note: print("  {}".format(note))
        print("  Best: {}".format(best))
        print()
        for coin, rec in (a.get("coins") or {}).items():
            action = rec.get("action","skip")
            cadj   = rec.get("confidence_adj", 1.0)
            reason = str(rec.get("reason",""))[:50]
            print("  {:<8} {:<16} adj={:.2f}x  {}".format(
                coin, action.upper(), cadj, reason))
        print()
    except Exception as e:
        print("\n  Error reading report: {}\n".format(e))


def set_key():
    print()
    print("  Enter your Anthropic API key (starts with sk-ant-)")
    print("  Get one at: https://console.anthropic.com")
    print()
    try:
        key = input("  API Key: ").strip()
    except (EOFError, KeyboardInterrupt):
        print("\n  Cancelled.\n")
        return

    if not key.startswith("sk-ant-"):
        print("\n  Invalid key format. Key must start with 'sk-ant-'\n")
        return

    env_path = Path(".env")
    if env_path.exists():
        import re
        content = env_path.read_text(encoding="utf-8")
        content = re.sub(r"#?\s*ANTHROPIC_API_KEY=.*\n?", "", content)
        content = content.rstrip() + "\nANTHROPIC_API_KEY={}\n".format(key)
    else:
        content = "ANTHROPIC_API_KEY={}\n".format(key)

    env_path.write_text(content, encoding="utf-8")
    print("\n  [OK] API key saved to .env")
    print("  The bot will use live Claude AI on the next run.")
    print("  Make sure the account has credits at console.anthropic.com\n")


if __name__ == "__main__":
    if "--report" in sys.argv or "--view" in sys.argv:
        show_report()
    elif "--setkey" in sys.argv:
        set_key()
    else:
        run_analysis()
