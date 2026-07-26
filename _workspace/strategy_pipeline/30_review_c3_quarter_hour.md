# 30 — AI Reviewer verdict: C3 quarter-hour / clock-boundary order imbalance

**Reviewer:** AI Reviewer (Opus 5), first execution under owner directive 2026-07-26 (commit `3fb8da4`)
**Date:** 2026-07-26
**Upstream:** `37_bull_c3_quarter_hour.md`, `37_bear_c3_quarter_hour_imbalance.md`, `37_debate_c3_quarter_hour.md`
**Ledger check (mandatory, done first):** `.claude/skills/refuted-families-ledger/SKILL.md` read in full. **No quarter-hour / clock-boundary row exists** (grep + full read). The only `quarter` hit is line 41, "Quarterly-futures basis leg-swap" — an unrelated family. C3 is therefore not blocked by an existing ledger row; it is blocked by its own measured verdict, which was never written down.

---

## VERDICT

**REJECT** — C3 does **not** proceed to a new hashed pre-registration or full screen. Close **CONFIRMED_NO_GO**.

The brief's premise is stale, and I verified this independently rather than accepting it from the theses: C3 was already pre-registered, harvested, screened, and audit-closed on **2026-07-23**. The action being requested has already been performed and lost on our own data.

I reject the proposal on evidence that is **stronger** than the debate's, and materially different in kind: the kill is **cost-model-independent** (see SURVIVED REFUTATION §3). Even at literally zero fees and zero slippage, C3 falls ~13.8× short of its own pre-registered expansion bar.

## SCOPE

**What this verdict authorizes:**
- Recording C3 as CONFIRMED_NO_GO in the program record.
- Adding one scoped row to the refuted-families ledger (text drafted in §HUMAN/ORCHESTRATOR ACTION). **I did not perform this edit** — `.claude/skills/refuted-families-ledger/SKILL.md` is outside my instructed write scope (`_workspace/strategy_pipeline/` only). The row is APPROVED as to content; the edit is assigned.

**What this verdict explicitly does NOT authorize:**
- No trade, no order flow, no live-path change, no shadow probe, no signal wiring.
- No 6-symbol expansion, no 2021–24 full-period harvest.
- **No forward extension.** I am explicitly *not* authorizing bear's R2 (no-retune forward Q3+ extension). Leaving it "formally open" would read as permission. A frozen spec whose best cell is **+1.45 bps gross** has no arithmetic path to a **+20 bps** bar; it needs new *evidence*, not more of the same data.
- Nothing here touches `docs/CONTROLLED_LIVE_CHECKLIST.md`, `CONTROLLED_LIVE`, or the frozen gate.

## EVIDENCE REVIEWED

Chain of custody verified cryptographically end-to-end. Every item below I computed or reproduced myself this session; I did not take a number from any thesis.

| Check | Method | Result |
|---|---|---|
| Prereg content unaltered since freeze | sha256 of `22_prereg_*.md` raw bytes | **MATCH** `7b33c639…` — bit-exact |
| Prereg frozen *before* outcomes | `frozen_utc` 2026-07-23T10:30:00Z vs screen `generated_utc` 11:13:55Z | **PASS** — frozen 43 min before |
| Harvest manifest unaltered | sha256 of manifest bytes, CRLF (Windows `write_text`) | **MATCH** `d3b5632d…` |
| Harvest data unaltered | sha256 of both parquets vs `file_sha256` | **MATCH** both, bit-exact |
| Screen bound to this prereg + data | both hashes carried in `22_screen_*.json` | **PASS** |
| Gate results | independent re-read of `22_screen_*.json` | 0/6 variants pass; all `mc_pass: false` |
| Funding genuinely charged | `research/screen_c3_quarter_hour_imbalance.py:223,242-245`; `data/funding_history/binance_{BTC,ETH}.csv` | **PASS** — 273 settlements/symbol in window (= 91d × 3/day, full coverage) |
| Independent corroboration | `29_mechanism_sweep_verdict.md:44` | "DO NOT ADD quarter-hour boundary columns" |

