# 37 — BEAR thesis: C3 quarter-hour / clock-boundary order-imbalance (4–12h horizon)

**Role:** bear-researcher (investment-committee)
**Date:** 2026-07-26
**Candidate:** C3 — quarter-hour opening aggressor-imbalance → 4–12h Binance USDT-M perp drift (arXiv 2607.09426 basis)
**Recommendation:** CLOSE NO_GO. Do not run a new pre-registration or full screen.

---

## Thesis

**C3 is not a queued candidate awaiting its first screen — it is an already-measured, already-closed CONFIRMED_NO_GO.** The research brief's framing ("should C3 proceed to a hashed pre-registration and full screen") is factually stale. This repo ran a hashed, frozen pre-registration and pilot screen of exactly this construct on 2026-07-23:

- Prereg frozen before harvest: `22_prereg_c3_quarter_hour_imbalance.md`, sha256 `7b33c639…` (FACT, verified on disk).
- Harvest: Binance USDT-M aggTrades, BTC+ETH, 2026-04-01→2026-06-30, 8,736 boundary events/symbol, manifest sha256 `d3b5632d…` (FACT).
- Screen (`22_screen_c3_quarter_hour_imbalance.md`): **0/6 residual variants pass any frozen gate.** Aligned OOS means after cost: **H4 −23.3 bps (n=436, WR 0.383), H8 −33.0 bps (n=242, WR 0.393), H12 −18.5 bps (n=168, WR 0.470)**. DSR ≈ 0 on every cell. MC P(total>0) ≤ 0.08 on all aligned cells. Best cell overall is contrarian H8 at −7.0 bps — the *opposite* of the paper's hypothesis, and it still fails WR/MC (FACT).
- Audit (`22_audit_c3_quarter_hour_imbalance.md`): CONFIRMED_NO_GO; **full 6-symbol / 2025–26 expansion explicitly NOT AUTHORIZED** — the pilot missed the pre-registered ≥+20 bps expansion bar by ~39–53 bps (FACT).
- Integration (`22_integration_report_c3.md`): NO-OP (FACT).
- The 2026-07-26 mechanism-sweep adversarial pass (`29_mechanism_sweep_verdict.md` line 44) reiterates: **"DO NOT ADD: quarter-hour boundary columns … (refuted above)"** (FACT, verified on disk).

Nothing new has arrived since 2026-07-23 that meets the ledger's reopen bar. Re-running this screen is re-litigation of a binding measured verdict — precisely the behavior the refuted-families-ledger exists to prevent. The Shynkevich peer-reviewed cost finding only corroborates a kill we already own on our own data, our own venue, our own fee schedule.

**Inference:** the arXiv effect, if it ever existed tradeably, is either (a) spanned by price-volume state variables, (b) an artifact of no-cost/no-multiplicity methodology, or (c) decayed post-publication — our 2026 Q2 forward data is consistent with all three and cannot distinguish them, which is irrelevant because all three imply the same action: do not trade it, do not re-screen it.

---

## Risks (why proceeding is the risky action, not the cautious one)

1. **Measured after-cost sign is NEGATIVE on our venue and era.** This is not "insufficient evidence"; it is adverse evidence at n=168–436 per cell on post-paper out-of-sample data. A full 2021–2024 harvest can only "rescue" C3 by including the paper's own in-sample era — i.e., by construction it would be fitting to the discovery period of a signal already dead forward. That is the textbook overfitting trap the frozen gates exist to block.
2. **Cost model in our pilot was, if anything, generous.** We charged flat 5 bps taker/side + 5+5 bps slippage + funding. Shynkevich (JFM 2026, peer-reviewed, per brief) finds clock-boundary windows carry *elevated* transaction costs (wider effective spreads) with no significant adverse price impact — meaning entering at T+10s realistically costs MORE than our model assumed. The true after-cost means are therefore likely *below* the already-negative measured −18 to −33 bps. Unmeasured boundary-specific slippage is an unknown that favors BEAR.
3. **Preprint methodology fails our evidence bar on three prongs** (per the brief and consistent with the 22_ audit): no transaction-cost/slippage treatment anywhere, no multiplicity correction, and the Section-6 regression tables underpinning the 4–12h claim were unretrievable across v1/v2 — the core promotable claim is *unverifiable*. Its OOS LASSO work targets opening returns (sub-second, latency-infeasible for us), not the 4–12h horizon. The independent replication (Kimbrough) is data-validation, not an after-cost tradeability study — corroborating that the *data* is real, not that the *trade* is.
4. **Mechanism fragility.** The proposed mechanism is periodic algorithmic execution flow (TWAP/clock-aligned schedulers). That is a crowding artifact: publicly documented (paper is public since 2026), venue-specific (single-venue Binance aggTrades), and exactly the class of effect that decays fastest post-publication. Our pilot's sign-inversion on 2026 Q2 is the expected signature of decay/crowd-out. Paper sample ends 2024-10; we would be trading 2026+.
5. **Program-level cost stack makes even a *true* small edge untradeable.** Binding context: ~$420 VIP-0 capital, measured 30-day directional expectancy −0.24R, entry AUC 0.531 on 270,830 OOS rows, and on 2026-07-26 three shadow probes hit their 30-outcome floors and ALL were gate-blocked — rsi2 lost money at 60% WR. A signal whose best *hypothesis-aligned* measured mean is −18.5 bps has no path through a stack where +20 bps incremental was the pre-registered minimum to even *expand the pilot*.
6. **Process-integrity risk.** Re-opening a hashed, expectation-registered, audit-confirmed NO_GO without reopen-bar evidence establishes the precedent that verdicts are renegotiable — which devalues every other row in the ledger and every future prereg hash.
7. **Ledger bookkeeping gap (flag for debate-engine, not a C3 argument):** `22_integration_report_c3.md` states "Ledger row added under Refuted (scoped)", but the current `refuted-families-ledger/SKILL.md` contains **no quarter-hour row** (verified by grep — only the unrelated quarterly-basis row matches). The correct remediation of this session is to ADD the missing scoped ledger row, not to run a new screen. Until that row exists, this exact re-litigation will keep recurring.

