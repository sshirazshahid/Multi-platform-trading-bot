# 🤖 Trading Bot

A multi-exchange, multi-profile automated cryptocurrency trading bot with AI analysis, real-time dashboard, and institutional-grade risk management.

> ⚠️ **DISCLAIMER**: This software is for educational purposes only. Cryptocurrency trading involves substantial risk. Always start with `DRY_RUN=true`. Never risk money you cannot afford to lose.

---

## ✨ Features

| Category | Details |
|---|---|
| **Exchanges** | Binance, MEXC, Bybit, Bitget — Spot + USDT-Margined Futures |
| **Asset Classes** | Crypto, Gold (XAU), Silver (XAG), Oil (WTI) |
| **Strategies** | Supertrend, MultiTF, MeanReversion, TrendFollowing, Grid, Scalping, DCA |
| **Risk Profiles** | Conservative, Moderate, Aggressive — run simultaneously, fully isolated |
| **AI Analysis** | Embedded Claude analyst (free) + optional live Anthropic API |
| **Arbitrage** | Cross-exchange arbitrage with 8 institutional filters |
| **Learning Engine** | Learns from closed trades, adjusts confidence per strategy |
| **Dashboard** | Real-time terminal dashboard with live prices and unrealized P&L |
| **Email Reports** | Daily HTML reports + instant halt alerts via Gmail |
| **Paper Trading** | Full paper wallet simulation before going live |

---

## 🏗️ Architecture

```
TradingBot/
├── core/
│   ├── multi_profile_runner.py   # Main engine — 3 profiles simultaneous
│   ├── bot_engine.py             # Single-profile engine
│   ├── strategy_selector.py      # Multi-timeframe signal scanner
│   ├── direct_executor.py        # Signal → position executor
│   ├── risk_manager.py           # Position sizing, circuit breakers
│   ├── learning_engine.py        # Trade history analysis
│   ├── arbitrage_engine.py       # Cross-exchange arbitrage
│   ├── blacklist_manager.py      # Per-profile symbol blacklisting
│   ├── news_scanner.py           # Fear & Greed, trending, headlines
│   ├── report_emailer.py         # HTML email reports
│   └── wallet_replicator.py      # Mirror live balance → paper wallets
├── exchanges/
│   ├── binance_client.py         # Binance spot + futures
│   ├── mexc_client.py            # MEXC spot + swap
│   ├── bybit_client.py           # Bybit Unified Account
│   └── bitget_client.py          # Bitget spot + futures
├── strategies/
│   ├── supertrend_strategy.py
│   ├── multi_tf_strategy.py
│   ├── mean_reversion.py
│   ├── trend_following.py
│   ├── grid_trading.py
│   ├── scalping.py
│   └── dca_strategy.py
├── dashboard.py                  # Live terminal dashboard
├── multi_profile_main.py         # Entry point (recommended)
├── main.py                       # Single-profile entry point
├── config.py                     # All settings in one place
├── TradingBot.bat                # Windows menu launcher
└── .env.example                  # Environment template
```

---

## 🚀 Quick Start

### 1. Clone & Install

```bash
git clone https://github.com/yourusername/trading-bot.git
cd trading-bot

python -m venv venv
# Windows:
venv\Scripts\activate
# macOS/Linux:
source venv/bin/activate

pip install -r requirements.txt
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with your API keys
```

### 3. Run (Paper Trading — safe, no real money)

**Windows** — double-click `TradingBot.bat` for the interactive menu.

**All platforms:**
```bash
# Multi-profile (recommended) — Conservative + Moderate + Aggressive simultaneously
python multi_profile_main.py

# Single-profile
python main.py

# Live dashboard (separate terminal)
python dashboard.py
```

---

## 📋 Risk Profiles

Three isolated profiles run simultaneously, each with its own paper wallet, position tracker, and blacklist:

| Profile | Position Size | Stop Loss | Take Profit | Leverage | Max Positions | Max Drawdown |
|---|---|---|---|---|---|---|
| **Conservative** | 2% | 1.0% | 3.0% | 2× | 4 | 10% |
| **Moderate** | 3% | 1.5% | 4.5% | 3× | 6 | 12% |
| **Aggressive** | 5% | 2.0% | 6.0% | 5× | 8 | 15% |

Each profile independently halts if its circuit breaker trips. Conservative continues trading even if Aggressive halts.

---

## 📊 Multi-Timeframe Strategy Selector

The scanner analyses each symbol across 5 timeframes (1d, 4h, 1h, 15m, 1m) using:

- **ADX** — trend strength
- **EMA crossovers** — direction (9/21/50/200)
- **RSI** — momentum and overbought/oversold
- **Bollinger Bands** — volatility and squeeze
- **Volume ratio** — participation confirmation
- **ATR** — dynamic stop/take-profit sizing

