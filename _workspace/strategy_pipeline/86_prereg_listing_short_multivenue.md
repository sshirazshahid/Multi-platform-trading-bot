# 86 — Prereg: new-to-venue listing-short OBSERVATION arms (bybit, bitget)

Registered BEFORE any outcome exists on these arms. This file is committed
alone, before the implementing code activates; its sha256 is stamped in the
commit message and re-verifiable at any time with
`python -c "import hashlib;print(hashlib.sha256(open('_workspace/strategy_pipeline/86_prereg_listing_short_multivenue.md','rb').read()).hexdigest())"`.

## Directive and type

Owner, 2026-08-19: "Implement those strategies we are doing PAPER trading for
evidence gathering so when we go live everything is sorted. PAPER trading can
be done on all connected exchanges."

Log-only forward OBSERVATION arms — the same owner-directed shadow-probe
pattern as TSMOM (2026-07-11), breakout (2026-07-11), and pullback
(2026-07-22). **NOT a pipeline GO.** No after-cost screen exists for these
populations, and none is claimed. Nothing registered here can place an order.

## Hypothesis under observation — and why the expectation is NO_GO

The binance rev3 listing-short screen (the pipeline's first CONFIRMED_GO,
capital-scaled 3%/12% variant) faded day-1 price discovery on FIRST-EVER perp
listings: 88 funding-charged binance listings, WR 0.75–0.81, DSR≈1, PBO 0.09.

A bybit or bitget "new listing" is usually a token ALREADY trading elsewhere,
so the day-1 discovery pump the screen faded is largely ABSENT in these
populations. The 2026-08-19 lane audit made this explicit: extending venues is
a DIFFERENT POPULATION, not more samples of the screened one. EXPECTATION:
NO_GO or materially weaker effect. These arms exist to MEASURE that, so venue
breadth becomes evidence instead of narrative. A NO_GO outcome is a successful
measurement and is pre-accepted now.

Context that motivates observation despite the expectation: binance's own
crypto-native listing rate has collapsed to ~1.5/month (51 of 53 recent
listings were tokenized equities, out of crypto scope), so the frozen lane
alone needs ~10 months to reach the 30-resolved floor. Watching all three
venues raises the OBSERVED event rate without touching any threshold.

## Frozen parameters — identical to the binance lane; population is the ONLY variable

- Detection: hourly set-diff of the venue's active USDT-M perp universe
  against a PER-VENUE state file (`data/shadow_listing_state_<venue>.json`).
- Entry: short at the first observed close after detection; horizons 7d & 30d.
- Sizing: 3% account stake per event, UNLEVERED; 12% / 4-concurrent cap per
  horizon (charter §2-derived caps, unchanged).
- Frozen score, never re-tuned post-outcome: tanh(pump/0.50) + 10 × day-1
  funding print.
- Crypto-only scope: SKIP_NOT_CRYPTO via venue metadata, exactly as binance
  (tokenized equities/ETFs/FX are out of scope on every venue).
- Shortability: `classify_shortability` (book, else last+quoteVolume;
  no-book AND no-trade fails CLOSED; inactive always fails).
- Time tolerance, funding accounting, MTM logging: unchanged from the
  binance lane implementation.

## Non-pooling (binding)

1. `model_version = listing_short_probe_<venue>_v1` and a venue-suffixed
   agent id for non-binance instances. The binance instance keeps its exact
   historical identity (`listing_short_probe_v1`, unsuffixed) — byte-compatible
   with all accrued history.
2. `shadow_listing_probe` rows gain a nullable `venue` column. Historical
   rows are NULL and are, by construction, binance.
3. The FROZEN binance funnel lane counts only `venue IS NULL OR
   venue='binance'` rows. New venues get their own lanes
   (`listing_short_bybit`, `listing_short_bitget`) and never pool into the
   frozen lane or each other.
4. Cross-listed bases can appear on multiple venues. The same base entering
   on two venues is PSEUDO-REPLICATION of one event; any future screen must
   cluster by base, and the lanes are never summed as independent evidence.
5. Promotion path PER venue-arm: ≥30 RESOLVED forward events + the frozen
   `core/promotion_gate.py` criteria + explicit owner sign-off. A passing
   result would still require a NEW after-cost screen for that venue's
   population — this observation lane is not a screen and cannot GO.

## Falsification of the expectation

Forward WR/after-cost expectancy on ≥30 resolved events for a venue-arm
comparable to the binance screen band (WR 0.75–0.81, positive after-cost
expectancy) would falsify the "no discovery to fade" expectation and justify
writing a real pre-registered screen for that population. Anything less
confirms it, closes the arm, and the ledger records the measurement.
