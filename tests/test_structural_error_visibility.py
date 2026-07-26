"""Structural failures must be LOUD, never swallowed at DEBUG.

Two committed-at-HEAD silent-failure classes, both verified 2026-07-26:

1. ``BotEngine._check_all_sl_tp`` runs ``order_manager.check_sl_tp`` per
   exchange x market in a thread pool.  Its ``_check_one`` closure caught
   every ``Exception`` and logged it at DEBUG, so an ``ImportError`` from a
   consumer/producer skew (HEAD's ``order_manager`` imports
   ``load_realized_settlements``, which HEAD's ``funding_history`` does not
   define) killed the whole paper SL/TP monitor tick invisibly.  Worse, the
   ``f.result()  # Propagate any unhandled exception`` rail was structurally
   disarmed: a bare ``except Exception`` means the future never holds one.
   Operational failures (a venue timing out) must STILL be isolated and
   quiet — one venue's outage may not abort the others.

2. ``ShadowPredictor._ensure_model`` logs every disable path at WARNING
   except the missing-artifact path, which logged at DEBUG.  Since
   ``LATEST_MODEL`` points at ``data/models/lr_v_latest.pkl`` — a filename
   nothing in the repo writes — that DEBUG path is the ONLY one ever taken,
   so the LR size multiplier has been silently pinned at 1.0x with no
   operator-visible signal.
"""
from __future__ import annotations

import threading

import pytest

from core.bot_engine import BotEngine


@pytest.fixture
def loguru_records():
    """Collect (level_name, message) for every loguru record emitted."""
    from loguru import logger as loguru_logger

    records: list[tuple[str, str]] = []

    def _sink(message):
        rec = message.record
        records.append((rec["level"].name, rec["message"]))

    handler_id = loguru_logger.add(_sink, level=0, format="{message}")
    yield records
    try:
        loguru_logger.remove(handler_id)
    except ValueError:
        pass


class _Tracker:
    def __init__(self, n=1):
        self._n = n

    def count_open(self):
        return self._n


class _OrderMgr:
    """order_manager double whose check_sl_tp raises a chosen exception."""

    def __init__(self, exc):
        self._exc = exc
        self.calls = 0
        self._pending_maker = {}

    def check_sl_tp(self, exchange, market_type):
        self.calls += 1
        raise self._exc


def _engine(exc):
    eng = BotEngine.__new__(BotEngine)
    eng.tracker = _Tracker()
    eng.order_mgr = _OrderMgr(exc)
    eng.active_exchanges = {"binance": object()}
    return eng


# ── Finding 1: SL/TP monitor structural-error visibility ────────────────
def test_import_error_in_sl_tp_check_is_logged_at_error(loguru_records):
    """The real HEAD repro: order_manager imports a name funding_history
    does not define.  This must surface at ERROR, not DEBUG."""
    eng = _engine(
        ImportError(
            "cannot import name 'load_realized_settlements' "
            "from 'core.funding_history'"
        )
    )
    with pytest.raises(ImportError):
        eng._check_all_sl_tp()

    errors = [m for lvl, m in loguru_records if lvl == "ERROR"]
    assert any("load_realized_settlements" in m for m in errors), (
        "a structural ImportError inside the paper SL/TP monitor must be "
        f"logged at ERROR; got records={loguru_records}"
    )


def test_structural_error_propagates_through_future_result():
    """`f.result()  # Propagate any unhandled exception` must be able to
    fire — a bare `except Exception` in the worker disarms it entirely."""
    for exc in (
        ImportError("no such name"),
        AttributeError("'NoneType' object has no attribute 'x'"),
        NameError("name '_foo' is not defined"),
    ):
        eng = _engine(exc)
        with pytest.raises(type(exc)):
            eng._check_all_sl_tp()


