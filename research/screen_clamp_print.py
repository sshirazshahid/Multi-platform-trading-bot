"""Clamp-print zero-information screen — prereg 39_ (sha256 dda32c8cf71d...).

Measurement-correctness overlay on F1. Proposes NO trade, NO order path.
Null H0: clamp prints (funding == regime-scaled venue baseline) predict the
sign of their own next settlement NO BETTER than contemporaneous non-clamp
positive prints. Decision statistic: Cochran-Mantel-Haenszel across
settlement-timestamp strata, per (venue, regime) cell, alpha = 0.05/12.

Prereg-binding details implemented here:
- R1: regime per row from the trailing median of up to 10 previous
  consecutive-ts deltas IN-FILE (min 3), snapped to nearest of {1,2,4,8}h.
  No global interval constant; no live-metadata lookup.
- R2: baseline clamps only (1e-4 * regime_h/8); cap clamps are out of scope.
- Stage-0: a cell is testable only with >= 30 informative strata (both arms
  non-empty at that timestamp); otherwise INSUFFICIENT_DATA. m stays 12.
- The run hard-aborts if the prereg file no longer matches the committed hash.

CLI:  ./venv/Scripts/python.exe research/screen_clamp_print.py
Reads data/funding_history/*.csv READ-ONLY; writes the screen artifact to
_workspace/strategy_pipeline/39_screen_clamp_print.{json,md}. The cell
verdicts here are the SCREEN output; the pipeline verdict is dual-model
(both-agree) and owner-visible — nothing here writes the ledger.
"""
from __future__ import annotations

import datetime as _dt
import hashlib
import json
import math
import statistics
import sys
import time
from pathlib import Path

import pandas as pd
from scipy.stats import chi2 as _chi2_dist

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data" / "funding_history"
WS = ROOT / "_workspace" / "strategy_pipeline"
PREREG_PATH = WS / "39_prereg_clamp_print_information_v2.md"
PREREG_SHA256 = "dda32c8cf71d9324a1ee38ec11f5fe863397a98e71b1adaee5d8ed1249626641"

BASELINE_8H = 1e-4          # venue baseline at the 8h regime (prereg R1)
EPS = 1e-9
REGIMES = (1, 2, 4, 8)      # hours
VENUES = ("binance", "bybit", "bitget")
M_CELLS = 12                # FIXED; Stage-0 attrition never shrinks it
ALPHA = 0.05 / M_CELLS
MIN_INFORMATIVE_STRATA = 30


# ── R1: regime derivation ────────────────────────────────────────────────────

def add_regime_hours(df: pd.DataFrame) -> pd.DataFrame:
    """Attach regime_h per row from in-file trailing deltas (prereg R1).

    For row i of a symbol, the available deltas are those between rows
    1..i (each observable at its own settlement). Regime = median of the
    last 10, snapped to the nearest of {1,2,4,8}h; None with <3 deltas.
    """
    out = df.copy()
    out["regime_h"] = None
    out["regime_h"] = out["regime_h"].astype(object)
    for _, g in out.groupby("symbol", sort=False):
        g = g.sort_values("ts")
        ts = g["ts"].tolist()
        deltas = [(ts[k] - ts[k - 1]) / 3600.0 for k in range(1, len(ts))]
        regimes: list = []
        for i in range(len(ts)):
            window = deltas[:i][-10:]
            if len(window) < 3:
                regimes.append(None)
            else:
                med = statistics.median(window)
                regimes.append(min(REGIMES, key=lambda h: abs(med - h)))
        out.loc[g.index, "regime_h"] = regimes
    return out


# ── R1/R2: clamp classification ─────────────────────────────────────────────

def classify_clamp(rate, regime_h) -> bool:
    """True iff the printed rate equals the regime-scaled venue baseline."""
    if rate is None or regime_h is None:
        return False
    try:
        r = float(rate)
        h = float(regime_h)
    except (TypeError, ValueError):
        return False
    if not math.isfinite(r) or r <= 0:
        return False
    return abs(r - BASELINE_8H * (h / 8.0)) < EPS


# ── strata construction ─────────────────────────────────────────────────────

