"""Fail-closed operational readiness reporting.

The states in this module are deliberately independent. Operational health is
not evidence of strategy edge, paper authorization does not claim
profitability, and live authorization is reported separately from both.
"""

from __future__ import annotations

import importlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Collection, Mapping

from core.entry_policy import (
    EntryPolicy,
    LiveApproval,
    evaluate_entry,
    load_live_approval,
    parse_allowlist,
)
from core.entry_policy import (
    current_git_sha as resolve_current_git_sha,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_EVIDENCE_REGISTRY_PATH = REPO_ROOT / "data" / "active_strategies.json"

_FULL_GIT_SHA = re.compile(r"[0-9a-fA-F]{40}\Z")
_VALID_OPERATING_MODES = frozenset({"OBSERVATION", "PAPER", "CONTROLLED_LIVE"})
_NON_HUMAN_APPROVERS = frozenset(
    {"", "auto", "automated", "automation", "bot", "machine", "system"}
)


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    """Four orthogonal readiness states plus their decision inputs."""

    system_healthy: bool
    paper_ready: bool
    edge_unproven: bool
    live_blocked: bool
    operating_mode: str
    entry_policy: str
    strategy_allowlist: frozenset[str]
    approved_paper_strategies: frozenset[str]
    approved_live_strategies: frozenset[str]
    approved_evidence: frozenset[str]
    config_valid: bool
    reasons: Mapping[str, str]

    def to_dict(self) -> dict[str, Any]:
        """Return a stable JSON-serializable payload with exact state names."""
        states = {
            "SYSTEM_HEALTHY": self.system_healthy,
            "PAPER_READY": self.paper_ready,
            "EDGE_UNPROVEN": self.edge_unproven,
            "LIVE_BLOCKED": self.live_blocked,
        }
        return {
            **states,
            "states": states,
            "state_reasons": dict(self.reasons),
            "operating_mode": self.operating_mode,
            "entry_policy": self.entry_policy,
            "strategy_allowlist": sorted(self.strategy_allowlist),
            "approved_paper_strategies": sorted(self.approved_paper_strategies),
            "approved_live_strategies": sorted(self.approved_live_strategies),
            "approved_evidence": sorted(self.approved_evidence),
            "config_valid": self.config_valid,
            "health_scope": "operational_only",
            "profitability_claimed": False,
        }


@dataclass(frozen=True, slots=True)
class _RuntimeConfig:
    valid: bool
    operating_mode: str
    entry_policy: EntryPolicy | None
    approved_paper: frozenset[str]
    approved_live: frozenset[str]
    live_latch_enabled: bool
    live_approval_path: Path | None


def _config_value(source: object, name: str, default: Any = None) -> Any:
    if isinstance(source, Mapping):
        return source.get(name, default)
    return getattr(source, name, default)


def _safe_allowlist(raw: object) -> tuple[frozenset[str], bool]:
    """Use the shared parser while rejecting ambiguous container types."""
    if raw is None:
        return frozenset(), True
    if isinstance(raw, str):
        values: str | Collection[str] = raw
    elif isinstance(raw, (list, tuple, set, frozenset)) and all(
        isinstance(value, str) for value in raw
    ):
        values = raw
    else:
        return frozenset(), False
    try:
        return parse_allowlist(values), True
    except (TypeError, ValueError):
        return frozenset(), False


def _load_runtime_config(config_module: object | None) -> _RuntimeConfig:
    try:
        source = importlib.import_module("config") if config_module is None else config_module
    except Exception:
        return _RuntimeConfig(
            False,
            "INVALID",
            None,
            frozenset(),
            frozenset(),
            False,
            None,
        )

    raw_policy = str(_config_value(source, "ENTRY_POLICY", "") or "").strip().upper()
    try:
        policy = EntryPolicy(raw_policy)
    except ValueError:
        policy = None

    operating_mode = str(_config_value(source, "OPERATING_MODE", "") or "").strip().upper()
    paper, paper_valid = _safe_allowlist(_config_value(source, "APPROVED_PAPER_STRATEGIES", None))
    live, live_valid = _safe_allowlist(_config_value(source, "APPROVED_LIVE_STRATEGIES", None))
    raw_latch = _config_value(source, "CONTROLLED_LIVE_ENABLED", False)
    latch_valid = isinstance(raw_latch, bool)
    latch_enabled = raw_latch is True

    raw_approval_path = _config_value(source, "LIVE_ENTRY_APPROVAL_PATH", None)
    if isinstance(raw_approval_path, (str, Path)) and str(raw_approval_path).strip():
        approval_path = Path(raw_approval_path)
        path_valid = True
    else:
        approval_path = None
        path_valid = policy is not EntryPolicy.CONTROLLED_LIVE

    valid = bool(
        policy is not None
        and operating_mode in _VALID_OPERATING_MODES
        and paper_valid
        and live_valid
        and latch_valid
        and path_valid
    )
    return _RuntimeConfig(
        valid,
        operating_mode if operating_mode in _VALID_OPERATING_MODES else "INVALID",
        policy,
        paper,
        live,
        latch_enabled,
        approval_path,
    )


def _approved_strategy_record(record: object) -> bool:
    if not isinstance(record, Mapping):
        return False
    status = str(record.get("promotion_status") or record.get("status") or "").upper()
    metrics = record.get("oos_metrics")
    accepted = bool(
        isinstance(metrics, Mapping)
        and metrics.get("accept") is True
        and metrics.get("validation_schema") == "paper-edge-v1"
        and isinstance(metrics.get("paper_edge"), Mapping)
        and metrics["paper_edge"].get("pass") is True
    )
    last_eval = record.get("last_eval")
    promoted = bool(
        isinstance(last_eval, Mapping)
        and last_eval.get("promote") is True
        and last_eval.get("validation_schema") == "paper-edge-v1"
        and isinstance(last_eval.get("paper_edge"), Mapping)
        and last_eval["paper_edge"].get("pass") is True
    )
    explicitly_approved = bool(record.get("evidence_approved") is True and accepted)
    if status == "PROMOTED":
        return accepted
    if status == "APPROVED":
        return accepted or explicitly_approved
    if status == "ACTIVE-PAPER":
        return promoted
    return False


def load_approved_evidence(
    path: Path | str = DEFAULT_EVIDENCE_REGISTRY_PATH,
) -> frozenset[str]:
    """Return strategy IDs with explicit accepted evidence; errors deny proof."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, TypeError, ValueError, json.JSONDecodeError):
        return frozenset()
    if not isinstance(payload, Mapping):
        return frozenset()

    approved: set[str] = set()
    for section in ("strategies", "variants"):
        records = payload.get(section)
        if not isinstance(records, Mapping):
            continue
        for strategy_id, record in records.items():
            normalized_id = str(strategy_id).strip()
            if normalized_id and _approved_strategy_record(record):
                approved.add(normalized_id)
    return frozenset(approved)


def _active_allowlist(
    policy: EntryPolicy | None,
    approved_paper: frozenset[str],
    approved_live: frozenset[str],
) -> frozenset[str]:
    if policy is EntryPolicy.APPROVED_PAPER:
        return approved_paper
    if policy is EntryPolicy.CONTROLLED_LIVE:
        return approved_live
    return frozenset()


def _paper_readiness_reason(
    *,
    config_valid: bool,
    policy: EntryPolicy | None,
    operating_mode: str,
    approved_paper: frozenset[str],
    prerequisites_met: bool,
) -> tuple[bool, str]:
    if not config_valid:
        return False, "entry_policy_config_invalid"
    if policy is not EntryPolicy.APPROVED_PAPER:
        return False, "approved_paper_policy_required"
    if operating_mode != "PAPER":
        return False, "paper_operating_mode_required"
    if not approved_paper:
        return False, "approved_paper_allowlist_empty"
    if not prerequisites_met:
        return False, "paper_correctness_prerequisites_failed"
    return True, "paper_correctness_and_config_ready"


def _live_authorization_reason(
    *,
    config_valid: bool,
    policy: EntryPolicy | None,
    operating_mode: str,
    approved_live: frozenset[str],
    live_latch_enabled: bool,
    live_approval: LiveApproval | None,
    current_git_sha: str,
    now: datetime | None,
) -> tuple[bool, str]:
    if not config_valid:
        return False, "entry_policy_config_invalid"
    if policy is not EntryPolicy.CONTROLLED_LIVE:
        return False, "controlled_live_entry_policy_required"
    if operating_mode != "CONTROLLED_LIVE":
        return False, "controlled_live_operating_mode_required"
    if not live_latch_enabled:
        return False, "live_latch_missing"
    if not approved_live:
        return False, "approved_live_allowlist_empty"
    if live_approval is None:
        return False, "live_approval_missing_or_invalid"
    if not str(live_approval.strategy_version or "").strip():
        return False, "live_strategy_version_missing"
    if str(live_approval.approved_by or "").strip().lower() in _NON_HUMAN_APPROVERS:
        return False, "human_live_approval_missing"
    if not _FULL_GIT_SHA.fullmatch(str(live_approval.git_sha or "")):
        return False, "live_approval_git_sha_not_full"
    if not _FULL_GIT_SHA.fullmatch(str(current_git_sha or "")):
        return False, "current_git_sha_not_full"

    authorization = evaluate_entry(
        policy=policy,
        operating_mode=operating_mode,
        strategy_id=live_approval.strategy_id,
        strategy_version=live_approval.strategy_version,
        approved_live=approved_live,
        live_latch_enabled=live_latch_enabled,
        live_approval=live_approval,
        current_git_sha=current_git_sha,
        now=now,
    )
    return authorization.allowed, authorization.reason


def evaluate_readiness(
    *,
    system_healthy: bool,
    operating_mode: str,
    entry_policy: EntryPolicy | str,
    approved_paper: Collection[str] = (),
    approved_live: Collection[str] = (),
    approved_evidence: Collection[str] = (),
    live_latch_enabled: bool = False,
    live_approval: LiveApproval | None = None,
    current_git_sha: str = "",
    now: datetime | None = None,
    config_valid: bool = True,
    paper_prerequisites_met: bool = True,
    system_health_reason: str = "",
) -> ReadinessReport:
    """Evaluate readiness with no I/O and no profitability inference."""
    try:
        policy = (
            entry_policy
            if isinstance(entry_policy, EntryPolicy)
            else EntryPolicy(str(entry_policy).strip().upper())
        )
    except ValueError:
        policy = None
        config_valid = False

    mode = str(operating_mode or "").strip().upper()
    if mode not in _VALID_OPERATING_MODES:
        mode = "INVALID"
        config_valid = False

    paper, paper_valid = _safe_allowlist(approved_paper)
    live, live_valid = _safe_allowlist(approved_live)
    evidence, evidence_valid = _safe_allowlist(approved_evidence)
    config_valid = bool(config_valid and paper_valid and live_valid and evidence_valid)
    active_allowlist = _active_allowlist(policy, paper, live)

    paper_ready, paper_reason = _paper_readiness_reason(
        config_valid=config_valid,
        policy=policy,
        operating_mode=mode,
        approved_paper=paper,
        prerequisites_met=paper_prerequisites_met is True,
    )
    evidence_complete = bool(
        evidence and (not active_allowlist or active_allowlist.issubset(evidence))
    )
    edge_unproven = not evidence_complete
    if evidence_complete:
        edge_reason = (
            "approved_evidence_present_for_allowlist"
            if active_allowlist
            else "approved_evidence_present"
        )
    else:
        edge_reason = "approved_evidence_missing_for_allowlist"

    live_allowed, live_reason = _live_authorization_reason(
        config_valid=config_valid,
        policy=policy,
        operating_mode=mode,
        approved_live=live,
        live_latch_enabled=live_latch_enabled is True,
        live_approval=live_approval,
        current_git_sha=current_git_sha,
        now=now,
    )
    healthy = system_healthy is True
    health_reason = system_health_reason.strip() or (
        "operational_checks_passed" if healthy else "operational_checks_failed"
    )
    reasons = {
        "SYSTEM_HEALTHY": health_reason,
        "PAPER_READY": paper_reason,
        "EDGE_UNPROVEN": edge_reason,
        "LIVE_BLOCKED": live_reason,
    }
    return ReadinessReport(
        system_healthy=healthy,
        paper_ready=paper_ready,
        edge_unproven=edge_unproven,
        live_blocked=not live_allowed,
        operating_mode=mode,
        entry_policy=policy.value if policy is not None else "INVALID",
        strategy_allowlist=active_allowlist,
        approved_paper_strategies=paper,
        approved_live_strategies=live,
        approved_evidence=evidence,
        config_valid=config_valid,
        reasons=reasons,
    )


def runtime_readiness(
    *,
    system_healthy: bool,
    system_health_reason: str = "",
    config_module: object | None = None,
    repo_root: Path | str = REPO_ROOT,
    evidence_path: Path | str | None = None,
    approved_evidence: Collection[str] | None = None,
    now: datetime | None = None,
    paper_prerequisites_met: bool = True,
) -> ReadinessReport:
    """Read local config/evidence/approval files and build a readiness report."""
    root = Path(repo_root).resolve()
    runtime = _load_runtime_config(config_module)
    evidence = (
        load_approved_evidence(evidence_path or (root / "data" / "active_strategies.json"))
        if approved_evidence is None
        else approved_evidence
    )

    approval = None
    sha = ""
    if runtime.entry_policy is EntryPolicy.CONTROLLED_LIVE:
        approval_path = runtime.live_approval_path
        if approval_path is not None:
            if not approval_path.is_absolute():
                approval_path = root / approval_path
            approval = load_live_approval(approval_path)
        sha = resolve_current_git_sha(root)

    return evaluate_readiness(
        system_healthy=system_healthy,
        system_health_reason=system_health_reason,
        operating_mode=runtime.operating_mode,
        entry_policy=runtime.entry_policy or "INVALID",
        approved_paper=runtime.approved_paper,
        approved_live=runtime.approved_live,
        approved_evidence=evidence,
        live_latch_enabled=runtime.live_latch_enabled,
        live_approval=approval,
        current_git_sha=sha,
        now=now or datetime.now(timezone.utc),
        config_valid=runtime.valid,
        paper_prerequisites_met=paper_prerequisites_met,
    )


def readiness_payload(**kwargs: Any) -> dict[str, Any]:
    """Convenience wrapper for health/status publishers."""
    return runtime_readiness(**kwargs).to_dict()
