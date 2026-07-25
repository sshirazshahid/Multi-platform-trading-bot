# 35 — BEAR Thesis: VPIN jump-risk veto (`vpin_jump_veto_v1`)

**Author:** bear-researcher (investment committee)
**Date:** 2026-07-25
**Candidate:** VPIN jump-risk VETO overlay on the AccBand PAPER lane, BTC/ETH Binance USDT-M (NOT directional — directional VPIN is anchored-adverse and stays STOP)
**Committee question:** proceed with the pre-registered screen (prereg `27_*`, FROZEN+hashed) vs park/defer
**Screen status:** NOT run. No screen results exist; none are invented below.

---

## Thesis

The screen as frozen cannot plausibly answer its own question: a GO requires the veto-kept subset of a lane with **measured expectancy ≈ −0.24R** to pass MC P(total>0) ≥ 0.95 — a positive-selection claim already refuted **0/16 on 14,555 outcomes of this exact lane** — while the frozen BTC/ETH-only substrate has produced **2 qualifying closed trades in ~6 days** (measured, warehouse), making INSUFFICIENT_DATA the modal outcome and the honest replay alternative a multi-GB aggTrades harvest whose only cheap substitute would break the prereg hash. Even the full-success case yields a second flow-cutting veto in the exact class (`BAND_REGIME_FILTER`) the owner shipped and then disabled within 8 days for flow. Park pending a cheap feasibility pre-check; do not spend the heavy screen slot buying a foregone conclusion.

---

## Risks

### R1 — Gate arithmetic: GO is near-unreachable on a measured −EV substrate (structural)

- **FACT:** The AccBand dual-goal screen (`30_screen_accband_frac_dual_goal.*`, CONFIRMED_NO_GO 2026-07-24, n≈8,700 after-cost geometry outcomes) measured expectancy ≈ **−0.24R at every frac** with breakeven WR ≥ 0.68. This is the substrate the veto keeps or skips.
- **FACT:** Prereg `27_*` GO requires, on the veto arm (kept trades): OOS ΔEV>0 AND MC P(total>0) ≥ 0.95 AND MC maxDD p95 ≤ 0.25.
- **INFERENCE:** For a subset of a −0.24R lane to be robustly positive at 95% MC confidence, VPIN must not merely flag jump risk — it must discriminate EV strongly enough to flip the lane's sign. The maxDD ≤ 0.25 gate compounds this: a still-negative kept arm has a drifting-down equity curve whose maxDD approaches total bleed. Two independent near-impossible gates.
- **FACT (precedent):** Positive selection on this exact lane is already refuted: `13_band_conditional` screen, 16 pre-registered conditioning buckets, Bonferroni m=16, 14,555 resolved band outcomes → **0/16 GO; every bucket after-cost negative** (best: f6, 78.5% WR at −$0.06/trade). VPIN would need to out-discriminate all 16 features already tested — against external evidence that VPIN's predictive power is "relatively weak" even for its home task (Frontiers 2026 proxy).
- **INFERENCE:** ΔEV>0 alone is achievable (13_ showed WR-shifting vetoes exist), but the prereg itself brands that outcome NO_GO ("veto without EV lift is churn" / "only WR improves with worse EV = bleed-mask"). So the reachable outcomes are NO_GO and INSUFFICIENT_DATA.

### R2 — Substrate starvation: INSUFFICIENT_DATA is the measured modal outcome (feasibility)

- **FACT (measured 2026-07-25, read-only warehouse query):** closed PAPER trades since 2026-07-19 00:00 UTC (MAX_FLOW_BAND epoch): **99 total, of which 2 are BTC/ETH**. Last-30d mix: 83/775 ≈ 10.7% BTC/ETH, but that spans other profile eras.
- **INFERENCE:** At ~0.3–0.9 BTC/ETH band-lane trades/day (range depends on whether the post-07-22 flow fix triples accrual), the frozen "min n ≥ 30 OOS skipped+kept events" takes roughly **1–3 months of forward accrual** on the live-outcome path — and the *skipped* arm (VPIN>θ coinciding with an AccBand signal on BTC/ETH) is strictly smaller than that.
- **FACT:** Prereg gate wording "Min n OOS skipped+kept events ≥ 30" does not specify per-arm vs pooled — an unfrozen degree of freedom that will have to be resolved after the hash.

