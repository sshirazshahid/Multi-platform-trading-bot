# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
