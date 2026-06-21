# Task: Close-in-profit overhaul (audit 2026-06-21) — Phase A in progress

Full audit: `reports/profit_close_audit_2026-06-21.md` (agent-architecture-audit, 30 agents,
8 authorities, adversarially verified). Verdict: BROKEN — no single owner of "close in profit";
6-8 authorities race the same winner and the lowest-yield one (mcp_take_profit +0.5%) wins.
Bot PAPER + running; edits apply on owner RESTART (NOT bounced).

## Phase A — correctness/attribution (safe, no EV-shape change)
- [x] **A3** `scalp_stale` live-net fix — spare profitable aged scalps (was reading always-None
      `pos.pnl_pct` -> 0.0<0.3 always true -> force-closed EVERY aged scalp incl. winners).
      `order_manager.py:2412-2425` now uses `_net_pnl_at_price`. + `tests/test_scalp_stale_winner_exempt.py`.
- [x] **A1** Canonical `exit_reason` enum — `_canonical_exit_reason()` collapses Claude prose
      (39 free-text labels) to `claude_close`; clean machine labels pass through. Wired into
      `_execute_close`; prose preserved in log + `exit_decision_id`. + `tests/test_canonical_exit_reason.py`.
- [ ] **A2** Entry gate (`recent_expectancy`) reads whole-trade PnL = `realized + COALESCE(partial,0)`.
      `warehouse.py:459-519` (OWNER-gated: changes the live gate).
- [ ] **A4** *(verify finding first — skeptic REFUTED it)* gate `_execute_close` discretionary LLM
      CLOSE on profitable/in-SL positions + `is_tsmom_position` guard.
- [ ] **A8** Backfill `entry_stop_px`; `r_multiple` reconstruct from ATR-SL when absent; reconcile
      patches the OPEN row. `position_tracker.py`, `scripts/backfill_warehouse_closes.py`.

## Phase B — EV-shape (CAN change WR -> default-OFF flag + PAPER A/B; flag before shipping)
ONE shared flag: `RISK["near_target_exit_enabled"]` (default False, env `NEAR_TARGET_EXIT_ENABLED`)
+ `RISK["near_target_frac"]` (0.8). Flag-OFF verified byte-identical (547 exit/monitor tests green).
- [x] **B5** `mcp_take_profit` near-target gate — `_effective_tp_threshold()` (bot_engine.py) scales the
      price-% TP distance by LEVERAGE (units trap confirmed: `_pnl_pct` built price%*lev at ~bot_engine:4396;
      verified by design workflow w/ 3x arithmetic). Wired at the TAKE_PROFIT block; flag-OFF returns the
      old 0.5/1.5. + `tests/test_near_target_tp_threshold.py` (11, incl. leverage trap).
- [x] **B6** planned-TP-first early block in `check_sl_tp` (after TSMOM, before partial-TP; TP-half only;
      reuses 'take_profit'; skips exchange-held TP) + trailing age-out activation floored above round-trip
      cost (`trailing_stop_manager._adaptive_activation`). + `tests/test_planned_tp_first_b6.py` (7, incl.
      the flag-ON take_profit vs flag-OFF trailing_stop discriminator). ⚠ A/B NOTE: flag-ON, a tick that
      gaps straight to full TP closes 100% (skips the partial-then-full shape) — intended, call out in A/B.
