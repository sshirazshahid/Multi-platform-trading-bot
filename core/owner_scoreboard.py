"""
core/owner_scoreboard.py — the owner's plain-English daily scoreboard.

One page that answers, without jargon:
  1. Is the bot running, and in which mode?
  2. How much money did each part of it make today / this week?
       - the directional trading strategy,
       - the funding-carry strategy (F1),
       - interest on idle cash (NOT trading profit; core/idle_yield.py).
  3. If a part is not trading, why not?

READ-ONLY over the bot's state files; never raises on missing or malformed
files (a missing input is shown as "not available"). Writes one markdown
file per UTC day to reports/owner_scoreboard_<date>.md.
"""
from __future__ import annotations

import json
import math
import re
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parents[1]

HEARTBEAT_FRESH_SEC = 15 * 60
GATE_WINDOW_SEC = 24 * 3600
GATE_TAIL_BYTES = 4 * 1024 * 1024
_EDGE_RE = re.compile(r"edge (-?\d+(?:\.\d+)?)bps < (-?\d+(?:\.\d+)?)bps")
_NUM_RE = re.compile(r"-?\d+(?:\.\d+)?")


# ── readers (never raise) ────────────────────────────────────────────────


def _read_json(path: Path) -> Optional[Any]:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _tail_jsonl(path: Path, *, max_bytes: int = GATE_TAIL_BYTES) -> list[dict]:
    """Parse JSON lines from the last ``max_bytes`` of a (possibly huge) file."""
    try:
        size = path.stat().st_size
        with path.open("rb") as fh:
            if size > max_bytes:
                fh.seek(size - max_bytes)
                fh.readline()  # drop the partial first line
            chunk = fh.read()
    except Exception:
        return []
    rows = []
    for line in chunk.decode("utf-8", errors="replace").splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _epoch(value: Any) -> Optional[float]:
    number = _num(value)
    if number is not None:
        return number
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        return (dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)).timestamp()
    return None


def _day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


