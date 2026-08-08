# 64 — TradingView Pine strategy sweep: classified inventory

**Date:** 2026-08-08 · **Run:** workflow `wf_827b3e7e-6fd` (6 fetch+classify
agents over 6 public strategy-library slices + 1 synthesizer) · **Trigger:**
owner "keep testing Pine scripts, find which can be profitable" (3× reaffirmed)

## Why this was a classification sweep, not a backtest

"Simulate Pine in the TradingView MCP" is mechanically impossible: TradingView
exposes NO strategy-tester API; Pine runs only in their web UI; the connected
MCP is read-only market data. Every number on a TradingView strategy page is an
**in-sample tester result at default costs** — the exact overfitting the frozen
gates and the row-8 PBO fix exist to catch. So the honest operation is: source
broadly, classify each script's real mechanism against the binding ledger, and
route only genuine novelty to a real harness screen. TradingView sources; the
harness judges.

## Result: the Pine expressiveness ceiling holds

**89 unique strategies** (from 113 raw rows across 6 slices: top, recent,
editors' picks, bitcoin, scalping, trend). Six pages fetched, zero failed.

| Disposition | Count |
|---|---|
| REFUTED family | 75 |
| Non-strategy (reporting / template / sizing util) | 11 |
| Unclear (mechanism undisclosed) | 1 |
| **NOVEL candidate** | **2** |

Refuted-family tally (breakout + SMC/structure-break collapse to one bucket per
the ledger → 27, the largest cluster):

| Family | n |
|---|---|
| breakout + SMC/structure-break | 27 |
| trend-following | 20 |
| confluence stack | 6 |
| grid/DCA/martingale | 5 |
| oscillator + price + VWAP mean-reversion | 7 |
| support/resistance/pivot | 4 |
| ML price-forecaster | 2 |
| candlestick | 2 |
| seasonality / time-of-day | 2 |

**Not one single-instrument, price-derived Pine script produced a non-refuted
mechanism.** This is the structural thesis confirmed at the source level: Pine
operates on the OHLCV of one chart symbol, so it can only express price-derived
TA — which is the ~2,400-pattern set already refuted on this bot's own data.

## The 2 survivors — both escape ONLY by reaching off-chart

Both confirm the rule rather than break it: each pulls in external/cross-market
data (the one thing that can lift a Pine script out of the refuted set), and
each still wraps a refuted execution layer. **Neither is promotable; both are
queued Stage-0-blocked** (edge-queue rows 10, 11).

1. **CME-spot BTC basis z-spread** (`presentTrading`) → edge-queue **row 10**.
   Cross-market basis (CME futures vs spot) is the price-analog of the validated
   cross-venue carry family, so not a-priori refuted. Blocker: no local CME
   futures substrate. Adjacency (ETH quarterly-basis leg-swap) already NO_GO.
2. **Adaptive MVRV & RSI V6** → edge-queue **row 11**. On-chain MVRV is genuine
   non-price data, but the trigger is RSI+DCA (refuted), it's BTC/ETH-only, and
   it's not in the feature store. Collapses to REFUTED if MVRV adds no
   separation over the refuted wrapper.

## Recommendation (committed)

Do not trade any of the 89 — all in-sample tester numbers. The 75 refuted are
closed. The 2 survivors earn only a pre-registered, after-cost Stage-0 screen
once substrate is harvested, and both are expected NO_GO by adjacency. **Nothing
is promotable now.** Do not re-run this sweep on the same library — the ceiling
finding is established; only a NEW class of source (off-chart data Pine can
compute) would change it.
