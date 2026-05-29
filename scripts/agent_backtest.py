"""Agent backtester — replay warehouse OHLCV through every shadow agent.

Reads either:
  - data/ohlcv_cache/<exchange>/<symbol>/<tf>.json (preferred), or
  - falls back to fetching via active exchange clients

For each historical bar window, builds the multi-timeframe context, calls
each agent's propose(), simulates exit at TP/SL/time-bar, and aggregates
per-agent stats.

Output: reports/agent_backtest_<date>.md
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@dataclass
class BacktestStats:
    agent: str
    n: int = 0
    wins: int = 0
    losses: int = 0
    sum_pnl: float = 0.0
    sum_fees: float = 0.0
    best: float = 0.0
    worst: float = 0.0


def _load_ohlcv_cache(exchange: str, symbol: str, tf: str) -> Optional[pd.DataFrame]:
    safe_sym = symbol.replace("/", "_").replace(":", "_")
    p = ROOT / "data" / "ohlcv_cache" / exchange / safe_sym / f"{tf}.json"
    if not p.exists():
        return None
    try:
        bars = json.loads(p.read_text(encoding="utf-8"))
        if not bars:
            return None
        df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
        return df
    except Exception:
        return None


def _build_ctx_at(t: int, symbol: str,
                  d_5m: pd.DataFrame, d_15m: pd.DataFrame,
                  d_1h: pd.DataFrame, d_4h: pd.DataFrame) -> Optional[dict]:
    """Slice each TF up to time t (UNIX ms), return ctx dict.
    All TFs must have at least the lookback bars before t.
    """
    def _slice(df, n_min):
        if df is None:
            return None
        sub = df[df["timestamp"] <= t]
        if len(sub) < n_min:
            return None
        return sub
    s5 = _slice(d_5m, 80)
    s15 = _slice(d_15m, 40)
    s1h = _slice(d_1h, 80)
    s4h = _slice(d_4h, 40)
    if any(x is None for x in (s5, s15, s1h, s4h)):
        return None
    return {"symbol": symbol, "ohlcv_5m": s5, "ohlcv_15m": s15,
            "ohlcv_1h": s1h, "ohlcv_4h": s4h}


def _simulate_exit(proposal, future_close: pd.Series,
                   max_bars: int = 24) -> tuple[float, str]:
    """Walk forward until TP, SL, or max_bars hit. Return (pnl_pct, reason)."""
    side = proposal.side
    entry, sl, tp = proposal.entry, proposal.sl, proposal.tp
    bars = future_close.iloc[1:max_bars + 1] if len(future_close) > 1 else future_close
    for c in bars:
        if side == "buy":
            if c >= tp:
                return (tp - entry) / entry, "tp"
            if c <= sl:
                return (sl - entry) / entry, "sl"
        else:
            if c <= tp:
                return (entry - tp) / entry, "tp"
            if c >= sl:
                return (entry - sl) / entry, "sl"
    last = bars.iloc[-1] if len(bars) > 0 else entry
    if side == "buy":
        pnl_pct = (last - entry) / entry
    else:
        pnl_pct = (entry - last) / entry
    return pnl_pct, "time"


def backtest_symbol(
    symbol: str,
    exchange: str = "binance",
    notional_usdt: float = 200.0,
    fee_bps_per_side: float = 6.0,
    step: int = 12,  # evaluate every N bars on 1h grid
) -> dict[str, BacktestStats]:
    """Run all 5 shadow agents over historical OHLCV for a single symbol."""
    from core.agents.liquidity_agent import LiquidityAgent
    from core.agents.mean_reversion_agent import MeanReversionAgent
    from core.agents.pattern_agent import PatternAgent
    from core.agents.scalp_agent import ScalpAgent
    from core.agents.trend_agent import TrendAgent

    d_5m = _load_ohlcv_cache(exchange, symbol, "5m")
    d_15m = _load_ohlcv_cache(exchange, symbol, "15m")
    d_1h = _load_ohlcv_cache(exchange, symbol, "1h")
    d_4h = _load_ohlcv_cache(exchange, symbol, "4h")

    if d_1h is None or len(d_1h) < 200:
        return {}

    agents = [TrendAgent(), ScalpAgent(), MeanReversionAgent(),
              PatternAgent(), LiquidityAgent()]
    stats: dict[str, BacktestStats] = {a.name: BacktestStats(agent=a.name) for a in agents}

    # Walk the 1h grid (cheapest to iterate); for each evaluation point
    # build a trimmed multi-TF context and let agents propose.
    timestamps = list(d_1h["timestamp"].iloc[100:].iloc[::step].astype(int))
    for t in timestamps:
        ctx = _build_ctx_at(t, symbol, d_5m, d_15m, d_1h, d_4h)
        if ctx is None:
            continue
        # Future closes from 1h forward
        future = d_1h[d_1h["timestamp"] > t]["close"].iloc[:25]
        if len(future) < 5:
            continue
        for a in agents:
            try:
                p = a.propose(ctx)
            except Exception:
                continue
            if p is None:
                continue
            pnl_pct, reason = _simulate_exit(p, future)
            notional_usdt / max(p.entry, 1e-9)
            gross = pnl_pct * notional_usdt
            fees = 2 * notional_usdt * fee_bps_per_side / 10_000.0
            net = gross - fees
            st = stats[a.name]
            st.n += 1
            if net > 0:
                st.wins += 1
            else:
                st.losses += 1
            st.sum_pnl += net
            st.sum_fees += fees
            st.best = max(st.best, net)
            st.worst = min(st.worst, net)
    return stats


def render_report(all_stats: dict[str, dict[str, BacktestStats]]) -> str:
    lines: list[str] = ["# Agent backtest report", ""]
    lines.append(f"_generated {time.strftime('%Y-%m-%d %H:%M:%S UTC', time.gmtime())}_")
    lines.append("")
    lines.append(f"Symbols evaluated: {len(all_stats)}")
    lines.append("")
    aggregated: dict[str, BacktestStats] = {}
    for sym, sym_stats in all_stats.items():
        for agent, st in sym_stats.items():
            agg = aggregated.setdefault(agent, BacktestStats(agent=agent))
            agg.n += st.n
            agg.wins += st.wins
            agg.losses += st.losses
            agg.sum_pnl += st.sum_pnl
            agg.sum_fees += st.sum_fees
            agg.best = max(agg.best, st.best)
            agg.worst = min(agg.worst, st.worst)
    lines.append("## Aggregated by agent")
    lines.append("")
    lines.append("| Agent | n | wins | WR | sum PnL | mean | best | worst |")
    lines.append("|---|---:|---:|---:|---:|---:|---:|---:|")
    for agent in sorted(aggregated):
        a = aggregated[agent]
        wr = a.wins / a.n if a.n else 0.0
        mean = a.sum_pnl / a.n if a.n else 0.0
        lines.append(f"| {agent} | {a.n} | {a.wins} | {wr:.1%} | "
                     f"${a.sum_pnl:+.2f} | ${mean:+.3f} | ${a.best:+.2f} | ${a.worst:+.2f} |")
    lines.append("")
    lines.append("## Per-symbol breakdown")
    lines.append("")
    for sym in sorted(all_stats):
        lines.append(f"### {sym}")
        lines.append("")
        sym_stats = all_stats[sym]
        for agent in sorted(sym_stats):
            st = sym_stats[agent]
            wr = st.wins / st.n if st.n else 0.0
            lines.append(f"- **{agent}**: n={st.n} sum=${st.sum_pnl:+.2f} WR={wr:.1%}")
        lines.append("")
    return "\n".join(lines)


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="*",
                    default=["BTC/USDT:USDT", "ETH/USDT:USDT"])
    ap.add_argument("--exchange", default="binance")
    ap.add_argument("--notional", type=float, default=200.0)
    ap.add_argument("--out-dir", default="reports")
    ns = ap.parse_args(argv)

    out_dir = Path(ns.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    all_stats: dict[str, dict[str, BacktestStats]] = {}
    for sym in ns.symbols:
        s = backtest_symbol(sym, exchange=ns.exchange, notional_usdt=ns.notional)
        if s:
            all_stats[sym] = s
            print(f"  {sym}: " + ", ".join(
                f"{name}={st.n}" for name, st in s.items() if st.n > 0
            ))

    if not all_stats:
        print("WARNING: no symbols had usable cache. Run the bot first to populate "
              "data/ohlcv_cache/, or implement live fetch fallback.")
        return 1

    report = render_report(all_stats)
    out = out_dir / f"agent_backtest_{time.strftime('%Y%m%d')}.md"
    out.write_text(report, encoding="utf-8")
    print(f"\nwrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
