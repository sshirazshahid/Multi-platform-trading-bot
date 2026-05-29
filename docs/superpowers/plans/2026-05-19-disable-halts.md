# Disable All Halt Mechanisms + Verify Tiered Leverage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Disable all 7 loss-driven halt/pause mechanisms by adding a single `HALT_MECHANISMS` config dict, gate each trigger site with its flag, and bundle a read-only verification of tiered-leverage wiring. Single atomic commit on `feat/profitability-upgrade`.

**Architecture:** New `HALT_MECHANISMS = {...}` dict in `config.py` with 7 boolean keys. Each existing halt-trigger site in `core/risk_manager.py` and `core/auto_mutator.py` gets a flag-check prepended: `if not HALT_MECHANISMS["<name>"]: skip-the-halt`. Existing halt CODE remains; only the trigger short-circuits. Sub-1-minute rollback by flipping any flag back to `True`. Path A leverage verification is read-only with a bundled 1-line fix only if the verification finds a routing bug.

**Tech Stack:** Python 3.9+, pytest, `unittest.mock`. No new dependencies.

---

## Context the implementer must know before starting

1. **Spec file:** `docs/superpowers/specs/2026-05-19-disable-halts-and-verify-tiered-leverage-design.md` — read this first; §7 (risk register) and §2.1 (the 7-mechanism inventory with line numbers) are load-bearing.

2. **Predecessor work:** Bleed-fix sprint (7 commits ending `81f5f49`) + partial-TP retune (`e0622e6`) are already deployed. HEAD of `feat/profitability-upgrade` at plan time is the halts-disable spec commit (`docs(halts): disable 7 halt/pause mechanisms...`).

3. **Critical pre-flight read:** the user has explicit informed consent. The L99 memory (single 99x trade = −$21.75) and the 44.9%-WR math were shown; user STILL chose all 7 mechanisms disabled. Don't second-guess via additional clarifying questions during implementation. If something is genuinely ambiguous in the spec, STOP and surface; don't invent.

4. **Phase 38 + Spec §12 hardening already added some flags:** `DRAWDOWN_HALT_ENABLED`, `SPEC12_SYMBOL_PAUSE_ENABLED`, `SPEC12_FAMILY_PAUSE_ENABLED` exist as legacy boolean constants. The new `HALT_MECHANISMS` dict is an OUTER gate — the mechanism fires only if BOTH the new dict key AND the legacy flag (where applicable) say True. Pattern:

   ```python
   if (HALT_MECHANISMS["drawdown_halt"]
           and DRAWDOWN_HALT_ENABLED   # legacy gate preserved
           and self._peak_balance > 0):
       drawdown = (self._peak_balance - effective_balance) / self._peak_balance
       if drawdown >= self.max_drawdown_pct and not self._halted:
           # existing halt logic
   ```

5. **AutoMutator has TWO blacklist write paths**, not one:
   - Symmetric per-symbol (`core/auto_mutator.py:183-192`): "BLACKLIST {sym} ... losses ({rate:.0%})"
   - SHORT-only per-symbol (`core/auto_mutator.py:211-223`): "SHORT-BLACKLIST {sym}"
   
   Both must be gated by `HALT_MECHANISMS["auto_mutator_blacklist"]`.

6. **Live bot is RUNNING on CONTROLLED_LIVE.** Module changes are picked up at user-initiated restart only. Edits are safe to land.

7. **Pre-commit hooks NOT installed locally.** Run `ruff check` manually. Don't use `--no-verify`.

8. **Selective staging required.** Repo has pre-existing unstaged mods on `core/risk_manager.py`, `core/auto_mutator.py`, and `config.py` from prior unrelated work. Use `git add -p` for these three; stage ONLY the halts-disable hunks. Same pattern Tasks 1–5 of the bleed-fix sprint used.

9. **Test invariants:**
   - `python -m pytest tests/ -q --tb=no --ignore=tests/test_risk_auto_resume.py --ignore=tests/test_live_risk_caps.py` — pass count grows by exactly 8 (Test count after = BASELINE + 8). No regressions.
   - `python main.py --status` exits clean.
   - ATOM 30d PnL > 0 (proven-edge guard).
   - **The Task 5 soak test `tests/test_spec12_post_sprint.py` will START FAILING after this commit** because it asserts Spec §12 trips on 5 losses, and we just disabled Spec §12 trip. **This is expected.** The implementer must update that test (see Step 7).

---

## File Structure

### NEW files

| Path | Responsibility |
|---|---|
| `tests/test_halt_mechanisms_disabled.py` | 8 tests covering pin (1), 6 mechanisms disabled (2-7), and re-enable proof (8) |

### MODIFIED files

| Path | Change | Line locations |
|---|---|---|
| `config.py` | Add `HALT_MECHANISMS` dict (7 boolean keys) | Append after the partial-TP retune block from `e0622e6` |
| `core/risk_manager.py` | Gate 6 halt sites with the new dict | Lines `901, 914, 1109, 1120, 1132, 1158` (per Step 8) |
| `core/auto_mutator.py` | Gate 2 blacklist-write sites | Lines `~187, ~216` (per Step 9) |
| `tests/test_spec12_post_sprint.py` | Update assertion: the soak test for the 5-consec halt cannot pass when the halt is disabled. Patch the test to monkeypatch `HALT_MECHANISMS["spec12_streak_halt"] = True` for its duration so it tests THE MECHANISM, not the deployment state. | Whole test body |

