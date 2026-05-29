# WR Restoration Plan v2 — 2026-04-19

## Problem Statement
- Today: -1.57 USDT, 24 trades, 29.2% WR
- Yesterday: -1.91 USDT, 22 trades, 27.3% WR
- All-time: -60.75 USDT, 241 trades, 41.1% WR
- User asked: "There is so much data and frameworks — bot should pick the best
  trades, monitor markets, always close when in profit."
- User WR floor target: ≥61% daily (per memory).

## Diagnosis (evidence-only)

Per `tasks/lessons.md` L1, L2, L5 — this plan is narrowed to bugs with a
concrete file+line+data artifact. Bugs without that are deferred.

### Bug 1 — Spec §12 halt state gets silently wiped
**Evidence**: `data/review_required.json` exists (timestamp 2026-04-19 18:21,
reason "5 consecutive global losses", action "force_observation_mode"). But
`data/risk_state.json` has `is_halted: false`, `halt_time: 0.0`,
`halt_reason: ""`, and `global_streak: [false,false,true,true,true,false,false,false,true,true]`
(no 5-loss run — it rotated out as new trades appended). Bot continued opening
positions after halt flag was on disk.

**Root cause A** — `core/risk_manager.py:149-154` (`_load_state` new-day branch):
hard-sets `self._halted = False` on day rollover without checking
`review_required.json`. Restart after midnight UTC wipes a valid halt.

**Root cause B** — `core/risk_manager.py:196-200` (`_should_auto_resume`):
`_REVIEW_FLAG_PATH.unlink()` is wrapped in a bare `except Exception`. On
Windows the unlink can fail under AV/file-lock — the flag stays on disk,
but `_halted` has already been cleared in memory and is subsequently saved
as `false`.

**Root cause C** — halt_time=0.0 in saved state means
`_should_auto_resume` sees `elapsed_min = (now - 0) / 60 = huge` → resumes
immediately on any `can_trade()` call. There is a code path that sets
`_halted=True` without updating `_halt_time`. Grep candidates: lines 450,
463, 499, 528.

### Bug 2 — `reconcile_closed_pnl` synthesizes fake -13% to -19% losses
**Evidence**: `data/risk_state.json` `trade_history` contains pnl_pct of
-7.86, -6.93, -5.98, -4.60, -4.23, -3.41 — all beyond the 1.5-3.5% ATR SL
clamp. Impossible from real SL fills.

**Root cause** — `core/position_tracker.py:414-417` synthesizes
`entry = 1.0`, `size = 1.0` when Binance's income ledger lacks them. Then
line 450 computes `pnl_pct = pnl / margin * 100` with `margin = 1/leverage`
— garbage denominator, realistic-looking garbage percentage.

**Consequence chain**: ghost-pnl_pct values feed `risk_manager._record_trade_closed`
(via `_global_streak.append(is_win)`). Phantom losses → false 5-streak halts →
halt-wipe bug (Bug 1) → bot resumes → real losses continue.

### Bug 3 — Phantom PnL feeds knowledge_model and learning
Same synthesized entries end up in `data/warehouse.sqlite` and
`data/knowledge_model.json` `hour_scores`/`symbol_stats`. Downstream gates
("caution strategy <50% WR", "fee-heavy") learn from wrong data.

**Fix** is a strict subset of Bug 2 — if we don't import the phantom
pnl_pct, the downstream data stays clean.

### Bug 4 — Source-of-truth config drift
**Evidence**:
- CLAUDE.md §Configuration: "currently narrowed to BTC/ETH only during
  learning-first phase"
- `config.py` `WHITELIST_SYMBOLS` = 16 symbols (ALGO, LUMIA, AVAX, BNB,
  BCH, ORDI, DOT, LINK, GRASS, QTUM, ACT, IOTA, FET, VET, BTC, ETH)
- `config.py` `TRADING_PAIRS` = 30 symbols per exchange
- Bot traded ARB, FET, ALGO today → obviously not BTC/ETH only

Three sources disagree. Whichever is the real intent, the other two are
lying. This isn't a single bug fix — it's a deliberate decision that has to
be made. Ask user. Do not guess.

## STOP-THE-BLEEDING (do before code changes)

- [ ] User sets `OPERATING_MODE=OBSERVATION` in `.env` (or kill process)
- [ ] User decides fate of open ALGO short manually
- [ ] User confirms intended trading universe (BTC/ETH-only vs 16-symbol
      whitelist) — decides Bug 4 scope

## Narrowed Fix Plan

**Dependency order REVISED 2026-04-19**: Phase 1 (halt persistence) runs
FIRST. Rationale: verified that `reconcile_closed_pnl` appends only to
`tracker._closed` — it does NOT call `risk.record_trade_pnl` or
`record_trade_result`. Therefore reconciled positions CANNOT feed
`_global_streak` or `_trade_history`. The 5-loss streak that wrote
`review_required.json` came from real trades via `order_manager.close_position`.
Bug 2 is data-hygiene only, not upstream of Bug 1. The earlier advisor
reordering rested on that causal chain — which grep disproved.

