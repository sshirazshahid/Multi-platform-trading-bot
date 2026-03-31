# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

---

## [Unreleased]

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
