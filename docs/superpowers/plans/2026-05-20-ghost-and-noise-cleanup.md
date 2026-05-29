# Ghost Path Accuracy + Log Cleanup + Small-TP Capture — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use `superpowers:subagent-driven-development` (recommended) or `superpowers:executing-plans` to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Five narrow improvements to the trading bot — sharper ghost-path PnL accuracy, cleaner logs, safer cancel-race handling, age-aware SL-to-breakeven tightening, and deterministic small-TP capture at +1% gain.

**Architecture:** All changes layer on top of the existing polling architecture (`schedule.every()...` + `position_tracker.sync_with_exchanges` + `_run_mcp_position_monitor`). No async refactor, no WebSocket dependency, no architectural change. Each area is gated by a config flag for instant rollback.

**Tech Stack:** Python 3.9+, ccxt 4.4+, loguru, pytest, existing `core/*` modules.

**Branch:** `feat/profitability-upgrade` (HEAD `8984018` — design spec). Bot is running `CONTROLLED_LIVE` and will pick up the changes at next user-initiated restart.

**Spec:** `docs/superpowers/specs/2026-05-20-ghost-and-noise-cleanup-design.md`

---

## File Map

| File | Type | Changes |
|---|---|---|
| `config.py` | Modify | Add 8 new flags (GHOST_LEDGER_WINDOW_H, GHOST_PENDING_REQUEUE, AGE_AWARE_SL_*, AUTO_SMALL_TP_*) |
| `core/position_tracker.py` | Modify | Area 1: extend ledger window 6h→24h, add `_pending_ghost_reconcile` map for two-pass retry, fall back to mark_price |
| `core/order_manager.py` | Modify | Area 2: demote `_verify_order_on_exchange` Bybit `fetchOrder` warnings to DEBUG |
| `core/bot_engine.py` | Modify | Area 2: demote first-attempt health failures to DEBUG. Areas 4+5: new `_maybe_capture_small_tp` and `_maybe_tighten_aged_position` methods + wire into `_run_mcp_position_monitor` |
| `exchanges/base.py` | Modify | Area 2: silence ticker retry warnings + ccxt acknowledged param. Area 3: rewrite `cancel_order` to swallow known race errors |
| `tests/test_ghost_and_noise_cleanup.py` | Create | 14 new tests covering all 5 areas |

---

## Task 1: Add config flags

**Files:**
- Modify: `config.py` (append new section)

- [ ] **Step 1: Read current config tail to find a good insertion point**

Run: `grep -n "^[A-Z_]* = " config.py | tail -10`

- [ ] **Step 2: Add 8 new config constants in a new section**

Append to `config.py` (before any trailing comments / sentinels):

```python
# ==============================================================
# 2026-05-20 GHOST + NOISE CLEANUP + SMALL-TP CAPTURE
# Per-area kill switches for the five-area improvement set.
# Spec: docs/superpowers/specs/2026-05-20-ghost-and-noise-cleanup-design.md
# ==============================================================

# Area 1 — Ghost path accuracy
GHOST_LEDGER_WINDOW_H = 24       # was 6; widen to catch lagged ledger writes
GHOST_PENDING_REQUEUE = True     # two-pass reconcile: ghost_sync upgrades on next sync

# Area 4 — Age-aware SL→breakeven tightening
AGE_AWARE_SL_ENABLED      = True
AGE_AWARE_SL_MIN_AGE_MIN  = 60   # fire at age >= 60 min
# Profit band [low, high) — exclusive on high to leave [1%, 2%) to Area 5
AGE_AWARE_SL_MIN_PNL_FRAC = 0.0001  # > 0 (strictly in profit)
AGE_AWARE_SL_MAX_PNL_FRAC = 0.02    # < 2% (trailing stop owns above)

# Area 5 — Deterministic small-TP capture
AUTO_SMALL_TP_ENABLED        = True
AUTO_SMALL_TP_MIN_AGE_MIN    = 30   # fire at age >= 30 min
AUTO_SMALL_TP_MIN_PNL_FRAC   = 0.01 # >= +1.0%
AUTO_SMALL_TP_MAX_PNL_FRAC   = 0.02 # < +2% (trailing stop owns above)
```

- [ ] **Step 3: Verify imports work**

Run: `python -c "from config import GHOST_LEDGER_WINDOW_H, GHOST_PENDING_REQUEUE, AGE_AWARE_SL_ENABLED, AGE_AWARE_SL_MIN_AGE_MIN, AGE_AWARE_SL_MIN_PNL_FRAC, AGE_AWARE_SL_MAX_PNL_FRAC, AUTO_SMALL_TP_ENABLED, AUTO_SMALL_TP_MIN_AGE_MIN, AUTO_SMALL_TP_MIN_PNL_FRAC, AUTO_SMALL_TP_MAX_PNL_FRAC; print('all 10 flags imported OK')"`

Expected: `all 10 flags imported OK`

- [ ] **Step 4: Commit**

```bash
git add config.py
git commit -m "feat(config): add 8 flags for ghost-cleanup + age-aware SL + small-TP capture

Per-area kill switches for the five-area improvement set per spec
2026-05-20-ghost-and-noise-cleanup-design.md. All default to enabled
values. Toggle any to disable that single area without rebuild.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 2: Area 1 — Ghost path accuracy (3 sub-changes)

**Files:**
- Modify: `core/position_tracker.py:638-676` (ghost reconcile block)
- Modify: `core/position_tracker.py:289` area (add `_pending_ghost_reconcile` instance attr)
- Test: `tests/test_ghost_and_noise_cleanup.py` (new file)

### 2a — Test scaffolding + failing tests

- [ ] **Step 1: Create the test file with the 3 Area 1 tests, all expected to FAIL**

Create `tests/test_ghost_and_noise_cleanup.py`:

```python
"""Tests for 2026-05-20 ghost-cleanup + age-aware SL + small-TP capture spec.

Covers 5 areas:
  1. Ghost path accuracy (24h window, two-pass reconcile, mark_price fallback)
  2. Log noise cleanup (demote 5 informational warnings)
  3. Reliability hardening (_safe_cancel_order swallows 110001/40034)
  4. Age-aware SL→BE tightening (fires at age>=60min, pnl in [0%, 1%))
  5. Deterministic small-TP capture (fires at age>=30min, pnl in [1%, 2%))
"""
from __future__ import annotations

import logging
import time
from unittest.mock import MagicMock, patch

import pytest

# Import once at module load so loguru sinks set by tests below survive
# downstream re-imports inside the test bodies.
from core import position_tracker as pt  # noqa: E402


@pytest.fixture
def caplog(caplog):
    """Bridge loguru -> stdlib so pytest's caplog can capture log lines.

    Loguru bypasses stdlib logging entirely. Add a sink that re-emits each
    loguru record via a stdlib logger which propagates to root.
    """
    from loguru import logger as loguru_logger

    py_logger = logging.getLogger("tests.loguru_bridge")
    py_logger.setLevel(logging.DEBUG)
    py_logger.propagate = True

    def _sink(message):
        record = message.record
        py_level = getattr(logging, record["level"].name, logging.INFO)
        py_logger.log(py_level, record["message"])

    handler_id = loguru_logger.add(_sink, level=0, format="{message}")
    caplog.set_level(logging.DEBUG, logger="tests.loguru_bridge")
    yield caplog
    loguru_logger.remove(handler_id)


# ---------------------------------------------------------------------------
# AREA 1 — Ghost path accuracy
# ---------------------------------------------------------------------------


def test_ghost_ledger_lookup_window_is_24h():
    """The since_ms argument passed to fetch_closed_pnl must reflect 24h, not 6h."""
    from config import GHOST_LEDGER_WINDOW_H

    assert GHOST_LEDGER_WINDOW_H == 24, (
        "Area 1 requires ledger lookup window of 24h; "
        f"config.GHOST_LEDGER_WINDOW_H={GHOST_LEDGER_WINDOW_H}"
    )