- **B7** scoped by design workflow (wqo628i7s) to net-new pieces; full `decide_profit_exit` merge
  DEFERRED (B5/B6 already captured the precedence — the merge is high-risk reorg with ~no new behavior):
  - [x] **B7-P1** `_reconcile_missing_tp` (LIVE/futures-only, no-op in PAPER) — restores an orphaned
        exchange TP. Design-verify caught: `fetch_open_orders` never raises (returns [] on error) so an
        empty list = INCONCLUSIVE no-op (no order churn); only acts on the SL-present/TP-absent signature.
        Impl review caught HIGH: don't cancel the working SL to restore the TP -> DOWNGRADE to polled
        (clear `_exchange_tp`) instead, never touching the SL. + hardening: clear `_exchange_tp` on a failed
        TP placement. + `tests/test_reconcile_missing_tp.py` (11).
  - [x] **B7-P2** per-position close/SL-mutation lock — SHIPPED (default-OFF PER_POSITION_LOCK_ENABLED).
        Deadlock-proof workflow (5 agents) + impl review (8 checks SHIP). Per-id **RLock** (MANDATORY:
        _replace_exchange_sl -> _place_exchange_sl_tp fail-closed re-enters close_position same-thread;
        plain Lock would self-deadlock the SL monitor). close_position/_replace_exchange_sl (om + engine)
        are thin flag-gated wrappers -> _impl, with 5s timeout-acquire (proceed-on-timeout so a bug can
        never freeze the monitor), in-lock tracker.is_position_open idempotency, and _pos_locks eviction
        in _finalize_close. + tracker.is_position_open. + tests/test_per_position_lock.py (9, incl.
        re-entrancy-no-deadlock + timeout backstop + concurrency one-real-close). Fixed 5 harness
        regressions from the rename (getattr-defensive wrappers; mock flag in test_paper_no_live_writes).
        Full suite 2050 green. LIVE-only value (inert in PAPER). LOW/deferred: add a timeout counter metric.
  - NOTE B7-P2 default OFF: flip PER_POSITION_LOCK_ENABLED=true for a PAPER soak before any live flip.
  - [ ] **B7-P3** full `decide_profit_exit` unification + 30s-monitor-as-advisory — DEFER until pre-live.
- [x] Adversarial review (4-lens workflow): verdict SHIP_WITH_NITS, **zero blockers**. B5 units
      confirmed correct at the wired call site (3x/1x arithmetic); flag-OFF feature-invariance confirmed.
      Closed the flagged coverage gaps: partial-TP-bypass discriminator, default-flag pin, spot trailing
      floor, leveraged B6, sell-side flag-OFF baseline (B6 tests 7 -> 13). Added units-footgun guard
      comment at the B5 call site. Full suite: 2030 passed, 0 failed. ruff clean (pre-existing I001@1732 only).
- [x] Committed A1/A3 + B5/B6 as 1e21250 (9 files). B7-P1 reconciler committed separately. Bot still
      running, not restarted (edits apply on owner restart). A/B note for whoever flips the flag: under
      flag-ON a tick that gaps straight to full TP closes 100% (skips partial-then-full).
- NOTE: H3 (scalp_stale) + H8 (exit_reason enum) are the prior-turn A1/A3 correctness fixes — un-gated
      by design (owner-approved "safe fixes now"), NOT under near_target_exit_enabled. Documented separately.

## Gate / honesty
Phase B must NOT be enabled hot. Ceiling remains BREAK-EVEN (entry NO_EDGE); stay PAPER. No
post-fix R:R uplift is "edge" — it is removal of self-harm, measured only after A2.

## Review (Phase A)
- A3 shipped + tested. Rest pending owner go on the EV-shape phase.

---

# Task: Full review + harden + extend (2026-06-20) — in progress

Branch: `harden/review-2026-06-20`. Bot stays PAPER. Plan: Foundation → Edge validation
→ Agent/MCP (shadow-first) → Research. Honesty guardrail: no edge is measured; new
agents/MCP ship log-only and cannot be promoted to live without an honest leak-free gate.

## Phase 0 — baseline (done)
- Deps were uninstalled in sandbox; installed requirements.txt + pytest/ruff/pyarrow +
  skill deps (jsonschema/pyyaml/scipy). `pip` works (PyPI reachable).
