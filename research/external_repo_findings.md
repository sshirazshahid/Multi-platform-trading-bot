# External Repo Study — Findings & Portable Patterns (2026-05-26)

Study of 5 trading repos to harden the bot's **live-execution safety**, **not** to find
predictive edge (a 443-alpha OHLCV search + a funding/carry/cross-venue sweep + 185 live
LLM-judgment trades all came back NO_EDGE). Each pattern is tagged:

- `[SAFETY→test-gated]` — execution/risk safety; may improve the (still-PAPER, still-halted) path, gated by tests + review.
- `[PREDICTIVE→promotion_gate]` — anything that changes entry direction; routed to shadow + `core/promotion_gate.py` only. **Not wired live.**

## Headline (all 5 repos agree)

**These repos are popular for infrastructure, orchestration, and demo appeal — not for proven
profitability.** freqtrade (mature OSS infra; user live results mixed-to-negative), QuantDinger
(broad real broker integration), TradingAgents (a clean abstraction + an arXiv paper whose results
are methodologically broken), Vibe-Trading (honest: claims no returns at all), FinceptTerminal
(ambitious desktop terminal). **Popularity ≠ edge** — directly consistent with our NO_EDGE finding.
Orchestration is not alpha.

---

## 1. freqtrade — DEEP (the live-safety gold standard)

Popular because it is the most mature open-source crypto-bot **infrastructure** (strategy-as-code,
unified backtest/hyperopt/dry-run/live, large community) — **not** because it is profitable.

Safety patterns **beyond what we already have** (we already have CooldownPeriod, StoplossGuard,
MaxDrawdown halt, AgeFilter, 15s unfilled→market fallback):

| # | Pattern | Tag | Status vs our code | Target file |
|---|---|---|---|---|
| F1 | **LowProfitPairs** quarantine (lock pair when Σ closed-profit over window < required) | SAFETY | PARTIAL — verify our May-15 F1 sums *realized PnL*, not just loss count | `core/risk_manager.py` / `core/meta_filter.py` |
| F2 | **Stoploss re-creation when canceled/missing** (not just mispriced) | SAFETY | PARTIAL — our drift guard fixes *mispriced*; verify it also re-places a *vanished* stop | `core/order_manager.py` |
| F3 | **Throttled trailing re-assert** (interval throttle + abort-if-closed recheck between cancel/replace) | SAFETY | PARTIAL — adds anti-thrash throttle | `core/trailing_stop_manager.py` |
| F4 | **startup_update_open_orders** — cross-restart stale-order sweep keyed off persisted timestamps | SAFETY | **MISSING** — ours is in-session only | `core/order_manager.py` + persistence |
| F5 | **handle_onexchange_order** — manual-close/missed-fill detection; **2% tolerance** before mutating tracked size | SAFETY | PARTIAL — the 2% guard is stronger than our ghost overwrite | `core/position_tracker.py` |
| F6 | **handle_insufficient_funds** — on funds error, re-fetch orders to recover unrecorded fills | SAFETY | **MISSING** — anti-phantom recovery on the funds-error path | `core/order_manager.py` |
| F7 | **exit_timeout_count → emergency market exit** (escalation ceiling on stuck exits) | SAFETY | **MISSING** — guarantees eventual flatten | `core/order_manager.py` |

---

## 2. TradingAgents (TauricResearch) — 79.7k stars

Sequential LLM agent graph (analysts → bull/bear debate → trader → risk-manager veto → reflection
memory). **No demonstrated out-of-sample, cost-aware edge.** The paper reports Sharpe 5.6–8.2 over
5 months on 3 mega-cap tech stocks, **zero transaction costs**, test window **inside the LLM
training cut** (news leakage), and a listed buy-and-hold AMZN Sharpe of 17.6 (impossible) — the
methodology is broken/non-standard. The one part that "works" in backtest (LLM directional
forecasts on 2024 news) is exactly what our 443-alpha + LLM-judgment already falsified.

