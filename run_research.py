"""
run_research.py  --  Run the institutional crypto research engine

Usage:
    python run_research.py              # Full 8-framework report (BTC focus)
    python run_research.py --coin eth   # Focus on ETH for valuation + quant
    python run_research.py --open       # Open last report in browser

The report is saved to:  data/research/daily_report.html
"""

import sys
import argparse
import subprocess
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description="Institutional Crypto Research")
    parser.add_argument("--coin",  default="bitcoin",
                        help="CoinGecko ID for valuation focus (default: bitcoin)")
    parser.add_argument("--open",  action="store_true",
                        help="Open last saved report in browser")
    args = parser.parse_args()

    report_path = Path("data/research/daily_report.html")

    if args.open:
        if report_path.exists():
            print("\n  Opening report in browser...")
            if os.name == "nt":
                os.startfile(str(report_path.resolve()))
            else:
                subprocess.run(["xdg-open", str(report_path.resolve())])
        else:
            print("\n  No report found. Run without --open first.\n")
        return

    # Set up logging
    try:
        from utils.logger import setup_logger
        setup_logger()
    except ImportError:
        pass

    from loguru import logger
    logger.info("=" * 60)
    logger.info("  INSTITUTIONAL CRYPTO RESEARCH ENGINE")
    logger.info("  8 Frameworks | Free data | No API key needed")
    logger.info("=" * 60)

    from research.research_engine import ResearchEngine

    engine = ResearchEngine()
    report = engine.run(coin_id=args.coin)

    if report:
        print()
        print("  =====================================================")
        print("  Report generated successfully!")
        print("  =====================================================")
        print()
        print("  HTML Report: {}".format(report_path.resolve()))
        print("  JSON Data:   {}".format(
            Path("data/research/daily_report.json").resolve()))
        print()

        # Quick summary
        sc = report.get("screener", {})
        top3 = sc.get("top15", [])[:3]
        if top3:
            print("  TOP 3 PICKS:")
            for r in top3:
                print("    #{rank}  {symbol:<8}  ${price:>12,.4f}  "
                      "Moat: {moat:<10}  Bull 12M: ${bull_12m:,.2f}".format(**r))

        val = report.get("valuation", {})
        base = val.get("scenarios", {}).get("base", {})
        if base:
            print()
            print("  VALUATION: {} -- {}  ({:+.1f}% vs market)".format(
                val.get("coin","?"),
                base.get("verdict","?"),
                base.get("vs_market_pct", 0)))

        port = report.get("portfolio", {})
        print()
        print("  REGIME: {}".format(port.get("regime","?")))

        comp = report.get("competitive", {})
        bp   = comp.get("best_pick", {})
        if bp:
            print()
            print("  BEST PICK: {}  --  {}".format(
                bp.get("coin","?"), bp.get("entry","?")))

        print()
        print("  Opening report in browser...")
        try:
            if os.name == "nt":
                os.startfile(str(report_path.resolve()))
            else:
                subprocess.run(["xdg-open", str(report_path.resolve())])
        except Exception:
            print("  Could not auto-open. Open manually: {}".format(
                report_path.resolve()))
        print()
    else:
        print("\n  ERROR: Report generation failed. Check internet connection.\n")


if __name__ == "__main__":
    main()