### NOT touched

- `core/order_manager.py` — only if Path A verification (Step 11) finds a tiered-leverage routing bug. Read-only otherwise.
- `core/position_tracker.py`, `core/mcp_brain.py`, the partial-TP retune from `e0622e6`, the bleed-fix sprint patches — all preserved.
- `data/warehouse.sqlite`, `data/positions.json`, any state files — read-only.

---

## Task 1 — Disable 7 halt mechanisms + verify tiered leverage (single atomic commit)

**Files:**
- Modify: `config.py` (add `HALT_MECHANISMS` dict)
- Modify: `core/risk_manager.py` (6 gate-site edits)
- Modify: `core/auto_mutator.py` (2 gate-site edits)
- Modify: `tests/test_spec12_post_sprint.py` (update to monkeypatch the flag True for test duration)
- Create: `tests/test_halt_mechanisms_disabled.py` (8 new tests)
- Verify: `core/order_manager.py` + `core/bot_engine.py` + `core/mcp_brain.py` (read-only; 1-line fix only if Path A finds a bug)

### Step 1: Capture pytest baseline

- [ ] Run:
```bash
python -m pytest tests/ -q --tb=no --ignore=tests/test_risk_auto_resume.py --ignore=tests/test_live_risk_caps.py 2>&1 | tail -3
```
Record output as `BASELINE_PASS_COUNT`. Expected: approximately 1020 passing (1014 bleed-fix sprint + 6 partial-TP retune).

If any failure shows up here OUTSIDE the two ignored files, STOP — unstable baseline.

### Step 2: Capture risk_state baseline

- [ ] Run:
```bash
python -c "
import json
rs = json.load(open('data/risk_state.json'))
print(f'is_halted={rs[\"is_halted\"]} halt_reason={rs[\"halt_reason\"]!r}')
print(f'symbol_pauses={len(rs.get(\"symbol_pauses\", {}))}')
print(f'family_pauses={len(rs.get(\"family_pauses\", {}))}')
print(f'daily_pnl={rs.get(\"daily_pnl\")}')
"
```
Expected: `is_halted=False halt_reason=''`, symbol/family pauses empty. Record for post-commit comparison.

### Step 3: Read the 6 risk_manager halt sites

- [ ] Read `core/risk_manager.py` at these lines so you understand the exact code being gated:
  - **Site A (Daily PnL halt):** lines 898–910 — `daily_loss_limit = ...; if self._daily_pnl < -daily_loss_limit and not self._halted: self._halted = True; self._halt_reason = "daily loss ..."`
  - **Site B (Drawdown halt):** lines 912–927 — `if DRAWDOWN_HALT_ENABLED and self._peak_balance > 0: drawdown = ...; if drawdown >= self.max_drawdown_pct and not self._halted:` (note: this site is ALREADY gated by `DRAWDOWN_HALT_ENABLED`; we add a new outer gate on top)
  - **Site D (Symbol pause):** lines 1109–1117 — `if _F12S and len(sym_hist) >= SPEC_SYMBOL_LOSSES_TO_PAUSE and not any(sym_hist[-...:]): until = ...; self._symbol_pauses[symbol] = until`
  - **Site E (Family pause):** lines 1120–1129 — same shape with `_F12F` and `_family_pauses`
  - **Site C (Spec §12 5-consec global):** lines 1132–1151 — `if len(self._global_streak) >= SPEC_GLOBAL_LOSSES_TO_REVIEW and not any(self._global_streak[-...:]): self._write_review_flag(...); self._halted = True; self._halt_reason = "spec12:..."`
  - **Site F (Outlier loss flag):** lines 1153–1167 — `if pnl_usd < -abs(_max_loss): self._write_review_flag(reason="outlier_loss(...)", action="manual_review", ...)`

### Step 4: Read the 2 AutoMutator gate sites

- [ ] Read `core/auto_mutator.py`:
  - **Site G1 (symmetric per-symbol blacklist):** lines ~183–192 — `if n_loss >= SYMBOL_LOSS_BLACKLIST and rate >= SYMBOL_BLACKLIST_MIN_RATE: ... self._state["blacklist"][sym] = now + SYMBOL_BLACKLIST_HOURS * 3600`
  - **Site G2 (SHORT-only blacklist):** lines ~211–223 — `if (n_loss >= SHORT_SYMBOL_LOSS_BLACKLIST and rate >= SHORT_SYMBOL_BLACKLIST_MIN_RATE): key = f"SHORT:{sym}"; ... self._state["blacklist"][key] = now + SHORT_SYMBOL_BLACKLIST_HOURS * 3600`

Both must be gated by the same `HALT_MECHANISMS["auto_mutator_blacklist"]` flag.

### Step 5: Add `HALT_MECHANISMS` dict to `config.py`

- [ ] Append to `config.py` (anywhere after the partial-TP retune block; place near the end of the file or near other risk constants):

