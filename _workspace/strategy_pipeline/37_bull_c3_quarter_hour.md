# 37 — Bull thesis: C3 quarter-hour clock-boundary order imbalance

**Role:** bull-researcher (investment-committee)
**Date:** 2026-07-26
**Candidate:** C3 — quarter-hour opening order imbalance → 4–12h directional drift, Binance USDT-M perps
**Decision under review (per brief):** proceed to hashed pre-registration + full screen, or close NO_GO

---

## MATERIAL FACT CORRECTION (before any thesis)

The research brief states C3 "has sat in the pre-registration queue" and asks whether it "should proceed to a hashed pre-registration and full screen." **The local record contradicts this: that exact action was already executed and closed.**

FACTS (all timestamped, all on disk):
- Pre-registration frozen 2026-07-23 BEFORE outcomes, sha256 `7b33c639…` — `_workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.md`
- Harvest completed: Binance aggTrades, BTC+ETH USDT-M, 2026-04-01→06-30, 8,736 boundary events/symbol, ~2.7GB raw → `data/aggtrades_qh/{BTC,ETH}USDT_qh_events.parquet`, manifest sha256 `d3b5632d…`
- Screen run 2026-07-23T11:13Z: **NO_GO, 0/6 residual variants pass** — `22_screen_c3_quarter_hour_imbalance.md`
- Audit: **CONFIRMED_NO_GO**, expansion to 6-symbol/2025-26 harvest **explicitly not authorized** — `22_audit_c3_quarter_hour_imbalance.md`
- Integration: NO-OP — `22_integration_report_c3.md`

A bull case for "run the screen" cannot be built when the screen has been run and lost. What follows is the strongest HONEST bull position that survives these facts.

---

## Thesis

The strongest surviving bull claims are retrospective and procedural, not directional:

1. **C3 fully earned its screen — and the decision to screen it was correct.** It had the best external evidence package of any candidate in the 2026-07 queue (a coin-diverse academic study plus an asserted independent replication), zero incremental data cost, and a horizon (4–12h) that matched this program's 4h cadence. The pipeline's answer to such a candidate is a cheap hashed prereg, and that is exactly what happened. The bull position is vindicated as PROCESS: the candidate was not dismissed on narrative (Shynkevich alone), it was measured.
2. **The measurement itself is banked value.** A zero-capital-risk, prereg-hashed, own-data refutation with a reusable aggTrades harvest pipeline (`scripts/harvest_binance_aggtrades_qh.py`) — infrastructure that already served the subsequent VPIN screen family.
3. **The remaining bull ACTION is bookkeeping, not trading:** `22_integration_report_c3.md` states "Ledger row added under Refuted (scoped)" but the current `refuted-families-ledger/SKILL.md` contains NO quarter-hour row (verified by grep, 2026-07-26). Until that row exists, this candidate can be re-proposed as "queued" — which is precisely what this brief just did. Adding the scoped ledger row is the highest-value bull deliverable left.

I do NOT recommend proceeding to a new full screen. A bull researcher who recommended re-running a just-lost pre-registered test would be manufacturing a case, which this desk does not do.

## Evidence

**What made C3 screen-worthy (the historical bull case, FACT vs INFERENCE marked):**
- FACT (external, per brief + prereg): arXiv 2607.09426 (Kim & Hansen, "Quarter-Hour Effect") — Binance USDT-M perps, BTC/ETH/XRP/SOL/DOGE/ADA, 2021-01→2024-10, public aggTrades: "opening order imbalance predicts returns over four to twelve hours." Cited in the frozen prereg itself.
- ASSERTED, NOT LOCALLY VERIFIABLE (single-source flag): independent data-validation/replication (Wade Kimbrough) + Duke/Yonsei seminar vetting — stated in the research brief; no local artifact confirms it. I could not verify this and do not rely on it.
- FACT: data buildable from `data.binance.vision` at zero vendor cost; 6 study coins ⊂ 43-symbol universe; ≤6 round trips/day compatible with cadence.

**What killed it (own data, after cost, prereg-hashed):**
- FACT (`22_screen`, OOS half of pilot): aligned residual means H4 **−23.3 bps** (n=436, WR 0.383), H8 **−33.0 bps** (n=242, WR 0.393), H12 **−18.5 bps** (n=168, WR 0.470). All 6 variants NO_GO; every WR below the 0.55 floor; MC pass False on all cells; best aligned mean −18.5 bps vs the frozen +20 bps expansion bar.
- INFERENCE (supported): the aligned direction being MORE negative than contrarian at H8 (−33.0 vs −7.0 bps) is consistent with the peer-reviewed counter-evidence (Shynkevich, JFM 2026, 46(5):904-930: boundary windows raise transaction costs without exploitable drift). The pilot did not merely find "no edge" — it found the paper-aligned trade to be the WRONG side after costs on post-publication data.
- FACT: the prereg deliberately tested post-paper OOS (2026 Q2) — the only window that is tradeable going forward. A 2021-2024 replication would validate the paper without validating a forward trade.