def test_ghost_sync_pending_then_reconciled(tmp_path, monkeypatch):
    """First sync returns ghost_sync (no ledger record); second sync finds
    the ledger record and upgrades the warehouse exit price to the real fill."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    tracker = pt.PositionTracker()
    p = pt.Position(
        id="TEST-GHOST-001",
        exchange="bybit",
        symbol="BNB/USDT:USDT",
        side="sell",
        size=0.1,
        entry_price=640.0,
        market_type="futures",
    )
    p.stop_loss = 645.0
    p.take_profit = 620.0
    p.open_time = time.time() - 3600  # opened 1h ago
    tracker._open[p.id] = p

    # First sync: ledger has NO matching record → ghost_sync expected
    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = []  # empty ledger
    fake_ex.fetch_ticker.return_value = {"last": 639.5, "info": {"markPrice": "639.2"}}
    fake_ex.name = "bybit"

    # First pass: no ledger, falls back to mark_price (per Area 1 §2.1 spec)
    tracker.sync_with_exchanges({"bybit": fake_ex})  # exchange has no position
    # Position should be pending — kept on a _pending_ghost_reconcile map
    assert "TEST-GHOST-001" in tracker._pending_ghost_reconcile

    # Second sync 15s later: ledger now has the fill record
    fake_ex.fetch_closed_pnl.return_value = [
        {
            "symbol": "BNB/USDT:USDT",
            "side": "sell",
            "exit_price": 644.5,
            "realized_pnl": -0.45,
            "ts": time.time() * 1000,
        }
    ]
    tracker.sync_with_exchanges({"bybit": fake_ex})

    # After second pass, the position is in _closed with ghost_reconciled
    closed = [c for c in tracker._closed if c.id == "TEST-GHOST-001"]
    assert len(closed) == 1, (
        f"expected position to be moved to _closed; "
        f"_pending={list(tracker._pending_ghost_reconcile.keys())} "
        f"_closed_ids={[c.id for c in tracker._closed]}"
    )
    assert closed[0].close_reason == "ghost_reconciled", (
        f"expected close_reason ghost_reconciled, got {closed[0].close_reason}"
    )
    assert abs(closed[0].exit_price - 644.5) < 0.01, (
        f"expected exit_price 644.5 (from ledger), got {closed[0].exit_price}"
    )


def test_ghost_fallback_uses_mark_price(tmp_path, monkeypatch):
    """When no ledger record exists, the fallback close price is taken from
    ticker.info.markPrice, NOT ticker.last (Area 1 §2.1 mark_price fallback)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()
    # Disable two-pass so the first sync finalizes immediately
    monkeypatch.setattr("config.GHOST_PENDING_REQUEUE", False)

    tracker = pt.PositionTracker()
    p = pt.Position(
        id="TEST-GHOST-002",
        exchange="binance",
        symbol="AAVE/USDT:USDT",
        side="sell",
        size=0.5,
        entry_price=88.0,
        market_type="futures",
    )
    p.stop_loss = 89.0
    p.open_time = time.time() - 3600
    tracker._open[p.id] = p

    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = []  # no ledger
    fake_ex.fetch_ticker.return_value = {
        "last": 87.50,                      # last-trade price
        "info": {"markPrice": "87.20"},     # exchange mark price (preferred)
    }
    fake_ex.name = "binance"

    tracker.sync_with_exchanges({"binance": fake_ex})

    closed = [c for c in tracker._closed if c.id == "TEST-GHOST-002"]
    assert len(closed) == 1
    # The Area 1 fix routes the fallback through mark_price (87.20), NOT
    # ticker.last (87.50). The 0.30 difference matters for PnL accuracy.
    assert abs(closed[0].exit_price - 87.20) < 0.01, (
        f"expected exit_price 87.20 (mark_price), got {closed[0].exit_price}"
    )
```

- [ ] **Step 2: Run the 3 tests and confirm they FAIL**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "ghost_ledger_lookup_window or ghost_sync_pending or ghost_fallback_uses_mark"`

Expected:
```
test_ghost_ledger_lookup_window_is_24h FAILED (AssertionError or AttributeError if config flag missing)
test_ghost_sync_pending_then_reconciled FAILED (AttributeError: _pending_ghost_reconcile)
test_ghost_fallback_uses_mark_price FAILED (exit_price uses ticker.last not markPrice)
```

### 2b — Implement Area 1 changes in `core/position_tracker.py`

- [ ] **Step 3: Add `_pending_ghost_reconcile` attribute to `__init__`**

Find the `PositionTracker.__init__` (around line 289). Locate the section that initializes attributes like `self._open = {}`. Add the new attribute next to those.

Read `core/position_tracker.py:289-320` to locate the exact line, then add:

```python
        # 2026-05-20 Area 1 — Two-pass ghost reconcile. First sync that
        # detects a missing position adds it here with the timestamp.
        # Second sync 15s later attempts ledger lookup again — most lagged
        # ledger writes appear within 15-30s of the actual fill.
        # Map: position_id -> first_seen_ts.
        self._pending_ghost_reconcile: dict[str, float] = {}
```

- [ ] **Step 4: Replace the ghost reconcile block in `sync_with_exchanges`**

Open `core/position_tracker.py:627-682`. Replace the entire block from the comment `# 2026-04-24: Reconcile ghost ...` through `self.close(pos.id, close_price, close_reason)` (inclusive) with the version below. This rewrites the ghost-handling section to honor `GHOST_LEDGER_WINDOW_H`, the `_pending_ghost_reconcile` two-pass map, and the mark_price fallback.

```python
            # 2026-04-24 / 2026-05-20: Reconcile ghost against exchange history
            # BEFORE falling back to ticker. The exchange ledger has the actual
            # SL/TP fill price — much more accurate than ticker.last.
            #
            # 2026-05-20 (Area 1): three improvements over the original code:
            #   1. Window widened from 6h → GHOST_LEDGER_WINDOW_H (24h default).
            #      Bitget and Bybit sometimes write ledger entries with several
            #      hours of lag; 24h captures these without bloating results.
            #   2. Two-pass reconcile: if the first sync finds no ledger record,
            #      record the position in _pending_ghost_reconcile and try again
            #      on the next sync cycle 15s later. Most lagged writes land in
            #      that 15-30s window. Only finalize as ghost_sync if BOTH the
            #      pending recheck AND the fresh attempt fail.
            #   3. Fallback price source is exchange mark_price (not ticker.last)
            #      when present. For SL/TP-triggered closes the trigger fires
            #      off mark price, so mark is closer to the actual fill.
            try:
                from config import GHOST_LEDGER_WINDOW_H, GHOST_PENDING_REQUEUE
            except ImportError:
                GHOST_LEDGER_WINDOW_H = 24
                GHOST_PENDING_REQUEUE = True

            reconciled = False
            if ex and hasattr(ex, "fetch_closed_pnl"):
                try:
                    since_ms = int((time.time() - GHOST_LEDGER_WINDOW_H * 3600) * 1000)
                    records = ex.fetch_closed_pnl(
                        since_ms=since_ms, symbol=pos.symbol) or []
                    exit_px, best = match_ghost_ledger_record(pos, records)
                    if exit_px > 0:
                        close_price = exit_px
                        reconciled = True
                        resolved_src = "exchange_ledger"
                        close_reason = "ghost_reconciled"
                        logger.info(
                            f"[Positions] GHOST reconciled via ledger: "
                            f"{pos.symbol} {pos.side.upper()} "
                            f"exit={exit_px:.6g} "
                            f"realized_pnl={best.get('realized_pnl') if best else None}"
                        )
                        # Clear from pending map if present
                        self._pending_ghost_reconcile.pop(pos.id, None)
                except Exception as e:
                    logger.debug(
                        f"[Positions] ghost reconcile lookup failed "
                        f"{pos.symbol}: {str(e)[:120]}")

            # If still not reconciled, decide between (a) parking for retry or
            # (b) finalizing now with the mark_price fallback.
            if not reconciled:
                pending_already = pos.id in self._pending_ghost_reconcile
                if GHOST_PENDING_REQUEUE and not pending_already:
                    # First time seeing this missing position — park it for the
                    # next sync cycle (15s later). Most lagged ledger writes land
                    # within that window. Do NOT close yet.
                    self._pending_ghost_reconcile[pos.id] = time.time()
                    logger.debug(
                        f"[Positions] GHOST pending reconcile: {pos.symbol} "
                        f"{pos.side.upper()} — will retry ledger in 15s")
                    continue  # skip this ghost for now; keep in _open
                else:
                    # Either two-pass disabled OR already pending → finalize now
                    # via mark_price fallback (preferred) or ticker.last.
                    if ex:
                        try:
                            ticker = ex.fetch_ticker(pos.symbol, pos.market_type)
                            info = ticker.get("info", {}) or {}
                            mark = info.get("markPrice") or info.get("mark")
                            try:
                                tp = float(mark) if mark else 0.0
                            except (TypeError, ValueError):
                                tp = 0.0
                            if tp <= 0:
                                tp = float(
                                    ticker.get("last") or ticker.get("close") or 0)
                            if tp > 0:
                                close_price = tp
                                resolved_src = (
                                    "mark_price_fallback" if mark
                                    else "ticker_fallback")
                        except Exception:
                            pass
                    self._pending_ghost_reconcile.pop(pos.id, None)

            # Demote routine GHOST detection to INFO when the reconcile path
            # produced a clean ledger match (Area 2 noise cleanup) — keep WARNING
            # only when we had to fall back to mark/ticker (genuine desync).
            log_fn = logger.info if reconciled else logger.warning
            log_fn(
                f"[Positions] GHOST close price source={resolved_src} "
                f"price={close_price:.6g}")
            self.close(pos.id, close_price, close_reason)
```

