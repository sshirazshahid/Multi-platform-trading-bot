"""Operating-mode tests: checklist sign-off gate for CONTROLLED_LIVE.

We test the gate in isolation (core/live_gate.py) so we don't have to
construct a full BotEngine. The gate is the load-bearing piece — every
other mode check is a simple branch on the config value.
"""
from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path

import pytest

from core.live_gate import (
    enforce_controlled_live_gate,
    is_checklist_signed,
)


def _write(p: Path, body: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(body, encoding="utf-8")


# ── is_checklist_signed ───────────────────────────────────────────────

def test_missing_checklist_not_signed(tmp_path):
    ok, msg = is_checklist_signed(tmp_path / "nope.md")
    assert ok is False
    assert "not found" in msg


def test_unsigned_checklist_rejected(tmp_path):
    f = tmp_path / "c.md"
    _write(f, "# Checklist\n\nAll criteria TBD.\n")
    ok, msg = is_checklist_signed(f)
    assert ok is False
    assert "no valid" in msg.lower() or "refusing" in msg.lower()


def test_example_comment_signature_does_not_count(tmp_path):
    """The shipped template has a commented-out example signature —
    it must NOT be read as a valid sign-off."""
    f = tmp_path / "c.md"
    _write(f, "# Checklist\n\n<!-- Signed-By: Placeholder 2026-12-31 -->\n")
    ok, _ = is_checklist_signed(f)
    assert ok is False


def test_future_date_rejected(tmp_path):
    f = tmp_path / "c.md"
    future = (date.today() + timedelta(days=7)).isoformat()
    _write(f, f"# Checklist\n\nSigned-By: Owner {future}\n")
    ok, _ = is_checklist_signed(f)
    assert ok is False


def test_valid_signature_accepted(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, f"# Checklist\n\nSigned-By: Real Owner {today}\n")
    ok, msg = is_checklist_signed(f)
    assert ok is True
    assert "Real Owner" in msg


def test_latest_signature_wins(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, (
        "# Checklist\n"
        "Signed-By: Old Owner 2020-01-01\n"
        f"Signed-By: Current Owner {today}\n"
    ))
    ok, msg = is_checklist_signed(f)
    assert ok is True
    assert "Current Owner" in msg


def test_malformed_signature_ignored(tmp_path):
    f = tmp_path / "c.md"
    _write(f, "Signed-By: Missing Date\nSigned-By: Only 2026\n")
    ok, _ = is_checklist_signed(f)
    assert ok is False


# ── enforce_controlled_live_gate ──────────────────────────────────────

def test_enforce_observation_never_raises(tmp_path):
    """OBSERVATION mode: gate is a no-op even without a checklist."""
    enforce_controlled_live_gate("OBSERVATION", path=tmp_path / "absent.md")


def test_enforce_paper_never_raises(tmp_path):
    enforce_controlled_live_gate("PAPER", path=tmp_path / "absent.md")


def test_enforce_controlled_live_unsigned_raises(tmp_path):
    f = tmp_path / "c.md"
    _write(f, "# unsigned\n")
    with pytest.raises(SystemExit) as exc:
        enforce_controlled_live_gate("CONTROLLED_LIVE", path=f)
    assert "REFUSING TO START" in str(exc.value)


def test_enforce_controlled_live_signed_passes(tmp_path):
    f = tmp_path / "c.md"
    today = date.today().isoformat()
    _write(f, f"Signed-By: Owner {today}\n")
    enforce_controlled_live_gate("CONTROLLED_LIVE", path=f)  # no raise
