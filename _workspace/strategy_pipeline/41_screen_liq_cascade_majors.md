# 41 — Screen: Liquidation-cascade majors (BTC/ETH)
*Prereg sha256 `13ee84e40f2604b6…` | Arm: majors_btc_eth*

## Verdict: **NO_GO**

## Stage-0

| Symbol | Cell | Side | Triggers | OK |
|--------|------|------|----------|----|
| BTC | abs_th_1000000 | long_flush | 206 | Y |
| BTC | abs_th_1000000 | short_flush | 173 | Y |
| BTC | abs_th_5000000 | long_flush | 75 | Y |
| BTC | abs_th_5000000 | short_flush | 48 | Y |
| BTC | z_overlay_2.5_thmin_1e6 | long_flush | 48 | Y |
| BTC | z_overlay_2.5_thmin_1e6 | short_flush | 37 | Y |
| ETH | abs_th_1000000 | long_flush | 208 | Y |
| ETH | abs_th_1000000 | short_flush | 138 | Y |
| ETH | abs_th_5000000 | long_flush | 65 | Y |
| ETH | abs_th_5000000 | short_flush | 30 | Y |
| ETH | z_overlay_2.5_thmin_1e6 | long_flush | 40 | Y |
| ETH | z_overlay_2.5_thmin_1e6 | short_flush | 48 | Y |

## After-cost cells

