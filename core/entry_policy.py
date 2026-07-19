"""Deterministic entry authorization shared by every order-producing lane.

The policy controls new exposure only. Reductions, protective orders,
reconciliation, and emergency closes are always allowed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Collection


class EntryPolicy(str, Enum):
    PROTECT_ONLY = "PROTECT_ONLY"
    SHADOW_ONLY = "SHADOW_ONLY"
    APPROVED_PAPER = "APPROVED_PAPER"
    CONTROLLED_LIVE = "CONTROLLED_LIVE"


@dataclass(frozen=True, slots=True)
class ModeProfile:
    name: str
    max_open_positions: int
    risk_per_trade_pct: float
    aggregate_open_risk_pct: float
    gross_exposure_pct: float
    max_leverage: float

    def __post_init__(self) -> None:
        if self.max_open_positions < 0:
            raise ValueError("max_open_positions cannot be negative")
        for field_name in (
            "risk_per_trade_pct",
            "aggregate_open_risk_pct",
            "gross_exposure_pct",
            "max_leverage",
        ):
            if float(getattr(self, field_name)) < 0:
                raise ValueError(f"{field_name} cannot be negative")


STANDARD_PAPER_PROFILE = "STANDARD"
AGGRESSIVE_RESEARCH_PAPER_PROFILE = "AGGRESSIVE_RESEARCH"
# 2026-07-19 max-flow band engine (owner-approved): cohort/epoch label for the
# firehose paper run. Same risk envelope as AGGRESSIVE_RESEARCH — the profile
# is the gate key for the accuracy-band geometry guard, not a risk expansion.
MAX_FLOW_BAND_PAPER_PROFILE = "MAX_FLOW_BAND"
_VALID_PAPER_PROFILES = frozenset(
    {
        STANDARD_PAPER_PROFILE,
        AGGRESSIVE_RESEARCH_PAPER_PROFILE,
        MAX_FLOW_BAND_PAPER_PROFILE,
    }
)

_MODE_PROFILES = {
    "OBSERVATION": ModeProfile("OBSERVATION", 0, 0.0, 0.0, 0.0, 0.0),
    "CONTROLLED_LIVE": ModeProfile("CONTROLLED_LIVE", 1, 0.001, 0.001, 0.02, 1.0),
}

# The aggressive profile increases independent PAPER sampling without increasing
# the portfolio risk envelope: twice the slots, half the risk per trade, and the
# same aggregate risk, gross exposure, and leverage. It is deliberately absent
# from CONTROLLED_LIVE; a non-standard profile outside PAPER fails closed.
_PAPER_PROFILES = {
    STANDARD_PAPER_PROFILE: ModeProfile("PAPER", 3, 0.0025, 0.0075, 0.10, 1.5),
    AGGRESSIVE_RESEARCH_PAPER_PROFILE: ModeProfile(
        "PAPER_AGGRESSIVE_RESEARCH", 6, 0.00125, 0.0075, 0.10, 1.5
    ),
    # Copy of AGGRESSIVE_RESEARCH (spec T4) — also absent from
    # CONTROLLED_LIVE, so the profile fails closed outside PAPER.
    MAX_FLOW_BAND_PAPER_PROFILE: ModeProfile(
        "PAPER_MAX_FLOW_BAND", 6, 0.00125, 0.0075, 0.10, 1.5
    ),
}


@dataclass(frozen=True, slots=True)
class LiveApproval:
    strategy_id: str
    strategy_version: str
    git_sha: str
    expires_at: datetime
    approved_by: str

    def __post_init__(self) -> None:
        if not all((self.strategy_id, self.strategy_version, self.git_sha, self.approved_by)):
            raise ValueError("live approval fields cannot be blank")
        if self.expires_at.tzinfo is None:
            raise ValueError("live approval expiry must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EntryAuthorization:
    allowed: bool
    reason: str
    policy: str
    strategy_id: str


def normalize_paper_profile(value: str | None) -> str:
    """Normalize a PAPER profile name and reject unknown profiles."""

    profile = str(value or STANDARD_PAPER_PROFILE).strip().upper().replace("-", "_")
    if profile not in _VALID_PAPER_PROFILES:
        raise ValueError(f"unknown PAPER trading profile: {value!r}")
    return profile


def mode_profile_for(
    operating_mode: str,
    paper_profile: str = STANDARD_PAPER_PROFILE,
) -> ModeProfile:
    """Return the immutable risk profile for an operating mode.

    A non-standard PAPER profile is invalid in OBSERVATION or CONTROLLED_LIVE,
    preventing a research setting from leaking into a real-capital process.
    """

    mode = str(operating_mode or "").strip().upper()
    profile = normalize_paper_profile(paper_profile)
    if mode == "PAPER":
        return _PAPER_PROFILES[profile]
    if profile != STANDARD_PAPER_PROFILE:
        raise ValueError(f"PAPER profile {profile!r} requires PAPER operating mode")
    try:
        return _MODE_PROFILES[mode]
    except KeyError as exc:
        raise ValueError(f"unknown operating mode: {operating_mode!r}") from exc


def parse_allowlist(raw: str | Collection[str] | None) -> frozenset[str]:
    if raw is None:
        return frozenset()
    values = raw.split(",") if isinstance(raw, str) else raw
    return frozenset(str(value).strip() for value in values if str(value).strip())


def strategy_id_for_action(action: dict, signal_source: str = "") -> str:
    """Resolve an exact strategy ID; legacy mcp is a deterministic registry alias."""
    explicit = str(action.get("strategy_id") or action.get("strategy") or "").strip()
    if explicit:
        return explicit
    source = str(signal_source or action.get("signal_source") or "").strip().lower()
    aliases = {
        "mcp": "mcp_registry",
        "mcp_det": "mcp_registry",
        "machine": "machine_registry",
        "tsmom": "tsmom_registry",
        "s3": "s3_registry",
    }
    return aliases.get(source, source)


def load_live_approval(path: Path | str) -> LiveApproval | None:
    """Load an expiring live approval. Invalid or incomplete files deny entry."""
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        expiry = datetime.fromisoformat(str(payload["expires_at"]).replace("Z", "+00:00"))
        return LiveApproval(
            strategy_id=str(payload["strategy_id"]).strip(),
            strategy_version=str(payload["strategy_version"]).strip(),
            git_sha=str(payload["git_sha"]).strip(),
            expires_at=expiry,
            approved_by=str(payload["approved_by"]).strip(),
        )
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError):
        return None


def current_git_sha(repo_root: Path | str = ".") -> str:
    """Resolve HEAD without spawning a process; return blank on any ambiguity."""
    try:
        root = Path(repo_root).resolve()
        git_dir = root / ".git"
        if git_dir.is_file():
            marker = git_dir.read_text(encoding="utf-8").strip()
            if not marker.lower().startswith("gitdir:"):
                return ""
            git_dir = (root / marker.split(":", 1)[1].strip()).resolve()
        head = (git_dir / "HEAD").read_text(encoding="utf-8").strip()
        if not head.startswith("ref:"):
            return head if len(head) >= 7 else ""
        ref_name = head.split(":", 1)[1].strip()
        ref_path = git_dir / ref_name
        if ref_path.exists():
            return ref_path.read_text(encoding="utf-8").strip()
        packed = git_dir / "packed-refs"
        if packed.exists():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if not line or line.startswith(("#", "^")):
                    continue
                sha, name = line.split(" ", 1)
                if name.strip() == ref_name:
                    return sha.strip()
    except (OSError, ValueError):
        pass
    return ""


def _decision(allowed: bool, reason: str, policy: object, strategy_id: str) -> EntryAuthorization:
    value = policy.value if isinstance(policy, EntryPolicy) else str(policy or "")
    return EntryAuthorization(allowed, reason, value, strategy_id)


def evaluate_entry(
    *,
    policy: EntryPolicy | str,
    operating_mode: str,
    strategy_id: str,
    strategy_version: str | None = None,
    approved_paper: Collection[str] = (),
    approved_live: Collection[str] = (),
    live_latch_enabled: bool = False,
    live_approval: LiveApproval | None = None,
    current_git_sha: str = "",
    now: datetime | None = None,
    is_reducing: bool = False,
) -> EntryAuthorization:
    """Evaluate a new-exposure request with no side effects."""
    strategy_id = str(strategy_id or "").strip()
    if is_reducing:
        return _decision(True, "reduce_only_always_allowed", policy, strategy_id)
    try:
        parsed_policy = policy if isinstance(policy, EntryPolicy) else EntryPolicy(str(policy).upper())
    except ValueError:
        return _decision(False, "invalid_entry_policy", policy, strategy_id)
    if not strategy_id:
        return _decision(False, "strategy_id_missing", parsed_policy, strategy_id)

    mode = str(operating_mode or "").upper()
    if mode == "OBSERVATION":
        return _decision(False, "observation_mode", parsed_policy, strategy_id)
    if parsed_policy is EntryPolicy.PROTECT_ONLY:
        return _decision(False, "entry_policy_protect_only", parsed_policy, strategy_id)
    if parsed_policy is EntryPolicy.SHADOW_ONLY:
        return _decision(False, "entry_policy_shadow_only", parsed_policy, strategy_id)

    if parsed_policy is EntryPolicy.APPROVED_PAPER:
        if mode != "PAPER":
            return _decision(False, "approved_paper_requires_paper_mode", parsed_policy, strategy_id)
        if strategy_id not in parse_allowlist(approved_paper):
            return _decision(False, "strategy_not_approved_for_paper", parsed_policy, strategy_id)
        return _decision(True, "approved_paper_strategy", parsed_policy, strategy_id)

    if mode != "CONTROLLED_LIVE":
        return _decision(False, "controlled_live_requires_live_mode", parsed_policy, strategy_id)
    if not live_latch_enabled:
        return _decision(False, "live_latch_missing", parsed_policy, strategy_id)
    if strategy_id not in parse_allowlist(approved_live):
        return _decision(False, "strategy_not_approved_for_live", parsed_policy, strategy_id)
    if live_approval is None:
        return _decision(False, "live_approval_missing", parsed_policy, strategy_id)
    if live_approval.strategy_id != strategy_id:
        return _decision(False, "live_strategy_id_mismatch", parsed_policy, strategy_id)
    if not strategy_version or live_approval.strategy_version != strategy_version:
        return _decision(False, "live_strategy_version_mismatch", parsed_policy, strategy_id)
    if not current_git_sha or live_approval.git_sha != current_git_sha:
        return _decision(False, "live_git_sha_mismatch", parsed_policy, strategy_id)
    current_time = now or datetime.now(timezone.utc)
    if current_time.tzinfo is None:
        current_time = current_time.replace(tzinfo=timezone.utc)
    if live_approval.expires_at <= current_time:
        return _decision(False, "live_approval_expired", parsed_policy, strategy_id)
    return _decision(True, "controlled_live_approved", parsed_policy, strategy_id)


def authorize_runtime_entry(
    strategy_id: str,
    *,
    strategy_version: str | None = None,
    is_reducing: bool = False,
    repo_root: Path | str = ".",
) -> EntryAuthorization:
    """Evaluate against the process configuration and live approval file."""
    try:
        from config import (
            APPROVED_LIVE_STRATEGIES,
            APPROVED_PAPER_STRATEGIES,
            CONTROLLED_LIVE_ENABLED,
            ENTRY_POLICY,
            LIVE_ENTRY_APPROVAL_PATH,
            OPERATING_MODE,
        )
    except Exception:
        return _decision(False, "entry_policy_config_unavailable", "", strategy_id)

    approval = None
    sha = ""
    if str(ENTRY_POLICY).upper() == EntryPolicy.CONTROLLED_LIVE.value:
        approval = load_live_approval(LIVE_ENTRY_APPROVAL_PATH)
        sha = current_git_sha(repo_root)
    authorization = evaluate_entry(
        policy=ENTRY_POLICY,
        operating_mode=OPERATING_MODE,
        strategy_id=strategy_id,
        strategy_version=strategy_version,
        approved_paper=APPROVED_PAPER_STRATEGIES,
        approved_live=APPROVED_LIVE_STRATEGIES,
        live_latch_enabled=CONTROLLED_LIVE_ENABLED,
        live_approval=approval,
        current_git_sha=sha,
        is_reducing=is_reducing,
    )
    if not authorization.allowed or is_reducing:
        return authorization

    from core.strategy_program import strategy_program_decision

    target = (
        "LIVE"
        if str(ENTRY_POLICY).upper() == EntryPolicy.CONTROLLED_LIVE.value
        else "PAPER"
    )
    program = strategy_program_decision(strategy_id, target=target)
    if not program.allowed:
        return _decision(False, program.reason, ENTRY_POLICY, strategy_id)
    return authorization
