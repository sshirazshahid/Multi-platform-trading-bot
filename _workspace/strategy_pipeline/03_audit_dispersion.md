# 03 — Audit Findings: Candidate A (Cross-venue funding-rate dispersion)

Agent: honesty-auditor · Date: 2026-07-09 · Input: `_workspace/strategy_pipeline/02a_screener_dispersion.md`
Artifacts audited: `research/screen_funding_dispersion.py`, `tests/test_screen_funding_dispersion.py`, `data/funding_carry/*.csv`

## VERDICT: INSUFFICIENT_DATA_CONFIRMED

The screener's INSUFFICIENT_DATA call survives every refutation attempt. The frozen
gates genuinely cannot be evaluated at n=16 and fail closed correctly. The
supporting-only after-cost-negative finding independently reproduces and is
structural (survives even a zero-fee stress test). Zero unresolved findings that
could flip the verdict.

## Attack surfaces (each attempted refutation → result)

1. **Look-ahead / leakage — CLEAN.** Direction pick (long lower-mean venue) is
   in-sample and disclosed as an optimistic lookahead; it still loses, which
   *strengthens* the negative finding. Carry is indexed by settlement timestamp
   (`next_funding_ts`); realized per-settlement rates are used, not averages. The
   model amortizes only ONE 4-leg round-trip over the entire 16-settlement hold
   while collecting carry on all 16 — the most generous possible framing for the
   strategy, and it still loses. No warehouse-as-of-now contamination (static
   harvested CSVs with explicit settlement ts). Every disclosed shortcut favors the
   strategy; none inflates against it.

2. **Cost realism — CLEAN.** Round-trip = `2·fee_long + 2·fee_short + 4·slippage`
   (all 4 legs charged). Fees match `config.FEE` exactly (binance 5/2bps, bybit
   6/1bps, bitget 6/2bps taker/maker). Slippage 5bps/fill matches `config.SLIPPAGE`
   pct_open/pct_close. Maker is flagged as an UNVERIFIED best-case (touch≠fill);
   taker is the honest default. **Stress test:** even at zero fees, the slippage
   floor alone (4×5bps = 20bps RT) exceeds the max gross carry anywhere (BTC 9.7bps
   over 5d) → structurally negative regardless of fee assumptions.

3. **Multiplicity — HONEST.** 12 variants reported (6 venue-pairs × 2 fee models)
   plus the disclosed in-sample direction pick. DSR/PBO correctly declared
   NOT_EVALUABLE rather than fabricated on a handful of points.

4. **Sample sufficiency / survivorship — CLEAN.** 16 settlements/coin, 2 coins, one
   5.0-day window (2026-07-04→07-09, verified from epochs; identical settlement grid
   across all 3 venues, no dropped/duplicate rows). This is << the pre-registered
   floor (60/coin) and << the repo's real `MIN_OOS` futures floor of **200** in
   `core/promotion_gate.py`. The 60-floor is therefore *looser* than the actual
   gate — it cannot have been inflated to manufacture an INSUFFICIENT verdict.
   Survivorship N/A (BTC/ETH majors; not a listing/universe screen).

5. **Charter compliance — CLEAN.** `git status --short`: only untracked
   `research/`, `tests/`, `_workspace/` files. No `core/` or `config.py` edits, no
   commits, no live-path changes, no WIDEN-SL.

## Independent verification (recomputed without the screen's own code)
- BTC bitget-long/binance-short gross carry = **9.7bps** (mean 0.606/8h) — matches.
- ETH bitget-long/binance-short gross carry = **3.03bps** (mean 0.19/8h) — matches.
- Per-venue mean funding confirms the direction pick shorts the higher-funding venue.
- Best case anywhere = BTC maker net 9.7 − 28 = **−18.3bps** — matches verdict.
- `pytest tests/test_screen_funding_dispersion.py` → **12 passed**.
- Frozen thresholds cited (DSR≥0.10, PBO≤0.5, OOS-WR≥0.55) match
  `core/promotion_gate.py` verbatim.

## Findings (severity-ranked)
- None blocking. The four screener self-challenges are all resolved in its favor.
- (INFO, non-blocking) The 5-day, 2-coin, single-window sample means the
  supporting-only after-cost table has no statistical standing on its own — but the
  screener already labels it supporting-only and does not lean on it for the verdict.
  Correct handling.

## Disposition
INSUFFICIENT_DATA is the honest label: the gates are not computable at n=16, so
fail-closed is correct (NO_GO would falsely imply the gates ran and failed on merit).
Harvest path to reopen is exact and non-synthetic:
`venv\Scripts\python.exe scripts\harvest_funding_carry.py` (schedule HOURLY; reach
≥60 settlements/coin, ideally the gate's 200-OOS floor, before re-screening). No
ledger row is added (INSUFFICIENT_DATA ≠ refuted family). No capital is at risk from
this verdict.
