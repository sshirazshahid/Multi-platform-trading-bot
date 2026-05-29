# CLAUDE.md

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
- `mcp_brain.py` — Algorithmic scoring engine. 4 required conditions (4h EMA gap >= 0.15%, 1h EMA alignment, RSI sweet spot, ADX >= 20) + 6 bonus conditions (MACD, slope, 15m timing, volume, structure, microstructure). Base score 50 on all-required pass, +5-12 per bonus, max theoretical 101. Entry requires score >= 65. ATR-based SL (1.5x ATR, clamped 1.5-3.5%) with 2.5:1 R:R. Two modes: Portfolio Analysis (5 min) and Position Monitor (90s).
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

All extend `BaseStrategy`. Active in backtest/research; Claude Portfolio mode makes its own entry/exit decisions.

Active: `SupertrendStrategy`, `MeanReversionStrategy`, `MultiTFStrategy`, `GridTradingStrategy`, `ScalpingStrategy`, `DCAStrategy`, `FundingRateArbStrategy`, `RebalancingStrategy`
Backtest-only: `TrendFollowingStrategy` (0% WR over 8 live trades)

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
- Risk management (tiered leverage: STANDARD 3x / STRONG 4x / CONVICTION 5x)
- Trading pairs: `TRADING_MODE=all` (in `.env`) runs `pair_discovery.discover_all_mode` against every liquid USDT perp on each exchange. The static `UNIVERSE_WHITELIST` gate in `bot_engine._execute_open` is SKIPPED when `TRADING_MODE=all` — quality is enforced by MCP score ≥65, meta-filter, universe_filter (spread/vol/depth), and risk gates. `WHITELIST_SYMBOLS` (16 high-WR symbols) is retained as a leverage-tier hint, not as an entry gate
- Trading gates (whitelist/blacklist/allowed hours from knowledge_model data)
- Strategy parameters (legacy, kept for DCA/rebalance reference)

### Runtime Data (`data/`)

- `positions.json` — Active/closed position tracking
- `warehouse.sqlite` — Historical trade + candidate warehouse
- `knowledge_model.json` — Learned patterns (hour scores, symbol stats)
- `mcp_decisions.jsonl` — MCP Brain decision log
- `mcp_state.json` — MCP Brain state
- `risk_state.json` — Risk manager state (drawdown, pauses)
- `capital_allocator.json` — Capital allocation state
- `spot_portfolio.json` — Spot holdings state

### Dashboard (`dashboard.py`)

Rich TUI dashboard. Launch: `python dashboard.py` or `TradingBot.bat` option [2].
Flags: `--refresh SEC` (3-3600), `--width COLS` (60-200).

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
- **Spec §12 halt is guarded auto-resume** (updated 2026-04-17): After 5 global consecutive losses, the risk manager writes `data/review_required.json` and halts. Bot auto-resumes after a **4-hour cooldown** (`SPEC12_AUTO_RESUME_COOLDOWN_MIN` in `core/risk_manager.py`); on resume it clears the stale global loss streak and deletes the review flag so future halts can retrigger cleanly. The flag still fires notifier/email warnings during the cooldown window for audit visibility. No human action required.
- **Kelly stats are all negative**: `data/kelly_stats.json` shows negative expected value for all strategies. This is expected during the learning-first phase — the bot should remain in PAPER mode.
- **knowledge_model PnL tracking broken**: `data/knowledge_model.json` hour scores all show `total_pnl: 0.00`. The learning engine records win/loss counts but not PnL amounts.
- **Spot positions get no exchange-side SL/TP**: Only futures positions receive exchange-side stop-loss orders. Spot relies on local monitoring only.
- **Failed SL placement triggers EMERGENCY alert**: If the exchange rejects an SL order, the position is flagged `_sl_failed=True` and the notifier sends an alert. The position has no exchange-side protection.

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
