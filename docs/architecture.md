# Architecture

## Overview

```
┌─────────────────────────────────────────────────────────────┐
│                    TradingBot.bat / CLI                      │
│              (Interactive menu + entry points)               │
└────────────────────┬────────────────────────────────────────┘
                     │
          ┌──────────▼──────────┐
          │  MultiProfileRunner  │  ← Recommended entry point
          │  (3 profiles, arb,  │
          │   Claude, email)    │
          └──────────┬──────────┘
                     │ runs
         ┌───────────┼───────────┐
         ▼           ▼           ▼
    Conservative  Moderate  Aggressive
    (isolated)   (isolated)  (isolated)
         │
    ┌────┴──────────────────────────┐
    │         ProfileInstance        │
    │  risk    wallet    tracker     │
    │  executor  blacklist trailing  │
    └────┬──────────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │       StrategySelector         │
    │  Multi-TF scan → opportunities │
    └────┬──────────────────────────┘
         │
    ┌────▼──────────────────────────┐
    │        DirectExecutor          │
    │  opportunity → open_position   │
    └───────────────────────────────┘
```

## Core Components

### MultiProfileRunner (`core/multi_profile_runner.py`)

Orchestrates all three profiles simultaneously. Each profile is fully isolated — separate wallet, position tracker, risk manager, and blacklist. A single `StrategySelector` instance scans symbols once and distributes the signals to all profiles.

**Scan cycle (every 15 minutes):**
1. Scan all configured symbols across 5 timeframes
2. Collect `TradeOpportunity` objects, sorted by confidence
3. For each profile, run `check_exits()` first, then attempt to open new positions
4. Save comparison snapshot

**Arbitrage scan (every 2 minutes):**
1. Collect all spot prices from all exchanges
2. Apply 8 institutional filters
3. Execute qualifying spread trades

### StrategySelector (`core/strategy_selector.py`)

Analyses each symbol across 5 timeframes using pure-pandas indicators:
- ADX + DMI (trend strength and direction)
- EMA 9/21/50/200 (structure and trend)
- RSI 14 (momentum, overbought/oversold)
- ATR 14 (volatility, stop sizing)
- Bollinger Bands 20 (ranging detection)
- Volume ratio (confirmation)

Produces `TradeOpportunity` objects with:
- `direction`: "buy" (long) or "sell" (short, futures only)
- `market_type`: "spot" or "futures"
- `strategy`: which strategy key to use
- `confidence`: 0.0 – 1.0
- `regime`: trending / ranging / volatile / weak_trend

### DirectExecutor (`core/direct_executor.py`)

Converts a `TradeOpportunity` into an actual position without re-running the strategy. This eliminates the "signal stale by the time it fires" problem.

Guards applied before opening:
1. Spot SHORT blocked
2. Blacklist check
3. Max open positions check
4. Duplicate position guard
5. Minimum notional ($2 paper / $10 live)
6. R:R minimum (1.5:1)

### Exchange Clients (`exchanges/`)

Each exchange has a dedicated client extending `BaseExchange`. Key quirks handled:

| Exchange | Quirks |
|---|---|
| Binance | Clock sync (`-1021` retry), `adjustForTimeDifference=True` |
| MEXC | Futures geo-blocked in some regions, custom OHLCV intervals |
| Bybit | Unified Account → balance in `total` not `free` |
| Bitget | Requires Passphrase, futures balance via `{"type":"umcbl"}` |

### Learning Engine (`core/learning_engine.py`)

Reads all closed trades from all 3 profile position files. Computes per-strategy statistics:
- Win rate
- Average P&L
- Fees paid
- Best/worst trade

Feeds into `KnowledgeModel` which adjusts confidence multipliers per strategy. Requires ≥ 15 trades before flagging a strategy as underperforming (prevents false alarms from small samples).

### Blacklist Manager (`core/blacklist_manager.py`)

Per-profile (each profile has `data/profiles/{name}/blacklist.json`). Triggers after 3 consecutive stop-losses on the same symbol. Auto-expires after 24 hours. A win resets the consecutive SL counter.

---

## Data Flow

```
Exchange API → fetch_ohlcv()
                │
                ▼
          TFSnapshot (per timeframe)
                │
                ▼
     _build_opportunities()
      (ADX/RSI/EMA/Volume scoring)
                │
                ▼
        TradeOpportunity
      (confidence, regime, direction)
                │
                ▼
     Claude AI confidence adjustment
                │
                ▼
      DirectExecutor.execute()
      (guards → size → SL/TP → open)
                │
                ▼
          Position (in tracker + wallet)
                │
    ┌───────────┴──────────┐
    ▼                      ▼
  SL/TP hit          Trailing stop
    │                      │
    ▼                      ▼
 close_position()    close_position()
    │
    ▼
  Learning engine ingests closed trade
    │
    ▼
  KnowledgeModel updates confidence
```
