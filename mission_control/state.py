"""Read-only Mission Control state loaders.

Pure file readers — no BotEngine, order_manager, risk_manager, live_gate, or
exchange clients. Nothing in this module writes to disk. The only database
access is ``sqlite3`` opened with ``mode=ro`` so a Mission Control page can
never take a write lock on the warehouse the live bot is appending to.
"""

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]

HEARTBEAT_PATH = Path("data/heartbeat.json")
RISK_STATE_PATH = Path("data/risk_state.json")
INCIDENT_LATCH_PATH = Path("data/risk_incident_latch.json")
POSITIONS_PATH = Path("data/positions.json")
WALLET_PATH = Path("data/virtual_wallet.json")
FUNNEL_PATH = Path("data/promotion_funnel.json")
GOAL_PATH = Path("data/goal_progress.json")
KILL_SWITCH_PATH = Path("data/KILL_SWITCH")
AUDIT_PATH = Path("data/mission_control_audit.jsonl")
LIVE_PID_PATH = Path("data/mission_control_live_pid.json")
CHECKLIST_PATH = Path("docs/CONTROLLED_LIVE_CHECKLIST.md")
WAREHOUSE_PATH = Path("data/warehouse.sqlite")

# position_tracker truncates data/positions.json 'closed' to the last 500 rows,
# so closed_count is a rolling ceiling and NOT a lifetime trade count.
CLOSED_RING_CAP = 500

# Mirrors core/live_gate._SIGN_RE. Duplicated deliberately: Mission Control is
# read-only and must not import any live rail module.
_SIGNATURE_RE = re.compile(
    r"^\s*(?:<!--\s*)?Signed-By:\s*(?P<name>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s*(?:-->)?\s*$"
)
_UNCHECKED_RE = re.compile(r"^\s*-\s*\[\s\]\s+", flags=re.IGNORECASE)
MAX_SIGNATURE_AGE_DAYS = 30

# Non-secret keys safe to surface from .env
_SAFE_ENV_KEYS = frozenset(
    {
        "OPERATING_MODE",
        "CONTROLLED_LIVE_ENABLED",
        "DRY_RUN",
        "PAPER_TRADING_PROFILE",
        "ENTRY_POLICY",
        "TRADING_MODE",
        "LOG_LEVEL",
    }
)


def _resolve(root: Path, rel: Path | str) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else root / p


