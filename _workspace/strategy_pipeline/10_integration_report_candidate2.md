# 10 — Integration Report: `pre_unlock_short_capital_scaled` (Candidate 2, log-only shadow probe)

Integrator: shadow-integrator | 2026-07-11 | Inputs: `09_audit_candidate2_final.md`
(CONFIRMED, 6 binding conditions), `08b_screen_preunlock_short.{md,json}` (frozen
pre-registration + execution addendum), `research/screen_preunlock_short.py` (frozen
constants), rev3 precedent `core/agents/listing_short_probe_agent.py` + `04_integration_report.md`.

## What was built

| File | Change |
|---|---|
| `core/agents/unlock_short_probe_agent.py` | NEW — `UnlockShortProbeAgent`, the log-only probe |
| `tests/test_unlock_short_probe.py` | NEW — 21-test TDD spec, one test per binding condition/boundary |
| `core/bot_engine.py` | `_build_unlock_probe` + 3 read-only providers; registered via the existing `ShadowRunner` `extra_probes` hook (same line that registers the listing probe) |
| `config.py` | `UNLOCK_SHORT_PROBE` block (`SHADOW_UNLOCK_PROBE_ENABLED`, default true; calendar dir; frozen venue order) |
| `scripts/resolve_shadow_outcomes.py` | funding provider extended to serve unlock rows (`-sl8` rows get the SL-frozen sum); renamed `_build_probe_funding_provider` with back-compat alias |
| `scripts/backfill_unlock_calendar.py` | `--forward-days N` — extends the event window's UPPER bound to now+N days so the calendar carries upcoming events (frozen historical bound untouched) |
| `.claude/skills/refuted-families-ledger/SKILL.md` | New "In shadow" section: event-driven unlock-short row with the F1/F2 fragility profile (binding condition 6) |

Mechanics: each tick the probe scans `data/unlock_calendar/*.json` for cliff events with
`ratio >= 0.10`; when `now` crosses `floor_day(T) − 28d` (W1) or `− 14d` (W2) it signals —
resolving the perp on the frozen venue order (binance>bybit>bitget), requiring live
bid/ask/funding (missing funding = SKIP, never guessed), enforcing the 4-concurrent cap
per arm chronologically, and writing **two** `shadow_decisions` rows per entered event/arm:

- **raw** (`unlock_short_{w1,w2}_v1`): `sl_px=0, tp_px=0`, `horizon_bars = (T − entry_ts)/1h`
  — naked hold to unlock T, the screened strategy;
- **sl8** (`unlock_short_{w1,w2}_sl8_v1`, proposal_id suffix `-sl8`): `sl_px = entry × 1.08`
  — the charter-§2 Stop-Loss-Guardian counterfactual.

All after-cost PnL comes from the KEYSTONE resolver (`core/shadow_resolver.py`) — the probe
performs no outcome PnL math. End-to-end smoke verified: probe tick → 2 PENDING rows →
`resolve_pending` → raw resolves `time`, sl8 resolves `stop_loss`, realized funding folded in.

## Binding conditions → code

