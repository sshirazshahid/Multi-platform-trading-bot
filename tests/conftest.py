"""Bot-suite conftest: PRODUCTION-WAREHOUSE ISOLATION GUARD (2026-07-10).

Found live: several tests construct the default Warehouse (directly or via
get_warehouse()) without chdir isolation, so running the suite from the repo
root wrote fixture rows into data/warehouse.sqlite — the PRODUCTION learning
substrate. The pollution was masked for months by idempotent fixed-key
re-inserts; it surfaced when a cleanup deleted the fixture rows and the next
run half-re-inserted them (UNIQUE failures + 12 synthetic trades corrupting
the live accuracy-band cohort measurement).

This autouse fixture makes the failure mode impossible suite-wide:
  * core.warehouse.WAREHOUSE_PATH -> a per-test tmp file, so every default
    construction lands outside data/ (explicit Warehouse(path=...) args are
    untouched — isolated tests keep their own tmp DBs);
  * the get_warehouse() singleton is reset before AND after each test, so no
    thread-local connection bleeds across tests or into production.

Tests must never rely on production data/warehouse.sqlite content; read-only
diagnostics of a real DB must pass an explicit path.
"""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _warehouse_isolation_guard(tmp_path, monkeypatch):
    import core.warehouse as cw

    monkeypatch.setattr(cw, "WAREHOUSE_PATH", tmp_path / "warehouse.sqlite")
    cw._default = None
    # 2026-07-11 (caught BY the pollution sentinel on its first run): the
    # path-anchoring fix (8bead2a) made health_watchdog's constructor default
    # `warehouse_path: Path = WAREHOUSE_PATH` absolute-to-repo, which broke
    # those tests' old chdir-based isolation — they wrote 52 fixture rows
    # into PRODUCTION. That default is a Python default ARGUMENT, bound once
    # at function-definition time — monkeypatching the module attribute
    # `hw.WAREHOUSE_PATH` afterward does NOT change it (and would only break
    # the legitimate static anchoring assertion in
    # test_entrypoint_paths_anchored.py). Override the bound default itself.
    import core.health_watchdog as hw

    _orig_hw_init = hw.HealthWatchdog.__init__

    def _hw_init(self, bot_engine, notifier=None, risk_manager=None,
                 warehouse_path=None):
        _orig_hw_init(self, bot_engine, notifier, risk_manager,
                      warehouse_path or (tmp_path / "warehouse.sqlite"))

    monkeypatch.setattr(hw.HealthWatchdog, "__init__", _hw_init)
    yield
    cw._default = None


@pytest.fixture(autouse=True, scope="session")
def _production_pollution_sentinel():
    """Loud tripwire (2026-07-11): if the suite ever pollutes the PRODUCTION
    warehouse again, FAIL the run instead of hiding for months.

    Signature check, not any-change (the live bot writes real trades during
    test runs): fixture rows carry synthetic pre-2026 ts_entry values
    (1000.0-era epochs, 2023-era ages) that no real trade of this bot can
    have. Asserted at session END so the per-test guards above get blamed
    first; this is the backstop.
    """
    yield
    import sqlite3
    from pathlib import Path

    prod = Path("data/warehouse.sqlite")
    if not prod.exists():
        return
    bound = 1767225600  # 2026-01-01 UTC — every real trade is newer
    try:
        conn = sqlite3.connect(f"file:{prod.as_posix()}?mode=ro", uri=True)
        n = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE ts_entry < ?", (bound,)
        ).fetchone()[0]
        conn.close()
    except sqlite3.Error:
        return  # locked/missing table — not this sentinel's business
    assert n == 0, (
        f"TEST POLLUTION: {n} synthetic pre-2026 trade rows found in the "
        f"PRODUCTION warehouse after this test session. A test escaped the "
        f"isolation guards — find it before it corrupts live measurement "
        f"(see tests/conftest.py history, 2026-07-10/11 incidents)."
    )


@pytest.fixture(autouse=True)
def _maker_state_isolation_guard(tmp_path, monkeypatch):
    """Maker-first state-file isolation (2026-07-11).

    The owner's .env sets MAKER_FIRST_PAPER_ENABLED=true, so any test that
    reaches open_position/_maker_first_boot with the DEFAULT state path
    (order_manager.py:291, cwd-relative data/pending_maker_entries.json)
    rewrites the PRODUCTION file — the boot sweep empties its pending map,
    wiping the live bot's persisted intents/counters mid-suite. Redirect the
    default at construction; tests that assign their own path keep theirs.
    """
    import core.order_manager as om_mod

    orig_init = om_mod.OrderManager.__init__

    def _patched(self, *a, **k):
        orig_init(self, *a, **k)
        self._pending_maker_path = tmp_path / "pending_maker_entries.json"

    monkeypatch.setattr(om_mod.OrderManager, "__init__", _patched)
    yield


