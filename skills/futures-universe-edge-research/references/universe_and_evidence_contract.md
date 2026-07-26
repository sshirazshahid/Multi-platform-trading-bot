# Universe and Evidence Contract

## Point-in-time universe

Persist one record per venue contract and observation time. Required fields are venue, canonical symbol, venue symbol/id, base, quote, settle, linear/inverse, active status, listing time, delisting time if known, contract multiplier, tick size, amount step, minimum amount/notional, margin tier source, observed time, and received time.

Ticker snapshots must include last/mid where available, bid, ask, quote volume, source timestamp, received timestamp, and a data-quality reason when rejected. Depth is side-specific: a buy consumes asks and a sell consumes bids. A sum of both sides is not executable capacity.

Screening should derive 1h, 24h, and 7d percentage returns from the nearest point-in-time snapshots within a documented tolerance. Persist both winners and losers. Use ATR or realized-volatility normalization when comparing different price scales. Absolute `$5–$200` movement is descriptive only and must not drive ranking.

## Candidate evidence JSON

The validator expects this shape. Objects, arrays, booleans, strings, and numbers must use their shown JSON types; numeric strings, non-finite constants, fractional count fields, and non-object roots fail closed. The command reads the evidence file and writes only its JSON result to standard output.

```json
{
  "candidate_id": "venue-neutral-name-v1",
  "market_type": "futures",
  "universe": {
    "point_in_time_master": true,
    "includes_delisted": true,
    "rank_features": ["return_pct", "atr_z", "quote_volume_usd", "spread_bps", "depth_usd"]
  },
  "data": {
    "event_time_observed": true,
    "received_time_recorded": true,
    "mark_index_funding": true,
    "side_specific_book": true,
    "closed_bars_only": true
  },
  "replay": {
    "causal": true,
    "next_event_fills": true,
    "chronological_portfolio": true,
    "funding_basis_liquidation": true,
    "partials_rejects_latency": true,
    "purged_walk_forward": true,
    "untouched_holdout": true,
    "venue_holdout": true,
    "parameter_plateau": true,
    "cost_stress_multipliers": [1.0, 1.5, 2.0]
  },
  "metrics": {
    "independent_trades": 500,
    "profit_factor": 1.25,
    "bootstrap_expectancy_lower": 0.0001,
    "positive_folds": 3,
    "total_folds": 4,
    "pbo": 0.20,
    "dsr": 0.96,
    "dominant_symbol_pnl_fraction": 0.20,
    "stressed_expectancy_positive": true
  },
  "shadow": {
    "matured": true,
    "days": 45,
    "independent_resolved": 150,
    "mismatch_count": 0
  }
}
```

## Minimum interpretation thresholds

These are screening thresholds, not proof of future profit:

- genuine futures data and all required causal/replay flags;
- at least 100 independent trades (prefer 500 intraday);
- profit factor at least 1.20 and bootstrap expectancy lower bound above zero;
- at least 3 positive folds out of 4 (or 75% for more folds);
- aligned PBO at most 0.25 and DSR at least 0.95;
- dominant symbol/regime contribution at most 25% of PnL;
- positive expectancy at stressed costs and a parameter plateau;
- at least 30 mature shadow days, 100 independent resolved setups, and zero execution mismatches.

Failing any field leaves the candidate `RESEARCH_ONLY`. Passing all fields makes it eligible only for a separate manual controlled-live review; it does not authorize activation.
