# 23 — Candidate queue additions (2026-07-23, briefs only — no screen run)

Per `19_dual_model_loop_protocol.md` (max one heavy stage per UTC day; today's was C3),
these two owner-named candidates are queued as briefs. Both carry **expectation NO_GO**.

## Q1 — VPIN (Volume-Synchronized Probability of Informed Trading), BTC/ETH perps

- **Sliver:** flow *toxicity* as a conditioning/veto variable — distinct from the
  directional QH-imbalance construct refuted today (22_*). Literature (Easley,
  López de Prado, O'Hara) frames VPIN as a market-maker risk signal, not alpha.
- **Local feasibility:** `scripts/harvest_binance_aggtrades_qh.py` proves the
  aggTrades pipeline; VPIN needs full-session volume buckets (not just opening 10s)
  → new harvest variant, ~same data source.
- **Honest framing:** primary use would be a **veto overlay** on the band lane
  (like BAND_REGIME_FILTER), NOT a directional entry. Screen-13 precedent: overlays
  protect WR, don't create edge.
- **Prereg requirements if advanced:** frozen bucket size + window, joint multiplicity
  over veto thresholds, after-cost expectancy delta vs no-veto baseline, ≥30-event floor.
- **Adjacency:** formulaic alphas (443+ refuted), QH-imbalance row (2026-07-23).
- **[2026-07-23 deep-research update]** Evidence now attached (24_deep_research report §5):
  peer-reviewed RIBAF Jan-2026 supports VPIN as a JUMP/volatility predictor only; a 2026
  practitioner walk-forward shows the DIRECTIONAL VPIN overlay decayed to **−15.6 bps net
  (t=0.96) in 2026**, BTC-only, bull-months-only; Frontiers 2026 measures its VPIN proxy
  "relatively weak" with nothing at 5-min surviving Binance fees. **Brief narrowed: only the
  veto-overlay (jump-risk filter) expression remains queued; directional expression is
  anchored-adverse and will not be screened.**

## Q2 — Cross-sectional mean reversion (rank-based, weekly, majors basket)

- **Sliver:** cross-sectional *rank* reversion (long losers/short winners basket)
  is locally untested; time-series RSI-MR is refuted; bundle-MR probes (zfade/rsi2)
  measure time-series MR forward already.
- **Blocking feasibility problem recorded up-front:** at $420 account, a basket of
  N long + N short perp legs violates min-notional and concurrency limits; costs
  multiply per leg. Any screen must model per-leg min notionals honestly — likely
  kill condition before signal quality is even reached.
- **Adjacency:** RSI-MR (refuted 2026-06), formulaic alphas.
- **[2026-07-23 deep-research update]** SSRN 4675565 (realistic-assumptions study on
  Binance futures — actual fees/ticks/slippage, margin/liquidation modeling): evidence
  for cross-sectional momentum is "weak"; 5/21 portfolios LIQUIDATED in-sample; short
  legs eaten by jump risk. Vol-managed variants (FMPM 2025) are pre-cost on universes
  we cannot trade. Priority lowered further; expectation NO_GO unchanged.

**Neither brief authorizes a screen.** Advancing either requires: prereg written+hashed
first, one heavy stage on a fresh UTC day, dual-model verdicts per protocol.

---

## 2026-07-23 WR-band plan Phase C update

See [`30_edge_queue_2026-07-23.md`](30_edge_queue_2026-07-23.md).

- **Next heavy stage (fresh UTC day):** VPIN **jump-risk veto** only (directional VPIN remains STOP).
- **F1:** remediation queued only if after-cost harvest evidence returns; no force-enable.
- Cross-sectional MR: still parked / likely capital-infeasible at ~$420.

---

## 2026-07-24 futures deep-research update (`32_deep_research_futures_2026-07-24.md`)

External delta since 07-23 = **NULL**. Queue reordered in `30_edge_queue` (binding):

- **C2 gamma-expiry elevated to #2** — peer-reviewed FRL Sep-2026 mechanism (daily-expiry BTC
  reversal, ATM-OI>p90 ∧ GEX<0) + free forward data path. Prereg `33_prereg_c2_gamma_expiry`
  FROZEN+hashed; Deribit chain snapshot harvester running (07:30/19:30 UTC). Screen at ≥30
  events/cell. Expectation lean-NO_GO on cost magnitude.
- **Q1 VPIN unchanged at #1** (prereg `27_*` hashed 2026-07-24; screen on 2026-07-25).
- **NEW brief #3: liquidation-cascade reversion** — strongest unscreened mechanism, zero rigorous
  after-cost backtests anywhere; binding data problem (forceOrder throttle/undercount); NO vendor
  spend — forward self-collection; 30–60 bps in-event costs; needs Codex cross-check at prereg
  (new family). Prior ~25% GO.
- **NEW brief #4: OI×funding regime classifier** — internal veto study only (OI-divergence stays
  refuted directionally).
- **NEW brief #5: Hyperliquid funding as F1 conditioner** — data signal only, vendor-grade
  evidence, cheap local screen.
- **Q2 cross-sectional MR: priority lowered again** — ML4Trading holdout collapse (+0.80→−1.17)
  adds to SSRN 4675565; still capital-infeasible.
- **Refused:** cross-exchange lead-lag (taker-dead), VRP overlays (vol timing), max-pain,
  standalone DVOL/skew. Delisting reopen accelerated operationally (deliveryDate-flip harvester
  running) but timeline unchanged (~6–12 mo to ≥30 covered events).

**Neither new brief authorizes a screen.** Advancing any item: prereg written+hashed first, one
heavy stage per fresh UTC day, dual-model verdicts per `19_` protocol.