Note: this block sits inside the existing `for pos in ghosts:` loop. The `continue` on the pending path keeps the position in `_open` for the next sync cycle.

- [ ] **Step 5: Run the 3 Area 1 tests — they should PASS**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "ghost_ledger_lookup_window or ghost_sync_pending or ghost_fallback_uses_mark"`

Expected: 3 passed.

- [ ] **Step 6: Run the broader position_tracker suite to confirm no regression**

Run: `pytest tests/test_ghost_reconcile.py tests/test_ghost_reroute_instrument.py tests/test_phantom_ghost_pending.py -q`

Expected: all pass (or all-pass + known skips; no new failures).

- [ ] **Step 7: Commit**

```bash
git add config.py core/position_tracker.py tests/test_ghost_and_noise_cleanup.py
git commit -m "feat(ghost): Area 1 — 24h ledger window + two-pass reconcile + mark_price

Three improvements to position_tracker.sync_with_exchanges ghost handler:
  - Widen fetch_closed_pnl window 6h -> 24h (config GHOST_LEDGER_WINDOW_H).
    Lagged ledger writes on Bybit/Bitget sometimes take >6h to appear.
  - Two-pass pending reconcile: first sync that detects a missing position
    parks it in _pending_ghost_reconcile; next sync 15s later retries the
    ledger lookup. Most lagged writes land within that window, so the close
    upgrades from ghost_sync -> ghost_reconciled with the real fill price.
  - Fallback uses exchange mark_price (when ticker.info.markPrice present)
    instead of ticker.last. SL/TP-triggered closes evaluate against mark,
    so mark is closer to the actual fill than the last-trade price.

Demotes the GHOST close-price-source log to INFO when reconciled cleanly
(Area 2 noise cleanup partial — full Area 2 in next commit).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 3: Area 2 — Log noise cleanup (5 demotions)

**Files:**
- Modify: `core/order_manager.py` (line ~387 — `_verify_order_on_exchange` Bybit fetchOrder warning)
- Modify: `core/bot_engine.py:2856` (first-attempt health failure)
- Modify: `exchanges/base.py:182` (ticker retry warning) + `exchanges/base.py:306` (cancel_order ERROR)
- Modify: `core/position_tracker.py` (GHOST detected WARNING — handled at Task 2 already; verify here)
- Test: `tests/test_ghost_and_noise_cleanup.py` (append 2 tests)

### 3a — Test scaffolding + failing tests

- [ ] **Step 1: Append 2 log-level tests to the test file**

Append to `tests/test_ghost_and_noise_cleanup.py`:

```python
# ---------------------------------------------------------------------------
# AREA 2 — Log noise cleanup
# ---------------------------------------------------------------------------


def test_ghost_detected_at_info_level_when_reconciled(tmp_path, monkeypatch, caplog):
    """When a ghost reconciles cleanly via the ledger path, the price-source
    log line should be at INFO level, not WARNING (Area 2 demotion)."""
    monkeypatch.chdir(tmp_path)
    (tmp_path / "data").mkdir()

    tracker = pt.PositionTracker()
    p = pt.Position(
        id="TEST-AREA2-001",
        exchange="bybit",
        symbol="BCH/USDT:USDT",
        side="sell",
        size=0.05,
        entry_price=370.0,
        market_type="futures",
    )
    p.open_time = time.time() - 3600
    tracker._open[p.id] = p

    fake_ex = MagicMock()
    fake_ex.fetch_closed_pnl.return_value = [
        {
            "symbol": "BCH/USDT:USDT",
            "side": "sell",
            "exit_price": 369.5,
            "realized_pnl": 0.025,
            "ts": time.time() * 1000,
        }
    ]
    fake_ex.name = "bybit"

    caplog.clear()
    tracker.sync_with_exchanges({"bybit": fake_ex})

    # The "GHOST close price source=" log line must be at INFO when reconciled.
    price_source_records = [
        r for r in caplog.records
        if "GHOST close price source" in r.message
    ]
    assert price_source_records, "expected the GHOST close price source log line"
    assert price_source_records[0].levelname == "INFO", (
        f"expected INFO when reconciled, got {price_source_records[0].levelname}"
    )


def test_bybit_110001_cancel_logged_at_debug(monkeypatch, caplog):
    """Bybit 110001 'order not exists or too late to cancel' is the race-
    condition expected case (the order already filled). It should log at
    DEBUG, NOT ERROR (Area 2 demotion)."""
    import ccxt
    from exchanges import base as base_mod

    # Build a tiny fake BaseExchange that throws ccxt-style 110001
    class _FakeExchange:
        id = "bybit"
        def __init__(self):
            self.name = "bybit"

        def cancel_order(self, order_id, symbol, params=None):
            raise ccxt.InvalidOrder(
                'bybit {"retCode":110001,"retMsg":"order not exists or too late to cancel"}'
            )

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "bybit"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeExchange()
    fake_be._futures_params = lambda: {}

    caplog.clear()
    # cancel_order is bound on the class; call the real one with our fake
    result = base_mod.BaseExchange.cancel_order(
        fake_be, "ABC-123", "BNB/USDT:USDT", "futures"
    )

    # Result should be a clean {} (no exception leaks out)
    assert result == {}, f"expected swallowed cancel to return {{}}, got {result}"
    # No ERROR-level log allowed for known-race 110001 (DEBUG/INFO are fine)
    error_records = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "cancel_order" in r.message
    ]
    assert not error_records, (
        f"110001 cancel race must not log at ERROR. Found: "
        f"{[r.message for r in error_records]}"
    )
```

- [ ] **Step 2: Run the 2 new tests; both should FAIL**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "ghost_detected_at_info or bybit_110001_cancel"`

Expected: 2 failed (the GHOST log is INFO-only when reconciled — Task 2 already set this, so test 1 *may* pass; test 2 fails because cancel_order still throws/ERRORs on 110001).

### 3b — Implement Area 2 demotions

- [ ] **Step 3: Demote `_verify_order_on_exchange` Bybit fetchOrder warning to DEBUG**

Find the warning at `core/order_manager.py:387` area. Read 5 lines of context first to locate:

Run: `grep -n "fetchOrder\|_verify_order_on_exchange\|can only access an order" core/order_manager.py | head -5`

The existing line is approximately:

```python
logger.warning(
    f"[Orders] Order verification failed for {order_id}: {e}")
```

Replace it with a level-aware variant that demotes the Bybit 20-min-window case to DEBUG:

```python
# Area 2 (2026-05-20): demote Bybit's "fetchOrder() can only access an order
# within last 20 mins" warning to DEBUG — it's a Bybit API limitation, not
# a bot issue. Other unexpected errors still surface at WARNING.
_err_text = str(e).lower()
if "can only access an order" in _err_text:
    logger.debug(
        f"[Orders] Order verification skipped (Bybit 20-min limit) "
        f"for {order_id}: {str(e)[:120]}")
else:
    logger.warning(
        f"[Orders] Order verification failed for {order_id}: {e}")
