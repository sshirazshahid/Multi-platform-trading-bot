# Futures Strategies That Actually Work: Deep-Research Report
*Generated: 2026-07-23 | Sources: 21 | Confidence: High (consensus findings), Medium (vendor/practitioner numbers)*

Owner request: "/deep-research Futures trading strategies that actually works! Simulate, Test then
wire it to the trading bot." Routed through the strategy-evidence-pipeline: this document is the
scout/research stage. Simulate/test = pre-registered screens under frozen gates; wire = log-only
shadow probes unless the promotion gate passes. No firecrawl/exa MCP available; native web search
used. Sub-questions: (1) what survives costs per rigorous 2025-26 evidence; (2) directional
TA/ML viability; (3) delta since our 2026-07-22 sweep; (4) event-driven evidence; (5) evidence
for the two queued briefs (VPIN, cross-sectional MR).

## Executive summary

The external evidence base and our own ~2,400 pre-registered local tests converge on the same
answer: **after-cost, the only repeatedly-certified crypto-futures strategy families are
delta-neutral carry/basis structures — funding-rate carry and quarterly cash-and-carry basis —
plus (fragile, event-driven) supply-shock shorts on newly-listed tokens.** Directional technical
analysis, ML forecasters, cross-sectional momentum, and microstructure alpha all show the same
pattern in 2025-26 studies: positive gross, dead-to-negative net of 10–28 bps costs, with
documented year-over-year alpha decay where an edge briefly existed. Nothing found this week
meets the reopen bar for any refuted family. One genuinely useful find: a 236-event unlock study
that materially **corroborates and refines our live unlock-short shadow probe** (the effect is
concentrated in newly-listed tokens; established tokens show no effect).

## 1. What actually works after costs (external consensus)

