# PF=0.55 Bleed — Diagnostic Synthesis (2026-05-23)

Read-only diagnostic per `chore/profitability-diagnostic-2026-05-23` plan. No code changes proposed here — this document ranks leaks and points the next planning round at the highest-$ target. Prior `tasks/todo.md` (Apr-19 WR Restoration Plan v2) is preserved at `tasks/todo.bak.2026-05-23.md`.

## TL;DR

The bot is **not** signal-broken at a deep level. In the 30-day window since 2026-04-23 the warehouse shows **gross PnL +$3.64 across 110 closed trades** (net -$3.77 + fees -$7.41) — i.e., before fees and ghost-class exits, the entry stack is marginal-positive. The bleed lives in three places, ranked by 30d $-impact:

| # | Leak | 30d $ | Evidence |
|---|---|---:|---|
| 1 | **GHOST-class exits (`ghost_reconciled` + `ghost_sync`)** | **-$15.05** | sprint_kpi GHOST bucket; gate_effectiveness §4 shows -$13.75 ghost_reconciled + -$1.08 ghost_sync |
| 2 | **Fee drag** | **-$7.41** | sprint_kpi-window SQL: net -$3.77, fees -$7.41 ⇒ gross +$3.64 |
| 3 | **Meta-filter removes winners, not losers** | **-$0.14 net, PF 0.53→0.51** | whatif: Raw 377 vs Meta 325 strictly worse on all metrics |

The dominant signal is leak #1: **38 of the 45 GHOST trades in the 30d window happened on May 22–23 alone** (84%), coinciding with the May 22 SCALP-tier ship. This looks like a fresh regression, not a chronic problem. The recommended next planning round is to root-cause the May 22–23 ghost spike, *not* to chase reroute logic — the ghost-reroute report shows 0/59 events were recoverable by reroute (Patch #1 is empirically dead).

## Per-leak detail

### Leak #1 — GHOST-class exits drain -$15.05 in 30d (-$15.40 in last 7d alone)

