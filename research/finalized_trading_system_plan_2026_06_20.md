# Finalized Trading-System Plan & Honest Verdict (2026-06-20)

**Written for a non-trader.** You asked me to fix the bugs, find why the bot loses,
build a "solid profitable trading mechanism," and evaluate a list of specific
chart strategies (harmonic patterns, stochastic crossovers, ICT, Asian range
breakout, Dow swing structure, Bollinger squeeze, AI valuation models). I ran a
deep, multi-source research pass (5 angles, ~50 cited sources, each claim
adversarially checked) plus an engineering audit of the code. Here is the honest,
evidence-based result.

---

## 1. The one thing to understand first

**No one can give you a crypto bot that reliably makes money — not me, not a
vendor, not a YouTube "system."** This isn't pessimism; it's what the evidence
says, and I verified it four independent ways:
- Your bot's own historical audits: 45.8% win rate vs 65.5% needed to break even;
  ~89% of losses are *missing edge*, not fees.
- ~50 external sources this session: ~73% of automated traders lose within 6
  months; ~5–7% are profitable at 5 years; in one real-fee test of 22 popular
  strategies, **16 lost money**.
- Live market data (today): majors are down ~18–20% in 30 days — a regime where a
  long-biased bot bleeds.
- Every specific strategy you named was researched separately and **none has
  proven, after-cost edge** (table below).

So the goal of this plan is NOT "get rich." It is: **stop the bleeding, preserve
capital, and only ever risk money on something that has *proven* it works on data
the bot has never seen.** That is the only honest path, and it's what professionals
actually do.

## 2. Why the program is (still) losing — the definitive answer

It is **not** a bug. The code is sound: 1933 automated tests pass, the close/risk
wiring is correct, and the strategies run. The bot loses because **its entry
signals have no real edge** — the patterns it (and you) want to trade do not
predict price well enough to overcome trading costs (fees + spread + slippage,
~0.1–1% per round trip). A coin-flip signal that pays costs on every flip loses
money by arithmetic, no matter how clean the code is. This is the single root
cause, confirmed by every audit.

## 3. Verdict on each strategy you named (evidence-based)

| Strategy | Verdict | Why (with evidence) |
|---|---|---|
| **Harmonic patterns** (Gartley/Bat/Butterfly/Crab) | ❌ No proven edge | Zero peer-reviewed validation; Fibonacci bounce levels are *statistically indistinguishable from random* price levels [ScienceDirect S0957417421012495]; results are subjective/hindsight-fitted. |
| **AI valuation models** (Stock-to-Flow, MVRV, NVT) | ❌ Discredited / weak | S2F predicted $100k+ in 2021–22, off by 50–65% [CryptoSlate; Zipmex]; MVRV is "a lens, not a crystal ball," cycle-dependent [Presto Research]. |
| **Stochastic crossovers** | ❌ No edge solo | 36–41% win rate; whipsaws badly in trends; needs other filters to be even marginal [QuantifiedStrategies]. |
| **Bollinger squeeze breakout** | ⚠️ Weak, needs confirmation | 20–30% false breakouts; solo profit factor ~1.15 (≈ coin-flip); regime-dependent [QuantifiedStrategies; SSRN 5775962]. |
| **ICT / Smart Money Concepts** (order blocks, FVG, BOS/CHoCH) | ❌ No proven edge | No peer-reviewed support; order blocks = rebranded support/resistance; rules are discretionary/unfalsifiable; ICT's own public accounts blew up [Phidias; ScienceDirect gap study]. |
| **Asian range breakout** | ❌ Doesn't fit crypto | Session logic assumes market open/close; crypto is 24/7 with no bell; only ~0.51% of opening-range configs survive walk-forward; 50–80% false breakouts [BreakOrb; ForTraders]. |
| **Dow Theory swing structure** | ❌ Insufficient alone | Higher-high/higher-low alone "not powerful enough to trade"; filter rules fail after costs (weak-form efficiency, Fama) [QuantifiedStrategies; Nobel/Fama]. |

**Pattern across all of them:** they look convincing on a chart *after the fact*,
but disappear under out-of-sample testing and realistic costs. Your repo already
implements three of these (`strategies/asian_range_breakout.py`,
`dow_swing_structure.py`, `bb_squeeze_breakout.py`) and its own research already
marked them NO_EDGE — which matches the external evidence exactly.

## 4. What actually has *some* evidence (the only honest directions)

Not prediction — these are **structural / cost-based**, and still must be proven
on your data before any real money:
- **Delta-neutral funding carry** (futures): hold long spot + short perp (or vice
  versa) to collect the funding rate; profit doesn't depend on price direction.
  ~8–20% gross historically but capacity-compressed and needs discipline.
- **Spot DCA + threshold rebalancing**: buy fixed amounts on a schedule; rebalance
  a basket when it drifts. Improves *risk-adjusted* return; shines in drawdowns
  (like right now). This is the lowest-risk, best-evidence path for a beginner.
- **Cost reduction (maker-only orders)**: the one lever that helps a marginal
  signal; ceiling ≈ breakeven, stated honestly.

## 5. How I would design/build/test the bot (answers to your "how" questions)

These are the evidence-based engineering answers — and your repo already does most
of them well:
- **Language:** **Python** — it owns this space (pandas/numpy, `ccxt` for 100+
  exchanges, `nautilus_trader`/`freqtrade`/`vectorbt` for backtesting). Your bot is
  Python; keep it.
