# Phase 2 — Execution-First Pivot (post Phase-1 STOP)

## Context

Phase 1 attribution diagnostic (`reports/attribution_diagnostic_20260426.md`,
n=254 closed trades) returned **STOP**:

| Strategy | n | mean alpha | alpha CI95 | verdict |
|---|---:|---:|:---|:---:|
| Supertrend | 39 | −$0.227 | [−0.407, −0.054] | **NEGATIVE_EDGE** |
| MultiTF | 58 | −$0.035 | [−0.147, +0.042] | EDGE_AMBIGUOUS |
| claude_portfolio | 100 | +$0.024 | [−0.053, +0.104] | EDGE_AMBIGUOUS |
| systematic_v3_1 | 49 | +$0.028 | [−0.021, +0.074] | EDGE_AMBIGUOUS |

The original draft's own gate rule fires: "≥ 1 NEGATIVE_EDGE and rest ambiguous
→ STOP. Rethink signal universe." The ML rebuild (draft Phases 3–9) does not
ship until this gate flips.

**Critical caveat from the diagnostic itself:** backfilled rows have
`spread = slippage = funding = 0` because mid prices were never captured at
entry/exit historically. Alpha on those rows is *gross PnL*, not true alpha.
Real edge could be more negative once costs are decomposed — we cannot judge
the gate fairly until forward-attribution is wired.

**Goal of this phase:** ship the four execution-side improvements that help
regardless of alpha sign, plus the forward-attribution wire-up that lets us
re-run the gate with real data, then re-evaluate after ~100 cleanly-attributed
new trades. ML phases stay parked.

## Shape of the change

```
                ┌─────────────────────────────────────┐
                │  Phase-1 STOP (already shipped)     │
                │  Supertrend killed in selector      │
                │  Backfilled attribution: gross PnL  │
                └────────────────┬────────────────────┘
                                 │
     ┌───────────────────────────┴──────────────────────────┐
     │                          │                            │
     ▼                          ▼                            ▼
[A] Forward-attrib       [B] Dist-fit SL/TP        [C] Vol-target sizer
  Position.entry_mid       core/dist_fit_sl.py        core/vol_target.py
  Position.exit_mid        risk_manager.get_sl_tp     risk_manager.calculate_
  Position.funding_paid       (replace 1.8/4.5)          position_size
  smart_executor mid                                      (replace 'volatility'
   capture                                                  branch)
  _finalize_close →                                       │
   attribution.record                                     │
     │                          │                          │
     │           [D] Trailing refit (scripts/fit_trailing.py)
     │                          │
     └──────────────────────────┴──────────────────────────┘
                                 │  ≥100 new closed trades, fully decomposed
                                 ▼
                  [E] Re-gate: diagnostic_report --since <date>
                      ≥1 strategy alpha LB > 0  →  unblock ML
                      else: stay parked, escalate
```

A is the only blocker for E. B/C/D run in parallel. Nothing here changes
operating mode — `CONTROLLED_LIVE` keeps running on the existing path while
these land.

---

## Task A — Forward-attribution wiring

**Problem.** `core/attribution.py` + `warehouse.record_attribution` exist and
work; the backfill script populates historical rows with zeros for spread /
slippage / funding because the data was never captured. `_finalize_close`
(`core/order_manager.py:1328`) writes the trade close to the warehouse but
**does not call `attribution.record(...)`**. New trades inherit the same
zero-cost blind spot.

### A.1 — Capture entry mid and exit mid on Position

Files:
- `core/position_tracker.py` (Position dataclass / mutator)
- `core/smart_executor.py` (already calls `fetch_order_book(..., limit=5)` at
  lines 50 and 87 — capture mid there)
- `core/order_manager.py` (entry path stamps Position.entry_mid; close path
  stamps Position.exit_mid before `_finalize_close` runs)

Steps:
- [ ] Add `entry_mid: float = 0.0`, `exit_mid: float = 0.0`,
      `funding_paid: float = 0.0` to the Position model (default 0 keeps
      backward-compat with already-open positions on disk).
- [ ] In `smart_executor`, return top-of-book mid alongside the chosen fill
      price. Both call sites (entry and exit fallback) already pull the book
      — just expose `(bid + ask) / 2` from the same fetch.
