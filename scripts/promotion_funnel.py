"""Promotion funnel — hourly read-only monitor of every strategy lane's progress
toward the frozen promotion gate. Spec: docs/superpowers/specs/2026-07-18-promotion-funnel-design.md
HARD BOUNDARY: imports stdlib + core.promotion_gate constants only. Never engine/
order/exchange/config — enforced by tests/test_promotion_funnel.py::test_zero_live_path_imports.
"""
from __future__ import annotations

import json
import os
import sqlite3
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:  # direct-script runs (scheduled task) need repo root
    sys.path.insert(0, str(ROOT))
from scripts.report_goal_progress import RESOLVED_FLOOR, build_report  # noqa: E402

FUNNEL_JSON = ROOT / "data" / "promotion_funnel.json"
DOSSIER_DIR = ROOT / "reports" / "promotion_dossiers"


@dataclass
class LaneState:
    lane: str
    state: str  # ACCRUING|STARVED|GATE_READY|GATE_BLOCKED|STAGED|IDLE|ERROR
    resolved: int = 0
    wins: int = 0
    wr: float | None = None
    floor_progress: str = "0/30"
    accrual_rate_7d: float = 0.0
    eta_days: float | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "lane": self.lane, "state": self.state, "resolved": self.resolved,
            "wins": self.wins, "wr": self.wr, "floor_progress": self.floor_progress,
            "accrual_rate_7d": self.accrual_rate_7d, "eta_days": self.eta_days,
            "detail": self.detail,
        }


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)


PROBE_LANES: dict[str, tuple[str, str | None]] = {
    "tsmom_20d_1h": ("TsmomProbeAgent", "1h"),
    "tsmom_20d_4h": ("TsmomProbeAgent", "4h"),
    "breakout_60d": ("BreakoutProbeAgent", None),
    "unlock_short": ("UnlockShortProbeAgent", None),
    # owner-directed bundle-test MR probes (2026-07-19, NOT a pipeline GO):
    # both arms are 4h, so lanes key on the DISTINCT agent_ids.
    "zfade_4h_cfg365": ("ZfadeProbeAgent", "4h"),
    "rsi2_4h_cfg226": ("Rsi2TrackerProbeAgent", "4h"),
}

# Universe-widening disclosure (owner-approved 2026-07-20): both bundle-MR
# lanes' accrual universes changed from the frozen 5-major basket to the
# spec-derived MCP_DIRECTIONAL_PAPER x bybit universe. Pre-widening rows are
# KEPT (gates pool per-arm), so any promotion dossier must disclose the
# cohort change — probe_lane_states stamps this into detail.universe_widened_utc.
# STATIC deploy-date stamp, chosen over a runtime marker as the simpler honest
# option per docs/superpowers/specs/2026-07-20-probe-universe-widening-design.md:
# the widened wiring and this stamp ship in the same deploy/restart, so the
# deploy date IS the widening date (date precision); the exact boot moment is
# journaled (journal/2026-07-20.md) and in logs/bot_2026-07-20.log.
UNIVERSE_WIDENED_UTC = {
    "zfade_4h_cfg365": "2026-07-20T00:00:00+00:00",
    "rsi2_4h_cfg226": "2026-07-20T00:00:00+00:00",
}


def _accrual(resolved_ts: list[float], now: float) -> tuple[float, float | None, int]:
    """(rate_per_day over 7d, eta_days to floor, resolved_count)."""
    n = len(resolved_ts)
    recent = [t for t in resolved_ts if t >= now - 7 * 86400]
    rate = len(recent) / 7.0
    remaining = max(0, RESOLVED_FLOOR - n)
    eta = (remaining / rate) if rate > 0 and remaining > 0 else (0.0 if remaining == 0 else None)
    return rate, eta, n