| Sym | Signal | Side | H | Cost | n | Mean | OOS-WR | MC P>0 | maxDD p95 | Verdict | Fails |
|-----|--------|------|---|------|---|------|--------|--------|-----------|---------|-------|
| ETH | abs_th_5000000 | short_flush | 12 | 30bps | 21 | 46.7bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | z_overlay_2.5_thmin_1e6 | short_flush | 12 | 30bps | 26 | 37.1bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | abs_th_5000000 | short_flush | 12 | 60bps | 21 | 16.7bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| BTC | z_overlay_2.5_thmin_1e6 | short_flush | 12 | 30bps | 25 | 11.0bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | abs_th_1000000 | short_flush | 12 | 30bps | 58 | 9.4bps | 0.625 | 0.593 | 0.269 | NO_GO | mc_p=0.593<0.95,maxdd_p95=0.269>0.25,holm_p=1.000>0.05 |
| ETH | z_overlay_2.5_thmin_1e6 | short_flush | 12 | 60bps | 26 | 7.1bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| BTC | z_overlay_2.5_thmin_1e6 | short_flush | 4 | 30bps | 29 | -9.1bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| BTC | abs_th_1000000 | short_flush | 12 | 30bps | 62 | -12.7bps | 0.680 | 0.263 | 0.294 | NO_GO | mean<=0,mc_p=0.263<0.95,maxdd_p95=0.294>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_5000000 | short_flush | 4 | 30bps | 25 | -14.0bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| BTC | abs_th_5000000 | short_flush | 12 | 30bps | 26 | -18.0bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | z_overlay_2.5_thmin_1e6 | short_flush | 4 | 30bps | 35 | -18.1bps | 0.500 | 0.221 | 0.204 | NO_GO | mean<=0,oos_wr=0.500<0.55,mc_p=0.221<0.95,holm_p=1.000>0.05 |
| BTC | z_overlay_2.5_thmin_1e6 | short_flush | 12 | 60bps | 25 | -19.0bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | abs_th_1000000 | short_flush | 12 | 60bps | 58 | -20.6bps | 0.583 | 0.231 | 0.412 | NO_GO | mean<=0,mc_p=0.231<0.95,maxdd_p95=0.412>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_1000000 | short_flush | 4 | 30bps | 101 | -21.8bps | 0.439 | 0.040 | 0.414 | NO_GO | mean<=0,oos_wr=0.439<0.55,mc_p=0.040<0.95,maxdd_p95=0.414>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_5000000 | short_flush | 4 | 30bps | 33 | -22.3bps | 0.643 | 0.077 | 0.157 | NO_GO | mean<=0,mc_p=0.077<0.95,holm_p=1.000>0.05 |
| BTC | abs_th_5000000 | long_flush | 4 | 30bps | 48 | -24.2bps | 0.450 | 0.130 | 0.281 | NO_GO | mean<=0,oos_wr=0.450<0.55,mc_p=0.130<0.95,maxdd_p95=0.281>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_1000000 | short_flush | 4 | 30bps | 87 | -24.8bps | 0.457 | 0.030 | 0.394 | NO_GO | mean<=0,oos_wr=0.457<0.55,mc_p=0.030<0.95,maxdd_p95=0.394>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_5000000 | long_flush | 4 | 30bps | 46 | -32.5bps | 0.368 | 0.046 | 0.283 | NO_GO | mean<=0,oos_wr=0.368<0.55,mc_p=0.046<0.95,maxdd_p95=0.283>0.25,holm_p=1.000>0.05 |
| BTC | z_overlay_2.5_thmin_1e6 | long_flush | 4 | 30bps | 35 | -32.7bps | 0.286 | 0.000 | 0.156 | NO_GO | mean<=0,oos_wr=0.286<0.55,mc_p=0.000<0.95,holm_p=1.000>0.05 |
| BTC | z_overlay_2.5_thmin_1e6 | short_flush | 4 | 60bps | 29 | -39.1bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| BTC | abs_th_1000000 | long_flush | 4 | 30bps | 116 | -40.0bps | 0.255 | 0.000 | 0.627 | NO_GO | mean<=0,oos_wr=0.255<0.55,mc_p=0.000<0.95,maxdd_p95=0.627>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_1000000 | short_flush | 12 | 60bps | 62 | -42.7bps | 0.400 | 0.029 | 0.467 | NO_GO | mean<=0,oos_wr=0.400<0.55,mc_p=0.029<0.95,maxdd_p95=0.467>0.25,holm_p=1.000>0.05 |
| ETH | z_overlay_2.5_thmin_1e6 | long_flush | 4 | 30bps | 32 | -43.4bps | 0.462 | 0.000 | 0.207 | NO_GO | mean<=0,oos_wr=0.462<0.55,mc_p=0.000<0.95,holm_p=1.000>0.05 |
| ETH | abs_th_5000000 | short_flush | 4 | 60bps | 25 | -44.0bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | abs_th_1000000 | long_flush | 4 | 30bps | 112 | -44.9bps | 0.200 | 0.000 | 0.705 | NO_GO | mean<=0,oos_wr=0.200<0.55,mc_p=0.000<0.95,maxdd_p95=0.705>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_5000000 | short_flush | 12 | 60bps | 26 | -48.0bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | z_overlay_2.5_thmin_1e6 | short_flush | 4 | 60bps | 35 | -48.1bps | 0.429 | 0.032 | 0.300 | NO_GO | mean<=0,oos_wr=0.429<0.55,mc_p=0.032<0.95,maxdd_p95=0.300>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_1000000 | short_flush | 4 | 60bps | 101 | -51.8bps | 0.317 | 0.000 | 0.709 | NO_GO | mean<=0,oos_wr=0.317<0.55,mc_p=0.000<0.95,maxdd_p95=0.709>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_5000000 | short_flush | 4 | 60bps | 33 | -52.3bps | 0.357 | 0.000 | 0.247 | NO_GO | mean<=0,oos_wr=0.357<0.55,mc_p=0.000<0.95,holm_p=1.000>0.05 |
| BTC | abs_th_5000000 | long_flush | 4 | 60bps | 48 | -54.2bps | 0.350 | 0.005 | 0.415 | NO_GO | mean<=0,oos_wr=0.350<0.55,mc_p=0.005<0.95,maxdd_p95=0.415>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_1000000 | short_flush | 4 | 60bps | 87 | -54.8bps | 0.371 | 0.000 | 0.643 | NO_GO | mean<=0,oos_wr=0.371<0.55,mc_p=0.000<0.95,maxdd_p95=0.643>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_5000000 | long_flush | 12 | 30bps | 32 | -56.6bps | 0.308 | 0.007 | 0.338 | NO_GO | mean<=0,oos_wr=0.308<0.55,mc_p=0.007<0.95,maxdd_p95=0.338>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_1000000 | long_flush | 12 | 30bps | 64 | -58.4bps | 0.269 | 0.005 | 0.635 | NO_GO | mean<=0,oos_wr=0.269<0.55,mc_p=0.005<0.95,maxdd_p95=0.635>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_1000000 | long_flush | 12 | 30bps | 64 | -59.7bps | 0.385 | 0.000 | 0.578 | NO_GO | mean<=0,oos_wr=0.385<0.55,mc_p=0.000<0.95,maxdd_p95=0.578>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_5000000 | long_flush | 4 | 60bps | 46 | -62.5bps | 0.316 | 0.001 | 0.412 | NO_GO | mean<=0,oos_wr=0.316<0.55,mc_p=0.001<0.95,maxdd_p95=0.412>0.25,holm_p=1.000>0.05 |
| BTC | z_overlay_2.5_thmin_1e6 | long_flush | 4 | 60bps | 35 | -62.7bps | 0.214 | 0.000 | 0.255 | NO_GO | mean<=0,oos_wr=0.214<0.55,mc_p=0.000<0.95,maxdd_p95=0.255>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_1000000 | long_flush | 4 | 60bps | 116 | -70.0bps | 0.213 | 0.000 | 0.968 | NO_GO | mean<=0,oos_wr=0.213<0.55,mc_p=0.000<0.95,maxdd_p95=0.968>0.25,holm_p=1.000>0.05 |
| ETH | z_overlay_2.5_thmin_1e6 | long_flush | 4 | 60bps | 32 | -73.4bps | 0.385 | 0.000 | 0.295 | NO_GO | mean<=0,oos_wr=0.385<0.55,mc_p=0.000<0.95,maxdd_p95=0.295>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_1000000 | long_flush | 4 | 60bps | 112 | -74.9bps | 0.156 | 0.000 | 1.029 | NO_GO | mean<=0,oos_wr=0.156<0.55,mc_p=0.000<0.95,maxdd_p95=1.029>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_5000000 | long_flush | 12 | 30bps | 36 | -79.3bps | 0.467 | 0.000 | 0.397 | NO_GO | mean<=0,oos_wr=0.467<0.55,mc_p=0.000<0.95,maxdd_p95=0.397>0.25,holm_p=1.000>0.05 |
| BTC | z_overlay_2.5_thmin_1e6 | long_flush | 12 | 30bps | 27 | -84.4bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| BTC | abs_th_5000000 | long_flush | 12 | 60bps | 32 | -86.6bps | 0.308 | 0.000 | 0.427 | NO_GO | mean<=0,oos_wr=0.308<0.55,mc_p=0.000<0.95,maxdd_p95=0.427>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_1000000 | long_flush | 12 | 60bps | 64 | -88.4bps | 0.231 | 0.000 | 0.815 | NO_GO | mean<=0,oos_wr=0.231<0.55,mc_p=0.000<0.95,maxdd_p95=0.815>0.25,holm_p=1.000>0.05 |
| BTC | abs_th_1000000 | long_flush | 12 | 60bps | 64 | -89.7bps | 0.269 | 0.000 | 0.762 | NO_GO | mean<=0,oos_wr=0.269<0.55,mc_p=0.000<0.95,maxdd_p95=0.762>0.25,holm_p=1.000>0.05 |
| ETH | abs_th_5000000 | long_flush | 12 | 60bps | 36 | -109.3bps | 0.333 | 0.000 | 0.501 | NO_GO | mean<=0,oos_wr=0.333<0.55,mc_p=0.000<0.95,maxdd_p95=0.501>0.25,holm_p=1.000>0.05 |
| BTC | z_overlay_2.5_thmin_1e6 | long_flush | 12 | 60bps | 27 | -114.4bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | z_overlay_2.5_thmin_1e6 | long_flush | 12 | 30bps | 26 | -115.4bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |
| ETH | z_overlay_2.5_thmin_1e6 | long_flush | 12 | 60bps | 26 | -145.4bps | nan | nan | nan | INSUFFICIENT_DATA | n<30 |

## Honest read
- GO requires ALL frozen gates jointly (n, mean>0, OOS-WR≥0.55, MC, maxDD, Holm).
- Prior ~25%. Undercount on Binance forceOrder is binding measurement error.
- No probe / no MCP unless owner-signed CONFIRMED_GO after audit.
