# 18 — Per-Pair Evidence Dossier (2026-07-22)

MEASUREMENT of existing local data for the Phase-3 dual-model per-pair
verdicts. No backtests, no strategy claims. Every number is reproducible
via `venv/Scripts/python.exe research/pair_evidence_dossier.py`.

## Schema (per pair, in 18_pair_dossier.json)

```
warehouse.all_time / .profile_cohort / .recent_14d:
  n, wr (share realized_pnl>0), net_pnl_usd, fees_usd — CLOSED futures
  trades, strategy_family NOT IN ['reconcile', 'reconciled_exchange', 'manual']
  profile_cohort = ts_entry >= heartbeat paper_profile_started_at
band_cache: n, wr, mean_r, mean_net_pnl_usd at frac 0.35 (13_band_outcome_cache.json, 2026-07-12 screen)
coverage.ohlcv_1h: parquet span days/rows; coverage.funding.<venue>: CSV span days
routing: spec-approved venues, quarantine (.env), ANALYSIS_ONLY/tradfi sets
probes: bundle_mr_zfade_rsi2 (spec x bybit, static mirror), tsmom, breakout_60d
cost_proxy: median 1h |close-open|/close bps over last 720 bars vs roundtrip 20.0 bps
```

Heartbeat profile at generation: **AGGRESSIVE_RESEARCH**, epoch 2026-07-21T20:24:08Z.

## Table

| pair | AT n | AT WR | AT netPnL$ | band n | band WR | band meanR | 1h days | fund b/y/g d | probes | move/cost | flags |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 1INCH | 0 | — | 0.0 | — | — | — | 365 | —/—/— | MR | 1.54 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget; band_cache_no_outcomes |
| AAVE | 90 | 0.31 | -21.2 | 789 | 0.695 | -0.183 | 1149 | —/—/— | MR | 2.41 | funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| ADA | 151 | 0.28 | -47.9 | 298 | 0.470 | -0.528 | 1149 | 2022/1946/41 | MR+BRK | 1.85 | — |
| ALGO | 72 | 0.39 | -14.3 | 141 | 0.652 | -0.258 | 1149 | 2022/1757/41 | MR | 1.76 | — |
| APT | 83 | 0.33 | -38.4 | 356 | 0.503 | -0.476 | 1149 | 1197/1197/33 | MR | 2.35 | — |
| ARB | 114 | 0.28 | -29.2 | 655 | 0.663 | -0.221 | 1149 | 1197/1197/33 | MR | 2.16 | — |
| ATOM | 88 | 0.42 | -18.8 | 90 | 0.622 | -0.292 | 1149 | 2022/1739/41 | MR | 1.56 | — |
| AVAX | 148 | 0.31 | -47.7 | 479 | 0.630 | -0.271 | 1149 | 1197/1197/33 | MR+BRK | 1.82 | — |
| BCH | 28 | 0.42 | -6.9 | 340 | 0.638 | -0.272 | 1149 | —/—/— | MR | 1.76 | funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| BNB | 157 | 0.32 | -36.5 | 11 | 0.000 | -1.575 | 1149 | 2022/1843/41 | MR+BRK | 0.96 | — |
| BTC | 149 | 0.34 | -29.1 | 1471 | 0.612 | -0.294 | 1149 | 2022/2022/41 | MR+TSM+BRK | 0.98 | — |
| COMP | 0 | — | 0.0 | — | — | — | 365 | 1197/1197/33 | MR | 1.91 | ohlcv_1h_stale:51.6d; band_cache_no_outcomes |
| CRV | 0 | — | 0.0 | — | — | — | 365 | —/—/— | MR | 2.03 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget; band_cache_no_outcomes |
| DASH | 0 | — | 0.0 | — | — | — | 365 | —/—/— | MR | 3.17 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget; band_cache_no_outcomes |
| DOT | 130 | 0.32 | -36.1 | 407 | 0.742 | -0.143 | 1149 | —/—/— | MR+BRK | 1.77 | funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| ENA | 0 | — | 0.0 | — | — | — | 365 | —/—/— | MR | 2.46 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget; band_cache_no_outcomes |
| ETC | 4 | 0.25 | -13.3 | 112 | 0.839 | 0.112 | 1149 | —/—/— | MR | 1.42 | funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| ETH | 69 | 0.28 | -29.8 | 1583 | 0.660 | -0.235 | 1149 | 2022/2022/41 | MR+TSM+BRK | 1.16 | — |
| FET | 16 | 0.40 | -2.7 | 237 | 0.616 | -0.302 | 1149 | —/—/— | — | 2.04 | funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| FIL | 13 | 0.25 | -5.5 | 234 | 0.799 | -0.033 | 1149 | —/—/— | MR | 1.92 | funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| GALA | 0 | — | 0.0 | — | — | — | 365 | —/—/— | MR | 2.43 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget; band_cache_no_outcomes |
| GRT | 5 | 0.33 | -0.2 | — | — | — | 1101 | 2022/1683/41 | MR | 2.23 | ohlcv_1h_stale:51.6d; band_cache_no_outcomes |
| HBAR | 4 | 0.00 | -3.8 | — | — | — | 413 | 1197/1197/33 | MR | 1.55 | band_cache_no_outcomes |
| INJ | 25 | 0.48 | -5.9 | 742 | 0.764 | -0.106 | 1149 | 1197/1197/33 | MR | 2.52 | — |
| JUP | 16 | 0.38 | -1.5 | — | — | — | 413 | 891/891/16 | MR | 2.84 | band_cache_no_outcomes |
| LINK | 249 | 0.36 | -59.8 | 246 | 0.646 | -0.253 | 1149 | 2022/2022/41 | MR+BRK | 1.44 | — |
| LTC | 7 | 0.29 | -2.3 | 78 | 0.513 | -0.487 | 1149 | 2022/2022/41 | MR | 1.35 | — |
| MANA | 4 | 0.25 | 0.3 | 541 | 0.678 | -0.207 | 1149 | —/—/— | MR | 2.11 | funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| NEAR | 0 | — | 0.0 | 514 | 0.650 | -0.259 | 1101 | —/—/— | MR | 3.10 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| ONDO | 0 | — | 0.0 | — | — | — | 365 | 902/900/16 | MR | 3.80 | ohlcv_1h_stale:51.6d; band_cache_no_outcomes |
| OP | 1 | 0.00 | -3.1 | 402 | 0.532 | -0.421 | 1101 | 1197/1197/33 | MR | 2.06 | ohlcv_1h_stale:51.6d |
| RENDER | 0 | — | 0.0 | 70 | 0.686 | -0.199 | 365 | —/—/— | MR | 2.54 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| SAND | 0 | — | 0.0 | 257 | 0.685 | -0.204 | 1101 | —/—/— | MR | 1.77 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| SEI | 2 | 0.00 | -0.9 | 394 | 0.599 | -0.336 | 365 | 1059/1060/33 | MR | 2.27 | ohlcv_1h_stale:51.6d |
| SNX | 0 | — | 0.0 | — | — | — | 365 | —/—/— | MR | 1.63 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget; band_cache_no_outcomes |
| SOL | 255 | 0.37 | -81.1 | 158 | 0.127 | -1.117 | 1149 | 2022/1843/41 | MR+TSM+BRK | 1.47 | — |
| SUI | 7 | 0.33 | -1.7 | 120 | 0.392 | -0.650 | 1149 | 1170/1170/41 | MR | 1.84 | — |
| TAO | 0 | — | 0.0 | — | — | — | 365 | —/—/— | MR | 2.38 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget; band_cache_no_outcomes |
| TIA | 0 | — | 0.0 | 706 | 0.697 | -0.179 | 365 | 983/983/16 | MR | 2.94 | ohlcv_1h_stale:51.6d |
| TRX | 4 | 0.00 | -0.3 | — | — | — | 1149 | 2022/1780/41 | MR | 0.46 | band_cache_no_outcomes |
| UNI | 1 | 0.00 | -7.0 | 789 | 0.755 | -0.102 | 365 | —/—/— | MR | 1.79 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| VET | 1 | 1.00 | 0.0 | 294 | 0.405 | -0.598 | 1101 | —/—/— | MR | 1.61 | ohlcv_1h_stale:51.6d; funding_missing:binance; funding_missing:bybit; funding_missing:bitget |
| XRP | 134 | 0.37 | -36.2 | 1 | 0.000 | -1.295 | 1149 | 2022/1890/41 | MR+BRK | 1.23 | — |
| ZEC | 0 | — | 0.0 | — | — | — | 365 | 2022/1695/29 | MR | 3.40 | ohlcv_1h_stale:51.6d; band_cache_no_outcomes |

