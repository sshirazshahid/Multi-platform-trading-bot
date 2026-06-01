# Task: Commodity/Equity Perp — Format Fix + Analysis-Tracking (2026-06-02)

## Goal
Let the bot FETCH + WAREHOUSE the liquid commodity/equity perps (gold/silver/oil + liquid
stock perps) for research, while NEVER routing them to live/paper orders until screened.

## Constraints
- SAFETY-CRITICAL: analysis-only symbols must be hard-blocked at the live-entry gate.
- Minimal impact; mirror existing patterns; no new bugs.

## Plan (checkable)
- [ ] 1. Map: (a) how the active universe is built + the var holding it, (b) the exact
        live-entry gate in `_execute_open` + existing allow/deny lists, (c) the OHLCV
        analysis-fetch path that builds spot `{base}/USDT` (the XAU 404).
- [ ] 2. Config: add `ANALYSIS_ONLY_SYMBOLS` (liquid commodity/equity perps, full perp fmt).
- [ ] 3. Format fix: analysis-fetch resolves perp `{base}/USDT:USDT` when spot is absent.
- [ ] 4. Safety gate: `_execute_open` refuses any base in ANALYSIS_ONLY (hard block).
- [ ] 5. Tracking: analysis-only symbols are fetched + warehoused, excluded from entry universe.
- [ ] 6. Tests FIRST (TDD): (a) entry gate blocks analysis-only; (b) format resolver picks perp.
- [ ] 7. Verify: run tests; restart; confirm bot fetches XAU/USDT:USDT and opens ZERO of them.

## Review
(to be filled in after implementation)
