#!/usr/bin/env python3
"""research/screen_vpin_jump_veto.py — AFTER-COST screen for frozen prereg 27.

Owner override 2026-07-25: full screen (Stage-0 skipped). Expectation NO_GO.

Treatment: veto AccBand OPEN when VPIN_t > θ ∈ {0.55,0.60,0.65,0.70}.
Baseline: identical AccBand resolved R multiples (13_band_conditional_rows).
Primary: after-cost Δ mean R (veto kept − baseline). Multiplicity n_trials=4.

Research path only. Never touches live decision code.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.decision.monte_carlo import monte_carlo_trade_sequence  # noqa: E402

WS = ROOT / "_workspace" / "strategy_pipeline"
PREREG_MD = WS / "27_prereg_vpin_jump_veto.md"
PREREG_JSON = WS / "27_prereg_vpin_jump_veto.json"
ROWS_CSV = WS / "13_band_conditional_rows.csv"
VPIN_DIR = ROOT / "data" / "aggtrades_vpin"
OUT_MD = WS / "27_screen_vpin_jump_veto.md"
OUT_JSON = WS / "27_screen_vpin_jump_veto.json"
REPORTS_MD = ROOT / "reports" / "screen_vpin_jump_veto_2026-07-25.md"
REPORTS_JSON = ROOT / "reports" / "screen_vpin_jump_veto_2026-07-25.json"

THETA_GRID = (0.55, 0.60, 0.65, 0.70)
MIN_N = 30
MC_P = 0.95
MC_DD = 0.25
N_RESAMPLES = 2000
SEED = 27


def _verify_prereg() -> str:
    expected = json.loads(PREREG_JSON.read_text(encoding="utf-8"))["sha256_md"]
    got = hashlib.sha256(PREREG_MD.read_bytes()).hexdigest()
    if got != expected:
        raise RuntimeError(f"prereg hash mismatch: {got} != {expected}")
    return got


def _load_band_rows() -> pd.DataFrame:
    df = pd.read_csv(ROWS_CSV)
    df = df[df["venue"] == "binance"].copy()
    # Perpetual BTC/ETH only (drop dated futures suffixes)
    df = df[df["symbol"].isin(["BTC/USDT:USDT", "ETH/USDT:USDT"])].copy()
    df["base"] = df["symbol"].map(
        {"BTC/USDT:USDT": "BTCUSDT", "ETH/USDT:USDT": "ETHUSDT"}
    )
    df["ts_ms"] = (df["ts"].astype(np.int64) * 1000).astype(np.int64)
    df = df.dropna(subset=["r_multiple", "ts_ms", "base"])
    return df.reset_index(drop=True)


def _load_vpin(symbol: str) -> pd.DataFrame:
    path = VPIN_DIR / f"{symbol}_vpin.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"missing {path} — run: python scripts/harvest_aggtrades_vpin.py"
        )
    v = pd.read_parquet(path)
    # Only use VPIN after N=50 buckets filled (prereg)
    v = v[v["n_closed"] >= 50].copy()
    v = v.sort_values("ts_ms").reset_index(drop=True)
    return v


def _attach_vpin(rows: pd.DataFrame) -> pd.DataFrame:
    parts = []
    for sym, g in rows.groupby("base"):
        v = _load_vpin(sym)
        # asof join: last VPIN bucket closed strictly before decision ts
        g = g.sort_values("ts_ms")
        merged = pd.merge_asof(
            g,
            v[["ts_ms", "vpin"]].rename(columns={"ts_ms": "vpin_ts_ms"}),
            left_on="ts_ms",
            right_on="vpin_ts_ms",
            direction="backward",
            allow_exact_matches=False,  # buckets closed BEFORE open
        )
        parts.append(merged)
    out = pd.concat(parts, ignore_index=True)
    return out


def _holm_reject(pvals: list[float], alpha: float = 0.05) -> list[bool]:
    """Holm–Bonferroni on one-sided ΔEV>0 tests (p from bootstrap)."""
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    reject = [False] * m
    for rank, i in enumerate(order, start=1):
        thr = alpha / (m - rank + 1)
        if pvals[i] <= thr:
            reject[i] = True
        else:
            break
    return reject


def _delta_ev_bootstrap(
    baseline: np.ndarray, kept: np.ndarray, n: int = 2000, seed: int = 1
) -> tuple[float, float]:
    """Return (delta_mean, one-sided p that delta<=0) via paired? independent block.

    Veto arm is a SUBSET of baseline trades (kept). Δ = mean(kept) − mean(baseline).
    Bootstrap both with replacement independently (conservative).
    """
    rng = np.random.default_rng(seed)
    if kept.size == 0 or baseline.size == 0:
        return float("nan"), 1.0
    base_mean = float(baseline.mean())
    kept_mean = float(kept.mean())
    delta = kept_mean - base_mean
    diffs = np.empty(n)
    for i in range(n):
        b = rng.choice(baseline, size=baseline.size, replace=True).mean()
        k = rng.choice(kept, size=kept.size, replace=True).mean()
        diffs[i] = k - b
    p = float(np.mean(diffs <= 0.0))
    return delta, p


def screen(df: pd.DataFrame) -> dict:
    base_rets = df["r_multiple"].to_numpy(dtype=float)
    baseline_mean = float(base_rets.mean())
    baseline_wr = float(np.mean(base_rets > 0))
    baseline_mc = monte_carlo_trade_sequence(
        base_rets, block_len=5, n_resamples=N_RESAMPLES, seed=SEED
    )

    results = []
    for theta in THETA_GRID:
        masked = df["vpin"].notna()
        fire = masked & (df["vpin"] > theta)
        kept = df.loc[masked & ~fire, "r_multiple"].to_numpy(dtype=float)
        skipped = df.loc[fire, "r_multiple"].to_numpy(dtype=float)
        n_kept = int(kept.size)
        n_skipped = int(skipped.size)
        n_events = n_kept + n_skipped
        covered = int(masked.sum())

        cell = {
            "theta": theta,
            "n_covered": covered,
            "n_kept": n_kept,
            "n_skipped": n_skipped,
            "n_events": n_events,
            "fire_rate": float(n_skipped / covered) if covered else float("nan"),
            "baseline_mean_R": baseline_mean,
            "kept_mean_R": float(kept.mean()) if n_kept else float("nan"),
            "skipped_mean_R": float(skipped.mean()) if n_skipped else float("nan"),
            "baseline_wr": baseline_wr,
            "kept_wr": float(np.mean(kept > 0)) if n_kept else float("nan"),
            "delta_mean_R": float("nan"),
            "delta_p_le_0": 1.0,
            "mc_p_pos": float("nan"),
            "mc_maxdd_p95": float("nan"),
            "gates": {},
            "verdict": "INSUFFICIENT_DATA",
        }

        if n_events < MIN_N or n_kept < 5:
            cell["verdict"] = "INSUFFICIENT_DATA"
            results.append(cell)
            continue

        delta, p_le0 = _delta_ev_bootstrap(base_rets, kept, n=N_RESAMPLES, seed=SEED)
        cell["delta_mean_R"] = delta
        cell["delta_p_le_0"] = p_le0

        mc = monte_carlo_trade_sequence(
            kept, block_len=5, n_resamples=N_RESAMPLES, seed=SEED + 1
        )
        cell["mc_p_pos"] = mc.p_total_positive
        cell["mc_maxdd_p95"] = mc.max_drawdown_p95

        g_delta = delta > 0
        g_mc = mc.p_total_positive >= MC_P
        g_dd = mc.max_drawdown_p95 <= MC_DD
        g_n = n_events >= MIN_N
        # Bleed-mask: WR up but EV down → NO_GO
        bleed = (cell["kept_wr"] > baseline_wr) and (delta <= 0)
        cell["gates"] = {
            "delta_ev_gt_0": g_delta,
            "mc_p_pos": g_mc,
            "mc_maxdd_p95": g_dd,
            "min_n": g_n,
            "bleed_mask": bleed,
        }
        if bleed or not (g_delta and g_mc and g_dd and g_n):
            cell["verdict"] = "NO_GO"
        else:
            cell["verdict"] = "CANDIDATE"  # pending Holm + adjacent-sign
        results.append(cell)

    # Multiplicity + adjacent-θ same-sign
    pvals = [c["delta_p_le_0"] if math.isfinite(c["delta_p_le_0"]) else 1.0 for c in results]
    holm = _holm_reject(pvals, alpha=0.05)
    for i, c in enumerate(results):
        c["holm_reject_H0"] = bool(holm[i])
        if c["verdict"] != "CANDIDATE":
            continue
        # adjacent same-sign on ΔEV
        neighbors = []
        if i > 0:
            neighbors.append(results[i - 1]["delta_mean_R"])
        if i + 1 < len(results):
            neighbors.append(results[i + 1]["delta_mean_R"])
        same = all(
            math.isfinite(n) and ((n > 0) == (c["delta_mean_R"] > 0)) for n in neighbors
        ) if neighbors else False
        c["adjacent_same_sign"] = same
        if c["holm_reject_H0"] and same and c["delta_mean_R"] > 0:
            c["verdict"] = "GO"
        else:
            c["verdict"] = "NO_GO"

    go_any = any(c["verdict"] == "GO" for c in results)
    insuff = all(c["verdict"] == "INSUFFICIENT_DATA" for c in results)
    overall = "GO" if go_any else ("INSUFFICIENT_DATA" if insuff else "NO_GO")

    return {
        "candidate": "vpin_jump_veto_v1",
        "hypothesis": (
            "High VPIN at AccBand decision time does not improve after-cost "
            "expectancy vs no-veto baseline (null); reject null only if gates pass."
        ),
        "expectation": "NO_GO",
        "owner_override": "full_screen_skip_stage0",
        "prereg_sha256_md": _verify_prereg(),
        "n_baseline": int(len(df)),
        "n_with_vpin": int(df["vpin"].notna().sum()),
        "baseline_mean_R": baseline_mean,
        "baseline_wr": baseline_wr,
        "baseline_mc": {
            "p_total_positive": baseline_mc.p_total_positive,
            "max_drawdown_p95": baseline_mc.max_drawdown_p95,
        },
        "theta_results": results,
        "verdict": overall,
        "gates": {
            "delta_ev_oos_gt_0": True,
            "mc_p_pos": MC_P,
            "mc_maxdd_p95": MC_DD,
            "min_n_oos": MIN_N,
            "n_trials": 4,
            "multiplicity": "holm",
            "adjacent_theta_same_sign": True,
        },
        "generated_utc": datetime.now(timezone.utc).isoformat(),
    }


def _write_md(payload: dict) -> str:
    lines = [
        "# 27 — Screen: VPIN jump-risk veto",
        "",
        f"**Generated:** {payload['generated_utc']}",
        f"**Prereg sha256_md:** `{payload['prereg_sha256_md']}`",
        f"**Owner override:** {payload['owner_override']} (`36_owner_override_vpin_full_screen.md`)",
        f"**Expectation:** {payload['expectation']}",
        f"**Overall verdict:** **{payload['verdict']}**",
        "",
        "## Substrate",
        "",
        f"- AccBand resolved rows (binance BTC/ETH perps): n={payload['n_baseline']}",
        f"- With pre-decision VPIN join: n={payload['n_with_vpin']}",
        f"- Baseline mean R: {payload['baseline_mean_R']:.4f}",
        f"- Baseline WR: {payload['baseline_wr']:.4f}",
        f"- Baseline MC P(>0): {payload['baseline_mc']['p_total_positive']:.3f}, "
        f"maxDD p95: {payload['baseline_mc']['max_drawdown_p95']:.3f}",
        "",
        "## θ grid results",
        "",
        "| θ | n_kept | n_skip | fire% | kept_R | ΔR | p(Δ≤0) | MC P>0 | maxDD p95 | verdict |",
        "|---|--------|--------|-------|--------|----|--------|--------|-----------|---------|",
    ]
    for c in payload["theta_results"]:
        lines.append(
            f"| {c['theta']:.2f} | {c['n_kept']} | {c['n_skipped']} | "
            f"{c['fire_rate']:.3f} | {c['kept_mean_R']:.4f} | {c['delta_mean_R']:.4f} | "
            f"{c['delta_p_le_0']:.3f} | {c['mc_p_pos']:.3f} | {c['mc_maxdd_p95']:.3f} | "
            f"**{c['verdict']}** |"
        )
    lines += [
        "",
        "## Gates (frozen)",
        "",
        "- ΔEV (kept − baseline) > 0 OOS",
        "- MC P(total>0) ≥ 0.95 on kept arm",
        "- MC maxDD p95 ≤ 0.25",
        "- n skipped+kept ≥ 30",
        "- Holm multiplicity on n_trials=4; adjacent-θ same-sign",
        "- Bleed-mask: WR↑ with EV↓ → NO_GO",
        "",
        "## Non-goals",
        "",
        "- No live install / MCP wire from this screen alone.",
        "- Directional VPIN remains ledger STOP.",
        "",
    ]
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--smoke", action="store_true", help="print join coverage only")
    args = ap.parse_args()

    print("Verifying prereg hash...")
    sha = _verify_prereg()
    print(f"  OK {sha[:16]}…")

    print("Loading AccBand substrate...")
    rows = _load_band_rows()
    print(f"  n={len(rows)} BTC/ETH binance perps")

    print("Joining VPIN (no lookahead)...")
    df = _attach_vpin(rows)
    cov = float(df["vpin"].notna().mean())
    print(f"  VPIN coverage={cov:.3f}  mean_vpin={df['vpin'].mean():.4f}")

    if args.smoke:
        print(df[["base", "ts_ms", "vpin", "r_multiple"]].head(10).to_string())
        return 0

    print("Screening θ grid...")
    payload = screen(df)
    md = _write_md(payload)

    for path in (OUT_JSON, REPORTS_JSON):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    for path in (OUT_MD, REPORTS_MD):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(md, encoding="utf-8")

    print(f"Verdict: {payload['verdict']}")
    print(f"Wrote {OUT_MD}")
    print(f"Wrote {OUT_JSON}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