---

## AdverseEvidence

| # | Evidence | Type | Source |
|---|---|---|---|
| 1 | 0/6 variants pass frozen gates; aligned OOS means −18.5 to −33.0 bps after cost; WR 0.38–0.47; MC P>0 ≤0.08; DSR ≈0 | FACT (internal, hashed prereg, on-disk) | `22_screen_c3_quarter_hour_imbalance.{md,json}` |
| 2 | Expansion bar (+20 bps) missed by 39–53 bps; full harvest explicitly not authorized | FACT (internal) | `22_audit_c3_quarter_hour_imbalance.md` |
| 3 | "DO NOT ADD quarter-hour boundary columns … refuted" — 2026-07-26 dual-researcher mechanism sweep | FACT (internal) | `29_mechanism_sweep_verdict.md:44` |
| 4 | Clock-boundary windows raise transaction costs, no significant adverse price impact | Peer-reviewed external; **cited from the research brief — not independently retrieved in this pass** | Shynkevich, J. Futures Markets 2026, 46(5):904–930 |
| 5 | Preprint: no cost model, no multiplicity control, Section-6 tables unretrievable (4–12h claim unverifiable); OOS work targets a different horizon | External critique per brief + consistent with 22_ audit finding #2 | arXiv 2607.09426 v1/v2 |
| 6 | Adjacent families already refuted: hour-of-day/seasonality (0 survive OOS, 2026-06-02), formulaic alphas (443+, best IR≈0.45 pre-cost, 2026-05-25); VPIN aggTrades overlay CONFIRMED_NO_GO 2026-07-25 | FACT (ledger) | `refuted-families-ledger/SKILL.md` rows |
| 7 | Program floor: three probes at 30-outcome floors all gate-blocked 2026-07-26; 60% WR still net-negative | FACT (per brief / funnel) | promotion funnel context |

Separation note: items 1–3, 6 are verified FACTS from this repo. Item 4–5 external citations are taken from the brief as given; I could not verify them from this session (no web access used) and say so explicitly. Item 7 is brief-supplied program context consistent with repo state.

---

## WorstCase (what running the "full screen" would actually cost)

