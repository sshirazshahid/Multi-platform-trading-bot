# 69 — F1 carry lane: idle measurement (2026-08-12)

**Type:** MEASUREMENT of the incumbent, not a screen. No prereg (nothing is
being decided); no config changed; read-only over `data/carry_gate_log.jsonl`.

**Why:** the refuted-families ledger names exactly one validated family —
delta-neutral cross-venue funding carry (F1). Under the owner's standing
profitability goal, F1 is the only lane where profitable trades could come
from. This measures whether it actually finds tradeable edge on this
account's real feeds.

## Result

| metric | value |
|---|---|
| total gate checks | **162,742** |
| gate PASSES | **0** |
| infra-blocked (`no_snapshot` / `feeds_stale`) | 11,855 (7.3%) — never reached economics |
| checks with positive net edge | 875 rows |
| **distinct positive EPISODES** | **47** (symbol × venue × 8h funding cycle) |
| polling inflation | **18.6×** — rows badly overstate opportunity |
| best net edge, any episode | **+1.00 bps** |
| median net edge, all checks | **−45.7 bps** |

## Two corrections applied before publishing

1. **`net_edge_bps` is POST-cost.** `research/funding_carry_lab.py:571` —
   `net = (funding_gross − round_trip_cost) × 1e4`. An earlier draft of this
   analysis called +1.0 bps "24× short of its own execution cost"; that was
   **wrong** — the cost is already subtracted. The correct statement is that
   +1.0 bps is a *real but economically meaningless* post-cost edge: on a
   ~$250 delta-neutral pair it is **$0.025 per funding cycle**, far below the
   execution risk of holding two legs on two venues.

2. **875 positive rows are 47 episodes.** The top ten "opportunities" were
   all ZEC/USDT at exactly +1.00 bps with `time_to_next_funding` counting
   down 471→306 min — one persistent state polled repeatedly. Counting rows
   would overstate opportunity 18.6×. Same artifact class as the SUI/GUN
   pseudo-replication already flagged on the unlock-short ledger row.

## Interpretation

The gate is **working, not broken**. It is refusing entries at −41, −37,
−35 bps — declining to lose money is correct behaviour, and the 0-pass count
is the gate doing its job, not a bug to be tuned around.

But the economic distribution is the finding: **median −45.7 bps, best-ever
+1.0 bps.** In the 2025–26 compressed-funding regime, cross-venue funding
carry does not clear its own execution cost at this account's size on these
venues. The one validated family is **structurally idle** — consistent with
the standing alert already on record (F1: 0 entries / ~49,384 checks,
2026-07-17) and with the external evidence cited there (carry Sharpe reported
negative for 2025, arXiv 2510.14435).

**This does NOT refute F1.** The family retains its validated status and its
peer-reviewed support; what is measured here is that the *current regime*
offers no edge above cost at this capital. Widening the gate to force entries
would convert a correct refusal into a guaranteed loss.

## What must NOT be done with this result

- Do **not** loosen the `[20,180]` funding-window bounds, the 5 bps spread
  cap, or the `trailing_funding_mean > 0` requirement to manufacture passes.
  Each exists because the edge is thinner than the cost of ignoring it.
- Do **not** read "0 passes" as broken wiring. The 7.3% infra blocks are
  worth fixing on their own merits, but the other 92.7% were evaluated and
  were economically negative.

## Standing with the other two findings today

Three independent lines of evidence, same conclusion:

1. **Entry signal** — `corr(mcp_score, realized_pnl) = −0.019` at n=1,170;
   gross −37.19 pre-fee over 279 trades
   (`reports/profitability_diagnostic_2026-08-12.md`).
2. **Exit geometry** — screen 68 CONFIRMED_NO_GO, 0/10 arms positive; root
   cause mean MFE 0.435% < mean MAE 0.523% (ratio 0.831).
3. **Carry lane (this file)** — 0/162,742 passes; best post-cost edge
   +1.0 bps across 47 distinct episodes.

The bot currently has **no lane with positive expected value**. That is a
finding about the market regime and the signal, not a configuration defect,
and the decision about what to do next is the owner's.