```python
# 2026-05-19 — User directive: "Don't halt or pause when losing trades."
# All seven loss-driven halt/pause mechanisms disabled. Flip any individual
# key to True to re-enable just that mechanism; restart bot to apply.
# Nuclear restore: set every value to True, restart. Sub-1-minute reversal.
#
# Per-position SL/TP still placed on every entry (Patches #0/#2/#3 from
# bleed-fix sprint verify exchange-side SL alive). Exchange-side liquidation
# still applies (not bot's control).
#
# Risk register: docs/superpowers/specs/2026-05-19-disable-halts-and-verify-tiered-leverage-design.md §7
HALT_MECHANISMS = {
    "daily_pnl_halt":         False,  # A: was True (gates core/risk_manager.py:~901)
    "drawdown_halt":          False,  # B: was True (gates core/risk_manager.py:~914)
    "spec12_streak_halt":     False,  # C: was True (gates core/risk_manager.py:~1132)
    "symbol_pause":           False,  # D: was True (gates core/risk_manager.py:~1109)
    "family_pause":           False,  # E: was True (gates core/risk_manager.py:~1120)
    "outlier_loss_flag":      False,  # F: was True (gates core/risk_manager.py:~1158)
    "auto_mutator_blacklist": False,  # G: was True (gates core/auto_mutator.py:~187, ~216)
}
```

### Step 6: Write the 8 tests in `tests/test_halt_mechanisms_disabled.py`

- [ ] Create the file with this content (full code — no shortcuts):

```python
"""Tests for the 2026-05-19 halt-disable directive.

The user instructed: "Don't halt or pause when losing trades." This file
verifies that with HALT_MECHANISMS flags = False, none of the 7 mechanisms
fire — AND that flipping ONE flag back to True restores just that mechanism
(Test 8 — the critical anti-revert protection).
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import MagicMock

import pytest


# Test 1 — pin all flags = False (catches accidental revert)

def test_halt_mechanisms_all_disabled_by_default():
    """All 7 mechanisms must be False in checked-in config."""
    from config import HALT_MECHANISMS
    expected_keys = {
        "daily_pnl_halt", "drawdown_halt", "spec12_streak_halt",
        "symbol_pause", "family_pause", "outlier_loss_flag",
        "auto_mutator_blacklist",
    }
    assert set(HALT_MECHANISMS.keys()) == expected_keys, \
        f"HALT_MECHANISMS keys drifted: {set(HALT_MECHANISMS.keys())}"
    for key, value in HALT_MECHANISMS.items():
        assert value is False, f"HALT_MECHANISMS[{key!r}] is {value}, expected False"


# Helper: seed risk_state and chdir so risk_manager writes into tmp dir

def _seed_risk_state(tmp_path):
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    rs = {
        "is_halted": False, "halt_reason": "", "halt_time": None,
        "daily_pnl": 0.0, "max_drawdown_pct": 0.0,
        "start_balance": 500.0, "peak_balance": 500.0,
        "trading_day": "2026-05-19",
        "trades_today": 0,
        "recent_results": [], "trade_history": [],
        "symbol_pauses": {}, "family_pauses": {},
        "global_streak": [], "timestamp": 0,
    }
    (tmp_path / "data" / "risk_state.json").write_text(json.dumps(rs))


# Test 2 — daily PnL halt does NOT fire when flag off

def test_daily_pnl_loss_does_not_halt_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager(initial_balance=500.0)

    # Drive daily_pnl far below the 1% threshold ($5) by recording large losses
    for i in range(3):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT", family="claude_portfolio",
            is_win=False, pnl_usd=-3.0, pnl_pct=-1.0, reason="stop_loss")

    # Now drive update_balance with current balance reflecting the cumulative loss
    rm.update_balance(491.0)  # equity down to $491, daily_pnl now -$9

    assert rm._halted is False, \
        f"Daily PnL halt fired when flag is off: halt_reason={rm._halt_reason}"
    assert not (tmp_path / "data" / "review_required.json").exists()


# Test 3 — drawdown halt does NOT fire when flag off

def test_drawdown_does_not_halt_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager(initial_balance=500.0)
    rm._peak_balance = 500.0

    # 12% drawdown — well above the 8% threshold
    rm.update_balance(440.0)

    assert rm._halted is False, \
        f"Drawdown halt fired when flag is off: halt_reason={rm._halt_reason}"


# Test 4 — Spec §12 5-consec halt does NOT fire when flag off

def test_spec12_streak_does_not_halt_when_flag_off(tmp_path, monkeypatch):
    """Inverse of tests/test_spec12_post_sprint.py — with flag off, 5 losses must NOT halt."""
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager(initial_balance=500.0)

    # 5 consecutive losses — Spec §12 trigger condition
    for i in range(5):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT", family="claude_portfolio",
            is_win=False, pnl_usd=-0.50, pnl_pct=-1.5, reason="stop_loss")

    assert rm._halted is False, \
        f"Spec §12 halt fired when flag is off: halt_reason={rm._halt_reason}"
    assert not (tmp_path / "data" / "review_required.json").exists(), \
        "review_required.json was written despite spec12_streak_halt=False"


# Test 5 — symbol pause does NOT set when flag off

def test_symbol_pause_not_set_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager(initial_balance=500.0)

    sym = "ATOM/USDT:USDT"
    # 2 consecutive losses on the same symbol — symbol-pause trigger
    rm.record_trade_result(symbol=sym, family="claude_portfolio",
                            is_win=False, pnl_usd=-0.5, pnl_pct=-1.5, reason="stop_loss")
    rm.record_trade_result(symbol=sym, family="claude_portfolio",
                            is_win=False, pnl_usd=-0.5, pnl_pct=-1.5, reason="stop_loss")

    assert rm._symbol_pauses.get(sym, 0) == 0, \
        f"Symbol pause set when flag is off: {sym} until {rm._symbol_pauses.get(sym)}"


# Test 6 — family pause does NOT set when flag off

def test_family_pause_not_set_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager(initial_balance=500.0)

    fam = "claude_portfolio"
    # 3 consecutive losses on the same family — family-pause trigger
    for i in range(3):
        rm.record_trade_result(symbol=f"X{i}/USDT:USDT", family=fam,
                                is_win=False, pnl_usd=-0.5, pnl_pct=-1.5, reason="stop_loss")

    assert rm._family_pauses.get(fam, 0) == 0, \
        f"Family pause set when flag is off: {fam} until {rm._family_pauses.get(fam)}"


# Test 7 — outlier-loss flag does NOT write when flag off

def test_outlier_loss_does_not_write_flag_when_off(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager
    rm = RiskManager(initial_balance=500.0)

    # Single loss exceeding MAX_LOSS_PER_TRADE_USD = $15
    rm.record_trade_result(symbol="X/USDT:USDT", family="claude_portfolio",
                            is_win=False, pnl_usd=-20.0, pnl_pct=-4.0, reason="stop_loss")

    assert not (tmp_path / "data" / "review_required.json").exists(), \
        "Outlier-loss review_required.json written despite outlier_loss_flag=False"


# Test 8 — re-enabling one flag restores that mechanism (anti-revert proof)

def test_re_enabling_one_flag_restores_that_mechanism(tmp_path, monkeypatch):
    """Critical: flipping spec12_streak_halt back to True must restore the halt.

    Proves the gate is wired correctly and rollback works. If this test fails,
    the gate code path is wrong and the rollback procedure won't work.
    """
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    import config
    # Patch the dict in-place; the gate sites read from the live module
    monkeypatch.setitem(config.HALT_MECHANISMS, "spec12_streak_halt", True)

    from core.risk_manager import RiskManager
    rm = RiskManager(initial_balance=500.0)

    # 5 consecutive losses with mechanism re-enabled — must trip
    for i in range(5):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT", family="claude_portfolio",
            is_win=False, pnl_usd=-0.50, pnl_pct=-1.5, reason="stop_loss")

    halted = bool(getattr(rm, "_halted", False))
    review_file_exists = (tmp_path / "data" / "review_required.json").exists()
    assert halted or review_file_exists, (
        "Spec §12 did NOT trip with spec12_streak_halt=True. "
        "Either the gate code path is wrong or record_trade_result API drifted. "
        f"is_halted={halted}, review_required exists={review_file_exists}"
    )
```

