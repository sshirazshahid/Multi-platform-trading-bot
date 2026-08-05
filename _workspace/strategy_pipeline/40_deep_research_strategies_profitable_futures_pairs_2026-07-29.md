# Strategy Development for Profitable Futures Pairs (Long & Short)
*Generated: 2026-07-29 | Sources: 22 | Confidence: High (ledger/local), Medium (external practitioner yields)*

**Goal:** Decision support — which strategies to *develop* for liquid USDT-M perps L&S on this bot’s FIT / major universe. Not a live build. Routed via `strategy-evidence-pipeline` Phase-1 (scout) + deep-research. Firecrawl/Exa MCP unavailable; WebSearch/WebFetch + local ledger/queue used.

**Pair context (prior report):** [`37_deep_research_profitable_futures_pairs_2026-07-29.md`](37_deep_research_profitable_futures_pairs_2026-07-29.md) — FIT_BAND_PAPER = ALGO, ARB, AVAX, ETH, LINK; liquidity backbone BTC/ETH/SOL; COST_UNFIT BNB/TRX; warehouse n≥10 all net-negative on MCP path.

## Executive Summary

Developing “profitable pair strategies” cannot mean inventing new RSI/breakout/TSMOM stacks for ALGO–LINK — those families are **ledger-REFUTED** or already log-only with NO-PROMOTE expectation. The only **validated** live-pipeline family remains **F1 delta-neutral funding carry** (long spot / short perp when funding persistently positive; flip when persistently negative), currently **structurally idle** under 2026 funding compression. AccBand MCP on FIT pairs is **WR-research geometry**, not a profit strategy (`AccBand frac dual-goal` CONFIRMED_NO_GO). The honest development plan is: (1) keep F1 remediation gated on after-cost edge revival; (2) accrue queued **event** strategies (C2 gamma-expiry fade L/S on BTC; liquidation-cascade reversion); (3) let CONFIRMED-GO **shadow shorts** (listing / unlock) resolve ≥30 events; (4) do **not** promote TA directional on FIT coins. Pair×strategy matrix below.

## 1. Binding constraints (read first)

| Constraint | Implication for “develop strategies” |
|------------|--------------------------------------|
| Refuted-families ledger | RSI-MR, textbook breakout/trend, TSMOM long-only, confluence, candlesticks, Kalman pairs, directional funding, ML forecasters, hour seasonality, grid/DCA, AccBand-for-profit, VPIN directional, QH imbalance → **STOP** unless reopen bar met ([ledger skill](.claude/skills/refuted-families-ledger/SKILL.md)) |
| AccBand dual-goal NO_GO | Cannot “tune frac to get WR *and* EV>0” on measured no-edge path (`30_screen_accband_frac_dual_goal`) |
| Capital ~$420 | Wide cross-sectional L/S baskets fail min-notional / concurrency ([23_candidate_queue](23_candidate_queue_2026-07-23.md); SSRN 4675565 weak CS mom + liquidations) |
| Promotion | Nothing live from research alone — screen → shadow → frozen gate + owner sign-off |
| Edge queue (binding order) | C2 accrual → liq cascade → OI×funding veto → HL F1 conditioner → F1 remediation ([30_edge_queue](30_edge_queue_2026-07-23.md)) |

## 2. Strategy families worth developing (eligible)

### S1 — F1 / basis carry (VALIDATED — develop *remediation*, not a new idea)

