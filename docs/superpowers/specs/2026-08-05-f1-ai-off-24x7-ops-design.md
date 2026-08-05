# Design: F1-First AI-Off 24×7 Profitable Ops
*Date: 2026-08-05 | Status: DRAFT — awaiting owner approval before implementation*

## Problem

Owner wants the trading bot running **24×7** and **profitable**, **without AI/LLM** decisions.

## Binding constraints (evidence)

- Ledger: **only F1** (delta-neutral funding carry) is a validated profit family when gates clear.
- AccBand / MCP directional ≈ **−0.24R** research geometry — not profit (CONFIRMED_NO_GO dual-goal).
- MTSI micro-MM — **CONFIRMED_NO_GO**; strategy-research-wiring forbids reopening from narrative.
- F1 extensions (dispersion, settlement timing, percentile selectivity) — **NO_GO** on this bot.
- Live F1 (2026-08-04/05): **idle is correct** — `ok_pct≈0`, negative `net_edge_bps` when feeds fresh; ~50% feed_stale is an ops gap, not a reason to loosen `F1_MIN_EDGE_BPS`.
- External: carry Sharpe can flip negative in compressed-funding regimes; cross-venue retail arb often dies after costs ([MDPI 14(2):346](https://doi.org/10.3390/math14020346), retail Binance–Bybit study).

**Honesty:** This design makes the bot **correctly ready to earn when funding/contango clears**. It cannot manufacture profit while the funding regime is negative.

## Chosen approach

**F1-first + AccBand bleed-cut (A∪B)** — not a new strategy screen (C).

| Component | Action |
|-----------|--------|
| Main bot | `SIGNAL_SOURCE=none`, `PAPER_TRADING_PROFILE=STANDARD`, `APPROVED_PAPER_STRATEGIES=F1` — no directional tuition |
| F1 | Keep frozen gates; keep `TradingBot-F1CarryPaper` + funding harvesters ON |
| Feeds | Reduce `feeds_stale` / `no_snapshot` (ops + any proven stamp bugs) |
| LLM jobs | Disable DualModelLoop / WeeklyResearch or force `--no-llm`; IntelSynthesis already no `--email` |
| Live | Stay PAPER; no CONTROLLED_LIVE from this design |
| Shadow | Log-only; no promotion |

## Non-goals

- Loosen `F1_MIN_EDGE_BPS` / `F1_COST_MULT`
- Reopen MTSI / AccBand / TA / grid / DCA as edge
- Merge F1 into MCP portfolio cycle
- Claim continuous daily PnL while funding is compressed

## Success criteria (verifiable)

1. Boot banner: `SignalSrc=none`, `Profile=STANDARD`, `AccBand=OFF`
2. Main bot: zero new MCP/`algo_det` opens after cutover
3. F1 schtask + harvest schtasks: Status Ready/Running; `carry_heartbeat` fresh (<1h)
4. 7d `classify_f1_gate_log.py`: `feed_stale` share ↓ vs 2026-08-04 baseline (~50.6%); `regime_idle` may stay high
5. First F1 `ok=true` open only when net_edge clears — logged, not forced

## Implementation slices (after approval)

1. `.env` + supervisor restart checklist (document; owner applies secrets)
2. Schtask audit script / Mission Control note: F1 + harvest ON; research LLM OFF
3. Feed-stale reduction: measure top `no_snapshot` symbols; fix only proven bugs
4. Journal + optional Mission Control “F1 idle honest” tile (optional, later)

## Risks

- Owner expects PnL this week → disappointment if funding stays negative (communicate).
- Cutting AccBand stops WR-band research accrual (intentional).
- Supervisor env pin: must restart tree after `.env` change.
