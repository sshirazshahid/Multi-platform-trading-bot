# Self-upgrade loop

How this bot discovers, tests, and (eventually) promotes strategies without
putting an LLM on the live trade path.

```
harvest  →  screen  →  shadow  →  funnel  →  promote
  ↑           ↑
  └─ LLM / investment committee only participate LEFT of the screen
```

## Stages

1. **Harvest** — `scripts/harvest_*.py`, `scripts/backfill_*.py`, unlock calendar,
   funding/OHLCV backfills. Outputs land under `data/` (often gitignored).
2. **Screen** — pre-register (`_workspace/strategy_pipeline/<N>_prereg_*`), run
   after-cost screen per `.claude/skills/after-cost-screening`, adversarial audit.
   Verdicts: GO / NO_GO / INSUFFICIENT_DATA. Commit artifacts the same day as any
   ledger row.
3. **Shadow** — CONFIRMED_GO only → log-only probe agent via
   `shadow-probe-integration`. Zero capital risk; warehouse rows only.
4. **Funnel** — `scripts/promotion_funnel.py` → `data/promotion_funnel.json`
   (+ optional owner dossiers under `reports/promotion_dossiers/`, gitignored).
5. **Promote** — frozen `core/promotion_gate.py` gates **and** owner sign-off
   **and** `live_gate` / CONTROLLED_LIVE checklist. Never promote on narrative.

## Schedulers

| Job | Where |
|-----|--------|
| Portfolio / monitor / learning | in-process `schedule` inside `BotEngine` |
| Promotion funnel refresh | in-process every 6h (PAPER-safe, fail-soft) + Windows Task Scheduler `TradingBot_PromotionFunnel` on the owner box |
| Unlock calendar / harvest | Windows schtasks / cron equivalents documented in `scripts/install_*.ps1` |

## Smoke

```bash
python -c "import scripts.promotion_funnel as pf; print('ok', hasattr(pf, 'main'))"
# Without warehouse: main() should exit non-zero or write an empty/idle snapshot — never crash the engine.
```

## Non-negotiables

- Do not loosen promotion / entry_policy / kill / live / risk gates to force flow.
- Consult `refuted-families-ledger` before proposing a family.
- CONTROLLED_LIVE stays off until the full gate stack clears.
