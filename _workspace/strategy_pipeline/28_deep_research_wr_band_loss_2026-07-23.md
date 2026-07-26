# Why PAPER Is Losing and Missing the 59–67% WR + Profit Band
*Generated: 2026-07-23 | Sources: 12 external + local warehouse/goal/config | Confidence: High*

## Executive Summary

The bot is not “broken” in a way that a code tweak restores both a 59–67% win rate **and** after-cost profit. Today’s exits show **avg win ≈ $0.14 vs avg loss ≈ $0.35** (reward/risk ≈ 0.39). That payoff shape needs **~72% WR** just to break even before any further friction. Even if AccBand geometry later lifts WR into 59–67%, expectancy with *these* win/loss sizes stays **negative**. Thirty-day directional expectancy is about **−0.24R**. AccBand was designed to deliver WR-by-geometry, not edge; screen-13 already showed every regime bucket after-cost negative. The binding failure is **no positive after-cost edge + compressed TP vs full SL**, not a missing indicator.

## Snapshot (live, this session)

| Lane | n | WR | Net PnL | Expectancy | Status |
|------|---|----|---------|------------|--------|
| UTC-day paper futures | 45 | 26.7% | −$8.87 | −0.20 | NEGATIVE_AFTER_COST_ECONOMICS |
| Rolling 24h | 53 | 34.0% | −$9.06 | −0.17 | NEGATIVE_AFTER_COST_ECONOMICS |
| 7d | 64 | 37.5% | −$9.85 | −0.15 | NEGATIVE_AFTER_COST_ECONOMICS |
| Directional 30d | 816 | 39.2% | −$199 | −0.24 | NEGATIVE_AFTER_COST_ECONOMICS |
| Fresh MAX_FLOW_BAND cohort | 21 | 19.0% | −$5.34 | −0.25 | INSUFFICIENT_SAMPLE |

Today by exit: **SL 31 / −$10.85** vs **TP 14 / +$1.63**. Avg MFE 0.29% < avg MAE 0.40% — adverse excursion dominates favorable.

Heartbeat: PAPER / MAX_FLOW_BAND / not halted / daily_pnl ≈ −$8.85.

## 1. The math that makes 59–67% + profit incompatible *today*

From today’s winning TPs and losing SLs:

- Average win ≈ **$0.136**
- Average loss ≈ **$0.350**
- Gross breakeven WR = L / (W+L) ≈ **72.1%**

| Assumed WR | Approx expectancy (same W/L) |
|------------|------------------------------|
| 26.7% (actual) | ≈ −$0.22 / trade |
| 59% | ≈ −$0.06 |
| 63% | ≈ −$0.04 |
| 67% | ≈ −$0.02 |
| 72% | ≈ $0 |

