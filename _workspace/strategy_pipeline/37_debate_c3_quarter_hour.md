# 37 — Debate memo: C3 quarter-hour clock-boundary order imbalance (4–12h horizon)

**Role:** debate-engine (investment-committee)
**Date:** 2026-07-26
**Inputs:** `37_bull_c3_quarter_hour.md`, `37_bear_c3_quarter_hour_imbalance.md`
**Question posed by brief:** proceed to hashed prereg + full screen, or close NO_GO?

---

## 0. Premise correction (verified independently by this engine, not taken from either thesis)

The brief's framing ("C3 has sat in the queue since 2026-07-22") is **factually stale**. Verified on disk this session:

- `22_prereg_c3_quarter_hour_imbalance.{md,json}` — prereg sha256 `7b33c63914c44a749b2cb57d3bec0dd1a1c33e593577d9eea3dc57e0fb2f1787`, frozen before harvest/screen (audit binding check PASS).
- `22_screen_c3_quarter_hour_imbalance.md` (2026-07-23T11:13:55Z) — **NO_GO, 0/6 residual variants pass.** Aligned OOS means after cost: H4 −23.3 bps (n=436, WR 0.383), H8 −33.0 bps (n=242, WR 0.393), H12 −18.5 bps (n=168, WR 0.470). DSR ≤0.003 everywhere; MC pass False on all 6 cells; joint PBO 0.078 (informational). Best aligned mean missed the frozen ≥+20 bps expansion bar by ~38.5 bps.
- `22_audit_c3_quarter_hour_imbalance.md` — **CONFIRMED_NO_GO**; 6-symbol / 2025–26 expansion **explicitly not authorized**.
- `22_integration_report_c3.md` — NO-OP; claims "Ledger row added under Refuted (scoped)".
- `29_mechanism_sweep_verdict.md:44` — "**DO NOT ADD** | quarter-hour boundary columns … (both refuted above)."
- `refuted-families-ledger/SKILL.md` — **NO quarter-hour row exists** (full ledger read this session; Refuted table rows checked individually). The integration report's claim is unfulfilled. This bookkeeping gap is the direct cause of C3 resurfacing as "queued" in this brief.

So the decision under debate — "should the screen be run?" — was answered empirically three days ago, at zero capital risk, under a hash frozen before outcomes. Both theses discovered this independently and converged.

## 1. SideScores

| Dimension | BULL | BEAR |
|---|---|---|
| Factual accuracy (vs disk) | HIGH — every load-bearing claim reverified | HIGH — every load-bearing claim reverified |
| Intellectual honesty | HIGH — refused to manufacture a pro-screen case; flagged Kimbrough as unverifiable and excluded it | HIGH — separated FACT/INFERENCE; disclosed Shynkevich not independently retrieved |
| Completeness | MEDIUM-HIGH — process-vindication framing adds little decision weight | HIGH — adverse-evidence table, cost-stack context, precedent-risk argument, sharpest stand-down condition (forward no-retune extension) |
| Weakest element | InvalidateIf #4 ("prove Q2 unrepresentative") is vague — no operational test stated | WorstCase harvest sizing (50–150 GB) is unmeasured extrapolation (self-flagged); "cost model generous" prong rests on an unretrieved paper |
| Net | **Strong, honest, essentially concedes** | **Strong, slightly more complete** |

This is NOT an average of two weak opinions. Both sides independently reverified the same hashed artifact chain and reached the same terminal action; the bear carries marginally more weight because its stand-down condition #3 (a zero-cost forward Q3+ extension of the SAME frozen spec, no re-tuning) is the only operationally precise path back — the bull's equivalent condition is under-specified.

## 2. Challenges pressed on each side

**Against BULL:**
- "The decision to screen it was correct" is self-serving process vindication. Accepted only because the prereg registered expectation NO_GO before the run and the harvest cost ~2.7 GB, not because the outcome retroactively blesses the queueing.
- Bull's residual 10% on the bookkeeping deliverable cites working-tree uncertainty (ledger modified-uncommitted). Resolved: this engine read the current working-tree ledger in full — the row is absent from the version that is operationally binding. The gap is real.

