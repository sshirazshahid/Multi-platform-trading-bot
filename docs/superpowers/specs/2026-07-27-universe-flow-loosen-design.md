# Universe Flow Loosen V1 — Design (plain English)

*Date: 2026-07-27 | Owner posture: “make the bot work; I’m new to trading”*

## What this is

Your bot **already runs in PAPER** (fake money). It was rejecting most trade ideas on purpose — not broken.

We keep the **accuracy** protections that stop trading in bad market regimes. We only **slightly loosen** the “is this coin liquid/tradable?” check for **7 days**, then a script tells us KEEP or REVERT.

## Decisions (locked)

| Choice | Decision |
|--------|----------|
| Band regime filter (quiet BTC / strong ADX) | **STAYS ON** — protects win-rate |
| Economic gate (trade must clear fees) | **STAYS STRICT** — no change until a later evidence screen |
| Universe filter (spread/depth/chop) | **Mild temporary loosen** for 7 days |
| After 7 days | Auto-review → KEEP or REVERT |

## Why not “trade everything / highest accuracy forever”

Measured research on this bot: chasing more trades without quality gates usually **loses money after fees**. “Highest accuracy” here means **keep regime + economic safety**, then carefully admit a few more liquid names.

## Phase 1 (shipping now)

1. Flag `UNIVERSE_FLOW_LOOSEN_V1=true` in `.env`
2. `UniverseFilter` uses slightly looser spread / depth / range / chop floors
3. Cohort file `data/universe_flow_loosen_cohort.json` records start time
4. Boot banner shows `UniverseLoosen: ON`
5. After 7 days: `scripts/review_universe_flow_loosen.py` compares fills vs prior week and prints KEEP/REVERT

### Loosen knobs (vs baseline)

| Knob | Baseline | Loosen V1 |
|------|----------|-----------|
| Max spread | 0.50% | 0.75% |
| Min book depth | $2000 | $1200 |
| Min 10d range | 2.0% | 1.5% |
| Min trend efficiency (chop) | 0.20 | 0.12 |

## Phase 2 (later, only if still starved)

After-cost screen on economic-gate stress / TP geometry. Apply only if **EV > 0 and profit factor > 1**. Never admit `breakeven ≥ 1` trades.

## Out of scope

- Live (real money) trading
- Turning off band regime filter
- Fake win-rate by lowering the score floor
- Shadow probes placing PAPER book orders (they stay log-only)

## Success

- More PAPER opens that still pass band + economic gates
- Win-rate does not collapse vs prior week
- Clear KEEP/REVERT after day 7
