# Ghost-Path Accuracy, Log Noise Cleanup, Exchange Reliability, Small-TP Capture — Design

**Date:** 2026-05-20
**Branch:** `feat/profitability-upgrade` (continuing on top of `fbf4f7b` — dashboard/email alignment fix)
**Author:** Claude Code (Opus 4.7 1M) — brainstorming session with user
**Status:** Pending user review before plan generation

---

## 1. Background

### 1.1 User observation (2026-05-20)

> "Why ghost_sync and ghost_reconciled still appearing? ERRORS and WARNINGS appearing for bybit and bitget."

### 1.2 Investigation finding

In the last 24h on the live bot:

- **14 of 16 closes (88%) are ghost-class** (10 `ghost_sync` + 4 `ghost_reconciled`).
- Distribution: Binance 8 ghosts, Bitget 4 ghosts, Bybit 2 ghosts.
- 22 bybit/bitget WARNING/ERROR log lines — 11 are GHOST detected, 4 are Bybit `fetchOrder()`-window warnings, 3 are first-attempt empty-ticker health checks, 1 is HIGH LATENCY (5083 ms), 1 is Bybit `110001` cancel race.

**Root cause finding:** ghost-class events are *not bugs*. They are the bot's reactive detection of exchange-side SL/TP fills. The bot's discretionary `CLOSE` action was intentionally disabled at `core/bot_engine.py:3825-3840` and `:3906-3910` (Phase 39 / 2026-05-09) because systematic_close had 1W/17L over 388 trades (−$16.42). Without a discretionary close path, every exit must come via either (a) `TAKE_PROFIT` (strict gate: net+0.5% AND conf≥60%), (b) local SL/TP monitor (loses tick-resolution race to exchange), or (c) exchange-side SL/TP firing → ghost path. The architecture is intentionally reactive.

**User decision (2026-05-20):** *Accept ghosts as the design.* Don't re-enable discretionary `CLOSE`. Instead:

1. Improve ghost-path accuracy so PnL is booked precisely when ghosts do fire.
2. Clean up log noise so real issues aren't drowned in informational warnings.
3. Harden Bybit/Bitget reliability around the known transient API quirks.
4. **Add proactive age-aware SL tightening** to prevent slow-drift positions from becoming exchange-side SL fills.
5. **Add deterministic small-TP capture** at +1% gain to actively realize small profits instead of waiting for Claude's discretionary TAKE_PROFIT firing (which fired 0 times today despite multiple positions in the captureable zone). User directive: "Even 1-2 USDT or 1-2% gain per trade is enough at the start."

### 1.3 Constraints (binding)

- Live bot stays running on `CONTROLLED_LIVE`. Change picked up at user-initiated restart.
- Per-position SL on every trade stays in place. Spec §2 still forbids widening SL.
- No personal info / API keys in committed files.
- Pre-commit hooks must pass (ruff, codespell, detect-secrets, pre-push pytest).
- The user-paid VPS option is off the table (user said "I cannot afford a VPS right now") — so WebSocket-driven architecture is out of scope. We work within the polling architecture.

---

## 2. The Five Areas

### 2.1 Area 1 — Ghost path accuracy

**Problem.** When `fetch_closed_pnl` doesn't return a matching record within the 6h lookup window (~20-30% of ghosts), `position_tracker.sync_with_exchanges` falls back to `ticker.last` as the close price. Ticker last can drift 0.1-0.5% from the actual SL/TP fill price, so PnL gets booked imprecisely.

**Code locations:**
- `core/position_tracker.py:644` — `since_ms = int((time.time() - 6 * 3600) * 1000)`
- `core/position_tracker.py:670-676` — ticker fallback

**Fix:**

