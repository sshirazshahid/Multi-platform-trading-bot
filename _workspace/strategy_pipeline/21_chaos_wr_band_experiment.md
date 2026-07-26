# Chaos experiment — PAPER WR-band resilience (NOT a profitability strategy)

**Created:** 2026-07-23  
**Skill:** chaos-engineering  
**Scope:** PAPER virtual wallet only. **0% live capital.**

## Critical honesty (read first)

Chaos engineering finds **failure modes**. It does **not** invent trading edges or guarantee daily 63–67% wins with profit.

This bot’s **accuracy band** (`MAX_FLOW_BAND` + AccBand geometry) can produce a **win-rate band by construction**. Profitability is a **separate** requirement and is currently **failing** (today UTC WR ≈ 38% on n=21 immature sample; expectancy negative — see `data/goal_progress.json`).

Owner target phrasing “63–67%” maps to configured goal band **[0.59, 0.67]** in `goal_progress.target.win_rate_band` (not guaranteed; `guaranteed: false`).

## Hypothesis

When OHLCV/ticker fetches are delayed (+500ms) **or** maker resolution books fail intermittently for 20 minutes:

1. Process remains `OPERATING_MODE=PAPER`, `PAPER_TRADING_PROFILE=MAX_FLOW_BAND`, `SIGNAL_SOURCE=mcp`, `is_halted=false`.
2. Accuracy geometry stays enabled (boot banner AccBand ON).
3. Once a UTC day reaches **≥30 mature** closed outcomes, reported daily WR is assessed against [0.59, 0.67] — experiment does **not** require inventing edge; it checks the **reporting + risk rails** under fault.

## Steady-state (measure BEFORE fault)

| Metric | Source | Pre-fault baseline window |
|--------|--------|---------------------------|
| `is_halted` | `data/heartbeat.json` | 5 min |
| `paper_trading_profile` | heartbeat | must be `MAX_FLOW_BAND` |
| `signal_source` | heartbeat | must be `mcp` |
| UTC-day WR / sample | `data/goal_progress.json` → `paper_futures_current_utc_day` | snapshot at T0 |
| Incident latch | `data/risk_incident_latch.json` exists? | must be absent |

## Attack

| Field | Value |
|-------|-------|
| Type | Latency (primary); dependency failure (secondary: maker book invalid) |
| Magnitude | +500ms on exchange OHLCV/ticker path (monkeypatch or local proxy — **not** production tc on whole NIC) |
| Duration | 20 minutes |
| Tooling | **Custom / DIY only** — no Chaos Mesh/k8s. Prefer a flagged test harness or temporary monkeypatch in a **forked dry worker**, not the live 24x7 process, until Game Day is scheduled. |

## Blast radius

- PAPER virtual balances only; single machine; no CONTROLLED_LIVE.
- Blast calculator (1 “user”, 100% of this bot, 20 min): **YELLOW** — proceed only with owner sign-off; prefer reducing to a **shadow worker** (0% of production paper fills).

**Recommendation:** Run first as **Game Day dry-run against a second `main.py` with DRY wallet copy**, not against `TradingBot-24x7`.

## Abort criteria (mandatory)

Abort **immediately** if any:

1. `heartbeat.is_halted == true`
2. `data/risk_incident_latch.json` appears
3. `OPERATING_MODE != PAPER`
4. `paper_trading_profile != MAX_FLOW_BAND`
5. Live latch / real orders attempted
6. Paper open positions exceed mode profile max for >2 cycles

## Rollback

1. Disable fault injection / kill experiment worker.  
2. Confirm heartbeat healthy within 2 minutes.  
3. If latch written: archive latch only after root-cause note (do not clear blindly on live).

## What actually maintains the 59–67% WR band (ops, not chaos)

1. Keep `PAPER` + `MAX_FLOW_BAND` + AccBand geometry ON (already gated in `config.py`).  
2. Do **not** switch to `AGGRESSIVE_RESEARCH` (disables band knobs).  
3. Do **not** re-add `--paper-profile` to the schtask.  
4. Accept: WR in band ≠ profitable. Expectancy/profit factor must still clear separately (`goal_progress` profitability_requirements).  
5. Refuse candlestick/Kronos/scalper installs (ledger REFUTED) — they destroy expectancy while gaming short WR streaks.

## Status this UTC day (pre-experiment)

From `data/goal_progress.json` (~09:40Z):

- `paper_futures_current_utc_day`: n=21, WR≈**0.38**, `target_status=INSUFFICIENT_SAMPLE`, net_after_cost_pnl≈**−2.27**, profit_factor≈**0.31**
- Chaos cannot “fix” that into 63–67% profitable; only more mature band-geometry sample + after-cost edge can.

## Learning question

Does feed latency / book failure cause **false HALT** or **silent maker drops** (addressed 2026-07-23 finalize fixes), or does the PAPER band pipeline stay measurable?

## Follow-ups (only if experiment run)

1. Confirm maker nonfill recording under book invalid (code already patched).  
2. Add a supervised **opt-in** `CHAOS_FEED_LATENCY_MS` env for PAPER-only workers (default 0) — **not shipped in this doc**; requires TDD + owner Game Day.
