# 05 — Accuracy-Band Geometry Simulation

_Generated 2026-07-10 02:27 UTC · research/sim_accuracy_band.py · after-cost, resolved-only._

## Pre-registration

- **Hypothesis:** with a no-edge signal, first-touch win rate is a pure geometry function `SL/(SL+TP)`. Compressing `TP = frac x SL` raises the raw hit rate; the after-cost win rate lands in 60-67% for some frac.
- **What this is / is NOT:** a TUNING measurement of the geometry knob `ACCURACY_TP_FRAC_OF_SL`. It is NOT an edge claim — after-cost expectancy is expected to be negative and is reported alongside the win rate.
- **Universe / source:** `shadow_decisions` (model_version `shadow_v1`), all Binance futures, 8878 rows with non-null barriers.
- **Replay rule:** keep production `sl_px`; set `TP% = max(0.5, frac x SL%)` (live geometry, floor and all); resolve via `core.shadow_resolver.resolve_one` (SL-first, 6bps/side fees, slippage, horizon censoring).
- **Frozen measure:** win rate = share of resolved trades with `net_pnl > 0`; **censored rows excluded** (never counted as wins); Wilson 95% CI per frac.
- **Decision rule:** recommend the frac whose overall after-cost WR is nearest mid-band (62-64%); flag if n < 300 or the CI straddles the band edge.

## Overall frac -> WR curve (after cost)

| frac | n resolved | censored | floor-bound | WR | Wilson 95% CI | exp (R) | exp ($) | avg win (R) | avg loss (R) |
|-----:|-----------:|---------:|------------:|------:|:-------------:|--------:|--------:|------------:|-------------:|
| 0.35 | 8743 | 135 | 6286 |  65.7% ✅ |  64.7%- 66.7% | -0.242 | -0.511 | +0.275 | -1.234 |
| 0.40 | 8736 | 142 | 5465 |  64.9% ✅ |  63.9%- 65.9% | -0.243 | -0.508 | +0.293 | -1.232 |
| 0.45 | 8731 | 147 | 4602 |  63.8% ✅ |  62.8%- 64.8% | -0.244 | -0.509 | +0.315 | -1.229 |
| 0.50 | 8729 | 149 | 3542 |  62.7% ✅ |  61.6%- 63.7% | -0.244 | -0.505 | +0.342 | -1.227 |
| 0.55 | 8725 | 153 | 2682 |  61.3% ✅ |  60.3%- 62.3% | -0.245 | -0.505 | +0.374 | -1.224 |
| 0.60 | 8715 | 163 | 2004 |  59.8% |  58.7%- 60.8% | -0.246 | -0.511 | +0.410 | -1.222 |
| 0.70 | 8698 | 180 | 1241 |  56.9% |  55.9%- 57.9% | -0.246 | -0.519 | +0.490 | -1.218 |

## By side

| frac | buy n | buy WR | buy CI | sell n | sell WR | sell CI |
|-----:|------:|-------:|:------:|-------:|--------:|:-------:|
| 0.35 | 5245 |  69.1% |  67.8%- 70.3% | 3498 |  60.7% |  59.1%- 62.3% |
| 0.40 | 5238 |  68.8% |  67.5%- 70.0% | 3498 |  59.1% |  57.4%- 60.7% |
| 0.45 | 5235 |  67.9% |  66.6%- 69.1% | 3496 |  57.7% |  56.1%- 59.4% |
| 0.50 | 5233 |  67.0% |  65.7%- 68.2% | 3496 |  56.2% |  54.5%- 57.8% |
| 0.55 | 5229 |  65.9% |  64.6%- 67.2% | 3496 |  54.4% |  52.7%- 56.0% |
| 0.60 | 5219 |  65.1% |  63.8%- 66.4% | 3496 |  51.9% |  50.2%- 53.5% |
| 0.70 | 5204 |  62.9% |  61.6%- 64.2% | 3494 |  48.0% |  46.3%- 49.6% |

## Stability: recent (<= 5d) vs older

_(History spans < 10 days, so a 30-day recent/older split is degenerate; a 5-day split is used instead.)_

| frac | recent n | recent WR | older n | older WR |
|-----:|---------:|----------:|--------:|---------:|
| 0.35 | 6836 |  65.2% | 1907 |  67.5% |
| 0.40 | 6829 |  64.2% | 1907 |  67.4% |
| 0.45 | 6824 |  62.9% | 1907 |  67.1% |
| 0.50 | 6822 |  61.6% | 1907 |  66.3% |
| 0.55 | 6818 |  60.1% | 1907 |  65.7% |
| 0.60 | 6808 |  58.4% | 1907 |  64.9% |
| 0.70 | 6791 |  54.9% | 1907 |  63.9% |

## Recommendation

- frac=0.50: WR= 62.7% (95% CI  61.6%- 63.7%), n=8729, exp=-0.244R. INSIDE the 60-67% band.
- **Set `ACCURACY_TP_FRAC_OF_SL=0.50`** to target the band, subject to the CI and n caveats above.
- **Honesty:** the win rate is a geometry artifact. After-cost expectancy is negative across the curve (no-edge signal), so a higher win rate does NOT imply profit — it re-shapes the win/loss ratio. This measurement tunes geometry only and does not clear any promotion gate.
