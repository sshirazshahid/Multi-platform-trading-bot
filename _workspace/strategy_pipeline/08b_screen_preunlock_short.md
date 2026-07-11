# 08b — Screen: Pre-Unlock Short, Capital-Scaled (NEW)

Screener: edge-screener | Phase 2 of strategy-evidence-pipeline | 2026-07-11
Candidate 2 of 07_scout_candidates_2026-07-11.md. Research paths only; no live code touched.

---

## PRE-REGISTRATION (frozen 2026-07-11, written BEFORE any sourcing attempt or computation — never edited after)

### Hypothesis
Shorting the perp of tokens facing large vesting-cliff unlocks — restricted to the externally
measured effect zone (unlock value ≥ 10% of market cap, non-insider allocations, early-stage
tokens preferred) — entering in the pre-unlock window and exiting at the unlock date, sized with
the capital-scaled template, produces positive after-cost account-level PnL that clears the
frozen gates. External anchor: unlocks.app 236-event study (2026-06-29), pre-event drift
−14.7% (1 month) / −9.1% (2 weeks), post-unlock movement minimal.

### Sizing template (reused EXACTLY from the listing-short CONFIRMED_GO, rev3)
- 3% of account equity per stake, 12% max concurrent gross exposure, UNLEVERED.
- All metrics on the ACCOUNT equity curve with concurrent mark-to-market (not per-stake).
- Skipped events (caps full, no perp, no funding coverage) are counted and reported.

### Window variants (frozen; ALL counted for DSR/PBO; the screen adjudicates pre vs post)
| Variant | Entry | Exit |
|---|---|---|
| W1 | T−28d (00:00 UTC bar) | T (unlock timestamp, nearest 1h bar) |
| W2 | T−14d | T |
| W3 | T | T+3d (the superseded post-unlock claim, kept as the adjudication arm) |

n_trials for Deflated Sharpe = 3.

### Event universe
All unlock events from an honestly sourced historical calendar with: date inside the local
OHLCV span (2023-06 → 2026-06, exit must resolve by 2026-07-04), unlock value ≥ 10% of
market cap at event time, non-insider allocation where the source labels it, AND a perp listed
on binance/bybit/bitget with LOCAL funding history covering the hold window. Events without
funding coverage are EXCLUDED AND COUNTED — never approximated.

### Prices and costs (same registered model as the listing-short screen — not softened)
- Entry/exit at local 1h closes (`data/ohlcv_cache/<BASE>-USDT_1h.parquet`), same
  spot-as-perp-proxy convention as the rev2/rev3 listing-short screens (stated divergence).
- Perp taker fee per side from `config.FEE` (binance 5 bps, bybit/bitget 6 bps), 5 bps
  slippage per crossing.
- Realized funding charged settlement-aligned to the SHORT from `data/funding_history/`
  per venue-symbol prints — shorts on negative-funding tokens PAY. This is the event-short
  killer and is charged in full.

### Frozen gates (identical to the listing-short CONFIRMED_GO; never loosened)
- DSR ≥ 0.10 (n_trials = 3), PBO ≤ 0.5 (CSCV), OOS-WR ≥ 0.55 (walk-forward, embargo+purge),
  AUC ≥ 0.60 for a pre-outcome discriminating score (frozen before outcomes are seen),
  MC on the ACCOUNT equity curve: P(total > 0) ≥ 0.95 AND maxDD p95 ≤ 0.25.
- NaN fails closed. Minimum n: ≥ 30 qualifying resolved events, else INSUFFICIENT_DATA.

### What NO_GO looks like (declared in advance)
Best window variant fails any single gate; or PnL concentrates in a handful of events such
that the MC bootstrap breaches the maxDD gate (the exact failure mode that made the
full-stake listing-short CONFIRMED_NO_GO); or funding paid erases the gross drift.

