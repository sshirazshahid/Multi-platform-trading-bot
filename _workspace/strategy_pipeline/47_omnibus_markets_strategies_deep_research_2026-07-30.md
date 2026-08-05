# Omnibus Markets / Strategies Deep Research + Improvement Pass
*Generated: 2026-07-30 | Sources: 12+ external + local ledger/runtime | Confidence: High (local wiring); Medium (external yield levels)*

## Executive Summary

Winning more trades by loosening AccBand / EconGate / MCP score floors would **increase fill count while destroying expectancy**. External and local evidence agree: retail directional TA and most spot oscillator stacks are after-cost −EV; the durable crypto class that survives costs is **funding / cash-and-carry**, and even that edge has **compressed sharply since 2024** (Sharpe turned negative in 2025 in the arXiv stylized-fact series). This pass therefore treated “make it win” as **unblock the only validated family when edge exists**, retire proven NO-PROMOTE probes, and keep refusing −EV directional opens.

Highest-leverage ship this session: **F1 `feeds_fresh` was permanently false** due to snapshot wiring (early `received_at` + Binance spot `timestamp=None`), so any positive-funding window that cleared economic gates still died on `feeds_stale`. That is fixed and regression-tested. Pullback probe retirement is wired into `.env` / `.env.example`.

## 1. Runtime honesty (local audit)

| Surface | State | Implication |
|--------|--------|-------------|
| PAPER + MAX_FLOW_BAND | EntryFloor 66, EconGate **strict**, AccBand on | Directional idle is correct (−EV refuse) |
| Open positions | 0; `last_trade_time=null` | No live bleed from AccBand |
| F1 gate log (24h) | ~4.3k checks, **0 ok** | Regime-idle + (pre-fix) freshness bug |
| Reject families (24h) | funding≤0 ~26%, time window ~15%, trailing mean≤0 ~9%, feeds_stale ~7% | Economics dominate; freshness was a silent veto |
| Pullback probe | Dual-agree **RETIRE / NO-PROMOTE** | Stop accrual |
| Listing / unlock | IDLE / calendar OK, 0 actionable cliffs | Viability clock still binds (Week-2 check 2026-08-13) |

## 2. Futures (long/short) — what survives after cost

