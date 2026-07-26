# The Fable Operating Design

*Written 2026-07-10 by Claude Fable 5, at the owner's request: "design and setup how you
(Claude Fable) want to do it." This is that design — in plain language, with every choice
tied to a measurement. It is the operating doctrine for this bot.*

## The one-paragraph design

Money goes only to what survived a trial. Everything else runs in a paper laboratory that
costs nothing and teaches constantly. The machine watches itself, reports honestly once a
day, and never spends the owner's real dollars — on trades **or on AI calls** — without
measured justification. Fable (the model) works at the *design* level: gates, geometry,
audits, promotions. It does not sit inside the per-trade loop, because that was measured
and found worthless at great cost.

## Why the model is NOT in the per-trade loop (decided 2026-07-10)

Measured on 2026-07-10: the per-cycle Claude advisory made **222 API calls costing
$118.50 in one day** (~$3,500/month run-rate) — real money — to advise a **paper** lane
whose signal is measured non-predictive (score/outcome correlation ≈ −0.008) and whose
trades earn nothing real. One day of that spend exceeds 25% of the whole trading account.

So: `CLAUDE_PORTFOLIO_MODE=off`. The deterministic scorer carries the paper lane (slower
flow, zero cost). The model's leverage lives where it measurably pays: designing the
system in sessions like the one that produced this document — the accuracy-band geometry,
the revenge-trade rail fix, the time-exit fix, the test-pollution guard, and the evidence
pipeline were all one day of design-level work.

## The four layers, as I run them

**1. Earn (real-money candidates — currently zero deployed, two in trial)**
- *F1 funding carry* — the only validated family (93.9% historical cycle accuracy).
  It earns from exchange payment mechanics, not prediction. Its honest constraint is
  capital: at $420 the two-leg positions can't clear exchange minimums. It evaluates
  every 15 minutes and will trade the moment spreads and capital allow.
- *Listing-short probe* — passed all eight frozen gates and an adversarial audit
  (the pipeline's first GO), now proving itself in shadow: every would-be trade logged
  with per-bar prices, real day-1 spreads, and a pre-registered score. At ~1-2 Binance
  listings/week it reaches its 30-decision evaluation floor in months, not days.
- Promotion to real money requires the frozen gate (DSR≥0.10, PBO≤0.5, OOS-WR≥0.55,
  AUC≥0.60) on forward data **plus** the owner's explicit sign-off. No exceptions;
  the double latch (PAPER → CONTROLLED_LIVE) stays.

**2. Learn (the paper laboratory)**
- The directional lane trades PAPER across Binance/Bybit/Bitget as a bounded
  research cohort. The old take-profit geometry that mechanically targeted a
  63-67% hit rate remains disabled because it was negative after costs. Daily
  reporting counts the real exits, requires at least 30 closed outcomes, and
  cannot declare success without positive expectancy and profit factor above 1.
- Every idea enters through the evidence pipeline: after-cost screen → independent
  adversarial audit → log-only shadow → frozen gate. Failures go to a permanent
  refuted-families ledger so no lesson is paid for twice.

**3. Protect (rails no strategy can bypass)**
- 3% max risk per trade, 12% max total exposure, leverage clamped, stop-loss on every
  futures position, daily-loss circuit breaker, post-stop-loss cooldowns (repaired
  2026-07-10 — they had been silently dead on futures), exchange minimum floors only
  when analysis says trade, and a loss clamp checked at every size.

**4. Watch (the machine watches itself)**
- Heartbeats with clock-drift checks; self-healing data feeds; email alerts; every
  open/close journaled within seconds; a daily 07:00 scoreboard in plain terms
  (journal/ + data/goal_progress.json) showing the current-boot cohort — the number
  that reflects the configuration actually running, not a blend of dead regimes.

## What triggers the owner (the only three decisions that are yours)

1. **Capital** — the single fastest lever: carry becomes economic as the account grows.
2. **Promotion sign-off** — when a strategy passes the frozen gate, you get a one-page
   case; real money moves only on your yes.
3. **Posture changes** — leverage, live-flips, or turning the model's per-trade
   advisory back on (with its measured cost) are owner calls, never silent defaults.

## What I will not do, permanently

Deploy unvalidated strategies to real money; widen stop losses; loosen frozen gates;
present paper, backtest, or geometry numbers as profit; or spend real dollars (API or
fees) that measurement says buy nothing.
