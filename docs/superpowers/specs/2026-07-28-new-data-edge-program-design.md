# New-Data Edge Program — Design Spec

**Date:** 2026-07-28 · **Status:** REVISED per ai-reviewer verdict (REVISE → R1–R7 applied; original owner approval 01:13 local) · **Approach:** C — portfolio (microstructure accrues-then-screens, gamma-expiry accrues)
**Owner decision trail:** edge source = new-data classes the ~2,400 refuted pattern tests never examined (owner choice over F1 extensions, external signals, cost-squeezing); shape = Approach C; ai-reviewer consulted at owner direction 01:18, verdict REVISE (confidence 88), all seven required changes incorporated below.

## Context

Every cheap strategy family has been refuted with pre-registered gates (see `refuted-families-ledger`). Three probes matured the week of 2026-07-21 and failed the frozen gate honestly — two inside the 59–67% WR band while net negative after costs. The open question is not "which indicator" but **where an edge could live that the refutations never looked**. Two data classes qualify:

1. **Microstructure store** (`microstructure_features`, warehouse.sqlite): signed order-flow imbalance, book imbalance, depth, liquidity. **Measured reality (ai-reviewer census, 2026-07-28): 516 rows = 43 symbols × 12 bars, bybit-only, 2026-07-25 → 2026-07-27. No backfill exists and none is structurally possible** (order-book snapshots and REST trade windows are not historically retrievable). All evidence from this store is single-venue (bybit) evidence.
2. **Deribit chain snapshots** (`TradingBot_DeribitChainSnap_AM/PM` schtasks, both verified Ready/LastResult=0): **BTC only at present — zero ETH records exist** despite the original design intent. Payload carries `mark_iv` but no greeks.

This program produces **verdicts, not trades**. Base-rate expectation for any single screen is NO_GO, stated here so a clean kill is never re-litigated.

## Architecture — no new machinery

- Prereg artifacts: `_workspace/strategy_pipeline/NN_*` (next free index at implementation time).
- Execution: `strategy-evidence-pipeline` (edge-screener ↔ honesty-auditor), verdicts under the dual-model both-agree rule (`19_dual_model_loop_protocol.md`), slotted into the daily loop's S2 queue (max one heavy stage per UTC day; queue verified empty — C1 and C3 closed 2026-07-23).
- Outcomes: NO_GO → ledger row; INSUFFICIENT_DATA → ledger Open section + exact wait/harvest condition; CONFIRMED_GO → `shadow-probe-integration` as a **log-only probe** only.
- Promotion beyond log-only: frozen `core/promotion_gate.py` on ≥30 RESOLVED forward events + owner signature. **Zero live/paper order-path changes in this program** (see H3 scope fence below).

## Workstream A — microstructure screens (ACCRUING; earliest screenable ≈ 2026-08-24)

**Not active yet.** Cells needing a trailing-30d baseline require ~180 bars/symbol at 6/day; ~168 remain from the 12 on hand, giving **≈ 2026-08-24** as the earliest screen date. (R1)

**A0 (first, cheap, read-only): instrument census.** Must report, per symbol: bar counts and coverage; the **`asof_ts − bar_ts` lag distribution** (measured 4.09–7.13h — snapshots land 0.09–3.13h AFTER bar close); the **`window_trades_n` saturation rate** (measured 516/516 at the 1000-trade REST cap); **venue coverage** (bybit-only); and implied trade-window duration wherever derivable. A0's job is to be able to FAIL on exactly these defects, not merely count rows. Results are recorded in the prereg before the grid is frozen. (R3)

**Instrument repair (R2 — binding, chosen option (a)):** the stored flow features are NOT bar-aggregated OFI — a saturated 1000-trade window of unknown, symbol-varying duration, sampled post-close, places part or all of the window inside the first outcome bar (look-ahead by construction; contamination varies with liquidity, itself a confound). **The harvester (`core/microstructure_store.py`) will be extended to record window start/end and to bar-bound trade aggregation. This is a data-collection change on the schtask lane, not order flow. The flow-cell accrual clock RESTARTS at that deployment** (cost ≈ 2 days of existing data — acceptable). Until deployed, flow cells (H1) are not screenable at all. Book/depth cells additionally anchor outcome windows at `asof_ts`, never bar close, as defense in depth.

**Hypothesis grid (frozen and hashed BEFORE any outcome computation):**

| Cell | Hypothesis | Expression | Gate class |
|---|---|---|---|
| H1 | OFI continuation | bar-bounded signed-OFI (post-repair data only) in top/bottom decile vs trailing 30d → next-4h drift in flow direction | directional, full frozen gates |
| H2 | imbalance reversal | book-imbalance z-extreme AND liquidity below median → asof-anchored next-1–2 bar mean reversion | directional, full frozen gates |
| H3 | liquidity-collapse overlay | depth < p10 → volatility-expansion veto signal, **computed and reported only** | report-only ΔEV study (see fence) |

