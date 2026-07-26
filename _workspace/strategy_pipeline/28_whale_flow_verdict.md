# 28 — Whale/exchange-flow source integration: VERDICT (RECORD-NO-ACTION)

**Date:** 2026-07-24 | **Session:** ef3c3ceb | **Owner directive (verbatim):** "Find sources for Big/Huge/Massive movements within the markets something like arkhamintelligence.com then use those sources and integrate them into strategies"
**Workflow:** wf_3d73a6a3-44b (4 agents: reopen-bar verify / procurement / adversarial claim-check / reconcile). Raw procurement artifact: `28_procurement_raw.md`.

## Decision
- **reopen_bar_met: FALSE** — no source clears the bar (peer-reviewed/equivalent 2025+, genuine OOS, after-cost, multiplicity control).
- **screenable_today: FALSE** — on-chain/flow data stored for 0 of 43 pairs (dossier 27_); feeds persist nothing to disk.
- **recommended_action: RECORD-NO-ACTION** — do NOT open a screen, do NOT fund a feed.
- **Family status:** exchange net-flow / whale-transfer = UNSCREENED / on-trial (NOT refuted). Adjacent to refuted ETF-flow + BTC-dominance timing (NO_EDGE 2026-06-07) and directional funding (NO_EDGE).

## Why (evidence)
- **Affirmative negative:** Baquero survey (arXiv 2606.00071, 2026) verbatim: "At short-to-medium horizons, no peer-reviewed study has shown robust superiority over the naive baseline across multiple market regimes"; cites Yae & Tian 2022 (network metrics "fail out-of-sample"), Shanaev 2019 (network-price link "spurious—driven by autocorrelation and endogeneity").
- **Named primary source** arXiv 2411.06327 (Chi/Chu/Hao): non-peer-reviewed working paper, no OOS/after-cost/multiplicity evidence. (A WebFetch summarizer FABRICATED Sharpe 1.47 / t-stat 4.82 / +287% for it — contradicted the abstract, DISCARDED.)
- **The OOS+after-cost signals that DO exist are different families:** microstructure SIGNED order-flow (trade-tape) and cross-crypto lead-lag — neither is exchange on-chain net-flow or whale transfers. Do not misread as reopening this family.
- **Three viral claims adversarially REFUTED:** "Coin Metrics 72% / 2-SD" = false attribution (traces to a signals-selling vendor, not Coin Metrics; base-rate-hollow); "30k-BTC → 5.2% drawdown/week" = no primary study, post-hoc news anecdote, curve-fit absolute threshold; "USDT inflows positively predict returns" = the one real anchor (Ante et al. 2021) is CONTEMPORANEOUS + direction-heterogeneous + in-sample, not predictive.

## Procurement reality (if ever, not now)
- **Whale Alert: DISQUALIFIED** — ~30-day history cap on EVERY tier incl. $699/mo Enterprise; no backfill, forward-accrue only.
- **CryptoQuant Professional (~$109/mo, daily netflow + Exchange-Whale-Ratio REST)** = only viable-if-ever target. BLOCKERS: (1) ~26% of the $420 account **per month** — data cost alone likely exceeds any plausible edge at this size; (2) netflow series explicitly **NOT point-in-time** (labels retroactively revised as clustering updates) = built-in lookahead hazard that contaminates OOS backtests; (3) daily-only (intraday = Premium $799/mo); (4) $109-tier endpoint entitlement doc-inferred, not invoice-confirmed.
- No genuinely free backfillable API found (Dune free-tier community dashboards NOT checked — the one open lead if this ever reopens).

## Reopen condition
A rigorous 2025+ peer-reviewed (or equivalent) study on liquid retail-venue crypto with genuine OOS + FDR/DSR multiplicity + after-cost accounting showing exchange-net-flow/whale directional-return edge. Quote verbatim → earns a pre-registered SCREEN, never a build. Promotion (if ever) = frozen gate + owner-signed.

**Ledger row (pending — deferred to avoid collision with the active sibling session editing SKILL.md; add when tree is quiescent):**
`| Exchange net-flow / whale-transfer directional timing (Arkham-class on-chain sources) | Reopen-bar sweep 2026-07-24 (wf_3d73a6a3-44b): NO source clears the bar. Baquero survey arXiv 2606.00071 affirmatively negative (network predictors fail OOS, "spurious"); named source arXiv 2411.06327 non-peer-reviewed no-OOS; 3 viral stats (Coin Metrics 72%, 30k-BTC→5.2%, USDT-inflow-predicts) all refuted/unverifiable. Data-blocked: 0/43 pairs stored. Feed cost (CryptoQuant $109/mo = 26% of account) + non-point-in-time lookahead hazard. UNSCREENED not refuted; adjacent to ETF-flow/dominance NO_EDGE | 2026-07-24 |`
