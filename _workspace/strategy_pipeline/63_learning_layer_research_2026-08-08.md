# 63 — Self-learning layer research: verified findings + disposition

**Date:** 2026-08-08 · **Run:** deep-research `wf_e71a5417-e10` (4 passes; the
synthesize agent died on session limits every pass, so this file IS the
synthesis, written from the verified-claim list only)
**Verification:** 25 of 94 extracted claims sent to 3-vote adversarial panels →
**17 CONFIRMED / 2 refuted / 6 unverified** (panel votes recorded per claim in
the run output). Question scope: what LEARNING machinery works — meta-labeling,
retraining/promotion cadence, strategy-portfolio bandits, regime overlays,
post-trade loops. Entry signals were excluded by construction (ledger-refuted
families hard-excluded from the query).

## Verified findings (votes in parentheses)

### 1. Meta-labeling — legitimate machinery, wrong lane for rescue
- Definition/mechanism confirmed (3-0, SSRN 4032018 + Hudson-Thames): a
  secondary model that sizes/filters an EXISTING primary signal by predicting
  trade outcome, not price. NOT in the refuted ML-forecaster family.
- **No verified source shows meta-labeling flipping a no-edge primary to
  positive expectancy.** The framework paper explicitly does not claim it
  (3-0). The strongest 2026 crypto result (arXiv 2606.00060, BTC hourly,
  Holm-corrected): a cost-aware filter (trade only when forecast magnitude
  > 2× expected transaction cost) cut turnover 10,619→251 trades and flipped
  −64.0% net to +65.4% ARC (3-0) — **and still did not beat buy-and-hold**
  after bootstrap-adjusted Sharpe comparison (2-1). Filtering reduces bleed;
  it does not create edge. This is our scalp-row verdict ("exits can't
  manufacture EV") reproduced externally for entry-filtering.
- JFDS Spring-2023 (Meyer/Barziy/Joubert): probability **calibration**
  significantly improves fixed position sizing (3-0) — the one verified
  mechanism with a legitimate application HERE: sizing the validated carry
  lane. The Hudson-Thames repo itself carries no after-cost/crypto/OOS
  numbers (3-0) — mechanism source only.

### 2. Retraining + promotion cadence — our architecture, externally validated
- "Shadow Before Swap" (arXiv 2607.28577, crypto perps): retrain often,
  promote ONLY after a paired shadow trial clears a deadband — beat calendar
  replacement, always-promote, and never-refit (2-1). The gate accepted only
  **114/528 challengers (21.6%)** — blind promote-every-retrain is a named
  failure mode (2-1). Caveat confirmed 2-0: gains are in NLL/Brier ONLY — no
  P&L, no costs; the authors disclaim profit transfer. Evidence for
  promotion MACHINERY, not for edge.
- Maps 1:1 onto what this bot already runs: frozen promotion_gate +
  champion/challenger + shadow probes + (row 8, shipped 2026-08-07) an
  honest CSCV/PBO. **Disposition: ALREADY SATISFIED — no new build.** The
  cost-aware admission mechanism from 2606.00060 is likewise already
  implemented as the stressed-cost economic entry gate.

### 3. Strategy-portfolio bandit allocation — REJECTED for now
- The only in-window bandit-for-strategy-selection source: its arms are
  trend-following, mean-reversion, momentum — **all ledger-refuted families**
  (3-0); it concedes all arms may be suboptimal and carries no costs, no
  OOS, no multiplicity control (2-0). Fails the evidence bar outright.
- ERC/low-turnover/window-tuning insufficient to beat naive 1/N after costs
  (3-0, ScienceDirect 2024). NOTE the refuted nuance: the broader claim
  "1/N remains unbeaten by optimized mean-variance over 20yr" was REFUTED
  1-2 as stated — do not cite it. The narrow ERC claim stands.
- Honest arithmetic stands regardless: allocation redistributes expectancy
  across arms; with one validated arm (F1 carry) and a measured-negative
  directional arm, the correct "allocator" is the promotion gate we have.

### 4. Regime overlay — one interesting verified mechanism, no build
- Statistical jump model beat HMM and buy-and-hold OOS on US/DE/JP EQUITY
  indices 1990-2023 for exposure on/off switching (2-0 — equities, not
  crypto; exposure-switching, not strategy selection).
- Its advantage is mechanistic and verified 3-0: a **jump penalty at state
  transitions enforces regime persistence and suppresses spurious flips** —
  directly applicable as a regularization to our EXISTING HMM module rather
  than a replacement. Folded into edge-queue row 3 (OI×funding regime
  study) as a design note; no standalone build.

### 5. Post-trade loops — thin verified evidence
Nothing survived verification beyond what areas 1-2 already cover. The
unverified leftovers (CPCV vs walk-forward false-discovery rates, regime
identity persistence via Wasserstein mapping, crypto execution-cost
decomposition, combined scheduled+drift-triggered retraining) died on
infrastructure limits, not refutation — available for a future targeted pass;
none blocks anything.

## Disposition

| # | Item | Verdict | Action |
|---|---|---|---|
| 1 | Carry-lane meta-labeling **sizing** (calibrated P(win) on F1 episodes) | decision-path candidate | **Edge-queue row 9** — hashed prereg + frozen-gate screen REQUIRED before any use; substrate = funding history + carry_gate_log + warehouse provenance |
| 2 | Meta-labeling to rescue the directional lane | rejected | Verified: filtering only reduces bleed on a no-edge primary; does not beat doing nothing |
| 3 | Champion/challenger shadow promotion | already satisfied | promotion_gate + probes + row-8 PBO = the verified pattern; no build |
| 4 | Cost-aware entry admission | already satisfied | stressed-cost economic gate = the 2606.00060 filter mechanism |
| 5 | Bandit strategy allocator | rejected | Only source fails the bar; arms are refuted families |
| 6 | Jump-penalty regime persistence | design note only | folded into edge-queue row 3; no standalone screen |

**Nothing in this round changes a live decision path.** Row 9 is the only new
work item and it is prereg-gated by construction.
