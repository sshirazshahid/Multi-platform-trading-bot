# Disable All Halt Mechanisms + Verify Tiered Leverage — Design

**Date:** 2026-05-19
**Branch:** `feat/profitability-upgrade` (continuing on top of `e0622e6` — partial-TP retune)
**Author:** Claude Code (Opus 4.7 1M) — brainstorming session with user
**Status:** Pending user review before plan generation

---

## 1. Background

### 1.1 User directive (2026-05-19)

> "Don't halt or pause when loosing trades. Understand how market reacts and move. Then take profitable trades with good leverage."

After being shown:
- Memory `project_l99_revert_go_all_in_2026_04_29`: a single 99x trade lost $21.75; user reverted to 2x leverage same day.
- Live state: bot is at 44.9% WR over 30d; doubling leverage at this WR doubles avg_win AND avg_loss — same EV sign, faster bleed (current −$0.078/trade → 4× leverage would be −$0.310/trade).
- Live state: `risk_state.json` shows `is_halted=False, halt_reason=""` — no halt has fired in the recent 30d window. Halts are NOT what's costing money today.

User explicitly chose:
- **Path A:** verify tiered leverage is wired correctly (`STANDARD 3x / STRONG 4x / CONVICTION 5x / AGGRESSIVE 10x` from `config.LEVERAGE_TIERS`).
- **Disable halts entirely:** all 7 loss-driven halt/pause mechanisms (A through G in §2.1 below) disabled.

User has informed consent. This spec implements the choice.

### 1.2 Constraints (binding)