Measured results (my read of the screen JSON, matching the screen `.md`):

| Variant | n_OOS | mean bps (after cost) | WR | DSR | MC P(>0) |
|---|---:|---:|---:|---:|---:|
| H4_aligned | 436 | −23.26 | 0.383 | 1.2e-07 | 0.001 |
| H8_aligned | 242 | −33.03 | 0.393 | 1.0e-06 | 0.000 |
| H12_aligned | 168 | −18.55 | 0.470 | 0.0031 | 0.080 |

Expansion bar +20 bps; best aligned −18.55 bps → **missed by 38.55 bps** (verified: `20.0 − (−18.5496) = 38.5496`). Joint PBO 0.078. Delta-drift kill not triggered (raw also failed, so the "raw passes while residual fails" condition never arose).

## SURVIVED REFUTATION

I attacked the closure conclusion three ways. It survived all three, and one attack made it stronger.

**1. "The pilot is a 3-month single-regime window; the paper covers 2021–24."**
Survives, but this is the one genuinely open point (see UNVERIFIED). It does not justify the requested action: running the full 2021–24 harvest would test the paper on **its own discovery era**, which cannot produce genuine out-of-sample evidence and is exactly the false-confidence trap this pipeline exists to block. The pilot, by contrast, is a clean disjoint forward window — the strongest available test of the paper's claim.

**2. "The after-cost numbers are just a punitive cost assumption."**
This attack **failed decisively**, and killing it is my main contribution. Decomposing the screen output:

| Horizon | aligned | contrarian | midpoint | **alpha gross of fee+slip** |
|---|---:|---:|---:|---:|
| H4 | −23.26 | −16.74 | **−20.0000** | **−3.26** |
| H8 | −33.03 | −6.97 | **−20.0000** | **−13.03** |
| H12 | −18.55 | −21.45 | **−20.0000** | **+1.45** |

The midpoint is **exactly −20.0000 bps at all three horizons**. That is the symmetric round-trip charge, confirmed in code at `screen_c3_quarter_hour_imbalance.py:84` — `rt_cost = 2 * (fee_per_side + slip_per_side)` = 2 × (5 + 5) bps. Funding flips sign with direction (`:86-89`), so it **cancels in the midpoint** and sits inside the residual column; at ≈0.1 bps/settle measured it shifts these figures by ≤0.15 bps — immaterial.

So the honest gross-alpha figures are **{−3.26, −13.03, +1.45} bps** — sign-scattered across horizons at n=168–436, i.e. **no detectable signal**, not an inverted one. And the decisive point: **even at zero fee and zero slippage, the best cell (+1.45 bps) is 13.8× short of the +20 bps bar.** The kill does not depend on the cost model at all. A critic disputing our fee/slip assumptions cannot rescue C3.

**3. "Both theses agree, so this may be groupthink rather than convergence."**
Survives: both independently re-verified the same hashed chain, and I re-verified it a third time from the raw artifacts and reproduced every hash. Agreement here is corroboration, not an echo. Bull's honesty is notable — it conceded 3/100 on proceeding rather than defending its assigned side.

## KILLED / DEMOTED

Claims I removed or downgraded from the upstream record. None change the verdict; all would corrupt the record if left standing.

1. **KILLED — "aligned is more negative than contrarian, consistent with Shynkevich's cost mechanism"** (bear headline; carried into the debate memo).
   Refuted by construction. Aligned and contrarian pay an **identical, symmetric** cost — that is precisely why the midpoint is exactly −20.0000. The asymmetry is nothing but the sign of the gross signal. Further, the pilot's **flat** 20 bps model is structurally incapable of testing a claim about *elevated boundary-minute* costs. The pilot neither confirms nor refutes Shynkevich. The debate half-caught this (correctly noting the H12 inversion) but retained the mechanism claim; it must not be cited again.

