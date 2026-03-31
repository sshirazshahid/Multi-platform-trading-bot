# Setup Guide

## Prerequisites

- Python 3.10 or higher
- At least one exchange account with API keys
- (Optional) Gmail account for email reports
- (Optional) Anthropic API key for live Claude AI analysis

---

## Installation

### 1. Clone

```bash
git clone https://github.com/yourusername/trading-bot.git
cd trading-bot
```

### 2. Virtual Environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure

```bash
cp .env.example .env
```

Open `.env` in any text editor and fill in:

- At least one exchange's API key + secret
- (Optional) Gmail credentials for email reports
- Keep `DRY_RUN=true` until you are confident

---

## First Run

### Windows (recommended)

Double-click `TradingBot.bat`. On first run, the setup wizard will guide you through configuration.

### All Platforms

```bash
# Multi-profile learning (recommended)
python multi_profile_main.py

# Open dashboard in a second terminal
python dashboard.py
```

---

## Verifying Exchange Connections

On startup you will see:

```
[Engine] Connected: ['binance', 'mexc', 'bybit', 'bitget']
[Engine] BINANCE unified: 142.8500 USDT
[Engine] MEXC spot: 0.0000 USDT
...
```

If an exchange shows `Authentication failed` or is missing from the connected list:
1. Double-check API key and secret in `.env`
2. Ensure the key has **Read** permission enabled
3. Check if your IP is whitelisted on the exchange
4. For Bybit: confirm the key is for the **Unified Trading Account**
5. For Bitget: confirm the passphrase is correct (it cannot be changed after creation)

---

## Paper Trading vs Live Trading

| Setting | Effect |
|---|---|
| `DRY_RUN=true` | All trades are simulated. No real orders placed. |
| `DRY_RUN=false` | Real orders placed on the exchanges. |

**Start with `DRY_RUN=true`.** Switch to live only after:
- Running paper trading for 1–2 weeks
- Reviewing the learning report (`data/learning_report.html`)
- Achieving ≥ 55% win rate on ≥ 30 closed paper trades
- Understanding the risk parameters for each profile

To switch to live: use **Option [A]** in `TradingBot.bat`, or set `DRY_RUN=false` in `.env` and restart.

---

## Replicating Your Real Balance

Use **Option [L]** in the menu to scan your live exchange balances and copy them into all three paper wallets. This makes DRY_RUN simulate with your actual capital, giving more realistic P&L figures.

---

## Updating

```bash
git pull origin main
pip install -r requirements.txt --upgrade
```

Your `.env` and `data/` files are git-ignored and will not be affected by updates.
