"""Conservatively repair known pytest pollution in runtime state files.

The command is a dry run unless ``--apply`` is supplied.  It recognizes only
the exact fixture signatures documented below, stages every changed file,
backs up the original bytes, and commits each rewrite with ``os.replace``.
Malformed input, a concurrently changed target, or an incomplete MCP fixture
cluster makes the plan ambiguous and prevents every write.

Risk-state reconstruction is separately opt-in.  It is available only when the
effective runtime is PAPER + dry-run and ``positions.json`` has zero open
positions; the calculation is entirely offline and uses the current UTC day's
paper-futures positions plus the virtual wallet.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime, time, timezone
from pathlib import Path
from typing import Callable, Iterable


PREDICTION_PROMPT_HASH = "0d4d63fbc6965522"

SPOT_FIXTURE = {
    "binance": {
        "BTC": {
            "coin": "BTC",
            "exchange": "binance",
            "balance": 0.1,
            "avg_cost": 50000.0,
            "current_price": 48000.0,
            "unrealized_pnl": -200.0,
            "unrealized_pnl_pct": -0.04,
            "peak_price": 52000.0,
            "last_updated": 0.0,
        }
    }
}

S1_STATES_FIXTURE = {
    "BTC": {
        "state": "above",
        "streak": 0,
        "streak_dir": None,
        "last_bar_ts": 249,
    },
    "ETH": {
        "state": "above",
        "streak": 0,
        "streak_dir": None,
        "last_bar_ts": 249,
    },
}

# These are the complete, already-identified synthetic rows in the current UTC
# journal.  Timestamps are part of the predicate so a superficially similar
# legitimate row is retained.
EXACT_JOURNAL_ROWS = frozenset(
    {
        "- 15:26:52Z **CLOSE** buy AAVE/USDT:USDT reason=sl_placement_failed pnl=$-0.01 strat=systematic_v3_1",
        "- 15:27:44Z **CLOSE** buy ETH/USDT reason=take_profit pnl=$+0.00 strat=claude_portfolio",
        "- 15:28:39Z **CLOSE** buy AAVE/USDT:USDT reason=sl_placement_failed pnl=$-0.01 strat=test",
        "- 15:31:52Z **CLOSE** buy BTC/USDT reason=take_profit pnl=$+1.31 strat=systematic_v3_1",
        "- 15:33:13Z **CLOSE** buy AAVE/USDT:USDT reason=sl_placement_failed pnl=$-0.01 strat=systematic_v3_1",
        "- 15:33:54Z **CLOSE** buy AAVE/USDT:USDT reason=sl_placement_failed pnl=$-0.01 strat=systematic_v3_1",
        "- 15:34:42Z **CLOSE** buy ETH/USDT reason=take_profit pnl=$+0.00 strat=claude_portfolio",
        "- 15:35:37Z **CLOSE** buy AAVE/USDT:USDT reason=sl_placement_failed pnl=$-0.01 strat=test",
        "- 15:38:47Z **CLOSE** buy BTC/USDT reason=take_profit pnl=$+1.31 strat=systematic_v3_1",
        "- 16:33:07Z **CLOSE** buy BTC/USDT reason=take_profit pnl=$+1.31 strat=systematic_v3_1",
    }
)


@dataclass(frozen=True)
class RepairPaths:
    close_fail: Path = Path("data/close_fail_count.json")
    spot: Path = Path("data/spot_portfolio.json")
    s1: Path = Path("data/s1_regime_state.json")
    post_mortem: Path = Path("data/post_mortem.json")
    prediction_log: Path = Path("data/prediction_agent_log.jsonl")
    mcp_decisions: Path = Path("data/mcp_decisions.jsonl")
    journal: Path = Path("journal/2026-07-18.md")
    risk_state: Path = Path("data/risk_state.json")
    positions: Path = Path("data/positions.json")
    wallet: Path = Path("data/virtual_wallet.json")
    backup_root: Path = Path("data/backups")


@dataclass(frozen=True)
class Change:
    label: str
    path: Path
    before: bytes
    after: bytes
    removed: int
    description: str


@dataclass
class RepairPlan:
    changes: list[Change] = field(default_factory=list)
    findings: dict[str, int] = field(default_factory=dict)
    ambiguities: list[str] = field(default_factory=list)
    applied: bool = False
    backup_dir: Path | None = None

    def add_change(self, change: Change) -> None:
        if change.before != change.after:
            self.changes.append(change)
        self.findings[change.label] = change.removed

    def as_dict(self) -> dict:
        return {
            "mode": "applied" if self.applied else "dry-run",
            "safe_to_apply": not self.ambiguities,
            "changed_files": [str(change.path) for change in self.changes],
            "findings": dict(sorted(self.findings.items())),
            "ambiguities": list(self.ambiguities),
            "backup_dir": str(self.backup_dir) if self.backup_dir else None,
        }


class RepairError(RuntimeError):
    """Base error for a repair that was not committed."""


def _finite_number(value: object) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _same_json_type(left: object, right: object) -> bool:
    """Deep equality that does not treat JSON ``1`` and ``1.0`` as equal."""
    if type(left) is not type(right):
        return False
    if isinstance(left, dict):
        return left.keys() == right.keys() and all(
            _same_json_type(left[key], right[key]) for key in left
        )
    if isinstance(left, list):
        return len(left) == len(right) and all(
            _same_json_type(a, b) for a, b in zip(left, right)
        )
    return left == right


def _decode(path: Path, plan: RepairPlan) -> tuple[bytes, str] | None:
    if not path.exists():
        return None
    if not path.is_file():
        plan.ambiguities.append(f"{path}: target is not a regular file")
        return None
    try:
        raw = path.read_bytes()
        return raw, raw.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        plan.ambiguities.append(f"{path}: unreadable UTF-8 input ({exc})")
        return None


def _json(path: Path, plan: RepairPlan) -> tuple[bytes, str, object] | None:
    decoded = _decode(path, plan)
    if decoded is None:
        return None
    raw, text = decoded
    try:
        return raw, text, json.loads(text)
    except json.JSONDecodeError as exc:
        plan.ambiguities.append(f"{path}: invalid JSON ({exc})")
        return None


def _json_bytes(value: object, original: str) -> bytes:
    # Preserve compact-vs-pretty style and final-newline convention.
    if "\n" in original or "\r" in original:
        rendered = json.dumps(value, indent=2, ensure_ascii=False)
    else:
        rendered = json.dumps(value, ensure_ascii=False)
    if original.endswith(("\n", "\r")):
        rendered += "\n"
    return rendered.encode("utf-8")


def _plan_close_fail(paths: RepairPaths, plan: RepairPlan) -> None:
    loaded = _json(paths.close_fail, plan)
    if loaded is None:
        plan.findings.setdefault("close_fail_a1", 0)
        return
    raw, text, payload = loaded
    if not isinstance(payload, dict):
        plan.ambiguities.append(f"{paths.close_fail}: expected a JSON object")
        return
    if "a1" not in payload or type(payload["a1"]) is not int or payload["a1"] != 1:
        plan.findings["close_fail_a1"] = 0
        return
    repaired = dict(payload)
    del repaired["a1"]
    plan.add_change(
        Change(
            "close_fail_a1",
            paths.close_fail,
            raw,
            _json_bytes(repaired, text),
            1,
            "remove exact pytest position-id counter a1=1",
        )
    )


def _plan_spot(paths: RepairPaths, plan: RepairPlan) -> None:
    loaded = _json(paths.spot, plan)
    if loaded is None:
        plan.findings.setdefault("spot_fixture_reset", 0)
        return
    raw, text, payload = loaded
    if not _same_json_type(payload, SPOT_FIXTURE):
        plan.findings["spot_fixture_reset"] = 0
        return
    plan.add_change(
        Change(
            "spot_fixture_reset",
            paths.spot,
            raw,
            _json_bytes({}, text),
            1,
            "reset exact SpotPortfolioManager fail-hold fixture",
        )
    )


def _is_s1_fixture(payload: object) -> bool:
    if not isinstance(payload, dict) or set(payload) != {
        "ts",
        "regime",
        "version",
        "states",
    }:
        return False
    return (
        _finite_number(payload["ts"])
        and payload["regime"] == "NORMAL"
        and type(payload["version"]) is int
        and payload["version"] == 2
        and _same_json_type(payload["states"], S1_STATES_FIXTURE)
    )


def _plan_s1(paths: RepairPaths, plan: RepairPlan) -> None:
    loaded = _json(paths.s1, plan)
    if loaded is None:
        plan.findings.setdefault("s1_fixture_reset", 0)
        return
    raw, text, payload = loaded
    if not _is_s1_fixture(payload):
        plan.findings["s1_fixture_reset"] = 0
        return
    plan.add_change(
        Change(
            "s1_fixture_reset",
            paths.s1,
            raw,
            _json_bytes({}, text),
            1,
            "reset exact S1 250-bar NORMAL-state fixture",
        )
    )


def _is_post_mortem_fixture(row: object) -> bool:
    if not isinstance(row, dict):
        return False
    exact = {
        "symbol": "ETH/USDT",
        "exchange": "Binance",
        "side": "buy",
        "market_type": "futures",
        "strategy": "claude_portfolio",
        "entry_price": 100.0,
        "close_price": 100.0,
        "pnl": 0,
        "pnl_pct": None,
        "close_reason": "take_profit",
        "leverage": 3,
        "duration_min": 0,
        "verdict": "LOSS",
        "mistakes": [],
        "lessons": ["Good entry on claude_portfolio"],
    }
    return _finite_number(row.get("timestamp")) and all(
        key in row and _same_json_type(row[key], value)
        for key, value in exact.items()
    )


def _plan_post_mortem(paths: RepairPaths, plan: RepairPlan) -> None:
    loaded = _json(paths.post_mortem, plan)
    if loaded is None:
        plan.findings.setdefault("post_mortem_fixture_rows", 0)
        return
    raw, text, payload = loaded
    if not isinstance(payload, dict) or not isinstance(payload.get("analyses"), list):
        plan.ambiguities.append(
            f"{paths.post_mortem}: expected object with an analyses list"
        )
        return
    analyses = payload["analyses"]
    repaired_rows = [row for row in analyses if not _is_post_mortem_fixture(row)]
    removed = len(analyses) - len(repaired_rows)
    if not removed:
        plan.findings["post_mortem_fixture_rows"] = 0
        return
    repaired = dict(payload)
    repaired["analyses"] = repaired_rows
    plan.add_change(
        Change(
            "post_mortem_fixture_rows",
            paths.post_mortem,
            raw,
            _json_bytes(repaired, text),
            removed,
            "remove exact ETH entry=exit=100 zero-PnL postmortem fixtures",
        )
    )


def _parse_jsonl(
    path: Path, plan: RepairPlan
) -> tuple[bytes, list[bytes], list[object | None]] | None:
    decoded = _decode(path, plan)
    if decoded is None:
        return None
    raw, _ = decoded
    lines = raw.splitlines(keepends=True)
    records: list[object | None] = []
    for number, line in enumerate(lines, 1):
        body = line.rstrip(b"\r\n")
        if not body.strip():
            records.append(None)
            continue
        try:
            records.append(json.loads(body.decode("utf-8")))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            plan.ambiguities.append(f"{path}:{number}: invalid JSONL ({exc})")
            return None
    return raw, lines, records


def _is_prediction_fixture(record: object) -> bool:
    if not isinstance(record, dict):
        return False
    signature = {
        "symbol": "ATOM/USDT",
        "side": "buy",
        "mcp_score": 72,
        "mode": "VETO",
        "prediction": "CONFIRM",
        "confidence": 0.8,
        "reasoning": "Looks good",
        "model_used": "haiku",
        "prompt_hash": PREDICTION_PROMPT_HASH,
    }
    return all(
        key in record and _same_json_type(record[key], value)
        for key, value in signature.items()
    )


def _plan_prediction_log(paths: RepairPaths, plan: RepairPlan) -> None:
    parsed = _parse_jsonl(paths.prediction_log, plan)
    if parsed is None:
        plan.findings.setdefault("prediction_mock_rows", 0)
        return
    raw, lines, records = parsed
    remove = {
        index for index, record in enumerate(records) if _is_prediction_fixture(record)
    }
    plan.findings["prediction_mock_rows"] = len(remove)
    if not remove:
        return
    after = b"".join(line for index, line in enumerate(lines) if index not in remove)
    plan.add_change(
        Change(
            "prediction_mock_rows",
            paths.prediction_log,
            raw,
            after,
            len(remove),
            "remove exact ATOM/72 CONFIRM mock-prediction rows",
        )
    )


def _parse_iso_timestamp(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    seconds = parsed.timestamp()
    return seconds if math.isfinite(seconds) else None


def _is_empty_portfolio(record: object) -> bool:
    return (
        isinstance(record, dict)
        and set(record) == {"ts", "type", "decisions"}
        and record.get("type") == "portfolio"
        and record.get("decisions") == {"actions": []}
        and _parse_iso_timestamp(record.get("ts")) is not None
    )


def _valid_uuid(value: object) -> bool:
    if not isinstance(value, str):
        return False
    try:
        return str(uuid.UUID(value)) == value.lower()
    except ValueError:
        return False


def _is_mcp_fake(record: object) -> bool:
    if (
        not isinstance(record, dict)
        or set(record) != {"ts", "type", "decisions"}
        or record.get("type") != "portfolio"
        or _parse_iso_timestamp(record.get("ts")) is None
    ):
        return False
    decisions = record.get("decisions")
    if not isinstance(decisions, dict) or set(decisions) != {"actions"}:
        return False
    actions = decisions["actions"]
    if not isinstance(actions, list) or len(actions) != 1:
        return False
    action = actions[0]
    if not isinstance(action, dict) or set(action) != {
        "type",
        "symbol",
        "side",
        "confidence",
        "exchange",
        "decision_id",
        "source",
    }:
        return False
    signature = {
        "type": "OPEN",
        "symbol": "BTC/USDT:USDT",
        "side": "buy",
        "confidence": 0.7,
        "exchange": "binance",
        "source": "algo_det",
    }
    return all(
        _same_json_type(action.get(key), value) for key, value in signature.items()
    ) and _valid_uuid(action.get("decision_id"))


def _within_point_one(first: object, second: object) -> bool:
    if not isinstance(first, dict) or not isinstance(second, dict):
        return False
    a = _parse_iso_timestamp(first.get("ts"))
    b = _parse_iso_timestamp(second.get("ts"))
    return a is not None and b is not None and abs(a - b) <= 0.1


def _plan_mcp_log(paths: RepairPaths, plan: RepairPlan) -> None:
    parsed = _parse_jsonl(paths.mcp_decisions, plan)
    if parsed is None:
        plan.findings.setdefault("mcp_fake_clusters", 0)
        return
    raw, lines, records = parsed
    fake_indices = [
        index for index, record in enumerate(records) if _is_mcp_fake(record)
    ]
    remove: set[int] = set()
    clusters = 0
    for index in fake_indices:
        exact_pair = (
            index >= 2
            and _is_empty_portfolio(records[index - 2])
            and _is_empty_portfolio(records[index - 1])
            and _within_point_one(records[index], records[index - 2])
            and _within_point_one(records[index], records[index - 1])
        )
        has_third = (
            index >= 3
            and _is_empty_portfolio(records[index - 3])
            and _within_point_one(records[index], records[index - 3])
        )
        if not exact_pair or has_third:
            plan.ambiguities.append(
                f"{paths.mcp_decisions}:{index + 1}: exact fake OPEN lacks "
                "exactly two immediate empty portfolio predecessors within 0.1s"
            )
            continue
        cluster = {index - 2, index - 1, index}
        if remove.intersection(cluster):
            plan.ambiguities.append(
                f"{paths.mcp_decisions}:{index + 1}: overlapping fake clusters"
            )
            continue
        remove.update(cluster)
        clusters += 1
    plan.findings["mcp_fake_clusters"] = clusters
    if not remove or plan.ambiguities:
        return
    after = b"".join(line for index, line in enumerate(lines) if index not in remove)
    plan.add_change(
        Change(
            "mcp_fake_clusters",
            paths.mcp_decisions,
            raw,
            after,
            clusters,
            "remove fake BTC OPEN and its two immediate empty portfolio rows",
        )
    )


def _plan_journal(paths: RepairPaths, plan: RepairPlan) -> None:
    decoded = _decode(paths.journal, plan)
    if decoded is None:
        plan.findings.setdefault("journal_fixture_rows", 0)
        return
    raw, _ = decoded
    lines = raw.splitlines(keepends=True)
    remove: set[int] = set()
    for index, line in enumerate(lines):
        try:
            body = line.rstrip(b"\r\n").decode("utf-8")
        except UnicodeDecodeError as exc:
            plan.ambiguities.append(f"{paths.journal}:{index + 1}: {exc}")
            return
        if body in EXACT_JOURNAL_ROWS:
            remove.add(index)
    plan.findings["journal_fixture_rows"] = len(remove)
    if not remove:
        return
    after = b"".join(line for index, line in enumerate(lines) if index not in remove)
    plan.add_change(
        Change(
            "journal_fixture_rows",
            paths.journal,
            raw,
            after,
            len(remove),
            "remove ten exact current-day journal fixture rows",
        )
    )


def _utc_bounds(now: datetime) -> tuple[float, float, str]:
    current = now.astimezone(timezone.utc)
    day = current.date()
    start = datetime.combine(day, time.min, tzinfo=timezone.utc).timestamp()
    return start, start + 86400.0, day.isoformat()


def _plan_risk_reconstruction(
    paths: RepairPaths,
    plan: RepairPlan,
    *,
    operating_mode: str | None,
    dry_run: bool | None,
    now: datetime,
) -> None:
    label = "risk_state_reconstruction"
    plan.findings.setdefault(label, 0)
    if operating_mode != "PAPER" or dry_run is not True:
        plan.ambiguities.append(
            "risk reconstruction requires effective OPERATING_MODE=PAPER and DRY_RUN=true"
        )
        return

    risk_loaded = _json(paths.risk_state, plan)
    positions_loaded = _json(paths.positions, plan)
    wallet_loaded = _json(paths.wallet, plan)
    if risk_loaded is None or positions_loaded is None or wallet_loaded is None:
        plan.ambiguities.append("risk reconstruction inputs are incomplete")
        return
    risk_raw, risk_text, state = risk_loaded
    _, _, positions = positions_loaded
    _, _, wallet = wallet_loaded

    if not isinstance(state, dict):
        plan.ambiguities.append(f"{paths.risk_state}: expected a JSON object")
        return
    preserved_types = {
        "is_halted": bool,
        "halt_reason": str,
        "symbol_pauses": dict,
        "family_pauses": dict,
    }
    for key, expected in preserved_types.items():
        if key not in state or type(state[key]) is not expected:
            plan.ambiguities.append(
                f"{paths.risk_state}: cannot safely preserve malformed {key}"
            )
            return

    if (
        not isinstance(positions, dict)
        or set(positions) != {"open", "closed"}
        or not isinstance(positions.get("open"), list)
        or not isinstance(positions.get("closed"), list)
    ):
        plan.ambiguities.append(
            f"{paths.positions}: expected exact open/closed position ledger"
        )
        return
    if positions["open"] != []:
        plan.ambiguities.append(
            f"{paths.positions}: risk reconstruction requires zero open positions"
        )
        return

    if (
        not isinstance(wallet, dict)
        or not isinstance(wallet.get("balances"), dict)
        or not wallet["balances"]
    ):
        plan.ambiguities.append(f"{paths.wallet}: missing wallet balances")
        return
    balances = list(wallet["balances"].values())
    if not all(_finite_number(value) and float(value) >= 0.0 for value in balances):
        plan.ambiguities.append(f"{paths.wallet}: invalid wallet balance")
        return
    current_balance = sum(float(value) for value in balances)
    if not math.isfinite(current_balance) or current_balance <= 0.0:
        plan.ambiguities.append(f"{paths.wallet}: non-positive total balance")
        return

    day_start, day_end, trading_day = _utc_bounds(now)
    closed_today: list[dict] = []
    opens_today = 0
    for index, row in enumerate(positions["closed"]):
        if not isinstance(row, dict):
            plan.ambiguities.append(
                f"{paths.positions}: closed row {index} is not an object"
            )
            return
        is_paper_future = (
            row.get("paper_trade") is True and row.get("market_type") == "futures"
        )
        if not is_paper_future:
            continue
        open_ts = row.get("open_time")
        close_ts = row.get("close_time")
        if not _finite_number(open_ts) or not _finite_number(close_ts):
            plan.ambiguities.append(
                f"{paths.positions}: paper-futures row {index} has invalid timestamps"
            )
            return
        if day_start <= float(open_ts) < day_end:
            opens_today += 1
        if day_start <= float(close_ts) < day_end:
            if not _finite_number(row.get("pnl")):
                plan.ambiguities.append(
                    f"{paths.positions}: current-day row {index} has invalid pnl"
                )
                return
            closed_today.append(row)

    daily_pnl = sum(float(row["pnl"]) for row in closed_today)
    start_balance = current_balance - daily_pnl
    if not math.isfinite(start_balance) or start_balance <= 0.0:
        plan.ambiguities.append("reconstructed start balance is not positive")
        return

    running = start_balance
    peak_balance = start_balance
    for row in sorted(closed_today, key=lambda item: float(item["close_time"])):
        running += float(row["pnl"])
        peak_balance = max(peak_balance, running)
    # The wallet is the final equity authority; require the ledger identity to
    # hold before proposing any write.
    if not math.isclose(running, current_balance, rel_tol=0.0, abs_tol=1e-7):
        plan.ambiguities.append("position PnL and wallet balance do not reconcile")
        return

    max_drawdown = max(0.0, (peak_balance - current_balance) / peak_balance)
    repaired = dict(state)
    repaired.update(
        {
            "daily_pnl": round(daily_pnl, 4),
            "max_drawdown_pct": round(max_drawdown, 4),
            "start_balance": round(start_balance, 2),
            "peak_balance": round(peak_balance, 2),
            "trading_day": trading_day,
            "trades_today": opens_today,
            "timestamp": now.astimezone(timezone.utc).timestamp(),
        }
    )
    after = _json_bytes(repaired, risk_text)
    if after == risk_raw:
        return
    plan.add_change(
        Change(
            label,
            paths.risk_state,
            risk_raw,
            after,
            1,
            "rebuild daily PAPER risk accounting while preserving latches and pauses",
        )
    )


def build_plan(
    paths: RepairPaths,
    *,
    reconstruct_risk: bool = False,
    operating_mode: str | None = None,
    dry_run: bool | None = None,
    now: datetime | None = None,
) -> RepairPlan:
    """Build a read-only repair plan for ``paths``."""
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    plan = RepairPlan()
    _plan_close_fail(paths, plan)
    _plan_spot(paths, plan)
    _plan_s1(paths, plan)
    _plan_post_mortem(paths, plan)
    _plan_prediction_log(paths, plan)
    _plan_mcp_log(paths, plan)
    _plan_journal(paths, plan)
    if reconstruct_risk:
        _plan_risk_reconstruction(
            paths,
            plan,
            operating_mode=operating_mode,
            dry_run=dry_run,
            now=current,
        )
    return plan


def _sha256(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def _stage(path: Path, payload: bytes) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.repair-", dir=path.parent)
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(payload)
            handle.flush()
            os.fsync(handle.fileno())
        if path.exists():
            shutil.copymode(path, staged)
        return staged
    except Exception:
        staged.unlink(missing_ok=True)
        raise


def _atomic_write(path: Path, payload: bytes) -> None:
    staged = _stage(path, payload)
    try:
        os.replace(staged, path)
    finally:
        staged.unlink(missing_ok=True)


def _backup_name(index: int, change: Change) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", change.path.name)
    return f"{index:02d}_{change.label}_{safe}"


def apply_plan(
    plan: RepairPlan,
    backup_root: Path,
    *,
    now: datetime | None = None,
    replace_func: Callable[[os.PathLike, os.PathLike], None] = os.replace,
) -> Path | None:
    """Transactionally apply a previously built, unambiguous plan."""
    if plan.ambiguities:
        raise RepairError("ambiguous repair plan; no files were changed")
    if not plan.changes:
        return None
    current = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    stamp = current.strftime("%Y%m%dT%H%M%S%fZ")
    backup_dir = Path(backup_root) / f"test_pollution_repair_{stamp}"
    try:
        backup_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise RepairError(f"backup directory already exists: {backup_dir}") from exc

    manifest_files = []
    for index, change in enumerate(plan.changes):
        backup_name = _backup_name(index, change)
        backup_path = backup_dir / backup_name
        with backup_path.open("xb") as handle:
            handle.write(change.before)
            handle.flush()
            os.fsync(handle.fileno())
        manifest_files.append(
            {
                "label": change.label,
                "target": str(change.path.resolve()),
                "backup": backup_name,
                "before_sha256": _sha256(change.before),
                "after_sha256": _sha256(change.after),
                "removed": change.removed,
                "description": change.description,
            }
        )

    manifest_path = backup_dir / "manifest.json"
    manifest = {
        "created_at": current.isoformat(),
        "status": "prepared",
        "files": manifest_files,
    }
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )

    staged: dict[Path, Path] = {}
    committed: list[Change] = []
    try:
        for change in plan.changes:
            staged[change.path] = _stage(change.path, change.after)

        # Optimistic concurrency guard: all targets must still be byte-for-byte
        # identical to the snapshot used to build the plan.
        for change in plan.changes:
            if not change.path.exists() or change.path.read_bytes() != change.before:
                raise RepairError(f"concurrent target change detected: {change.path}")

        for change in plan.changes:
            replace_func(staged[change.path], change.path)
            committed.append(change)
        for change in plan.changes:
            if change.path.read_bytes() != change.after:
                raise RepairError(f"post-commit verification failed: {change.path}")
    except Exception as exc:
        rollback_errors = []
        for change in reversed(committed):
            try:
                _atomic_write(change.path, change.before)
            except Exception as rollback_exc:  # pragma: no cover - catastrophic I/O
                rollback_errors.append(f"{change.path}: {rollback_exc}")
        manifest["status"] = "rollback_failed" if rollback_errors else "rolled_back"
        manifest["error"] = str(exc)
        manifest["rollback_errors"] = rollback_errors
        _atomic_write(
            manifest_path,
            (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
        )
        if rollback_errors:
            raise RepairError(
                f"repair failed and rollback was incomplete: {rollback_errors}"
            ) from exc
        raise RepairError(f"repair failed; committed targets were rolled back: {exc}") from exc
    finally:
        for path in staged.values():
            path.unlink(missing_ok=True)

    manifest["status"] = "committed"
    _atomic_write(
        manifest_path,
        (json.dumps(manifest, indent=2) + "\n").encode("utf-8"),
    )
    plan.applied = True
    plan.backup_dir = backup_dir
    return backup_dir


def repair(
    paths: RepairPaths,
    *,
    apply: bool = False,
    reconstruct_risk: bool = False,
    operating_mode: str | None = None,
    dry_run: bool | None = None,
    now: datetime | None = None,
) -> RepairPlan:
    """Plan repairs and optionally apply them.  Default: read-only dry run."""
    plan = build_plan(
        paths,
        reconstruct_risk=reconstruct_risk,
        operating_mode=operating_mode,
        dry_run=dry_run,
        now=now,
    )
    if apply:
        apply_plan(plan, paths.backup_root, now=now)
    return plan


def _path(value: str) -> Path:
    return Path(value)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true", help="commit the planned repairs")
    parser.add_argument(
        "--reconstruct-risk-state",
        action="store_true",
        help="also propose the separately gated offline PAPER risk rebuild",
    )
    defaults = RepairPaths()
    for option, attribute in (
        ("close-fail-path", "close_fail"),
        ("spot-path", "spot"),
        ("s1-path", "s1"),
        ("post-mortem-path", "post_mortem"),
        ("prediction-log-path", "prediction_log"),
        ("mcp-decisions-path", "mcp_decisions"),
        ("journal-path", "journal"),
        ("risk-state-path", "risk_state"),
        ("positions-path", "positions"),
        ("wallet-path", "wallet"),
        ("backup-root", "backup_root"),
    ):
        parser.add_argument(
            f"--{option}",
            type=_path,
            default=getattr(defaults, attribute),
            dest=attribute,
        )
    return parser


def main(argv: Iterable[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    paths = RepairPaths(
        close_fail=args.close_fail,
        spot=args.spot,
        s1=args.s1,
        post_mortem=args.post_mortem,
        prediction_log=args.prediction_log,
        mcp_decisions=args.mcp_decisions,
        journal=args.journal,
        risk_state=args.risk_state,
        positions=args.positions,
        wallet=args.wallet,
        backup_root=args.backup_root,
    )
    operating_mode = None
    effective_dry_run = None
    if args.reconstruct_risk_state:
        # Importing config is local/offline; it reads the effective environment
        # but performs no exchange or network calls.
        import config

        operating_mode = config.OPERATING_MODE
        effective_dry_run = config.DRY_RUN
    try:
        plan = repair(
            paths,
            apply=args.apply,
            reconstruct_risk=args.reconstruct_risk_state,
            operating_mode=operating_mode,
            dry_run=effective_dry_run,
        )
    except RepairError as exc:
        print(json.dumps({"mode": "aborted", "error": str(exc)}, indent=2))
        return 2
    print(json.dumps(plan.as_dict(), indent=2))
    return 2 if plan.ambiguities else 0


if __name__ == "__main__":
    raise SystemExit(main())
