#!/usr/bin/env python3
"""research/screen_accband_frac_dual_goal.py — score frozen prereg cells.

Uses the AFTER-COST AccBand geometry sim published in
``_workspace/strategy_pipeline/05_accuracy_band_sim.md`` (same resolver
stack as ``research/sim_accuracy_band.py``, n≈8700 resolved). Side-split
cells are blended from the sim's per-side rows at the cell's buy/sell fracs.

Joint GO (all required): BE_WR≤0.59 ∧ WR∈[0.59,0.67] ∧ EV>0 ∧ PF>1.
Expectation: NO_GO — documents that AccBand cannot deliver the dual goal
on a no-edge signal.

Does NOT fetch candles or touch order paths. Research-only.
"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PREREG_JSON = ROOT / "_workspace/strategy_pipeline/30_prereg_accband_frac_dual_goal.json"
PREREG_MD = ROOT / "_workspace/strategy_pipeline/30_prereg_accband_frac_dual_goal.md"
OUT_JSON = ROOT / "_workspace/strategy_pipeline/30_screen_accband_frac_dual_goal.json"
OUT_MD = ROOT / "_workspace/strategy_pipeline/30_screen_accband_frac_dual_goal.md"

# Published overall curve (05_accuracy_band_sim.md) — after-cost resolved.
OVERALL = {
    0.35: dict(n=8743, wr=0.657, exp_r=-0.242, avg_win_r=0.275, avg_loss_r=1.234),
    0.40: dict(n=8736, wr=0.649, exp_r=-0.243, avg_win_r=0.293, avg_loss_r=1.232),
    0.45: dict(n=8731, wr=0.638, exp_r=-0.244, avg_win_r=0.315, avg_loss_r=1.229),
    0.50: dict(n=8729, wr=0.627, exp_r=-0.244, avg_win_r=0.342, avg_loss_r=1.227),
    0.55: dict(n=8725, wr=0.613, exp_r=-0.245, avg_win_r=0.374, avg_loss_r=1.224),
    0.60: dict(n=8715, wr=0.598, exp_r=-0.246, avg_win_r=0.410, avg_loss_r=1.222),
    0.70: dict(n=8698, wr=0.569, exp_r=-0.246, avg_win_r=0.490, avg_loss_r=1.218),
}

# Per-side WR from 05 (n approx constant across fracs).
SIDE_WR = {
    0.35: dict(buy_n=5245, buy_wr=0.691, sell_n=3498, sell_wr=0.607),
    0.40: dict(buy_n=5238, buy_wr=0.688, sell_n=3498, sell_wr=0.591),
    0.45: dict(buy_n=5235, buy_wr=0.679, sell_n=3496, sell_wr=0.577),
    0.50: dict(buy_n=5233, buy_wr=0.670, sell_n=3496, sell_wr=0.562),
    0.55: dict(buy_n=5229, buy_wr=0.659, sell_n=3496, sell_wr=0.544),
    0.60: dict(buy_n=5219, buy_wr=0.651, sell_n=3496, sell_wr=0.519),
    0.70: dict(buy_n=5204, buy_wr=0.629, sell_n=3494, sell_wr=0.480),
}


def _interp_overall(frac: float) -> dict:
    """Linear interpolate overall metrics for fracs between published knots."""
    keys = sorted(OVERALL)
    if frac in OVERALL:
        return dict(OVERALL[frac])
    if frac < keys[0] or frac > keys[-1]:
        # Extrapolate from last segment (used for 0.80).
        a, b = keys[-2], keys[-1]
    else:
        a = max(k for k in keys if k <= frac)
        b = min(k for k in keys if k >= frac)
        if a == b:
            return dict(OVERALL[a])
    t = (frac - a) / (b - a)
    out = {}
    for k in ("n", "wr", "exp_r", "avg_win_r", "avg_loss_r"):
        out[k] = OVERALL[a][k] + t * (OVERALL[b][k] - OVERALL[a][k])
    out["n"] = int(round(out["n"]))
    out["extrapolated"] = frac not in OVERALL
    return out


def _nearest_side(frac: float) -> dict:
    keys = sorted(SIDE_WR)
    k = min(keys, key=lambda x: abs(x - frac))
    return dict(SIDE_WR[k])


def score_metrics(wr: float, exp_r: float, avg_win_r: float, avg_loss_r: float, n: int) -> dict:
    al = abs(avg_loss_r)
    aw = abs(avg_win_r)
    be = al / (aw + al) if (aw + al) > 0 else 1.0
    pf = (wr * aw) / ((1.0 - wr) * al) if (1.0 - wr) * al > 0 else 0.0
    gates = {
        "breakeven_wr_le_059": be <= 0.59,
        "wr_in_band": 0.59 <= wr <= 0.67,
        "expectancy_gt_0": exp_r > 0.0,
        "profit_factor_gt_1": pf > 1.0,
        "n_ge_80": n >= 80,
    }
    return {
        "n": n,
        "wr": round(wr, 4),
        "breakeven_wr": round(be, 4),
        "win_loss_ratio": round(aw / al, 4) if al else None,
        "expectancy_r": round(exp_r, 4),
        "profit_factor": round(pf, 4),
        "avg_win_r": round(aw, 4),
        "avg_loss_r": round(al, 4),
        "gates": gates,
        "joint_go": all(gates.values()),
    }


def score_cell(cell: dict) -> dict:
    buy_f = cell.get("tp_frac_buy")
    sell_f = cell.get("tp_frac_sell")
    g = float(cell["tp_frac_of_sl"])
    if buy_f is None and sell_f is None:
        m = _interp_overall(g)
        scored = score_metrics(m["wr"], m["exp_r"], m["avg_win_r"], m["avg_loss_r"], m["n"])
        scored["method"] = "overall_curve" + ("_extrapolated" if m.get("extrapolated") else "")
        return scored

    # Blend buy@buy_f with sell@sell_f using nearest published side WR;
    # expectancy/W-L from overall interp at each side frac, size-weighted.
    bf = float(buy_f if buy_f is not None else g)
    sf = float(sell_f if sell_f is not None else g)
    sb = _nearest_side(bf)
    ss = _nearest_side(sf)
    nb, ns = sb["buy_n"], ss["sell_n"]
    # Use side WR at nearest published frac for that side's frac.
    # Prefer exact SIDE_WR keys when present.
    def side_wr(frac: float, side: str) -> float:
        if frac in SIDE_WR:
            return SIDE_WR[frac][f"{side}_wr"]
        # interpolate
        keys = sorted(SIDE_WR)
        a = max(k for k in keys if k <= frac) if frac >= keys[0] else keys[0]
        b = min(k for k in keys if k >= frac) if frac <= keys[-1] else keys[-1]
        if a == b:
            return SIDE_WR[a][f"{side}_wr"]
        t = (frac - a) / (b - a)
        return SIDE_WR[a][f"{side}_wr"] + t * (SIDE_WR[b][f"{side}_wr"] - SIDE_WR[a][f"{side}_wr"])

    wr_b, wr_s = side_wr(bf, "buy"), side_wr(sf, "sell")
    n = nb + ns
    wr = (wr_b * nb + wr_s * ns) / n
    mb, ms = _interp_overall(bf), _interp_overall(sf)
    # Weight win/loss R by side counts (approx).
    aw = (mb["avg_win_r"] * nb + ms["avg_win_r"] * ns) / n
    al = (mb["avg_loss_r"] * nb + ms["avg_loss_r"] * ns) / n
    exp = (mb["exp_r"] * nb + ms["exp_r"] * ns) / n
    scored = score_metrics(wr, exp, aw, al, n)
    scored["method"] = "side_blend"
    scored["buy_frac"] = bf
    scored["sell_frac"] = sf
    return scored


def main() -> int:
    prereg = json.loads(PREREG_JSON.read_text(encoding="utf-8"))
    md_hash = hashlib.sha256(PREREG_MD.read_bytes()).hexdigest()
    body = {k: v for k, v in prereg.items() if not str(k).startswith("sha256")}
    body_hash = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    hash_ok = (
        md_hash == prereg.get("sha256_md")
        and body_hash == prereg.get("sha256_json_body")
    )

    cells_out = []
    for cell in prereg["cells"]:
        s = score_cell(cell)
        cells_out.append({"cell_id": cell["cell_id"], **cell, **s})

    go_cells = [c for c in cells_out if c["joint_go"]]
    verdict = "CONFIRMED_GO" if go_cells else "CONFIRMED_NO_GO"

    payload = {
        "screen_id": "accband_frac_dual_goal_2026-07-24",
        "generated_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "prereg_id": prereg["prereg_id"],
        "prereg_hash_ok": hash_ok,
        "sha256_md_observed": md_hash,
        "evidence_source": "05_accuracy_band_sim.md / research/sim_accuracy_band.py",
        "verdict": verdict,
        "go_cells": [c["cell_id"] for c in go_cells],
        "cells": cells_out,
        "honesty": (
            "AccBand WR-in-band and after-cost profit are mutually exclusive on "
            "the measured no-edge MCP path: every cell has expectancy_r≈-0.24 "
            "and breakeven_wr≥0.68. Dual goal requires EDGE, not frac retuning."
        ),
    }
    OUT_JSON.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")

    lines = [
        "# 30 — Screen: AccBand frac dual-goal",
        f"*Generated: {payload['generated_utc']} | Verdict: **{verdict}***",
        "",
        f"Prereg hash OK: `{hash_ok}` (md `{md_hash[:12]}…`).",
        "Evidence: published after-cost geometry sim (`05_accuracy_band_sim.md`).",
        "",
        "## Joint gates",
        "BE_WR≤0.59 ∧ WR∈[0.59,0.67] ∧ EV>0 ∧ PF>1 ∧ n≥80.",
        "",
        "| cell | frac | WR | BE_WR | W/L | EV(R) | PF | joint |",
        "|------|------|-----|-------|-----|-------|----|-------|",
    ]
    for c in cells_out:
        side = ""
        if c.get("tp_frac_buy") is not None:
            side = f" b{c['tp_frac_buy']}/s{c['tp_frac_sell']}"
        lines.append(
            f"| {c['cell_id']} | {c['tp_frac_of_sl']}{side} "
            f"| {c['wr']:.3f} | {c['breakeven_wr']:.3f} | {c['win_loss_ratio']:.3f} "
            f"| {c['expectancy_r']:+.3f} | {c['profit_factor']:.3f} | {c['joint_go']} |"
        )
    lines += [
        "",
        "## Verdict",
        f"**{verdict}** — 0/{len(cells_out)} cells clear joint gates.",
        payload["honesty"],
        "",
        "## Implication",
        "- Do **not** retune AccBand fracs expecting profit.",
        "- AccBand may still target WR-in-band for research; profit must come from",
        "  validated/evidence lanes (F1 carry, event probes) after promotion gates.",
        "- Mid-band research geometry remains ≈ global 0.50 / buy 0.45 / sell 0.35",
        "  (05 recommendation), with BAND_REGIME_FILTER on.",
        "",
    ]
    OUT_MD.write_text("\n".join(lines), encoding="utf-8")
    print(verdict, "go=", go_cells, "wrote", OUT_MD)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
