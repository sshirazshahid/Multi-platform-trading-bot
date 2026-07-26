# 22 — Screen: C3 quarter-hour opening imbalance (pilot)

**Verdict:** `NO_GO` (expectation NO_GO)
**Generated:** 2026-07-23T11:13:55.038732+00:00
**Prereg sha256:** `7b33c63914c44a749b2cb57d3bec0dd1a1c33e593577d9eea3dc57e0fb2f1787`
**Harvest manifest sha256:** `d3b5632d6bbc07fb5d371d9086e85d1de6cb434db1fa420914eedd54a8604186`
**Best aligned OOS mean (bps):** -18.549553398160757
**Expansion bar (bps):** 20.0
**Joint PBO:** 0.07847707847707848
**Delta-drift kill:** False

## Residual variants

| Variant | n_OOS | mean (bps) | WR | DSR | MC pass | Verdict |
|---|---:|---:|---:|---:|---|---|
| H12_aligned | 168 | -18.549553398160757 | 0.47023809523809523 | 0.0031192797242146036 | False | NO_GO |
| H12_contrarian | 168 | -21.45044660183925 | 0.4107142857142857 | 0.003017338350246311 | False | NO_GO |
| H4_aligned | 436 | -23.258402091368975 | 0.3830275229357798 | 1.2407076324219945e-07 | False | NO_GO |
| H4_contrarian | 436 | -16.741597908631025 | 0.3922018348623853 | 3.799398748180975e-05 | False | NO_GO |
| H8_aligned | 242 | -33.03063915463959 | 0.3925619834710744 | 1.037484113907403e-06 | False | NO_GO |
| H8_contrarian | 242 | -6.969360845360411 | 0.44214876033057854 | 0.02378407151559361 | False | NO_GO |

- Re-harvest: `python scripts/harvest_binance_aggtrades_qh.py`
