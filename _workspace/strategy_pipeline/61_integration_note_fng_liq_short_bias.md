# 61 — Integration note: F&G + long-liq SHORT-bias (ACCRUE ONLY)

**Date:** 2026-08-05  
**Verdict:** ACCRUE_ONLY — no probe, no MCP SHORT path, no live authorization  
**Prereg:** `61_prereg_fng_liq_short_bias.md`  
**sha256:** `6fe2cca96f791b21a6363c44891b47041ff23e62a094fa1f75a19fc125b4bdbc` (frozen before outcomes)

## What shipped

| Artifact | Role |
|----------|------|
| `core/regime_short_bias.py` | Pure evaluate + 24h ALL `long_usd` sum |
| `scripts/record_regime_short_bias.py` | Fetch F&G, refresh `news_cache`, write `data/regime_short_bias_latest.json` + append `data/regime_short_bias_log.jsonl` |
| `tests/test_regime_short_bias.py` | Fire rules + window math |
| `tests/test_decision_path_purity.py` | Forbids `core.regime_short_bias` on decision-path modules |

## Binding honesty

- `live_short_authorized` is always `false`.
- De-Emotion intact: decision path still has no `alternative.me` / F&G / news_scanner.
- Vendor multi-venue liquidation headlines (e.g. ~$208M) are **not** this series. Recorder uses Binance `forceOrder` history via `liquidations_history.jsonl` (undercounts vs Coinglass-class prints).

## First live snapshot (2026-08-05T15:13:03Z)

- F&G = **27** (Fear) → `fng_ok`
- Binance ALL long_usd 24h ≈ **$49.6M** / short_usd 24h ≈ **$241M** / 24 hours present
- Cells: Θ25M **fired**; Θ50M / 100M / 200M **not** fired
- Narrative: `SHORT_BIAS_ENV` (measurement label only)

## Ops

```text
python scripts/record_regime_short_bias.py
# hourly schtask recommended (intel-only):
# TradingBot-RegimeShortBias → python scripts/record_regime_short_bias.py
```

## Next (not this ship)

1. Accrue ≥30 independent fired UTC days in `regime_short_bias_log.jsonl`.
2. Stage-0 fire-rate check under a **new** trade-rule prereg (entry/exit/costs) — never reuse 61 as a trade authority.
3. Only then: after-cost screen → audit → log-only probe if CONFIRMED_GO.
