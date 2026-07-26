# 04 — Integration Report: Listing-Short Log-Only Shadow Probe (rev3)

Agent: shadow-integrator · Date: 2026-07-09
Input: `03_rev3_audit_findings.md` (Candidate B — **CONFIRMED_GO, log-only shadow probe**),
`02b_rev3_screener_listing_short.md`/`.json` (frozen pre-registration, GO @7d,30d).
Charter: LOG-ONLY. No `mcp_brain` / `order_manager` / risk-gate / config-live edits. No commit.

---

## 1. What was built

A `ListingShortProbeAgent` (`core/agents/listing_short_probe_agent.py`) that forward-soaks the
pipeline's first CONFIRMED_GO candidate — the capital-scaled post-listing perp short — at **zero
capital risk**. It runs inside the already-log-only shadow lane and is **structurally incapable of
placing an order**: its constructor receives only read-only providers (markets / market-data /
OHLCV / balance) plus the warehouse, and the module contains no reference to any order path
(guarded by `test_structural_log_only_no_order_path`).

Per shadow tick (300 s) it does three things:

1. **Detect** new Binance USDT-M perp listings — diffs the current perp universe against a
   persisted known-symbols set. The **first** run only *seeds* the baseline and proposes nothing
   (we can only honestly shadow-probe listings that appear *after* the baseline). New symbols are
   filtered **crypto-only** using a mirror of the frozen screen's `is_crypto_base` +
   `EQUITY_COMMODITY_BASES` (drift-guarded by `test_is_crypto_base_mirrors_screen`), excluding the
   10 equity/commodity perps and non-ASCII junk bases.
2. **Enter** each detected listing at its **day-1 close** (24 h after listing, matching the screen's
   `first_ts + DAY`), sized **3% of the paper account notional**, **max 4 concurrent per horizon**
   (12% cap); latecomers beyond the cap are **SKIPPED and counted** in chronological order (no
   cherry-picking). Both **7d and 30d** horizon variants are logged (90d is INSUFFICIENT per the
   audit → not probed). Non-shortable or funding-less listings are skipped and recorded.
3. **Monitor** every open short each tick: append the **per-bar (1h) intra-hold MTM path**, accrue
   **realized funding** (one rate per 8h settlement bucket), and recompute + log the **concurrent
   account-MTM drawdown** per horizon.

Realized after-cost economics are **not** computed here. Each entry writes a standard
`shadow_decisions` row (`side='sell'`, `sl_px=0`, `tp_px=0`, `horizon_bars=H*24`) that the vetted
keystone resolver (`core/shadow_resolver.py`) replays into `shadow_outcomes` as a held-to-horizon
time-exit. Custom PnL math in a probe is a bug; this probe does none. (Verified end-to-end: a 7d row
resolved to a `time` exit with a real after-cost `net_pnl`; the 30d row correctly stays PENDING
until 720 forward bars accrue.)

## 2. Where it hooks in

| Layer | Change | File |
|---|---|---|
| Probe agent | new class + pure math + 3 companion tables | `core/agents/listing_short_probe_agent.py` (new) |
| Shadow lane | `ShadowRunner(..., extra_probes=[...])` — market-wide probes tick once/tick, guarded | `core/shadow_runner.py` |
| Engine wiring | `_build_listing_probe` + read-only providers (`_listing_markets/_market_data/_ohlcv`) | `core/bot_engine.py` |
| Config | `LISTING_SHORT_PROBE` (shadow-lane only, env `SHADOW_LISTING_PROBE_ENABLED`) | `config.py` |
| Resolution | **reused unchanged** — `shadow_resolver.resolve_pending` (scheduled hourly) | `scripts/resolve_shadow_outcomes.py` |
| Tests | probe lifecycle + math + structural log-only + runner hook + engine wiring | `tests/test_listing_short_probe.py` (new), `tests/test_botengine_shadow_wire.py` |

Restart-safe: the known-listings set + pending (detected-but-not-yet-entered) listings persist to
`data/shadow_listing_state.json` (gitignored via `/data/`); open positions are reconstructed from
the warehouse and their MTM paths re-derived idempotently. `test_state_survives_restart` proves a
listing detected before a restart still enters after it.

