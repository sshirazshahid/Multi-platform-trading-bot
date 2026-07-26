# Smart-Money Hard Entry Gate (Approach 1) — Design

**Date:** 2026-07-24  
**Owner choices:** Directional entry (C) · Immediate PAPER wire (C) · Free Binance Web3 ranks (D) · Approach 1 (hard gate)

## Honesty

This is an **owner-directed, unscreened PAPER experiment**. Binance Web3 smart-money ranks are a live snapshot with **no historical PIT series**; config previously disabled B13 for that reason (2026-05-30). Escalating them to entry authority does **not** create the 59–67% profit band (AccBand dual-goal CONFIRMED_NO_GO). Expectation: bleed / NO_GO until evidence says otherwise.

## Scope

- PAPER + `MAX_FLOW_BAND` only (profile gate mirrors AccBand).
- Hard gate in `_execute_open`: **buy** requires `smart_money_inflow`; **sell** rejected while coin is in inflow top-N (do not short SM accumulation).
- Stale/missing feed: **fail-open** (allow) so API outages do not halt aggressive PAPER; log warning.
- Re-enable MCP B13 bonus when gate is on (aligned buys get +5).
- CONTROLLED_LIVE: gate forced off (cannot enable via env alone without mode PAPER).
- Out of scope: Arkham/Whale Alert, TF sweet spots, indicator strategies, any-pair any-TF alpha hunt (ledger STOP).

## Config

```text
SMART_MONEY_ENTRY_GATE_ENABLED=true   # honored only PAPER+MAX_FLOW_BAND
SMART_MONEY_ENTRY_FAIL_OPEN_STALE=true
DATA_FEEDS b13_smart_money_enabled=true when gate active (or env override)
```

## Verification

Boot banner: `SmartMoney: ON (hard entry gate)`. Restart supervisor after `.env` change.