def read_json(path: Path, default: Any = None) -> Any:
    """Load JSON from ``path``; return ``default`` on missing/invalid."""
    if default is None:
        default = {}
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError, ValueError):
        return default


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a dotenv file into a flat dict (no expansion, no secrets filter)."""
    values: dict[str, str] = {}
    if not path.exists():
        return values
    try:
        text = path.read_text(encoding="utf-8-sig")
    except OSError:
        return values
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        values[key.strip()] = val.strip().strip('"').strip("'")
    return values


def safe_env_flags(root: Path | None = None) -> dict[str, str]:
    """Return non-secret operating flags from ``.env``."""
    root = root or ROOT
    raw = parse_env_file(root / ".env")
    return {k: raw.get(k, "") for k in _SAFE_ENV_KEYS}


def _parse_iso(ts: str | None) -> datetime | None:
    if not ts:
        return None
    try:
        s = str(ts).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (TypeError, ValueError):
        return None


def file_meta(path: Path) -> dict[str, Any]:
    """Freshness metadata for a state file.

    ``exists=False`` is a first-class answer: the UI renders "no data (file
    absent)" rather than a silent zero.
    """
    try:
        st = path.stat()
    except OSError:
        return {"exists": False, "mtime_utc": None, "age_seconds": None, "size_bytes": 0}
    return {
        "exists": True,
        "mtime_utc": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        "age_seconds": max(0.0, time.time() - st.st_mtime),
        "size_bytes": st.st_size,
    }


def heartbeat_age_seconds(heartbeat: dict[str, Any]) -> float | None:
    """Seconds since heartbeat timestamp; None if unknown."""
    for key in ("ts", "timestamp", "updated_at", "written_at", "time"):
        dt = _parse_iso(heartbeat.get(key) if isinstance(heartbeat, dict) else None)
        if dt is not None:
            return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds())
    return None


def supervisor_liveness(
    root: Path | None = None, *, heartbeat_pid: int | None = None
) -> dict[str, Any]:
    """Enumerate supervisor / worker processes and reconcile with the heartbeat.

    Returns the full process list (not just a boolean) and classifies each as
    ``supervisor`` or ``worker``, because duplicate supervisor trees or a
    heartbeat pid that is not running are exactly the failure classes this
    console exists to catch. ``alive=None`` means UNKNOWN (psutil missing) and
    must never be rendered as "down".
    """
    root = root or ROOT
    found: list[dict[str, Any]] = []
    try:
        import psutil  # type: ignore
    except ImportError:
        return {
            "available": False,
            "processes": [],
            "alive": None,
            "supervisor_count": 0,
            "worker_count": 0,
            "heartbeat_pid": heartbeat_pid,
            "heartbeat_pid_running": None,
            "warnings": ["psutil is not installed — process liveness is UNKNOWN, not down."],
        }
    markers = {"launcher_supervisor.py": "supervisor", "main.py": "worker"}
    root_s = str(root).lower()
    matched: dict[int, dict[str, Any]] = {}
    for proc in psutil.process_iter(["pid", "ppid", "name", "cmdline", "create_time"]):
        try:
            cmd = [str(c) for c in (proc.info.get("cmdline") or [])]
            if len(cmd) < 2:
                continue
            # `python -c "<code>"` must not match just because the inline code
            # mentions main.py — only a real script ARGUMENT counts.
            if cmd[1] in {"-c", "-m"}:
                continue
            script_role = None
            for arg in cmd[1:]:
                base = arg.replace("\\", "/").rsplit("/", 1)[-1]
                if base in markers:
                    script_role = markers[base]
                    break
            if script_role is None:
                continue
            joined = " ".join(cmd)
            if root_s and root_s not in joined.lower() and "Trading_Bot" not in joined:
                try:
                    cwd = proc.cwd()
                except (psutil.Error, OSError):
                    cwd = ""
                if str(root) not in str(cwd):
                    continue
            started = proc.info.get("create_time")
            matched[proc.info["pid"]] = {
                "pid": proc.info["pid"],
                "ppid": proc.info.get("ppid"),
                "role": script_role,
                "interpreter": cmd[0],
                "cmdline": joined[:240],
                "started_utc": (
                    datetime.fromtimestamp(started, timezone.utc).isoformat() if started else None
                ),
            }
        except (psutil.Error, OSError):
            continue

    # The venv's python.exe is a shim that re-execs the base interpreter as a
    # CHILD running the same script. Counting both halves reports two
    # supervisors where there is one, so collapse each shim pair into the
    # top-most process and keep the child pid for reference.
    for info in matched.values():
        parent = matched.get(info.get("ppid"))
        info["is_shim_child"] = bool(parent and parent["role"] == info["role"])
    for info in matched.values():
        info["shim_pids"] = [
            c["pid"] for c in matched.values() if c["is_shim_child"] and c.get("ppid") == info["pid"]
        ]
    found = [i for i in matched.values() if not i["is_shim_child"]]

    supervisors = [p for p in found if p["role"] == "supervisor"]
    workers = [p for p in found if p["role"] == "worker"]
    all_pids = set(matched)
    hb_running = (heartbeat_pid in all_pids) if heartbeat_pid else None

    warnings: list[str] = []
    if len(supervisors) > 1:
        warnings.append(
            f"{len(supervisors)} independent launcher_supervisor trees are running "
            f"(pids {', '.join(str(p['pid']) for p in supervisors)}) — they fight over the same state files."
        )
    if len(workers) > 1:
        warnings.append(
            f"{len(workers)} independent main.py workers are running "
            f"(pids {', '.join(str(p['pid']) for p in workers)}); expected 1."
        )
    if heartbeat_pid and hb_running is False:
        warnings.append(
            f"heartbeat.json claims pid {heartbeat_pid}, but that process is not running — "
            "the heartbeat is from a dead process or another tree is writing it."
        )
    return {
        "available": True,
        "processes": found,
        "alive": bool(found),
        "supervisor_count": len(supervisors),
        "worker_count": len(workers),
        "heartbeat_pid": heartbeat_pid,
        "heartbeat_pid_running": hb_running,
        "warnings": warnings,
    }


def load_status(root: Path | None = None) -> dict[str, Any]:
    """Aggregate operational status for the Mission Control header."""
    root = root or ROOT
    hb = read_json(_resolve(root, HEARTBEAT_PATH), {})
    risk = read_json(_resolve(root, RISK_STATE_PATH), {})
    latch_path = _resolve(root, INCIDENT_LATCH_PATH)
    latch = read_json(latch_path, {}) if latch_path.exists() else None
    kill = _resolve(root, KILL_SWITCH_PATH).exists()
    env = safe_env_flags(root)
    live_pid = read_json(_resolve(root, LIVE_PID_PATH), {})
    hb = hb if isinstance(hb, dict) else {}
    risk = risk if isinstance(risk, dict) else {}

    # AUTHORITY: the heartbeat is what the RUNNING process reports. .env is only
    # intent — a long-running supervisor passes its inherited environ to children
    # and never re-reads .env, so an edited .env can be silently vetoed for days.
    # Showing .env as the mode is the failure class that cost this repo 22.4h of
    # zero-entry starvation on 2026-07-22.
    # Two independent sources claim "how many positions are open". If they
    # disagree, that disagreement is the most important fact on the page — this
    # repo has a documented ghost/phantom-position history.
    hb_open = hb.get("open_positions")
    positions_raw = read_json(_resolve(root, POSITIONS_PATH), {})
    file_open = None
    if isinstance(positions_raw, dict):
        file_open = len(_as_list(positions_raw.get("open") or positions_raw.get("open_positions") or []))
    open_divergent = (
        isinstance(hb_open, int) and file_open is not None and hb_open != file_open
    )

    hb_mode = str(hb.get("operating_mode") or "").upper()
    hb_profile = str(hb.get("paper_trading_profile") or "")
    env_mode = str(env.get("OPERATING_MODE") or "").upper()
    env_profile = str(env.get("PAPER_TRADING_PROFILE") or "")
    mode = hb_mode or env_mode
    profile = hb_profile or env_profile

    return {
        "heartbeat": hb,
        "heartbeat_age_seconds": heartbeat_age_seconds(hb),
        "heartbeat_file": file_meta(_resolve(root, HEARTBEAT_PATH)),
        "risk_state": risk,
        "incident_latch_present": latch_path.exists(),
        "incident_latch": latch if isinstance(latch, dict) else latch,
        "kill_switch": kill,
        "env": env,
        # mode/paper_profile are the RUNNING values (heartbeat-sourced).
        "mode": mode,
        "mode_source": "heartbeat" if hb_mode else ("env" if env_mode else "unknown"),
        "paper_profile": profile,
        "profile_source": "heartbeat" if hb_profile else ("env" if env_profile else "unknown"),
        # …and these are what .env would apply on the NEXT supervisor restart.
        "env_mode": env_mode,
        "env_paper_profile": env_profile,
        "mode_divergent": bool(hb_mode and env_mode and hb_mode != env_mode),
        "profile_divergent": bool(hb_profile and env_profile and hb_profile != env_profile),
        "open_positions_heartbeat": hb_open,
        "open_positions_file": file_open,
        "open_positions_divergent": open_divergent,
        "is_halted": bool(risk.get("is_halted") or latch_path.exists()),
        "supervisor": supervisor_liveness(root, heartbeat_pid=hb.get("pid")),
        "live_pid": live_pid if isinstance(live_pid, dict) else {},
        "now_utc": datetime.now(timezone.utc).isoformat(),
    }


_CLOSED_FIELDS = (
    "id",
    "symbol",
    "side",
    "exchange",
    "strategy",
    "market_type",
    "entry_price",
    "exit_price",
    "stop_loss",
    "take_profit",
    "size",
    "leverage",
    "pnl",
    "gross_pnl",
    "pnl_pct",
    "total_fees",
    "close_reason",
    "open_time",
    "close_time",
    "confidence",
    "paper_trade",
)


def _as_list(raw: Any) -> list[Any]:
    if isinstance(raw, dict):
        return list(raw.values())
    if isinstance(raw, list):
        return raw
    return []


def _num(value: Any) -> float | None:
    """Strict numeric coercion: '' / None / bools / junk -> None, never 0."""
    if value is None or isinstance(value, bool) or value == "":
        return None
    try:
        f = float(value)
    except (TypeError, ValueError):
        return None
    return f if f == f else None  # drop NaN


def load_positions(root: Path | None = None) -> dict[str, Any]:
    """Open book, closed-trade history, and wallet with an explicit basis."""
    root = root or ROOT
    pos_path = _resolve(root, POSITIONS_PATH)
    wallet_path = _resolve(root, WALLET_PATH)
    positions = read_json(pos_path, {})
    wallet = read_json(wallet_path, {})

    open_list: list[Any] = []
    closed_rows: list[dict[str, Any]] = []
    if isinstance(positions, dict):
        open_list = _as_list(positions.get("open") or positions.get("open_positions") or [])
        for row in _as_list(positions.get("closed") or positions.get("closed_positions") or []):
            if isinstance(row, dict):
                closed_rows.append({k: row.get(k) for k in _CLOSED_FIELDS})

    closed_count = len(closed_rows)
    realized = [_num(r.get("pnl")) for r in closed_rows]
    realized = [v for v in realized if v is not None]
    fees = [_num(r.get("total_fees")) for r in closed_rows]
    fees = [v for v in fees if v is not None]
    wins = sum(1 for v in realized if v > 0)
    losses = sum(1 for v in realized if v < 0)

    wallet = wallet if isinstance(wallet, dict) else {}
    balances = wallet.get("balances")
    balances = balances if isinstance(balances, dict) else {}
    numeric_balances = {k: _num(v) for k, v in balances.items()}
    known = [v for v in numeric_balances.values() if v is not None]
    total = sum(known) if known else None
    per_venue_start = _num(wallet.get("start"))
    seed_total = (
        per_venue_start * len(numeric_balances)
        if per_venue_start is not None and numeric_balances
        else None
    )

    return {
        "file": file_meta(pos_path),
        "open": open_list,
        "open_count": len(open_list),
        "closed": closed_rows,
        "closed_count": closed_count,
        # positions.json keeps only the last CLOSED_RING_CAP rows — the number
        # below is a rolling ceiling, NOT a lifetime trade count.
        "closed_is_capped": closed_count >= CLOSED_RING_CAP,
        "closed_ring_cap": CLOSED_RING_CAP,
        "closed_stats": {
            "with_pnl": len(realized),
            "wins": wins,
            "losses": losses,
            "net_pnl": round(sum(realized), 6) if realized else None,
            "total_fees": round(sum(fees), 6) if fees else None,
            "win_rate": round(wins / (wins + losses), 6) if (wins + losses) else None,
        },
        "wallet": wallet,
        "wallet_summary": {
            "file": file_meta(wallet_path),
            "balances": numeric_balances,
            "venues": len(numeric_balances),
            "total": round(total, 6) if total is not None else None,
            "start_per_venue": per_venue_start,
            "seed_total": round(seed_total, 6) if seed_total is not None else None,
            "delta_vs_seed": (
                round(total - seed_total, 6)
                if (total is not None and seed_total is not None)
                else None
            ),
            "basis": "virtual_wallet.json — paper wallet, 'start' is the PER-VENUE seed",
        },
    }


def load_risk(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    latch_path = _resolve(root, INCIDENT_LATCH_PATH)
    risk_path = _resolve(root, RISK_STATE_PATH)
    risk = read_json(risk_path, {})
    risk = risk if isinstance(risk, dict) else {}
    # risk_manager persists max_drawdown_pct as a FRACTION ((peak - equity)/peak,
    # 4dp). Publish an explicitly-named percent field so no renderer has to guess.
    dd_fraction = _num(risk.get("max_drawdown_pct"))
    history = risk.get("trade_history")
    curve: list[dict[str, Any]] = []
    if isinstance(history, list):
        cum = 0.0
        for i, item in enumerate(history):
            pnl = None
            won = None
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                won = bool(item[0])
                pnl = _num(item[1])
            elif isinstance(item, dict):
                won = bool(item.get("win"))
                pnl = _num(item.get("pnl"))
            if pnl is None:
                continue
            cum += pnl
            curve.append({"i": i, "pnl": round(pnl, 6), "cum": round(cum, 6), "win": won})
    return {
        "file": file_meta(risk_path),
        "risk_state": risk,
        "max_drawdown_fraction": dd_fraction,
        "max_drawdown_percent": round(dd_fraction * 100.0, 4) if dd_fraction is not None else None,
        "trade_history_curve": curve,
        "incident_latch_present": latch_path.exists(),
        "incident_latch": read_json(latch_path, {}) if latch_path.exists() else None,
        "kill_switch": _resolve(root, KILL_SWITCH_PATH).exists(),
    }


def _snapshot(path: Path, kind: str) -> dict[str, Any]:
    """Wrap a periodically-regenerated JSON snapshot with its own freshness.

    These files are written by hourly scheduled tasks, so "how old is the data"
    is a different question from "when did the browser last poll" and the UI
    must be able to answer both.
    """
    meta = file_meta(path)
    data = read_json(path, {})
    if not meta["exists"]:
        return {
            "available": False,
            "reason": f"{kind} snapshot has never been written ({path.name} is absent)",
            "file": meta,
        }
    if not isinstance(data, dict) or not data:
        return {
            "available": False,
            "reason": f"{kind} snapshot exists but is empty or unparsable JSON",
            "file": meta,
        }
    out = dict(data)
    out["available"] = True
    out["file"] = meta
    return out


# --- STARVED lane triage: BROKEN vs IDLE-BY-MARKET ------------------------
#
# A lane reading STARVED can mean two very different things, and the dashboard
# used to assert the alarming one for both:
#   (a) BROKEN        — the probe cannot emit entries at all.
#   (b) IDLE-BY-MARKET — the probe works; nothing in its frozen universe
#                        qualified.
# The promotion-funnel snapshot alone cannot separate them (its
# ``starvation_reason`` is a single string covering both), so the decision
# column on the probe tables is read here instead.
#
# ONLY these two shadow probe tables carry a ``decision`` column. Every other
# ``shadow_*_probe`` table stores accepted proposals only, so no other lane can
# be triaged this way and none is claimed to be.
_LANE_SKIP_TABLES = {
    "listing_short": "shadow_listing_probe",
    "unlock_short": "shadow_unlock_probe",
}

# Skip reasons that PROVE the event fell outside the lane's frozen universe:
# the probe ran, evaluated, and correctly declined. A lane whose proposals are
# unanimously these is idle by market, not broken.
#
# SKIP_UNSHORTABLE is deliberately ABSENT. On 2026-07-28 all 25 listing_short
# rows carried it because the shortability test was structurally incapable of
# returning True (bid/ask are None on every binance USD-M fetch_ticker), so a
# 100% SKIP_UNSHORTABLE rate is the signature of that REAL fault, not of an
# empty market. The agent fix exists in the working tree but is inactive until
# an owner restart; after it, out-of-universe listings emit SKIP_NOT_CRYPTO and
# land here honestly. Adding an ambiguous reason to this set would silence a
# genuine fault, which is the exact failure this triage exists to prevent.
_SCOPE_SKIP_REASONS = frozenset({"SKIP_NOT_CRYPTO"})

LANE_SKIP_WINDOW_DAYS = 30


def load_lane_skip_reasons(
    root: Path | None = None, *, days: int = LANE_SKIP_WINDOW_DAYS
) -> dict[str, Any]:
    """Per-lane probe decision distribution, read STRICTLY read-only.

    Cost note: this rides the 12s ``/api/funnel`` poll deliberately. The two
    tables are tiny (25 and 416 rows on 2026-07-28) and the whole call —
    connection open plus both GROUP BY scans — measured ~15 ms, which is
    negligible at that cadence. ``mode=ro`` + ``PRAGMA query_only=ON`` means it
    can never take a write lock on the database the live bot is appending to.

    Window note: the 30-day window matches the funnel snapshot's own
    ``recent_proposals_30d`` so the two are comparable. They can still differ
    slightly because the snapshot is written hourly by a scheduled task while
    this read is live — that lag is expected, not corruption.

    FAIL DIRECTION: every failure (absent database, missing table, sqlite
    error) returns ``available: False``. Callers must keep a STARVED lane in
    the ALARM tier when this is unavailable — an unreadable warehouse is not
    evidence that a lane is idle.
    """
    root = root or ROOT
    path = _resolve(root, WAREHOUSE_PATH)
    meta = file_meta(path)
    days = max(1, min(int(days or LANE_SKIP_WINDOW_DAYS), 365))
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "file": meta,
        "window_days": days,
        "lanes": {},
    }
    if not meta["exists"]:
        out["reason"] = "data/warehouse.sqlite is absent — probe decisions unknown"
        return out

    since = time.time() - days * 86400
    conn = None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 4000")
        present = {
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        lanes: dict[str, Any] = {}
        for lane, table in _LANE_SKIP_TABLES.items():
            if table not in present:
                lanes[lane] = {
                    "table": table,
                    "readable": False,
                    "reason": f"table {table} is not present in the warehouse",
                }
                continue
            rows = [
                (str(r["decision"] or "(none)"), int(r["c"]))
                for r in conn.execute(
                    f"SELECT decision, COUNT(*) AS c FROM {table} "  # noqa: S608 - table from a frozen literal map
                    "WHERE created_ts >= ? GROUP BY decision ORDER BY c DESC",
                    (since,),
                )
            ]
            total = sum(c for _, c in rows)
            skips = sum(c for d, c in rows if d.upper().startswith("SKIP"))
            scoped = sum(c for d, c in rows if d.upper() in _SCOPE_SKIP_REASONS)
            lanes[lane] = {
                "table": table,
                "readable": True,
                "total": total,
                "skips": skips,
                "scope_skips": scoped,
                # Unanimity is required: one proposal that is neither a skip nor
                # an out-of-scope skip means the lane is not idle-by-market.
                "scope_only": bool(total) and scoped == total,
                "by_decision": [{"decision": d, "count": c} for d, c in rows],
                "dominant": rows[0][0] if rows else None,
                "dominant_count": rows[0][1] if rows else 0,
            }
        out["lanes"] = lanes
        out["available"] = True
    except sqlite3.Error as exc:
        out["reason"] = f"warehouse read failed: {exc}"
        out["lanes"] = {}
    finally:
        if conn is not None:
            conn.close()
    return out


def load_funnel(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    snap = _snapshot(_resolve(root, FUNNEL_PATH), "promotion funnel")
    # Attached to the funnel payload rather than served separately: the lane
    # grid needs both in the same render pass to tell BROKEN from IDLE.
    snap["lane_skips"] = load_lane_skip_reasons(root)
    return snap


def load_goals(root: Path | None = None) -> dict[str, Any]:
    root = root or ROOT
    return _snapshot(_resolve(root, GOAL_PATH), "goal progress")


def inspect_checklist(path: Path) -> tuple[bool, str, int]:
    """Read-only mirror of the CONTROLLED_LIVE checklist gate.

    Returns ``(signed, message, unchecked_count)``. Deliberately reimplemented
    instead of importing ``core.live_gate``: Mission Control must not import a
    live rail. This function only ever reads.
    """
    if not path.exists():
        return False, f"checklist not found at {path.name}", 0
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        return False, f"cannot read checklist: {exc}", 0

    lines = text.splitlines()
    unchecked = [raw for raw in lines if _UNCHECKED_RE.match(raw)]
    if unchecked:
        return (
            False,
            f"{len(unchecked)} acceptance item(s) remain unchecked — CONTROLLED_LIVE refused",
            len(unchecked),
        )

    signatures: list[tuple[str, date]] = []
    for raw in lines:
        s = raw.strip()
        if not s or s.startswith("#"):
            continue
        if s.startswith("<!--") and s.endswith("-->"):
            continue
        m = _SIGNATURE_RE.match(s)
        if not m:
            continue
        try:
            d = date.fromisoformat(m.group("date"))
        except ValueError:
            continue
        if d > date.today():
            continue
        signatures.append((m.group("name").strip(), d))

    if not signatures:
        return False, "no valid 'Signed-By: <name> <YYYY-MM-DD>' line — CONTROLLED_LIVE refused", 0
    name, signed_on = signatures[-1]
    if signed_on < date.today() - timedelta(days=MAX_SIGNATURE_AGE_DAYS):
        return (
            False,
            f"signature by {name} on {signed_on.isoformat()} is older than "
            f"{MAX_SIGNATURE_AGE_DAYS} days — re-sign required",
            0,
        )
    return True, f"signed by {name} on {signed_on.isoformat()}", 0


def load_gates(root: Path | None = None) -> dict[str, Any]:
    """CONTROLLED_LIVE gate status — read-only; never mutates the checklist."""
    root = root or ROOT
    checklist = _resolve(root, CHECKLIST_PATH)
    env = safe_env_flags(root)
    signed, message, unchecked_count = inspect_checklist(checklist)
    return {
        "checklist_path": str(checklist.relative_to(root) if checklist.is_relative_to(root) else checklist),
        "checklist_exists": checklist.exists(),
        "checklist_signed": signed,
        "message": message,
        "unchecked_count": unchecked_count,
        "operating_mode": env.get("OPERATING_MODE", ""),
        "controlled_live_enabled": env.get("CONTROLLED_LIVE_ENABLED", "").lower()
        in {"1", "true", "yes"},
        # config.py derives DRY_RUN = (OPERATING_MODE != "CONTROLLED_LIVE"); the
        # .env DRY_RUN key is NOT consumed by the bot. Report the derived value
        # and keep the raw key visible but clearly labelled as ignored.
        "dry_run_derived": env.get("OPERATING_MODE", "").upper() != "CONTROLLED_LIVE",
        "dry_run_env_raw": env.get("DRY_RUN", ""),
        "dry_run_note": "derived from OPERATING_MODE by config.py — the .env DRY_RUN key is ignored by the bot",
        "read_only": True,
        "note": (
            "Mission Control is read-only: it never writes checklist boxes, "
            "Signed-By lines, .env, or any file under data/."
        ),
    }


def list_scheduled_tasks() -> dict[str, Any]:
    """List TradingBot* Windows scheduled tasks.

    Returns ``{"tasks": [...], "error": str|None, "supported": bool}`` so the UI
    can tell "the query failed" apart from "there are genuinely zero tasks".
    """
    if sys.platform != "win32":
        return {"tasks": [], "error": None, "supported": False}
    try:
        proc = subprocess.run(
            [
                "powershell",
                "-NoProfile",
                "-Command",
                (
                    "Get-ScheduledTask | Where-Object { $_.TaskName -like 'TradingBot*' } | "
                    "ForEach-Object { $i = Get-ScheduledTaskInfo -TaskName $_.TaskName "
                    "-TaskPath $_.TaskPath -ErrorAction SilentlyContinue; "
                    "[PSCustomObject]@{ Name=$_.TaskName; State=[string]$_.State; "
                    "LastResult=$i.LastTaskResult; LastRun=[string]$i.LastRunTime; "
                    "NextRun=[string]$i.NextRunTime } } | ConvertTo-Json -Compress"
                ),
            ],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"tasks": [], "error": f"task query failed: {exc}", "supported": True}
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "Get-ScheduledTask failed").strip()[:400]
        return {"tasks": [], "error": f"task query failed: {detail}", "supported": True}
    raw = (proc.stdout or "").strip()
    if not raw:
        return {"tasks": [], "error": None, "supported": True}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {"tasks": [], "error": "failed to parse task list JSON", "supported": True}
    if isinstance(data, dict):
        return {"tasks": [data], "error": None, "supported": True}
    if isinstance(data, list):
        return {"tasks": data, "error": None, "supported": True}
    return {"tasks": [], "error": "unexpected task list shape", "supported": True}


def load_audit(root: Path | None = None, *, limit: int = 100) -> list[dict[str, Any]]:
    root = root or ROOT
    path = _resolve(root, AUDIT_PATH)
    if not path.exists():
        return []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    out: list[dict[str, Any]] = []
    for line in lines[-max(1, limit) :]:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            row = {"raw": line}
        if isinstance(row, dict):
            out.append(row)
    out.reverse()
    return out


def load_candidates(
    root: Path | None = None,
    *,
    hours: int = 24,
    limit: int = 40,
    decision: str | None = None,
    family: str | None = None,
) -> dict[str, Any]:
    """Decision funnel from warehouse.sqlite, opened STRICTLY read-only.

    ``mode=ro`` + ``query_only`` means this can never take a write lock on the
    database the live bot is appending to. Called on demand only — never on the
    auto-refresh cycle.

    STRATEGY: on-demand. MEASURED 2026-07-28 on the live 777k-row table:
    un-hinted GROUP BY over a 1 h window = 1,133 ms warm / 4,680 ms cold;
    the same query with ``INDEXED BY idx_candidates_ts`` = 4.9 ms warm.
    ``EXPLAIN QUERY PLAN`` shows the planner otherwise picks
    ``idx_candidates_decision`` and full-scans. The hint is wrapped by
    ``_grouped()`` below, which falls back to the un-hinted form if the index
    is absent (``INDEXED BY`` is a hard constraint, not a hint — a missing
    index raises OperationalError rather than degrading).
    """
    root = root or ROOT
    path = _resolve(root, WAREHOUSE_PATH)
    meta = file_meta(path)
    hours = max(1, min(int(hours or 24), 720))
    limit = max(1, min(int(limit or 40), 200))
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "file": meta,
        "window_hours": hours,
        "since_utc": datetime.fromtimestamp(
            time.time() - hours * 3600, timezone.utc
        ).isoformat(),
        "by_decision": [],
        "skip_families": [],
        "top_skip_reasons": [],
        "distinct_skip_reasons": 0,
        "recent": [],
        "total_in_window": 0,
    }
    if not meta["exists"]:
        out["reason"] = "data/warehouse.sqlite is absent — no decision history on this machine"
        return out

    since = time.time() - hours * 3600
    conn = None
    try:
        conn = sqlite3.connect(
            f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0
        )
        conn.row_factory = sqlite3.Row
        # Belt and braces: refuse any statement that could write.
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 4000")

        def _grouped(select: str, params: tuple[Any, ...]) -> list[sqlite3.Row]:
            """Run ``select`` on the ts index, falling back to the un-hinted plan.

            ``{idx}`` is substituted with the INDEXED BY clause. The fallback is
            today's (slow) behaviour, which is acceptable HERE because this
            reader is on-demand — the poll-safe reader in ``load_brain`` falls
            back to the count-only path instead and never to this plan.
            """
            try:
                return conn.execute(
                    select.format(idx=" INDEXED BY idx_candidates_ts"), params
                ).fetchall()
            except sqlite3.OperationalError:
                return conn.execute(select.format(idx=""), params).fetchall()

        out["by_decision"] = [
            {"decision": r["decision"] or "(none)", "count": r["c"]}
            for r in _grouped(
                "SELECT decision, COUNT(*) AS c FROM candidates{idx} "
                "WHERE ts > ? GROUP BY decision ORDER BY c DESC",
                (since,),
            )
        ]
        out["total_in_window"] = sum(r["count"] for r in out["by_decision"])
        # Skip reasons embed live measurements ("scalp_veto:quiet(atr=0.57%)"),
        # which fragments one real cause into hundreds of near-identical rows.
        # Group by the reason FAMILY so the dominant cause is visible, and keep
        # the verbatim reasons alongside it.
        raw_reasons = [
            (r["skip_reason"] or "(none)", r["c"])
            for r in _grouped(
                "SELECT skip_reason, COUNT(*) AS c FROM candidates{idx} "
                "WHERE ts > ? AND decision = 'SKIP' "
                "GROUP BY skip_reason ORDER BY c DESC LIMIT 5000",
                (since,),
            )
        ]
        families: dict[str, dict[str, Any]] = {}
        for reason, count in raw_reasons:
            fam = reason_family(reason) or (reason or "(none)")
            slot = families.setdefault(fam, {"family": fam, "count": 0, "variants": 0, "example": reason})
            slot["count"] += count
            slot["variants"] += 1
        out["skip_families"] = sorted(families.values(), key=lambda d: -d["count"])[:20]
        out["top_skip_reasons"] = [{"reason": r, "count": c} for r, c in raw_reasons[:limit]]
        out["distinct_skip_reasons"] = len(raw_reasons)
        # Drill-down: the rows behind a summary figure are fetched from the
        # database, not filtered out of an unrelated 40-row sample.
        where = ["ts > ?"]
        params: list[Any] = [since]
        if decision:
            where.append("decision = ?")
            params.append(decision)
        if family:
            if family == "scalp_score_below_floor":
                # Prefixed form + legacy bare indicator strings.
                where.append(
                    "(skip_reason LIKE 'scalp_score_below_floor%' "
                    "OR skip_reason LIKE 'vwap_near=%' "
                    "OR skip_reason LIKE 'vwap_prime=%')"
                )
            else:
                where.append("(skip_reason = ? OR skip_reason LIKE ?)")
                params.extend([family, f"{family}(%"])
        params.append(limit)
        out["recent"] = [
            {
                "ts_utc": datetime.fromtimestamp(r["ts"], timezone.utc).isoformat(),
                "exchange": r["exchange"],
                "symbol": r["symbol"],
                "side": r["side"],
                "strategy_family": r["strategy_family"],
                "decision": r["decision"],
                "skip_reason": r["skip_reason"],
                "confidence": r["confidence"],
            }
            for r in conn.execute(
                "SELECT ts, exchange, symbol, side, strategy_family, decision, "
                "skip_reason, confidence FROM candidates "
                f"WHERE {' AND '.join(where)} ORDER BY ts DESC LIMIT ?",
                params,
            )
        ]
        out["filter"] = {"decision": decision or None, "family": family or None}
        out["available"] = True
    except sqlite3.Error as exc:
        out["reason"] = f"warehouse query failed: {exc}"
    finally:
        if conn is not None:
            conn.close()
    return out


# ==========================================================================
# BRAIN — live reasoning + research pipeline
# ==========================================================================
#
# Every reader below states its polling strategy and the cost MEASURED on this
# machine on 2026-07-28 against the live 777k-row warehouse (full per-request
# cost: fresh connection + PRAGMAs + query + fetch + close, warm median of 4).
#
# The reasoning the bot records is a small grammar embedded in strings:
#
#   scalp_req_fail(2/4:adx=42,atr=0.83%)   -> 2 of 4 required conditions met
#   scalp_veto:quiet(atr=0.63%)            -> vetoed, volatility too low
#   scalp_veto:ranging(4h_adx=14)          -> vetoed, rangebound
#   meta_advisory:loss_streak=20>=3 + ...  -> ADVISORY on an ALLOW row
#
# Decoding is FAIL-OPEN: an unrecognised string yields plain=None and the
# verbatim string is still published, so a grammar change degrades to "raw
# only" and never to a fabricated translation.

# Keys can start with a digit ("4h_adx=14"), so the class allows digits at the
# head; a bare number can never be a key because the character preceding the
# match must not itself be part of a key.
_MEASURE_RE = re.compile(r"([A-Za-z0-9_]*[A-Za-z_][A-Za-z0-9_]*)\s*=\s*(-?\d+(?:\.\d+)?)(%?)")
_REQUIRED_RE = re.compile(r"\((\d+)\s*/\s*(\d+)\s*[:)]")

# Metric key -> human label. Keys absent here render with their raw key, which
# is honest (it is what the bot wrote) rather than guessed.
_METRIC_LABELS = {
    "adx": "ADX",
    "adx_1h": "1h ADX",
    "adx_4h": "4h ADX",
    "4h_adx": "4h ADX",
    "atr": "ATR",
    "rsi": "RSI",
    "vol": "volume ratio",
    "vol_pctl": "volatility percentile",
    "vwap_near": "price vs VWAP distance",
    "conf": "confidence",
    "loss_streak": "consecutive losses",
    "score": "MCP score",
}

# Reason FAMILY -> plain-language sentence. Families are the part before "(".
_REASON_PLAIN = {
    # scorer stage
    "scalp_req_fail": "Not enough of the required entry conditions were met",
    "scalp_veto:quiet": "Vetoed — volatility too low for the move to pay for the trade",
    "scalp_veto:ranging": "Vetoed — the market is rangebound, not trending",
    "scalp_score_below_floor": (
        "Required scalp conditions passed, but the score stayed under the entry floor"
    ),
    # Reason CODE keeps its original spelling (the warehouse and the classic
    # console index on it), but the LABEL no longer mentions AccBand: the guard
    # was decoupled from ACCURACY_TARGET_MODE on 2026-08-01 and now fires
    # whether that mode is on or off. Naming a mode that may not be running as
    # the reason for the skip was actively misleading.
    "analysis_only_accband_scope": (
        "Skipped — non-crypto base (tokenized equity / commodity perp). No "
        "screened edge: every strategy verdict was measured on crypto data"
    ),
    "score_floor": "Scored below the entry score floor",
    # downstream terminal blocks
    "economic_gate_stressed_breakeven": (
        "Blocked — the take-profit cannot clear stressed costs, so the trade is "
        "arithmetically unprofitable before it is placed"
    ),
    "economic_gate_model_missing": (
        "Blocked — no promoted model is available and the fallback could not clear costs"
    ),
    "universe_filter_blocked": (
        "Blocked — live universe filter failed (often chop/trend-efficiency, "
        "or spread / depth)"
    ),
    "strategy_spec_route_not_approved": (
        "Blocked — no active paper StrategySpec authorises this venue/symbol route"
    ),
    "strategy_not_approved_for_paper": (
        "Blocked — this strategy id is not on the approved paper-trading allowlist"
    ),
    "kill_switch_active": "Blocked — the kill-switch file is present",
    "order_manager:maker_first_pending": "Maker-first order is resting and not yet resolved",
    "maker_first_maker_fill": "Filled as maker",
    "maker_first_taker_fallback_fill": "Filled as taker after the maker attempt did not fill",
}

_REASON_PREFIX_PLAIN = (
    ("band_regime_filter:", "Blocked — the band regime filter vetoed this entry"),
    ("universe_filter_blocked:", "Blocked — live universe filter failed (chop / spread / depth)"),
    ("meta_advisory:", "Advisory annotation from the meta-filter — NOT a rejection"),
)


def reason_family(reason: str | None) -> str:
    """The stable part of a reason string, with its measurements stripped.

    Mirrors the grouping ``load_candidates`` already uses so the Brain tab and
    the "Why no trades" tab speak the same vocabulary.

    Legacy scalp score-floor SKIPs were stored as bare ``vwap_near=…|rsi=…``
    (no family prefix) — map those to ``scalp_score_below_floor`` so the funnel
    aggregates them with the post-2026-07-30 prefixed form.
    """
    raw = str(reason or "").strip()
    if not raw:
        return ""
    if raw.startswith("scalp_score_below_floor"):
        return "scalp_score_below_floor"
    if raw.startswith("vwap_near=") or raw.startswith("vwap_prime="):
        return "scalp_score_below_floor"
    if raw.startswith("analysis_only_accband_scope"):
        return "analysis_only_accband_scope"
    if raw.startswith("universe_filter_blocked"):
        return "universe_filter_blocked"
    if raw.startswith("band_regime_filter"):
        return "band_regime_filter"
    return raw.split("(")[0].strip() or ""


def _tradfi_allow_base(symbol: str | None) -> bool:
    """True when the candidate symbol's base is in ANALYSIS_ONLY_BASES."""
    try:
        from config import ANALYSIS_ONLY_BASES
    except ImportError:
        return False
    base = str(symbol or "").upper().split("/")[0]
    return bool(base) and base in ANALYSIS_ONLY_BASES


