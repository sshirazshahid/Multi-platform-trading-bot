# 26 ΓÇö Scout (Codex): Indicators + Order Book + Trades + Network

*Date: 2026-07-23 | Stage: research/scout only | No code, harvest, probes, screens, simulations, or wiring performed*

## Bottom line

The classical indicators named by the owner do not create a new strategy queue. MA, EMA, MACD, RSI, Bollinger Bands, Parabolic SAR, Average Volume Level, SuperTrend, raw volume, KDJ, OBV, and Williams %R are stopped by the binding ledger rows cited below. Combining, retuning, changing timeframe, or applying them separately to every coin would re-litigate refuted families.

Order-book depth and individual trades have one narrow adjacent use: execution-risk or entry-veto conditioning, not standalone direction. The exact quarter-hour aggressor-flow expression was locally refuted today. Network/on-chain flow is genuinely open, but lacks durable local point-in-time history and sufficient after-cost futures evidence.

At a $420 account, the honest autonomous-bot portfolio remains the validated delta-neutral F1 carry pipeline plus log-only event probes. Indicator-based direction remains unfit after costs.

## Binding novelty table

| Requested family | Intended expression | Verdict | Binding row cite / disposition |
|---|---|---:|---|
| MA | SMA cross, slope, price/MA | **REFUTED** | `Textbook trend/breakout` ΓÇö 8 strategies ├ù 5 majors, **0/40 OOS** (2026-06-13); `Formulaic alphas` ΓÇö 443+, best IRΓëê0.45 pre-cost (2026-05-25). **STOP.** |
| EMA | EMA cross, ribbon, pullback | **REFUTED** | Same rows; `Pullback-momentum MA20/RSI14` is owner-directed shadow only and explicitly not reopened (2026-07-22). **STOP.** |
| MACD | Line, signal, histogram | **REFUTED** | `Indicator-confluence stacks` ΓÇö refuted on own data (2026-06-08); `Formulaic alphas` (2026-05-25). **STOP.** |
| RSI | Overbought/oversold, crossing | **REFUTED** | `RSI mean-reversion (any timeframe)` ΓÇö 5 coins ├ù 3 years, NO_EDGE (2026-06). Momentum/confluence use also hits the 2026-06-08 row. **STOP.** |
| BOLL | Band MR, squeeze, breakout | **REFUTED** | `Textbook trend/breakout` explicitly includes BB squeeze, 0/40 OOS (2026-06-13). **STOP.** |
| SAR | Parabolic SAR reversal/trend | **REFUTED** | `Textbook trend/breakout` and `Formulaic alphas` (2026-06-13/05-25). **STOP.** |
| AVL | Average Volume Level/divergence | **REFUTED** | Treated as Average Volume Level. A bar-derived directional trigger is a `Formulaic alpha`; stacking it hits `Indicator-confluence` (2026-05-25/06-08). **STOP.** |
| SUPER | SuperTrend | **REFUTED** | Explicitly named in `Textbook trend/breakout (supertrend, Dow swing, Asian range, BB squeeze)`, 0/40 OOS (2026-06-13). **STOP.** |
| VOL | Bar volume, relative volume | **REFUTED** | Directional volume rules are `Formulaic alphas`; confirmation stacks are `Indicator-confluence` (2026-05-25/06-08). Trade-level toxicity is separated below. **STOP as entry alpha.** |
| KDJ | Stochastic K/D/J oscillator | **REFUTED** | Oscillator MR maps to `RSI mean-reversion`; trend confirmation maps to `Indicator-confluence` (2026-06/06-08). **STOP.** |
| OBV | Cumulative signed bar volume | **REFUTED** | `Formulaic alphas` and `Indicator-confluence` (2026-05-25/06-08). **STOP.** |
| WR | Williams %R oscillator | **REFUTED** | Oscillator MR maps to `RSI mean-reversion`; confirmation maps to `Indicator-confluence` (2026-06/06-08). **STOP.** |
| Order-book depth | Depth imbalance, spread, walls, slippage | **ADJACENT** | Standalone short-horizon direction is adjacent to `Formulaic alphas` and `$1ΓÇô2 scalping` ΓÇö no edge after costs (2026-05-28). A fill/slippage veto is a distinct non-alpha expression. |
| Trades/tape | Aggressor imbalance, arrival intensity, VPIN | **ADJACENT** | Exact `Quarter-hour opening aggressor imbalance` row is **NO_GO**: best aligned OOS ΓêÆ18.5 bps versus +20 bps bar (2026-07-23). Directional VPIN is adverse; jump-risk veto remains queued in report 23. |
| Network | Exchange flows, active addresses, whale/entity flow | **OPEN** | No matching ledger refutation, but local continuous point-in-time history and a costed futures screen are absent. Existing evidence is association/regime evidence, not autonomous directional edge. |

