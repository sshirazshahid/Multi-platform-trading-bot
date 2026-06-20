# Pair & Strategy Research Roadmap — 2026-06-20

**Status: roadmap, not results.** This environment has no exchange API keys and a
restricted network egress, so live OHLCV could not be fetched here. Per
`CLAUDE.md` §5 (do not invent market reasons), this document specifies *what to
test and how* — it does not claim edge that wasn't measured. Run the harnesses
below where keys/network are available; record after-cost outcomes.

## 1. Where the edge stands (established, do not re-litigate)

Every independent forensic pass converges on **no exploitable edge after costs**:

| Study | Sample | Finding |
|-------|--------|---------|
| Scoring-engine forensic (`docs/superpowers/specs/2026-05-25-...`) | 284 trades | WR 45.8% vs 65.5% breakeven; PF 0.44; score anti-monotonic |
| Exec-cost audit (`research/exec_cost_and_agent_upgrade_2026_05_29.md`) | 498 trades | −$110.95 PnL = ~−$99 alpha + ~−$12 cost (≈89% missing edge) |
| Scalp edge (`research/scalp_edge_finding_2026_05_28.md`) | mechanical | No class/timeframe/symbol set clears breakeven after costs |
| TP accuracy (`tasks/todo.md`) | 495 trades | Alpha flat; naive +0.21R was a BTC-beta artifact |
| Leverage escalation (`data/decisions/2026-06-06-...`) | — | MCP score corr ≈ −0.008 with outcome → escalation disabled |

Implication: **the lever that works is cost reduction (maker-only), not more
prediction.** Any new pair/strategy must be judged on after-cost edge, and any
new prediction layer (agents/MCP/model) stays log-only until it clears the
honest gate (`core/promotion_gate.py`: MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55,
AUC≥0.60).

## 2. New pairs / coins to screen (futures, USDT perps)

Run dynamic discovery + the liquidity/quality filter, then the range/chop gate:

```bash
TRADING_MODE=all python -c "from core.pair_discovery import discover_all; \
import json; print(json.dumps(discover_all(['Binance','Bybit','Bitget']), indent=2))"
```

`core/pair_discovery.UniverseFilter` already enforces: spread ≤0.5%, ATR
0.15–12%, depth ≥$2k, Kaufman efficiency ≥0.20 (tunable `MIN_TREND_EFFICIENCY`),
≥2% 10-day range. Candidate buckets worth screening beyond the current 31-symbol
set (all subject to the filter, none added to the live whitelist without edge):

- **High-efficiency majors/L1s** already core: BTC, ETH, SOL, BNB, XRP — baseline.
- **Liquid L2/infra**: ARB, OP, SUI, SEI, TIA, INJ — already present; re-screen
  efficiency monthly (regime-dependent).
- **Screen-in candidates to evaluate**: high-volume perps not yet in the set
  (e.g. screen the top-N by 24h volume each venue via discovery). Only those that
  pass `UniverseFilter` AND show ≥breakeven after-cost in §3 should graduate.
- **De-prioritize**: low-efficiency meme perps (PEPE/WIF) for trend strategies;
  they pass volume but fail Kaufman efficiency — keep only if a mean-reversion
  variant shows after-cost edge.

## 3. Futures strategies to (re)test

The backtest engine is verified working (synthetic smoke in
`tests/test_strategies_smoke.py`). Run each with real data:

```bash
python backtest_v3.py --symbol BTC/USDT --days 90      # current scoring engine, OOS split
python auto_backtest.py --days 60                       # 6-strategy sweep, all pairs (now fixed)
python strategy_lab.py                                  # isolated named-strategy lab
python run_research.py                                  # research engine
```

Hypotheses worth a clean after-cost test (each NULL until proven):
- **Maker-only entry on the existing rubric** — the one edge-agnostic lever the
  audits endorse. Measure fill-rate vs adverse-selection on real fills
  (`MAKER_ONLY_ENABLED=true` soak).
- **TSMOM long-only** (`core/tsmom_signal.py`, ntrader port) at varied lookbacks
  — recent commits already explore this; extend OOS folds, report per-fold.
- **Vol-targeted position sizing** (`scripts/vol_targeting_walkforward.py`) —
  edge-agnostic risk normalization; test whether it improves Sharpe stability.
- **Funding-carry / OI screens** (`scripts/run_funding_carry_screen.py`,
  `run_oi_edge_screen.py`) — market-neutral-ish; least beta-contaminated.