### Validated / keep
- **F1 delta-neutral carry** (long spot + short perp when funding>0, trailing mean>0, contango, spread/depth/time windows): only ledger-validated live profit family. Gate thresholds must **not** be loosened.
- Academic carry: He et al. show perpetual basis arb can deliver high Sharpes historically ([arXiv 2212.06888](https://arxiv.org/html/2212.06888v6)); Liu et al. stylized fact 6: crypto carry Sharpe **6.45 full sample → 4.06 from 2024 → negative in 2025** ([arXiv 2510.14435](https://arxiv.org/html/2510.14435v4)). Idle F1 today is consistent with compressed funding, not a dead runner.
- Practical guides (Kraken, Finder, Hedonist): carry only when funding persistently clears all-in RT cost; maker execution and flip exits matter ([Kraken](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage), [Finder](https://finder-arbitrage.com/blog/funding-arbitrage)).

### Refuted / do not reopen without new hashed prereg
- Textbook trend / breakout / TSMOM (ledger).
- Pullback MA20/RSI14 (forward probe NO-PROMOTE 2026-07-30).
- Liq-cascade, VPIN veto, clamp-print-as-edge, C1 CFTC options-pressure, wrapper-discount, F1-percentile-selectivity, stablecoin-depeg (pipeline CONFIRMED_NO_GO / measurement-only).

### Mechanism note (prediction / accuracy)
- AccBand WR is **geometry**, not edge. Raising ALLOW count via ATR loosen or `paper_fallback` is explicitly out of scope (plan premises A).

## 3. Spot — what survives after cost

- Pre-registered Binance spot study: **no simple retail strategy beats buy-and-hold BTC OOS** after realistic costs ([chocotrader-research](https://github.com/cbenitezpy/chocotrader-research); [Medium write-up](https://chocopy.medium.com/i-tried-to-beat-buy-and-hold-bitcoin-for-80-hours-here-is-the-honest-result-217f802edf8a)).
- StratProof 10-day paper of 22 popular strategies with real fees+L2: **16/22 lost**; survivors clustered in RSI mean-reversion on liquid pairs only — and that family is multiplicity-fragile ([StratProof](https://stratproof.com/blog/paper-trading-22-strategies-real-fees)). Local ledger already treats RSI/indicator confluence as REFUTED for promotion.
- Retail microstructure sweep: **no signal cleared ~0.13% RT cost** at intraday–daily horizons ([retail-crypto-alpha](https://github.com/Mykola-Quant/retail-crypto-alpha)).

**Inference (labeled):** Spot directional TA should stay research/telemetry only. Spot’s role in this bot is the **long leg of F1 carry**, not a second AccBand book.

## 4. News / feeds / “insider” / tips

| Input | Role today | Improvement stance |
|-------|------------|--------------------|
| `news_scanner` / sentiment feeds | Context for MCP / risk narrative | Keep advisory; never sole entry authority |
| Twitter feed (new) | Research telemetry | Same — no live OPEN without pipeline GO |
| Funding history / HL harvest | F1 regime + measurement | Keep cadence; T6 harvest exit codes already shipped |
| Tips / influencer “alpha” | Not in decision path | Correct — narrative ≠ prereg |

No change that promotes news→OPEN without a hashed screen.

## 5. Root cause fixed this pass (F1 freshness)

Live diagnose (Binance BTC):
- Spot book `timestamp=None` → `spot_observed_at=None` → snap age = ∞ → `stale=True`.
- `received_at` stamped **before** funding fetch; harvester `ts=time.time()` after → funding looked **future** → `funding.age_sec=inf` → `funding.stale=True`.
- Result: `feeds_fresh_rate=0` on ~4k logged rows with the key present.

**Fix** (`scripts/run_f1_carry_paper.py`):
1. Stamp `received_at` after all network fetches.
2. Fallback observation clock for REST polls missing exchange timestamps to receipt time.
3. Regression test `test_live_snapshot_provider_rest_poll_without_spot_ts_stays_fresh`.

**Measurement** (`mcp_server/warehouse_reader.py`): `f1_edge_status` now exposes `feeds_fresh_rate`, `top_reject_families`.

## 6. Prioritized improvement plan (refuse −EV)

| Priority | Action | Status |
|----------|--------|--------|
| P0 | Fix F1 feeds_fresh wiring | **SHIPPED** (needs F1 runner restart) |
| P0 | Retire pullback probe (`SHADOW_PULLBACK_PROBE_ENABLED=false`) | **SHIPPED** in `.env` + `.env.example` (needs attended bot restart) |
| P1 | Do not reopen AccBand/`paper_fallback`/EntryFloor&lt;66 | Hold |
| P1 | Week-2 viability call (2026-08-13): F1 episodes / unlock / listing | Pending calendar |
| P2 | Listing/unlock deploy readiness (Track D) | Prior work; continue only if accrual path moves |
| P3 | News/Twitter as scored veto research | Only with new prereg — not this pass |

## Key Takeaways

1. **Refuse −EV is winning** for directional AccBand under strict EconGate.
2. **F1 is the only live profit path**; it was incorrectly blocked by a freshness bug even when economics might clear — fix unblocks true positive-funding episodes without lowering thresholds.
3. External research + local ledger both say: do not chase spot/TA “more trades.”
4. Owner-attended restart still required for: honesty scorer labels (46_), pullback retire, F1 provider fix (carry process).

## Sources

1. [arXiv 2510.14435 — Cryptocurrency as an Investable Asset Class](https://arxiv.org/html/2510.14435v4) — carry compressed / negative Sharpe 2025.
2. [arXiv 2212.06888 — Fundamentals of Perpetual Futures](https://arxiv.org/html/2212.06888v6) — perpetual pricing + historical arb Sharpes.
3. [Kraken — Funding rate arbitrage](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage) — cost vs persistence.
4. [Finder — Funding arbitrage](https://finder-arbitrage.com/blog/funding-arbitrage) — deployed-capital APR vs fees.
5. [StratProof — 22 strategies real fees](https://stratproof.com/blog/paper-trading-22-strategies-real-fees) — 16/22 −EV after L2+fees.
6. [chocotrader-research](https://github.com/cbenitezpy/chocotrader-research) — spot OOS no-beat B&H.
7. [retail-crypto-alpha](https://github.com/Mykola-Quant/retail-crypto-alpha) — microstructure −EV after ~13 bps RT.
8. Local: `data/carry_gate_log.jsonl`, refuted-families ledger, plan `2026-07-30-profitability-improvement-loop.md`, artifacts 45_/46_.

## Methodology

Searched web (Firecrawl/Exa MCP unavailable this session → WebSearch + WebFetch). Sub-questions: (1) futures carry after-cost, (2) spot/TA after-cost, (3) local F1/AccBand blockers, (4) news/feed role, (5) highest-leverage shippable fix. Deep-read arXiv HTML + StratProof article. Live-probed Binance/Bybit funding + F1 snapshot provider.

## Explicit non-actions

- No AccBand ATR loosen, no EconGate `paper_fallback`, no EntryFloor below 66.
- No new directional TA probe without dual-agree + hashed prereg.
- No CONTROLLED_LIVE.
- No unattended bot restart (standing rule).