- **No cell may carry a price-oscillator or price-z leg** — binding prereg constraint; this is what keeps H2 a genuinely new evidence class rather than an RSI-MR re-skin. (reviewer condition)
- Multiplicity: the hashed prereg enumerates a **fixed m** (every direction × horizon × threshold cell counted; no "target"). **Stage-0 per-cell attrition does NOT shrink the Bonferroni denominator** — correction is over the enumerated m, not survivors. (R4)
- Trigger floor: Stage-0's ≥30 triggers per cell count **distinct bar timestamps** (or apply an explicit clustering discount) — 43 cross-correlated symbols in one 4h bar are ~1 bet, not 43 (precedent: unlock row's n=32/36 ≈ 19/22 independent). (R4)
- **OOS split (R7):** time-based, boundary fixed in the hashed prereg. Stated plainly: on a sample this thin the OOS half is ~15–20 days and the 0.55 OOS-WR floor is weakly informative; the verdict language must carry that caveat.
- Costs: repo-standard stressed model (1.5× fee, 2× slippage, exit floor). Gates: frozen — DSR ≥ 0.10, OOS-WR ≥ 0.55 (directional cells), MC P(total>0) ≥ 0.95, maxDD p95 ≤ 0.25.

**H3 scope fence (R5):** this program authorizes **computing and reporting ΔEV only**. The pass bar is **ΔEV > 0 with MC P(ΔEV>0) ≥ 0.95 AND a flow-retention floor ≥ 10%** (screen-13 precedent for the floor; note screen 13's own row disclaims edge — "WR-band protection, NOT edge" — so no softer bar can be justified by it, and ΔEV ≥ 0 alone is a zero bar a pure-noise filter passes ~50% of the time). **Wiring any veto into any lane is a separate proposal, and because it alters paper order flow it is owner-signed.** This supersedes and resolves the apparent conflict with Out-of-scope.

## Workstream B — C2 gamma-expiry frozen spec (accruing; BTC-only until ETH capture lands)

- **Event definition:** Deribit option expiry where the dealer-gamma proxy — computed *only* from chain snapshots (OI by strike) — exceeds a pre-registered threshold at T−1d.
- **Counting-start = the prereg hash timestamp (R6).** Snapshots already on disk (accruing since 07-24, containing already-passed expiries) are **excluded from the counted sample**; they may serve as a *disclosed formula-design set* or not at all. Without this, "the spec predates every outcome it is judged on" would be false on arrival.
- **Pinned in the hashed prereg:** the full gamma model (payload has no greeks — BS inputs, IV source = `mark_iv`, day-count and rate convention are researcher degrees of freedom and must be frozen); **which snapshot is the T−1d observation (the AM snapshot, ~07:30Z)**; entry window; exit windows (pin arm at expiry, release arm at release horizon); direction rule; stressed costs; notational 1%-risk sizing.
- **Arms share expiries:** pin and release arms observe the SAME qualifying events, so the floor is **~30 events total**, not 60.
- **Timeline, honestly (R6):** at BTC-only accrual the ≥30 floor is **~15–30 months**. Extending the snapshot task to ETH is cheap, touches no live path, and roughly halves that; it is Step B0 of implementation. Until ETH flows, the BTC-only rate governs the stated timeline.
- The 08:00-reversal proxy substitute remains forbidden (committee ruling, 2026-07-22).

## Honesty rails (binding)

1. Prereg committed/hashed before outcomes (process rule, 2026-07-17).
2. Stage-0 is a stopping rule only (2026-07-25 precedent: VPIN); applied per-cell (stricter than the skill's all-cells minimum), and per R4 its attrition never shrinks the multiplicity denominator.
3. Both-agree (Fable + Codex) at every verdict; disagreement parks the item.
4. Every terminal verdict lands in the ledger. **Per this rail, C2's INSUFFICIENT_DATA status (recorded 2026-07-22 in the CLAUDE.md log but absent from the ledger) is owed an Open-section ledger row; adding it is part of implementation (U1).**
5. No probe without CONFIRMED_GO; no promotion without frozen gate + owner signature.
6. Public repo: raw data manifests and account-derived artifacts stay in gitignored paths; the spec records content hashes and local paths only.

## Testing & verification

- Screen scripts under `research/` with fixture-driven unit tests; no network and no live warehouse dependency in tests.
- The honesty-auditor independently reproduces headline counts read-only (`mode=ro`) before any verdict stands.
- The harvester window-bounding change (R2a) ships with tests proving bar-bounded aggregation and recorded window bounds; its deployment date is the flow-cell accrual epoch.
- **Known downstream hazard (reviewer flag, out of this spec's scope but recorded):** `microstructure_store` rows key to `bar_ts` and advertise joining to probe `signal_bar_ts`; given the measured post-close sampling, that join hands post-close information to ANY consumer. Any future consumer must anchor at `asof_ts` until R2a lands.

## Out of scope

New probes before a CONFIRMED_GO; any live or paper order-path change (including wiring the H3 veto — owner-signed separate proposal per the scope fence); new data purchases; re-screening refuted families; the 08:00-reversal C2 substitute; universe changes. The AccBand/MCP live lane is untouched.
