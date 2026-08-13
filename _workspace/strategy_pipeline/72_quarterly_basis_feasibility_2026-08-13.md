# 72 — BTC/ETH quarterly cash-and-carry: $5k feasibility re-check (2026-08-13)

**Type:** Feasibility re-check authorized by council r2 (2026-08-13) — the
ledger's quarterly-basis row was closed "INFEASIBLE-AT-CURRENT-CAPITAL, not
re-probed" and the owner's capital changed $420 → $5,000. This is the ONE
door that change reopens. Read-only public-ticker measurement; no prereg
needed because the gate is arithmetic (observable basis vs. observable
costs), not statistical.

## Result: FEASIBLE at $5k — NOT ECONOMIC at current basis

**Capital feasibility (the old blocker): CLEARED.**
- Binance USDT-margined BTC quarterlies: min 0.001 BTC ≈ **$63 notional**
  (BTC/USDT:USDT-260925, -261225). Bybit dated futures same min. A
  $1,000–2,000 carry position is executable at $5k. The
  INFEASIBLE-AT-CURRENT-CAPITAL verdict no longer holds.

**Economics (measured 2026-08-13 17:53Z, BTC spot $63,200):**

| contract | days | premium | annualized gross |
|---|---|---|---|
| BTC Sep-26 | 42 | +55.9 bps | **+4.86%** |
| BTC Dec-26 | 133 | +169.2 bps | **+4.64%** |
| ETH Sep-26 | 42 | +29.1 bps | +2.53% |
| ETH Dec-26 | 133 | +104.5 bps | +2.87% |

Cost side: 2 entry legs (spot buy + future sell) + exit at expiry
convergence ≈ 30–60 bps round trip taker+slip. Net annualized:
- BTC Sep: ~1.3–2.2% — **below stablecoin yield (~4–5%)**
- BTC Dec: ~3.6–3.8% — **at/below stablecoin yield**
- ETH: worse on both.

And the carry is NOT risk-equivalent to stable yield: the ledger's existing
row carries the AEA 2026 anchor — **quarterlies fall 8–10% vs spot in stress
events** (perps ~3%), i.e. the short-future leg has adverse stress convexity,
plus legging and margin-management risk.

## Verdict

**Same shape as F1: validated mechanism, wrong regime.** The contango is
real but compressed to roughly the risk-free rate — consistent with the
compressed-funding regime that idles F1 (the two are the same carry priced
in different instruments; no-arbitrage keeps them linked).

**Trigger condition (recorded so the next look is a comparison, not a
rediscovery):** annualized NET basis (gross − 50 bps cost) sustained
**> 8%** on a front or next quarterly. Historically this occurs in bull
expansions (2021: 20–40%+; brief 2024-25 episodes >10%). Below that, holding
USDT in earn strictly dominates on both return and risk.

**Re-check cadence:** monthly, alongside the liq-cascade screen-41 re-run —
a ~30-second public-ticker read. No scheduled task, no probe, no config
change — the basis moves slowly and a monthly glance is proportionate to
the effort cap.

## Ledger effect
The quarterly-basis row's capital blocker is superseded (feasible at $5k);
its economic NO_GO **stands** at current basis. No reopen; trigger recorded.
