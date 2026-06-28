# Self-Healing And Adaptive Strategy Control

This bot uses bounded autonomy. It can repair runtime support processes and
adapt PAPER machine-strategy configuration from backtest evidence. It does not
rewrite Python strategy code, bypass live gates, increase leverage, or promote
live trading by itself.

## Runtime Layers

1. `main.py` watchdog restarts the bot after fatal main-loop crashes.
2. `HealthWatchdog` alerts on stale heartbeat, feed failures, stale model
   pointers, loss streaks, and stuck positions.
3. `SelfHealingSupervisor` repairs missing/stale feed harvesters, runs stale
   model retraining wrappers, tests machine-strategy candidates, and writes a
   validated PAPER-only adaptive config.
4. `MachineSignal` hot-reloads `data/adaptive_machine_config.json` each cycle
   through `core.adaptive_config`, which rejects unknown keys and non-paper
   scopes.

## Manual Commands

Dry-run repair/adaptation plan:

```powershell
python scripts\self_heal.py --force --dry-run
```

Run only runtime repair:

```powershell
python scripts\self_heal.py --force --no-retrain --no-adapt
```

Run strategy adaptation research without process repair:

```powershell
python scripts\self_heal.py --force --no-repair --no-retrain
```

## Safety Rules

- Adaptive config applies only when `apply_scope` is `paper` or `paper_shadow`.
- Allowed knobs are limited to machine score thresholds, RR/ATR stop settings,
  detector weights, confirmation detectors, and EMA/RSI detector parameters.
- Live promotion remains controlled by `core.live_gate` and
  `core.strategy_readiness`.
- Candidate strategies must pass after-cost replay thresholds before the
  supervisor writes `data/adaptive_machine_config.json`.
