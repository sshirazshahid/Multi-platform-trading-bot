# Profitable Futures Pairs (Long & Short): Research Report

*Generated: 2026-07-29 | Sources: 18 | Confidence: Medium*

**Goal defaults (user: just research it):** decision-support for this bot’s PAPER futures universe — which coins are *tradeable*, which have *structural carry*, and which the local adjudication already ranks for AccBand research. Not investment advice.

## Executive Summary

There is no credible public list of “guaranteed profitable” crypto perpetual pairs for directional long/short trading. What the market *does* show clearly is a **liquidity hierarchy** (BTC / ETH / SOL dominate volume and open interest), a **funding/carry** income channel that is delta-neutral and after-cost fragile, and **extreme micro-cap funding** that is a squeeze/liquidity trap—not an edge. Against this bot’s own warehouse (last ~800 closed trades), **every symbol with n≥10 is net negative** after realized PnL; the least-bleed names are not the same as the dual-model `FIT_BAND_PAPER` set. For AccBand PAPER research under the current profile, the locally agreed priority bases remain **ALGO, ARB, AVAX, ETH, LINK**; avoid **BNB/TRX** (`COST_UNFIT`) and **FET** (`EXCLUDE`). Long vs short: the book is heavily long-biased historically; shorts on high-churn names (e.g. JUP, ATOM, DOGE) also bled—side alone is not alpha.

## 1. Liquidity ≠ profitability

External market structure (2025–2026) is unambiguous on *where* size can trade, not on *who* makes money directionally:

- Perp CEXes processed **>$85.3T** volume in 2025; perp DEXes **~$6.2–6.4T**, with DEX share of perp volume ~**10%** by Apr 2026 ([CoinGecko 2026 State of Crypto Perpetuals Report](https://www.coingecko.com/research)).
- **Asset concentration:** despite many listings, volume/OI still dominated by **BTC + ETH**; BTC alone averaged ~$69B/day in 2025 and was still ~**50%** of CEX perp volume by end-Apr 2026; ETH share peaked ~57% (Aug 2025) then ~**36%** by Apr 2026; next tier charted as SOL / DOGE / XRP ([CoinGecko](https://www.coingecko.com/research)).
- Venue depth: Binance remains the liquidity benchmark (~**30% of BTC futures OI**); Bybit is a top perp venue with deep BTC/ETH books ([Datawallet 2026 futures exchange review](https://www.datawallet.com/crypto/best-crypto-futures-exchanges); [Bitcoin Foundation Binance vs Bybit 2026](https://bitcoinfoundation.org/news/altcoins/crypto-exchange-battle-2026-binance-vs-bybit-where-do-traders-prefer-to-trade/)).
- Snapshot ranking on major CEX perps (Loris Tools, mid-2026 snapshots): **BTC / ETH / SOL** lead 24h volume and OI on both Binance and Bybit ([Bybit markets](https://loris.tools/markets/exchanges/bybit); [Binance markets](https://loris.tools/markets/exchanges/binance)).
- BTC funding was positive ~**83%** of days over the CoinGecko window, with a prolonged **negative** stretch in Apr 2026 before recovering ([CoinGecko](https://www.coingecko.com/research)) — side of carry flips with regime.

**Inference (labeled):** Prefer these majors for *execution quality* (spread, depth, funding continuity). Do **not** infer positive expectancy from volume alone.

## 2. Directional long/short: what “profitable” actually means after costs

Directional retail/perp strategies face:

- Round-trip fees + slippage that easily eat sub-0.5% TPs (this bot’s AccBand geometry lives near that edge).
- Regime dependence (ADX / vol filters already measured locally as WR headwinds).
- Multiplicity / selection bias in “best pair” blog lists (unverified single-source claims discarded).

Cross-sectional momentum literature still reports modest Sharpe with large drawdowns after costs (Delphi / academic anchors in prior pipeline work)—**not** a pair-picker that clears this bot’s frozen promotion gates.

**Local warehouse evidence (this repo, ~800 most recent closed trades):**

| Tier | Finding |
|------|---------|
| Hard fact | **0 symbols with n≥10 have positive total realized PnL** |
| Least negative (n≥10) | MSTR, HBAR, FIL, AAVE, BCH (still net loss) |
| High activity bleeders | LINK, ARB, ADA, SOL, ETH, XRP, AVAX (large n, negative) |
| Side skew | Book is **long-dominated**; shorts also negative where sampled (JUP S=38/−7.70, ATOM S=18/−7.48, DOGE S=16/−8.89) |
| TP vs SL | Several high-n names show SL counts ≫ TP (e.g. AVAX tp=1/sl=27; ADA tp=6/sl=35) |

**Inference:** Historical MCP/PAPER path did **not** discover a profitable directional pair set. Use warehouse as a *loss map*, not a winner list.

## 3. Funding / carry: the only widely cited “structural” income (not MCP directional)

When funding is **persistently positive**, the textbook delta-neutral trade is **long spot + short perp** (longs pay shorts). When funding is **persistently negative**, the flip is **long perp + short spot** ([Kraken funding-rate arb explainer](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage); [Echo Zero](https://blog.echozero.app/article/funding-rate-arbitrage-between-perpetual-and-spot-markets); [Finder arb notes](https://finder-arbitrage.com/blog/funding-arbitrage)).

Reality checks from industry + academic sources:

- Fees on four legs + hold time often require **days** of elevated funding to break even ([Finder](https://finder-arbitrage.com/blog/funding-arbitrage); MDPI two-tier funding study: many opportunities fail a conservative exit protocol after ~$220 RT costs on illiquid names — [MDPI 2026](https://www.mdpi.com/2227-7390/14/2/346)).
- Marketing APR ranges (e.g. ~8–18% calm / much higher in short bull windows) are **condition-dependent** and compress fast ([Arbitrage Scanner guide](https://arbitragescanner.io/blog/crypto-funding-rate-arbitrage-strategy-guide)).
- This bot’s F1 carry gate has been structurally idle / after-cost weak in prior pipeline audits — treat carry as a **separate product**, not a free upgrade to MCP OPEN.

**Long vs short carry map (structural, not directional):**

| Funding regime | Income side on perp | Hedge |
|----------------|---------------------|-------|
| Positive (longs crowded) | **Short** perp collects | Long spot |
| Negative (shorts crowded) | **Long** perp collects | Short spot / equivalent |

## 4. Extreme funding coins: do not confuse APR with profit

As of **2026-07-21**, Quantority flagged micro-cap perps with extreme **negative** annualized funding (BLAST −3376%, LA −3310%, DEXE −2971%, ERA −1913%, etc.) with small OI and high leverage-risk scores ([Quantority funding extremes](https://quantority.com/insights/funding-extremes-2026-07-21)). Publisher labels this as **descriptive positioning, not a trade recommendation**.

**Inference:** Extreme carry on thin books is typically a **liquidity/squeeze hazard**. Illiquid OI ($1–60M class) + exploding OI into negative funding (ERA/LA) is the opposite of “profitable pair” for this bot’s risk rails.

## 5. This bot’s dual-model pair fitness (2026-07-22)

Source: `_workspace/strategy_pipeline/18_final_pair_verdicts.json` (Codex + Fable; conditionality: PAPER + MAX_FLOW_BAND).

| Verdict | Bases | Role |
|---------|-------|------|
| **FIT_BAND_PAPER** (5) | **ALGO, ARB, AVAX, ETH, LINK** | Best local fit for AccBand PAPER accrual / priority OPEN soft-rank |
| **FIT_WITH_GAPS** (22) | AAVE, ADA, APT, ATOM, BCH, BTC, DOT, ETC, FIL, INJ, LTC, MANA, NEAR, OP, RENDER, SAND, SEI, SOL, SUI, TIA, UNI, VET | Tradeable with data/funding/OHLCV gaps; not excluded |
| **DATA_STARVED** (14) | 1INCH, COMP, CRV, DASH, ENA, GALA, GRT, HBAR, JUP, ONDO, SNX, TAO, XRP, ZEC | Do not promote on narrative |
| **COST_UNFIT** (2) | **BNB, TRX** | Move/cost fails local cost screen |
| **EXCLUDE** (1) | **FET** | No live Bybit USDT-perp route at audit |

**Honesty:** FIT_BAND_PAPER is **fitness for band research**, not proven after-cost edge. Warehouse PnL for those five is still negative in the recent closed sample (LINK/ARB/AVAX/ETH among larger bleeders; ALGO smaller n, still negative).

## 6. Practical long & short shortlist (decision map)

### A. Directional PAPER (AccBand / MCP path) — research priority

- **Primary bases:** ALGO, ARB, AVAX, ETH, LINK (both long *and* short allowed by product; do not force side from blogs).
- **Liquidity backbone (execution):** BTC, ETH, SOL — use for depth; expect FIT_WITH_GAPS / cost sensitivity on BTC.
- **Deprioritize / avoid:** BNB, TRX (cost), FET (route), micro-cap extreme-funding names (BLAST/LA/DEXE/ERA class).

### B. Side bias (evidence-based, weak)

- Historical bot fills are **long-heavy**; that is a **process skew**, not proof longs are better.
- Where shorts were tried in size (JUP/ATOM/DOGE), shorts **also lost** — no warehouse short-alpha claim.
- Funding-informed *overlay* (not MCP authority): prefer **short** when funding is persistently positive *and* you are running carry-style hedges; prefer **long** only under persistent negative funding with a hedge — else stay directional-neutral on funding.

### C. What would change this read

- Warehouse cohort under AccBand + cost-clearance (`ACCURACY_MIN_TP_COST_PCT≥0.35`) flipping to positive expectancy on a base with n≥30 RESOLVED.
- Shadow probes clearing frozen promotion gates (DSR/PBO/OOS-WR/AUC) + owner sign-off.
- Funding backfill closing FIT_WITH_GAPS data holes for majors.

## Key Takeaways

1. **No external source proves a profitable directional L/S pair set for this bot** — liquidity leaders ≠ winners.
2. **Local warehouse: all n≥10 symbols net negative** in the recent closed sample; treat as a bleed map.
3. **Best local PAPER research basket:** ALGO, ARB, AVAX, ETH, LINK; avoid BNB/TRX/FET and micro-cap funding extremes.
4. **Structural income** is funding carry (delta-neutral), after-cost fragile — separate from MCP OPEN.
5. **Long vs short:** do not pick side from “profitable coins” lists; use regime + funding only as overlays, and demand after-cost evidence before any promotion.

## Sources

1. [CoinGecko 2026 State of Crypto Perpetuals Report](https://www.coingecko.com/research) — CEX/DEX perp volume, listings, OI landscape Jan 2025–Apr 2026.
2. [Datawallet: Best crypto futures exchanges 2026](https://www.datawallet.com/crypto/best-crypto-futures-exchanges) — Binance depth / Bybit ranking, fee/leverage context.
3. [Bitcoin Foundation: Binance vs Bybit 2026](https://bitcoinfoundation.org/news/altcoins/crypto-exchange-battle-2026-binance-vs-bybit-where-do-traders-prefer-to-trade/) — venue roles (spot depth vs perp flow).
4. [Loris Tools — Bybit perps](https://loris.tools/markets/exchanges/bybit) — BTC/ETH/SOL volume & OI snapshot.
5. [Loris Tools — Binance perps](https://loris.tools/markets/exchanges/binance) — BTC/ETH/SOL volume & OI snapshot.
6. [Quantority: Funding extremes 2026-07-21](https://quantority.com/insights/funding-extremes-2026-07-21) — micro-cap extreme negative funding + OI risk.
7. [Kraken: Funding rate arbitrage](https://www.kraken.com/learn/futures-trading-funding-rate-arbitrage) — delta-neutral mechanics + cost caveats.
8. [Echo Zero: Funding arb spot/perp](https://blog.echozero.app/article/funding-rate-arbitrage-between-perpetual-and-spot-markets) — rate flip / basis risks.
9. [Finder: Funding arbitrage](https://finder-arbitrage.com/blog/funding-arbitrage) — after-fee APR vs hold-time breakeven.
10. [Arbitrage Scanner: Funding arb guide](https://arbitragescanner.io/blog/crypto-funding-rate-arbitrage-strategy-guide) — illustrative APR bands under bull vs calm regimes.
11. [MDPI: Two-tiered funding rate markets (2026)](https://www.mdpi.com/2227-7390/14/2/346) — after-cost profitability fragile under forced exits.
12. Local: `_workspace/strategy_pipeline/18_final_pair_verdicts.json` — dual-model 44-pair futures adjudication.
13. Local: `data/warehouse.sqlite` trades (last ~800 closed) — per-base realized PnL / side split.
14. Local pipeline notes (CLAUDE.md harness log) — F1 carry idle / AccBand geometry ≠ edge.

## Methodology

- **Sub-questions:** (1) Which perps have deepest liquidity? (2) Is there after-cost directional L/S evidence by coin? (3) What does funding/carry imply for long vs short? (4) Which extreme-funding names are traps? (5) What does *this* bot’s adjudication + warehouse say?
- **Tools:** WebSearch + WebFetch (Firecrawl/Exa MCP **not available** in this Cursor session — coverage gap noted). Local SQLite + verdict JSON.
- **Queries:** ~8 web searches + 3 deep fetches (CoinGecko PDF extract, Quantority, exchange/liquidity pages) + academic/industry funding-arb pages.
- **Quality:** Every market claim cited; warehouse PnL treated as fact for this install; FIT_BAND_PAPER explicitly *not* labeled “profitable.”