### Step 7: Update the existing Spec §12 soak test

The Task 5 soak test at `tests/test_spec12_post_sprint.py` asserts that the 5-consec halt fires. With `spec12_streak_halt=False`, it would START FAILING after this commit. The fix: monkeypatch the flag True for the duration of that test.

- [ ] Open `tests/test_spec12_post_sprint.py` and read the existing test body. Around the line where `RiskManager(...)` is instantiated, ADD a `monkeypatch.setitem` for `HALT_MECHANISMS["spec12_streak_halt"]`:

Add this snippet just BEFORE the `from core.risk_manager import RiskManager` line in the existing test:

```python
    # 2026-05-19: halts are disabled by default in checked-in config; this test
    # asserts the halt mechanism still WORKS when re-enabled, not that the
    # default is True.
    import config
    monkeypatch.setitem(config.HALT_MECHANISMS, "spec12_streak_halt", True)
```

This change tests THE MECHANISM (does the halt trigger work when enabled?), not THE DEPLOYMENT STATE (is the halt currently active?). The Task 5 soak test's INTENT is preserved.

### Step 8: Verify Step 6 tests FAIL with current code

- [ ] Run:
```bash
python -m pytest tests/test_halt_mechanisms_disabled.py -v
```
Expected: Test 1 FAILS (no `HALT_MECHANISMS` in config). Tests 2-7 FAIL (mechanisms still fire). Test 8 may pass or fail depending on whether `monkeypatch.setitem` on a non-existent dict raises.

### Step 9: Add the 6 risk_manager gates

Open `core/risk_manager.py`. Apply these 6 in-place edits:

**Site A (line ~901) — Daily PnL halt.** Replace:
```python
        # Daily loss circuit-breaker
        if self._daily_pnl < -daily_loss_limit and not self._halted:
```
with:
```python
        # Daily loss circuit-breaker
        # Gated by HALT_MECHANISMS["daily_pnl_halt"] (2026-05-19)
        from config import HALT_MECHANISMS as _HM
        if (_HM.get("daily_pnl_halt", True)
                and self._daily_pnl < -daily_loss_limit
                and not self._halted):
```