### Phase 1 — Halt persistence (Bug 1) — RUN FIRST
- [ ] **Fix 1A** — `_load_state`: on BOTH same-day AND new-day branches, if
      `data/review_required.json` exists, read its `ts` field and treat it
      as the authoritative `_halt_time`. Set `_halted=True`,
      `_halt_reason="spec12:persisted_from_flag"` regardless of saved
      state. File: `core/risk_manager.py:108-158`
- [ ] **Fix 1B** — `_should_auto_resume`: reorder to delete the flag
      FIRST; only clear `_halted` if `unlink()` succeeded. If unlink
      raises, log ERROR and return False (stay halted). File:
      `core/risk_manager.py:186-204`
- [ ] **Fix 1C** — Audit every `self._halted = True` assignment. At each
      site, ensure `self._halt_time = _time.time()` is set in the same
      block. Candidates: lines 450, 463, 615 (already correct). File:
      `core/risk_manager.py`
- [ ] **Fix 1D** — After Fix 1A: on startup if review flag present,
      log WARNING with elapsed time and cooldown remaining, so it's
      visible the bot is in spec §12 state.

### Phase 2 — Reconcile phantom PnL (Bug 2 + Bug 3 together) — RUN SECOND
- [ ] **Fix 2A** — `reconcile_closed_pnl`: if `entry <= 0` AND
      `exit_p <= 0` AND `size <= 0`, do NOT synthesize. Set
      `pos.pnl = pnl` (realized dollar amount trusted), set
      `pos.pnl_pct = None`, set `pos.close_reason =
      "reconciled_no_context"`. File: `core/position_tracker.py:404-450`
- [ ] **Fix 2B** — `risk_manager._record_trade_closed`: if `pnl_pct`
      is None, still append `is_win = (pnl > 0)` to `_global_streak`,
      `_sym_hist`, `_fam_hist` (dollar P&L from exchange is ground
      truth — losing trades MUST still count toward spec §12 safety).
      Store `None` in `trade_history` for the pct slot. Dollar loss
      still counts toward `daily_pnl`. Do NOT skip any streak append.
      The fix is purely that `pnl_pct` never leaves `reconcile_closed_pnl`
      as a fake number. File: `core/risk_manager.py` (find
      `_record_trade_closed`).
- [ ] **Fix 2C** — `warehouse.record_trade_close`: if `pnl_pct` is None,
      write NULL into the pnl_pct column (assumes column is nullable —
      verify schema).
- [ ] **Fix 2D** — `knowledge_model` ingestion: ignore trades with
      `pnl_pct` None when computing symbol/hour WR and PnL.

### Phase 3 — Config source-of-truth (Bug 4)
_Requires user decision before implementation._
- [ ] **Fix 3** — Option A: Enforce BTC/ETH-only per CLAUDE.md. Narrow
      `WHITELIST_SYMBOLS` and `TRADING_PAIRS` to {"BTC/USDT", "ETH/USDT"}.
      Remove 14 symbols from whitelist. File: `config.py:380-445`
      Option B: Retain 16-symbol research universe. Rewrite CLAUDE.md
      paragraph to list actual whitelist.
      Option C: Different. User specifies.

## Deferred (no concrete evidence — would violate lesson L2)
- **Short-bias filter** (8 shorts losing in a rally today). Could be
  symbol-selection or timing; pattern-matching on one day is not
  enough. Revisit after Phase 1+2 produce a clean 7-day WR sample.
- **Leverage reduction 3x → 1x**. User's checklist is 3x-signed.
  Reducing leverage without a bad-hold case is L2 violation.
- **TP-at-breakeven / trailing relaxation**. "Always close when in
  profit" is an ambient wish, not a bug. Current trailing already
  advances SL toward breakeven. Need a concrete "gave back winner"
  trade, not a vibe.
- **Short-side veto during BTC bull**. `SHORTS_REQUIRE_BTC_BEAR=False`
  by design per prior decision. Would need a new regime signal to wire.
- **Soak-test harness before live resume**. Useful; low priority. After
  Phase 1+2, run PAPER for 48h and check WR naturally.

## Verification
- [ ] `python -m pytest tests/ -v` — no regressions
- [ ] Delete `data/review_required.json` after user confirms halt is safe to
      clear, restart bot. Confirm `is_halted=True` on next start if we
      re-create the flag by hand (manual smoke test of Fix 1A).
- [ ] Set `entry=0` in a reconcile fixture (if test exists) — confirm
      `pos.pnl_pct is None` and `_global_streak` unchanged.
- [ ] 48h PAPER soak. Compare WR before/after. Target: ≥50% WR (not 61%
      immediately — user's floor is aspirational; first milestone is
      "stop bleeding").

## Out of Scope
- Entry scoring changes (spec §-protected, checklist-signed)
- Leverage tier changes (checklist-signed)
- R:R ratio changes (kept per memory note)
- Claude LLM path modifications

## Review
_Pending implementation. Requires user approval on scope and Bug 4 option
before proceeding._
