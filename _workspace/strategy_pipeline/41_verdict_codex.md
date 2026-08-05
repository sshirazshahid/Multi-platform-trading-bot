# 41 - Independent Codex verdict: liquidation-cascade reversion

## Preregistration identity

This verdict is based only on `41_dossier_liq_cascade.md` and the frozen
`41_prereg_liq_cascade.md`.

Preregistered `sha256_md`:
`13ee84e40f2604b660d763082f2681200532c1f6bd55cbbb21f4c9491085afaf`.

## Ledger check

The closest binding ledger row is **OI-divergence**, refuted as
`NO_EDGE -> gates disabled` on 2026-05-30. This candidate is sufficiently
different to earn a bounded screen because its frozen signal is observed
liquidation USD flow from Binance `forceOrder` aggregates. The preregistration
expressly forbids OI level and OI change. It is therefore not a rerun of the
refuted directional OI signal.

The ledger also refutes generic RSI mean reversion and indicator-confluence
entries. Those rows do not directly dispose of this candidate: the frozen
entry contains no RSI, moving average, oscillator, or price-pattern condition.
The reversion direction is tied to a specified forced-flow event.

This distinction is narrow. Calling the idea an "OI-flush" does not authorize
an OI-derived implementation. Adding OI, funding-sign, or technical-analysis
conditions would leave the cited hash and require a new preregistration. The
existing ledger refutations remain binding.

## Evidence weighed

Evidence favoring a screen:

- The hypothesis has a concrete microstructure mechanism: forced selling
  followed by a long, and forced buying followed by a short. It is falsifiable
  and directionally symmetric.
- The majors have credible Stage-0 feasibility at the primary USD threshold:
  the dossier reports 396 BTC hours and 373 ETH hours at or above $1 million.
  That is enough pre-outcome evidence to justify running the frozen counting
  rule rather than waiting merely for raw trigger volume.
- Signal timing is explicit: hour `t` liquidation flow is known at the close,
  entry is at `close[t]`, and outcomes begin after that point.
- The preregistration addresses the main execution objection up front with
  30 bps primary and 60 bps stress round-trip costs, funding charges, bounded
  holds, overlap suppression, joint multiplicity control, and fail-closed
  gates.
- No vendor purchase is required, and the full after-cost computation is
  separated from this verdict day.

Evidence against promotion or broad screening:

- The local sample spans only about 3,130 hours, with about 2,010 distinct
  observed hours. A large event count is not the same as broad regime coverage
  or independent bets.
- Binance's throttled `!forceOrder@arr` stream undercounts liquidation
  notional. Threshold assignment can therefore be noisy and cannot be
  represented as complete cascade measurement.
- There is no rigorous after-cost strategy result in the supplied evidence.
  The dossier's prior is only about 25%, and stressed execution costs are a
  plausible structural kill.
- Entering at the hourly close may observe the event too late to capture the
  proposed exhaustion, while sub-hour entries are outside this preregistration.
- FIT-alt feasibility is weak and concentrated in LINK: the dossier reports
  only 4, 2, and 6 trigger hours at $100,000 for ALGO, ARB, and AVAX,
  respectively, versus 78 for LINK. A pooled or basket result could otherwise
  conceal the lack of cross-symbol replication.
- The z-score overlay, thresholds, horizons, arms, and two cost assumptions
  create meaningful multiplicity. Holm correction is necessary, but it does
  not create temporal or cross-symbol independence.

## Chosen label

# SCREEN_NOW

The label applies to a **majors-first execution of the frozen screen**, not to
trading or probe deployment. BTC and ETH have enough pre-outcome trigger
evidence to make further passive accrual unnecessary before the binding
Stage-0 count. The screen is the appropriate instrument for deciding whether
the forced-flow mechanism survives the deliberately harsh cost model.

The FIT-alt arm must remain fail-closed under its own frozen Stage-0 rules.
Only independently eligible cells may proceed; LINK must not lend sample size
to ALGO, ARB, or AVAX, and a basket cell must remain an additional
multiplicity-controlled cell rather than a substitute for symbol-level
evidence.

## Implement recommendation if both agree

If the other independent verdict agrees and the required reviewer approves:

1. On the separate authorized heavy UTC day, implement or adapt an outcome
   engine that cites the hash above and exactly enforces the two arms, side
   mapping, threshold grid, past-only 168-hour z-score, entry timing,
   horizons, overlap rule, funding charge, and 30/60 bps cost grid.
2. Run the frozen Stage-0 trigger counts first. Stop an ineligible arm without
   computing its after-cost outcomes.
3. Treat BTC/ETH as the primary screen. Evaluate FIT symbols separately and
   keep the equal-weight basket as the preregistered extra multiplicity cell.
4. Apply the preregistered joint Holm correction and all conjunctive gates.
   Do not reuse or relabel the existing 10 bps liquidation-screen outputs.
5. Produce research artifacts only. Any paper probe, MCP integration,
   capital-scaled sizing variant, vendor-data substitution, or live action
   requires the later authority specified by the pipeline.

No implementation or outcome computation is recommended on this verdict day.

## Explicit non-claims

- This verdict does **not** claim positive expectancy, gate passage, a
  validated edge, or authorization for paper or live trading.
- No after-cost screen outcome, return, win rate, Monte Carlo statistic,
  drawdown statistic, or corrected significance result was computed for this
  verdict.
- It does **not** claim the Binance liquidation stream is complete, that its
  reported trigger hours are independent, or that the sample covers multiple
  market regimes.
- It does **not** claim the FIT-alt arm is adequately sampled or that a
  LINK-dominated basket demonstrates an altcoin-wide effect.
- It does **not** reopen OI-divergence, RSI mean reversion,
  indicator-confluence, directional VPIN, or quarter-hour imbalance.
- It does **not** generalize to vendor-complete data, sub-hour entries, other
  venues, OI/funding overlays, discretionary exits, or capital-scaled
  variants.
