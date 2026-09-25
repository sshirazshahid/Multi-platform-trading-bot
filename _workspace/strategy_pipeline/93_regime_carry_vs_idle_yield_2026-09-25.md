# 93 — Regime arithmetic: F1 carry vs idle stablecoin yield (2026-09-25)

**Type:** planning arithmetic. This is not a screen and not a pre-registration, because it decides no trading rule. No gate, threshold or runtime flag is changed by this note.

**Trigger:** the owner asked for "a 24x7 profitable trading bot" that makes "profitable trades daily/weekly", paper first, then live.

## Pipeline status (Phase 0 → Phase 4)

- **Phase 0.** Latest measured artifacts:
  - `92_diagnosis_why_no_trades.md` (2026-08-22): the directional book is ≈ −0.24R per trade; the bot has no lane with positive EV.
  - `69_f1_carry_idle_measurement_2026-08-12.md`: F1 passed 0 of 162,742 gate checks; the best post-cost episode was +1.0 bps.
  - The refuted-families ledger: ~2,400+ pre-registered tests refuted; F1 is the only validated family.
- **Phase 1 (scout).** No new trading family was proposed. Every directional idea the owner has raised maps to a refuted ledger row.
- **Phases 2–3.** Nothing to screen or integrate. **The outcome is an explicit no-op for trading lanes, which is a valid, honest outcome.**

## Why F1 is idle now: the gate threshold vs. today's funding

The gate is `research/funding_carry_lab.py:f1_entry_gate`. It requires net edge ≥ max(15 bps, 3 × round-trip cost), projected over `DEFAULT_HOLD_SETTLEMENTS=21` using min(current, trailing-21 mean).

Binance round-trip cost from `config/costs.py`:
- Fees: 2 × (10 bps spot taker + 5 bps perp taker) = 30 bps.
- Slippage: 4 × 5 bps (`DEFAULT_SLIP_FRAC`) = 20 bps.
- Total: **≈ 50 bps**.

What the gate therefore needs:
- Net ≥ 150 bps over 21 settlements, so gross ≥ 200 bps.
- That is **≥ 9.5 bps per 8h settlement, sustained for a week — about 104% APR.**

What the market pays today:
- Late-September 2026 median BTC perp funding is **0.594 bps per settlement (≈ 6.5% APR)**, positive on 24 of the 25 largest perps after the Sep 21 squeeze ([CryptoTicker](https://cryptoticker.io/en/bitcoin-open-interest-funding-rate-check/)).
- The gate needs about **16× today's funding**.
- Idle is the correct behaviour. Do not loosen the gate (`69_*`, "What must NOT be done").

## Opportunity cost: stablecoin yield dominates carry in this regime

Assumptions: F1 is unlevered (1×), so capital is about 2 × notional (spot leg plus perp margin). Funding stays at today's 0.594 bps per settlement. Cost is 50 bps per round trip.

| Hold | Gross on notional | Net on notional | Net on capital, annualized |
|---|---|---|---|
| 30 days (90 settlements) | 53.5 bps | +3.5 bps | ≈ 0.2% / yr |
| 90 days (270 settlements) | 160 bps | +110 bps | ≈ 2.2% / yr |

Reference stablecoin supply rate: Aave v3 USDC on Ethereum at **~3.6–3.7% APY** as of mid-September 2026 ([Eco / DefiLlama](https://eco.com/support/en/articles/15182156-usdc-yield-in-2026-where-to-earn-interest-on-usdc)). Binance advertises "up to 6%" on tiered USDT flexible products ([Binance](https://www.binance.com/en/support/announcement/detail/7c272e07830b47fa9378827c5b15de81)); tiered promotional rates apply only to small balances.

For carry turned over quarterly to beat a 3.6% yield, funding must sustain **≳ 0.84 bps per 8h (≈ 9.2% APR)**, and that ignores funding variance, legging risk and exchange risk.

**Conclusion.** In the current regime, the highest risk-adjusted return available to this account's *idle* cash is stablecoin yield, not any trading lane. This is a statement about the regime, not a refutation of F1. F1 keeps its validated status and its frozen gate, and it re-arms automatically when funding rises.

## What this licenses (and what it does not)

- **Licensed:** a PAPER-only *idle-cash yield* accrual that is labelled as **yield, not trading edge**. It is kept in its own ledger (`data/idle_yield.json`) so no trading-lane evidence is contaminated. It fails closed (accrues 0) when the reference rate is unavailable.
- **Licensed:** a plain-English owner scoreboard that reports each lane separately.
- **Not licensed:**
  - any change to F1 gates;
  - any directional-lane change;
  - any live deposit into a savings product. A live yield lane needs its own owner decision covering exchange and platform counterparty risk (the FTX precedent in `docs/owner/TRADING_PLAN.md` §3), plus a CONTROLLED_LIVE checklist entry.
- **Recommendation to the owner, not applied, because `.env` is the owner's file:** set `ENTRY_POLICY=SHADOW_ONLY`. The directional book has 2,547 closed trades measured at no edge, so executing it in PAPER now only adds losses, not information. Its hypotheses keep accruing as log-only shadow decisions.
