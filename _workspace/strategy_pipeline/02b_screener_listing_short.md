# 02b — Screener Verdict: Candidate B (Post-listing perp short)

Screener: edge-screener agent · Date: 2026-07-09 · Data: local only (no synthetic)

---

## PRE-REGISTRATION (frozen before any screen code ran)

**Hypothesis (directional, testable):** Listing hype is realized pre-listing; after the
perp lists, supply/attention decay drives price down faster than the broad market. A
fixed-window SHORT opened at the day-1 close of a newly-listed USDT perp earns a positive
**after-ALL-cost** return, and beats an equal-weight market control over the same calendar
window. FMZQuant measured this pre-cost on 86 Binance 2023 listings (near-universal decline);
this screen revalidates on 2024–2026 listings **after costs, including the funding leg**.

**Killer risk being tested (pre-declared):** new listings frequently carry deeply NEGATIVE
funding → a short PAYS funding every settlement. Realized funding must be charged to the short
leg from actual per-listing funding history. It is NOT optional and NOT to be averaged/guessed.

**Universe:** `data/ohlcv_cache/*_1h.parquet`. Listing date = first-candle timestamp (FMZQuant
method) **only for symbols whose first candle is idiosyncratic**. Backfill-cluster caveat is
enforced: any first-candle timestamp shared by ≥3 symbols is a cache-backfill-start artifact
(a bulk backfill truncates many pre-existing coins to the same start hour) → those symbols are
EXCLUDED and counted; their true listing date is unknown. Only idiosyncratic first candles in
`[2024-01-01, 2026-06-01]` with ≥30d of post-listing data are eligible listings.

**Mandatory funding-coverage gate (pre-declared exclusion):** a listing is screen-eligible ONLY
if per-listing realized funding is locally available (`data/funding_cache/<COIN>-USDTUSDT_8h.parquet`
or `data/derivs_history.jsonl`) covering its holding window. Listings WITHOUT funding coverage are
EXCLUDED and COUNTED — never charged an assumed/averaged funding number. This is the honest test of
the killer risk.

**Entry / horizons (ONE pre-registered family):** SHORT at the day-1 close = close of the bar at
`first_ts + 24h`. Exit at close of `entry_ts + H·24h` for **H ∈ {7, 30, 90} days**. Three horizons =
**3 variants in one family** → multiplicity `n_trials = 3` for DSR/PBO. No other entry/exit variants
are tried; if any are added later they are counted in multiplicity.

**Cost model (charge ALL of it — authoritative from `config.py`):**
- Fees: `config.FEE['futures_taker'] = 0.0005` (5 bps) per side. Short entry (sell) + close (buy) are
  taker market legs → 2 × 5 bps.
- Slippage: `config.SLIPPAGE['pct_open'] = 0.0005`, `pct_close = 0.0005` (5 bps each side).
- Round-trip fee+slippage = 2 × (5+5) = **20 bps** deducted from the short return.
- Funding: charged to the short leg as `funding_return_short = +Σ rate_i` over held settlements
  (short RECEIVES positive funding, PAYS negative funding). Modeled from realized history only.

**Control:** equal-weight basket of liquid majors with full history (funding_cache 31-coin set ∩
available `_1h` OHLCV), close-to-close over each listing's identical `[entry_ts, exit_ts]` window.
Reported both as absolute short and as market-relative underperformance `(control_ret − listing_ret)`.

**Frozen gates (never loosened — `core/promotion_gate.py`):** DSR ≥ 0.10, PBO ≤ 0.5, OOS-WR ≥ 0.55;
walk-forward with embargo+purge; Monte-Carlo capital-preservation (P(total>0) ≥ 0.95, maxDD p95 ≤ 0.25).
NaN fails closed.

