# Plan: Apply the research design — replace signal layer with long-only TSMOM

**Source:** `reports/systematic_design_research_2026-06-15.md` · **Owner choice:** "replace the signal layer, keep the infra."
**Posture:** PAPER-first, validate-before-deploy. The design is a HYPOTHESIS, not proven edge — my own OOS test
(2026-06-13) already showed momentum-family strategies collapse OOS on these coins, so the honest prior is skeptical.
This plan gates the whole rebuild on a real validation result.

## Keep (infrastructure the design requires — do NOT touch)
Exchange clients; `order_manager` (sim + live) + DRY_RUN gating; `risk_manager` (loss-bounded SL, daily-loss breaker,
Spec-12, pause policy); the frozen venue-write / order-leak guards; `sim_execution` cost model; `warehouse`;
provenance bundle (decision_id/source/raw capture); `position_tracker`; vol-target sizing + portfolio ES cap; the
1,811-test harness. These ARE the design's "validation infrastructure + paper-first + measurement + risk caps."

## Replace (the edgeless signal layer)
Retire as the live entry source: the net-short MCP scalp scoring path, the legacy strategies, the scalp leverage tiers,
the hour-gate profit allow-list, the ~93%-net-short bias, the Claude-portfolio entry path. Replace with the design's
**long-only time-series trend (TSMOM)** signal on a small liquid-majors universe, vol-targeted, lower frequency.

## Sequenced phases (each gates the next)

### Phase 1 — VALIDATION GATE (DONE 2026-06-15)
Built `scripts/tsmom_validation_backtest.py` -> `reports/tsmom_validation_2026-06-15.md`.
**Result: NO_GO on the strict PROFIT gate (2/5 positive OOS Sharpe).** ETH +1.00, BNB +0.79 positive; BTC/SOL ~0
(beat B&H but flat); XRP failed. BUT: long-only TSMOM **beat buy-and-hold risk-adjusted on 4/5** and **roughly
halved drawdown** (TSMOM -24..-40% vs B&H -51..-76%) by sitting in cash ~55% of the time — the design's
"downside reduction / capital preservation" thesis, confirmed. It is NOT a profit engine; it IS a capital
preserver. Caveat: daily yfinance spot, one OOS window, no CPCV, no perp funding modeled.
**Owner decision (AskUserQuestion 2026-06-15): RE-SCOPE objective from profit to CAPITAL PRESERVATION and build
Phase 2 in PAPER.** The pre-registered profit STOP is honored (we are NOT claiming profit); we proceed under the
research's own stated realistic objective (don't-lose), behind a reversible flag, PAPER-only, CPCV-gated before live.

### Phase 2 — wire as entry source (DONE 2026-06-15; reversible, default-off)
- [x] `core/tsmom_signal.py` — `TSMOMSignal.analyze_portfolio(...)` emits the SAME action-dict shape as
  `mcp_brain.analyze_portfolio` (verified against the contract map). Long-only (never `side=sell`), majors-only
  universe {BTC,ETH,SOL,BNB,XRP}, daily 28d momentum, OPEN-on-positive / CLOSE-on-flip / hold otherwise,
  leverage=1, vol-targeted size. Fresh `decision_id` + `source="tsmom"` per action (provenance intact).
- [x] Config flag `SIGNAL_SOURCE=mcp|tsmom` (default `mcp`, raises on bad value). `config.py`.
- [x] `bot_engine._claude_portfolio_cycle` branches at the call site; `_tsmom_signal()` lazily builds from
  `self.exchanges`. Default path byte-for-byte unchanged.
- [x] TDD: `tests/test_tsmom_signal.py` (11 tests) — long-only invariant, universe filter, momentum gating,
  exit-on-flip, decision_id uniqueness, full action-dict contract, insufficient-data robustness. Suite 1811→1822 green.

### Phase 2b — GATE THE SCALP EXITS (DONE 2026-06-15; default-off, 1822→1834 green)
Discovery via an adversarially-verified workflow (4 exit-surface readers → 3 verifiers: completeness,
capital-preservation safety, tag-correctness) mapped 64 exit-site mentions → a small surgical change-set. The
verifiers caught 4 reader errors and the real blockers. Implemented:

**Prerequisites (without these nothing worked):**
- [x] **Source tag threaded** — `_execute_open` passed `strategy=action.get("source") or "claude_portfolio"`
  (was hardcoded "claude_portfolio"), so a tsmom position is identifiable at exit via the persisted
  `Position.strategy`. Per-position tag (NOT the global flag) → correct across mode flips with positions open.
- [x] **Entry SL widened** — tsmom now keeps its ~8% disaster stop (`tsmom_entry_shape`), instead of the
  tier-fallback silently swapping in a ~1.5% scalp stop.
- [x] **R:R gate bypassed** + `take_profit` forced to literal 0 (no TP; avoids the `take_profit==entry`
  first-tick-close trap and the `tp=0 → R:R=0 → reject` block).

**Exit gating (the consolidated design — one branch, not ten guards):**
- [x] `order_manager.check_sl_tp`: a single `is_tsmom_position(pos)` branch right after the wick-stop —
  enforces disaster-stops-only (widened hard-max-loss `TSMOM_HARD_MAX_LOSS_PCT=9%` + a LIVE polled SL trigger
  **independent of `take_profit`**, resolving the `_tp_ok/_sltp_valid` coupling the audit flagged) then `continue`s
  past partial-TP/scalp-wall/trailing/early-BE/fixed-TP/entry-staleness/age-stale.
- [x] `bot_engine._run_mcp_position_monitor`: `is_tsmom_position` guards on the deterministic pre-pass
  (`_maybe_capture_small_tp` / `_maybe_tighten_aged_position`) and the MCP advice loop (TAKE_PROFIT/TIGHTEN/BREAKEVEN).

**Disaster stops PRESERVED (cardinal rule — verifier confirmed no proposal removes the stop):** wick-stop (paper,
fires on `pos.stop_loss` independent of TP) + widened hard-max-loss (all modes) + live polled SL + exchange-side SL.
- [x] TDD: `tests/test_tsmom_exit_gate.py` (7) + helper tests in `tests/test_tsmom_signal.py` (5). Suite 1822→1834.

**Known residual (live-only, out of PAPER scope):** `position_tracker` ghost-sync auto-close bypasses the gate, but
PAPER positions are skipped by the live ghost loop — a CONTROLLED_LIVE concern, addressed before any live talk.

### Next — PAPER soak (owner restart) → measure → CPCV/perp-cost re-validate before any CONTROLLED_LIVE
Now safe to set `SIGNAL_SOURCE=tsmom` in `.env` for a PAPER soak (applies on restart). Measure with provenance;
re-validate with CPCV + perp-cost model before any live conversation. Still a capital-preservation tool, not profit.

### Phase 3 — retire the old path (ONLY after TSMOM proves out in PAPER)
Move the scalp/strategy cruft to `legacy/`, simplify config, update CLAUDE.md. Reversible until here.

## Honest expectation
Even if Phase 1 passes, the realistic objective at ~$1k is capital preservation, not market-beating (the research's
own verdict). If Phase 1 fails (likely, per today's OOS evidence), the design doesn't apply and we keep the current
PAPER bot. Either way, no infrastructure is destroyed and nothing goes live unvalidated.
