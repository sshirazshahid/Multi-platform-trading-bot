# PR draft — overhaul/de-emotion

**Compare / open PR:** https://github.com/sshirazshahid/Multi-platform-trading-bot/compare/main...overhaul/de-emotion?expand=1

**Title:** `overhaul/de-emotion: blueprint + log-only SHORT-bias + QA fixes`

## Summary
- Deep-research trading-system blueprint covering setup, design, MCP/tooling, monitoring, stale/stuck positions, TA/backtest/sentiment, futures scalp + spot, strategy lifecycle, and continuous rebuild (`docs/research/deep-research_trading_system_blueprint_2026-08-05.md`).
- Prereg-61 log-only F+G / long-liq SHORT-bias recorder (`core/regime_short_bias.py` + hourly schtask) — never authorizes live shorts; De-Emotion purity gate extended.
- Includes prior overhaul/de-emotion work already on this branch (Mission Control QA auth fixes, stuck-email fix, Phase cleanup).

## Test plan
- [x] `pytest tests/test_regime_short_bias.py tests/test_decision_path_purity.py` (7 passed)
- [ ] Smoke: `python scripts/record_regime_short_bias.py --dry-run`
- [ ] Confirm Mission Control login still works after QA fixes
- [ ] Confirm no decision-path import of `core.regime_short_bias`

## Documentation
- Blueprint: `docs/research/deep-research_trading_system_blueprint_2026-08-05.md`
- Pipeline: `_workspace/strategy_pipeline/61_*` and `62_*`
- CHANGELOG Unreleased updated

## Ship notes
- Branch pushed: `abfb88c`
- `gh` installed but not authenticated — run `gh auth login` then `gh pr create --base main --head overhaul/de-emotion --title "..." --body-file docs/research/PR_DRAFT_overhaul_de-emotion.md`
- No repo `VERSION` file (gstack 4-digit bump skipped)