```

- [ ] **Step 4: Demote first-attempt health-check failure to DEBUG**

Open `core/bot_engine.py:2856` area. The current code:

```python
            except Exception as e:
                fails = self._consecutive_api_fails.get(ex_name, 0) + 1
                self._consecutive_api_fails[ex_name] = fails
                self._api_latency[ex_name] = -1
                logger.warning(
                    f"[Health] {ex_name} health check FAILED "
                    f"(attempt {fails}): {e}")
```

Replace the `logger.warning(...)` block with a level-aware version:

```python
                # Area 2 (2026-05-20): first-attempt failure is usually a
                # transient API blip — the existing retry path handles it.
                # Only escalate to WARNING on the 2nd+ failure.
                _level_fn = logger.debug if fails == 1 else logger.warning
                _level_fn(
                    f"[Health] {ex_name} health check FAILED "
                    f"(attempt {fails}): {e}")
```

- [ ] **Step 5: Demote ticker retry warning to DEBUG in `exchanges/base.py:172-183`**

Open `exchanges/base.py:170-185`. The existing block:

```python
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    logger.warning(
                        f"[{self.name}] fetch_ticker {symbol}: transient, "
                        f"retry {attempt+1}/{MAX_RETRIES-1} in {delay:.1f}s")
                    _time.sleep(delay)
                    continue
                if attempt == MAX_RETRIES - 1:
                    logger.warning(f"[{self.name}] fetch_ticker {symbol}: "
                                   f"failed after {MAX_RETRIES} attempts: {e}")
                else:
                    logger.warning(f"[{self.name}] fetch_ticker {symbol}: {e}")
                return {}
```

Replace with:

```python
                if _is_transient_error(e) and attempt < MAX_RETRIES - 1:
                    delay = _backoff_delay(attempt)
                    # Area 2: retry-in-progress messages go to DEBUG so only the
                    # final-attempt failure surfaces at WARNING.
                    logger.debug(
                        f"[{self.name}] fetch_ticker {symbol}: transient, "
                        f"retry {attempt+1}/{MAX_RETRIES-1} in {delay:.1f}s")
                    _time.sleep(delay)
                    continue
                if attempt == MAX_RETRIES - 1:
                    logger.warning(f"[{self.name}] fetch_ticker {symbol}: "
                                   f"failed after {MAX_RETRIES} attempts: {e}")
                else:
                    # Mid-attempt non-transient miss — DEBUG (next attempt may succeed)
                    logger.debug(f"[{self.name}] fetch_ticker {symbol}: {e}")
                return {}
```

- [ ] **Step 6: Demote Bybit 110001 cancel error to DEBUG in `exchanges/base.py:298-307`**

Open the `cancel_order` method at `exchanges/base.py:298`. Current code:

```python
    def cancel_order(self, order_id: str, symbol: str,
                     market_type: str = "spot") -> dict:
        if not self._ready():
            return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.cancel_order(order_id, symbol, params=params)
        except Exception as e:
            logger.error(f"[{self.name}] cancel_order {order_id}: {e}")
            return {}
```

Replace with the Area 2 + Area 3 combined version (Area 3 is the "swallow known races" handler — implementing both here saves a duplicate edit later):

```python
    # Known race-condition error markers — the order was already filled or
    # cancelled between our last observation and our cancel call. Returning
    # success silently is the correct behavior; the position is already gone.
    _CANCEL_RACE_MARKERS = (
        "order not exists",
        "too late to cancel",
        "110001",       # Bybit
        "40034",        # Bitget
        "order does not exist",
        "orderid not found",
    )

    def cancel_order(self, order_id: str, symbol: str,
                     market_type: str = "spot") -> dict:
        if not self._ready():
            return {}
        try:
            params = self._futures_params() if market_type == "futures" else {}
            return self.exchange.cancel_order(order_id, symbol, params=params)
        except Exception as e:
            # Areas 2 + 3 (2026-05-20): swallow known cancel-race errors.
            # If the order is already gone, our intent (cancel) is satisfied;
            # logging at ERROR creates noise and triggers alerts unnecessarily.
            _err_lc = str(e).lower()
            if any(m in _err_lc for m in self._CANCEL_RACE_MARKERS):
                logger.debug(
                    f"[{self.name}] cancel_order {order_id}: race "
                    f"(already filled/cancelled) — {str(e)[:80]}")
                return {}
            # Unknown error — keep at ERROR
            logger.error(f"[{self.name}] cancel_order {order_id}: {e}")
            return {}
```

- [ ] **Step 7: Silence ccxt's `acknowledged` warning at the relevant call sites**

Run: `grep -n "fetch_open_orders\|fetchOpenOrders\|acknowledged" exchanges/base.py core/order_manager.py | head -10`

Search for call sites that currently emit ccxt's "Set params[acknowledged] = True" warning. Looking at the spec evidence, this is emitted by `fetch_open_orders` calls on Bybit for closed-orders fetching. Wherever a `fetch_open_orders(symbol, params={"stop": True})` exists in `exchanges/base.py` (line ~325 from Task 2 exploration), add `"acknowledged": True` to the params dict:

For each occurrence like:
```python
algo = self.exchange.fetch_open_orders(
    symbol, params={"stop": True}) or []
```

Replace with:
```python
algo = self.exchange.fetch_open_orders(
    symbol, params={"stop": True, "acknowledged": True}) or []
```

Locate each call:
Run: `grep -n 'fetch_open_orders.*"stop"' exchanges/base.py exchanges/bybit_client.py`

Apply the param addition at every match. If `acknowledged` is already present, skip.

- [ ] **Step 8: Run the 2 Area 2 tests — both should PASS**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "ghost_detected_at_info or bybit_110001_cancel"`

Expected: 2 passed.

- [ ] **Step 9: Run broader regression**

Run: `pytest tests/ -q -x --deselect tests/test_ghost_and_noise_cleanup.py`

Expected: all currently-passing tests still pass (1030+).

- [ ] **Step 10: Commit**

```bash
git add core/order_manager.py core/bot_engine.py exchanges/base.py exchanges/bybit_client.py tests/test_ghost_and_noise_cleanup.py
git commit -m "feat(noise): Area 2 — demote 5 informational warnings, silence ccxt ack

Five log-level cleanups in core/bot_engine, core/order_manager,
exchanges/base.py to remove non-actionable noise:
  - Bybit fetchOrder() 20-min-window warning -> DEBUG (API limitation)
  - First-attempt health-check failure -> DEBUG (retry path handles it)
  - Ticker retry-in-progress warning -> DEBUG (only final attempt warns)
  - Bybit 110001 'order not exists' on cancel -> DEBUG + return {} (race)
  - ccxt acknowledged-warning silenced via params on fetch_open_orders

Net effect on logs: ~22 daily WARN/ERROR lines -> ~3-5 (real signal only).

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 4: Area 3 — `_safe_cancel_order` centralized in `exchanges/base.py`

**Note:** Area 3's "swallow known race errors" logic was implemented as part of Task 3, Step 6 (the `_CANCEL_RACE_MARKERS` tuple + the catch in `BaseExchange.cancel_order`). This central placement means EVERY call site already benefits — including the 6 call sites in `core/order_manager.py` that use `cancel_all_orders` (which iterates and calls `cancel_order` per order), and the direct `self.exchange.cancel_order(...)` call for algo orders in `exchanges/base.py:329`.

This task adds the 3 dedicated tests for Area 3 to pin the behavior and confirm unknown errors still propagate.

**Files:**
- Modify: `exchanges/base.py` (algo-cancel path inside `cancel_all_orders`)
- Test: `tests/test_ghost_and_noise_cleanup.py` (append 3 tests)

- [ ] **Step 1: Make the algo-cancel path in `cancel_all_orders` also use the safe wrapper**

Find the algo-cancel block in `exchanges/base.py:323-334`:

```python
            if market_type == "futures":
                try:
                    algo = self.exchange.fetch_open_orders(
                        symbol, params={"stop": True, "acknowledged": True}) or []
                    for o in algo:
                        try:
                            self.exchange.cancel_order(
                                o["id"], symbol, params={"stop": True})
                        except Exception as _ce:
                            logger.debug(
                                f"[{self.name}] algo cancel {o.get('id')}: {_ce}")
                except Exception as _ae:
                    ...