def _week_days(now: float) -> set[str]:
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    return {(today - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(7)}


# ── sections ─────────────────────────────────────────────────────────────


def _bot_section(data: Path, now: float) -> dict:
    hb = _read_json(data / "heartbeat.json")
    if not isinstance(hb, dict):
        return {"available": False}
    ts = _epoch(hb.get("timestamp"))
    age = None if ts is None else max(0.0, now - ts)
    return {
        "available": ts is not None,
        "running": age is not None and age <= HEARTBEAT_FRESH_SEC,
        "age_sec": age,
        "mode": str(hb.get("operating_mode") or "UNKNOWN"),
        "entry_policy": str(hb.get("entry_policy") or "UNKNOWN"),
    }


def _trading_section(data: Path, now: float) -> dict:
    raw = _read_json(data / "positions.json")
    out = {"available": isinstance(raw, dict), "today_usd": 0.0, "today_trades": 0,
           "week_usd": 0.0, "week_trades": 0, "week_wins": 0, "open_positions": 0}
    if not isinstance(raw, dict):
        return out
    today, week = _day(now), _week_days(now)
    opened = raw.get("open")
    out["open_positions"] = len(opened) if isinstance(opened, list) else 0
    for pos in raw.get("closed") or []:
        if not isinstance(pos, dict):
            continue
        pnl, closed = _num(pos.get("pnl")), _epoch(pos.get("close_time"))
        if pnl is None or closed is None:
            continue
        day = _day(closed)
        if day == today:
            out["today_usd"] += pnl
            out["today_trades"] += 1
        if day in week:
            out["week_usd"] += pnl
            out["week_trades"] += 1
            out["week_wins"] += pnl > 0
    return out


def _gate_section(data: Path, now: float) -> dict:
    path = data / "carry_gate_log.jsonl"
    out = {"available": path.exists(), "checks": 0, "passes": 0,
           "best_edge_bps": None, "needed_edge_bps": None, "top_reasons": []}
    reasons: Counter = Counter()
    for row in _tail_jsonl(path):
        ts = _num(row.get("ts"))
        if ts is None or now - ts > GATE_WINDOW_SEC or ts > now + 60:
            continue
        out["checks"] += 1
        out["passes"] += bool(row.get("ok"))
        reason = str(row.get("reason") or "")
        match = _EDGE_RE.search(reason)
        if match:
            edge, needed = float(match.group(1)), float(match.group(2))
            if out["best_edge_bps"] is None or edge > out["best_edge_bps"]:
                out["best_edge_bps"], out["needed_edge_bps"] = edge, needed
        if reason and not row.get("ok"):
            reasons[_NUM_RE.sub("#", reason)] += 1
    out["top_reasons"] = reasons.most_common(3)
    return out


def _carry_section(data: Path, now: float) -> dict:
    raw = _read_json(data / "carry_positions.json")
    out = {"available": isinstance(raw, dict), "today_usd": 0.0, "week_usd": 0.0,
           "all_time_usd": 0.0, "resolved_cycles": 0, "open_positions": 0,
           "recovery_latched": False}
    if isinstance(raw, dict):
        today, week = _day(now), _week_days(now)
        positions = raw.get("positions")
        out["open_positions"] = len(positions) if isinstance(positions, dict) else 0
        recovery = raw.get("recovery")
        out["recovery_latched"] = bool(isinstance(recovery, dict) and recovery.get("active"))
        for cyc in raw.get("cycles") or []:
            if not isinstance(cyc, dict) or cyc.get("label_status") != "RESOLVED":
                continue
            pnl, resolved = _num(cyc.get("net_pnl")), _epoch(cyc.get("resolved_ts"))
            if pnl is None or resolved is None:
                continue
            out["all_time_usd"] += pnl
            out["resolved_cycles"] += 1
            day = _day(resolved)
            out["today_usd"] += pnl if day == today else 0.0
            out["week_usd"] += pnl if day in week else 0.0
    out["gate_24h"] = _gate_section(data, now)
    return out


def _interest_section(data: Path, now: float) -> dict:
    raw = _read_json(data / "idle_yield.json")
    out = {"available": isinstance(raw, dict), "today_usd": 0.0, "week_usd": 0.0,
           "all_time_usd": 0.0, "apy_pct": None, "rate_status": None}
    if not isinstance(raw, dict):
        return out
    daily = raw.get("daily") if isinstance(raw.get("daily"), dict) else {}
    week = _week_days(now)
    for day, row in daily.items():
        earned = _num((row or {}).get("earned_usd")) if isinstance(row, dict) else None
        if earned is None:
            continue
        out["today_usd"] += earned if day == _day(now) else 0.0
        out["week_usd"] += earned if day in week else 0.0
    out["all_time_usd"] = _num(raw.get("total_earned_usd")) or 0.0
    rate = raw.get("rate") if isinstance(raw.get("rate"), dict) else {}
    out["apy_pct"] = _num(rate.get("apy_pct"))
    out["rate_status"] = rate.get("status")
    return out


def build_scoreboard(root: Path | str = ROOT, *, now: Optional[float] = None) -> dict:
    root = Path(root)
    now = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    data = root / "data"
    board = {
        "date": _day(now),
        "now": now,
        "bot": _bot_section(data, now),
        "trading": _trading_section(data, now),
        "carry": _carry_section(data, now),
        "interest": _interest_section(data, now),
    }
    lanes = (board["trading"], board["carry"], board["interest"])
    board["totals"] = {
        "today_usd": sum(lane["today_usd"] for lane in lanes),
        "week_usd": sum(lane.get("week_usd", 0.0) for lane in lanes),
    }
    return board


# ── rendering ────────────────────────────────────────────────────────────


def _usd(value: float) -> str:
    sign = "+" if value > 0 else ("-" if value < 0 else "")
    return f"{sign}${abs(value):,.2f}"


def _bot_lines(bot: dict) -> list[str]:
    if not bot.get("available"):
        return ["**Is the bot running?** Not available — no heartbeat file found."]
    age_h = (bot.get("age_sec") or 0.0) / 3600.0
    if bot.get("running"):
        alive = f"**Is the bot running?** Yes — it checked in {age_h * 60:.0f} minutes ago."
    else:
        alive = (f"**Is the bot running?** No — last check-in was {age_h:.1f} hours ago. "
                 "Restart it with TradingBot.bat, option [1].")
    mode = bot.get("mode", "UNKNOWN")
    meaning = {"PAPER": "simulated money, no real orders",
               "OBSERVATION": "watching only, no trades at all",
               "CONTROLLED_LIVE": "REAL MONEY"}.get(mode, "unknown")
    return [alive, f"**Mode:** {mode} ({meaning})."]


def _trading_note(board: dict) -> str:
    policy = board["bot"].get("entry_policy", "UNKNOWN")
    if policy == "SHADOW_ONLY":
        return ("The trading strategy is set to SHADOW_ONLY: it is not placing paper "
                "trades, only recording what it would have done.")
    if policy == "APPROVED_PAPER":
        return ("The trading strategy is placing paper trades. Its measured history shows "
                "no proven edge after costs, so expect small losses here. To stop it, see "
                "docs/owner/START_HERE.md, step 2.")
    return f"Trading-strategy entry policy: {policy}."


def _carry_note(carry: dict) -> str:
    if carry.get("recovery_latched"):
        return ("The carry strategy is LOCKED after an unexpected event and needs a human "
                "check before it can trade again (docs/owner/TRADING_PLAN.md §5).")
    if carry.get("open_positions"):
        return f"The carry strategy has {carry['open_positions']} position(s) open, collecting funding."
    gate = carry.get("gate_24h") or {}
    if not gate.get("available") or not gate.get("checks"):
        return ("No carry checks were logged in the last 24 hours — the carry runner may not "
                "be scheduled (Windows Task Scheduler task TradingBot-F1CarryPaper).")
    if gate.get("best_edge_bps") is not None:
        return (f"The carry strategy is waiting for bigger funding payments. Best chance in "
                f"the last 24 hours: {gate['best_edge_bps']:.1f} bps of expected profit; it "
                f"needs {gate['needed_edge_bps']:.1f} bps before it will trade. Waiting is "
                "the rule working, not a fault.")
    reasons = ", ".join(r for r, _ in gate.get("top_reasons") or []) or "none logged"
    return (f"The carry strategy checked {gate['checks']} times in 24 hours and did not trade. "
            f"Most common reasons: {reasons}.")


def _interest_note(interest: dict) -> str:
    if not interest.get("available"):
        return "Interest on idle cash: not available yet (the ledger starts after the bot runs an hour)."
    apy = interest.get("apy_pct")
    if apy is None:
        return (f"Interest on idle cash: nothing booked right now — the reference rate is "
                f"unavailable ({interest.get('rate_status')}). No rate means no money is "
                "counted.")
    return (f"Interest on idle cash is counted at {apy:.2f}% a year (a public stablecoin "
            "savings rate). This is not trading profit — it is what your idle cash would "
            "earn in a savings product. Earning it for real needs your decision (START_HERE.md).")


def render_markdown(board: dict) -> str:
    t, c, i, tot = board["trading"], board["carry"], board["interest"], board["totals"]
    lines = [f"# Your trading bot — daily scoreboard ({board['date']}, UTC)", ""]
    lines += _bot_lines(board["bot"]) + [""]
    lines += [
        "## Money made, by where it came from", "",
        "| Source | Today | Last 7 days |",
        "|---|---|---|",
        (f"| Trading strategy | {_usd(t['today_usd'])} ({t['today_trades']} trades) | "
         f"{_usd(t['week_usd'])} ({t['week_trades']} trades, {t['week_wins']} won) |"
         if t["available"] else "| Trading strategy | not available | not available |"),
        (f"| Funding-carry strategy | {_usd(c['today_usd'])} | {_usd(c['week_usd'])} |"
         if c["available"] else "| Funding-carry strategy | not available | not available |"),
        (f"| Interest on idle cash (not trading) | {_usd(i['today_usd'])} | {_usd(i['week_usd'])} |"
         if i["available"] else "| Interest on idle cash (not trading) | not available | not available |"),
        f"| **Total** | **{_usd(tot['today_usd'])}** | **{_usd(tot['week_usd'])}** |",
        "",
        "## What each part is doing", "",
        f"- **Trading strategy:** {_trading_note(board)}",
        f"- **Funding-carry strategy:** {_carry_note(c)}",
        f"- **Interest:** {_interest_note(i)}",
        "",
        "_Past results do not predict future results. A flat or slightly negative "
        "week is normal and is not, by itself, a reason to change anything._",
        "",
    ]
    return "\n".join(lines)


def write_scoreboard(root: Path | str = ROOT, *, now: Optional[float] = None) -> Path:
    root = Path(root)
    board = build_scoreboard(root, now=now)
    out = root / "reports" / f"owner_scoreboard_{board['date']}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_markdown(board), encoding="utf-8")
    return out
