# /daily-sync

Morning briefing for the trading bot. All checks are READ-ONLY — never restart, kill, or modify the bot process (watchdog is in-process; kill ≠ respawn).

1. Engine alive: `Get-Process python` and freshness of the newest file in `logs/` (heartbeat age). Report uptime impression, do not bounce the process.
2. Status snapshot: `venv\Scripts\python.exe main.py --status` (documented no-engine-start path).
3. Mode sanity: `OPERATING_MODE` in `.env`; `is_halted`/`halt_reason`/`daily_pnl`/`trades_today` in `data/risk_state.json`; flag if `data/review_required.json` exists.
4. Wallets: read `data/profiles/*/wallet.json` — per-exchange `balances` per profile, vs `start`.
5. Positions: `data/positions.json` — `open` count, oldest `open_time` age, any `_sl_failed`.
6. Trades today: filter `data/compliance/trades_<YYYY-MM>.csv` rows to today (UTC) — count and summed `pnl_usdt`.
7. Experiments: `scripts/experiments.json` — each experiment's `label`, elapsed time since `start_ts` (epoch seconds; 0 = NOT_STARTED), and whether `min_n` is plausibly reached.
8. Scheduled tasks: `schtasks /query /fo csv | Select-String "TradingBot"` — flag any not "Ready" or with stale next-run.
9. Open decisions: list `data/decisions/*.md` whose Status is `open`.
10. Output one screen: engine status, PnL today, open risk, blockers, suggested next actions. Save a copy to `reports/daily_sync_<YYYY-MM-DD>.md`.