- **Compute/storage/quota:** the 3-month, 2-symbol pilot required ~2.7 GB of raw aggTrades. A 6-symbol × 2021–2026 harvest is plausibly 50–150 GB of downloads and hours-to-days of harvest/screen wall-time (INFERENCE from pilot scaling; not measured), plus one of the pipeline's scarce screen slots — displacing C2 gamma-expiry and the whale-network queue, which at least have *unmeasured* hypotheses.
- **False-confidence tail (the expensive one):** a full-period screen that includes 2021–2024 could produce in-sample-era "passes" (the paper's own discovery window) even though 2026 forward data is negative. That result would pressure a shadow probe → months of accrual on a decayed signal → a promotion debate anchored on pre-decay data. Every stage of that path consumes attention and quota and its terminal value is a bleed. This is the exact "paid for twice" failure this role exists to prevent — except here it would be paid for a *third* time, since the pilot already paid once.
- **Precedent damage:** one tolerated re-litigation of a hashed CONFIRMED_NO_GO converts ~2,400 refutations and 40+ ledger rows from binding verdicts into opening bids.
- **Opportunity cost at $420:** zero. There is no scenario in which this screen changes live behavior — even a GO could not clear the frozen promotion gate on an effect measured at negative forward mean, and the owner's capital cannot absorb boundary-window taker costs that a peer-reviewed study says are structurally elevated.

---

## EvidenceQuality

- Internal 22_* chain: **HIGH** — prereg hash frozen pre-outcome, expectation registered (NO_GO) before the run, harvest manifest hashed, audit passed all binding checks, artifacts verified on disk this session.
- 29_ mechanism sweep: **HIGH** (internal, dual-researcher, verified on disk).
- Shynkevich 2026: **MEDIUM-HIGH as cited** (peer-reviewed journal) but **unverified by me this session** — I did not retrieve the paper; the citation and its finding are taken from the research brief.
- arXiv 2607.09426 weaknesses: **MEDIUM** — the three-prong failure is per the brief and consistent with the 22_ audit; the underlying unretrievability of Section 6 is itself a documented verification failure, which cuts against the BULL side, not against this thesis.
- Overall: the thesis rests primarily on our own hashed measurement, which is the strongest evidence class this program produces.

## Confidence

**92 / 100.** The residual 8: (a) the pilot window is a single quarter (2026 Q2) and n per cell is 168–436 — a genuinely anomalous quarter cannot be fully excluded; (b) Shynkevich not independently retrieved; (c) constructions outside the pilot's frozen scope (maker-only boundary execution, cross-venue, sub-second) were never measured and are formally open as NEW candidates — but none of them is C3-as-proposed.

## InvalidateIf (exact stand-down conditions for this bear thesis)

1. **Artifact integrity failure:** recomputation shows the 22_ prereg hash does not match the run, or a demonstrated sign/residualization bug in `harvest_binance_aggtrades_qh.py` / the screen that, when fixed, flips the aligned OOS means positive after cost. (Verify, don't assume — the audit already checked hash freezing.)
2. **Reopen bar met verbatim:** peer-reviewed (or equivalently rigorous) **2026+** evidence testing the 4–12h clock-boundary effect **after realistic retail taker costs**, with FDR/DSR-grade multiplicity control and genuine out-of-sample validation **on post-2025 data**, showing it survives on Binance-class venues. The current arXiv preprint does not qualify; a future accepted, cost-aware version with retrievable 4–12h tables could.
3. **Forward re-measure contradicts the pilot:** a zero-cost-to-run forward extension (2026 Q3+, same frozen pilot spec, no re-tuning) showing aligned after-cost means ≥ +20 bps with WR ≥ 0.55 — i.e., evidence the Q2 window was the anomaly. This is the ONLY screen-shaped action I would not oppose, because it reuses the existing hashed spec and cannot cherry-pick.
4. **Scope carve-out (not an invalidation, a boundary):** a maker-first construction that *earns* the boundary spread Shynkevich documents rather than paying it is a different mechanism and would deserve its own NEW prereg on its own merits. Its existence would not rehabilitate C3-as-specified.

Absent 1–3, this thesis stands: **close C3 NO_GO, add the missing scoped ledger row, spend the next screen slot on an unmeasured hypothesis.**

## Sources

- `_workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.md` (+ `.json`, sha256 `7b33c63914c44a749b2cb57d3bec0dd1a1c33e593577d9eea3dc57e0fb2f1787`)
- `_workspace/strategy_pipeline/22_screen_c3_quarter_hour_imbalance.{md,json}` (verdict NO_GO, generated 2026-07-23T11:13:55Z)
- `_workspace/strategy_pipeline/22_audit_c3_quarter_hour_imbalance.md` (CONFIRMED_NO_GO; expansion not authorized)
- `_workspace/strategy_pipeline/22_integration_report_c3.md` (NO-OP; claims ledger row that is NOT present — bookkeeping gap)
- `_workspace/strategy_pipeline/29_mechanism_sweep_verdict.md` line 44 ("DO NOT ADD" quarter-hour columns)
- `.claude/skills/refuted-families-ledger/SKILL.md` (adjacent refutations: seasonality 2026-06-02, formulaic alphas 2026-05-25, VPIN 2026-07-25; reopen bar; NO quarter-hour row present — verified by grep)
- arXiv 2607.09426 (Kim & Hansen, "Quarter-Hour Effect") — as characterized in the brief and the 22_ prereg
- Shynkevich, "Trading Periodicity and Algorithmic Divide in Cryptocurrency Markets", J. Futures Markets 2026, 46(5):904–930 — cited from the research brief; not independently retrieved this session
- Research brief program facts: −0.24R 30d expectancy, AUC 0.531/270,830 rows, three probes gate-blocked at floor 2026-07-26, ~$420 VIP-0 capital