2. **DEMOTED — "DSR ≤ 0.003"** (debate memo). Max DSR across all cells is **0.02554** (`H8_aligned_raw`), ~8× the stated figure. All remain far below the 0.10 floor; conclusion unaffected.

3. **DEMOTED — "MC P(>0) ≤ 0.08"** (bear). True only for the *aligned residual* cells. Across all cells the max is **0.2595** (`H8_aligned_raw`). All remain far below the 0.95 floor; conclusion unaffected.

4. **DEMOTED — WR 0.38–0.47 as evidence the signal is bad.** `WR_aligned + WR_contrarian` = 0.775 / 0.835 / 0.881 — all **< 1.0**, because a trade must move >20 bps to count as a win, so both directions lose inside the cost band. Under a pure null, expected WR at H4 is ≈0.39. The OOS-WR ≥ 0.55 gate is therefore **near-unpassable at these horizons regardless of signal quality**. The gate is arithmetic and I do not touch it — but the WR failure is largely structural and must not be quoted as "the signal is inverted."

5. **EXCLUDED from evidence weight (single-source, unverifiable locally):** the Kimbrough replication, the Duke/Yonsei seminar vetting, and the content of arXiv 2607.09426 — all asserted only in the brief. Both theses correctly self-flagged these. Note my §3 finding makes them moot: the kill stands on internal data alone.

6. **CORRECTED — process defect.** `22_integration_report_c3.md` states "Ledger row added under Refuted (scoped)." **This is false.** No such row exists. I pinned down the cause: the C1 row, closed the *same day* (2026-07-23), **is** present at ledger line 46, as are the 07-24 AccBand and 07-25 VPIN rows. So this is an **isolated omission, not systemic loss or reversion** — and it is precisely why C3 resurfaced today as "queued," costing a full bull/bear/debate/review cycle.

7. **PROCESS NOTE — no dual-model agreement this cycle.** `30_verdict_c3_codex.md` is **0 bytes**. Under the standing dual-model loop (`19_dual_model_loop_protocol.md`), the both-agree rule gates *action*, not refusal — so this is immaterial to a REJECT. It would have **blocked an APPROVE.** Recorded so the gap is visible.

## UNVERIFIED

Named honestly; none assumed in the proposal's favour.

- **Shynkevich (JFM 2026, 46(5):904-930)** — not retrieved this session; known only from the brief. I give it **zero weight**. It happens to point the same way as my finding, but I do not lean on it, and per §KILLED-1 the pilot cannot test its mechanism anyway.
- **arXiv 2607.09426 content, the Kimbrough replication, Duke/Yonsei vetting** — not independently retrievable locally. Excluded.
- **Whether 2026 Q2 was regime-anomalous** relative to the paper's 2021–24 window. Genuinely unresolved and unresolvable from local data. This is the honest residual uncertainty in this verdict — but note it cuts against C3's *gross* alpha too, which is ≈0, not merely cost-crushed.
- **Manifest self-hash caveat:** reproducing `d3b5632d…` required knowing the harvest script writes CRLF via Windows `write_text`. I confirmed this at `scripts/harvest_binance_aggtrades_qh.py:159-161` rather than assuming; five LF-based serializations failed first. Flagging so a future auditor does not mistake this for tampering.

## Stage-0 feasibility gate — required?

**Not applicable to the closure decision, and it must not be run as a way to keep C3 alive.**

- **Was it satisfied?** Implicitly yes. The pilot produced n_OOS 168–436 per cell, all ≥ 30. No cell was trigger-starved.
- **Pass criterion, were a future C3-family prereg ever authorized:** ≥ 30 **non-overlapping** OOS events per `(symbol-pool × horizon × direction)` cell; if every cell is < 30 → STOP, INSUFFICIENT_DATA.
- **Binding caution:** Stage-0 is **not** the binding gate here and passing it must never be cited in C3's favour. The non-overlap rule yields ~2 trades/day/symbol at H12, so roughly one month of forward BTC+ETH data clears 30 events trivially. Stage-0 measures *whether we can measure*, not whether there is an edge. The binding constraint is the **+20 bps expansion bar — missed by 38.5 bps after cost, and by 18.5 bps even gross of fee and slippage.**

