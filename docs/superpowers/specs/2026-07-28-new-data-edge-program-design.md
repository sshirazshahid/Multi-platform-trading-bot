# New-Data Edge Program — Design Spec

**Date:** 2026-07-28 · **Status:** APPROVED (owner, 01:13 local) · **Approach:** C — portfolio (microstructure leads, gamma-expiry accrues)
**Owner decision trail:** edge source = new-data classes the ~2,400 refuted pattern tests never examined (owner choice over F1 extensions, external signals, cost-squeezing); shape = Approach C over A-only / B-only.

## Context

Every cheap strategy family has been refuted with pre-registered gates (see `refuted-families-ledger`): MA/EMA/RSI/MACD, breakout, candlestick, momentum, pairs, funding-direction, ML forecasters, indicator confluence. Three probes matured the week of 2026-07-21 and failed the frozen gate honestly — two inside the 59–67% WR band while net negative after costs. The open question is therefore not "which indicator" but **where an edge could live that the refutations never looked**. Two data classes qualify and both are already flowing:

1. **Microstructure store** (`microstructure_features`, warehouse.sqlite): signed order-flow imbalance, book imbalance, depth, liquidity — 43 symbols, 4h bars, live since 2026-07-26 (d0413f1) with backfill. Never screened.
2. **Deribit chain snapshots** (`TradingBot_DeribitChainSnap_AM/PM` schtasks): BTC/ETH option chains captured twice daily for the C2 gamma-expiry candidate (INSUFFICIENT_DATA in the ledger's Open section). Accruing at ~2–4 qualifying expiry events/month toward the ≥30-event floor.

This program produces **verdicts, not trades**. Base-rate expectation for any single screen is NO_GO, stated here so a clean kill is never re-litigated.

## Architecture — no new machinery

- Prereg artifacts: `_workspace/strategy_pipeline/NN_*` (next free index at implementation time).
- Execution: `strategy-evidence-pipeline` (edge-screener ↔ honesty-auditor), verdicts under the dual-model both-agree rule (`19_dual_model_loop_protocol.md`), slotted into the daily loop's S2 queue (max one heavy stage per UTC day).
- Outcomes: NO_GO → ledger row; INSUFFICIENT_DATA → ledger Open section + exact harvest/wait condition; CONFIRMED_GO → `shadow-probe-integration` as a **log-only probe** only.
- Promotion beyond log-only: frozen `core/promotion_gate.py` on ≥30 RESOLVED forward events + owner signature. **Zero live-path changes in this program.**

## Workstream A — microstructure screens (active)

**Step A0 (first, cheap): data-depth census.** Read-only count of bars per symbol/feature in `microstructure_features`, plus trailing-window coverage. If the trailing-30d percentile baselines H1/H2 need are not yet computable, the affected cells stop at Stage-0 INSUFFICIENT_DATA with the accrual date when they become screenable. The census result is recorded in the prereg artifact before the grid is frozen.

**Hypothesis grid (frozen and hashed BEFORE any outcome computation):**

| Cell | Hypothesis | Expression | Gate class |
|---|---|---|---|
| H1 | OFI continuation | signed-OFI in top/bottom decile vs trailing 30d → next-4h drift in flow direction | directional, full frozen gates |
| H2 | imbalance reversal | book-imbalance z-extreme AND liquidity below median → next-1–2 bar mean reversion | directional, full frozen gates |
| H3 | liquidity-collapse overlay | depth < p10 → volatility-expansion veto/timing filter for the existing band lane | overlay: after-cost ΔEV vs baseline ≥ 0 with MC support; can only VETO, never generate entries |

- Multiplicity: Bonferroni across the full cell count (directions × horizons × thresholds counted explicitly in the prereg; keep the grid small — target m ≤ 12).
- **Stage-0 feasibility (binding stopping rule):** ≥30 triggers per cell on the pre-registered thresholds or STOP INSUFFICIENT_DATA. Never converts a NO_GO to GO; passing grants nothing.
- Costs: repo-standard stressed model (1.5× fee, 2× slippage, exit floor).
- Gates: frozen — DSR ≥ 0.10, OOS-WR ≥ 0.55 (directional cells), MC P(total>0) ≥ 0.95, maxDD p95 ≤ 0.25; H3 judged on after-cost ΔEV vs the band-lane baseline, per the band-conditional precedent (screen 13).
- Timeline: A0 within days; screen when Stage-0 passes.

## Workstream B — C2 gamma-expiry frozen spec (accruing)

- **Event definition:** Deribit BTC/ETH option expiry where the dealer-gamma proxy — computed *only* from the twice-daily chain snapshots (OI by strike; exact formula pinned in the spec artifact, e.g. OI-weighted gamma concentration and max-pain/max-gamma strike distance) — exceeds a pre-registered threshold at T−1d.
- **Hypothesis:** pre-expiry pinning toward the max-gamma strike; post-expiry release drift; both expressed on the perp.
- **Pinned before the first counted event:** entry window (T−1d snapshot time), exit windows (expiry + release horizon), direction rule, stressed costs, notational 1%-risk sizing, per-arm accounting (pin arm, release arm scored separately).
- **Integrity:** spec artifact content-hashed and committed before any qualifying event is evaluated — the spec predates every outcome it is judged on. The 08:00-reversal proxy substitute remains forbidden (committee ruling, 2026-07-22).
- **Floor:** ≥30 qualifying events per arm (~months at current accrual). One screen, one verdict, when the floor is met. No new scheduled tasks needed — the snapshot tasks are already running; the only new artifact is the spec itself.

## Honesty rails (binding)

1. Prereg committed/hashed before outcomes (process rule, 2026-07-17).
2. Stage-0 is a stopping rule only (2026-07-25 precedent: VPIN).
3. Both-agree (Fable + Codex) at every verdict; disagreement parks the item; Codex unavailable → agreement-gated actions are invalid single-model and wait.
4. Every terminal verdict lands in the ledger (NO_GO row or Open-section row). Silence is not an outcome.
5. No probe without CONFIRMED_GO; no promotion without frozen gate + owner signature.
6. Public repo: raw data manifests and account-derived artifacts stay in gitignored paths (`data/`, local `_workspace/` files); the spec records content hashes and local paths only.

## Testing & verification

- Screen scripts under `research/` with fixture-driven unit tests; no network and no live warehouse dependency in tests.
- The honesty-auditor independently reproduces headline counts read-only (`mode=ro`) before any verdict stands.
- Verification of this program's own health: A0 census output recorded in the prereg; screen artifacts carry the prereg hash; ledger rows cite artifact paths.

## Out of scope

New probes before a CONFIRMED_GO; any live/paper order-path change; new data purchases; re-screening refuted families; the 08:00-reversal C2 substitute; universe changes. The AccBand/MCP live lane is untouched.