### Calendar sourcing protocol (pre-registered; runs AFTER this section is frozen)
Historical unlock dates MUST come from a real calendar source. Routes to attempt, keyless/free:
1. DefiLlama emissions endpoints (re-verify the 402 the scout hit on 07-11).
2. CoinMarketCap keyless data-api (api.coinmarketcap.com/data-api) token-unlock routes.
3. Tokenomist (ex-TokenUnlocks) public/keyless API surface.
4. unlocks.app public JSON/Next.js data routes (source of the 236-event study).
5. CryptoRank public v0 endpoints.
HARD RULE: if none yields a usable HISTORICAL calendar (dates + unlock size/mcap), the verdict
is INSUFFICIENT_DATA naming the gap. Unlock dates are NEVER inferred from price action —
that is leakage/fabrication by construction. Upcoming-only calendars do NOT unblock a
historical screen (no past events → no backtest); they would only support a forward probe.

---

## SOURCING ATTEMPTS (executed after freeze; raw results, 2026-07-11)

| # | Route | Result |
|---|---|---|
| 1a | `api.llama.fi/emissions` | **HTTP 402** — "Upgrade to the paid API plan" (scout's 07-11 finding re-verified) |
| 1b | `api.llama.fi/emission/aptos` (per-protocol) | **HTTP 402** — same paywall |
| 2a | `api.coinmarketcap.com/data-api/v3/token-unlock/detail?slug=aptos` | **404** (route does not exist keylessly) |
| 2b | `api.coinmarketcap.com/data-api/v3/token-unlock/latest` | **404** |
| 2c | `coinmarketcap.com/currencies/aptos/unlocks/` (page scrape for embedded data-api routes) | **404, empty body** to curl |
| 3a | `tokenomist.ai/api/unlocks` (followed 307) | **200 but a Figma-Sites marketing page** — no API; real app is key-gated |
| 3b | `api.tokenomist.ai/v1/unlocks` | **404** `{"message":"Not Found"}` |
| 4a | `unlocks.app/api/projects` | **404** Next.js error shell (app served from space.tokenomist.ai bucket — unlocks.app is Tokenomist-affiliated) |
| 4b | `unlocks.app/` homepage route mining | only `/api/code-preview/unlocks` found → **404** |
| 5a-c | `api.cryptorank.io/v0/{token-unlock/upcoming, coin-unlock, /}` | **404** structured NestJS errors; documented CryptoRank API is key-gated |

Additional note: even a working widget/upcoming endpoint would NOT have unblocked this screen —
the pre-registered rule requires a HISTORICAL calendar (past events with dates + size/mcap);
upcoming-only feeds support at most a forward probe.

The unlocks.app 236-event study article itself does not publish a machine-readable event list;
transcribing its handful of named examples would be a cherry-picked, selection-biased subset —
refused.

## VERDICT: INSUFFICIENT_DATA

**Exact blocking gap:** no free/keyless historical token-unlock calendar (event dates +
unlock-value/market-cap ratio + allocation type) covering 2023-06 → 2026-06. DefiLlama's
emissions endpoint — the only known bulk historical source — is HTTP 402 (paywalled) as of
2026-07-11; CMC/Tokenomist/unlocks.app/CryptoRank keyless routes all dead-end (table above).
Unlock dates were NOT approximated from price action (pre-registered refusal: that is
leakage/fabrication by construction).

**Unblock routes (owner decision, not screener action):**
1. One month of DefiLlama paid API (or any licensed unlock dataset) → the frozen
   pre-registration above runs as written, no re-registration needed.
2. Start a forward unlock-calendar logger (scheduled scrape of upcoming-unlock pages into
   `data/unlock_calendar/`) → supports a future log-only forward probe after ~3–6 months of
   accumulation; does NOT unblock the historical screen.

The pre-registration above is FROZEN: if a calendar is later secured, the screen runs with
these exact windows, sizing, costs, and gates.

---

## EXECUTION-FREEZE ADDENDUM (2026-07-11, written BEFORE the re-run executes — audit 08d unblock)

Audit 08d finding C2-a OVERTURNED the blocking claim: the same emissions dataset is served
keylessly from `https://defillama-datasets.llama.fi/emissionsProtocolsList` +
`/emissions/{slug}` + `/emissionsIndex`. **Only the calendar's transport changes** —
hypothesis, windows W1/W2/W3, sizing, costs, and gates above are untouched. The following
construction details are frozen NOW, before any outcome is computed:

1. **Event extraction:** `metadata.events[]` entries with `unlockType == "cliff"` and
   timestamp in [2023-06-01, 2026-06-01) UTC. Same-timestamp allocations for a protocol are
   MERGED into one event. Event tokens = Σ `noOfTokens` over merged allocations EXCLUDING
   `category ∈ {"insiders", "noncirculating"}` — insiders per the frozen "non-insider where
   labeled" rule; noncirculating (treasury/reserve) because those tokens do not enter float
   and counting them would manufacture qualifying events. Unlabeled ("Uncategorized")
   allocations are retained (the filter applies "where the source labels it").
2. **≥10%-of-mcap filter:** event_tokens / circ_documented(T) ≥ 0.10, where
   circ_documented(T) = Σ cumulative `unlocked` across `documentedData.data` labels NOT
   mapped to `categories["noncirculating"]`, as-of T (daily step series). Price cancels in
   value/mcap, so no price enters the filter. Per-protocol calibration of circ_documented
   (now) vs emissionsIndex `circSupply` (now) is recorded as a data-quality diagnostic,
   NOT used as a filter.
3. **Symbol mapping:** gecko_id → ticker via CoinGecko `/api/v3/coins/list` (keyless), then
   cross-referenced against USDT-linear-perp bases on binance/bybit/bitget (keyless ccxt
   `load_markets`). Protocols without a perp on any of the 3 venues are excluded and counted.
4. **Venue choice per event:** first venue in the fee-preference order (binance 5 bps,
   bybit 6 bps, bitget 6 bps) whose LOCAL realized funding history covers the hold window
   (`window_funding_covered`, same tolerance as the listing-short screens). Data-filling:
   `data/funding_history/` is extended to the calendar-matched bases with the SAME harvest
   machinery (`scripts/backfill_funding_history.py` functions) — real prints only.
5. **Bars:** entry for W1/W2 at the first 1h bar with ts ≥ (T floored to 00:00 UTC) − 28d/14d;
   exit at the 1h bar whose open ts is NEAREST to T. W3 enters at the nearest-to-T bar and
   exits at the first bar ≥ T+3d. Any leg farther than 12h from its target ts → event
   excluded and counted (this also handles the local OHLCV end at 2026-06-14 — a data-end
   exclusion, not a window change).
6. **AUC discriminating score (frozen pre-outcome, before any return is computed):**
   score_i = tanh(ratio_i / 0.20) + 10 × funding_entry_i, where ratio_i is the §2 unlock
   ratio and funding_entry_i is the chosen venue's realized funding print nearest the entry
   bar (positive funding favors the short). Label = per-event net return > 0.
7. **Account curve / MC:** per-event account returns = 0.03 × net short return, entry-ordered
   under the 4-concurrent cap (`apply_concurrency_cap`); MC = block bootstrap over that
   chronological sequence (`core/decision/monte_carlo.monte_carlo_trade_sequence`). Per the
   rev3-audit precedent, the realized CONCURRENT-MTM daily account curve is also built, and
   the maxDD gate passes only if BOTH mc_maxdd_p95 ≤ 0.25 AND realized concurrent-MTM
   maxDD ≤ 0.25 (stricter direction only — never looser).

No result below this line existed when this addendum was written.

---

## EXECUTED RESULTS (2026-07-11, run AFTER the freeze + addendum above)

Data landed: `scripts/backfill_unlock_calendar.py` → `data/unlock_calendar/` (147
perp-matched symbols, 10,330 merged in-window cliff events, 195 with ratio ≥ 10%; 24
protocols unmapped to a ticker, 67 with no perp on the 3 venues, 2 bucket fetch errors —
all counted). Funding store extended 137 → 492 venue-symbol CSVs (same harvest machinery,
real prints only). Screen: `research/screen_preunlock_short.py`. Accepted-event dump for
the audit: `_workspace/tmp_phase2_run/diag_08b.json`.

Qualifying events (ratio ≥ 10%, tokens > 0, in window): 171 after OHLCV-file gate (24 lost
to missing files). Per-window ~130-137 further excluded for NO PRICE WINDOW — local 1h
OHLCV depth (many later-listed tokens carry only ~1 year of local bars). This exclusion is
outcome-independent (file depth + event date only). Funding coverage after the extension:
ZERO events excluded for funding.

### Gate table (frozen thresholds; all three windows evaluable)

| Gate | W1 (T−28d→T) | W2 (T−14d→T) | W3 (T→T+3d) |
|---|---|---|---|
| n accepted (≥30) | 32 (2 capped) | 36 (1 capped) | 41 |
| mean net/event > 0 | **+9.8%** ✅ | **+10.0%** ✅ | +5.0% ✅ |
| win rate ≥ 0.55 | 0.750 ✅ | 0.639 ✅ | 0.683 ✅ |
| OOS-WR ≥ 0.55 | 0.875 ✅ | 0.750 ✅ | 0.719 ✅ |
| DSR ≥ 0.10 (n_trials=3) | 0.847 ✅ | 0.999 ✅ | 0.975 ✅ |
| PBO ≤ 0.5 (across windows) | 0.452 ✅ | 0.452 ✅ | 0.452 ✅ |
| AUC ≥ 0.60 (frozen score) | 0.693 ✅ | 0.682 ✅ | **0.591 ❌** |
| MC P(total>0) ≥ 0.95 | 0.959 ✅ | 0.996 ✅ | 1.000 ✅ |
| MC maxDD p95 ≤ 0.25 | 0.062 ✅ | 0.035 ✅ | 0.016 ✅ |
| realized MTM maxDD ≤ 0.25 | 0.073 ✅ | 0.026 ✅ | 0.012 ✅ |
| funding paid (mean/event) | −1.50% (59% neg) | −0.35% | −0.18% |

### Adversarial self-checks run BEFORE accepting the verdict (all outcome-independent)

- **Not market beta:** control basket (liquid majors) mean ≈ 0.0% (W1) / −1.3% (W2) over
  the same windows; unlock tokens UNDERPERFORMED control by +10.8% (W1) / +8.9% (W2),
  positive in 73% of events. The short PnL is idiosyncratic to the unlock tokens.
- **Multi-regime:** accepted events span 2023-07 → 2026-05 (2023: 8, 2024: 2, 2025: 7-10,
  2026: 17). 2024 is nearly absent (OHLCV depth) — stated.
- **No single-event dependence:** largest |event| in W1 is a LOSS (BASED −61.5%, pumped
  into unlock); largest winner = 13-15% of total PnL. Losses are in-sample and real
  (APT 2023-11 −58%, SUI 2023-12 −34%).
- **Pre vs post adjudication (frozen):** W3 (post-unlock) is the weakest arm and fails the
  AUC gate — consistent with the external anchor's "post-unlock movement minimal".

### Verdict: **GO** (W1 and W2 clear ALL frozen gates)

The pipeline's second-ever GO. Binding caveats carried forward:
1. Spot-as-perp-proxy prices (frozen convention, stated divergence).
2. 137/171 qualifying events unmeasurable from local OHLCV depth — the verdict covers the
   measurable slice; a deeper OHLCV backfill would raise n (both directions possible).
3. Symbol concentration: SUI 7/32 W1 events; monthly-cliff tokens recur.
4. Calendar/perp-listing survivorship (delisted tokens absent) — conservative for shorts.
5. Ratio denominator = documented circulating supply (median calibration 0.91 vs index,
   p10 0.53; recorded per protocol, not filtered).
6. n = 32/36 sits just above the frozen 30 floor.
7. Per pipeline rules this GO authorizes AT MOST a log-only shadow probe after
   honesty-auditor review — it is NOT a live-trading authorization.
