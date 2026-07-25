# 27 — Pre-registration: VPIN jump-risk veto overlay (band lane)

**Status:** FROZEN before any outcome computation  
**Date:** 2026-07-24  
**Queue:** `#1` after AccBand dual-goal CONFIRMED_NO_GO (`30_*`)  
**Expectation:** NO_GO (literature + 2026 practitioner WF adverse for directional VPIN; veto may still fail ΔEV gates)  
**Scope:** **Veto overlay only** — NOT a directional entry. Directional VPIN remains ledger STOP.

## Hypothesis (null)

High VPIN (volume-synchronized toxicity) at decision time does **not** improve after-cost expectancy of AccBand PAPER entries vs the no-veto baseline on BTC/ETH USDT-M, after multiplicity control on the pre-registered θ grid.

## Pilot scope (frozen)

| Field | Value |
|-------|-------|
| Symbols | BTCUSDT, ETHUSDT |
| Venue | Binance USDT-M aggTrades → `data/aggtrades_vpin/` |
| Treatment | Veto AccBand OPEN when `VPIN_t > θ` |
| Baseline | Identical AccBand entries with veto OFF |
| Primary metric | After-cost Δ mean R (veto − baseline); secondary: WR, maxDD |

## Signal construction (frozen — do not change after hash)

1. Volume buckets: VPIN classic volume-clock buckets (bucket volume = 1/50 of trailing 24h volume, recalculated daily UTC).
2. Buy/sell volume via Lee-Ready on aggTrade aggressor (`is_buyer_maker`).
3. VPIN_t = rolling mean of |V_buy − V_sell| / V_bucket over last N=50 buckets (frozen N).
4. θ grid (multiplicity): `{0.55, 0.60, 0.65, 0.70}` — n_θ = 4.
5. No lookahead: VPIN uses only buckets closed before the AccBand signal bar open.

## Trade rules (frozen)

- Universe/path: AccBand MCP directional PAPER outcomes (or replay of same geometry) on BTC/ETH.
- When veto fires: skip OPEN (same as BAND_REGIME_FILTER reject).
- Costs: identical to AccBand / config FEE + SIM slip; no veto-side fee change.
- No new entries invented by VPIN alone.

## Gates (frozen — never loosen)

| Gate | Threshold |
|------|-----------|
| ΔEV after cost (veto − baseline) | > 0 on OOS (else NO_GO — veto without EV lift is churn) |
| MIN_DSR (baseline & veto arms) | ≥ 0.10 informational; promotion still needs full frozen gate |
| MC P(total>0) on veto arm | ≥ 0.95 |
| MC maxDD p95 | ≤ 0.25 |
| Min n OOS skipped+kept events | ≥ 30 else INSUFFICIENT_DATA |

**Multiplicity:** n_trials = 4 (θ grid). Holm or Bonferroni on ΔEV.

## Verdict rules

- **GO:** ≥1 θ with OOS ΔEV>0 AND all hard gates AND adjacent θ same-sign (anti threshold-mining).
- **NO_GO:** no θ improves EV, or only WR improves with worse EV (bleed-mask).
- **INSUFFICIENT_DATA:** harvest missing or n<30.

## Non-goals

- No live install without frozen `core/promotion_gate.py` + owner sign-off.
- No directional VPIN screen.
- No MCP live-path change from this prereg alone.

## Artifacts

- This file + `27_prereg_vpin_jump_veto.json`
- sha256 of markdown body recorded in JSON before any screen outcomes
- Screen outputs (later): `27_screen_vpin_jump_veto.{md,json}`
