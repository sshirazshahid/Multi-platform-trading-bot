# Operating Plan — what to do now (plain English)

You are not a full-time trader, so this is deliberately simple and conservative.
Goal of this phase: gather honest PAPER evidence under the frozen promotion gates
before risking a cent.

## What you actually have

- A **deterministic** crypto futures/spot paper bot (`main.py` → `BotEngine`) on
  Binance / Bybit / Bitget. Live portfolio decisions come from the algorithmic
  scorer (`core/mcp_brain.py`) and `SIGNAL_SOURCE` routing — **not** from an LLM.
- Log-only shadow probes + the strategy-evidence pipeline (ledger → after-cost
  screen → audit → shadow). Promotion requires the frozen gate **and** owner
  sign-off. CONTROLLED_LIVE stays off until those bars clear.
- LLM / investment-committee tools are **offline research only** — they never
  authorize entries.

## Do this (ongoing PAPER)

1. `cd` to the repo, then `venv\Scripts\python.exe main.py` (or `TradingBot.bat`
   option to run). Leave the supervisor running; logs go to `logs\bot_<date>.log`
   and the warehouse.
2. Check Mission Control (`python scripts/run_mission_control.py` →
   `http://127.0.0.1:8787/`) or `data/promotion_funnel.json` for lane progress.
3. Do not loosen `core/promotion_gate.py`, `entry_policy`, `kill_switch`,
   `live_gate`, or `risk_manager` to chase fills.

## How to judge a strategy

Use the frozen gates in `core/promotion_gate.py` and the refuted-families ledger —
not narrative backtests. Shadow lanes need ≥30 resolved outcomes before any
promotion conversation.

## Hard guardrails BEFORE real money

- `OPERATING_MODE=CONTROLLED_LIVE` **and** `CONTROLLED_LIVE_ENABLED=true` **and**
  a signed `docs/CONTROLLED_LIVE_CHECKLIST.md` (see `core/live_gate.py`).
- `risk_manager.is_halted` / incident latch are real and enforced — clear
  `data/risk_incident_latch.json` only after diagnosing the halt.
- Preflight: paper parity, venue keys, and an owner-signed promotion dossier.

## What NOT to do

- Don't invent new strategies on narrative alone — route through the evidence
  pipeline and consult the ledger first.
- Don't go live on in-sample backtest numbers.
- Don't treat Claude / committee output as decision authority.
