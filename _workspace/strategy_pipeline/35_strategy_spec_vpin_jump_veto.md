# 35 — Strategy Spec (committee fill) — VPIN jump-risk veto

Filled from `docs/templates/STRATEGY_SPEC_COMMITTEE.md` after debate **REVISE**.
Prereg already frozen: `27_prereg_vpin_jump_veto.*` — do not re-freeze θ/gates.

```text
STRATEGY NAME: vpin_jump_veto_v1
MARKET: crypto_usdt_perp
ASSET: BTCUSDT, ETHUSDT
TIMEFRAME: AccBand signal bars + VPIN volume-clock (N=50 buckets)
DATA SOURCE: Binance USDT-M aggTrades → data/aggtrades_vpin/ (harvest pending Stage-0)
ENTRY RULES: none (veto only — skip AccBand OPEN when VPIN_t > θ)
EXIT RULES: n/a (no new entries)
STOP-LOSS RULES: n/a (underlying AccBand stops unchanged)
POSITION-SIZING RULES: n/a for veto; AccBand sizing unchanged
MAXIMUM DAILY LOSS: DAILY_LOSS_BREAKER (config)
MAXIMUM DRAWDOWN: MC maxDD p95 ≤ 0.25 on veto arm (prereg)
FEES / SLIPPAGE: identical AccBand / config.FEE + sim
BACKTEST PERIOD: per prereg OOS after Stage-0 feasibility
OUT-OF-SAMPLE PERIOD: prereg half-split / frozen gates
PAPER-TRADING PERIOD: only if screen GO → flag-gated veto soak (owner)
KILL-SWITCH CONDITIONS: incident latch; halt
HUMAN APPROVAL REQUIRED: yes
LIVE TRADING: false
LEDGER NOVELTY: ADJACENT
PREREG PATH: _workspace/strategy_pipeline/27_prereg_vpin_jump_veto.md
SHA256_MD: (see 27_prereg_vpin_jump_veto.json)
EXPECTATION: NO_GO
COMMITTEE: REVISE — Stage-0 feasibility before full screen
```
