"""scripts/weekly_scorecard.py — weekly self-evaluation of live experiments.

2026-06-11 (owner: make the bot "technically/mathematically/financially
advanced"). For each experiment in scripts/experiments.json, compares
pre/post windows of whole-trade PnL from the warehouse with proper
inference (Wilson CI on WR, t + bootstrap CI on expectancy, Welch +
bootstrap-delta verdict from core/stats_inference), applies BH-FDR
across experiments, and writes reports/scorecard_<date>.{md,json}.

HONESTY RULES baked in:
- pre/post designs are regime-confounded; all current experiments share
  one activation restart, so attribution is BUNDLE-level. The report
  says so on every run.
- "IMPROVED" at 95% is screening evidence, not confirmation — weekly
  re-looks inflate false positives (footer states this).
- fee_share is labeled "modeled (warehouse fee col)" — the cost split
  is CONTESTED (2026-06-05 memory); never presented as exact.

Exit code 0 on NOT_STARTED / NO_DATA / INCONCLUSIVE (degraded data is a
report, not a failure); nonzero only on crash.

Usage:
    python scripts/weekly_scorecard.py [--db data/warehouse.sqlite]
        [--registry scripts/experiments.json] [--out-dir reports]
        [--as-of EPOCH] [--seed 42] [--default-start-ts EPOCH]
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from core.stats_inference import (  # noqa: E402
    mean_ci,
    mean_ci_bootstrap,
    two_sample_verdict,
    wilson_interval,
)

try:
    from core.alpha_zoo.screen import fdr_bh  # noqa: E402
except Exception:  # pragma: no cover - alpha_zoo optional
    fdr_bh = None


# ── registry ──────────────────────────────────────────────────────────


def load_registry(path: Path) -> list:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    out = []
    for e in data.get("experiments", []):
        if not e.get("id"):
            continue
        out.append(
            {
                "id": str(e["id"]),
                "label": str(e.get("label", e["id"])),
                "start_ts": float(e.get("start_ts", 0) or 0),
                "end_ts": e.get("end_ts"),
                "mode": str(e.get("mode", "PAPER")),
                "baseline_days": float(e.get("baseline_days", 14)),
                "min_n": int(e.get("min_n", 20)),
                "aux": str(e.get("aux", "")),
            }
        )
    return out


# ── warehouse access (read-only) ──────────────────────────────────────


def fetch_window(db: Path, mode: str, t0: float, t1: float) -> list:
    """Closed trades entered in [t0, t1). Read-only URI; one retry on a
    write-lock; [] on failure (NO_DATA, not a crash)."""
    sql = (
        "SELECT ts_entry, ts_exit, "
        "       realized_pnl + COALESCE(partial_realized_pnl, 0) AS pnl, "
        "       COALESCE(fee, 0) AS fee, exit_reason, symbol, side "
        "FROM trades "
        "WHERE status='CLOSED' AND mode=? AND ts_entry >= ? AND ts_entry < ? "
        "ORDER BY ts_entry"
    )
    uri = f"file:{Path(db).as_posix()}?mode=ro"
    for attempt in (0, 1):
        try:
            conn = sqlite3.connect(uri, uri=True, timeout=10)
            try:
                conn.row_factory = sqlite3.Row
                return [dict(r) for r in conn.execute(sql, (mode, t0, t1))]
            finally:
                conn.close()
        except sqlite3.OperationalError:
            if attempt == 0:
                time.sleep(2)
                continue
            return []
        except Exception:
            return []
    return []


# ── per-window statistics ─────────────────────────────────────────────


def window_stats(rows: list, window_days: float, seed: int = 42) -> dict:
    pnls = [float(r["pnl"] or 0.0) for r in rows]
    n = len(pnls)
    wins = sum(1 for p in pnls if p > 0)
    fees = sum(float(r["fee"] or 0.0) for r in rows)
    net = sum(pnls)
    wr_lo, wr_hi = wilson_interval(wins, n)
    m, t_lo, t_hi = mean_ci(pnls) if n else (float("nan"),) * 3
    _, b_lo, b_hi = mean_ci_bootstrap(pnls, seed=seed) if n >= 2 else (float("nan"),) * 3
    eps = 1e-9
    return {
        "n": n,
        "net": round(net, 4),
        "wins": wins,
        "wr_pct": round(100.0 * wins / n, 2) if n else None,
        "wr_ci95": [round(100 * wr_lo, 2), round(100 * wr_hi, 2)],
        "expectancy": None if n == 0 else round(m, 4),
        "exp_ci95_t": [None if v != v else round(v, 4) for v in (t_lo, t_hi)],
        "exp_ci95_boot": [None if v != v else round(v, 4) for v in (b_lo, b_hi)],
        # modeled (warehouse fee col) — the cost split is CONTESTED; never "exact"
        "fees_total_modeled": round(fees, 4),
        "fee_share_modeled": round(fees / max(eps, fees + abs(net)), 4),
        "trades_per_day": round(n / max(window_days, eps), 2),
    }


# ── experiment-specific auxiliary metrics (fail-soft) ─────────────────


def _calls_per_day(t0: float, t1: float, audit_dir: Path = Path("data/claude_audit")) -> float:
    """Audit-log calls/day in [t0, t1). Line-streamed (plan E-11): calls_*.jsonl
    rows may carry multi-KB prompt/raw_response fields and monthly files reach
    hundreds of MB — never load a whole file into memory."""
    total = 0
    for p in audit_dir.glob("calls_*.jsonl"):
        try:
            with p.open(encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    try:
                        ts = json.loads(line).get("ts") or 0
                        if isinstance(ts, str):
                            ts = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
                        if t0 <= float(ts) < t1:
                            total += 1
                    except Exception:
                        continue
        except OSError:
            continue
    days = max((t1 - t0) / 86400, 1e-9)
    return round(total / days, 1)


def aux_metrics(
    aux: str, pre_rows: list, post_rows: list, pre_days: float, post_days: float
) -> dict:
    try:
        if aux == "entry_invalidated":

            def _s(rows, days):
                ei = [r for r in rows if (r.get("exit_reason") or "") == "entry_invalidated"]
                pn = [float(r["pnl"] or 0) for r in ei]
                return {
                    "n": len(ei),
                    "mean_pnl": round(sum(pn) / len(pn), 4) if pn else None,
                    "per_day": round(len(ei) / max(days, 1e-9), 2),
                }

            return {"pre": _s(pre_rows, pre_days), "post": _s(post_rows, post_days)}
        if aux == "repeat_sl":

            def _frac(rows):
                last_sl = {}
                rep = tot = 0
                for r in rows:  # already ts_entry-ordered
                    key = (r.get("symbol"), r.get("side"))
                    if (r.get("exit_reason") or "") == "stop_loss":
                        tot += 1
                        prev = last_sl.get(key)
                        if prev is not None and float(r["ts_entry"]) - prev <= 180 * 60:
                            rep += 1
                        last_sl[key] = float(r.get("ts_exit") or r["ts_entry"])
                return round(rep / tot, 4) if tot else None

            return {"pre_repeat_sl_frac": _frac(pre_rows), "post_repeat_sl_frac": _frac(post_rows)}
        if aux == "profitable_hour_share":
            ev = json.loads(Path("data/hour_gate_evidence.json").read_text(encoding="utf-8"))
            prof = {int(h) for h in (ev.get("profitable") or [])}

            def _share(rows):
                if not rows or not prof:
                    return None
                hits = sum(
                    1
                    for r in rows
                    if datetime.fromtimestamp(float(r["ts_entry"]), tz=timezone.utc).hour in prof
                )
                return round(hits / len(rows), 4)

            return {
                "pre_share": _share(pre_rows),
                "post_share": _share(post_rows),
                "profitable_hours": sorted(prof),
            }
        if aux == "claude_calls":
            pre_t = [float(r["ts_entry"]) for r in pre_rows] or [0, 0]
            post_t = [float(r["ts_entry"]) for r in post_rows] or [0, 0]
            return {
                "pre_calls_per_day": _calls_per_day(min(pre_t), max(pre_t) + 1),
                "post_calls_per_day": _calls_per_day(min(post_t), max(post_t) + 1),
            }
    except Exception as e:
        return {"aux_error": str(e)[:120]}
    return {}


# ── provenance health (plan amendment 4/5: scorecard line) ───────────


def _import_recon():
    here = Path(__file__).resolve().parent
    if str(here) not in sys.path:
        sys.path.insert(0, str(here))
    import decision_reconciliation

    return decision_reconciliation


def provenance_health(
    decisions_glob="data/mcp_decisions*.jsonl",
    warehouse="data/warehouse.sqlite",
    audit_failures="data/claude_audit_failures.json",
) -> dict:
    """Counters for the provenance-health report line. Every input is optional:
    pre-restart data has none of the new fields/files, so each value degrades
    to "n/a" independently instead of failing the scorecard."""
    prov = {
        "audit_write_failures": "n/a",
        "repaired": "n/a",
        "truncated": "n/a",
        "rejections": "n/a",
        "true_orphans": "n/a",
        "per_source": None,
    }
    try:
        prov["audit_write_failures"] = int(
            json.loads(Path(audit_failures).read_text(encoding="utf-8")).get("count", 0)
        )
    except Exception:
        pass
    try:
        recon = _import_recon()
        dec = recon.load_decisions(str(decisions_glob))
    except Exception:
        return prov
    if not dec["instrumented"]:
        return prov  # old-format rows only: counts would be misleading zeros
    prov["repaired"] = dec["repaired_count"]
    prov["truncated"] = dec["truncated_count"]
    prov["rejections"] = dec["rejection_count"]
    try:
        res = recon.reconcile(dec, recon.load_trades(Path(warehouse)))
        prov["true_orphans"] = res["orphans"]["true_total"]
        prov["per_source"] = res["per_source"] or None
    except Exception:
        pass
    return prov


def _fmt_prov(v) -> str:
    return "n/a" if v in (None, "n/a") else str(v)


def _provenance_line(prov: dict) -> str:
    return (
        "Provenance health: audit-write failures: "
        f"{_fmt_prov(prov.get('audit_write_failures'))} | "
        f"repaired: {_fmt_prov(prov.get('repaired'))} | "
        f"truncated: {_fmt_prov(prov.get('truncated'))} | "
        f"rejections: {_fmt_prov(prov.get('rejections'))} | "
        f"true orphans: {_fmt_prov(prov.get('true_orphans'))}"
    )


def _per_source_line(per_source) -> str:
    if not per_source:
        return ""
    parts = []
    for src in sorted(per_source):
        s = per_source[src]
        wr = f"{s['wr_pct']}%" if s.get("wr_pct") is not None else "n/a"
        mean = s.get("mean_pnl")
        mean_s = f"{mean:+.4f}" if isinstance(mean, (int, float)) else "n/a"
        parts.append(f"{src} n={s['n']} WR={wr} mean_pnl={mean_s}")
    return "Per-source joined outcomes: " + " | ".join(parts)


# ── evaluation ────────────────────────────────────────────────────────


def evaluate(exp: dict, db: Path, as_of: float, seed: int = 42) -> dict:
    out = {"id": exp["id"], "label": exp["label"], "mode": exp["mode"]}
    start = float(exp.get("start_ts") or 0)
    if start <= 0:
        out["status"] = "NOT_STARTED"
        return out
    end = float(exp["end_ts"]) if exp.get("end_ts") else as_of
    end = min(end, as_of)
    pre_t0 = start - exp["baseline_days"] * 86400
    pre_rows = fetch_window(db, exp["mode"], pre_t0, start)
    post_rows = fetch_window(db, exp["mode"], start, end)
    pre_days = exp["baseline_days"]
    post_days = max((end - start) / 86400, 1e-9)
    if not pre_rows or not post_rows:
        out["status"] = "NO_DATA"
        out["n_pre"], out["n_post"] = len(pre_rows), len(post_rows)
        return out
    out["status"] = "EVALUATED"
    out["pre"] = window_stats(pre_rows, pre_days, seed)
    out["post"] = window_stats(post_rows, post_days, seed)
    out["verdict"] = two_sample_verdict(
        [float(r["pnl"] or 0) for r in pre_rows],
        [float(r["pnl"] or 0) for r in post_rows],
        min_n=exp["min_n"],
        seed=seed,
    )
    out["aux"] = aux_metrics(exp.get("aux", ""), pre_rows, post_rows, pre_days, post_days)
    return out


# ── rendering ─────────────────────────────────────────────────────────


def render_markdown(results: list, meta: dict) -> str:
    lines = [
        f"# Weekly Experiment Scorecard — {meta.get('date', '')}",
        "",
        "All four current experiments activated on the SAME bot restart —",
        "attribution is BUNDLE-level until they diverge. Pre/post designs",
        "are regime-confounded; treat IMPROVED as screening evidence, not",
        "confirmation (weekly re-looks inflate false positives).",
        "",
    ]
    prov = meta.get("provenance")
    if prov:
        lines.append(_provenance_line(prov))
        src_line = _per_source_line(prov.get("per_source"))
        if src_line:
            lines.append(src_line)
        lines.append("")
    for r in results:
        lines.append(f"## {r['label']}")
        if r.get("status") != "EVALUATED":
            lines.append(
                f"- status: **{r.get('status')}**"
                + (f" (n_pre={r.get('n_pre')}, n_post={r.get('n_post')})" if "n_pre" in r else "")
            )
            lines.append("")
            continue
        v = r["verdict"]
        lines += [
            "| window | n | net | WR (Wilson 95%) | expectancy (t 95%) | "
            "fee share (modeled) | trades/day |",
            "|---|---|---|---|---|---|---|",
        ]
        for tag, w in (("pre", r["pre"]), ("post", r["post"])):
            lines.append(
                f"| {tag} | {w['n']} | {w['net']:+.2f} | {w['wr_pct']}% "
                f"{w['wr_ci95']} | {w['expectancy']} {w['exp_ci95_t']} | "
                f"{w['fee_share_modeled']:.0%} | {w['trades_per_day']} |"
            )
        fdr = " fdr_reject" if r.get("fdr_reject") else ""
        lines.append(
            f"\n**VERDICT: {v['verdict']}** at 95% "
            f"(delta={v['delta']:+.3f}/trade, CI [{v['delta_ci'][0]:+.3f},"
            f"{v['delta_ci'][1]:+.3f}], welch_p={v['welch_p']:.3f}, "
            f"n={v['n_pre']}/{v['n_post']}){fdr} — {v['reason']}"
        )
        if r.get("aux"):
            lines.append(f"- aux: `{json.dumps(r['aux'])[:300]}`")
        lines.append("")
    lines.append("---")
    lines.append(
        "Footer: BH-FDR applied across experiment welch_p values; "
        "fee shares are MODELED (warehouse fee column — the cost "
        "split is contested, never exact)."
    )
    return "\n".join(lines)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--db", default="data/warehouse.sqlite", type=Path)
    ap.add_argument("--registry", default="scripts/experiments.json", type=Path)
    ap.add_argument("--out-dir", default="reports", type=Path)
    ap.add_argument("--as-of", default=None, type=float)
    ap.add_argument("--seed", default=42, type=int)
    ap.add_argument(
        "--default-start-ts",
        default=0.0,
        type=float,
        help="substitute for start_ts=0 entries (ad-hoc runs)",
    )
    ap.add_argument(
        "--decisions-glob",
        default="data/mcp_decisions*.jsonl",
        help="decision files for the provenance-health line",
    )
    ap.add_argument("--audit-failures", default="data/claude_audit_failures.json", type=Path)
    args = ap.parse_args(argv)

    as_of = args.as_of if args.as_of else time.time()
    exps = load_registry(args.registry)
    if args.default_start_ts > 0:
        for e in exps:
            if e["start_ts"] <= 0:
                e["start_ts"] = args.default_start_ts

    results = [evaluate(e, args.db, as_of, args.seed) for e in exps]

    # BH-FDR across the evaluated experiments' welch p-values
    evaluated = [r for r in results if r.get("status") == "EVALUATED"]
    if fdr_bh is not None and evaluated:
        try:
            pvals = [r["verdict"]["welch_p"] for r in evaluated]
            rejected = fdr_bh(pvals, q=0.05)
            for r, rej in zip(evaluated, rejected):
                r["fdr_reject"] = bool(rej)
        except Exception:
            pass

    date_s = datetime.fromtimestamp(as_of, tz=timezone.utc).strftime("%Y-%m-%d")
    meta = {
        "date": date_s,
        "as_of": as_of,
        "seed": args.seed,
        "provenance": provenance_health(args.decisions_glob, args.db, args.audit_failures),
    }
    args.out_dir.mkdir(parents=True, exist_ok=True)
    md = render_markdown(results, meta)
    (args.out_dir / f"scorecard_{date_s}.md").write_text(md, encoding="utf-8")
    (args.out_dir / f"scorecard_{date_s}.json").write_text(
        json.dumps({"meta": meta, "results": results}, indent=2, default=str), encoding="utf-8"
    )
    print(
        f"[scorecard] wrote {args.out_dir}/scorecard_{date_s}.md "
        f"({sum(1 for r in results if r.get('status') == 'EVALUATED')} evaluated, "
        f"{sum(1 for r in results if r.get('status') == 'NOT_STARTED')} not started)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
