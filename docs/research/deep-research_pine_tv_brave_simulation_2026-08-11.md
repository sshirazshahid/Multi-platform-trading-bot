# TradingView Pine Simulation Guide: Research Report
*Generated: 2026-08-11 | Sources: 12+ | Confidence: Medium (methods High; public “profitable” claims Low)*

## Executive Summary

Public TradingView “profitable Pine” scripts are mostly marketing artifacts: zero commission, zero slippage, cherry-picked windows, and often same-bar fills. Independent write-ups show Strategy Tester equity can look excellent while live expectancy collapses once realistic fees/slippage and closed-bar execution are applied ([ValorAlgo](https://www.valoralgo.com/blog/tradingview-backtest-lies); [Thrive crypto backtest guide](https://thrive.fi/blog/trading/pinescript-strategy-backtesting-crypto-guide); [PickMyTrade Claude paper experiment](https://blog.pickmytrade.io/can-claude-code-trading-beat-the-market/)). Naive RSI(2) mean-reversion lost money across five assets once costs were modeled ([Betashorts Medium](https://medium.com/@betashorts1998/i-backtested-the-same-pine-script-rsi-strategy-on-5-different-assets-every-single-one-lost-money-431dc2b9d13e)).

For **this bot**, the only honest TV simulation targets are scripts that **mirror existing log-only shadow probes** (not new live entries). Three Pine files were written under `research/pine_scripts/` and Brave was driven via CDP (`scripts/tv_pine_brave_sim.py`) against `BYBIT:BTCUSDT.P`. **None are claimed profitable.** Applying any survivor to the bot still requires the strategy-evidence pipeline (after-cost screen → audit → shadow → frozen gate) — TV Strategy Tester is **not** a GO.

## 1. What “profitable Pine” usually means (and why it fails)

- Default Strategy Tester commission/slippage are often **0** as of 2025–2026 write-ups; adding 0.05–0.1%/side and 2+ ticks routinely flips PF below 1 ([ValorAlgo](https://www.valoralgo.com/blog/tradingview-backtest-lies); [Supa backtest settings 2026](https://supa.is/article/tradingview-strategy-tester-backtest-settings-2026)).
- `process_orders_on_close=true` / same-bar fills overstate fill quality vs live next-open fills ([TradingView strategy FAQ](https://www.tradingview.com/pine-script-docs/faq/strategies/); [TradePilot guide](https://tradepilot.co.in/blog/tradingview-backtesting-guide-pine-script)).
- Marketed ICT OB/FVG and “EDGE” repos publish WR tables without multiplicity control or OOS gates; treat as **unverified** ([DEV ICT post](https://dev.to/isabelle_dubuis_d858453d7/ict-order-block-fvg-strategy-in-pine-script-v5-backtest-results-full-code-l5c); [V33X GitHub](https://github.com/visioneth/V33X-Pine-Scripts)).
- Even AI-built Pine that “beats SPY” in paper still shows live Sharpe 30–50% below optimistic backtests when defaults are used ([PickMyTrade](https://blog.pickmytrade.io/can-claude-code-trading-beat-the-market/)).

**Honest TV checklist (binding for these sims):**

| Knob | Value used here | Why |
|------|-----------------|-----|
| commission | 0.055%/side | Bybit-ish perp taker |
| slippage | 2 ticks | Minimum liquid-major friction |
| `process_orders_on_close` | false | Next-bar open fills |
| `calc_on_every_tick` | false | No tick lookahead |
| Size | 1–3% equity | Matches probe notional spirit |
| TF / venue | 4h / BYBIT USDT.P | Matches shadow probes |

Read reports in order: **max DD → profit factor → trade count → net profit last** ([ValorAlgo](https://www.valoralgo.com/blog/tradingview-backtest-lies)).

## 2. Candidates chosen for *this* repo (applicability > hype)

| Script | Bot mirror | Status in bot doctrine | Why simulate on TV |
|--------|------------|------------------------|--------------------|
| `zfade_4h_cfg365_v1.pine` | ZfadeProbeAgent | CANDIDATE / G2-fail / 1-of-432 | Apples-to-apples vs warehouse |
| `rsi2_4h_cfg226_v1.pine` | Rsi2TrackerProbeAgent | TRACKER / net NEGATIVE OOS | Band-vs-profit tension |
| `pullback_ma20_rsi14_4h_v1.pine` | PullbackMomentumProbeAgent | REFUTED family / log-only | Confirm TV agrees with shadow |

**Explicit rejects for live application:** ICT OB/FVG, generic RSI(2) without costs, Ichimoku+MFI SOL single-asset winners, V33X “91% WR” marketing, live F&G/liq SHORT bias (prereg 61 remains log-only).

Perpetual funding is a major P&amp;L term TV Strategy Tester **does not** model well ([DexTools crypto backtesting 2026](https://www.dextools.io/tutorials/what-is-backtesting-in-crypto-guide-2026); [FuturesPulse fees](https://futurespulse.io/en/futures-trading-fees/)). Any TV “edge” that ignores funding is incomplete for this bot’s F1/carry lanes.

## 3. Brave + TradingView simulation harness

1. Launch Brave with CDP (already used this session):
   ```text
   brave.exe --remote-debugging-port=9222 --user-data-dir=%TEMP%\brave-cdp-tradingview https://www.tradingview.com/chart/
   ```
2. Run:
   ```text
   python scripts/tv_pine_brave_sim.py --script zfade_4h_cfg365_v1.pine
   ```
3. In UI: confirm **4h**, **Add to chart**, open **Strategy Tester**, verify Properties match the table above.
4. Repeat for ETH/SOL/BNB/XRP `.P` — single-asset BTC curves are not evidence.

**Session note:** Brave CDP connected (`brave.exe --remote-debugging-port=9222`). Monaco inject succeeded for `zfade_4h_cfg365_v1.pine` (editor screenshot shows z-score / SMA-ATR / 2.4 SL inputs). **“Add to chart” / Strategy Tester then hit TradingView’s sign-in wall** (“Look first / Then leap.”) on the fresh CDP profile — automated fill of Strategy Tester metrics **blocked until you sign in** in that Brave window (or relaunch CDP against your already-logged-in Brave profile). Chrome DevTools MCP cannot run here (no Google Chrome) — Brave-only path is correct.

## 4. How results map back to the bot

| TV outcome | Bot action |
|------------|------------|
| Looks great, zero costs | Discard — invalid run |
| Survives costs on BTC only | Still not GO — multi-asset + local after-cost screen |
| Matches warehouse shadow sign/WR within noise | Useful **cross-check**, still log-only |
| Conflicts with warehouse | Prefer warehouse (exchange fills + funding accrual) |

Promotion remains: ≥30 RESOLVED/arm + frozen gate + **owner sign-off**. No CONTROLLED_LIVE from Pine screenshots.

## Key Takeaways

- Do **not** trust public “profitable” Pine; assume costs/fills are wrong until proven otherwise.
- Simulate the three **bot-mirror** scripts with realistic fees on 4h Bybit perps in Brave.
- TV cannot authorize live entries; it is a visualization/cross-check layer only.
- Next evidence step (if owner wants “apply”): strategy-evidence-pipeline on any new idea — not paste-into-MCP.

## Sources

1. [ValorAlgo — TradingView backtests lie](https://www.valoralgo.com/blog/tradingview-backtest-lies) — commission/slippage/overfit checklist  
2. [Thrive — PineScript crypto backtesting](https://thrive.fi/blog/trading/pinescript-strategy-backtesting-crypto-guide) — PF bands, fee math  
3. [PickMyTrade — Claude Code $100k paper](https://blog.pickmytrade.io/can-claude-code-trading-beat-the-market/) — live Sharpe decay vs TV  
4. [Betashorts — RSI strategy lost on 5 assets](https://medium.com/@betashorts1998/i-backtested-the-same-pine-script-rsi-strategy-on-5-different-assets-every-single-one-lost-money-431dc2b9d13e) — cost drag  
5. [TradingView — Strategies FAQ](https://www.tradingview.com/pine-script-docs/faq/strategies/) — fill model  
6. [TradingView — Strategy properties](https://www.tradingview.com/support/solutions/43000628599-strategy-properties/) — commission/slippage semantics  
7. [Supa — Backtest settings 2026](https://supa.is/article/tradingview-strategy-tester-backtest-settings-2026) — defaults  
8. [TradePilot — Backtesting guide](https://tradepilot.co.in/blog/tradingview-backtesting-guide-pine-script) — next-bar open  
9. [DexTools — Crypto backtesting 2026](https://www.dextools.io/tutorials/what-is-backtesting-in-crypto-guide-2026) — funding gap  
10. [FuturesPulse — Fees & funding](https://futurespulse.io/en/futures-trading-fees/) — round-trip cost  
11. [Alpaca — Z-score mean reversion](https://alpaca.markets/learn/how-to-build-backtest-mean-reversion-strategy-with-alpaca) — educational MR (not crypto-GO)  
12. Local: `core/agents/bundle_mr_probe_agent.py`, `pullback_momentum_probe_agent.py` — frozen probe specs  

## Methodology

Searched web for Pine profitability / cost realism / crypto perp simulation (2025–2026). Deep-read ValorAlgo + TradingView docs + Thrive/DexTools. Cross-checked against this repo’s shadow-probe constants. Drove Brave via CDP + Playwright. Firecrawl/Exa MCP were **not** available in this environment; WebSearch/WebFetch + local CDP used instead.

Sub-questions: (1) which public Pine claims survive costs? (2) what TV settings are honest for crypto perps? (3) which scripts are applicable to *this* bot? (4) how to automate Brave/TradingView? (5) what must never be promoted from TV alone?
