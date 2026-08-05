# 41 — Shared dossier: Liquidation-cascade / OI-flush reversion

*Created: 2026-07-29 | Dual-model loop iteration (option 4) | Inputs: 37_ pairs + 40_ strategies + 32_/30_ queue*

## Candidate

**Liquidation-cascade / OI-flush reversion** on USDT-M perps:
- After a large **long**-liquidation spike (forced selling) → fade exhaustion → **LONG**
- After a large **short**-liquidation spike (forced buying) → fade → **SHORT**

Two pre-registered symbol classes (joint multiplicity; do not pool):
1. **Majors:** BTC, ETH
2. **FIT-alt flush:** ALGO, ARB, AVAX, LINK (from 37_ FIT_BAND_PAPER minus ETH)

## Why this candidate (both deep-research reports)

| Report | Mapping |
|--------|---------|
| [37_ pairs](37_deep_research_profitable_futures_pairs_2026-07-29.md) | Liquid majors for execution; FIT basket for research priority — without inventing TA “alpha” on FIT coins |
| [40_ strategies](40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md) | S3 = strongest *unscreened* developable L/S mechanism; event long-after-long-flush / short-after-short-flush |
| [30_edge_queue](30_edge_queue_2026-07-23.md) | Queue #2 after C2; accrue then NEW prereg + Codex cross-check |
| [32_ futures DR](32_deep_research_futures_2026-07-24.md) | Microstructure support (cascade depth collapse); zero rigorous after-cost strategy backtest; forceOrder undercount |

**Not chosen this iteration:** C2 (Deribit snaps not screen-ready locally), F1 remediation (idle, not a new build), listing/unlock (already shadow GO), RSI/breakout/TSMOM/AccBand-profit (ledger STOP).

## Novelty vs ledger

| Nearby family | Status | Differentiation required |
|---------------|--------|--------------------------|
| OI-divergence | REFUTED (directional) | Signal must be **liquidation USD flow** (forceOrder aggregates), not OI level/divergence |
| RSI / MR / TA | REFUTED | No oscillator entry |
| Existing `scripts/run_liquidation_edge_screen.py` | Prior tooling (10 bps, z≥2.5, H1/H2/H3, beta-adj) | This prereg freezes **stressed 30–60 bps** in-event costs + explicit major/alt split + Stage-0 gate; outcomes of any *new* screen only after this hash |

## Data reality (2026-07-29 snapshot)

- Path: `data/liquidations_history.jsonl`
- Schema: `{hour, symbol, long_usd, short_usd, count}` (hour = UTC unix at hour start)
- Side map (harvester): Binance forceOrder SELL → long liq; BUY → short liq ([`scripts/harvest_liquidations.py`](../../scripts/harvest_liquidations.py))
- Rows: **46,428**; distinct hours: **~2,010**; span: **~3,130 h**
- Venue field: absent (Binance-only WS path)
- Binding caveat: Binance `!forceOrder@arr` throttles to latest liq per symbol per ~1s → **notional undercount** (Tardis / prior 32_ notes)

### Stage-0 trigger feasibility (empirical, pre-outcome — distribution only)

| Universe | Threshold max(long,short) USD/hour | Trigger hours |
|----------|-------------------------------------|---------------|
| Market-wide sum | ≥1e6 | 785 |
| BTC alone | ≥1e6 | 396 |
| ETH alone | ≥1e6 | 373 |
| ALGO / ARB / AVAX | ≥1e5 | 4 / 2 / 6 |
| LINK | ≥1e5 / ≥5e5 | 78 / 9 |
| FIT alts at ≥1e6 | — | ~0–2 (LINK only) |

**Stage-0 read:** Majors clear ≥30 triggers at multiple thresholds. FIT-alt high-USD cascades are sparse → FIT variant likely `INSUFFICIENT_DATA` / ACCRUE unless a lower thr is pre-registered (and cost-stressed harder).

## Cost model (binding for future screen)

- In-event stressed round-trip: **30 bps primary**, **60 bps stress** (spreads blow out in cascades — 32_/40_)
- Funding charged if hold crosses settlement
- No vendor spend; use self-collected JSONL only

## Honest prior

~**25% GO** prior. Likely kill: undercount + stressed costs wipe mean. Expectation for this loop’s verdict vocabulary: prefer **`ACCRUE_ONLY`** unless both models can justify `SCREEN_NOW` for majors-only with Stage-0 pass (full after-cost screen = **separate UTC day** per protocol).

## Allowed verdicts (both models)

`ACCRUE_ONLY` | `SCREEN_NOW` | `STOP`

## Implement map (only if both-agree + ai-reviewer APPROVE)

| Verdict | Action this iteration |
|---------|----------------------|
| ACCRUE_ONLY | Verify harvest path live / JSONL append / status; document undercount; **no probe, no MCP** |
| SCREEN_NOW | Only if Stage-0 ≥30 on frozen thr; schedule screen next heavy day — do not burn outcomes into this UTC day if harvest work is the agreed implement |
| STOP | Open-section note; no code |

## Parked for next loop day

S1 adjudication: `tsmom_20d_1h` GATE_BLOCKED at 42/30 (OOS-WR 0.33, AUC 0.5, −EV) in `data/promotion_funnel.json`.
