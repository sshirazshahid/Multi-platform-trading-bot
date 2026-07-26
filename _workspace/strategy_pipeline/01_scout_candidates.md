# 01 — Scout Candidate Briefs (condensed from 2026-07-08 deep research)

## Candidate A: Cross-venue funding-rate dispersion (CEX-CEX, same coin)
- Novelty-vs-ledger: ADJACENT-to-carry (screen-eligible; extends the validated F1 family)
- Mechanism: structural funding differences between venues (different user flows/inventory);
  long the perp where funding is low/negative, short where high — delta-neutral, collect the spread.
- External evidence: peer-reviewed family support (ScienceDirect 2025, 60 scenarios, <=115.9%/6mo,
  max loss 1.92%); practitioner spread measurements 0.01-0.025%/8h between venue pairs (single source).
- Cost reality at $420: FOUR fee legs per round trip (2 open + 2 close) + spread persistence risk;
  spread must clear ~4x maker (or worse taker) legs amortized over holding period.
- LOCAL DATA (inventoried 2026-07-09): data/funding_carry/{binance,bybit,bitget}_{BTC,ETH}.csv;
  data/derivs_history.jsonl (hourly multi-venue harvester snapshots since ~2026-05-29, 1.3MB);
  data/funding_cache/*.parquet (31 coins, 8h, single-venue); data/funding_oi/*.csv (majors, 2019+, Binance).

## Candidate B: Post-listing perp short
- Novelty-vs-ledger: NEW (event-driven; not a refuted family)
- Mechanism: listing hype is realized pre-listing; post-listing supply/attention decay.
  Short the perp for a fixed window from listing (or day-1 close), sized small, SL-capped.
- External evidence: FMZQuant measured ALL 86 Binance perp listings of 2023 (4h bars, 150d):
  near-universal decline, worse than index. Pre-cost, 2023 — must revalidate on 2024-26 after costs.
- KILLER RISK to test honestly: new listings often have deeply NEGATIVE funding — shorts PAY;
  realized funding must be charged to the short leg from actual data, not averages.
- LOCAL DATA: data/ohlcv_cache/*.parquet (635 symbols, 1h; listing date = first candle timestamp —
  the FMZQuant method); funding for the cost leg from funding_cache/derivs_history where covered;
  listings WITHOUT funding coverage are excluded and counted (never guessed).

## Not dispatched this run
- Post-unlock short: needs an unlock-calendar source not present locally (INSUFFICIENT_DATA by inspection).
- Settlement-window timing: queued behind A (shares the funding datasets; A's verdict informs it).
