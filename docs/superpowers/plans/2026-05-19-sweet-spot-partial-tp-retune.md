# Sweet-Spot Partial-TP Retune Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Retune `PARTIAL_TP` config to fire earlier (35% of TP distance) and capture more of position (60%), targeting the bot's proven 67%-WR 30-60min hold-time sweet spot. Add 6 unit tests (first-ever direct PARTIAL_TP coverage) + a tiny testability extraction.

**Architecture:** Two-value config change in `config.py` `PARTIAL_TP` dict. Tiny extraction in `core/order_manager.py` of the fire-decision into a pure function `_should_fire_partial_tp(position, price, partial_tp_config) -> (should_fire, take_size, partial_level)` so the behavior tests exercise real code rather than parallel arithmetic. Single atomic commit on `feat/profitability-upgrade` after the bleed-fix sprint head.

**Tech Stack:** Python 3.9+, pytest, `unittest.mock`. No new dependencies.

---

## Context the implementer must know before starting

1. **Spec file:** `docs/superpowers/specs/2026-05-19-sweet-spot-partial-tp-retune-design.md` — read this first; the math, sensitivity, and risk register live there.

2. **Predecessor work:** Bleed-fix sprint shipped 7 commits ending at `81f5f49`. The spec commit for THIS work is on top of that. HEAD of `feat/profitability-upgrade` at plan time is the spec commit (`docs(retune): sweet-spot partial-TP retune design`).

3. **Current production state:**
   - `config.py` `PARTIAL_TP = {"enabled": True, "first_take_at_pct": 0.5, "first_take_size": 0.5, "move_sl_to_breakeven": True}`
   - `core/order_manager.py:2388-2414` contains the inline partial-TP firing logic, reading the config values via `PARTIAL_TP.get(...)`.
   - `core/order_manager.py:1640+` has `partial_close_position(exchange, position, take_sz, "partial_tp", price)` — already implemented; not changing.
   - Position side convention: `"buy"` / `"sell"` (NOT `"long"`/`"short"`).
   - The bot uses `loguru`, imported via `from loguru import logger` in `core/order_manager.py`. Don't introduce stdlib `logging` in the production code path.

4. **30d baseline (locked at deploy time, for §5 measurement reference):**
   - Net PnL: −$14.96 (n=238)
   - 30-60min bucket: +$10.05 / 33 trades / 67% WR / avg_win $0.55
   - WR overall: 44.9%
   - ATOM 30d: +$16.02 / 51 trades (proven-edge guard)

5. **Live bot is running on CONTROLLED_LIVE.** Module changes are picked up at user-initiated restart only. Your edits are safe to land; they don't take effect on the running process until the user restarts.

6. **Pre-commit hooks** (ruff, codespell, detect-secrets, pre-push pytest) must pass. The user's local environment does NOT have pre-commit installed; run `ruff check` manually before committing.

7. **Selective staging required:** the repo has substantial pre-existing unstaged mods on `core/order_manager.py` and `config.py` from prior unrelated work. Stage ONLY your retune hunks. Same pattern the bleed-fix sprint used. Do NOT stage random pre-existing mods.

8. **Test invariants** that must hold after the commit:
   - `python -m pytest tests/ -q --tb=no` shows ≥ baseline + 6 new tests passing (no new failures).
   - `python main.py --status` exits clean.
   - ATOM 30d PnL > 0 (proven-edge guard).

---

## File Structure

### NEW files

| Path | Responsibility | Touched by |
|---|---|---|
| `tests/test_partial_tp_retune.py` | 6 tests covering pinned config values and partial-TP fire-decision behavior | Task 1 |

### MODIFIED files

| Path | Change | Touched by |
|---|---|---|
| `config.py` | Update `PARTIAL_TP` dict: `first_take_at_pct` 0.5 → 0.35, `first_take_size` 0.5 → 0.6. Expand surrounding comment. | Task 1 |
| `core/order_manager.py` | Add `_should_fire_partial_tp(...)` module-level pure function (~15 LOC). Refactor `check_sl_tp` partial-TP branch at line ~2388-2414 to call the helper. Behavior unchanged at the refactor step; behavior changes after config edit. | Task 1 |

### NOT touched

