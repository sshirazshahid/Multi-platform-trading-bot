# Owner's Trading Plan

**Status:** Active from 2026-07-05 · Amended only in writing (see §7)
**Design basis:** `docs/superpowers/specs/2026-07-04-owner-trading-plan-design.md` (approved). Companion Owner's Manual: forthcoming per the same spec.

This plan is the written rulebook for how capital, risk, and decisions work in this trading system. It is written for an owner who is new to trading: every term is defined the first time it appears. It contains no personal dollar amounts (this repository is public); every rule is a percentage of whatever capital the account holds.

## 1. Objective

**Prove it, then decide.** The system accumulates evidence in PAPER mode (simulated trading — no real orders are ever sent) until the record supports a defensible GO or NO-GO decision on live capital, with a first formal review at roughly six months. Success is NOT "the bot made money this month." Success is (a) a readable, honest evidence record and (b) an owner who can read it. A verdict of "NO-GO — the edge does not clear costs at this scale" is a success of the process, not a failure. (An *edge* = a repeatable statistical advantage: trades that make money on average after all fees, not just sometimes.)

Be clear about what six months can and cannot decide: given how rarely the strategy's entry conditions occur (§6), **a GO verdict within six months is not an expected outcome.** The realistic six-month outcomes are NO-GO, or a written decision to continue accumulating paper evidence. That is by design, not a delay.

## 2. Capital rules

The system trades on paper only, today. A real dollar moves ONLY when ALL THREE conditions hold:

1. **The F1 promotion checklist is fully green.** F1 is the funding-carry strategy (explained in §6). Green means: at least 60 resolved paper cycles (a *cycle* = one complete carry round trip: enter the paired spot + short position, hold it, exit both sides; *resolved* = both sides closed and the final profit or loss booked); net profit above zero after costs; profit factor ≥ 1.25 (*profit factor* = gross wins ÷ gross losses); at least 2 of 3 chronological evidence folds positive (a *fold* = one of three equal, consecutive time slices of the paper record — the strategy must be profitable in at least 2 of the 3 slices on their own, not just overall); the cost-stress test green (the same record re-scored with fees and slippage deliberately worsened, to check the profit survives); zero unresolved one-leg events (a *one-leg event* = one side of a hedged position filled without the other side; *hedged* = made of two offsetting positions, so a price move that hurts one side helps the other by the same amount); and concentration caps respected.
2. **Every live-activation precondition in the F1 report is resolved** — collateral unification (both sides of the position draw margin from one shared wallet on the exchange — the *venue* — being used), maker-first order legs (a *maker* order adds liquidity at a posted price and pays lower fees than a *taker* order, which takes an existing price), and event-driven hedge monitoring (the bot reacts to fills the moment the exchange reports them, instead of checking on a timer). The F1 report is generated on the bot machine in the `reports/` folder as `f1_carry_report_<date>.md`; the promotion checklist and the preconditions section are inside that report.
3. **The owner signs `docs/CONTROLLED_LIVE_CHECKLIST.md` himself.**

Any one condition missing = no live capital. No exceptions, no matter how good a week looks.

## 3. Risk rules (the charter, codified)

These limits already exist in the system's code and configuration; this section is the human-readable copy. If code and plan ever disagree, that is a kill criterion (§5, item 7): stop and reconcile before trading continues.

