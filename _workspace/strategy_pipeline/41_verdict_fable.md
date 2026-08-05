# 41 — Fable independent verdict: liq-cascade reversion

*Model: Claude (orchestrator / Fable role) | Submitted BEFORE seeing Codex | 2026-07-29*  
*Prereg hash cited: `13ee84e40f2604b660d763082f2681200532c1f6bd55cbbb21f4c9491085afaf`*

## Ledger check

- Not a reopen of OI-divergence: signal is forceOrder USD flow only (prereg forbids OI).
- Not RSI/MR/TA.
- Adjacent prior script at 10 bps must not be treated as this prereg’s result.

## Evidence weighed

1. Microstructure: cascades create temporary depth collapse / overshoot (arXiv Slippage-at-Risk / ADL clustering cited in 32_). Mechanism is plausible.
2. Measurement: Binance forceOrder undercount is binding — signal SNR is structurally degraded.
3. Costs: 30–60 bps in-event is the correct stress; many “bounce” anecdotes die here.
4. Stage-0: majors clear n≥30 at Θ=1e6 easily; FIT-alt at high Θ is sparse (ALGO/ARB/AVAX nearly empty at 1e5–1e6).
5. Deep-research prior ~25% GO; existing honest prior on the 10 bps tooling was NO_EDGE / gate-at-best.

## Verdict

**`ACCRUE_ONLY`**

### Rationale

- Hash is correctly frozen; Stage-0 majors-ready does **not** alone authorize burning a heavy screen day in the same iteration as the first dual-agree on a NEW family (protocol: max one heavy stage/UTC day; harvest integrity still needs explicit verify after WS path fixes).
- FIT-alt arm is not Stage-0 clean at cascade magnitudes that match “cascade” economic meaning — do not SCREEN_NOW a pooled FIT story.
- `SCREEN_NOW` would be premature until: (a) harvester status proves live append post-07-25 fix, (b) separate UTC day reserved for after-cost screen under this hash only.
- `STOP` would be wrong: mechanism is unscreened, data exists, Stage-0 majors pass — Open accrual is correct.

## Implement recommendation if both-agree

Verify `scripts/harvest_liquidations.py` is on the start_all / supervisor path; confirm `data/liquidations_status.json` freshness and JSONL monotonic hour growth; document undercount in status/README note. **No shadow probe. No MCP path.**

## Explicit non-claims

Does not claim positive expectancy. Does not authorize FIT indicator strategies. Does not clear tsmom S1 (parked).
