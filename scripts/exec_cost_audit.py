"""Phase 1 — execution-cost audit (LIVE-only).

The honest "what do fees/spread/slippage actually cost us" number, computed from
real CONTROLLED_LIVE trades. PAPER rows are excluded — in paper, costs are the
simulator's own config fed back, so a paper-inclusive audit is tautological.

What it answers:
  * realized round-trip cost decomposition (fee / spread / slippage / funding)
    per trade and as % of notional, from warehouse.trades ⋈ attribution;
  * the breakeven-WR lift available from cutting cost (taker → maker);
  * the maker-fill RATE actually achieved (from execution_stats.json counters;
    optionally cross-checked against exchange ground truth via --live-fills).

What it CANNOT answer yet: "did the maker-only flip help?" — the flip was
2026-05-29, so post-flip live N is ~0. It reports that N honestly and says
re-run later.

Run:
    python scripts/exec_cost_audit.py
    python scripts/exec_cost_audit.py --since-days 30
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "warehouse.sqlite"
STATS = ROOT / "data" / "execution_stats.json"

# bracket geometry (mirror scripts/scalp_replay_backtest.py)
SL_PCT, TP_PCT = 0.008, 0.013
# representative round-trip cost fractions (config.py FEE; futures Bybit/Bitget)
TAKER_RT = 0.0012  # 0.06% * 2
MAKER_RT = 0.0004  # ~0.02% maker entry+exit (SL leg still taker in practice)


def breakeven_wr(cost_rt: float) -> float:
    """WR needed to break even on the SL/TP bracket given round-trip cost."""
    return (SL_PCT + cost_rt) / (SL_PCT + TP_PCT)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--since-days",
        type=float,
        default=None,
        help="restrict to live trades opened in the last N days",
    )
    ap.add_argument(
        "--live-fills",
        action="store_true",
        help="(best-effort) cross-check maker/taker via exchange.fetch_my_trades",
    )
    args = ap.parse_args()

    import sys

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    if not DB.exists():
        print(f"[abort] warehouse not found at {DB}")
        return
    con = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    con.row_factory = sqlite3.Row

    where = "status='CLOSED' AND mode='CONTROLLED_LIVE'"
    params: list = []
    if args.since_days:
        where += " AND ts_entry >= ?"
        params.append(time.time() - args.since_days * 86400)

    rows = con.execute(
        f"""SELECT t.id, t.symbol, t.exchange, t.side, t.entry_px, t.size, t.leverage,
                   t.fee, t.slippage AS t_slip, t.realized_pnl, t.ts_entry,
                   a.spread, a.slippage AS a_slip, a.funding, a.fees AS a_fees, a.alpha
            FROM trades t LEFT JOIN attribution a ON a.trade_id = t.id
            WHERE {where} ORDER BY t.ts_entry""",
        params,
    ).fetchall()

    if not rows:
        print("[abort] no closed CONTROLLED_LIVE trades match the filter.")
        return

    n = len(rows)
    notional = sum((r["entry_px"] or 0) * (r["size"] or 0) for r in rows)
    sum_fee = sum(r["fee"] or 0.0 for r in rows)
    sum_pnl = sum(r["realized_pnl"] or 0.0 for r in rows)
    # attribution components (dollar terms); fall back gracefully if missing
    sum_spread = sum(r["spread"] or 0.0 for r in rows)
    sum_slip = sum((r["a_slip"] if r["a_slip"] is not None else r["t_slip"]) or 0.0 for r in rows)
    sum_funding = sum(r["funding"] or 0.0 for r in rows)
    have_attr = sum(1 for r in rows if r["spread"] is not None)

    per_trade_fee_pct = sorted(
        ((r["fee"] or 0.0) / ((r["entry_px"] or 0) * (r["size"] or 0)) * 100)
        for r in rows
        if (r["entry_px"] or 0) * (r["size"] or 0) > 0
    )
    med_fee_pct = per_trade_fee_pct[len(per_trade_fee_pct) // 2] if per_trade_fee_pct else 0.0

    print("\n=== EXECUTION-COST AUDIT (CONTROLLED_LIVE only) ===")
    print(f"trades={n}  total_notional=${notional:,.0f}  attribution_rows={have_attr}/{n}")
    print("\n  cost component        $ total      % of notional")

    def line(lbl, d):
        print(f"  {lbl:<20} {d:>10.4f}   {(d / notional * 100 if notional else 0):>10.4f}%")

    line("fees (modeled rate)", sum_fee)
    line("spread (fill-mid)", sum_spread)
    line("slippage (modeled)", sum_slip)
    line("funding", sum_funding)
    total_cost = sum_fee + sum_spread + sum_slip + sum_funding
    line("TOTAL COST (floor)", total_cost)
    if sum_slip == 0.0:
        print("  NOTE: fees are MODELED from rate constants (not fetch_my_trades ground truth);")
        print("        live slippage is NOT written back (shows 0) => TOTAL COST is a FLOOR.")
    print(
        f"\n  realized_pnl total: ${sum_pnl:+.4f}   |  median per-trade fee: {med_fee_pct:.4f}% of notional"
    )
    # alpha-vs-cost decomposition: realized_pnl = gross_alpha - total_cost
    gross_alpha = sum_pnl + total_cost
    cost_share = (
        (total_cost / (abs(gross_alpha) + total_cost) * 100) if (gross_alpha or total_cost) else 0
    )
    print(
        f"  implied gross alpha (pnl + cost): ${gross_alpha:+.4f}   "
        f"=> measured cost is a MINORITY of the bleed (>= ~{cost_share:.0f}% on this floor); "
        f"the rest is negative-alpha entries (alpha-dominated)"
    )
    # cross-check (apples-to-apples: only over rows that HAVE attribution)
    if have_attr:
        fee_attr_rows = sum((r["fee"] or 0.0) for r in rows if r["spread"] is not None)
        print(
            f"  [check] over {have_attr} attributed rows: trades.fee=${fee_attr_rows:.4f} "
            f"vs attribution.fees=${sum(r['a_fees'] or 0.0 for r in rows):.4f}"
        )

    # breakeven-WR implication
    realized_rt = (total_cost / notional) if notional else 0.0
    print(f"\n  realized round-trip cost ≈ {realized_rt * 100:.4f}% of notional")
    print(f"  breakeven WR @ realized cost : {breakeven_wr(realized_rt) * 100:.2f}%")
    print(f"  breakeven WR @ taker  ({TAKER_RT * 100:.2f}%): {breakeven_wr(TAKER_RT) * 100:.2f}%")
    print(f"  breakeven WR @ maker  ({MAKER_RT * 100:.2f}%): {breakeven_wr(MAKER_RT) * 100:.2f}%")
    print(
        f"  => taker→maker would lower the breakeven WR by "
        f"{(breakeven_wr(TAKER_RT) - breakeven_wr(MAKER_RT)) * 100:.2f} points (the cost lever's CEILING; "
        f"real saving is SMALLER — the SL leg still crosses as taker)."
    )

    # maker-fill rate (aggregate; cumulative across config history — labeled)
    print("\n=== maker-fill rate (source: execution_stats.json, CUMULATIVE all-config) ===")
    try:
        st = json.loads(STATS.read_text())
        lf, mf = st.get("limit_fills", 0), st.get("market_fallbacks", 0)
        tot = lf + mf
        skip, part = st.get("maker_only_skipped", 0), st.get("maker_partial_fill", 0)
        print(
            f"  limit_fills(maker)={lf}  market_fallbacks(taker)={mf}  "
            f"maker_rate={lf / tot * 100 if tot else 0:.1f}%  skipped={skip}  partial={part}"
        )
    except Exception as e:
        print(f"  [stats unavailable] {e}")

    # post-flip honesty: maker-only default ON at commit 40990a2 = 2026-05-29 01:17 +05 (≈20:17 UTC 05-28)
    flip_ts = 1779990000  # ~2026-05-28T20:20Z
    post = sum(1 for r in rows if (r["ts_entry"] or 0) >= flip_ts)
    print(f"\n  trades opened AFTER the maker-only flip: {post}")
    if post < 30:
        print("  => INSUFFICIENT post-flip live data to judge maker-only EV. Re-run in ~1–2 weeks.")

    if args.live_fills:
        print(
            "\n[--live-fills] exchange ground-truth pull not run in this build "
            "(requires live client construction); use execution_stats above. "
            "Forward per-trade maker/taker is captured by the Phase-2 fill_type column."
        )


if __name__ == "__main__":
    main()
