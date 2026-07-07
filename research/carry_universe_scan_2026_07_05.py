"""carry_universe_scan_2026_07_05.py — LAB-ONLY carry-universe expansion scan.

generated 2026-07-05, carry-universe expansion scan

RESEARCH-ONLY: no orders, no exchange writes, no state files. The ONLY file this
script writes is ``reports/carry_universe_scan_2026_07_05.md``. Funding history
is fetched in-memory from Binance public data (ccxt ``fetch_funding_rate_history``,
no API keys) and is NOT cached to disk.

Question answered
=================
The shipped F1 delta-neutral funding-carry strategy (research/funding_carry_lab.py)
is latched to F1_DEFAULT_SYMBOLS = (BTC, ETH) and historically qualifies only
~2 entries/year — the binding bottleneck on the 60-resolved-cycle promotion
floor (F1_MIN_CYCLES). This scan replays the SAME Rev-5 entry/exit gate over the
full Binance funding history of 15 candidate USDT-perps and reports how many
qualifying carry ENTRIES per year each symbol would contribute.

Gate fidelity (no copy-paste drift)
===================================
All thresholds are IMPORTED from the shipped code:
  * research.funding_carry_lab: F1_MIN_EDGE_BPS, F1_COST_MULT,
    F1_MAX_HOLD_SETTLEMENTS, MAX_CONSEC_NEG_SETTLEMENTS
  * core.carry_runner: DEFAULT_SLIP_FRAC (and DEFAULT_HOLD_SETTLEMENTS = the
    21-settlement planning horizon)
  * core.cost_model: round_trip_cost (taker/taker both legs, binance)
The replay walk itself mirrors scripts/f1_replay_historical.py ``replay()``
(the source of the ~2 entries/year baseline) and every symbol's cycle list is
CROSS-CHECKED for exact equality against that imported reference function —
if the two ever disagree the script aborts rather than report drifted numbers.

Cycle semantics: a persistent qualifying window is ONE entry (cycle start);
the position is then held until 2 consecutive negative settlements, trailing
7d-average regime flip (<= 0), or the max-hold window — identical to the
reference replay. Entries are therefore deduplicated by construction.

Usage:  venv/Scripts/python.exe research/carry_universe_scan_2026_07_05.py
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.carry_runner import DEFAULT_HOLD_SETTLEMENTS, DEFAULT_SLIP_FRAC  # noqa: E402
from core.cost_model import round_trip_cost  # noqa: E402
from research.funding_carry_lab import (  # noqa: E402
    F1_COST_MULT,
    F1_MAX_HOLD_SETTLEMENTS,
    F1_MIN_EDGE_BPS,
    MAX_CONSEC_NEG_SETTLEMENTS,
)

# Reference replay (scripts/ is not a package -> load by file path). Reusing the
# exact function that produced the ~2/yr BTC+ETH baseline guards against drift.
_frh_spec = importlib.util.spec_from_file_location(
    "f1_replay_historical", ROOT / "scripts" / "f1_replay_historical.py"
)
_frh = importlib.util.module_from_spec(_frh_spec)
_frh_spec.loader.exec_module(_frh)

AVG_WINDOW = _frh.AVG_WINDOW      # 21 settlements ~ 7d — mirrors avg_funding_7d
PLAN_HOLD = _frh.PLAN_HOLD        # 21 — mirrors DEFAULT_HOLD_SETTLEMENTS
assert PLAN_HOLD == DEFAULT_HOLD_SETTLEMENTS, "planning horizon drifted from runner"

SYMBOLS = (
    "BTC", "ETH", "BNB", "SOL", "LINK", "TRX", "SUI", "ATOM",
    "DOGE", "ALGO", "ZEC", "ADA", "XRP", "LTC", "GRT",
)
NOTIONAL_USD = 150.0              # matched notional per the scan brief
MS_PER_YEAR = 365.25 * 24 * 3600 * 1000.0
REPORT_PATH = ROOT / "reports" / "carry_universe_scan_2026_07_05.md"

# Taker/taker shipped-baseline round-trip cost, exactly as the reference replay
# builds it (spot RT + perp RT + 4 slippage crossings on binance).
COST_FRAC = (
    round_trip_cost("binance", market_type="spot")
    + round_trip_cost("binance", market_type="futures")
    + 4.0 * DEFAULT_SLIP_FRAC
)
FLOOR_BPS = max(F1_MIN_EDGE_BPS, F1_COST_MULT * COST_FRAC * 1e4)


def fetch_full_funding_history(exchange, market_symbol: str):
    """Full funding history for one perp, oldest-first, paginated by ``since``.

    Returns list of (timestamp_ms, rate). In-memory only — never cached to disk.
    """
    rows: dict[int, float] = {}
    since = 1  # epoch start -> Binance returns from listing, oldest first
    while True:
        batch = exchange.fetch_funding_rate_history(market_symbol, since=since, limit=1000)
        if not batch:
            break
        for rec in batch:
            ts = int(rec["timestamp"])
            rate = rec.get("fundingRate")
            if rate is not None:
                rows[ts] = float(rate)
        last_ts = int(batch[-1]["timestamp"])
        if last_ts + 1 <= since or len(batch) < 1000:
            break
        since = last_ts + 1
    return sorted(rows.items())


def replay_with_held_rates(rates, cost_frac):
    """Mirror of f1_replay_historical.replay(), additionally returning the
    funding rates accrued inside each held cycle (for the while-qualified mean).

    Cross-checked below for exact equality with the imported reference.
    """
    cost = cost_frac
    cycles: list[float] = []
    held_rates_per_cycle: list[list[float]] = []
    i = AVG_WINDOW
    while i < len(rates):
        avg7 = sum(rates[i - AVG_WINDOW:i]) / AVG_WINDOW
        edge_bps = (avg7 * PLAN_HOLD - cost) * 1e4
        floor_bps = max(F1_MIN_EDGE_BPS, F1_COST_MULT * cost * 1e4)
        if rates[i] <= 0 or avg7 <= 0 or edge_bps < floor_bps:
            i += 1
            continue
        accrued, consec_neg, held = 0.0, 0, 0
        held_rates: list[float] = []
        j = i
        while j < len(rates) and held < F1_MAX_HOLD_SETTLEMENTS:
            r = rates[j]
            accrued += r
            held_rates.append(r)
            consec_neg = consec_neg + 1 if r < 0 else 0
            held += 1
            trail = sum(rates[max(0, j - AVG_WINDOW):j]) / AVG_WINDOW if j else 0.0
            if consec_neg >= MAX_CONSEC_NEG_SETTLEMENTS or trail <= 0:
                break
            j += 1
        cycles.append(accrued - cost)
        held_rates_per_cycle.append(held_rates)
        i = j + 1
    return cycles, held_rates_per_cycle


def interval_profile(timestamps_ms):
    """Median settlement interval (h) and share of sub-6h intervals (4h periods)."""
    if len(timestamps_ms) < 2:
        return None, 0.0
    deltas = [
        (b - a) / 3.6e6
        for a, b in zip(timestamps_ms[:-1], timestamps_ms[1:])
        if 0 < (b - a) / 3.6e6 <= 48  # ignore listing gaps / outages
    ]
    if not deltas:
        return None, 0.0
    deltas.sort()
    median = deltas[len(deltas) // 2]
    sub6 = sum(1 for d in deltas if d <= 6.0) / len(deltas) * 100.0
    return median, sub6


def scan_symbol(exchange, sym: str):
    market_symbol = f"{sym}/USDT:USDT"
    pairs = fetch_full_funding_history(exchange, market_symbol)
    if len(pairs) < AVG_WINDOW + 2:
        return {"symbol": sym, "error": f"insufficient history ({len(pairs)} settlements)"}
    ts = [p[0] for p in pairs]
    rates = [p[1] for p in pairs]
    years = (ts[-1] - ts[0]) / MS_PER_YEAR

    cycles, held_rates = replay_with_held_rates(rates, COST_FRAC)
    ref_cycles = _frh.replay(rates, COST_FRAC)  # drift guard vs shipped replay
    if cycles != ref_cycles:
        raise AssertionError(f"{sym}: instrumented replay drifted from reference replay")

    all_held = [r for cyc in held_rates for r in cyc]
    med_iv, sub6_pct = interval_profile(ts)
    n = len(cycles)
    return {
        "symbol": sym,
        "n_settlements": len(rates),
        "first_ts": ts[0],
        "last_ts": ts[-1],
        "years": years,
        "n_entries": n,
        "entries_per_year": (n / years) if years > 0 else 0.0,
        "mean_funding_while_qualified_pct": (
            (sum(all_held) / len(all_held) * 100.0) if all_held else None
        ),
        "avg_hold_settlements": (len(all_held) / n) if n else None,
        "net_per_cycle_usd": [c * NOTIONAL_USD for c in cycles],
        "mean_net_per_cycle_usd": (sum(cycles) / n * NOTIONAL_USD) if n else None,
        "win_cycles": sum(1 for c in cycles if c > 0),
        "median_interval_h": med_iv,
        "sub6h_interval_pct": sub6_pct,
    }


def _fmt_date(ms):
    return time.strftime("%Y-%m-%d", time.gmtime(ms / 1000.0))


def build_report(results, errors):
    lines = [
        "# Carry-universe expansion scan — F1 gate replayed over 15 Binance USDT-perps",
        "",
        "generated 2026-07-05, carry-universe expansion scan",
        "",
        "RESEARCH-ONLY (LAB). No orders, no state files. Full Binance funding history",
        "per symbol via ccxt `fetch_funding_rate_history` (public data, in-memory only).",
        "",
        "## Gate replayed (imported from shipped code — no copied thresholds)",
        f"- Entry: current funding > 0 AND trailing {AVG_WINDOW}-settlement (7d) avg > 0 AND",
        f"  planned edge over {PLAN_HOLD}-settlement horizon >= max({F1_MIN_EDGE_BPS:.0f}bps, "
        f"{F1_COST_MULT:.0f}x cost) = {FLOOR_BPS:.0f}bps",
        f"- Exit: {MAX_CONSEC_NEG_SETTLEMENTS} consecutive negative settlements, 7d-avg regime"
        f" flip (<= 0), or max hold {F1_MAX_HOLD_SETTLEMENTS} settlements",
        f"- Cost model: taker/taker both legs + 4 slippage crossings on binance = "
        f"{COST_FRAC * 1e4:.1f}bps round-trip (core.cost_model + carry_runner slippage)",
        f"- Per-cycle net in USD at ${NOTIONAL_USD:.0f} matched notional",
        "- Every symbol's cycle list cross-checked (exact equality) against",
        "  scripts/f1_replay_historical.py `replay()` — the shipped reference.",
        "",
        "## Per-symbol results",
        "",
        "| symbol | history | years | settlements | entries | entries/yr | "
        "mean funding while qualified (%/settle) | avg hold (settles) | "
        f"mean net/cycle @ ${NOTIONAL_USD:.0f} | win cycles |",
        "|---|---|---|---|---|---|---|---|---|---|",
    ]
    for r in results:
        mf = r["mean_funding_while_qualified_pct"]
        nn = r["mean_net_per_cycle_usd"]
        ah = r["avg_hold_settlements"]
        lines.append(
            "| {sym} | {a} → {b} | {yr:.2f} | {ns} | {ne} | {epy:.2f} | {mf} | {ah} "
            "| {nn} | {w}/{ne2} |".format(
                sym=r["symbol"], a=_fmt_date(r["first_ts"]), b=_fmt_date(r["last_ts"]),
                yr=r["years"], ns=r["n_settlements"], ne=r["n_entries"],
                epy=r["entries_per_year"],
                mf=(f"{mf:+.4f}%" if mf is not None else "n/a"),
                ah=(f"{ah:.1f}" if ah is not None else "n/a"),
                nn=(f"${nn:+.3f}" if nn is not None else "n/a"),
                w=r["win_cycles"], ne2=r["n_entries"],
            )
        )
    total_epy = sum(r["entries_per_year"] for r in results)
    base_epy = sum(r["entries_per_year"] for r in results if r["symbol"] in ("BTC", "ETH"))
    lines += [
        "",
        f"**Baseline (BTC+ETH): {base_epy:.2f} entries/yr.  All 15 symbols: "
        f"{total_epy:.2f} entries/yr — additional {total_epy - base_epy:.2f}/yr from the "
        "13 expansion candidates (recent-rate weighting differs; see caveats).**",
        "",
        "## Caveats (read before acting)",
        "- MULTIPLE TESTING: 15 symbols were scanned against one gate; picking the",
        "  best-looking symbols after the fact is selection. Any expansion choice must",
        "  be pre-registered and validated out-of-sample, not cherry-picked from this table.",
        "- Entries/yr is a WHOLE-HISTORY average; funding regimes decay (2020-21 alt",
        "  funding was far richer than 2024-26). Recent-years-only rates will be lower.",
        "- Basis PnL assumed 0 and microstructure entry gates (depth/spread/basis/",
        "  time-to-funding/atomic-fill) are NOT replayable from funding history alone —",
        "  live-qualifying entries will be FEWER than these counts (upper bound).",
        "- Some symbols have 4h funding periods in parts of their history (Binance",
        "  interval switches); a settlement there is 4h, not 8h — flagged per symbol",
        "  where >1% of intervals are sub-6h. Mean funding is per SETTLEMENT.",
        "- Cost model is the pessimistic shipped taker/taker baseline; maker-first",
        "  execution reduces the floor slightly (see f1_replay_historical scenarios).",
        "- The Rev-5 universe latch (F1_DEFAULT_SYMBOLS=BTC,ETH) still stands; this",
        "  scan is evidence for a proposal, not a config change.",
    ]
    flagged = [r for r in results if r["sub6h_interval_pct"] > 1.0]
    if flagged:
        lines.append("- Sub-6h funding intervals detected (share of history): "
                     + ", ".join(f"{r['symbol']} {r['sub6h_interval_pct']:.0f}%"
                                 for r in flagged))
    if errors:
        lines.append("- Skipped/failed symbols: "
                     + "; ".join(f"{s}: {e}" for s, e in errors))
    lines += [
        "",
        "PAPER/LAB only — this scan places no orders and changes no bot state.",
        "",
    ]
    return "\n".join(lines)


def main() -> None:
    import ccxt

    exchange = ccxt.binanceusdm({"enableRateLimit": True})
    results, errors = [], []
    for sym in SYMBOLS:
        try:
            r = scan_symbol(exchange, sym)
            if "error" in r:
                errors.append((sym, r["error"]))
                print(f"[scan] {sym}: SKIP ({r['error']})")
            else:
                results.append(r)
                print(f"[scan] {sym}: {r['n_settlements']} settlements, "
                      f"{r['years']:.2f}y, {r['n_entries']} entries "
                      f"({r['entries_per_year']:.2f}/yr)")
        except Exception as exc:  # honest failure, never fabricate
            errors.append((sym, f"{type(exc).__name__}: {exc}"))
            print(f"[scan] {sym}: FAILED ({type(exc).__name__}: {exc})")

    report = build_report(results, errors)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n[scan] report -> {REPORT_PATH}")

    payload = {
        "cost_frac_bps": COST_FRAC * 1e4,
        "floor_bps": FLOOR_BPS,
        "rows": [
            {
                "symbol": r["symbol"],
                "years_of_history": round(r["years"], 2),
                "n_entries": r["n_entries"],
                "qualifying_entries_per_year": round(r["entries_per_year"], 3),
                "mean_funding_while_qualified_8h_pct": (
                    round(r["mean_funding_while_qualified_pct"], 5)
                    if r["mean_funding_while_qualified_pct"] is not None else None
                ),
                "est_net_per_cycle_usd_at_150_notional": (
                    round(r["mean_net_per_cycle_usd"], 4)
                    if r["mean_net_per_cycle_usd"] is not None else None
                ),
                "avg_hold_settlements": (
                    round(r["avg_hold_settlements"], 1)
                    if r["avg_hold_settlements"] is not None else None
                ),
                "win_cycles": r["win_cycles"],
                "sub6h_interval_pct": round(r["sub6h_interval_pct"], 1),
            }
            for r in results
        ],
        "errors": [{"symbol": s, "error": e} for s, e in errors],
    }
    print("RESULT_JSON " + json.dumps(payload))


if __name__ == "__main__":
    # Keep cwd-independence: report path is absolute via ROOT.
    os.chdir(ROOT)
    main()
