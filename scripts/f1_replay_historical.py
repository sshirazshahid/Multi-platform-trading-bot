"""F1 carry — HISTORICAL replay over real harvested funding data (research-only).

Replays the Rev-5 F1 delta-neutral carry entry/exit rules over the actual
Binance funding history in ``data/funding_oi/<SYM>_funding.csv`` and measures
the per-cycle win rate, profit factor and net carry — the honest, empirical
answer to "what accuracy does this strategy demonstrate?".

STATED ASSUMPTIONS (flagged in the report; the PAPER runner measures the rest):
- Basis PnL is assumed 0 (delta-neutral; basis drift/exits are only measurable
  live/paper). Cost-stress rows below bound the sensitivity instead.
- Costs are pessimistic taker/taker both legs + 4 slippage crossings, exactly
  like the paper runner (core.cost_model + DEFAULT_SLIP_FRAC).
- Entry forecast uses the trailing 7d average funding (21 settlements), the
  same regime signal as f1_entry_gate's avg_funding_7d check.

No orders, no exchange calls, fully deterministic. CLI:
    venv/Scripts/python.exe scripts/f1_replay_historical.py
"""
from __future__ import annotations

import csv
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.carry_runner import DEFAULT_SLIP_FRAC  # noqa: E402
from core.cost_model import round_trip_cost  # noqa: E402
from research.funding_carry_lab import (  # noqa: E402
    F1_COST_MULT,
    F1_MAX_HOLD_SETTLEMENTS,
    F1_MIN_EDGE_BPS,
    MAX_CONSEC_NEG_SETTLEMENTS,
)

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "funding_oi"
SYMBOLS = ("BTC", "ETH", "BNB", "SOL", "XRP")
AVG_WINDOW = 21          # 7d of 8h settlements — mirrors avg_funding_7d
PLAN_HOLD = 21           # planning horizon, mirrors DEFAULT_HOLD_SETTLEMENTS


def load_funding(sym: str) -> list[float]:
    rows = []
    with open(DATA_DIR / f"{sym}_funding.csv", newline="", encoding="utf-8") as f:
        for r in csv.DictReader(f):
            try:
                rows.append((int(r["timestamp"]), float(r["funding_rate"])))
            except (KeyError, ValueError):
                continue
    rows.sort()
    return [fr for _, fr in rows]


def replay(rates: list[float], cost_frac: float, cost_mult: float = 1.0):
    """Walk the settlement series with the Rev-5 F1 entry/exit rules."""
    cost = cost_frac * cost_mult
    cycles: list[float] = []
    i = AVG_WINDOW
    while i < len(rates):
        avg7 = sum(rates[i - AVG_WINDOW:i]) / AVG_WINDOW
        # entry gate: positive current funding, positive 7d regime, and the
        # planned edge over the hold clears max(15bps, 3x cost) like the lab.
        edge_bps = (avg7 * PLAN_HOLD - cost) * 1e4
        floor_bps = max(F1_MIN_EDGE_BPS, F1_COST_MULT * cost * 1e4)
        if rates[i] <= 0 or avg7 <= 0 or edge_bps < floor_bps:
            i += 1
            continue
        # hold: accrue actual funding; exit on 2 consec negatives, regime flip
        # (trailing avg <= 0), or max-hold.
        accrued, consec_neg, held = 0.0, 0, 0
        j = i
        while j < len(rates) and held < F1_MAX_HOLD_SETTLEMENTS:
            r = rates[j]
            accrued += r
            consec_neg = consec_neg + 1 if r < 0 else 0
            held += 1
            trail = sum(rates[max(0, j - AVG_WINDOW):j]) / AVG_WINDOW if j else 0.0
            if consec_neg >= MAX_CONSEC_NEG_SETTLEMENTS or trail <= 0:
                break
            j += 1
        cycles.append(accrued - cost)
        i = j + 1
    return cycles


def stats(cycles: list[float]) -> dict:
    n = len(cycles)
    if n == 0:
        return {"n": 0}
    wins = [c for c in cycles if c > 0]
    losses = [c for c in cycles if c <= 0]
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else float("inf")
    return {"n": n, "wr": 100.0 * len(wins) / n, "pf": pf,
            "net_bps": sum(cycles) * 1e4, "avg_bps": sum(cycles) / n * 1e4}