def _brain_scorer_context(scorer: dict[str, Any], *, allow_crypto: int, allow_tradfi: int) -> dict[str, Any]:
    """Read-only interpretation so a low allow-rate is not misread as a crash."""
    try:
        from config import SCALP_MODE
        scorer_mode = "scalp" if (SCALP_MODE or {}).get("enabled") else "standard"
    except ImportError:
        scorer_mode = "unknown"
    scored = int(scorer.get("scored") or 0)
    allowed = int(scorer.get("allowed") or 0)
    allow_rate = (allowed / scored) if scored else 0.0
    causes = scorer.get("skip_causes") or []
    top_fam = (causes[0].get("family") if causes else None) or ""
    quiet_dominant = top_fam.startswith("scalp_veto:quiet") or top_fam == "scalp_req_fail"
    if scored == 0:
        interpretation = "No candidates were scored in this window."
    elif allow_tradfi > 0 and allow_tradfi >= allow_crypto:
        interpretation = (
            f"Most ALLOWs are TradFi/analysis-only bases ({allow_tradfi} of {allowed}) — "
            "not crypto AccBand edge. AccBand scope skip should clear this."
        )
    elif scorer_mode == "scalp" and allow_rate < 0.02 and quiet_dominant:
        interpretation = (
            "Low allow-rate under SCALP_MODE is expected in a quiet/ranging regime "
            "(ATR / required-condition filters). This is protective idle, not a "
            "broken counter — do not loosen scalp ATR without a new hashed prereg."
        )
    elif allow_rate < 0.02:
        interpretation = (
            f"Allow-rate {allow_rate:.1%} — scorer is selective; check binding "
            "constraint below before changing gates."
        )
    else:
        interpretation = (
            f"Scorer mode={scorer_mode}; allow-rate {allow_rate:.1%} "
            f"({allowed} of {scored})."
        )
    return {
        "scorer_mode": scorer_mode,
        "allow_rate": round(allow_rate, 6),
        "allow_crypto": allow_crypto,
        "allow_tradfi": allow_tradfi,
        "interpretation": interpretation,
    }

