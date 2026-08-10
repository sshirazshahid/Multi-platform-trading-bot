# Cleanup inventory (2026-08-11) — no deletes this pass

**Policy:** Evolve (1A). Mark status only. **Zero deletes** until a later owner-approved cleanup PR.

| Path | Status | Notes |
|------|--------|-------|
| `strategies/legacy/*` | KEEP | Backtest/research only; not MCP live path |
| `multi_profile_main.py` | KEEP | Separate DRY_RUN entry; not `main.py` |
| `core/blacklist_manager.py` | KEEP | Present; audit before any ARCHIVE |
| `core/kelly_sizer.py` | ARCHIVE-CANDIDATE | Vestigial vs live Claude Portfolio path — confirm callers first |
| `core/arbitrage_engine.py` | ARCHIVE-CANDIDATE | Same |
| `core/market_regime.py` | KEEP | May still be referenced by research/filters |
| `.claude/settings.local.json.bak-*` | DELETE-CANDIDATE | Local backup junk (untracked) |
| `.gitignore.bak-*` | DELETE-CANDIDATE | Local backup junk (untracked) |
| `_workspace/strategy_pipeline/` | KEEP | Evidence audit trail |
| `_workspace_prev/` (if present) | KEEP | Prior pipeline runs |
| `research/pine_scripts/_tv_*.png` | DELETE-CANDIDATE | Session screenshots; gitignore preferred |
| `S4` (repo root oddity) | DELETE-CANDIDATE | Inspect before remove |
| Shadow probe agents | KEEP | Log-only fleet; promotion evidence |
| `mcp_server/` | KEEP | Read-only introspection |
| `dashboard/` package | KEEP | Ops TUI |
| `mission_control/` | KEEP | Ops UI |

## Explicit refuses
- Do not delete `strategies/legacy` “because unused in live path” without owner sign-off — still used for research honesty.
- Do not delete warehouse, positions, or `_workspace` artifacts.
