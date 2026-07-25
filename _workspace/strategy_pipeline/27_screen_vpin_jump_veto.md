# 27 — Screen: VPIN jump-risk veto

**Generated:** 2026-07-25T01:19:53.313309+00:00
**Prereg sha256_md:** `2b880d1beaefd5f9b16b23997c15214b66489d36b4ac707432524c5d788c4406`
**Owner override:** full_screen_skip_stage0 (`36_owner_override_vpin_full_screen.md`)
**Expectation:** NO_GO
**Overall verdict:** **NO_GO**

## Substrate

- AccBand resolved rows (binance BTC/ETH perps): n=3050
- With pre-decision VPIN join: n=3050
- Baseline mean R: -0.2623
- Baseline WR: 0.6374
- Baseline MC P(>0): 0.000, maxDD p95: 932.495

## θ grid results

| θ | n_kept | n_skip | fire% | kept_R | ΔR | p(Δ≤0) | MC P>0 | maxDD p95 | verdict |
|---|--------|--------|-------|--------|----|--------|--------|-----------|---------|
| 0.55 | 3050 | 0 | 0.000 | -0.2623 | 0.0000 | 0.496 | 0.000 | 936.344 | **NO_GO** |
| 0.60 | 3050 | 0 | 0.000 | -0.2623 | 0.0000 | 0.496 | 0.000 | 936.344 | **NO_GO** |
| 0.65 | 3050 | 0 | 0.000 | -0.2623 | 0.0000 | 0.496 | 0.000 | 936.344 | **NO_GO** |
| 0.70 | 3050 | 0 | 0.000 | -0.2623 | 0.0000 | 0.496 | 0.000 | 936.344 | **NO_GO** |

## Gates (frozen)

- ΔEV (kept − baseline) > 0 OOS
- MC P(total>0) ≥ 0.95 on kept arm
- MC maxDD p95 ≤ 0.25
- n skipped+kept ≥ 30
- Holm multiplicity on n_trials=4; adjacent-θ same-sign
- Bleed-mask: WR↑ with EV↓ → NO_GO

## Non-goals

- No live install / MCP wire from this screen alone.
- Directional VPIN remains ledger STOP.