Signals only fire when timeframes **agree** — a 4h+1h+15m consensus is required for the highest-confidence trades.

---

## ⚙️ Configuration

All settings are in `config.py`. Key sections:

```python
# Trading pairs per exchange
TRADING_PAIRS = {
    "binance": {
        "spot":    ["BTC/USDT", "ETH/USDT", ...],
        "futures": ["BTC/USDT:USDT", "ETH/USDT:USDT", ..., "XAU/USDT:USDT"],
    },
    ...
}

# Risk parameters (single-profile)
RISK = {
    "max_position_pct":   0.05,
    "default_stop_loss":  0.025,
    "default_take_profit":0.065,
    "default_leverage":   5,
    ...
}
```

---

## 🔑 API Key Setup

### Binance
1. Go to [API Management](https://www.binance.com/en/my/settings/api-management)
2. Create API key → enable **Reading** + **Spot Trading** + **Futures**
3. Add your IP to the whitelist

### Bybit
1. Go to [API Management](https://www.bybit.com/app/user/api-management)
2. Create key with **Read** + **Trade** permissions
3. Whitelist your IP — Bybit requires this for trading keys

### MEXC
1. Go to [Open API](https://www.mexc.com/user/openapi)
2. Create key with **Trade** permission
> Note: MEXC futures may be geo-restricted in some regions

### Bitget
1. Go to [API Management](https://www.bitget.com/account/newapi)
2. Create key — **Passphrase is required** and cannot be changed later
3. Enable **Read** + **Trade** + **Futures**

---

## 📧 Email Reports

Set `GMAIL_SENDER`, `GMAIL_APP_PASSWORD`, and `GMAIL_RECIPIENT` in `.env`.

Use a [Gmail App Password](https://myaccount.google.com/apppasswords) — not your main password.

Reports include:
- All 3 profile P&L + win rate
- Open/closed positions
- Fear & Greed index
- Top performing strategies
- Circuit breaker status

---

## 🤖 Claude AI Integration

The bot includes an **embedded Claude analyst** that works with zero API cost. It analyses market data and adjusts trade confidence without calling any external API.

To enable the **live Anthropic API** (higher quality analysis):
1. Get a key at [console.anthropic.com](https://console.anthropic.com)
2. Add credits
3. Uncomment `ANTHROPIC_API_KEY=...` in `.env`

---

## 🛡️ Safety Features

- **DRY_RUN mode** — full simulation, zero real orders
- **Circuit breakers** — halt on max drawdown or daily loss limit
- **Per-profile blacklist** — symbols with 3 consecutive SLs are auto-blocked for 24h
- **R:R enforcement** — minimum 1.5:1 risk/reward required to open
- **Minimum notional** — trades below $2 (paper) / $10 (live) are skipped
- **Spot SHORT guard** — impossible shorts on spot markets are silently blocked
- **Duplicate position guard** — same symbol+side+market cannot be opened twice

---

## 📈 Going Live

The bot defaults to `DRY_RUN=true`. To switch to live trading:

1. Run in DRY_RUN for at least a week — understand the risk profile
2. Use Option **[L]** in the menu to mirror your real balance into paper wallets
3. Review the learning report — ensure WR ≥ 55% on at least 30+ trades
4. Use Option **[A]** or set `DRY_RUN=false` in `.env`
5. **Restart the bot** — the mode change takes effect on next start

---

## 🗂️ Data Files

All runtime data is stored in `data/` (git-ignored):

```
data/
├── positions.json                    # Single-bot positions
├── profiles/
│   ├── conservative/
│   │   ├── positions.json            # Isolated position tracker
│   │   ├── wallet.json               # Paper wallet balance
│   │   └── blacklist.json            # Per-profile blacklist
│   ├── moderate/  ...
│   └── aggressive/ ...
├── comparison.json                   # Profile ranking + stats
├── knowledge_model.json              # Learned strategy scores
├── learning_report.json              # Learning engine analysis
├── news_cache.json                   # Fear & Greed + headlines
└── arbitrage/opportunities.json      # Last arb scan
```

---

## 🧪 Backtesting

```bash
# Single exchange
python backtest.py --strategy multitf --symbol BTC/USDT --exchange binance --days 60

# All exchanges
python backtest.py --strategy supertrend --symbol ETH/USDT --all-exchanges --days 90

# Arbitrage backtest
python backtest.py --strategy arbitrage --symbol BTC/USDT --days 30
```

Available strategies: `supertrend`, `meanreversion`, `multitf`, `trend`, `grid`, `scalping`

---

## 🤝 Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).

Pull requests are welcome. For major changes, please open an issue first.

---

## 📜 License

MIT — see [LICENSE](LICENSE).

**This is not financial advice. Use at your own risk.**