## Caveats (binding)

- **Profile-cohort is empty for every pair.** The heartbeat profile is `AGGRESSIVE_RESEARCH` with epoch 2026-07-21T20:24:08Z (reset at the last bot boot). Zero trades have ts_entry after that epoch, so no MAX_FLOW_BAND cohort is derivable from the current heartbeat field. `recent_14d` is provided as the objective recent-activity window instead.
- Band-cache outcomes were resolved at frac 0.35 by the 2026-07-12 screen and are 14,551/14,555 binance — treat as binance-only. Band WR is GEOMETRY, not edge; every screen-13 bucket was after-cost negative.
- Cost proxy uses binance futures taker (0.0005/side) + sim slippage (0.0005/side) => 20.0 bps roundtrip; a median 1h bar move below ~1x this roundtrip means 1h-scale geometry is structurally cost-dominated on that pair.
- Probe membership is a STATIC mirror of the agents' frozen constants + spec routes; the FET bybit runtime skip is sourced from the 2026-07-20 boot log, not re-verified against exchanges (this script never hits exchange APIs).
- Warehouse WR/netPnL aggregates directional CLOSED futures trades across ALL historical engines/profiles (claude, claude_portfolio, algo, algo_det, systematic_v3_1, ...) — an honest all-history measure, NOT a current-engine performance claim.
- listing/unlock probes are event-driven (n/a per pair).

Generated 2026-07-22T11:27:26Z by research/pair_evidence_dossier.py; universe data\strategy_specs\MCP_DIRECTIONAL_PAPER.json (n=44).