- `core/order_manager.py` `partial_close_position` method (already correct).
- `core/position_tracker.py`, `core/risk_manager.py`, `core/mcp_brain.py` — unrelated to this change.
- `data/warehouse.sqlite`, `data/positions.json`, any state files.
- Bleed-fix sprint patches (Patches #0, #2, #3) — all preserved unchanged.

---

## Task 1 — Sweet-spot partial-TP retune (single atomic commit)

**Files:**
- Modify: `config.py` (2 dict values + comment expansion)
- Modify: `core/order_manager.py:2388-2414` (extract + use helper)
- Create: `tests/test_partial_tp_retune.py`

The full task is 12 steps. One commit at the end captures (a) the test additions, (b) the testability extraction, (c) the config change. They are bundled because they're co-dependent: pin tests would fail if config isn't updated; behavior tests need the helper to exist; config change without tests is unprotected.

### Step 1: Capture pytest baseline pass count

- [ ] Run:
```bash
python -m pytest tests/ -q --tb=no 2>&1 | tail -3
```
Expected output: `XXXX passed, Y skipped` (record XXXX as `BASELINE_PASS_COUNT`). After Task 1 should be `BASELINE_PASS_COUNT + 6` passing.

If any failures show up here that ARE NOT in `tests/test_risk_auto_resume.py` or `tests/test_live_risk_caps.py`, STOP and report — Task 1 is going on top of an unstable baseline.

### Step 2: Capture warehouse 30-60min bucket baseline for measurement reference

- [ ] Run:
```bash
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 30*86400
row = c.execute('''SELECT COUNT(*),
    ROUND(SUM(realized_pnl),2),
    ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),0),
    ROUND(AVG(CASE WHEN realized_pnl>0 THEN realized_pnl END),3)
    FROM trades WHERE status=\"CLOSED\" AND ts_entry>=?
    AND hold_sec>=1800 AND hold_sec<3600''', (since,)).fetchone()
print(f'30-60min: n={row[0]}  pnl=\${row[1]}  WR={row[2]}%  avg_win=\${row[3]}')
"
```
Expected output approximately: `30-60min: n=33 pnl=$10.05 WR=67% avg_win=$0.55`. Record this in your task notes for §5 measurement comparison.

### Step 3: Read the existing partial-TP code in `core/order_manager.py`

- [ ] Open `core/order_manager.py` lines 2388-2414 and read:
```python
# ── PARTIAL TAKE PROFIT ──
try:
    from config import PARTIAL_TP
    if (PARTIAL_TP.get("enabled") and not pos.partial_taken
            and pos.take_profit and pos.entry_price):
        take_at = PARTIAL_TP.get("first_take_at_pct", 0.5)
        take_sz = PARTIAL_TP.get("first_take_size", 0.5)
        if pos.side == "buy":
            tp_dist = pos.take_profit - pos.entry_price
            partial_level = pos.entry_price + tp_dist * take_at
            if price >= partial_level:
                logger.info(
                    f"[Orders] PARTIAL TP: {pos.symbol} BUY "
                    f"@ {price:.4f} ({take_at:.0%} of TP)")
                self.partial_close_position(
                    exchange, pos, take_sz, "partial_tp", price)
        else:
            tp_dist = pos.entry_price - pos.take_profit
            partial_level = pos.entry_price - tp_dist * take_at
            if price <= partial_level:
                logger.info(
                    f"[Orders] PARTIAL TP: {pos.symbol} SELL "
                    f"@ {price:.4f} ({take_at:.0%} of TP)")
                self.partial_close_position(
                    exchange, pos, take_sz, "partial_tp", price)
except ImportError:
    pass
```

Confirm this is what you'll be extracting and replacing.

### Step 4: Write the 6 tests in `tests/test_partial_tp_retune.py`

- [ ] Create the file with this exact content:

```python
"""Tests for the sweet-spot partial-TP retune (2026-05-19).

PARTIAL_TP had zero unit tests despite being live for weeks. This file
establishes coverage AND pins the new threshold values so a future
accidental revert is caught immediately.

The behavior tests (3-6) exercise the extracted helper
core.order_manager._should_fire_partial_tp directly. The pin tests
(1-2) read config.PARTIAL_TP straight from the config module.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest


# -- Pin tests (catch accidental revert of config values) --------------

def test_partial_tp_default_first_take_at_pct_is_035():
    """Retune value pinned at 0.35 (was 0.5 before 2026-05-19)."""
    from config import PARTIAL_TP
    assert PARTIAL_TP["first_take_at_pct"] == 0.35


def test_partial_tp_default_first_take_size_is_06():
    """Retune value pinned at 0.6 (was 0.5 before 2026-05-19)."""
    from config import PARTIAL_TP
    assert PARTIAL_TP["first_take_size"] == 0.6


# -- Behavior tests via the extracted _should_fire_partial_tp helper --

def _stub_position(side, entry, tp, partial_taken=False):
    pos = MagicMock()
    pos.symbol = "ATOM/USDT:USDT"
    pos.side = side
    pos.entry_price = entry
    pos.take_profit = tp
    pos.partial_taken = partial_taken
    return pos


def test_partial_fires_at_35pct_long():
    """Long: entry=100, tp=105. Price at 101.75 (35% of TP distance) fires partial with size=0.6."""
    from core.order_manager import _should_fire_partial_tp
    from config import PARTIAL_TP

    pos = _stub_position(side="buy", entry=100.0, tp=105.0)
    # Price exactly at 35% of TP distance: 100 + 0.35*(105-100) = 101.75
    should_fire, take_sz, partial_level = _should_fire_partial_tp(
        pos, 101.75, PARTIAL_TP)
    assert should_fire is True
    assert take_sz == 0.6
    assert abs(partial_level - 101.75) < 1e-9


def test_partial_fires_at_35pct_short():
    """Short mirror: entry=100, tp=95. Price at 98.25 (35% of TP distance below entry)."""
    from core.order_manager import _should_fire_partial_tp
    from config import PARTIAL_TP

    pos = _stub_position(side="sell", entry=100.0, tp=95.0)
    # Price exactly at 35% of TP distance: 100 - 0.35*(100-95) = 98.25
    should_fire, take_sz, partial_level = _should_fire_partial_tp(
        pos, 98.25, PARTIAL_TP)
    assert should_fire is True
    assert take_sz == 0.6
    assert abs(partial_level - 98.25) < 1e-9


def test_partial_does_not_fire_below_35pct():
    """Long at 101.70 (34% of TP distance) is below threshold; no fire."""
    from core.order_manager import _should_fire_partial_tp
    from config import PARTIAL_TP

    pos = _stub_position(side="buy", entry=100.0, tp=105.0)
    should_fire, _, partial_level = _should_fire_partial_tp(
        pos, 101.70, PARTIAL_TP)
    assert should_fire is False
    assert abs(partial_level - 101.75) < 1e-9  # level is correct, just not reached


def test_partial_taken_flag_prevents_double_fire():
    """Position with partial_taken=True never re-fires, even at higher price."""
    from core.order_manager import _should_fire_partial_tp
    from config import PARTIAL_TP

    pos = _stub_position(side="buy", entry=100.0, tp=105.0, partial_taken=True)
    # Price at 102.5 (50% of TP distance) — would normally fire if partial_taken=False
    should_fire, _, _ = _should_fire_partial_tp(pos, 102.5, PARTIAL_TP)
    assert should_fire is False
```

### Step 5: Run the tests, verify they FAIL with current code

- [ ] Run:
```bash
python -m pytest tests/test_partial_tp_retune.py -v
```
Expected: all 6 failures (mix of `AssertionError` for pin tests since config still says 0.5, and `ImportError: cannot import name '_should_fire_partial_tp'` for behavior tests since helper doesn't exist yet).

### Step 6: Extract `_should_fire_partial_tp` helper in `core/order_manager.py`

- [ ] Add this module-level function near the existing module-level helpers (e.g., near `_try_soft_close` if present from the bleed-fix sprint, OR near the top of the file just after imports — either is fine; the test imports it directly):

```python
def _should_fire_partial_tp(position, price: float, partial_tp_config: dict):
    """Pure decision function: should the partial-TP fire on this position at this price?

    Returns:
        (should_fire: bool, take_size: float, partial_level: float)

    Inputs:
        position: object with .side ('buy'/'sell'), .entry_price, .take_profit, .partial_taken
        price: current monitor-cycle price for the position
        partial_tp_config: the config.PARTIAL_TP dict

    Extracted from the inline check_sl_tp logic on 2026-05-19 to enable direct
    unit testing. Behavior is byte-equivalent to the original inline block.
    """
    if not partial_tp_config.get("enabled"):
        return (False, 0.0, 0.0)
    if getattr(position, "partial_taken", False):
        return (False, 0.0, 0.0)
    if not (position.take_profit and position.entry_price):
        return (False, 0.0, 0.0)

    take_at = partial_tp_config.get("first_take_at_pct", 0.5)
    take_sz = partial_tp_config.get("first_take_size", 0.5)

    if position.side == "buy":
        tp_dist = position.take_profit - position.entry_price
        partial_level = position.entry_price + tp_dist * take_at
        return (price >= partial_level, take_sz, partial_level)
    else:
        tp_dist = position.entry_price - position.take_profit
        partial_level = position.entry_price - tp_dist * take_at
        return (price <= partial_level, take_sz, partial_level)
```

### Step 7: Replace the inline partial-TP block in `check_sl_tp` to call the helper

- [ ] In `core/order_manager.py`, find the existing block at lines ~2388-2414 (the `# ── PARTIAL TAKE PROFIT ──` block shown in Step 3 above) and replace it with:

```python
            # ── PARTIAL TAKE PROFIT ──
            try:
                from config import PARTIAL_TP
                should_fire, take_sz, _level = _should_fire_partial_tp(
                    pos, price, PARTIAL_TP)
                if should_fire:
                    take_at = PARTIAL_TP.get("first_take_at_pct", 0.5)
                    logger.info(
                        f"[Orders] PARTIAL TP: {pos.symbol} {pos.side.upper()} "
                        f"@ {price:.4f} ({take_at:.0%} of TP)")
                    self.partial_close_position(
                        exchange, pos, take_sz, "partial_tp", price)
            except ImportError:
                pass
```

This collapses the if-else side-branching into the helper. Behavior is identical to the prior code (verified by Step 9 full-suite run — any existing test that depends on partial-TP behavior must still pass).

### Step 8: Update `config.py` `PARTIAL_TP` dict

- [ ] Open `config.py`, find the existing `PARTIAL_TP` definition (search for `PARTIAL_TP` to locate it — it's a single dict near the runtime config block). Replace the dict with:

```python
# 2026-05-19 sweet-spot retune. Captures more wins in the empirically
# proven 30-60min hold-time cell (67% WR, +$10.05 / 33 trades / 30d).
# Lowered first_take_at_pct from 0.5 to 0.35 (fires earlier) and raised
# first_take_size from 0.5 to 0.6 (books larger chunk early), so more
# positions book a small win before deteriorating into the
# 120-240min bleed band (-$23.63 / 30d).
# Rollback: revert both values to 0.5, restart bot. Sub-1-minute reversal.
# Spec: docs/superpowers/specs/2026-05-19-sweet-spot-partial-tp-retune-design.md
PARTIAL_TP = {
    "enabled": True,
    "first_take_at_pct": 0.35,
    "first_take_size": 0.6,
    "move_sl_to_breakeven": True,
}
```

If a similar comment block already exists above the current `PARTIAL_TP` definition, replace it entirely with the block above (don't accumulate stale-narrative comments).

### Step 9: Run the new tests, verify they PASS

- [ ] Run:
```bash
python -m pytest tests/test_partial_tp_retune.py -v
```
Expected: 6 passed.

### Step 10: Run full test suite, verify no regressions

- [ ] Run:
```bash
python -m pytest tests/ -q --tb=no --ignore=tests/test_risk_auto_resume.py --ignore=tests/test_live_risk_caps.py 2>&1 | tail -5
```
Expected: pass count = BASELINE_PASS_COUNT + 6, 0 failures.

If any test that previously passed now fails, STOP. The refactor in Step 7 may have changed behavior unintentionally — diff the new code against the original (Step 3 snapshot) and resolve before commit.

### Step 11: Sanity check the bot startup

- [ ] Run:
```bash
python main.py --status
```
Expected: clean exit. Dashboard renders position/PnL summary, no Python tracebacks.

### Step 12: Lint check on the changed files

- [ ] Run:
```bash
ruff check tests/test_partial_tp_retune.py core/order_manager.py config.py
```
Expected: no new errors introduced by your changes. Pre-existing warnings in unstaged hunks are not your problem; report them if you see them but don't fix.

### Step 13: Commit (selective staging)

- [ ] The repo has pre-existing unstaged mods on `core/order_manager.py` and `config.py`. Use selective staging:

```bash
git add tests/test_partial_tp_retune.py
git add -p core/order_manager.py     # stage only the _should_fire_partial_tp + call-site hunks
git add -p config.py                  # stage only the PARTIAL_TP dict + comment block hunk
```

For each `-p` prompt, accept (`y`) ONLY the hunks that are your changes; reject (`n`) any pre-existing unstaged hunks (e.g., from prior unrelated work). If you accidentally stage a pre-existing hunk, run `git reset HEAD <file>` and start the patch staging again.

- [ ] Verify the stage:
```bash
git diff --staged --stat
```
Expected: 3 files changed; only `tests/test_partial_tp_retune.py` (new), `core/order_manager.py` (~25 lines), `config.py` (~10 lines) should appear with sensible line counts.

- [ ] Commit:
```bash
git commit -m "$(cat <<'EOF'
feat(retune): partial-TP fires at 35% of TP, size 0.6 (sweet-spot capture)

Config change targeting the proven 67%-WR 30-60min hold-time cell
(+$10.05 / 33 trades / 30d). Tightens PARTIAL_TP first_take_at_pct
0.5 -> 0.35 (fires earlier on positions that touched but didn't reach
the 50% threshold) and raises first_take_size 0.5 -> 0.6 (books larger
partial chunk).

Refactor: extracted _should_fire_partial_tp(position, price, config)
into a module-level pure function in core/order_manager.py. Behavior
is byte-equivalent to the prior inline block at check_sl_tp; the
extraction enables direct unit testing.

Tests: 6 new tests in tests/test_partial_tp_retune.py establishing the
first-ever direct PARTIAL_TP coverage. 2 pin tests catch accidental
config revert; 4 behavior tests exercise the extracted helper for
long/short fire conditions, below-threshold no-fire, and partial_taken
double-fire prevention.

Expected effect (sensitivity in spec section 3.3):
- 20 wiggle wins / 100 trades: +$16.51 net  (point estimate, +$15 to +$25 / 30d)
- 10 wiggle wins / 100 trades: +$1.51 net   (break-even)
-  5 wiggle wins / 100 trades: -$4.99 net   (mild bleed; rollback)

Rollback: revert PARTIAL_TP dict to 0.5/0.5, restart. <1 minute.
Spec: docs/superpowers/specs/2026-05-19-sweet-spot-partial-tp-retune-design.md

Co-Authored-By: RuFlo <ruv@ruv.net>
EOF
)"
```

- [ ] Verify the commit landed:
```bash
git log --oneline -3
```
Expected: top of log shows the new commit `feat(retune): partial-TP fires at 35% of TP...` on top of the spec commit.

---

## Verification (overall, post-commit)

Run after Task 1's commit lands:

```bash
# 1. All tests still green
python -m pytest tests/ -q --tb=no --ignore=tests/test_risk_auto_resume.py --ignore=tests/test_live_risk_caps.py
```
Expected: BASELINE_PASS_COUNT + 6 passing, 0 failures.

```bash
# 2. Partial-TP tests specifically
python -m pytest tests/test_partial_tp_retune.py -v
```
Expected: 6 passed.

```bash
# 3. Config values picked up at import time
python -c "from config import PARTIAL_TP; assert PARTIAL_TP['first_take_at_pct'] == 0.35; assert PARTIAL_TP['first_take_size'] == 0.6; print('config OK')"
```
Expected: `config OK`.

```bash
# 4. Helper is importable and works on long/short
python -c "
from core.order_manager import _should_fire_partial_tp
from config import PARTIAL_TP
class P: pass
p = P(); p.side='buy'; p.entry_price=100.0; p.take_profit=105.0; p.partial_taken=False
print('long@101.75:', _should_fire_partial_tp(p, 101.75, PARTIAL_TP))
p2 = P(); p2.side='sell'; p2.entry_price=100.0; p2.take_profit=95.0; p2.partial_taken=False
print('short@98.25:', _should_fire_partial_tp(p2, 98.25, PARTIAL_TP))
"
```
Expected: both print `(True, 0.6, <level>)`.

```bash
# 5. Bot status check still clean
python main.py --status
```
Expected: clean exit.

```bash
# 6. ATOM 30d guard still positive
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 30*86400
row = c.execute('SELECT SUM(realized_pnl), COUNT(*) FROM trades WHERE symbol=\"ATOM/USDT:USDT\" AND status=\"CLOSED\" AND ts_entry >= ?', (since,)).fetchone()
assert row[0] is not None and row[0] > 0, f'ATOM 30d PnL {row[0]} must be > 0'
print(f'ATOM 30d: n={row[1]} pnl=\${row[0]:.2f}')
"
```
Expected: ATOM 30d PnL > 0.

```bash
# 7. Commit log clean
git log --oneline feat/profitability-upgrade -5
```
Expected top commit: `feat(retune): partial-TP fires at 35% of TP, size 0.6 (sweet-spot capture)`.

---

## Deferred to user (post-deploy measurement)

The implementer's job ends at commit. The following are the user's measurement steps per spec §5:

**At deploy time (user-initiated bot restart):**
- Record the timestamp. Subsequent measurement windows reference this.

**Day 7 signal check:**
```bash
python scripts/sprint_kpi.py --since 2026-05-19 --markdown
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 7*86400
row = c.execute('''SELECT COUNT(*), ROUND(SUM(realized_pnl),2),
    ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),0)
    FROM trades WHERE status=\"CLOSED\" AND ts_entry>=?
    AND hold_sec>=1800 AND hold_sec<3600''', (since,)).fetchone()
print(f'7d 30-60min: n={row[0]}  pnl=\${row[1]}  WR={row[2]}%')
"
```

**Pass/fail criteria** (spec §5.3):

| Signal | Pass | Borderline | Rollback trigger |
|---|---|---|---|
| 30-60min bucket WR | ≥ 60% | 55–60% | < 55% over 20+ trades |
| 30-60min bucket count | ≥ 12 (vs current ~8/week) | 8–12 | < 8 |
| Net 7d PnL | ≥ 0 | −$5 to 0 | < −$5 |
| Partial-TP exit-reason count | ≥ 8 in 7d | 4–8 | < 4 (config not picked up?) |

**Rollback (sub-1-minute):**
1. Edit `config.py` `PARTIAL_TP` dict back to `first_take_at_pct: 0.5, first_take_size: 0.5`.
2. Restart bot.
3. The 2 pin tests will fail on next pytest run — that's expected and a clear signal the rollback is in effect. Remove or update `tests/test_partial_tp_retune.py` tests 1-2 as the operator decides whether the rollback is temporary or permanent.

---

## Risk and rollback summary

| Risk | Rollback action | Time |
|---|---|---:|
| 30-60min cell WR drops < 55% over 20+ trades | Revert `PARTIAL_TP` dict to 0.5/0.5 in `config.py`, restart | <1m |
| Net 7d PnL < −$5 | Revert PARTIAL_TP, restart | <1m |
| Partial-TP exit-reason count < 4 in 7d | Investigate: config picked up? Bot restarted? Check logs for `[Orders] PARTIAL TP:` lines. If 0, the bot hasn't reloaded; user must restart. | <5m |
| Any test in `tests/test_partial_tp_retune.py` fails after subsequent unrelated changes | Diagnose the failing test; if pin test fails, someone reverted the config — find out who. If behavior test fails, the helper logic was changed. | varies |

---

## Spec-coverage self-review

| Spec section | Plan task | Status |
|---|---|---|
| §2 The Change (config dict edit) | Task 1 Step 8 | ✓ |
| §3 Expected Effect (math/sensitivity) | Not implemented in code — informational; referenced in commit message | ✓ |
| §4 Tests Added (6 tests) | Task 1 Step 4, all 6 named exactly per spec | ✓ |
| §5 Measurement Plan | "Deferred to user" block at end of plan | ✓ |
| §6 Rollback | "Risk and rollback summary" + spec §5.3 reproduced in deferred-to-user block | ✓ |
| §7 Risk Register | "Risk and rollback summary" + commit message body | ✓ |
| §8 Out of Scope | Not implemented (correctly out of scope); preserved by Task 1 scope | ✓ |
| §9 Decision Summary | Captured in commit message body | ✓ |
| §10 Plan-file persistence note | Acknowledged: this plan file is being written via Write; if persistence fails again, the implementer references the spec + this task list directly from conversation. | ✓ |

No spec section unaddressed.
