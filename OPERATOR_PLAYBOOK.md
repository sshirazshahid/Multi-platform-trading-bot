# Operator Playbook (for non-traders)

**For:** running this bot when you're not a trader/analyst.
**Date:** 2026-05-01.
**Account:** ~$791 across Binance / Bybit / Bitget.

---

## What this bot will and won't do

**Will:**
- Trade any symbol where MCP score (≥65) + model gate (p_win ≥ 0.55) + meta-filter all agree
- Self-correct via expectancy filter: blocks symbols with proven negative recent mean
- Cap any single day's loss at 1% of balance (~$7.91)
- Halt all trading if total drawdown exceeds 8% (~$63 from peak)
- Defend spot holdings via peak-drawdown selling (-25% half / -40% full exit)
- Place exchange-side stop-losses on new entries

**Won't (be honest with yourself):**
- Make you rich
- Generate consistent alpha — at $791 capital with 0.1% fees, the realistic ceiling is ±2%/month
- Run forever without supervision — you need to check on it
- Predict the market — it follows rules, not crystal balls

---

## Daily routine (5 minutes)

### Morning (or whenever you check)

```bash
python main.py --status
```

Look for:
- `Open Positions` count — should be 0–6
- `Total PnL` direction — is the trend up or down over multiple days?

### Generate the diagnostic report

```bash
python scripts/gate_effectiveness_report.py --window 7
```

The report saves to `data/reports/gate_effectiveness_<date>.md`. Open it and look at:

1. **Top-line PnL** — is the 7-day net positive?
2. **Per-symbol section** — are STAR symbols (ATOM, ARB, DOGE) still in the "_pass_" tier? If any flipped to "BLOCK", the auto-filter handles it.
3. **Exit-reason section** — what's the ratio of `mcp_take_profit` (profitable) to `stop_loss` (losers)?

**Healthy signs:**
- mcp_take_profit appears multiple times with positive avg
- stop_loss count is < 1.5× the take_profit count
- Net 7-day PnL is non-negative (some weeks will be slightly negative — that's normal)

**Warning signs (act on these):**
- 3+ consecutive days of negative PnL
- stop_loss count > 2× take_profit count
- Total 7-day PnL < −2% of balance ($16+)

---

## When to act

### Bot has halted (yellow flag)

The bot logs say `consec_global_losses=5` or `drawdown=8%` triggered halt:

1. Read the gate report — what's gone wrong?
2. If it's normal market chop: **wait it out**. Halt auto-resumes after 4h cooldown.
3. If a STAR symbol flipped to losing: re-run report tomorrow — expectancy filter auto-blocks it.

### Bot keeps halting daily (red flag)

If halts trigger more than twice per week:

```bash
# Tighten further — trade only ATOM (most reliable STAR)
# Edit config.py: STAR_SYMBOLS = {"ATOM/USDT:USDT"}
```

### A position is going badly and bot isn't closing (red flag)

Open the exchange UI directly (Binance/Bybit/Bitget web). Manually close. The bot's monitor cycle should catch it within 2–3 minutes, but if you're worried, manual close is always faster than soft-SL.

### Net P/L is consistently down for 2+ weeks

The bot is structurally not profitable at this market regime + your account size. Options:

1. **Pause it.** Stop `main.py`. Your money sits in USDT, no further bleed.
2. **Drop to ATOM-only**, the most consistent STAR.
3. **Switch to spot-only mode**: edit config to disable futures entry, bot becomes purely defensive on spot holdings.

---

## What "successful" looks like

At $791 capital, **realistic** outcomes per month:

| Outcome | Likelihood | Net |
|---|---|---|
| **Best case** | 20% | +$15 to +$25 (+2–3%) |
| **Modest profit** | 30% | +$5 to +$15 (+0.5–2%) |
| **Breakeven-ish** | 30% | −$10 to +$5 |
| **Small loss** | 15% | −$10 to −$25 (−1–3%) |
| **Bad month** | 5% | −$25 to −$60 (−3–8%, halt fires) |

**Translation:** half the months will likely be flat-to-positive, half will be flat-to-negative, with occasional bigger swings. If you cannot tolerate the bad-month scenario, **don't run the bot**.

---

## What I changed today (2026-05-01) and why

1. **Cleared HALTED + drawdown state.** Bot was paused from a 17.2% drawdown that's now ancient history. Started fresh.

2. **Cell-filter → DISABLED (UNBLOCK_ALL stance restored).**
   Reasoning: the historical "OTHER cell -$51.62" loss tape was generated
   under OLD config (no model gate, no expectancy filter, no tighter caps).
   With current config, those gates evaluate every trade per-symbol —
   layering an additional symbol-universe block on top contradicts the
   bot's own scoring engine. The expectancy filter is the data-driven
   self-correction; cell-filter was an a-priori veto.
   - Trade any symbol where MCP score + model gate + meta-filter agree
   - Expectancy filter still blocks proven-negative symbols (n>=5, mean<floor)
   - Capital safety nets (1% daily, 8% drawdown) catch when engine is wrong
   - Restore by setting `CELL_FILTER.enabled=True` in config.py

3. **Daily loss limit 1.5% → 1.0%.** Tighter circuit breaker. At $791, that's $7.91/day max loss before same-day halt.

4. **Max drawdown 12% → 8%.** Capital-preservation tightening. Combined with the daily limit, the bot has at most ~8 bad days before forced halt.

5. **SPOT defense flipped to live.** Was logging-only since April. Now actively sells spot holdings on −25%/−40% drawdown.

6. **Backfilled 48 phantom warehouse rows.** Data integrity fix; doesn't change trading directly.

7. **Registered exchange-side SL on 2 of 6 manual positions** (Bitget XRP + DOGE). 3 positions remain on bot's soft-SL only.

8. **Added startup gate health check.** Surfaces silent-mode flags in one log line at every start.

---

## Files you might need

| File | Purpose |
|---|---|
| `data/reports/gate_effectiveness_*.md` | Weekly/daily diagnostic reports |
| `data/risk_state.json` | Current halt/drawdown state (don't edit unless directed) |
| `data/positions.json` | Bot's view of open positions |
| `data/exchange_positions.json` | Exchange's actual state (snapshot) |
| `OPERATOR_PLAYBOOK.md` | This file |

---

## When to ask for help

Open a fresh Claude Code session and paste this:

> "Read OPERATOR_PLAYBOOK.md. The bot has [problem]. Latest gate effectiveness report is at data/reports/gate_effectiveness_<date>.md. Help me decide: keep running, tighten further, or pause."

Don't try to read warehouse SQL or modify configs without backup. The state files are auto-saved every cycle, but a bad edit can confuse the bot.

---

**Bottom line:** This bot will not turn $791 into $7,910. The realistic range is ±2%/month. Use this as a controlled experiment in algorithmic trading, not a wealth-generation tool. Capital preservation is the primary goal at this scale.
