# 12 — Deep-Breakout ACTIVE PAPER Lane (2026-07-11)

Owner directive: **"start trading aggressively."** Bot is in PAPER mode (sim fills
via `core/sim_execution.py`), so this lane actively places PAPER orders for the
Codex `deep_breakout` strategy — unlike the log-only `BreakoutProbeAgent`
(`core/agents/breakout_probe_agent.py`), which is **untouched** and keeps
collecting frozen-gate evidence in parallel. "Aggressive" = every valid signal
at the researched size, never above it.

## Source spec (fidelity basis)

| Source | Used for |
|---|---|
| `Codex .../bot/strategies/deep_breakout.py` | rules reference: 360-bar channel off the signal close, 2.2×ATR(14) stop, 3R target, SL/TP anchored to the signal-bar close |
| `Codex .../docs/DEEP_60D_BREAKOUT_STRATEGY.md` | 4h TF, 126-bar (21d) max hold, 1% risk, 2× equity notional cap, one position per market, exchange confidence (binance 78.8 / bybit 57.4 / bitget 44.7), "do not connect to live orders until it passes forward testing" |
| `core/agents/breakout_probe_agent.py` (ours, tested) | signal math **imported, not re-implemented**: `breakout_signal_last` (shifted channel), `wilder_atr_last`, frozen constants `WINDOW_BARS=360`, `STOP_ATR=2.2`, `REWARD_RISK=3.0`, `MAX_HOLD_BARS=126` |

## Rails → code (each binding)

| # | Rail | Code | Test pin |
|---|---|---|---|
| 1 | **PAPER-only hard gate** | `core/deep_breakout_lane.py::assert_paper_only()` — raises `PaperOnlyViolation` at **construction AND every `tick()`** when `config.DRY_RUN` is False; `bot_engine.run()` additionally refuses to even schedule the lane off-PAPER ("PAPER-only hard gate" log). Going live = owner decision + code change. | `test_paper_gate_blocks_construction_off_paper`, `test_paper_gate_blocks_tick_after_mode_flip`, `test_bot_engine_wiring_pins` |
| 2 | **Cohort separation** | every entry passes `strategy="deep_breakout"` → `order_manager` writes `trades.strategy_family='deep_breakout'` and `Position.strategy`. Goal/band reporting now excludes the family: `scripts/report_goal_progress.py` (`directional_summary`, `current_boot_summary`) and `dashboard.py` THIS-BOOT SQL carry `COALESCE(strategy_family,'') <> 'deep_breakout'`. | `test_long_entry_cohort_tag_and_geometry`, `test_goal_report_excludes_deep_breakout_cohort`, `test_dashboard_this_boot_excludes_deep_breakout` |
| 3 | **Sizing = researched config, capped by charter** | `_eval_base`: units = 1% equity risk / (2.2×ATR); notional ≤ 2× equity; then max 4 concurrent lane positions; lane gross ≤ 6% equity; correlation sizing (`risk.check_correlation`) and §2 12% portfolio cap (`risk_manager.exposure_breached`) **on top**; `leverage=1`; the order path's own gates (`risk.can_trade` incl. daily-loss breaker, min-notional, blacklist) are not bypassed. | `test_lane_exposure_cap_clamps_and_blocks`, `test_max_concurrent_blocks_fifth_position`, `test_portfolio_exposure_cap_applies_on_top`, `test_correlation_sizing_applies` |
| 4 | **Venues binance+bybit; one position per BASE** | `config.DEEP_BREAKOUT_LANE["venues"]=("binance","bybit")` (bitget excluded in a code comment: 44.7 confidence, ~100d usable 4h history); `_closed_candles` is binance-first with bybit fallback; `_eval_base` skips a base already held by the lane on ANY venue. | `test_config_block_matches_researched_spec`, `test_bybit_fallback_when_binance_has_no_data`, `test_one_position_per_base_across_venues` |
| 5 | **Existing safety rails unmodified** | entries via `order_manager.open_position` (normal SL/TP placement; sim wick-triggers in paper); daily-loss breaker fires inside `risk.can_trade` on that path; BTC vol pause replicated for the lane via `bot_engine._deep_breakout_entry_paused` (fail-CLOSED, A2 convention); charter §2 8% guardian kept as the `DEEP-BREAKOUT GUARDIAN` backstop in `order_manager.check_sl_tp`. What IS suppressed for lane positions (tsmom precedent, or the 21-day hold is impossible): scalp machinery (partial-TP, trailing, BE, entry-staleness, age/stale, 3% hard-max-loss → replaced by the 8% guardian), MCP monitor advice, discretionary portfolio CLOSE (`_execute_close` guard). Maker-first paper interception excluded for the lane (researched fill = taker at next print; waiting at the touch adverse-selects breakouts). | `test_btc_vol_pause_blocks_then_retries_same_bar`, `test_broken_pause_check_fails_closed`, `test_order_manager_wiring_pins`, `test_bot_engine_wiring_pins` |
| 6 | **Signal timing** | CLOSED 4h bars only (`_closed_candles` drops the forming bar — backtest_v3 closed-bar discipline); one evaluation per 4h boundary (`_last_seen` per base, persisted to `data/deep_breakout_lane.json` across restarts); re-entry earliest one bar after the exit bar (append-only warehouse `MAX(ts_exit)` query); lane tick scheduled every 300s in `bot_engine.run()`, no-ops off-boundary; 126-bar max-hold closed through `order_manager.close_position` (reason `max_hold`) so every on_close hook fires. | `test_closed_bar_discipline_no_forming_bar_signal`, `test_one_evaluation_per_bar`, `test_reentry_earliest_one_bar_after_exit_bar`, `test_max_hold_close_after_126_bars` |

## Files

- **NEW** `core/deep_breakout_lane.py` — lane module (gate, signal eval, sizing, max-hold)
- **NEW** `tests/test_deep_breakout_lane.py` — 24 tests, one per rail facet
- `config.py` — `DEEP_BREAKOUT_LANE` block (default `enabled=False`; env `DEEP_BREAKOUT_LANE_ENABLED`)
- `core/bot_engine.py` — `_run_deep_breakout_lane` + `_deep_breakout_entry_paused`, scheduler wiring, monitor/advice/close guards
- `core/order_manager.py` — maker-first exclusion + DEEP-BREAKOUT exit-policy block (8% guardian + polled SL/TP fallback)
- `scripts/report_goal_progress.py`, `dashboard.py` — cohort exclusion in band metrics
- `.env` — `DEEP_BREAKOUT_LANE_ENABLED=true` (owner directed activation; lane arms on next bot restart)

## Honesty notes

- Textbook breakout remains a **REFUTED family** on the ledger; the Codex deep run does not meet the reopen bar (holdout burned across 20 candidates; Codex's own MC fails our frozen capital gates: P(positive) 91.5% < 0.95, maxDD p95 42.5% > 0.25). This lane is the owner-directed forward paper test at researched size — **not** a pipeline GO and **not** promotable to live without the frozen gate + owner sign-off.
- ~33% WR by design. Never read this lane's WR against the 65–67% band goal; the cohort filters exist precisely so that comparison cannot happen by accident.
- With current equity (~$420), the 6% lane cap ≈ $25 gross → entries will usually clamp to the lane headroom and may occasionally reject on the $5 min-notional gate. That is the charter working as intended, not a bug.
- Expected signal rate is LOW (60-day channel breaks are rare) — days-to-weeks between entries is normal.
