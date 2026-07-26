# Autonomous Futures Bots: Mechanisms, Techniques, Time-Frames & Methodologies
*Generated: 2026-07-23 | Sources: 17 (this pass) + 21 (companion 24_ report) | Confidence: High on convergent findings; single-source items labeled*

Second deep-research pass (owner request "re-verify everything + /deep-research …mechanisms,
skills, techniques, time-frames & methodologies which can work with an autonomous trading bot").
Companion: `24_deep_research_futures_2026-07-23.md` (strategy families). This pass: the HOW.
Sub-questions: (1) time-frames vs costs; (2) what separates surviving bots; (3) validation
methodology; (4) execution mechanics; (5) sizing/risk frameworks.

## Re-verification (local, measured)

- Full bot suite: **3,669 passed / 1 skipped / 0 failed** (`_workspace/pytest_reverify_2026-07-23.txt`)
  — the 4 failures found this morning are fixed (config dotenv hook, 2 stale test stubs, 1 heuristic).
- Runtime: PAPER + MAX_FLOW_BAND, `signal_source=mcp`, `entry_policy=APPROVED_PAPER`, not halted,
  cycle 65, heartbeat fresh at 11:40Z, ES budget ok. Maker-finalize fixes live since the 10:08Z boot.

## 1. Time-frames (the sweet-spot question, answered with data)

- A 2026 systematic study publishing a net-of-cost timeframe ladder finds the cost-turnover
  gradient we predicted: **H1 Sharpe 1.54 (847 trades/mo) < H4 2.08 < H6 2.41 (142/mo) >
  H8 2.18 > D1 1.63** at 4 bps/trade ([arXiv 2602.11708](http://arxiv.org/abs/2602.11708)).
  Caveats stated plainly: it is a trend-following paper (family locally refuted), the "H6
  optimum" is that study's own 6-cell sweep, and no DSR/multiplicity control is visible —
  we cite the GRADIENT (hourly loses to 4–8h after costs), not the specific winner.
- Funding dominates holding costs beyond multi-day holds: for 30d+ perp positions, holding
  fees run **5–20× entry/exit fees** ([Ostium 2026](https://www.ostium.com/blog/long-term-dex-perps-cheapest-places-to-hold-in-2026)) —
  any multi-day directional hold must charge realized funding (ours do).
- Consumer-grade corroboration of scalping fee-death at 1m–5m ([FinanceFeeds](https://financefeeds.com/best-chart-time-intervals-for-crypto-traders/)).
- **Local synthesis (unchanged):** 5m–30m = fee burn; 45m doesn't exist on Binance; the
  defensible research anchor stays **4h** (all 7 probes), with the live band lane's measured
  holds at 0.5–3h. External H6 evidence suggests 4h–8h is the right *band*; a 6h variant is
  only a prereg question, not a change.

## 2. What separates surviving autonomous bots (survivorship evidence)

- **75–90% of retail bot operators lose money over a full cycle** — triangulated from ESMA
  broker disclosures (74–89%), academic day-trader studies (Chague et al. 2019: 97%;
  Barber et al.: ~80%), and bot-marketplace data; no study isolates crypto bots more precisely
  ([MarketTrace forensic analysis](https://markettrace.ai/blog/why-crypto-trading-bots-lose-money)).
- UC Berkeley/AnChain: retail bots lost **77× more per user** than manual traders on studied
  platforms; ~95% of retail "AI" bots are rule-based scripts with AI branding
  ([Ventureburn](https://ventureburn.com/ai-trading-bots-vs-human-traders-in-2026-what-the-data-actually-shows/), [Memeburn](https://memeburn.com/ai-trading-bots-vs-human-traders-what-the-data-says-in-2026/)).
- The named survivor traits map 1:1 onto this bot (verified in code today):
  | Survivor trait (external) | This bot |
  |---|---|
  | Point-in-time universes | spec regen from own eligibility rules (07-20) |
  | Signal gating | MCP floor + meta-filter + economic gate |
  | Regime filtering | `BAND_REGIME_FILTER` (shipped; currently OFF by owner directive) |
  | Calibrated RR thresholds | ATR brackets; AccBand geometry |
  | Decision-time slippage capture | sim_execution slippage model + decision provenance |
  | Live monitoring + kill-switch | watchdog, heartbeat, ES budget, daily-loss breaker, incident latch |
  | Hard drawdown limits | 2%/day breaker + charter 8% hard SL + ES cap |
  | Forward verification, not backtests | shadow probes + promotion funnel + owner-signed gate |
- **Honest local note:** infrastructure survival ≠ edge. Our directional lane still measures
  negative expectancy; the edge instruments remain carry + event-driven probes. The external
  data says our machinery is what the surviving 10–25% run — it does not say the machinery
  manufactures profit.

## 3. Validation methodology (external best practice = our frozen gates)

- DSR is a probability, not a Sharpe: a Sharpe 1.8 found across 100 trials on 1,000 obs
  deflates to DSR ≈ 0.62 — "a coin flip with a slight tilt"
  ([ARIA Analyst](https://ariaanalyst.pro/blog/walk-forward-backtesting)).
- "A corpus that never logged N cannot be deflated after the fact… the search that selected
  the configuration is never written down. Until that search is counted, the out-of-sample
  curve means nothing" ([TrustedQuant](https://trustedquant.com/backtesting/walk-forward-optimization-isnt-enough/)) —
  this is precisely why our pipeline preregisters trials/hashes BEFORE runs.
- Walk-forward mechanics: IS ≥ 5× OOS, step = OOS length (no overlapping folds)
  ([AI Fin Hub cookbook](https://aifinhub.io/articles/walk-forward-validation-cookbook/));
  CSCV/PBO complementary to walk-forward ([Alpha Learning](https://stockalpha.ai/alpha-learning/backtest-reality-checks-deflated-sharpe-pbo-and-multiple-testing-control);
  [arXiv 2512.12924](https://arxiv.org/html/2512.12924v1)).
- **Verdict: no methodology change needed** — the external gold standard (prereg + trial
  counting + DSR/PBO + walk-forward + cost stress) is already the frozen local gate set.

## 4. Execution mechanics (maker-first, quantified)

- Post-only economics: EV_maker = p·(g−2m) vs EV_taker = g−2t; at a 2/5 bps maker/taker split
  and 5 bps captured move, post-only stops paying when fill probability drops below **~0.72**
  ([JackTrader maker-taker analysis](https://dev.to/jacktrader/maker-taker-economics-for-grid-bots-when-post-only-actually-pays-4ihm)) —
  a quantitative frame for the maker-first lane's existing fill-rate soak counters
  (`_maker_counters`/`_maker_fills`): compare measured fill rate against the crossover, per pair.
- Bybit futures maker 2 bps vs taker 5.5 bps; post-only (`timeInForce=GTX` on Binance) is the
  guarantee mechanism ([cryptoprofitcalc](https://cryptoprofitcalc.com/bybit-trading-bot-fees-2026-full-breakdown-savings-guide/),
  [trading-strategies.academy](https://trading-strategies.academy/archives/46899)).
- Backtest fee modeling should use blended maker/taker shares, not a flat constant — ours
  stresses 1.5× fee / 2× slip in the economic gate; consistent.
- **No wiring change:** maker-first is already ON with measured counters. The only follow-up
  worth an owner decision later: publish the per-pair fill-rate vs 0.72-crossover readout in
  the soak report (monitoring, not behavior).

## 5. Sizing & risk frameworks (external norms vs ours)

- "The magnitude of per-trade risk, rather than the choice of signal, is the primary
  determinant of long-run survival"; institutional per-trade risk norm **0.1–0.5% of AUM**;
  vol-targeting 10–15% annualized; fractional Kelly ¼–½ as a ceiling, full Kelly "almost never"
  ([Falco risk](https://falcoalgo.com/insights/risk-management-futures/), [Falco Kelly](https://falcoalgo.com/insights/kelly-criterion-futures/),
  [Keel vol-targeting](https://usekeel.io/learn/volatility-targeting), [NexusFi Kelly](https://nexusfi.com/a/risk-management/kelly-criterion)).
- Ours: risk_per_trade **0.125%** (inside the institutional band), max leverage 1.5×
  (< charter 2.5×), vol-target sizing module, ES budget with projected-legs check,
  2%/day loss breaker, equity-protecting kill-switches, Kelly module present but correctly
  NOT sizing up (all measured edges ≤ 0 → Kelly says don't bet bigger).
- Layered model (per-trade risk % → Kelly ceiling → portfolio vol scaling) matches our stack.
- **Verdict: no sizing change** — external norms validate current parameters; any increase
  without a measured positive edge is overbetting by definition.

## Key takeaways

1. **Re-verified: suite 3,669/0 green; runtime healthy; today's fixes live or runtime-neutral.**
2. The bot already implements every trait the survivorship literature attributes to the
   profitable minority; the binding deficit is EDGE, which only the pipeline can supply.
3. Time-frame: stay on 4h research anchor / band-lane geometry; external evidence brackets the
   viable zone at 4–8h; sub-1h remains fee death. A 6h-variant test is prereg-eligible, low priority.
4. Execution/monitoring micro-follow-up (optional, no behavior change): per-pair maker
   fill-rate vs the 0.72 post-only crossover in the soak readout.
5. **Restart: nothing requires it** — config change is runtime-neutral, maker fixes already
   live since the 10:08Z boot. Safe to restart any time; verify the MAX_FLOW_BAND boot banner.

## Sources (this pass)

1. [arXiv 2602.11708](http://arxiv.org/abs/2602.11708) — net-of-cost timeframe ladder (H1→D1); single-study sweep, trend family
2. [Ostium](https://www.ostium.com/blog/long-term-dex-perps-cheapest-places-to-hold-in-2026) — funding dominates 30d+ holding costs (5–20×)
3. [FinanceFeeds](https://financefeeds.com/best-chart-time-intervals-for-crypto-traders/) — scalping fee-death (consumer-grade)
4. [Zenodo 19132841](https://zenodo.org/records/19132841) — ML perp-trading systematic review (Mar 2026)
5. [MarketTrace](https://markettrace.ai/blog/why-crypto-trading-bots-lose-money) — 75–90% loss-rate triangulation; 7 failure modes
6. [Block Research](https://blockresearch.ai/blog/is-crypto-trading-bot-profitable) — profitable minority is mechanical, not mystical
7. [Altrady](https://www.altrady.com/blog/crypto-bots/are-ai-crypto-trading-bots-profitable-2026) — verified operator returns 5–25%/yr above B&H; many bots underperform holding
8. [Ventureburn](https://ventureburn.com/ai-trading-bots-vs-human-traders-in-2026-what-the-data-actually-shows/) / [Memeburn](https://memeburn.com/ai-trading-bots-vs-human-traders-what-the-data-says-in-2026/) — UC Berkeley/AnChain 77× finding; 95% "AI" bots are scripts
9. [AI Fin Hub](https://aifinhub.io/articles/walk-forward-validation-cookbook/) — walk-forward parameters and leakage traps
10. [TrustedQuant](https://trustedquant.com/backtesting/walk-forward-optimization-isnt-enough/) — trial counting; expected max Sharpe under the null
11. [ARIA Analyst](https://ariaanalyst.pro/blog/walk-forward-backtesting) — DSR-as-probability worked example
12. [Alpha Learning](https://stockalpha.ai/alpha-learning/backtest-reality-checks-deflated-sharpe-pbo-and-multiple-testing-control) — DSR + PBO practice checklist
13. [arXiv 2512.12924](https://arxiv.org/html/2512.12924v1) — hypothesis-driven walk-forward validation framework
14. [JackTrader](https://dev.to/jacktrader/maker-taker-economics-for-grid-bots-when-post-only-actually-pays-4ihm) — post-only EV math; 0.72 crossover
15. [trading-strategies.academy](https://trading-strategies.academy/archives/46899) — GTX post-only implementation
16. [Falco](https://falcoalgo.com/insights/risk-management-futures/) + [Kelly](https://falcoalgo.com/insights/kelly-criterion-futures/), [Keel](https://usekeel.io/learn/volatility-targeting), [NexusFi](https://nexusfi.com/a/risk-management/kelly-criterion) — sizing norms
17. [cryptoprofitcalc](https://cryptoprofitcalc.com/bybit-trading-bot-fees-2026-full-breakdown-savings-guide/) — Bybit bot fee mechanics

## Methodology

5 sub-questions, 5 search queries (this pass), 17 unique sources graded academic > practitioner >
consumer; cross-referenced against companion report 24_ and the local ledger/artifacts. Local
re-verification: full pytest suite + heartbeat/runtime inspection. Single-source numbers labeled;
vendor content used only for mechanics (fees/APIs), not edge claims.