| Pattern | Tag | Note |
|---|---|---|
| Bull/bear **adversarial debate** before committing | SAFETY→test-gated | Decision hygiene, not signal |
| **Risk-manager veto gate** orthogonal to signal | SAFETY | We already have this (`core/agents/risk_agent.py`, `meta_filter.py`) |
| **Deterministic instructional memory** (recall recent same/cross-ticker trades + outcome + reflection; NOT embeddings) | SAFETY→test-gated | Cheap to bolt onto `core/agents/shadow_panel.py` |
| LLM analyst directional views | **PREDICTIVE→promotion_gate** | Already falsified OOS — **do not wire** |

## 3. Vibe-Trading (HKUDS)

Single ReAct loop with 5-layer context compression + per-run/persistent memory; 75 finance skills.
**Honest** — README states research/sim/backtest only and makes zero return claims.

| Pattern | Tag | Note |
|---|---|---|
| ReAct loop + **5-layer context compression** | SAFETY→test-gated | Useful for long-running monitor sessions |
| Read/write **tool batching** (parallel reads, serial writes) | SAFETY | Orchestration hygiene |
| Walk-forward / bootstrap-CI as first-class eval | SAFETY | We already do (`core/walk_forward.py`, `screen.py`) |

## 4. QuantDinger (brokermr810) — ~6.6k stars

Substantive self-hosted quant platform (multi-broker CCXT/IBKR/MT5). Its **execution worker** maps
directly onto our known pain points (ghosts, SL drift, orphan orders):

| Pattern | Tag | Status | Target |
|---|---|---|---|
| **Stale-order requeue** — claim `processing`, revert stuck >90s to `pending` | SAFETY | overlaps F4 | `core/order_manager.py` |
| **Reconciliation drift thresholds** — correct only on **1% qty / 0.5% price** divergence | SAFETY | concrete numbers vs our `reconcile_sl_drift` | `core/position_tracker.py` |
| **Fee-aware close-qty clamp** — clamp close/reduce qty to actual exchange position | SAFETY | addresses our Bitget BE-SL reject / free-vs-equity bug class | `core/order_manager.py` |
| Limit-first-then-market sweep w/ phase fill/fee accumulation | SAFETY | cleaner than our market-only paths | `core/smart_executor.py` |

## 5. FinceptTerminal (Fincept-Corporation) — ~24k stars

C++/Qt "Bloomberg-style" desktop terminal. **Nothing actionable for us.** Sentiment is a paywalled
external dependency; the news layer is an unimplemented spec (no feeds/APIs in source). Only the
Polymarket public API surfaced as a possible alt-data probe — `[PREDICTIVE]`, adjacent to alpha,
treat with skepticism. Stars reflect the ambitious pitch, not usable code for this bot.

---

## What this study changes NOW (and what it deliberately does not)

**Done now (test-only, zero behavior change, provable safety):**
- PAPER↔live parity test (`tests/test_sim_live_parity.py`) — lock in that paper is never optimistic vs live.
- Double-latch test + a behavior-preserving `live_latch_permits_execution` extraction in `core/live_gate.py`.

**Deliberately DEFERRED (behavioral live-path changes — F4/F6/F7, fee-aware clamp, etc.):** the bot
is **halted with no edge**; adding unvalidated live-execution behavior that cannot be live-tested is
premature and itself a risk. These are cataloged above as a prioritized roadmap. Revisit only if a
gated, OOS-validated edge ever justifies reconsidering live — at which point F4 (cross-restart stale
sweep), F6 (insufficient-funds fill recovery), F7 (exit escalation ceiling), and the fee-aware
close-qty clamp are the highest-value, defect-preventing additions.

**Not adopted:** every `[PREDICTIVE]` pattern (LLM directional forecasts, indicator signals) — same
information class already falsified OOS; would re-run a failed experiment with extra plumbing.