- **Per-trade risk:** never more than 3% of total capital at risk on a single trade. (A *stop-loss* = a standing instruction that automatically closes a position once its loss reaches a preset level, capping the damage. "At risk" = the loss if the stop-loss is hit — the reading the sizing code enforces; position size itself is independently bounded by the exposure cap and the leverage cap below.)
- **Total open exposure:** all open positions together ≤ 12% of capital, measured on gross notional (*notional* = a position's full face value; *gross* = both sides of a hedged pair count).
- **Leverage:** never above 2.5×. (*Leverage* = trading with borrowed funds so the position is bigger than the cash put up; 2.5× means positions up to two-and-a-half times the account's own money — losses are magnified by the same multiple.) The carry strategy runs at 1× (no leverage).
- **Carry-specific caps:** ≤ 5% of capital per symbol; ≤ 20% total carry notional; no averaging down (never add to a losing position). **Reconciliation:** the charter's 12% total-exposure cap is the binding ceiling on the live path and is measured on gross notional; the 20% carry cap is the paper lab's own sizing ceiling, measured against paper equity. Before any live promotion, carry gross notional must fit inside the charter's 12% — or the charter must first be amended in writing per §7.
- **Stops:** every directional futures position carries a hard stop-loss from entry (a *futures position* = a contract that tracks a coin's price without owning the coin — the specific kind this system trades is defined in §6; *directional* = betting on the price going one way), plus a charter-level backstop — any directional position 8% below its entry price is closed immediately. For a carry position, the charter's 8% guardian applies to the hedged pair's combined value, which cannot drop 8% while both sides are on; a failure of one side instead triggers the one-leg recovery latch (§4/§5). Carry positions use gate rules as their stop: notional mismatch, adverse basis (*basis* = the gap between the futures price and the *spot* price; spot = buying and holding the actual coin itself, paid in full — the spot price is what the coin costs outright right now), margin-buffer erosion (*margin* = the cash the exchange holds as security for the short side; *erosion* = that cushion shrinking toward the level where the exchange force-closes the position), funding — the periodic fee defined in §6 — flipping negative (the position starts paying instead of collecting), and a maximum hold time. Those rules ARE the stop; removing any of them is removing the stop.
- **Why the margin-buffer rule matters:** the hedge's cancellation only holds while both sides stay open. The exchange sees only the short side's margin, so a sharp rally can force-close ("liquidate") the short side alone even though the pair as a whole is flat — in the March 2024 BTC rally of +74%, a 2× short would have been liquidated. The margin-buffer gate exists to prevent exactly that.
- **What the hedge does NOT protect against:** the exchange itself failing. A carry position holds BOTH sides on one venue, so venue failure is a total loss of that position (the FTX collapse is the precedent). That is why venue stress is a kill criterion (§5, item 6).

Some external conventions (for example, common readings of the "3-5-7 rule") cap total risk across open positions at 5% — a risk-at-stop measure, not directly comparable to the charter's 12% gross-exposure cap, but a useful reminder that the 12% is a ceiling, not a target. The system has historically run far below it.

## 4. Decision rights

**The bot alone decides:** individual entries and exits inside its gates; settlement accrual (booking each funding payment as it arrives); exits on its own rules.

**The owner alone decides:** any operating-mode change (PAPER ↔ CONTROLLED_LIVE — the mode in which real orders can be sent); going live; clearing the reduce-only recovery latch (a safety lock that blocks new orders after an unexpected event until a human clears it); adding or removing capital; changing any risk number in §3; restarting the process.

Neither party exercises the other's rights. If the owner feels tempted to override an in-gate decision, that impulse is written into the weekly-review notes — not into the system.

## 5. Kill criteria — stop everything, investigate before restart

Any ONE of these means: stop the process and investigate. Restart only after the cause is understood and written down. (Item 5 is the single exception with a softer remedy, stated inline.)

1. A recovery latch the owner cannot explain.
2. The watchdog — the background process that writes a periodic "still alive" heartbeat to `data/heartbeat.json` and `data/carry_heartbeat.json` and emails alerts — has been silent for more than 24 hours while the machine is on.
3. ANY evidence of a real order while the mode says PAPER.
4. An unexplained change in equity (*equity* = the account's total current value: cash plus open positions) larger than 0.5% of capital that the daily report cannot account for.
5. Two consecutive weekly reviews skipped — exception to the rule above: no investigation needed; trading simply pauses (no new entries) until a review is completed and the gap is noted in it.
6. The venue holding a carry position shows withdrawal delays, API halts, or solvency stress — close both sides and reduce exposure to that venue; a hedged position on a failed exchange is a total loss (FTX precedent).
7. The code and this plan are found to disagree on any §3 number — reconcile in writing before trading continues.

## 6. Expectations — what the evidence actually supports

**What runs and why.** Months of systematic falsification found NO after-cost edge in any directional pattern family tested (dozens of families, thousands of variants — trend, mean-reversion, candlestick, funding-signal, ETF-flow, confluence, and more). The single evidence-backed family is **delta-neutral funding carry** (F1): hold spot and an equal-size *short* perpetual future at the same time. (A *short* = a position that profits when the price falls; a *long* is the opposite. Because the account holds the coin itself AND a short of equal size, a price move up or down gains on one side what it loses on the other. A *perpetual future* is a futures contract with no expiry date.) The position collects the *funding fee* — the periodic payment that perp longs make to shorts while the perp trades above the spot price. This payment flips sign: when funding turns negative, the carry position pays instead of collects — which is why "funding flips negative" is an exit rule, not a hold-and-hope situation. F1 profits from market *structure*, not from predicting direction.

**What the record says.** Our in-house historical replay over ~6.8 years found 13 qualifying cycles, all 13 profitable at current costs — but thirteen is far too small a sample to establish a win rate, which is precisely why the promotion checklist demands 60 resolved cycles before any live decision. Treat 13/13 as "the gate logic behaved correctly in replay," not as a promise of future wins. The replay also implies only about **2 qualifying entries per year** at current costs, because the entry gate rejects everything that would not clear fees.

**The arithmetic the owner must hold onto:** at ~2 qualifying entries per year, the 60-cycle floor would take decades to reach. Unless paper cycles accrue much faster than replay entries (the weekly review tracks the actual rate — that observed rate, not hope, is the only valid basis for any timeline), the realistic six-month outcomes are NO-GO or continue-in-paper. **GO within six months is not an expected outcome.**

**External context (historical snapshots, not guarantees):** OI-weighted BTC funding was positive more than 85% of the time over a two-year sample ending mid-2024 (Coinbase market-intelligence data) — evidence of a structural skew, not a standing promise. Neutral-market carry has historically run roughly 5–15% annualized on notional (industry estimates), before the roughly-half haircut for capital tied up in both sides of the position. At this account's scale that supports roughly **0–0.5% per month, possibly negative after costs** — and at ~2 qualifying entries per year, the realized figure sits at the bottom of that range most months (zero, because no cycle is open). Anyone promising more at this scale is selling something.

**Therefore:** most days the correct output is *no trade*. A flat week is the gates working, not the system failing. The slowness IS the honesty of the process.

## 7. Review cadence

- **Daily (~5 min):** open the owner view (a plain-language status screen — forthcoming per the design spec; until it ships, run `python main.py --status` from the bot folder). The liveness lines show whether the bot and its watchdog reported in recently; the attention line is the single most important item today. Anything red → until the Owner's Manual ships, treat it as a §5 event: stop the process and investigate.
- **Weekly (20–30 min):** cycles this week and cumulative progress toward 60; win rate (*win rate* = winning cycles ÷ all resolved cycles) and profit factor, always with the small-sample caveat; top rejection reasons — they should match the market story; recovery/blocks status; re-read one plan section; write down one thing learned.
- **Monthly:** re-read this whole plan.
- **Amendments:** this plan changes only by written edit with a dated changelog line below. Never ad hoc, and never mid-drawdown. (A *drawdown* = any period when account equity is below its previous peak; "mid-drawdown" means equity has not yet recovered to that peak — during such a period no amendment is allowed.)

## Changelog

- 2026-07-05 — Initial version, per the approved design of 2026-07-04.
- 2026-07-05 — Revised after a three-lens adversarial review (charter compliance, evidence honesty, beginner clarity; 33 findings applied): reconciled the 12%/20% cap tension, restored the 8% guardian as universal, added venue-failure and code/plan-disagreement kill criteria, added the short-leg liquidation explanation, re-dated and qualified all external statistics, added the small-sample caveat to the replay result, stated the 60-cycle/6-month arithmetic plainly, and defined every remaining term at first use.