```

The inner `self.exchange.cancel_order(...)` bypasses our `BaseExchange.cancel_order` wrapper because it calls the raw ccxt instance directly. Route it through the wrapper so 110001 races are handled there too. Replace the inner cancel with:

```python
            if market_type == "futures":
                try:
                    algo = self.exchange.fetch_open_orders(
                        symbol, params={"stop": True, "acknowledged": True}) or []
                    for o in algo:
                        # Area 3 (2026-05-20): route algo cancels through our
                        # wrapper so 110001/40034 race errors are absorbed
                        # uniformly, not just on regular-order cancels.
                        try:
                            params_algo = self._futures_params() if market_type == "futures" else {}
                            params_algo["stop"] = True
                            self.exchange.cancel_order(
                                o["id"], symbol, params=params_algo)
                        except Exception as _ce:
                            _err_lc = str(_ce).lower()
                            if any(m in _err_lc for m in self._CANCEL_RACE_MARKERS):
                                logger.debug(
                                    f"[{self.name}] algo cancel {o.get('id')}: "
                                    f"race (already filled/cancelled)")
                            else:
                                logger.debug(
                                    f"[{self.name}] algo cancel {o.get('id')}: {_ce}")
                except Exception as _ae:
                    ...  # leave the outer except as it was
```

- [ ] **Step 2: Append the 3 Area 3 tests**

Append to `tests/test_ghost_and_noise_cleanup.py`:

```python
# ---------------------------------------------------------------------------
# AREA 3 — Reliability hardening (_safe_cancel_order)
# ---------------------------------------------------------------------------


def test_safe_cancel_order_swallows_110001(caplog):
    """Bybit 110001 'order not exists or too late to cancel' returns {} cleanly."""
    import ccxt
    from exchanges import base as base_mod

    class _FakeBybit:
        def cancel_order(self, order_id, symbol, params=None):
            raise ccxt.InvalidOrder(
                'bybit {"retCode":110001,"retMsg":"order not exists or too late to cancel"}'
            )

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "bybit"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeBybit()
    fake_be._futures_params = lambda: {}
    fake_be._CANCEL_RACE_MARKERS = base_mod.BaseExchange._CANCEL_RACE_MARKERS

    result = base_mod.BaseExchange.cancel_order(
        fake_be, "ABC-123", "BNB/USDT:USDT", "futures"
    )

    assert result == {}, f"expected swallowed cancel to return empty dict, got {result}"


def test_safe_cancel_order_swallows_bitget_40034(caplog):
    """Bitget 40034 'order does not exist' race — same swallow semantics."""
    import ccxt
    from exchanges import base as base_mod

    class _FakeBitget:
        def cancel_order(self, order_id, symbol, params=None):
            raise ccxt.InvalidOrder(
                'bitget {"code":"40034","msg":"order does not exist"}'
            )

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "bitget"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeBitget()
    fake_be._futures_params = lambda: {}
    fake_be._CANCEL_RACE_MARKERS = base_mod.BaseExchange._CANCEL_RACE_MARKERS

    result = base_mod.BaseExchange.cancel_order(
        fake_be, "XYZ-987", "AVAX/USDT:USDT", "futures"
    )
    assert result == {}


def test_safe_cancel_order_reraises_unknown_errors(caplog):
    """Unknown error classes (RuntimeError, ConnectionError, generic) must
    still be logged at ERROR and not silently swallowed."""
    from exchanges import base as base_mod

    class _FakeExchange:
        def cancel_order(self, order_id, symbol, params=None):
            raise RuntimeError("boom: something genuinely unexpected")

    fake_be = MagicMock(spec=base_mod.BaseExchange)
    fake_be.name = "binance"
    fake_be._ready = lambda: True
    fake_be.exchange = _FakeExchange()
    fake_be._futures_params = lambda: {}
    fake_be._CANCEL_RACE_MARKERS = base_mod.BaseExchange._CANCEL_RACE_MARKERS

    caplog.clear()
    result = base_mod.BaseExchange.cancel_order(
        fake_be, "DEF-456", "ETH/USDT:USDT", "futures"
    )
    assert result == {}  # caller still gets a safe {}; behavior preserved
    # But the unknown error MUST surface at ERROR (visibility for real bugs)
    error_records = [
        r for r in caplog.records
        if r.levelname == "ERROR" and "boom" in r.message
    ]
    assert error_records, (
        f"unknown errors must still log at ERROR; got: "
        f"{[(r.levelname, r.message) for r in caplog.records]}"
    )
```

- [ ] **Step 3: Run the 3 Area 3 tests**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "safe_cancel_order"`

Expected: 3 passed.

- [ ] **Step 4: Commit**

```bash
git add exchanges/base.py tests/test_ghost_and_noise_cleanup.py
git commit -m "feat(cancel): Area 3 — safe cancel race handling for Bybit/Bitget

BaseExchange.cancel_order now swallows known race errors:
  - Bybit 110001 'order not exists or too late to cancel'
  - Bitget 40034 'order does not exist'
  - Generic 'order does not exist' / 'orderid not found'
  - Any other unknown error still surfaces at ERROR (preserved visibility)

The algo-cancel path in cancel_all_orders also routes through the same
race-handling logic so STOP_MARKET / TAKE_PROFIT_MARKET cancels benefit
uniformly across regular + algo order books.

Three tests pin the swallow semantics for 110001, 40034, and a control
RuntimeError that must continue to log at ERROR.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 5: Area 4 — Age-aware SL→breakeven tightener

**Files:**
- Modify: `core/bot_engine.py` (add `_maybe_tighten_aged_position` method + helper `_unrealized_pnl_frac`)
- Test: `tests/test_ghost_and_noise_cleanup.py` (append 2 tests)

### 5a — Write the 2 Area 4 tests

- [ ] **Step 1: Append 2 Area 4 tests**

Append to `tests/test_ghost_and_noise_cleanup.py`:

```python
# ---------------------------------------------------------------------------
# AREA 4 — Age-aware SL→breakeven tightening
# ---------------------------------------------------------------------------


def _make_long_position(age_min: float, entry: float, current_mark: float):
    """Build a minimal Position-like mock for Area 4/5 unit tests."""
    p = MagicMock()
    p.id = f"TEST-AREA4-{age_min}-{entry}-{current_mark}"
    p.exchange = "binance"
    p.symbol = "ARB/USDT:USDT"
    p.side = "buy"
    p.size = 50.0
    p.entry_price = entry
    p.stop_loss = entry * (1 - 0.015)   # 1.5% below entry (default SL)
    p.take_profit = entry * 1.03
    p.market_type = "futures"
    p.leverage = 2
    p.duration_minutes = age_min
    return p


def _make_short_position(age_min: float, entry: float, current_mark: float):
    p = MagicMock()
    p.id = f"TEST-AREA4-S-{age_min}-{entry}-{current_mark}"
    p.exchange = "bybit"
    p.symbol = "DOGE/USDT:USDT"
    p.side = "sell"
    p.size = 100.0
    p.entry_price = entry
    p.stop_loss = entry * (1 + 0.015)
    p.take_profit = entry * 0.97
    p.market_type = "futures"
    p.leverage = 2
    p.duration_minutes = age_min
    return p


def test_age_aware_tighten_fires_at_60min_in_profit(monkeypatch):
    """Age 70min, pnl +0.8%, SL far from entry → _replace_exchange_sl called
    with the breakeven price (entry × (1 + 2·fee + 0.0005))."""
    from core import bot_engine
    from config import FEE

    # Long position +0.8% in profit at age 70min
    entry = 1.00
    current_mark = entry * 1.008  # +0.8%
    p = _make_long_position(age_min=70.0, entry=entry, current_mark=current_mark)

    # Wire up a minimal BotEngine-like object with just the method under test
    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng._replace_exchange_sl = MagicMock(return_value=True)

    # Mock pnl_frac computation: return +0.008 (+0.8%)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.008)

    # Run the method under test (unbound; bind manually)
    fired = bot_engine.BotEngine._maybe_tighten_aged_position(eng, p)

    assert fired is True, "expected age-aware tightener to fire"
    eng._replace_exchange_sl.assert_called_once()
    # Verify the new SL is the breakeven price for a long
    _, _, new_sl_arg = eng._replace_exchange_sl.call_args[0]
    fee_rate = FEE.get("futures_taker", 0.0005)
    expected = entry * (1 + 2 * fee_rate + 0.0005)
    assert abs(new_sl_arg - expected) < 1e-6, (
        f"expected new SL {expected:.6f}, got {new_sl_arg:.6f}"
    )