- **System design:** event loop → data feed → signal → **risk gate** → execution →
  logging/warehouse, with a hard separation between *deciding* and *placing
  orders*. Your repo has this (`core/bot_engine.py`, `order_manager.py`,
  `risk_manager.py`, `warehouse.py`).
- **Tools/connectors/skills:** `ccxt` (exchanges); SQLite warehouse for an audit
  trail (you have it); the new **read-only MCP server** I built (`mcp_server/`) to
  interrogate results; live-data MCPs (CoinDesk/Crypto.com/LunarCrush) for
  research. Don't add more *prediction* layers (LLM agents add cost+variance, no
  edge — LiveTradeBench, "The Alpha Illusion").
- **Monitoring the market:** scheduled scans + a separate 10-second SL/TP watcher
  (you have this); funding/OI/volatility dashboards; alerts on drawdown.
- **Technical analysis:** use indicators as *filters/context* (trend, volatility,
  liquidity), never as a lone crystal ball — research is unanimous that single
  indicators don't beat buy-and-hold after costs; 3 well-chosen filters max.
- **Finding strategies:** start from an *economic reason* (carry, liquidity
  provision, rebalancing premium), not a chart shape. Shapes without a "why" are
  curve-fits.
- **Testing strategies (the most important part):** walk-forward + ≥20–30%
  out-of-sample; model real fees+slippage; **embargo ≥ label horizon** to avoid
  leakage; Deflated Sharpe + Monte Carlo (1,000+ reshuffles); keep <15 parameters;
  demand out-of-sample Sharpe ≥70% of in-sample. **Paper-trade first.** Your repo
  has the gate (`core/promotion_gate.py`) and walk-forward (`core/walk_forward.py`)
  for exactly this.
- **Reading charts / patterns:** honestly — *don't* trade visual patterns. The
  evidence says they're subjective and don't survive testing. Let code measure
  objective things (volatility, funding, spread, trend strength), not "draw" shapes.

## 6. Finalized roadmap (priority order, capital-preservation first)

1. **Keep the bot in PAPER.** No real money until step 4 passes. (Done — it is.)
2. **Run the honest leak-check** on the ML model with real data
   (`scripts/leak_check_embargo.py`, embargo ≥96); expect the inflated accuracy to
   collapse. Promote nothing that fails the honest gate.
3. **Backtest the evidence-based sleeves** properly (walk-forward, real costs,
   Monte Carlo): (a) delta-neutral **funding carry**, (b) **spot DCA + rebalancing**.
   A runnable, offline, fully-tested harness for (b) now exists:
   `research/dca_rebalance_lab.py` (lump-sum vs DCA vs threshold-rebalance with
   real fees+slippage; 16 unit tests in `tests/test_dca_rebalance_lab.py`). Run
   `python research/dca_rebalance_lab.py` for the demo; feed real exchange closes
   for a study. Also reuse `quant_suite/funding_carry.py`, and the live
   `DCAStrategy`/`RebalancingStrategy` for execution once a config is validated.
   For (a), an offline analytics lab now exists too:
   `research/funding_carry_lab.py` (delta-neutral long-spot/short-perp cash-and-
   carry — net yield, break-even funding, % positive settlements, after-cost; 12
   tests in `tests/test_funding_carry_lab.py`). Pull real funding history with
   `quant_suite/funding_carry.py` and pass it in. NB: this is the market-neutral
   carry, NOT the directional funding signal (which already screened NO_EDGE).
4. **Promote only what clears the honest gate out-of-sample.** If nothing does
   (the likely outcome), the correct action is: stay in PAPER / DCA-only spot, and
   treat "no edge" as a valid finding — not a reason to risk more.
5. **Keep agents/MCP log-only**; cost-reduce with maker-only; never re-enable
   leverage/shorts into a chop regime (like today's).

## 6b. Worked example on REAL data (the labs in action)

Real-data ingestion now exists (`research/data_io.py`, 11 tests) — hand it a CSV
or exchange/MCP candles and it feeds the labs. Demonstrated on 32 days of real
BTC daily closes (`research/sample_data/btc_daily_2026-06-20.csv`, May 20→Jun 20,
a −17.6% month):

| Approach | Return | Max drawdown |
|---|---|---|
| Lump-sum / HODL | **−17.7%** | 21.5% |
| DCA (daily) | **−6.1%** | 8.4% |

The honest lesson, in one table: in a falling market DCA loses **much less** and
is far less volatile — but it still loses. DCA reduces *risk*; it does not turn a
declining asset into a profit. (32 bars is far too short to conclude anything —
this only shows the mechanism. Run years across bull/bear/chop before trusting a
config.)

## 7. If you take nothing else away

- Be deeply skeptical of anyone selling a "profitable system" or a pattern with a
  fancy name. The research here shows those are how beginners lose money.
- The bot is well-built; its honesty about *not* having edge is its best feature.
- The safest real-money path for a non-trader is **spot DCA into majors during
  drawdowns**, not a directional bot. Everything else stays PAPER until proven.

---
*Sources for every claim are in this folder's companion docs and the per-claim
citations gathered this session (harmonic/Fibonacci: ScienceDirect; S2F: CryptoSlate;
ICT: Phidias/ScienceDirect; ORB: BreakOrb; efficiency: Fama/Nobel; build practices:
NautilusTrader/Freqtrade/CCXT; ML leakage: López de Prado).*