@pytest.fixture(autouse=True)
def _mutable_runtime_state_isolation_guard(tmp_path, monkeypatch):
    """Redirect mutable default artifacts away from the running bot.

    A test suite is an untrusted writer. Several otherwise-correct unit tests
    constructed production classes from the repository root and consequently
    rewrote real close counters, post-mortems, spot state, decision logs and
    prediction evidence. Patch injectable defaults before test constructors
    run; explicit paths supplied by a test remain authoritative.
    """
    data_dir = tmp_path / "data"

    import core.order_manager as om

    monkeypatch.setattr(om, "ORDER_MODE_STATE_PATH", data_dir / "order_mode_state.json")
    monkeypatch.setattr(om, "SL_WIDENED_STATE_PATH", data_dir / "sl_widened.json")
    monkeypatch.setattr(om, "PAPER_FUNDING_WINDOWS_PATH", data_dir / "paper_funding_windows.json")
    monkeypatch.setattr(om, "PENDING_MAKER_ENTRIES_PATH", data_dir / "pending_maker_entries.json")
    monkeypatch.setattr(om, "CLOSE_FAIL_COUNT_PATH", data_dir / "close_fail_count.json")

    import core.position_tracker as pt

    monkeypatch.setattr(pt.PositionTracker, "SAVE_PATH", data_dir / "positions.json")
    monkeypatch.setattr(pt.PositionTracker, "BACKUP_PATH", data_dir / "positions.json.bak")

    import core.post_mortem as pm
    import core.auto_mutator as am

    monkeypatch.setattr(pm, "SAVE_PATH", data_dir / "post_mortem.json")
    monkeypatch.setattr(am, "POST_MORTEM_FILE", data_dir / "post_mortem.json")
    monkeypatch.setattr(am, "MUTATIONS_FILE", data_dir / "auto_mutations.json")

    import core.trailing_stop_manager as tsm

    monkeypatch.setattr(tsm, "PEAKS_FILE", data_dir / "trailing_peaks.json")
    monkeypatch.setattr(tsm, "PARAMS_FILE", data_dir / "trailing_params.json")

    import core.journal as journal

    monkeypatch.setattr(journal, "_JOURNAL_DIR", tmp_path / "journal")

    import core.spot_manager as spot

    monkeypatch.setattr(spot, "STATE_FILE", data_dir / "spot_portfolio.json")
    monkeypatch.setattr(spot, "RECOMMENDATIONS_FILE", data_dir / "spot_recommendations.jsonl")
    monkeypatch.setattr(spot, "S1_REGIME_STATE_FILE", data_dir / "s1_regime_state.json")

    import core.mcp_brain as mb

    monkeypatch.setattr(mb, "DECISION_LOG", data_dir / "mcp_decisions.jsonl")
    monkeypatch.setattr(mb, "ACCURACY_FILE", data_dir / "mcp_accuracy.json")
    monkeypatch.setattr(mb, "STATE_FILE", data_dir / "mcp_state.json")

    import core.virtual_wallet as vw
    import core.blacklist_manager as bm
    import core.compliance_logger as cl
    import core.smart_executor as se
    import core.capital_allocator as ca
    import core.probability_calibrator as pc
    import core.knowledge_model as km
    import core.learning_engine as le
    import core.machine_signal as ms

    monkeypatch.setattr(vw, "WALLET_FILE", data_dir / "virtual_wallet.json")
    monkeypatch.setattr(bm, "DEFAULT_BLACKLIST_FILE", data_dir / "blacklist.json")
    monkeypatch.setattr(cl, "COMPLIANCE_DIR", data_dir / "compliance")
    monkeypatch.setattr(se, "EXEC_STATS_FILE", data_dir / "execution_stats.json")
    monkeypatch.setattr(ca, "STATE_FILE", data_dir / "capital_allocator.json")
    monkeypatch.setattr(ca, "RECOMMENDATIONS_FILE", data_dir / "allocation_recommendations.jsonl")
    monkeypatch.setattr(pc, "SAVE_PATH", data_dir / "calibration.json")
    monkeypatch.setattr(pc, "ISO_PATH", data_dir / "calibration_iso.pkl")
    monkeypatch.setattr(pc, "PAIRS_PATH", data_dir / "calibration_pairs.json")
    monkeypatch.setattr(km, "MODEL_FILE", data_dir / "knowledge_model.json")
    monkeypatch.setattr(km, "BACKUP_FILE", data_dir / "knowledge_model.bak.json")
    monkeypatch.setattr(le, "POSITIONS_FILE", data_dir / "positions.json")
    monkeypatch.setattr(le, "LEARNING_FILE", data_dir / "learning_report.json")
    monkeypatch.setattr(le, "REPORT_HTML_FILE", data_dir / "learning_report.html")
    monkeypatch.setattr(ms, "MACHINE_DECISION_LOG", data_dir / "machine_decisions.jsonl")
    yield


@pytest.fixture(autouse=True)
def _kill_switch_isolation_guard(tmp_path, monkeypatch):
    """Unit tests must not inherit the operator's real containment latch."""
    import core.kill_switch as ks

    path = tmp_path / "KILL_SWITCH"
    monkeypatch.setattr(ks, "KILL_SWITCH_PATH", path)
    monkeypatch.setattr(ks, "_default", ks.KillSwitch())
    yield


@pytest.fixture(autouse=True)
def _risk_state_isolation_guard(tmp_path, monkeypatch):
    """Never let a unit test overwrite the running bot's risk ledger.

    RiskManager historically used a cwd-relative ``data/risk_state.json``.
    Tests that instantiated it without changing cwd therefore loaded and
    rewrote production state.  Redirect every mutable risk artifact to the
    per-test directory; tests that chdir to ``tmp_path`` and explicitly seed
    ``data/risk_state.json`` continue to address the same file.
    """
    import core.risk_manager as rm

    data_dir = tmp_path / "data"
    monkeypatch.setattr(rm, "RISK_STATE_PATH", data_dir / "risk_state.json")
    monkeypatch.setattr(rm, "_REVIEW_FLAG_PATH", data_dir / "review_required.json")
    monkeypatch.setattr(rm, "_INCIDENT_LATCH_PATH", data_dir / "risk_incident_latch.json")
    monkeypatch.setattr(rm, "_INCIDENT_HISTORY_PATH", data_dir / "risk_incident_history.jsonl")
    yield
