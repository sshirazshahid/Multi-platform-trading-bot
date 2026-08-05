# Architecture (truth-pass, De-Emotion 2026-08-04)

## Overview

Primary entry: `main.py` → `BotEngine` (`core/bot_engine.py`).

```
TradingBot.bat / main.py
        │
        ▼
   BotEngine (watchdog + schedule)
        │
        ├── Portfolio cycle → MCPBrain algorithmic scorer (deterministic)
        ├── SIGNAL_SOURCE routing (default: tsmom — not an LLM)
        ├── OrderManager + RiskManager + PositionTracker
        ├── ShadowRunner + probe agents (log-only)
        └── Mission Control / warehouse / promotion funnel (read-mostly)
```

`multi_profile_main.py` is a separate DRY_RUN-required research entry — not the
24×7 Claude Portfolio path (that name is retired; cycle is `_portfolio_cycle`).

## Decision authority

- **Live opens/closes:** deterministic scoring + entry_policy + kill/live/risk gates.
- **LLM / committee:** offline research and adjudication only — never authorize entries.
- **Shadow probes:** warehouse evidence only; promotion = frozen gate + owner sign-off.

## Scoring gate (unchanged geometry)

- Standard path: score ≥ 66 and layers_ok ≥ 6 (or `MCP_ENTRY_MIN_SCORE` when set).
- Scalp path: score ≥ 65 and layers_ok ≥ 4.
- Sentiment / F&G / news veto / FOMO multipliers: **removed** (not renormalized).

## Dual promotion WR floors

Documented in `core/promotion_gate.py` without changing values:

| Gate | Constant | Floor |
|------|----------|-------|
| Shadow-vs-live `PromotionGate` | `wr_floor` | **0.65** |
| Model-artifact gate | `MIN_OOS_WR` | **0.55** |

Also: `MIN_DSR=0.10`, `MAX_PBO=0.5`, `MIN_AUC=0.60`.

## Related docs

- `docs/SELF_UPGRADE_LOOP.md` — harvest → screen → shadow → funnel → promote
- `OPERATING_PLAN.md` — operator checklist
- `.claude/skills/refuted-families-ledger/SKILL.md` — binding strategy ledger
