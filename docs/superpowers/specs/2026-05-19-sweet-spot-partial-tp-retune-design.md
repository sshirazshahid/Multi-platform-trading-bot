# Sweet-Spot Partial-TP Retune — Design

**Date:** 2026-05-19
**Branch:** `feat/profitability-upgrade` (continuing from bleed-fix sprint, head `81f5f49`)
**Author:** Claude Code (Opus 4.7 1M) — brainstorming session with user
**Status:** Pending user review before plan generation
**Predecessor work:** Bleed-fix sprint shipped 7 commits (`357d025` → `81f5f49`). This is the immediate follow-up, targeting the 67%-WR 30-60min hold-time sweet spot uncovered during sprint diagnostics.

---

## 1. Background and Goal

### 1.1 Why this work exists

User directive (2026-05-19): "Focus on small TPs in the beginning gradually testing and improving on all types of pairs available on all connected exchange. Higher trades, small TPs and SL. Even 1-2 USDT gain per trade is enough at the start."

A data analysis pass uncovered:
- The bot's `mcp_take_profit` average win is already **$1.02** (n=25 over 30d) — squarely in the user's $1–2 target zone.
- Trade frequency is already ~7.8 closed trades/day across 3 exchanges.
- `TRADING_MODE=all` is already on; whitelist is 60 symbols.
- The literal interpretation ("compress TPs to fire faster") would push trades into the **<30min bucket which is net negative** (32% WR, −$2.02 over 30d).

The structural finding: there is a **proven profitable cell at 30–60min hold time** — 33 trades, 67% WR, avg_win $0.55, avg_loss −$0.18, net +$10.05 over 30d. The retune targets this cell directly.

### 1.2 30-day hold-time bucket data (lock at sprint baseline)

```
<30min       n=28  WR=32%  avg_win=$0.30  avg_loss=$-0.25  sum=$-2.02
30-60min     n=33  WR=67%  avg_win=$0.55  avg_loss=$-0.18  sum=$+10.05  ← sweet spot
60-120min    n=53  WR=42%  avg_win=$0.36  avg_loss=$-0.30  sum=$-1.46
120-240min   n=57  WR=35%  avg_win=$0.34  avg_loss=$-0.82  sum=$-23.63  ← biggest bleed
>240min      n=59  WR=49%  avg_win=$0.62  avg_loss=$-0.53  sum=$+1.97
```

### 1.3 Goal

