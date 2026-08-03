# Operating Plan — what to do now (plain English)

You are not a full-time trader, so this is deliberately simple and conservative.
Goal of this phase: find out, with REAL paper evidence, whether the one edge holds —
before risking a cent.

## What you actually have
- **One in-sample backtest number that is NOT yet validated:** the confluence engine.
  The real saved figures (`reports/ensemble_backtest.json`) are **profit factor 1.65,
  ~37% hit target, ~43% win, +0.75%/trade** — corrected 2026-06-08; an earlier draft of
  this file overstated them as 1.73 / 44% / 49%, which appear in no artifact. Three hard
  caveats: it is a single ~2-month **in-sample** window (no out-of-sample / walk-forward),
  it was the **best of ~9 mode×timeframe candidates** (selection bias), and returns are
  **not beta-adjusted** — a trend-follower mechanically prints positive in a trending
  window, so this could be trend/direction beta, not alpha. The live paper forward-test so
  far is **net-negative and failing the gate below** (small sample). Treat as UNPROVEN.
- **Everything else is honestly flagged:** mean-reversion / PCA / pairs / grid / DCA all
  backtested NEGATIVE in the current trending regime — built, gated, OFF by default.
  HMM regime, VPIN, execution planner = tools, not money-makers.
- It runs **pure price/quant** (no sentiment/fear/greed), **paper-only**, and never
  places real orders or blocks your trades.

## Do this (2–3 weeks)
1. `cd D:\Downloads\Trading_Bot`  then  `venv\Scripts\python.exe main.py`  — leave
   it running (the supervisor respawns it; `TradingBot.bat` is the menu front end).
   It trades paper-only and logs to `logs\bot_<date>.log` and the warehouse.
   (The old `run_confluence_paper.bat` step was removed: its runner script had
   already been deleted, so the batch file only printed "disabled".)
2. Each morning, glance at the daily scan briefing (the scheduled task), or run
   `python -m quant_suite.daily_scan`.
3. Don't touch the strategy parameters. Let it gather an honest, untampered sample.

## How to judge it (after ~30–50 closed paper trades)
- Win rate ~45%+ AND profit factor > 1.3 AND positive expectancy  ->  edge plausibly real.
- Below that  ->  the backtest edge did NOT survive live conditions. Do not trade it.
- Either outcome is a win: you learned the truth for $0.

## Hard guardrails BEFORE you ever consider real money (not yet)
- Re-enable the circuit breakers (`risk_manager.is_halted` currently returns False).
- Fix the stop-loss clamp (realized stops were ~0.89% vs the 1.5% floor).
- Decide the shorts policy (the `UNBLOCK_ALL` switch makes shorts always fire).
- Start microscopic (0.25–0.5% risk/trade) and scale only after live-paper parity.
Run `python -m quant_suite.preflight_check` — it must say `live_ready: True` first.

## What NOT to do
- Don't add more strategies, indicators, or data feeds — it won't raise the edge,
  it raises overfitting and complexity.
- Don't go live on backtest numbers.
- Don't override the discipline (selective entries, fixed risk) chasing more trades.

## The honest bottom line
This is a disciplined research + paper framework with **no validated edge yet** — not a
money machine, and I won't pretend otherwise. So far its biggest value is keeping you OUT
of negative-EV trades. Forward-test, judge on evidence, and we improve from real results.
(2026-06-08 audit: confluence is an in-sample/beta artifact failing its own forward test;
every other strategy and the candlestick/sweet-spot screens are NO_EDGE after cost.)
