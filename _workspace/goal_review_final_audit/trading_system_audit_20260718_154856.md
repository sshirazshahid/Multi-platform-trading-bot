# Trading System Audit

Generated: 2026-07-18T15:48:56.993463+00:00
Verdict: **NOT_READY**

| Status | Check | Detail |
| --- | --- | --- |
| PASS | config.mode | OPERATING_MODE=PAPER, DRY_RUN=True |
| PASS | config.signal_source | SIGNAL_SOURCE=mcp |
| WARN | live_gate.readiness | CONTROLLED_LIVE is not armed; this is expected for research/PAPER |
| PASS | secrets.env_file | .env is ignored by git |
| PASS | secrets.tracked_scan | no tracked hardcoded secret assignments detected |
| PASS | runtime.processes | runtime process layout is sane |
| PASS | feeds.forward_harvesters | forward feed harvesters are running and status files are fresh |
| PASS | runtime.heartbeat | heartbeat age is 0.4 minutes |
| PASS | warehouse.closed_trades | 2304 closed trades in warehouse |
| FAIL | strategy.paper_30d_expectancy | 30d paper performance is not live-ready |
| PASS | warehouse.open_rows | 0 open warehouse rows, 0 older than 24h |
| WARN | strategy.loss_forensics | last 24h paper PnL is -0.6778 |
| PASS | warehouse.integrity | warehouse trade keys and lifecycle fields are sane |
| PASS | execution.paper_live_parity | paper/live pessimistic execution parity is covered |
| FAIL | strategy.promotion_gate | strict promotion-readiness gate blocks live promotion |
| PASS | confluence.paper_log | confluence paper CSV has no exact duplicate closes |
| FAIL | learning.report | latest learning report is negative; do not promote live |
| FAIL | model.pointer.futures | latest model pointer rejected: latest pointer missing ModelManifest |
| WARN | model.pointer.spot | latest model pointer missing for spot |
| PASS | automation.retrain | stale-model retrain automation scripts are present |
