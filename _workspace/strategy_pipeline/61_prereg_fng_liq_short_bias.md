# 61 — Pre-registration: F&G + 24h liquidation SHORT-bias (measurement only)

**Status:** FROZEN before any after-cost outcome / expectancy claim  
**Date:** 2026-08-05  
**Owner directive:** Use fear/liq news for SHORT context — implemented as **log-only accrual**, not live shorts  
**Expectation:** ACCRUE_ONLY until ≥30 independent fired days; prior on narrative SHORT edge ≈ low  
**Binding:** De-Emotion intact — no import from `bot_engine` / `mcp_brain` / `order_manager`  
**sha256_md:** `6fe2cca96f791b21a6363c44891b47041ff23e62a094fa1f75a19fc125b4bdbc`

## Hypothesis (null)

Days with Fear & Greed ≤ 30 **and** elevated Binance forceOrder long-liquidation USD over the prior 24 completed UTC hours do **not** produce positive after-cost expectancy for a pre-registered SHORT rule on majors (to be specified only when Stage-0 fires ≥30). This prereg freezes the **signal definition** for forward logging only.

## Signal (frozen — log-only)

| Field | Value |
|-------|-------|
| F&G source | `https://api.alternative.me/fng/?limit=1` → cached in `data/news_cache.json` `fear_greed` |
| F&G fire | `value ≤ 30` (inclusive) |
| Liq source | `data/liquidations_history.jsonl` symbol=`ALL` rows |
| Liq window | Sum `long_usd` over the last **24 completed UTC hours** (exclude in-progress hour) |
| Liq undercount | Accepted (Binance forceOrder ≠ vendor “$208M” prints); do not post-hoc scale to CoinGlass |
| Liq threshold grid Θ | `{25e6, 50e6, 100e6, 200e6}` USD long-liq in 24h |
| Fire | F&G fire **AND** `long_usd_24h ≥ Θ` for each cell logged separately |
| Direction claim | Narrative SHORT-bias environment only — **no entry/exit/sizing in this prereg** |

## Explicit non-goals (this iteration)

- No shadow probe agent  
- No MCP / entry_policy / APPROVED_PAPER change  
- No SHORT orders from this signal  
- No after-cost returns computed until a **new** Stage-0→screen under a hash that includes trade rules  

## Artifacts

| Path | Role |
|------|------|
| `data/regime_short_bias_latest.json` | Latest evaluation snapshot (MC / ops read) |
| `data/regime_short_bias_log.jsonl` | Append-only firings + non-firings (hourly/intel cadence) |
| `scripts/record_regime_short_bias.py` | Recorder (network + disk only) |
| `core/regime_short_bias.py` | Pure evaluation helpers (no network required in tests) |

## Next action when accrued

Stage-0: count distinct UTC days with ≥1 cell fire. If every Θ cell has &lt;30 days → INSUFFICIENT_DATA. Only then write a **trade-rule** prereg (majors SHORT, costs, horizons) as a separate hash.
