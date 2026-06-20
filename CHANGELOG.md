# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

### Added (2026-06-20 — review/harden pass: read-only MCP server + offline evidence-backed research labs)
Audit/harden pass after a full review (3 internal audits + ~50 external sources + live data) that
re-confirmed the bot has no measured entry edge and that the requested chart strategies (harmonic,
stochastic, ICT, Asian-range, Dow-swing, Bollinger-squeeze, AI-valuation) lack after-cost edge. Bot
stays PAPER; agents/MCP stay log-only.
- **MCP server** (`mcp_server/`): read-only server (6 tools) over the warehouse/decisions — opens
  SQLite `mode=ro`, imports no bot/config/ccxt code, freeform query guarded to a single SELECT.
  Register via `.mcp.json.example`. Pure data layer in `warehouse_reader.py` (no `mcp` dependency).
- **Research labs** (`research/`): offline, deterministic, with out-of-sample splits + 1000-path
  moving-block Monte-Carlo — `dca_rebalance_lab.py` (DCA / lump-sum / threshold-rebalance),
  `funding_carry_lab.py` (delta-neutral cash-and-carry), `data_io.py` (CSV/candle loaders),
  `run_spot_study.py` (multi-asset study). Real ~3y BTC/ETH/SOL fixtures in `research/sample_data/`.
  Findings + refined roadmap in `research/finalized_trading_system_plan_2026_06_20.md` (DCA is
  risk-reduction not edge; carry needs real funding history; promote nothing without after-cost OOS edge).
- **CI**: new `research-labs` job (Python 3.11) lints + tests the new modules (the existing CI never
  ran `tests/`).
- +31 tests (suite 1988 green).

### Fixed (2026-06-20 — review/harden pass)
- `auto_backtest.py`: repaired the dead parameter-sweep runner — removed the deleted
  `core.strategy_selector._make_strategy` import (added a local legacy-strategy factory), fixed the
  stale `OrderManager(exchanges=,dry_run=)` signature, and `result.sharpe`→`sharpe_ratio`. This was
  the cause of the 2026-03-30 sweep recording 144 jobs / 0 successes. +7 regression tests run all 6
  strategies on a mock exchange.
- `core/order_manager.py`: added `_reconcile_missing_sl`, wired into `check_sl_tp`. A position whose
  exchange-side SL placement failed set `_sl_failed=True`, but that flag was never read — the
  position then relied only on the slower polled-SL checks and never regained exchange-side
  protection. The reconciler re-attempts placement once per minute per position (no-op in paper/spot
  and when already protected). +4 tests.
- Reconciled two stale docs to code (with regression tests): the Spec §12 loss-driven halts were
  permanently removed 2026-05-27 (`CLAUDE.md` previously described a non-existent 4h auto-resume);
  the `MODEL_GATE` honest thresholds (MIN_DSR=0.10 / MAX_PBO=0.5) are already enforced in
  `core/promotion_gate.py` (`config.py` comment was stale).

