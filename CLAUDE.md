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


## Task Artifacts

- Plan work in `tasks/todo.md`; after any correction from the user, record the pattern in `tasks/lessons.md` and review it at session start.

## Repository Purpose

This repository contains two interconnected systems:

1. **Crypto Trading Bot** — An autonomous 24/7 crypto futures/spot bot running on Binance, Bybit, and Bitget. Uses a multi-factor scoring engine (MCP Brain), learning engine, warehouse, and meta-filter. Currently in a learning-first rebuild (Apr 2026 pivot) with three operating modes: OBSERVATION, PAPER, CONTROLLED_LIVE.

2. **Claude Skills** — 50+ packaged skills for equity investors and traders, designed for Claude's web app and Claude Code. Each skill bundles prompts, knowledge bases, and helper scripts for market analysis, technical charting, and trading strategy development.

**Also in-repo:** `qtb/` = stdlib research lab (audit-only; not live path). See `docs/superpowers/specs/2026-08-11-qtb-audit-boundary.md`.

**OctoBot note:** Vendored OctoBot was evaluated then **removed** (2026-08-11). Lessons only: `docs/superpowers/specs/2026-08-11-octobot-lessons-adopted.md`. Primary runtime remains `main.py` / MCP.

**Nine OSS stacks (2026-08-18):** Freqtrade, Qlib, TensorTrade, Backtrader, Lean, VectorBT, FinRL/FinRL-X, Hummingbot, NautilusTrader — researched, **not installed**. Lessons: `docs/superpowers/specs/2026-08-18-oss-stack-lessons.md`. Sample catalogs (TA/RL/grid/MM/Hyperopt) stay ledger-STOP. PAPER directional book measured −EV; cash-move = `ENTRY_POLICY=SHADOW_ONLY` (plan `_workspace/strategy_pipeline/73_plan_paper_then_cash.md`).

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

All extend `BaseStrategy`. **None of these classes are in the live portfolio decision
path** — `mcp_brain` makes every live entry/exit decision via the deterministic
scorer (De-Emotion 2026-08-04: LLM/sentiment removed from the trade path).
These strategy classes are for backtest/research only.

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

## Test and Tooling Gotchas

- Skill tests are PER-SKILL because of module-name collisions (`scorer.py`, `helpers.py`): `python -m pytest skills/<name>/scripts/tests/ -v`. The root `conftest.py` evicts conflicting module names and pushes the active skill's `scripts/` to the front of `sys.path`.
- The `docs-completeness` pre-commit hook blocks commits when a new skill lacks its generated doc pages (`python3 scripts/generate_skill_docs.py --skill <name>`, then both skill catalogs and both READMEs).

## Standing directive: the four-lens check (owner, 2026-08-22)

Owner directive: "always do thoroughly Fundamental Analysis (FA), Technical
Analysis (TA), Sentiment Analysis, Order FLOW for any coin stock pair oil gold
silver". Honour it — but honour it HONESTLY. A 9-agent audit (2026-08-22) mapped
what each lens actually does here. State the lens's real status; never imply an
input is steering a decision when it is not.

| Lens | Collected | Reaches a live decision? | Measured |
|---|---|---|---|
| **TA** | yes | **YES** — the 4 required conditions ARE the entry (`core/scoring/entry_score.py:174`); side chosen at `:136`; ATR stop. **TP is NOT TA-derived** — overwritten by fixed geometry (`core/scoring/helpers.py:33`) | score↔profit \|r\|<0.07; indicator-confluence "refuted on own data" (ledger:27) |
| **FA** | yes, heavily | **~1%** — only `funding_rate`, as feature 13/15 of the ensemble → `core/economic_entry_gate.py:321`. Every explicit funding/OI bonus+veto is OFF and has fired 0 times | funding direction "IR 0.248, NO_EDGE" (ledger:30) |
| **Order flow** | yes | **Cost filter, not direction.** Live: thin-book/spread/slippage vetoes. Directional `ob_imbalance` is feature 15/15 and is 0.0 for 82.2% of rows | VPIN CONFIRMED_NO_GO; L2 "no directional information" (ledger:49,52) |
| **Sentiment** | partly | **NO.** `core/engine/cycle.py:40` `news_context = {}`; `symbol_news_sentiment=None` hardcoded, callee says "Deprecated (De-Emotion); ignored if passed" | "sentiment paths are inert, measured over 30 days" |

⚠ **`core/scoring/data_sources.py:125` computes `sentiment` as clamped price
momentum.** It is TA wearing a sentiment label — never cite it as sentiment.

⚠ **THE ARITHMETIC THAT EXPLAINS THE LOSS.** `ACCURACY_TARGET_MODE` sets
`tp_frac_buy=0.45` / `tp_frac_sell=0.35`: the bot risks 1 to make 0.45. Required
break-even win rate is then **74–99%**, and at SL <=1% under stressed costs it
exceeds **100% — mathematically unwinnable**. Actual WR is 52.5%. The measured
avg-win/avg-loss ratio of 0.38 is not a market outcome, it IS this config.
Ledgered as `AccBand frac dual goal — CONFIRMED_NO_GO, 0/12 cells`: WR-in-band
and after-cost profit are mutually exclusive on the measured no-edge path.
Quote this before proposing any signal work — the geometry binds first.

**So a four-lens check means:** report all four, label each live / cost-filter /
research-only / disconnected, cite the measured verdict, and never present a
disconnected lens as if it informed the call.

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
### gstack
use the /browse skill from gstack for all web browsing, never use mcp__claude-in-chrome__* tools, and lists the available skills: /office-hours, /plan-ceo-review, /plan-eng-review, /plan-design-review, /design-consultation, /design-shotgun, /design-html, /review, /ship, /land-and-deploy, /canary, /benchmark, /browse, /connect-chrome, /qa, /qa-only, /design-review, /setup-browser-cookies, /setup-deploy, /setup-gbrain, /retro, /investigate, /document-release, /document-generate, /codex, /cso, /autoplan, /plan-devex-review, /devex-review, /careful, /freeze, /guard, /unfreeze, /gstack-upgrade, /learn. Then ask the user if they also want to add gstack to the current project so teammates get it.
---

## 하네스: Strategy Evidence Pipeline

**Goal:** Every trading-strategy request flows through evidence gates — ledger check → after-cost screen → adversarial audit → log-only shadow probe → frozen promotion gate. Nothing reaches live decisions on narrative alone.

**Trigger:** For any request to research, screen, apply, or implement trading strategies/patterns (including "update the program" with strategies, or re-running/refining previous strategy work), use the `strategy-evidence-pipeline` skill. Questions about already-refuted families are answered directly from the `refuted-families-ledger` skill — conversationally, without launching workflows.

**변경 이력:** moved out of always-loaded context 2026-08-22. The full 43-row pipeline changelog (2026-07-08 → 2026-08-19) is preserved verbatim in git: `git show HEAD~1:CLAUDE.md`. Live verdicts are in the `refuted-families-ledger` skill; run artifacts are in `_workspace/strategy_pipeline/`.