**What NO_GO looks like (pre-declared):** after-cost (incl. funding) mean short return ≤ 0, OR WR < 0.55,
OR DSR < 0.10, OR PBO > 0.5, OR it fails to beat the control. **What INSUFFICIENT_DATA looks like
(pre-declared):** the mandatory funding-coverage gate excludes so much of the universe that the
after-cost-with-funding sample is too small to evaluate the killer risk (target: report exact n; if
the funding-charged eligible n is ~0, the pre-registered screen cannot be executed → INSUFFICIENT_DATA
naming the exact harvest command). **What GO looks like:** all frozen gates pass on the funding-charged
sample AND it beats the control.

---

## RESULTS

**VERDICT: INSUFFICIENT_DATA** (a pre-registered outcome — see "What INSUFFICIENT_DATA looks like").
The mandatory funding-coverage gate excludes **103/103 (100%)** of the eligible listing universe, so
the after-cost-**with-funding** sample is **n = 0**. The pre-declared killer risk — a short PAYS the
deeply-negative funding that new listings typically carry — is **unmeasurable from local data**.
No GO/NO_GO on the after-cost thesis can be honestly issued.

Artifacts: screen `research/screen_listing_short.py` · tests `tests/test_screen_listing_short.py`
(9/9 pass) · this file.

### Universe (backfill-cluster caveat VALIDATED and enforced)
- 525 symbols have `_1h` data. The FMZQuant "first candle = listing date" method is **corrupted by
  backfill truncation**: 8 first-candle timestamps are each shared by ≥3 symbols — bulk-backfill start
  hours, not real listings. The worst: **2025-05-31** (177 + 121 + 52 = **350 symbols** share 3 adjacent
  hours), **2023-05-26** (32), **2025-07-04** (33). These **419 symbols are EXCLUDED** — their true
  listing predates the backfill and is unknown.
- **103 symbols** have an idiosyncratic first candle in `[2024-01-01, 2026-06-01]` (all fall 2025-06 →
  2026-05, exactly the post-backfill window where the first candle IS the real listing) with ≥30d data.
  These are the genuine listings. 11 of the 103 are tokenized-equity/commodity perps (AAPL, TSLA, MSFT,
  NVDAX, COIN, MSTR, XAU, XAG, COPPER, CL, AMZN) that behave unlike crypto hype — flagged.

### Funding coverage (the mandatory gate → the killer)
- Local funding history covers only majors: `funding_cache` = **31 coins**, `derivs_history.jsonl` =
  **8 coins** (ADA BNB BTC DOGE ETH LINK SOL XRP, only since 2026-05-29). None are new listings.
- **Genuine listings WITH funding coverage: 0. WITHOUT (EXCLUDED, counted): 103.** Funding-charged
  eligible n = **0**. Per pre-registration, funding is never guessed/averaged → the trade cannot be
  costed.

### Diagnostic ONLY — fees+slippage charged, **funding UNCHARGED** (NOT gate-eligible)
Charged: 20 bps round-trip (`config.FEE.futures_taker` 5bps + `config.SLIPPAGE` 5bps, each × 2 sides).
Funding = 0 (the missing killer cost). Equal-weight control = 31 majors, same windows.

| Horizon | n | listing price ret (mean) | short net, fees-only (mean / median) | short WR | control ret (mean) | underperf vs ctrl (mean) | listing<ctrl rate |
|--------:|--:|--:|--:|--:|--:|--:|--:|
| 7d  | 103 | −9.4% | **+9.2% / +13.0%** | 68.9% | −1.8% | +7.6% | 68.0% |
| 30d | 102 | −26.2% | **+26.0% / +33.4%** | 78.4% | −9.6% | +16.6% | 77.5% |
| 90d |  87 | −38.0% | **+37.8% / +54.1%** | 78.2% | −25.9% | +12.1% | 71.3% |

The FMZQuant pattern **replicates strongly pre-funding on 2025-26 data**: new listings decline hard and
underperform the majors basket. Diagnostic DSR (fees-only) is high (Pr[SR>0] ≈ 0.95 / 0.9999 / 0.996).

