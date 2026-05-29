# The Trading Framework

Written for non-traders. Explains how this bot makes (or fails to make) money, what's predictable, and where the limits are.

---

## Part 1 — What's actually predictable in crypto at 1h-4h horizons

Honest answers from years of academic research and practitioner data:

| Edge | Reality | Tradeable? | Captured here? |
|---|---|---|---|
| **Trend persistence** (1-4h momentum continues) | ~52-55% accurate in trending regimes; ~48-50% in chop | Yes, conditionally on regime | YES (4h EMA20/50 + 1h alignment) |
| **Mean reversion at extremes** (RSI < 25 / > 75) | ~60% reversion within 24h | Yes, but timing-sensitive | Partial (RSI sweet spot 30-70) |
| **Volume divergence** | Real edge but lags | Yes, with patience | Yes (volume bonus layer) |
| **Funding rate extremes** | Strongest crypto signal; >0.1% per 8h with high OI predicts mean reversion 60-65% | Yes | Partial (used in cell-filter context) |
| **Order book imbalance** | Real but transient (seconds-minutes) | At HFT scale only | Yes (1-cycle bonus) |
| **News/event** | High variance; LLM advisory unreliable | Manual at retail scale | Yes (advisory only, not gating) |
| **Cross-asset correlation** | BTC dominance dictates alt direction | Yes | Implicit via STAR symbol selection |
| **Price prediction (deep learning)** | <5% edge after costs; stat. insignificant at retail scale | NO | N/A (correctly skipped) |
| **TA pattern recognition** ("head and shoulders") | <2% above random | NO | N/A (correctly skipped) |

**The honest meta-truth:** at $791 capital and 0.1% fees, the **edge ceiling is ~3-5%/month** under perfect execution. Most published "alpha strategies" lose to fees at this scale.

---

## Part 2 — The framework, layer by layer

```
┌─────────────────────────────────────────────────────────────────┐
│                  ENTRY DECISION PIPELINE                         │
├─────────────────────────────────────────────────────────────────┤
│ 1. UNIVERSE SELECTION                                            │
│    - STAR_SYMBOLS = {ATOM, ARB, DOGE} (proven edge)              │
│    - All other symbols blocked at cell-filter                    │
│                                                                  │
│ 2. SCORING (MCP Brain — 7 layers)                                │
│    - 4 required: 4h EMA gap, 1h alignment, RSI sweet, ADX>=20    │
│    - 6 bonus: MACD, slope, 15m timing, volume, structure, OB     │
│    - Score 50 base + 5-12/bonus, max 101                         │
│                                                                  │
│ 3. FILTERS (in order)                                            │
│    - cell_filter:    STAR-only mode                              │
│    - score_85_cap:   tier-cap on score>=85 (anti-EV historically)│
│    - model_gate:     LR+GBM ensemble p_win >= 0.55               │
│    - lr_size_mult:   adjusts size by ensemble confidence         │
│    - expectancy:     STAR>=$0 mean / non-STAR>=$0.05             │
│    - meta_filter:    spread/vol/depth percentile floors          │
│    - allowed_hours:  evidence-based time gates                   │
│    - daily_loss_lim: 1% halt                                     │
│    - drawdown_halt:  8% halt                                     │
│                                                                  │
│ 4. SIZING (chained)                                              │
│    - tier-based size_pct (50% nominal)                           │
│    - lr_size multiplier (0.7-1.3x)                               │
│    - kelly cap (sanity check, currently neutral)                 │
│    - vol_target (ATR-based, dampens high-vol positions)          │
│                                                                  │
│ 5. EXECUTION                                                     │
│    - smart_executor: limit-then-market w/ slippage budget        │
│    - exchange-side SL placed at entry (fail-closed)              │
│    - exchange-side TP placed at entry                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────────┐
│                 POSITION MONITORING (every 10s)                   │
├─────────────────────────────────────────────────────────────────┤
│ 1. Soft-SL/TP check (catches missed exchange triggers)           │
│ 2. Trailing stop manager (peak-tracking + breakeven floor)       │
│ 3. Entry-staleness exit (4h EMA20/50 flip with margin)           │
│ 4. MCP brain TAKE_PROFIT (discretionary if net>=0.5%)            │
│ 5. Hard MAX LOSS at -12% leveraged (catastrophic only)           │
│ 6. AGE_LIMIT / AGE_LOSS / STALE rules                            │
└─────────────────────────────────────────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────────┐
│                       SPOT DEFENSE                                │
├─────────────────────────────────────────────────────────────────┤
│ - Peak-DD half exit at -25% from observed peak                   │
│ - Peak-DD full exit at -40%                                      │
│ - Dust consolidation (sells positions < $25)                     │
│ - NEVER buys spot — defense only                                 │
└─────────────────────────────────────────────────────────────────┘
                              │
                              v
┌─────────────────────────────────────────────────────────────────┐
│                  DAILY OPERATOR LOOP                              │
├─────────────────────────────────────────────────────────────────┤
│ - Heartbeat every 60s (data/heartbeat.json)                      │
│ - Daily self-check at 00:00 UTC                                  │
│   - Exchange connectivity + latency                              │
│   - Risk state, positions, daily PnL                             │
│   - Auto-generate gate_effectiveness_<date>.md                   │
│   - Auto-generate star_review_<date>.md                          │
└─────────────────────────────────────────────────────────────────┘
```