Tighten `PARTIAL_TP` config so partial firings happen earlier in the position lifecycle, harvesting more wins in the 30–60min sweet spot. **Three lines of config change.** All other behavior preserved (entry quality, TP target levels, SL levels, age cutoffs from the bleed-fix sprint, Patch #3 amplification).

### 1.4 Constraints (binding)

- Live bot stays running on `CONTROLLED_LIVE`. Change picked up at user-initiated restart.
- WR floor ≥ 65% (memory directive). The retune must not drop the 30–60min cell WR below ~60% over a 50-trade rolling window (early-warning signal for rollback).
- No personal info / API keys in committed files.
- Pre-commit hooks (ruff, codespell, detect-secrets, pre-push pytest) must pass.
- Don't dilute entry quality (no MCP-score threshold change).
- Don't compress age cutoffs further (75min cap from bleed-fix sprint preserved).

---

## 2. The Change

**File modified:** `D:\Downloads\Trading_Bot\config.py`

Replace the existing `PARTIAL_TP` dict:

```python
# Current (deployed)
PARTIAL_TP = {
    "enabled": True,
    "first_take_at_pct": 0.5,
    "first_take_size": 0.5,
    "move_sl_to_breakeven": True,
}

# Proposed
# 2026-05-19 sweet-spot retune. Captures more wins in the empirically
# proven 30-60min hold-time cell (67% WR, +$10.05 / 33 trades / 30d).
# Lowered partial-trigger from 50% to 35% of TP distance and increased
# size from 50% to 60% so more positions book a small win before
# deteriorating into the 120-240min bleed band (-$23.63 / 30d).
# Rollback: revert both values, restart. Sub-1-minute reversal.
PARTIAL_TP = {
    "enabled": True,
    "first_take_at_pct": 0.35,
    "first_take_size": 0.6,
    "move_sl_to_breakeven": True,
}
```

Two-value change. Surrounding comment block expands to document rationale + rollback path.

**No code changes required.** The consumer at `core/order_manager.py:2390-2412` reads `first_take_at_pct` and `first_take_size` directly from the config dict via `PARTIAL_TP.get(...)`. The threshold and size values change; the trigger logic is unchanged.

---

## 3. Expected Effect (the math)

Modeled on $50-notional positions with 5% TP / 2% SL targets, 100 hypothetical trades. Assumptions taken from 30d warehouse cohort.

### 3.1 Current 50%/50% partial

| Path | n | PnL per trade | Subtotal |
|---|---:|---:|---:|
| Full-TP winners (mcp_take_profit) | 25 | +$1.88 (= $0.63 partial + $1.25 runner) | +$46.88 |
| Partial-then-BE-SL ("wiggle wins") | 15 | +$0.63 | +$9.38 |
| Direct SL hits (never reach 50% of TP) | 60 | −$1.00 | −$60.00 |
| **Net per 100** | — | — | **−$3.74** |

### 3.2 Proposed 35%/60% partial

| Path | n | PnL per trade | Subtotal |
|---|---:|---:|---:|
| Full-TP winners (unchanged entry quality) | 25 | +$1.53 (= $0.53 partial + $1.00 runner) | +$38.13 |
| Partial-then-BE-SL — same 15 plus 20 new ("wiggle wins" that touched 35% but not 50% with current config; were age-killed or SL-hit) | 35 | +$0.53 | +$18.38 |
| Direct SL hits (never reach 35% of TP) | 40 | −$1.00 | −$40.00 |
| **Net per 100** | — | — | **+$16.51** |

### 3.3 Load-bearing assumption and sensitivity

The **20 new wiggle wins per 100 trades** estimate is the load-bearing assumption. It assumes a meaningful share of currently-losing trades touch 35%-of-TP but reverse before reaching 50%.

**Sensitivity check:**
- 20 wiggle wins: +$16.51 / 100 trades → ~+$15-$25 / 30d at current trade rate
- 10 wiggle wins: +$1.51 / 100 trades → near-neutral
- 5 wiggle wins: −$4.99 / 100 trades → mild bleed
- 0 wiggle wins: −$8.74 / 100 trades → bleed (worse than current by ~$5)

The patch is **directionally safe in the realistic range** (10+ wiggle wins ⇒ break-even or positive). The 30d 60-120min cell (n=53, sum −$1.46, WR 42%) and 120-240min cell (n=57, sum −$23.63, WR 35%) together contain 110 trades, many of which plausibly touched 35%-of-TP before deteriorating. Converting a fifth of those into 35% partials is the operating thesis.

### 3.4 Combined trajectory

Bleed-fix sprint baseline (locked 2026-05-19): −$14.96 / 30d.

If retune delivers mid-range estimate: **−$14.96 → −$0 to +$10 / 30d.**

Combined with bleed-fix sprint Patches #2/#3 effects (small per the §4.5 / §5.7 conservative estimates), the realistic net trajectory is **between +$0 and +$15 / 30d** within the next 30-day measurement window.

---

## 4. Tests Added

New file: `tests/test_partial_tp_retune.py`. There are currently **zero** unit tests for `PARTIAL_TP`; this patch establishes that coverage as a side benefit.

Six tests:

1. **`test_partial_tp_default_first_take_at_pct_is_035`** — pin the new config value. Catches accidental revert.
2. **`test_partial_tp_default_first_take_size_is_06`** — pin the new config value.
3. **`test_partial_fires_at_35pct_long`** — synthetic long position with entry=100, take_profit=105. Mock `check_sl_tp` price input at 101.75 (35% of TP distance). Assert `partial_close_position` called with `take_sz=0.6`.
4. **`test_partial_fires_at_35pct_short`** — short mirror: entry=100, take_profit=95. Price input at 98.25 (35% of TP distance). Assert `partial_close_position` called.
5. **`test_partial_does_not_fire_below_35pct`** — long at 101.70 (34% — just below threshold). Assert NOT called.
6. **`test_partial_taken_flag_prevents_double_fire`** — fires once at 35%; sets `pos.partial_taken = True`; subsequent price at 50% does not re-fire.

Tests use `MagicMock` for the OrderManager + Position objects and patch `core.order_manager.PARTIAL_TP` via monkeypatch where needed for setup isolation. No warehouse touches.

---

## 5. Measurement Plan

### 5.1 Baseline (locked at deploy time)

- 30d net PnL: −$14.96 (n=238)
- 30d 30-60min bucket: +$10.05 / 33 trades / 67% WR / avg_win $0.55
- 30d partial-tp close exits in warehouse: TBD (operator runs the query at deploy time and records)
- 30d AGE bucket (clean): −$4.07 / 23 trades

### 5.2 Operator commands

**Daily smoke check (any time after restart):**
```bash
python scripts/sprint_kpi.py --since 2026-05-19 --markdown
```

**7-day signal check (around 2026-05-26):**
```bash
python scripts/sprint_kpi.py --since 2026-05-19 --markdown
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 7*86400
print('=== 7d 30-60min bucket ===')
row = c.execute('''SELECT COUNT(*),
    ROUND(SUM(realized_pnl),2),
    ROUND(100.0*SUM(CASE WHEN realized_pnl>0 THEN 1 ELSE 0 END)/COUNT(*),0),
    ROUND(AVG(CASE WHEN realized_pnl>0 THEN realized_pnl END),3)
    FROM trades WHERE status=\"CLOSED\" AND ts_entry>=?
    AND hold_sec>=1800 AND hold_sec<3600''', (since,)).fetchone()
print(f'n={row[0]}  pnl=\${row[1]}  WR={row[2]}%  avg_win=\${row[3]}')
"
```

### 5.3 Pass / fail criteria (7-day window)

| Signal | Pass | Borderline | Rollback trigger |
|---|---|---|---|
| 30-60min bucket WR | ≥ 60% | 55–60% | < 55% over 20+ trades |
| 30-60min bucket count | ≥ 12 (vs current ~8/week) | 8–12 | < 8 |
| Net 7d PnL | ≥ 0 | −$5 to 0 | < −$5 |
| Partial-TP exit-reason count | ≥ 8 in 7d | 4–8 | < 4 (config not picked up?) |

### 5.4 30-day full review

Run the same `sprint_kpi.py` snapshot at 30 days after deploy. Compare to the baseline above. Decision tree:
- 30-60min cell sum > +$15 AND WR ≥ 60% → keep, consider Option B (universe expansion) next.
- 30-60min cell sum in [+$5, +$15] → keep, no further action.
- 30-60min cell sum in [−$5, +$5] → marginal; keep but reassess at 60d.
- 30-60min cell sum < −$5 → roll back, investigate.

---

## 6. Rollback

`config.py` `PARTIAL_TP` dict reverted to:
```python
PARTIAL_TP = {
    "enabled": True,
    "first_take_at_pct": 0.5,
    "first_take_size": 0.5,
    "move_sl_to_breakeven": True,
}
```

Restart bot. Total time: under 1 minute. Reversion takes effect on next monitor cycle (every ~2 min). All in-flight positions complete with whatever partial state they have; new positions follow current rules.

---

## 7. Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Premature partial reduces full-TP wins | Medium | Bounded: full-TP win drops from $1.88 to $1.53 (−19%). Math captures this; overall net is positive under realistic wiggle-win assumptions. |
| Many trades wiggle to 35% but partial-then-runner still hits full TP | Low (good case) | This is the desired path. PnL = $0.53 partial + ~$1.00 runner = $1.53. |
| Many trades wiggle to 35%, partial fires, then reverse to BE-SL | Medium (expected) | This IS the wiggle-win path. +$0.53 vs alternative −$1.00 SL hit. |
| Wiggle assumption (20 new wins / 100 trades) is wrong | Medium | Sensitivity table shows even 10 wiggle wins keeps the patch break-even. Pass/fail criteria detect this within 7 days. |
| Interaction with bleed-fix sprint Patch #3 amplification | Low | Patch #3 wraps soft-close paths (AGE_LIMIT/AGE_LOSS/STALE). Partial-TP fires at `check_sl_tp:2390`, BEFORE the soft-close gates at line 2647+. Independent paths. |
| Interaction with bleed-fix sprint Patch #2 age cutoffs | Low | Age cutoffs fire on age + flat-or-losing PnL. Partial fires on price ≥ 35% of TP distance (in profit). Different conditions; no overlap. |
| Config dict change breaks bot startup | Trivial | Same keys, different values. No code change. Restart-tested via `python main.py --status` in plan. |
| Pre-commit hooks fail on config edit | Trivial | Ruff/codespell don't touch config dict semantics. Test additions will be in `tests/` and follow existing pattern. |

---

## 8. Out of Scope

- **Universe expansion** (Option B from prior turn) — deferred until this retune is measured.
- **Hour-gate relaxation** — deferred.
- **MCP score threshold change** — explicitly excluded per "don't dilute entry quality" constraint.
- **Trailing-stop retune** — Phase 11 (2026-05-14) raised trailing_activation 1.2% → 2.0% based on swarm audit data. Not revisiting in this patch.
- **Wrapping `entry_invalidated`** in Patch #3 amplification — open follow-up from bleed-fix sprint code-quality review.
- **Compression into <30min bucket** — explicitly excluded per data (32% WR, −$2.02 / 30d).

---

## 9. Decision Summary

| Question | Decision |
|---|---|
| Goal | Capture more wins in the proven 30-60min sweet spot |
| Mechanism | Tighten partial-TP threshold (50%→35%) + larger size (50%→60%) |
| Code surface | Config dict only (2-value change); 0 code edits to consumer logic |
| Test coverage | 6 new unit tests; first-ever direct PARTIAL_TP test coverage |
| Rollback | Revert dict + restart. Sub-1-minute. |
| Measurement | 7-day signal check via `sprint_kpi.py` + bucket query; 30-day full review |
| Sprint patches compatibility | Independent; no overlap with Patches #0/2/3 |
| Constraint compliance | CONTROLLED_LIVE preserved, WR floor monitored, no API key/PII exposure, pre-commit clean |
| Expected effect (30d, point estimate) | +$15 to +$25 lift on top of bleed-fix sprint baseline |

---

## 10. Plan-File Note

The bleed-fix sprint's plan file at `docs/superpowers/plans/2026-05-19-bleed-fix-sprint.md` did not persist to disk when the Write tool reported success (an apparent tooling/hook issue). The implementation completed by referencing the spec + per-task subagent prompts directly. This retune is small enough that the plan file generated by the writing-plans skill will fit cleanly in a single document and can be referenced inline by the implementer; if persistence issues recur, the implementer should work directly from this spec's §2-5 sections.