**Site B (line ~914) — Drawdown halt.** Replace:
```python
        # Max drawdown circuit-breaker (with smart recovery)
        # Phase 38: gated by DRAWDOWN_HALT_ENABLED (operator can disable)
        if DRAWDOWN_HALT_ENABLED and self._peak_balance > 0:
```
with:
```python
        # Max drawdown circuit-breaker (with smart recovery)
        # Phase 38: gated by DRAWDOWN_HALT_ENABLED (operator can disable)
        # 2026-05-19: also gated by HALT_MECHANISMS["drawdown_halt"]
        if (_HM.get("drawdown_halt", True)
                and DRAWDOWN_HALT_ENABLED and self._peak_balance > 0):
```

(Note: `_HM` is already in scope from Site A; if A and B are not in the same method, re-import. If they ARE in the same method body, one import is enough.)

**Site D (line ~1109) — Symbol pause.** Replace:
```python
        # Per-symbol pause
        if _F12S and len(sym_hist) >= SPEC_SYMBOL_LOSSES_TO_PAUSE and not any(
            sym_hist[-SPEC_SYMBOL_LOSSES_TO_PAUSE:]
        ):
            until = now + SPEC_SYMBOL_PAUSE_HOURS * hour
            self._symbol_pauses[symbol] = until
```
with:
```python
        # Per-symbol pause
        # 2026-05-19: gated by HALT_MECHANISMS["symbol_pause"]
        from config import HALT_MECHANISMS as _HM2
        if (_HM2.get("symbol_pause", True)
                and _F12S and len(sym_hist) >= SPEC_SYMBOL_LOSSES_TO_PAUSE
                and not any(sym_hist[-SPEC_SYMBOL_LOSSES_TO_PAUSE:])):
            until = now + SPEC_SYMBOL_PAUSE_HOURS * hour
            self._symbol_pauses[symbol] = until
```

**Site E (line ~1120) — Family pause.** Replace:
```python
        # Per-family pause
        if _F12F and len(fam_hist) >= SPEC_FAMILY_LOSSES_TO_PAUSE and not any(
            fam_hist[-SPEC_FAMILY_LOSSES_TO_PAUSE:]
        ):
            key = family or "unknown"
            until = now + SPEC_FAMILY_PAUSE_HOURS * hour
            self._family_pauses[key] = until
```
with:
```python
        # Per-family pause
        # 2026-05-19: gated by HALT_MECHANISMS["family_pause"]
        if (_HM2.get("family_pause", True)
                and _F12F and len(fam_hist) >= SPEC_FAMILY_LOSSES_TO_PAUSE
                and not any(fam_hist[-SPEC_FAMILY_LOSSES_TO_PAUSE:])):
            key = family or "unknown"
            until = now + SPEC_FAMILY_PAUSE_HOURS * hour
            self._family_pauses[key] = until
```

(Both D and E use `_HM2` — the second import in the same method is fine; Python caches modules.)

**Site C (line ~1132) — Spec §12 5-consec halt.** Replace:
```python
        # 5 global consec → force observation + review
        if len(self._global_streak) >= SPEC_GLOBAL_LOSSES_TO_REVIEW and not any(
            self._global_streak[-SPEC_GLOBAL_LOSSES_TO_REVIEW:]
        ):
            self._write_review_flag(
                reason=f"{SPEC_GLOBAL_LOSSES_TO_REVIEW} consecutive global losses",
                action="force_observation_mode",
            )
            err_msg = (
                f"[Risk/Spec12] {SPEC_GLOBAL_LOSSES_TO_REVIEW} consecutive losses — "
                f"review flag written; bot_engine will refuse to open new trades "
                f"until OPERATING_MODE is manually cleared"
            )
            logger.error(err_msg)
            self._halted = True
            self._halt_reason = f"spec12:{SPEC_GLOBAL_LOSSES_TO_REVIEW}_consec_global_losses"
            self._halt_time = now
            self._notify_halt(
                f"SPEC §12 HALT: {SPEC_GLOBAL_LOSSES_TO_REVIEW} CONSECUTIVE LOSSES",
                err_msg,
            )
```
with:
```python
        # 5 global consec → force observation + review
        # 2026-05-19: gated by HALT_MECHANISMS["spec12_streak_halt"]
        if (_HM2.get("spec12_streak_halt", True)
                and len(self._global_streak) >= SPEC_GLOBAL_LOSSES_TO_REVIEW
                and not any(self._global_streak[-SPEC_GLOBAL_LOSSES_TO_REVIEW:])):
            self._write_review_flag(
                reason=f"{SPEC_GLOBAL_LOSSES_TO_REVIEW} consecutive global losses",
                action="force_observation_mode",
            )
            err_msg = (
                f"[Risk/Spec12] {SPEC_GLOBAL_LOSSES_TO_REVIEW} consecutive losses — "
                f"review flag written; bot_engine will refuse to open new trades "
                f"until OPERATING_MODE is manually cleared"
            )
            logger.error(err_msg)
            self._halted = True
            self._halt_reason = f"spec12:{SPEC_GLOBAL_LOSSES_TO_REVIEW}_consec_global_losses"
            self._halt_time = now
            self._notify_halt(
                f"SPEC §12 HALT: {SPEC_GLOBAL_LOSSES_TO_REVIEW} CONSECUTIVE LOSSES",
                err_msg,
            )
```

