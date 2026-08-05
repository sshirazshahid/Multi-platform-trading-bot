# De-Emotion Overhaul — Phase 2 (2026-08-04)

## Skill-tree consolidation (2c)

### Deleted
- **`.agents/skills/`** — entire tree removed (91 skill directories; ~789 files).
- **`.claude/skills/`** — 30 directories removed:
  - `01-devops-basics` … `20-enterprise-workflows` (20)
  - `crypto-market-rank`, `doc-coauthoring`, `docx`, `mcp-builder`, `pdf`, `pptx`, `skill-creator`, `slack-gif-creator`, `template-skill`, `webapp-testing`, `xlsx`

### Kept — `.claude/skills/` (6 + README)
- `after-cost-screening`
- `investment-committee`
- `refuted-families-ledger`
- `shadow-probe-integration`
- `strategy-evidence-pipeline`
- `strategy-research-wiring`
- `README.md` (updated to include strategy-research-wiring)

### Kept — root `skills/` (7 trading skills, untouched)
- `exchange-connectivity`
- `futures-universe-edge-research`
- `tp-precision-engine`
- `trading-backtest-validation`
- `trading-monitoring`
- `trading-risk-management`
- `windows-bot-deployment`

### Kept — `.claude/agents/` (9 agents, untouched)

### `skills-lock.json`
- Rewritten (v2): 6 local governance/research-wiring skills only; `rootSkills` array documents the 7 trading skills separately.

## Safe-now deletions (2a)

| Path | Action |
|------|--------|
| `show_arb.py` | deleted |
| `claude_ai_runner.py` | deleted |
| `claude_analysis_runner.py` | deleted |
| `claude_daily.py` | deleted |
| `_workspace/tmp_phase2_run/` | deleted (13 files) |
| `_workspace/*stderr*` (6 files under strategy_pipeline/) | deleted |
| `_workspace/pytest_*.txt` (2) | deleted |
| `_workspace/rebuild_test_*.txt` (2) | deleted |
| `backtest_all.py`, `backtest_split.py`, `analyze_trades*.py`, `strategy_lab.py`, `fix_ghost_positions.py`, `clear_cache.py`, `verify_*.py` (root), `canslim_screener_2026-07-26_*`, `run_confluence_paper.bat`, `.obsidian/` | not present — skipped |

**Preserved:** `backtest.py`, `backtest_v3.py`, `auto_backtest.py`, `strategies/legacy/*`, `core/signals/*`

## Bat/ps1 sweep (2e)

| File | Change |
|------|--------|
| `TradingBot.bat` | Removed menu `[Z]` (show_arb), `[E]`/`[F]`/`[G]` (claude_ai_runner); removed `:arb_view`, `:ai_run`, `:ai_key`, `:ai_view` handlers. `[Y]` arbitrage backtest (backtest.py) retained. |
| `TradingBot.ps1`, `scripts/*.bat`, `scripts/*.ps1` | no references to deleted runners |

## `.gitignore`

- No standalone `ntrader/` section found — only a comment reference on the `/data/` rule; no edit.

## Blockers

None. No changes to `core/promotion_gate.py`, entry_policy, kill_switch, live_gate, risk_manager, or `SIGNAL_SOURCE`.
