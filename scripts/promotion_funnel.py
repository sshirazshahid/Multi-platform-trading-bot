"""Promotion funnel — hourly read-only monitor of every strategy lane's progress
toward the frozen promotion gate. Spec: docs/superpowers/specs/2026-07-18-promotion-funnel-design.md
HARD BOUNDARY: imports stdlib + core.promotion_gate constants only. Never engine/
order/exchange/config — enforced by tests/test_promotion_funnel.py::test_zero_live_path_imports.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVED_FLOOR = 30  # per-lane promotion floor (>=30 resolved, owner-signed)
FUNNEL_JSON = ROOT / "data" / "promotion_funnel.json"
DOSSIER_DIR = ROOT / "reports" / "promotion_dossiers"


@dataclass
class LaneState:
    lane: str
    state: str  # ACCRUING|STARVED|GATE_READY|STAGED|IDLE|ERROR
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
            out.append(LaneState(lane, state, n, wins, (wins / n) if n else None,
                                 f"{n}/{RESOLVED_FLOOR}", round(rate, 3),
                                 round(eta, 1) if eta is not None else None,
                                 {"proposals": n_prop, "agent_id": agent}))
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
    return "crypto"


def listing_lane_state(conn: sqlite3.Connection, now: float) -> LaneState:
    try:
        rows = conn.execute(
            "SELECT base, decision, created_ts FROM shadow_listing_probe"
            " WHERE created_ts >= ?", (now - 30 * 86400,)).fetchall()
        resolved = conn.execute(
            "SELECT COUNT(*) FROM shadow_listing_probe WHERE decision NOT LIKE 'SKIP%'"
        ).fetchone()[0]
        native = sum(1 for b, _, _ in rows if classify_base(b) == "crypto")
        tokenized = len(rows) - native
        state = ("STARVED" if rows and native == 0 else
                 "GATE_READY" if resolved >= RESOLVED_FLOOR else
                 "ACCRUING" if resolved else "STARVED" if rows else "IDLE")
        return LaneState("listing_short", state, resolved, 0, None,
                         f"{resolved}/{RESOLVED_FLOOR}", 0.0, None,
                         {"crypto_native_listings_30d": native,
                          "tokenized_listings_30d": tokenized,
                          "note": "starved while venue listing flow is tokenized-equity"})
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
