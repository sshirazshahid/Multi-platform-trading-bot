# 26 — Scout (Fable): Indicator / Order-Book / Network Suite (2026-07-23)

Phase 1A dual-model scout. Fable voice (Composer). Codex Sol scout runs in parallel
(`26_scout_codex_*`). Opus 4.8 requested by owner but **usage-limited** this month —
Fable substitutes as the Claude-side voice; both-agree rule still requires Codex.

**Owner ask:** MA EMA MACD RSI BOLL SAR AVL SUPER VOL KDJ OBV WR + Order-Book Depth +
Trades + Network, per-pair data, then plan/test/simulate for an autonomous futures bot.

**Ledger read first.** Bottom line: **almost the entire classical-indicator list is already
REFUTED on this bot's own data.** Marketing confluence blogs and a YouTube "153% SuperTrend+
MACD+RSI" clip do **not** meet the reopen bar (no FDR/DSR, no honest retail cost accounting,
selection bias). Order-book depth and trade tape have ADJACENT slivers already partially
tested (C3 NO_GO today; VPIN veto queued). Network/on-chain is OPEN but **data-starved /
paid-API gated** for rigorous after-cost screening.

---

## Per-family novelty-vs-ledger

| Token | Interpretation | Verdict | Binding cite / note |
|---|---|---|---|
| MA / EMA | Moving averages, crossovers, ribbons | **REFUTED** | Formulaic alphas (443+, IR≈0.45 pre-cost, 2026-05-25); textbook trend 0/40 OOS (2026-06-13); pullback MA20/RSI14 is **shadow probe only**, reopen bar NOT met (arXiv 2606.00060 adverse) |
| MACD | MACD line/signal/histogram | **REFUTED** | Formulaic alphas + indicator-confluence stacks (2026-06-08) |
| RSI | RSI(14) MR or momentum | **REFUTED** | RSI mean-reversion NO_EDGE (2026-06); rsi2_4h_cfg226 is shadow TRACKER, not validated |
| BOLL | Bollinger Bands MR / squeeze | **REFUTED** | Textbook trend/breakout includes BB squeeze (0/40 OOS); MR adjacency |
| SAR | Parabolic SAR | **REFUTED** | Textbook trend/breakout family |
| AVL | Likely ADX or avg-volume — ambiguous | **REFUTED as signal** | ADX already used as **regime veto** (`BAND_REGIME_FILTER` ADX>30) — WR protection, NOT edge (screen-13). Avg-volume alone = formulaic |
| SUPER | SuperTrend | **REFUTED** | Explicitly named in textbook trend/breakout row (0/40 OOS, 2026-06-13) |
| VOL | Volume / volume ratio | **REFUTED as entry** | Formulaic / confluence; volume already an MCP bonus condition — anti-predictive score history |
| KDJ | Stochastic KDJ | **REFUTED** | RSI-MR / oscillator family |
| OBV | On-Balance Volume | **REFUTED** | Formulaic alphas |
| WR | Williams %R | **REFUTED** | Oscillator / RSI-MR family |
| Order-Book Depth | Bid/ask depth, walls, depth_ratio | **ADJACENT** | Local feed exists (`core/data_feeds/orderbook_depth_feed.py`). Short-horizon OBI dies at fees ([Frontiers 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full): no 5-min strategy survives Binance fees). Possible **execution/veto overlay** only — same class as VPIN-veto queued in `23_candidate_queue` |
| Trades / tape | aggTrades aggressor flow | **ADJACENT (tested)** | C3 quarter-hour imbalance **CONFIRMED_NO_GO today** (−18.5 bps best aligned vs +20 bps bar). VPIN directional decayed to −15.6 bps net 2026; veto-only remains queued |
| Network | Exchange inflow/outflow, whale, smart money | **OPEN / INSUFFICIENT_DATA** | `smart_money_feed.py` exists (15-min cache, Binance SM lists) but **no frozen after-cost screen** on local history; Glassnode/CryptoQuant-grade series not harvested. Industry sources treat flows as **regime filters**, not standalone triggers ([crypto-resources](https://crypto-resources.com/exchange-inflow-outflow-in-crypto/)). Expectation NO_GO until harvest + prereg |

**STOP list (do not screen, do not wire as live/paper authority):** MA, EMA, MACD, RSI, BOLL, SAR, SUPER, KDJ, OBV, WR, VOL-as-entry, AVL-as-entry, any "confluence stack" of the above.

---

## Screen-worthy candidates (max 3; honest)

### Candidate A — Order-book depth as ENTRY veto (not directional alpha)
- **Mechanism:** Skip or shrink entries when `depth_ratio_log` / wall flags show adverse liquidity (ask walls on long, bid walls on short) or when top-of-book imbalance is extreme-and-fragile (spoof risk).
- **Novelty:** ADJACENT to formulaic/microstructure; distinct from C3 (clock-boundary imbalance) and from VPIN (toxicity buckets). Literature frames OBI as short-horizon / MM feature that **fails as standalone after fees**.
- **Local data:** `orderbook_depth_feed.py` live; warehouse candidate rows may lack historical depth series → likely need persist-to-warehouse harvest for a proper screen.
- **Expectation:** NO_GO as directional; **possible GO as WR-protection veto** only if after-cost expectancy delta vs no-veto baseline clears gates (screen-13 precedent: overlays protect WR, don't create edge).
- **Rank:** LOW; queue behind VPIN-veto (same overlay class; share one multiplicity budget).

### Candidate B — Network / exchange-flow regime filter (BTC/ETH first)
- **Mechanism:** Condition band-lane entries on net exchange outflow (accumulation) vs inflow (distribution), jointly with funding/OI — never alone.
- **Novelty:** OPEN vs ledger; no local after-cost screen yet.
- **Blocker:** Glassnode/CryptoQuant paid; free smart_money_feed is rank lists, not continuous net-flow history. Harvest command TBD after API feasibility check.
- **Expectation:** INSUFFICIENT_DATA until harvest; then NO_GO expected (filter, not edge).
- **Rank:** LOW; do not advance without free/keyless durable series.

### Candidate C — none (third slot empty)
No third family clears novelty + reopen + cost. Timeframe/indicator retunes of MCP bonuses = formulaic re-litigation.

---

## Autonomous-bot fitness (what can actually work)

External consensus this week (reports 24_/25_) + local ledger:

1. **After-cost survivors remain delta-neutral carry/basis + (fragile) event-driven supply shorts** — already wired (F1 idle correctly; unlock/listing probes log-only).
2. **Classical indicators are chart literacy for humans, not autonomous edge.** Confluence blogs claim 68–78% WR without FDR/DSR or honest cost stress — they fail the reopen bar and contradict our 2,400+ tests.
3. **Microstructure (OB depth, tape, VPIN) is useful as execution quality / veto**, not as a new directional strategy. C3 closed that door for QH imbalance today.
4. **Per-pair "indicator data"**: the bot already computes EMA/RSI/ADX/MACD-like features inside MCP/FeatureVector for scoring — that is **not** evidence those features predict; MCP score has been anti-predictive (confidence leverage escalation disabled).

---

## Reopen-bar check (explicit)

Searched for 2025–26 after-cost walk-forward evidence that would reopen indicator-confluence / RSI / SuperTrend. Findings:
- CoinXSight / KuCoin / GraphDex / YouTube SuperTrend+MACD+RSI — marketing or single-path backtests; **no** peer-reviewed FDR/DSR-grade multiplicity + retail cost accounting that meets the bar.
- Frontiers 2026 microstructure: statistical edge at 5-min **dies at Binance fees**.
- Standing adverse anchor arXiv 2606.00060 (momentum +31% gross → −46% net).

**Reopen bar: NOT MET for any classical indicator family.**

---

## Sources (this scout)

1. Ledger rows: RSI-MR, textbook trend/breakout, indicator-confluence, formulaic alphas, C3 QH imbalance, VPIN brief in 23_/24_
2. [Frontiers microstructure 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full) — no 5-min strategy survives fees
3. [arXiv 2607.09426](https://arxiv.org/html/2607.09426) — QH imbalance (C3 source; local NO_GO)
4. Local feeds: `orderbook_depth_feed.py`, `smart_money_feed.py`, C3 harvest `data/aggtrades_qh/`
5. Exchange-flow framing: [crypto-resources](https://crypto-resources.com/exchange-inflow-outflow-in-crypto/) — regime filter not trigger
6. Marketing confluence claims (CoinXSight, KuCoin, YouTube) — cited only to **reject** as reopen evidence

*Scout complete. 0 classical-indicator candidates. 2 low-priority overlay briefs (OB veto, network filter). Expectation: plan phase should refuse indicator strategy installs and queue at most one overlay prereg after VPIN.*
