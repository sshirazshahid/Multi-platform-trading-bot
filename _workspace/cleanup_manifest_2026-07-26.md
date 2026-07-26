# Cleanup Deletion Manifest - 2026-07-26

## PHASE 2 — owner-approved bulk deletions ("delete them", 2026-07-26)
Pre-checks: 0 git-tracked files under all four targets; 0 runtime references in core/, scripts/, config.py, main.py; 0 scheduled tasks referencing them. Bot untouched (runs from venv/, core/).
| Path | Size | Rationale |
|---|---|---|
| models/kronos/ | 436.2 MB | Kronos foundation-model weights — family NO_EDGE (ledger 2026-05-29, −EV both directions); ledger forbids re-litigation, so weights are dead freight |
| ntrader/ | 529.1 MB | NautilusTrader Phase 0+1 parity experiment (2026-07-13 wk) incl. its own venv; dormant since, unreferenced |
| .repo_study/ | 304.2 MB | Study/scratch tree, unreferenced |
| .uv-cache/ | 200.8 MB | Package-manager cache, regenerates on demand |
**Phase 2 total: 1,470.4 MB** (session total with Phase 1: ~1,588.8 MB)

## PHASE 1 (agent-executed safe classes)
**Total bytes freed: 124142988 bytes (118.39 MB)**
- S1 caches: 60320325 bytes (57.53 MB), 316 directories
- S2 logs older than 7 days: 63822663 bytes (60.87 MB), 39 files
- S3 orphaned *.tmp: 0 bytes (0 files found repo-wide)
- S4 untracked scripts: nothing deleted (see KEEP list)

Age cutoff for logs: rolling now-minus-7-days at execution time (2026-07-26 06:11Z local), stricter than the inventory calendar cutoff of 2026-07-19. Newest deleted log: logs/bot_2026-07-18.log.zip, mtime 2026-07-19 00:00:32, verified strictly older than the cutoff before deletion. Zero git-tracked files were deleted (git ls-files over the logs directory returned empty; Python/pytest/ruff cache dirs are gitignored by nature). 0 deletion failures.

## Deleted - S2 logs older than 7 days (39 files)

| Path | Bytes |
|---|---|
| logs\restart_2026-04-20.log | 25284 |
| logs\skill_improvement.log | 168 |
| logs\restart_stderr.log | 1480 |
| logs\kronos_xsec_run.log | 1391 |
| logs\restart_stdout.log | 318107 |
| logs\weekly_research.log | 5143 |
| logs\liquidations_harvester.log | 10161 |
| logs\bot_2026-07-01.log.zip | 305126 |
| logs\bot_2026-07-02.log.zip | 523046 |
| logs\bot_2026-07-03.log | 3930242 |
| logs\bot_2026-07-03.log.zip | 206193 |
| logs\bot_2026-07-04.log | 747418 |
| logs\bot_2026-07-05.log.zip | 378573 |
| logs\bot_2026-07-06.log | 5207568 |
| logs\bot_2026-07-06.log.zip | 469323 |
| logs\bot_2026-07-07.log | 4660800 |
| logs\bot_2026-07-08.log | 862321 |
| logs\bot_2026-07-08.log.zip | 56551 |
| logs\bot_2026-07-09.log | 4220962 |
| logs\bot_2026-07-09.log.zip | 232893 |
| logs\scan_rsi_vol_full.log | 913 |
| logs\scan_7d.log | 335 |
| logs\bot_2026-07-10.log | 5471317 |
| logs\bot_2026-07-10.log.zip | 468582 |
| logs\bot_2026-07-11.log | 6043112 |
| logs\bot_2026-07-11.log.zip | 385914 |
| logs\bot_2026-07-12.log | 5705300 |
| logs\bot_2026-07-12.log.zip | 371978 |
| logs\bot_2026-07-13.log | 5361187 |
| logs\bot_2026-07-13.log.zip | 318884 |
| logs\bot_2026-07-14.log.zip | 255000 |
| logs\bot_2026-07-15.log | 2366429 |
| logs\bot_2026-07-15.log.zip | 115199 |
| logs\bot_2026-07-16.log | 4951420 |
| logs\bot_2026-07-16.log.zip | 255376 |
| logs\bot_2026-07-17.log | 4039407 |
| logs\bot_2026-07-17.log.zip | 220658 |
| logs\bot_2026-07-18.log | 4980656 |
| logs\bot_2026-07-18.log.zip | 348246 |