## 3. The pre-specified discriminating score (binding condition B5)

Frozen in code **before any outcome exists** (`listing_short_score`). The AUC≥0.60 gate is
un-computable without a per-decision score; this is it. Monotone **increasing** in the two levers of
the hypothesis:

```
score = tanh(pump_pct / 0.50) + 10.0 * funding_rate_8h
```

- `pump_pct` = (max first-24h high − listing price) / listing price — the pump the short will fade.
  Bigger pump ⇒ more expected decay ⇒ higher score. `tanh` bounds a single +290% outlier so it
  cannot dominate the ranking.
- `funding_rate_8h` = day-1 funding rate. Short **receives** positive / **pays** negative funding, so
  higher funding ⇒ better short ⇒ higher score.

It is **not** tuned to outcomes (none exist). It **varies** across proposals (pump + funding differ
per listing); the AUC gate later tests whether it actually ranks resolved winners above losers.
Logged per proposal in `shadow_listing_probe.score`; the binary label is derived from the resolved
outcome.

## 4. Schema changes (3 lazy-migration companion tables)

Created by the probe on init (the `warehouse.record_carry_cycle` "CREATE TABLE IF NOT EXISTS"
idiom); `core/warehouse.py` is untouched. `shadow_decisions`/`shadow_outcomes` are reused as-is.

- **`shadow_listing_probe`** (1 row per listing×horizon) — the decision + binding evidence:
  `proposal_id` (PK, = the `shadow_decisions.proposal_id` for ENTER rows), `symbol, base,
  horizon_days, decision` (ENTER | SKIP_CAP | SKIP_UNSHORTABLE | SKIP_NO_FUNDING | SKIP_NO_DATA),
  `detected_ts, entry_ts, entry_px, listing_px, stake_frac, notional_usd, day1_spread_bps,
  day1_funding_rate, shortable, quote_volume_usd, pump_pct, score, realized_funding_rate_sum,
  last_funding_bucket, concurrent_open_at_entry, created_ts`.
