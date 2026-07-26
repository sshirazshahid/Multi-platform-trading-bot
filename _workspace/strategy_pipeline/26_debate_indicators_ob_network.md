# 26 — Dual-model reconciliation: indicators / OB / network (2026-07-23)

**Models:** Codex GPT-5.6-Sol (scout landed) + Fable/Composer (Claude-side; Opus 4.8
usage-limited this billing cycle — substituted). **Both-agree rule applied.**

## Agreement matrix

| Item | Codex | Fable | Final |
|---|---|---|---|
| MA/EMA/MACD/RSI/BOLL/SAR/SUPER/VOL/KDJ/OBV/WR as strategies | REFUTED STOP | REFUTED STOP | **STOP — both agree** |
| Indicator confluence / SuperTrend+MACD+RSI stacks | Reopen bar NOT met | Reopen bar NOT met | **STOP — both agree** |
| Order-book depth as directional alpha | Unfit after costs | Unfit (Frontiers fee-death) | **NO screen as alpha** |
| Order-book depth as entry/execution veto | ADJACENT, #2 priority | ADJACENT, LOW, behind VPIN | **QUEUE #2 (brief only)** |
| Trades/tape directional (QH imbalance) | C3 NO_GO binding | C3 NO_GO binding | **CLOSED** |
| VPIN / toxicity as jump-risk veto | Screen-worthy #1, expect NO_GO | Same (already in 23_ queue) | **QUEUE #1 — both agree** |
| Network / exchange flows | OPEN, not screen-ready | OPEN / INSUFFICIENT_DATA | **NO screen until harvest** |
| Wire classical indicators to bot | Refuse | Refuse | **REFUSE — both agree** |
| Simulate/install indicator strategies now | No | No | **NO — both agree** |

**Disagreements:** none material. AVL interpretation (ADX vs avg-volume) — both STOP either way.

## Binding plan consequence (for /plan phase)

1. **Do not** implement, screen, or shadow-probe MA/EMA/MACD/RSI/BOLL/SAR/SUPER/VOL/KDJ/OBV/WR confluence strategies.
2. **Do not** expand "per-pair indicator data" as a research project — features already computable; more columns ≠ edge.
3. Next legal heavy stage (fresh UTC day, after today's C3): **VPIN-veto prereg** only (already queued).
4. Order-book depth veto shares multiplicity with VPIN — at most one overlay screen before both-agree re-rank.
5. Network flows: feasibility/licensing check only; no strategy claim.

Artifacts: `26_scout_fable_*`, `26_scout_codex_*`, this file.