def test_age_aware_tighten_no_op_outside_band(monkeypatch):
    """No-op conditions: age too young, in loss, above 2% profit, or SL already at/past BE."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng._replace_exchange_sl = MagicMock(return_value=True)

    # (1) age 45min — too young
    p1 = _make_long_position(age_min=45.0, entry=1.0, current_mark=1.008)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.008)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p1) is False

    # (2) pnl -0.5% — in loss
    p2 = _make_long_position(age_min=70.0, entry=1.0, current_mark=0.995)
    eng._unrealized_pnl_frac = MagicMock(return_value=-0.005)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p2) is False

    # (3) pnl +2.5% — above trailing-stop activation zone
    p3 = _make_long_position(age_min=70.0, entry=1.0, current_mark=1.025)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.025)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p3) is False

    # (4) SL already tighter than breakeven — refuse to widen
    p4 = _make_long_position(age_min=70.0, entry=1.0, current_mark=1.008)
    p4.stop_loss = 1.005  # already past breakeven; tightening would WIDEN
    eng._unrealized_pnl_frac = MagicMock(return_value=0.008)
    assert bot_engine.BotEngine._maybe_tighten_aged_position(eng, p4) is False

    # No tightener calls in any of the above
    assert eng._replace_exchange_sl.call_count == 0
```

- [ ] **Step 2: Run the 2 tests; both should FAIL**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "age_aware_tighten"`

Expected: 2 failed (method doesn't exist yet).

### 5b — Implement `_maybe_tighten_aged_position` and `_unrealized_pnl_frac` in bot_engine

- [ ] **Step 3: Find a spot in `core/bot_engine.py` near `_run_mcp_position_monitor` and add the helper + method**

Locate `_run_mcp_position_monitor` (around line 3655). Just above it (so the helpers are visible when reading the monitor code), add the new methods. First, read 5 lines above line 3655 to find the right insertion point:

Run: `sed -n '3645,3660p' core/bot_engine.py`

Insert the following block immediately before `def _run_mcp_position_monitor(self):`:

```python
    def _unrealized_pnl_frac(self, p) -> float:
        """Compute unrealized PnL as a fraction (e.g. 0.012 = +1.2%) using
        the current mark price from the exchange. Returns 0.0 on any error.

        Notes:
        - Fraction is computed against entry_price (unleveraged) so the
          [0%, 2%) bands in Areas 4 + 5 refer to PRICE move, not equity move.
          A +1% price move on a 2x position = +2% on equity but only +1% by
          this fraction. This matches the existing SL/TP percentages.
        """
        ex = self._exchange_for(p.exchange)
        if ex is None:
            return 0.0
        try:
            ticker = ex.fetch_ticker(p.symbol, p.market_type)
            info = ticker.get("info", {}) or {}
            mark = info.get("markPrice") or info.get("mark")
            try:
                price = float(mark) if mark else 0.0
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                price = float(ticker.get("last") or 0.0)
            if price <= 0 or p.entry_price <= 0:
                return 0.0
            if p.side == "buy":
                return (price - p.entry_price) / p.entry_price
            else:
                return (p.entry_price - price) / p.entry_price
        except Exception:
            return 0.0

    def _maybe_tighten_aged_position(self, p) -> bool:
        """Area 4 — Age-aware SL→breakeven tightener.

        Fires when:
          - config.AGE_AWARE_SL_ENABLED is True
          - position is futures (spot has no leverage-side ghost problem)
          - age >= AGE_AWARE_SL_MIN_AGE_MIN
          - pnl_frac in [AGE_AWARE_SL_MIN_PNL_FRAC, AGE_AWARE_SL_MAX_PNL_FRAC)
          - new breakeven SL is TIGHTER than current SL (never widens — Spec §2)

        Action: replaces exchange-side SL with entry × (1 ± 2·fee_rate ± 0.0005).

        Returns True iff an SL replacement actually fired.
        """
        try:
            from config import (
                AGE_AWARE_SL_ENABLED, AGE_AWARE_SL_MIN_AGE_MIN,
                AGE_AWARE_SL_MIN_PNL_FRAC, AGE_AWARE_SL_MAX_PNL_FRAC,
                FEE,
            )
        except ImportError:
            return False
        if not AGE_AWARE_SL_ENABLED:
            return False
        if getattr(p, "market_type", "") != "futures":
            return False
        try:
            age_min = float(p.duration_minutes)
        except Exception:
            return False
        if age_min < AGE_AWARE_SL_MIN_AGE_MIN:
            return False

        pnl_frac = self._unrealized_pnl_frac(p)
        if not (AGE_AWARE_SL_MIN_PNL_FRAC <= pnl_frac < AGE_AWARE_SL_MAX_PNL_FRAC):
            return False

        # Breakeven SL — covers round-trip fee + 5bp buffer
        rate = FEE.get("futures_taker", 0.0005)
        if p.side == "buy":
            new_sl = p.entry_price * (1 + 2 * rate + 0.0005)
            # Refuse to widen: must be ABOVE current SL for a long
            if new_sl <= float(p.stop_loss or 0):
                return False
        else:
            new_sl = p.entry_price * (1 - 2 * rate - 0.0005)
            # Refuse to widen: must be BELOW current SL for a short
            if new_sl >= float(p.stop_loss or 0):
                return False

        # Replace the exchange-side SL
        ex = self._exchange_for(p.exchange)
        if ex is None:
            return False
        if not self._replace_exchange_sl(ex, p, new_sl):
            return False
        old_sl = p.stop_loss
        p.stop_loss = new_sl
        logger.info(
            f"[AgeAwareSL] {p.symbol} {p.side.upper()} age={age_min:.0f}m "
            f"pnl={pnl_frac*100:+.2f}% SL→breakeven "
            f"({old_sl:.6g}→{new_sl:.6g})")
        return True
```

Note: this assumes `self._exchange_for(exchange_name)` and `self._replace_exchange_sl(ex, p, new_sl)` already exist on BotEngine. Verify:

Run: `grep -n "def _exchange_for\|def _replace_exchange_sl" core/bot_engine.py`

Expected: both methods present. If `_exchange_for` doesn't exist on BotEngine but exists on `order_manager`, adapt the call to `self.order_mgr._exchange_for(p.exchange)`.

- [ ] **Step 4: Run the 2 Area 4 tests**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "age_aware_tighten"`

Expected: 2 passed.

- [ ] **Step 5: Commit (no wiring yet — that's Task 7)**

```bash
git add core/bot_engine.py tests/test_ghost_and_noise_cleanup.py
git commit -m "feat(sl): Area 4 — age-aware SL→breakeven tightener method

Adds _maybe_tighten_aged_position + _unrealized_pnl_frac helper to
BotEngine. Fires when a futures position is in profit (0%, 1%) and
has held >=60 min — replaces the exchange-side SL with breakeven
(entry × (1 ± 2·fee_rate ± 0.0005)), refusing to widen (Spec §2).

Method is added but NOT yet wired into _run_mcp_position_monitor — that
happens in Task 7 after Area 5 also exists so both are wired together
with the correct ordering (Area 5 close decisive over Area 4 SL move).

Two tests pin: (1) fires at 70min/+0.8% with correct BE price;
(2) no-op for age<60, pnl<0, pnl>=2%, or SL already past BE.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 6: Area 5 — Deterministic small-TP capture

**Files:**
- Modify: `core/bot_engine.py` (add `_maybe_capture_small_tp` method next to Area 4 method)
- Test: `tests/test_ghost_and_noise_cleanup.py` (append 4 tests)

### 6a — Write the 4 Area 5 tests

- [ ] **Step 1: Append 4 Area 5 tests**

Append to `tests/test_ghost_and_noise_cleanup.py`:

```python
# ---------------------------------------------------------------------------
# AREA 5 — Deterministic small-TP capture
# ---------------------------------------------------------------------------


def test_auto_small_tp_fires_at_1pct_after_30min(monkeypatch):
    """Futures position age 35min pnl +1.2% → close_position called with
    reason='auto_small_tp_1pct'."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    eng._unrealized_pnl_frac = MagicMock(return_value=0.012)  # +1.2%
    monkeypatch.setattr("config.STAR_SYMBOLS", set())  # empty STAR set for this test

    p = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.012)

    fired = bot_engine.BotEngine._maybe_capture_small_tp(eng, p)
    assert fired is True, "expected Area 5 to fire"

    # close_position was called with the right close_reason
    eng.order_mgr.close_position.assert_called_once()
    _, kwargs = eng.order_mgr.close_position.call_args.args, eng.order_mgr.close_position.call_args.kwargs
    pos_args = eng.order_mgr.close_position.call_args.args
    # Expected signature: close_position(exchange, position, reason)
    assert pos_args[2] == "auto_small_tp_1pct", (
        f"expected close reason 'auto_small_tp_1pct', got {pos_args[2]}"
    )