def decode_reason(reason: str | None) -> dict[str, Any]:
    """Translate one verbatim reason string into plain language + measurements.

    Returns ``{raw, family, plain, required: {met, of} | None, measurements: []}``.
    ``plain`` is None when the family is unrecognised — the caller renders the
    raw string alone rather than inventing a translation.
    """
    raw = "" if reason is None else str(reason)
    family = reason_family(raw)
    plain = _REASON_PLAIN.get(family)
    if plain is None:
        for prefix, text in _REASON_PREFIX_PLAIN:
            if family.startswith(prefix):
                plain = text
                break
    req = _REQUIRED_RE.search(raw)
    required = None
    if req:
        try:
            required = {"met": int(req.group(1)), "of": int(req.group(2))}
        except ValueError:
            required = None
    measurements = []
    for key, value, pct in _MEASURE_RE.findall(raw):
        val = _num(value)
        if val is None:
            continue
        measurements.append(
            {
                "key": key,
                "label": _METRIC_LABELS.get(key, key),
                "value": val,
                "unit": "%" if pct else "",
            }
        )
    return {
        "raw": raw,
        "family": family,
        "plain": plain,
        "required": required,
        "measurements": measurements,
    }


def _weighted_stats(pairs: list[tuple[float, int]]) -> dict[str, Any] | None:
    """min / weighted-median / max over (value, count) pairs."""
    if not pairs:
        return None
    ordered = sorted(pairs)
    total = sum(c for _, c in ordered)
    if total <= 0:
        return None
    seen = 0
    median = ordered[-1][0]
    for value, count in ordered:
        seen += count
        if seen * 2 >= total:
            median = value
            break
    return {
        "min": round(ordered[0][0], 6),
        "median": round(median, 6),
        "max": round(ordered[-1][0], 6),
        "n": total,
    }


