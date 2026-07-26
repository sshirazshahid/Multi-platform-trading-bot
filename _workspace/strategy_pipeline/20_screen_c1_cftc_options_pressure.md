# 20 — Screen: C1 CFTC options-pressure

**Verdict:** `NO_GO` (expectation NO_GO)
**Generated:** 2026-07-23T02:51:31.809710+00:00
**Prereg sha256:** `2765e26912747c008bd9b97605bfc5b5b7676b2c40ffe9c26a5f9f6e73ec5732`
**Harvest manifest sha256:** `19a3d990ea4a7075248e9b4e06582a59e62bb159d7881ad41176e526d0e5d89c`
**n_trials:** 6
**Joint PBO:** 0.1034965034965035
**Horizon-mined flag:** False
**Delta-drift kill:** False

## Variants (residual signal)

| Variant | n_OOS | mean | WR | DSR | OOS-WR | MC pass | Verdict |
|---|---:|---:|---:|---:|---:|---|---|
| H1_long_on_pos | 67 | -0.0023685302162739738 | 0.4925373134328358 | 0.023222596906303492 | 0.4807692307692308 | False | NO_GO |
| H1_short_on_pos | 67 | -0.0016314697837260274 | 0.44776119402985076 | 0.03963658270617234 | 0.4423076923076923 | False | NO_GO |
| H2_long_on_pos | 69 | 0.009981821773280827 | 0.6086956521739131 | 0.5320427851727311 | 0.5769230769230769 | False | NO_GO |
| H2_short_on_pos | 69 | -0.013981821773280834 | 0.36231884057971014 | 0.0009078332188373537 | 0.38461538461538464 | False | NO_GO |
| H3_long_on_pos | 68 | 0.00956839837790352 | 0.5441176470588235 | 0.37559160266139385 | 0.5576923076923077 | False | NO_GO |
| H3_short_on_pos | 68 | -0.013568398377903522 | 0.45588235294117646 | 0.003155600385097484 | 0.4423076923076923 | False | NO_GO |

## Notes

- Raw Δ variants retained for delta-drift kill comparison only.
- Costs: Binance futures taker + 5 bps slip/side + realized funding.
- Re-harvest: `python scripts/harvest_cftc_tff_btc.py`