- **`shadow_listing_mtm`** (per-bar path, B1 #1) — `(proposal_id, bar_ts, mark_px,
  unrealized_short_ret)`, PK `(proposal_id, bar_ts)`.
- **`shadow_listing_concurrent`** (per-horizon snapshot, B2) — `(horizon_days, snapshot_ts, n_open,
  account_mtm, peak_equity, max_drawdown)`, PK `(horizon_days, snapshot_ts)`.

## 5. How the promotion gate becomes evaluable — metric → data source

The gate stays **frozen** (`core/promotion_gate.py`: MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55,
AUC≥0.60) and is applied **only** by the owner, only after ≥30 resolved trades (B6). Every input is
now logged:

| Gate metric | Source |
|---|---|
| **Per-trade after-cost return** | `shadow_outcomes.net_pnl` (fees + slippage, SL-first) **plus realized funding** `shadow_listing_probe.realized_funding_rate_sum × notional_usd` (short receives +). JOIN on `proposal_id`, filter `model_version='listing_short_probe_v1'`. |
| **Win rate ≥ 0.55** | fraction of resolved trades with (net_pnl + funding$) > 0 |
| **OOS-WR ≥ 0.55** | purged/embargoed walk-forward over listing-date order using `entry_ts`/`entry_ts+H·DAY` from the probe rows (screen's `_oos_wr_walk_forward`) |
| **DSR ≥ 0.10** | deflated Sharpe on the account-scaled return series, `n_trials ≥ 6` carried forward (grows with each family registration — B6) |
| **PBO ≤ 0.50** | CSCV across the 7d/30d horizons (needs both resolved) |
| **AUC ≥ 0.60** | ROC over `(shadow_listing_probe.score, win)` — the whole reason B5 exists |
| **Concurrent-MTM maxDD ≤ 0.25** | `shadow_listing_concurrent.max_drawdown` (the corrected B1 number, **not** the realized cumsum) — recomputable from `shadow_listing_mtm` via `concurrent_account_mtm()` |
| **Day-1 execution realism** (replaces modeled 5bps) | `shadow_listing_probe.day1_spread_bps, day1_funding_rate, shortable, quote_volume_usd` |

## 6. What the owner should watch during the soak

- **`trading_bot_shadow_vs_live`** (MCP) — the listing shorts flow into `shadow_outcomes`, so they
  join the resolved shadow-vs-live comparison automatically. This is aggregate; use the queries
  below to isolate the listing family.
- **Listing family, resolved-only** (via `trading_bot_query`):
  ```sql
  SELECT p.symbol, p.horizon_days, p.score, p.pump_pct, p.day1_funding_rate,
         o.net_pnl, p.realized_funding_rate_sum * p.notional_usd AS funding_usd,
         (o.net_pnl + p.realized_funding_rate_sum * p.notional_usd) AS after_funding_net,
         o.exit_reason, o.bars_held
  FROM shadow_listing_probe p JOIN shadow_outcomes o ON o.proposal_id = p.proposal_id
  WHERE p.decision='ENTER' AND o.label_status='RESOLVED' ORDER BY p.entry_ts DESC;
  ```
- **Concurrent-MTM drawdown (the corrected risk number, gate ≤0.25):**
  ```sql
  SELECT horizon_days, MAX(max_drawdown) AS worst_concurrent_dd, MAX(snapshot_ts) AS as_of
  FROM shadow_listing_concurrent GROUP BY horizon_days;
  ```
- **Pipeline / cap health:**
  ```sql
  SELECT horizon_days, decision, COUNT(*) FROM shadow_listing_probe GROUP BY 1,2;
  ```
- **AUC readiness:** count resolved ENTER rows; do **not** compute AUC/DSR/MC until ≥30 resolved
  (B6). Watch the day-1 `day1_spread_bps` distribution vs the modeled 5 bps (B7) — if real day-1
  spreads dwarf 5 bps, re-run the gate battery at the observed cost before any promotion talk.

**Promotion remains a manual owner decision** on `core/promotion_gate.py` evidence. Nothing here
moves capital, changes leverage, adds a stop, or touches a live decision path.

## 7. Deviations from the binding conditions (all disclosed)

- **B1 realized funding:** captured as a forward per-8h-settlement rate accrual
  (`realized_funding_rate_sum`), not fetched from a funding-history endpoint per settlement. This is
  an honest forward accrual; combine with `shadow_outcomes.net_pnl` for the true after-funding net
  (query in §6). The resolver itself runs `funding=0`, so `shadow_outcomes.net_pnl` alone is
  funding-EXCLUSIVE — always add the funding term.
- **B2/B3 unlevered, no-SL, held-to-horizon (naked 1×):** modeled exactly (`sl_px=0`, `tp_px=0`).
  This is the audit's safe variant. The probe does **not** inherit any live leverage tier or the
  mandatory 8% SL; a levered/SL variant, if ever wanted, must be a separate labeled probe (B2).
- **B4 shortability/borrow/position-cap:** `shortable` = market active + live bid/ask; funding
  presence required (never guessed → SKIP_NO_FUNDING). Perps have no borrow; a hard exchange
  open-interest cap on a brand-new listing is not observable read-only and is left to surface as a
  fill-side anomaly during the soak.
- **Cost model:** the resolver charges 6 bps/side taker (vs the screen's 5 bps) + 5 bps slippage —
  slightly **more** conservative, and consistent with the rest of the shadow lane.
- **Entry timing:** the day-1 close is taken from the first observed candle + 24h, gated on ≥24h
  since detection. Detection lag is ≤ one shadow tick (300 s).

## 8. Verification

- `venv/Scripts/python.exe -m pytest tests/test_listing_short_probe.py -q` → 16 passed.
- `venv/Scripts/python.exe -m pytest tests/ -q -k "shadow or listing"` → 116 passed.
- Full bot suite `pytest tests/ -q` → **2659 passed**.
- `ruff check` clean on all touched files.
- End-to-end smoke: probe ENTER → `shadow_resolver.resolve_pending` → `shadow_outcomes` (7d
  time-exit `net_pnl`; 30d correctly PENDING).
