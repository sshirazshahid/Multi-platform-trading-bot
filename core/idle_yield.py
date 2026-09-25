"""
core/idle_yield.py — PAPER-only idle-cash yield ledger.

Most of the time the paper wallet holds idle USDT: the carry lane (F1) is
gated shut in compressed-funding regimes and the directional book has no
measured edge (`_workspace/strategy_pipeline/92_*`, `93_*`). In real life that
same cash could sit in a stablecoin savings product and earn the market
supply rate. This module books that interest so the owner sees the honest
opportunity cost of idle money.

It is YIELD, NOT TRADING EDGE, and it is kept apart from trading evidence:

  * its own ledger file (data/idle_yield.json) — never the trading wallet,
    so no trading-lane P&L, sizing or promotion metric is touched;
  * fail-closed — no fresh, sane reference rate means no accrual;
  * conservative — a gap longer than MAX_GAP_SEC (bot down) credits only
    MAX_GAP_SEC; the remainder is recorded as skipped, never back-filled;
  * organic rate only — the reference is the pool's base supply APY, never
    token-incentive rewards;
  * PAPER-latched — refuses to write outside PAPER/DRY_RUN.

Reference rate: DefiLlama yields chart for the USDC supply pool on Aave V3
(Ethereum). It is a public proxy for "what stablecoins earn at a large,
long-running lending venue". Earning it for real means depositing to a
savings product, which carries platform/counterparty risk — that decision is
the owner's and is NOT made here (docs/owner/TRADING_PLAN.md §3).
"""
from __future__ import annotations

import json
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Optional

from loguru import logger

SCHEMA_VERSION = 1
DEFAULT_PATH = Path("data/idle_yield.json")

# DefiLlama pool id for USDC supply on Aave V3 (Ethereum).
REFERENCE_POOL_ID = "aa70268e-4b52-42bf-a116-608b370f9501"
REFERENCE_URL = f"https://yields.llama.fi/chart/{REFERENCE_POOL_ID}"
REFERENCE_DESC = "USDC supply rate, Aave V3 (Ethereum), via DefiLlama — base APY only"

YEAR_SEC = 365.0 * 86400.0
MAX_GAP_SEC = 2 * 3600.0          # longest slice credited per tick (job runs hourly)
RATE_REFRESH_SEC = 6 * 3600.0     # re-fetch the reference rate at most this often
RATE_MAX_AGE_SEC = 3 * 86400.0    # a reading older than this is stale -> no accrual
DEFAULT_MAX_APY_PCT = 8.0         # sanity ceiling; above it the reading is anomalous
MAX_DAILY_ROWS = 400

LABEL = (
    "Idle-cash stablecoin yield (PAPER). Not trading profit: it is what idle "
    "paper cash would earn at the reference savings rate."
)

FetchJson = Callable[[str], Any]


# ── rate parsing ─────────────────────────────────────────────────────────


def _to_epoch(value: Any) -> Optional[float]:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return float(value)
    if isinstance(value, str) and value:
        try:
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.timestamp()
    return None


def _num(value: Any) -> Optional[float]:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def _base_apy(point: dict) -> Optional[float]:
    base = _num(point.get("apyBase"))
    if base is not None:
        return base
    # No explicit base: accept the headline APY only if it carries no rewards.
    reward = _num(point.get("apyReward"))
    if reward in (None, 0.0):
        return _num(point.get("apy"))
    return None


def parse_reference_chart(
    payload: Any, *, now: float, max_apy_pct: float = DEFAULT_MAX_APY_PCT
) -> tuple[Optional[float], Optional[float], str]:
    """Return (apy_pct, as_of_ts, reason). apy_pct is None unless reason == "ok"."""
    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list) or not data:
        return None, None, "malformed: no data points"
    for point in reversed(data):
        if not isinstance(point, dict):
            continue
        apy = _base_apy(point)
        if apy is None:
            continue
        as_of = _to_epoch(point.get("timestamp"))
        if as_of is None:
            return None, None, "malformed: unparseable timestamp"
        if now - as_of > RATE_MAX_AGE_SEC:
            return None, as_of, f"stale: reading is {(now - as_of) / 86400:.1f}d old"
        if apy < 0 or apy > max_apy_pct:
            return None, as_of, f"anomalous: {apy:.2f}% outside [0, {max_apy_pct:.1f}]%"
        return apy, as_of, "ok"
    return None, None, "malformed: no base APY in any point"