def test_operational_error_stays_isolated_and_quiet(loguru_records):
    """Per-venue isolation is load-bearing (the maker-first resolver needs
    one venue's outage not to abort the others) — operational failures must
    NOT propagate and must NOT be promoted to ERROR."""
    eng = _engine(TimeoutError("binance read timeout"))
    eng._check_all_sl_tp()  # must not raise

    assert not [lvl for lvl, _ in loguru_records if lvl == "ERROR"], (
        "an operational venue failure must stay a quiet, isolated DEBUG "
        f"line; got {loguru_records}"
    )


class _OneShotStop:
    """stop_event double: one loop iteration, then stop."""

    def __init__(self):
        self._stopped = False
        self.waits = 0

    def is_set(self):
        return self._stopped

    def wait(self, _timeout):
        self.waits += 1
        self._stopped = True


def test_monitor_loop_logs_structural_error_at_error_and_survives(
    loguru_records, monkeypatch
):
    """The thread-top-level handler must not silently downgrade a
    structural failure — but it must also NOT re-raise, or the 10s monitor
    dies for the rest of the process lifetime."""
    eng = BotEngine.__new__(BotEngine)

    def _boom():
        raise ImportError("cannot import name 'load_realized_settlements'")

    monkeypatch.setattr(eng, "_check_all_sl_tp", _boom, raising=False)
    stop = _OneShotStop()
    eng._sltp_monitor_loop(stop)  # must not raise

    assert stop.waits == 1, "the monitor loop must keep running after the error"
    errors = [m for lvl, m in loguru_records if lvl == "ERROR"]
    assert any("load_realized_settlements" in m for m in errors), (
        "a structural failure escaping the pool must be logged at ERROR by "
        f"the monitor thread; got {loguru_records}"
    )


def test_monitor_loop_keeps_operational_errors_quiet(loguru_records, monkeypatch):
    eng = BotEngine.__new__(BotEngine)

    def _boom():
        raise TimeoutError("venue timeout")

    monkeypatch.setattr(eng, "_check_all_sl_tp", _boom, raising=False)
    eng._sltp_monitor_loop(_OneShotStop())

    assert not [lvl for lvl, _ in loguru_records if lvl == "ERROR"]


def test_monitor_loop_signature_still_accepts_a_real_event():
    """Guard against the doubles above drifting from the real caller."""
    eng = BotEngine.__new__(BotEngine)
    ev = threading.Event()
    ev.set()
    eng._sltp_monitor_loop(ev)  # already set -> returns immediately


# ── Finding 2: ShadowPredictor missing-artifact path is the only one
#    ever taken, and it was the only one logged below WARNING ────────────
def test_missing_model_artifact_is_logged_at_warning(loguru_records, tmp_path):
    """`LATEST_MODEL` names an artifact nothing in the repo writes, so this
    is the disable path taken on every real boot.  Its siblings all log at
    WARNING; this one logged at DEBUG, hiding a permanently inert LR size
    multiplier from the operator."""
    import core.shadow_predictor as sp

    missing = tmp_path / "models" / "definitely_absent.pkl"
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sp, "LATEST_MODEL", missing)
    try:
        pred = sp.ShadowPredictor()
        assert pred._ensure_model() is False
    finally:
        monkey.undo()

    assert pred.disabled_reason and "not found" in pred.disabled_reason
    warns = [m for lvl, m in loguru_records if lvl == "WARNING"]
    assert any("definitely_absent.pkl" in m for m in warns), (
        "the missing-artifact disable path must log at WARNING like every "
        f"other disable path in _ensure_model; got {loguru_records}"
    )


def test_missing_model_warning_fires_once_per_process(loguru_records, tmp_path):
    """`_load_attempted` short-circuits, so the warning must not spam every
    entry decision."""
    import core.shadow_predictor as sp

    missing = tmp_path / "models" / "definitely_absent.pkl"
    monkey = pytest.MonkeyPatch()
    monkey.setattr(sp, "LATEST_MODEL", missing)
    try:
        pred = sp.ShadowPredictor()
        for _ in range(3):
            pred._ensure_model()
    finally:
        monkey.undo()

    warns = [m for lvl, m in loguru_records if "definitely_absent.pkl" in m]
    assert len(warns) == 1, f"expected exactly one warning, got {warns}"