def _required_split(
    total: int,
    required_of: Any,
    required_rows: int,
    required_met_sum: float,
    passes_by_key: dict[str, int],
    labels: dict[str, tuple[str, str]],
) -> dict[str, Any]:
    """Which REQUIRED conditions failed, for families that only log the ones that passed.

    ``scalp_req_fail(3/4:rsi=51,adx=33,atr=0.84%)`` names the three conditions
    that PASSED (``core/mcp_brain.py`` appends to ``req_reasons`` only inside
    each passing branch); the condition that actually rejected the candidate is
    the one MISSING from the list, and its measured value is never written. So
    the count of rows a key appears in IS its pass count, and
    ``total - passes`` is its failure count.

    This is derived, not assumed, so it is guarded rather than trusted. The
    discriminating check is ``sum(passes) == sum(met x count)``: if a family
    ever logged the FAILING conditions instead, the observed appearances would
    equal ``of*total - sum(met)`` and this identity would break (they coincide
    only when exactly half of all checks pass). No condition roster is
    hardcoded here — the checksum is what earns that.

    FAIL DIRECTION: any guard that does not hold returns ``derivable: False``
    with the reason, and the caller shows pass counts only. Naming a failing
    condition from arithmetic that did not close would be the inversion this
    function exists to remove.
    """
    out: dict[str, Any] = {
        "derivable": False,
        "note": "",
        "of": None,
        "candidates": total,
        "checks": None,
        "passes": None,
        "failures": None,
        "conditions": [],
        "unnamed": None,
    }
    try:
        of = int(required_of)
    except (TypeError, ValueError):
        return out
    out["of"] = of
    observed = {k: int(v) for k, v in passes_by_key.items()}
    passes = sum(observed.values())
    out["checks"] = of * total
    out["passes"] = passes
    out["conditions"] = sorted(
        (
            {
                "key": key,
                "label": labels.get(key, (key, ""))[0],
                "passes": n,
                "failures": total - n,
                "fail_share": round((total - n) / total, 4) if total else None,
            }
            for key, n in observed.items()
        ),
        key=lambda c: (-c["failures"], c["key"]),
    )
    if of <= 0 or total <= 0:
        out["note"] = "no required-condition count was recorded on these rows"
        return out
    if required_rows != total:
        out["note"] = (
            f"only {required_rows} of {total} rows in this cause carry an "
            "'n/of' required count, so the pass/fail split would not cover them all"
        )
        return out
    if len(observed) > of:
        out["note"] = (
            f"{len(observed)} distinct measurements appear but only {of} required "
            "conditions are declared — the extra keys are not required checks, so "
            "a per-condition failure count cannot be attributed"
        )
        return out
    if any(n > total for n in observed.values()):
        out["note"] = "a measurement appears on more rows than the cause has — not a per-row condition"
        return out
    if abs(required_met_sum - passes) > 0.5:
        out["note"] = (
            f"checksum failed: the rows declare {required_met_sum:,.0f} passing "
            f"conditions but {passes:,} measurements are recorded, so it cannot be "
            "established that the logged values are the conditions that PASSED"
        )
        return out
    out["failures"] = of * total - passes
    unnamed = of - len(observed)
    if unnamed > 0:
        out["unnamed"] = {
            "conditions": unnamed,
            "failures": unnamed * total,
            "note": (
                f"{unnamed} required condition(s) failed on all {total:,} of these "
                "candidates. The bot writes a condition's value only when it passes, "
                "so a condition that never passed leaves no trace and its identity is "
                "not recoverable from this log."
            ),
        }
    out["derivable"] = True
    return out


def _top_required_failure(split: dict[str, Any] | None) -> dict[str, Any] | None:
    """The named condition failing most often, or None when it cannot be named."""
    if not split or not split.get("derivable"):
        return None
    unnamed = split.get("unnamed")
    conds = split.get("conditions") or []
    top = conds[0] if conds else None
    # An unnamed condition fails on EVERY row, so it always outranks any named
    # one. Returning the named runner-up here would name the wrong constraint.
    if unnamed and (top is None or unnamed["failures"] >= top["failures"]):
        return None
    if top is None or not top["failures"]:
        return None
    return top


def _finite(value: Any) -> Any:
    """Drop non-finite floats.

    ``json.loads`` happily produces ``float('nan')`` and ``json.dumps`` re-emits
    a bare ``NaN``, which makes the WHOLE response unparseable in the browser —
    not just one field. Every number lifted out of a parsed payload goes through
    here.
    """
    if isinstance(value, float) and value != value:
        return None
    if isinstance(value, float) and value in (float("inf"), float("-inf")):
        return None
    return value


MCP_DECISIONS_PATH = Path("data/mcp_decisions.jsonl")
PIPELINE_DIR = Path("_workspace/strategy_pipeline")
LEDGER_PATH = Path(".claude/skills/refuted-families-ledger/SKILL.md")
DOSSIER_DIR = Path("reports/promotion_dossiers")

BRAIN_WINDOW_MINUTES = 60
# 163 decision_events/hour measured on 2026-07-28, so 600 rows ≈ 3.7 h of
# cover for a 60-minute window. ``tail_complete`` below reports whether the
# tail actually reached the window start rather than assuming it did.
_EVENT_TAIL_ROWS = 600
_JSONL_TAIL_BYTES = 65_536
_OUTCOME_RANK = {"filled": 3, "rejected": 2, "deferred": 1}


