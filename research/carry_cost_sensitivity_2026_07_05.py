"""carry_cost_sensitivity_2026_07_05.py — LAB-ONLY cost-sensitivity re-run of the
2026-07-05 carry-universe scan (research/carry_universe_scan_2026_07_05.py).

RESEARCH-ONLY: no orders, no exchange writes, no state files. The ONLY file this
script writes is ``reports/carry_cost_sensitivity_2026_07_05.md``.

Why this re-run exists
======================
The scan's shipped 70bps baseline DOUBLE-COUNTS slippage: core.cost_model.
round_trip_cost() already embeds config.SLIPPAGE (5bps open + 5bps close = 10bps
RT per market; config.py:113-114 via cost_model.slippage_rt, core/cost_model.py:
77-79), and the reference replay then adds 4 x DEFAULT_SLIP_FRAC (5bps) ON TOP
(scripts/f1_replay_historical.py:110-111) -> 40bps of slippage where ~20bps was
intended. Cheaper cost -> lower entry floor (max(15bps, 3x cost)) -> MORE
qualifying entries and more resolved cycles/yr. This script replays the SAME
gate under four cost scenarios, every cost derived from repo code (file:line
cited in the report), never invented.

Scenarios (round-trip cost of the full 2-leg carry, binance):
  a. baseline-70bps   — exactly the scan's COST_FRAC (self-check: must reproduce
                        today's per-symbol entries/wins bit-for-bit).
  b. corrected-50bps  — taker fees only (cost_model.round_trip_fee, no embedded
                        slippage) + the intended 4 x DEFAULT_SLIP_FRAC = 20bps.
  c. lab-44bps        — research.funding_carry_lab.round_trip_cost_pct()
                        (4 x (6bps blended taker fee + 5bps slip)).
  d. maker-first-40bps— the SHIPPED maker-first execution model from
                        core/carry_runner.py:266-271: spot leg rests maker at
                        mid (no slip), perp hedge ALWAYS stays taker; rt_cost =
                        2*spot_maker_fee + 2*perp_taker_fee + 2*DEFAULT_SLIP_FRAC.
                        NOTE the repo does NOT support both-legs-maker in the
                        runner; the both-maker VIP row from
                        scripts/f1_replay_historical.py:115-118 is included as
                        an INFORMATIONAL optimistic bound only.

Data: the scan is in-memory-only by design (it caches nothing to disk), so no
funding-history cache exists for the 15-symbol universe — this script refetches
the full Binance funding history via the scan's own fetch_full_funding_history()
ONCE per symbol and replays all scenarios on that single in-memory copy
(data/funding_oi/*.csv covers only 5 symbols via the harvester and is not the
scan's data source, so it is not used).

Usage:  venv/Scripts/python.exe research/carry_cost_sensitivity_2026_07_05.py
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.carry_runner import DEFAULT_SLIP_FRAC  # noqa: E402
from core.cost_model import fee_rate, round_trip_cost, round_trip_fee  # noqa: E402

# Reuse today's scan machinery verbatim: fetcher, symbols, COST_FRAC and the
# imported scripts/f1_replay_historical.py reference replay (scan._frh).
from research import carry_universe_scan_2026_07_05 as scan  # noqa: E402
from research.funding_carry_lab import (  # noqa: E402
    F1_COST_MULT,
    F1_MIN_EDGE_BPS,
    round_trip_cost_pct,
)

SYMBOLS = scan.SYMBOLS
MS_PER_YEAR = scan.MS_PER_YEAR
REPORT_PATH = ROOT / "reports" / "carry_cost_sensitivity_2026_07_05.md"

# Self-check target: per-symbol (entries, win_cycles) published in today's
# reports/carry_universe_scan_2026_07_05.md — the baseline scenario must
# reproduce these EXACTLY or the whole run aborts.
EXPECTED_BASELINE = {
    "BTC": (0, 0), "ETH": (3, 3), "BNB": (3, 3), "SOL": (1, 1),
    "LINK": (5, 5), "TRX": (2, 2), "SUI": (0, 0), "ATOM": (5, 5),
    "DOGE": (2, 2), "ALGO": (3, 2), "ZEC": (4, 3), "ADA": (4, 4),
    "XRP": (6, 6), "LTC": (5, 5), "GRT": (6, 5),
}


def scenario_costs() -> list[dict]:
    """Four cost scenarios + one informational bound, every number from repo code."""
    venue = "binance"
    slip = DEFAULT_SLIP_FRAC  # core/carry_runner.py:48 — 5bps per crossing

    baseline = (round_trip_cost(venue, market_type="spot")
                + round_trip_cost(venue, market_type="futures")
                + 4.0 * slip)
    # Self-check #1: identical to the scan's shipped COST_FRAC.
    assert abs(baseline - scan.COST_FRAC) < 1e-12, "baseline drifted from scan COST_FRAC"

    corrected = (round_trip_fee(venue, market_type="spot")
                 + round_trip_fee(venue, market_type="futures")
                 + 4.0 * slip)

    lab = round_trip_cost_pct()  # research/funding_carry_lab.py:72-80 defaults :42-43

    # Shipped maker-first execution model (core/carry_runner.py:266-271):
    # spot rests maker at mid (no slip), perp hedge ALWAYS taker (:175-183),
    # only the 2 perp crossings pay slippage.
    spot_maker_fee = fee_rate(venue, "spot", "maker")      # config.py:78 (0.001)
    perp_taker_fee = fee_rate(venue, "futures", "taker")   # config.py:83 (0.0005)
    maker_first = 2.0 * spot_maker_fee + 2.0 * perp_taker_fee + 2.0 * slip

    # INFORMATIONAL optimistic bound only — both legs maker + VIP 0.5x fee tier
    # (scripts/f1_replay_historical.py:115-118). Not a shipped execution mode.
    vip_both = (round_trip_cost(venue, market_type="spot", entry_liq="maker",
                                exit_liq="maker", tier_mult=0.5)
                + round_trip_cost(venue, market_type="futures", entry_liq="maker",
                                  exit_liq="maker", tier_mult=0.5))

    return [
        {"key": "baseline-70bps", "cost": baseline, "informational": False,
         "basis": ("core/cost_model.py:82-94 round_trip_cost(spot)+(futures) "
                   "[fees config.py:77-83 + embedded slippage config.py:113-114 via "
                   "cost_model.py:77-79] + 4x DEFAULT_SLIP_FRAC core/carry_runner.py:48; "
                   "composed at research/carry_universe_scan_2026_07_05.py:82-86 / "
                   "scripts/f1_replay_historical.py:110-111 — slippage double-counted")},
        {"key": "corrected-50bps", "cost": corrected, "informational": False,
         "basis": ("core/cost_model.py:63-74 round_trip_fee(spot)+(futures) "
                   "[taker fees config.py:77-83, NO embedded slippage] + the intended "
                   "4x DEFAULT_SLIP_FRAC=20bps core/carry_runner.py:48")},
        {"key": "lab-44bps", "cost": lab, "informational": False,
         "basis": ("research/funding_carry_lab.py:72-80 round_trip_cost_pct() = "
                   "4x(fee 0.0006 + slip 0.0005), defaults funding_carry_lab.py:42-43")},
        {"key": "maker-first-40bps", "cost": maker_first, "informational": False,
         "basis": ("core/carry_runner.py:266-271 rt_cost = 2*spot_maker + 2*perp_taker "
                   "+ 2*slip; spot maker fee config.py:78 (== taker, 0.001), perp taker "
                   "config.py:83 (0.0005), slip core/carry_runner.py:48; perp leg always "
                   "taker per core/carry_runner.py:175-183 — both-legs-maker NOT shipped")},
        {"key": "vip-both-maker-32bps (informational)", "cost": vip_both,
         "informational": True,
         "basis": ("scripts/f1_replay_historical.py:115-118 — VIP 0.5x fee tier + both "
                   "legs maker; optimistic bound, needs a fee tier + accepts perp "
                   "hedge-fill risk; still embeds config.SLIPPAGE per round_trip_cost")},
    ]


def replay_scenario(rates_by_sym: dict, cost: float) -> dict:
    """Run the reference replay for one cost over all symbols; return per-symbol rows."""
    rows = {}
    for sym, (ts, rates) in rates_by_sym.items():
        years = (ts[-1] - ts[0]) / MS_PER_YEAR
        cycles = scan._frh.replay(rates, cost)
        rows[sym] = {
            "years": years,
            "n_entries": len(cycles),
            "entries_per_year": (len(cycles) / years) if years > 0 else 0.0,
            "win_cycles": sum(1 for c in cycles if c > 0),
        }
    return rows


def main() -> None:
    import ccxt

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    rates_by_sym: dict = {}
    fetch_errors: list = []
    for sym in SYMBOLS:
        try:
            pairs = scan.fetch_full_funding_history(exchange, f"{sym}/USDT:USDT")
            if len(pairs) < scan.AVG_WINDOW + 2:
                fetch_errors.append((sym, f"insufficient history ({len(pairs)})"))
                continue
            rates_by_sym[sym] = ([p[0] for p in pairs], [p[1] for p in pairs])
            print(f"[fetch] {sym}: {len(pairs)} settlements")
        except Exception as exc:  # honest failure, never fabricate
            fetch_errors.append((sym, f"{type(exc).__name__}: {exc}"))
            print(f"[fetch] {sym}: FAILED ({type(exc).__name__}: {exc})")

    scenarios = scenario_costs()
    results = []
    for sc in scenarios:
        rows = replay_scenario(rates_by_sym, sc["cost"])
        floor_bps = max(F1_MIN_EDGE_BPS, F1_COST_MULT * sc["cost"] * 1e4)
        total_epy = sum(r["entries_per_year"] for r in rows.values())
        total_entries = sum(r["n_entries"] for r in rows.values())
        total_wins = sum(r["win_cycles"] for r in rows.values())
        results.append({**sc, "floor_bps": floor_bps, "rows": rows,
                        "total_epy": total_epy, "total_entries": total_entries,
                        "total_wins": total_wins,
                        "years_to_60": (60.0 / total_epy) if total_epy > 0 else None})
        print(f"[replay] {sc['key']}: cost {sc['cost'] * 1e4:.1f}bps, floor "
              f"{floor_bps:.0f}bps -> {total_entries} entries "
              f"({total_epy:.2f}/yr), {total_wins}/{total_entries} won")

    # Self-check #2: baseline must reproduce today's published scan EXACTLY.
    base = results[0]
    mismatches = []
    for sym, (exp_n, exp_w) in EXPECTED_BASELINE.items():
        got = base["rows"].get(sym)
        if got is None:
            mismatches.append(f"{sym}: missing (fetch failed?)")
        elif (got["n_entries"], got["win_cycles"]) != (exp_n, exp_w):
            mismatches.append(f"{sym}: got {got['n_entries']}/{got['win_cycles']}, "
                              f"expected {exp_n}/{exp_w}")
    if mismatches:
        raise AssertionError(
            "baseline-70bps failed to reproduce today's scan (likely a new funding "
            "settlement arrived since the morning run — rerun and re-verify): "
            + "; ".join(mismatches)
        )
    print("[self-check] baseline-70bps reproduces today's scan exactly (49 entries, 46 won)")

    # Symbols gaining the most entries from baseline -> maker-first.
    maker = next(r for r in results if r["key"] == "maker-first-40bps")
    deltas = sorted(
        ((sym, maker["rows"][sym]["n_entries"] - base["rows"][sym]["n_entries"])
         for sym in rates_by_sym),
        key=lambda kv: kv[1], reverse=True,
    )

    report = build_report(results, deltas, fetch_errors)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n[sensitivity] report -> {REPORT_PATH}")

    payload = {
        "scenarios": [
            {
                "name": r["key"],
                "informational": r["informational"],
                "round_trip_cost_bps": round(r["cost"] * 1e4, 2),
                "entry_floor_bps": round(r["floor_bps"], 1),
                "total_entries": r["total_entries"],
                "total_entries_per_year_15sym": round(r["total_epy"], 3),
                "cycles_won": f"{r['total_wins']}/{r['total_entries']}",
                "years_to_60_cycles": (round(r["years_to_60"], 2)
                                       if r["years_to_60"] is not None else None),
                "per_symbol_entries": {s: r["rows"][s]["n_entries"] for s in r["rows"]},
                "cost_basis": r["basis"],
            }
            for r in results
        ],
        "maker_vs_baseline_entry_deltas": dict(deltas),
        "fetch_errors": [{"symbol": s, "error": e} for s, e in fetch_errors],
    }
    print("RESULT_JSON " + json.dumps(payload))


def build_report(results, deltas, fetch_errors) -> str:
    base, maker = results[0], next(r for r in results if r["key"] == "maker-first-40bps")
    lines = [
        "# Carry cost-sensitivity — F1 gate replayed under 4 execution-cost scenarios",
        "",
        "generated 2026-07-05, cost-sensitivity re-run of the carry-universe scan",
        "",
        "RESEARCH-ONLY (LAB). No orders, no state files. Same 15-symbol Binance",
        "funding-history replay as reports/carry_universe_scan_2026_07_05.md, same",
        "reference replay (scripts/f1_replay_historical.py `replay()`), only the",
        "round-trip cost — and therefore the entry floor max(15bps, 3x cost) — varies.",
        "",
        "## Why: the 70bps baseline double-counts slippage",
        "core.cost_model.round_trip_cost() already embeds config.SLIPPAGE (5bps open +",
        "5bps close = 10bps RT per market; config.py:113-114, core/cost_model.py:77-79).",
        "The reference replay adds 4 x DEFAULT_SLIP_FRAC (5bps, core/carry_runner.py:48)",
        "on top (scripts/f1_replay_historical.py:110-111) -> 40bps total slippage where",
        "~20bps was intended. Fees themselves (30bps RT: spot 10bps/side x2 + perp",
        "5bps/side x2, config.py:77-83) are not in dispute.",
        "",
        "## Scenario totals (15-symbol universe)",
        "",
        "| scenario | RT cost | entry floor | entries | entries/yr (15 sym) | "
        "cycles won | years to 60 cycles* |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in results:
        y60 = f"{r['years_to_60']:.1f}y" if r["years_to_60"] is not None else "n/a"
        lines.append(
            f"| {r['key']} | {r['cost'] * 1e4:.1f}bps | {r['floor_bps']:.0f}bps "
            f"| {r['total_entries']} | {r['total_epy']:.2f} "
            f"| {r['total_wins']}/{r['total_entries']} | {y60} |"
        )
    lines += [
        "",
        "*years_to_60_cycles = 60 / total_entries_per_year — WHOLE-HISTORY-RATE",
        "arithmetic, an OPTIMISTIC bound (2020-21 funding regimes were far richer than",
        "2024-26; recent-years-only rates are lower, so real time-to-60 is LONGER).",
        "",
        "## Per-symbol qualifying entries by scenario",
        "",
        "| symbol | " + " | ".join(r["key"] for r in results) + " |",
        "|---|" + "---|" * len(results),
    ]
    for sym in base["rows"]:
        lines.append("| " + sym + " | " + " | ".join(
            f"{r['rows'][sym]['n_entries']} ({r['rows'][sym]['win_cycles']}W)"
            for r in results) + " |")
    lines += [
        "",
        "## Symbols gaining the most entries: baseline-70bps -> maker-first-40bps",
        "",
        "| symbol | baseline entries | maker-first entries | delta |",
        "|---|---|---|---|",
    ]
    for sym, d in deltas:
        lines.append(f"| {sym} | {base['rows'][sym]['n_entries']} "
                     f"| {maker['rows'][sym]['n_entries']} | {d:+d} |")
    lines += [
        "",
        "## Cost-basis provenance (every scenario derived from repo code)",
        "",
    ]
    for r in results:
        lines.append(f"- **{r['key']}** ({r['cost'] * 1e4:.1f}bps): {r['basis']}")
    lines += [
        "",
        "## Self-checks",
        "- baseline-70bps cost is asserted equal to the scan's shipped COST_FRAC.",
        "- baseline-70bps per-symbol (entries, wins) asserted EXACTLY equal to today's",
        "  reports/carry_universe_scan_2026_07_05.md table (49 entries, 46 won).",
        "",
        "## Caveats (read before acting)",
        "- WHOLE-HISTORY RATES ARE UPPER BOUNDS: entries/yr averages over 2019-2026;",
        "  funding regimes decay, so forward-looking entry rates (and time-to-60-cycles)",
        "  will be worse than these numbers.",
        "- MAKER FILLS ARE NOT GUARANTEED: the runner rests the spot leg post-only and",
        "  only when spread >= 2bps (core/carry_runner.py:64); a missed spot fill or a",
        "  moved perp leg is fill-or-kill risk on the hedge — the 40bps scenario",
        "  ASSUMES every maker fill happens. The perp leg always pays taker in shipped",
        "  code; the 32bps both-maker VIP row is an optimistic bound, not shipped.",
        "- Microstructure entry gates (depth/spread/basis/time-to-funding/atomic-fill)",
        "  are NOT replayable from funding history alone — live-qualifying entries will",
        "  be FEWER than every count above.",
        "- MULTIPLE TESTING: same 15-symbol selection caveat as the base scan; any",
        "  universe change must be pre-registered, not cherry-picked from these tables.",
        "- Basis PnL assumed 0, identical to the base scan and reference replay.",
        "- Funding history refetched in-memory (the scan caches nothing to disk);",
        "  fetched once per symbol, all scenarios replayed on the same copy.",
    ]
    if fetch_errors:
        lines.append("- Skipped/failed symbols: "
                     + "; ".join(f"{s}: {e}" for s, e in fetch_errors))
    lines += [
        "",
        "PAPER/LAB only — this run places no orders and changes no bot state.",
        "",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    os.chdir(ROOT)
    main()
