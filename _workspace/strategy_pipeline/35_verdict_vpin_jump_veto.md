# 35 — Final Verdict: VPIN jump-risk veto (Investment Committee)

**Date:** 2026-07-25  
**Candidate:** `vpin_jump_veto_v1`  
**Live trading:** DISABLED  
**Orders placed by this committee:** none

## Summary

Default committee run on queue #1 (VPIN jump-risk **veto**). Research brief, bull (conf 68), bear (conf 85), and debate completed. Screen **not run**. Prereg remains FROZEN with expectation NO_GO.

## Bull (compressed)

Run the hashed screen now for information value and binding queue order; peer-reviewed jump-prediction + shipped negative-selection precedent; admit low P(GO).

## Bear (compressed)

−0.24R AccBand substrate + 0/16 prior positive-selection + only 2/99 BTC/ETH cohort trades → modal INSUFFICIENT_DATA / NO_GO; full harvest may be expensive; success case resembles a veto class the owner already disabled for flow.

## Debate

**REVISE** — Stage-0 feasibility (raw VPIN fire rates + n projection + pin substrate/n semantics) before full heavy screen. Not REJECT; not blind APPROVE-for-screen.

## ScreenStatus

NOT RUN. No metrics invented. Handoff to `strategy-evidence-pipeline` only after Stage-0 passes.

## RiskStatus (docs mirror + heartbeat)

| Item | Status |
|------|--------|
| live_trading | false |
| operating_mode / signal | PAPER / mcp (heartbeat 2026-07-24T23:52Z) |
| is_halted / latch | false / absent |
| paper profile | MAX_FLOW_BAND |
| require_human_approval | true |
| Binding authority | `config.py` + `.env` (not `risk_committee.yaml`) |

## PaperStatus

Funnel @ 2026-07-24T23:40Z: `rsi2_4h_cfg226` GATE_BLOCKED (WR in band, after-cost loss); `pullback_ma20_4h` 12/30 WR 0.0; `tsmom_20d_1h` 29/30 WR 0.34; `zfade_4h_cfg365` 24/30 WR 0.67; F1 IDLE. Directional cohort accruing.

## Limitations

- Debate authored in-orchestrator after Fable Task spend limit on debate agent spawn.  
- Bear’s 2/99 count not independently re-queried in this final step (cited from bear artifact).  
- BAND_REGIME_FILTER env may differ from docs mirror — verify boot banner before any veto install.

## Confidence

**72 / 100** that REVISE (Stage-0 first) is the correct committee action.

## Verdict

**REVISE**

## HumanActionRequired

1. **Approve Stage-0** (recommended): small aggTrades slice → VPIN θ fire-rate report → go/no-go for full screen.  
2. Or **explicitly override** to full screen today (accept INSUFFICIENT_DATA / multi-GB harvest risk).  
3. Or **park VPIN** and free the slot (C2 harvest continues; liquidation-cascade prep).  
4. Do **not** enable live trading or wire a VPIN veto into MCP from this memo alone.

Artifacts: `35_research_brief_*`, `35_bull_*`, `35_bear_*`, `35_debate_*`, `35_strategy_spec_*`, prereg `27_*`.