## 4. Spot strategies to test

Spot logic is wired (`core/spot_manager.py` HOLD/SCALE_OUT/SELL/HEDGE;
`core/capital_allocator.py` profit-sweep / structure-break deploy / drawdown
hedge) and instantiates cleanly. Spot has **no exchange-side SL** (local monitor
only) — bias spot toward accumulation/rebalance, not tight stops.

Candidates to backtest before wiring any new rule:
- **DCA accumulation on majors** (`DCAStrategy` exists; live DCA is hard-OFF) —
  test as a spot-only sleeve vs lump-sum on BTC/ETH, after-cost.
- **Profit-sweep cadence**: vary the futures→spot sweep threshold in
  `CapitalAllocator`; measure realized vs paper drift.
- **Rebalance band** (`RebalancingStrategy`): test 5%/10% bands on a 3-asset
  spot basket; compare turnover-cost vs drift capture.

## 5. Honest exit criteria

For each candidate: report after-cost (fees+spread+slippage) expectancy, WR vs
geometric breakeven, profit factor, and an OOS/forward fold. **Promote nothing
that doesn't clear breakeven after costs on out-of-sample data.** If everything
is NULL again (the prior record), that is a valid, publishable result — keep the
bot in PAPER and keep cost reduction as the only live lever.

## 6. External evidence (5-angle deep research, 2026-06-20)

A web research pass (~50 sources, adversarially checked) independently
**corroborates the internal NO_EDGE record** and the chosen direction:

- **Retail algo edge is rare**: ~73% of automated traders lose within 6 months;
  5–7% profitable at 5yr; >90% of bots fail; overfitting erodes live returns
  26–58%; in a real-fee paper-trade of 22 strategies, **16 lost money**
  (avg −0.078%/trade). Matches this bot's 45.8% WR / ~−$0.25/trade.
  [stratproof.com paper-trading-22-strategies; quantifiedstrategies.com
  day-trading-statistics; BIS WP1049]
- **Directional ML on OHLCV loses after costs**: honest OOS AUC for crypto
  direction is ~0.55–0.65, not 0.76; AUC 0.60 nets ~$0 after costs.
  [ScienceDirect S0927538X25003701; PMC12571449]
- **ML leak-check is textbook-correct**: PBO=1.0 ⇒ near-certain overfit; embargo
  MUST be ≥ label horizon (24<96 = leakage); use purged/combinatorial CV +
  Deflated Sharpe; expect AUC to deflate after the fix.
  [López de Prado, AFML; Deflated Sharpe SSRN 2460551; QuantInsti]
- **LLM/agent layers add cost + variance, not durable edge**: LiveTradeBench
  (live, 2025) negligible/negative correlation; look-ahead bias >15pp alpha
  decay; "The Alpha Illusion" (2026) — LLM-agent reported alpha is not
  deployment evidence. Validates keeping agents/MCP log-only.
  [arXiv 2511.03628, 2601.13770, 2605.16895]
- **Only market-neutral / cost-aware edges have support**:
  - Futures: funding-rate carry / basis (delta-neutral; ~8–20% net, capacity-
    compressed, needs discipline); vol-scaled TSMOM (Sharpe ~1.8 backtest,
    degrades OOS); maker rebates need scale. [ScienceDirect S2096720925000818;
    arXiv 2602.11708]
  - Spot: DCA (Sharpe ~1.45–1.85, lower drawdown) + threshold rebalancing (±15%
    drift beat HODL ~77% of the time); 200-day MA standalone = whipsaw.
    [Quantpedia rebalancing-premium; yellow.com DCA]

### Finalized forward roadmap (priority order)
1. Run the honest leak-check (`scripts/leak_check_embargo.py`, embargo ≥96) on
   real warehouse data; keep the honest gate; promote nothing that fails.
2. Make maker-only the default live lever; measure fill-rate vs adverse selection.
3. Build + backtest a delta-neutral funding-carry sleeve (highest-confidence
   futures edge); require after-cost OOS-positive + capacity check before capital.
4. Spot DCA + threshold-rebalancing sleeves; backtest vs lump-sum/HODL; wire via
   spot_manager/capital_allocator only if they beat HODL after costs.
5. Keep agents/MCP log-only; promote only on the honest gate; add no new
   directional LLM prediction.
6. Stay PAPER until 1–4 produce after-cost OOS edge. Expect null — that is a
   valid result.
