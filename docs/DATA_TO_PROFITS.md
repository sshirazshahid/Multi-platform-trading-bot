# How This Bot's Data Turns Into Profits — The Honest Playbook

*2026-07-10. Written from the evidence ledger, not from hope. Sources: `.claude/skills/refuted-families-ledger/SKILL.md`, warehouse (`data/warehouse.sqlite`), shadow-lane reports, journal goal-progress sections.*

## The one-sentence answer

The data says profits come from **carry (F1), a single validated event edge (listing-short, still in shadow), and cost reduction** — and explicitly **not** from directional price prediction, which this system has refuted ~2,400 times on its own 3-year multi-venue data.

## The data assets

| Asset | Where | What it's for |
|---|---|---|
| Trade + candidate warehouse (append-only, lifecycle rows, decision_id provenance) | `data/warehouse.sqlite` | Every entry/exit/SL move; the substrate for every gate below |
| Real per-trade MFE/MAE extremes | warehouse (2026-07 addition) | Honest SL/TP geometry (DistFitSL fits real extremes, not assumptions) |
| Shadow lanes (mcp / tp-probe / listing-short) | `shadow_runner` tables, `trading_bot_shadow_vs_live` MCP tool | Log-only counterfactuals; the promotion evidence |
| Funding-rate history, 3 venues, 137 venue-symbol series | `data/funding_history/` | Carry family screens (the only validated family) |
| Per-source WR / performance summaries | warehouse queries, learning-engine reports | Tells you which decision source is bleeding (spoiler: directional) |
| TV/derivs forward harvests | `data/tv_history.jsonl`, `data/derivs_history.jsonl` | Point-in-time regime data (repaint-proof), screen inputs only |

## Lever (a): F1 delta-neutral funding carry — VALIDATED

Cross-venue delta-neutral funding carry, 15 coins, maker-first. The only strategy family that survived pre-registered gates, with independent external support (peer-reviewed 2025 study: ≤115.9%/6mo across 60 scenarios, max loss 1.92%, zero HODL correlation).

**Status:** PAPER soak (runner shipped 2026-07-02; 0 forward cycles resolved as of today's journal).
**The capital constraint is the profit constraint:** carry is margin-hungry — both legs must be funded on separate venues, and per-coin capacity at ~$420 account size makes the honest ceiling small in dollar terms. Carry scales with capital, not with cleverness. Do not lever it up to force yield.

## Lever (b): listing-short shadow probe — the promotion path

Post-listing perp short: signal itself robust (WR 0.75–0.81, DSR≈1, PBO 0.09, OOS-WR 0.82–0.88 on 88 funding-charged Binance listings), but full-stake sizing failed the maxDD gate. The **capital-scaled variant (3% per-trade / 12% exposure cap) earned CONFIRMED_GO** (rev3) and is live as an **unlevered, log-only shadow probe** (`ListingShortProbeAgent`, registered in today's bot log).

**Promotion path — frozen, no shortcuts:** ≥30 resolved probe outcomes, then the frozen gate (DSR ≥ 0.10, PBO ≤ 0.5, OOS-WR ≥ 0.55, AUC ≥ 0.60) plus owner sign-off. Today: 0/30 resolved. The probe stays unlevered even after promotion — 3× leverage breaches the drawdown gate.

## Lever (c): cost reduction — real but ceiling-capped

Maker-first fills, venue fee tiers, and slippage discipline are worth real basis points (maker B-lite shipped 2026-06-11). But the measured ceiling is honest and hard: **cost reduction alone gets the directional book to ~breakeven, not to profit** (TP-accuracy diagnosis 2026-06-04; exec-cost audit 2026-05-29: costs are a minority of the loss). Cost work is worth doing only on top of a lane that has edge — it cannot manufacture one. (Note: the two internal cost-split tools disagree ~10×; don't quote either split as fact.)

## What the data says does NOT work

- **Directional price prediction, period.** ~2,400+ pattern tests with pre-registered gates on own data: candlesticks (1,989 tests, 0 survivors), RSI mean-reversion, textbook trend/breakout (0/40 OOS), confluence stacks, Kalman pairs (435 pairs, 0 FDR survivors), formulaic alphas (443+), ML forecasters (Kronos: −EV both directions), seasonality (0 survive OOS), ETF-flow/dominance timing, scalping.
- **The live mcp directional lane is −EV**: 30-day contrast in today's journal — 1,077 closed trades, WR 29.1%, net −335 USDT. The mcp_score is non-predictive (corr ≈ −0.008). Claude advisory is exactly that — advisory; deterministic rails are what protect capital.
- **Accuracy bands are geometry, not profit**: the TP-probe shadow lane showed ~78% TP-hit is achievable geometrically and still −EV after costs. Hit-rate targets do not create expectancy.

## Operator rules that follow

1. Capital preservation first — the deterministic rails (ATR SL, 3%/12% caps, 2× leverage clamp) are the product until a lever above matures.
2. New strategy ideas go through the ledger, then the evidence pipeline (screen → audit → shadow → frozen gate). Never straight to live.
3. Watch `trading_bot_shadow_vs_live` — promotion happens on the gate, not on a good week.
4. Spend engineering time on: carry capacity/execution, listing-probe resolution counting, and fee/maker improvements — in that order.