def test_auto_small_tp_no_op_below_1pct_or_below_30min(monkeypatch):
    """No-op when: pnl < 1%, age < 30min, pnl >= 2% (trailing zone)."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    monkeypatch.setattr("config.STAR_SYMBOLS", set())

    # (1) pnl +0.5% (below 1% threshold)
    p1 = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.005)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.005)
    assert bot_engine.BotEngine._maybe_capture_small_tp(eng, p1) is False

    # (2) age 25min (below 30min threshold)
    p2 = _make_long_position(age_min=25.0, entry=1.0, current_mark=1.015)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.015)
    assert bot_engine.BotEngine._maybe_capture_small_tp(eng, p2) is False

    # (3) pnl +2.5% (trailing stop zone, not Area 5's job)
    p3 = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.025)
    eng._unrealized_pnl_frac = MagicMock(return_value=0.025)
    assert bot_engine.BotEngine._maybe_capture_small_tp(eng, p3) is False

    assert eng.order_mgr.close_position.call_count == 0


def test_auto_small_tp_skips_star_symbols(monkeypatch):
    """STAR_SYMBOLS positions ride per Phase 46 policy — Area 5 must skip."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    eng._unrealized_pnl_frac = MagicMock(return_value=0.012)

    # Pin ATOM/ARB as STAR
    monkeypatch.setattr(
        "config.STAR_SYMBOLS",
        {"ATOM/USDT:USDT", "ARB/USDT:USDT"},
    )

    p = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.012)
    p.symbol = "ATOM/USDT:USDT"   # STAR symbol

    fired = bot_engine.BotEngine._maybe_capture_small_tp(eng, p)
    assert fired is False, "STAR symbol should be exempt from Area 5"
    eng.order_mgr.close_position.assert_not_called()


def test_auto_small_tp_skips_spot(monkeypatch):
    """Spot positions keep existing logic — Area 5 is futures-only."""
    from core import bot_engine

    eng = MagicMock(spec=bot_engine.BotEngine)
    eng.active_exchanges = {"binance": MagicMock()}
    eng.order_mgr = MagicMock()
    eng.order_mgr.close_position = MagicMock()
    eng._unrealized_pnl_frac = MagicMock(return_value=0.015)
    monkeypatch.setattr("config.STAR_SYMBOLS", set())

    p = _make_long_position(age_min=35.0, entry=1.0, current_mark=1.015)
    p.market_type = "spot"   # spot, not futures

    fired = bot_engine.BotEngine._maybe_capture_small_tp(eng, p)
    assert fired is False, "spot should be exempt from Area 5"
    eng.order_mgr.close_position.assert_not_called()
```

- [ ] **Step 2: Run the 4 tests — all should FAIL**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "auto_small_tp"`

Expected: 4 failed (method missing).

### 6b — Implement `_maybe_capture_small_tp` in bot_engine

- [ ] **Step 3: Add the method next to the Area 4 method**