def _brain_scorer_stage(conn: sqlite3.Connection, since: float) -> dict[str, Any]:
    """Stage 1 of the cascade: what the scorer decided.

    STRATEGY: poll-safe. MEASURED 4.8–5.3 ms warm for the 60-minute window
    (60 groups) with ``INDEXED BY idx_candidates_ts``; the same query un-hinted
    measured 1,133 ms warm / 4,680 ms cold and is FORBIDDEN on the 12 s poll.

    FAIL DIRECTION: if the ts index is absent, ``INDEXED BY`` raises
    OperationalError. This falls back to the count-only form (un-hinted;
    MEASURED 953–957 ms warm / 4.5 s cold on 2026-07-29 against the live
    warehouse — far over the indexed path, acceptable ONLY because this runs
    in an already-degraded broken-index state) and sets
    ``aggregation_available: False`` — never to the un-hinted reason-level
    GROUP BY.

    HONESTY: ALLOW and SKIP are split BEFORE anything is ranked. ``skip_reason``
    is populated on ALLOW rows too, as a meta-filter ADVISORY (564 of 1,200
    ALLOW rows in 24 h carry ``meta_advisory:loss_streak=...``). Ranking those
    as causes of death would name the wrong constraint.
    """
    out: dict[str, Any] = {
        "aggregation_available": True,
        "aggregation_note": "",
        "scored": 0,
        "skipped": 0,
        "allowed": 0,
        "by_decision": [],
        "skip_causes": [],
        "allow_advisories": [],
        "distinct_skip_reasons": 0,
    }
    rows: list[tuple[str, str, int]]
    try:
        rows = [
            (str(r["decision"] or "(none)"), str(r["skip_reason"] or ""), int(r["c"]))
            for r in conn.execute(
                "SELECT decision, skip_reason, COUNT(*) AS c FROM candidates "
                "INDEXED BY idx_candidates_ts WHERE ts > ? GROUP BY 1, 2",
                (since,),
            )
        ]
    except sqlite3.OperationalError as exc:
        out["aggregation_available"] = False
        # Do NOT assert a cause this did not verify: OperationalError also
        # covers "database is locked". Name the missing index only when sqlite
        # actually said so; otherwise describe what happened, not why.
        detail = (
            "idx_candidates_ts is not present"
            if "no such index" in str(exc).lower()
            else "the grouped query could not run"
        )
        out["aggregation_note"] = (
            f"reason-level breakdown unavailable — {detail} ({exc}); only the "
            "decision totals below were read (count-only fallback, measured "
            "~1 s warm — far slower than the indexed path)"
        )
        for r in conn.execute(
            "SELECT decision, COUNT(*) AS c FROM candidates WHERE ts >= ? GROUP BY 1",
            (since,),
        ):
            decision = str(r["decision"] or "(none)")
            count = int(r["c"])
            out["by_decision"].append({"decision": decision, "count": count})
            out["scored"] += count
            if decision.upper() == "SKIP":
                out["skipped"] += count
            else:
                out["allowed"] += count
        out["by_decision"].sort(key=lambda d: -d["count"])
        return out

    by_decision: dict[str, int] = {}
    skip_variants: dict[str, list[tuple[str, int]]] = {}
    advisories: dict[str, int] = {}
    for decision, reason, count in rows:
        by_decision[decision] = by_decision.get(decision, 0) + count
        out["scored"] += count
        if decision.upper() == "SKIP":
            out["skipped"] += count
            skip_variants.setdefault(reason_family(reason) or "(none)", []).append(
                (reason, count)
            )
        else:
            out["allowed"] += count
            if reason:
                advisories[reason] = advisories.get(reason, 0) + count

    out["by_decision"] = sorted(
        ({"decision": k, "count": v} for k, v in by_decision.items()),
        key=lambda d: -d["count"],
    )
    out["distinct_skip_reasons"] = sum(len(v) for v in skip_variants.values())

    causes = []
    for family, variants in skip_variants.items():
        total = sum(c for _, c in variants)
        example = max(variants, key=lambda t: t[1])[0]
        decoded = decode_reason(example)
        # Aggregate the live measurements the strings embed, so the panel can
        # say "vetoed for quiet: ATR 0.41–0.79%, median 0.66%" instead of a
        # bare count.
        buckets: dict[str, list[tuple[float, int]]] = {}
        labels: dict[str, tuple[str, str]] = {}
        req_met: list[tuple[float, int]] = []
        req_of = None
        req_of_seen: set[int] = set()
        req_rows = 0
        req_met_sum = 0.0
        passes_by_key: dict[str, int] = {}
        for reason, count in variants:
            info = decode_reason(reason)
            if info["required"]:
                req_met.append((float(info["required"]["met"]), count))
                req_of = info["required"]["of"]
                req_of_seen.add(int(info["required"]["of"]))
                req_rows += count
                req_met_sum += float(info["required"]["met"]) * count
            for m in info["measurements"]:
                buckets.setdefault(m["key"], []).append((float(m["value"]), count))
                labels[m["key"]] = (m["label"], m["unit"])
                passes_by_key[m["key"]] = passes_by_key.get(m["key"], 0) + count
        # Mixed 'of' values inside one family would make of*total meaningless.
        split = (
            _required_split(total, req_of, req_rows, req_met_sum, passes_by_key, labels)
            if len(req_of_seen) == 1
            else {
                "derivable": False,
                "note": (
                    "this cause mixes different required-condition counts "
                    f"({sorted(req_of_seen)}), so one split cannot describe it"
                ),
                "of": None,
                "candidates": total,
                "checks": None,
                "passes": None,
                "failures": None,
                "conditions": [],
                "unnamed": None,
            }
            if req_of_seen
            else None
        )
        stats = []
        for key, pairs in buckets.items():
            s = _weighted_stats(pairs)
            if s:
                s.update({"key": key, "label": labels[key][0], "unit": labels[key][1]})
                stats.append(s)
        causes.append(
            {
                "family": family,
                "count": total,
                "variants": len(variants),
                "example": example,
                "plain": decoded["plain"],
                "required_of": req_of,
                "required_met": _weighted_stats(req_met),
                "required_split": split,
                "top_failure": _top_required_failure(split),
                # The values below were recorded when a condition PASSED (see
                # _required_split); the UI must never present them as the
                # measurements that caused the rejection.
                "measurements_are_passing_values": bool(split and split.get("derivable")),
                "measurements": sorted(stats, key=lambda s: s["key"]),
            }
        )
    out["skip_causes"] = sorted(causes, key=lambda c: -c["count"])[:20]
    advisory_rows = []
    for reason, count in advisories.items():
        info = decode_reason(reason)
        advisory_rows.append(
            {
                "reason": reason,
                "count": count,
                "plain": info["plain"],
                "measurements": info["measurements"],
            }
        )
    out["allow_advisories"] = sorted(advisory_rows, key=lambda d: -d["count"])[:10]
    return out


def _brain_terminal_stage(
    conn: sqlite3.Connection, since: float, allow_ids: set[int]
) -> dict[str, Any]:
    """Stage 2: what happened to the candidates the scorer ALLOWED.

    STRATEGY: poll-safe. MEASURED 6.2 ms for the 600-row rowid tail plus 7.9 ms
    to json-parse it. ``ORDER BY occurred_at DESC`` measured 123 ms (TEXT column
    with no standalone index -> full scan + temp b-tree); the table is
    append-only so rowid order IS time order, which was verified monotonic.

    CONSERVATION: events are joined to the window's ALLOW rows by
    ``payload_json.context.candidate_id`` -> ``candidates.id``, so the counts
    below are SUBSETS of ``allowed`` and the waterfall closes. Every ALLOW row
    with no event in the tail lands in ``residual`` — without that bucket the
    arithmetic would not add up and the panel would be lying.
    """
    out: dict[str, Any] = {
        "matched": 0,
        "residual": len(allow_ids),
        "filled": 0,
        "deferred": 0,
        "blocked": 0,
        "other": 0,
        "blocks": [],
        "tail_rows": 0,
        "tail_complete": True,
        "tail_oldest_utc": None,
        "unparsable_rows": 0,
    }
    rows = conn.execute(
        "SELECT occurred_at, payload_json FROM decision_events "
        "ORDER BY rowid DESC LIMIT ?",
        (_EVENT_TAIL_ROWS,),
    ).fetchall()
    out["tail_rows"] = len(rows)
    if not rows:
        return out
    oldest = _parse_iso(rows[-1]["occurred_at"])
    if oldest is not None:
        out["tail_oldest_utc"] = oldest.isoformat()
        # If the tail did not reach the window start, terminal counts are
        # TRUNCATED. Say so rather than publishing an understated number.
        out["tail_complete"] = oldest.timestamp() <= since or len(rows) < _EVENT_TAIL_ROWS

    best: dict[int, dict[str, Any]] = {}
    for row in rows:
        try:
            payload = json.loads(row["payload_json"] or "")
        except (json.JSONDecodeError, TypeError, ValueError):
            out["unparsable_rows"] += 1
            continue
        if not isinstance(payload, dict):
            out["unparsable_rows"] += 1
            continue
        ctx = payload.get("context")
        ctx = ctx if isinstance(ctx, dict) else {}
        cid = ctx.get("candidate_id")
        if not isinstance(cid, int) or cid not in allow_ids:
            continue
        outcome = str(payload.get("outcome") or "").lower()
        rank = _OUTCOME_RANK.get(outcome, 0)
        prior = best.get(cid)
        if prior is not None and prior["rank"] >= rank:
            continue
        best[cid] = {
            "rank": rank,
            "outcome": outcome or "(none)",
            "reason": str(ctx.get("reason") or ""),
            "stage": str(ctx.get("terminal_stage") or ""),
            "occurred_at": row["occurred_at"],
        }

    blocks: dict[tuple[str, str], int] = {}
    for info in best.values():
        if info["outcome"] == "filled":
            out["filled"] += 1
        elif info["outcome"] == "rejected":
            out["blocked"] += 1
            blocks[(info["reason"], info["stage"])] = (
                blocks.get((info["reason"], info["stage"]), 0) + 1
            )
        elif info["outcome"] == "deferred":
            out["deferred"] += 1
        else:
            out["other"] += 1
    out["matched"] = len(best)
    out["residual"] = max(0, len(allow_ids) - len(best))
    out["blocks"] = sorted(
        (
            {
                "reason": reason,
                "stage": stage,
                "count": count,
                "plain": decode_reason(reason)["plain"],
                "measurements": decode_reason(reason)["measurements"],
            }
            for (reason, stage), count in blocks.items()
        ),
        key=lambda b: -b["count"],
    )
    return out