So the owner dual target (WR in band **and** profitability) fails under current payoff asymmetry even *if* WR recovers into band. External literature says the same: high WR with small wins / large losses is negative expectancy after friction ([CurvedTrading](https://curvedtrading.com/articles/en/trading/expected-value-trading-high-win-rate-losing-money/), [BearBullRadar fee death](https://www.bearbullradar.com/blog/botlab-fee-death), [minimum edge vs costs](https://retired.today/blog/minimum-edge-trading), [CryptoSignalsReview expectancy tables](https://cryptosignalsreview.com/crypto-signal-risk-reward/)).

## 2. AccBand delivers WR geometry, not edge (by design)

`config.py` AccBand docs state explicitly: TP is compressed to a fraction of SL so theoretical hit rate ≈ SL/(SL+TP); **expectancy stays ≈ −costs on a no-edge signal**. Profit promotion still requires after-cost expectancy.

That matches:

- Internal honesty on AccBand / MAX_FLOW_BAND (WR by geometry)
- Screen-13 band-conditional: **0 GO**, all buckets after-cost negative
- Historical directional expectancy ≈ **−0.24R** (goal lane still reports this order of magnitude)

Stop/TP optimization literature: higher WR often coincides with *worse* profit factor when stops widen or targets shrink ([Varrd SL/TP grid](https://www.varrd.com/guides/stop-loss-optimization.html)). Crypto R:R guides: profitability is WR × R:R interaction, not WR alone ([ChartScout](https://chartscout.io/risk-reward-ratio-crypto-trading), [Thrive](https://thrive.fi/blog/trading/risk-reward-ratios-crypto-trading)).

**Inference (labeled):** Hitting 59–67% WR via tighter TP is the *opposite* of the usual profitable asymmetry (small losses, large wins). The band goal and the profit goal pull geometry in opposite directions unless there is a real directional edge large enough to pay for the compressed TP.

## 3. Why WR is *below* band right now (separate from profit)

Band geometry assumes roughly random first-touch of SL vs TP under the set distances. Live WR is ~27–39%, not ~60–65%, because:

1. **No edge / anti-predictive MCP path** — MAE > MFE today; entries are wrong more often than noise geometry assumes.
2. **Fresh cohort after halt/reboot** — profile epoch reset after the maker-provenance HALT fix; n=21 cohort WR 19% is noise + cold start, not a new strategy family.
3. **SL count dominates TP** — 31 SL vs 14 TP today; stops are being hit before compressed TPs.
4. **Fill drag** — prior autopsy (`21_loss_autopsy.md`): `taker_fallback` bled more than `maker` on a 7d window.
5. **Regime / concentration** — earlier 7d losses clustered on JUP/INJ/ADA; toxic-regime buckets in screen-13 had WR ~55–59% even under band math.

A prior lucky stretch near ~46–49% WR still lost money (TP dollars < SL dollars) — so “WR looked better” never equaled “profitable.”

## 4. Turnover and fee death amplify the hole

High-frequency small-target styles pay round-trip fees/slip repeatedly. Empirically, 65% WR scalpers still lose when avg win cannot cover avg loss + fees ([BearBullRadar](https://www.bearbullradar.com/blog/botlab-fee-death)). Retail cost-floor analyses put break-even gross edge often in the **0.10–0.25%+** per trade range depending on turnover ([retired.today](https://retired.today/blog/minimum-edge-trading)). AccBand’s `min_tp_pct` tries to keep TP above round-trip costs, but when **loss size remains ~2.5× win size**, costs only widen the deficit.

Crypto perp case study of high WR + catastrophic loss asymmetry (profit factor ≪ 1) matches the shape of SL-dominated books ([Medium / Liu 2026](https://medium.com/@gwrx2005/winning-the-battle-and-losing-the-war-loss-asymmetry-tail-concentration-and-the-case-for-29624327f6be)).

## 5. What is *not* the root cause

| Hypothesis | Verdict |
|------------|---------|
| Missing RSI/MACD/EMA confluence | **Refuted** (ledger); dual-model scout 26_ STOP’d classic indicators |
| Bot “not trading enough” | False — UTC-day already 45 closes; more trades without edge = faster bleed |
| Single HALT bug still open | Latch cleared; maker provenance rehydrate shipped; currently not halted |
| AccBand “broken” because WR ≠ 63% | Geometry needs near-no-edge first-touch; live path has adverse MAE>MFE |
| Profit will appear once WR hits band | **Falsified by today’s W/L sizes** (still negative at 59–67%) |

## Key Takeaways

1. **Binding failure = negative after-cost expectancy**, not a dashboard WR cosmetic issue.
2. **With current win/loss sizes, breakeven WR ≈ 72%** — above the owner band — so band WR alone cannot deliver profit.
3. **AccBand was never claimed to create edge**; screen-13 and 30d lanes already measured that.
4. Fixing “59–67% and profitable” requires a **new positive-edge source** (carry/basis-style or vetted veto that improves *expectancy*), or a **payoff redesign** that accepts lower WR for larger winners — not installing refuted indicators or loosening gates to fake WR.
5. Near-term honesty: report status as `NEGATIVE_AFTER_COST_ECONOMICS`; treat WR-in-band as geometry research, not a profit SLA.

## Sources

### Local
1. `data/goal_progress.json` / `data/heartbeat.json` / `data/warehouse.sqlite` — live lanes and exit mix (this session).
2. `_workspace/strategy_pipeline/21_loss_autopsy.md` — earlier SL>TP dollar asymmetry, fill-type bleed.
3. `config.py` AccBand block — WR-by-geometry honesty contract.
4. Screen-13 / CLAUDE.md pipeline notes — all AccBand buckets after-cost negative; ~−0.24R directional expectancy.
5. `_workspace/strategy_pipeline/25_deep_research_bot_methodology_2026-07-23.md` — infrastructure ≠ edge deficit.

### External
6. [Why High Win Rate Strategies Lose Money](https://curvedtrading.com/articles/en/trading/expected-value-trading-high-win-rate-losing-money/) — negative-skew high-WR trap.
7. [65% Win Rate, Still Losing: Fee Death](https://www.bearbullradar.com/blog/botlab-fee-death) — fee drag vs small wins.
8. [Minimum edge that survives commissions](https://retired.today/blog/minimum-edge-trading) — cost floor vs turnover.
9. [Crypto Signal Expectancy & Costs](https://cryptosignalsreview.com/crypto-signal-risk-reward/) — 70% WR / 0.4R win / 1.2R loss → negative net expectancy.
10. [Stop-Loss and Take-Profit Optimization](https://www.varrd.com/guides/stop-loss-optimization.html) — high WR can worsen profit factor.
11. [Risk reward ratio crypto trading](https://chartscout.io/risk-reward-ratio-crypto-trading) — WR × R:R interaction.
12. [Loss asymmetry in crypto perps](https://medium.com/@gwrx2005/winning-the-battle-and-losing-the-war-loss-asymmetry-tail-concentration-and-the-case-for-29624327f6be) — high WR, ruinous loss tail.

## Methodology

Sub-questions: (1) What do live exits say about WR vs dollars? (2) Is AccBand supposed to produce profit? (3) What WR is required for break-even given observed W/L? (4) What does external expectancy/cost literature say? (5) Which popular “fixes” are already refuted?

Queries: high win-rate negative expectancy; fee death 65% WR; minimum edge commissions slippage; asymmetric TP/SL WR vs profit factor. Local SQL + goal lanes + prior autopsy. Deep-read: CurvedTrading, CryptoSignalsReview, Varrd, ChartScout, Liu case study.
