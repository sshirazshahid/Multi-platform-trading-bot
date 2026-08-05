# Strategy Hunt: Mid-Cap Absolute Movers + Solid Mechanisms
*Generated: 2026-07-30 | Sources: 14 | Confidence: High (local ledger), Medium (external)*

**Owner ask:** Deep-research strategies; develop solid trading mechanisms for coins moving ~$5–$200 USDT on H/D/W; futures L/S or spot; add agents/MCP; plug into the bot.

## Executive Summary

External 2025–2026 evidence and this bot’s ledger **converge**: minute/hourly directional prediction and textbook TA rarely survive retail costs; **delta-neutral funding/basis carry** remains the only structural profit class already validated here (currently idle). Absolute **$5–$200 USDT** movers are a useful *research universe*, not a proven edge. This pass **plugs in** (1) absolute-USDT-band shadow shortlist, (2) MCP tools for movers + F1 status, (3) Hyperliquid funding harvest for F1 conditioning, (4) a **hashed measurement prereg** for continuation/fade — **without** reopening AccBand/RSI/breakout as live profit.

## 1. What the literature says (after costs)

- Minute microstructure ML: features exist, **no strategy survives Binance fees/slippage**; models do not transfer across coins ([Frontiers Blockchain 2026](https://www.frontiersin.org/journals/blockchain/articles/10.3389/fbloc.2026.1811716/full)).
- Intraday “profit-taking” on crypto L/S books is **volatility timing, not reversion** — directional rates ~50% (Zenodo 2026-06-25, Tanaka).
- Hourly ML forecasts: gross can look strong; **naive sign strategies die at 10 bps**; cost-aware magnitude filters matter more than model architecture ([arXiv 2606.00060](https://arxiv.org/html/2606.00060v1)) — already anchors our pullback/TSMOM STOP.
- AdaptiveTrend / multi-horizon trend claims (arXiv 2602.11708) are **textbook trend** — **ledger REFUTED** (0/40 OOS); do not reopen without the reopen bar.
- Funding/basis carry remains the practitioner structural edge ([AI Trading Ranked 2026 guide](https://ai-trading-ranked.com/posts/funding-rate-trading-strategy-explained); ScienceDirect 2025 carry study cited in ledger).

## 2. What this bot already knew (binding)

| Family | Status |
|--------|--------|
| AccBand / MCP directional for profit | CONFIRMED_NO_GO |
| Liq-cascade majors fade | CONFIRMED_NO_GO (2026-07-30) |
| RSI / breakout / TSMOM / pullback live | REFUTED (shadows only) |
| F1 carry | Validated; **idle** (0 ok / ~30k checks / 7d) |
| Listing / unlock shorts | Shadow GO; need events |

## 3. Solid mechanisms (honest product)

1. **Fail-closed economic gate** — refuse −EV AccBand opens (already on).
2. **F1 carry when net_edge > 0** — only live profit path with evidence.
3. **Absolute-USDT mover universe ($5–$200)** — focus shadow agents on mid-priced movers (implemented this pass).
4. **Measurement-first screens** — hashed prereg before outcomes (44_ abs-band continuation/fade).
5. **Telemetry MCP** — interrogate movers + F1 without touching orders.
6. **HL funding harvest** — F1-adjacent conditioner data (queue #4).

## 4. What was plugged in (this pass)

| Component | Role |
|-----------|------|
| `UniverseMonitor` abs USDT band + prefer-$ rank | Shadow shortlist filter |
| `data/mover_shortlist_latest.json` | Snapshot for research/MCP |
| MCP `trading_bot_recent_movers` | Read-only movers |
| MCP `trading_bot_f1_edge_status` | Read-only F1 gate summary |
| `scripts/harvest_hl_funding.py` | HL funding JSONL harvest |
| `44_prereg_abs_usdt_mover_band.md` | Frozen measurement prereg (no outcomes yet) |

**Not plugged (refuse):** live RSI/breakout/AccBand reopen; AdaptiveTrend; “predict every coin” ML without cost-aware gates.

## Key Takeaways

- Moving $5–$200/day is **common**; **profiting** from that after costs is not automatic.
- Prediction without a cost filter increases turnover death — literature + ledger agree.
- Best next screens: **44_ Stage-0** when OHLCV accrual is dense; **C2 gamma** when Deribit snaps ≥30; **F1** when funding clears costs.
- Restart supervisor to activate abs-band shortlist in-process.

## Sources

1. Frontiers Blockchain 2026 — microstructure alpha, fee death  
2. Zenodo Tanaka 2026 — profit-taking = vol timing  
3. arXiv 2606.00060 — cost-aware ML trading  
4. arXiv 2602.11708 — AdaptiveTrend (does **not** reopen our trend STOP)  
5. Local ledger + 30_/41_/42_/43_ pipeline artifacts  
6. Funding carry practitioner guides 2026  

## Methodology

Firecrawl/Exa MCP unavailable — WebSearch + WebFetch + local ledger/warehouse. Sub-questions: (1) after-cost mid-cap patterns, (2) H/D/W momentum/MR survival, (3) what to plug without false edge, (4) absolute-$ band novelty vs %-movers, (5) F1/HL conditioner path.
