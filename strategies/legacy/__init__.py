"""strategies/legacy/ — research / backtest-only strategy classes.

These strategies are NOT wired into the live `bot_engine` path. They're
preserved here for:
  * `backtest.py` / `auto_backtest.py` — historical replay + parameter sweep
  * `core/auto_optimizer.py` — Sunday hyperparameter search

History (why each was retired):

  multi_tf.py          KILLED 2026-04-27 (Phase 10.1) — 58 trades / -$11.33
                        / 48% WR over 30 days. Negative edge while running
                        in parallel with claude_portfolio's futures
                        direction calls.
  supertrend.py        KILLED 2026-04-20 — 39 trades / -$45.18 / 41% WR.
                        Worst single contributor to all-time loss tape.
  trend_following.py   KILLED — 0% WR over 8 trades early April. Never
                        re-exported in package; backtest direct-import only.
  grid_trading.py      Never wired into live bot_engine path.
  scalping.py          Never wired into live bot_engine path.
  funding_rate_arb.py  Parked as Phase 5 alpha source; not wired.
  mean_reversion.py    Used only by core/auto_optimizer.py (research).

Active strategies remain in the parent `strategies/` directory:
  * dca_strategy.py     scheduled accumulator
  * rebalancing.py      scheduled rebalancer
  * rule_engine.py      generic rule executor

If reviving any legacy strategy for live trading, document the
attribution evidence and revert the corresponding `_DISABLED_LIVE_*`
gates (e.g. `core/strategy_selector._DISABLED_LIVE_STRATEGIES`).
"""
