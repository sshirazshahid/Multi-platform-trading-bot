# Probe Universe Widening — Design (2026-07-20)

**Owner-approved** (AskUserQuestion, 2026-07-20): widen both bundle-MR shadow probes (ZfadeProbeAgent / cfg365 candidate, Rsi2TrackerProbeAgent / cfg226 tracker) from the 5-major basket to the full approved universe, so forward "any pair" universality evidence accrues immediately. Tradeoffs accepted on record: per-pair statistics dilute, generalization is learned forward (not historically), accrual cohort is annotated rather than wiped.

## Design

1. **Symbol source:** at probe construction, derive the symbol list from the active PAPER-futures spec artifact (`data/strategy_specs/MCP_DIRECTIONAL_PAPER.json`) via `core.strategy_spec.load_all_specs()` + `approved_paper_futures_routes` (bases × the probe's venue). One artifact governs the directional lane and the probes; future universe edits propagate automatically. **Fail-closed:** if the spec is missing/invalid or yields zero routes, fall back to the current frozen 5 (BTC/ETH/SOL/BNB/XRP) and log one warning.
2. **Venue policy:** bybit (unchanged). Bases without a bybit USDT-perp are skipped with ONE boot-log warning line listing them; never fabricated, never re-fetched per cycle.
3. **Cohort honesty:** existing resolved/pending rows are KEPT (gates pool per-arm). The widening moment is (a) journaled and (b) stamped into each funnel lane's `detail.universe_widened_utc` (ISO string) so any future promotion dossier discloses the universe change.
4. **Load/risk:** ~+470 OHLCV fetches/day at 4h cadence; log-only lane; existing concurrency caps and all live paths untouched.
5. **Tests (TDD):** spec-derived resolution (44-base fixture → expected symbol list), fail-closed fallback (missing/empty spec → frozen 5), bybit-missing skip, funnel annotation presence. Sibling probe + funnel tests stay green.

## Executor tasks

T1: symbol-source resolver in `core/agents/bundle_mr_probe_agent.py` (or its spec-builder in `core/bot_engine.py` `_PROBE_SPECS` — whichever the current wiring makes cleaner) + tests red→green. T2: funnel `detail.universe_widened_utc` stamp for the two lanes + test. T3: restart main.py (code-only change), verify in-process boot log lists the widened universe count and any skipped bases. T4: journal entry + CLAUDE.md changelog row (one line, table style) + commits on the CURRENT feature branch `probe/bundle-mr-shadow-2026-07-19` (never checkout main; git add only named files; Fable co-author trailer).