- BASELINE test results (pre-change):
  * `tests/` (bot): 1902 passed, 0 failed (after pyarrow install).
  * full root (skills+bot): 2504 passed, 17 skipped, 1 failed.
  * The 1 root failure = `skills/ftd-detector/.../test_fmp_client.py` — ENVIRONMENTAL
    (network egress blocks financialmodelingprep.com, 403 host-not-in-allowlist). Not a
    code bug; out of scope (equity skill, live API). Leave as-is.
- Conclusion: suite is healthy; it simply had never been run here (missing deps). The
  "make sure everything is tested and working" ask is largely satisfied by Phase 0.

## Phase 1 — foundation (done)
- [x] Backtest engine: original `timeframe` kwarg bug already resolved in current
  code; the real remaining break was `auto_backtest.py` (dead `_make_strategy`
  import + stale `OrderManager(exchanges=,dry_run=)` signature + `result.sharpe`).
  Fixed all three with a local strategy factory; added 7 regression tests
  (test_strategies_smoke.py) running all 6 strategies on a mock exchange.
- [x] Spec §12 doc/code drift: all nine loss-driven halts were removed 2026-05-27
  (replaced by soft daily-loss breaker). Rewrote the stale CLAUDE.md gotcha to
  match; added test_five_global_losses_do_not_halt (test_risk_pauses.py).
- [x] SL placement naked-exposure: `_sl_failed` was set but never read. Added
  `OrderManager._reconcile_missing_sl` (re-attempts exchange SL each monitor
  cycle, throttled 60s, clears flag on success) + wired into check_sl_tp; 4 tests.
- [x] Wiring verification: confirmed close path → on_close → _finalize_close
  (bot_engine.py:113). Added test_close_fires_on_close_hook +
  test_close_hook_failure_does_not_lose_close (test_position_tracker.py).
- Result: tests/ 1902 → 1914 passing (12 new), 0 regressions. Ruff clean on edits.

## Phase 2 — edge validation (verified; code already shipped)
- Found the honest gate ALREADY enforced: core/promotion_gate.py MIN_DSR=0.10,
  MAX_PBO=0.5, MIN_OOS_WR=0.55, MIN_AUC=0.60. test_promotion_gate_honest.py
  already rejects the exact PBO=1.0/DSR=0.0008 overfit profile (18 gate tests green).
- Leak-check infra present + tested: core/walk_forward.py embargo+purge
  (test_walk_forward_embargo.py, 13 green) and scripts/leak_check_embargo.py
  (embargo >= 96-bar horizon). Cannot RE-RUN the retrain here: load_dataset needs
  warehouse training data, and data/models/ is empty in this fresh clone (so the
  gate auto-bypasses to rule-only — no overfit model is live).
- Fixed stale config.py MODEL_GATE comment (still claimed gate loosened to
  min_dsr=0.0/max_pbo=1.0). Now documents the resolved honest thresholds.
- NET: nothing to promote (no edge, no model); honest gate guards future retrains.

## Phase 3 — agent/MCP infra (shadow-first) — done
- Multi-agent shadow ensemble ALREADY wired: core/shadow_runner.py builds
  Trend/Scalp/MeanReversion/Pattern/Liquidity + RiskAgent + ExecutionAgent via
  AgentCoordinator, writes warehouse.shadow_decisions, places NO orders, gated
  by SHADOW_MODE_ENABLED. No rebuild needed.
- NEW: read-only MCP server (mcp_server/) — trading_bot_mcp.py (FastMCP, stdio,
  6 tools: list_tables, recent_trades, performance_summary, recent_candidates,
  shadow_vs_live, query). Pure data layer warehouse_reader.py (no mcp dep);
  opens warehouse mode=ro; freeform query guarded to single SELECT. Registered
  via .mcp.json. 17 tests (test_trading_bot_mcp.py). Verified: 6 tools list,
  read-only, graceful "warehouse not found" on fresh clone.
- Documented shadow->live promotion criterion in CLAUDE.md + MCP README: agents
  & MCP stay log-only; promote only after beating live on the honest gate.