def _brain_live_feed(root: Path) -> dict[str, Any]:
    """Recent entry INTENTS and their terminal fate, from the decision log tail.

    STRATEGY: poll-safe. MEASURED 1.41 ms to read the last 64 KB of
    ``data/mcp_decisions.jsonl`` (1.06 MB and growing) and parse the 65 cycle
    objects it contains — ~15 minutes of cover. A full parse measured 39.5 ms
    and grows without bound, so it is never done.

    The three cycle ``type``s have DIFFERENT top-level shapes and the parser
    branches on ``type``:
      * ``portfolio``        -> {decisions: {actions: [ ...entry intents... ]}}
      * ``rejection``        -> flat, one line per terminal block
      * ``position_monitor`` -> {decisions: {POSKEY: {...}}}

    EXIT DECISIONS ARE NOT DROPPED. ``core/mcp_brain.py``
    ``_algorithmic_position_monitor`` returns CLOSE at confidence 0.99 on the
    -12% hard max-loss backstop, CLOSE at 0.88 on trend-reversal-plus-loss,
    plus TAKE_PROFIT and BREAKEVEN. Filtering monitor cycles out by
    construction would give forced exits on losing positions ZERO coverage —
    exactly the bad-news class — so every non-HOLD monitor decision in the
    tail is surfaced and the HOLD rows are reported as a COUNTED residual.
    What ``monitor`` reports is a measurement OF THIS TAIL, never a claim
    about history: ``mcp_brain`` rotates this file to
    ``mcp_decisions.<timestamp>.jsonl`` past 2 MB, and only the last 64 KB of
    the active file is read.

    ``portfolio.actions[].decision_id`` joins to ``rejection.decision_id``,
    which is the whole intent -> terminal-block cascade in one file read.
    The first line after the seek is partial and is discarded; a truncated
    final line is tolerated (the bot appends to this file live).
    """
    path = _resolve(root, MCP_DECISIONS_PATH)
    meta = file_meta(path)
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "file": meta,
        "cycles": 0,
        "bytes_read": 0,
        "intents": [],
        "rejections_unmatched": 0,
        "monitor": {
            "cycles": 0,
            "decisions": 0,
            "hold": 0,
            "exits_found": 0,
            "exits": [],
        },
    }
    if not meta["exists"]:
        out["reason"] = "data/mcp_decisions.jsonl is absent — no live decision log on this machine"
        return out
    start = max(0, int(meta["size_bytes"] or 0) - _JSONL_TAIL_BYTES)
    try:
        with path.open("rb") as fh:
            fh.seek(start)
            raw = fh.read()
        blob = raw.decode("utf-8", "replace")
        out["bytes_read"] = len(raw)
    except OSError as exc:
        out["reason"] = f"could not read the decision log: {exc}"
        return out

    lines = blob.split("\n")
    if start > 0 and lines:
        lines = lines[1:]  # the first line after a mid-file seek is partial
    cycles: list[dict[str, Any]] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue  # truncated final line while the bot appends — expected
        if isinstance(obj, dict):
            cycles.append(obj)
    out["cycles"] = len(cycles)

    terminal: dict[str, dict[str, Any]] = {}
    for obj in cycles:
        if obj.get("type") == "rejection":
            did = obj.get("decision_id")
            if isinstance(did, str):
                terminal[did] = {
                    "reason": str(obj.get("reason") or ""),
                    "stage": str(obj.get("stage") or ""),
                    "ts_utc": obj.get("ts"),
                }

    # Position-monitor cycles: every non-HOLD action is an EXIT decision and is
    # kept. HOLD rows are counted, not discarded, so the panel can state the
    # split it measured instead of asserting one.
    monitor = out["monitor"]
    exits: list[dict[str, Any]] = []
    for obj in cycles:
        if obj.get("type") != "position_monitor":
            continue
        monitor["cycles"] += 1
        decisions = obj.get("decisions")
        if not isinstance(decisions, dict):
            continue
        for poskey, dec in decisions.items():
            if not isinstance(dec, dict):
                continue
            monitor["decisions"] += 1
            action = str(dec.get("action") or "").upper()
            conf = _finite(_num(dec.get("confidence")))
            if action in ("", "HOLD"):
                monitor["hold"] += 1
                continue
            monitor["exits_found"] += 1
            exits.append(
                {
                    "ts_utc": obj.get("ts"),
                    "position": str(poskey),
                    "action": action,
                    "confidence": conf,
                    "reason": str(dec.get("reason") or ""),
                    "source": str(dec.get("source") or ""),
                }
            )
    exits.reverse()
    monitor["exits"] = exits[:20]

    intents: list[dict[str, Any]] = []
    matched: set[str] = set()
    for obj in cycles:
        if obj.get("type") != "portfolio":
            continue
        decisions = obj.get("decisions")
        actions = decisions.get("actions") if isinstance(decisions, dict) else None
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict):
                continue
            did = action.get("decision_id")
            fate = terminal.get(did) if isinstance(did, str) else None
            if fate is not None and isinstance(did, str):
                matched.add(did)
            intents.append(
                {
                    "ts_utc": obj.get("ts"),
                    "type": action.get("type"),
                    "symbol": action.get("symbol"),
                    "exchange": action.get("exchange"),
                    "side": action.get("side"),
                    "leverage": _finite(_num(action.get("leverage"))),
                    "mcp_score": _finite(_num(action.get("mcp_score"))),
                    "confidence": _finite(_num(action.get("confidence"))),
                    "p_win_ensemble": _finite(_num(action.get("p_win_ensemble"))),
                    "sl_pct": _finite(_num(action.get("sl_pct"))),
                    "tp_pct": _finite(_num(action.get("tp_pct"))),
                    "reason": str(action.get("reason") or ""),
                    "outcome": (
                        {
                            "reason": fate["reason"],
                            "stage": fate["stage"],
                            "plain": decode_reason(fate["reason"])["plain"],
                        }
                        if fate
                        else None
                    ),
                }
            )
    intents.reverse()
    out["intents"] = intents[:30]
    out["rejections_unmatched"] = max(0, len(terminal) - len(matched))
    out["available"] = True
    return out


def load_brain(
    root: Path | None = None, *, window_minutes: int = BRAIN_WINDOW_MINUTES
) -> dict[str, Any]:
    """The live-reasoning half of the Brain tab: the decision cascade.

    STRATEGY: poll-safe, one connection for both warehouse queries. MEASURED
    total ~25 ms warm (scorer GROUP BY 5 ms + ALLOW ids 1 ms + event tail 6 ms +
    payload parse 8 ms + decision-log tail 1.4 ms) against a 250 ms budget.
    Connection setup dominates the fixed cost, which is why both queries share
    one connection rather than opening one per panel.
    """
    root = root or ROOT
    path = _resolve(root, WAREHOUSE_PATH)
    meta = file_meta(path)
    window_minutes = max(5, min(int(window_minutes or BRAIN_WINDOW_MINUTES), 720))
    since = time.time() - window_minutes * 60
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "file": meta,
        "window_minutes": window_minutes,
        "since_utc": datetime.fromtimestamp(since, timezone.utc).isoformat(),
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "scorer": {},
        "terminal": {},
        "binding": {"scorer": None, "downstream": None},
        "context": {},
        "live": _brain_live_feed(root),
    }
    if not meta["exists"]:
        out["reason"] = "data/warehouse.sqlite is absent — no decision cascade on this machine"
        return out

    conn = None
    try:
        conn = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True, timeout=5.0)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA query_only = ON")
        conn.execute("PRAGMA busy_timeout = 4000")
        scorer = _brain_scorer_stage(conn, since)
        allow_ids: set[int] = set()
        allow_crypto = 0
        allow_tradfi = 0
        try:
            for r in conn.execute(
                "SELECT id, symbol FROM candidates INDEXED BY idx_candidates_ts "
                "WHERE ts > ? AND decision <> 'SKIP'",
                (since,),
            ):
                if r[0] is not None:
                    allow_ids.add(int(r[0]))
                if _tradfi_allow_base(r[1] if len(r) > 1 else None):
                    allow_tradfi += 1
                else:
                    allow_crypto += 1
        except sqlite3.OperationalError:
            allow_ids = set()
            allow_crypto = 0
            allow_tradfi = 0
        terminal = _brain_terminal_stage(conn, since, allow_ids)
        terminal["join_available"] = bool(allow_ids) or scorer["allowed"] == 0
        out["scorer"] = scorer
        out["terminal"] = terminal
        out["binding"] = _binding_constraints(scorer, terminal)
        out["context"] = _brain_scorer_context(
            scorer, allow_crypto=allow_crypto, allow_tradfi=allow_tradfi
        )
        out["available"] = True
    except sqlite3.Error as exc:
        out["reason"] = f"warehouse query failed: {exc}"
    finally:
        if conn is not None:
            conn.close()
    return out


def _binding_constraints(
    scorer: dict[str, Any], terminal: dict[str, Any]
) -> dict[str, Any]:
    """Name the single condition killing the most candidates AT EACH STAGE.

    Reported per stage on purpose. The scorer's dominant cause and the dominant
    downstream block are usually different things (2026-07-28: scorer
    ``scalp_req_fail``, downstream ``economic_gate_stressed_breakeven``), and a
    single "the binding constraint is X" would name the scorer while the thing
    actually preventing trades sits downstream.
    """
    causes = scorer.get("skip_causes") or []
    blocks = terminal.get("blocks") or []
    scorer_out = None
    if causes:
        top = causes[0]
        skipped = scorer.get("skipped") or 0
        scorer_out = {
            "stage": "scorer",
            "family": top["family"],
            "plain": top["plain"],
            "count": top["count"],
            "share_of_stage": round(top["count"] / skipped, 4) if skipped else None,
            "measurements": top["measurements"],
            "measurements_are_passing_values": top.get("measurements_are_passing_values", False),
            "required_of": top.get("required_of"),
            "required_met": top.get("required_met"),
            "required_split": top.get("required_split"),
            "top_failure": top.get("top_failure"),
            "example": top["example"],
        }
    downstream_out = None
    if blocks:
        top = blocks[0]
        blocked = terminal.get("blocked") or 0
        downstream_out = {
            "stage": top["stage"] or "downstream",
            "family": top["reason"],
            "plain": top["plain"],
            "count": top["count"],
            "share_of_stage": round(top["count"] / blocked, 4) if blocked else None,
            "measurements": top["measurements"],
        }
    return {"scorer": scorer_out, "downstream": downstream_out}


# --- research pipeline (Brain, part 2) ------------------------------------

ARTIFACT_TTL_SECONDS = 300.0
# (cache_key, stored_at, payload). Held as ONE tuple and replaced by a single
# assignment: Starlette runs these sync handlers in a threadpool, and a
# three-field dict could be read half-updated. Rebinding a module global is
# atomic under the GIL, so a concurrent reader sees either the old triple or
# the new one, never a mixture.
_ARTIFACT_CACHE: tuple[str | None, float, dict[str, Any] | None] = (None, 0.0, None)
# Heading text keyed by (name, mtime): a re-scan only re-opens files whose
# mtime moved, so the steady state collapses to the scandir alone.
_HEADING_CACHE: dict[tuple[str, float], str | None] = {}

_ARTIFACT_KINDS = (
    "prereg",
    "screen",
    "audit",
    "verdict",
    "verdicts",
    "debate",
    "integration",
    "review",
    "scout",
    "adjudication",
    "rebuttal",
)
_RUN_RE = re.compile(r"^(\d+)[_.]")


def _artifact_heading(path: Path, name: str, mtime: float) -> str | None:
    """First markdown heading of an artifact, cached by (name, mtime)."""
    key = (name, mtime)
    if key in _HEADING_CACHE:
        return _HEADING_CACHE[key]
    head: str | None = None
    try:
        with path.open("r", encoding="utf-8", errors="replace") as fh:
            for _ in range(12):
                line = fh.readline()
                if not line:
                    break
                stripped = line.strip()
                if stripped.startswith("#"):
                    head = stripped.lstrip("#").strip()[:180]
                    break
    except OSError:
        head = None
    if len(_HEADING_CACHE) > 400:
        _HEADING_CACHE.clear()
    _HEADING_CACHE[key] = head
    return head