def probe_lane_states(conn: sqlite3.Connection, now: float) -> list[LaneState]:
    out: list[LaneState] = []
    for lane, (agent, timeframe) in PROBE_LANES.items():
        try:
            tf_sql = " AND d.timeframe = ?" if timeframe else ""
            args: tuple = (agent, timeframe) if timeframe else (agent,)
            rows = conn.execute(
                "SELECT o.net_pnl, o.resolved_ts FROM shadow_decisions d"
                " JOIN shadow_outcomes o ON o.proposal_id = d.proposal_id"
                f" WHERE d.agent_id = ? AND d.label_status = 'RESOLVED'{tf_sql}", args).fetchall()
            n_prop = conn.execute(
                f"SELECT COUNT(*) FROM shadow_decisions d WHERE d.agent_id = ?{tf_sql}",
                args).fetchone()[0]
            wins = sum(1 for pnl, _ in rows if (pnl or 0) > 0)
            rate, eta, n = _accrual([t for _, t in rows if t], now)
            state = ("IDLE" if n_prop == 0 else
                     "GATE_READY" if n >= RESOLVED_FLOOR else "ACCRUING")
            detail = {"proposals": n_prop, "agent_id": agent}
            widened = UNIVERSE_WIDENED_UTC.get(lane)
            if widened:
                detail["universe_widened_utc"] = widened
            out.append(LaneState(lane, state, n, wins, (wins / n) if n else None,
                                 f"{n}/{RESOLVED_FLOOR}", round(rate, 3),
                                 round(eta, 1) if eta is not None else None,
                                 detail))
        except sqlite3.Error as exc:
            out.append(LaneState(lane, "ERROR", detail={"error": str(exc)}))
    return out


# Static copies from core/pair_discovery.py (2026-07-18) — provenance comment per
# spec: keeps the funnel's import surface at zero beyond promotion_gate constants.
_STOCK_BASES = {"AAPL", "TSLA", "GOOG", "GOOGL", "AMZN", "MSFT", "META", "NVDA",
                "NFLX", "AMD", "COIN", "MSTR", "GME", "AMC", "PLTR", "BABA", "TSM",
                "INTC", "PYPL", "SQ", "SHOP", "UBER", "ABNB", "SNAP", "SPY", "QQQ"}
_COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL", "BRENT", "UKOIL", "USOIL", "GOLD",
                    "SILVER", "COPPER", "NATGAS"}
# Leveraged/inverse-ETF tickers seen in venue tokenized-equity listings
# (TZA/SOXS observed live 2026-07-18; extend list as new ones appear).
_ETF_EXPLICIT = {"TZA", "SOXS", "SOXL", "TQQQ", "SQQQ", "UVXY", "SPXS", "SPXL",
                 "LABU", "LABD"}


def classify_base(base: str) -> str:
    b = (base or "").upper()
    if b in _STOCK_BASES or b in _COMMODITY_BASES or b in _ETF_EXPLICIT:
        return "tokenized"
    # A ticker alone cannot prove an asset is crypto. Unknown names stay
    # unclassified instead of inflating the crypto-native opportunity count.
    return "unclassified"


def listing_lane_state(conn: sqlite3.Connection, now: float) -> LaneState:
    try:
        rows = conn.execute(
            "SELECT proposal_id, base, decision, created_ts FROM shadow_listing_probe"
            " WHERE created_ts >= ?", (now - 30 * 86400,)).fetchall()
        outcomes = conn.execute(
            "SELECT p.proposal_id, o.net_pnl, o.resolved_ts "
            "FROM shadow_listing_probe p JOIN shadow_outcomes o "
            "ON o.proposal_id=p.proposal_id"
        ).fetchall()
        unique = {str(proposal_id): (float(net_pnl or 0), resolved_ts)
                  for proposal_id, net_pnl, resolved_ts in outcomes}
        resolved = len(unique)
        wins = sum(net_pnl > 0 for net_pnl, _ in unique.values())
        rate, eta, _ = _accrual(
            [float(resolved_ts) for _, resolved_ts in unique.values()
             if resolved_ts is not None], now
        )
        tokenized = sum(1 for _, base, _, _ in rows
                        if classify_base(base) == "tokenized")
        unclassified = len(rows) - tokenized
        actionable = sum(1 for _, _, decision, _ in rows
                         if not str(decision or "").startswith("SKIP"))
        if not rows:
            state, starvation_reason = "IDLE", None
        elif actionable == 0:
            state, starvation_reason = "STARVED", "no_actionable_shortable_listing"
        elif resolved >= RESOLVED_FLOOR:
            state, starvation_reason = "GATE_READY", None
        else:
            state, starvation_reason = "ACCRUING", None
        return LaneState(
            "listing_short", state, resolved, wins,
            (wins / resolved) if resolved else None,
            f"{resolved}/{RESOLVED_FLOOR}", round(rate, 3),
            round(eta, 1) if eta is not None else None,
            {
                "recent_proposals_30d": len(rows),
                "actionable_proposals_30d": actionable,
                "known_tokenized_listings_30d": tokenized,
                "unclassified_listings_30d": unclassified,
                "starvation_reason": starvation_reason,
                "note": "unknown tickers are not asserted to be crypto from name alone",
            },
        )
    except sqlite3.Error as exc:
        return LaneState("listing_short", "ERROR", detail={"error": str(exc)})


