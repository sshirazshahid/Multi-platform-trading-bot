---
name: crypto-trade-journal
description: Create structured post-trade reviews for crypto spot and futures trades. Use this whenever the user wants to log, journal, review, or analyze a crypto trade they've just taken (or an old one), whether they paste trade details, upload a CSV export from Binance/Bitget/Bybit, or share chart screenshots. Also use when the user says things like "review this trade", "journal my trades", "did I follow my plan", "what went wrong with this trade", or shares chart screenshots with trade context. Handles SMC/ICT/price action/volume profile setups. Produces both a human-readable markdown review and a structured JSON row appended to a running log for later analysis.
---

# Crypto Trade Journal

This skill turns raw trade data (text, CSV exports, or chart screenshots) into a structured, reflective post-trade review — the kind of journaling that actually compounds into edge over time, not just "I entered here, exited here."

The skill produces **two outputs per trade**:
1. A markdown review file (human-readable, the template below)
2. A JSON row appended to a running log (`trades_log.json`) so trades can be queried later ("my win rate on 4H FVG fills in uptrending BTC")

## Why this skill exists

Most traders either don't journal or journal the wrong things. They write "long BTC at 68k, stop at 67k, TP at 70k, WIN +$400" and call it done. That record doesn't help them next month. What helps is capturing the **context** the trade lived in (market regime, session, funding, catalysts), the **process** that led to it (setup, thesis, conviction, confluence), the **execution** reality (deviations from plan, slippage, stop management), and the **psychology** (emotional state at entry/during/exit, rule-following, streak effects). That's what this template captures.

The user is a crypto trader working across spot and perps on Binance, Bitget, and Bybit. Their stylistic preference leans toward SMC/ICT concepts, volume profile, and price action — so when describing setups from screenshots, use that vocabulary accurately. `references/methodologies.md` has the definitions — read it before analyzing any chart screenshot.

## Workflow

### 1. Figure out what you have

The user might give you:
- **Pasted text** describing the trade (e.g., "I longed ETH at 3240 on Bybit with 5x, closed at 3408")
- **A CSV/XLSX export** from an exchange (see `references/exchange-formats.md` for format notes on Binance/Bitget/Bybit)
- **Chart screenshots** (pre-entry setup, entry, exit)
- **Some combination** of the above

Read what's provided. If critical fields are missing (entry price, exit price, size, direction), ask — but ask for them all at once, not one at a time. Don't invent numbers.

### 2. For chart screenshots: describe what you see, accurately

When the user provides chart screenshots, look at them and describe the setup using the correct methodology vocabulary. Don't say "the price bounced off support" if what you're looking at is a bullish order block with a liquidity sweep below. Read `references/methodologies.md` first so you're using the right terms.

Things worth noting from a chart screenshot:
- Market structure state (BOS, CHoCH, trending, ranging)
- Relevant levels (swing highs/lows, equal highs/lows indicating liquidity pools)
- Imbalances/FVGs visible
- Order blocks or supply/demand zones
- Volume profile nodes if visible (POC, VAH, VAL)
- Fibonacci confluence if applicable
- The actual entry/exit marks

Be honest about what you can and can't tell. If you can't see the timeframe, say so and ask. If the screenshot shows the trade after the fact (outcome visible), your "post-entry analysis" should reflect that — don't pretend to be predicting forward.

### 3. Compute the numbers

Use `scripts/compute_pnl.py` for P&L math. It handles:
- Spot vs perp vs futures
- Leverage, margin, notional
- Fees (maker/taker per exchange)
- Funding for perps held across funding windows
- R-multiple (given planned risk)
- Holding period
- Scaled entries/exits (pass a list of fills)

Don't eyeball the math — leverage + fees + funding is easy to get wrong. Use the script.

### 4. Write the review using the template

The canonical template is in `references/template.md`. Use it exactly — the structure matters because the running JSON log needs consistent fields for later analysis. Every trade review has these sections in this order:

1. Trade Summary
2. Market Context
3. Setup & Thesis
4. Execution
5. Discipline & Psychology
6. Post-trade Review
7. Charts

Fields marked optional can be left blank or omitted when the user didn't supply the info. **Do not fabricate context the user didn't give you** (e.g., don't invent "funding rate was 0.01%" if you don't know it — leave it blank or ask).

#### Getting the tone right

The review is for the user's future self, not for an audience. Write it directly, second-person or plain statement form. Avoid hype language ("crushed this trade") and avoid moralizing ("you shouldn't have chased the entry"). The user can spot their own mistakes in the raw facts; your job is to record clearly, not to coach.

Exception: in the "Lessons" and "What went wrong" fields, it's appropriate to state observations plainly ("entered 0.3% above the retest level — chased"). State, don't lecture.

### 5. Also produce the structured JSON row

Every review also produces a JSON row appended to `trades_log.json` in the output directory. Schema is in `assets/log_schema.json`. Use the exact field names — consistency is what makes later queries possible. If the user later asks "how do my FVG trades do?", the skill needs to be able to filter on `setup_pattern == "FVG fill"`.

### 6. Save outputs

Save both files to the user's output directory:
- `{YYYY-MM-DD}_{TICKER}_{DIRECTION}.md` (the review)
- `trades_log.json` (append to existing, or create if new)

Use `present_files` to show both to the user at the end.

## What this skill is NOT

- **Not a predictor.** Don't tell the user whether future trades will work. Don't extrapolate from one trade.
- **Not a coach.** The review is a record. Observations are fine; directives ("you should never...") are not — the user is capable of drawing their own conclusions from accurate records.
- **Not a screener or scanner.** This skill reviews trades the user has already taken. Separate skills handle forward-looking research.
- **Not an exchange API client.** This skill processes data the user provides; it doesn't fetch live data from exchanges.

## Edge cases worth handling well

- **Scaled entries/exits**: average the fills weighted by size. The `compute_pnl.py` script handles this — pass it a list of fills.
- **Partial still open**: if the user exited 70% but 30% is still open, note "partial — 30% runner open" in the Trade Summary and mark `status: "partial"` in the JSON. Review the closed portion now; update the row when the runner closes.
- **Stop moved to BE mid-trade**: don't treat this as "no stop hit." Record both the original stop and the final stop in Execution. This is meaningful data for the Discipline section.
- **Trade that violated the user's rules but won anyway**: still a rule violation — record it honestly in the Discipline section. The outcome doesn't sanctify the process.
- **Trade with no pre-defined risk**: `R-multiple` field stays blank. Don't invent a planned risk retroactively.
- **User didn't give a setup name**: propose one based on what you see, but mark it with `(proposed)` and let the user correct it.

## Reference files

- `references/template.md` — the canonical markdown template for the review
- `references/methodologies.md` — definitions of SMC, ICT, FVG, order blocks, liquidity, volume profile concepts, price action patterns, Fibonacci usage. Read before analyzing any chart screenshot.
- `references/exchange-formats.md` — column layouts and quirks for Binance, Bitget, and Bybit CSV/XLSX exports

## Scripts

- `scripts/compute_pnl.py` — P&L, ROE, R-multiple, fees, funding calculations for spot and perp trades with optional scaling