- [ ] Order-manager entry path: stamp `pos.entry_mid` immediately after the
      fill resolves and before the position is persisted.
- [ ] Order-manager close path: stamp `pos.exit_mid` from a fresh top-of-book
      lookup *before* calling `tracker.close()` (which fires `_finalize_close`).
- [ ] Ghost-sync close path (the one whose attribution rails were patched on
      2026-04-26): no live mid is available — leave `exit_mid = 0.0`. The
      attribution recorder must skip rows where either mid is 0 (see A.3).

Test: `tests/test_attribution_forward.py` — synthetic Position with both
mids set produces an `AttributionRow` whose `spread > 0` matches the
fill-vs-mid difference.

### A.2 — Live funding accrual

For paper trades `core/sim_execution.py:funding_payment` already credits/debits
funding at 8h boundaries on the wallet — wire that sum onto Position so it
reaches attribution.

For live trades funding is paid by the exchange and reflected in the wallet,
not on the position object. Add an exchange-side fetch:

- [ ] On position open, record `pos.funding_start_ts` (open_time).
- [ ] In `_finalize_close`, before the warehouse write, call
      `exchange.fetch_funding_history(symbol, since=funding_start_ts)` and sum
      payments for this symbol/side over the hold window. Store on
      `pos.funding_paid` (signed: positive = we paid).
- [ ] Wrap in try/except and default to 0.0 — never block close on funding
      lookup failure.

Test: paper round-trip across an 8h boundary records non-zero `funding_paid`;
mocked `fetch_funding_history` returns funding rows that sum correctly.

### A.3 — Wire `attribution.record` into `_finalize_close`

File: `core/order_manager.py:1391-1429` (the existing
`record_trade_close` block).

Steps:
- [ ] After `wh.record_trade_close(trade_id=tid, ...)` returns, build a
      `core.attribution.Trade` from the closed Position:
      `entry_mid, entry_fill=pos.entry_price, exit_mid, exit_fill=price,
      funding_paid=pos.funding_paid, fees=pos.total_fees,
      slippage_modeled=0.0` (live) / `cumulative slippage from sim_execution`
      (paper).
- [ ] Skip when `entry_mid == 0.0 or exit_mid == 0.0` (ghost-sync /
      pre-A.1 positions). Log at DEBUG, not WARN.
- [ ] Otherwise call `attribution.record(wh, trade_id=tid, trade=Trade(...))`.
      `INSERT OR REPLACE` on `trade_id` makes this idempotent.

Test: closing a synthetic Position writes both a `trades` row *and* an
`attribution` row whose `realized_pnl` matches `pos.pnl` within $0.01.

### A.4 — Verify the invariant on a clean run

- [ ] Paper-mode soak (≥10 trades). Assert in a script:
      `abs(realized_pnl − (alpha − spread − slippage − funding − fees)) < $0.01`
      on every newly-attributed row.

---

## Task B — Distribution-fitted SL/TP

**Replaces** the fixed `ATR × 1.8 / 4.5` block in `core/risk_manager.py:464`
(`get_sl_tp`) and the `sl_pct = max(1.5, min(3.5, atr_1h_pct * 1.5))` line
in `core/mcp_brain.py:2187`.

Files:
- New: `core/dist_fit_sl.py`
- Modify: `core/risk_manager.py:464-505` (`get_sl_tp`)
- Modify: `core/mcp_brain.py:2187` (delegate to risk_manager rather than
  recompute — avoid the existing two-source-of-truth split)
- New: `tests/test_dist_fit_sl.py`

Spec (`DistFitSL.compute(symbol, regime, side, atr_pct) → (sl_pct, tp_pct)`):
- [ ] Pull last 90 days of closed trades for `(symbol, regime)` from the
      warehouse (`SELECT entry_px, sl, exit_px, ts_entry, ts_exit, side FROM
      trades WHERE …`).
- [ ] Compute MAE = max adverse excursion as % of entry, MFE = max favorable
      excursion as % of entry. (Existing rows don't store intra-trade
      extremes — for now approximate MAE with `(entry - sl) / entry` for
      losers and MFE with `(exit - entry) / entry` for winners. Note the
      approximation in the docstring; revisit when intra-trade extremes are
      logged.)
