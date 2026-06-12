"""TD-2 (tasks/plan_provenance_bundle_2026-06-12.md): atomic persistence for
knowledge_model.json / trailing_peaks.json via the shared utils.atomic_io helper.

A simulated kill mid-write (exception raised inside the tmp-file write, BEFORE
os.replace runs) must leave the original target file intact and parseable.
Pattern mirrors the proven one in core/risk_manager._save_state (lines 212-215).
All file IO is confined to tmp_path.
"""

import json
from pathlib import Path

import pytest

# Representative payload shapes (synthetic values).
KM_STYLE = {  # knowledge_model.json (subset of real shape)
    "dry_run_trades": 7,
    "live_trades": 0,
    "hour_scores": {
        "3": {
            "trades": 2,
            "wins": 1,
            "win_rate": 50.0,
            "net_pnl": 1.23,
            "avg_pnl": 0.615,
            "total_fees": 0.1,
        }
    },
}
PEAKS_STYLE = {  # trailing_peaks.json shape: position_id -> peak record
    "BTC/USDT:USDT_1999999999": {"peak_pnl": 0.034, "activated": True},
}


def _install_exploding_write(monkeypatch):
    """Make every Path.write_text crash partway through.

    Simulates a process kill mid-write: partial bytes land on disk, then the
    exception fires before any os.replace can run. Installed AFTER seeding so
    only the save-under-test is affected.
    """
    original = Path.write_text

    def boom(self, data, *args, **kwargs):
        with open(self, "w", encoding="utf-8") as fh:
            fh.write(data[: max(1, len(data) // 3)])
        raise OSError("simulated kill mid-write")

    monkeypatch.setattr(Path, "write_text", boom)
    return original


# ── direct helper behavior ───────────────────────────────────────────────────


def test_atomic_write_text_writes_content(tmp_path):
    from utils.atomic_io import atomic_write_text

    target = tmp_path / "out.txt"
    atomic_write_text(target, "hello world")
    assert target.read_text(encoding="utf-8") == "hello world"


def test_atomic_write_json_round_trips(tmp_path):
    from utils.atomic_io import atomic_write_json

    target = tmp_path / "out.json"
    atomic_write_json(target, KM_STYLE, indent=2, default=str)
    assert json.loads(target.read_text(encoding="utf-8")) == KM_STYLE


@pytest.mark.parametrize("payload", [KM_STYLE, PEAKS_STYLE])
def test_kill_mid_write_preserves_original(tmp_path, monkeypatch, payload):
    from utils.atomic_io import atomic_write_json

    target = tmp_path / "state.json"
    target.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    _install_exploding_write(monkeypatch)
    with pytest.raises(OSError):
        atomic_write_json(target, {"clobbered": True})

    # Original file untouched and still parseable.
    assert json.loads(target.read_text(encoding="utf-8")) == payload


# ── module save paths use the helper ─────────────────────────────────────────


def test_knowledge_model_save_is_atomic(tmp_path, monkeypatch):
    import core.knowledge_model as km_mod

    model_file = tmp_path / "knowledge_model.json"
    model_file.write_text(json.dumps({"dry_run_trades": 42}), encoding="utf-8")
    monkeypatch.setattr(km_mod, "MODEL_FILE", model_file)
    monkeypatch.setattr(km_mod, "BACKUP_FILE", tmp_path / "knowledge_model.bak.json")

    km = km_mod.KnowledgeModel()
    km._save()  # happy path: atomic write lands and parses
    assert json.loads(model_file.read_text(encoding="utf-8"))["dry_run_trades"] == 42

    _install_exploding_write(monkeypatch)
    km._model["dry_run_trades"] = 99999
    km._save()  # _save swallows the simulated kill (logs it)

    after = json.loads(model_file.read_text(encoding="utf-8"))  # parseable, not torn
    assert after["dry_run_trades"] == 42  # pre-kill content survives


def test_trailing_peaks_save_is_atomic(tmp_path, monkeypatch):
    import core.trailing_stop_manager as tsm

    peaks_file = tmp_path / "trailing_peaks.json"
    peaks_file.write_text(json.dumps(PEAKS_STYLE, indent=2), encoding="utf-8")
    monkeypatch.setattr(tsm, "PEAKS_FILE", peaks_file)
    monkeypatch.setattr(tsm, "PARAMS_FILE", tmp_path / "trailing_params.json")

    mgr = tsm.TrailingStopManager()
    assert mgr._tracking == PEAKS_STYLE

    mgr._save_peaks()  # happy path: atomic write lands and parses
    assert json.loads(peaks_file.read_text(encoding="utf-8")) == PEAKS_STYLE

    _install_exploding_write(monkeypatch)
    mgr._tracking["NEW_POS_999"] = {"peak_pnl": 0.5}
    mgr._save_peaks()  # _save_peaks swallows the simulated kill (logs it)

    after = json.loads(peaks_file.read_text(encoding="utf-8"))  # parseable, not torn
    assert after == PEAKS_STYLE  # pre-kill content survives