### R3 — The replay escape hatch is expensive and integrity-fragile

- **FACT:** Prereg allows "replay of same geometry" as the substrate, with VPIN built from Binance aggTrades (`data/aggtrades_vpin/`, Lee-Ready on `is_buyer_maker`) — frozen construction.
- **INFERENCE:** A replay window long enough for n≥30 per θ means harvesting weeks-to-months of BTC+ETH USDT-M aggTrades (order hundreds of MB–GB/symbol-day compressed; tens-to-hundreds of GB total, or a rate-limited multi-day API backfill). The cheap substitute — a klines `takerBuyBaseVolume` VPIN proxy (as the Frontiers study uses) — is **not** what was hashed; swapping it in post-hash is exactly the prereg-integrity failure the pipeline exists to prevent.
- **INFERENCE:** Replay also cannot reproduce the live lane's full entry-gate stack (13-layer authorization history, vetoes, profile epochs), so "identical AccBand entries" is only approximately true under replay — a second unfrozen choice (which entries count) after the hash.

### R4 — Threshold calibration froze a guess (metric fragility)

- **FACT:** The θ grid {0.55, 0.60, 0.65, 0.70} is on **raw** VPIN; the flash-crash literature (Easley–López de Prado–O'Hara) operates on the VPIN **CDF transform** precisely because raw levels are not comparable across instruments/bucketings. No distribution pre-check on Binance BTC/ETH perps was pre-registered.
- **INFERENCE:** If realized raw VPIN on the most liquid perps rarely crosses 0.55, the veto never fires → n_skipped ≈ 0 → INSUFFICIENT_DATA after the full harvest. If it crosses routinely, the veto starves an already-starved lane. Both tails of the miscalibration risk end badly; neither was measured before freezing.
- **FACT (classic adverse anchor):** Andersen & Bondarenko (J. Financial Markets 17, 2014, "VPIN and the Flash Crash"): VPIN's predictive content is largely subsumed by trading intensity/realized volatility, and the metric is highly sensitive to bucket-size and sampling choices. Contested by the Easley–López de Prado–O'Hara rejoinder (JFM 2014), but the parameter-sensitivity findings stand unrebutted. Pre-2025, non-crypto — cited as mechanism risk, not as reopen-bar evidence.

### R5 — Redundancy with an already-shipped, already-disabled veto class

- **FACT:** A regime veto for this exact lane already shipped from the 13_ screen (`BAND_REGIME_FILTER_ENABLED`: 4h ADX>30, BTC 1h vol-ratio<0.7) as WR-protection-not-edge — and the **owner disabled it on 2026-07-20** for aggressive PAPER accrual (CLAUDE.md changelog).
- **INFERENCE:** VPIN spikes cluster with high-vol/trending toxic states (RIBAF 2026 links VPIN to jump frequency/size; A&B 2014 links it to vol itself), so its veto overlaps the ADX/vol veto's firing set. The incremental ΔEV of a *second, correlated* veto — measured against a baseline that currently runs with the *first* veto off — is the difference of two small numbers on a small n.
- **FACT (revealed preference):** The owner's standing directive is flow maximization in PAPER (starvation incidents 07-21/07-22; economic-gate `paper_fallback` shipped to restore flow). A veto's only output is less flow.

### R6 — Session-effect confound

- **FACT:** RIBAF 2026 documents time-zone and day-of-the-week structure in VPIN, and reverse causality (jumps feed back into VPIN).
- **FACT:** Hour-of-day/seasonality selection on our own data is a refuted family (0 survive OOS, ledger 2026-06-02).
- **INFERENCE:** A θ-veto may partially proxy session effects already known to carry no exploitable structure here; any marginal ΔEV could be a repackaged refuted signal.

### R7 — Opportunity cost of the heavy slot

- **FACT:** One heavy screen per UTC day (pipeline norm); queue order is binding — C2 Deribit and the liquidation-cascade prereg wait behind VPIN closing (`32_*` takeaway 3). Harvest for this screen is scheduled to consume UTC day 2026-07-25 (brief FACT 4).
- **INFERENCE:** Spending the slot on a screen whose reachable outcomes are NO_GO/INSUFFICIENT_DATA buys information the ledger already prices (R1). A cheap, pre-registered feasibility pre-check (raw-VPIN distribution on a small aggTrades slice + substrate n-count) would close the same queue item honestly at a fraction of the cost — or license the full screen if it surprises.

---

## AdverseEvidence

| # | Evidence | Type | What it kills |
|---|----------|------|---------------|
| 1 | AccBand lane expectancy ≈ −0.24R, BE_WR ≥ 0.68, 0/12 cells (`30_*`, CONFIRMED_NO_GO 2026-07-24, n≈8,700) | Local, pre-registered, own data | The substrate the veto must make positive |
| 2 | Band-conditional positive selection 0/16 buckets GO on 14,555 outcomes; best bucket still after-cost negative (`13_*`, ledger 2026-07-12) | Local, pre-registered, Bonferroni m=16 | The "kept subset can be positive" claim, for 16 prior conditioning features |
| 3 | Directional VPIN overlay decayed to **−15.6 bps net (t=0.96), 2026, BTC-only, bull-months-only** (MEXC practitioner walk-forward, cited `24_*` §5) | External, practitioner, single-source | Directional expression (already STOP); bounds veto-adjacent hopes |
| 4 | RIBAF Jan 2026 (Kitvanitphasu et al., DOI 10.1016/j.ribaf.2025.103163): VPIN predicts BTC jumps in a descriptive VAR; **no strategy, no costs, no ΔEV**; jumps feed back into VPIN; time-zone/day-of-week structure | External, peer-reviewed | The mechanism supports *jump prediction only* — it is the bull's best card and it contains no after-cost evidence |
| 5 | Frontiers in Blockchain 2026 (10.3389/fbloc.2026.1811716): VPIN-proxy among 12 features — "no trading strategy based on these 5-min forecasts survives standard Binance exchange fees and slippage"; "microstructure models trained on one cryptocurrency do not generalise to other cryptocurrencies" | External, peer-reviewed | After-cost microstructure alpha on the same venue; BTC→ETH transfer assumption |
| 6 | Andersen & Bondarenko, JFM 2014 (+ Easley et al. rejoinder, JFM 2014) | External, peer-reviewed, contested, non-crypto | VPIN ≈ repackaged vol/intensity; parameter sensitivity of frozen bucket/N/θ choices |
| 7 | Warehouse count 2026-07-25: **2/99** BTC/ETH closed PAPER trades since MAX_FLOW_BAND epoch | Local, measured this session | Live-substrate n≥30 within any near-term window |
| 8 | Quarter-hour imbalance pilot NO_GO on own data (C3, `24_*` source 9, 2026-07-23); formulaic alphas refuted (443+, ledger 2026-05-25) | Local, pre-registered | Adjacent order-flow-derived families on this venue's own data (scope: veto does not reopen them — brief concurs) |
| 9 | `BAND_REGIME_FILTER` shipped 2026-07-12 → owner-disabled 2026-07-20 (CLAUDE.md) | Local, operational | The utility of the success case: veto flags on this lane get turned off for flow |

---

## WorstCase

Direct capital risk of the screen itself is **≈ $0** (log-only research on PAPER data; no order path). The loss channels are process and time:

1. **Base worst case (likely):** Full UTC-day harvest (multi-GB aggTrades) → θ grid barely fires or BTC/ETH n<30 → INSUFFICIENT_DATA. The queue item does not close cleanly (an INSUFFICIENT_DATA row still blocks "VPIN closed" semantics or forces a judgment call), the liquidation-cascade prereg stays parked, and a day's heavy slot + disk are spent confirming what the substrate count already showed this morning.
2. **Integrity worst case (worse):** Facing INSUFFICIENT_DATA, the screen quietly swaps substrate (live→replay), classifier (aggTrades→klines proxy), or pools skipped+kept arms to reach n≥30 — three unfrozen choices identified above — and a marginal, multiplicity-lucky ΔEV>0 at one θ squeaks past Holm m=4 with adjacent-θ same-sign on n≈30–60 (wide CIs). Result: a false GO → shadow-probe build + config flag + funnel lane on a WR-research lane with no profit path, later disabled like its sibling. Each such episode erodes the "veto without EV lift is churn" rule and the prereg-hash discipline — the two rails that keep this pipeline honest.
3. **Decision-noise worst case:** A "VPIN veto GO" headline gets remembered as "VPIN works," becoming a future pressure point to re-litigate directional VPIN (anchored-adverse, −15.6 bps net) — the exact re-litigation cost the ledger exists to prevent.

---

## EvidenceQuality

| Evidence class | Rating | Notes |
|---|---|---|
| Local pre-registered screens on own data (30_, 13_) | **HIGH** | Frozen gates, multiplicity control, n in the thousands, same lane and venue as the candidate |
| Warehouse substrate count (2/99) | **HIGH** (narrow) | Direct measurement this session; small window (~6 days), mix could shift |
| RIBAF Jan 2026 jump-prediction | **MEDIUM-HIGH** for mechanism; **NIL** for ΔEV | Peer-reviewed VAR; spot BTC high-frequency; no costs, no strategy, no veto test |
| Frontiers 2026 after-cost + transfer findings | **MEDIUM-HIGH** | Peer-reviewed, same venue fees modeled; VPIN is a proxy variant, 5-min horizon differs from lane horizon |
| MEXC practitioner walk-forward (−15.6 bps) | **MEDIUM-LOW** | Vendor/practitioner, single-source, directional-not-veto; adverse direction aligns with everything else |
| Andersen & Bondarenko 2014 | **MEDIUM** here | Peer-reviewed but non-crypto, pre-2025, contested by rejoinder; used only for metric-fragility mechanism |
| Missing: raw-VPIN distribution on Binance BTC/ETH perps; skipped-arm base rates | **UNKNOWN** | Per role protocol, unknowns count toward BEAR until measured — these two unknowns decide feasibility and were not pre-checked |

---

## Confidence

**85 / 100** that the screen as frozen returns NO_GO or INSUFFICIENT_DATA (i.e., no actionable veto emerges), and that proceeding with the full heavy screen *now* — before a cheap feasibility pre-check — is negative-expectation use of the day's slot.

Residual 15: (a) VPIN could be a genuinely orthogonal EV discriminator none of the 16 tested features captured — jump-toxicity is mechanistically distinct from ADX/vol regime; (b) the 2/99 window is short and the mix could improve; (c) closing a frozen prereg has process value the bear discounts but does not deny.

---

## InvalidateIf

Stand down from this bear thesis if ANY of the following is demonstrated:

1. **Feasibility pre-check passes:** a small pre-registered aggTrades slice shows raw VPIN on Binance BTC/ETH USDT-M crosses the frozen θ grid at rates that give BOTH arms (skipped AND kept) n≥30 on the frozen substrate within the screen window — with the substrate (live vs replay) and arm-count semantics pinned in writing before outcomes.
2. **Positive-selection precedent breaks:** any conditioning feature on the AccBand lane reaches after-cost expectancy >0 with multiplicity control (would overturn the 0/16 structural prior that kept subsets cannot be positive).
3. **Direct external evidence appears:** peer-reviewed 2025+ crypto-futures evidence that a VPIN-style *veto* improves after-cost EV of a directional lane (not merely predicts jumps/vol), with genuine OOS and multiplicity control.
4. **Substrate sign flips:** the MCP band lane's measured expectancy reaches ≥0 after costs — a jump veto on a profitable lane is a different, materially stronger candidate (the MC gates become reachable).
5. **Owner re-values WR-protection:** the owner re-enables the `BAND_REGIME_FILTER` veto class or states on record that WR-band protection without EV lift is a deliverable he wants — this converts the "success case is a dead flag" argument into a live use case.

---

## Sources

**Local (pipeline artifacts, own data):**
1. `_workspace/strategy_pipeline/27_prereg_vpin_jump_veto.md` — frozen prereg, gates, θ grid, expectation NO_GO
2. `_workspace/strategy_pipeline/35_research_brief_vpin_jump_veto.md` — committee brief (facts 1–5)
3. `_workspace/strategy_pipeline/30_screen_accband_frac_dual_goal.{md,json}` + ledger row 2026-07-24 — substrate −0.24R, BE_WR≥0.68, 0/12
4. `13_band_conditional` ledger row 2026-07-12 — 0/16 positive-selection buckets, 14,555 outcomes; BAND_REGIME_FILTER provenance
5. `_workspace/strategy_pipeline/24_deep_research_futures_2026-07-23.md` §5 + sources 16–17 — RIBAF anchor; MEXC −15.6 bps practitioner WF; Frontiers proxy "relatively weak"
6. `_workspace/strategy_pipeline/32_deep_research_futures_2026-07-24.md` — binding queue order; VPIN-first scheduling
7. `data/warehouse.sqlite` read-only queries, 2026-07-25 (this session): trades since 2026-07-19 → 2/99 BTC/ETH; last-30d 83/775
8. Refuted-families ledger (`.claude/skills/refuted-families-ledger/SKILL.md`) — hour-of-day 2026-06-02; formulaic alphas 2026-05-25; C3 QH-imbalance pilot NO_GO; BAND_REGIME_FILTER disabled 2026-07-20 (CLAUDE.md changelog)

**External:**
9. Kitvanitphasu, Kyaw, Likitapiwat, Treepongkaruna (2026). "Bitcoin wild moves: Evidence from order flow toxicity and price jumps." *Research in International Business and Finance* 81, 103163. [DOI 10.1016/j.ribaf.2025.103163](https://doi.org/10.1016/j.ribaf.2025.103163) — VPIN→jumps (descriptive VAR; no costs/strategy; reverse causality; session effects)
10. Frontiers in Blockchain (2026). "Microstructure alpha: hierarchical learning and cross-asset transfer in cryptocurrency markets." [10.3389/fbloc.2026.1811716](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full) — nothing survives Binance fees at 5-min; no cross-coin transfer; VPIN-proxy weak
11. MEXC microstructure practitioner study — [directional VPIN overlay −15.6 bps net, 2026](https://www.mexc.co/news/1002105) (vendor-grade, single-source)
12. Andersen, T.G. & Bondarenko, O. (2014). "VPIN and the Flash Crash." *Journal of Financial Markets* 17, 1–46 — VPIN subsumed by intensity/vol; parameter sensitivity. Rejoinder: Easley, López de Prado, O'Hara, *JFM* 17, 47–52 (contested; pre-2025, non-crypto — mechanism-risk citation only)
13. MDPI JRFM 19(1):59 (2026). "Informed Trading Through the COVID-19 Pandemic: Evidence from the Bitcoin Market" — corroborates VPIN-jump correlation, descriptive only

**Handoff:** → `debate-engine`. This file argues the bear side only; no screen results are asserted anywhere above.
