# [ops] AccBand drought — SCALP quiet + band BTC-vol

**UTC:** 2026-08-01 ~19:00Z  
**Mode:** PAPER / MAX_FLOW_BAND  
**Action:** diagnose + fix (no CONTROLLED_LIVE)

## Verdict

Bot was alive and correctly refusing. Post-allowlist restore it still printed **0 ALLOW** because `SCALP_MODE` quiet-ATR veto owned the scorer. Turning scalp off restored OPEN attempts; those then died on `band_regime_filter:btc_vol<0.7`. Band off → **first fill BCH/USDT** (maker).

## Evidence

| Window | Signal |
|--------|--------|
| Pre-fix funnel | `strategy_not_approved_for_paper` ×2 (F1-only window) then allowlist restored |
| Post-boot (scalp ON) | 2430 candidates, **0 ALLOW**; families: scalp_veto:quiet 1324, analysis_only 756 |
| After `SCALP_MODE_ENABLED=false` | ALLOW 10+, OPEN ADA/XRP/APT/ATOM/ETC; all execute_open → `band_regime_filter:btc_vol<0.7` |
| After `BAND_REGIME_FILTER_ENABLED=false` | trade_events OPEN+FILL **BCH/USDT**; funnel `filled=1` |

External: BTC perp funding still compressed near 0% → F1 `idle_no_edge` is regime-correct ([Convex](https://convextrade.com/metrics/btc-funding), [FuturesPulse](https://futurespulse.io/en/crypto-in-depth-funding-vs-long-skew-2026-07-25/)).

## Changes

1. `.env` `SCALP_MODE_ENABLED=false` (do **not** lower `SCALP_MIN_ATR` without hashed prereg — MC standing warning).
2. `.env` `BAND_REGIME_FILTER_ENABLED=false` (re-admits low-vol WR headwind ~55.6% from screen-13; AccBand still ≈ −0.24R expectancy).
3. Launcher pins `SCALP_MODE_ENABLED` from `.env` (`scripts/launcher_supervisor.py` + test).
4. Attended `TradingBot-24x7` End → kill orphans → Run.

## Honesty

- Restores **PAPER research flow**, not a CONFIRMED_GO edge.
- F1 remains idle until funding/contango clears — do not loosen F1 thresholds from narrative.
- Re-enable band when accuracy-first > accrual.

## ECC recipe (advisory)

Workflow: diagnose and fix zero PAPER fills.  
Best fit: `/orch-fix-defect` — reproduce → fix → review.  
Alt: `/python-review` after the change.  
STOP when: heartbeat fresh + ≥1 OPEN fill or named non-starvation reject in a post-boot window.