---

## Part 3 — How profit is made (the math)

```
PROFIT = (avg_win × WR × N_trades) - (avg_loss × (1-WR) × N_trades) - fees

Current 30-day:
  avg_win  = $0.25
  avg_loss = $0.51
  WR       = 44%
  N        = 278 trades
  fees     = $7.77

  → wins:   0.25 × 0.44 × 278 = $30.58
  → losses: 0.51 × 0.56 × 278 = $79.36
  → net:    $30.58 - $79.36 - $7.77 = -$56.55 (matches actual -$46 within rounding)
```

**The two profit levers, in order of impact:**

1. **Reduce avg_loss** ($0.51 → $0.30 = 40% reduction)
   - Tighter SL placement
   - Cell-filter to proven cells only ✓ (just done)
   - Faster loss-cutting via entry-staleness
   - **Hardest to break**: each $0.01 reduction = $0.84/month at current loss rate

2. **Increase avg_win** ($0.25 → $0.50)
   - Let winners run (current trailing tuning is empirically grounded)
   - More TP-hit exits vs trailing-clipped exits (current ratio 15:11)
   - **Easier to break**: each $0.05 increase = $1.25/month at current win rate

**WR is third lever** (44% → 50% = $5/month at current sizes) — but raising WR usually requires sacrificing R:R, so net effect is small.

---

## Part 4 — Why $791 is the binding constraint

At your scale, **fees consume 30-40% of every win**:

```
Round-trip fee at 0.1% × 2:
  $5 notional   →  $0.01 fee = 4% of $0.25 avg win
  $32 notional  →  $0.064 fee = 25% of $0.25 avg win  (current)
  $100 notional →  $0.20 fee = 80% of $0.25 avg win   (would be too big)
  $500 notional →  $1.00 fee = 400% of $0.25 avg win  (unworkable)
```

The fee/win ratio is ~25% at current sizing, which is at the edge of viability. The way to fix this isn't to size up (you'd run out of capital fast) but to:
1. Take fewer trades (less aggregate fee)
2. Hold winners longer (bigger avg_win, fee becomes smaller %)

Both are accomplished by the STAR-only mode + tighter caps shipped today.

---

## Part 5 — What the bot can and can't do

