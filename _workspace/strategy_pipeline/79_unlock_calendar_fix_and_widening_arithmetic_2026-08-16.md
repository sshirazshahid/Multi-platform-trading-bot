# 79 — Unlock calendar FIXED; universe-widening arithmetic (not shipped)

**Type:** FIX (calendar) + MEASUREMENT (widening). **No universe change was
made; no trading config changed.**

**Triggers:** owner — "widen the universe" and "fix unlock calendar is almost
entirely historical (2023-2024) issue. forward calendar backfill".

## Part 1 — Unlock calendar: FIXED

**Root cause:** `data/unlock_calendar/` existed but was **completely empty**.
Artifact 78's "the calendar is historical (2023-06 -> 2024)" was measuring the
`shadow_unlock_probe` warehouse table — rows logged long ago — because the
calendar the probe actually reads had no files at all.

**Fix:** `scripts/backfill_unlock_calendar.py --forward-days 120`

| | before | after |
|---|---|---|
| calendar files | **0** | **164** |
| total events | — | 11,709 |
| **FUTURE events** | **6** (warehouse) | **421** |
| bases with future events | — | 65 |
| **…on the current 44-base spec** | **0** | **7** -> **154 events** |

The 7 on-spec bases: **APT, ARB, AVAX, OP, SEI, SUI, TIA** — nearest event
lands the following day.

**This substantially unblocks artifact 78's event-window test on the CURRENT
universe.** 78's reopen condition (>=30 closed trades within +/-7d of a logged
event on a traded base) now has 154 forward events to accrue against instead
of zero. It becomes a waiting problem, not a structural one.

## Part 2 — Universe widening: the arithmetic

Measured headroom (live `load_markets`, active USDT perps, no expiry):

| | count |
|---|---|
| binance / bybit / bitget | 689 / 713 / 754 |
| union across venues | 919 |
| listed on >=2 venues | 686 |
| current spec | **44** |
| **headroom (>=2 venues, new)** | **642** (~15x) |

**Note the regen script cannot do this.** Its candidacy pool is
`CORE_SYMBOLS (7) ∪ EXTENDED_CRYPTO (30)` = **37 bases — smaller than the
current 44.** Re-running it would SHRINK the universe. Widening requires
changing the candidacy rule itself, not a regen.

**Event-coverage gain from widening:** 58 additional bases carrying 267
future unlock events. Real, but secondary now that the calendar fix delivered
154 events on already-traded bases.

## Part 3 — Why this was NOT shipped tonight

**The universe is not the binding constraint.** Measured since the prereg-77
ADX fix shipped (23:38, 2.6h of data):

- distinct symbols scored: **41**; **27 (66%) are inside the tradeable
  15-30 ADX window right now**
- opens: **0**; ALLOW decisions: **0**
- dominant skip is now `req_fail` (**1,500**) — the scorer's 4-of-4 required
  conditions — not a shortage of symbols

At 44 bases roughly 27 are eligible at any moment and still nothing trades.
Multiplying eligible symbols ~15x multiplies the rate at which a
**-38.6 bps / 36.1% WR** signal (artifact 78, n=2,547) is applied. **Widening
is a FLOW change wearing an EDGE costume** — the same distinction prereg 77
had to state explicitly.

## Constraints any widening prereg MUST encode

1. **Liquidity is the whole risk.** The tail of 686 perps is thin-book
   territory; `universe_filter` and the `thin_book:$930<$1200` rejection
   already fire on the current 44. `load_markets` carries **no volume** — gate
   on measured spread/depth from live tickers, never on listing existence
   (the 2026-07-20 regen note flags exactly this).
2. **It resets the cohort.** n=36 at 83.3% WR was measured on 44 bases;
   post-widening trades are a different population and need a new epoch stamp
   or the only clean measurement gets contaminated.
3. **Expected outcome must be stated in advance:** more trades, unchanged
   sign. Any later flow recovery is not evidence of edge.

## Status

- Calendar: **FIXED and verified** (421 future events, 154 on-spec).
- Widening: **measured, specified, NOT shipped** — it needs its own hashed
  prereg per the pipeline rule, and the flow data says it would not fix the
  current idleness.
