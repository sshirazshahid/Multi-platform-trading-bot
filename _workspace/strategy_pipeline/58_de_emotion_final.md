# De-Emotion Overhaul — final status (2026-08-04)

## Wave 1 (Phase 0–1)
- Baseline: 4151 passed, 1 skipped (`58_de_emotion_baseline.md`)
- Claude/news/F&G/FOMO stripped from live decision path
- `PORTFOLIO_CYCLE` rename; NewsScanner deleted; purity tests added
- Exit: 4045 passed, 1 skipped (deleted obsolete sentiment tests)

## Phase 2
- Deleted `.agents/skills/` + pruned `.claude/skills/` to governance 5 + `strategy-research-wiring`
- Root `skills/` (7) + `.claude/agents/` (9) kept
- Safe-now junk + orphaned Claude runners removed; `TradingBot.bat` cleaned
- See `58_de_emotion_phase2.md`

## Phases 3–5
- Docs truth-pass: OPERATING_PLAN, README, architecture, CLAUDE.md; Jekyll catalogs deleted
- Dual WR floors documented (0.65 vs 0.55) without changing constants
- Ledger evidence-loss note on MTSI row; artifact commit rule in after-cost-screening + CONTRIBUTING
- `docs/SELF_UPGRADE_LOOP.md`; in-process 6h `promotion_funnel` job
- `tests/test_de_emotion_audit.py` (purity, gates, census=10, probes, checksums)

## Phase D (monolith decomposition)
| Phase | Result |
|-------|--------|
| D0 | AST goldens + safety hashes + monkeypatch inventory |
| D1 | `config/` package (facade via `__init__.py`) |
| D2 | `dashboard/` package |
| D3 | `core/scoring/` + `mcp_brain.py` facade |
| D4 | `core/order_mgmt/` mixins + `order_manager.py` facade |
| D5 | `core/engine/` mixins + `bot_engine.py` facade |

## Final pytest
**4068 passed, 1 skipped** (2026-08-04)

Non-negotiables held: promotion/entry_policy/kill/live/risk untouched for loosening;
`SIGNAL_SOURCE` default remains `tsmom`; CONTROLLED_LIVE not activated.
