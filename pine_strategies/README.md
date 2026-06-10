# QuantSuite — TradingView Pine Script Strategies (v6)

Four crypto strategies, each in **two flavours**:

| # | Strategy | `strategy()` (backtest) | `indicator()` (alerts) | quant_suite counterpart |
|---|----------|--------------------------|------------------------|--------------------------|
| 1 | **Multi-Factor** | `01_multifactor_strategy.pine` | `01_multifactor_signals.pine` | `engine.prep()` `mf_vote` |
| 2 | **Regime-Filtered Trend** | `02_regime_trend_strategy.pine` | `02_regime_trend_signals.pine` | `engine.prep()` `rg_vote` |
| 3 | **Mean-Reversion + Rel-Strength** | `03_meanrev_relstrength_strategy.pine` | `03_meanrev_relstrength_signals.pine` | `engine.prep()` `mr_vote` |
| 4 | **Funding / Carry-Aware** | `04_funding_carry_strategy.pine` | `04_funding_carry_signals.pine` | `engine.prep()` `fc_vote` ("carry" mode; real 8h funding via `quant_suite/funding_carry.py`) |

> Counterpart note (corrected 2026-06-10): an earlier revision of this table pointed at
> `factors.score_symbol` / `regime.py` / `portfolio.macro_overlay` — modules of the
> **quarantined** suite (`legacy/quant_suite_2026-06-06_quarantined/`, audit-failed).
> The validated counterparts are the vote columns in `quant_suite/engine.py`.
> Note the shipped Python default is the **confluence** ensemble (mf AND rg agree in a
> 4h-ADX>=25 trend); there is no confluence `.pine`, so a TradingView backtest of any
> single file validates that sleeve only, not the shipped ensemble.

All are written for **Pine Script v6** and designed for **crypto, multi-timeframe**: run them on the **1h chart**; they pull **4h** trend context (non-repainting) and #1 adds optional **15m** timing.

---

## 1. Load a script

1. Open TradingView → a crypto **perpetual** chart (e.g. `BINANCE:BTCUSDT.P`), set timeframe to **1h**.
2. Bottom panel → **Pine Editor** → paste the contents of a `.pine` file → **Save** → **Add to chart**.
3. For `strategy()` files, the **Strategy Tester** tab shows the equity curve and stats. For `signals` files, you'll get plot markers + alerts.

## 2. Backtest (strategy files)

Commission (0.05%) matches the Python harness fee; Pine's 2-tick slippage is **lighter**
than the Python engine's 5–10 bps slippage model, and Pine fills at the signal bar's
close while the Python backtest fills at the next bar's open — so Pine results are
mildly optimistic vs `quant_suite/engine.py`. Pine also has no 72-bar time exit or
2-bar re-entry cooldown (the Python harness has both). Position sizing is
**risk-based**: each trade risks `Risk % of equity / trade` (default **0.5%**) at the stop.

Use the **in-code defaults** (score 65 / ATR×1.5 / R:R 2.5 for #1) — they match the
validated Python engine.

> ⚠ A previous revision recommended "Entry score 70, ATR×2.0, R:R 3.0 (best
> out-of-sample)". Those numbers came verbatim from the **quarantined** suite's
> walk-forward sweep, which three audits found look-ahead-biased, non-time-ordered,
> and multiple-testing-uncontrolled (zero DSR/PBO/FDR). They are NOT validated —
> do not use them.

Per-strategy starting inputs:

- **Multi-Factor:** score 65, ATR×1.5, R:R 2.5 (engine defaults).
- **Regime Trend:** 4h ADX ≥ 25, ATR×1.8, R:R 2.5. (ATR×1.8 is a Pine-only variant —
  the Python engine backtests 1.5×; also note Pine #2 enters on the EMA20 *re-cross*
  event, a pullback-entry variant, while the Python `rg_vote` is a state — trade sets
  differ by design.)
- **Mean-Rev:** deviation 2.0×ATR, RSI 32/68, R:R 1.5 (reversion targets are closer).
- **Funding/Carry:** premium threshold 0.05%, R:R 2.0. **Set the Spot symbol** to match your perp (e.g. perp `BTCUSDT.P` → spot `BINANCE:BTCUSDT`).

## 3. Alerts → automation (signals files)

Each `signals` indicator fires on **bar close** (`alert.freq_once_per_bar_close`, non-repainting) and emits a **JSON payload** ready for a webhook:

```json
{"strategy":"multifactor","action":"buy","symbol":"BTCUSDT.P","price":60600.7,"score":76,"sl":59933.4,"tp":62208.5}
```

To wire it up: right-click chart → **Add alert** → Condition = the indicator → "Any alert() function call" → paste your webhook URL. Alerts are **edge-triggered** (fire once at signal onset, not every bar the state holds). Note: the bot currently has **no webhook listener** — the strategies are natively ported in `quant_suite/engine.py`, which is the supported integration path; webhooks would need net-new infrastructure. The `strategy` files only emit TradingView's generic order-fill alerts (they do not set `alert_message`).

> Funding/Carry **signals** plots in a **separate pane** (it's the premium oscillator); the other three plot on the price chart.

## 4. How these map to the validated engine

These mirror the Python `quant_suite/engine.py` votes: same EMA20/50 + RSI + ADX + ATR + rolling-VWAP feature set, the same 4-required-conditions + bonus scoring for #1, the same ATR-clamped stop (1.5–3.5%) and R:R logic, and the same 0.5%-risk sizing. Known residual divergences vs the Python harness: Pine fills at signal-bar close (Python: next-bar open + slippage), no 72-bar time exit / 2-bar cooldown, #2 is a crossover-event pullback variant of the state-based `rg_vote`, and #2 uses ATR×1.8. Also note the mirror target is the **validated quant_suite vote**, not the live `mcp_brain` standard tier (which now uses VWAP +5, a 66/2-bonus gate, and a dead structure bonus).

## Caveats (read before trading)

- **No funding in Pine.** #4 uses a **perp-vs-spot premium proxy**, not exchange funding. It's directional-sentiment, not exact carry. Use a real funding ticker in the input if your data feed exposes one. The Python port (`engine.prep(funding=...)` + `quant_suite/funding_carry.py`) uses **real 8h funding history** instead of the proxy. ⚠ Edge status: **NO_EDGE** — both the 70d prereg screen (2026-05-26) and the 3-year / 31-major re-screen of this exact rule (2026-06-10, `reports/funding_carry_screen_2026-06-10.md`: all 6 pre-registered variants negative net per trade, 0/6 FDR) failed the frozen gates. Do not trade #4; it is ported for completeness and registered fail-closed.
- **15m timing (strategy #1)** is now **default OFF** (matches the Python engine, and on long histories missing 15m data would silently block all longs). If enabled, it uses a lower-timeframe request that can update intrabar on the *realtime* forming bar.
- **Multi-timeframe is non-repainting** for the 4h context (`expr[1]` + `lookahead_on`), at the cost of acting on confirmed 4h bars only.
- **Not compiled by me.** I wrote these to v6 spec but couldn't run TradingView's compiler. Paste into the Pine Editor — it will flag anything instantly; ping me and I'll fix.
- **Backtest ≠ live.** Crypto perps have funding, liquidations, and gaps. Forward-test in paper first. Not financial advice.
