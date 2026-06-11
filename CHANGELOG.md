# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
