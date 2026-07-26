# Buy-After-Pullback (close>MA20 ∧ RSI14>55): Research Report
*Generated: 2026-07-23 | Sources: 14 | Confidence: High*

## Executive Summary

**Verdict: STOP — do not wire to live or AccBand PAPER entries.** This exact family is already on the binding ledger as refuted (textbook trend / RSI / confluence) with an owner-directed **log-only** shadow probe already running. Your pseudocode is a *weaker* variant of that probe (level condition `RSI>55` vs event `RSI cross↑55`; no SMA50>SMA200 gate). Forward accrual so far: funnel lane `pullback_ma20_4h` **7/30 resolved, WR 0.0**. External peer-reviewed walk-forward evidence shows naive momentum on BTC hourly **+31.16% gross → −45.93% net** at 10 bps costs ([arXiv 2606.00060](https://arxiv.org/abs/2606.00060)). Blog “high WR” RSI+MA stories do **not** meet the reopen bar (no FDR/DSR multiplicity, often survivorship / low n / wrong family).

## Spec under review

```text
// Entry
if close > ma(20) and rsi(14) > 55: enter_long()
// Exit
if rsi(14) > 70 or close < ma(20): exit_long()
```

| Dimension | Your rules | Bot probe (`pullback_ma20_rsi14_4h_v1`) |
|-----------|------------|----------------------------------------|
| Entry | State: close>SMA20 ∧ RSI>55 | Event: SMA50>SMA200 ∧ close>SMA20 ∧ RSI **cross↑** 55 |
| Exit | RSI>70 or close<SMA20 | Same + 1.5×ATR stop + 42-bar time stop |
| Orders | Implied live | **Log-only** shadow only |
| Status | — | ACCRUING 7/30, WR 0.0 (expectation NO-PROMOTE) |

Your level-gate fires on every bar while RSI stays >55 above the MA → **higher turnover** than the cross event → worse cost death, not better edge.

## 1. Ledger binding (local — authoritative)

Already classified:

- **Refuted parents:** RSI mean-reversion (any TF, 2026-06); textbook trend/breakout 0/40 OOS (2026-06-13); indicator-confluence stacks (2026-06-08); long-only TSMOM no profit edge (2026-06-15).
- **In shadow (not reopened):** Pullback-momentum MA20/RSI14 row dated 2026-07-22 — explicit **NOT a pipeline GO**; reopen bar not met.
- Dual-model indicator scout 2026-07-23 (`26_*`): both models **STOP** on MA/EMA/RSI as entry signals; pullback probe is the honest forward instrument only.

Reopen bar requires peer-reviewed (or equivalent) 2025+ evidence with genuine OOS, FDR/DSR-grade multiplicity, and after-cost retail crypto accounting. Meeting it earns a **SCREEN**, never a live build.

## 2. Adverse external anchor (meets rigor; kills naive momentum)

Bysik & Ślepaczuk, *Machine Learning-Based Bitcoin Trading Under Transaction Costs* ([arXiv 2606.00060](https://arxiv.org/abs/2606.00060); HTML [v1](https://arxiv.org/html/2606.00060v1)):

- ~70k hourly BTC/USDT bars, 2018–2026, **27-fold walk-forward**, Holm-corrected block bootstrap.
- Table 4 **24h Momentum** benchmark: ARC **+31.16%** at 0 cost → **−45.93%** at **0.1%** (10 bps) costs; ~5984 trades; Sharpe collapses negative.
- Authors’ thesis: prediction-to-trading gap — naive sign/momentum rules die on **turnover**, not only weak forecasts.

**Inference:** A state-based `close>MA20 and RSI>55` long with `RSI>70 / close<MA20` exits is the same failure class (frequent regime flicker around MA20 and RSI thresholds). Unverified claim of “this exact threshold set works after costs” was **not** found in peer-reviewed form.

## 3. Why the popular bullish backtests do not reopen the family

| Source | Claim | Why it fails reopen bar |
|--------|-------|-------------------------|
| [Coinquant RSI settings](https://www.coinquant.ai/blog/rsi-trading-strategy-best-settings-for-crypto-backtested) | RSI+HMA positive on BTC 4h 2020–24 | Vendor blog; parameter variants without FDR; not this MA20/55/70 spec |
| [Coinquant RSI+HMA 85.7% WR](https://www.coinquant.ai/blog/rsi-hma-on-btc-backtest-results-2020-2024-857-win-rate-7-trades-full-data) | +31.8% / 7 trades / 5y | **n=7** — unusable for DSR/PBO; wrong exit family |
| [Boring Edge RSI range-momentum](https://boringedge.com/bitcoin-rsi-range-momentum-strategy-backtest/) | 83% WR, 6 trades, 75d window | Different rule; **n=6**; lookback optimized on same asset |
| [YouTube 6047% RSI](https://www.youtube.com/watch?v=jvKNDZ0ucSA) | 4h multi-coin huge return | No peer review; optimization + marketing; not reproducible under our gates |
| [Quant Signals — RSI fails](https://quant-signals.com/rsi-trading-strategy/) | 2397 trades; crypto MR negative | Supports **STOP** on RSI-as-edge for crypto |
| [Boring Edge RSI 30/70 MR](https://boringedge.com/bitcoin-rsi-mean-reversion-strategy-backtest/) | −5.3% CAGR vs B&H | Supports **STOP** on classic RSI exits |
| [Vantixs RSI range shift](https://vantixs.com/blog/rsi-range-shift-crypto-bots) | Exit at 70 cuts trends early | Explains why `rsi>70` exit **caps** winners in crypto bulls |

## 4. Structural mismatch with the owner 59–67% WR + profit band

- AccBand profit failure research (`28_*`): compressed TP needs ~72% WR to break even with current W/L sizes.
- This strategy’s natural R:R is **undefined / path-dependent**; exits often cut winners at RSI 70 while losers run to MA break — classic **small win / larger loss** shape unless a hard stop dominates (probe adds 1.5×ATR; your pseudocode has **no stop**).
- High bar-frequency state entries inflate fee drag (same mechanism as arXiv cost death).

## 5. What is already implemented (do nothing more)

- Code: `core/agents/pullback_momentum_probe_agent.py`
- Funnel: `pullback_ma20_4h` — ACCRUING **7/30**, wins **0**, WR **0.0**, η≈23d to floor
- Promotion: only if ≥30 resolved **and** frozen `promotion_gate` **and** owner sign-off — expectation **NO-PROMOTE**

Wiring your level-gate into MCP / order_manager would violate the ledger and duplicate a worse probe.

## Key Takeaways

1. **STOP** — family refuted; reopen bar not met by this deep-research pass.
2. Honest instrument already exists: **log-only** `pullback_ma20_rsi14_4h_v1` (stricter than your snippet).
3. Strongest rigorous external evidence **adverse** to naive momentum after costs ([2606.00060](https://arxiv.org/abs/2606.00060)).
4. Marketing backtests with n≈6–7 or YouTube returns do not authorize a screen or live path.
5. Next useful work is **not** another MA/RSI install — wait for ≥30 probe resolutions, or pursue ADJACENT non-indicator candidates (e.g. VPIN veto) under the evidence pipeline.

## Sources

1. [arXiv 2606.00060](https://arxiv.org/abs/2606.00060) — walk-forward BTC; momentum +31.16% → −45.93% after 10 bps.
2. [arXiv HTML v1 Table 4](https://arxiv.org/html/2606.00060v1) — exact momentum ARC numbers.
3. `.claude/skills/refuted-families-ledger/SKILL.md` — pullback shadow row + refuted parents.
4. `core/agents/pullback_momentum_probe_agent.py` — frozen probe spec.
5. `data/promotion_funnel.json` — pullback_ma20_4h 7/30 WR 0.0.
6. `_workspace/strategy_pipeline/26_*` — dual-model STOP on indicators.
7. `_workspace/strategy_pipeline/28_deep_research_wr_band_loss_2026-07-23.md` — WR-band vs expectancy.
8–14. Coinquant, Boring Edge, Quant Signals, Vantixs, YouTube, Bitcoin Foundation RSI overview — cited above as non-reopen or adverse.

## Methodology

Sub-questions: (1) Is this new vs ledger? (2) Does peer-reviewed after-cost evidence support reopen? (3) Do vendor backtests meet reopen bar? (4) What is live forward status? (5) Should anything be implemented?

Firecrawl/Exa MCP were not available in this environment; used web search + full arXiv HTML fetch + local ledger/funnel/probe. Queries covered MA20/RSI55 pullback, crypto after-cost momentum, RSI failure OOS.
