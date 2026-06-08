# QuantSuite Ensemble — integration & paper-enable guide

A regime-switching **ensemble** of the validated strategies, integrated into the
bot as a **paper-safe, advisory-only** module. It improves trade quality over the
bot's current single-factor engine (`core/mcp_brain.py`).

## Backtest (10 liquid symbols, 1500×1h bars, fees+slippage, look-ahead-safe)

| metric | MF baseline (current bot) | ensemble | **confluence (shipped)** |
|---|---|---|---|
| trades | 289 | 220 | 150 |
| win rate | 37.0% | 44.5% | 43.3% |
| TP-hit rate | 32.2% | 39.5% | 37.3% |
| expectancy / trade | 0.31% | 0.49% | **0.75%** |
| avg R | 0.105 | 0.207 | **0.31** |
| profit factor | 1.26 | 1.44 | **1.65** |
| max drawdown | −27.2% | −20.6% | **−17.4%** |

Per-tier analysis showed the range mean-reversion sleeve was **negative-EV**
(−0.18%/trade) and the edge lives entirely in **trend-confluence** — so the
shipped default (`mode=confluence`) trades only when the 4h regime-trend AND the
multi-factor engine agree. Full numbers: `reports/ensemble_backtest.json`.

> In-sample caveat: this is a single ~2-month window on 10 symbols. Treat as
> evidence the confluence filter helps, not as a performance guarantee. Keep in
> PAPER and forward-test before trusting it with size.

## Files
- `quant_suite/indicators.py` — causal TA (EMA/RSI/ADX/ATR/VWAP/Hurst).
- `quant_suite/engine.py` — per-strategy votes, regime-switching ensemble, backtest.
- `quant_suite/ensemble_backtest.py` — baseline vs ensemble vs confluence comparison.
- `quant_suite/bot_adapter.py` — paper-safe bridge (advisory setups; never orders).
- `quant_suite/market_monitor.py` — live regime + movers dashboard.
- `tests/test_quant_suite_ensemble.py` — 6 offline tests (incl. safety guards).

## How it works
1. Classify market by 4h ADX: **trend** (≥25) vs **range** (<25).
2. **Trend** → require multi-factor (score≥65) AND regime-trend (4h EMA20>50>200 +
   1h price/EMA) to agree → directional entry, ATR stop, 2.5:1.
3. **Range** → mean-reversion sleeve (off by default in `confluence` mode).
4. Conviction tier = how many sub-strategies align → position-size multiplier.

## Safety guards (hard)
- `bot_adapter.PLACES_ORDERS = False` — the module imports no order/trading client
  and only returns advisory dicts (enforced by a unit test).
- `generate_setups()` returns `[]` when `OPERATING_MODE=CONTROLLED_LIVE` — it will
  not feed an automated live loop. Live execution stays your manual decision.
- Disabled by default: `QS_ENSEMBLE_ENABLED=0`.

## Enable in PAPER
```bash
# .env (paper research only)
QS_ENSEMBLE_ENABLED=1
QS_ENSEMBLE_MODE=confluence      # confluence (default) | ensemble | multifactor
QS_RISK_PCT=0.005                # 0.5% equity risk/trade, tier-scaled
# OPERATING_MODE stays PAPER
```
Generate current advisory setups:
```bash
python -m quant_suite.bot_adapter
python -m quant_suite.ensemble_backtest      # refresh the comparison
python tests/test_quant_suite_ensemble.py    # run tests
```

## Optional bot hook (advisory candidates, paper only)
Add to `core/bot_engine.py` portfolio cycle — **commented until you choose to use it**:
```python
# from quant_suite import bot_adapter as qs
# if qs.is_enabled():                       # False unless QS_ENSEMBLE_ENABLED=1 & PAPER
#     advisory = qs.generate_setups(universe_symbols, account_equity=equity)
#     # feed `advisory` into the warehouse / Claude-portfolio candidate list as a
#     # SIGNAL SOURCE; existing risk_manager + paper sim still own execution.
```
This adds the ensemble as an extra signal source; the bot's own risk manager,
meta-filter, and paper-sim execution remain authoritative.

## Keep improving (next iterations)
- Walk-forward re-tune confluence thresholds per regime.
- Add the live funding/carry overlay to nudge conviction (history not in backtest).
- Per-symbol regime instead of BTC-only; widen the backtest window + symbol set.