## Deleted - S1 cache directories (316 dirs: __pycache__, .pytest_cache, .ruff_cache)

| Path | Bytes |
|---|---|
| .pytest_cache | 332280 |
| .ruff_cache | 497911 |
| __pycache__ | 566277 |
| .repo_study\__pycache__ | 16953 |
| .repo_study\FinceptTerminal\.github\scripts\__pycache__ | 8003 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\__pycache__ | 4920847 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\deepagents\__pycache__ | 64625 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\finagent_core\__pycache__ | 311859 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\finagent_core\agentic\__pycache__ | 125316 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\finagent_core\modules\__pycache__ | 260880 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\finagent_core\registries\__pycache__ | 72911 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\finagent_core\tests\__pycache__ | 35939 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\finagent_core\tools\__pycache__ | 24776 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\__pycache__ | 36542 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\agents\__pycache__ | 43844 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\agents\tools\__pycache__ | 59292 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\evaluation\__pycache__ | 73771 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\guardrails\__pycache__ | 70421 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\knowledge\__pycache__ | 97919 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\memory\__pycache__ | 100266 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\models\__pycache__ | 22698 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\organization\__pycache__ | 70655 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\reasoning\__pycache__ | 83289 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\schemas\__pycache__ | 59549 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\strategies\__pycache__ | 88877 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\teams\__pycache__ | 22743 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\tests\__pycache__ | 36916 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\tracing\__pycache__ | 59980 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\utils\__pycache__ | 51465 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\hedgeFundAgents\renaissance_technologies_hedge_fund_agent\workflows\__pycache__ | 100337 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\rdagents\__pycache__ | 105958 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\tests\agentic\__pycache__ | 19897 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agents\_tools\__pycache__ | 108531 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agno_trading\__pycache__ | 1193 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agno_trading\agents\__pycache__ | 725 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agno_trading\core\__pycache__ | 92927 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agno_trading\db\__pycache__ | 36014 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agno_trading\framework\__pycache__ | 81400 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agno_trading\tools\__pycache__ | 38385 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\agno_trading\utils\__pycache__ | 14768 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\ai_quant_lab\__pycache__ | 394026 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\alpha_arena\__pycache__ | 13369 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\alpha_arena\core\__pycache__ | 263 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\alpha_arena\types\__pycache__ | 343 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\alpha_arena\utils\__pycache__ | 214 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\__pycache__ | 213551 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\alternateInvestment\__pycache__ | 730025 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\backtesting\backtestingpy\__pycache__ | 205506 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\backtesting\base\__pycache__ | 46795 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\backtesting\bt\__pycache__ | 147707 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\backtesting\fasttrade\__pycache__ | 190803 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\backtesting\fincept\__pycache__ | 26552 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\backtesting\vectorbt\__pycache__ | 442601 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\backtesting\zipline\__pycache__ | 268791 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\__pycache__ | 3330 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\advanced_analytics\__pycache__ | 41205 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\deal_comparison\__pycache__ | 28265 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\deal_database\__pycache__ | 107768 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\deal_structure\__pycache__ | 71387 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\fairness_opinion\__pycache__ | 52343 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\industry_metrics\__pycache__ | 69735 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\lbo\__pycache__ | 50299 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\merger_models\__pycache__ | 54970 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\startup_valuation\__pycache__ | 66451 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\synergies\__pycache__ | 64707 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\corporateFinance\valuation\__pycache__ | 115112 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\derivatives\__pycache__ | 228951 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\economics\__pycache__ | 461067 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\equityInvestment\__pycache__ | 2356 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\equityInvestment\base\__pycache__ | 68266 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\equityInvestment\company_analysis\__pycache__ | 94873 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\equityInvestment\equity_valuation\__pycache__ | 153158 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\equityInvestment\market_analysis\__pycache__ | 127964 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\equityInvestment\utils\__pycache__ | 27099 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\ffn_wrapper\__pycache__ | 112094 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\finanicalanalysis\__pycache__ | 1949 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\finanicalanalysis\core\__pycache__ | 50914 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\finanicalanalysis\specialized_analysis\__pycache__ | 415724 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\finanicalanalysis\statement_analyzers\__pycache__ | 170825 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\finrl\__pycache__ | 150008 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\fixedIncome\__pycache__ | 310360 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\fortitudo_tech_wrapper\__pycache__ | 167715 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\functime_wrapper\__pycache__ | 317353 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\gluonts_wrapper\__pycache__ | 53754 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\gs_quant_wrapper\__pycache__ | 378285 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\options\__pycache__ | 89839 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\pmdarima_wrapper\__pycache__ | 34287 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\portfolioManagement\__pycache__ | 396767 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\pypme_wrapper\__pycache__ | 13828 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\pyportfolioopt_wrapper\__pycache__ | 90337 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\python_skfolio_lib\__pycache__ | 416406 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\py_vollib_wrapper\__pycache__ | 18954 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\quant\__pycache__ | 130999 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\statsmodels_wrapper\__pycache__ | 261469 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\talipp_wrapper\__pycache__ | 80087 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\technical_analysis\__pycache__ | 14099 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\tsmoothie_wrapper\__pycache__ | 13795 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\Analytics\vnpy_wrapper\__pycache__ | 25705 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\exchange\__pycache__ | 139702 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\i18n\__pycache__ | 11020 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\mcp\__pycache__ | 359 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\mcp\edgar\__pycache__ | 102762 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\technicals\__pycache__ | 57574 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\vision_quant\__pycache__ | 73494 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\vision_quant\models\__pycache__ | 25799 |
| .repo_study\FinceptTerminal\fincept-qt\scripts\voice\__pycache__ | 51078 |
| .repo_study\FinceptTerminal\fincept-qt\src\python\__pycache__ | 13395 |
| .repo_study\freqtrade\build_helpers\__pycache__ | 11008 |
| .repo_study\freqtrade\freqtrade\__pycache__ | 180092 |
| .repo_study\freqtrade\freqtrade\commands\__pycache__ | 107334 |
| .repo_study\freqtrade\freqtrade\configuration\__pycache__ | 86380 |
| .repo_study\freqtrade\freqtrade\config_schema\__pycache__ | 29820 |
| .repo_study\freqtrade\freqtrade\enums\__pycache__ | 13351 |
| .repo_study\freqtrade\freqtrade\exchange\__pycache__ | 396589 |
| .repo_study\freqtrade\freqtrade\freqai\__pycache__ | 156957 |
| .repo_study\freqtrade\freqtrade\freqai\base_models\__pycache__ | 42327 |
| .repo_study\freqtrade\freqtrade\freqai\prediction_models\__pycache__ | 66569 |
| .repo_study\freqtrade\freqtrade\freqai\RL\__pycache__ | 66911 |
| .repo_study\freqtrade\freqtrade\freqai\tensorboard\__pycache__ | 10492 |
| .repo_study\freqtrade\freqtrade\freqai\torch\__pycache__ | 31615 |
| .repo_study\freqtrade\freqtrade\ft_types\__pycache__ | 6154 |
| .repo_study\freqtrade\freqtrade\leverage\__pycache__ | 4525 |
| .repo_study\freqtrade\freqtrade\loggers\__pycache__ | 20799 |
| .repo_study\freqtrade\freqtrade\mixins\__pycache__ | 2415 |
| .repo_study\freqtrade\freqtrade\optimize\__pycache__ | 109074 |
| .repo_study\freqtrade\freqtrade\optimize\analysis\__pycache__ | 44091 |
| .repo_study\freqtrade\freqtrade\optimize\hyperopt\__pycache__ | 62638 |
| .repo_study\freqtrade\freqtrade\optimize\hyperopt_loss\__pycache__ | 25937 |
| .repo_study\freqtrade\freqtrade\optimize\optimize_reports\__pycache__ | 64990 |
| .repo_study\freqtrade\freqtrade\optimize\space\__pycache__ | 5279 |
| .repo_study\freqtrade\freqtrade\persistence\__pycache__ | 177713 |
| .repo_study\freqtrade\freqtrade\plot\__pycache__ | 29000 |
| .repo_study\freqtrade\freqtrade\plugins\__pycache__ | 18959 |
| .repo_study\freqtrade\freqtrade\plugins\pairlist\__pycache__ | 147913 |
| .repo_study\freqtrade\freqtrade\plugins\protections\__pycache__ | 28073 |
| .repo_study\freqtrade\freqtrade\resolvers\__pycache__ | 41935 |
| .repo_study\freqtrade\freqtrade\rpc\__pycache__ | 244051 |
| .repo_study\freqtrade\freqtrade\rpc\api_server\__pycache__ | 138852 |
| .repo_study\freqtrade\freqtrade\rpc\api_server\ws\__pycache__ | 21293 |
| .repo_study\freqtrade\freqtrade\strategy\__pycache__ | 145681 |
| .repo_study\freqtrade\freqtrade\system\__pycache__ | 3613 |
| .repo_study\freqtrade\freqtrade\templates\__pycache__ | 36223 |
| .repo_study\freqtrade\freqtrade\util\__pycache__ | 27003 |
| .repo_study\freqtrade\freqtrade\util\migrations\__pycache__ | 12018 |
| .repo_study\freqtrade\ft_client\freqtrade_client\__pycache__ | 27439 |
| .repo_study\freqtrade\ft_client\test_client\__pycache__ | 10075 |
| .repo_study\freqtrade\scripts\__pycache__ | 14725 |
| .repo_study\freqtrade\tests\__pycache__ | 308124 |
| .repo_study\freqtrade\tests\commands\__pycache__ | 77736 |
| .repo_study\freqtrade\tests\exchange\__pycache__ | 495802 |
| .repo_study\freqtrade\tests\exchange_online\__pycache__ | 54619 |
| .repo_study\freqtrade\tests\freqai\__pycache__ | 75032 |
| .repo_study\freqtrade\tests\freqai\test_models\__pycache__ | 7707 |
| .repo_study\freqtrade\tests\freqtradebot\__pycache__ | 344975 |
| .repo_study\freqtrade\tests\leverage\__pycache__ | 5447 |
| .repo_study\freqtrade\tests\optimize\__pycache__ | 287808 |
| .repo_study\freqtrade\tests\persistence\__pycache__ | 175510 |
| .repo_study\freqtrade\tests\plugins\__pycache__ | 158925 |
| .repo_study\freqtrade\tests\rpc\__pycache__ | 400971 |
| .repo_study\freqtrade\tests\strategy\__pycache__ | 111152 |
| .repo_study\freqtrade\tests\strategy\strats\__pycache__ | 58919 |
| .repo_study\freqtrade\tests\strategy\strats\broken_strats\__pycache__ | 5679 |
| .repo_study\freqtrade\tests\strategy\strats\lookahead_bias\__pycache__ | 2744 |
| .repo_study\freqtrade\tests\util\__pycache__ | 44466 |
| .repo_study\QuantDinger\backend_api_python\__pycache__ | 6001 |
| .repo_study\QuantDinger\backend_api_python\app\__pycache__ | 18762 |
| .repo_study\QuantDinger\backend_api_python\app\data_providers\__pycache__ | 125630 |
| .repo_study\QuantDinger\backend_api_python\app\data_sources\__pycache__ | 239984 |
| .repo_study\QuantDinger\backend_api_python\app\openapi\__pycache__ | 20642 |
| .repo_study\QuantDinger\backend_api_python\app\openapi\routes\__pycache__ | 2717 |
| .repo_study\QuantDinger\backend_api_python\app\openapi\schemas\__pycache__ | 4268 |
| .repo_study\QuantDinger\backend_api_python\app\routes\__pycache__ | 784649 |
| .repo_study\QuantDinger\backend_api_python\app\routes\agent_v1\__pycache__ | 70671 |
| .repo_study\QuantDinger\backend_api_python\app\services\__pycache__ | 1637205 |
| .repo_study\QuantDinger\backend_api_python\app\services\alpaca_trading\__pycache__ | 34960 |
| .repo_study\QuantDinger\backend_api_python\app\services\bot_scripts\__pycache__ | 17421 |
| .repo_study\QuantDinger\backend_api_python\app\services\experiment\__pycache__ | 101441 |
| .repo_study\QuantDinger\backend_api_python\app\services\grid\__pycache__ | 194757 |
| .repo_study\QuantDinger\backend_api_python\app\services\ibkr_trading\__pycache__ | 24324 |
| .repo_study\QuantDinger\backend_api_python\app\services\live_trading\__pycache__ | 612585 |
| .repo_study\QuantDinger\backend_api_python\app\services\mt5_trading\__pycache__ | 34905 |
| .repo_study\QuantDinger\backend_api_python\app\services\pending_orders\__pycache__ | 34021 |
| .repo_study\QuantDinger\backend_api_python\app\services\usdt_payment\__pycache__ | 45739 |
| .repo_study\QuantDinger\backend_api_python\app\services\usdt_payment\watchers\__pycache__ | 44254 |
| .repo_study\QuantDinger\backend_api_python\app\utils\__pycache__ | 181028 |
| .repo_study\QuantDinger\backend_api_python\scripts\__pycache__ | 134943 |
| .repo_study\QuantDinger\backend_api_python\tests\__pycache__ | 551959 |
| .repo_study\QuantDinger\mcp_server\src\quantdinger_mcp\__pycache__ | 31973 |
| .repo_study\QuantDinger\mcp_server\tests\__pycache__ | 7806 |
| .repo_study\QuantDinger\scripts\__pycache__ | 22710 |
| .repo_study\TradingAgents\__pycache__ | 1536 |
| .repo_study\TradingAgents\cli\__pycache__ | 89674 |
| .repo_study\TradingAgents\scripts\__pycache__ | 7530 |
| .repo_study\TradingAgents\tests\__pycache__ | 220576 |
| .repo_study\TradingAgents\tradingagents\__pycache__ | 4899 |
| .repo_study\TradingAgents\tradingagents\agents\__pycache__ | 14413 |
| .repo_study\TradingAgents\tradingagents\agents\analysts\__pycache__ | 24916 |
| .repo_study\TradingAgents\tradingagents\agents\managers\__pycache__ | 6015 |
| .repo_study\TradingAgents\tradingagents\agents\researchers\__pycache__ | 6971 |
| .repo_study\TradingAgents\tradingagents\agents\risk_mgmt\__pycache__ | 11989 |
| .repo_study\TradingAgents\tradingagents\agents\trader\__pycache__ | 2397 |
| .repo_study\TradingAgents\tradingagents\agents\utils\__pycache__ | 44599 |
| .repo_study\TradingAgents\tradingagents\dataflows\__pycache__ | 94972 |
| .repo_study\TradingAgents\tradingagents\graph\__pycache__ | 48624 |
| .repo_study\TradingAgents\tradingagents\llm_clients\__pycache__ | 38480 |
| .repo_study\Vibe-Trading\agent\__pycache__ | 202007 |
| .repo_study\Vibe-Trading\agent\backtest\__pycache__ | 86810 |
| .repo_study\Vibe-Trading\agent\backtest\engines\__pycache__ | 123390 |
| .repo_study\Vibe-Trading\agent\backtest\loaders\__pycache__ | 108993 |
| .repo_study\Vibe-Trading\agent\backtest\optimizers\__pycache__ | 18151 |
| .repo_study\Vibe-Trading\agent\cli\__pycache__ | 349866 |
| .repo_study\Vibe-Trading\agent\cli\commands\__pycache__ | 48435 |
| .repo_study\Vibe-Trading\agent\cli\components\__pycache__ | 21676 |
| .repo_study\Vibe-Trading\agent\cli\ui\__pycache__ | 45192 |
| .repo_study\Vibe-Trading\agent\cli\utils\__pycache__ | 7299 |
| .repo_study\Vibe-Trading\agent\scripts\__pycache__ | 18831 |
| .repo_study\Vibe-Trading\agent\src\__pycache__ | 34990 |
| .repo_study\Vibe-Trading\agent\src\agent\__pycache__ | 82639 |
| .repo_study\Vibe-Trading\agent\src\api\__pycache__ | 22509 |
| .repo_study\Vibe-Trading\agent\src\core\__pycache__ | 14189 |
| .repo_study\Vibe-Trading\agent\src\factors\__pycache__ | 113934 |
| .repo_study\Vibe-Trading\agent\src\factors\zoo\__pycache__ | 157 |
| .repo_study\Vibe-Trading\agent\src\factors\zoo\academic\__pycache__ | 19062 |
| .repo_study\Vibe-Trading\agent\src\factors\zoo\alpha101\__pycache__ | 278310 |
| .repo_study\Vibe-Trading\agent\src\factors\zoo\gtja191\__pycache__ | 369655 |
| .repo_study\Vibe-Trading\agent\src\factors\zoo\qlib158\__pycache__ | 189113 |
| .repo_study\Vibe-Trading\agent\src\goal\__pycache__ | 65811 |
| .repo_study\Vibe-Trading\agent\src\hypotheses\__pycache__ | 31867 |
| .repo_study\Vibe-Trading\agent\src\live\__pycache__ | 115505 |
| .repo_study\Vibe-Trading\agent\src\live\extractors\__pycache__ | 1933 |
| .repo_study\Vibe-Trading\agent\src\live\mandate\__pycache__ | 34746 |
| .repo_study\Vibe-Trading\agent\src\live\runtime\__pycache__ | 127533 |
| .repo_study\Vibe-Trading\agent\src\memory\__pycache__ | 16453 |
| .repo_study\Vibe-Trading\agent\src\providers\__pycache__ | 43440 |
| .repo_study\Vibe-Trading\agent\src\security\__pycache__ | 6950 |
| .repo_study\Vibe-Trading\agent\src\session\__pycache__ | 65186 |
| .repo_study\Vibe-Trading\agent\src\shadow_account\__pycache__ | 96227 |
| .repo_study\Vibe-Trading\agent\src\skills\ashare-pre-st-filter\scripts\__pycache__ | 28243 |
| .repo_study\Vibe-Trading\agent\src\skills\candlestick\__pycache__ | 26779 |
| .repo_study\Vibe-Trading\agent\src\skills\chanlun\__pycache__ | 9663 |
| .repo_study\Vibe-Trading\agent\src\skills\cross-market-strategy\__pycache__ | 4526 |
| .repo_study\Vibe-Trading\agent\src\skills\elliott-wave\__pycache__ | 15464 |
| .repo_study\Vibe-Trading\agent\src\skills\fundamental-filter\__pycache__ | 9038 |
| .repo_study\Vibe-Trading\agent\src\skills\harmonic\__pycache__ | 17492 |
| .repo_study\Vibe-Trading\agent\src\skills\ichimoku\__pycache__ | 8287 |
| .repo_study\Vibe-Trading\agent\src\skills\minute-analysis\__pycache__ | 7124 |
| .repo_study\Vibe-Trading\agent\src\skills\multi-factor\__pycache__ | 29328 |
| .repo_study\Vibe-Trading\agent\src\skills\okx-market\scripts\__pycache__ | 9743 |
| .repo_study\Vibe-Trading\agent\src\skills\pair-trading\__pycache__ | 6552 |
| .repo_study\Vibe-Trading\agent\src\skills\seasonal\__pycache__ | 6674 |
| .repo_study\Vibe-Trading\agent\src\skills\smc\__pycache__ | 8706 |
| .repo_study\Vibe-Trading\agent\src\skills\technical-basic\__pycache__ | 13467 |
| .repo_study\Vibe-Trading\agent\src\skills\tushare\scripts\__pycache__ | 7159 |
| .repo_study\Vibe-Trading\agent\src\skills\vnpy-export\scripts\__pycache__ | 5922 |
| .repo_study\Vibe-Trading\agent\src\skills\volatility\__pycache__ | 7541 |
| .repo_study\Vibe-Trading\agent\src\swarm\__pycache__ | 135626 |
| .repo_study\Vibe-Trading\agent\src\tools\__pycache__ | 362734 |
| .repo_study\Vibe-Trading\agent\src\trading\__pycache__ | 31161 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\__pycache__ | 229 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\alpaca\__pycache__ | 31904 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\binance\__pycache__ | 37549 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\futu\__pycache__ | 46546 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\ibkr\__pycache__ | 29686 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\longbridge\__pycache__ | 36469 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\okx\__pycache__ | 38218 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\robinhood\__pycache__ | 10493 |
| .repo_study\Vibe-Trading\agent\src\trading\connectors\tiger\__pycache__ | 36389 |
| .repo_study\Vibe-Trading\agent\tests\__pycache__ | 1472206 |
| .repo_study\Vibe-Trading\agent\tests\factors\__pycache__ | 106111 |
| .repo_study\Vibe-Trading\agent\tests\fixtures\__pycache__ | 7361 |
| .repo_study\Vibe-Trading\wiki\scripts\__pycache__ | 18730 |
| Codex\__pycache__ | 21397 |
| Codex\bot\__pycache__ | 45280 |
| Codex\bot\exchanges\__pycache__ | 41107 |
| Codex\bot\execution\__pycache__ | 16437 |
| Codex\bot\forward\__pycache__ | 9926 |
| Codex\bot\gates\__pycache__ | 30033 |
| Codex\bot\portfolio\__pycache__ | 2825 |
| Codex\bot\risk\__pycache__ | 8477 |
| Codex\bot\scoring\__pycache__ | 6605 |
| Codex\bot\storage\__pycache__ | 34210 |
| Codex\bot\strategies\__pycache__ | 27416 |
| Codex\bot\webhooks\__pycache__ | 16702 |
| Codex\research\__pycache__ | 150430 |
| Codex\tests\__pycache__ | 206032 |
| core\__pycache__ | 2828959 |
| core\agents\__pycache__ | 279040 |
| core\data_feeds\__pycache__ | 86085 |
| core\data_sources\__pycache__ | 49174 |
| core\decision\__pycache__ | 46728 |
| core\selfmod\__pycache__ | 8560 |
| core\signals\__pycache__ | 45764 |
| exchanges\__pycache__ | 128869 |
| mcp_server\__pycache__ | 21389 |
| research\__pycache__ | 644709 |
| scripts\__pycache__ | 1596651 |
| scripts\hooks\__pycache__ | 8858 |
| scripts\tests\__pycache__ | 141779 |
| skills\exchange-connectivity\scripts\__pycache__ | 18880 |
| skills\exchange-connectivity\scripts\tests\__pycache__ | 19940 |
| skills\futures-universe-edge-research\scripts\__pycache__ | 21049 |
| skills\futures-universe-edge-research\scripts\tests\__pycache__ | 74960 |
| skills\tp-precision-engine\scripts\__pycache__ | 30841 |
| skills\tp-precision-engine\scripts\tests\__pycache__ | 35777 |
| skills\trading-backtest-validation\scripts\__pycache__ | 13863 |
| skills\trading-backtest-validation\scripts\tests\__pycache__ | 29761 |
| skills\trading-monitoring\scripts\__pycache__ | 12199 |
| skills\trading-monitoring\scripts\tests\__pycache__ | 27832 |
| skills\trading-risk-management\scripts\__pycache__ | 11182 |
| skills\trading-risk-management\scripts\tests\__pycache__ | 24878 |
| skills\windows-bot-deployment\scripts\__pycache__ | 12665 |
| skills\windows-bot-deployment\scripts\tests\__pycache__ | 29477 |
| strategies\__pycache__ | 65053 |
| strategies\legacy\__pycache__ | 82432 |
| tests\__pycache__ | 19330836 |
| utils\__pycache__ | 125277 |