def build_cell_strata(df: pd.DataFrame) -> dict:
    """(venue, regime_h) -> list of (a,b,c,d) per informative settlement ts.

    a/b: clamp-arm successes/failures; c/d: control-arm (positive, non-clamp).
    Outcome = sign of the SAME symbol's next settlement print (> 0 = success).
    A stratum is informative only when BOTH arms are non-empty (prereg §5/§6).
    """
    # Outcome linkage is per (venue, symbol): the SAME instrument's own next
    # settlement. Grouping by symbol alone leaked outcomes across venues for
    # multi-venue symbols (Codex INVALID_RUN finding, 2026-07-28) — 66.9% of
    # eligible rows linked cross-venue, 32.3% at the same timestamp.
    d = df.sort_values(["venue", "symbol", "ts"]).copy()
    d["next_rate"] = d.groupby(["venue", "symbol"])["funding_rate"].shift(-1)
    d = d[d["regime_h"].notna() & d["next_rate"].notna()]
    d = d[d["funding_rate"] > 0]
    if d.empty:
        return {}
    d["clamp"] = [classify_clamp(r, h)
                  for r, h in zip(d["funding_rate"], d["regime_h"])]
    d["success"] = d["next_rate"] > 0
    cells: dict = {}
    for (venue, reg), g in d.groupby(["venue", "regime_h"]):
        strata = []
        for _, s in g.groupby("ts"):
            cl = s[s["clamp"]]
            ct = s[~s["clamp"]]
            if len(cl) == 0 or len(ct) == 0:
                continue
            a = int(cl["success"].sum())
            b = int(len(cl) - a)
            c = int(ct["success"].sum())
            dd = int(len(ct) - c)
            strata.append((a, b, c, dd))
        if strata:
            cells[(str(venue), int(reg))] = strata
    return cells


# ── CMH decision statistic ──────────────────────────────────────────────────

def cmh_test(strata):
    """Cochran-Mantel-Haenszel chi-square (no continuity correction) + MH OR.

    Returns (chi2, p, or_mh, n_strata_used). p is None when untestable
    (no strata, or zero variance).
    """
    usable = [s for s in strata if sum(s) > 1]
    if not usable:
        return 0.0, None, None, 0
    num = 0.0
    var = 0.0
    or_num = 0.0
    or_den = 0.0
    for a, b, c, d in usable:
        n = a + b + c + d
        n1, n2 = a + b, c + d
        m1, m2 = a + c, b + d
        num += a - (n1 * m1) / n
        var += (n1 * n2 * m1 * m2) / (n * n * (n - 1))
        or_num += (a * d) / n
        or_den += (b * c) / n
    if or_den > 0:
        or_mh = or_num / or_den
    elif or_num > 0:
        or_mh = math.inf
    else:
        or_mh = None
    if var <= 0:
        return 0.0, None, or_mh, len(usable)
    chi2 = (num * num) / var
    return chi2, float(_chi2_dist.sf(chi2, 1)), or_mh, len(usable)


# ── real-data run ───────────────────────────────────────────────────────────

def _load_file(path: Path):
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    cols = {str(c).lower().strip(): c for c in df.columns}
    if "ts" not in cols or "funding_rate" not in cols:
        return None
    stem = path.stem
    fallback_venue = stem.split("_", 1)[0]
    fallback_symbol = stem.split("_", 1)[1] if "_" in stem else stem
    out = pd.DataFrame({
        "ts": pd.to_numeric(df[cols["ts"]], errors="coerce"),
        "funding_rate": pd.to_numeric(df[cols["funding_rate"]], errors="coerce"),
        "venue": df[cols["venue"]].astype(str) if "venue" in cols else fallback_venue,
        "symbol": df[cols["symbol"]].astype(str) if "symbol" in cols else fallback_symbol,
    })
    out = out.dropna(subset=["ts", "funding_rate"])
    out = out.drop_duplicates(subset=["ts"]).sort_values("ts")
    return out if len(out) else None


