# PREREG 80 — Universe widening: 44 -> 184 bases, liquidity-gated

**Status:** PRE-REGISTERED. Hashed and committed BEFORE the spec is
regenerated and BEFORE any post-change outcome exists. Any edit after the
hash is a NEW pre-registration.

**Date:** 2026-08-16. Owner directive: "widen the universe" -> "write prereg
80 and ship it".

## 1. What changes

The MCP directional PAPER spec's symbol list grows from **44 bases to 184**.

Candidacy rule (frozen):

```
eligible = { base :  max_24h_quoteVolume across venues >= $5,000,000
                AND listed as an ACTIVE USDT perp (no expiry) on >= 2 of
                    {binance, bybit, bitget} }
final    = eligible  UNION  current_incumbents        # never silently shrink
```

Measured at pre-registration time: eligible = **171**, incumbents = **44**,
union = **184**, new bases = **140**, incumbents dropped = **0**.

Everything else is unchanged: venues, entry rules, score floor, exit
geometry, sizing, risk limits, PAPER-only status.

## 2. Why $5M, and why not the full 686

The venue tail is genuinely thin. Measured 24h quote volume, binance USDT
perps (n=689):

| | value |
|---|---|
| current spec: min / p25 / median | **$0.8M / $4.3M / $18.4M** |
| universe p25 / p50 / p75 / p90 | $5.31M / **$1.68M** / $0.80M / $0.39M |
| universe minimum | $21.8K (SOFI) |

Bases clearing a floor: **$5M -> 174, $10M -> 125, $25M -> 67, $50M -> 37.**

$5M is chosen because it is the lowest floor that keeps every new base at or
above the *incumbent* distribution's own p25 ($4.3M) — widening must not
lower the liquidity bar the current universe already meets. Going to all 686
would admit a median-$1.68M tail where `universe_filter` and the
`thin_book:$930<$1200` rejection already fire on today's 44: symbols the bot
would reject anyway, at multiplied scan cost.

**Volume is read from live `fetch_tickers`, never from `load_markets`**,
which carries no volume (the 2026-07-20 regen note flags exactly this trap).

## 3. Expected outcome - stated in advance

**This is a FLOW change, not an EDGE change.** The measured baseline is
**-38.6 bps / 36.1% WR over n=2,547** (artifact 78). Widening multiplies the
rate at which that signal is applied; it does not change its sign.

Specifically expected: **more candidates scored, more opens per day,
unchanged (negative) expectancy per trade.** A later increase in trade count
is NOT evidence the change worked and must never be reported as such.

Secondary, and the reason this is worth doing at all: **forward unlock-event
reach goes 154 -> 191 events across 7 -> 17 traded bases**, which is what
artifact 78's blocked event-window test needs to become answerable.

## 4. Cohort discipline (binding)

The n=36 / 83.3% WR cohort was measured on 44 bases. Post-widening trades are
a **different population**. The regeneration stamps `universe_widened_utc` in
`regen_provenance`, and any analysis spanning the boundary must report
pre/post **split**, never pooled. Pooling them silently destroys the only
clean measurement in the system.

## 5. Success / failure criteria (frozen)

- **PRIMARY (flow):** opens/UTC-day recovers above 0 within 72h of shipping.
- **SECONDARY (no harm):** at n >= 100 post-widening trades, mean PnL/trade is
  **no worse than -38.6 bps** (the pre-widening baseline).
- **FAILURE -> REVERT:** post-widening mean worse than -60 bps at n >= 100, OR
  median realised spread on new bases exceeding 2x that of incumbents.
- **NOT a success criterion:** profitability. It is not expected, and its
  absence does not falsify this change.

## 6. What this does NOT license

- No change to entry rules, score floor, TP/SL geometry, or leverage.
- No disabling of `band_regime_filter`, the ADX bounds (prereg 77), the daily
  open budget, or `universe_filter` / thin-book rejection — those remain the
  per-trade quality gates and are what make a wide universe safe.
- No live-mode switch (the 39-finding audit stands).
- No claim of edge from any observed flow recovery.

## 7. Rollback

`data/strategy_specs/MCP_DIRECTIONAL_PAPER.json` is regenerated atomically
and the previous file retained as `.bak`. Revert = restore the backup and
restart the supervisor. The `universe_widened_utc` stamp keeps pre/post
cohorts separable permanently.