### Why this is NOT a GO (honest reading)
1. **The one cost that decides the trade is entirely unmeasured.** New-listing funding is typically
   negative → the short PAYS it, every 8h, over 21–270 settlements (7–90d). Its sign is **adverse** and
   its magnitude on hype-phase listings can be extreme. A +26% pre-funding 30d edge is not safe from an
   unknown, systematically-negative funding drag — that is exactly the gap the screen exists to close.
2. **Gates NOT EVALUABLE** on the after-cost sample: n=0 → DSR/PBO/OOS-WR/walk-forward/Monte-Carlo are
   NaN → **fail-closed**. The diagnostic stats above are funding-uncharged and therefore inadmissible.
3. **Additional real-world costs unmodeled** (would further erode the diagnostic, never help it):
   day-1 shortability/borrow limits, position caps, and much wider spreads/slippage than 5bps on
   illiquid brand-new perps; touch≠fill on any resting entry. **Survivorship**: the cache holds only
   currently-listed perps — delisted post-listing names are absent (direction ambiguous).

### To unblock → re-run with the killer cost charged
Harvest per-listing realized funding (keyless Binance public endpoint), then wire the screen to read
`data/funding_oi/<SYM>_funding.csv` and charge `funding_sum` to the short leg:

```
venv/Scripts/python.exe scripts/fetch_binance_funding_oi.py \
  --symbols 0G,2Z,ASTER,AVNT,AZTEC,BANK,BARD,... (all 103 listing bases) \
  --since 2025-06-01 --no-oi
```
Caveat: many of the 103 are Bybit/Bitget-only listings absent from Binance USDT-M — those will still
be excluded (and counted). Only listings whose realized funding is locally covered become
screen-eligible; the verdict flips to GO/NO_GO only after the funding-charged gates run.

### Verdict JSON
```json
{
  "candidate": "B — post-listing perp short",
  "hypothesis": "Short newly-listed USDT perp at day-1 close for a fixed window (7/30/90d); post-listing supply/attention decay makes it decline faster than the market, netting a positive after-ALL-cost return.",
  "n": {
    "total_1h_symbols": 525,
    "excluded_backfill_cluster": 419,
    "genuine_listings_ge30d": 103,
    "funding_covered_eligible": 0,
    "funding_uncovered_excluded": 103
  },
  "after_cost_metrics": {
    "note": "funding-charged after-cost sample is n=0; numbers below are DIAGNOSTIC ONLY — fees+slippage charged (20bps round-trip), FUNDING UNCHARGED, NOT gate-eligible",
    "diagnostic_feesonly": {
      "7d":  {"n": 103, "short_net_mean": 0.0917, "short_net_median": 0.1304, "win_rate": 0.689, "underperf_vs_ctrl_mean": 0.0758},
      "30d": {"n": 102, "short_net_mean": 0.2602, "short_net_median": 0.3337, "win_rate": 0.784, "underperf_vs_ctrl_mean": 0.1659},
      "90d": {"n": 87,  "short_net_mean": 0.3781, "short_net_median": 0.5414, "win_rate": 0.782, "underperf_vs_ctrl_mean": 0.1206}
    }
  },
  "gates": {
    "status": "NOT_EVALUABLE",
    "reason": "funding-charged sample n=0 -> DSR/PBO/OOS-WR/walk-forward/Monte-Carlo NaN -> fail-closed",
    "thresholds": {"MIN_DSR": 0.10, "MAX_PBO": 0.5, "MIN_OOS_WR": 0.55}
  },
  "verdict": "INSUFFICIENT_DATA",
  "blocking_reason": "mandatory funding-coverage gate excludes 103/103 eligible listings; the pre-declared killer cost (short pays negative funding) is unmeasurable from local data and never guessed",
  "harvest_command": "venv/Scripts/python.exe scripts/fetch_binance_funding_oi.py --symbols <103 listing bases> --since 2025-06-01 --no-oi",
  "artifacts": {
    "screen": "research/screen_listing_short.py",
    "tests": "tests/test_screen_listing_short.py (9/9 pass)",
    "verdict": "_workspace/strategy_pipeline/02b_screener_listing_short.md"
  }
}
```