No classical-indicator row is reopened. A recent cost-aware BTC study is adverse rather than qualifying: its 24-hour momentum baseline produced **ΓêÆ45.93% annualized net at 10 bps**, and its technical-indicator increment did not survive Holm correction under 168-hour blocks ([Bysik & ┼Ülepaczuk 2026](https://arxiv.org/html/2606.00060v1)).

## Local per-pair/coin data reality

ΓÇ£For each pair/coinΓÇ¥ is not presently supportable for every requested data family. Computable indicators are not equivalent to validated signals.

| Data family | Local path and coverage | Scout conclusion |
|---|---|---|
| OHLCV indicators | `core/features.py`, `core/feature_store.py`, existing OHLCV caches and warehouse | Broadly computable per eligible pair, but screening them is barred by the cited rows. More columns would add trials, not evidence. |
| Live order-book depth | `core/data_feeds/orderbook_depth_feed.py`; current futures depth fetched through `core/mcp_brain.py` | Live snapshots exist. No discovered durable, multi-year, sequence-correct depth archive exists per pair. |
| Trades | `data/aggtrades_qh/BTCUSDT_qh_events.parquet`, `ETHUSDT_qh_events.parquet`, `manifest.json`; harvester precedent `scripts/harvest_binance_aggtrades_qh.py` | Only BTC/ETH quarter-hour extracts are local. They support the failed C3 expression, not full-session VPIN or all-pair tape research. |
| Network/smart money | `core/data_feeds/smart_money_feed.py` supplies current Binance Web3 ranking context with a 15-minute cache | Current ranks are not a historical, point-in-time, entity-adjusted exchange-flow panel. No honest OOS simulation is possible. |
| Funding/OI context | `data/funding_history/`, `data/funding_cache/`, `data/premium_index/`, `data/funding_oi/` | Useful context exists, but directional funding and OI-divergence are refuted. Valid use remains F1 carry and risk accounting. |

## Screen-worthy queue ΓÇö maximum two

These are research briefs only. Neither authorizes a screen, probe, or bot change.

### 1. VPIN/trade-toxicity entry veto ΓÇö ADJACENT, strongest

- **Mechanism:** Estimate volume-bucket order-flow toxicity and veto or reduce an already approved entry during elevated jump/adverse-selection risk. It predicts risk, not direction, and must be compared with the unchanged baseline lane.
- **Local data path:** BTC/ETH extracts under `data/aggtrades_qh/` prove aggTrades access but are insufficient because VPIN requires continuous full-session trades.
- **Cost at $420:** The veto creates no additional order. Using report 25ΓÇÖs 2/5.5 bps per-side assumptions, a 1├ù-account round trip costs approximately **$0.17 maker/maker** or **$0.46 taker/taker**; at 1.5├ù gross, approximately $0.25/$0.69, before spread, slippage, and funding.
- **Expectation:** **NO_GO expected**; plausible WR/drawdown protection, not new positive EV. Peer-reviewed evidence supports VPIN as a jump/volatility predictor, while the directional practitioner variant decayed to ΓêÆ15.6 bps net, as recorded in reports 23/24.
- **Harvest if starved:** Under separate authorization, retain continuous Binance Futures aggTrades for BTC/ETH first, including exchange timestamps, aggressor side, price, quantity, trade ID, gaps, and listing boundaries under a new immutable `data/aggtrades_vpin/` manifest. Do not expand to every coin before a two-major kill-first screen.

### 2. Order-book liquidity/adverse-selection veto ΓÇö ADJACENT, lower priority

- **Mechanism:** Use pre-decision spread, top-N depth, imbalance, estimated market impact, and staleness to skip entries whose expected fill/slippage or adverse selection exceeds gross edge. Extreme walls must not be interpreted directionally because cancellations and spoofing make snapshots fragile.
- **Local data path:** Live metrics are available through `core/data_feeds/orderbook_depth_feed.py`, including `depth_ratio_log` and slippage estimates. Adequate historical sequences are absent.
- **Cost at $420:** No incremental trade is created. The same $0.17/$0.46 1├ù round-trip fee floor applies to allowed trades, plus realized spread and slippage. Small capital does not solve queue risk: maker orders may remain unfilled or fill precisely when informed flow arrives.
- **Expectation:** **NO_GO as directional alpha; low-probability GO only as execution/risk protection.** A 2026 five-asset Binance Futures study finds portable one-second book/trade feature shapes using time-series cross-validation and maker/taker backtests, but its result is latency- and queue-sensitive and does not establish $420 retail feasibility ([Bieganowski & ┼Ülepaczuk 2026](https://arxiv.org/abs/2602.00776)). A 2025 Bybit study reports OOS classification but insufficient cost evidence for a strategy ([Wang 2025](https://arxiv.org/abs/2506.05764)).
- **Harvest if starved:** Only after a preregistration decision, store synchronized incremental futures-book updates and snapshots, sequence IDs, exchange and receive timestamps, gaps/resyncs, trades, and contract metadata under a proposed `data/orderbook_depth_history/`. Start with BTC/ETH. Snapshots lacking sequence and latency fields are not valid execution evidence.

### Network flow ΓÇö OPEN but not screen-worthy

- **Mechanism:** Point-in-time, entity-adjusted exchange netflows could serve as a slow BTC/ETH risk regime or event flag. Active addresses, fees, and transaction volume are more plausible state variables than precise futures triggers.
- **Local data path:** `core/data_feeds/smart_money_feed.py` supplies current-rank context only.
- **Cost at $420:** A veto adds no exchange order, but a commercial historical API subscription would be disproportionate to $420 unless free, licensed, and reproducible. Allowed trades still incur fees, slippage, and funding.
- **Expectation:** **INSUFFICIENT_DATA / NO_GO expected** for direction. Recent peer-reviewed evidence links on-chain/off-chain variables to Bitcoin market efficiency, but uses 2014ΓÇô2022 BTC data and does not demonstrate after-cost perpetual-futures execution ([British Accounting Review 2026](https://www.sciencedirect.com/science/article/pii/S0890838925000915)).
- **Harvest if starved:** First identify a source preserving timestamp-of-availability, entity-label revisions, chain reorganizations, and licensing rights. Land BTC/ETH-only hourly/daily observations under a proposed `data/network_flows/` manifest. If point-in-time revisions or licensing cannot be archived, classify **INFEASIBLE**.

## Autonomous-bot fitness after costs

| Family | Signal half-life | Operational burden | $420 fitness | Honest role |
|---|---:|---:|---:|---|
| Classical indicators | HoursΓÇôdays | Low | **Unfit** | Refuted; no screen or wiring |
| Directional book/tape alpha | MillisecondsΓÇôminutes | Very high | **Unfit** | Cost/latency-sensitive; exact C3 flow expression failed |
| VPIN/depth veto | MinutesΓÇôhours | Medium/high | **Marginal** | May protect an existing lane; cannot manufacture edge |
| Network flow | HoursΓÇôdays | High integrity/licensing burden | **Not ready** | Slow regime/event context after point-in-time harvest |
| F1 delta-neutral funding carry | Settlements/days | Multi-venue and counterparty controls | **Best available** | Only validated family; already in PAPER soak |
| Event supply shorts | Weeks/event time | Sparse events and severe tail controls | **Shadow only** | Log-only unlock/listing probes |

## Planning consequence

1. Refuse all indicator-strategy, confluence, SuperTrend, candlestick, formulaic-alpha, and unqualified scalping installations under the cited ledger rows.
2. If the pipeline later authorizes one preregistration, rank the already queued **VPIN jump-risk veto** first. Treat the depth veto as a competing overlay within the same multiplicity budget.
3. Do not harvest every pair first. Use BTC/ETH kill-first acquisition and expand only after a preregistered result clears after-cost delta, coverage, DSR/PBO, OOS-WR/AUC, and capital-preservation gates.
4. Research never grants autonomous authority: confirmed screen ΓåÆ log-only shadow ΓåÆ at least 30 resolved forward events ΓåÆ frozen promotion gate ΓåÆ explicit owner sign-off.

## Sources and scope

1. [Bysik & ┼Ülepaczuk, *Machine Learning-Based Bitcoin Trading Under Transaction Costs* (2026)](https://arxiv.org/html/2606.00060v1) ΓÇö walk-forward, 10 bps, block bootstrap, and Holm correction; adverse indicator/cost anchor.
2. [Bieganowski & ┼Ülepaczuk, *Explainable Patterns in Cryptocurrency Microstructure* (2026)](https://arxiv.org/abs/2602.00776) ΓÇö five Binance perpetuals, one-second order book/trades, time-series CV, maker/taker backtests.
3. [Wang, *Exploring Microstructural Dynamics in Cryptocurrency Limit Order Books* (2025)](https://arxiv.org/abs/2506.05764) ΓÇö Bybit BTC order-book OOS prediction; insufficient cost evidence.
4. [Kim & Hansen, *The Quarter-Hour Effect* (2026)](https://arxiv.org/abs/2607.09426) ΓÇö six Binance perpetuals and Bybit robustness; its local C3 implementation is nevertheless a binding NO_GO.
5. [*The dual impact of on-chain and off-chain factors on Bitcoin market efficiency* (2026)](https://www.sciencedirect.com/science/article/pii/S0890838925000915) ΓÇö association/regime evidence, not a costed futures strategy.

The binding ledger and local evidence dominate external novelty claims. Reports `23_candidate_queue_2026-07-23.md`, `24_deep_research_futures_2026-07-23.md`, and `25_deep_research_bot_methodology_2026-07-23.md` were read and reconciled. No probe or empirical result was generated.