- [ ] `sl_pct = quantile(MAE, 0.85)` clamped to `[1.0 × atr_pct, 4.0 × atr_pct]`.
- [ ] `tp_pct = sl_pct × R_target` where `R_target` is solved from the realized
      win rate of the per-cell sample to maximise expectancy
      `wr · tp − (1 − wr) · sl`. Clamp `R_target ∈ [1.5, 3.0]`.
- [ ] **Fallback** when fitted-cell n < 30: use the current ATR×1.8 / 4.5
      formula. Log at INFO with the cell name.

Test: synthetic Gaussian returns → quantile-fitted SL ≈ −1.04σ within 5%;
high-vol regime fits wider stops than low-vol on the same symbol;
degenerate empty cell falls back without error.

Wire-up:
- [ ] `risk_manager.get_sl_tp` becomes the single authority. Delete the
      `sl_pct = max(...)` line in `mcp_brain._score_coin` and have
      `_score_coin` call `risk_manager.get_sl_tp(...)` with `regime` and
      `atr_pct`.
- [ ] Keep the 1.0% absolute floor (existing `base_floor` at line 487) — it's
      a leverage-safety net, not a vol-fit decision.

---

## Task C — Volatility-target sizer

**Replaces** the under-specified `volatility` branch in
`core/risk_manager.py:341-350` (`calculate_position_size`).

Files:
- New: `core/vol_target.py`
- Modify: `core/risk_manager.py:329-374`
- New: `tests/test_vol_target.py`