def main() -> int:
    got = hashlib.sha256(PREREG_PATH.read_bytes()).hexdigest()
    if got != PREREG_SHA256:
        print(f"ABORT: prereg hash mismatch\n  expected {PREREG_SHA256}\n  got      {got}")
        return 2
    t0 = time.time()
    files = sorted(DATA_DIR.glob("*.csv"))
    frames, skipped = [], 0
    for f in files:
        df = _load_file(f)
        if df is None:
            skipped += 1
            continue
        frames.append(add_regime_hours(df))
    big = pd.concat(frames, ignore_index=True)
    cells = build_cell_strata(big)

    results = []
    for venue in VENUES:
        for reg in REGIMES:
            strata = cells.get((venue, reg), [])
            n_inf = len(strata)
            row = {"venue": venue, "regime_h": reg, "informative_strata": n_inf,
                   "clamp_n": int(sum(a + b for a, b, *_ in strata)),
                   "control_n": int(sum(c + d for *_, c, d in strata))}
            if n_inf < MIN_INFORMATIVE_STRATA:
                row.update({"cell_verdict": "INSUFFICIENT_DATA", "chi2": None,
                            "p": None, "or_mh": None})
            else:
                chi2, p, or_mh, used = cmh_test(strata)
                a_tot = sum(s[0] for s in strata)
                ab_tot = sum(s[0] + s[1] for s in strata)
                c_tot = sum(s[2] for s in strata)
                cd_tot = sum(s[2] + s[3] for s in strata)
                row.update({
                    "chi2": round(chi2, 4) if chi2 is not None else None,
                    "p": p, "strata_used": used,
                    "or_mh": (None if or_mh is None
                              else ("inf" if or_mh == math.inf else round(or_mh, 4))),
                    "clamp_success_rate": round(a_tot / ab_tot, 4) if ab_tot else None,
                    "control_success_rate": round(c_tot / cd_tot, 4) if cd_tot else None,
                    "cell_verdict": ("FALSIFIED" if (p is not None and p < ALPHA)
                                     else "NULL_RETAINED"),
                })
            results.append(row)

    artifact = {
        "prereg": str(PREREG_PATH.name),
        "prereg_sha256": PREREG_SHA256,
        "generated_utc": _dt.datetime.now(_dt.timezone.utc).isoformat(),
        "alpha_per_cell": ALPHA, "m_cells": M_CELLS,
        "min_informative_strata": MIN_INFORMATIVE_STRATA,
        "files_total": len(files), "files_skipped": skipped,
        "rows_total": int(len(big)),
        "runtime_s": round(time.time() - t0, 1),
        "cells": results,
        "verdict": "PENDING_DUAL_MODEL",
        "note": ("Cell verdicts are the screen computation only. The pipeline "
                 "verdict requires both-agree (Fable + Codex) and is owner-"
                 "visible; no ledger row is written by this script."),
    }
    (WS / "39_screen_clamp_print.json").write_text(
        json.dumps(artifact, indent=1), encoding="utf-8")

    md = [f"# 39 — Screen run: clamp-print zero-information (prereg sha256 {PREREG_SHA256[:12]})",
          f"Generated {artifact['generated_utc']} | files {len(files)} "
          f"(skipped {skipped}) | rows {len(big):,} | runtime {artifact['runtime_s']}s",
          f"alpha/cell = {ALPHA:.6f} (0.05/{M_CELLS}) | Stage-0 floor = "
          f"{MIN_INFORMATIVE_STRATA} informative strata", "",
          "| venue | regime | strata | clamp_n | ctrl_n | chi2 | p | OR_MH | clampWR | ctrlWR | cell verdict |",
          "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in results:
        fmt = {k: (f"{v:.3g}" if isinstance(v, float) else ("-" if v is None else v))
               for k, v in r.items()}
        md.append("| {venue} | {regime_h}h | {informative_strata} | {clamp_n} | {control_n} "
                  "| {chi2} | {p} | {or_mh} | {cwr} | {xwr} | {cell_verdict} |".format(
                      cwr=fmt.get("clamp_success_rate", "-"),
                      xwr=fmt.get("control_success_rate", "-"), **fmt))
    md += ["", "**Screen verdict: PENDING_DUAL_MODEL** — cell computations above are the",
           "screen output; the pipeline verdict needs both-agree (Fable + Codex).",
           "No ledger row is written by this run."]
    (WS / "39_screen_clamp_print.md").write_text("\n".join(md) + "\n", encoding="utf-8")

    print(f"screen complete: {len(big):,} rows, {artifact['runtime_s']}s "
          f"-> {WS / '39_screen_clamp_print.md'}")
    for r in results:
        print("  {venue:8s} {regime_h}h  strata={informative_strata:<6} {cell_verdict}".format(**r))
    return 0


if __name__ == "__main__":
    sys.exit(main())