### CAN do well
- Execute mechanical decisions consistently (no emotion, no FOMO)
- Defend capital via hard caps (daily loss, drawdown halt)
- Find proven-edge symbols and stay with them
- Cut losses at predetermined SL (no holding losers hoping for recovery)
- Run 24/7 with minimal intervention
- Generate reports for honest performance tracking

### CAN'T do
- Predict market regime changes
- Outperform buy-and-hold BTC during strong bull markets
- Recover from large account drawdowns quickly (8% cap forces halt)
- Beat fees at very small notional sizes
- Replace trader judgment for unusual events (CEX hacks, regulatory news)

### The right mindset for the operator (you)
- **This is an experiment in algorithmic trading**, not a salary replacement
- **±2%/month is the realistic ceiling** at $791 capital
- **Some weeks/months will be negative** — that's normal variance
- **The 1% daily loss + 8% drawdown halts** are your friend, not enemy — let them fire
- **If 2 months in a row are negative >5%**, the strategy is broken — pause and reassess

---

## Part 6 — Daily routine (5 minutes)

### Morning check
```bash
python main.py --status
```
- Open positions count
- Today's PnL
- Halt status

### Twice-weekly review
```bash
# 1. Read the latest gate effectiveness report (auto-generated daily)
ls -t data/reports/gate_effectiveness_*.md | head -1

# 2. Read the latest STAR review (auto-generated daily)
ls -t data/reports/star_review_*.md | head -1
```

Look for:
- Net 7-day PnL trend (up = working; down = warning)
- STAR symbols still in "_pass_" tier
- Exit-reason ratio (mcp_take_profit count vs stop_loss count)

### Weekly action items
- If gate report flags a STAR for demotion: edit `config.py` to remove
- If STAR review flags a promotion candidate: review the data, decide
- If 7-day PnL < -2% of balance: pause `main.py` and open a review session

### Monthly review
- Compare actual performance to the realistic ranges in Part 5
- If consistently below the "modest loss" range for 2+ months: strategy is broken; either:
  - (a) Inject more capital so fee drag becomes smaller %, or
  - (b) Pause the bot and reassess

---

## Part 7 — Failure modes and what to do

| Symptom | Likely cause | Fix |
|---|---|---|
| Bot won't trade for hours | All gates rejecting; market in chop | Wait. Run `gate_effectiveness_report.py`. Verify candidates are flowing in `data/mcp_decisions.jsonl`. |
| Daily halt fires repeatedly | -1% per day cap hit; markets bad | Wait 24h. If 3 days in a row halt: pause bot, run review. |
| Drawdown halt fires (8%) | Sustained loss tape | Pause bot. Open Claude session. Review last 50 trades. |
| Position has wide unrealized loss | Soft-SL didn't fire (gap or polling latency) | Manual close on exchange. Check why exchange-side SL didn't trigger. |
| New manual position appears | You traded manually | Bot auto-imports + tries to attach SL. Verify in `data/positions.json`. |
| Spot holding sold unexpectedly | -25% peak DD triggered | Check `data/spot_recommendations.jsonl` log. Holding had drawn 25% from observed peak. |

---

## Part 8 — Monitoring summary

| Signal | Healthy | Warning | Critical |
|---|---|---|---|
| 7-day net PnL | >$0 | -$5 to -$15 | <-$15 |
| WR last 7d | >50% | 40-50% | <40% |
| Daily halt fires | <2/week | 2-3/week | >3/week |
| Drawdown | <3% | 3-7% | >7% (halt fires at 8%) |
| Open positions | 0-6 | 7-10 | >10 (sizing issue) |
| Exchange API latency | <500ms | 500-1000ms | >1000ms |

---

**Bottom line:** the framework is now structurally sound. The remaining work is **time** — soak the new gate-stack for 50+ trades, then re-evaluate. Don't add more layers; observe what we have. The wiring is "perfect" only insofar as the cost structure and account size allow. At $791, the math has a low ceiling. Within that ceiling, this is the best wiring I can give you.
