"""
core/live_gate.py — CONTROLLED_LIVE sign-off gate (spec Appendix B).

Standalone helper that verifies the owner has signed
`docs/CONTROLLED_LIVE_CHECKLIST.md`. A valid signature is a line that
matches ``Signed-By: <name> <YYYY-MM-DD>`` where the date parses as an
ISO date and is not in the future.

Used by `BotEngine.run()` when `OPERATING_MODE == "CONTROLLED_LIVE"`: an
unsigned checklist raises SystemExit before any live trading begins.

We isolate this as its own module so it can be tested without importing
the full engine (which drags in `schedule`, exchanges, etc.).
"""

from __future__ import annotations

import re
from datetime import date, timedelta
from pathlib import Path

CHECKLIST_PATH = Path("docs/CONTROLLED_LIVE_CHECKLIST.md")
MAX_SIGNATURE_AGE_DAYS = 30

# Matches `Signed-By: Owner Name 2026-12-31`, allowing a leading `<!-- ` /
# trailing ` -->` pair so the repo can ship an example signature that is
# explicitly commented out. Whitespace is flexible; the pattern still rejects
# a line that's purely inside the example HTML comment because the matcher
# below calls .strip() and re-checks the bracket structure.
_SIGN_RE = re.compile(
    r"^\s*(?:<!--\s*)?Signed-By:\s*(?P<name>.+?)\s+(?P<date>\d{4}-\d{2}-\d{2})\s*(?:-->)?\s*$"
)


def _line_is_signature(line: str) -> tuple[str, date] | None:
    """Parse `line` as a signature. Returns (name, date) or None."""
    s = line.strip()
    if not s or s.startswith("#"):
        return None
    # Reject commented-out examples explicitly — the repo ships one as a
    # documented template and we don't want it to count as sign-off.
    if s.startswith("<!--") and s.endswith("-->"):
        return None
    m = _SIGN_RE.match(s)
    if not m:
        return None
    try:
        d = date.fromisoformat(m.group("date"))
    except ValueError:
        return None
    if d > date.today():
        return None
    return (m.group("name").strip(), d)


def is_checklist_signed(
    path: Path | str | None = None,
    *,
    max_signature_age_days: int = MAX_SIGNATURE_AGE_DAYS,
) -> tuple[bool, str]:
    """Return (ok, message). `ok=True` means CONTROLLED_LIVE may proceed."""
    p = Path(path) if path else CHECKLIST_PATH
    if not p.exists():
        return False, f"checklist not found at {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"cannot read checklist: {e}"

    unchecked = [
        raw.strip()
        for raw in text.splitlines()
        if re.match(r"^\s*-\s*\[\s\]\s+", raw, flags=re.IGNORECASE)
    ]
    if unchecked:
        return False, (
            f"{len(unchecked)} acceptance item(s) remain unchecked in {p} "
            "— refusing to run CONTROLLED_LIVE"
        )

    signatures = []
    for raw in text.splitlines():
        sig = _line_is_signature(raw)
        if sig:
            signatures.append(sig)
    if not signatures:
        return False, (
            "no valid 'Signed-By: <name> <YYYY-MM-DD>' line in "
            f"{p} — refusing to run CONTROLLED_LIVE. See file for criteria."
        )
    name, d = signatures[-1]
    if max_signature_age_days >= 0:
        oldest_allowed = date.today() - timedelta(days=max_signature_age_days)
        if d < oldest_allowed:
            return False, (
                f"signature by {name} on {d.isoformat()} is older than "
                f"{max_signature_age_days} days — re-verify and re-sign {p}"
            )
    return True, f"signed by {name} on {d.isoformat()}"


def enforce_controlled_live_gate(operating_mode: str,
                                  path: Path | str | None = None,
                                  controlled_live_enabled: bool | None = None) -> None:
    """Raise SystemExit if mode=CONTROLLED_LIVE and the checklist is unsigned.

    No-op for OBSERVATION or PAPER modes.
    """
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return
    if controlled_live_enabled is False:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: "
            "CONTROLLED_LIVE_ENABLED is not true"
        )
    ok, msg = is_checklist_signed(path)
    if not ok:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: " + msg
        )


def enforce_strategy_readiness_gate(
    operating_mode: str,
    *,
    db_path: Path | str = "data/warehouse.sqlite",
    strategy_family: str | None = None,
) -> None:
    """Raise SystemExit if CONTROLLED_LIVE lacks proven strategy evidence.

    Checklist signature proves operator intent. This gate proves the selected
    strategy has recent after-cost evidence. Missing or negative evidence is a
    hard stop for live startup.
    """
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return
    from core.strategy_readiness import evaluate_warehouse

    report = evaluate_warehouse(db_path, mode="PAPER", strategy_family=strategy_family)
    if report.get("ready"):
        return
    reasons = "; ".join((report.get("reasons") or [])[:5]) or "not promotion-ready"
    name = report.get("name", "strategy")
    raise SystemExit(
        "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: "
        f"{name} evidence gate failed: {reasons}"
    )


def live_latch_permits_execution(operating_mode: str,
                                 controlled_live_enabled: bool) -> bool:
    """Latch 2 of the CONTROLLED_LIVE double-latch (the env-var latch).

    Real-order execution is permitted UNLESS we are in CONTROLLED_LIVE without
    the `CONTROLLED_LIVE_ENABLED` env latch set. PAPER places paper orders
    regardless; OBSERVATION is blocked by a separate upstream gate, so this
    latch alone does not block it. Latch 1 (a signed checklist) is enforced
    separately by `enforce_controlled_live_gate` at startup.
    """
    if (operating_mode or "").upper() == "CONTROLLED_LIVE" and not controlled_live_enabled:
        return False
    return True
