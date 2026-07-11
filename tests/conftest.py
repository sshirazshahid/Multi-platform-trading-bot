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