## Automation (daily scan)
- **Script:** `quant_suite/daily_scan.py` → writes `reports/daily_scan_<date>.md`
  (human briefing) and `data/ensemble_setups.json` (machine artifact for the bot).
  Run manually: `QS_ENSEMBLE_ENABLED=1 python -m quant_suite.daily_scan`
- **Scheduled (Cowork):** a task `daily-confluence-scan` runs **daily at 08:01 local**,
  executes the scan, and messages you the briefing. It runs while the Claude app is
  open (if closed at fire time, it runs on next launch). Manage it under the
  "Scheduled" sidebar; click **Run now** once to pre-approve the shell tool so future
  runs don't pause on permission prompts.
- **Bot consumption hook** (paper, feature-flagged) — in `core/bot_engine.py`:
  ```python
  # from quant_suite import bot_adapter as qs
  # if qs.is_enabled():                         # QS_ENSEMBLE_ENABLED=1 and PAPER
  #     latest = qs.load_latest_setups()        # freshest daily scan, no re-fetch
  #     for s in latest.get("setups", []):
  #         # feed s as an advisory candidate; risk_manager + paper sim still execute
  ```
- **Host-level 24/7 option** (independent of the Claude app):
  - Bot's own scheduler: `schedule.every().day.at("00:05").do(lambda: __import__('quant_suite.daily_scan', fromlist=['run']).run())`
  - or Windows Task Scheduler: `schtasks /create /tn "QS Daily Scan" /sc daily /st 08:00 /tr "cmd /c cd /d D:\Downloads\Trading_Bot && python -m quant_suite.daily_scan"`

## Sweet-timeframe research (which candle to trade)
`quant_suite/timeframe_research.py` backtested the confluence strategy across
timeframes on the liquid basket. Edge by timeframe (per-trade expectancy):

| chart TF | HTF ctx | trades | win% | TP% | expectancy | PF | maxDD |
|---|---|---|---|---|---|---|---|
| 15m | 1h | 396 | 33.1 | 19.2 | −0.18% | 0.83 | −62.5% |
| 30m | 2h | 220 | 38.6 | 30.0 | +0.27% | 1.24 | −19.7% |
| **1h** | **4h** | **150** | **43.3** | **37.3** | **+0.75%** | **1.65** | **−17.4%** |
| 4h | 1D | 25 | 28.0 | 20.0 | −0.64% | 0.69 | −22.0% |
| 1d | 1W | 0 | — | — | — | — | — |

**Sweet timeframe = 1h entries + 4h context.** Edge is noise below 1h and dries up
above it (4h/1d under-sampled in-window). This is now an explicit, tunable config:
```bash
QS_ENSEMBLE_TF=1h     # validated sweet timeframe
QS_ENSEMBLE_HTF=4h    # higher-TF regime/trend context
QS_ENSEMBLE_BARS=500  # bars fetched for live signal warmup
```
Re-run anytime: `python -m quant_suite.timeframe_research 15m|30m|1h|4h|1d` then
`... summary`. Report saved to `reports/timeframe_research.json`. Caveat: windows
differ per TF and stops use a fixed 1.5–3.5% ATR clamp; treat 4h/1d as
under-powered (low trade counts), not definitively dead.

## External feeds (news + sentiment) — perception->engine bridge
The bot's Python can't call MCP connectors, so Claude (interactively or via the
scheduled daily task) fetches + assesses them and writes caches that `quant_suite/feeds.py` reads:
- **News risk (MT Newswires) — ACTIVE.** Daily task pulls global headlines, scores macro
  risk-off (Fed/rates, selloffs, war, regulation) -> `data/news_flags.json`. The engine
  dampens position size: risk_score >=0.66 -> x0.3, >=0.33 -> x0.6. (Today: HIGH -> x0.3.)
- **Sentiment (LunarCrush) — WIRED BUT INACTIVE.** Free/connected tier still returns
  "subscription required"; needs a PAID LunarCrush plan. When available, write
  `data/sentiment.json` = {"BTC":{"vote":+1/0/-1,...}} and the engine's sentiment vote
  auto-activates (aligned -> x1.1 size, opposed -> x0.5). No code change required.
Both are graceful no-ops when their cache is absent/stale (>12h) -> engine behaves as before.
