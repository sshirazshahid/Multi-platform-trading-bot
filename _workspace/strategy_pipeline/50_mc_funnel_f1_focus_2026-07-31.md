# 50 — Mission Control schtask + 1h funnel watch + F1-only focus

**Date:** 2026-07-31  
**Owner directive:** a + b + c

## A) Mission Control survives reboot

- Script: `scripts/install_mission_control_task.ps1`
- Task: `TradingBot-MissionControl` (AtLogOn, RestartCount 999, IgnoreNew)
- Action: `venv\Scripts\python.exe scripts\run_mission_control.py` (cwd repo; token from `.env`)
- Verified: Status Running, `GET http://127.0.0.1:8787/api/health` → ok

## B) 1h open-funnel watch

- Snapshot tool: `scripts/watch_funnel_f1_snapshot.py`
- Log: `_workspace/strategy_pipeline/50_funnel_watch_2026-07-31.jsonl`
- Cadence: every 10 min × 6 ticks (agent loop)
- Success signal: post-restart window `econ_model_missing` stays ~0 on new OPEN attempts; drought_status may be `no_open_attempts` under F1-only (directional opens paused)

**Baseline (first snap):**  
`FUNNEL drought=no_open_attempts attempts=0 filled=0 econ_blocked=0 model_missing=0 | F1 status=idle_no_edge checks=1048 ok=0 fresh≈0.998`

## C) F1-only until funding turns

- `.env`: `APPROVED_PAPER_STRATEGIES=F1` (removed `mcp_registry,algo_det`)
- Launcher pins `ENTRY_POLICY` + `APPROVED_PAPER_STRATEGIES` from `.env`
- Bot restarted 23:23Z local; heartbeat pid=9548, profile MAX_FLOW_BAND, EconGate paper_fallback, EntryFloor 66
- AccBand geometry still ON for research scoring; **directional PAPER OPENs blocked** by entry allowlist
- F1 runner remains the only paper-eligible strategy; currently `idle_no_edge` (funding/contango) with healthy `feeds_fresh_rate`

### Restore AccBand directional PAPER (when funding clears + owner says so)

```
APPROVED_PAPER_STRATEGIES=F1,mcp_registry,algo_det
```

Then End/kill-tree/Run `TradingBot-24x7` (schtask End alone can orphan `main.py`).

## Honesty

- F1-only refuses AccBand −EV bleed while waiting for carry edge.
- Idle F1 under negative funding is correct — do not loosen thresholds.
- Mission Control / funnel watch are observability; they do not create edge.

## B) Watch COMPLETE — 6/6 ticks (2026-07-31 18:23–19:26Z)

| Snap | drought | attempts | model_missing | F1 | best net_edge |
|------|---------|----------|---------------|----|---------------|
| all 7 lines | `no_open_attempts` | 0 | **0** | `idle_no_edge` | GRT/USDT bybit ≈ **−32.3 bps** |

**Verdict**
- EconGate drought class is allowlist/idle, not `economic_gate_model_missing` (stayed 0 entire hour).
- F1 correctly refuses: contango_fail + funding_rate_le_0 dominate; feeds_fresh_rate ≈ 0.994–0.998.
- Zero OPEN attempts expected under F1-only while carry edge absent — do not reopen AccBand from this quiet window.
- Loop ended; **do not re-arm**.

Log: `50_funnel_watch_2026-07-31.jsonl` (7 snapshots = baseline + 6 ticks).