# ── arithmetic ───────────────────────────────────────────────────────────


def interest_for(principal_usd: float, apy_pct: float, seconds: float) -> float:
    """Interest on ``principal_usd`` over ``seconds`` at an annual percentage yield."""
    values = (principal_usd, apy_pct, seconds)
    if not all(isinstance(v, (int, float)) and math.isfinite(float(v)) for v in values):
        return 0.0
    if principal_usd <= 0 or apy_pct <= 0 or seconds <= 0:
        return 0.0
    return float(principal_usd) * ((1.0 + apy_pct / 100.0) ** (seconds / YEAR_SEC) - 1.0)


# ── state ────────────────────────────────────────────────────────────────


def new_state() -> dict:
    return {
        "schema": SCHEMA_VERSION,
        "label": LABEL,
        "reference": {
            "source": "DefiLlama yields",
            "pool_id": REFERENCE_POOL_ID,
            "url": REFERENCE_URL,
            "description": REFERENCE_DESC,
        },
        "total_earned_usd": 0.0,
        "skipped_seconds": 0.0,
        "last_accrual_ts": None,
        "last_idle_usd": None,
        "rate": {"apy_pct": None, "as_of_ts": None, "fetched_ts": None,
                 "status": "not_fetched"},
        "daily": {},
    }


def load_state(path: Path | str = DEFAULT_PATH) -> dict:
    path = Path(path)
    state = new_state()
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(data, dict) and data.get("schema") == SCHEMA_VERSION:
                state.update(data)
    except Exception as e:  # corrupt file: start fresh, never crash the bot
        logger.warning(f"[IdleYield] ledger unreadable ({type(e).__name__}); starting fresh")
    return state


def save_state(state: dict, path: Path | str = DEFAULT_PATH) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, path)


def _trim_daily(state: dict) -> None:
    daily = state.get("daily") or {}
    if len(daily) > MAX_DAILY_ROWS:
        keep = sorted(daily)[-MAX_DAILY_ROWS:]
        state["daily"] = {k: daily[k] for k in keep}


def _utc_day(ts: float) -> str:
    return datetime.fromtimestamp(ts, tz=timezone.utc).strftime("%Y-%m-%d")


# ── rate refresh ─────────────────────────────────────────────────────────


def refresh_rate(
    state: dict, *, now: float, fetch_json: FetchJson,
    max_apy_pct: float = DEFAULT_MAX_APY_PCT,
) -> None:
    """Refresh state["rate"] when due; keep a recent reading if a refresh fails."""
    rate = state.setdefault("rate", {})
    fetched = rate.get("fetched_ts")
    if fetched is not None and now - float(fetched) < RATE_REFRESH_SEC and rate.get("apy_pct") is not None:
        return
    try:
        apy, as_of, reason = parse_reference_chart(
            fetch_json(REFERENCE_URL), now=now, max_apy_pct=max_apy_pct)
    except Exception as e:  # network/egress/JSON errors all fail closed
        apy, as_of, reason = None, None, f"unavailable: {type(e).__name__}"
    if apy is not None:
        state["rate"] = {"apy_pct": apy, "as_of_ts": as_of, "fetched_ts": now, "status": "ok"}
        return
    prev_as_of = rate.get("as_of_ts")
    if rate.get("apy_pct") is not None and prev_as_of is not None and \
            now - float(prev_as_of) <= RATE_MAX_AGE_SEC:
        rate["status"] = f"ok (cached; refresh failed: {reason})"
        return
    state["rate"] = {"apy_pct": None, "as_of_ts": as_of, "fetched_ts": fetched,
                     "status": reason}


# ── tick ─────────────────────────────────────────────────────────────────