def _scenario_costs(venue: str = "binance") -> list:
    """(label, round-trip cost fraction) per execution scenario — honest + bounded.

    Mirrors core.carry_runner: maker-first rests the spot leg (no slip) but the
    perp hedge stays taker, and on Binance/Bybit spot maker fee == taker fee, so
    the only maker saving is 2 slippage crossings. The VIP row additionally
    discounts fees and makes both legs — the bigger lever, but it needs a fee
    tier and accepts perp hedge-fill risk, so it is an OPTIMISTIC bound.
    """
    slip = DEFAULT_SLIP_FRAC
    taker = (round_trip_cost(venue, market_type="spot")
             + round_trip_cost(venue, market_type="futures") + 4.0 * slip)
    maker_first = (round_trip_cost(venue, market_type="spot", entry_liq="maker",
                                   exit_liq="maker")
                   + round_trip_cost(venue, market_type="futures") + 2.0 * slip)
    vip_both = (round_trip_cost(venue, market_type="spot", entry_liq="maker",
                                exit_liq="maker", tier_mult=0.5)
                + round_trip_cost(venue, market_type="futures", entry_liq="maker",
                                  exit_liq="maker", tier_mult=0.5))
    return [
        ("taker/taker (shipped baseline)", taker),
        ("maker-first spot, perp taker", maker_first),
        ("VIP fee-tier + both-maker (optimistic bound)", vip_both),
    ]


def main() -> None:
    rates_by_sym = {sym: load_funding(sym) for sym in SYMBOLS}
    scenarios = _scenario_costs()
    lines = [
        "# F1 carry — historical replay (real Binance funding, research-only)",
        f"Generated {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime())} | "
        "basis PnL assumed 0 (see module docstring) | 5 majors, no lookahead",
        "",
        "## Execution-scenario sensitivity (the actionable-update measurement)",
        "| scenario | cost/cycle | entry floor | cycles | win rate | PF | avg net |",
        "|---|---|---|---|---|---|---|",
    ]
    for label, cost in scenarios:
        floor = max(F1_MIN_EDGE_BPS, F1_COST_MULT * cost * 1e4)
        cyc = [c for sym in SYMBOLS for c in replay(rates_by_sym[sym], cost)]
        s = stats(cyc)
        if s["n"]:
            lines.append(f"| {label} | {cost * 1e4:.1f}bps | {floor:.0f}bps | {s['n']} "
                         f"| {s['wr']:.1f}% | {s['pf']:.2f} | {s['avg_bps']:+.1f}bps |")
        else:
            lines.append(f"| {label} | {cost * 1e4:.1f}bps | {floor:.0f}bps "
                         "| 0 | n/a | n/a | n/a |")
    lines += ["", "## Per-symbol: baseline vs maker-first",
              "| symbol | settlements | taker cycles | maker-first cycles |",
              "|---|---|---|---|"]
    t_cost, m_cost = scenarios[0][1], scenarios[1][1]
    for sym in SYMBOLS:
        r = rates_by_sym[sym]
        lines.append(f"| {sym} | {len(r)} | {len(replay(r, t_cost))} "
                     f"| {len(replay(r, m_cost))} |")
    lines += [
        "",
        "Scenario 2 (maker-first spot) is the honest actionable update: on Binance/Bybit",
        "spot maker fee == taker fee, so the saving is slippage + half-spread only — a",
        "modest floor reduction. Scenario 3 needs a VIP fee tier AND making the perp leg",
        "(hedge-fill risk), the bigger lever but not free. The replay assumes maker fills",
        "are achievable (spread room); the live PAPER runner applies a per-cycle spread",
        "gate, so these counts are an UPPER bound. Replay-grade; not a guarantee.",
    ]
    report = "\n".join(lines) + "\n"
    out = ROOT / "reports" / f"f1_replay_historical_{time.strftime('%Y%m%d')}.md"
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"[f1_replay] report -> {out}")


if __name__ == "__main__":
    main()
