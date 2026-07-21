# CLAUDE.md

# Agentic Trading Core Directives & Rules

## 1. Core Identity & Execution
* **Role:** You are an autonomous algorithmic trading agent. Your job is to research market conditions, evaluate risk-adjusted opportunities, and manage portfolio allocations.
* **Philosophy:** Patient, risk-averse, systematic. Avoid over-optimizing to prevent textbook overfitting.
* **Validation:** Before final execution or backtesting, you must run an out-of-sample validation or Monte Carlo test.

## 2. Hard Portfolio Risk Layers (Circuit Breakers)
* **Max Allocation:** Never risk more than $3\%$ of the total portfolio capital on any single trade.
* **Exposure Limit:** Total open exposure across all trades must not exceed $12\%$ of total portfolio capital.
* **Stop-Loss Guardian:** If a position's value drops $8\%$ below the exact entry price, close the position immediately via a market order.
* **Leverage Limit:** Maximum active leverage must never exceed $2.5\times$.

## 3. Workflow & Schedule Rules
* **Market Prep:** Before market open, pull historical OHLCV and volume confidence data for specified tickers.
* **Decision Framework:** Before recommending an action, answer these 5 questions:
  1. What is the current portfolio cash balance?
  2. What positions are already open?
  3. What is the fundamental direction of the news/sentiment?
  4. What do the 20-day and 50-day moving averages tell you?
  5. What is the potential risk (drawdown) if this trade fails?
* **Order Logic:** Never place generic market orders for entries. Use limit orders that sit within $0.2\%$ of the ask.

## 4. Output & Logging
* **Output Format:** Whenever an action (research, entry, exit, or update) is taken, generate a structured markdown journal entry at `/journal/YYYY-MM-DD.md`.
* **Data Standards:** Always use adjusted close prices for calculations. Factor in transaction costs of $0.1\%$ on backtesting. Returns must be returned as percentages.

## 5. Restrictions
* Do not attempt to bypass brokerage API requirements.
* Do not execute code without proper paper-trading or a dry-run confirmation first.
* Do not invent or hallucinate market reasons for price actions. If data is missing or ambiguous, state that it is missing.


# Role and Persona
You are an expert software engineering agent. You possess deep knowledge of software design patterns, clean code principles, and efficient debugging.

# Core Directives
1. **Goal-Driven Execution:** Do not just write code; define verifiable success criteria. For "Add validation", write a test, then write the code to make it pass. For "Refactor X", ensure tests pass before and after the change.
2. **Context Management:** Only read the files necessary for the current task. If a task spans multiple files, explore the dependency graph first. Do not dump the entire codebase into the prompt, as it leads to noisy self-evaluation.
3. **Verification over Explanation:** Verify your assumptions by running the code (or writing a test script) before returning the final solution.

# Coding Guidelines
- **DRY & KISS:** Keep solutions modular, readable, and simple. 
- **Error Handling:** Anticipate failures and implement graceful error handling.
- **Documentation:** Write clear, concise docstrings for classes and functions, and update markdown documentation if public APIs change.

# Output Format
- Keep your conversational responses concise.
- Provide the final edited code with clear boundaries (e.g., proper markdown code blocks).
- State clearly if a task requires multiple iterative steps or git commits.



Behavioral guidelines to reduce common LLM coding mistakes. Merge with project-specific instructions as needed.
Tradeoff: These guidelines bias toward caution over speed. For trivial tasks, use judgment.

#1. Think Before Coding
Don't assume. Don't hide confusion. Surface tradeoffs.
Before implementing:
State your assumptions explicitly. If uncertain, ask.
If multiple interpretations exist, present them - don't pick silently.
If a simpler approach exists, say so. Push back when warranted.
If something is unclear, stop. Name what's confusing. Ask.

#2. Simplicity First
Minimum code that solves the problem. Nothing speculative.
No features beyond what was asked.
No abstractions for single-use code.
No "flexibility" or "configurability" that wasn't requested.
No error handling for impossible scenarios.
If you write 200 lines and it could be 50, rewrite it.
Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify.

#3. Surgical Changes:
Touch only what you must. Clean up only your own mess.
When editing existing code:
Don't "improve" adjacent code, comments, or formatting.
Don't refactor things that aren't broken.
Match existing style, even if you'd do it differently.
If you notice unrelated dead code, mention it - don't delete it.
When your changes create orphans:
Remove imports/variables/functions that YOUR changes made unused.
Don't remove pre-existing dead code unless asked.
The test: Every changed line should trace directly to the user's request.