### Fixed (2026-06-15 — TSMOM mode was invisible: relabel + disable MCP monitor under tsmom)
After setting `SIGNAL_SOURCE=tsmom` and restarting, the bot looked like it was "still Claude/MCP".
Investigation found the entry path WAS correctly routing to tsmom (the post-flip run dropped the
`mcp_brain.analyze_portfolio -> algorithmic` log line and sat in cash, which is correct tsmom
behaviour when majors aren't trending up), but tsmom was indistinguishable from MCP because:
(1) the portfolio cycle hardcoded a `[Claude]` log prefix, (2) nothing announced the active signal
at startup, and (3) the MCP position monitor (`_run_mcp_position_monitor`) kept making Claude/MCP
advisory calls every 90s. Fixes in `core/bot_engine.py`: the cycle log is now source-aware
(`[TSMOM]` vs `[Claude]`); startup logs `Signal source: TSMOM ...`; and under `SIGNAL_SOURCE=tsmom`
the MCP position monitor is skipped entirely (the deterministic 10s SL/TP thread and tsmom's own
momentum-flip exits still run, so disaster stops stay live) — so tsmom mode makes **no** Claude/MCP
decisions. +2 tests (`tests/test_tsmom_mode_wiring.py`). Note: `run_confluence_paper.py` is a
SEPARATE standalone paper test (imports nothing from `core/`) and is unaffected by `SIGNAL_SOURCE`.

### Added (2026-06-15 — long-only TSMOM signal layer + capital-preservation exit gate; default-off)
Applies the systematic-design research (`reports/systematic_design_research_2026-06-15.md`) as a
reversible, validate-first signal redesign. Keeps all infrastructure; swaps only the signal layer,
behind a flag, defaulting to the existing path so nothing changes until explicitly enabled.
- **Validation gate first.** `scripts/tsmom_validation_backtest.py` → `reports/tsmom_validation_2026-06-15.md`:
  long-only TSMOM (28d lookback, vol-targeted) on BTC/ETH/SOL/BNB/XRP, IS 2021-24 / OOS 2025-26, after
  cost. NO_GO on the strict *profit* gate (2/5 positive OOS Sharpe) but beat buy-and-hold risk-adjusted
  on 4/5 and roughly **halved drawdown** — a capital preserver, not a profit engine. Owner re-scoped the
  objective to capital preservation and approved building it in PAPER.
- **Signal** (`core/tsmom_signal.py`): `TSMOMSignal.analyze_portfolio()` emits the exact `mcp_brain`
  action-dict. Long-only (never shorts), majors-only universe, daily 28d momentum — OPEN on positive /
  CLOSE on flip / hold otherwise, leverage 1, vol-targeted size, fresh `decision_id` + `source="tsmom"`.
- **Flag** `SIGNAL_SOURCE=mcp|tsmom` (config.py, default `mcp`, raises on bad value);
  `bot_engine._claude_portfolio_cycle` branches at the call site via a lazy `_tsmom_signal()` builder.
- **Exit gate (Phase 2b).** A tsmom position is held to its momentum-flip CLOSE: a single
  `is_tsmom_position` branch in `order_manager.check_sl_tp` enforces disaster-stops-only (widened
  `TSMOM_HARD_MAX_LOSS_PCT=9%` + a live SL trigger independent of `take_profit`) then skips partial-TP /
  trailing / breakeven / fixed-TP / entry-staleness / age-stale; `_run_mcp_position_monitor` skips tsmom
  in its deterministic pre-pass and MCP advice loop. Entry shaping (`tsmom_entry_shape`) keeps the ~8%
  disaster stop (was silently downgraded to a ~1.5% tier stop), sets `take_profit=0`, leverage 1, and
  bypasses the R:R gate. Source threaded into the persisted `Position.strategy` tag so the policy follows
  how a position was opened, immune to a mid-flight flag flip. Disaster stops preserved end-to-end
  (wick / hard-max-loss / live-SL / exchange-SL). Exit surfaces mapped by an adversarially-verified audit.
- Tests: `tests/test_tsmom_signal.py` (16) + `tests/test_tsmom_exit_gate.py` (7); suite +29 (1834 green).
- Known residual (CONTROLLED_LIVE-only): `position_tracker` ghost-sync auto-close bypasses the gate but
  skips PAPER positions; to address before any live use. Plan: `tasks/plan_tsmom_redesign_2026-06-15.md`.

### Fixed (2026-06-14 — daily-counter rollover decoupled from entry path)
The UTC-day rollover of `daily_pnl`/`trades_today` lived only in `risk_manager.can_trade()`,
which is reached only via the entry-execution path (`bot_engine._execute_open`). When entries
were gated upstream for >1 day (the 2026-06-11 `CLAUDE_PORTFOLIO_MODE=off` experiment routed to
the algo-only SCALP path → 0 ALLOW over 45,750 candidates in 48h), `can_trade()` was never
called, so the counters froze at their 06-11 values across two day-boundaries and
dashboards/notifiers reading `risk_state.json` reported a 2-day-stale "today". Extracted
`risk_manager.roll_day_if_needed()` (idempotent, side-effect-free within a day), call it once per
portfolio cycle in `bot_engine._claude_portfolio_cycle`, and route the two existing entry-path
rollovers through it (DRY). Pure correctness fix; no behavior change to gating, sizing, or exits.
+3 tests (`tests/test_daily_counter_rollover.py`). Applies on RESTART.
- Hygiene: gitignore `.gstack/` (gstack-generated artifacts in the repo root).

### Added (2026-06-12 — decision provenance bundle; /autoplan-reviewed, owner-approved)
Record fidelity, not new signals: every entry/exit decision becomes reconstructable
end-to-end (raw LLM response → parse/clamps → order → warehouse row). Root cause of
the Jun-4 "attribution corrupt" finding. All engine changes apply on RESTART.
- **Provenance core**: `decision_id` (uuid4 per action) + `source: claude|algo` +
  pre-clamp `sl/tp/leverage/size_pct_raw` + `repaired`/`attempt`/`response_sha256`
  minted at parse and threaded `mcp_brain → bot_engine → order_manager → warehouse`
  (`trades.decision_id`, `trades.exit_decision_id`, `candidates.decision_id`,
  idempotent ALTERs). Parse-time leverage/size clamps record-only (`_execute_open`
  ignores LLM sizing by design); symbol check is LOG-ONLY (`symbol_unlisted`).
- **Rejection taxonomy**: reason stashes at all 45 `_execute_open` exits + 19
  `open_position` exits (meta-test enforces coverage) + `cycle_cap` rows for
  actions dropped by the per-cycle cap; logged as `{"type":"rejection"}` rows
  keyed by decision_id. Spot-fallback retry now forwards
  `candidate_id/mcp_score/model_version/decision_id` (fixes silent kwarg drop).
- **Raw LLM capture**: full `prompt` + `raw_response` per call in
  `data/claude_audit/calls_*.jsonl` (thread-locked appends); audit-write failures
  surface in `data/claude_audit_failures.json` instead of being swallowed;
  truncation logs `(orig_len, cut_len, tail_80)`.
- **Decision-log archive rotation**: `mcp_decisions.jsonl` 2MB rotation now
  archive-renames to `mcp_decisions.<ts>.jsonl` (was: destructive truncate to
  last 500 lines, which would have erased the reconciliation substrate).
- **Reconciliation consumer**: `scripts/decision_reconciliation.py` — per-source
  WR/expectancy, orphan taxonomy (monitor advice / cycle-capped / ghost-import
  exemptions), decisions↔orders diff, NULL-`r_multiple` <2% label-quality gate —
  plus weekly-scorecard provenance-health line and line-streaming reads.
- **Atomic state writes**: `utils/atomic_io.py` (temp+rename) for
  `knowledge_model.json` + `trailing_peaks.json` — crash mid-write no longer
  wipes learned state. Stale `position_advice` discarded on warm restart
  (declared behavior change, bounded by the existing 10-min gate).
- **Guards & ops**: frozen-inventory venue-write test (AST scan, zero new ccxt
  write sites allowed unaudited) + zero-`withdraw` invariant; gitignore pins for
  `data/claude_audit/` + `data/mcp_decisions*` with pin tests; `/daily-sync` +
  `/decision` project commands; `data/decisions/` ADR records; doc-rot fixed
  (advisory-only headers, stale `total_pnl` gotcha). Tests 1,748 → 1,808.

### Added (2026-06-11 — quant infrastructure upgrade, owner: "technically/mathematically/financially advanced")
Risk / execution / measurement only — NO new signal families (~17 screened
NO_EDGE under frozen gates; "advanced" machinery pretending alpha would be
re-litigation). All engine changes apply on RESTART.
- **Vol-target risk-budget sizing** (`core/vol_target.risk_budget_margin` +
  `VOL_TARGET_SIZING`, default ON): margin capped so loss-at-SL ≤ 0.5% of
  the pocket. Ceiling-only min() with the multiplier chain — can only
  shrink, never grow. With the ATR-based SL this equalizes per-trade risk
  (previously varied 2.3× with SL width) and makes notional ~ 1/ATR.
- **Portfolio Expected-Shortfall soft-cap** (`core/portfolio_risk.py` +
  `ES_RISK`, default ON): EWMA (RiskMetrics λ=0.94) covariance on 1h
  returns → parametric ES₉₇.₅ of the open book in USD (signed legs net
  longs/shorts) → soft size taper (floor 0.25, never blocks) when
  projected ES exceeds 0.5% of equity (2% measured to never bind at paper
  scale). Static corr-bucket taper demoted behind
  `RISK["corr_group_taper_enabled"]`. Live ES on heartbeat + dashboard.
- **Statistical inference layer** (`core/stats_inference.py`: Wilson CI,
  stdlib Student-t, Welch + bootstrap-delta verdicts): informational CI
  fields in hour-gate evidence and `recent_expectancy` (+`[EV-CI]`
  NOISE/SIGNAL log) — gate rules unchanged (owner directives stand).
- **Weekly experiment scorecard** (`scripts/weekly_scorecard.py` +
  `scripts/experiments.json` registry, wired into `retrain_weekly.ps1`):
  pre/post inference (Wilson/t/bootstrap/Welch + BH-FDR across
  experiments) on every active experiment (Claude-off, gap-flip, 180min
  cooldown, hour gate) with honesty baked in: bundle-level attribution
  disclaimer, "IMPROVED = screening not confirmation", fee shares labeled
  modeled. Set each experiment's `start_ts` at the activating restart.
- **Maker-first groundwork (B-lite)**: venue+fill-aware `_fee_rate`
  (Bybit/Bitget taker was undercharged 5bps vs real 6bps; LIVE maker
  fills were booked at taker), and removed the raw
  `timeInForce="PostOnly"` in `smart_executor` (Bybit-only vocabulary —
  would break Binance USD-M live maker orders; latent since 2026-05-29).
  The full honest paper maker-fill model (pending post-only orders
  resolved against subsequent candles, TTL→taker fallback) is blueprinted
  (4-agent trace, Jun 11) and queued as its own change — prize measured
  at −64% fees all-maker on the Jun-11 tape.
  Tests +41 (suite 1707→1748).

### Changed (2026-06-11 — three efficiency tunes, owner: "ship it all")
- **entry_invalidated gap-flip semantics** (`mcp_brain.is_entry_invalidated`
  + `order_manager` + `ENTRY_STALENESS_EXIT["require_flip_after_entry"]`):
  the 4h-EMA staleness exit now fires ONLY when the gap actually flipped
  after entry. Born-invalid positions (gap already ≥0.15% against at the
  entry bar — ALL 85 of its closes since Jun 4, every one a long killed at
  exactly 30min) are exempt; SL/TP/trailing manage them. Entry-bar gap is
  computed lazily from a fresh 250-bar 4h window, memoized per position;
  unknown → old fire behavior (fail-conservative). Set the knob False to
  restore old semantics exactly.
- **Phase-29 post-SL cooldown: 30→180 min, re-armed as a BLOCK, persisted**
  (`risk_manager`, `bot_engine._execute_open`): the cooldown had been
  advisory-only since 2026-05-27 — it blocked nothing while ADA/DOT/BNB/APT
  were re-shorted 9-12× into 70 stop-losses on Jun 11. Now a hard block per
  (symbol, side) for 180 min after a stop_loss (6h Layer-2 guard after 2+
  SLs in 24h also blocking again); ledger persisted in `risk_state.json`
  so it survives restarts. Time-based protection, kept under UNBLOCK.
- **CLAUDE_PORTFOLIO_MODE=off PAPER experiment** (.env, gitignored): 7 days
  of algo-only entries + deterministic exits. Baseline (throttled, Jun
  5-11): −146.03 / 411 trades / WR 29.2% / ~279 Claude calls/day.
  All three apply on bot RESTART. Tests +19 (suite 1675→1694).
- **3-lens adversarial review fixes** (pre-merge): `note_sl_hit` now locks
  + `_save_state` snapshots the ledger and writes atomically (tmp +
  os.replace — a torn risk_state.json previously meant "starting fresh"
  on reboot, losing daily_pnl + pauses + the ledger); `_entry_gap_at_bar`
  memo read race-proofed (.get) and venue order now exactly mirrors the
  indicator fetch (all-spot first, perp fallback); stale "30min cooldown,
  not a block" comment fixed in config.py.

### Added (2026-06-11 — profit-only hour gate, owner: "Trade only in those hours where its profitable")
- `HOUR_GATE_PROFIT_ONLY` (default ON, env-disable): entries allowed only
  during UTC hours whose 60-day warehouse history (current mode,
  whole-trade PnL, entry-hour basis, n≥8) is net-positive — consumed from
  `data/hour_gate_evidence.json` `profitable` (refreshed weekly by
  `scripts/refresh_hour_gates.py`, which now also scopes by mode and sums
  partial-TP legs). Fail-open on missing/stale(>14d)/empty evidence.
  At ship time: profitable = {1, 13, 18, 20}. Dashboard TRADING GATES
  mirrors the block ("BLOCKED (hour not profitable)"). HONEST CAVEAT
  (recorded in config): hour patterns did NOT survive IS/OOS validation
  (2026-06-02, 0 survivors) — expected effect is fewer trades / less
  bleed, not profit. Supersedes the 2026-05-27 dynamic-hour-gate disable.
  Tests +13 (suite 1694→1707). Applies on RESTART.

### Fixed (2026-06-11 — dashboard truth + paper-wallet integrity)
- **CRITICAL: pytest clobbered the production paper wallet.**
  `tests/test_partial_tp_accounting.py` wrote the real `data/virtual_wallet.json`
  (start=1000) on every repo-root test run; the next bot restart silently
  re-seeded all paper balances to 5000/exchange (fired 4× Jun 10-11), erasing
  paper losses — and the 8 positions open across the final re-seed credited
  +913.52 USDT of unmatched margin at close. The dashboard's "+477.98 / ROI
  +3.19%" vs "−173.86 all-time" contradiction reconciled to <$0.10 residual.
  Fixes: test isolated (tmp_path/chdir); `VirtualWallet._save` refuses to write
  the production file under pytest; `_load`'s re-seed path now re-debits margin
  (+est. entry fee) of open paper positions (`_redebit_open_margin`).
- **Dashboard LIVE/PAPER scoping** (`dashboard.py`): EXCHANGE BREAKDOWN `bal:`
  showed REAL account balances in PAPER mode → now mode-scoped; warehouse panels
  (PER-SYMBOL EDGE / LOSS-CLUSTER / SLIPPAGE) mixed 772 PAPER + 498
  CONTROLLED_LIVE rows → queries now filter `mode`, titles show the mode;
  balances panel adds a "Trade PnL (mode, all history)" truth line beside the
  sim-wallet ROI.
- **Dashboard consistency**: all PnL sums now whole-trade (runner `pnl` +
  `realized_partial_pnl`; partial-TP profits +22.49 were invisible); Performance/
  Daily buckets moved local→UTC to match the engine's risk counters; "All Time
  (since X)" relabeled "Last 500 trades (since X)" once the position-tracker
  ring buffer is full; risk panel "Trades Today" relabeled "Opens Today (UTC)"
  (it counts opens, not closes); equity curve applies the real-trade filter;
  MARKET REGIME title names its 1h timeframe (vs the gates panel's 4h BTC trend).
  +15 tests (`tests/test_dashboard_mode_scoping.py`), suite 1659→1674.
- **Daily-loss breaker now sees partial-TP legs** (owner: "wire it"):
  `partial_close_position` records the partial's net PnL via
  `risk.record_trade_pnl(..., is_win=None)` when it banks — previously only the
  runner's `pos.pnl` reached the breaker, so banked partial profits were
  invisible to the loss budget. `is_win=None` keeps Spec §12 streaks /
  recent-results / Kelly untouched (those update once, on the whole trade, at
  `_finalize_close`). Suite 1674→1675.

### Added (2026-06-11 — TradingView integration layer)
- `quant_suite/tv_client.py`: keyless TradingView chart-websocket OHLCV client
  (research-only; 2,600+ daily bars incl. CRYPTOCAP aggregates) + `scripts/backfill_tv_cache.py`
- `scripts/harvest_tv.py`: keyless forward-harvester (CoinGecko dominance + TV
  Recommend.All ratings, hourly point-in-time records) — wired into `start_all.ps1`
- `scripts/run_tv_regime_screen.py`: pre-registered frozen-gate screen of
  USDT.D/BTC.D/TOTAL/TOTAL3 regime signals (8 variants + price-only control twins)
  → **NO_EDGE** (best-IS variant failed OOS and was redundant vs its control;
  zero-cost rerun also fails; `reports/tv_regime_screen_2026-06-10.md`)
- `scripts/tv_crosscheck_ohlcv.py`: TV-vs-exchange data verification → 10/10 majors
  OK, median divergence 0.0 bps (`reports/tv_crosscheck_2026-06-10.md`)
- UNBLOCK directive #2 (2026-06-11, owner: "Don't block any trades"): every
  remaining edge-opinion hard block converted to soft sizing, re-armable via RISK
  flags (all default OFF) — Phase 27 EV catastrophic → ×0.25 floor
  (`ev_catastrophic_block_enabled`), regime counter-trend → ×0.4 soft
  (`regime_countertrend_block_enabled`), Phase 23/40 calibrator hard-refuse →
  soft mult only (`calibrator_hard_refuse_enabled`), AutoMutator dynamic
  blacklist → tracking-only (`auto_mutator_block_enabled`). Risk rails unchanged
  (Spec §12 global halt, daily-loss breaker, R:R floor, liquidity filter,
  exchange-halted, spot-can't-short, BTC-vol pause).
- UNBLOCK directive (2026-06-11, owner): analysis-only entry block now opt-in via
  `ANALYSIS_ONLY_ENFORCED` (default OFF) — commodity/equity perps listed on all 3
  exchanges (XAU/XAG/CL/BZ/COPPER + TSLA/NVDA/AMZN/AAPL/GOOGL/META/MSFT/MSTR/COIN)
  are tradeable; they enter via `TRADING_MODE=all` discovery. All other blocks
  verified already clear (BLACKLIST_HARD empty, hours open, no pauses).
- `scripts/run_tv_macro_screen.py`: pre-registered tradfi→crypto regime screen
  (TV global data: DXY/VIX/SPX/US10Y/GOLD, 7 variants + BTC-price controls,
  session-stamp causality lag) → **NO_EDGE** (0/7 FDR, best IS p=0.38;
  `reports/tv_macro_screen_2026-06-10.md`)

### Added
- Multi-profile runner: Conservative, Moderate, and Aggressive profiles run simultaneously
- Per-profile isolated wallets, position trackers, and blacklists
- Live real-time dashboard with exchange balance fetching and unrealized P&L
- Bybit Unified Account balance fix (`total` instead of `free`)
- Cross-exchange arbitrage engine with 8 institutional filters
- Learning engine: analyses closed trades, adjusts strategy confidence
- Embedded Claude AI analyst (zero API cost)
- Optional live Anthropic API integration
- Email reports: daily HTML + instant halt alerts
- Fear & Greed index integration (Alternative.me)
- Multi-timeframe strategy selector (1d/4h/1h/15m/1m consensus)
- ATR-based dynamic stop-loss and take-profit
- Trailing stop manager
- Per-profile circuit breakers (max drawdown + daily loss limit)
- Auto-resume after drawdown halt (30-min cooldown + win rate check)
- Wallet replicator: mirror live exchange balance into paper wallets
- Dynamic pair discovery with volume filtering
- Commodity futures support: Gold (XAU), Silver (XAG)
- Windows interactive menu launcher (`TradingBot.bat`)

### Fixed
- `supertrend_spot` 0% win rate: now requires `full_bull` (all TFs agree) before opening
- Minimum notional: $2 for paper trading (was $10, blocked all $100-wallet trades)
- Blacklist shared across profiles: each profile now has its own file
- `_extract_usdt` for Bybit: reads `total` equity, not `free` (which is 0 on Unified)
- Bybit balance double-counted: called once for unified account, not once per market type
- `pair_discovery.py`: volume filter was defined but never applied
- `base_strategy.py`: minimum candle check raised from 2 → 30
- Insufficient OHLCV warnings demoted from WARNING to DEBUG

### Security
- No credentials stored in source code
- `.env` excluded from git via `.gitignore`
- Paper trading mode enforced by default (`DRY_RUN=true`)

---

## How to Update This File

When you make a change, add an entry under `[Unreleased]` in the appropriate section:

- **Added** — new features
- **Changed** — changes to existing functionality
- **Deprecated** — features that will be removed in a future release
- **Removed** — removed features
- **Fixed** — bug fixes
- **Security** — security-related changes