- Live bot stays running on `CONTROLLED_LIVE`. Change picked up at user-initiated restart.
- Per-position SL on every trade STAYS in place (Patches #0/#2/#3 from bleed-fix sprint preserved). This is the ONLY remaining per-trade safety net.
- Exchange-side liquidation is NOT in bot's control and stays regardless.
- No personal info / API keys in committed files.
- Pre-commit hooks must pass (ruff, codespell, detect-secrets, pre-push pytest).
- Each mechanism gets its own boolean flag for granular rollback.

---

## 2. The Change

### 2.1 The 7 mechanisms being disabled

| ID | Mechanism | Today's behavior | Code location |
|---|---|---|---|
| **A** | Daily PnL halt | Halts when `daily_pnl ≤ -max_daily_loss_pct × balance`. Resumes next UTC day. | `core/risk_manager.py:~901` |
| **B** | Max-drawdown halt | Halts when `drawdown ≥ 8%` from peak. Auto-resume cooldown. | `core/risk_manager.py:~916` |
| **C** | Spec §12 streak halt | 5 consecutive global losses → writes `data/review_required.json` → halts. 4h auto-resume. | `core/risk_manager.py` `record_trade_result` 5-consec path (~line 1132–1151 per Task 5 comment) |
| **D** | Symbol pause | 2 consecutive losses on a symbol → `_symbol_pauses[symbol] = now + 6h` | `core/risk_manager.py` wherever `_symbol_pauses[...]` is assigned |
| **E** | Family pause | 3 consecutive losses on a strategy_family → `_family_pauses[family] = now + 12h` | `core/risk_manager.py` wherever `_family_pauses[...]` is assigned |
| **F** | Outlier-loss flag halt | Single trade loses ≥ `MAX_LOSS_PER_TRADE_USD` ($15) → writes review flag → halts | `core/risk_manager.py` outlier-loss write path |
| **G** | AutoMutator blacklist | Symbol with 80%+ loss rate over recent window → 12h auto-blacklist | `core/auto_mutator.py` blacklist-write entry |

### 2.2 The flag block in `config.py`

```python
# 2026-05-19 — User directive: "Don't halt or pause when losing trades."
# All seven halt/pause mechanisms disabled. Flip any individual key to True
# to re-enable just that mechanism; restart bot to pick up the change.
# Rollback to full safety: set every value to True, restart. <1 minute.
# Per-position SL/TP still placed on every entry (Patches #0/#2/#3 verify
# exchange-side SL alive). Exchange-side liquidation still applies.
# Risk register: see docs/superpowers/specs/2026-05-19-disable-halts-and-verify-tiered-leverage-design.md §7
HALT_MECHANISMS = {
    "daily_pnl_halt":         False,
    "drawdown_halt":          False,
    "spec12_streak_halt":     False,
    "symbol_pause":           False,
    "family_pause":           False,
    "outlier_loss_flag":      False,
    "auto_mutator_blacklist": False,
}
```

### 2.3 Gate pattern at each halt site

At each of the 7 sites, the existing halt-triggering condition is wrapped:

**Before:**
```python
if self._daily_pnl < -daily_loss_limit and not self._halted:
    self._halted = True
    self._halt_reason = f"daily loss ({self._daily_pnl:+.4f} USDT)"
    ...
```

**After:**
```python
if (HALT_MECHANISMS["daily_pnl_halt"]
        and self._daily_pnl < -daily_loss_limit
        and not self._halted):
    self._halted = True
    self._halt_reason = f"daily loss ({self._daily_pnl:+.4f} USDT)"
    ...
```

The existing logic is preserved; the trigger short-circuits when the flag is False. Same pattern for all 7 mechanisms. Re-enabling = flip flag → True, restart.

### 2.4 Path A — Tiered leverage verification (bundled into same task)

`config.LEVERAGE_TIERS` is defined at `config.py:516` with:
- STANDARD: leverage=3, min_confidence=0.55
- STRONG: leverage=4, min_confidence=0.72
- CONVICTION: leverage=5, min_confidence=0.80, requires_peak_hour
- AGGRESSIVE: leverage=10, min_confidence=0.85

`RISK.default_leverage = 2` (config.py:447) is the legacy fallback. Memory `phase51` (commit `a56ac96`) restored tier wiring.

**Verification steps (read-only, in implementer task):**

1. `grep -n "set_leverage\|LEVERAGE_TIERS" core/order_manager.py core/bot_engine.py core/mcp_brain.py` to find call sites.
2. At each `set_leverage(...)` call before position open, confirm the leverage value comes from `LEVERAGE_TIERS[tier_name]["leverage"]` and NOT `RISK["default_leverage"]`.
3. If the tier-to-leverage mapping is correctly applied → document in commit message, no code change.
4. If `default_leverage` is the unconditional fallback (i.e., tiers are computed but not honored at the set_leverage call) → one-line fix bundled into the same commit.

The verification adds at most ~5 LOC of fix. If no fix is needed, only the documentation step happens.

---

## 3. Tests Added

New file: `tests/test_halt_mechanisms_disabled.py`. 8 tests:

1. **`test_halt_mechanisms_all_disabled_by_default`** — pin all 7 flags = False. Catches accidental revert.

2. **`test_daily_pnl_loss_does_not_halt_when_flag_off`** — instantiate RiskManager with `start_balance=500`. Drive `daily_pnl` to −$10 (well below −1% threshold). Assert `is_halted=False`.

3. **`test_drawdown_does_not_halt_when_flag_off`** — peak=$500, simulate drawdown to $450 (−10%, above 8% threshold). Assert `is_halted=False`.

4. **`test_spec12_streak_does_not_halt_when_flag_off`** — 5 consecutive losses through `record_trade_result(symbol, family, is_win=False, pnl_usd=-1.0, pnl_pct=-1.5, reason="stop_loss")`. Assert NO `data/review_required.json` created and `is_halted=False`. (Inverse of the Task 5 soak test.)

5. **`test_symbol_pause_not_set_when_flag_off`** — drive 2 losses on `ATOM/USDT:USDT`. Assert `rm._symbol_pauses.get("ATOM/USDT:USDT", 0) == 0`.

6. **`test_family_pause_not_set_when_flag_off`** — 3 losses on family `claude_portfolio`. Assert `rm._family_pauses.get("claude_portfolio", 0) == 0`.

7. **`test_outlier_loss_does_not_write_flag_when_off`** — single $20 loss (above $15 threshold). Assert `data/review_required.json` not created.

8. **`test_re_enabling_one_flag_restores_that_mechanism`** — set `HALT_MECHANISMS["spec12_streak_halt"] = True` (monkeypatched), drive 5 consec losses, assert halt DOES fire. Proves rollback works.

**Tests use `tmp_path` + `monkeypatch.chdir(...)`** for filesystem isolation, matching the Task 5 soak test pattern.

**Test 8 critically inverts the Task 5 soak test** — Task 5 asserts halts work; this asserts the disable-flag actually works. Both tests in the suite means a future revert in either direction breaks one test loudly.

---

## 4. Measurement Plan

### 4.1 Baseline at deploy time

Recorded by implementer:
- 30d net PnL at deploy time.
- Number of halts triggered in last 7d (expected: 0).
- Number of symbol/family pauses currently active (expected: 0).
- AutoMutator blacklist set size at deploy (expected: 0 currently).
- Trade frequency (~7.8/day currently).

### 4.2 Operator check after first 24h of running

Confirm at least ONE losing-trade scenario passed through without booking a halt:
```bash
grep -E "DAILY LOSS LIMIT|drawdown|spec12|SYMBOL PAUSED|FAMILY PAUSED|outlier.*review_required" logs/bot_*.log | tail -20
```
Expected: zero matches after the 24h window. If matches appear, the flag isn't wired correctly at one of the gate sites.

### 4.3 7-day signal check

```bash
python scripts/sprint_kpi.py --since 2026-05-19 --markdown
```

Expected vs. pre-disable baseline:
- **Trade count UP** (no pauses blocking entries on losing symbols).
- **Loss bucket DOWN in count, UP in sum** (more individual losses, no streak truncation).
- **Daily PnL volatility UP** (no daily-loss circuit-breaker).

This is the user's chosen trade-off: more trading activity, more volatility, no safety net for the bad day.

### 4.4 Rollback triggers (you decide your threshold)

These are NOT automated. The user makes the call:
- Equity drops > 15% from current → MANUALLY flip `drawdown_halt: True` + restart.
- Single losing day > 5% of equity → flip `daily_pnl_halt: True` + restart.
- Specific symbol shows clear catastrophic edge (≥ 10 losses in a row, no wins) → flip `symbol_pause: True` + restart, OR manually add to BLACKLIST_HARD.

---

## 5. Rollback

### 5.1 Per-mechanism rollback (sub-1-minute)

Open `config.py`. In `HALT_MECHANISMS`, flip ONE key (or multiple) from `False` to `True`. Restart bot. The flipped mechanism is restored; others stay disabled.

Example — re-enable just Spec §12 streak halt:
```python
HALT_MECHANISMS = {
    "daily_pnl_halt":         False,
    "drawdown_halt":          False,
    "spec12_streak_halt":     True,   # re-enabled
    "symbol_pause":           False,
    "family_pause":           False,
    "outlier_loss_flag":      False,
    "auto_mutator_blacklist": False,
}
```

### 5.2 Nuclear restore (sub-1-minute)

Set all 7 values to `True` in `HALT_MECHANISMS`. Restart bot. All safety nets back.

### 5.3 If `data/review_required.json` is present during disabled window

It SHOULDN'T be (mechanism F is disabled), but defensively: if it exists when you restore, delete it:
```bash
rm data/review_required.json
```
Then restart. The restored halt mechanism will re-create it next time the trigger fires.

---

## 6. Out of Scope

- **Increasing leverage above current LEVERAGE_TIERS values.** Path A is verification, not amplification. Tiered values stay at 3x/4x/5x/10x.
- **Removing per-position SL.** Every entry still places exchange-side SL via the bleed-fix sprint patches.
- **Removing the daily trade count cap** (`RISK.max_trades_per_day=200`). This isn't a loss-driven halt; it's a sanity cap.
- **Removing exchange-side rejections** (min notional, position-count limits per symbol). These are venue policies; bot can't override.
- **Tuning AutoMutator's loss-rate threshold.** Just disabling its blacklist write entirely.
- **Adding new halt mechanisms.** No additions.
- **Rebuild swarm (master plan Phases 2–6).** Deferred separately.

---

## 7. Risk Register (Read This)

This is what you're explicitly choosing to lose:

| Risk | Old protection | New state |
|---|---|---|
| 5 consecutive global losses in 30min wipe a meaningful chunk of equity | Spec §12 4h cooldown halt | **REMOVED.** Bot continues trading. Only per-trade SL caps each loss to 1.5-3.5% of position notional. |
| Daily PnL ≤ −1% of equity (a "bad morning") | Daily-loss halt + reset at next UTC day | **REMOVED.** No daily ceiling on cumulative loss. |
| Equity drawdown ≥ 8% from peak | Drawdown halt + cooldown | **REMOVED.** Drawdown can grow until margin runs out at exchange. |
| One symbol structurally losing (e.g., ETH bleed last month) | Symbol pause (6h) + AutoMutator (12h) | **REMOVED.** Bot keeps entering the losing symbol if MCP says go. |
| One strategy family persistently negative | Family pause (12h) | **REMOVED.** |
| Single trade loses ≥ $15 (typically leverage-amplified or slippage outlier) | Review flag write → operator notified, halt | **REMOVED.** Bot keeps trading; only logged. |
| Liquidation-level drawdown (margin runs out) | Drawdown halt would have caught this before liquidation | **NEW EXPOSURE.** Exchange-side liquidation is the final stop. |

**The operating thesis you're committing to:**
- Per-trade SL is sufficient per-trade protection.
- Per-trade SL × N losing trades < equity at any point (otherwise liquidation).
- You will manually monitor and manually flip flags back if a bad regime starts.

**One concrete number for context:** at current ~$562 equity with positions averaging $50 notional × 2x leverage, a 2% per-trade SL costs ~$2.00 per losing trade. **40 consecutive losses (no wins) would wipe ~$80, i.e., 14% of equity.** That's not impossible in a regime change. Spec §12 would have stopped at trade 5 today.

---

## 8. Files Touched

### NEW
- `tests/test_halt_mechanisms_disabled.py` — 8 tests.

### MODIFIED
- `config.py` — add `HALT_MECHANISMS` dict (8 keys including 7 flags + comment block).
- `core/risk_manager.py` — gate the 6 risk-manager-resident halts (A, B, C, D, E, F) with the corresponding flag. Estimated ~25 LOC of gate additions across the file.
- `core/auto_mutator.py` — gate the blacklist write (G). ~5 LOC.
- `core/order_manager.py` — IF Path A verification finds a fix is needed, a one-line tier-leverage routing fix. Otherwise no change.

Total estimated diff: 1 new file (~150 LOC tests), 3 modified files (~35 LOC of additions).

---

## 9. Decision Summary

| Question | Decision |
|---|---|
| What's being disabled | All 7 loss-driven halt/pause mechanisms |
| Mechanism | Per-mechanism boolean flag in `HALT_MECHANISMS` config dict |
| Granularity | Per-mechanism rollback (flip one flag, restart) OR nuclear restore (flip all, restart) |
| Path A leverage | Verification step bundled into same task; one-line fix if needed |
| Tests | 8 new tests; Test 8 (re-enable proves rollback works) is the critical anti-revert protection |
| Rollback | <1 minute via config flag; no DB / state cleanup needed |
| Per-trade SL | UNCHANGED, every trade still has exchange-side SL |
| Exchange liquidation | Still applies (not bot's control) |
| Risk acceptance | User has reviewed §7 with informed consent |
| Expected effect | Trade count UP, daily PnL volatility UP, no halts on bad days; safety net is now operator vigilance + per-trade SL only |

---

## 10. Plan-File Note

The bleed-fix sprint's plan file at `docs/superpowers/plans/2026-05-19-bleed-fix-sprint.md` did not persist to disk on initial Write (a tooling issue). The retune plan written immediately after DID persist. Future plan files may or may not persist; the implementer should work from this spec's §2 (gate sites), §3 (tests), and §4 (measurement) if the plan file is missing.
