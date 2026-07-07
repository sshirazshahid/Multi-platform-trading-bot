# Pre-registration: F1 Carry PAPER-Universe Expansion

**Date registered:** 2026-07-05 (before activation)
**Owner approval:** explicit ("do it"), same date, following review of the
15-symbol replay evidence.
**Status:** ACTIVE in PAPER from the first scheduled `TradingBot-F1CarryPaper`
run after this commit. The Rev-5 live latch (`F1_DEFAULT_SYMBOLS` = BTC/ETH,
`build_f1_spec` extended-universe guard) is **unchanged**.

## Hypothesis

Expanding the PAPER carry universe from BTC+ETH to the frozen 15-symbol set
below multiplies qualifying F1 cycle frequency roughly an order of magnitude
(replay estimate: ~0.45 → ~7.8 qualifying entries/year at the shipped 70 bps
taker cost baseline) at unchanged per-cycle gate semantics, materially
shortening the time to the 60-resolved-cycle promotion floor **without any
change to entry/exit/sizing rules, caps, or venues**.

## Frozen symbol set (order fixed; no additions or removals without a new pre-registration)

BTC/USDT, ETH/USDT, BNB/USDT, SOL/USDT, LINK/USDT, TRX/USDT, SUI/USDT,
ATOM/USDT, DOGE/USDT, ALGO/USDT, ZEC/USDT, ADA/USDT, XRP/USDT, LTC/USDT,
GRT/USDT

The set is the owner's named list, frozen **in full** — including BTC and SUI,
which showed **zero** qualifying entries in the 6.8-year replay
(`research/carry_universe_scan_2026_07_05.py`). Keeping the zero-entry symbols
is deliberate: it prevents any later claim that the universe was selected on
the replay's results.

## What changes / what does not

- CHANGED: `scripts/run_f1_carry_paper.py` `SYMBOLS` → `F1_EXPANDED_UNIVERSE_2026_07_05`
  (constant in `research/funding_carry_lab.py`).
- UNCHANGED: every gate (`f1_entry_gate`, `carry_exit_signal`, `f1_sizing_gate`),
  all thresholds, per-symbol cap (5% of paper equity), total carry cap (20%
  notional), venues (binance/bybit/bitget), leverage caps, execution mode,
  promotion checklist (60 resolved cycles, PF ≥ 1.25, 2/3 folds, cost-stress,
  zero unresolved one-leg events, concentration caps).
- ROLLBACK: env `F1_UNIVERSE=legacy` reverts to BTC/ETH without a code change.

## Acceptance / evaluation criteria (fixed now, evaluated later)

1. The F1 promotion checklist is evaluated over the **entire expanded set**,
   with **no post-hoc per-symbol exclusion**. A symbol may only be removed by a
   new dated pre-registration stating the reason *before* removal.
2. Multiple-testing: 15 symbols were scanned against one gate. Any per-symbol
   performance claims must carry the trial count; the primary endpoint is the
   POOLED expanded-universe cycle record, not any single symbol's.
3. The replay entries/year figures are whole-history averages and treated as
   **upper bounds** (funding regimes decay; live microstructure gates —
   depth/spread/basis/time-to-funding/atomic-fill — cannot be replayed from
   funding history and will reject additional entries).

## Operational notes

- Gate evaluations rise from 2 to 15 per venue per 15-minute pass (45 total);
  snapshot fetches are per-symbol and rate-limited; symbols not listed on a
  venue (or with unavailable snapshots) are gate-skipped fail-closed.
- Expected economics at current paper scale remain small; the purpose of this
  expansion is **faster honest evidence accumulation**, not income. A NO-GO on
  the expanded evidence remains a success of the process.

## Provenance

- Evidence: `research/carry_universe_scan_2026_07_05.py` (+ its report), which
  reproduces the shipped `scripts/f1_replay_historical.py` baseline bit-for-bit
  (5-major total 2.02 entries/yr) per independent adversarial verification.
  Replay record over the full set: 46/49 cycles won (93.9%) at the 70 bps
  baseline. (Correction 2026-07-05: an earlier summary cited 50/53; the
  published per-symbol table sums to 49 entries / 46 won.)
- Cost-sensitivity companion run (maker-first scenarios): see
  `research/carry_cost_sensitivity_2026_07_05.py` when present.
