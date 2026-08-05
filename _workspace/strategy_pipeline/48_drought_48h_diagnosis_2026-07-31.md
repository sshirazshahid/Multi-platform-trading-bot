# 48h Zero-Trade Drought: Diagnosis + Fixes
*Generated: 2026-07-31 | Sources: warehouse + logs + design + web funding | Confidence: High*

## Executive Summary

The program is **not hung**. Over the last 48h it recorded **888 OPEN attempts**, **978 ALLOW candidates**, and **8 fills** (all on 2026-07-29 UTC). Since then it has correctly refused entries. Dominant terminal rejects: **`band_regime_filter:adx_4h>30` (553)** and **`universe_filter_blocked` / chop (305)**. F1 carry ran **8631 checks / 0 ok** — mostly contango/funding≤0, plus a still-live **`feeds_fresh_rate=0`** until the carry process is restarted onto the Jul-30 fix.

## What looked “broken”

| Symptom | Reality |
|--------|---------|
| `last_trade_time=null`, open=0 | Last fill 2026-07-29T18:01Z ETH (~40h ago) |
| “No trades” | 888 execute_open terminals; 872 rejected |
| Heartbeat fresh, not halted | Alive (cycle ~1429, uptime ~32h) |
| EconGate=strict, EntryFloor=66 | Intentional −EV refuse |

## Reject breakdown (48h OPEN funnel)

1. `band_regime_filter:adx_4h>30` — 553 (WR-band protection; filter **ON** in `.env`)
2. `universe_filter_blocked` — 305 (logs: mostly `chop:ER=…<0.12`; loosen V1 already ON)
3. `economic_gate_model_missing` — 14 (strict mode, no promoted model)
4. Fills — 8 (ALGO/FIL/AAVE/ETH on Jul 29)

Candidate SKIPs are dominated by **`scalp_veto:quiet(atr≈0.5–0.8%)`** — quiet regime, not a crash.

## F1 / external regime

- Live F1 gate log still shows `feeds_fresh_rate=0` → **carry worker has not loaded the Jul-30 snapshot fix**. Restart F1/supervisor.
- Even after that, families show **contango_fail + funding_le0** dominate — consistent with compressed BTC funding near 0% ([Convex Jul 27](https://convextrade.com/metrics/btc-funding), [PerpFinder](https://perpfinder.com/funding-rates)). Idle F1 on negative/flat funding is correct.

## Locked design (do not silently violate)

[Universe Flow Loosen V1](docs/superpowers/specs/2026-07-27-universe-flow-loosen-design.md): band **STAYS ON**, EconGate **STAYS STRICT**, only mild universe loosen (already `UNIVERSE_FLOW_LOOSEN_V1=true`).

## Shipped this pass

1. `reject_reason` now `universe_filter_blocked:<detail>` (chop/spread visible in funnel)
2. MCP `open_funnel_status` → `drought_status` + reject families
3. Mission Control `reason_family` collapses detailed universe/band strings
4. Launcher pins `UNIVERSE_FLOW_LOOSEN_V1` + `SHADOW_PULLBACK_PROBE_ENABLED` from `.env`

## Owner decision (plan-tune)

Disabling `BAND_REGIME_FILTER` would increase fills but re-admit the toxic ADX>30 bucket (measured WR headwind). Reply with a letter below.

## Key Takeaways

- Drought ≠ bug; primary choke is **band ADX veto** + **chop**, with F1 **funding-compressed**.
- Restart needed for F1 freshness fix + scorer honesty labels.
- More trades without new edge = more −EV AccBand fills.