Spec (`VolTarget.size(balance, price, atr_pct, target_annual_vol=0.10) →
notional`):
- [ ] `daily_vol_pct = atr_pct` (1h ATR ≈ daily-vol proxy at 1× — the bot
      doesn't have a fitted GARCH yet; defer that to a follow-up).
- [ ] `target_daily_vol = target_annual_vol / sqrt(252)`.
- [ ] `notional = balance × (target_daily_vol / daily_vol_pct)`.
- [ ] Clamp to `[balance × 0.05, balance × max_position_pct × 3 × leverage]`
      (existing risk-manager bounds — preserve them).
- [ ] When `atr_pct` is missing, fall back to `balance × max_position_pct`
      (current default).

Wire-up: when `sizing_mode == "volatility"`, call `VolTarget.size(...)`
instead of the inline `target_risk = balance * 0.01` formula. The `kelly`
branch stays as-is — Kelly is already an enforcing sizer.

Test: 2× ATR → ½ size; 0.5× ATR → 2× size; portfolio realized vol over a
30-day simulated tape lands within ±20% of `target_annual_vol`.

---

## Task D — Refit trailing-stop activation/lock

**Problem.** Memory feedback `project_trailing_clips_winners_2026_04_21.md`:
50 trailing wins averaged $0.57 vs 4 TPs at $2.84. Trailing is clipping the
right tail of the winner distribution.

Files:
- New: `scripts/fit_trailing.py`
- Modify: `core/trailing_stop_manager.py` (load JSON params at startup)
- New: `tests/test_fit_trailing.py`

Spec:
- [ ] Grid: `activation ∈ {0.5%, 0.8%, 1.2%, 1.5%, 2.0%}`,
      `lock_fraction ∈ {0.5, 0.6, 0.7, 0.8, 0.85}` (25 cells).
- [ ] For each (a, l) replay every warehouse winner against its 1h
      OHLC tape, simulate the trailing rule, sum simulated `realized_pnl`.
- [ ] Persist the maximising pair to `data/trailing_params.json`.
- [ ] `trailing_stop_manager.__init__` reads the JSON; falls back to
      current hardcoded `(0.8%, 0.7)` when missing.

Acceptance: backtest replay shows post-fit `Σ winner pnl` ≥ pre-fit. Log the
delta per the existing dashboard pattern.

---

## Task E — Re-gate after ≥100 fully-attributed new trades

Files:
- Modify: `scripts/diagnostic_report.py` (already has `--exclude-strategy`,
  `--mode`, `--tag` per commit `8701179`)
- Modify: `tests/test_diagnostic_report.py`

Steps:
- [ ] Add `--since <ISO-date>` flag. Filter `trades` rows by `ts_entry`.
- [ ] Add a one-line guardrail: skip rows where the joined `attribution`
      row has `spread = slippage = funding = 0` AND `entry_mid = 0` (the
      pre-A.1 zero-cost bug). Print the count of skipped rows.
- [ ] Re-run weekly: `python scripts/diagnostic_report.py --since 2026-04-27
      > reports/attribution_diagnostic_<date>_post_phase2.md`.

Decision rule (operator-side, documented here so the next pickup knows):
- ≥ 1 strategy `EDGE_PRESENT` (alpha LB > 0) on **post-Phase-2 sample only**
  → unblock the ML phases from the original draft.
- All `EDGE_AMBIGUOUS` and the spread+funding decomposition shows costs >
  alpha → ship a smart-executor / maker-only / time-of-day filter pass; do
  not start ML.
- Any `NEGATIVE_EDGE` → stop, escalate to operator. Don't iterate.

---

## Out of scope (deliberately deferred)

- Triple-barrier labeller, LR scoring model, isotonic calibration, walk-forward
  CV — all parked behind the Task E gate. `core/walk_forward.py` and
  `core/stat_tests.py` already exist; they remain unused until the gate flips.
- HMM regime detection, GARCH(1,1), cointegration pair scanner — separate
  alpha sources; revisit only after the existing signal stack proves edge.
- Meta-labeller, XGBoost — sample size still doesn't justify either.
- Shadow runner, promotion gate — there is no rival pipeline to shadow yet.

## Cleanup (one-shot, low risk)

- [ ] Remove `SupertrendStrategy` from `strategies/__init__.py:9` re-exports.
      The class file stays for `backtest.py` / `auto_backtest.py` which import
      directly. Live selector (`core/strategy_selector.py:370-385`) already
      gates it off.
- [ ] Confirm `core/arbitrage_engine.py` has no live caller
      (`grep -rn arbitrage_engine` outside backtest tooling). If clean, delete.
      If a caller exists, leave and add a TODO.

---

## Verification (end-to-end)

```bash
# 1. Tests
python -m pytest tests/ -q

# 2. Forward-attribution invariant on paper soak (≥10 trades)
python -c "from core.warehouse import get_warehouse; \
  rows = get_warehouse().query( \
    'SELECT a.realized_pnl, a.alpha, a.spread, a.slippage, a.funding, a.fees \
     FROM attribution a WHERE a.attributed_at > strftime(\"%s\",\"now\",\"-1 day\")'); \
  bad = [r for r in rows if abs(r['realized_pnl'] - (r['alpha'] - r['spread'] \
        - r['slippage'] - r['funding'] - r['fees'])) > 0.01]; \
  print('rows', len(rows), 'invariant_violations', len(bad)); assert not bad"

# 3. Dist-fit SL on a per-symbol cell with ≥30 trades
python -c "from core.dist_fit_sl import DistFitSL; \
  print(DistFitSL().compute('BTC/USDT:USDT', 'trend', 'long', 1.2))"

# 4. Vol-target sanity
python -c "from core.vol_target import VolTarget; \
  print(VolTarget().size(balance=420, price=60000, atr_pct=0.012))"

# 5. Trailing fit
python scripts/fit_trailing.py --report
cat data/trailing_params.json

# 6. Re-gate diagnostic
python scripts/diagnostic_report.py --since 2026-04-27
```

**Success criteria for this phase:**
- `_finalize_close` writes a non-zero-cost attribution row on every
  live or paper close where mid was captured.
- Invariant violations on the new soak window: 0.
- `risk_manager.get_sl_tp` returns SL/TP from `DistFitSL` for any
  `(symbol, regime)` cell with n ≥ 30; falls back cleanly otherwise.
- Realized 30-day portfolio vol within ±20% of 10% annualised target.
- Trailing-stop fit ships measurable improvement vs current `(0.8%, 0.7)`
  on warehouse-replay backtest.
- Re-gate diagnostic runs without errors and includes a "rows skipped due to
  pre-Phase-2 zero-cost bug" line.

ML rebuild does not start until the re-gate flips. That's the contract.