## KEPT (with reasons)

- logs/ - 22 files with mtime within the last 7 days: DENYLIST (live bot logs).
- Cache dirs inside virtualenvs (.venv, ntrader/venv_nt), .uv-cache, .claude, .agents, data/, _workspace/, docs/, reports/, config/, prompts/: excluded fail-safe because their parent trees are DENYLIST (venvs / provenance / runtime state of the live bot). This is why S1 freed 57.5 MB vs the inventory estimate of 124.7 MB - the difference sits inside denylisted trees and was deliberately not touched.
- scripts/repair_test_pollution.py - S4 candidate SKIPPED: the inventory itself leans KEEP (incident tooling from the 2026-07-18 test-pollution repair; owner confirmation required before removal). Cost of keeping: 32 KB.
- scripts/backfill_pair_gaps.py - untracked but referenced (pair_indicator_dossier.py, core/research_brief.py; active FIT_WITH_GAPS remediation).
- scripts/harvest_cftc_tff_btc.py - C1 CFTC screen provenance (20_* artifacts, research/screen_cftc_options_pressure.py).
- scripts/harvest_binance_aggtrades_qh.py - C3/VPIN provenance (22_*, 35_* artifacts).
- scripts/harvest_deribit_chain_snapshots.py - C2 gamma-expiry harvester + tests/test_deribit_chain_snapshot_harvester.py.
- scripts/harvest_exchange_announcements.py - has tests/test_exchange_announcement_harvester.py.
- scripts/reconcile_virtual_wallet.py - phantom-wallet fix tooling (referenced in _workspace 26_/27_ artifacts).
- scripts/install_24x7_task.ps1 - referenced by tests/test_windows_24x7_assets.py, docs/setup.md, windows-bot-deployment skill.
- scripts/create_rebuild_backup.py - has tests/test_rebuild_backup.py.
- All hard-denylist trees (data/**, _workspace/**, journal/**, docs/**, reports/**, .claude/**, .agents/**, prompts/**, config/**, .remember/**, env files, git internals, all venvs), every tracked file, and all ~319 uncommitted in-flight working-tree paths: untouched.
- S3: no orphaned *.tmp files existed anywhere in the repo (verified scan excluding venv/git internals) - data/ohlcv_cache clean.