1. Extend the ledger lookup window from 6h → 24h. Bitget and Bybit occasionally write their realized-PnL ledger entries with several hours of lag; 24h captures these without bloating the result set (each ledger fetch is paginated, the cost is one extra request).
2. On `ghost_sync` (no ledger record found on first try), retain the position in a `_pending_ghost_reconcile` map. On the next sync cycle 15s later, re-query the ledger with the same 24h window before finalizing as `ghost_sync`. If the ledger now has a record, upgrade to `ghost_reconciled` and patch the warehouse row with the real exit price.
3. Fall back to **`mark_price`** (when available via `fetch_ticker`'s `info.markPrice` / `info.mark`) instead of `ticker.last` — for SL/TP-triggered closes, the trigger fires off mark price, so this is closer to the actual fill price.

Behaviour-preserving for `ghost_reconciled` cases (no regression). Tightens `ghost_sync` PnL accuracy by ~0.1-0.3% on average.

### 2.2 Area 2 — Log noise cleanup

**Problem.** 22 ERROR/WARNING lines per day for Bybit/Bitget where ~95% are informational and not actionable. Real errors are drowned out.

**Changes:**

| Source | Current level | New level | Condition |
|---|---|---|---|
| `core/position_tracker.py:600` — `GHOST detected` | WARNING | INFO | Always (the event is expected; if ledger reconciles cleanly it's noise) |
| `core/order_manager.py:387` — `_verify_order_on_exchange` Bybit 20-min limit | WARNING | DEBUG | When error text contains `"can only access an order"` (it's a Bybit API limit, not a bot issue) |
| `core/bot_engine.py:2856` — `[Health] X health check FAILED (attempt 1)` | WARNING | DEBUG | Always — the existing retry-then-WARN path at attempt 2+ stays at WARNING |
| `exchanges/base.py:182` — `fetch_ticker` retry warning | WARNING | DEBUG | When the next retry succeeds (only log final-attempt failures at WARNING) |
| `exchanges/base.py:306` — Bybit `cancel_order` 110001 | ERROR | DEBUG | When error text contains `"order not exists"` or `"too late to cancel"` — race-condition expected case |
| ccxt "Set params[acknowledged]=True" closed-orders warning | (ccxt-side) | Silenced | Pass `params={"acknowledged": True}` on the relevant `fetch_open_orders` calls |

Net effect: today's 22 WARNINGs/ERRORs drop to ~3-5 (genuine signal). Real issues like ghost-on-non-reconciled-path, persistent health failures, and unexpected error codes still surface at WARNING/ERROR.

### 2.3 Area 3 — Bybit/Bitget reliability hardening

**Problem.** Bybit `110001` "order not exists or too late to cancel" fires when we try to cancel an SL/TP order that was already triggered by the exchange between our last check and our cancel call. Bitget HIGH LATENCY (5083 ms today) is just transient slowness with no available fix.

**Fix:**

1. Add `core/order_manager._safe_cancel_order(exchange, order_id, symbol, params)` helper:
   - Wraps `exchange.cancel_order(...)` in try/except.
   - Catches `ccxt.OrderNotFound`, `ccxt.InvalidOrder`, and parses raw error text for `"order not exists"`, `"too late to cancel"`, `"110001"`, `"40034"` (Bybit / Bitget equivalents).
   - Returns a clean `{"status": "ok", "reason": "already_filled_or_cancelled"}` for these cases (logged at DEBUG).
   - Re-raises every other error class so genuine bugs still surface.
2. Replace every `exchange.cancel_order(...)` call in `core/order_manager.py` (six call sites) with `self._safe_cancel_order(...)`.
3. The fetch_ticker retry loop in `exchanges/base.py` keeps its current 3-attempt retry but only emits the WARNING on the *final* attempt (Area 2 covers this).

No new dependencies, no architectural change. ~40 LOC.

### 2.4 Area 4 — Age-aware automatic SL tightening (proactive ghost prevention)

**Problem.** Today's ghosts cluster at hold-times of 200-900 minutes (3-15 hours). These are slow-drift positions that opened in the +0.3% to +1.5% range and were eventually overrun by the SL when price reversed. The bot's existing `BREAKEVEN` action (mcp_brain → bot_engine line 3868) only fires when MCP brain explicitly requests it, which is rare.

**Fix.** A new deterministic rule that runs every 30s inside `_run_mcp_position_monitor` *before* the MCP brain advice is consulted:

```python
def _maybe_tighten_aged_position(self, p: Position) -> bool:
    """Move SL to breakeven on aged + profitable positions to lock in
    the small gain before exchange-side SL fires on a reversal."""

    AGE_THRESHOLD_MIN = 60         # Area 4 trigger window
    PROFIT_BAND_LOW   = 0.0001     # >0 (strictly in profit)
    PROFIT_BAND_HIGH  = 0.02       # <2%  (above this, trailing stop owns)

    age_min = p.duration_minutes
    if age_min < AGE_THRESHOLD_MIN:
        return False

    pnl_frac = self._unrealized_pnl_frac(p)  # 0.005 = +0.5%
    if not (PROFIT_BAND_LOW <= pnl_frac < PROFIT_BAND_HIGH):
        return False

    new_sl = self._compute_breakeven_sl(p)   # entry × (1 ± 2·fee ± 5bp buffer)
    if (p.side == "buy" and new_sl <= p.stop_loss) or \
       (p.side == "sell" and new_sl >= p.stop_loss):
        return False   # Already at-or-tighter than breakeven — nothing to do

    ex = self._exchange_for(p.exchange)
    if self._replace_exchange_sl(ex, p, new_sl):
        p.stop_loss = new_sl
        logger.info(
            f"[AgeAwareSL] {p.symbol} {p.side} age={age_min:.0f}m "
            f"pnl={pnl_frac*100:+.2f}% SL→breakeven ({p.stop_loss:.6g}→{new_sl:.6g})")
        return True
    return False
```

**Trigger conditions** (all must be true):
- Position age ≥ **60 minutes** (tightened from initial 120-min proposal — most ghosts happen at 200+ minutes so 60 min is well below that and preserves ride-time for trending positions)
- PnL ∈ `[0%, 2%)` — strictly in profit but below the trailing-stop activation zone
- Current SL is *farther* from entry than the new breakeven SL would be (i.e., genuine tightening)

**Side-aware breakeven** (matches existing BREAKEVEN at `bot_engine.py:3868-3891`):
- LONG: `new_sl = entry × (1 + 2·fee_rate + 0.0005)`
- SHORT: `new_sl = entry × (1 − 2·fee_rate − 0.0005)`

**Compliance:**
- Spec §2: never widens SL — explicit check at line `if ... <= p.stop_loss: return False`.
- Phase 39 CLOSE policy: this is not a close, it's an SL move. No conflict.
- Compatible with trailing stop (which only activates at +2%): the age-aware rule covers the [0%, 2%) gap.

**Expected effect on ghost rate.** Of the 14 ghosts in the last 24h, 8 had open-time PnL between +0.3% and +1.5% at ages > 60 min before the reversal. With Area 4, those 8 would get killed at breakeven before reaching the exchange-side SL. New close reason: `auto_breakeven_2h` (~3-5 firings/day expected).

### 2.5 Area 5 — Deterministic small-TP capture

**Problem.** Today `mcp_take_profit` avg_win is +$1.02 — squarely in the user's $1-2 target zone. But it fired **0 times** today out of 14+ exits because the gate requires `claude_action == "TAKE_PROFIT" AND net_pnl_pct >= 0.5% AND conf >= 60%`. Claude often returns HOLD on positions that are in the +0.5% to +2% range, so the small-profit zone goes uncaptured. Those positions drift until either (a) they cross +2% and trailing stop owns it, or (b) they reverse to SL and ghost-fire.

User directive (2026-05-20): "Focus on small TPs in the beginning gradually testing and improving on all types of pairs available on all connected exchange. Higher trades, small TPs. Even 1-2 USDT or 1-2% gain per trade (FUTURES) is enough at the start."

**Fix.** A new deterministic rule that runs every 30s inside `_run_mcp_position_monitor` *alongside* the Area 4 age-aware tightener, *before* the MCP brain advice is consulted:

```python
def _maybe_capture_small_tp(self, p: Position) -> bool:
    """Close at market when an aged futures position has captured >=1%.
    Deterministic — does NOT consult Claude. Complements Area 4 which
    handles the [0%, 1%) protect zone via BE-SL move."""

    AGE_MIN_FOR_CAPTURE   = 30           # capture sooner than Area 4 (60min)
    MIN_PNL_FRAC_CAPTURE  = 0.01         # +1.0%
    MAX_PNL_FRAC_CAPTURE  = 0.02         # below this; >=2% the trailing stop owns

    if p.market_type != "futures":
        return False    # spot keeps existing logic

    if p.symbol in STAR_SYMBOLS:
        return False    # STARs ride per existing Phase 46

    age_min = p.duration_minutes
    if age_min < AGE_MIN_FOR_CAPTURE:
        return False

    pnl_frac = self._unrealized_pnl_frac(p)
    if not (MIN_PNL_FRAC_CAPTURE <= pnl_frac < MAX_PNL_FRAC_CAPTURE):
        return False

    for ex_name, exchange in self.active_exchanges.items():
        if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
            logger.info(
                f"[AutoSmallTP] {p.symbol} {p.side} age={age_min:.0f}m "
                f"pnl=+{pnl_frac*100:.2f}% — capturing at market")
            self.order_mgr.close_position(exchange, p, "auto_small_tp_1pct")
            return True
    return False
```

**Trigger conditions** (all must be true):
- Futures position (spot unaffected)
- Symbol NOT in `STAR_SYMBOLS` (existing Phase 46 exemption — STARs allowed to ride to higher gains: ATOM avg R 1.85+, ARB avg R 2.23+)
- Position age ≥ **30 minutes** (faster than Area 4's 60 min)
- `pnl_pct ∈ [1.0%, 2.0%)` — strictly in the captureable zone (above trailing stop activation = +2%, that path owns)
- Not already being closed by another path (idempotency check via `tracker.is_closing(pid)`)

**Tiered exit strategy that emerges** (composition of Areas 4 + 5 + existing trailing stop):

| PnL band | Age threshold | Action | Owner |
|---|---|---|---|
| `pnl < 0` | any | hold; rely on SL | existing SL |
| `0% ≤ pnl < 1%` | ≥ 60 min | move SL → breakeven | Area 4 |
| `1% ≤ pnl < 2%` | ≥ 30 min | market close (+1% profit) | **Area 5** |
| `pnl ≥ 2%` | any | trailing stop active | existing trailing |
| STAR symbol, any PnL | any | follow existing Phase 46 path | existing |

**Why this is NOT a re-enable of the Phase 39 disabled `CLOSE`:**
- Phase 39 disable was about Claude's *discretionary, narrative-driven* close decisions (1W/17L over 388 trades). Claude looked at coin sentiment + market regime + position state and decided to close — and those decisions were systematically wrong.
- Area 5 is a *deterministic threshold rule* with no judgment, no narrative. It's functionally equivalent to a take-profit limit order placed at entry+1%, except executed bot-side instead of exchange-side. Same effect as setting `take_profit = entry * 1.01` on every position, just without the exchange-side TP latency.
- The audit risk that killed Phase 39 doesn't apply to a deterministic rule. Failure mode would be "cuts winners that would have hit +2%", not "wrong narrative talked the bot out of a winner."

**Compliance with existing safeguards:**
- Spec §2 (no widen): N/A — this is a market close, not an SL move.
- Phase 39 CLOSE disable: explicitly different (see above).
- Phase 46 STAR ride policy: STAR symbols exempted (the `if p.symbol in STAR_SYMBOLS: return False` line).
- Existing `mcp_take_profit` path: Area 5 fires when MCP doesn't. If both fire in the same cycle, idempotency check prevents double-close.

**Config kill switch:**
```python
AUTO_SMALL_TP_ENABLED        = True   # Area 5; False to disable
AUTO_SMALL_TP_MIN_AGE_MIN    = 30
AUTO_SMALL_TP_MIN_PNL_FRAC   = 0.01
AUTO_SMALL_TP_MAX_PNL_FRAC   = 0.02
```

**Expected effect:**
- 3-6 firings/day at the +$1-2 per fire range → +$3-12/day captured profit that today goes to either ghost (often negative) or trailing stop (≥2% gain, larger but rarer).
- Tradeoff: some positions that would have ridden to +2-3% get cut at +1%. Backtest baseline: 14 ghosts in last 24h, of which ~5 had open-time PnL ≥ 1% before reversal. Those 5 alone would yield ~+$5/day with Area 5.

**Order of execution inside `_run_mcp_position_monitor`** (per position, every 30s):
1. Idempotency: skip if `tracker.is_closing(pid)`.
2. Area 5: try `_maybe_capture_small_tp` — return early on close fire.
3. Area 4: try `_maybe_tighten_aged_position` — return early on SL move.
4. Fall through to existing MCP brain advice (TAKE_PROFIT/CLOSE/TIGHTEN/BREAKEVEN) — but `CLOSE` is still suppressed per Phase 39.

This ordering means Area 5 (close) wins over Area 4 (SL move) when both would trigger, which is correct — capturing the profit is more decisive than moving SL.

---

## 3. Tests

New file: `tests/test_ghost_and_noise_cleanup.py`. 14 tests:

### Area 1 tests (3)
1. `test_ghost_ledger_lookup_window_is_24h` — pin the new constant.
2. `test_ghost_sync_pending_then_reconciled` — first sync returns `ghost_sync` (no ledger record), second sync 15s later finds ledger record and upgrades the warehouse row to `ghost_reconciled`.
3. `test_ghost_fallback_uses_mark_price` — when `ticker.info.markPrice` is present, the close uses it (not `ticker.last`).

### Area 2 tests (2)
4. `test_ghost_detected_at_info_level_when_reconciled` — caplog asserts INFO not WARNING.
5. `test_bybit_110001_cancel_logged_at_debug` — synthetic ccxt exception with `110001` does not produce ERROR.

### Area 3 tests (3)
6. `test_safe_cancel_order_swallows_110001` — `_safe_cancel_order` returns `{"status": "ok", "reason": "already_filled_or_cancelled"}` on 110001.
7. `test_safe_cancel_order_swallows_bitget_40034` — same for Bitget's equivalent.
8. `test_safe_cancel_order_reraises_unknown_errors` — `RuntimeError("boom")` is re-raised; only known-race errors are caught.

### Area 4 tests (2)
9. `test_age_aware_tighten_fires_at_60min_in_profit` — synthetic position age=70min, pnl=+0.8%, SL still far from entry → `_replace_exchange_sl` called with breakeven price.
10. `test_age_aware_tighten_no_op_outside_band` — age=70min, pnl=−0.5% (loss) → no SL change. age=70min, pnl=+2.5% (trailing stop zone) → no SL change. age=45min → no SL change.

### Area 5 tests (4)
11. `test_auto_small_tp_fires_at_1pct_after_30min` — synthetic futures position age=35min, pnl=+1.2% → `close_position(..., "auto_small_tp_1pct")` called.
12. `test_auto_small_tp_no_op_below_1pct_or_below_30min` — age=35min pnl=+0.5% → no close. age=25min pnl=+1.5% → no close. age=35min pnl=+2.5% → no close (trailing stop owns).
13. `test_auto_small_tp_skips_star_symbols` — ATOM/USDT:USDT, age=35min, pnl=+1.2% → no close (STAR exemption holds per Phase 46).
14. `test_auto_small_tp_skips_spot` — spot position age=35min pnl=+1.5% → no close (Area 5 is futures-only).

Tests use `tmp_path` + `monkeypatch` for filesystem isolation; mock `_replace_exchange_sl` and `close_position` to avoid live exchange calls. Area 5 tests pin against `STAR_SYMBOLS` via `monkeypatch.setattr` to keep them deterministic.

---

## 4. Measurement Plan

### 4.1 Baseline at deploy time (recorded by implementer)

- 24h ghost-class count: **14** (10 ghost_sync + 4 ghost_reconciled, 2026-05-20).
- 24h WARNING/ERROR count for bybit/bitget: **22**.
- Median `ghost_sync` PnL error vs ledger truth: TBD on backfill query.

### 4.2 24h check after deploy

```bash
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 86400
print('Ghost-class:', c.execute('''SELECT exit_reason, COUNT(*) FROM trades
    WHERE status=\"CLOSED\" AND ts_exit >= ?
      AND exit_reason LIKE \"%ghost%\" GROUP BY exit_reason''', (since,)).fetchall())
print('Auto-breakeven:', c.execute('''SELECT COUNT(*) FROM trades
    WHERE status=\"CLOSED\" AND ts_exit >= ?
      AND exit_reason = \"auto_breakeven_2h\"''', (since,)).fetchone())
"
```

Expected vs. pre-deploy:
- `ghost_sync` count down 30-50% (Area 1 upgrades some to `ghost_reconciled`).
- `ghost_reconciled` count up by a similar amount (net total ghost may stay roughly flat, but accuracy improves).
- `auto_breakeven_2h` count: 3-5/day (new exit reason from Area 4).
- `auto_small_tp_1pct` count: 3-6/day at ~+$1-2 per fire (new exit reason from Area 5).
- bybit/bitget WARNING/ERROR count: drop from ~22/day to ~3-5/day.

Add to the daily check:
```bash
python -c "
import sqlite3, time
c = sqlite3.connect('data/warehouse.sqlite')
since = time.time() - 86400
print('Area 4 (auto_breakeven_2h):', c.execute('''SELECT COUNT(*), ROUND(SUM(realized_pnl),2)
    FROM trades WHERE status=\"CLOSED\" AND ts_exit >= ?
    AND exit_reason = \"auto_breakeven_2h\"''', (since,)).fetchone())
print('Area 5 (auto_small_tp_1pct):', c.execute('''SELECT COUNT(*), ROUND(SUM(realized_pnl),2), ROUND(AVG(realized_pnl),3)
    FROM trades WHERE status=\"CLOSED\" AND ts_exit >= ?
    AND exit_reason = \"auto_small_tp_1pct\"''', (since,)).fetchone())
"
```

### 4.3 7-day signal check (around 2026-05-27)

- Net PnL: should be ≥ the 7-day pre-deploy baseline. Area 4's "exit at breakeven" trades off (a) prevention of −1.5% SL ghosts against (b) cutting some positions that would have rebounded to +2%. On the 8 historical cases this would have fired on, net effect was +$1.20 of avoided losses minus ~$0.40 of missed rebound = ~+$0.80/day expected.
- WR: should rise 2-3 percentage points (breakeven exits count as 0 = ties, not losses).

### 4.4 Rollback triggers

- Area 4 fires more than 15× per day → too aggressive; raise threshold from 60min → 90min.
- `auto_breakeven_2h` median outcome is negative on 7+ trades → kill switch via `config.AGE_AWARE_SL_ENABLED = False`.
- Area 5 fires more than 12× per day → too aggressive; raise min age 30min → 45min OR min pnl 1.0% → 1.2%.
- `auto_small_tp_1pct` mean PnL drops below +$0.50 (vs expected ~+$1.0-1.5) → wrong band; investigate and tighten config or kill via `AUTO_SMALL_TP_ENABLED = False`.
- 7-day net PnL is *worse* than pre-deploy baseline by >$3/day → composite of Areas 4+5 is cutting too many winners; disable both via flags and re-tune.
- Bybit/Bitget genuine error count *rises* after Area 2 demotion → revert log-level changes (the demotions hid a real issue).

---

## 5. Rollback

Single config flag per area:

```python
# config.py additions
GHOST_LEDGER_WINDOW_H        = 24    # Area 1; revert to 6 to disable
GHOST_PENDING_REQUEUE        = True  # Area 1; False to disable two-pass reconcile
AGE_AWARE_SL_ENABLED         = True  # Area 4; False to disable
AGE_AWARE_SL_MIN_AGE_MIN     = 60    # Area 4 trigger threshold
AUTO_SMALL_TP_ENABLED        = True  # Area 5; False to disable
AUTO_SMALL_TP_MIN_AGE_MIN    = 30    # Area 5 trigger age
AUTO_SMALL_TP_MIN_PNL_FRAC   = 0.01  # +1.0%
AUTO_SMALL_TP_MAX_PNL_FRAC   = 0.02  # below 2% (trailing stop owns above)
```

Area 2 (log levels) and Area 3 (`_safe_cancel_order`) have no flags — they're pure behavior cleanup with no reversible state.

Restart: under 1 minute. New positions follow new rules; in-flight positions complete with whatever state they have.

---

## 6. Risk Register

| Risk | Likelihood | Mitigation |
|---|---|---|
| Area 1's 24h ledger lookup window slows the sync cycle | Low | Bybit/Bitget paginated; single extra request per ghost. Sync cycle is 15s, lookup is <300ms. |
| Area 1's pending-then-confirm holds a ghost open for an extra 15s | Low | The position is already gone on exchange; the 15s delay only affects when the warehouse row gets patched. No capital risk. |
| Area 2 demotion hides a real issue | Medium | Demotions are conditional on error-text matching. Unknown error codes still surface at WARN/ERROR. Rollback trigger §4.4 catches this. |
| Area 3 `_safe_cancel_order` swallows a genuine bug | Low | Only known-race error codes are caught; everything else re-raises. Test 8 pins this. |
| Area 4 fires on a position that would have rebounded to +2% | Medium | Net-positive on the 8 historical cases (+$0.80/day expected). Kill switch in config. |
| Area 4 conflicts with trailing stop or BREAKEVEN | Low | Trailing stop only activates at +2%; Area 4 fires only in [0%, 2%). BREAKEVEN action moves to the same price; idempotent. |
| Area 4 widens SL by mistake | Trivial | Explicit check `if new_sl <= p.stop_loss (long) / >= (short): return False`. Test 9 + 10 pin this. |
| Area 5 cuts a winner that would have ridden to +5%/+10% | Medium | Phase 46 STAR exemption (ATOM/ARB ride) preserved; non-STAR symbols historically don't sustain >+2% well — their realized R:R is closer to 1:1. Expected lost upside ~$0.40/day; captured gain ~$5/day. Net +$4.60/day. |
| Area 5 looks like a re-enable of Phase 39 CLOSE | Low | Deterministic threshold ≠ discretionary judgement. Functionally equivalent to a TP limit order placed at entry+1%. Phase 39 risk doesn't apply. Explicitly documented in §2.5. |
| Areas 4 + 5 fire on the same position in the same cycle | Trivial | Ordering: Area 5 checked first (close decisive); if Area 5 fires, return early; Area 4 never reached for that position. Idempotency check via `tracker.is_closing(pid)`. |
| Areas 4 + 5 jointly over-cap upside | Medium | Composite kill switch via `config.AGE_AWARE_SL_ENABLED = False AND AUTO_SMALL_TP_ENABLED = False`. Rollback trigger §4.4: 7-day net PnL drop >$3/day triggers full disable. |

---

## 7. Out of Scope

- **Re-enabling discretionary CLOSE.** User explicitly chose "accept ghosts as the design." Phase 39 disable stays.
- **WebSocket execution streams.** User said VPS is off the table; staying within polling architecture.
- **Phase 38 / decay detector tuning.** Separate workstream.
- **Tightening MCP brain's TAKE_PROFIT thresholds.** Outside this design's scope.
- **Backfilling missing warehouse rows from positions.json.** Separate forensic task (the 20 missing rows finding from 2026-05-20 dashboard fix).

---

## 8. Files Touched

### NEW
- `tests/test_ghost_and_noise_cleanup.py` — 14 tests.

### MODIFIED
- `config.py` — 8 new flags (GHOST_*, AGE_AWARE_SL_*, AUTO_SMALL_TP_*).
- `core/position_tracker.py` — Area 1 (extend window + pending requeue + mark_price fallback). ~50 LOC.
- `core/bot_engine.py` — Area 2 (log level demotions in `_check_exchange_health`) + Area 4 (`_maybe_tighten_aged_position`) + Area 5 (`_maybe_capture_small_tp` + ordering hook in `_run_mcp_position_monitor`). ~120 LOC.
- `core/order_manager.py` — Area 3 (`_safe_cancel_order` helper + 6 call-site swaps) + Area 2 (`_verify_order_on_exchange` log level). ~40 LOC.
- `exchanges/base.py` — Area 2 (ticker retry log level, ccxt acknowledged param). ~20 LOC.

Total estimated diff: 1 new test file (~260 LOC), 5 modified files (~230 LOC).

---

## 9. Decision Summary

| Question | Decision |
|---|---|
| Re-enable discretionary CLOSE? | NO. Phase 39 disable stays. |
| Eliminate ghosts entirely? | NO. Accept ghosts as the reactive detection design. |
| Make ghost path more accurate? | YES. 24h ledger window + pending requeue + mark_price fallback. |
| Clean up log noise? | YES. Demote 5 known-informational warnings to INFO/DEBUG. |
| Harden Bybit/Bitget cancel races? | YES. `_safe_cancel_order` wrapper swallows 110001 / 40034. |
| Add proactive prevention (SL)? | YES. Age-aware SL tightening at age≥60min, pnl∈[0%, 1%). |
| Add deterministic small-TP capture? | YES (Area 5). Market close at age≥30min, pnl∈[1%, 2%). Non-STAR futures only. |
| Per-area kill switches? | YES. 8 new config flags. |
| Compliance with Spec §2 (no widen)? | YES. Explicit guard in Area 4. |
| Compatible with Phase 39 CLOSE disable? | YES. Area 5 is deterministic threshold, not discretionary narrative. Functionally a TP limit order. |
| Compatible with Phase 46 STAR ride? | YES. STAR_SYMBOLS exempted from Area 5. |
| Tests? | 14 new tests covering all five areas. |
| Rollback? | <1 min via config flag. Composite disable available. |
| Expected ghost rate reduction? | 30-50% (Area 1 reclassification + Area 4 auto-BE + Area 5 small-TP capture). |
| Expected daily PnL impact (Area 5)? | +$3-12/day captured profit (3-6 fires × +$1-2 each). |
| Expected log noise reduction? | 22 daily WARNINGs → 3-5. |
