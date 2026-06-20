#!/usr/bin/env python3
"""Phase-1 validation gate for the TSMOM redesign (plan_tsmom_redesign_2026-06-15.md).

Tests the research's central claim: a LONG-ONLY time-series-momentum strategy (28-day lookback,
5-day hold, vol-targeted, no short leg) delivers BETTER RISK-ADJUSTED returns than buy-and-hold on
liquid crypto majors, via downside reduction (sit in cash during downtrends), AFTER realistic costs.

Method per coin: daily bars; signal = 28-day momentum > 0 (price today > price 28d ago) -> target long,
else flat; rebalance every 5 days; position scaled by vol-targeting (target 60% annual vol, capped 1.0x);
costs 0.1% commission + 0.05% slippage applied on every position-size change. Split IS 2021-01..2024-12,
OOS 2025-01..2026-06. Benchmark: buy-and-hold each coin over OOS.

GO criterion: OOS Sharpe(TSMOM) > OOS Sharpe(B&H) on >=3 of 5 coins AND TSMOM OOS Sharpe > 0.
Read-only research; writes only to reports/. Reuses the yfinance cache from the 2026-06-13 triangulation.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

SKILL = Path(r"C:/Users/SyedShirazShahid/.claude/skills/backtesting-trading-strategies/scripts")
sys.path.insert(0, str(SKILL))
from backtest import load_data  # noqa: E402  (reuses cached CSVs / yfinance fetch)

DATA_DIR = SKILL.parent / "data"
REPO = Path(__file__).resolve().parents[1]
OUT_MD = REPO / "reports" / "tsmom_validation_2026-06-15.md"
OUT_JSON = REPO / "reports" / "tsmom_validation_2026-06-15.json"

COINS = ["BTC-USD", "ETH-USD", "SOL-USD", "BNB-USD", "XRP-USD"]
FULL_START, SPLIT, OOS_END = datetime(2021, 1, 1), datetime(2024, 12, 31), datetime(2026, 6, 15)
LOOKBACK, HOLD = 28, 5
TARGET_ANN_VOL, MAX_LEV = 0.60, 1.0
COST = 0.001 + 0.0005  # per position-size change (round-trip ~ 2x)
ANN = 365.0  # crypto trades daily


def _sharpe(rets: pd.Series) -> float:
    r = rets.dropna()
    if len(r) < 2 or r.std() == 0:
        return 0.0
    return float(r.mean() / r.std() * np.sqrt(ANN))


def _cagr(eq: pd.Series) -> float:
    if len(eq) < 2 or eq.iloc[0] <= 0:
        return 0.0
    yrs = len(eq) / ANN
    return float((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) if yrs > 0 else 0.0


def _maxdd(eq: pd.Series) -> float:
    peak = eq.cummax()
    return float(((eq - peak) / peak).min())


def tsmom_positions(df: pd.DataFrame) -> pd.Series:
    """Long-only TSMOM target weight in [0, MAX_LEV], rebalanced every HOLD days."""
    close = df["close"]
    mom = close > close.shift(LOOKBACK)  # 28d momentum positive -> eligible long
    realized = close.pct_change().rolling(20).std() * np.sqrt(ANN)
    volscale = (TARGET_ANN_VOL / realized).clip(upper=MAX_LEV).fillna(0.0)
    raw = (mom.astype(float) * volscale).fillna(0.0)
    # Rebalance only every HOLD days: hold the weight set on the rebalance bar.
    w = raw.copy()
    last = 0.0
    for i in range(len(w)):
        if i % HOLD == 0:
            last = raw.iloc[i]
        w.iloc[i] = last
    return w.clip(lower=0.0, upper=MAX_LEV)


def run_coin(df: pd.DataFrame) -> dict:
    df = df.sort_index()
    ret = df["close"].pct_change().fillna(0.0)
    w = tsmom_positions(df)
    # strategy return = yesterday's weight * today's return, minus cost on weight change
    turn = w.diff().abs().fillna(w.abs())
    strat_ret = w.shift(1).fillna(0.0) * ret - turn * COST
    eq = (1 + strat_ret).cumprod()
    bh_eq = (1 + ret).cumprod()
    return {
        "tsmom_sharpe": round(_sharpe(strat_ret), 3),
        "tsmom_cagr": round(_cagr(eq) * 100, 1),
        "tsmom_maxdd": round(_maxdd(eq) * 100, 1),
        "bh_sharpe": round(_sharpe(ret), 3),
        "bh_cagr": round(_cagr(bh_eq) * 100, 1),
        "bh_maxdd": round(_maxdd(bh_eq) * 100, 1),
        "exposure_pct": round(float((w > 0).mean()) * 100, 1),
        "n_bars": len(df),
    }


def main():
    rows = []
    for coin in COINS:
        load_data(coin, FULL_START, OOS_END, DATA_DIR)  # prime cache
        is_df = load_data(coin, FULL_START, SPLIT, DATA_DIR)
        oos_df = load_data(coin, SPLIT, OOS_END, DATA_DIR)
        is_r = run_coin(is_df)
        oos_r = run_coin(oos_df)
        beats = oos_r["tsmom_sharpe"] > oos_r["bh_sharpe"]
        rows.append(
            {
                "coin": coin,
                "is": is_r,
                "oos": oos_r,
                "oos_beats_bh_sharpe": bool(beats),
                "oos_tsmom_sharpe_pos": bool(oos_r["tsmom_sharpe"] > 0),
            }
        )
        print(
            f"[{coin}] IS Sharpe TSMOM {is_r['tsmom_sharpe']:+.2f} / B&H {is_r['bh_sharpe']:+.2f} | "
            f"OOS Sharpe TSMOM {oos_r['tsmom_sharpe']:+.2f} / B&H {oos_r['bh_sharpe']:+.2f} "
            f"(ret {oos_r['tsmom_cagr']:+.0f}% vs {oos_r['bh_cagr']:+.0f}%, DD {oos_r['tsmom_maxdd']:.0f}% "
            f"vs {oos_r['bh_maxdd']:.0f}%, exp {oos_r['exposure_pct']:.0f}%) beats_bh={beats}"
        )

    n_beat = sum(r["oos_beats_bh_sharpe"] and r["oos_tsmom_sharpe_pos"] for r in rows)
    go = n_beat >= 3
    verdict = "GO" if go else "NO_GO"
    payload = {
        "generated": "2026-06-15",
        "strategy": "long-only TSMOM 28d/5d vol-targeted",
        "go_criterion": "OOS Sharpe > B&H Sharpe AND OOS Sharpe > 0 on >=3 of 5 coins",
        "coins_passing": n_beat,
        "verdict": verdict,
        "rows": rows,
        "costs": {"per_change": COST},
        "split": {"is": "2021..2024", "oos": "2025..2026-06"},
    }
    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")

    lines = [
        "# TSMOM Validation Gate — long-only 28d/5d, vol-targeted, after-cost",
        "",
        f"**Verdict: {verdict}** ({n_beat}/5 coins: OOS Sharpe > buy-and-hold AND positive). "
        f"GO needs >=3. IS 2021-2024, OOS 2025-2026-06. Costs {COST * 100:.2f}%/change. yfinance daily majors.",
        "",
        "| Coin | OOS Sharpe TSMOM | OOS Sharpe B&H | OOS ret% TSMOM | OOS ret% B&H | OOS DD TSMOM | OOS DD B&H | exposure% | beats? |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in rows:
        o = r["oos"]
        lines.append(
            f"| {r['coin']} | {o['tsmom_sharpe']:+.2f} | {o['bh_sharpe']:+.2f} | "
            f"{o['tsmom_cagr']:+.0f} | {o['bh_cagr']:+.0f} | {o['tsmom_maxdd']:.0f} | {o['bh_maxdd']:.0f} | "
            f"{o['exposure_pct']:.0f} | {'YES' if (r['oos_beats_bh_sharpe'] and r['oos_tsmom_sharpe_pos']) else 'no'} |"
        )
    lines += [
        "",
        f"**{'GO - proceed to Phase 2 (wire behind a flag, PAPER).' if go else 'NO_GO - long-only TSMOM does not clear the gate on these majors after costs. Do NOT rewire the engine; keep the current PAPER bot. The design does not survive validation here either.'}**",
    ]
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"\nVERDICT: {verdict} ({n_beat}/5). Wrote {OUT_MD.name}")


if __name__ == "__main__":
    main()