## CONFIDENCE

**96 / 100.**

Basis: the entire evidence chain is cryptographically verified (prereg content, freeze-before-outcome ordering, manifest, and both data files all reproduce bit-exact), the gate arithmetic is re-derived from raw JSON, and the central kill is now **independent of the cost model** — the weakest link in the original screen. Three independent lines converge: the hashed pilot, the gross-alpha decomposition, and `29_mechanism_sweep_verdict.md:44`.

The 4 points withheld are for the one thing I could not resolve: whether 2026 Q2 is regime-atypical versus the paper's 2021–24 window. I withhold them deliberately rather than round up — but I note the residual doubt is small, because the failure is not marginal in any dimension.

## HUMAN / ORCHESTRATOR ACTION REQUIRED

No escalation. Nothing here touches real capital; this decision is final for PAPER scope under the owner directive.

**One assigned task (R1, required — outside my write scope):** add this row to the `## Refuted` table of `.claude/skills/refuted-families-ledger/SKILL.md`. Content APPROVED as written:

```
| Quarter-hour clock-boundary opening order-imbalance → 4–12h perp drift (Binance USDT-M; signed aggressor $-imbalance over [T, T+10s) at UTC :00/:15/:30/:45, residualized vs ret_1h/ret_4h/log_vol_1h; H4/H8/H12 × aligned/contrarian) | CONFIRMED_NO_GO: prereg `22_prereg_c3_quarter_hour_imbalance` (sha256 `7b33c639…`, frozen 2026-07-23T10:30Z, 43 min before the screen; content re-verified bit-exact 2026-07-26). Pilot BTC+ETH 2026-04→06, 8,736 boundaries/symbol, harvest manifest `d3b5632d…` (re-verified; both parquets bit-exact). **0/6 residual variants pass** — aligned OOS mean H4 −23.3 / H8 −33.0 / H12 −18.5 bps after cost, WR 0.383–0.470 vs 0.55 floor, MC P(>0) ≤0.08 on aligned cells, max DSR 0.0255 ≪ 0.10 floor, joint PBO 0.078; expansion bar +20 bps missed by 38.5 bps. **Cost-model-INDEPENDENT kill (AI-reviewer 2026-07-26):** the aligned/contrarian midpoint is exactly −20.0000 bps at all three horizons — the symmetric 2×(5bps fee + 5bps slip) charge — so alpha gross of fee+slip is H4 −3.26 / H8 −13.03 / H12 **+1.45** bps: sign-scattered ≈ no detectable signal. Even at ZERO fee and slippage the best cell is 13.8× short of the bar. Funding verified charged (273 settlements/symbol, ≈0.1 bps — immaterial). Delta-drift kill not triggered. ⚠ WR 0.38–0.47 is largely a cost-band artifact (WR_aligned+WR_contrarian < 1.0), NOT signal inversion — do not re-cite it as such. NOT covered (NEW hashed prereg only): XRP/SOL/DOGE/ADA, maker-only/resting-limit boundary constructions, sub-minute horizons (latency-excluded by prereg), and the 2021–24 full period (the paper's own discovery era — cannot yield genuine OOS). Corroborated independently by `29_mechanism_sweep_verdict.md:44` ("DO NOT ADD quarter-hour boundary columns"). Artifacts `22_prereg/screen/audit/integration_c3_*`; harvest `data/aggtrades_qh/` (gitignored) | 2026-07-23 |
```

**Two recommended (not required):**
- **R2:** correct the false completion claim in `22_integration_report_c3.md` line 7 once the row exists.
- **R3:** the integration step asserted a ledger write it never performed, and nothing caught it for three days. Consider having the integration agent *verify by grep* before claiming a ledger row was added — this omission cost a full four-agent cycle.