In `core/bot_engine.py`, find the `_maybe_tighten_aged_position` method just added in Task 5. Add the Area 5 method IMMEDIATELY ABOVE it (so when wired in Task 7 it's natural to check Area 5 first):

```python
    def _maybe_capture_small_tp(self, p) -> bool:
        """Area 5 — Deterministic small-TP capture.

        Fires when:
          - config.AUTO_SMALL_TP_ENABLED is True
          - position is futures (spot keeps existing logic)
          - symbol NOT in STAR_SYMBOLS (those ride per Phase 46)
          - age >= AUTO_SMALL_TP_MIN_AGE_MIN
          - pnl_frac in [AUTO_SMALL_TP_MIN_PNL_FRAC, AUTO_SMALL_TP_MAX_PNL_FRAC)

        Action: market close via order_mgr.close_position with reason
        'auto_small_tp_1pct'. Returns True iff a close was actually fired.

        NOT a re-enable of Phase 39 disabled CLOSE — that was Claude's
        discretionary, narrative-driven decision (1W/17L over 388 trades).
        This is a deterministic threshold rule, equivalent in nature to a
        take-profit limit order placed at entry+1%.
        """
        try:
            from config import (
                AUTO_SMALL_TP_ENABLED, AUTO_SMALL_TP_MIN_AGE_MIN,
                AUTO_SMALL_TP_MIN_PNL_FRAC, AUTO_SMALL_TP_MAX_PNL_FRAC,
                STAR_SYMBOLS,
            )
        except ImportError:
            return False
        if not AUTO_SMALL_TP_ENABLED:
            return False
        if getattr(p, "market_type", "") != "futures":
            return False
        if p.symbol in (STAR_SYMBOLS or set()):
            return False
        try:
            age_min = float(p.duration_minutes)
        except Exception:
            return False
        if age_min < AUTO_SMALL_TP_MIN_AGE_MIN:
            return False

        pnl_frac = self._unrealized_pnl_frac(p)
        if not (AUTO_SMALL_TP_MIN_PNL_FRAC <= pnl_frac < AUTO_SMALL_TP_MAX_PNL_FRAC):
            return False

        # Resolve the exchange and fire close_position
        ex = None
        for ex_name, exchange in self.active_exchanges.items():
            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                ex = exchange
                break
        if ex is None:
            return False

        logger.info(
            f"[AutoSmallTP] {p.symbol} {p.side.upper()} age={age_min:.0f}m "
            f"pnl=+{pnl_frac*100:.2f}% — capturing at market")
        self.order_mgr.close_position(ex, p, "auto_small_tp_1pct")
        return True
```

- [ ] **Step 4: Run the 4 Area 5 tests**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v -k "auto_small_tp"`

Expected: 4 passed.

- [ ] **Step 5: Run the entire new test file**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v`

Expected: 14 passed (3 Area 1 + 2 Area 2 + 3 Area 3 + 2 Area 4 + 4 Area 5).

- [ ] **Step 6: Commit**

```bash
git add core/bot_engine.py tests/test_ghost_and_noise_cleanup.py
git commit -m "feat(tp): Area 5 — deterministic small-TP capture method

Adds _maybe_capture_small_tp to BotEngine. Fires when a non-STAR futures
position is in profit [1%, 2%) and has held >=30 min — market closes via
order_mgr.close_position with reason='auto_small_tp_1pct'.

Distinct from Phase 39 disabled CLOSE: that was Claude's discretionary
narrative-driven decision (lost -$16.42 / 388 trades). This is a pure
deterministic threshold rule, functionally equivalent to a take-profit
limit order placed at entry+1%.

STAR_SYMBOLS (ATOM, ARB) exempt per Phase 46 ride policy. Spot positions
exempt — Area 5 is futures-only. Tests pin: fires at 35min/+1.2%; no-op
on pnl<1%, age<30, pnl>=2%, STAR, or spot.

Method added but NOT yet wired into _run_mcp_position_monitor — Task 7
wires both Area 4 and Area 5 together with Area 5 ordered first.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Task 7: Wire Areas 4 + 5 into `_run_mcp_position_monitor` and final verification

**Files:**
- Modify: `core/bot_engine.py:3764` area — insert deterministic-exits hook before MCP advice loop

- [ ] **Step 1: Locate the insertion point in `_run_mcp_position_monitor`**

Open `core/bot_engine.py:3758-3776`. The structure is:

```python
            # ── Phase 4: Send to MCP Brain ──
            advice = self.mcp_brain.monitor_positions(pos_data)
            if not advice:
                return

            # ── Phase 5: Apply actions ──
            for pos_entry in pos_data:
                pid = pos_entry["id"]
                ...
```

We want to insert a deterministic-exits loop BEFORE the MCP advice is fetched (so Area 5 close decisions can fire without waiting for Claude's network round-trip), iterating ONLY the `tracker_map` positions (Position objects with stop_loss, side, entry_price, duration_minutes — which mock-positions / external positions lack).

- [ ] **Step 2: Insert the deterministic-exits block**

Just before the `# ── Phase 4: Send to MCP Brain ──` comment, insert:

```python
            # ── Phase 3.5 (2026-05-20): Deterministic exits BEFORE MCP brain ──
            # Areas 5 + 4 are pure threshold rules (no judgment, no narrative).
            # Running them here means they fire even on cycles where Claude
            # would have said HOLD. Ordering matters: Area 5 (close) is decisive
            # over Area 4 (SL move) — check Area 5 first, return early on fire.
            #
            # Only TRACKER positions get this treatment — external (exchange-
            # discovered) positions don't have full Position state (stop_loss,
            # entry_price, side as a Position attr) and are handled in the
            # source=='exchange' branch of the MCP-advice loop below.
            for pid_local, p_local in list(tracker_map.items()):
                try:
                    if self._maybe_capture_small_tp(p_local):
                        continue  # Area 5 fired — position closing; skip Area 4
                    self._maybe_tighten_aged_position(p_local)
                except Exception as _de:
                    logger.debug(
                        f"[DeterministicExits] {p_local.symbol}: {_de}")
```

- [ ] **Step 3: Verify the file still parses cleanly**

Run: `python -c "import core.bot_engine; print('bot_engine.py: imports OK')"`

Expected: `bot_engine.py: imports OK`

- [ ] **Step 4: Add a wiring/ordering integration test**

Append to `tests/test_ghost_and_noise_cleanup.py`:

```python
# ---------------------------------------------------------------------------
# WIRING — ordering invariant: Area 5 checked before Area 4 (close decisive)
# ---------------------------------------------------------------------------


def test_area5_checked_before_area4_ordering():
    """The deterministic-exits hook must invoke _maybe_capture_small_tp
    first and short-circuit Area 4 when it fires. Otherwise a position in
    the [1%, 2%) overlap band could have its SL moved instead of being
    closed for the +1-2% profit — losing the captured win."""
    import inspect
    from core import bot_engine

    src = inspect.getsource(bot_engine.BotEngine._run_mcp_position_monitor)
    # Both calls must exist
    assert "_maybe_capture_small_tp" in src, (
        "_run_mcp_position_monitor must wire Area 5 (_maybe_capture_small_tp)"
    )
    assert "_maybe_tighten_aged_position" in src, (
        "_run_mcp_position_monitor must wire Area 4 (_maybe_tighten_aged_position)"
    )
    # Area 5 must appear strictly before Area 4 in source order
    idx5 = src.index("_maybe_capture_small_tp")
    idx4 = src.index("_maybe_tighten_aged_position")
    assert idx5 < idx4, (
        f"Area 5 must be called BEFORE Area 4 in _run_mcp_position_monitor; "
        f"found area5_idx={idx5}, area4_idx={idx4}"
    )
```

- [ ] **Step 5: Run all 15 tests in the new file (14 area-tests + 1 ordering)**

Run: `pytest tests/test_ghost_and_noise_cleanup.py -v`

Expected: 15 passed.

- [ ] **Step 6: Run the full repository test suite for regression check**

Run: `python -m pytest tests/ -q`

Expected: previously-passing count + 15 new = 1045+ passed, 0 failed.

- [ ] **Step 7: Smoke-test the bot startup**

Run: `python main.py --status`

Expected: bot status table prints, no import errors, no traceback. Open positions still listed correctly.

- [ ] **Step 8: Commit and tag**

```bash
git add core/bot_engine.py tests/test_ghost_and_noise_cleanup.py
git commit -m "feat(monitor): wire Areas 4+5 into _run_mcp_position_monitor (Area 5 first)

Inserts a deterministic-exits pass at the top of _run_mcp_position_monitor,
BEFORE the MCP brain advice fetch. Iterates tracker_map positions and
tries Area 5 (small-TP capture) first; on fire it short-circuits Area 4
for that position. If Area 5 doesn't fire, Area 4 (SL→breakeven) gets a
chance.

Ordering invariant pinned by test_area5_checked_before_area4_ordering: if
a future edit swaps the order, the test fails loudly. The reason: in the
overlap band [1%, 2%) at age>=30min AND >=60min, both rules' conditions
are true. We want the position CAPTURED (close at +1%), not just
PROTECTED (SL to breakeven that may never trigger if price retraces).

External (exchange-discovered) positions skip this path — they don't have
full Position objects with stop_loss/side/duration_minutes and are handled
in the source=='exchange' branch of the MCP-advice loop below.

15 new tests total: 3 Area 1 + 2 Area 2 + 3 Area 3 + 2 Area 4 + 4 Area 5
+ 1 wiring ordering.

Co-Authored-By: Claude Opus 4.7 (1M context) <noreply@anthropic.com>"
```

---

## Verification (overall, end-to-end)

After all 7 tasks complete:

```bash
# 1. All tests green
python -m pytest tests/ -q
# Expected: 1045+ passed, 0 failed

# 2. New tests all pass on their own
python -m pytest tests/test_ghost_and_noise_cleanup.py -v
# Expected: 15 passed

# 3. Bot still imports and runs --status
python main.py --status
# Expected: status table prints, no errors

# 4. Config flags all defined
python -c "from config import (
    GHOST_LEDGER_WINDOW_H, GHOST_PENDING_REQUEUE,
    AGE_AWARE_SL_ENABLED, AGE_AWARE_SL_MIN_AGE_MIN,
    AGE_AWARE_SL_MIN_PNL_FRAC, AGE_AWARE_SL_MAX_PNL_FRAC,
    AUTO_SMALL_TP_ENABLED, AUTO_SMALL_TP_MIN_AGE_MIN,
    AUTO_SMALL_TP_MIN_PNL_FRAC, AUTO_SMALL_TP_MAX_PNL_FRAC,
); print('all 10 flags OK')"
# Expected: all 10 flags OK

# 5. Git log shows clean per-area commits
git log --oneline feat/profitability-upgrade ^main | head -8
# Expected: 7 new commits with feat(...) / docs(...) prefixes
```

## Post-Deploy Soak (operator step — after restart)

After the user restarts the bot, monitor for 24 hours and check the warehouse:

```bash
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 86400
print('Area 4 (auto_breakeven_2h):', c.execute('''SELECT COUNT(*), ROUND(SUM(realized_pnl),2)
    FROM trades WHERE status=\"CLOSED\" AND ts_exit >= ?
    AND exit_reason = \"auto_breakeven_2h\"''', (since,)).fetchone())
print('Area 5 (auto_small_tp_1pct):', c.execute('''SELECT COUNT(*), ROUND(SUM(realized_pnl),2), ROUND(AVG(realized_pnl),3)
    FROM trades WHERE status=\"CLOSED\" AND ts_exit >= ?
    AND exit_reason = \"auto_small_tp_1pct\"''', (since,)).fetchone())
print('Ghost breakdown:', c.execute('''SELECT exit_reason, COUNT(*)
    FROM trades WHERE status=\"CLOSED\" AND ts_exit >= ?
    AND exit_reason LIKE \"%ghost%\" GROUP BY exit_reason''', (since,)).fetchall())
"
```

Expected after 24h:
- `auto_small_tp_1pct`: 3-6 fires at avg ~+$1.0-1.5
- `auto_breakeven_2h`: 3-5 fires
- `ghost_sync` count down 30-50%
- `ghost_reconciled` count up by a similar amount (Area 1 reclassification)

Rollback triggers documented in spec §4.4 — if anything trips, disable the offending area via its config flag and restart.

---

**Plan complete.** Estimated execution: 7 tasks × subagent dispatch + 2-stage review each ≈ 2-3 hours. Total diff: ~280 LOC across 5 modified files + 1 new test file.
