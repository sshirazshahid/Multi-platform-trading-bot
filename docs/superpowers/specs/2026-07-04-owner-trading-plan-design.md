# Owner's Trading Plan + Manual with Dashboard Owner-View — Design

Date: 2026-07-04 · Status: approved design, pre-implementation
Owner objective (chosen): **prove it, then decide** — accumulate paper evidence to a
readable GO/NO-GO over ~6 months while the owner learns to operate the system.
Time budget (chosen): **~5 min daily + one 20-30 min weekly review**.
Delivery approach (chosen): **C — dashboard owner-view** + the plan documents.

## Context and problem

The trading system already exists and runs locally on Windows 11 (engine, carry
runner on Binance/Bybit/Bitget, risk rails, warehouse, watchdog, 2,400+ tests).
Months of falsification recorded NO after-cost edge in every directional pattern
family tested; the one evidence-backed family (delta-neutral funding carry) runs
in PAPER behind fail-closed gates. The gap is the OWNER: a self-described
beginner who cannot yet read what the system reports, when it needs him, or what
must be true before real money moves. A trading plan here is written rules for
capital, risk, decisions, and review that the owner understands — not new
trading machinery.

## Goals

1. A written trading plan the owner can follow without prior trading knowledge.
2. A manual that translates every system artifact (reports, logs, alerts) into
   plain English with the exact response each calls for.
3. A single beginner-first live screen (dashboard owner mode) that turns the
   5-minute daily check into reading one page.
4. Zero changes to the trading path; everything is documentation + read-only view.

## Non-goals

- No new strategies, signals, or pattern hunting (falsified exhaustively).
- No changes to gates, risk rails, or execution.
- No income projections beyond what the recorded evidence supports.
- Not a general trading course; every concept taught is one this system uses.

## Deliverable 1 — docs/owner/TRADING_PLAN.md

Plain language, every term defined at first use, percentages only (public repo:
no personal dollar amounts). Sections:

1. **Objective** — prove-it-then-decide, 6-month horizon; success = a defensible
   GO or NO-GO verdict plus an owner who can read the system.
2. **Capital rules** — paper-only now. A real dollar moves ONLY when ALL hold:
   (a) F1 promotion checklist fully green (>=60 resolved cycles, net>0, PF>=1.25,
   2/3 chronological folds positive, cost-stress green, zero unresolved one-leg
   events, concentration caps); (b) every live-activation precondition in the F1
   report is resolved (collateral unification verified on the target venue,
   maker-first legs, event-driven hedge monitoring); (c) the owner signs the
   CONTROLLED_LIVE checklist himself. Any one missing = no live capital, no
   exceptions.
3. **Risk rules (codified from the existing charter)** — per-trade risk <=3% of
   capital; total open exposure <=12%; leverage <=2.5x (carry runs 1x);
   carry-specific: <=5% per symbol, <=20% total carry notional, no averaging
   down. Directional futures positions carry a hard stop; carry positions exit
   on their own gate rules (notional mismatch, adverse basis, margin buffer,
   funding flips, max-hold) — the plan explains why those are the stop.
4. **Decision rights** — the bot alone: individual entries/exits inside its
   gates, settlement accrual, exits on its rules. Owner-only: operating-mode
   changes, going live, clearing the reduce-only recovery latch, adding/removing
   capital, changing risk numbers, restarting the process.
5. **Kill criteria (stop everything, investigate before restarting)** — a
   recovery latch the owner cannot explain; watchdog silence >24h with the
   machine on; any real-order evidence while in PAPER; equity change the reports
   cannot explain; two consecutive weekly reviews skipped (the plan is off).
6. **Expectations (from recorded evidence)** — gates reject most days; flat is
   correct behavior. Historical replay: 13/13 winning cycles over 6.8 years but
   only ~2 qualifying entries/year at current costs; external evidence supports
   roughly 0-0.5%/month at this scale in the current regime, possibly negative.
   The 60-cycle floor may take a long time; that slowness IS the honesty.
7. **Review cadence** — daily 5-min check; weekly 20-30 min review; monthly
   plan re-read; the plan is amended only in writing, never ad hoc.

## Deliverable 2 — docs/owner/OWNERS_MANUAL.md

1. **What you own** — one-page tour: engine (monitors, never opens under
   SIGNAL_SOURCE=none), carry runner (the only opener; every 15 min on three
   venues), harvesters, watchdog + email alerts, dashboard.
