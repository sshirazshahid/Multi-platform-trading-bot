# Setup Guide

## Prerequisites

- Python 3.12 (the pinned pandas/scikit-learn stack is tested on 3.12)
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
- Keep `OPERATING_MODE=PAPER` and `ENTRY_POLICY=SHADOW_ONLY` during setup.
  `OPERATING_MODE` is authoritative; `DRY_RUN` is retained only for legacy tools.

---

## First Run

### Windows (recommended)

Double-click `TradingBot.bat`. It is the canonical Windows launcher and guides
first-time setup. Credential prompts run inside Python with hidden input; API
keys, secrets, passphrases, and app passwords are never placed in process
arguments.

For crash restart behavior, `auto_restart.bat` remains as a compatibility shim
that delegates to `TradingBot.bat --supervise`. The canonical supervisor allows
only one bot worker, starts persistent collectors once, restarts crash loops with
bounded backoff, and stops/restarts its own PAPER worker if the heartbeat stalls.

After a successful PAPER soak, an administrator can register the same fail-closed
supervisor at Windows startup:

```powershell
# Preview first
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_24x7_task.ps1 -WhatIf

# Register; add -StartNow only when PAPER monitoring should begin immediately
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\install_24x7_task.ps1
```

If Windows reports `Access is denied`, open PowerShell with **Run as
Administrator** and run the same command. The installer does not weaken the
task to live mode or silently install a less durable logon-only fallback.

The task installer refuses `CONTROLLED_LIVE`. Configure Windows not to sleep,
keep clock synchronization healthy, and use wired networking/UPS power for a
real 24x7 host.

### All Platforms

```bash
# Canonical PAPER/OBSERVATION worker
python scripts/launcher_supervisor.py run --restart

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

## Operating Modes

| Setting | Effect |
|---|---|
| `OPERATING_MODE=PAPER` | All trades are simulated. No real orders are placed. |
| `OPERATING_MODE=OBSERVATION` | Data collection only. No paper or real orders are placed. |
| `OPERATING_MODE=CONTROLLED_LIVE` | Real-order mode, gated outside the Windows launcher. |

**Start with `OPERATING_MODE=PAPER`.** Consider controlled live only after:

- 30–60 days of fully matured, event-deduplicated shadow evidence
- at least 100 independent resolved setups (prefer 500 for intraday candidates)
- purged walk-forward symbol/time/venue holdouts plus an untouched final holdout
- positive lower-bound expectancy after 1x/1.5x/2x costs, PF ≥ 1.20, and stable parameters
- valid model/strategy manifests, zero execution mismatches, and every startup gate passing
- explicit manual approval of the tiny controlled-live risk profile

Win rate alone is not a promotion criterion, and no result guarantees future profit.

`TradingBot.bat` intentionally cannot activate or start `CONTROLLED_LIVE`.
Option **[A]** switches only between `PAPER` and `OBSERVATION`. Controlled-live
operation requires the signed checklist, both environment latches, and the
audited direct launch procedure described in
[`CONTROLLED_LIVE_CHECKLIST.md`](CONTROLLED_LIVE_CHECKLIST.md).

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
