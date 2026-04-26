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
from datetime import date
from pathlib import Path

CHECKLIST_PATH = Path("docs/CONTROLLED_LIVE_CHECKLIST.md")

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


def is_checklist_signed(path: Path | str | None = None) -> tuple[bool, str]:
    """Return (ok, message). `ok=True` means CONTROLLED_LIVE may proceed."""
    p = Path(path) if path else CHECKLIST_PATH
    if not p.exists():
        return False, f"checklist not found at {p}"
    try:
        text = p.read_text(encoding="utf-8", errors="replace")
    except OSError as e:
        return False, f"cannot read checklist: {e}"

    # Walk every non-empty, non-header line and return on the first valid
    # signature we find. Multiple signatures just means the latest counts.
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
    return True, f"signed by {name} on {d.isoformat()}"


def enforce_controlled_live_gate(operating_mode: str,
                                  path: Path | str | None = None) -> None:
    """Raise SystemExit if mode=CONTROLLED_LIVE and the checklist is unsigned.

    No-op for OBSERVATION or PAPER modes.
    """
    if (operating_mode or "").upper() != "CONTROLLED_LIVE":
        return
    ok, msg = is_checklist_signed(path)
    if not ok:
        raise SystemExit(
            "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: " + msg
        )