- Honesty: built as the user asked, but log-only — cannot degrade paper PnL and
  cannot be promoted on a no-edge signal.
- Full suite: 1933 passing, 0 failures.

## Phase 4 — research (futures+spot) — done (offline scope)
- Verified spot_manager + capital_allocator instantiate cleanly and ARE wired
  into bot_engine (212-222) — no signature-drift bug like auto_backtest had.
- Verified research harnesses import OK: strategy_lab, run_research,
  portfolio_scanner, run_confluence_paper. Backtest engine proven on synthetic.
- Could NOT run live pair discovery / strategy backtests: no API keys + network
  egress restricted in sandbox. Per CLAUDE.md §5, did NOT fabricate edge.
- Wrote research/pair_and_strategy_roadmap_2026_06_20.md: consolidated NO_EDGE
  evidence + a runnable roadmap (new pairs to screen via discover_all, futures
  + spot strategies to test, exact commands, honest after-cost exit criteria).

# REVIEW (2026-06-20 session)
- Branch harden/review-2026-06-20, 4 commits, bot stays PAPER throughout.
- Tests: baseline 1902 -> 1933 passing (+31 new), 0 regressions, 0 failures.
- Real bugs fixed: auto_backtest.py dead (3 bugs) -> works; _sl_failed never
  read -> reconciliation hook re-protects naked positions; 2 stale docs (Spec
  §12 halt, MODEL_GATE caveat) reconciled to code.
- New: read-only MCP server (6 tools) for interrogating the bot's reasoning.
- Honesty held: no live flip, no leverage re-enable, no overfit-model promotion;
  agents/MCP are log-only and gated. The headline finding stands — NO measured
  edge; profitability cannot be promised, only foundation + honest validation.
- BLOCKER: no git remote in sandbox -> could not push. Commits are local on the
  branch; attach origin to push. No API keys -> live data work deferred (roadmap).

## Deep research (5-angle, ~50 sources) — appended to roadmap doc
- External evidence independently CORROBORATES the internal NO_EDGE record:
  retail algo edge rare (16/22 real-fee strategies lost; 5-7% profitable at 5yr);
  directional ML OOS AUC ~0.55-0.65 not 0.76; leak-check is textbook-correct
  (embargo >= label horizon, DSR, CPCV); LLM/agent layers add cost+variance, no
  durable edge (validates log-only); only market-neutral/cost-aware edges have
  support (funding carry, DCA + threshold rebalancing). See research/
  pair_and_strategy_roadmap_2026_06_20.md §6 for citations + finalized roadmap.

## Environment limits hit
- No git remote ('origin') configured in sandbox → cannot push; commits are
  local on branch harden/review-2026-06-20. User must attach a remote to push.
- No exchange API keys + network egress allowlist → live OHLCV fetch, model
  retrain, and live-data research/backtests cannot execute here (verified offline
  via synthetic/mock data instead).

---

# Task: UNBLOCK ALL trades — edge-opinion gates to soft sizing (2026-06-11) — shipped

## Review (follow-up to symbols unblock; user saw "[EV] BLOCKED ... Phase 27" + said
## "Don't block any trades")
- Converted ALL remaining edge-opinion HARD blocks in _execute_open to soft sizing,
  each re-armable via RISK flag (defaults OFF, read with .get — no config.py change):
  * Phase 27 EV catastrophic (mean<−$0.50, n≥5): 0.0-block → ×0.25 smallest size
    (`ev_catastrophic_block_enabled`). Graduated 0.5/0.75 tiers unchanged.
  * Regime counter-trend (Phase 16): hard block → ×0.4 soft like VOLATILE
    (`regime_countertrend_block_enabled`, `regime_countertrend_size_mult`).
  * Phase 23/40 calibrator <30% hard-refuse: → rely on Phase 18 soft mult 0.7 floor
    (`calibrator_hard_refuse_enabled`). Supersedes Phase-33 carve-out that kept it.
  * AutoMutator dynamic blacklist: enforcement opt-in, tracking stays
    (`auto_mutator_block_enabled`). shorts_blocked() was already neutered (Apr 28).
