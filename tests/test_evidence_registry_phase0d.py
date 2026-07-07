"""Phase 0d — EvidenceRegistry per-strategy ledger (S1-S5).

Pins the three acceptance bullets:
  * the registry holds S1-S5 stub records after init;
  * re-registering a fingerprint already marked NO_EDGE without a new-info flag
    is refused with the PRIOR failure_reason (and a new-info flag lets it through);
  * the per-strategy honest trial-count N is read into accept()'s DSR.
"""
from __future__ import annotations

import json
import sys

import numpy as np
import pytest

import config  # noqa: F401 -- ensure sys.modules["config"] exists for monkeypatch
from core.decision import promotion_loop as pl
from core.decision.promotion_loop import NoEdgeReplayError


def _paper(monkeypatch):
    monkeypatch.setattr(sys.modules["config"], "OPERATING_MODE", "PAPER", raising=False)


def test_init_holds_s1_to_s5_stubs(monkeypatch, tmp_path):
    _paper(monkeypatch)
    reg_path = tmp_path / "active_strategies.json"
    reg = pl.init_evidence_registry(path=reg_path)
    strategies = reg["strategies"]
    assert set(strategies) >= {"S1", "S2", "S3", "S4", "S5"}
    for sid in ("S1", "S2", "S3", "S4", "S5"):
        rec = strategies[sid]
        # required ledger fields present
        for field in (
            "hypothesis", "rules", "data_sources", "data_depth", "cost_model",
            "cost_calibration_status", "test_window", "oos_metrics", "honest_n",
            "promotion_status", "failure_reason", "last_tested_ts",
            "universe_construction_method",
        ):
            assert field in rec, f"{sid} missing {field}"
    # persisted on disk and did not clobber legacy variants block
    on_disk = json.loads(reg_path.read_text())
    assert "strategies" in on_disk
    assert "variants" in on_disk


def test_no_edge_fingerprint_refused_without_new_info(monkeypatch, tmp_path):
    _paper(monkeypatch)
    reg_path = tmp_path / "active_strategies.json"
    pl.init_evidence_registry(path=reg_path)

    rules = {"signal": "ema_cross", "tf": "1h"}
    sources = ["binance_ohlcv"]
    cost = {"fee_bps": 6.0}
    fp = pl.compute_fingerprint(rules=rules, data_sources=sources, cost_model=cost)

    # First registration marks it NO_EDGE -> allowed.
    pl.register_evidence(
        "S1", rules=rules, data_sources=sources, cost_model=cost,
        promotion_status="NO_EDGE", failure_reason="IR 0.21 < 0.50",
        path=reg_path,
    )

    # Same fingerprint, no new-info flag -> refused with the PRIOR failure_reason.
    with pytest.raises(NoEdgeReplayError) as ei:
        pl.register_evidence(
            "S1", rules=rules, data_sources=sources, cost_model=cost,
            path=reg_path,
        )
    assert ei.value.fingerprint == fp
    assert "IR 0.21" in ei.value.failure_reason

    # With an explicit new-information-source flag -> allowed through.
    rec = pl.register_evidence(
        "S1", rules=rules, data_sources=sources, cost_model=cost,
        new_info_source="added on-chain mempool feed",
        promotion_status="testing", path=reg_path,
    )
    assert rec["promotion_status"] == "testing"


def test_honest_n_read_into_accept_dsr(monkeypatch, tmp_path):
    _paper(monkeypatch)
    reg_path = tmp_path / "active_strategies.json"
    pl.init_evidence_registry(path=reg_path)
    pl.register_evidence("S2", honest_n=137, promotion_status="testing", path=reg_path)

    assert pl.honest_n_for_strategy("S2", path=reg_path) == 137

    rng = np.random.default_rng(0)
    rets = rng.normal(0.5, 1.0, size=120).tolist()
    v = pl.accept_for_strategy(
        "S2", returns=rets, strategy_class="intraday",
        n_trials=3, trial_budget=10,
        resolved_paper_returns=[0.1] * 40, confirm_days=45,
        path=reg_path,
    )
    # honest N fed to DSR == max(n_trials, trial_budget, registry honest_n) == 137
    assert v["honest_n_dsr"] == 137
    assert v["sub_gates"]["dsr"]["n_trials"] == 137


# --- 2026-07-07 registry write fixes ----------------------------------------
# Found by audit: (1) run_f1_carry_paper passed a DIRECTORY as registry_path;
# _save_registry's write raised, carry_runner swallowed it, and every F1
# evidence write since Jul 2 silently did nothing. (2) _save_registry wrote
# non-atomically and load_registry silently stubbed corrupt files — together a
# wipe vector for variants and the NO_EDGE replay-refusal memory.


def test_save_registry_rejects_directory(monkeypatch, tmp_path):
    _paper(monkeypatch)
    with pytest.raises(ValueError, match="directory"):
        pl._save_registry({"variants": {}}, tmp_path)  # tmp_path IS a directory


def test_register_evidence_f1_shape_writes_file(monkeypatch, tmp_path):
    """The exact F1 carry call shape must land on disk and read back."""
    _paper(monkeypatch)
    reg_file = tmp_path / "active_strategies.json"
    pl.register_evidence(
        "F1",
        oos_metrics={"paper_cycles": 3, "paper_net_pnl": 1.25, "failed_leg_events": 0},
        promotion_status="lab_paper",
        path=reg_file,
    )
    assert reg_file.exists()
    reg = pl.load_registry(reg_file)
    assert "F1" in reg.get("variants", {}) or "F1" in reg.get("strategies", {})
    assert not reg_file.with_suffix(".json.tmp").exists()  # atomic tmp cleaned


def test_load_registry_preserves_corrupt_file_aside(tmp_path):
    """A corrupt registry must not be silently discarded: the bytes are moved
    aside (forensics) and an empty stub is returned for THIS read only."""
    reg_file = tmp_path / "active_strategies.json"
    reg_file.write_text("{not valid json", encoding="utf-8")
    out = pl.load_registry(reg_file)
    assert out == {"variants": {}, "updated_at": None}
    corrupt_copies = list(tmp_path.glob("active_strategies.json.corrupt-*"))
    assert corrupt_copies, "corrupt registry bytes must be preserved aside"
    assert corrupt_copies[0].read_text(encoding="utf-8") == "{not valid json"


def test_save_registry_content_is_valid_json(monkeypatch, tmp_path):
    _paper(monkeypatch)
    reg_file = tmp_path / "reg.json"
    pl._save_registry({"variants": {"X": {"status": "shadow"}}}, reg_file)
    data = json.loads(reg_file.read_text(encoding="utf-8"))
    assert data["variants"]["X"]["status"] == "shadow"
    assert data["updated_at"]  # stamped
