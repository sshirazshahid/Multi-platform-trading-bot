## Part 1 — Fable candidate attack

**Verdict: MODIFY.**  
The `ADJACENT`—not `REFUTED`—classification is narrowly correct only for incremental aggressor-order-imbalance information; the unconditional clock effect remains covered by the refuted seasonality row, while price-volume-spanned content falls into refuted formulaic alphas.  
The proposed harvest means 108 completed monthly archives for six symbols through June 2026, plus July daily files because the July monthly dump cannot exist yet, producing roughly 327,000 heavily overlapping quarter-hour observations.  
Four-to-twelve-hour labels overlap 16–48 consecutive signals, and the paper supplies no bps estimate to clear $0.42–$0.50 per $420 notional at 10–12 bps—or $0.84 under the repository’s conservative 20-bps proxy.  
Given the registered `NO_GO`, run only a kill-first BTC/ETH three-month pilot, stream-discarding everything except the opening ten seconds, residualizing price-volume controls, and using day-clustered inference with explicit signal-netting and holding rules.  
Expand to the remaining symbols and months only if the untouched pilot shows incremental drift robustly above 20 bps; otherwise drop it and ledger the failure.

## Part 2 — Per-pair verdicts

These are Binance-dominated paper-band fitness verdicts, not edge findings. The profile cohort is empty for every pair, so historical warehouse statistics cannot be attributed to the current profile; moreover, every measured directional screen was negative after costs.

Any geometry retuning must be frozen and tested on new forward outcomes—not fitted to the existing cache.

- `FIT_BAND_PAPER` (5): ALGO, ARB, AVAX, ETH, LINK
- `FIT_WITH_GAPS` (9): AAVE, ATOM, BCH, MANA, NEAR, RENDER, SAND, SEI, TIA
- `DATA_STARVED` (15): 1INCH, COMP, CRV, DASH, ENA, GALA, GRT, HBAR, JUP, LTC, ONDO, SNX, TAO, XRP, ZEC
- `COST_UNFIT` (3): BNB, BTC, TRX
- `EXCLUDE` (12): ADA, APT, DOT, ETC, FET, FIL, INJ, OP, SOL, SUI, UNI, VET

Robust geometry-tuning outliers include SOL 12.7% (`n=158`) and ETC 83.9% (`n=112`), alongside ADA, APT, DOT, FIL, INJ, OP, SUI, UNI, and VET as detailed below.

```json
{
  "pairs": {
    "1INCH/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is 51.6d stale and funding archives are absent."
    },
    "AAVE/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band WR 69.5% (n=789), near-high; backfill Binance/Bybit/Bitget funding."
    },
    "ADA/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 47.0% (n=298), far below 63-67% at frac 0.35."
    },
    "ALGO/USDT:USDT": {
      "verdict": "FIT_BAND_PAPER",
      "note": "Fresh full stores; band WR 65.3% (n=141); move/cost 1.76."
    },
    "APT/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 50.3% (n=356), far below 63-67% at frac 0.35."
    },
    "ARB/USDT:USDT": {
      "verdict": "FIT_BAND_PAPER",
      "note": "Fresh full stores; band WR 66.3% (n=655); move/cost 2.16."
    },
    "ATOM/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band store has only n=90; backfill outcomes before trusting near-band WR 62.2%."
    },
    "AVAX/USDT:USDT": {
      "verdict": "FIT_BAND_PAPER",
      "note": "Fresh full stores; band WR 63.1% (n=479); move/cost 1.82."
    },
    "BCH/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band WR 63.8% (n=340); backfill all three venue funding archives."
    },
    "BNB/USDT:USDT": {
      "verdict": "COST_UNFIT",
      "note": "1h move/cost is 0.96; n=11 band outcomes cannot rescue structural cost dominance."
    },
    "BTC/USDT:USDT": {
      "verdict": "COST_UNFIT",
      "note": "1h move/cost is 0.985 below 1x, despite a deep n=1471 band cache."
    },
    "COMP/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is 51.6d stale."
    },
    "CRV/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is stale and all funding archives are absent."
    },
    "DASH/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is stale and all funding archives are absent."
    },
    "DOT/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 74.2% (n=407); funding stores are also absent."
    },
    "ENA/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is stale and all funding archives are absent."
    },
    "ETC/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 83.9% (n=112), far above 63-67%; funding is absent."
    },
    "ETH/USDT:USDT": {
      "verdict": "FIT_BAND_PAPER",
      "note": "Fresh full stores; band WR 66.0% (n=1583); move/cost 1.16."
    },
    "FET/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "2026-07-20 incident: runtime found no live Bybit USDT perp; static route is not executable."
    },
    "FIL/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 79.9% (n=234), far above 63-67%; funding is absent."
    },
    "GALA/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is stale and all funding archives are absent."
    },
    "GRT/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is 51.6d stale."
    },
    "HBAR/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; four warehouse trades cannot establish geometry."
    },
    "INJ/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 76.4% (n=742), well above 63-67% at frac 0.35."
    },
    "JUP/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; warehouse n=16 does not measure band geometry."
    },
    "LINK/USDT:USDT": {
      "verdict": "FIT_BAND_PAPER",
      "note": "Fresh full stores; band WR 64.6% (n=246); move/cost 1.44."
    },
    "LTC/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Only n=78 band outcomes; robust geometry store is missing (observed WR 51.3%)."
    },
    "MANA/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band WR 67.8% (n=541), near-high; backfill all three venue funding archives."
    },
    "NEAR/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band WR 65.0% (n=514); refresh 51.6d-stale OHLCV and all funding archives."
    },
    "ONDO/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is 51.6d stale."
    },
    "OP/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 53.2% (n=402); stale OHLCV compounds the mismatch."
    },
    "RENDER/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Only n=70; refresh stale OHLCV/funding and backfill band outcomes before admission."
    },
    "SAND/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band WR 68.5% (n=257), near-high; refresh stale OHLCV and all funding archives."
    },
    "SEI/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band WR 59.9% (n=394), near-low; refresh stale OHLCV before frozen geometry retune."
    },
    "SNX/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is stale and all funding archives are absent."
    },
    "SOL/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 12.7% (n=158), catastrophically below 63-67%."
    },
    "SUI/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 39.2% (n=120), far below 63-67% at frac 0.35."
    },
    "TAO/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is stale and all funding archives are absent."
    },
    "TIA/USDT:USDT": {
      "verdict": "FIT_WITH_GAPS",
      "note": "Band WR 69.7% (n=706), near-high; refresh 51.6d-stale OHLCV."
    },
    "TRX/USDT:USDT": {
      "verdict": "COST_UNFIT",
      "note": "1h move/cost is only 0.455; geometry is structurally dominated before edge questions."
    },
    "UNI/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 75.5% (n=789); OHLCV and funding stores are stale/missing."
    },
    "VET/USDT:USDT": {
      "verdict": "EXCLUDE",
      "note": "Geometry-tuning outlier: WR 40.5% (n=294); OHLCV and funding stores are stale/missing."
    },
    "XRP/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Band-outcome store has n=1; no honest geometry verdict is possible."
    },
    "ZEC/USDT:USDT": {
      "verdict": "DATA_STARVED",
      "note": "Missing band-outcome store; OHLCV is 51.6d stale."
    }
  },
  "fable_candidate_verdict": "MODIFY"
}
```
