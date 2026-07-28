# 39 — Screen run: clamp-print zero-information (prereg sha256 dda32c8cf71d)
Generated 2026-07-28T15:01:16.127962+00:00 | files 510 (skipped 0) | rows 1,292,601 | runtime 72.6s
alpha/cell = 0.004167 (0.05/12) | Stage-0 floor = 30 informative strata

| venue | regime | strata | clamp_n | ctrl_n | chi2 | p | OR_MH | clampWR | ctrlWR | cell verdict |
|---|---|---|---|---|---|---|---|---|---|---|
| binance | 1h | 473 | 1024 | 578 | 9.14 | 0.00251 | 1.55 | 0.879 | 0.763 | FALSIFIED |
| binance | 2h | 0 | 0 | 0 | - | - | - | - | - | INSUFFICIENT_DATA |
| binance | 4h | 5673 | 256419 | 63288 | 1.17e+04 | 0 | 3.93 | 0.925 | 0.79 | FALSIFIED |
| binance | 8h | 5517 | 77900 | 55074 | 3.14e+03 | 0 | 3 | 0.933 | 0.811 | FALSIFIED |
| bybit | 1h | 2045 | 20522 | 2456 | 42.7 | 6.52e-11 | 1.63 | 0.931 | 0.896 | FALSIFIED |
| bybit | 2h | 601 | 2070 | 645 | 327 | 5.3e-73 | 7.34 | 0.911 | 0.581 | FALSIFIED |
| bybit | 4h | 3267 | 143917 | 15744 | 4.99e+03 | 0 | 3.94 | 0.904 | 0.695 | FALSIFIED |
| bybit | 8h | 5650 | 165539 | 73303 | 8.21e+03 | 0 | 3.91 | 0.94 | 0.819 | FALSIFIED |
| bitget | 1h | 0 | 0 | 0 | - | - | - | - | - | INSUFFICIENT_DATA |
| bitget | 2h | 0 | 0 | 0 | - | - | - | - | - | INSUFFICIENT_DATA |
| bitget | 4h | 97 | 7345 | 730 | 490 | 1.36e-108 | 7.83 | 0.959 | 0.748 | FALSIFIED |
| bitget | 8h | 119 | 1902 | 935 | 167 | 3.96e-38 | 4.23 | 0.918 | 0.743 | FALSIFIED |

**Screen verdict: PENDING_DUAL_MODEL** — cell computations above are the
screen output; the pipeline verdict needs both-agree (Fable + Codex).
No ledger row is written by this run.
