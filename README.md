# Trading Bot

Deterministic multi-venue crypto paper-trading bot (Binance / Bybit / Bitget) with
a pre-registered strategy-evidence pipeline. Live portfolio decisions are
**algorithmic only** — LLM / sentiment / fear-greed were removed from the trade
path (De-Emotion overhaul, 2026-08-04).

## Quick start

```bash
cp .env.example .env   # fill exchange keys; OPERATING_MODE=PAPER
python -m venv venv && venv\Scripts\activate   # Windows
pip install -r requirements.txt
python main.py                                 # or TradingBot.bat
python scripts/run_mission_control.py          # http://127.0.0.1:8787/
```

`DRY_RUN` is **derived** from `OPERATING_MODE` in `config.py` (not an independent
env switch). CONTROLLED_LIVE requires the signed checklist + double latch.

## Decision path

| Layer | Role |
|-------|------|
| `core/mcp_brain.py` | Deterministic multi-factor scorer (portfolio + position monitor) |
| `SIGNAL_SOURCE` | Routing (default `tsmom`) — never flip to LLM |
| `core/entry_policy.py` / `kill_switch` / `live_gate` / `risk_manager` | Hard gates — do not loosen |
| Shadow probes | Log-only warehouse evidence toward promotion |
| LLM / `.claude/agents` committee | Offline research only |

Entry geometry: score ≥ 66 & layers ≥ 6 (scalp 65/4); `MCP_ENTRY_MIN_SCORE` may override floors.

## Strategy evidence

See `docs/SELF_UPGRADE_LOOP.md` and `.claude/skills/`:

- `refuted-families-ledger` (consult first)
- `strategy-evidence-pipeline` / `after-cost-screening` / `investment-committee` / `shadow-probe-integration`
- `strategy-research-wiring`

Root `skills/` holds seven trading ops skills (exchange connectivity, risk, monitoring, …).

## Tests

```bash
python -m pytest tests/ -q
```

## Docs

- `OPERATING_PLAN.md` — what to run day-to-day
- `docs/architecture.md` — component map + dual WR floors (0.65 vs 0.55)
- `docs/SELF_UPGRADE_LOOP.md` — harvest → screen → shadow → funnel → promote
- `docs/CONTROLLED_LIVE_CHECKLIST.md` — live sign-off (owner only)
