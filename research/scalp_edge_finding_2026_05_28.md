# Scalp-edge investigation — "why is it still losing & can we scalp $1-2/trade?"

**Date:** 2026-05-28
**Verdict:** **NO_EDGE_CONFIRMED.** A mechanical 1-2 USDT futures scalp (0.8% SL /
1.3% TP, 15-60m hold) has **no exploitable edge** after realistic costs — confirmed
across two signal classes, two timeframes (45-day 15m and 3-year 1h), per-symbol,
ensembles, regime-conditioning, and threshold sweeps, by a 3-lens adversarial review.
The live record is -EV for the same reason: **the entry has no edge; fees finish it.**

This is NOT a "stop trying" note — it is the precise, quantified answer the prior
"NO_EDGE" findings only asserted. It also names the single real lever (fees) and why
it is insufficient at this account size.

---

## 1. The question

User directive (2026-05-28): *"Fix why it's losing. Build a scalper that wins
$1-2/trade on FUTURES at the 15-60m sweet spot. Test and ship it."*

## 2. The decisive insight (advisor-corrected)

The bot's config justified a scalper with: "454-trade dataset shows 15-60m holds =
63.8% WR" and "at 1.625:1 R:R, 55% WR ⇒ +$0.48/trade." **Both reasonings are unsound:**

- **"15-60m holds win more" is conditioning on the future.** Hold time is determined
  by *when the exit fires* — losers stop out fast, winners run. You cannot decide at
  entry which bucket a trade lands in. It's an outcome, not a tradeable rule.
- **Exits cannot manufacture EV.** On a driftless (no-edge) entry, the probability of
  hitting TP before SL is `SL/(SL+TP) = 0.8/2.1 = 38.1%` **by construction**, and that
  bracket's EV is *exactly zero before fees*. R:R and its breakeven WR are the same
  number under no edge. Fixing exits (trailing, discretionary closes) only moves you
  along the iso-EV frontier — **only entry edge lifts EV above zero.**

The live 44.8% WR looked like it beat the 38% geometric floor, but that was inflated
by 53 cheap trailing-stop "wins" (+0.55 avg). Strip the exit machinery to a clean
bracket and WR falls to the geometric floor; fees then make it negative.

## 3. Breakeven WR for the 0.8% SL / 1.3% TP bracket (honest, asymmetric)

Costs cut the win leg AND deepen the loss leg (and the SL leg always crosses the book):

| Cost model | Round-trip cost | Breakeven WR |
|---|---|---|
| No cost (geometric null) | 0 | **38.1%** |
| Taker fees only (0.12%) | 0.0012 | 43.8% |
| Taker fees + slippage | ~0.0022 | 48.6% |
| **Honest taker (asymmetric + stop slip)** | — | **49.8%** |
| **Honest maker** (maker entry+TP on wins; **taker SL always**) | — | **43.1%** |
| Realized (from actual payoffs, ~30% mark-outs ⇒ ~1:1) | — | **~45.7%** |

Note the maker rescue cannot use a maker stop — the SL leg must cross the spread, so it
is always taker + slippage. The "maker breakeven = 39%" fantasy (maker on both legs) is
wrong; the honest figure is 43.1%.

## 4. Results (look-ahead-free bracket replay; `scripts/scalp_replay_backtest.py`)

Entry fills at signal_bar+1 OPEN; SL/TP scanned forward; pessimistic on double-wick;
fees+slippage per `config.py`. Walk-forward time-split into 3 OOS folds.

| Test | Best-cohort OOS WR | EV/trade | vs breakeven |
|---|---|---|---|
| T1 — bot's **actual** entries, clean bracket | 27.8% (gap-cleaned 35.3%) | -0.40% | **FAIL** (anti-predictive) |
| T2 — momentum scalp (taker) | 41% longs | -0.14% | FAIL |
| T2 — mean-reversion scalp (taker) | 44.7% shorts | -0.07% | FAIL |
| T2 — momentum (charitable maker) | 48.4% longs | +0.015% | marginal, fold-decays |
| T2 — mean-reversion (charitable maker) | 51.5% shorts | +0.071% | marginal, fold-decays |
| **3-year 1h, momentum (charitable maker)** | 37.9% | **-0.134%** | FAIL all folds |
| **3-year 1h, mean-reversion (charitable maker)** | 36.1% | **-0.188%** | FAIL all folds |