2. **What carry is** — hedged both-ways position collecting the funding fee
   shorts/longs exchange; price moves cancel; income = funding minus costs.
3. **Reading each artifact** — F1 carry report field-by-field (incl. measured
   accuracy caveats and the [UNMET] preconditions section); gate-log reasons
   translated (e.g. "perp_mark < spot_mid" -> "futures below spot: no carry to
   collect, skipping is correct"; "time_to_next_funding outside [20,180]" ->
   "wrong part of the funding clock"); heartbeats; each watchdog email
   (carry_heartbeat_stale -> the scheduled task died, how to check schtasks;
   carry_recovery_active -> what latched and the exact clear command);
   recovery latch: what it means, when to clear, when NOT to.
4. **Daily 5-minute checklist** — launch owner view; confirm liveness lines
   green; read the attention line; done. If anything is red: the manual names
   the section to read next.
5. **Weekly review (20-30 min)** — cycles this week and cumulative progress
   toward 60; win rate + PF with small-sample caveat; top rejection reasons
   (should match the market story); recovery/blocks status; re-read one plan
   section; note one thing learned.
6. **Glossary** — every term the system emits: funding, basis, delta-neutral,
   maker/taker, slippage, notional, leverage, liquidation, PF, WR vs expectancy,
   drawdown, DSR/PBO (one-liners each).

## Deliverable 3 — core/owner_view.py + dashboard --owner

**core/owner_view.py** (pure, no UI imports, fully unit-tested):

- `build_owner_briefing(*, now=None, heartbeat_path=..., carry_heartbeat_path=...,
  state_path=..., gate_log_path=..., window_days=7) -> dict` returning:
  `{"liveness": [...], "progress": [...], "this_week": [...], "attention": [...],
  "generated_ts": float}` — each a list of plain-language strings with a leading
  status glyph (OK/WARN/ALERT).
- Inputs (all read-only, all already exist):
  - `data/heartbeat.json` (engine liveness; `timestamp` ISO8601).
  - `data/carry_heartbeat.json` (`{"ts": epoch-float, "venue": str, "summary":
    {..., "recovery_active": bool}}`).
  - `data/carry_positions.json` (`positions` keyed `venue:symbol`; `cycles` with
    `net_pnl`/`label_status`; `recovery`; `blocks`).
  - `data/carry_gate_log.jsonl` (per-eval `{ts, symbol, venue, ok, reason}`).
  - Promotion progress via `core.carry_runner.promotion_checklist` (reuse, no
    reimplementation).
- Reason translation table REASON_EXPLANATIONS: stable-prefix match on gate
  reasons -> one plain-English sentence; unknown reasons pass through verbatim
  flagged "(unexplained — see manual)". Missing/corrupt files -> WARN lines,
  never exceptions (the owner screen must never traceback).

**dashboard.py `--owner` flag**: renders ONLY the owner briefing full-screen
(Rich panel groups mirroring the four sections), existing refresh loop,
`--refresh` respected. No changes to the default trader view; the diff is one
argparse flag + one render function calling `build_owner_briefing`.

**TradingBot.bat**: new menu entry "Owner view (plain-language status)".

## Error handling

- owner_view never raises to the dashboard: every reader is wrapped; failures
  become visible WARN lines ("could not read X — see manual §Y").
- Stale thresholds mirror the watchdog (engine 10 min, carry 60 min) so the
  screen and the emails never disagree.

## Testing

- Unit fixtures per state: all-healthy; stale carry heartbeat; recovery latched
  (attention line names the clear command); N resolved cycles progress string
  ("4 of 60"); missing files; corrupt JSON; unknown gate reason passthrough.
- Reason-translation table covers every reason string emitted by
  `f1_entry_gate`/`carry_exit_signal`/`f1_sizing_gate` (enumerated from
  research/funding_carry_lab.py at implementation time; test asserts coverage so
  a new gate reason fails the test until translated).
- Full suite green; ruff clean. Manual E2E: `python dashboard.py --owner`.

## Rollout

1. Branch `feat/owner-plan-and-view` off main.
2. Commit 1: the two documents (reviewable words first).
3. Commit 2: owner_view + tests + dashboard flag + bat entry.
4. Full suite; merge; push. No scheduled-task changes needed.

## Success criteria

- The owner can answer, from the owner screen alone: is it alive? did it trade?
  why not? does anything need me? how far to the GO/NO-GO?
- Every watchdog email maps to one manual section with one concrete response.
- Docs contain no personal dollar amounts and no promises beyond recorded
  evidence.
