# Strategy Specification Template (Investment Committee)

Educational template mapped to this bot’s prereg / StrategySpec fields.
Fill before any screen. Hash the frozen prereg **before** outcome computation.

| Committee field | Maps to (this repo) |
|-----------------|---------------------|
| STRATEGY NAME | prereg `id` / probe `agent_id` arm name |
| MARKET / ASSET | USDT-M perps; symbols list |
| TIMEFRAME | bar size (e.g. 4h) |
| DATA SOURCE | venue + local path under `data/` |
| ENTRY / EXIT / STOP | frozen rules in prereg md |
| POSITION-SIZING | ≤3% risk / trade; ≤12% exposure (binding rails) |
| MAX DAILY / WEEKLY LOSS | `DAILY_LOSS_BREAKER` (config.py); weekly = owner policy |
| MAX DRAWDOWN | screen MC maxDD p95 ≤ 0.25 |
| MAX OPEN POSITIONS | risk_manager / allocator caps |
| FEES / SLIPPAGE | `config.FEE` + sim slip (research screens charge all) |
| BACKTEST / OOS | prereg IS/OOS split + walk-forward if stated |
| PAPER PERIOD | shadow soak ≥30 RESOLVED / arm |
| KILL-SWITCH | incident latch / halt; daily-loss breaker |
| HUMAN APPROVAL | owner sign-off for promotion; CONTROLLED_LIVE latch |

---

```text
STRATEGY NAME:
MARKET: crypto_usdt_perp
ASSET:
TIMEFRAME:
DATA SOURCE:
ENTRY RULES:
EXIT RULES:
STOP-LOSS RULES:
POSITION-SIZING RULES:
MAXIMUM DAILY LOSS:
MAXIMUM WEEKLY LOSS:
MAXIMUM DRAWDOWN:
MAXIMUM OPEN POSITIONS:
TRADING HOURS:
FEES:
SLIPPAGE:
MINIMUM LIQUIDITY:
MARKET CONDITIONS TO AVOID:
BACKTEST PERIOD:
OUT-OF-SAMPLE PERIOD:
PAPER-TRADING PERIOD:
KILL-SWITCH CONDITIONS:
HUMAN APPROVAL REQUIRED: yes
LIVE TRADING: false
LEDGER NOVELTY: NEW | ADJACENT | REFUTED
PREREG PATH:
SHA256_MD:
EXPECTATION: GO | lean_NO_GO | NO_GO | INSUFFICIENT_DATA
```

After fill: write `_workspace/strategy_pipeline/{NN}_prereg_{id}.{md,json}`, hash md into JSON, then run `strategy-evidence-pipeline` / screen script. Do not implement live or MCP path changes from this template alone.
