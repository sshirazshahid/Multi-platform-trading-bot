#!/usr/bin/env python3
"""Independent out-of-sample triangulation of the 8 textbook strategies from the
`backtesting-trading-strategies` skill, on crypto majors.

WHY: the repo has found NO_EDGE across ~17 in-house screens. This cross-checks that
verdict with a fully INDEPENDENT toolchain — different data source (yfinance daily
spot) and different code (the skill's own strategies.py + run_backtest) — and adds the
train/test discipline the skill's optimize.py lacks (it grid-searches and reports the
best in-sample Sharpe, i.e. overfit).

METHOD per (coin, strategy):
  1. IS window: grid-search params, pick best by IN-SAMPLE Sharpe (expected to look good).
  2. OOS window: run the WINNING params ONCE on held-out data -> after-cost OOS metrics.
  3. Benchmark: buy-and-hold OOS return for the same coin (a strategy must BEAT B&H, not
     merely be positive).
SURVIVOR = OOS Sharpe > 0.5 AND OOS return > B&H AND positive OOS return on >= 3 coins
(guards single-asset luck + the 8x5 multiple-comparison surface).

Costs: commission 0.1% + slippage 0.05% per side (the skill's defaults). Read-only research;
writes only to reports/. Does NOT touch the bot, .env, or data/.
"""

from __future__ import annotations

import dataclasses
import itertools
import json
import sys
from datetime import datetime
from pathlib import Path

SKILL = Path(r"C:/Users/SyedShirazShahid/.claude/skills/backtesting-trading-strategies/scripts")
sys.path.insert(0, str(SKILL))
from backtest import load_data, run_backtest  # noqa: E402

DATA_DIR = SKILL.parent / "data"
REPO = Path(__file__).resolve().parents[1]
OUT_MD = REPO / "reports" / "independent_oos_triangulation_2026-06-13.md"
OUT_JSON = REPO / "reports" / "independent_oos_triangulation_2026-06-13.json"

COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD"]
FULL_START = datetime(2021, 1, 1)
SPLIT = datetime(2024, 12, 31)  # IS: 2021-01..2024-12  | OOS: 2025-01..now
OOS_END = datetime(2026, 6, 13)
COMMISSION, SLIPPAGE, CAPITAL = 0.001, 0.0005, 10_000.0

GRIDS = {
    "sma_crossover": {"fast_period": [10, 20, 30, 50], "slow_period": [50, 100, 150, 200]},
    "ema_crossover": {"fast_period": [8, 12, 20, 30], "slow_period": [26, 50, 100, 150]},
    "rsi_reversal": {"period": [7, 14, 21], "overbought": [70, 75, 80], "oversold": [20, 25, 30]},
    "macd": {"fast": [8, 12], "slow": [21, 26], "signal": [7, 9]},
    "bollinger_bands": {"period": [14, 20, 30], "std_dev": [1.5, 2.0, 2.5]},
    "breakout": {"lookback": [20, 50, 100], "threshold": [0.0, 0.5, 1.0]},
    "mean_reversion": {"period": [14, 20, 30], "z_threshold": [1.0, 1.5, 2.0]},
    "momentum": {"period": [10, 20, 30], "threshold": [0.0, 2.0, 5.0]},
}


def _combos(grid):
    keys = list(grid)
    for vals in itertools.product(*[grid[k] for k in keys]):
        d = dict(zip(keys, vals))
        if "fast_period" in d and "slow_period" in d and d["fast_period"] >= d["slow_period"]:
            continue
        if "fast" in d and "slow" in d and d["fast"] >= d["slow"]:
            continue
        yield d


def _m(result, key, default=0.0):
    d = dataclasses.asdict(result)
    if key in d and d[key] is not None:
        return d[key]
    for alt in (key.replace("_ratio", ""), key.replace("num_", "total_")):
        if alt in d and d[alt] is not None:
            return d[alt]
    return default


def _bh_return(df) -> float:
    if len(df) < 2:
        return 0.0
    return (df["close"].iloc[-1] / df["close"].iloc[0] - 1.0) * 100.0