- **Mechanism:** Delta-neutral cash-and-carry: long spot + short perp to collect positive funding; reverse when funding persistently negative ([BackQuant basis trade](https://www.backquant.com/learn/basis-trade); [Kraken funding arb](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage); Binance USDⓈ-M arb evaluation PDF).
- **Long vs short:** **Short perp** is the income leg in +funding regimes; **long perp** only under persistent −funding with spot short/hedge.
- **Symbols:** BTC, ETH first (deepest books); expand only when net edge clears costs. FIT alts secondary (spread + funding instability).
- **Novelty:** VALIDATED incumbent — **not** NEW.
- **Develop next:** Gate-log proof of ≥30 positive net-edge episodes; Hyperliquid hourly funding as **conditioner** (queue #4); do **not** force-enable under compression (~2.7% ann cross-venue spread vs 10–28 bps RT — [Bitsgap Q2-2026 cited in 32_](32_deep_research_futures_2026-07-24.md)).
- **Expected failure:** Funding flip / venue / liquidation on short leg ([Echo Zero basis risks](https://blog.echozero.app/article/basis-trade-risk-and-reward-in-crypto-derivatives-markets)).
- **Action:** **QUEUE remediation** — SCREEN only when harvest shows edge > cost.

### S2 — C2 Deribit gamma-expiry fade (NEW — prereg frozen, accruing)

- **Mechanism:** Fade pre-expiry BTC move into daily 08:00 UTC Deribit expiry on ATM-OI>p90 ∧ GEX<0 days; ~90–100 min hold (Weiss et al. FRL 2026; prereg [`33_prereg_c2_gamma_expiry`](33_prereg_c2_gamma_expiry.md)).
- **Long vs short:** **Short** if pre-expiry move >0; **long** if <0 (fade). Follow arm = multiplicity control.
- **Symbols:** **BTCUSDT** perp only (Binance expression).
- **Novelty:** NEW / peer-reviewed mechanism; reopen path from 18_ adjudication.
- **Data:** `data/deribit_chain_snapshots/` — screen at ≥30 events/conditioned cell.
- **Expected failure:** Reversal magnitude < RT costs (lean-NO_GO prior).
- **Action:** **ACCRUE → SCREEN** (do not invent substitute 08:00 reversal without options conditioning).

### S3 — Liquidation-cascade / OI-flush reversion (NEW — harvest first)

- **Mechanism:** Forced liquidations are price-insensitive; post-cascade overshoot mean-reverts over 2–10h ([arXiv 2603.09164 Slippage-at-Risk](https://doi.org/10.48550/arxiv.2603.09164); [ADL clustering arXiv 2512.01112](https://arxiv.org/abs/2512.01112); practitioner anecdotes in [32_](32_deep_research_futures_2026-07-24.md)).
- **Long vs short:** **Long** after aggressive **long** flush; **short** after aggressive **short** flush (define from forceOrder flow — must **not** collapse to refuted OI-divergence).
- **Symbols:** BTC/ETH majors + separate **alt-flush** variant (ARB/AVAX/LINK/ALGO as alt basket — pre-register separately).
- **Novelty:** NEW unscreened mechanism; zero rigorous after-cost strategy backtest found.
- **Data:** `data/liquidations_history.jsonl` (collector live again 2026-07-25); stress in-event costs 30–60 bps.
- **Expected failure:** Undercounted forceOrder feed + blowout spreads at signal time → NO_GO (~25% prior).
- **Action:** **ACCRUE → NEW prereg** (Codex cross-check) before any screen.

### S4 — Listing-short & unlock-short (CONFIRMED-GO shadow — develop *resolution*, not new code)

- **Mechanism:** Event shorts around listing pumps / pre-unlock cliffs (capital-scaled 3%/12% unlevered).
- **Long vs short:** **Short-only** expressions (by design).
- **Symbols:** Event-driven (not FIT ranking); calendar/listing coverage.
- **Novelty:** ALREADY-IN-SHADOW (pipeline GO as log-only).
- **Develop next:** Forward ≥30 RESOLVED/arm; young-token unlock conditioning = **NEW prereg only** after arms resolve (ledger fragility: 2023 net-negative unlocks).
- **Action:** **ALREADY_PROBED** — wait for gate; no MCP wire.

### S5 — OI×funding joint regime classifier (ADJACENT — veto only)

- **Mechanism:** Internal **veto** refinement for band/carry lanes (like `BAND_REGIME_FILTER`) — never a directional OPEN signal (OI-divergence stays REFUTED).
- **Long vs short:** Blocks bad entries both sides; does not choose side.
- **Symbols:** FIT_BAND_PAPER + BTC/ETH.
- **Novelty:** ADJACENT to band-regime work; NEW as joint OI×funding prereg.
- **Action:** **QUEUE brief** behind C2/liq accrual.

### S6 — Hyperliquid funding as F1 timing conditioner (F1-ADJACENT)

- **Mechanism:** Use HL hourly funding as data signal only (cannot trade DEX here) to gate CEX F1 entries ([32_](32_deep_research_futures_2026-07-24.md)).
- **Long vs short:** Conditions when to run short-perp carry.
- **Symbols:** BTC/ETH overlapping HL/CEX.
- **Action:** **QUEUE** cheap screen vs `data/carry_gate_log.jsonl` when F1 revival evidence appears.

## 3. Explicit STOP list (blogs will suggest these — ledger kills)

| Idea | Ledger / evidence | Verdict |
|------|-------------------|---------|
| RSI / MA / MACD / SuperTrend / BB / confluence entries on FIT coins | RSI-MR, confluence, breakout 0/40 OOS | **STOP** |
| “Tune AccBand / TP frac until profitable” | AccBand dual-goal 0/12 cells | **STOP** |
| Long-only or 20d TSMOM as new live strategy | TSMOM REFUTED; probe already log-only NO-PROMOTE | **STOP** (already probed) |
| Breakout-60d / Donchian as live | Breakout REFUTED; probe log-only | **STOP** (already probed) |
| Pullback MA20+RSI55 live install | REFUTED; probe 0/12 wins accruing | **STOP** |
| Directional funding / OI-divergence OPEN | IR 0.248 / NO_EDGE | **STOP** |
| Cross-sectional L/S momentum basket | Weak after costs; liquidations; capital-infeasible ([SSRN 4675565](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565); [FMPM momentum moments](https://link.springer.com/article/10.1007/s11408-025-00474-9) fails reopen for our gates) | **STOP / park** |
| Cross-exchange lead-lag at taker | Dead at retail RT ([32_ refusals](30_edge_queue_2026-07-23.md)) | **STOP** |
| Extreme-funding microcaps (BLAST/LA/…) as “APR shorts/longs” | Liquidity/squeeze trap ([Quantority](https://quantority.com/insights/funding-extremes-2026-07-21)) | **STOP** |
| VPIN directional / θ retune without NEW prereg | CONFIRMED_NO_GO 2026-07-25 | **STOP** |

## 4. Pair × strategy development matrix

| Base | Role | Develop | Do not develop |
|------|------|---------|----------------|
| **ETH** | FIT + liquidity | F1 carry when edge returns; AccBand PAPER accrual only (WR research); unlock/listing if evented | TA directional “ETH alpha” |
| **LINK, ARB, AVAX, ALGO** | FIT_BAND_PAPER | AccBand PAPER soft-priority; alt-flush **variant** of S3 only after prereg; never cost-inflate TP for profit | RSI/breakout stacks; force short because “FIT” |
| **BTC** | Liquidity + events | **Primary** for S2 C2 fade L/S; F1; liq-cascade major variant | Untimed 08:00 reversal without GEX/ATM-OI |
| **SOL** | Liquidity (FIT_WITH_GAPS) | F1 secondary; liq major; AccBand with gaps disclosed | Promote on narrative momentum |
| **BNB, TRX** | COST_UNFIT | — | Any new directional strategy |
| **FET** | EXCLUDE | — | Until Bybit route exists |

**Warehouse reminder:** Recent closed sample — FIT names still bled under MCP; developing strategies ≠ claiming historical MCP PnL proves edge.

## 5. Long vs short playbook (operational)

| Regime / signal class | Prefer | Notes |
|-----------------------|--------|-------|
| Persistent +funding, basis healthy | **Short perp** (+ long spot) | F1 / basis — only validated income path ([BackQuant](https://www.backquant.com/learn/basis-trade)) |
| Persistent −funding | **Long perp** (+ short spot hedge) | Rarer; flip carefully |
| Pre-expiry BTC up into conditioned Deribit day | **Short** BTC (fade) | S2 only after ≥30 events |
| Pre-expiry BTC down into conditioned day | **Long** BTC (fade) | S2 |
| Post long-liquidation cascade | **Long** reversion | S3 — after prereg |
| Post short-liquidation cascade | **Short** reversion | S3 |
| Listing pump / unlock cliff | **Short** event | S4 shadow only |
| AccBand / MCP directional | Either side if signal says | Geometry WR research — **not** profit claim; funding costs punish longs in +funding bulls ([StratBase funding impact](https://stratbase.ai/en/blog/crypto-futures-backtesting)) |

## 6. Recommended development sequence (owner actions)

1. **Do nothing to MCP for “profit strategies”** — AccBand stays research geometry; FIT soft-priority already shipped.
2. **Let clocks run:** Deribit snaps (C2), liquidations JSONL (S3), unlock/listing probes (S4).
3. **Next heavy screen day (when ≥30 C2 conditioned events):** run hashed C2 screen + honesty audit — expectation lean-NO_GO.
4. **If F1 gate-log shows ≥30 positive net-edge episodes:** remediation screen + optional HL conditioner.
5. **Never:** reopen RSI/breakout/TSMOM/AccBand-profit without reopen-bar citation.

## Key Takeaways

- **Develop income + event L/S**, not indicator stacks on “profitable” FIT coins.
- **Only validated strategy class:** F1 basis carry — currently idle; remediation ≠ force-on.
- **Best directional L/S candidates still accruing:** C2 BTC fade; liq-cascade reversion; shadow listing/unlock shorts.
- **FIT_BAND_PAPER (ALGO/ARB/AVAX/ETH/LINK)** = AccBand PAPER research basket + future alt-flush variant — **not** a proven alpha set.
- **Promotion remains owner-signed** after frozen gates; this report authorizes **no** live path change.

## Sources

1. Local ledger — `.claude/skills/refuted-families-ledger/SKILL.md`
2. Local queues — `30_edge_queue_2026-07-23.md`, `23_candidate_queue_2026-07-23.md`
3. Local futures research — `32_deep_research_futures_2026-07-24.md`, `24_deep_research_futures_2026-07-23.md`
4. Local pairs — `37_deep_research_profitable_futures_pairs_2026-07-29.md`, `18_final_pair_verdicts.json`
5. C2 prereg — `33_prereg_c2_gamma_expiry.md` + Weiss et al. FRL 2026
6. [BackQuant — Basis trade BTC/ETH](https://www.backquant.com/learn/basis-trade)
7. [Kraken — Funding rate arbitrage](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage)
8. [Echo Zero — Basis trade risk](https://blog.echozero.app/article/basis-trade-risk-and-reward-in-crypto-derivatives-markets)
9. [StratBase — Futures backtesting & funding drag](https://stratbase.ai/en/blog/crypto-futures-backtesting)
10. [SSRN 4675565 — TS/CS momentum under realistic assumptions](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=4675565)
11. [FMPM 2025 — Cryptocurrency momentum has (not) its moments](https://link.springer.com/article/10.1007/s11408-025-00474-9)
12. [arXiv 2603.09164 — Slippage-at-Risk / cascades](https://doi.org/10.48550/arxiv.2603.09164)
13. [arXiv 2512.01112 — ADL clustering](https://arxiv.org/abs/2512.01112)
14. [Quantority — Funding extremes 2026-07-21](https://quantority.com/insights/funding-extremes-2026-07-21)
15. Binance USDⓈ-M arbitrage evaluation (Contentful PDF) — portfolio-margin basis notes
16. Permissionless spot–perp basis (arXiv-class 2605.05089 extract) — DeFi collateral control (context only; not our venue)

## Methodology

- **Sub-questions:** (1) Which strategy classes clear the ledger for FIT/major futures L&S? (2) What is already queued/probed? (3) What external evidence supports carry vs event vs momentum? (4) How to map long vs short by regime? (5) What must be refused?
- **Pipeline phase:** Phase-0 context check (workspace exists; prior pair report 37_); Phase-1 scout synthesis (strategy-scout Agent blocked by API limit — main-loop fallback per pipeline error protocol). **No Phase-2 screen** this pass (no new hashed prereg outcomes).
- Searched ~10 web queries; deep-read basis trade + prior local 32_/30_/33_; cross-checked momentum literature against ledger STOP.