- **$-impact (30d):** sprint_kpi GHOST bucket is -$15.05 / 46 trades. Decomposed by gate_effectiveness §4: -$13.75 from `ghost_reconciled` (n=21, WR 28.6%) and -$1.08 from `ghost_sync` (n=24, WR 37.5%). 7-day total is -$15.40 / 43 trades — meaning the prior 23 days produced only +$0.35 across 3 ghost trades. The ghost path was essentially dormant before May 17 and erupted on May 22.
- **Evidence:** `data/reports/gate_effectiveness_2026-05-23.md` §4 (exit_reason breakdown); `reports/diag_sprint_kpi_30d.md`; `reports/diag_sprint_kpi_7d.md`; `reports/diag_ghost_30d.md`.
- **Inflection (May 22–23):** Of the 21 `ghost_reconciled` rows in the 30d window, **17 fired on 2026-05-22 (n=7, -$8.60) and 2026-05-23 (n=10, -$5.62)**. Of the 22 `ghost_sync` rows in the 7d window, **21 fired on 2026-05-22 (n=2) and 2026-05-23 (n=19)**. Combined: 38 of 45 ghost trades (84%) within 48 hours.
- **Concentration on STAR symbols:** ATOM+ARB account for **7 of the 21 ghost_reconciled trades and -$8.63 of the -$13.75**. ATOM alone owns -$7.97 from 2 ghost_reconciled trades. This is why the gate-effectiveness "STAR cell" cell looks broken — it is *recording* the ghost loss, not generating it.
- **Recoverability:** `ghost_reroute_report.py` since 2026-04-23 detected 59 ghost events and flagged **0 as `would_reroute=True`** with $0 expected saved-PnL. The proposed reroute patch (Patch #1) would not have recovered any of this.
- **One-line hypothesis:** Something shipped on/around 2026-05-22 (SCALP tier @ conf≥0.40 / 2x lev / 10% size, Claude TP clamp [1.0%, 2.0%], blend-formula fix for algo_conf==0) raised trade frequency past the threshold where `position_tracker`'s phantom-grace, close-time race detection, or ghost-classification rules behave correctly — driving healthy trades into the ghost path.

### Leak #2 — Fees consume the alpha (30d gross +$3.64, fees -$7.41 ⇒ net -$3.77)

- **$-impact (30d):** -$7.41 in fees against +$3.64 gross. Fees alone are **196% of the net loss**; remove them and the 30d window flips to mildly positive.
- **Evidence:** SQL on the sprint_kpi window (ts_entry >= 2026-04-23 UTC): `(count=110, sum_realized_pnl=-$3.77, sum_fee=$7.41, gross=$3.64)`. 7d window (ts_entry >= 2026-05-16 UTC): `(count=68, sum_realized_pnl=-$8.62, sum_fee=$6.27, gross=-$2.35)` — gross is also negative in 7d, but ~70% of the net hole is fees.
- **All-time context:** Fees are -$12.24 vs all-time net -$64.39 — fees are **only ~19% of the chronic bleed**. So fee drag is severe in the *recent* window but does **not** explain the all-time deficit.
- **One-line hypothesis:** The SCALP tier (1.5% TP at 2x lev ⇒ ~$1.20 per win on a $400 book) operates at a fee-to-edge ratio where round-trip fees (~$0.10–0.15 per trade at the configured notionals) eat 8–15% of every win, *and* every loser pays full fees too — so even at the 50% WR scalp-mode break-even, fees compound to multi-dollar drag fast.

### Leak #3 — Meta-filter / Claude veto remove winners, not losers

- **$-impact (all-time):** Raw 377 trades, net -$64.39, PF 0.53. Meta-filtered: 325 trades, net **-$64.53**, PF **0.51**. Claude-vetoed is identical to meta. So the filter drops 52 trades, loses $0.14 of net PnL, and worsens PF.
- **Evidence:** `reports/whatif_2026-05-23.md` summary table. Cross-supporting: `data/reports/gate_effectiveness_2026-05-23.md` §1 score-bucket calibration shows inverse-monotonicity in the high band — score 75-79 WR=23.5%, score 80-84 WR=27.3%, vs score 70-74 WR=59.4%. The filter trusts high mcp_score; reality says high-score is worse.
- **One-line hypothesis:** The mcp_score → expected-PF mapping is calibrated against a pre-Phase-2 trade population that no longer matches live conditions; the meta-filter and Claude veto both inherit that mis-calibration, so they SKIP some marginal winners while LETTING THROUGH the now-anti-EV high-score bucket.

## Data quality caveats

- **Forward attribution coverage is 9/267 (3.4%) in 30d.** `diagnostic_report.py --since 30d` says **EDGE_PRESENT** (n=24, alpha CI95% [+$0.11, +$0.47]) but 62.5% of those rows are backfill-blind. The same query with `--exclude-zero-cost` says **EDGE_AMBIGUOUS** (n=9, alpha CI [-$0.18, +$0.38] straddles zero). Both verdicts reported honestly — neither is decisive.
- **Pre-MCP NULL-score trades**: gate_effectiveness §1 score buckets sum to 93 of 377 closed trades (24.7%) — the rest have NULL mcp_score (pre-MCP wire-up or claude_portfolio path that discarded the score; see memory `[[project_mcp_score_warehouse_gap_2026_04_21]]`). All score-calibration claims hold only for the 93-row subset.
- **Ghost-reroute log retention is adequate (19 log files in `logs/`)**, so the "0 would_reroute" finding is not a measurement gap — it is a real signal that reroute logic doesn't see these as recoverable.
- **Sprint_kpi vs fee-SQL reconciliation**: both show 110 trades / 30d when filtered on `ts_exit` (the column sprint_kpi uses). Earlier discrepancy traced to `ts_entry` vs `ts_exit` filter — resolved.

## Open questions (require operator input before any code change)

1. **What actually shipped on 2026-05-22 evening that turned ghost-close into the dominant exit?** Memory lists three changes (SCALP tier, Claude TP clamp, blend fix). Are any of them position-monitor-adjacent — i.e., did they raise trade-cycle frequency above some threshold the ghost detector wasn't designed for? Diffs against the prior commit are needed to answer this.
2. **Is the score-inversion at 75-84 a regression or expected post-cell-filter?** If `CELL_FILTER=star_only` is what caused high-score non-STAR trades to be skipped while STAR trades with comparable score executed, the score buckets here are confounded by cell-status. Need a cell × score crosstab to separate.
3. **Is fee drag tolerable at SCALP tier @ $400 equity?** A 1.0:1 R:R scalp design with $0.10–0.15 round-trip fee per trade requires the entry signal to be ~50%+ WR *after* slippage and funding to be break-even — is that a target the entry stack can hit, or is fee-floor / smaller-notional a structural prerequisite?
4. **Why are `sl_placement_failed` (n=4, -$0.50) and `sl_crossed_at_placement` (n=8, +$0.12) still appearing 30 days after the original SL-placement fix?** Small impact but suggests the SL race fix is incomplete on at least one venue.

## Recommended next planning round

**Target leak #1 (GHOST), specifically the 2026-05-22 → 2026-05-23 regression.**

- The $-impact is the largest, and 80% of it accrued in 48 hours — implying a specific, recent, recoverable cause rather than a chronic edge problem.
- The proposed reroute fix is empirically dead (0/59 events recoverable). Whatever the next round proposes must target a layer *upstream* of reroute: the close detector, position_tracker phantom-grace, or whatever the SCALP tier introduced that races with `fetch_positions`.
- Stand-down on Top-3 #2 (fees) and #3 (meta-filter calibration) until the ghost spike is contained — both leaks are small ($7 and $0.14) compared to the $14.83 the ghost spike is generating per 30d at *current* velocity (which on its 48-hour rate would project to roughly $7/day going forward if uncontrolled).

Concrete planning-round prompt for the next session:

> "Root-cause the 2026-05-22 → 2026-05-23 ghost spike. Inputs: `data/reports/gate_effectiveness_2026-05-23.md` §4, `reports/diag_ghost_30d.md`, `reports/diag_sprint_kpi_7d.md`, and git diff for commits landing on or around 2026-05-22. Output: a fix or a guarded-revert plan, not a tuning patch."