- **Delta-neutral funding carry** is the recurring certified survivor. An adversarial-validation
  practitioner audit of 28 "validated" deployments found exactly one strategy class surviving
  Deflated-Sharpe + five-tier cost stress: "a delta-neutral funding-rate strategy… positive in
  every calendar year 2021–2026… boring, capacity-limited, and real"
  ([strategy-edge-audit](https://github.com/matt-tokarz/strategy-edge-audit)). A cross-sectional
  funding-carry book on Hyperliquid reports Sharpe 1.69 net over 2024-08→2026-07
  ([Keel](https://usekeel.io/strategies/funding-carry)) — vendor backtest, not peer-reviewed.
- **BUT the premium is compressing**: the peer-reviewed asset-class study finds the BTC carry
  Sharpe fell from 6.45 full-sample to ~4 in 2024 and **negative in 2025**
  ([arXiv 2510.14435](https://arxiv.org/abs/2510.14435)) — matching our F1 incumbent's measured
  0-entries/49k-checks idleness. An independent research repo reports funding carry "bled ~−1.2
  Sharpe in recent OOS" and promotes only **quarterly cash-and-carry basis** (~3.2%/yr unlevered
  after costs, OOS Sharpe ~2.9, 2024-06→2026-06)
  ([crypto-carry-research](https://github.com/boyam01/crypto-carry-research)).
- **Market making + funding capture** is where new academic profitability claims concentrate
  (RL market maker, 24.6% ann/Sharpe 1.49 on BTCUSDT hourly with fee-aware quoting —
  [JFDS 2026](https://doi.org/10.1016/j.jfds.2026.100197)) — but it requires quoting infrastructure,
  latency, and inventory control we do not have; the paper itself lists latency constraints as
  unresolved before production.

**Local mapping:** F1 carry = already our validated family (in PAPER soak, regime-idle, correctly
so given compression). Quarterly basis = screened locally (`08c_screen_basis_swap.md`: ETH
leg-swap variant CONFIRMED_NO_GO on MC P>0 = 0.683; BTC infeasible at capital). At $420 account,
3.2%/yr unlevered ≈ $13/yr — real but immaterial; not re-opened. Market making = infeasible
(latency/infra), not queued.

## 2. Directional TA / ML / timeframe evidence (all adverse)

- Hourly ML forecasting (XGBoost/LSTM/iTransformer, 27-fold walk-forward, 2018–2026): "naive
  sign-based strategies fail once transaction costs of ten basis points are imposed"; only
  cost-aware execution filters restore profitability "in selected configurations" — a
  selection-bias caveat the authors themselves flag
  ([arXiv 2606.00060](https://arxiv.org/html/2606.00060v1)) — already our adverse anchor.
- Minute-level microstructure features (6 majors, Aug 2025–Feb 2026, strict leakage controls):
  "**no trading strategy based on these 5-min forecasts survives standard Binance exchange fees
  and slippage**"; models don't transfer across coins
  ([Frontiers in Blockchain 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full)).
- HFT ML under full cost modeling: "significant disconnect between model prediction accuracy and
  actual net returns… simple cost-based filtering rules beat deep-architecture iteration"
  ([J. European Academy OU 2026](https://ojs.shiharr.com/index.php/eaou/article/view/1672)).
- **Timeframe implication:** the same cost math that killed our C3 pilot (this morning) and the
  hourly-momentum anchor applies across 5m–1h. No candlestick timeframe manufactures edge;
  4h remains the cost-amortization anchor for research probes. (Also: 45m is not a Binance interval.)

## 3. Delta since 2026-07-22 (our last sweep)

- **Quarter-hour effect** (arXiv 2607.09426): our C3 pilot — closed **NO_GO today** — is, to our
  knowledge, the first cost-aware out-of-sample replication attempt: best aligned residual cell
  **−18.5 bps** vs the +20 bps bar on 2026-Q2 data. The paper's own framing ("more useful for
  execution/liquidity timing than standalone profit") is consistent with our kill.
- Multi-pair trading with deep RL ([arXiv 2606.04574](https://arxiv.org/html/2606.04574v2)):
  pairs/cointegration family (locally refuted: 435 pairs, 0 FDR survivors) + RL execution;
  outperformance significant only at the **10% level** — does not meet the reopen bar.
- Robust HF market making ([arXiv 2607.08291](https://arxiv.org/abs/2607.08291v1)): theory;
  latency-infeasible.
- [AutoQuant](http://arxiv.org/abs/2512.22476): execution-constrained configuration-selection
  framework — a process/validation tool ("not a strategy generator"), philosophically aligned
  with our pipeline; nothing to wire.

**Zero new screen-eligible directional edges in the delta — the expected outcome.**

## 4. Event-driven: the one materially useful find

[Tokenomist/unlocks.app 236-event study](https://tokenomist.ai/research/do-token-unlocks-crash-prices)
(Jun 2024→Mar 2026, BTC-subtracted returns, matched-peer + placebo controls):

- Pre-event drift −14.7% median over the month before unlock (p<0.001, n=164) — **the risk window
  is BEFORE the unlock**, matching our probe's W1 (T−28d) / W2 (T−14d) entry design.
- Matched-peer causal estimate −4.85% (p=0.02), but split: **established tokens −2.57% (p=0.42,
  no effect) vs newly-listed −16.02% (p=0.03); age-matched ≤120d −14.8% (p=0.001)** — the effect
  is a young-token phenomenon.
- Large non-insider unlocks −26% (descriptive) — matches our probe's ≥10%-of-mcap non-insider filter.
- Corroborating: 46/52 Binance unlock events negative in the 72h window, mean −16.97%,
  Bonferroni-robust across 17 tests
  ([vibe-investing replication](https://github.com/gameworkerkim/vibe-investing/tree/main/01.Trading%20Strategy/Token%20unlock%2072h%20shock%20analysis%20)).
- Honesty note: the widely-shared "token_unlock_dilution" backtest (CAR −15.05%, WR 77.3%,
  maxDD −86.6%, [Medium](https://medium.com/coinmonks/i-backtested-shorting-token-unlocks-heres-why-i-m-not-trading-it-yet-42e237d40d9a)/[MEXC](https://www.mexc.com/news/1018427))
  is the SAME study already on our ledger row (Tigro Blanc numbers) — republication, not
  independent replication. Do not double-count.

**Action:** evidence update recorded on the unlock-short ledger row (below). The frozen probe is
NOT re-tuned (never re-tune post-outcome); the age-conditioning insight is noted for a possible
future pre-registered W-variant only after the current arms resolve ≥30 events.

## 5. Queued briefs — evidence now attached

- **VPIN (order-flow toxicity):** peer-reviewed evidence supports VPIN as a **jump/volatility
  predictor** ([RIBAF Jan 2026](https://www.sciencedirect.com/science/article/pii/S0275531925004192)),
  NOT directional alpha: the Frontiers study measures "relatively weak predictive power" for its
  VPIN proxy, and a practitioner walk-forward shows the directional VPIN overlay **decaying to
  −15.6 bps net (t=0.96) in 2026, BTC-only, bull-months-only**
  ([MEXC microstructure study](https://www.mexc.co/news/1002105)). Brief narrowed accordingly:
  only the **veto-overlay expression** (jump-risk filter for the band lane) remains queued;
  directional expression is anchored-adverse. Expectation NO_GO.
- **Cross-sectional MR/momentum:** the realistic-assumptions study (Binance futures, actual
  fees/ticks/slippage, margin modeling) finds cross-sectional evidence "weak" — 5/21 portfolios
  LIQUIDATED in-sample, short legs eaten by jump risk
  ([SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)); vol-managed
  variants show gross payoffs only pre-cost on wide universes
  ([FMPM 2025](https://link.springer.com/article/10.1007/s11408-025-00474-9)). At $420, a
  multi-leg L/S basket also fails min-notional feasibility. Brief stays queued with expectation
  NO_GO; kill condition likely at the feasibility stage.

## Key takeaways

1. **Nothing new to wire.** The honest wired instruments remain: F1 carry (validated, regime-idle),
   7 log-only shadow probes, and the band-geometry PAPER lane. External evidence this week
   *strengthens* the unlock-short probe's design and *weakens* the case for everything directional.
2. **Simulate/test next:** the VPIN-veto screen is the strongest queued candidate (data tooling
   ready via the C3 aggTrades harvester); one heavy screen per UTC day — today's was C3, so the
   earliest run is tomorrow, prereg-first.
3. **Wire criteria unchanged:** CONFIRMED_GO screen → log-only shadow probe → ≥30 resolved
   forward events → frozen promotion gate (DSR≥0.10, PBO≤0.5, OOS-WR≥0.55, AUC≥0.60) → owner
   sign-off. No shortcut exists that isn't a documented way to lose money.

## Sources

1. [strategy-edge-audit](https://github.com/matt-tokarz/strategy-edge-audit) — adversarial validation of 28 deployments; sole survivor = delta-neutral funding carry
2. [arXiv 2510.14435](http://arxiv.org/abs/2510.14435) — carry Sharpe 6.45 full-sample → negative 2025 (peer-grade)
3. [Keel funding carry](https://usekeel.io/strategies/funding-carry) — cross-sectional carry Sharpe 1.69 net, Hyperliquid (vendor)
4. [crypto-carry-research](https://github.com/boyam01/crypto-carry-research) — quarterly basis ~3.2%/yr unlevered after costs; funding carry regime-bleed (practitioner, adversarially reviewed)
5. [JFDS 2026 RL market making](https://doi.org/10.1016/j.jfds.2026.100197) — MM+funding capture Sharpe 1.49; latency unresolved
6. [arXiv 2606.00060](https://arxiv.org/html/2606.00060v1) — hourly ML dies at 10 bps; cost-aware filter caveat (standing adverse anchor)
7. [Frontiers in Blockchain 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full) — nothing at 5-min survives Binance fees; no cross-coin transfer
8. [J. European Academy OU 2026](https://ojs.shiharr.com/index.php/eaou/article/view/1672) — prediction-accuracy vs net-return disconnect
9. [arXiv 2607.09426](https://arxiv.org/html/2607.09426) — quarter-hour effect (C3 source paper; our pilot NO_GO)
10. [arXiv 2606.04574](https://arxiv.org/html/2606.04574v2) — pairs+DRL, 10%-level significance only
11. [arXiv 2607.08291](https://arxiv.org/abs/2607.08291v1) — HF MM robustness theory
12. [AutoQuant, arXiv 2512.22476](http://arxiv.org/abs/2512.22476) — execution-constrained validation framework
13. [Tokenomist unlock study](https://tokenomist.ai/research/do-token-unlocks-crash-prices) / [unlocks.app mirror](https://insights.unlocks.app/do-token-unlocks-crash-prices/) — 236 events; newly-listed concentration
14. [vibe-investing 72h unlock analysis](https://github.com/gameworkerkim/vibe-investing/tree/main/01.Trading%20Strategy/Token%20unlock%2072h%20shock%20analysis%20) — 46/52 negative, Bonferroni-robust
15. [Medium unlock backtest](https://medium.com/coinmonks/i-backtested-shorting-token-unlocks-heres-why-i-m-not-trading-it-yet-42e237d40d9a) / [MEXC republication](https://www.mexc.com/news/1018427) — same study as ledger's Tigro Blanc row (not independent)
16. [RIBAF Jan 2026 VPIN/jumps](https://www.sciencedirect.com/science/article/pii/S0275531925004192) — VPIN predicts jumps (volatility), peer-reviewed
17. [MEXC VPIN practitioner study](https://www.mexc.co/news/1002105) — directional VPIN overlay decayed to −15.6 bps net 2026
18. [SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565) — cross-sectional momentum weak under realistic assumptions; liquidation risk
19. [FMPM 2025 momentum moments](https://link.springer.com/article/10.1007/s11408-025-00474-9) — vol-managed momentum, pre-cost, wide universe
20. [Carry Trade docs, tradingstrategy.ai](https://tradingstrategy.ai/docs/learn/carry-trade.html) — DEX carry upper bounds; funding-rate DAR forecasting context
21. [The Crypto Carry (Gerbil)](https://gerbil.life/papers/CarryTrade.v1.2.pdf) — early-era carry Sharpe 8.76; exchange-bankruptcy risk haircut

## Methodology

6 search queries across academic (arXiv/SSRN/ScienceDirect/Springer/Frontiers), practitioner
(GitHub research repos, vendor backtests), and news sources; 8 full texts pulled for deep reads.
Sub-questions: after-cost survivors; TA/ML viability; delta since 07-22; event-driven; VPIN;
cross-sectional MR. Every candidate checked against the refuted-families ledger before routing.
Single-source claims are labeled (vendor/practitioner); republications de-duplicated (source 15).