**Site F (line ~1158) — Outlier-loss flag.** Replace:
```python
        if pnl_usd < -abs(_max_loss):
            self._write_review_flag(
                reason=f"outlier_loss({pnl_usd:+.2f} USD beyond ${_max_loss:.2f} cap)",
                action="manual_review",
                symbol=symbol, family=family,
            )
            logger.error(
                f"[Risk/Spec12] Outlier loss {pnl_usd:+.2f} on {symbol} "
                f"exceeds cap ${_max_loss:.2f} — review flag written"
            )
```
with:
```python
        # 2026-05-19: gated by HALT_MECHANISMS["outlier_loss_flag"]
        if _HM2.get("outlier_loss_flag", True) and pnl_usd < -abs(_max_loss):
            self._write_review_flag(
                reason=f"outlier_loss({pnl_usd:+.2f} USD beyond ${_max_loss:.2f} cap)",
                action="manual_review",
                symbol=symbol, family=family,
            )
            logger.error(
                f"[Risk/Spec12] Outlier loss {pnl_usd:+.2f} on {symbol} "
                f"exceeds cap ${_max_loss:.2f} — review flag written"
            )
```

### Step 10: Add the 2 AutoMutator gates

Open `core/auto_mutator.py`. Apply these 2 in-place edits:

**Site G1 (line ~183) — symmetric per-symbol blacklist.** Replace:
```python
        for sym, n_loss in sym_losses.items():
            total = sym_total.get(sym, n_loss)
            rate = n_loss / total if total else 0.0
            if n_loss >= SYMBOL_LOSS_BLACKLIST and rate >= SYMBOL_BLACKLIST_MIN_RATE:
                # Only (re)apply if not already active — prevents spam
                current_exp = self._state["blacklist"].get(sym, 0)
                if current_exp < now:
                    self._state["blacklist"][sym] = now + SYMBOL_BLACKLIST_HOURS * 3600
                    logger.warning(
                        f"[AutoMutator] BLACKLIST {sym} for {SYMBOL_BLACKLIST_HOURS}h "
                        f"— {n_loss}/{total} losses ({rate:.0%}) in last "
                        f"{LOOKBACK_ANALYSES} trades")
                    mutations_applied += 1
```
with:
```python
        # 2026-05-19: gated by HALT_MECHANISMS["auto_mutator_blacklist"]
        from config import HALT_MECHANISMS as _HM_AM
        if _HM_AM.get("auto_mutator_blacklist", True):
            for sym, n_loss in sym_losses.items():
                total = sym_total.get(sym, n_loss)
                rate = n_loss / total if total else 0.0
                if n_loss >= SYMBOL_LOSS_BLACKLIST and rate >= SYMBOL_BLACKLIST_MIN_RATE:
                    # Only (re)apply if not already active — prevents spam
                    current_exp = self._state["blacklist"].get(sym, 0)
                    if current_exp < now:
                        self._state["blacklist"][sym] = now + SYMBOL_BLACKLIST_HOURS * 3600
                        logger.warning(
                            f"[AutoMutator] BLACKLIST {sym} for {SYMBOL_BLACKLIST_HOURS}h "
                            f"— {n_loss}/{total} losses ({rate:.0%}) in last "
                            f"{LOOKBACK_ANALYSES} trades")
                        mutations_applied += 1
```

**Site G2 (line ~211) — SHORT-only blacklist.** Replace:
```python
        for sym, n_loss in sym_losses_short.items():
            total = sym_total_short.get(sym, n_loss)
            rate = n_loss / total if total else 0.0
            if (n_loss >= SHORT_SYMBOL_LOSS_BLACKLIST
                    and rate >= SHORT_SYMBOL_BLACKLIST_MIN_RATE):
                key = f"SHORT:{sym}"
                current_exp = self._state["blacklist"].get(key, 0)
                if current_exp < now:
                    self._state["blacklist"][key] = (
                        now + SHORT_SYMBOL_BLACKLIST_HOURS * 3600
                    )
                    logger.warning(
                        f"[AutoMutator] SHORT-BLACKLIST {sym} for "
                        f"{SHORT_SYMBOL_BLACKLIST_HOURS}h — {n_loss}/{total} "
                        f"sell losses ({rate:.0%})"
                    )
                    mutations_applied += 1
```
with:
```python
        # 2026-05-19: gated by HALT_MECHANISMS["auto_mutator_blacklist"] (same flag as G1)
        if _HM_AM.get("auto_mutator_blacklist", True):
            for sym, n_loss in sym_losses_short.items():
                total = sym_total_short.get(sym, n_loss)
                rate = n_loss / total if total else 0.0
                if (n_loss >= SHORT_SYMBOL_LOSS_BLACKLIST
                        and rate >= SHORT_SYMBOL_BLACKLIST_MIN_RATE):
                    key = f"SHORT:{sym}"
                    current_exp = self._state["blacklist"].get(key, 0)
                    if current_exp < now:
                        self._state["blacklist"][key] = (
                            now + SHORT_SYMBOL_BLACKLIST_HOURS * 3600
                        )
                        logger.warning(
                            f"[AutoMutator] SHORT-BLACKLIST {sym} for "
                            f"{SHORT_SYMBOL_BLACKLIST_HOURS}h — {n_loss}/{total} "
                            f"sell losses ({rate:.0%})"
                        )
                        mutations_applied += 1
```

### Step 11: Path A verification — read-only check of tiered leverage

- [ ] Find all `set_leverage` call sites in the codebase:
```bash
grep -rn "set_leverage(" core/ --include="*.py"
```