- **T1 = 27.8% WR raw → 35.3% gap-cleaned**: 132/424 of the bot's entries window-snapped
  to a far-off cache bar (the 15m cache only spans the recent window); cleaning those
  leaves 35.3% WR — i.e. the bot's entries sit *at* the ~38% no-edge floor, not a usable
  edge but not "worse than random" once the snap artifact is removed.
- The only taker cohorts above the geometric floor (longs 41%, meanrev-shorts 44.7%)
  are weak **regime artifacts** (the 45-day window had +2.4% mean drift) and **decay to
  negative in the latest fold**.
- Under maker fees, the best cohort (meanrev-shorts) pools to **+0.0026/trade ≈ zero**,
  is **negative in the forward-most fold**, and **dies at a 15% maker-fill-miss rate**.
- **Zero-cost gross meanrev-shorts is only +0.07-0.09% (statistically flat)** — there is
  no gross edge being hidden by costs. The signal itself is noise.

## 5. Adversarial verification (3 independent lenses — all NO_EDGE_CONFIRMED)

- **Quant/math:** re-derived breakevens independently (script's bars were if anything
  charitable); EV (not WR) is the real metric; the WR-pass "survivor" loses money on EV
  in the forward fold; maker edge dies under adverse selection.
- **Code/methodology:** found & corrected the T1 window-snap; ran the signal on 3-year
  1h data (43,831 signals) — uniformly negative; zero-cost gross is flat ⇒ no hidden edge.
- **Rescue-search:** per-symbol 0/32 pass OOS (vs ~4 by chance), ensembles & regime gates
  negative, best threshold-sweep t-stat +1.49 (< 1.65). Corrected the maker-fee math.

## 6. The bugs are (mostly) already fixed — the loss is structural

Checked against code, not assumed:
- Discretionary `mcp_brain_close` (the -$20, 29.5% WR drag): **already disabled** (Phase 39).
- `longs_only`: **already enforced** (mcp_brain.py:3087).
- SCALP path: **live-wired and mechanically working** (mcp_brain.py:3052) — it just has no edge.
- `r_multiple` ~50% null: **benign** — null only for reconciled/external/ghost closes that
  have no known stop distance (order_manager.py:1715). Analytics gap, not a money leak.

Remaining real hygiene item: ghost-class exits (`reconciled_from_exchange`) — a genuine
accounting class flagged in the 2026-05-23 diagnostic, worth a separate look, but it does
not create or restore edge.

## 7. Recommendation

1. **Do not ship the scalper as a profit engine, and do not enable live for it.** It is
   -EV by ~fees/trade; at $400-800 equity, round-trip fees (~$0.14 on ~$136 notional) eat
   8-12% of a $1-2 target on *every* trade. Keep PAPER.
2. **The only structural lever is cost, not signal.** Taker→maker moves EV from -0.09% to
   ~0% — but (a) a scalp maker order suffers adverse selection (you get filled on losers,
   skipped on the bounce), which bar-backtests cannot validate, and (b) even charitable
   maker fills are negative across 3-year folds. A VIP/volume fee tier would help but is
   out of reach at this account size.
3. **A profitable scalp requires a genuine micro-edge the bot does not currently have** —
   e.g. validated order-flow / L2-depth / funding-timing signals (the prior funding search
   was also NO_EDGE). Until one passes the frozen edge gates OUT-OF-SAMPLE, no amount of
   exit tuning, sizing, or pair-hunting changes the sign of EV.
4. **Reusable tools shipped:** `scripts/scalp_replay_backtest.py`,
   `scripts/scalp_edge_probe.py`, `scripts/scalp_rescue_search.py` — any future strategy
   idea can be screened against the breakeven bar *before* risking capital.

## 8. Reconciliation with the live record

444 closed trades, **-$110.95, EV -$0.25/trade, 44.8% WR, realized R:R 0.46.** The
backtest reproduces this: ~zero gross edge − fees − the historical discretionary-close
leak ≈ -$0.25/trade. The model and the tape agree.