def load_research_artifacts(
    root: Path | None = None, *, heads: int = 12, ttl: float = ARTIFACT_TTL_SECONDS
) -> dict[str, Any]:
    """Inventory of ``_workspace/strategy_pipeline`` — FILE PRESENCE ONLY.

    STRATEGY: cached with a TTL, served stale-with-age. MEASURED 3.0 ms warm /
    132 ms cold for the scandir plus 12 heading reads (153 files). The cold
    figure is why this is cached rather than run on every 12 s poll; the TTL is
    300 s because artifacts move on the order of hours.

    HONESTY — the hard limit of this panel: **nothing on disk records whether a
    screen is running.** A ``NN_prereg_*.md`` with no matching ``NN_screen_*``
    is indistinguishable from queued, abandoned, or renamed. This reader
    therefore reports which artifact KINDS exist per run id and when each was
    last touched, and never infers a pipeline status or an ordered queue.
    """
    global _ARTIFACT_CACHE
    root = root or ROOT
    directory = _resolve(root, PIPELINE_DIR)
    heads = max(0, min(int(heads or 0), 40))
    now = time.time()
    cache_key = f"{directory}|{heads}"
    cached_key, cached_at, cached_value = _ARTIFACT_CACHE
    if (
        cached_value is not None
        and cached_key == cache_key
        and (now - cached_at) < max(0.0, ttl)
    ):
        value = dict(cached_value)
        value["cache_age_seconds"] = round(now - cached_at, 3)
        value["cached"] = True
        return value

    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "directory": str(PIPELINE_DIR).replace("\\", "/"),
        "file_count": 0,
        "runs": [],
        "recent": [],
        "cached": False,
        "cache_age_seconds": 0.0,
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "caption": (
            "File presence and mtime only. Nothing on disk records whether a "
            "screen is running, so this is an inventory, not a pipeline status."
        ),
    }
    try:
        entries = [
            (e.name, e.stat().st_mtime)
            for e in os.scandir(directory)
            if e.is_file()
        ]
    except OSError as exc:
        out["reason"] = f"{PIPELINE_DIR} could not be read: {exc}"
        _ARTIFACT_CACHE = (cache_key, now, dict(out))
        return out

    entries.sort(key=lambda t: -t[1])
    out["file_count"] = len(entries)
    runs: dict[str, dict[str, Any]] = {}
    for name, mtime in entries:
        m = _RUN_RE.match(name)
        run_id = m.group(1) if m else "(unnumbered)"
        slot = runs.setdefault(
            run_id, {"run": run_id, "files": 0, "kinds": [], "newest_mtime": 0.0}
        )
        slot["files"] += 1
        slot["newest_mtime"] = max(slot["newest_mtime"], mtime)
        lowered = name.lower()
        for kind in _ARTIFACT_KINDS:
            if kind in lowered and kind not in slot["kinds"]:
                slot["kinds"].append(kind)

    ordered = sorted(runs.values(), key=lambda r: -r["newest_mtime"])
    out["runs"] = [
        {
            "run": r["run"],
            "files": r["files"],
            "kinds": sorted(r["kinds"]),
            # Factual statements about which files exist — NOT a claim about
            # what the pipeline is doing.
            "has_verdict": bool({"verdict", "verdicts", "audit"} & set(r["kinds"])),
            "prereg_only": r["kinds"] == ["prereg"],
            "newest_utc": datetime.fromtimestamp(
                r["newest_mtime"], timezone.utc
            ).isoformat(),
            "newest_age_seconds": round(max(0.0, now - r["newest_mtime"]), 1),
        }
        for r in ordered[:12]
    ]
    out["recent"] = [
        {
            "name": name,
            "mtime_utc": datetime.fromtimestamp(mtime, timezone.utc).isoformat(),
            "age_seconds": round(max(0.0, now - mtime), 1),
            "heading": _artifact_heading(directory / name, name, mtime),
        }
        for name, mtime in entries[:heads]
    ]
    out["available"] = True
    _ARTIFACT_CACHE = (cache_key, now, dict(out))
    return out


_LEDGER_SECTIONS = (
    ("shadow", "## In shadow"),
    ("refuted", "## Refuted"),
    ("open", "## Open"),
    ("reopen_tests", "### Reopen tests run"),
)


def load_refuted_ledger(root: Path | None = None) -> dict[str, Any]:
    """Counts and family names from the refuted-families ledger.

    STRATEGY: poll-safe. MEASURED 0.31–0.45 ms to read and scan the 23 KB file.

    Deliberately NOT a copy of the ledger: only the family name and date of each
    row are lifted, plus the path. The verdict-evidence column is thousands of
    characters of binding reasoning and belongs in the file, which stays the one
    source of truth.
    """
    root = root or ROOT
    path = _resolve(root, LEDGER_PATH)
    meta = file_meta(path)
    out: dict[str, Any] = {
        "available": False,
        "reason": "",
        "file": meta,
        "path": str(LEDGER_PATH).replace("\\", "/"),
        "counts": {},
        "families": {},
    }
    if not meta["exists"]:
        out["reason"] = "the refuted-families ledger is not present on this machine"
        return out
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        out["reason"] = f"the ledger could not be read: {exc}"
        return out

    section = None
    counts: dict[str, int] = {}
    families: dict[str, list[dict[str, Any]]] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if line.startswith("#"):
            section = None
            for key, prefix in _LEDGER_SECTIONS:
                if line.startswith(prefix):
                    section = key
                    break
            continue
        if section is None or not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) < 2 or set(cells[0]) <= {"-", ":"} or cells[0] in {"Family", "Date"}:
            continue
        counts[section] = counts.get(section, 0) + 1
        # Column 1 is the family (or the date, for the reopen-tests table);
        # the LAST column is the date in every table. Nothing in between is
        # lifted — that is the verdict evidence and it stays in the file.
        families.setdefault(section, []).append(
            {"family": cells[0][:160], "date": cells[-1][:40]}
        )
    out["counts"] = counts
    out["families"] = {k: v[:40] for k, v in families.items()}
    out["available"] = bool(counts)
    if not counts:
        out["reason"] = (
            "the ledger is present but no table rows could be parsed — its shape "
            "may have changed; read the file directly"
        )
    return out


def load_promotion_path(root: Path | None = None) -> dict[str, Any]:
    """Whether any owner-signed promotion dossier exists.

    STRATEGY: poll-safe. A single ``Path.exists`` plus, if present, a listing.
    Reported because the honest answer today is "none has ever been written",
    and an absent directory must read as that rather than as an empty table.
    """
    root = root or ROOT
    directory = _resolve(root, DOSSIER_DIR)
    out: dict[str, Any] = {
        "directory": str(DOSSIER_DIR).replace("\\", "/"),
        "exists": directory.exists(),
        "dossiers": [],
        "note": (
            "Probes are LOG-ONLY. Promotion requires the frozen gate to pass AND "
            "an owner signature — no probe can place an order."
        ),
    }
    if not out["exists"]:
        return out
    try:
        out["dossiers"] = sorted(
            e.name for e in os.scandir(directory) if e.is_file()
        )[:40]
    except OSError as exc:
        out["dossiers"] = []
        out["note"] = f"{out['note']} (directory listing failed: {exc})"
    return out


def load_mtsi(root: Path | None = None) -> dict[str, Any]:
    """Read-only MTSI inventory / micro-clip status for Mission Deck.

    Fail-open when ``data/mtsi_status.json`` is missing (research not run yet).
    Honesty: inventory balance ≠ edge.
    """
    base = root if root is not None else Path(".")
    path = _resolve(base, Path("data/mtsi_status.json"))
    empty = {
        "family": "mtsi_inventory_v1",
        "present": False,
        "final_verdict": "NOT_RUN",
        "max_gross_inventory_usd": 1.0,
        "inventory_usd": 0.0,
        "inventory_utilization": 0.0,
        "clip_pnl_histogram": [],
        "display_cell": None,
        "n_clips": 0,
        "mean_clip_pnl_usd": 0.0,
        "candle_spark": [],
        "honesty": (
            "MTSI not run yet — balanced inventory ≤ $1 is a control, not an edge claim."
        ),
        "file": file_meta(path),
    }
    if not path.is_file():
        return empty
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        empty["honesty"] = "mtsi_status.json unreadable — fail-open empty metrics"
        return empty
    if not isinstance(raw, dict):
        return empty
    hist = raw.get("clip_pnl_histogram") or []
    if not isinstance(hist, list):
        hist = []
    spark = raw.get("candle_spark") or []
    if not isinstance(spark, list):
        spark = []
    inv = float(raw.get("inventory_usd") or 0.0)
    cap = float(raw.get("max_gross_inventory_usd") or 1.0) or 1.0
    return {
        "family": str(raw.get("family") or "mtsi_inventory_v1"),
        "present": True,
        "final_verdict": str(raw.get("final_verdict") or "UNKNOWN"),
        "max_gross_inventory_usd": cap,
        "inventory_usd": inv,
        "inventory_utilization": float(raw.get("inventory_utilization") or abs(inv) / cap),
        "clip_pnl_histogram": [int(x) for x in hist[:32]],
        "display_cell": raw.get("display_cell"),
        "n_clips": int(raw.get("n_clips") or 0),
        "mean_clip_pnl_usd": float(raw.get("mean_clip_pnl_usd") or 0.0),
        "candle_spark": [float(x) for x in spark[:120]],
        "honesty": str(
            raw.get("honesty")
            or "balanced inventory ≠ edge — many small clips only"
        ),
        "file": file_meta(path),
    }


def validate_bind_host(host: str, *, allow_lan: bool = False, token_set: bool = False) -> None:
    """Refuse non-loopback binds unless explicitly allowed with a token."""
    h = (host or "").strip().lower()
    loopback = {"127.0.0.1", "localhost", "::1"}
    if h in loopback:
        return
    if allow_lan and token_set:
        return
    raise ValueError(
        f"refusing bind host {host!r}: Mission Control binds loopback only "
        "(set MISSION_CONTROL_ALLOW_LAN=1 with MISSION_CONTROL_TOKEN to override)"
    )


def token_from_environ(environ: dict[str, str] | None = None) -> str:
    env = environ if environ is not None else os.environ
    return str(env.get("MISSION_CONTROL_TOKEN") or "").strip()
