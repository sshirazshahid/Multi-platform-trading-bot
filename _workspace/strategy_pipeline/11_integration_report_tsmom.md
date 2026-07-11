# 11 — Integration Report: Codex Regime-Watch Probes — TSMOM-20d + Breakout-60d (owner-directed)

Date: 2026-07-11 · Integrator: shadow-integrator · Status: **SHIPPED to the shadow lane (staged, not committed)**

Two owner-directed LOG-ONLY forward paper tests of external Codex-project
recommendations. Part 1: TSMOM-20d (two arms). Part 2 (§Breakout below):
breakout_60d, the Codex deep-research winner. NEITHER is a pipeline GO.

## What this is — and what it is NOT

**NOT a pipeline GO.** Time-series momentum is a REFUTED family on
`.claude/skills/refuted-families-ledger/SKILL.md` (long-only TSMOM: no profit
edge, 2026-06-15; textbook trend/breakout 0/40 OOS, 2026-06-13). The external
Codex backtest behind this probe does NOT meet the ledger's reopen bar: ~30
OOS trades on a ~1.8-month single-regime window, the winner of a ~90-run sweep
with no multiplicity control, and a preceding period of **−17.4% with 0%
profitable runs**. The Codex report's own verdict was **"regime-watch —
monitor in paper mode, do NOT automate" (bot_weight 0.0)**.

This probe exists ONLY because (a) the owner explicitly directed the forward
paper test and (b) a log-only forward test is the honest instrument for
collecting the forward evidence that could someday meet the reopen bar.
**Expectation: NO-PROMOTE unless forward evidence surprises.** A bleed on a
tape turn is the documented failure mode playing out, not a surprise.

## Strategy spec (extracted from the Codex Pine sources — THEIR configuration)

Sources: `pine_strategies/17_time_series_momentum_20d_futures.pine` (1h),
`pine_strategies/18_time_series_momentum_20d_4h_full_history.pine` (4h),
cross-checked against `research/futures_backtest.py` (the run that produced
the regime-watch verdict).

| Rule | 1h arm | 4h arm |
|---|---|---|
| momentum = close/close[N] − 1 | N = 480 | N = 120 |
| trend = EMA(close, M) | M = 120 | M = 30 |
| LONG | momentum > 0 AND close > trend | same |
| SHORT | momentum < 0 AND close < trend | same |
| stop | entry ∓ 2.0 × ATR(14, Wilder) | same |
| target | entry ± 2R (rewardRisk 2.0) | same |
| max hold | 168 bars (7d), close at bar close | 42 bars (7d) |
| fill | signal-bar close (`process_orders_on_close=true`) | same |
| markets | BTC/ETH/SOL USDT perps, Bybit linear | same |
| sizing (notational) | 1% equity risk / risk distance, 2× notional cap | same |

**Pine-vs-reference divergence, resolved:** the Pine allows a reversal flip
while in a position (`position_size <= 0` entry guard); the reference backtest
enforces "no overlapping trades per strategy/market" and enters only when
flat. The probe follows the **reference** (one position per (symbol, arm), no
flips, re-entry earliest one bar after the exit bar) because that is the
configuration that actually produced the regime-watch verdict.

## Binding conditions → code

