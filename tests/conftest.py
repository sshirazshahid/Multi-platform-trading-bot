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
    yield
    cw._default = None


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
