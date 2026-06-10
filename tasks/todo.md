# Task: pine_strategies study + integrate (2026-06-10) — shipped

## Plan (executed)
1. 6-agent study: parity audit Pine #1-#3 vs quant_suite, Pine static review, wiring map,
   data recon, provenance → verify: structured findings, all returned
2. Port Pine #4 (funding/carry) as fc_vote + "carry" mode (TDD) → verify: 9 new tests green
3. Fix audit-classified Pine bugs + README provenance corrections → verify: pine reviewer pass
4. Pre-registered 3y/31-major screen, frozen gates → verify: report written, registry note honest
5. 4-agent adversarial review → apply fixes → verify: 23/23 tests, ruff clean → commit

## Review
- PROVENANCE: pine_strategies/ authored against the QUARANTINED suite (same external sandbox,
  Jun 6); README's "walk-forward" params (70/2.0/3.0) verbatim from the audit-failed sweep →
  flagged in README, NOT adopted. Pine #1-#3 already natively integrated as engine.py votes.
- NEW: engine.prep(funding=...) → fc_vote (Pine #4 port, REAL 8h funding not the Pine proxy),
  "carry" mode, quant_suite/funding_carry.py (paginated backfill + parquet cache),
  funding_carry registry entry (fail-closed, NOT in select_for_regime).
- SCREEN: scripts/run_funding_carry_screen.py, 6 pre-registered variants, 31 majors, ~3y,
  engine costs + real funding accrual, embargo, DSR>=0.90 promote bar, n_trials=27 →
  **NO_EDGE** (all variants IS mean −0.15..−0.42%/trade net, 0/6 FDR, best OOS −0.30%/trade,
  DSR≈0). Adversarial review: every found bias pushed TOWARD passing — NO_EDGE trustworthy.
- Pine fixes: useLTF default OFF (v6 bool-na long-block bug), hidden emaGap floor removed,
  is_bench bypass via ticker.standard (03 dead-on-BTC-chart bug), slMin/slMax parity,
  alert + alertcondition edge-triggering, maxval 84. README counterpart table → engine votes.
- Confluence paper forward-test untouched (fc_vote neutral by default; tier/confluence
  semantics unchanged — verified by test_fc_does_not_change_confluence_or_tier).

# Task: TP booking-completeness FIX (2026-06-04) — implemented

## Review (DONE, TDD; effective on next bot RESTART — bot not bounced)
Follows the code-path audit (reports/tp_booking_codepath_audit_2026-06-04.md). Fixed the two
highest-value BOOKING defects (NOT a profit lever — edge is settled flat-alpha). Files:
core/position_tracker.py, core/order_manager.py, core/warehouse.py. Tests:
tests/test_tp_booking_fix.py (18 new). Full suite 1459 green (was 1441).
- **B1 — r_multiple contamination → fixed.** Added immutable `Position.entry_stop_loss`
  (captured once in __post_init__, never moved by trailing/BE) + `Position.r_multiple()` /
  `entry_risk_stop()`. `_finalize_close` now books R against the ENTRY stop, not the mutated one
  (a BE move used to NULL R via denom==0, or inflate it). Falls back to stop_loss for legacy
  in-flight positions.
- **B1 audit trail — `trades.entry_stop_px` column added** (idempotent ALTER, mirrors
  mcp_score/fill_type). Migration smoke-tested on a COPY of the live DB: column added, 836 rows
  preserved, live warehouse.sqlite untouched. `record_trade_close(entry_stop_px=...)` (default
  None → existing/ reconcile callers unaffected).
- **B3 — partial-TP invisible to warehouse → fixed (separate column, live-safe).** Added
  `Position.book_partial_exit()` + accumulators + `effective_exit_price()`. `partial_close_position`
  records the taken fraction on the Position; `_finalize_close` books it in a NEW
  `trades.partial_realized_pnl` column. NOTE: distinct from the Jun-3 #2 fix (7b72159) which fixed
  the paper *wallet* balance; this fixes the *warehouse trade row* (the learning substrate).
- **⚠ realized_pnl DELIBERATELY left runner-only (advisor catch).** `recent_expectancy`
  (warehouse.py:404) feeds the LIVE entry gate (bot_engine._execute_open:1675) + health/mcp/
  promotion readers all SELECT realized_pnl. Folding the partial in would loosen the gate (more
  trades, against flat-alpha thesis). So realized_pnl is unchanged; whole-trade $ =
  realized_pnl + partial_realized_pnl. r_multiple change is safe (no active reader; grep-verified).
  Gate→whole-trade recalibration = separate OWNER decision.
- **Risk rails + live read-path UNCHANGED** — record_trade_pnl / Spec§12 / is_win stay runner-leg;
  this commit shifts NO value any live consumer reads. Pure additive booking completeness.
- **Deferred (offered, not done):** B2/B4 reconcile-path TP re-labeling (LIVE-only, low-impact,
  sensitive ghost/dedup code) and D failed-exch-TP retry (0 "TP order FAILED" in retained logs).
- Did NOT restart the bot (in-process watchdog; user-restart applies the fix).
  Restart: `"D:\Downloads\Trading_Bot\venv\Scripts\python.exe" main.py`

---

# Task: Why isn't the bot hitting TP "with accuracy" — diagnosis (2026-06-04)

## Review (Phase 1 DONE; Phase 2 = no trading-behavior fix warranted)
Built scripts/tp_accuracy_diagnosis.py (committed 96c7160; report reports/tp_accuracy_2026-06-04.md).
- D1 (495 trades): loss is ~90% COSTS (fees $30.5 + spread $36.2); alpha FLAT (median +$0.01). Corrects prior "alpha-dominated".
- D2 (5720 candidates): TP-before-SL 14.9% vs 33% no-edge → no directional edge.
- D3: naive +0.21R "edge" was a BETA artifact — 6-day net-short slice during −13% BTC; side split LONG 0% vs SHORT 40% TP-first. Killed by horizon-sweep + side-split + contamination checks.
- Phase 2 GATED OUT: machinery (trailing/mcp_tp/partial-BE) is ~net-neutral (−$2.32), NOT the leak → hypothesized fixes NOT applied (diagnostic caught the beta artifact before I chased it). "Widen stops" is gambler's-ruin, rejected.
- Only edge-agnostic lever = cost reduction (maker entries / liquidity filter); ceiling ~breakeven (flat alpha), NOT profit. Left as owner decision — did not cram a money-path change. Bot stays PAPER.

---

# Task: Multi-agent audit follow-up — safety fixes (2026-06-03/04)

## Review (DONE — all PAPER; effective on next bot RESTART)
- #2 partial-TP paper-wallet leak (HIGH) — SHIPPED 7b72159. partial_close_position booked
  nothing in VirtualWallet → every partial TP leaked the taken fraction's margin+profit from
  the paper balance (reported ~−$0.30/trade slightly OVERstated the real loss). Fixed +
  tests/test_partial_tp_accounting.py.
- #3 Bybit/Bitget defaultType race — SHIPPED b5aafdd (+9d4a5d5 UTC test fix). Added reentrant
  _defaultType_lock mirroring Binance; wraps every switch→call→reset region (Bybit 4, Bitget 5).
  Verified pure re-indent via `git diff -w`. tests/test_exchange_defaulttype_lock.py = AST check
  that EVERY switch_to_*() call in all 3 clients is lock-guarded. Full suite 1420 green.
- #4 B5 swing bonus — DOC-ONLY (89ec726). Dead by design; enabling = more no-edge trades. Hold.
- #5 is_entry_invalidated staleness rail — PARKED for supervised work. Off-top-25 positions get
  "no_indicators" (blanket early-return serves top-25 cache). Do the cache-BYPASS minimal fix
  (advisor); per-coin-timestamp idea rejected (leaks). Hot-path; needs supervision.
- Did NOT auto-restart the healthy 40h bot (user away; in-process watchdog = no auto-respawn).
  Restart when back: `"D:\Downloads\Trading_Bot\venv\Scripts\python.exe" main.py`

## Batch 2 (2026-06-04) — verified multi-agent bug-class audit (wvjam7dy2): 6 confirmed, 0 rejected
- #1 Bitget set_position_mode → DRY_RUN-gated (8eba053). Real private POST fired at init in PAPER.
- #3/#5 VirtualWallet → RLock + atomic save + locked apply_funding (911a2d0). Paper-balance lost-update.
- #4 RiskManager.record_trade_pnl → RLock (38760de). LIVE daily-loss rail lost-update.
- #6 Bybit/Bitget fetch_order_book/open_orders → lock+switch (3c3c8ba). Gap in my own b5aafdd.
  ⚠ BEHAVIOR CHANGE: pair_discovery futures reads now use futures book → universe may widen (PAPER-only).
- #2 cap-alloc PAPER transfer = owner-authorized; NOT changed (surface only).
- Deadlock check: all 4 locks are leaf locks, no opposite-order nesting → deadlock-free.
- 1431 tests green. SIX commits pending ONE restart; none exercised under live concurrency yet.
- Per advisor: stopped here (no further unattended audit) — consolidate + report + hold.

---

# Task: Commodity/Equity Perp — Format Fix + Analysis-Tracking (2026-06-02)

## Goal
Let the bot FETCH + WAREHOUSE the liquid commodity/equity perps (gold/silver/oil + liquid
stock perps) for research, while NEVER routing them to live/paper orders until screened.

## Constraints
- SAFETY-CRITICAL: analysis-only symbols must be hard-blocked at the live-entry gate.
- Minimal impact; mirror existing patterns; no new bugs.

## Plan (checkable)
- [ ] 1. Map: (a) how the active universe is built + the var holding it, (b) the exact
        live-entry gate in `_execute_open` + existing allow/deny lists, (c) the OHLCV
        analysis-fetch path that builds spot `{base}/USDT` (the XAU 404).
- [ ] 2. Config: add `ANALYSIS_ONLY_SYMBOLS` (liquid commodity/equity perps, full perp fmt).
- [ ] 3. Format fix: analysis-fetch resolves perp `{base}/USDT:USDT` when spot is absent.
- [ ] 4. Safety gate: `_execute_open` refuses any base in ANALYSIS_ONLY (hard block).
- [ ] 5. Tracking: analysis-only symbols are fetched + warehoused, excluded from entry universe.
- [ ] 6. Tests FIRST (TDD): (a) entry gate blocks analysis-only; (b) format resolver picks perp.
- [ ] 7. Verify: run tests; restart; confirm bot fetches XAU/USDT:USDT and opens ZERO of them.

## Review (2026-06-02 — DONE, commit b07e11c)
- config.is_analysis_only() + ANALYSIS_ONLY_BASES added (base-exact; XAUT not caught).
- _execute_open hard-blocks analysis-only in EVERY mode (placed after Phase-23 cooldown
  to keep the brittle window-test green); verified: 0 analysis-only open positions,
  0 recent analysis-only trades (only 2 ever — XAG live 2026-03-30, pre-gate).
- mcp_brain perp fallback ({coin}/USDT:USDT when spot 404s) — MCP coverage 23 -> 39 coins.
- _collect_all_coins always includes analysis-only bases.
- 6 new tests + full bot suite 1406 green. Bot restarted + verified live (no errors).
- NOTE: live candidate-warehousing is action-driven; for screenable history use
  scripts/backfill_universe_ohlcv.py on the analysis-only perps (next data step).
