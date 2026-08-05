# Bot Feature Stack (Customization → Risk → Grid/DCA/Trend): Research Report
*Generated: 2026-07-31 | Sources: ~24 unique | Confidence: Medium–High on risk/WFO/overfit; Medium on grid/DCA (mostly practitioner); High on this-repo mapping*

**Goal (default):** Decision aid for *this* Trading_Bot — what to keep, refuse, or only run behind evidence gates.  
**Angle:** After-cost honesty; retail “feature checklist” vs what actually survives fees, funding, and regime shifts.  
**Tooling:** Firecrawl/Exa unavailable → WebSearch + prior local architecture knowledge.

## Executive Summary

Exchange and SaaS bots sell a **feature laundry list** (grid, DCA, auto-optimize, adaptive switching, trailing stops…). Externally, the pieces that repeatedly survive scrutiny are **risk plumbing** (fixed-fraction sizing, ATR stops, hard DD/daily-loss circuit breakers) and **honest validation** (walk-forward, multiplicity control, paper with realistic costs) — not more entry widgets ([FXStreet sizing](https://www.fxstreet.com/education/why-position-sizing-not-entry-signals-decides-your-drawdown-202607211403), [WFO guides](https://usekeel.io/learn/walk-forward-optimization), [arXiv 2604.15531](https://arxiv.org/pdf/2604.15531)).

**Grid** and **retail “DCA bots”** are regime tools that print in chop/mean-reversion and **bleed in trends**; fee density and leveraged breakouts are the killers ([Cryptogates backtests](https://cryptogates.io/common-grid-trading-mistakes-in-crypto-backtests/), [Block Research DCA](https://blockresearch.ai/blog/dca-bot)). **Auto-optimize / real-time adaptation** without frozen prereg + OOS gates is a known path to spurious predictability.

**This repo already implements** most of the *serious* stack (rule/MCP engine, PAPER modes, ATR SL/TP, trailing stops, risk manager, soft daily-loss CB, shadow probes, evidence pipeline) and correctly **hard-OFFs DCA** / keeps legacy grid out of the live Claude Portfolio path. Current owner posture (F1-only until funding clears; AccBand −EV research) is consistent with the evidence.

## 1. Strategy customization + rule-based engine

**External:** Professional systems are rule/registry driven with explicit allowlists, not free-form “AI decides.” Regime-aware designs prefer **soft allocation** over hard strategy flipping to cut turnover at boundaries ([RegimeSense](https://github.com/moh1tt/RegimeSense); friction-aware agentic optimizer [Springer 2026](https://doi.org/10.1007/s41060-026-01066-0)).

**This bot:** `strategies/rule_engine`, `ENTRY_POLICY` / `APPROVED_PAPER_STRATEGIES`, MCP Brain scoring, StrategySpec route gates, meta-filter. Live path is Claude Portfolio + MCP — legacy strategy classes are backtest/research only (wiring audit).

**Verdict:** Keep. Customization = **allowlisted IDs + specs**, not unlimited indicator soup.

## 2. Integrated risk management tools

Consensus pillars (practitioner + prop-style guides 2025–26):
- Risk a **fixed fraction** of equity per trade (often ≤1–2%); size from stop distance ([AlfaTactix](https://alfatactix.com/academy/risk-management), [QuantVPS](https://www.quantvps.com/blog/trading-risk-management)).
- Volatility-aware sizing (ATR) so risk stays constant when ranges expand ([FXStreet](https://www.fxstreet.com/education/why-position-sizing-not-entry-signals-decides-your-drawdown-202607211403)).
- Account circuit breakers: daily loss, max open risk, max leverage, DD pause ([For Traders](https://fortraders.com/blog/7-risk-management-strategies-for-day-trading-success)).
- Recovery math: 50% DD needs 100% to recover — size for survival.

**This bot:** `risk_manager` (tiers, correlation soft-size, soft daily-loss CB), portfolio ES, leverage clamps, incident latch, economic entry gate, band regime filter. Spec §12 consecutive-loss halts were **removed** by design (owner); soft daily-loss CB is the replacement.

**Verdict:** Risk layer is the product. Do not weaken for fill rate.

## 3. Backtesting + paper trading

**External:** Single backtests overfit; **walk-forward** (optimize IS → freeze → OOS stitch) is the cheap defense ([Keel WFO](https://usekeel.io/learn/walk-forward-optimization), [D&T WFA](https://dtsystems.dev/blog/walk-forward-analysis-backtesting)). Adaptive search can still look significant on **zero-predictability** placebos — need falsification audits ([arXiv 2604.15531](https://arxiv.org/pdf/2604.15531)). Paper must model fees, slip, funding, wick SL — or it lies.

**This bot:** `backtest_v3` / labs; PAPER / OBSERVATION / CONTROLLED_LIVE; `sim_execution` (slip, wick, funding); warehouse; shadow probes; promotion gate (DSR/PBO/OOS-WR/AUC). Honesty gap: paper SL on last-price candles vs live mark-price option (documented).

**Verdict:** Prefer pipeline screens + PAPER accrual over “pretty backtest UI.” Never promote on paper WR alone (AccBand lesson).

## 4. Grid trading (sideways markets)

**Mechanism:** Scale-in buys below / sells above in a range; needs **realized vol > fee+funding drag**.

**Evidence / practice:**
- Per-grid gross should be **~3–5×** round-trip fees; dense grids → fees eat 40–55% of gross ([KXCC handbook](https://kxccex.com/en/articles/grid-strategy-handbook.html), [Cryptogates](https://cryptogates.io/common-grid-trading-mistakes-in-crypto-backtests/)).
- Claim: >60% of manual grids underperform spot hold after fees/slip ([Cryptogates](https://cryptogates.io/common-grid-trading-mistakes-in-crypto-backtests/) — practitioner study; treat as directional warning, not peer-reviewed).
- Trends + **levered** grids → average into losers / liquidation; funding can be 10–30% of grid gross over a month ([KXCC](https://kxccex.com/en/articles/grid-strategy-handbook.html), [Steyble May 2026](https://steyble.com/blog/perp-grid-trading-strategy-may-2026)).
- Need breakout exit / recenter / trend filter.

**This bot:** `GridTradingStrategy` in legacy/backtest-only tree — **not** in live Claude Portfolio path.

**Verdict:** Do **not** enable live futures grid without hashed prereg (range filter + fee multiple + breakout kill). Expectation NO_GO under current quiet/chop + AccBand −EV lessons. Spot grid ≠ perp grid.

## 5. Dollar-cost averaging bots (DCA)

**Critical distinction** ([Block Research](https://blockresearch.ai/blog/dca-bot)):
1. **True DCA:** fixed $ on a calendar (investment process).  
2. **Retail “DCA bot”:** base order + safety ladder + TP (mean-reversion trade) — high WR, fat left tail when asset never reverts.

Bull markets: lump sum often beats calendar DCA; bears: averaging can help **cost basis** if capital survives ([Echo Zero](https://blog.echozero.app/article/dca-bot-performance-during-market-downturns-vs-bull-markets) — blog stats; verify before trusting %). Fixed ladders blow up in grind-downs / cascades; volatility-adaptive still holds underwater inventory ([Block Research adaptive](https://blockresearch.ai/blog/crypto-dca-bot)).

**This bot:** `ENABLE_DCA=False` hard-OFF; DCA strategy not Claude-primary.

**Verdict:** Keep OFF for futures. Spot calendar DCA is portfolio ops, not an edge screen. Safety-ladder DCA on alts = REFUSE without capital caps + mean-reversion eligibility + hashed prereg.

## 6. Trend-following

**External:** Institutional multi-asset CTA/trend is a real diversifier (2026 YTD indexes mid-single to ~9%; June whipsaws) — different from single-pair crypto TA ([prior report `51_*`](_workspace/strategy_pipeline/51_deep_research_futures_strategies_2026-07-31.md)). Crypto TSMOM/breakout often fail after costs / multiplicity in this bot’s ledger.

**This bot:** Shadow TSMOM + breakout probes (log-only, NO-PROMOTE expectation); AccBand is geometry not trend edge; F1 is carry.

**Verdict:** Shadow OK; live trend installs need reopen bar + frozen gate. Do not confuse CTA marketing with enabling `TrendFollowingStrategy`.

## 7. Rebalancing

**External:** Calendar/threshold rebalancing of spot portfolios is **ops + tax/fee management**, not alpha. Too-frequent rebalance loses to costs (general portfolio theory; crypto-specific peer-reviewed sizing thin in this pass — gap acknowledged).

**This bot:** `RebalancingStrategy` path exists; `ENABLE_REBALANCE=False` (same hard-OFF pattern as DCA).

**Verdict:** Spot rebalance = fine as ops. Futures “rebalance” grids/positions ≠ the same thing.

## 8. Auto-optimizing

**External:** Continuous parameter search → overfitting. WFO measures degradation; it does **not** license endless re-tuning on the same OOS ([D&T](https://dtsystems.dev/blog/walk-forward-analysis-backtesting)). Spec search can invent significance on martingale noise ([arXiv 2604.15531](https://arxiv.org/pdf/2604.15531)).

**This bot:** `auto_mutator` / learning engine / knowledge model — advisory and pause/blacklist style; promotion is owner-signed. Auto-backtest sweeps exist for research.

**Verdict:** Auto-opt **parameters into live** = refuse. Auto-opt **offline** behind hashed prereg + honesty audit = allowed research (see `52_*` κ-filter).

## 9. Real-time strategy adaptation

**External:** Regime detectors (HMM etc.) + soft weights reduce hard-switch churn ([RegimeSense](https://github.com/moh1tt/RegimeSense)). Cost-aware controllers only trade when benefit > friction ([Springer agentic](https://doi.org/10.1007/s41060-026-01066-0)). Hard flip every bar = fee farm.

**This bot:** Band regime filter, BTC trend soft-size, meta-filter, scalp quiet veto, F1 edge gates — **adaptation of risk/admit**, not swapping to random strategies. Shadow ensemble stays log-only.

**Verdict:** Adapt **gates and size**, not “pick a new strategy every cycle.” Aligns with F1-only until funding clears.

## 10. Position sizing

Evidence: sizing dominates drawdown path more than entry cosmetics ([FXStreet](https://www.fxstreet.com/education/why-position-sizing-not-entry-signals-decides-your-drawdown-202607211403)). Fixed fractional + stop distance; shrink in high vol.

**This bot:** Risk % / leverage tiers / correlation and regime soft-multipliers / economic gate / portfolio ES. Kelly stats currently negative → stay small / PAPER.

**Verdict:** Keep conservative; do not raise risk % to “get fills.”

## 11. Stop-loss + trailing stops

ATR-based stops (≈1.5–2×) often beat naive fixed % for swing horizons ([TradeAlgo guide](https://www.tradealgo.com/trading-guides/stocks/swing-trading-risk-management-position-sizing-stop-losses-and-portfolio-rules) — practitioner). Trailing locks gains but can cut winners into “trailing_stop” exits before TP (this bot has lived that tension under AccBand).

**This bot:** ATR SL authoritative at entry (MCP cannot widen); `TrailingStopManager`; AccBand holds time-exit suppression; mark vs last trigger flag.

**Verdict:** Keep hard SL. Trailing is optional research — measure after-cost ΔEV before any AccBand trailing change (would need its own prereg).

## 12. Max drawdown limits

Hard DD / daily loss limits are survival tools; prop trailing DD is especially unforgiving ([For Traders](https://fortraders.com/blog/7-risk-management-strategies-for-day-trading-success)). Risk manager `max_drawdown_pct` + soft daily-loss CB + incident latch cover the spirit.

**This bot:** Soft daily-loss CB (entries only); open positions keep SL; Spec §12 streak halt removed. Peak-to-trough DD tracked in risk state.

**Verdict:** Prefer **soft CB + per-trade SL** over process-killing halts that freeze F1 when AccBand bleeds — current design matches owner history.

## Feature → bot matrix

| Feature | External after-cost read | This bot today | Action |
|---------|--------------------------|----------------|--------|
| Rule engine / customization | Necessary hygiene | Strong (policy + MCP + specs) | Keep |
| Integrated risk | Decides survival | Strong | Keep / don’t loosen |
| Backtest + paper | Required; easy to fake | PAPER + sim + warehouse + gates | Prefer evidence pipeline |
| Grid (sideways) | Regime tool; fees/trend kill | Legacy only | No live without prereg |
| DCA bots | Ladder ≠ true DCA; tail risk | `ENABLE_DCA=False` | Keep OFF |
| Trend-following | CTA ≠ crypto TA | Shadow only | No promote |
| Rebalancing | Spot ops | Spot path exists | Ops only |
| Auto-optimizing | Overfit machine | Research sweeps OK | Never auto-live |
| Real-time adaptation | Soft regime OK | Gates/sizing adapt | Don’t strategy-thrash |
| Position sizing | Critical | Implemented | Keep tight |
| SL + trailing | SL mandatory; trail tradeoff | ATR SL + trail mgr | SL keep; trail measure |
| Max DD limits | Circuit breakers | Soft CB + latch | Keep |

## Key Takeaways

1. **Buy risk infrastructure, not feature count.** Grid/DCA/auto-opt are where retail bots bleed.
2. **This stack is already past the “missing UI checkbox” stage** — the binding constraint is **edge (F1 funding)** and **refusing −EV AccBand**, not missing grid/DCA.
3. **Any new family (grid, ladder-DCA, live trend)** needs hashed prereg + after-cost screen; expectation often NO_GO.
4. **Adaptation should mean cost/regime gates**, matching `52_prereg_cost_aware_accband_kappa` and F1-only — not Optuna-into-live.
5. **Paper without funding+slip+wick is marketing**, not validation.

## Sources

1. [Steyble — perp grid May 2026](https://steyble.com/blog/perp-grid-trading-strategy-may-2026)  
2. [KXCC — grid handbook](https://kxccex.com/en/articles/grid-strategy-handbook.html)  
3. [Cryptogates — grid backtest mistakes](https://cryptogates.io/common-grid-trading-mistakes-in-crypto-backtests/)  
4. [Phemex — grid bot 2026](https://phemex.com/academy/grid-trading-bot-setup-passive-income)  
5. [XT — futures grid guide](https://www.xt.com/en/blog/post/futures-grid-trading-bot-strategy-leverage-optimization-risk-management-guide-for-btc-traders)  
6. [Block Research — DCA bot](https://blockresearch.ai/blog/dca-bot)  
7. [Block Research — adaptive DCA](https://blockresearch.ai/blog/crypto-dca-bot)  
8. [Block Research — DCA into a drop](https://blockresearch.ai/blog/dca-bot-strategy)  
9. [Echo Zero — DCA bull vs bear](https://blog.echozero.app/article/dca-bot-performance-during-market-downturns-vs-bull-markets)  
10. [Keel — walk-forward optimization](https://usekeel.io/learn/walk-forward-optimization)  
11. [D&T — walk-forward analysis](https://dtsystems.dev/blog/walk-forward-analysis-backtesting)  
12. [arXiv 2604.15531 — spurious predictability](https://arxiv.org/pdf/2604.15531)  
13. [FXStreet — position sizing vs drawdown](https://www.fxstreet.com/education/why-position-sizing-not-entry-signals-decides-your-drawdown-202607211403)  
14. [AlfaTactix — risk management 2026](https://alfatactix.com/academy/risk-management)  
15. [QuantVPS — risk management](https://www.quantvps.com/blog/trading-risk-management)  
16. [TradeAlgo — swing risk / ATR stops](https://www.tradealgo.com/trading-guides/stocks/swing-trading-risk-management-position-sizing-stop-losses-and-portfolio-rules)  
17. [For Traders — day trading risk](https://fortraders.com/blog/7-risk-management-strategies-for-day-trading-success)  
18. [RegimeSense](https://github.com/moh1tt/RegimeSense) — soft regime allocation  
19. [Springer — regime-aware agentic portfolio opt](https://doi.org/10.1007/s41060-026-01066-0)  
20. [MarketRegimeTrader](https://github.com/0x596173736972/MarketRegimeTrader) — HMM + WFO demos  
21. Local: `CLAUDE.md` architecture; `config.py` ENABLE_DCA; `core/risk_manager.py`; `core/trailing_stop_manager.py`; `51_*` / `52_*` pipeline artifacts  

## Methodology

Sub-questions: (1) which checklist features are survival vs alpha? (2) grid/DCA after costs? (3) auto-opt/adaptation overfit? (4) sizing/SL/DD best practice? (5) what does this repo already have?

~10 search queries; practitioner-heavy on grid/DCA (flagged); stronger on WFO/risk. Mapped each item to live vs legacy bot paths.

## Pipeline honesty

This report does **not** authorize enabling DCA, live grid, auto-live Optuna, or AccBand reopen. F1-only + `52_*` cost-filter prereg remain the active research track. New feature asks → ledger check → hashed prereg → screen → audit → shadow only.
