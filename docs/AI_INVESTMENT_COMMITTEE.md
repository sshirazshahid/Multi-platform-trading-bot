# AI Investment Committee (Integrated)

*Educational / research orchestration only. Not financial advice. Not a real hedge fund.
Does not promise profits. Claude Fable does not place broker/exchange orders from this committee.
Defaults: historical testing, after-cost screens, log-only shadow probes, PAPER simulation.
Live trading and withdrawals stay OFF. Human (owner) approval required for any promotion.*

Adapted from the public “AI Hedge Fund with Claude Fable” educational guide (seb.ai-style committee).
**Implementation choice (2026-07-25): Option A — integrate into this Trading_Bot**, not a greenfield tree.

## Why integrate (not rebuild)

This repository already has a stricter investment committee than a prompt pack:

| Guide stage | Existing substrate |
|-------------|-------------------|
| Data | `core/data_feeds/*`, exchange clients, `data/warehouse.sqlite`, read-only MCP |
| Research | `.claude/agents/strategy-scout.md`, dual-model scouts |
| Debate | Dual-model protocol [`_workspace/strategy_pipeline/19_dual_model_loop_protocol.md`](../_workspace/strategy_pipeline/19_dual_model_loop_protocol.md) + `honesty-auditor` |
| Strategy + backtest | `strategy-evidence-pipeline` → `research/screen_*.py` + frozen preregs |
| Risk | `core/risk_manager.py`, daily-loss breaker, 3%/12% rails, economic/band/smart-money gates |
| Paper | `OPERATING_MODE=PAPER`, `core/sim_execution.py`, BotEngine |
| Promotion / human | `core/promotion_gate.py`, funnel, owner-signed dossiers |

Fable operates at **design/session level** only — see [`docs/FABLE_OPERATING_DESIGN.md`](FABLE_OPERATING_DESIGN.md). Measured 2026-07-10: per-trade Claude advisory cost ~$118/day on a non-predictive paper lane. Do not put Fable back into the per-trade MCP cycle.

## Binding rules (never override)

1. **Ledger first** — `.claude/skills/refuted-families-ledger/SKILL.md`
2. **“Implement” = log-only** until frozen gate + owner sign-off
3. **Prereg hash BEFORE outcomes**
4. **Both-agree** for dual-model actions (`19_*` protocol)
5. **`SIGNAL_SOURCE=mcp` + PAPER** remain the runtime defaults unless the owner changes `.env` and restarts the supervisor
6. Committee FINAL VERDICT **never** submits orders

## Gap map (guide role → this repo)

```text
PRICE+NEWS+FEEDS → strategy-scout (+ analyst prompts)
                 → bull-researcher + bear-researcher
                 → debate-engine
                 → STRATEGY_SPEC_COMMITTEE / prereg JSON
                 → strategy-evidence-pipeline (screen + honesty-auditor)
                 → risk_manager / gates (binding) + risk_committee.yaml (docs mirror)
                 → PAPER / shadow probes
                 → final-verdict
                 → OWNER approve/reject
```

| Guide agent | Existing | Added (2026-07-25) |
|-------------|----------|-------------------|
| Research | strategy-scout | `prompts/committee/*` analyst packs |
| Bull / Bear | Ad-hoc in 18_* | `.claude/agents/bull-researcher.md`, `bear-researcher.md` |
| Debate | 19_* + honesty-auditor | `.claude/agents/debate-engine.md` |
| Strategy | StrategySpec / prereg | `docs/templates/STRATEGY_SPEC_COMMITTEE.md` |
| Backtest | `research/screen_*.py` | No new engine — hand off to pipeline |
| Risk | config.py + risk_manager | `config/risk_committee.yaml` (**docs only**) |
| Paper | BotEngine | Unchanged |
| Final verdict | Promotion dossiers | `.claude/agents/final-verdict.md` |
| Orchestrator | strategy-evidence-pipeline | `.claude/skills/investment-committee/SKILL.md` |

## Master workflow (guide §20 → our stages)

1. Collect / validate data → warehouse + feeds + MCP read tools  
2. Research brief → strategy-scout (+ optional analyst prompts)  
3. Bull + bear → parallel agents  
4. Debate → debate-engine (APPROVE / REVISE / REJECT; ledger check)  
5. Strategy rules → STRATEGY_SPEC_COMMITTEE + prereg JSON  
6. Backtest / screen → **must** enter `strategy-evidence-pipeline` (hash first)  
7. Risk → existing gates; summarize against `risk_committee.yaml` mirror  
8. Paper / shadow → BotEngine PAPER or log-only probe (GO only)  
9. Final verdict → final-verdict agent  
10. Human approval → owner only  

## Will not install (explicit)

Do **not** `pip install` or vendor into this repo as a second trading stack:

- Jesse (paper/live crypto framework — live surface risk)
- OpenBB (AGPL data platform — use as external reference only)
- TradingAgents (research framework — we already have agents)
- TensorTrade (RL sim)
- Qlib (equity ML research)

Cite them as educational references if useful; they are not decision authority here.

## How to run a committee session

Invoke the skill `investment-committee` (or ask the orchestrator to “run investment committee on [candidate]”).  
Outputs land under `_workspace/strategy_pipeline/{NN}_*.md`.  
Any APPROVE that implies a screen continues in `strategy-evidence-pipeline`.

## Related files

- Agents: `.claude/agents/{bull-researcher,bear-researcher,debate-engine,final-verdict}.md`
- Skill: `.claude/skills/investment-committee/SKILL.md`
- Template: `docs/templates/STRATEGY_SPEC_COMMITTEE.md`
- Risk mirror: `config/risk_committee.yaml`
- Loop link: `_workspace/strategy_pipeline/19_dual_model_loop_protocol.md` §S3