- [ ] For each call site, read the surrounding context. Confirm the leverage value being passed comes from `LEVERAGE_TIERS[tier_name]["leverage"]` (or equivalent tier lookup) and NOT unconditionally from `RISK["default_leverage"]`.

- [ ] If ALL call sites properly route through tier-based lookup, document in the commit message: "Path A verification: tiered leverage correctly wired at <file>:<line>; no code change."

- [ ] If ANY call site unconditionally uses `RISK["default_leverage"]` ignoring the computed tier, that's the bug Path A was meant to find. Apply a one-line fix to route through `LEVERAGE_TIERS[tier]["leverage"]`. Document the fix in the commit message.

- [ ] **DO NOT change `LEVERAGE_TIERS` values.** Path A is verification only; the tier values themselves (3x/4x/5x/10x) stay as-is.

### Step 12: Run tests, verify expected outcomes

- [ ] Run the new tests:
```bash
python -m pytest tests/test_halt_mechanisms_disabled.py -v
```
Expected: 8 passed.

- [ ] Run the existing Spec §12 soak test (which was modified in Step 7):
```bash
python -m pytest tests/test_spec12_post_sprint.py -v
```
Expected: still passes (test now monkeypatches the flag True for its duration).

- [ ] Run the full suite:
```bash
python -m pytest tests/ -q --tb=no --ignore=tests/test_risk_auto_resume.py --ignore=tests/test_live_risk_caps.py 2>&1 | tail -5
```
Expected: pass count = BASELINE_PASS_COUNT + 8, 0 failures.

If the count is less than expected, some test broke. Investigate before commit.

### Step 13: Sanity checks

- [ ] Bot startup:
```bash
python main.py --status
```
Expected: clean exit, dashboard renders.

- [ ] ATOM proven-edge guard:
```bash
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

- [ ] Verify config import:
```bash
python -c "
from config import HALT_MECHANISMS
print('HALT_MECHANISMS:')
for k, v in HALT_MECHANISMS.items():
    print(f'  {k}: {v}')
assert all(v is False for v in HALT_MECHANISMS.values()), 'expected all False'
assert len(HALT_MECHANISMS) == 7, f'expected 7 keys, got {len(HALT_MECHANISMS)}'
print('config OK')
"
```
Expected: 7 lines printed (all False), then `config OK`.

### Step 14: Lint check

- [ ] Run:
```bash
ruff check tests/test_halt_mechanisms_disabled.py tests/test_spec12_post_sprint.py core/risk_manager.py core/auto_mutator.py config.py
```
Expected: no new errors. Pre-existing warnings in unstaged hunks are not your problem; report them, don't fix.

### Step 15: Selective-staged commit

Repo has pre-existing unstaged mods on `config.py`, `core/risk_manager.py`, and `core/auto_mutator.py`. Use `git add -p` for these three; accept (`y`) ONLY the halts-disable hunks, reject (`n`) any pre-existing.

- [ ] Stage:
```bash
git add tests/test_halt_mechanisms_disabled.py
git add tests/test_spec12_post_sprint.py
git add -p config.py
git add -p core/risk_manager.py
git add -p core/auto_mutator.py
```

- [ ] Verify:
```bash
git diff --staged --stat
```
Expected: 5 files changed; tests/test_halt_mechanisms_disabled.py (NEW, ~200 LOC), tests/test_spec12_post_sprint.py (~3 lines added), config.py (~15 lines added), core/risk_manager.py (~25 lines net added), core/auto_mutator.py (~10 lines net added).

If any pre-existing unstaged hunk got staged accidentally, run `git reset HEAD <file>` and start the `-p` for that file over.

- [ ] Commit:
```bash
git commit -m "$(cat <<'EOF'
feat(halts): disable all 7 loss-driven halt/pause mechanisms

Per 2026-05-19 user directive after explicit informed consent (shown L99
memory -$21.75 single 99x trade + 44.9% WR math). HALT_MECHANISMS config
dict added to config.py with 7 boolean keys, all defaulted False.

Gate sites:
  A daily_pnl_halt         -> core/risk_manager.py L~901
  B drawdown_halt          -> core/risk_manager.py L~914
  C spec12_streak_halt     -> core/risk_manager.py L~1132
  D symbol_pause           -> core/risk_manager.py L~1109
  E family_pause           -> core/risk_manager.py L~1120
  F outlier_loss_flag      -> core/risk_manager.py L~1158
  G auto_mutator_blacklist -> core/auto_mutator.py L~187 and L~216

Halt CODE preserved at each site; only the TRIGGER short-circuits when
the flag is False. Per-mechanism rollback: flip one key True, restart.
Nuclear restore: all 7 True, restart. Sub-1-minute reversal.

