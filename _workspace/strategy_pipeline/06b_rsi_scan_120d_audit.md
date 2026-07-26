# 06b — Honesty audit: RSI+Volume scan + bot-entry replay (120d)

**Artifacts audited:** `reports/rsi_volume_scan_replay_120d_2026-07-10.{md,json}`, `research/scan_rsi_vol_replay.py`
**Method:** independent recompute from raw cached parquet (my own throwaway scripts, since deleted — not the producer's code) + re-running the production `score_bar` / `resolve_one`.
**Date:** 2026-07-10

## VERDICT: CORRECTED

Every headline number in the report is **arithmetically faithful and reproduces exactly** from raw candles and the production code. RSI is genuinely Wilder (not SMA). Scores, sides, outcomes, and all aggregates reconcile. **Two honesty gaps must be added to the report** — neither changes any GO/NO-GO (this is situational intel on a REFUTED family, explicitly framed as no-edge), but both are unstated and one materially qualifies what "would-be entries" means:

1. **4h forming-bar LOOKAHEAD in the replay** (inherited from `backtest_v3`) — 3 of every 4 1h decision bars are scored against a 4h bar that has not yet closed (peeks up to 3h ahead). Part B is therefore **not** a clean real-time replay of live entries.
2. **Survivorship** — the universe is `cache ∩ TODAY's live binance USDT-M perps`; ~130 cached symbols (116 whose data ends at the May-31 harvest cutoff) contribute **zero** rows. Delisted/inactive names are absent from all historical months. Unstated.

---

## Check 1 — Part A event reproduction (independent Wilder RSI + vol) — PASS

Recomputed RSI from scratch (recursive Wilder) and trailing-vol independently; also computed SMA-RSI to expose the classic trap.

| symbol | bar | close | rep RSI | my Wilder | SMA-RSI | rep vol× | my vol× | verdict |
|---|---|---|---|---|---|---|---|---|
| KAVA | 03-12 00:00 | 0.06137✓ | 26.8 | **26.8** | 28.5 | 3.32 | **3.32** | exact |
| TRUMP | 03-12 13:00 | 2.769✓ | 20.6 | **20.6** | 16.8 | 5.94 | **5.94** | exact |
| STO | 03-31 16:00 | 0.1112✓ | 16.4 | **16.4** | 9.4 | 3.13 | **3.13** | exact |
| EDU | 03-13 02:00 | 0.0918✓ | 15.8 | **15.8** | 16.2 | 7.48 | **7.48** | exact |
| SOON | 03-12 09:00 | 0.1488✓ | 26.5 | 21.3¹ | 9.4 | 19.73 | **19.73** | exact under slice |

- **The bot's `utils.indicators.rsi` is `ewm(com=period-1, adjust=False)` = Wilder smoothing.** The scan imports this exact function. SMA-RSI differs by 5–10 points at every event, confirming the scan did NOT accidentally use SMA. Wilder-vs-SMA trap cleared.
- ¹ **SOON resolved:** my first pass (full-parquet) gave 21.3, but SOON has a data gap; under the scan's actual `WARMUP_BUFFER_BARS=420` slice only **28 real bars precede the event**, so `bot_rsi` on that slice = **26.51 ≈ reported 26.5** (reproduced exactly). The report is internally consistent. Note the side-effect below.
- **Minor caveat (not a bug):** symbols with data gaps or <~420 contiguous pre-event bars get **under-warmed** indicators (RSI needs ~60–100 bars; EMA50 ~50+). Their RSI/score can be a few points off a fully-warmed value near the window start. Bounded, edge-only; SOON's true RSI (~21) is still <30 so its Part A membership is unaffected.

## Check 2 — Part B score/side reproduction — PASS (5/5)

Re-ran production `bt.score_bar` on the scan's exact slice (`w0−420h`) at `2026-03-12 00:00`:

| symbol | rep score/side | reproduced | 
|---|---|---|
| STO | 86 / LONG | **86 / LONG** |
| TRX | 74 / LONG | **74 / LONG** |
| PAXG | 74 / SHORT | **74 / SHORT** |
| NEAR | 86 / LONG | **86 / LONG** |
| IO | 86 / LONG | **86 / LONG** |

- **No forming-bar on the 1h axis:** decision uses closed bar `d1.iloc[i]`; forward candles start at `i+1`. Confirmed.
- **Entry gate equivalence checked:** the scan gates on `score≥65` only (not `layers_ok≥6` as `run_backtest` does). This is harmless — the bonus increments are {12,8,8,8}, so reachable scores are {50,58,66,70,74,78,86}; `score≥65` ⇒ ≥2 bonuses ⇒ `layers_ok≥6` automatically, and no score of 65 exists (65 vs 66 is a no-op). The two gates select identically.
- SL%/TP% come from `DistFitSL` fit **as-of-now** (`_production_fit`) — non-reproducible by construction and already caveated in the report ("current-policy replay, not OOS"). Score/side (what determines whether an entry exists) are independent of the fit and reproduce exactly.

## Check 3 — Outcome re-resolution — PASS (10/10)

Re-ran `core.shadow_resolver.resolve_one` on 10 random pre-June entries (fully cache-covered), both geometries:

- **10/10 win/loss labels match** (engine and accuracy). SL-first tie-break, 6bps/side fee + 5/10bps slippage confirmed applied on both legs.
- **Censoring handled correctly:** all 10 had a full 72-bar forward window (`nfwd=72`). Where the horizon is not full the resolver returns `None` → counted `pending`, **excluded from `resolved`**, never scored as a win. Verified structurally: `resolved + pending = total` for both geometries (see Check 4).

## Check 4 — Aggregation / sanity — PASS

| item | check |
|---|---|
| Σ monthly events = 6787 = Part A total = `len(part_a)` | ✓ |
| Σ monthly entries = 130896 = Part B total = `len(part_b)` | ✓ |
| engine W+L = 130390 = resolved; 47923/130390 = **36.75% → 36.8%** | ✓ |
| accuracy W+L = 130784 = resolved; 92650/130784 = **70.84% → 70.8%** | ✓ |
| engine resolved(130390)+pending(506)=130896=Part B; pending excluded from WR | ✓ |
| universe 395 = 525 cached parquets ∩ live binance USDT-M perps; 0 skipped | plausible ✓ |

---

## Finding A (MEDIUM) — 4h forming-bar lookahead in the replay engine

The scan's 4h lookup — `j = searchsorted(d4_ts, ts, "right") − 1` — mirrors `backtest_v3.run_backtest`'s `df_4h.index <= ts`. pandas left-labels 4h bins, so the selected bar's indicators (`ema20_above_50`, `ema_gap_pct`, `adx`, `bb_width`, `ema20_slope`) incorporate the **last 1h close in the 4h window**, which is in the future for any decision bar not at the `:03` phase.

Empirically probed (TRUMP, one 4h window):
```
1h 00:00 -> 4h label 00:00 (closes @03:00)  LOOKAHEAD
1h 01:00 -> 4h label 00:00 (closes @03:00)  LOOKAHEAD
1h 02:00 -> 4h label 00:00 (closes @03:00)  LOOKAHEAD
1h 03:00 -> 4h label 00:00 (closes @03:00)  ok
1h 04:00 -> 4h label 04:00 (closes @07:00)  LOOKAHEAD
```
**3 of every 4 decision bars peek 1–3h into the forming 4h candle.** These 4h inputs set the trade **side** (`ema20_above_50_4h`) and pass/fail the regime + EMA-gap gates. Near 4h EMA crossovers this can flip an entry that the live path (which uses only closed 4h candles) would not have taken. So Part B is **not** a faithful real-time replay — it is inflated/distorted by a look-ahead the live bot does not have.

- **Inherited, not introduced:** the bug lives in `backtest_v3`, the sanctioned replay tool; the scan copies it exactly. Not a fabrication by the producer.
- **Does not change the conclusion:** RSI mean-reversion is a refuted family; the report already labels the 70.8% accuracy-TP WR a "hit-rate artifact, not edge." No promotion rides on these numbers.
- **Required fix in the report:** disclose that the replay carries `backtest_v3`'s 4h forming-bar look-ahead. (Structural fix — building as-of 4h bars — is a `backtest_v3` change, out of scope here.)

## Finding B (LOW-MEDIUM) — Survivorship, unstated

`build_universe` = cached parquets **∩ today's active binance USDT-M perps**. 525 cached → 395 universe; **130 cached symbols contribute zero rows**, 116 of them with data ending at the 2026-05-31 harvest cutoff (`A8, AB, ACM, ADX, AI, AIOZ, AMP, AUDIO, …`). Any perp that delisted or fell out of the active set between March and July is **absent from every historical month**. The report's "0 skipped / 395 reached" is *within survivors only* and reads as full coverage. Delisted oversold-pump names are exactly the worst-behaving RSI<30+volume candidates, so their absence likely **flatters** both Part A forward returns and Part B WR. Add a one-line survivorship caveat.

## Finding C (LOW) — under-warmed indicators for gapped/new symbols

`WARMUP_BUFFER_BARS=420` is ample for continuous series but insufficient when a symbol has a data gap or lists inside the buffer (SOON: 28 effective bars → RSI unconverged, 26.5 vs fully-warmed ~21). Perturbs RSI/EMA50/ADX by a few points for a minority of symbols concentrated at the window start, which can nudge Part A membership and Part B scores at the margin. Bounded edge effect; note it.

---

## Notes on the producer's process claims
- "RSI Wilder, matches the bot" — **verified true** (imports `utils.indicators.rsi`; SMA cross-check rules out the trap).
- "Everything rendered correctly and verified" — the numbers are correct and reproduce exactly; **"verified" overstates** in that the 4h look-ahead and survivorship filter are undisclosed. Hence CORRECTED, not CONFIRMED.
- I made **no commits and no live-code edits**; throwaway audit scripts were written under `_workspace/` and deleted. (Aside: a `taskkill python.exe` I issued to clear a stalled audit process may have also stopped other running python processes — flagging in case the live bot needs a restart via `venv\Scripts\python.exe main.py`.)