- Already inert (verified, no change): caution-symbol/strategy blocks (flags off),
  BLACKLIST_HARD (empty), hours (open), SHORT_SIDE_FILTER (off), ShortGate/Spec12
  symbol+family pauses (Phase 33 flags off).
- KEPT (risk rails, not edge opinions): Spec §12 global halt, daily-loss breaker,
  drawdown halt, R:R floor, universe liquidity filter, exchange-halted, spot-can't-
  short, BTC-vol pause, position limits, post-SL cooldown.
- Tests: phase27 tier tests updated (0.25 floor + re-arm test), phase33 contract
  amended (directive supersedes carve-out), phase23 window widened. Suite 1659 green.
- ⚠ Applies on bot RESTART. ⚠ Carries into CONTROLLED_LIVE if flipped.

# Task: UNBLOCK ALL symbols + all-exchange listings (2026-06-11) — shipped

## Review
- Audit of every block mechanism: BLACKLIST_HARD empty (6-major block only inside the
  inert SCALP_TIER_ENABLED=false kill-switch branch; no env override), data/blacklist.json
  {}, auto_mutations {}, risk_state symbol/family pauses {}, hours all-open
  (BLOCKED_HOURS_UTC=set()), short-side filter off. The ONLY standing block was
  ANALYSIS_ONLY_BASES (15 commodity/equity perp bases — all listed on all 3 venues).
- Change: is_analysis_only() gated behind ANALYSIS_ONLY_ENFORCED (env, default OFF).
  Bases set RETAINED as perp-only registry (mcp_brain fetch routing + _collect_all_coins
  depend on it — emptying it would break data fetching). Re-arm: ANALYSIS_ONLY_ENFORCED=true.
- "Add new symbols on all exchanges": TRADING_MODE=all discovery already scans every
  liquid USDT perp per venue incl. these perps; the unblock is what makes them tradeable.
  No static-list change (commodity perps deliberately pruned from _TOP_SPOT 2026-05-31
  to avoid spot-404 noise; discovery + perp fallback handle them).
- Tests: test_analysis_only_gating.py reworked (8 pass: default-unblocked pinned, match
  logic preserved behind flag). Suite 1657 green. ⚠ Applies on bot RESTART. ⚠ Carries to
  CONTROLLED_LIVE if ever flipped — these instruments have NO screened edge (2026-06-02
  probe: noise-like).

# Task: TradingView integration layer (2026-06-11) — shipped

## Plan (executed; user-approved via plan mode)
1. Probe TV MCP capabilities + keyless endpoints → verify: depth/auth findings recorded
2. TDD: 4 offline test files first (red) → implement tv_client/harvest/screen/crosscheck (green)
3. Backfill data/tv_cache (5 CRYPTOCAP 1D + 31 alt 1D + 10 majors 1h) → verify: bar counts
4. Pre-registered frozen-gate dominance screen → verify: report written, verdict honest
5. Forward harvester + start_all wiring + TV watchlist → verify: --once run, dedup launcher
6. Adversarial 3-reviewer workflow → fix findings → full pytest + ruff → commit, merge main