def main():
    results = []
    for coin in COINS:
        # Prime cache with full history (load_data fetches yfinance on first call).
        load_data(coin, FULL_START, OOS_END, DATA_DIR)
        is_df = load_data(coin, FULL_START, SPLIT, DATA_DIR)
        oos_df = load_data(coin, SPLIT, OOS_END, DATA_DIR)
        bh = _bh_return(oos_df)
        print(f"[{coin}] IS bars={len(is_df)} OOS bars={len(oos_df)} B&H_OOS={bh:+.1f}%")

        for strat, grid in GRIDS.items():
            # 1) in-sample grid search -> best by IS Sharpe
            best, best_sharpe = None, -1e9
            for params in _combos(grid):
                try:
                    r = run_backtest(strat, is_df, CAPITAL, params, COMMISSION, SLIPPAGE)
                except Exception:
                    continue
                s = _m(r, "sharpe_ratio")
                if s > best_sharpe:
                    best_sharpe, best = s, params
            if best is None:
                continue
            # 2) out-of-sample evaluation of the WINNING params (once)
            try:
                oos = run_backtest(strat, oos_df, CAPITAL, best, COMMISSION, SLIPPAGE)
            except Exception as e:
                print(f"  {strat:16s} OOS failed: {e}")
                continue
            row = {
                "coin": coin,
                "strategy": strat,
                "best_params": best,
                "is_sharpe": round(best_sharpe, 3),
                "oos_sharpe": round(_m(oos, "sharpe_ratio"), 3),
                "oos_return_pct": round(_m(oos, "total_return"), 2),
                "oos_max_dd_pct": round(_m(oos, "max_drawdown"), 2),
                "oos_win_rate": round(_m(oos, "win_rate"), 1),
                "oos_trades": int(_m(oos, "num_trades")),
                "bh_oos_return_pct": round(bh, 2),
                "beats_bh": _m(oos, "total_return") > bh,
            }
            results.append(row)
            print(
                f"  {strat:16s} IS_Sharpe={row['is_sharpe']:+.2f} -> "
                f"OOS_Sharpe={row['oos_sharpe']:+.2f} OOS_ret={row['oos_return_pct']:+.1f}% "
                f"(B&H {bh:+.1f}%) trades={row['oos_trades']} beats_bh={row['beats_bh']}"
            )

    # Survivor analysis: per strategy, count coins with positive OOS return that beat B&H.
    survivors = {}
    for strat in GRIDS:
        rows = [r for r in results if r["strategy"] == strat]
        pos_beat = [
            r for r in rows if r["oos_return_pct"] > 0 and r["beats_bh"] and r["oos_sharpe"] > 0.5
        ]
        survivors[strat] = {
            "coins_pos_beat_bh_sharpe_gt_0p5": len(pos_beat),
            "n_coins": len(rows),
            "survivor": len(pos_beat) >= 3,
            "mean_oos_sharpe": round(sum(r["oos_sharpe"] for r in rows) / max(1, len(rows)), 3),
            "mean_oos_return": round(sum(r["oos_return_pct"] for r in rows) / max(1, len(rows)), 2),
        }
    any_survivor = any(v["survivor"] for v in survivors.values())

    payload = {
        "generated": "2026-06-13",
        "method": "IS grid-search (best Sharpe) -> single OOS eval; survivor = OOS Sharpe>0.5 "
        "AND beats buy-and-hold AND positive on >=3 of 5 coins",
        "split": {"is": "2021-01-01..2024-12-31", "oos": "2025-01-01..2026-06-13"},
        "costs": {"commission": COMMISSION, "slippage": SLIPPAGE},
        "coins": COINS,
        "verdict": "EDGE_FOUND" if any_survivor else "NO_EDGE",
        "survivors": survivors,
        "results": results,
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    # numpy scalars (np.bool_/np.float64) aren't JSON-serializable — coerce via .item().
    OUT_JSON.write_text(
        json.dumps(payload, indent=2, default=lambda o: o.item() if hasattr(o, "item") else str(o)),
        encoding="utf-8",
    )

    lines = [
        "# Independent OOS Triangulation — Textbook Strategies on Crypto Majors",
        "",
        f"**Verdict: {payload['verdict']}**  | IS 2021-01..2024-12, OOS 2025-01..2026-06 | "
        f"costs {COMMISSION * 100:.1f}%+{SLIPPAGE * 100:.2f}%/side | independent toolchain (yfinance + skill strategies.py)",
        "",
        "## Per-strategy OOS summary (params chosen in-sample, evaluated once out-of-sample)",
        "",
        "| Strategy | mean OOS Sharpe | mean OOS ret% | coins beat B&H+Sharpe>0.5 | survivor |",
        "|---|---|---|---|---|",
    ]
    for s, v in survivors.items():
        lines.append(
            f"| {s} | {v['mean_oos_sharpe']:+.2f} | {v['mean_oos_return']:+.1f} | "
            f"{v['coins_pos_beat_bh_sharpe_gt_0p5']}/{v['n_coins']} | "
            f"{'YES' if v['survivor'] else 'no'} |"
        )
    lines += [
        "",
        "## Every (coin, strategy) — IS Sharpe vs OOS reality",
        "",
        "| Coin | Strategy | IS Sharpe | OOS Sharpe | OOS ret% | B&H% | beats B&H | trades |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        lines.append(
            f"| {r['coin']} | {r['strategy']} | {r['is_sharpe']:+.2f} | {r['oos_sharpe']:+.2f} | "
            f"{r['oos_return_pct']:+.1f} | {r['bh_oos_return_pct']:+.1f} | "
            f"{'Y' if r['beats_bh'] else 'n'} | {r['oos_trades']} |"
        )
    OUT_MD.parent.mkdir(parents=True, exist_ok=True)
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(
        f"\nVERDICT: {payload['verdict']}  (survivors: "
        f"{[s for s, v in survivors.items() if v['survivor']] or 'none'})"
    )
    print(f"Wrote {OUT_MD} + {OUT_JSON.name}")


if __name__ == "__main__":
    main()
