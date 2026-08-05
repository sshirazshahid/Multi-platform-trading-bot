"""Operating mode, entry policy, and derived DRY_RUN flags."""
import os

from core.entry_policy import (
    AGGRESSIVE_RESEARCH_PAPER_PROFILE,
    STANDARD_PAPER_PROFILE,
    mode_profile_for,
    normalize_paper_profile,
    parse_allowlist,
)

# ------------------------------------------------------------------
# OPERATING MODE — learning-first rebuild (2026-04-14 pivot)
# ------------------------------------------------------------------
# OBSERVATION     — collect balances/positions/candidates, place NO orders
#                   (not even paper). Used for warehouse population & feature
#                   learning when the bot is in diagnostic mode.
# PAPER           — simulate all fills via core/sim_execution.py. The default
#                   during rebuild. Risk engine fully active; no real capital
#                   moves.
# CONTROLLED_LIVE — real orders. Disabled unless ALL of:
#                     * OPERATING_MODE=CONTROLLED_LIVE
#                     * env var CONTROLLED_LIVE_ENABLED=true
#                     * docs/CONTROLLED_LIVE_CHECKLIST.md is signed
#                   Spec requires owner sign-off before any live capital.
# ------------------------------------------------------------------
_VALID_MODES = {"OBSERVATION", "PAPER", "CONTROLLED_LIVE"}
OPERATING_MODE = os.getenv("OPERATING_MODE", "PAPER").upper()
if OPERATING_MODE not in _VALID_MODES:
    raise ValueError(f"OPERATING_MODE must be one of {_VALID_MODES}, got {OPERATING_MODE!r}")

PAPER_TRADING_PROFILE = normalize_paper_profile(
    os.getenv("PAPER_TRADING_PROFILE", STANDARD_PAPER_PROFILE)
)
# Research profiles are PAPER-only. Coerce (don't raise) so a menu switch to
# OBSERVATION leaves PAPER_TRADING_PROFILE=MAX_FLOW_BAND in .env but still boots —
# F3/geometry knobs stay inert because they require OPERATING_MODE=PAPER.
if OPERATING_MODE != "PAPER" and PAPER_TRADING_PROFILE != STANDARD_PAPER_PROFILE:
    PAPER_TRADING_PROFILE = STANDARD_PAPER_PROFILE
PAPER_PROFILE_STARTED_AT = os.getenv("PAPER_PROFILE_STARTED_AT", "").strip()
_AGGRESSIVE_PAPER_RESEARCH = (
    OPERATING_MODE == "PAPER"
    and PAPER_TRADING_PROFILE == AGGRESSIVE_RESEARCH_PAPER_PROFILE
)

# Legacy DRY_RUN is now derived from the mode. Any existing code path that
# branches on DRY_RUN gets paper execution unless we are explicitly CONTROLLED_LIVE.
DRY_RUN = OPERATING_MODE != "CONTROLLED_LIVE"

# Extra latch for live mode — env var must also be flipped explicitly.
CONTROLLED_LIVE_ENABLED = os.getenv("CONTROLLED_LIVE_ENABLED", "false").lower() == "true"

# New-exposure authorization is independent from process mode. PAPER can run
# feeds, shadow decisions, reconciliation, and exits while entries stay denied.
_VALID_ENTRY_POLICIES = {
    "PROTECT_ONLY", "SHADOW_ONLY", "APPROVED_PAPER", "CONTROLLED_LIVE",
}
ENTRY_POLICY = os.getenv("ENTRY_POLICY", "SHADOW_ONLY").strip().upper()
if ENTRY_POLICY not in _VALID_ENTRY_POLICIES:
    raise ValueError(
        f"ENTRY_POLICY must be one of {_VALID_ENTRY_POLICIES}, got {ENTRY_POLICY!r}"
    )
APPROVED_PAPER_STRATEGIES = parse_allowlist(os.getenv("APPROVED_PAPER_STRATEGIES", ""))
APPROVED_LIVE_STRATEGIES = parse_allowlist(os.getenv("APPROVED_LIVE_STRATEGIES", ""))
LIVE_ENTRY_APPROVAL_PATH = os.getenv(
    "LIVE_ENTRY_APPROVAL_PATH", "data/live_entry_approval.json"
)
MODE_PROFILE = mode_profile_for(OPERATING_MODE, PAPER_TRADING_PROFILE)

LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