## Catalysts

None live. The only future catalyst is new external evidence meeting the ledger's reopen bar (see InvalidateIf), which would justify a NEW prereg — likely alt-scoped (XRP/SOL/DOGE/ADA were never screened) — never a silent re-run of the failed BTC/ETH pilot.

## BestCase

Honest best case is already realized: a candidate with genuine academic backing was measured at zero capital risk, refuted on our own venue/fees/regime, and left behind reusable harvest infrastructure. Forward best case: the scoped ledger row is added, this family stops consuming committee cycles (this brief is the second time it has been litigated), and the aggTrades pipeline serves future microstructure preregs. There is no plausible best case involving P&L from this signal as screened.

## EvidenceQuality

- Local artifacts (prereg/screen/audit/harvest): **HIGH** — hashed before outcomes, internally consistent, manifest-checksummed.
- arXiv 2607.09426: **MODERATE-LOW** for our purpose — not peer-reviewed, no transaction-cost treatment, no multiplicity correction, key Section 6 tables unretrievable (per brief and the 2026-07-26 adversarial pass referenced therein; I could not independently retrieve them either — stated, not assumed).
- Kimbrough replication + seminar vetting: **UNVERIFIED / single-source (brief only)** — excluded from weight.
- Shynkevich JFM 2026: peer-reviewed, mechanism-consistent with our measured signs — strengthens the NO_GO, weakens any bull revival.

## Confidence

- That C3 should proceed to a (re-)pre-registration and full screen now: **3/100.**
- That the honest bull-side deliverables are (a) close NO_GO citing the 22_* artifacts and (b) add the missing scoped ledger row: **90/100.** (Not 100: the missing-ledger-row observation depends on the current working-tree ledger being authoritative; the file is modified-uncommitted per git status.)

## InvalidateIf (exact conditions that would revive a NEW prereg — not this thesis's closure recommendation)

1. Peer-reviewed (or reopen-bar-equivalent) 2026+ evidence that the 4–12h imbalance effect survives explicit after-cost accounting (≥10 bps round-trip taker) with FDR/DSR-grade multiplicity control AND an OOS window extending past 2026-06 — quoted verbatim per the ledger reopen bar. This directly falsifies the "post-publication decay/inversion" reading of our pilot.
2. Demonstrated corruption of the pilot harvest — manifest hash mismatch vs `d3b5632d…`, or an aggressor-flag (`is_buyer_maker`) polarity inversion in `scripts/harvest_binance_aggtrades_qh.py` — which would void the pilot's signs and require a re-run under the SAME frozen prereg.
3. Independent after-cost-positive evidence specific to the four unscreened study coins (XRP/SOL/DOGE/ADA) — justifying a NEW alt-scoped prereg with its own hash, expressly not an expansion of the failed pilot (the frozen expansion bar was missed by ~38 bps).
4. Proof that 2026 Q2 was a structurally unrepresentative regime for the mechanism AND a regime-conditional variant pre-registered fresh with its own multiplicity budget.

Absent one of these, any re-proposal of C3 is re-litigation of a CONFIRMED_NO_GO and should be answered from the ledger — once the missing row is added.

## Sources

- `_workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.md` (frozen 2026-07-23, sha256 `7b33c639…`)
- `_workspace/strategy_pipeline/22_screen_c3_quarter_hour_imbalance.md` (NO_GO, 6-variant table)
- `_workspace/strategy_pipeline/22_audit_c3_quarter_hour_imbalance.md` (CONFIRMED_NO_GO; expansion not authorized)
- `_workspace/strategy_pipeline/22_integration_report_c3.md` (NO-OP; claims ledger row that is absent from current ledger)
- `data/aggtrades_qh/` (harvest, manifest sha256 `d3b5632d…`)
- `.claude/skills/refuted-families-ledger/SKILL.md` (no quarter-hour row as of 2026-07-26; reopen bar §"Reopen Bar")
- arXiv 2607.09426 (Kim & Hansen) — via prereg citation
- Shynkevich, Journal of Futures Markets 2026, 46(5):904-930 — via research brief (peer-reviewed counter-evidence)
- Research brief (this committee run) — source of the Kimbrough-replication assertion (UNVERIFIED locally) and the 2026-07-26 adversarial-pass conclusion