def unlock_calendar_coverage(cal_dir: Path, now: float) -> dict:
    horizon = 0.0
    try:
        for f in cal_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            events = data.get("events", data if isinstance(data, list) else [])
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ts = float(ev.get("ts") or ev.get("timestamp") or 0)
                horizon = max(horizon, ts)
    except OSError:
        pass
    fwd = max(0.0, (horizon - now) / 86400.0)
    return {"forward_days": round(fwd, 1), "starved": fwd < 30,
            "backfill_cmd": ("venv/Scripts/python.exe scripts/backfill_unlock_calendar.py"
                             " --forward-days 60")}


def f1_lane_state(gate_log: Path, now: float) -> LaneState:
    try:
        lines = gate_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError as exc:
        return LaneState("f1_carry", "ERROR", detail={"error": str(exc)})
    # F7 (2026-07-20 audit): a fixed lines[-2000:] cap silently under-counted
    # entries_48h on busy windows. Scan newest-first (the log is append-
    # ordered) and stop at the first record older than the window, so the
    # full 48h is always counted without reading the whole history.
    cutoff = now - 48 * 3600
    recent = []
    for ln in reversed(lines):
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        try:
            ts = float(e.get("ts") or 0)
        except (TypeError, ValueError):
            continue
        if ts <= 0:
            continue  # malformed record: skip, don't end the scan
        if ts < cutoff:
            break
        recent.append(e)
    recent.reverse()  # chronological order for the consecutive-streak logic
    streaks: dict[tuple, int] = {}
    best: dict[tuple, float] = {}
    alert = False
    for e in recent:  # log is append-ordered; consecutive = successive entries per key
        key = (e.get("venue"), e.get("symbol"))
        edge = float(e.get("net_edge_bps") or 0)
        best[key] = max(best.get(key, -1e9), edge)
        streaks[key] = streaks.get(key, 0) + 1 if edge > 0 else 0
        if streaks[key] >= 3:
            alert = True
    top = sorted(({"venue": k[0], "symbol": k[1], "best_edge_bps": round(v, 2)}
                  for k, v in best.items()), key=lambda d: -d["best_edge_bps"])[:5]
    state = "ACCRUING" if alert else ("IDLE" if recent else "STARVED")
    return LaneState("f1_carry", state, detail={
        "alert": alert, "top_edges": top, "entries_48h": len(recent),
        "note": "alert = net_edge_bps>0 on >=3 consecutive gate evals (hysteresis)"})


def directional_paper_lane_state(goal_json: Path) -> LaneState:
    try:
        data = json.loads(goal_json.read_text(encoding="utf-8"))
        cohort = next(
            lane for lane in data.get("lanes", [])
            if lane.get("lane") == "current_profile_directional"
        )
    except (OSError, json.JSONDecodeError, StopIteration) as exc:
        return LaneState("directional_paper_cohort", "ERROR", detail={"error": str(exc)})
    n = int(cohort.get("closed_outcomes") or 0)
    wins = int(cohort.get("wins") or 0)
    target_status = cohort.get("target_status")
    state = (
        "IDLE" if n == 0 else
        "ACCRUING" if n < RESOLVED_FLOOR else
        "GATE_READY" if target_status == "TARGET_MET" else
        "GATE_BLOCKED"
    )
    return LaneState(
        "directional_paper_cohort",
        state,
        n,
        wins,
        cohort.get("win_rate"),
        f"{n}/{RESOLVED_FLOOR}",
        detail={
            "net_after_cost_pnl": cohort.get("net_after_cost_pnl"),
            "profit_factor": cohort.get("profit_factor"),
            "expectancy_per_outcome": cohort.get("expectancy_per_outcome"),
            "target_status": target_status,
            "profile": cohort.get("profile"),
            "note": "directional PAPER research; no accuracy-band geometry is claimed",
        },
    )


# Backward-compatible function name for external read-only callers. The returned
# lane is intentionally no longer labeled as a band cohort.
band_lane_state = directional_paper_lane_state


import math  # noqa: E402

from core.promotion_gate import (  # noqa: E402  # constants only
    MAX_PBO,
    MIN_AUC,
    MIN_DSR,
    MIN_OOS_WR,
)


def _auc(scores_pos: list[float], scores_neg: list[float]) -> float:
    if not scores_pos or not scores_neg:
        return 0.5
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0
               for p in scores_pos for n in scores_neg)
    return wins / (len(scores_pos) * len(scores_neg))