#4. Goal-Driven Execution
Define success criteria. Loop until verified.
Transform tasks into verifiable goals:
"Add validation" → "Write tests for invalid inputs, then make them pass"
"Fix the bug" → "Write a test that reproduces it, then make it pass"
"Refactor X" → "Ensure tests pass before and after"
For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```
Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.
---
These guidelines are working if: fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

## Workflow Orchestration

### 1. Plan Mode Default
- Enter plan mode for ANY non-trivial task (3+ steps or architectural decisions)
- If something goes sideways, STOP and re-plan immediately
- Use plan mode for verification steps, not just building
- Write detailed specs upfront to reduce ambiguity

### 2. Subagent Strategy
- Use subagents liberally to keep main context window clean
- Offload research, exploration, and parallel analysis to subagents
- For complex problems, throw more compute at it via subagents
- One task per subagent for focused execution

### 3. Self-Improvement Loop
- After ANY correction from the user: update tasks/lessons.md with the pattern
- Write rules for yourself that prevent the same mistake
- Ruthlessly iterate on these lessons until mistake rate drops
- Review lessons at session start for relevant project

### 4. Verification Before Done
- Never mark a task complete without proving it works
- Diff behavior between main and your changes when relevant\
- Ask yourself: "Would a staff engineer approve this?"
- Run tests, check logs, demonstrate correctness

### 5. Demand Elegance (Balanced)
- For non-trivial changes: pause and ask "is there a more elegant way?"
- If a fix feels hacky: "Knowing everything I know now, implement the elegant solution"
- Skip this for simple, obvious fixes -- don't over-engineer
- Challenge your own work before presenting it

### 6. Autonomous Bug Fixing
- When given a bug report: just fix it. Don't ask for hand-holding
- Point at logs, errors, failing tests -- then resolve them
- Zero context switching required from the user
- Go fix failing CI tests without being told how

## Task Management

1. Plan First: Write plan to tasks/todo.md with checkable items
2. Verify Plan: Check in before starting implementation
3. Track Progress: Mark items complete as you go
4. Explain Changes: High-level summary at each step
5. Document Results: Add review section to tasks/todo.md
6. Capture Lessons: Update tasks/lessons.md after corrections

## Core Principles

- Simplicity First: Make every change as simple as possible. Impact minimal code.
- No Laziness: Find root causes. No temporary fixes. Senior developer standards.
- Minimal Impact: Only touch what's necessary. No side effects with new bugs.




This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.


## Repository Purpose

This repository contains two interconnected systems:

1. **Crypto Trading Bot** — An autonomous 24/7 crypto futures/spot bot running on Binance, Bybit, and Bitget. Uses a multi-factor scoring engine (MCP Brain), learning engine, warehouse, and meta-filter. Currently in a learning-first rebuild (Apr 2026 pivot) with three operating modes: OBSERVATION, PAPER, CONTROLLED_LIVE.

2. **Claude Skills** — 50+ packaged skills for equity investors and traders, designed for Claude's web app and Claude Code. Each skill bundles prompts, knowledge bases, and helper scripts for market analysis, technical charting, and trading strategy development.

## Bot Architecture

### Entry Point and Lifecycle

`main.py` → `BotEngine` (watchdog wrapper with crash restart, max 10/hour)

`BotEngine` (`core/bot_engine.py`) is the main loop:
- Initializes exchange clients, risk manager, order manager, position tracker
- Runs on a timer: portfolio analysis cycle (5 min), position monitor (2 min), news scan (30 min), learning engine (60 min)
- Claude Portfolio mode is the active path — legacy strategy scanner is disabled

### Operating Modes (config.py)

| Mode | Behavior | Env Vars |
|------|----------|----------|
| `OBSERVATION` | Collect data only, no orders (not even paper) | `OPERATING_MODE=OBSERVATION` |
| `PAPER` | Simulated fills via `sim_execution.py`. **Default.** | `OPERATING_MODE=PAPER` |
| `CONTROLLED_LIVE` | Real orders. Requires double latch. | `OPERATING_MODE=CONTROLLED_LIVE` + `CONTROLLED_LIVE_ENABLED=true` + signed `docs/CONTROLLED_LIVE_CHECKLIST.md` |

The legacy `DRY_RUN` flag is derived: `DRY_RUN = (OPERATING_MODE != "CONTROLLED_LIVE")`.

### Core Modules (`core/`)

**Decision Pipeline:**
- `mcp_brain.py` — Algorithmic scoring engine. 4 required conditions (4h EMA gap >= 0.15%, 1h EMA alignment, RSI sweet spot, ADX >= 20) + 6 bonus conditions (MACD, slope, 15m timing, volume, structure, microstructure). Base score 50 on all-required pass, +5-12 per bonus, max theoretical 101. Entry score floor is env-driven (`MCP_ENTRY_MIN_SCORE`; legacy 66/65 when unset, currently 50 under MAX_FLOW_BAND). ATR-based SL (1.5x ATR, clamped 1.5-3.5%) with 2.5:1 R:R — under `ACCURACY_TARGET_MODE` (profile-gated) the TP is geometry-compressed instead (band WR by construction). Two modes: Portfolio Analysis (5 min) and Position Monitor (90s).
- `features.py` — Unified `FeatureVector` for meta-filter inputs. Percentile-based (vs 30-day window).
- `meta_filter.py` — Rule-based quality gate. Returns ALLOW/SKIP/REVIEW. Orthogonal to risk engine.
- `claude_advisor.py` — Claude CLI advisory (advisory-only, not decision authority).

**Execution:**
- `order_manager.py` — Order placement, SL/TP management, partial TP. Consults `sim_execution.py` in paper mode.
- `sim_execution.py` — Paper trading realism: slippage model (5 bps open/close, 10 bps SL), wick-based SL/TP triggers, 8h funding charges.
- `risk_manager.py` — Dynamic SL/TP (ATR-based), correlation-aware sizing, drawdown recovery, regime-adaptive leverage. Pause policy: 2 consec losses on symbol → 6h pause, 3 on strategy family → 12h, 5 global → OBSERVATION + review.
- `position_tracker.py` — Tracks all open/closed positions across exchanges. Persists to `data/positions.json`.
- `live_gate.py` — CONTROLLED_LIVE sign-off gate. Validates `docs/CONTROLLED_LIVE_CHECKLIST.md` signature.

**Learning & Data:**
- `warehouse.py` — SQLite append-only store (`data/warehouse.sqlite`) for every candidate setup, trade, and advisory review. The learning substrate.
- `learning_engine.py` — Reads closed trades, feeds `KnowledgeModel`, produces insights + HTML report.
- `knowledge_model.py` — Persistent knowledge model (hour scores, symbol stats, strategy stats). Survives mode switches.

**Portfolio Management:**
- `capital_allocator.py` — Spot/futures capital reallocation. Sweep profits, deploy on structure breaks, hedge drawdowns.
- `spot_manager.py` — Spot portfolio tracking across exchanges. HOLD/SCALE_OUT/SELL/HEDGE decisions.

**Other (active):**
- `trailing_stop_manager.py` — Trailing stop execution
- `auto_mutator.py` — Auto-blacklists symbols with high loss rates; blocks shorts when counter-trend losses cluster
- `correlation_manager.py` — Cross-asset correlation tracking; reduces position size for correlated assets
- `news_scanner.py` — Crypto news monitoring (30 min cycle)

**Other (vestigial / backtest-only):**
- `blacklist_manager.py`, `market_regime.py`, `kelly_sizer.py`, `arbitrage_engine.py` — present but not called from the active Claude Portfolio pipeline

### Exchange Layer (`exchanges/`)

All extend `BaseExchange` (`exchanges/base.py`) which wraps ccxt with:
- Auto-retry (3 attempts, exponential backoff with jitter)
- Timestamp sync on Binance -1021 errors
- Silent handling of symbol-not-found errors

Active exchanges: `BinanceClient`, `BybitClient`, `BitgetClient` (three-exchange setup).

### Strategies (`strategies/`)

All extend `BaseStrategy`. **None of these classes are in the live Claude Portfolio decision
path** — `mcp_brain` makes every live entry/exit decision (Claude-primary, algorithmic-fallback).
These are for backtest/research only. (Corrected 2026-06-07 wiring audit; the prior "Active" list
was misleading.)

- `strategies/` root: `DCAStrategy` (live DCA is hard-OFF: `ENABLE_DCA=False`, config.py),
  `RebalancingStrategy`, `rule_engine`, `base_strategy`.
- `strategies/legacy/` (backtest-only, moved out of the live tree): `SupertrendStrategy`,
  `MeanReversionStrategy`, `MultiTFStrategy`, `GridTradingStrategy`, `ScalpingStrategy`,
  `FundingRateArbStrategy`, `TrendFollowingStrategy` (0% WR over 8 live trades).

Note: live SCALP behavior is the SCALP *leverage tier* in `mcp_brain`/`bot_engine`, NOT the legacy
`ScalpingStrategy` class. "Breakout" is the MCP structure/microstructure bonus, NOT
`SupertrendStrategy`. The standalone `strategy_selector`/`arbitrage` strategies run only via the
separate `multi_profile_main.py` entry point (DRY_RUN-required), not via `main.py`.

**Shadow-probe fleet (`core/agents/`, log-only forward research — the "strategy building" lanes):**
six probes registered at boot via `bot_engine._PROBE_SPECS` / ShadowRunner, each writing warehouse
rows the promotion funnel tracks toward the ≥30-resolved frozen gate: `ListingShortProbeAgent`,
`UnlockShortProbeAgent`, `TsmomProbeAgent` (2 arms), `BreakoutProbeAgent`, and the bundle-MR pair
`ZfadeProbeAgent` (cfg365 candidate) + `Rsi2TrackerProbeAgent` (cfg226 tracker) on a 40-symbol
spec-derived universe. None can place orders; promotion is owner-signed only.

### Environment Setup

Requires `.env` with exchange API keys (Binance, Bybit, Bitget). Copy from template:
```bash
cp .env.example .env  # then fill in exchange API keys
```

Key env vars: `BINANCE_API_KEY/SECRET`, `BYBIT_API_KEY/SECRET`, `BITGET_API_KEY/SECRET/PASSPHRASE`, `OPERATING_MODE` (default: PAPER).

### Configuration (`config.py`)

All settings centralized. Loaded from `.env` via `python-dotenv`. Key sections:
- Exchange credentials (Binance, Bybit, Bitget)
- Operating mode and DRY_RUN derivation
- Fee structure per exchange
- Sim-live realism settings (slippage, wick SL/TP, funding)
- Risk management — five `LEVERAGE_TIERS` (config.py): STANDARD 3x / STRONG 4x / CONVICTION 5x / AGGRESSIVE 10x / SCALP 3x. **As of 2026-06-06 (`CONFIDENCE_LEVERAGE_ESCALATION=False`), confidence cannot escalate leverage above STANDARD** — the MCP score is anti-predictive, so STRONG/CONVICTION/AGGRESSIVE are blocked; only STANDARD/SCALP are reachable. SCALP is dropped from the dict when `SCALP_TIER_ENABLED=false`.
- Trading pairs: `TRADING_MODE=all` (in `.env`) runs `pair_discovery.discover_all_mode` against every liquid USDT perp on each exchange. The static `UNIVERSE_WHITELIST` gate in `bot_engine._execute_open` is SKIPPED when `TRADING_MODE=all` — quality is enforced by the MCP score floor (env `MCP_ENTRY_MIN_SCORE`; legacy default 66/65, **currently 50 under the MAX_FLOW_BAND profile**), meta-filter, universe_filter (spread/vol/depth), and risk gates. `WHITELIST_SYMBOLS` (16 high-WR symbols) is retained as a leverage-tier hint, not as an entry gate
- Trading gates (whitelist/blacklist/allowed hours from knowledge_model data)
- Strategy parameters (legacy, kept for DCA/rebalance reference)

### Runtime Data (`data/`)

- `positions.json` — Active/closed position tracking
- `warehouse.sqlite` — Historical trade + candidate warehouse (+ shadow_decisions/shadow_outcomes/shadow_listing_probe/shadow_bundle_mr_probe probe tables)
- `knowledge_model.json` — Learned patterns (hour scores, symbol stats)
- `mcp_decisions.jsonl` — MCP Brain decision log
- `mcp_state.json` — MCP Brain state
- `risk_state.json` — Risk manager state (drawdown, pauses); **incident halts persist separately in `risk_incident_latch.json`** (see Gotchas)
- `capital_allocator.json` — Capital allocation state
- `spot_portfolio.json` — Spot holdings state
- `promotion_funnel.json` — hourly funnel snapshot: per-lane state/floor-progress toward the frozen promotion gate (written by `scripts/promotion_funnel.py`)
- `strategy_specs/*.json` — StrategySpec artifacts; an `active-paper` futures spec here IS the route-gate authorization for directional paper OPENs (cached at boot)
- `goal_progress.json` — daily goal report lanes (UTC-day WR/profitability vs the 63–67% target, `target_status` field)
- `carry_positions.json` / `carry_gate_log.jsonl` — F1 carry runner state + per-check net-edge log (the funnel's F1 regime-watch reads the latter)
- `heartbeat.json` — includes `paper_trading_profile` + `paper_profile_started_at` (cohort epoch)

### Dashboard (`dashboard.py`)

Rich TUI dashboard. Launch: `python dashboard.py` or `TradingBot.bat` option [2].
Flags: `--refresh SEC` (3-3600), `--width COLS` (60-200).

### MCP Server (`mcp_server/`, read-only introspection)

`mcp_server/trading_bot_mcp.py` is a local stdio MCP server (registered via
`.mcp.json`) that exposes the warehouse + decision data as read-only tools
(`trading_bot_list_tables`, `_recent_trades`, `_performance_summary`,
`_recent_candidates`, `_shadow_vs_live`, `_query`). It opens
`data/warehouse.sqlite` in `mode=ro` and imports no bot/config/ccxt code, so it
**cannot place orders or change state** — it is for interrogating the bot's
reasoning, not driving it. Pure data-access lives in `warehouse_reader.py` (no
`mcp` dependency, unit-tested in `tests/test_trading_bot_mcp.py`). Install with
`pip install -r mcp_server/requirements.txt`.

**Shadow → live promotion criterion (agents + MCP):** the multi-agent shadow
ensemble (`core/shadow_runner.py`) and any new prediction layer remain
**log-only**. They may be promoted to the live decision path ONLY after their
decisions beat the live path on the honest gate (`core/promotion_gate.py`:
MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55, AUC≥0.60). Never promote on a no-edge
signal — use `trading_bot_shadow_vs_live` to watch the comparison.

## Common Development Commands

### Running the Bot

```bash
# Paper mode (default)
python main.py

# Status check (no engine start)
python main.py --status

# Dashboard
python dashboard.py
```

### Running Tests

```bash
# All skill tests (bulk, uses importlib mode to handle name collisions)
python -m pytest

# Single skill tests
python -m pytest skills/position-sizer/scripts/tests/ -v

# Bot-level tests
python -m pytest tests/ -v

# Specific bot test
python -m pytest tests/test_warehouse.py -v

# With coverage
python -m pytest --cov=skills --cov-report=term-missing
```

### Backtesting

```bash
# V3 backtest (current scoring engine)
python backtest_v3.py

# Multi-strategy backtest
python backtest_all.py

# Auto-backtest (parameter sweep)
python auto_backtest.py
```

### Linting

```bash
# Ruff check + format (matches CI)
ruff check skills/ scripts/
ruff format --check skills/ scripts/

# Codespell
codespell --toml pyproject.toml skills/ scripts/
```

### Pre-commit Hooks

```bash
pre-commit install && pre-commit install --hook-type pre-push
```

Pre-commit: trailing-whitespace, end-of-file-fixer, check-yaml, check-toml, check-merge-conflict, check-added-large-files (500KB), ruff (lint+format), codespell, detect-secrets, no-absolute-paths, skill-frontmatter, docs-completeness.
Pre-push: pytest (all skill tests via `scripts/run_all_tests.sh`).

### CI Pipeline (`.github/workflows/ci.yml`)

Three jobs on PR/push to main: `lint` (ruff + codespell), `test` (per-skill pytest + coverage), `security` (bandit SAST + detect-secrets).

### Dependencies

Bot: `requirements.txt` — ccxt, pandas, numpy, python-dotenv, loguru, rich, schedule, requests, websockets.
Skills: `pyproject.toml` — jsonschema, pyyaml, scipy. Dev: pytest, ruff, bandit, detect-secrets, codespell, pre-commit.

## Skill System Architecture

### Skill Structure

Each skill in `skills/` follows:
```
<skill-name>/
├── SKILL.md              # Required: YAML frontmatter (name + description) + workflow instructions
├── references/           # Knowledge bases (markdown) loaded into Claude's context
├── scripts/             # Executable Python scripts (not auto-loaded)
└── assets/              # Templates and resources for output
```

`name` in frontmatter must match directory name. Instructions use imperative form ("Analyze the chart", not "You should analyze").

### Creating a New Skill

Use the skill-creator plugin, then complete ALL of:
1. `python3 scripts/generate_skill_docs.py --skill <name>` (generates EN + JA doc pages, updates indexes)
2. Add to catalog category sections in `docs/en/skill-catalog.md` and `docs/ja/skill-catalog.md`
3. Add to API Requirements Matrix in both catalogs
4. Add to `README.md` and `README.ja.md`
5. Add API key requirements if applicable

The `docs-completeness` pre-commit hook blocks commits if doc pages are missing.

### Skill Testing

```bash
# Tests are per-skill due to module name collisions (scorer.py, helpers.py, etc.)
python -m pytest skills/<name>/scripts/tests/ -v
```


The root `conftest.py` handles sys.path isolation: evicts conflicting module names and pushes the active skill's `scripts/` to front of sys.path.

### API Keys for Skills

Some skills require paid APIs. Key environment variables:
- `FMP_API_KEY` — Financial Modeling Prep (earnings, economic calendar, dividend screeners)
- `FINVIZ_API_KEY` — FINVIZ Elite (optional speedup for dividend screeners)
- `ALPACA_API_KEY` / `ALPACA_SECRET_KEY` — Alpaca (portfolio manager only)

See the API Requirements table in `README.md` for per-skill requirements.

### Skill Self-Improvement Loop

Automated pipeline: `scripts/run_skill_improvement_loop.py` (round-robin selection, auto scoring via `dual-axis-skill-reviewer`, Claude CLI improvement, quality gate, PR creation). Daily at 05:00 via launchd.

### Skill Auto-Generation Pipeline

`scripts/run_skill_generation_pipeline.py` — Weekly: mine session logs + score ideas. Daily: design highest-scoring idea as a complete skill PR.

## Gotchas and Non-Obvious Behavior

- **WIDEN action removed** (Apr 15 2026): Spec §2 forbids widening stop losses. The position monitor prompt and bot_engine no longer accept WIDEN. If re-added it will silently map to HOLD.
- **MCP SL override removed**: `order_manager.py` no longer lets MCP Brain widen the deterministic ATR-based SL at entry time. SL is authoritative once computed.
- **Meta-filter requires warehouse data**: `spread_pctl`/`vol_pctl` are computed from 30-day warehouse history. On a fresh install with empty `warehouse.sqlite`, all meta-filter SKIP rules are neutral (defaulting to ALLOW). The filter becomes effective after ~1 week of candidate data.
- **Spec §12 / loss-driven halts are REMOVED** (2026-05-27): All nine loss-driven halt/pause mechanisms — including the Spec §12 global 5-consecutive-loss halt, per-symbol and per-family pauses, and the outlier-loss flag — were permanently disabled (the `HALT_MECHANISMS` dict was deleted from `config.py` and the gate checks in `core/risk_manager.py` are now `if False` guards; see `risk_manager.py:977-1035`). The bot no longer writes `data/review_required.json` on a loss streak, no longer switches to OBSERVATION, and there is **no** `SPEC12_AUTO_RESUME_COOLDOWN_MIN` cooldown (the symbol does not exist). The user-requested replacement is the **soft daily-loss circuit breaker** (2026-05-28, `config.py` "DAILY-LOSS CIRCUIT BREAKER"): opt-in, refuses only NEW entries once today's realized loss exceeds `max_loss_pct` of start-of-day balance, then auto-resets at UTC day rollover. It does not halt the process, does not switch mode, and does not touch open positions (their fail-closed per-trade SLs still protect them). Per-trade ATR SL/TP and exchange-side liquidation remain the only hard loss rails.
- **Kelly stats are all negative**: `data/kelly_stats.json` shows negative expected value for all strategies. This is expected during the learning-first phase — the bot should remain in PAPER mode.
- **knowledge_model hour-score PnL field is `net_pnl`, not `total_pnl`**: hour scores in `data/knowledge_model.json` record PnL as `net_pnl`/`avg_pnl` (plus `total_fees`) — see `core/knowledge_model.py:257-267`. PnL recording works; tooling still reading the old `total_pnl` name sees a missing field, not broken tracking.
- **Spot positions get no exchange-side SL/TP**: Only futures positions receive exchange-side stop-loss orders. Spot relies on local monitoring only.
- **Failed SL placement triggers EMERGENCY alert**: If the exchange rejects an SL order, the position is flagged `_sl_failed=True` and the notifier sends an alert. The position has no exchange-side protection.
- **SL/TP conditionals trigger on MARK price by default** (C3, 2026-07-08): `SLTP_TRIGGER_MARK_PRICE=true` sets Binance `workingType=MARK_PRICE`, Bybit `triggerBy`/`slTriggerBy`/`tpTriggerBy=MarkPrice`, Bitget `triggerType=mark_price` in `build_sl_tp_order_params`. Bitget was ALREADY mark-price via the ccxt 4.5.54 default; Binance/Bybit changed from last-price. ⚠ Honest divergence: the PAPER sim (`sim_execution.check_wick_trigger`) still triggers on 1m last-price candles — paper and live SL trigger feeds differ until the sim models mark price. Revert with `SLTP_TRIGGER_MARK_PRICE=false` (flag-off params are byte-identical to pre-C3). **Update 2026-07-19:** `bot_engine` previously HARDCODED `enforce_mark_price_triggers=True`, silently ignoring this flag — now honors config. Currently set `false` (last-price triggers) after the AXS mark-fetch halt loop; ghost-AXS/mark-fetch diagnosis is the pending follow-up before re-enabling.
- **`.env` changes require restarting the SUPERVISOR, not main.py** (2026-07-18): `launcher_supervisor._safe_worker_env` passes its own inherited environ to children and `config.load_dotenv()` never overrides inherited vars — a long-running supervisor silently vetoes every `.env` edit. Kill the launcher_supervisor tree and relaunch detached.
- **Incident clearance = `data/risk_incident_latch.json`** (2026-07-18): `risk_manager.latch_incident` persists to this dedicated file "until manual clearance" and every boot reloads it; clearing `is_halted` in `risk_state.json` does nothing. Archive/delete the latch file, then restart.
- **Verify runtime flags via the in-process boot banner, never subprocess re-parses** (2026-07-18): `bot_engine.__init__` logs `Profile / EntryFloor / SLCooldown / AccBand` lines at boot — that is ground truth. A fresh `python -c "import config"` bypasses the supervisor inheritance chain and can show values the bot does not have.
- **Paper entry authorization stack** (2026-07-19) — an OPEN must pass ALL of: `APPROVED_PAPER_STRATEGIES` allowlist (needs BOTH the normalized id `mcp_registry` AND order_manager's raw id `algo_det`), the frozen catalog (`core/strategy_program.py` MCP_DIRECTIONAL_PAPER), an **active-paper futures StrategySpec** in `data/strategy_specs/` (otherwise the route gate blocks with `strategy_spec_no_active_paper_futures_universe`; specs are cached at boot — restart after adding), `band_regime_filter` (vetoes while BTC 1h vol ratio <0.7), and ExecutionGuard.
- **MAX_FLOW_BAND knobs** (2026-07-19): `MCP_ENTRY_MIN_SCORE` (unset = legacy 66/65), `SL_COOLDOWN_ENABLED`, `PAPER_TRADING_PROFILE=MAX_FLOW_BAND` (clean cohort epoch via heartbeat). Band WR is geometry, not edge — expectancy is reported as measured. **2026-07-20: `BAND_REGIME_FILTER_ENABLED=false` by owner directive** (aggressive PAPER accrual for frac tuning; accepts screen-13's measured WR headwind in toxic regimes — ADX>30 ≈ 59% at frac 0.40; re-enable = flip the flag + supervisor restart).
- **Promotion funnel** (2026-07-18): `scripts/promotion_funnel.py` runs hourly (schtask `TradingBot_PromotionFunnel`) → `data/promotion_funnel.json` + owner-signed dossiers in `reports/promotion_dossiers/`. Consult it before any "what's promotable" question. Weekly `TradingBot_UnlockCalendar` keeps the unlock calendar ≥30d forward.
- **GateGuard fact-forcing hook (this machine)**: the first Bash/Edit/Write after every session resume is blocked until the required facts (request summary + what the command produces; for writes: callers/duplicates/schema/verbatim instruction) are presented in the SAME message — present them and retry the identical call; typically passes on the second attempt.

## Key Conventions

### Code Generation (TDD)

Write/update tests first (expect failure) → implement minimal change → refactor keeping tests green → run suite before finishing.

### No Personal Information in Committed Files

This is a public repository. Never hardcode:
- Absolute paths with usernames — use relative paths or `Path(__file__).resolve().parents[N]`
- API keys / secrets — use env vars or `.gitignore`-listed config files

### Ruff Configuration

Line length: 100. Target: Python 3.9. Selected rules: E, F, I, W, UP, B. Ignored: E501 (formatter handles), E402 (conftest sys.path), B904/B007/B017 (incremental fix).

### SKILL.md Writing Style

Imperative/infinitive verbs. Instructions for Claude to execute, not user instructions. Structure: Overview, When to Use, Workflow, Output Format, Resources.

### Analysis Output

All skill outputs saved to `reports/` directory. Filename: `<skill>_<analysis-type>_<date>.md` (and `.json`).

### Language

Code and analysis outputs in English. README available in English and Japanese. User interactions may be in Japanese.

---

# Ruflo Multi-Agent Integration

When to lean on Ruflo's coordination layer (added 2026-05-03 after `ruflo init`).

## Agent Comms (SendMessage-First Coordination)

Named agents coordinate via `SendMessage`, not polling or shared state.

```
Lead (you) ←→ architect ←→ developer ←→ tester ←→ reviewer
              (named agents message each other directly)
```

### Spawning a Coordinated Team

```javascript
// ALL agents in ONE message, each knows WHO to message next
Agent({ prompt: "Research the codebase. SendMessage findings to 'architect'.",
  subagent_type: "researcher", name: "researcher", run_in_background: true })
Agent({ prompt: "Wait for 'researcher'. Design solution. SendMessage to 'coder'.",
  subagent_type: "system-architect", name: "architect", run_in_background: true })
Agent({ prompt: "Wait for 'architect'. Implement it. SendMessage to 'tester'.",
  subagent_type: "coder", name: "coder", run_in_background: true })
Agent({ prompt: "Wait for 'coder'. Write tests. SendMessage results to 'reviewer'.",
  subagent_type: "tester", name: "tester", run_in_background: true })
Agent({ prompt: "Wait for 'tester'. Review code quality and security.",
  subagent_type: "reviewer", name: "reviewer", run_in_background: true })

// Kick off the pipeline
SendMessage({ to: "researcher", summary: "Start", message: "[task context]" })
```

### Patterns

| Pattern | Flow | Use When |
|---------|------|----------|
| **Pipeline** | A → B → C → D | Sequential dependencies (feature dev) |
| **Fan-out** | Lead → A, B, C → Lead | Independent parallel work (research) |
| **Supervisor** | Lead ↔ workers | Ongoing coordination (complex refactor) |

### Rules

- ALWAYS name agents — `name: "role"` makes them addressable
- ALWAYS include comms instructions in prompts — who to message, what to send
- Spawn ALL agents in ONE message with `run_in_background: true`
- After spawning: STOP, tell user what's running, wait for results
- NEVER poll status — agents message back or complete automatically

## When to Swarm

- **YES**: 3+ files, new features, cross-module refactoring, API changes, security review, performance work
- **NO**: single file edits, 1-2 line fixes, docs updates, config changes, simple questions

For trading-bot work specifically: most fixes are single-file or 2-3 file. Reach for swarm only on bigger initiatives like the Phase A multi-agent shadow build (which spans 12+ files).

## Agent Routing

| Task | Agents | Topology |
|------|--------|----------|
| Bug Fix | researcher, coder, tester | hierarchical |
| Feature | architect, coder, tester, reviewer | hierarchical |
| Refactor | architect, coder, reviewer | hierarchical |
| Performance | perf-engineer, coder | hierarchical |
| Security | security-architect, auditor | hierarchical |

## Memory & Learning (optional, daemon-required)

When the Ruflo daemon is running, MCP tools become available for cross-session memory:

```bash
# Before any task
npx @claude-flow/cli@latest memory search --query "[task keywords]" --namespace patterns
npx @claude-flow/cli@latest hooks route --task "[task description]"

# After success
npx @claude-flow/cli@latest memory store --namespace patterns --key "[name]" --value "[what worked]"
npx @claude-flow/cli@latest hooks post-task --task-id "[id]" --success true --store-results true
```

## Background Workers

| Worker | When |
|--------|------|
| `audit` | After security changes |
| `optimize` | After performance work |
| `testgaps` | After adding features |
| `map` | Every 5+ file changes |
| `document` | After API changes |

```bash
npx @claude-flow/cli@latest hooks worker dispatch --trigger audit
```

## Setup (already done; commands here for reference)

```bash
ruflo init --start-all                       # already run; populated .claude/ + .claude-flow/
npx @claude-flow/cli@latest doctor           # health check
npx @claude-flow/cli@latest daemon start     # for hooks/memory features (run in own terminal)
```

The trading bot itself does NOT use Ruflo — Ruflo is purely for me (Claude Code) to coordinate development work on the bot. Bot runtime is independent.

### gstack
use the /browse skill from gstack for all web browsing, never use mcp__claude-in-chrome__* tools, and lists the available skills: /office-hours, /plan-ceo-review, /plan-eng-review, /plan-design-review, /design-consultation, /design-shotgun, /design-html, /review, /ship, /land-and-deploy, /canary, /benchmark, /browse, /connect-chrome, /qa, /qa-only, /design-review, /setup-browser-cookies, /setup-deploy, /setup-gbrain, /retro, /investigate, /document-release, /document-generate, /codex, /cso, /autoplan, /plan-devex-review, /devex-review, /careful, /freeze, /guard, /unfreeze, /gstack-upgrade, /learn. Then ask the user if they also want to add gstack to the current project so teammates get it.
---

## 하네스: Strategy Evidence Pipeline

**Goal:** Every trading-strategy request flows through evidence gates — ledger check → after-cost screen → adversarial audit → log-only shadow probe → frozen promotion gate. Nothing reaches live decisions on narrative alone.

**Trigger:** For any request to research, screen, apply, or implement trading strategies/patterns (including "update the program" with strategies, or re-running/refining previous strategy work), use the `strategy-evidence-pipeline` skill. Questions about already-refuted families are answered directly from the `refuted-families-ledger` skill — conversationally, without launching workflows.

**변경 이력:**
| 날짜 | 변경 내용 | 대상 | 사유 |
|------|----------|------|------|
| 2026-07-08 | 초기 구성: 4 agents (strategy-scout, edge-screener, honesty-auditor, shadow-integrator) + 4 skills (ledger, screening, shadow-probe, orchestrator) | 전체 | /harness 요청; 2026-07-08 deep-research 후속 |
| 2026-07-08 | Live spawn tests (Phase 6-3/6-4) deferred | 검증 | API session limit; structural validation only this pass |
| 2026-07-08 | CLAUDE.md restored from 94fb521 after accidental overwrite by CLAUDE-FABLE-5.md rename (plugin-install window); replaced content preserved in claude_md_replaced_backup_2026-07-08.md | CLAUDE.md | working-copy corruption recovery |
| 2026-07-09 | Maiden run: dispersion + listing-short screens → both INSUFFICIENT_DATA (audit-confirmed); artifacts in _workspace/strategy_pipeline/, screens in research/ | 파이프라인 | 3-venue funding overlap = 5d BTC/ETH only; 0/103 listings funding-covered — funding-history backfill queued |
| 2026-07-09 | Rev2 re-screen on backfilled funding (scripts/backfill_funding_history.py, 137 venue-symbol CSVs): listing-short = CONFIRMED_NO_GO (sizing-only ledger row; signal robust, MC maxDD gate fails), dispersion = NO_GO action upheld but screener cost model audited UNSAFE (per-fold round-trip artifact; sign IS persistent) — no ledger row | 파이프라인 | honesty-auditor 03_rev2_audit_findings.md; follow-ups: binance∩bybit long-hold dispersion re-screen (REQUIRED) + capital-scaled listing-short as NEW pre-registration |
| 2026-07-09 | Rev3 follow-ups: capital-scaled listing-short (3%/12% cap, account equity curve) = **CONFIRMED_GO** — the pipeline's first — strictly as unlevered log-only shadow probe (true concurrent-MTM maxDD 0.10–0.14, ~2× the screen's 0.073; 3× leverage would breach 0.25 → UNSAFE); dispersion binance∩bybit hold-until-flip = CONFIRMED_NO_GO (OOS-WR 0.378 < 0.55; positive mean is fat-tail artifact) → ledger row added | 파이프라인 | honesty-auditor 03_rev3_audit_findings.md; shadow probe must log per-bar intra-hold MTM + emit discriminating score (AUC gate otherwise un-computable) |
| 2026-07-09 | Phase 3 integration SHIPPED (2d42173): ListingShortProbeAgent in shadow lane via ShadowRunner extra_probes hook — all 6 binding conditions met; score = tanh(pump/0.50)+10·funding8h frozen pre-outcome; 2,659 tests; activates on next bot restart | shadow-integrator | 04_integration_report.md; promotion needs frozen gate on ≥30 resolved + owner sign-off |
| 2026-07-12 | 13_band_conditional screen (16 pre-registered buckets, Bonferroni m=16, 14,555 resolved band outcomes): 0 GO — positive selection refuted (ledger row added; f4/f5 INSUFFICIENT_DATA await forward backfills). The two toxic-regime findings shipped as `BAND_REGIME_FILTER_ENABLED` (config default false, .env true): band-lane-ONLY veto inside the `_acc_mode_on` carve-out — 4h ADX>30 (WR 59.0% vs 65.7%) / BTC 1h ATR ratio<0.7 (WR 55.6%); fail-open, reject_reasons `band_regime_filter:*`. HONESTY: WR-band protection + bleed reduction, NOT edge — every bucket stays after-cost negative | 파이프라인 | screen research/screen_band_conditional.py; tests test_band_regime_filter.py; deep_breakout/shadow lanes verified untouched |
| 2026-07-11 | Phase 1-3 full run on 4 new scout candidates (07_scout_candidates.md): funding-settlement-window timing = NO_GO (OOS-WR 0.510<0.55, sign-unstable across folds/venues), ETH quarterly-basis leg-swap = NO_GO (MC P>0 0.683<0.95), pre-unlock capital-scaled short (W1 T-28d/W2 T-14d, 3%/12% unlevered) = **CONFIRMED_GO** (pipeline's 2nd) — audit flagged fragile n (32/36 really ~19/22 independent bets via SUI/GUN monthly-cliff pseudo-replication) and single-regime profit (100% from 2025-26, 2023 net-negative) as binding caveats, not disqualifying; integrated as UnlockShortProbeAgent (core/agents/), 6 binding conditions in code, W3 arm NOT implemented (failed AUC gate); 2,850→2,871 tests; **staged, not committed** | 파이프라인 | 09_audit_candidate2_final.md, 10_integration_report_candidate2.md; unlock calendar needs `--forward-days 60` backfill before next restart or probe starts silent |
| 2026-07-11 | Owner-directed TSMOM-20d regime-watch probe (Codex external backtest, bot_weight 0.0): TsmomProbeAgent (core/agents/tsmom_probe_agent.py) — **NOT a pipeline GO; TSMOM remains a REFUTED family (long-only TSMOM 2026-06-15, textbook trend 0/40 OOS 2026-06-13) and the Codex evidence does NOT meet the reopen bar (~1.8-month single-regime OOS, ~90-run sweep winner with no multiplicity control, prior period −17.4% / 0% profitable)** — log-only forward paper test, BTC/ETH/SOL bybit perps, two arms scored separately (tsmom_20d_1h_v1: 480/120/168 bars; tsmom_20d_4h_v1: 120/30/42), momentum-sign + 5d-EMA side filter, 2×ATR(14) stop / 2R target / 7d max hold at signal-bar close, notational 1%-risk sizing, frozen score tanh(\|mom20d\|/0.10); Pine-vs-reference flip divergence resolved to the reference backtest's no-overlap rule; expectation NO-PROMOTE (promotion only via frozen gate on ≥30 RESOLVED/arm + owner sign-off); +23 tests; **staged, not committed** | shadow-integrator | 11_integration_report_tsmom.md; owner directive — a log-only forward test is the only honest instrument that could someday meet the reopen bar |
| 2026-07-11 | Owner-directed breakout-60d probe (Codex deep-run winner, same directive): BreakoutProbeAgent (core/agents/breakout_probe_agent.py) — **NOT a pipeline GO; textbook trend/breakout remains a REFUTED family (0/40 OOS 2026-06-13; donchian F in Codex's own first sweep). Deep run is the family's strongest external evidence (5-6yr×10 markets, 2× cost survival, 9/9 parameter cells stable around the (2.2, 3.0) spec confirmed by the 16:19 --finalize-only re-run) BUT winner was selected on burned holdout across 20 candidates and Codex's OWN MC fails our frozen gates (P>0 91.5%<0.95; maxDD p95 42.5%>0.25); ~30-35% WR by design conflicts with the owner's ≥65% WR-floor preference** — log-only forward paper test, 10 majors bybit 4h, arm breakout_60d_4h_v1 (shifted prior-360-bar channel, 2.2×ATR(14) stop, 3R target, 126-bar hold → 127-bar entry-inclusive scan, signal-bar-close fill), frozen score tanh(penetration/0.02) from the cache distribution (median 0.0137, n=1,604); expectation NO-PROMOTE; +15 tests (2,871→2,909 total with TSMOM); **staged, not committed** | shadow-integrator | 11_integration_report_tsmom.md §breakout; Codex's own creation gate requires forward paper trading — this probe is that instrument |
| 2026-07-17 | Futures+spot deep-research run (owner-directed 3-model debate: Sonnet 4.6/Opus 4.8/Fable 5 attack each verdict, Fable reconciles — one-run deviation from Fable-only policy, debate stage only): 3 scouts → 4 pre-registered screens → 12 adversarial audits. **0 GO.** Wrapper-discount NO_GO (p95 21–28bps < 50bps floor), F1-percentile-selectivity NO_GO (79% harvest forfeit, 2×-cost negative; CI/fold gates demoted by debate), stablecoin-depeg NO_GO (mean/AUC oppose in cost-space; regime≠events) → 3 scoped ledger rows; delisting forced-flow INSUFFICIENT_DATA (n=34<30/variant; ALPACA squeeze −22.4× stake; new ledger "Open" section, reopen ~1–2yr). Reopen-bar sweep: nothing qualifies, 3 evidence touch-ups applied. Binding process rules added to pipeline skill: prereg commit/hash BEFORE run; persist raw fee-API artifacts. ⚠ Run's key alert: **F1 incumbent structurally idle** — 0 entries/49,384 live gate checks, edges −25 to −41bps, carry Sharpe externally reported negative for 2025 (arXiv 2510.14435). Artifacts 14_–17_ in _workspace/strategy_pipeline/ | 파이프라인 | 17_integration_report_2026-07-16.md (no-op); 16_debate_15{a,b,c,d}*.md; owner report in session |
| 2026-07-19 | Owner-directed bundle-test MR probes (cloud paper_bundle_test deliverables, same directive pattern as 07-11): ZfadeProbeAgent (zfade_4h_cfg365_v1, CANDIDATE) + Rsi2TrackerProbeAgent (rsi2_4h_cfg226_v1, TRACKER) in core/agents/bundle_mr_probe_agent.py — **NOT a pipeline GO: cfg365 FAILED the bundle's own gate G2 (OOS WR 70-71% ABOVE the frozen 63-67 band) and is a 1-of-432 sweep survivor → plausible-unconfirmed; cfg226 was in/near band but net NEGATIVE OOS — kept solely to measure the band-vs-profit tension forward** — log-only forward paper tests, BTC/ETH/SOL/BNB/XRP bybit 4h perps: cfg365 z20 ±1.5 fade WITH EMA200 trend side, TP 1.0×ATR14 / SL 2.4×ATR14; cfg226 RSI(2) 10/90 with-trend, TP 0.8×ATR14 / SL 2.0×ATR14; both 12-bar time-stop, signal-bar-close entry, notational 3% stake; indicator math mirrors the reference harness EXACTLY (SMA-ATR14 not Wilder, no-min_periods EMA200 with 210-bar gate, dn==0→50 RSI, ddof=1 zscore); frozen pre-outcome scores tanh(\|z\|/3.0) / tanh((10−RSI2)/10); distinct agent_ids because both arms are 4h → funnel lanes zfade_4h_cfg365 + rsi2_4h_cfg226; expectation NO-PROMOTE (promotion only via frozen gate on ≥30 RESOLVED/arm + owner sign-off); +26 tests (suite 3,546 passed; 3 pre-existing failures in owner-modified order_manager/_execute_open/dashboard code, untouched); committed c9ed5b5 on branch probe/bundle-mr-shadow-2026-07-19 (only feature hunks staged; unrelated owner working-tree mods left unstaged); bot restarted 17:30Z−5 — new boot log shows both probes registered | shadow-integrator | owner directive "implement these strategies" + cloud bundle report; a log-only forward test is the honest instrument for an unconfirmed 1-of-432 survivor |
| 2026-07-20 | Owner-approved probe universe widening SHIPPED (T1-T4, docs/superpowers/specs/2026-07-20-probe-universe-widening-design.md): both bundle-MR probes derive symbols from the active PAPER-futures spec artifact (MCP_DIRECTIONAL_PAPER bases x bybit via core.strategy_spec routes) — boot resolved 40/44 symbols, 4 bases skipped in ONE aggregated warning (FTM/MKR/PEPE/TON no live bybit USDT-perp); FAIL-CLOSED to the frozen 5-major basket on missing/invalid/zero-route spec (test-pinned); accrual cohort KEPT and disclosed via funnel detail.universe_widened_utc (static deploy-date stamp, both lanes) so any promotion dossier carries the universe change; log-only lane, zero live-path changes; restart verified in-process 04:53:27 | shadow-integrator | owner AskUserQuestion 2026-07-20; per-pair dilution + forward-learned generalization accepted on record; T1 ba10ddc / T2 4e59431 |
| 2026-07-20 | Deep-audit fix bundle SHIPPED (verified audit: **7 confirmed / 0 refuted**, all fixes TDD red→green, owner-directed restart): F1 virtual_wallet lev-1 futures SIGN-FLIP fixed — futures margin branch now applies at ALL leverages, open+close (0087c78); F2 promotion-funnel run_gate dsr/pbo un-bricked — DSR computed via own proxy vs MIN_DSR, PBO informational-with-note, dossier leg can fire on real evidence (4f0795f); F3 MCP_ENTRY_MIN_SCORE + SL_COOLDOWN_ENABLED profile-gated to PAPER+MAX_FLOW_BAND like the T3 geometry knob (c64486a); F4 repo-integrity — committed strategy_program+contracts (imported by committed entry_policy/order_manager), kill-switch fail-CLOSED OSError hunk, pair_discovery fail-closed rewrite incl. INCIDENT_QUARANTINE_BASES consumer, + 5 test files; 5 further untracked-but-HEAD-imported modules flagged, left to their owners (69f4fc2); F5 scorer loud STARTUP-UNIVERSE-EMPTY warning — silent-idle class that cost 8h on 07-19 (86447c8); F6 watchdog ISO-ts parsing — 6h zero-OPENs starvation alert fires again (8bf11f0); F7 eta=Noned + entries_48h 2000-line cap fixed (77244d9); F8 funnel task restaggered :35→:40 (schtasks, no repo change). Suite 3,594✓/1 skip/3 failed — all 3 attributed NOT-OURS (uncommitted conftest wallet fixture; order_manager/bot_engine in-flight pins). Restart 06:34:44Z verified in-process: MAX_FLOW_BAND banner (EntryFloor 50, SLCooldown DISABLED, AccBand ON), 6 probe registrations, bundle-MR 40-symbol line, universe check OK (44 bases), 0 HALTED | 봇 코어 | journal/2026-07-20.md §06:34Z; 2026-07-20 verified deep audit, owner-authorized program update |
| 2026-07-20 | Spec-gate starvation fix SHIPPED (owner "ultrathink and fix it" on BZ/CL `strategy_spec_route_not_approved` blocks): NEW `scripts/regen_directional_spec.py` derives MCP_DIRECTIONAL_PAPER symbols from the bot's OWN eligibility rules — candidacy = CORE∪EXTENDED_CRYPTO∪incumbents, kept iff an eligible USDT-perp on ≥1 venue via CALLING `pair_discovery._market_rejection_reason` (uncapped: the ALL-mode 25/venue cap is a scan budget, not an authorization rule; load_markets carries no volume so liquidity stays runtime universe_filter's job), fresh INCIDENT_QUARANTINE_BASES, atomic write, --dry-run, fail-closed on venue outage/empty/invalid. HONESTY CORRECTION: BZ is NOT crypto — binance `TRADIFI_PERPETUAL/COMMODITY` (Brent, in ANALYSIS_ONLY_BASES like CL); its block was correct-by-design, so pair_discovery gained metadata-driven `tradfi_asset:*` rejection (+ BZ/PAXG/XAUT statics) and the scorer warning now NAMES blocked routes. Regen: 44→44 bases, +FET/HBAR/JUP/RENDER/TAO/VET, −DOGE/PEPE/WIF (meme) / FTM/MKR/TON (no eligible perp); spec JSON under data/ (gitignored), provenance appended in-file. Restart 22:32:58 verified in-process: bundle-MR probe universe 40→43 (skipped only FET, no bybit perp — probe universe follows every spec regen), universe check OK 44 routes; first cycle 22:35:03 blocked exactly `[BZ@binance, CL@binance]`, zero crypto-native blocks. +17 tests | 봇 코어 | journal/2026-07-20.md §22:35Z; owner directive 22:11 |
| 2026-07-21 | Economic-gate paper_fallback SHIPPED (owner "Still no trades?" — layer-12 terminal blocker): `_apply_mcp_directional_economic_gate` blocked 100% of MCP_DIRECTIONAL_PAPER entries with `economic_gate_model_missing` because `data/models/ensemble_futures_latest.json` is a pre-manifest May-25 artifact with NO top-level `manifest` key ("latest pointer missing ModelManifest", promotion_gate.py:784; would also fail ~57d staleness + pbo=1.0) — no futures model has EVER legitimately passed promotion, validator is CORRECT, nothing was faked. Fix: new knob `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE` ∈ {strict (default = fail-closed unchanged), paper_fallback} in the gate dict, honored ONLY under PAPER+MAX_FLOW_BAND (F3 profile gate); in fallback with no promoted model the gate skips the model-probability term and admits iff the bracket's TP clears the SAME stressed costs (1.5x fee/2x slip/exit floor: geometric breakeven_wr<1.0; else `economic_gate_stressed_breakeven`). Model path resumes automatically on any legitimate promotion; boot banner gained `EconGate : mode=` line. HONESTY: restores PAPER research flow, NOT edge — 30d directional expectancy stays ≈ −0.24R. +16 tests (42✓ across both gate files; same 2 pre-existing decision-provenance failures NOT-OURS); commit also tracks the F4-class untracked-but-HEAD-imported `core/economic_entry_gate.py`, `tests/test_economic_entry_gate.py`, `.env.example` | 봇 코어 | journal/2026-07-21.md §07:00Z; owner standing directive: aggressive PAPER, WR 59-67 band |
