# AccBand Quiet-Regime Drought: Research Report
*Generated: 2026-08-01 | Sources: 14 | Confidence: High (local warehouse) / Medium (external regime)*

## Executive Summary

The 2026-08-01 “still no trades” incident was **protective idle stacked three deep**, not a hung process. Local evidence showed `SCALP_MODE` quiet-ATR veto zeroing the allow funnel, then `band_regime_filter:btc_vol<0.7` rejecting every OPEN, while F1 correctly idled on compressed funding. External literature agrees: ATR gates belong on scalp engines, and quiet markets need a **regime switch to a slower path** — not a lowered ATR floor without a new study ([Coinquant](https://www.coinquant.ai/blog/how-to-use-atr-in-a-crypto-trading-strategy-with-backtest), [unCoded](https://uncoded.ch/blogs/regime-switching-teaching-your-bot-to-read-the-room)). August 2026 tape commentary describes cycle-low realized vol ([HokaNews / CryptoQuant](https://www.hokanews.com/2026/08/bitcoin-volatility-drops-to-cycle-lows.html)), consistent with our warehouse ATR prints (majors ~0.2–0.76% vs scalp floor 0.8%).

## 1. Local defect anatomy (warehouse ground truth)

- Post-allowlist-restore boot: **2430 candidates, 0 ALLOW**; families `scalp_veto:quiet` 1324, `analysis_only` 756, `scalp_req_fail` 268.
- After `SCALP_MODE_ENABLED=false`: OPEN proposals returned; execute_open terminals were **`band_regime_filter:btc_vol<0.7`**.
- After `BAND_REGIME_FILTER_ENABLED=false`: PAPER **fills resumed** (BCH, then APT open book).
- F1 remained `idle_no_edge` (contango / funding≤0) — regime-correct given compressed funding prints through late July ([Convex](https://convextrade.com/metrics/btc-funding), [FuturesPulse](https://futurespulse.io/en/crypto-in-depth-funding-vs-long-skew-2026-07-25/)).

## 2. Why quiet ATR kills scalp (and should)

- ATR thresholds screen compression / thin follow-through environments where fixed scalp stops are noise ([Coinquant](https://www.coinquant.ai/blog/how-to-use-atr-in-a-crypto-trading-strategy-with-backtest); [Algomatic volatility filters](https://algomatictrading.substack.com/p/10-volatility-filters-every-serious)).
- Low-vol crypto playbooks recommend **range / funding / squeeze tools**, not trend-scalp logic ([Mudrex](https://mudrex.com/learn/low-volatility-crypto-trading-futures-strategies/)).
- Mission Control already warned: do not loosen `SCALP_MIN_ATR` without a hashed prereg (`mission_control/state.py` brain interpretation).

## 3. Regime-switch architecture (recommended pattern)

- Hierarchical gatekeeper: scalp module only when ATR regime is healthy; otherwise arm swing/standard ([unCoded regime switching](https://uncoded.ch/blogs/regime-switching-teaching-your-bot-to-read-the-room); [MQL5 scalp vs swing EA](https://www.mql5.com/en/articles/19989)).
- ADX/ATR dual-axis filters improve trend systems by **refusing** wrong regimes, not by inventing edge ([FX Strategy Analyzer](https://fxstrategyanalyzer.com/en/blog/article31-adx-regime-filter-ea-switching-logic); [AlgoGrade ADX journey](https://algograde.com/strategies/bot-journey-adx-regime-filter)).
- Inference for this bot: AccBand PAPER under `MAX_FLOW_BAND` should **fall through to `_score_coin`** on `scalp_veto:quiet|ranging`, rather than hard-idle the whole directional funnel.

## 4. Market context (Aug 2026) — cross-check

- Realized vol described at cycle lows with compression-before-expansion framing ([HokaNews](https://www.hokanews.com/2026/08/bitcoin-volatility-drops-to-cycle-lows.html)).
- 2025→2026 forensic regimes emphasize subdued leverage / unattractive carry after cascade ([Amberdata R6](https://blog.amberdata.io/the-six-market-regimes-of-2025-a-forensic-analysis)).
- Funding narrative is mixed across vendors (neutral/compressed vs episodic extremes) — treat live F1 gate log as authority for *this* book; do not loosen F1 thresholds from articles.

## Key Takeaways

- **Do not** lower `SCALP_MIN_ATR` from narrative; **do** regime-switch (implemented: `_route_score_coin` fall-through under PAPER+MAX_FLOW_BAND).
- Band BTC-vol veto is WR-protection (~55.6% bucket in screen-13), not a crash — AccBand remains ≈ −0.24R; fills ≠ GO.
- F1 idle under compressed funding is correct; carry resumes when net edge clears cost.

## Sources

1. [Coinquant — ATR in crypto strategies](https://www.coinquant.ai/blog/how-to-use-atr-in-a-crypto-trading-strategy-with-backtest) — ATR as quality filter in compression.
2. [Mudrex — low-vol futures](https://mudrex.com/learn/low-volatility-crypto-trading-futures-strategies/) — range/funding vs trend-scalp.
3. [Algomatic — volatility filters](https://algomatictrading.substack.com/p/10-volatility-filters-every-serious) — regime-aware filters.
4. [Amberdata — 2025 regimes](https://blog.amberdata.io/the-six-market-regimes-of-2025-a-forensic-analysis) — subdued positioning / carry.
5. [HokaNews — BTC vol cycle lows Aug 2026](https://www.hokanews.com/2026/08/bitcoin-volatility-drops-to-cycle-lows.html) — compression narrative.
6. [Convex BTC funding](https://convextrade.com/metrics/btc-funding) — near-zero funding prints.
7. [FuturesPulse funding vs long skew](https://futurespulse.io/en/crypto-in-depth-funding-vs-long-skew-2026-07-25/) — premium compression.
8. [unCoded — regime switching bots](https://uncoded.ch/blogs/regime-switching-teaching-your-bot-to-read-the-room) — HTF gatekeeper pattern.
9. [MQL5 — scalp vs swing multimode](https://www.mql5.com/en/articles/19989) — dual execution modules.
10. [FX Strategy Analyzer — ADX regime EA](https://fxstrategyanalyzer.com/en/blog/article31-adx-regime-filter-ea-switching-logic) — ADX+ATR axes.
11. [AlgoGrade — ADX filter journey](https://algograde.com/strategies/bot-journey-adx-regime-filter) — refuse ranging for trend bots.
12. [Signal Pilot — regime recognition](https://education.signalpilot.io/curriculum/advanced/49-market-regime-recognition.html) — wrong-regime WR collapse.
13. Local: warehouse candidates + `56_drought_scalp_band_fix_2026-08-01.md`.
14. Local: screen-13 band conditional WR buckets (ADX>30 / BTC vol&lt;0.7).

## Methodology

Searched ~10 web queries (Exa/Firecrawl MCP **not configured** in this Cursor session — used WebSearch + local warehouse). Sub-questions: (1) quiet ATR scalp failure, (2) Aug 2026 funding/vol regime, (3) dual-path fallback architecture, (4) ADX/ATR veto tradeoffs.

## Orch-fix-defect outcome

- **Size:** small (mcp_brain routing + tests + launcher pin already landed).
- **Red→green:** `tests/test_scalp_quiet_fallback.py` (4) + launcher pin test.
- **Fix:** `_max_flow_scalp_fallback_enabled` + `MCPBrain._route_score_coin`.
- **GATE 2:** commit pending owner confirm (see chat).

## Verification stamp (2026-08-01 ~22:04Z)

- Pytest slice: `test_scalp_quiet_fallback` + `test_launcher_safety` + `test_scalp_mode` + `test_mcp_scalp_skip_reasons` + `test_band_regime_filter` + `test_mcp_entry_min_score` → **81 passed**.
- Live heartbeat: `PAPER` / `MAX_FLOW_BAND`, EconGate `paper_fallback`, EntryFloor 66; **5 open PAPER positions** (BCH/LTC/BTC/LINK/XRP).
- Launcher pin check: `SCALP_MODE_ENABLED=false`, `BAND_REGIME_FILTER_ENABLED=false`, allowlist `F1,mcp_registry,algo_det`.
- External refresh: cycle-low realized vol + building ADX still the dominant Aug-1 narrative ([Coinomedia](https://coinomedia.com/bitcoin-realized-volatility/), [HokaNews](https://www.hokanews.com/2026/08/bitcoin-volatility-drops-to-cycle-lows.html)); regime-route pattern reconfirmed ([agiprolabs regime-detection](https://github.com/agiprolabs/claude-trading-skills/blob/main/skills/regime-detection/SKILL.md)).
- Honesty unchanged: AccBand fills ≠ CONFIRMED_GO; F1 idle until funding clears.
