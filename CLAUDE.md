# CLAUDE.md

## Quick Start

```bash
# Setup (first time)
python -m venv venv && venv/Scripts/pip install -r requirements.txt
cp .env.example .env  # Fill in exchange API keys

# Run bot (dry-run by default)
venv/Scripts/python main.py

# Status check
venv/Scripts/python main.py --status

# Live dashboard (60s refresh)
venv/Scripts/python dashboard.py --refresh 60

# Multi-profile learning mode (DRY_RUN only)
venv/Scripts/python multi_profile_main.py

# Backtest a strategy
venv/Scripts/python backtest.py --strategy multitf --symbol BTC/USDT --days 60

# Syntax-check after edits
python -m py_compile <file.py>
```

## Architecture

```
main.py                  # Single-profile entry point -> BotEngine
multi_profile_main.py    # 3-profile (conservative/moderate/aggressive) runner
dashboard.py             # Rich terminal dashboard -- reads live exchange state
config.py                # ALL tunable parameters: pairs, risk, strategy params
bot_helper.py            # CLI helper called by TradingBot.bat menu
TradingBot.bat           # Windows menu launcher (setup wizard, bot, dashboard, etc.)

core/
  bot_engine.py           # Main loop: scan -> signal -> decide -> execute -> track
  mcp_brain.py            # AI meta-controller -- adjusts confidence/sizing dynamically
  strategy_selector.py    # Maps (exchange, market_type) -> strategy list
  order_manager.py        # Places/cancels orders via exchange clients
  position_tracker.py     # Tracks open/closed positions in data/positions.json
  risk_manager.py         # Position sizing, daily loss halt, max drawdown
  kelly_sizer.py          # Kelly criterion position sizing
  trailing_stop_manager.py # Trailing stop logic
  arbitrage_engine.py     # Cross-exchange arb detection
  claude_analyst.py       # Anthropic API calls for market analysis
  claude_trader.py        # AI-assisted trade decisions

exchanges/
  base.py                 # ABC -- all clients inherit; handles ccxt errors, retries
  binance_client.py       # Binance spot + futures
  mexc_client.py          # MEXC spot only (futures geo-blocked from Pakistan)
  bybit_client.py         # Bybit unified account
  bitget_client.py        # Bitget spot + futures

strategies/
  base_strategy.py        # ABC -- all strategies implement analyze()
  multi_tf.py             # #1 performer -- multi-timeframe trend (4h/1h/15m)
  supertrend.py           # Supertrend + RSI filter
  mean_reversion.py       # Bollinger Band mean reversion (spot)
  dca_strategy.py         # Dollar-cost averaging accumulation
  grid_trading.py         # Grid bot
  scalping.py             # 1m scalping
  trend_following.py      # EMA crossover trend (currently disabled -- 0% WR live)
  funding_rate_arb.py     # Funding rate carry trades
  rule_engine.py          # Custom indicator-based rules from config

utils/
  logger.py               # Loguru setup -- stdout + daily rotated logs/ + errors.log
  claude_client.py        # Anthropic SDK wrapper
  anthropic_key.py        # API key management
  notifier.py             # Email/alert dispatch
```

## Key Patterns

- **ccxt symbol format**: Spot = `"BTC/USDT"`, Futures = `"BTC/USDT:USDT"`. The `:USDT` suffix is required for USDT-margined perpetuals. Commodities (Gold, Silver, Oil) use the same format: `"XAU/USDT:USDT"`.
- **Exchange client pattern**: All inherit `exchanges/base.py`. Every method calls `self._ready()` before touching ccxt. Symbol-not-found errors are silenced (return empty, not logged).
- **Strategy pattern**: All inherit `base_strategy.py` and implement `analyze()`. Returns signal dict with confidence, side, SL/TP.
- **Config is king**: Nearly all tunable values live in `config.py` -- strategy params, risk limits, trading pairs, fee structure, blocked hours. Edit config.py, not the strategy files.
- **Positions stored as JSON**: `data/positions.json` (single-profile) or `data/profiles/{conservative,moderate,aggressive}/positions.json` (multi-profile).
- **MCP Brain** (`core/mcp_brain.py`): AI meta-controller that monitors ALL exchange positions and adjusts decisions. State persisted in `data/mcp_state.json`.

## Environment

All secrets in `.env` (copied from `.env.example`). Required:
- `BINANCE_API_KEY`, `BINANCE_SECRET_KEY`
- `BYBIT_API_KEY`, `BYBIT_SECRET_KEY`
- `BITGET_API_KEY`, `BITGET_SECRET_KEY`, `BITGET_PASSPHRASE`
- `ANTHROPIC_API_KEY` (for Claude AI analysis features)
- `DRY_RUN=true` (MUST be true until validated -- controls real money)

Optional: `MEXC_*`, `GMAIL_*`, `TRADING_MODE` (usdt_only|portfolio|all).

## Gotchas

- **NEVER edit `.env` via Claude** -- contains live exchange API keys and credentials.
- **MEXC futures are geo-blocked** from Pakistan (403). `TRADING_PAIRS["mexc"]["futures"]` is empty by design.
- **Bybit unified account** -- balance is in `bal["total"]["USDT"]`, NOT `bal["free"]["USDT"]`. See bot_engine.py header comment.
- **DRY_RUN_BALANCE** defaults to $100/exchange/profile. Use TradingBot.bat [L] to replicate live wallet balances.
- **Loguru imported at module level** in `utils/logger.py` -- `setup_logger()` runs on import. Don't call it twice.
- **Multi-profile mode** requires DRY_RUN=true. It won't start in LIVE mode.
- **TrendFollowing strategy** is disabled (0% WR in 8 live trades). Don't re-enable without new backtest evidence.

## Logs

- `logs/bot_YYYY-MM-DD.log` -- daily rotated, 14-day retention, zipped
- `logs/errors.log` -- ERROR+ only, 10MB rotation, 30-day retention
- Dashboard fetch warnings throttled to 1/min per distinct message