| # | Condition | Where enforced |
|---|---|---|
| 1 | LOG-ONLY, promotion only via frozen gate + owner | Probe holds only read-only providers; structural test `test_structural_log_only_no_order_path`; grep over the module finds ZERO references to `order_manager`/`OrderManager`/`create_order`/`place_order`/`open_position`/`cancel_order`/`mcp_brain`/`risk_manager`/`_execute_open`. Module referenced only from: `bot_engine._build_unlock_probe` (feeds the log-only `ShadowRunner.extra_probes`), the read-only resolver runner, tests, ledger. `mcp_brain` decision output, order paths, risk gates, PAPER/CONTROLLED_LIVE latch: untouched. |
| 2 | Per-bar intra-hold MTM + entry/exit ts+px, venue, realized funding, ratio, frozen score AT ENTRY | `shadow_unlock_mtm` (per-1h-bar mark + unrealized short return, closed bars only, no repaint); `shadow_unlock_probe` row carries entry_ts/px, venue, unlock_ts (exit target), `unlock_ratio`, `funding_entry`, `score` (frozen `tanh(ratio/0.20)+10×funding`, mirror-tested byte-identical to the screen's `auc_score`), per-8h `realized_funding_rate_sum`; `shadow_unlock_concurrent` logs the per-arm concurrent account-MTM drawdown (the F10 risk number). |
| 3 | 8%-adverse-MTM SL counterfactual alongside raw hold-to-T | Two-row design: the resolver itself produces BOTH PnLs (raw + `-sl8`) — no custom PnL math. First breaching bar flagged on `shadow_unlock_probe.sl_cf_hit_ts/px` (wick trigger `high ≥ entry×1.08`, matching the resolver and the live Guardian); `sl_cf_funding_rate_sum` freezes at the flag so the counterfactual isn't charged post-stop funding. |
| 4 | W1+W2 only, separate; NO W3 | `ARMS = {"W1": 28d, "W2": 14d}` — W3 has no code path; per-arm model_versions make per-arm promotion queries trivial; concurrency cap and concurrent-DD snapshots are per-arm. Tests: `test_w3_arm_not_implemented`, `test_w2_enters_separately_at_t_minus_14`. |
| 5 | Calendar snapshotted AS-OF SIGNAL TIME | At signal, event `ts/tokens/ratio` + the doc's `source` string (which embeds the bucket fetch timestamp) are copied into the probe row via plain `INSERT` (never REPLACE); the (base, T, arm) key is persisted in `data/shadow_unlock_state.json` so a refreshed/edited calendar can neither rewrite nor re-signal a logged event; the exit is pinned by `horizon_bars` at entry, so a calendar edit can't move a logged exit either. Test: `test_calendar_edit_after_signal_never_rewrites_logged_row`. Late-discovered windows (>12h, the screen's frozen bar tolerance) are `SKIP_LATE` — never retro-entered. |
| 6 | Ledger row with fragility profile | `refuted-families-ledger/SKILL.md` new "In shadow" section: 100% of profit from 2025-26 bear tape (2023 net-negative both arms), n=32/36 ≈ 19/22 independent bets (SUI/GUN pseudo-replication), W2 robustness-primary, frozen score expected to fail forward AUC (legitimate NO-PROMOTE), months-long timeline by design. |

Also implemented per the audit's capital-scaling check (§5): `notional = 0.03 × account
equity`, additive convention, **no leverage multiplier exists anywhere in the module**
(asserted in `test_probe_row_snapshot_carries_binding_condition_2_fields`).

## How to read the results

```sql
-- per-arm resolved evidence (raw strategy)
SELECT d.model_version, COUNT(*) n, AVG(o.net_pnl) avg_net, AVG(o.net_pnl>0) wr
FROM shadow_outcomes o JOIN shadow_decisions d USING(proposal_id)
WHERE d.model_version LIKE 'unlock_short_w%_v1' GROUP BY 1;

-- Guardian question (audit F6): raw vs SL-counterfactual, same events
SELECT REPLACE(o.proposal_id,'-sl8','') pid,
       MAX(CASE WHEN o.proposal_id LIKE '%-sl8' THEN 0 ELSE o.net_pnl END) raw_pnl,
       MAX(CASE WHEN o.proposal_id LIKE '%-sl8' THEN o.net_pnl END) sl8_pnl
FROM shadow_outcomes o WHERE o.proposal_id IN
  (SELECT proposal_id FROM shadow_decisions WHERE agent_id='UnlockShortProbeAgent')
GROUP BY 1;

-- drawdown-tail question (audit F10): SELECT * FROM shadow_unlock_concurrent;
-- signal/skip audit trail:            SELECT * FROM shadow_unlock_probe;
```

Or the `trading_bot_shadow_vs_live` MCP tool / `trading_bot_query`. NEVER read a win-rate
without the resolved `net_pnl` next to it (TP-probe precedent), and read every number
against the ledger's fragility profile first.

## Promotion criteria (frozen — not negotiable here)

`core/promotion_gate.py` thresholds (MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55, AUC≥0.60) on
**≥30 RESOLVED forward events PER ARM**, assessed per-arm, PLUS an explicit owner decision.
At ~1-3 qualifying events/month this takes months — by design. Audit F4 expects the frozen
score to FAIL the forward AUC gate; that outcome is a legitimate NO-PROMOTE, and the score
must not be re-tuned after outcomes exist (new score ⇒ new pre-registration).

## Verification

- TDD: 21-test spec written first (red), then implementation (green).
- Full suite: **2850 → 2871 passed** (+21), 0 failures. Ruff clean on all touched files.
- End-to-end smoke: probe tick → resolver → `shadow_outcomes` for both row variants.
- Log-only grep sweep: zero order-path references (details in condition 1 above).
- Changes staged, NOT committed (owner review).

## Operational requirements (owner)

1. **The calendar snapshot ends 2026-06-01 — the probe sees ZERO upcoming events until it
   is refreshed.** Run, then schedule (weekly is sufficient for monthly cliffs; the probe's
   as-of-signal snapshotting makes re-runs safe):
   `venv\Scripts\python.exe scripts\backfill_unlock_calendar.py --forward-days 60`
2. The hourly `TradingBot-ShadowResolver` task resolves outcomes as usual — no change needed
   (the funding provider extension ships in the same script it already runs).
3. Probe activates on the next bot restart (`SHADOW_MODE.enabled` + `UNLOCK_SHORT_PROBE.enabled`,
   both default-on). Kill switch: `SHADOW_UNLOCK_PROBE_ENABLED=false` — restores the
   pre-probe shadow lane exactly. Do NOT bounce a healthy bot for this; it can wait for the
   next natural restart.