**Against BEAR:**
- The "aligned worse than contrarian" pattern (used to invoke Shynkevich's cost mechanism) holds at H4 (−23.3 vs −16.7) and H8 (−33.0 vs −7.0) but **inverts at H12** (−18.5 aligned vs −21.5 contrarian). The mechanism story is suggestive, not uniform — it does not change the verdict (all 6 cells fail everything) but the memo records the nuance so the Shynkevich-consistency claim is not over-read later.
- Pilot window is one quarter (2026 Q2), n=168–436/cell. Bear concedes this in its residual-8. It is why the forward-extension stand-down condition matters and why the ledger row must be SCOPED (BTC/ETH, 2026 Q2 pilot, taker), not family-wide beyond what was measured.

## 3. UnsupportedClaims (both sides, correctly self-flagged; confirmed unsupported by this engine)

1. Kimbrough replication + Duke/Yonsei vetting — asserted only in the brief; no local artifact; both sides excluded it from weight. Even if true, a data-validation replication corroborates the dataset, not after-cost tradeability.
2. Shynkevich JFM 2026 content — cited from the brief by both sides; not retrieved this session. Treated as corroborating-only (see §5); the verdict does not depend on it.
3. arXiv 2607.09426 three-prong failure (no costs, no multiplicity, Section 6 unretrievable) — per brief + consistent with the 22_ audit; not independently re-verified here.
4. Bear's 50–150 GB full-harvest estimate — inference from pilot scaling, unmeasured.

None of these is load-bearing: the decision rests entirely on the hashed internal 22_* chain, which was verified.

## 4. KeyUnknowns

1. Whether 2026 Q2 was an anomalous quarter for the mechanism — only resolvable via the no-retune forward extension (bear InvalidateIf #3), never via a 2021–24 harvest (that would fit the paper's own discovery era; the false-confidence trap both theses name).
2. True boundary-window slippage on our venue at VIP-0 (Shynkevich says elevated; our flat model may be generous) — unknown sign-magnitude, but it can only make the measured negatives worse.
3. The four unscreened study coins (XRP/SOL/DOGE/ADA) — formally open, but only as a NEW alt-scoped prereg with reopen-bar-grade external evidence; the pilot's expansion bar failure forecloses expansion of THIS prereg.
4. Maker-first constructions that would EARN the boundary spread rather than pay it — a different mechanism, out of scope of this verdict (both sides agree).

## 5. Peer-review weighting (explicit, per committee instruction)

External-tier ranking applied: **peer-reviewed journal finding (Shynkevich, JFM 2026) > non-peer-reviewed preprint (arXiv 2607.09426) even with an independent replication**, because (a) the replication validates the data, not the trade — it has no cost model to replicate since the preprint has none; (b) the preprint's central 4–12h evidence (Section 6 tables) is unretrievable, so its promotable claim is unverifiable; (c) the peer-reviewed finding addresses precisely the margin that kills retail execution (transaction costs at boundaries). HOWEVER — both externals are subordinate here: the controlling evidence class is our **own hashed, pre-registered, after-cost measurement on our venue and era**, which is adverse. The peer-reviewed finding merely agrees with what we measured; the preprint's hypothesis is what we falsified forward. If the external hierarchy had pointed the other way, the internal measurement would still control.

## 6. LedgerCheck

- Adjacent refuted families: hour-of-day/seasonality (2026-06-02), formulaic alphas (2026-05-25), VPIN aggTrades overlay (2026-07-25) — all present in ledger. The 22_ audit explicitly placed C3 ADJACENT to (not reopening) the first two.
- Reopen bar: NOT met by the arXiv preprint (fails costs/multiplicity/verifiability prongs). Shynkevich is peer-reviewed but adverse — it supports closure.
- **Defect found: the scoped C3 refutation row promised by `22_integration_report_c3.md` is ABSENT from `refuted-families-ledger/SKILL.md`.** Per ledger Usage ("When a new screen concludes NO_GO, ADD a row"), this row must be added. This engine's write scope is confined to `_workspace/strategy_pipeline/` — the ledger edit is assigned to the orchestrator/owner. Proposed row text:

  > | Quarter-hour clock-boundary opening order-imbalance → 4–12h drift (Binance USDT-M majors, taker, residualized vs ret_1h/ret_4h/log_vol_1h) | CONFIRMED_NO_GO: prereg `22_prereg_c3_quarter_hour_imbalance` (sha256 `7b33c639…`) frozen pre-outcome; BTC+ETH 2026-Q2 pilot, 8,736 events/symbol (manifest `d3b5632d…`); **0/6 residual variants pass** — aligned OOS means H4 −23.3 / H8 −33.0 / H12 −18.5 bps after cost, WR 0.38–0.47 < 0.55 floor, MC pass False all cells, DSR ≤0.003; +20 bps expansion bar missed by ~38 bps, 6-symbol/2025–26 expansion NOT authorized. External: Shynkevich JFM 2026 46(5):904–930 (peer-reviewed — boundaries carry elevated transaction costs, no exploitable drift) corroborates; arXiv 2607.09426 fails reopen bar (no costs, no multiplicity, §6 tables unretrievable). NOT covered (NEW prereg only): XRP/SOL/DOGE/ADA, maker-first boundary execution, cross-venue, sub-second; a no-retune forward Q3+ extension of the SAME frozen spec is the only sanctioned re-measure. Artifacts `22_*` | 2026-07-23 |

## 7. Recommendation

**REJECT — close C3 NO_GO. Do not run a new pre-registration or full screen.**

Rationale in one line: the exact proposed action was executed under a pre-outcome-frozen hash on 2026-07-23 and lost on our own venue, fees, and forward era (0/6 cells, best aligned mean −18.5 bps vs a +20 bps bar); peer-reviewed external evidence points the same direction; nothing meeting the ledger's reopen bar has arrived since; and re-opening a hashed CONFIRMED_NO_GO without that bar would convert every ledger row into an opening bid.

**ConditionsForRevise (none required for this verdict; recorded for the future):**
- R1 (bookkeeping, REQUIRED regardless): orchestrator/owner adds the missing scoped ledger row (§6). Until it exists, this candidate will keep resurfacing as "queued" — this brief is the proof.
- R2 (only sanctioned screen-shaped follow-up): zero-cost forward extension on 2026 Q3+ data under the SAME frozen 22_ spec, no re-tuning — legitimate because it cannot cherry-pick. Optional, low priority; requires its own dated note, not a new construct.
- R3 (reopen path): any of the bull/bear InvalidateIf conditions met with evidence quoted verbatim → NEW hashed prereg (alt-scoped or maker-first), never a re-run of the failed pilot.

**Deadlock status:** none — both sides converged after independent verification; no rebuttal round needed.

## Sources

- `_workspace/strategy_pipeline/37_bull_c3_quarter_hour.md`; `37_bear_c3_quarter_hour_imbalance.md`
- `_workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.{md,json}` (sha256 `7b33c639…`)
- `_workspace/strategy_pipeline/22_screen_c3_quarter_hour_imbalance.{md,json}` (NO_GO, 2026-07-23T11:13:55Z — 6-variant table reverified this session)
- `_workspace/strategy_pipeline/22_audit_c3_quarter_hour_imbalance.md` (CONFIRMED_NO_GO — reverified)
- `_workspace/strategy_pipeline/22_integration_report_c3.md` (NO-OP; unfulfilled ledger-row claim — reverified)
- `_workspace/strategy_pipeline/29_mechanism_sweep_verdict.md:44` (reverified via grep)
- `.claude/skills/refuted-families-ledger/SKILL.md` (full read; no quarter-hour row; reopen bar §)
- arXiv 2607.09426; Shynkevich JFM 2026 46(5):904–930 — both via brief/prereg citation, NOT independently retrieved this session (flagged §3)
