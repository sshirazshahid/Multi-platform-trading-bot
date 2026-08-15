"""Tests for the aggressive research-hunt loop (scripts/run_research_hunt.py).

The loop's ONLY job is to answer, on a schedule: "is there new evidence that
makes a NEW screen possible, or an OLD frozen screen re-runnable?" It surfaces
candidates and re-runs frozen preregs. It must NEVER:
  - touch trading config, mode, gates, or leverage
  - invent a screen without a hashed pre-registration
  - re-run a screen whose prereg hash has changed
  - claim a GO on its own authority

Design constraint that shapes every test below: this loop runs unattended, so
its failure mode must be "does nothing and says so", never "acts on a guess".

Run: venv/Scripts/python.exe -m pytest tests/test_research_hunt_loop.py -v
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import scripts.run_research_hunt as hunt  # noqa: E402

SRC = (ROOT / "scripts" / "run_research_hunt.py").read_text(encoding="utf-8")


# ── safety: the loop is structurally incapable of trading ────────────────
def test_module_has_no_order_path():
    """No order/exchange-write symbol may be reachable from this module."""
    for forbidden in (
        "create_order", "cancel_order", "open_position", "close_position",
        "set_leverage", "OrderManager",
    ):
        assert forbidden not in SRC, f"research loop must not reference {forbidden}"


def test_never_mutates_runtime_config():
    """The loop reports; it does not tune."""
    for forbidden in ("os.environ[", "putenv", "CONTROLLED_LIVE_ENABLED"):
        assert forbidden not in SRC, f"loop must not mutate runtime config ({forbidden})"


# ── data-readiness gate ──────────────────────────────────────────────────
def test_asset_below_threshold_is_not_proposed(tmp_path):
    """A feed with too little history must NOT become a screen candidate."""
    f = tmp_path / "thin.jsonl"
    f.write_text("\n".join('{"hour": %d}' % (1_780_000_000 + i * 3600)
                           for i in range(10)), encoding="utf-8")
    st = hunt.assess_asset(hunt.Asset("thin", f, min_rows=1000, min_days=30))
    assert st["ready"] is False
    assert "row" in st["reason"] or "day" in st["reason"]


def test_asset_above_threshold_is_proposed(tmp_path):
    """A feed that has grown past its floor becomes a candidate."""
    f = tmp_path / "fat.jsonl"
    rows = ['{"hour": %d}' % (1_780_000_000 + i * 3600) for i in range(2000)]
    f.write_text("\n".join(rows), encoding="utf-8")
    st = hunt.assess_asset(hunt.Asset("fat", f, min_rows=1000, min_days=30))
    assert st["ready"] is True, st["reason"]


def test_missing_asset_is_reported_not_crashed(tmp_path):
    """An absent feed is a finding, not an exception."""
    st = hunt.assess_asset(hunt.Asset("gone", tmp_path / "nope.jsonl",
                                      min_rows=10, min_days=1))
    assert st["ready"] is False and "absent" in st["reason"].lower()


# ── frozen-screen re-run gate ────────────────────────────────────────────
def test_rerun_blocked_when_prereg_hash_changed(tmp_path):
    """A screen whose prereg was EDITED must never be silently re-run."""
    prereg = tmp_path / "p.md"
    prereg.write_text("original", encoding="utf-8")
    script = tmp_path / "s.py"          # must exist, or the missing-script
    script.write_text("pass", encoding="utf-8")  # guard fires first
    scr = hunt.FrozenScreen("s", prereg, "deadbeef" * 8, script)
    ok, why = hunt.rerun_allowed(scr)
    assert ok is False and "hash" in why.lower()


def test_rerun_allowed_when_hash_matches(tmp_path):
    import hashlib

    prereg = tmp_path / "p.md"
    prereg.write_text("frozen text", encoding="utf-8")
    h = hashlib.sha256(prereg.read_bytes()).hexdigest()
    script = tmp_path / "s.py"
    script.write_text("print('x')", encoding="utf-8")
    ok, why = hunt.rerun_allowed(hunt.FrozenScreen("s", prereg, h, script))
    assert ok is True, why


def test_rerun_blocked_when_script_missing(tmp_path):
    import hashlib

    prereg = tmp_path / "p.md"
    prereg.write_text("frozen", encoding="utf-8")
    h = hashlib.sha256(prereg.read_bytes()).hexdigest()
    ok, why = hunt.rerun_allowed(
        hunt.FrozenScreen("s", prereg, h, tmp_path / "absent.py"))
    assert ok is False and "script" in why.lower()


# ── verdict discipline ───────────────────────────────────────────────────
def test_loop_never_emits_go():
    """The loop may propose and re-run; only a human/audit may declare GO."""
    assert '"GO"' not in SRC and "'GO'" not in SRC, (
        "the hunt loop must never emit a GO verdict on its own authority"
    )


def test_report_is_written_and_shaped(tmp_path, monkeypatch):
    """One machine-readable report per tick, with the fields ops needs."""
    out = tmp_path / "hunt.json"
    monkeypatch.setattr(hunt, "REPORT_PATH", out)
    monkeypatch.setattr(hunt, "ASSETS", ())
    monkeypatch.setattr(hunt, "FROZEN_SCREENS", ())
    rc = hunt.main([])
    assert rc == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    for k in ("ts", "assets", "screens", "candidates", "actions", "posture"):
        assert k in d, f"report missing {k}"
    assert d["posture"]["trading_unchanged"] is True


def test_dry_run_writes_nothing(tmp_path, monkeypatch):
    out = tmp_path / "hunt.json"
    monkeypatch.setattr(hunt, "REPORT_PATH", out)
    monkeypatch.setattr(hunt, "ASSETS", ())
    monkeypatch.setattr(hunt, "FROZEN_SCREENS", ())
    hunt.main(["--dry-run"])
    assert not out.exists(), "--dry-run must not write the report"


def test_tick_survives_a_broken_asset(tmp_path, monkeypatch):
    """One unreadable feed must not kill the whole tick."""
    bad = tmp_path / "bad.jsonl"
    bad.write_bytes(b"\xff\xfe not json at all")
    out = tmp_path / "hunt.json"
    monkeypatch.setattr(hunt, "REPORT_PATH", out)
    monkeypatch.setattr(hunt, "ASSETS",
                        (hunt.Asset("bad", bad, min_rows=1, min_days=0),))
    monkeypatch.setattr(hunt, "FROZEN_SCREENS", ())
    assert hunt.main([]) == 0
    d = json.loads(out.read_text(encoding="utf-8"))
    assert d["assets"], "the broken asset must still be reported"
