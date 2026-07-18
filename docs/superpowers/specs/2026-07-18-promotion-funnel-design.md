# Promotion Funnel — Design Spec (2026-07-18)

**Goal:** convert the bot's existing validated/shadow strategy lanes into promoted, capital-earning strategies as fast as the evidence allows — by fixing accrual blockers, monitoring every lane against the frozen promotion gate, and auto-staging a sign-off-ready dossier the moment a lane passes. Owner-approved scope: **fix + monitor + auto-stage** (promotion itself always stays owner-signed).

**Why now:** the 2026-07-16/17 pipeline run produced 0 GO; the ledger holds 19 refuted families. The honest path to "proven & tested" runs through forward evidence on lanes that already exist: F1 carry (validated, regime-idle), listing-short (starved by tokenized-stock listing flow), unlock-short (calendar-freshness risk), TSMOM-20d ×2 arms, breakout-60d, and the accuracy-band cohort. Nothing today surfaces their gate-readiness or alerts when F1's regime thaws.

## 1. Architecture

- New `scripts/promotion_funnel.py`, executed hourly by Windows scheduled task `TradingBot_PromotionFunnel` (venv python, 10-min timeout).
- **Read-only inputs:** `data/warehouse.sqlite` (`mode=ro` URI), `data/funding_history/*.csv`, `data/carry_gate_log.jsonl` (tail), `data/goal_progress.json`, unlock-calendar store, probe state files.
- **Outputs:** `data/promotion_funnel.json` (atomic tmp+replace), journal section appended to `journal/YYYY-MM-DD.md` **only on state change or alert**, dossiers under `reports/promotion_dossiers/<lane>_<YYYYMMDD>/`.
- **Hard boundary:** zero changes to the live bot process, its config, or any decision path. The funnel imports only pure-computation modules (`core/promotion_gate.py` gate math) — never engine/order/exchange code.
- Small additive patch to `scripts/report_goal_progress.py`: the daily 23:55 report includes a funnel summary section read from `promotion_funnel.json` (reporting script, not a live path).

## 2. Components

1. **LaneTracker** — per lane (`listing_short`, `unlock_short`, `tsmom_20d_1h`, `tsmom_20d_4h`, `breakout_60d`, `f1_carry`, `band_cohort`): resolved count, wins, floor progress (n/30), 7-day accrual rate, ETA-to-floor (days; `null` when rate=0), state ∈ `ACCRUING | STARVED | GATE_READY | STAGED | IDLE | ERROR`.
2. **StarvationDiagnostics** —
   - listing lane: classify recent proposals crypto-native vs tokenized-stock/leveraged-ETF using a static copy of the asset-class base sets (duplicated from `core/pair_discovery` with a provenance comment — keeps the funnel's import surface at zero beyond `promotion_gate`); report drought explicitly (`crypto_native_listings_30d: N`).
   - unlock lane: calendar coverage check — if forward coverage < 30 days ⇒ `STARVED` with the exact backfill command in the report.
3. **F1RegimeWatch** — parse recent `carry_gate_log.jsonl` entries (the bot's own net-edge numbers; no re-derivation): alert when any venue-symbol shows `net_edge_bps > 0` on ≥3 consecutive gate evals (hysteresis); always publish daily top-5 edges so the regime thaw is visible before it's tradeable.
4. **GateRunner** — when a lane reaches ≥30 resolved: run the frozen battery (DSR≥0.10, PBO≤0.5, OOS-WR≥0.55, AUC≥0.60, MC P>0≥0.95, maxDD p95≤0.25) via existing `core/promotion_gate.py` unchanged; record per-gate numbers in the funnel JSON.
5. **DossierBuilder** — on gate pass ⇒ `STAGED`: write dossier directory containing (a) `evidence.md` — per-gate numbers, resolved-outcome table, binding caveats copied from the lane's integration report; (b) `evidence.json`; (c) `proposed_change.patch` — the config/status diff promotion would require. **Never applies anything; never touches git.** Owner sign-off = owner applies the patch.
6. **Accrual fix (separate task)** — weekly Windows task `TradingBot_UnlockCalendar` running the existing unlock-calendar backfill with `--forward-days 60` (exact script per `10_integration_report_candidate2.md`; implementation plan verifies the name before scheduling).

## 3. Data flow

Hourly: read stores → compute lane states → atomic-write `promotion_funnel.json` → journal append only on state transition / F1 alert / new dossier. Daily: goal report embeds funnel summary. Dossier creation is idempotent (skips if today's dossier for that lane exists).

## 4. Error handling

Per-lane fail-open: any store read failure sets that lane to `ERROR` with the exception text; other lanes proceed; process exit code 0 unless the JSON write itself fails. No network calls anywhere in the funnel (the backfill task is a separate, already-existing networked script). All file writes atomic. Warehouse strictly `mode=ro`.

## 5. Testing (~20–25 pytest cases, TDD)

- LaneTracker state transitions (fixture stores per state, incl. rate=0 → ETA null, ERROR isolation).
- Starvation classifier: TZA/SOXS ⇒ tokenized; crypto-native base ⇒ native; mixed window counts.
- F1RegimeWatch hysteresis: 2 consecutive positives = no alert, 3 = alert; edge-case gaps in log.
- GateRunner wiring against a synthetic 30-resolved lane (pass and fail variants).
- DossierBuilder completeness + idempotency; atomic-write behavior.
- Zero-live-path guard: test asserts the funnel module imports no engine/order/exchange modules.

## 6. Binding honesty constraints

- The funnel accelerates *surfacing*, not evidence: it cannot manufacture resolved outcomes. ETAs state plainly when a lane is months out (listing-short is expected to read STARVED until crypto-native listings resume).
- Promotion remains owner-signed by construction; the funnel's terminal action is a dossier, never a config change.
- The 63–67% band cohort is tracked as a lane but its tuning protocol (frac steps at 5-trade checkpoints) stays where it is — the funnel reports it, never adjusts it.

## Out of scope

New strategy screens (pipeline territory), any live decision-path change, auto-promotion, widening the listing probe to tokenized stocks (standing prohibition), dashboards/UI.
