# Why the bot is not trading — measured, 2026-08-22

**Method:** systematic-debugging. Every number below is read from the warehouse or
live state files, not inferred. Three of my own initial readings were wrong and
were corrected by measurement; each correction is recorded.

---

## 1. The bot is healthy. It is refusing every entry on purpose.

| Signal | Value |
|---|---|
| Operating mode | `PAPER`, `ENTRY_POLICY=APPROVED_PAPER` (entries **authorised**) |
| Process | up 10.4 h, 298 cycles, heartbeat written 0.0 h ago |
| Risk state | `is_halted=False`, no symbol or family pauses |
| Scorer output | **300–1,600 ALLOW/day** (407 today) |
| Decisions written | every ALLOW produces a `decision_event` — the lane works |
| **Trades** | **0. Last fill 2026-08-17 20:04 — 113 h ago.** |

**All 2,806 decisions since 08-18 carry `outcome=rejected`.** Nothing is broken
upstream; the refusal is at execution.

> **Correction to a stale belief:** I expected `ENTRY_POLICY=SHADOW_ONLY` from an
> earlier memory. It is `APPROVED_PAPER`. The memory had decayed; the flag was
> flipped. Checking beat recalling.

## 2. "Model gate starving" is a misnomer

The **model is not starving** — it emits hundreds of ALLOWs a day. The watchdog's
name points at the scorer while the block sits two layers below it, in
`_execute_open`. That naming is why this took hours to diagnose on 2026-08-21 and
again today.

## 3. The five blockers, and they rotate daily

From `decision_events.payload_json → reason_codes` (CLI-stamped, complete):

| day | total | btc_vol_pause | soft_stale | econ_gate | chop filter |
|---|---|---|---|---|---|
| 08-15 | 1,549 | 0 | 27 | 0 | 0 |
| 08-16 | 1,388 | 0 | 35 | 0 | 0 |
| 08-17 | 962 | 0 | 19 | 11 | 41 |
| 08-18 | 618 | 0 | 132 | 116 | 0 |
| 08-19 | 802 | 0 | 11 | **329** | **400** |
| 08-20 | 331 | **326** | 5 | 0 | 0 |
| 08-21 | 750 | **301** | **449** | 0 | 0 |
| 08-22 | 319 | **184** | **135** | 0 | 0 |

No single wall — a rotating cast. `entry_policy_shadow_only` (393) is entirely
08-18 and is a **dead flag**, already flipped; it is not a current blocker.

**`economic_gate_negative_expectancy` (445) is not a defect.** It is the gate
saying *this trade loses money*. It belongs in a different category from the
other four.

## 4. Root cause of the largest fixable slice: a dead research feed

`soft_stale_entry_block` = **42.3% of today's decisions**. Its reason is
`forward_feeds_stale`. Traced:

```
data/skew_status.json
  {"updated": 1787386558.1, "connected": false, "open_hours": 0, "total_polls": 0}
```

`core/feed_health.py:171 unhealthy_forward_feeds` ORs four conditions; **`not
connected` alone** marks a feed bad. So:

- The **skew** harvester is alive — sampled twice 95 s apart, `updated` advanced
  62.4 s. It rewrites the file every ~60 s.
- It has **never once succeeded**: `total_polls: 0`, `connected: false`.
- `fresh: true` is **misleading** — it times the *failure record*, not any data.
  The other three feeds are healthy (liquidations, l2, tv).

**And skew gates nothing.** Every `skew` reference in the live path is *clock*
skew or statistical *skewness* (`_skew` for DSR). The options-skew feed exists
for `research/screen_skew_shock_drift.py`. **A research-only feed that has never
worked is blocking ~42% of live trading decisions.**

> **Correction:** I first derived a 65.8% duty cycle from log transitions. Clears
> are under-logged (178 blocks vs 25 clears), so that was an upper bound. The
> CLI-stamped warehouse figure is **42.3%**.

## 5. Did my own change this morning make it worse? Measured: no.

I tightened `btc_vol_pause` today (whole-buffer median → 30-day-window median,
threshold 0.86% → 0.72%). A naive pre/post split suggested 9.4% → 60.5%, which
would have been alarming. It is confounded — the gate fired **0 times** on
08-15→08-19 and **98.5%** on 08-20, two days *before* my restart.

Direct measurement instead:

| | value |
|---|---|
| Current BTC 1h ATR | **1.16%** |
| 30-day median | 0.36% (**current vol is 3.2× normal**) |
| OLD threshold 0.86% | pauses → **True** |
| NEW threshold 0.72% | pauses → **True** |
| Effect across all 720 samples | pause rate 4.3% → 8.3% (**+4.0 pp**) |

**Both thresholds pause at today's volatility.** My fix is marginally stricter
and is *not* the cause. BTC is genuinely 3.2× its normal volatility and the gate
is doing exactly what it was designed to do.

## 6. TradFi / oil / gold / stocks — the belief is half right

The instruments **are listed and tradeable** on the connected venues. But
`config/universe.py:126-138` documents **four independent** blocks:

1. `pair_discovery._is_tradfi_market` → `tradfi_asset:{base}`
2. empty `COMMODITY_BASES`/`STOCK_BASES` + `_DISABLED_BASES`
3. AccBand scope skip `analysis_only_accband_scope` — **7,829 hits/24 h, the #1
   scorer skip reason**
4. directional StrategySpec regen (no BZ/CL routes)

**What they actually are:** Binance `TRADIFI_PERPETUAL` USDT-margined synthetics
with 4-hour funding — *not* CME futures and *not* shares. Screen 91 (today)
tested RSI mean-reversion on the real underlyings (GC1! to 1975, CL1! to 1983,
SPY to 1993, EURUSD to 1971) and measured **no edge above 30.1 bps/trade**. So
for that family the block is now empirically justified on the actual instrument,
not just asserted.

## 7. "Not testing strategies" — it is

- **122,294 `shadow_decisions`**, most recent written **seconds ago**.
- Six shadow probes live (bundle-MR ×2, TSMOM ×2, breakout, listing, unlock,
  pullback), all log-only, accruing toward the frozen ≥30-resolved gate.
- 903,168 backtests ran today (screen 91 / Lane 1).

---

## What to do, in order

1. **Fix or ungate the skew feed.** It has never worked and gates 42% of
   entries. Either repair `scripts/harvest_skew.py`, or remove `skew` from
   `FORWARD_FEEDS` in `core/feed_health.py:20` since nothing live consumes it.
   ⚠ This changes what gates live entries — **owner decision, not mine.**
   Note `open_hours: 0` suggests it is an options feed with market hours, which
   would make permanent disconnection *expected* off-hours and gating 24/7 crypto
   entries on it wrong by construction.
2. **Leave `btc_vol_pause` alone.** BTC is 3.2× normal volatility. The gate is
   correct. It will clear on its own.
3. **Leave `economic_gate_negative_expectancy` alone.** It is the only gate
   measuring profitability, and it is the honest one.

**Honesty, stated plainly:** fixing item 1 restores **FLOW**, not **EDGE**. The
measured expectancy of this directional book is ≈ −0.24 R/trade; unblocking it
means more losing trades, faster. Flow is worth restoring only because evidence
accrues through it — not because the trades are expected to profit.
