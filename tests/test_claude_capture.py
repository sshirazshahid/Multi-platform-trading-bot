"""Decision Provenance Bundle — Tranche B (Claude capture).

Test specs 9-13 from shiraz-main-test-plan-20260612-082500.md:
  9.  calls_*.jsonl entry carries full prompt + raw_response; round-trip
      equality with what the parser consumed.
  10. Concurrent-append integrity through the _audit_log lock.
  11. Audit-write failure counter increments on injected OSError.
  12. meta_out contract: existing call sites unaffected (signature compat).

MCPBrain._call_claude / _repair_json tests removed (De-Emotion: Claude
decision path deleted). Research-side capture via utils.claude_client kept.

NEVER touches data/ — AUDIT_DIR / AUDIT_FAILURES_FILE / DECISION_LOG are
monkeypatched to tmp_path.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import subprocess
import threading

import utils.claude_client as cc


def _fake_cli_success(monkeypatch, response_text: str):
    """Mock the CLI subprocess with a successful JSON envelope."""
    envelope = json.dumps({"result": response_text, "is_error": False, "total_cost_usd": 0})
    cp = subprocess.CompletedProcess(args=["claude"], returncode=0, stdout=envelope, stderr="")
    monkeypatch.setattr(cc.subprocess, "run", lambda *a, **k: cp)
    monkeypatch.setattr(cc, "_claude_code_available", True)


def _patch_audit_paths(monkeypatch, tmp_path):
    audit_dir = tmp_path / "claude_audit"
    fails = tmp_path / "claude_audit_failures.json"
    monkeypatch.setattr(cc, "AUDIT_DIR", audit_dir)
    monkeypatch.setattr(cc, "AUDIT_FAILURES_FILE", fails)
    return audit_dir, fails


# ── Spec 9: full prompt + raw_response round-trip ──


def test_audit_entry_full_prompt_and_raw_response_roundtrip(monkeypatch, tmp_path):
    audit_dir, _ = _patch_audit_paths(monkeypatch, tmp_path)
    response_text = '{"actions": [{"type": "OPEN", "symbol": "BTC/USDT"}]}'
    _fake_cli_success(monkeypatch, response_text)

    meta: dict = {}
    out = cc.call_claude_cli("PROMPT TEXT with unicode é", "SYS", meta_out=meta)

    assert out == response_text
    files = list(audit_dir.glob("calls_*.jsonl"))
    assert len(files) == 1
    entry = json.loads(files[0].read_text(encoding="utf-8").splitlines()[-1])
    # New provenance fields
    assert entry["prompt"] == "PROMPT TEXT with unicode é"
    assert entry["raw_response"] == response_text  # == what the parser consumed
    assert entry["response_sha256"] == hashlib.sha256(response_text.encode("utf-8")).hexdigest()
    # ALL existing fields kept
    for key in (
        "attempt",
        "model",
        "effort",
        "timeout",
        "prompt_len",
        "system_prompt_len",
        "latency_sec",
        "returncode",
        "status",
        "cost_usd",
        "response_len",
        "ts",
    ):
        assert key in entry, f"existing audit field dropped: {key}"
    assert entry["status"] == "success"
    # meta_out contract: 1-based final attempt + raw text
    assert meta == {"attempt": 1, "raw": response_text}


# ── Spec 10: 2-thread append integrity through the lock ──


def test_audit_log_concurrent_append_every_line_valid_json(monkeypatch, tmp_path):
    audit_dir, _ = _patch_audit_paths(monkeypatch, tmp_path)
    big = "y" * 4096  # multi-KB lines exceed Windows append atomicity
    n = 50

    def worker(tag):
        for i in range(n):
            cc._audit_log(
                {"status": "success", "tag": tag, "i": i, "prompt": big, "raw_response": big}
            )

    t1 = threading.Thread(target=worker, args=("t1",))
    t2 = threading.Thread(target=worker, args=("t2",))
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    files = list(audit_dir.glob("calls_*.jsonl"))
    assert len(files) == 1
    lines = files[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2 * n
    for ln in lines:
        json.loads(ln)  # every line must be intact JSON (no interleaving)


# ── Spec 11: audit-write failure counter on injected OSError ──


def test_audit_write_failure_counter_increments(monkeypatch, tmp_path):
    blocker = tmp_path / "audit_dir_blocked"
    blocker.write_text("a FILE where the dir should be", encoding="utf-8")
    monkeypatch.setattr(cc, "AUDIT_DIR", blocker)  # mkdir → OSError
    fails = tmp_path / "claude_audit_failures.json"
    monkeypatch.setattr(cc, "AUDIT_FAILURES_FILE", fails)

    cc._audit_log({"status": "x"})  # must not raise
    data = json.loads(fails.read_text(encoding="utf-8"))
    assert data["count"] == 1
    assert "last_ts" in data
    assert "last_err" in data and data["last_err"]

    cc._audit_log({"status": "x"})
    assert json.loads(fails.read_text(encoding="utf-8"))["count"] == 2


# ── Spec 13: meta_out signature compatibility ──


def test_call_claude_cli_meta_out_is_optional_final_param():
    sig = inspect.signature(cc.call_claude_cli)
    params = list(sig.parameters.values())
    assert params[-1].name == "meta_out"
    assert params[-1].default is None
    # The 4 other call sites' shapes still bind WITHOUT meta_out:
    sig.bind("p")  # minimal
    sig.bind("p", "s")  # advisor-style
    sig.bind("p", "s", "model", 60, "max")  # call_claude wrapper (positional)
    sig.bind("p", system_prompt="s", model="m", timeout=30, effort="low")


def test_call_claude_wrapper_unaffected(monkeypatch, tmp_path):
    _patch_audit_paths(monkeypatch, tmp_path)
    _fake_cli_success(monkeypatch, "hello world")
    assert cc.call_claude("p") == "hello world"


def test_call_claude_cli_without_meta_out_unchanged(monkeypatch, tmp_path):
    _patch_audit_paths(monkeypatch, tmp_path)
    _fake_cli_success(monkeypatch, "plain")
    assert cc.call_claude_cli("p", "s") == "plain"