| # | Condition | Where |
|---|---|---|
| 1 | LOG-ONLY, no order path | `core/agents/tsmom_probe_agent.py` holds only read-only providers (OHLCV / market data / balance) + warehouse. Grep-verified: zero references to `order_manager`, `create_order`, `_execute_open`, `mcp_brain`, `risk_manager`; only importers are `bot_engine._build_tsmom_probe` (inside the already log-only ShadowRunner `extra_probes` hook) and tests. Pinned by `test_structural_log_only_no_order_path`. |
| 2 | Per-bar intra-hold MTM + per-event rows with signal inputs | `shadow_tsmom_mtm` (proposal_id, bar_ts, mark_px, side-signed unrealized_ret); `shadow_tsmom_probe` rows carry signal_bar_ts, entry_px, venue, mom_20d, ema_trend, atr_entry, sl/tp, score. Tests: `test_mtm_path_logged_with_signed_returns`, `test_long_entry_writes_decision_and_probe_rows`. |
| 3 | Frozen pre-outcome discriminating score | `tsmom_score(mom) = tanh(|mom_20d| / SCORE_MOM_SCALE)`, `SCORE_MOM_SCALE = 0.10` frozen in code and named as frozen (extracting the Codex momentum distribution was not feasible offline; the task's default 0.10 is used — a 10% 20d move ≈ unit argument). Never re-tuned post-outcome; a new score requires a new pre-registration. Test: `test_score_frozen_symmetric_monotone_varies`. |
| 4 | 1% equity-risk logged sizing, purely notational | `codex_position_units()` = min(equity×0.01/risk_distance, equity×2.0/entry_px); written to `notional_usd`/`units`/`risk_frac`; feeds only `projected_notional_current` on the decision row (the resolver's dollar basis). No sizing hook anywhere. Test: `test_codex_sizing_risk_and_leverage_cap`. |
| 5 | Ledger row in "In shadow" | `.claude/skills/refuted-families-ledger/SKILL.md` — row explicitly marked *owner-directed forward paper probe, NOT a pipeline GO, reopen bar NOT met*, with the Codex regime-fragility profile (−17.4% prior period, 0% profitable) and our standing refutations cited. The family also STAYS in the Refuted table. |
| 6 | CLAUDE.md harness changelog row | 변경 이력 table, 2026-07-11 row (shadow-integrator). |

## What logs where

- `shadow_decisions` — one row per entry, `agent_id='TsmomProbeAgent'`,
  `model_version='tsmom_20d_1h_v1'` or `'tsmom_20d_4h_v1'` (arms scored
  separately), side buy/sell, entry at signal-bar close, real sl_px/tp_px,
  `horizon_bars` 168/42, `timeframe` 1h/4h, venue bybit.
- `shadow_tsmom_probe` — per-event evidence row (signal inputs, frozen score,
  notational sizing, realized 8h funding-rate sum, occupancy `closed_hint_*`
  bookkeeping — the resolver remains the only readable outcome).
- `shadow_tsmom_mtm` — per-bar intra-hold MTM path.
- `shadow_outcomes` — written ONLY by `core/shadow_resolver.py` (SL-first
  tie-break, fees + slippage, censoring guard) via the hourly
  `scripts/resolve_shadow_outcomes.py` task, whose probe funding provider now
  also reads `shadow_tsmom_probe.realized_funding_rate_sum` (a LONG pays
  positive funding, a SHORT receives it — handled by the resolver's side sign).

## How to read the verdict

Use `trading_bot_shadow_vs_live` or query `shadow_outcomes` joined to
`shadow_decisions` filtered per `model_version` arm. **Never read a win-rate
or TP-hit-rate without the resolved after-cost `net_pnl` next to it** (the
TP-probe precedent: `core/agents/tp_probe_agent.py` — ~78% hit-rate was a
geometry artifact, −EV after cost).

## Promotion criteria (if ever)

Per arm, via the FROZEN `core/promotion_gate.py` thresholds (MIN_DSR ≥ 0.10,
MAX_PBO ≤ 0.5, OOS-WR ≥ 0.55, AUC ≥ 0.60 on the frozen score) on **≥ 30
RESOLVED forward events per arm**, PLUS an explicit owner decision. An honest
AUC/gate FAIL is a legitimate NO-PROMOTE outcome — the expected one.

---

# §Breakout — breakout_60d_4h_v1 (Codex deep-research winner)

## What this is — and what it is NOT

**NOT a pipeline GO.** Textbook trend/breakout is a REFUTED family on the
ledger (0/40 OOS on an independent toolchain, 2026-06-13; donchian_breakout
scored F in Codex's own first 6-month sweep). The Codex deep run
(`research/deep_futures_research.py`, `reports/DEEP_FUTURES_RESEARCH.md`,
winner `reports/deep_futures_winner.csv`) is the family's strongest external
evidence yet — complete 5-6yr histories × 10 markets, survives 2× costs
(100% cost survival), deep score 56.0, eligible_to_create True — BUT it does
NOT meet the reopen bar:

- the winner was selected **on holdout metrics across 20 candidates** — the
  holdout is burned, and a flat 6-point trial penalty is not DSR/PBO
  multiplicity control;
- **Codex's own block-bootstrap Monte Carlo fails our frozen
  capital-preservation gates**: P(positive) = 91.5% < the 0.95 floor and
  maxDD p95 = 42.49% > the 0.25 cap.

**Expected outcome: NO-PROMOTE at the frozen gate.** The probe exists because
Codex's own creation gate requires forward paper trading before any live
execution, and the owner directed implementing that recommendation.

**WR conflict (flagged per directive):** ~30-35% win rate BY DESIGN (3:1 R:R
trend system; Codex per-market full-history WR 23-41%). This conflicts with
the owner's standing ≥65% WR-floor preference — even a gate-passing result
could never be promoted into the accuracy-band lane and would need its own
explicit owner decision.

## Strategy spec (extracted from the Codex sources)

Sources: `deep_futures_research.py::breakout_signal(60)` + `candidates()`
`breakout_60d` spec; execution semantics from
`research/futures_backtest.py::backtest()`.

| Rule | Value |
|---|---|
| channel | prior 60d rolling max HIGH / min LOW, shifted 1 bar = 360 bars @4h |
| LONG / SHORT | close > channel high / close < channel low (no volume filter) |
| stop | entry ∓ 2.2 × ATR(14, Wilder, at the signal bar) |
| target | entry ± 3R |
| max hold | 504h = 126 bars; reference scan is entry-bar-INCLUSIVE → `horizon_bars=127` |
| fill | reference: next-bar open + slippage; probe logs signal-bar close (observable real-time equivalent; resolver open-slippage stands in) |
| markets | 10 Codex majors (BTC ETH SOL XRP BNB ADA DOGE LINK AVAX DOT), Bybit USDT perps |
| overlap | one position per symbol; re-entry earliest one bar after the exit bar |
| sizing (notational) | 1% equity risk / risk distance, 2× notional cap |

**Spec provenance (resolved):** `candidates()` was edited after the 15:58
run; the 2026-07-11 16:19 `--finalize-only` re-run confirms (2.2, 3.0) as the
real winner config — 9/9 parameter-neighborhood cells stable around it, and
the chosen center is not the neighborhood's best cell (1.7/3.0 shows PF
1.21), i.e. not perched on an overfit peak. Verified directly against the
regenerated `deep_futures_winner.csv` (deep score 56.0, 100% cost + 100%
parameter survival) and report grid before adoption.

## Binding conditions → code (breakout arm)

| Condition | Where |
|---|---|
| LOG-ONLY, no order path | `core/agents/breakout_probe_agent.py` — read-only providers only; grep-verified zero order-path references; only importers are `bot_engine._build_breakout_probe` (inside the log-only ShadowRunner `extra_probes` hook) and tests. Pinned by `test_structural_log_only_no_order_path`. |
| Per-bar MTM + signal inputs | `shadow_breakout_mtm` (side-signed unrealized_ret); `shadow_breakout_probe` rows carry channel_edge, penetration, atr_entry, sl/tp, frozen score, realized funding sum. |
| Frozen score | `breakout_score(pen) = tanh(penetration / 0.02)`; `SCORE_PEN_SCALE = 0.02` frozen from the Codex cache distribution (n=1,604 signal bars: median 0.0137, mean 0.0211, p75 0.028) BEFORE any outcome exists. Never re-tuned. |
| 1% risk notational sizing | shared `codex_position_units` (imported from the TSMOM module); no sizing hook anywhere. |
| Realized funding | 8h-bucket accrual in the probe row; `scripts/resolve_shadow_outcomes.py` funding provider chain extended (listing → unlock → tsmom → breakout). |
| Ledger row | `.claude/skills/refuted-families-ledger/SKILL.md` In-shadow row — NOT-a-GO framing, MC gate failure, WR-floor conflict, family stays Refuted. |
| CLAUDE.md row | 변경 이력 table, 2026-07-11 breakout row. |

## How to read / promotion criteria

Same as the TSMOM arms: `shadow_outcomes` joined per `model_version =
'breakout_60d_4h_v1'`; never a hit-rate without resolved after-cost
`net_pnl`; promotion only via frozen `core/promotion_gate.py` on ≥30
RESOLVED forward events + explicit owner sign-off. At the Codex trade
frequency (~60 trades per market over ~5yr ≈ 1/month/market, 10 markets)
≥30 resolved events take roughly 3-4 months — by design.

---

## Files changed (all staged, not committed)

- `core/agents/tsmom_probe_agent.py` (new)
- `core/agents/breakout_probe_agent.py` (new)
- `tests/test_tsmom_probe.py` (new, 23 tests)
- `tests/test_breakout_probe.py` (new, 15 tests)
- `core/bot_engine.py` (`_build_tsmom_probe` + `_build_breakout_probe` +
  extra_probes registration)
- `config.py` (`TSMOM_PROBE` + `BREAKOUT_PROBE` blocks;
  `SHADOW_TSMOM_PROBE_ENABLED|VENUE` / `SHADOW_BREAKOUT_PROBE_ENABLED|VENUE`
  env overrides; off restores the lane exactly)
- `scripts/resolve_shadow_outcomes.py` (funding provider chain reads both
  new probe tables)
- `.claude/skills/refuted-families-ledger/SKILL.md` (two In-shadow rows)
- `CLAUDE.md` (two changelog rows)
- this report

Activation: next bot restart (ShadowRunner init registers both probes). The
TSMOM 1h arm needs ~622 closed 1h bars per symbol per fetch and the breakout
arm ~382 4h bars — both served by the existing `fetch_ohlcv(limit=1000)`
path; no backfill required.