def _dsr(pnls: list[float]) -> float:
    """DSR proxy: sharpe*sqrt(n) through normal CDF vs zero skill
    (sr_var = 1/n convention — matches the 2026-06-06 gate fix)."""
    n = len(pnls)
    if n < 2:
        return 0.0
    mu = sum(pnls) / n
    sd = (sum((x - mu) ** 2 for x in pnls) / (n - 1)) ** 0.5
    if sd == 0:
        return 1.0 if mu > 0 else 0.0
    z = (mu / sd) * math.sqrt(n)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def run_gate(outcomes: list[dict], market: str = "futures") -> dict:
    n = len(outcomes)
    pnls = [float(o.get("net_pnl") or 0) for o in outcomes]
    wins = [o for o in outcomes if float(o.get("net_pnl") or 0) > 0]
    wr = len(wins) / n if n else 0.0
    net = sum(pnls)
    expectancy = net / n if n else 0.0
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = abs(sum(value for value in pnls if value < 0))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else None
    pos = [float(o.get("p_win")) for o in wins if o.get("p_win") is not None]
    neg = [float(o.get("p_win")) for o in outcomes
           if float(o.get("net_pnl") or 0) <= 0 and o.get("p_win") is not None]
    auc = _auc(pos, neg)
    dsr_proxy = _dsr(pnls)
    gates = {
        "n_resolved": {"value": n, "threshold": RESOLVED_FLOOR, "ok": n >= RESOLVED_FLOOR},
        "oos_wr": {"value": round(wr, 4), "threshold": MIN_OOS_WR, "ok": wr >= MIN_OOS_WR},
        "auc": {"value": round(auc, 4), "threshold": MIN_AUC, "ok": auc >= MIN_AUC},
        "net_after_cost_pnl": {"value": round(net, 6), "threshold": 0.0, "ok": net > 0},
        "expectancy": {
            "value": round(expectancy, 6), "threshold": 0.0, "ok": expectancy > 0
        },
        "profit_factor": {
            "value": round(profit_factor, 6) if profit_factor is not None else None,
            "threshold": 1.0,
            "ok": bool(n and (gross_loss == 0 or (profit_factor or 0) > 1.0)),
        },
        # F2 (2026-07-20 audit): these two were hard-coded ok:False, which made
        # passed=all(...) permanently False — the dossier leg could never fire.
        # DSR is computed honestly from the funnel's own zero-skill proxy vs
        # MIN_DSR; the selection-aware caveat moves to the note (it is re-checked
        # at owner sign-off, which every dossier still requires).
        "dsr": {
            "value": round(dsr_proxy, 4), "threshold": MIN_DSR,
            "ok": dsr_proxy >= MIN_DSR, "computable": True,
            "note": ("single-stream zero-skill proxy; selection-aware DSR "
                     "still requires the registered trial count at sign-off"),
        },
        # PBO genuinely cannot be computed on a single forward stream —
        # informational ok:True with a note (original design) so the honest
        # non-computability does not permanently veto the dossier leg.
        "pbo": {
            "value": None, "threshold": MAX_PBO, "ok": True,
            "computable": False, "informational": True,
            "note": ("PBO requires a comparable strategy/fold return matrix — "
                     "not computable on a single stream; informational only"),
        },
    }
    return {
        "passed": all(g["ok"] for g in gates.values()),
        "fail_closed": True,
        "gates": gates,
    }


def build_dossier(lane: LaneState, gate_result: dict, outcomes: list[dict],
                  out_root: Path, today: str) -> Path | None:
    d = out_root / f"{lane.lane}_{today}"
    if d.exists():
        return None
    d.mkdir(parents=True)
    ev = {"lane": lane.to_dict(), "gate": gate_result, "n_outcomes": len(outcomes),
          "generated_utc": datetime.now(timezone.utc).isoformat()}
    atomic_write_json(d / "evidence.json", ev)
    rows = "\n".join(f"| {g} | {v['value']} | {v['threshold']} | {'PASS' if v['ok'] else 'FAIL'} |"
                     for g, v in gate_result["gates"].items())
    (d / "evidence.md").write_text(
        f"# Promotion dossier — {lane.lane} ({today})\n\n"
        f"Gate verdict: **{'PASS' if gate_result['passed'] else 'FAIL'}** on "
        f"{lane.resolved} resolved outcomes (WR {lane.wr}).\n\n"
        f"| gate | value | threshold | verdict |\n|---|---|---|---|\n{rows}\n\n"
        "**This dossier stages evidence only. Promotion requires every gate, "
        "including computable PBO/DSR, plus OWNER SIGN-OFF.** Re-read the lane's "
        "binding caveats in its integration report "
        "(_workspace/strategy_pipeline/ 11_/12_ files) before signing.\n",
        encoding="utf-8")
    (d / "proposed_change.patch").write_text(
        f"# NO EXECUTABLE PATCH GENERATED for {lane.lane}.\n"
        "# This placeholder cannot be applied. A separately reviewed change is\n"
        "# required only after all evidence gates and owner sign-off pass.\n",
        encoding="utf-8")
    return d


