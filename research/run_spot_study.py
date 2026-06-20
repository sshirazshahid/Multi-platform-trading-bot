"""run_spot_study.py — reproducible multi-asset, multi-regime spot study.

Runs the DCA / lump-sum / rebalance labs on the committed ~3y BTC/ETH/SOL daily
fixtures with:
  * full-period comparison,
  * an in-sample (first 70%) vs out-of-sample (last 30%) split, and
  * a moving-block-bootstrap Monte-Carlo robustness band (1000 paths).

It is read-only and offline (uses research/sample_data/*.csv). Run:
    python research/run_spot_study.py

Honest framing (see research/finalized_trading_system_plan_2026_06_20.md §5/§6b):
single-history backtests, even with MC and an OOS split, are necessary but not
sufficient. DCA buys *risk reduction*, not return; in these bull-heavy windows
lump-sum wins on return while DCA wins on drawdown. Nothing here is a profit
promise, and the bot stays in PAPER.
"""

from __future__ import annotations

import sys
from pathlib import Path

# Make the repo root importable when launched as a bare script.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from research import data_io  # noqa: E402
from research import dca_rebalance_lab as lab  # noqa: E402

REPO = Path(__file__).resolve().parents[1]
SAMPLE = REPO / "research" / "sample_data"
ASSETS = {
    "BTC": SAMPLE / "btc_daily_fmp_2023_2026.csv",
    "ETH": SAMPLE / "eth_daily_fmp_2023_2026.csv",
    "SOL": SAMPLE / "sol_daily_fmp_2023_2026.csv",
}
CAPITAL = 1000.0
DCA_INTERVAL = 7  # weekly DCA on daily bars
MC_PATHS = 1000
MC_BLOCK = 20


def _line(tag: str, r) -> str:
    return (
        f"    {tag:<22} ret={r.total_return_pct:+8.1f}%  "
        f"maxDD={r.max_drawdown_pct:5.1f}%  Sharpe={r.sharpe:+5.2f}"
    )


def _mc_line(tag: str, mc: dict) -> str:
    rp, dd = mc["return_pct"], mc["max_drawdown_pct"]
    return (
        f"    {tag:<22} ret p5/p50/p95 = "
        f"{rp['p5']:+7.1f}/{rp['p50']:+7.1f}/{rp['p95']:+7.1f}%   "
        f"maxDD p50/p95 = {dd['p50']:.1f}/{dd['p95']:.1f}%"
    )


def study_asset(name: str, path: Path) -> None:
    if not path.exists():
        print(f"[{name}] fixture missing: {path}")
        return
    closes = data_io.load_closes_csv(path)
    split = int(len(closes) * 0.70)
    ins, oos = closes[:split], closes[split:]
    chg = (closes[-1] / closes[0] - 1) * 100
    print(f"\n{name}  [{len(closes)} daily bars, {chg:+.0f}% net]")

    print("  Full period:")
    print(_line("lump_sum", lab.simulate_lump_sum(closes, capital=CAPITAL)))
    print(
        _line("dca(weekly)", lab.simulate_dca(closes, capital=CAPITAL, interval_bars=DCA_INTERVAL))
    )

    print(f"  In-sample (first 70%, {len(ins)}d) vs Out-of-sample (last 30%, {len(oos)}d):")
    print(_line("dca IS", lab.simulate_dca(ins, capital=CAPITAL, interval_bars=DCA_INTERVAL)))
    print(_line("dca OOS", lab.simulate_dca(oos, capital=CAPITAL, interval_bars=DCA_INTERVAL)))

    print(f"  Monte-Carlo robustness ({MC_PATHS} block-bootstrap paths, block={MC_BLOCK}):")
    print(
        _mc_line(
            "lump_sum MC",
            lab.monte_carlo(
                closes, strategy="lump_sum", n_paths=MC_PATHS, block=MC_BLOCK, capital=CAPITAL
            ),
        )
    )
    print(
        _mc_line(
            "dca MC",
            lab.monte_carlo(
                closes,
                strategy="dca",
                n_paths=MC_PATHS,
                block=MC_BLOCK,
                capital=CAPITAL,
                interval_bars=DCA_INTERVAL,
            ),
        )
    )


def study_basket() -> None:
    series = {}
    for name, path in ASSETS.items():
        if path.exists():
            series[name] = data_io.load_closes_csv(path)
    if len(series) < 2:
        return
    n = min(len(v) for v in series.values())
    series = {k: v[-n:] for k, v in series.items()}
    w = round(1.0 / len(series), 6)
    tgt = {k: w for k in series}
    # fix rounding so weights sum to exactly 1.0
    first = next(iter(tgt))
    tgt[first] += 1.0 - sum(tgt.values())
    print(f"\nBasket {'/'.join(series)} equal-weight  [{n} bars] — hold vs rebalance(5%):")
    print(_line("buy_and_hold", lab.simulate_buy_and_hold_basket(series, tgt, capital=CAPITAL)))
    print(
        _line(
            "rebalance(5%)",
            lab.simulate_threshold_rebalance(series, tgt, capital=CAPITAL, threshold=0.05),
        )
    )


def main() -> None:
    print("=" * 72)
    print("SPOT STUDY — DCA / lump-sum / rebalance on real 3y BTC/ETH/SOL")
    print("=" * 72)
    for name, path in ASSETS.items():
        study_asset(name, path)
    study_basket()
    print("\nReminder: DCA = risk reduction, not return; bull windows favor lump-sum.")
    print("Single-history results — validate further before any real capital. PAPER only.")


if __name__ == "__main__":
    main()
