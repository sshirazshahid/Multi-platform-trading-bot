"""Screen 68 — TP-geometry replay on realized MFE/MAE (PAPER cohort).

Implements _workspace/strategy_pipeline/68_prereg_tp_geometry_replay.md
EXACTLY. That prereg was hashed and committed (67b46f1,
sha256 4a848c84d4d7c1f98bcbbd52857649f7ab7cb269356736669a7200f033fe1a7a)
BEFORE this file existed and before any outcome was computed.

Question: does ANY fixed TP multiple k (TP = k x SL_distance), applied to
the same realized entries, yield positive after-cost expectancy?

Read-only (`mode=ro`). Places no trade, changes no config, never imported by
the bot. Per prereg §7 a GO would authorize only a per-bar OHLCV re-run —
never a runtime flag.

Run: venv/Scripts/python.exe research/screen_tp_geometry_replay.py
"""

from __future__ import annotations

import hashlib
import math
import pathlib
import sqlite3
import statistics as st

DB = "file:data/warehouse.sqlite?mode=ro"
PREREG = pathlib.Path("_workspace/strategy_pipeline/68_prereg_tp_geometry_replay.md")
PREREG_SHA = "4a848c84d4d7c1f98bcbbd52857649f7ab7cb269356736669a7200f033fe1a7a"

# ── Frozen constants (prereg §5, §6) ─────────────────────────────────────────
FEE_BPS_PER_SIDE = 6.0
OPEN_SLIP_BPS = 5.0
EXIT_SLIP_BPS = 5.0
SL_SLIP_BPS = 10.0
K_GRID = (0.35, 0.45, 0.6, 0.8, 1.0, 1.25, 1.5, 2.0, 2.5, 3.0)
M_TESTS = len(K_GRID)
ALPHA = 0.05 / M_TESTS          # 0.005 Bonferroni
Z_CORRECTED = 2.807             # two-sided z at alpha=0.005
MIN_N_EFFECTIVE = 100           # prereg §7


def verify_prereg() -> None:
    """Refuse to run if the prereg changed after it was hashed."""
    actual = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if actual != PREREG_SHA:
        raise SystemExit(
            f"PREREG HASH MISMATCH\n  expected {PREREG_SHA}\n  actual   {actual}\n"
            "The frozen spec was edited. That is a NEW pre-registration — "
            "this screen's result would be inadmissible."
        )
    print(f"prereg hash OK: {actual[:16]}…")


def load_population() -> list:
    """The frozen population (prereg §3). Read-only."""
    c = sqlite3.connect(DB, uri=True)
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT symbol, side, entry_px, entry_stop_px, mfe, mae "
        "FROM trades WHERE status='CLOSED' AND mfe IS NOT NULL "
        "  AND mae IS NOT NULL AND entry_stop_px > 0 AND entry_px > 0"
    ).fetchall()
    out = []
    for r in rows:
        ep, sp = float(r["entry_px"]), float(r["entry_stop_px"])
        sl_frac = abs(ep - sp) / ep
        if sl_frac <= 0:
            continue
        out.append({
            "symbol": r["symbol"], "side": r["side"], "sl_frac": sl_frac,
            "mfe": abs(float(r["mfe"])), "mae": abs(float(r["mae"])),
        })
    c.close()
    return out


def replay_arm(pop: list, k: float) -> dict:
    """One arm of the grid — the frozen replay model (prereg §4, §5)."""
    rets, n_sl, n_tp, n_time = [], 0, 0, 0
    for t in pop:
        sl_frac, tp_frac = t["sl_frac"], k * t["sl_frac"]
        hit_sl = t["mae"] >= sl_frac
        hit_tp = t["mfe"] >= tp_frac
        if hit_sl:                       # SL-FIRST conservative tie-break
            gross, exit_slip, n_sl = -sl_frac, SL_SLIP_BPS, n_sl + 1
        elif hit_tp:
            gross, exit_slip, n_tp = +tp_frac, EXIT_SLIP_BPS, n_tp + 1
        else:                            # time exit, booked flat (prereg §4)
            gross, exit_slip, n_time = 0.0, EXIT_SLIP_BPS, n_time + 1
        cost = (2 * FEE_BPS_PER_SIDE + OPEN_SLIP_BPS + exit_slip) / 10_000.0
        rets.append(gross - cost)
    n = len(rets)
    mean = st.mean(rets)
    se = st.pstdev(rets) / math.sqrt(n) if n else float("inf")
    return {
        "k": k, "n": n, "mean": mean, "se": se,
        "lo": mean - Z_CORRECTED * se, "hi": mean + Z_CORRECTED * se,
        "n_sl": n_sl, "n_tp": n_tp, "n_time": n_time, "total": sum(rets),
    }


def main() -> None:
    verify_prereg()
    pop = load_population()
    print("=" * 78)
    print("SCREEN 68 — TP-geometry replay (prereg 4a848c84…, commit 67b46f1)")
    print("=" * 78)
    print(f"population n = {len(pop)}   grid m = {M_TESTS}   "
          f"Bonferroni alpha = {ALPHA}  (z = {Z_CORRECTED})")
    print("time exits booked FLAT; SL-first tie-break; costs 12bps fee + "
          "5/5 slip (10 on stop)\n")

    hdr = (f"{'k':>5s} {'n':>5s} {'TPhit':>6s} {'SLhit':>6s} {'flat':>5s} "
           f"{'mean/trade':>12s} {'corrected 95% CI':>26s} {'verdict':>8s}")
    print(hdr)
    print("-" * len(hdr))

    results = [replay_arm(pop, k) for k in K_GRID]
    for r in results:
        passes = r["lo"] > 0 and r["n"] >= MIN_N_EFFECTIVE and r["k"] != K_GRID[-1]
        verdict = "PASS" if passes else ("neg" if r["mean"] <= 0 else "n.s.")
        print(f"{r['k']:5.2f} {r['n']:5d} {r['n_tp']:6d} {r['n_sl']:6d} "
              f"{r['n_time']:5d} {r['mean']:+12.6f} "
              f"[{r['lo']:+.6f}, {r['hi']:+.6f}] {verdict:>8s}")

    winners = [r for r in results
               if r["lo"] > 0 and r["n"] >= MIN_N_EFFECTIVE and r["k"] != K_GRID[-1]]
    best = max(results, key=lambda r: r["mean"])

    print("\n" + "=" * 78)
    if winners:
        print(f"VERDICT: {len(winners)} arm(s) pass the frozen gate — k = "
              f"{[w['k'] for w in winners]}")
        print("NOT decision-grade (prereg §4): MFE/MAE are path-agnostic and")
        print("this model is OPTIMISTIC for wide TPs. Authorizes ONLY a per-bar")
        print("OHLCV re-run. No runtime flag may change on this result.")
    else:
        print("VERDICT: NO_GO — no arm produces positive after-cost expectancy")
        print("at the corrected threshold. This matches the pre-registered")
        print("expectation (prereg §8) and is SOUND: the replay model is tilted")
        print("in H1's favour, so failing to find edge under it is strong")
        print("evidence that the ENTRIES carry no directional edge.")
    print(f"\nbest arm by raw mean: k={best['k']} mean={best['mean']:+.6f} "
          f"(needs > {Z_CORRECTED} SE = {Z_CORRECTED * best['se']:.6f} to pass)")
    print("=" * 78)


if __name__ == "__main__":
    main()