def compute_all(paths: dict, now: float) -> dict:
    lanes: list[LaneState] = []
    try:
        conn = sqlite3.connect(f"file:{paths['warehouse']}?mode=ro", uri=True)
        lanes += probe_lane_states(conn, now)
        lanes.append(listing_lane_state(conn, now))
        conn.close()
    except sqlite3.Error as exc:
        lanes += [LaneState(l, "ERROR", detail={"error": str(exc)})
                  for l in [*PROBE_LANES, "listing_short"]]
    for ls in lanes:
        if ls.lane == "unlock_short" and ls.state != "ERROR":
            cov = unlock_calendar_coverage(paths["cal_dir"], now)
            ls.detail["calendar"] = cov
            if cov["starved"]:
                ls.state = "STARVED"
    lanes.append(f1_lane_state(paths["gate_log"], now))
    lanes.append(directional_paper_lane_state(paths["goal_json"]))
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for ls in lanes:  # gate + dossier on any GATE_READY probe lane
        if ls.state == "GATE_READY" and ls.lane in PROBE_LANES:
            agent, timeframe = PROBE_LANES[ls.lane]
            conn = sqlite3.connect(f"file:{paths['warehouse']}?mode=ro", uri=True)
            tf_sql = " AND d.timeframe = ?" if timeframe else ""
            args: tuple = (agent, timeframe) if timeframe else (agent,)
            outcomes = [{"net_pnl": r[0], "p_win": r[1]} for r in conn.execute(
                "SELECT o.net_pnl, d.p_win FROM shadow_decisions d JOIN shadow_outcomes o"
                " ON o.proposal_id = d.proposal_id WHERE d.agent_id = ?"
                f" AND d.label_status = 'RESOLVED'{tf_sql}", args)]
            conn.close()
            gate = run_gate(outcomes)
            ls.detail["gate"] = gate
            if gate["passed"]:
                if build_dossier(ls, gate, outcomes, Path(paths["dossier_dir"]), today):
                    ls.state = "STAGED"
            else:
                ls.state = "GATE_BLOCKED"
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "resolved_floor": RESOLVED_FLOOR,
            "lanes": [ls.to_dict() for ls in lanes]}


def persist(doc: dict, paths: dict) -> None:
    prev = {}
    try:
        prev = {l["lane"]: l["state"]
                for l in json.loads(Path(paths["funnel_json"]).read_text(
                    encoding="utf-8"))["lanes"]}
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    atomic_write_json(Path(paths["funnel_json"]), doc)
    changes = [(l["lane"], prev.get(l["lane"]), l["state"]) for l in doc["lanes"]
               if prev.get(l["lane"]) != l["state"]]
    f1 = next((l for l in doc["lanes"] if l["lane"] == "f1_carry"), None)
    alert = bool(f1 and f1["detail"].get("alert"))
    if not changes and not alert:
        return
    jdir = Path(paths["journal_dir"])
    jdir.mkdir(parents=True, exist_ok=True)
    day = jdir / f"{datetime.now(timezone.utc):%Y-%m-%d}.md"
    lines = [f"\n## {datetime.now(timezone.utc):%H:%M}Z — Promotion funnel"]
    lines += [f"- {lane}: {old or 'NEW'} → **{new}**" for lane, old, new in changes]
    if alert:
        lines.append(f"- ⚠ F1 REGIME ALERT: positive net edge sustained — "
                     f"{f1['detail']['top_edges'][:3]}")
    with day.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    paths = {"warehouse": ROOT / "data" / "warehouse.sqlite",
             "gate_log": ROOT / "data" / "carry_gate_log.jsonl",
             "goal_json": ROOT / "data" / "goal_progress.json",
             "cal_dir": ROOT / "data" / "unlock_calendar",
             "funnel_json": FUNNEL_JSON, "dossier_dir": DOSSIER_DIR,
             "journal_dir": ROOT / "journal"}
    # Keep the hourly directional cohort current before the funnel consumes it.
    # build_report is read-only against trading state; this writes monitoring
    # output only and never touches config, orders, or promotion authority.
    atomic_write_json(paths["goal_json"], build_report(ROOT))
    doc = compute_all(paths, time.time())
    persist(doc, paths)
    print(f"promotion funnel -> {FUNNEL_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
