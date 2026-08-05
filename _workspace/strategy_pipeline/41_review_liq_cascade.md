# 41 — AI Reviewer: liq-cascade ACCRUE_ONLY

*Reviewer: main-loop fallback (Task `ai-reviewer` blocked by API usage limit 2026-07-29); adversarial posture per `.claude/agents/ai-reviewer.md`. Ledger read first.*

```
VERDICT: APPROVE
SCOPE: PAPER-only authorization for (1) harvest integrity verification of
  scripts/harvest_liquidations.py + data/liquidations_history.jsonl /
  liquidations_status.json, and (2) recording the dual-agree ACCRUE_ONLY
  + next-UTC-day majors screen QUEUE under prereg sha256
  13ee84e40f2604b660d763082f2681200532c1f6bd55cbbb21f4c9491085afaf.
  Explicitly DOES NOT authorize: after-cost screen outcomes today, shadow
  probe install, MCP/order-path changes, CONTROLLED_LIVE, promotion, or
  FIT-alt pooling.
```

## Checks

| Check | Result |
|-------|--------|
| Ledger | PASS — forceOrder USD flow ≠ refuted OI-divergence; no RSI/TA entry; prereg forbids OI/funding-sign/price patterns |
| Both-agree | PASS — after rebuttal both labels = `ACCRUE_ONLY` (`41_verdict_codex_rebuttal.md` + Fable) |
| Prereg before outcomes | PASS — hash frozen in `41_prereg_liq_cascade.md` before verdicts; no after-cost means computed this iteration |
| No live / order path | PASS — implement map is harvest-only |
| Vocabulary policing | PASS — initial Codex `SCREEN_NOW` corrected; same-day outcome ban preserved |
| Multiplicity / FIT | PASS — FIT fail-closed cell-by-cell called out; majors queue only |
| Adjacent 10 bps script | PASS — explicit non-reuse of `run_liquidation_edge_screen.py` outputs |

## Attempted refutations

1. **“Already have a liq screen script → duplicate family.”** Rejected as kill: prior tooling uses 10 bps and different multiplicity; this hash freezes 30/60 bps + arm split. Still must not relabel old outputs.
2. **“Stage-0 majors ready → must SCREEN_NOW today.”** Rejected: protocol one-heavy-stage/day; both models post-rebuttal chose ACCRUE_ONLY; Codex first draft already forbade same-day outcomes.
3. **“OI-flush name = OI-divergence reopen.”** Rejected if and only if implementation stays on forceOrder USD; any OI feature → REJECT future work.

## Risks (remain open)

- ForceOrder undercount may make Θ thresholds noisy → future screen likely NO_GO (~25% prior).
- Harvest may be idle despite JSONL history (stale process) — verify must prove **live** append, not merely file existence.
- Queued next-day screen still requires a fresh ai-reviewer pass at that stage before outcomes.

## Hard stops honored

No CONTROLLED_LIVE. No frozen-gate override. No order-flow promotion. No edits to `config.py` / `live_gate` / `promotion_gate` / `.env` authorized by this APPROVE.