## Review
- CAPABILITY REALITY: TV MCP = data only (no order placement, no strategy-tester, no alert
  creation; get_ohlcv can't page — `to` ignored). "Test on TradingView" honestly = TV as
  independent data source + verifier. No rewrite: integration layer only.
- NEW: quant_suite/tv_client.py (keyless TV chart-websocket OHLCV; 2,600 daily bars verified;
  intraday res mapping 1h→"60"), scripts/backfill_tv_cache.py, scripts/harvest_tv.py
  (CoinGecko dominance + TV Recommend.All point-in-time hourly; skew-harvester skeleton;
  in start_all.ps1), scripts/run_tv_regime_screen.py, scripts/tv_crosscheck_ohlcv.py.
- SCREEN (pre-registered, 8 variants + price-only CONTROL TWINS, h=1d, 2020-01-02→2026-06-10,
  2,352 days, 10 bps/side, n_trials=17 provenance-traced): **NO_EDGE**. Best-IS btcd_rot14
  (IS p=.006, FDR Y) flipped sign OOS (Sharpe −0.023, DSR .006) and was redundant vs its
  control (6/8 flagged redundant — point-estimate column, noise at sub-gate magnitudes).
  Reviewer zero-cost rerun: best OOS p=0.10 → verdict cost-model-independent. No engine
  wiring (pre-registered skip); confluence forward-test untouched.
- ADVERSARIAL REVIEW (3 agents): APPROVE_WITH_NITS ×3; all found biases push PRO-PASS →
  NO_EDGE a fortiori. Fixed pre-ship: partial-final-bar drop + keep-last dedup + null-price
  skip + incomplete-fetch raise (tv_client), flush-prunes-after-write (harvest_tv),
  USDT filter (backfill), survivorship/redundancy/n_trials caveats (screen docstring).
- CROSSCHECK: TV vs ccxt Binance 1h closes, 10 majors, ~1,910 bars each → 10/10 OK, median
  0.0 bps, 0 missing bars. Bot's data pipeline independently verified.
- Watchlist "TradingBot Universe" (id 335211589) created on the TV account (17 whitelist
  perps + 4 CRYPTOCAP regime series).
- PAPER mode unchanged (user-confirmed); CONTROLLED_LIVE latch untouched. Live-path code
  untouched entirely.
- FOLLOW-UP (same session, user: "use [TV's] Global Market Data..."): pre-registered
  tradfi→crypto macro screen (scripts/run_tv_macro_screen.py — TVC:DXY/VIX, SP:SPX,
  TVC:US10Y/GOLD, 2,599 daily bars each; 7 variants + BTC-price control twins; macro
  series enter ffilled + 1-day lag b/c tradfi sessions cross 00:00 UTC) → **NO_EDGE**,
  0/7 FDR, best IS p=0.38; vix_z14's OOS p=.038 is an OOS-only fluke on an IS-rejected
  variant (DSR 0.50 < 0.90). 4 alignment tests; suite 1655 green. Strategy-tester /
  paper-trading / bar-replay are TV UI-only (no MCP endpoints) — user-facing path is
  loading pine_strategies/*.pine on charts; get_script can't return source (even STD;RSI)
  so Pine-library mining is not programmatically possible (and is re-mining price anyway).

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

## 2026-06-11 — Dashboard truth + paper-wallet integrity (review)
- [x] Root-caused the +477.98-wallet vs −173.86-PnL contradiction: pytest
      clobbered data/virtual_wallet.json (start=1000) → restart re-seeded
      wallets 4x; +913.52 unmatched margin credits for cross-reset positions;
      arithmetic reconciled to <$0.10.
- [x] Fixed: test isolation; VirtualWallet._save pytest-production guard;
      _load re-seed now re-debits open paper margin (_redebit_open_margin).
- [x] Dashboard LIVE/PAPER scoping: breakdown bal: column mode-scoped;
      warehouse panels mode-filtered (+titles); trade-history truth line in
      balances panel; whole-trade PnL everywhere; UTC day buckets; honest
      "Last 500 trades" label; "Opens Today (UTC)" relabel; equity-curve
      filter; regime title (1h).
- [x] Suite 1659→1674 green; wallet file verified UNTOUCHED after full run.
- [x] Why-losing analysis delivered (short SL cascade on bounce day 92%,
      UNBLOCK volume 4.7x, entry_invalidated kills all longs at 30min,
      no-edge structure: breakeven WR 60% vs actual 30%).
- NOTE (not done, owner decisions): entry_invalidated entry-side consistency
  tune; engine partial-TP legs not in risk.record_trade_pnl (breaker errs
  conservative); one clean wallet re-seed at zero-open-positions moment.

## 2026-06-11 — Three efficiency tunes shipped (review)
- [x] entry_invalidated gap-flip: born-invalid exempt, flip fires, unknown=old
      behavior; knob require_flip_after_entry (default True). +11 behavioral
      tests (fake-exchange MCPBrain) + replica/pin updates.
- [x] Phase-29 cooldown 30->180min, RE-ARMED as block (was advisory-only
      since 05-27 = blocked nothing), ledger persisted in risk_state.json.
- [x] CLAUDE_PORTFOLIO_MODE=off in .env (7-day PAPER experiment; baseline
      Jun 5-11: -146.03/411 trades/WR 29.2%/~279 calls/day).
- [x] Suite 1694 green; 3-lens adversarial review before merge.
- [ ] MEASURE after restart: entry_invalidated mix (expect born-invalid
      exempt lines, fewer 30-min long culls), [Risk29] BLOCKED lines,
      Claude calls/day ~0, WR/realized-R vs baseline on ~2026-06-19.

## 2026-06-11 - Profit-only hour gate (review)
- [x] Owner: "Trade only in those hours where its profitable" -> dynamic
      allow-list gate (HOUR_GATE_PROFIT_ONLY, default ON): evidence-driven
      via refresh_hour_gates.py (60d, mode-scoped, whole-trade, entry-hour,
      n>=8); fail-open on missing/stale/empty; dashboard mirrors block.
- [x] Evidence regenerated: profitable={1,13,18,20}; weekly task refreshes.
- [x] Honest caveat recorded: hour patterns 0/0 OOS survivors (Jun 2);
      expected effect = fewer trades / less bleed, NOT profit.
- [x] 3-lens review fixes: risk_state atomic write + note_sl_hit lock +
      ledger snapshot; entry-gap memo race + exact venue mirror; config
      comment lie. Suite 1707 green.

## 2026-06-11 - Quant infrastructure upgrade (review)
- [x] Vol-target risk-budget sizing (ceiling-only, 0.5%/trade, ON).
- [x] Portfolio ES soft-cap (EWMA cov, ES97.5 4h, 0.5% equity budget,
      floor 0.25, never blocks; corr buckets demoted behind flag;
      heartbeat + dashboard ES line).
- [x] core/stats_inference.py (Wilson/t/Welch/bootstrap) + CI fields in
      hour evidence & recent_expectancy ([EV-CI] log; gate rules UNCHANGED).
- [x] weekly_scorecard.py + experiments.json registry + retrain_weekly
      step 6 (BH-FDR, bundle-attribution honesty). SET start_ts AT RESTART.
- [x] B-lite: venue/fill-aware fees (Bybit/Bitget 6bps fix; maker fills
      booked as maker) + smart_executor raw PostOnly TIF removed
      (Binance live maker breaker, latent since 05-29).
- [ ] QUEUED (blueprinted, own change): honest paper maker-fill model
      (pending post-only resolver in order_manager + sim; ~150 lines;
      prize -64% fees all-maker on Jun-11 tape; measure fill rate +
      adverse selection >=2wk before any live fallback flip).
- [x] Suite 1748 green; 2-lens adversarial review before merge.

## Deferred from /autoplan provenance-bundle review (2026-06-12, plan APPROVED)
- [ ] P2 Retention policy: zip mcp_decisions archives + calls_*.jsonl older than 3 months (S)
- [ ] P2 Dashboard provenance panel: source/repaired/orphan % (S-M)
- [ ] P1-if-triggered: deep exit-side label repair — escalates if post-restart NULL r_multiple >=2% (M)
- [ ] P2 OWNER: stagger experiment knobs (one-at-a-time reversion) for clean attribution (operational)
- [ ] P3 Prompt-builder section structure -> named truncation attribution (M)
- [ ] P3 Claude-path candidates.decision_id post-parse UPDATE linkage (S)