def tick(
    state: dict, *, idle_usd: float, now: float, fetch_json: FetchJson,
    max_apy_pct: float = DEFAULT_MAX_APY_PCT,
) -> dict:
    """Accrue interest on ``idle_usd`` for the slice since the previous tick."""
    refresh_rate(state, now=now, fetch_json=fetch_json, max_apy_pct=max_apy_pct)
    apy = state["rate"].get("apy_pct")
    last = state.get("last_accrual_ts")
    earned = 0.0
    credited = 0.0
    if last is not None and now > float(last):
        elapsed = now - float(last)
        credited = min(elapsed, MAX_GAP_SEC) if apy is not None else 0.0
        earned = interest_for(idle_usd, apy, credited) if apy is not None else 0.0
        if earned <= 0.0:
            credited = 0.0
        state["skipped_seconds"] = float(state.get("skipped_seconds") or 0.0) + (elapsed - credited)
    state["last_accrual_ts"] = now
    state["last_idle_usd"] = idle_usd if isinstance(idle_usd, (int, float)) and math.isfinite(idle_usd) else None
    if earned > 0.0:
        state["total_earned_usd"] = float(state.get("total_earned_usd") or 0.0) + earned
        day = state.setdefault("daily", {}).setdefault(_utc_day(now), {"earned_usd": 0.0})
        day["earned_usd"] = float(day.get("earned_usd") or 0.0) + earned
        day["apy_pct"] = apy
        day["idle_usd"] = idle_usd
        _trim_daily(state)
    return {"earned_usd": earned, "credited_seconds": credited, "apy_pct": apy,
            "rate_status": state["rate"].get("status")}


def default_fetch_json(url: str, timeout: float = 10.0) -> Any:
    import requests

    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    return resp.json()


def _env_enabled() -> bool:
    return os.getenv("IDLE_YIELD_ENABLED", "true").strip().lower() not in {"0", "false", "no", "off"}


def _env_max_apy() -> float:
    try:
        value = float(os.getenv("IDLE_YIELD_MAX_APY_PCT", str(DEFAULT_MAX_APY_PCT)))
    except ValueError:
        return DEFAULT_MAX_APY_PCT
    return value if math.isfinite(value) and value > 0 else DEFAULT_MAX_APY_PCT


def run_idle_yield_tick(
    idle_usd: float,
    *,
    paper: bool,
    enabled: Optional[bool] = None,
    now: Optional[float] = None,
    path: Path | str = DEFAULT_PATH,
    fetch_json: Optional[FetchJson] = None,
    max_apy_pct: Optional[float] = None,
) -> dict:
    """One accrual pass. PAPER-latched; never raises into the caller's loop."""
    if not paper:
        return {"skipped": "not_paper"}
    if not (_env_enabled() if enabled is None else enabled):
        return {"skipped": "disabled"}
    now = datetime.now(timezone.utc).timestamp() if now is None else float(now)
    state = load_state(path)
    out = tick(
        state, idle_usd=idle_usd, now=now,
        fetch_json=fetch_json or default_fetch_json,
        max_apy_pct=_env_max_apy() if max_apy_pct is None else max_apy_pct,
    )
    save_state(state, path)
    return out


def summarize(state: dict, *, now: float) -> dict:
    """Plain numbers for reports: today, trailing 7 UTC days, all-time, current rate."""
    daily = state.get("daily") or {}
    today = datetime.fromtimestamp(now, tz=timezone.utc).date()
    week = {(today - timedelta(days=k)).strftime("%Y-%m-%d") for k in range(7)}
    rate = state.get("rate") or {}
    return {
        "today_usd": float((daily.get(today.strftime("%Y-%m-%d")) or {}).get("earned_usd") or 0.0),
        "last_7d_usd": sum(float((v or {}).get("earned_usd") or 0.0)
                           for k, v in daily.items() if k in week),
        "total_earned_usd": float(state.get("total_earned_usd") or 0.0),
        "apy_pct": rate.get("apy_pct"),
        "rate_status": rate.get("status"),
        "reference": REFERENCE_DESC,
    }
