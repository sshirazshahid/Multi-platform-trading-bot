# scripts/run_alpha_search.py
"""Alpha-search orchestrator.

FROZEN PRE-REGISTRATION — do not change between data collection and run:
"""
from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path

from core.alpha_zoo import alphas as alpha_mod
from core.alpha_zoo import screen
from core.alpha_zoo.panel import Panel, build_panel, split_panel

# ── FROZEN constants (pre-registration) ──────────────────────────────────
HORIZON = 24          # forward-return bars (24h on 1h panel)
SPLIT_FRAC = 0.60     # in-sample fraction
EMBARGO = 24          # bars dropped at the IS/OOS boundary (= HORIZON)
MIN_WIDTH = 10        # min symbols per bar for IC / portfolio
QUANTILE = 0.20       # long-short top/bottom fraction
IR_MIN = 0.50         # Stage-1 survivor bar
DSR_MIN = 0.10        # Stage-2 deflated-Sharpe bar (Pr[SR>0])
PBO_MAX = 0.50        # Stage-2 overfit ceiling
FDR_Q = 0.05          # Benjamini-Hochberg level
PBO_PARTITIONS = 16

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "reports"


def run_search(panel: Panel, registry: list | None = None, *,
               report_dir: Path | None = None) -> dict:
    """Run the two-stage screen and return the result dict (and write a report
    when `report_dir` is given)."""
    registry = registry if registry is not None else alpha_mod.computable_alphas()
    n_computable = len(registry)
    n_eff = 2 * n_computable

    is_p, oos_p = split_panel(panel, frac=SPLIT_FRAC, embargo=EMBARGO)

    # ── Stage 1 (in-sample) ──────────────────────────────────────────────
    stage1: dict[str, dict] = {}
    for a in registry:
        sig = a.fn(is_p)
        ic = screen.cross_sectional_ic(sig, is_p.fwd_ret, min_width=MIN_WIDTH)
        ir_v = screen.ir(ic)
        stage1[a.id] = {
            "ir_is": ir_v,
            "sign": 1.0 if ir_v >= 0 else -1.0,
            "category": screen.categorize(ir_v, IR_MIN),
            "passed_s1": abs(ir_v) >= IR_MIN,
        }

    # ── Stage 2 (out-of-sample): portfolio returns for ALL alphas (PBO) ──
    returns_by_alpha: dict[str, object] = {}
    oos_stats: dict[str, dict] = {}
    for a in registry:
        sig = a.fn(oos_p)
        r = screen.long_short_returns(sig, oos_p.fwd_ret,
                                      sign=stage1[a.id]["sign"],
                                      q=QUANTILE, min_width=MIN_WIDTH)
        returns_by_alpha[a.id] = r
        r_clean = r.dropna().to_numpy()
        oos_stats[a.id] = {
            "oos_sharpe": screen.sharpe(r_clean) if r_clean.size else 0.0,
            "dsr": screen.dsr_for_returns(r, n_trials=n_eff),
            "p_raw": screen.sharpe_pvalue(r),
        }

    pbo_value = screen.pbo_over_alphas(returns_by_alpha, n_partitions=PBO_PARTITIONS)

    # ── BH-FDR across Stage-1 survivors only ─────────────────────────────
    s1_ids = [a.id for a in registry if stage1[a.id]["passed_s1"]]
    flags = screen.fdr_bh([oos_stats[i]["p_raw"] for i in s1_ids], q=FDR_Q)
    fdr_pass = dict(zip(s1_ids, flags))

    # ── Survivor decision (conjunction) ──────────────────────────────────
    table, survivors = [], []
    for a in registry:
        s1, s2 = stage1[a.id], oos_stats[a.id]
        is_survivor = (
            s1["passed_s1"]
            and s2["dsr"] >= DSR_MIN
            and pbo_value <= PBO_MAX
            and fdr_pass.get(a.id, False)
        )
        if is_survivor:
            survivors.append(a.id)
        table.append({
            "id": a.id, "source": a.source,
            "ir_is": round(s1["ir_is"], 4), "category": s1["category"],
            "oos_sharpe": round(s2["oos_sharpe"], 4), "dsr": round(s2["dsr"], 4),
            "fdr_p": round(s2["p_raw"], 6), "fdr_pass": fdr_pass.get(a.id, False),
            "survivor": is_survivor,
        })

    table.sort(key=lambda r: r["ir_is"], reverse=True)
    result = {
        "verdict": "EDGE_FOUND" if survivors else "NO_EDGE",
        "survivors": survivors,
        "n_computable": n_computable, "n_eff": n_eff,
        "pbo": round(pbo_value, 4),
        "pre_registration": {
            "horizon": HORIZON, "split_frac": SPLIT_FRAC, "embargo": EMBARGO,
            "min_width": MIN_WIDTH, "quantile": QUANTILE, "ir_min": IR_MIN,
            "dsr_min": DSR_MIN, "pbo_max": PBO_MAX, "fdr_q": FDR_Q,
        },
        "panel": {"bars": len(panel.ts), "symbols": len(panel.symbols)},
        "table": table,
    }
    if report_dir is not None:
        _write_report(result, Path(report_dir))
    return result


def _write_report(result: dict, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    (report_dir / f"alpha_search_{stamp}.json").write_text(
        json.dumps(result, indent=2, default=float))
    lines = [
        f"# Alpha Search — {stamp}", "",
        f"**Verdict:** {result['verdict']}",
        f"**Survivors:** {result['survivors'] or 'none'}",
        f"**N_computable / N_eff:** {result['n_computable']} / {result['n_eff']}",
        f"**PBO:** {result['pbo']}",
        f"**Panel:** {result['panel']['bars']} bars × {result['panel']['symbols']} symbols",
        "", "## Pre-registration (frozen)",
        "```json", json.dumps(result["pre_registration"], indent=2), "```",
        "", "## Full ranking", "",
        "| id | source | IR_is | category | OOS Sharpe | DSR | FDR p | survivor |",
        "|----|--------|-------|----------|-----------|-----|-------|----------|",
    ]
    for r in result["table"]:
        lines.append(
            f"| {r['id']} | {r['source']} | {r['ir_is']} | {r['category']} | "
            f"{r['oos_sharpe']} | {r['dsr']} | {r['fdr_p']} | {r['survivor']} |")
    (report_dir / f"alpha_search_{stamp}.md").write_text("\n".join(lines))


def _load_live_panel(timeframe: str = "1h") -> Panel:
    """Load every cached *_<tf>.parquet into a Panel."""
    import pandas as pd
    cache = ROOT / "data" / "ohlcv_cache"
    raw = {}
    for p in sorted(cache.glob(f"*_{timeframe}.parquet")):
        base = p.name[: -len(f"_{timeframe}.parquet")]
        sym = base.replace("-", "/", 1)
        raw[sym] = pd.read_parquet(p)
    return build_panel(raw, timeframe=timeframe, horizon=HORIZON)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1h")
    args = ap.parse_args()
    panel = _load_live_panel(args.timeframe)
    result = run_search(panel, report_dir=DEFAULT_REPORT_DIR)
    print(f"VERDICT: {result['verdict']}  survivors={result['survivors']}  "
          f"PBO={result['pbo']}  N_eff={result['n_eff']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