Per-position SL on every trade UNCHANGED (bleed-fix sprint patches stay).
Exchange-side liquidation still applies (not bot's control).

Tests: 8 new in tests/test_halt_mechanisms_disabled.py. Critically Test 8
proves rollback works by monkeypatching one flag True and verifying the
mechanism fires. tests/test_spec12_post_sprint.py updated to monkeypatch
spec12_streak_halt=True for its duration so it tests THE MECHANISM rather
than the deployment state.

Path A verification: <FILL IN — "tiered leverage correctly wired, no code
change" OR "fixed one-line routing bug at core/<file>:<line>">.

Risk register: docs/superpowers/specs/2026-05-19-disable-halts-and-verify-tiered-leverage-design.md §7

Co-Authored-By: RuFlo <ruv@ruv.net>
EOF
)"
```

- [ ] Verify:
```bash
git log --oneline -3
```
Expected top of log: `feat(halts): disable all 7 loss-driven halt/pause mechanisms` on top of the spec commit on top of the retune commit.

---

## Verification (overall, post-commit)

```bash
# 1. All tests green
python -m pytest tests/ -q --tb=no --ignore=tests/test_risk_auto_resume.py --ignore=tests/test_live_risk_caps.py 2>&1 | tail -3
```
Expected: BASELINE_PASS_COUNT + 8, 0 failures.

```bash
# 2. New halt-disable tests pass
python -m pytest tests/test_halt_mechanisms_disabled.py -v
```
Expected: 8 passed.

```bash
# 3. Spec §12 soak test still passes (now via monkeypatch)
python -m pytest tests/test_spec12_post_sprint.py -v
```
Expected: 1 passed.

```bash
# 4. Config values picked up
python -c "from config import HALT_MECHANISMS; assert all(v is False for v in HALT_MECHANISMS.values()); print('all 7 disabled')"
```
Expected: `all 7 disabled`.

```bash
# 5. Status sanity
python main.py --status
```
Expected: clean exit.

```bash
# 6. ATOM 30d guard
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 30*86400
row = c.execute('SELECT SUM(realized_pnl), COUNT(*) FROM trades WHERE symbol=\"ATOM/USDT:USDT\" AND status=\"CLOSED\" AND ts_entry >= ?', (since,)).fetchone()
assert row[0] is not None and row[0] > 0, f'ATOM 30d PnL {row[0]} must be > 0'
print(f'ATOM 30d: n={row[1]} pnl=\${row[0]:.2f}')
"
```
Expected: positive PnL.

```bash
# 7. Commit log clean
git log --oneline -5
```
Expected top: `feat(halts): disable all 7 loss-driven halt/pause mechanisms`.

---

## Risk and rollback summary

| What triggers rollback | Action | Time |
|---|---|---:|
| Equity drops > 15% from current → flip drawdown_halt back | Edit `config.py` `HALT_MECHANISMS["drawdown_halt"] = True`, restart | <1m |
| Single losing day > 5% of equity → flip daily_pnl_halt back | Same pattern, different key | <1m |
| Specific symbol catastrophic losses → flip symbol_pause back OR manually add to BLACKLIST_HARD | Edit config, restart | <1m |
| Nuclear: I changed my mind, restore all safety nets | Set all 7 keys to `True` in HALT_MECHANISMS, restart | <1m |
| `data/review_required.json` exists during disabled window | (Shouldn't happen) Delete file, then restore the relevant flag, restart | <2m |
| Tests in `tests/test_halt_mechanisms_disabled.py` fail after future changes | Diagnose: did someone revert HALT_MECHANISMS? Did a gate site get unchanged? | varies |

---

## Deferred to user (post-deploy measurement)

After user-initiated bot restart, monitor for:

**24h check — verify no halts fired:**
```bash
grep -E "DAILY LOSS LIMIT|DRAWDOWN HALT|SPEC §12 HALT|SYMBOL PAUSED|FAMILY PAUSED|outlier.*review_required|BLACKLIST.*for [0-9]+h" logs/bot_*.log | tail -20
```
Expected: zero matches. If matches appear, ONE of the 7 gates didn't take effect; investigate which.

**7-day check — trade frequency and bucket distribution:**
```bash
python scripts/sprint_kpi.py --since 2026-05-19 --markdown
```
Expected vs. pre-disable baseline: trade count UP, daily PnL volatility UP, GHOST/AGE/SL buckets may shift.

**Risk register (§7 of spec) — review weekly.** The user has accepted the risk that catastrophic losing days are no longer truncated by halts. Manual flag flips are the only recovery.

---

## Spec-coverage self-review

| Spec section | Plan task | Status |
|---|---|---|
| §1 Background + L99 + 44.9% WR math | Captured in Context block + commit message | ✓ |
| §2.1 The 7 mechanisms with line numbers | Steps 3, 4, 9, 10 | ✓ |
| §2.2 HALT_MECHANISMS dict | Step 5 | ✓ |
| §2.3 Gate pattern (wrap existing condition) | Step 9 + Step 10 (each gate site) | ✓ |
| §2.4 Path A leverage verification | Step 11 | ✓ |
| §3 Tests (8 named tests) | Step 6 | ✓ |
| §4 Measurement plan | "Deferred to user" block | ✓ |
| §5 Rollback (per-mechanism + nuclear) | "Risk and rollback summary" + commit message | ✓ |
| §6 Out of scope | Preserved by task scope; no scope drift | ✓ |
| §7 Risk register | Captured in spec + summarized in plan rollback table | ✓ |
| §8 Files Touched | "File Structure" block | ✓ |
| §9 Decision Summary | Captured in commit message | ✓ |
| §10 Plan-file note | Acknowledged: this plan file is being written; if it persists, great; if not, the implementer uses the spec directly | ✓ |

No spec section unaddressed.
