# 49 — Drought (~52h) fix + PAPER restart

**Date:** 2026-07-31  
**Scope:** AccBand PAPER research stack restore + launcher profile pin  
**Mode:** PAPER only (no CONTROLLED_LIVE)

## Verdict

Nothing was “dead.” The bot was alive with 0 opens because the activation surface had drifted to an incoherent stack:

| Layer | Pre-fix state | Effect |
|-------|----------------|--------|
| Profile | `AGGRESSIVE_RESEARCH` in `.env` + heartbeat | Disables AccBand / EntryFloor / paper_fallback profile gates (A1 trap) |
| EconGate | `strict` | Dominates OPEN attempts with `economic_gate_model_missing` (no model ever promoted) |
| Band | ADX>30 ON | Correct WR protection — left ON |
| Universe | chop `ER<0.12` | Quiet-market family blocks |
| F1 | `idle_no_edge` | Contango / funding≤0 / time — market regime |
| Last AccBand fill | ~2026-07-29T18:01Z ETH | Matches “no trade” complaint |

## Policy (locked)

- Do **not** disable `BAND_REGIME_FILTER_ENABLED`
- Do **not** lower `MCP_ENTRY_MIN_SCORE` below 66
- Do **not** go CONTROLLED_LIVE
- Do **not** loosen F1 funding thresholds
- Restore designed research stack: `MAX_FLOW_BAND` + `paper_fallback` + AccBand geometry ON

## What shipped

### 1. `.env` activation surface
- `PAPER_TRADING_PROFILE=MAX_FLOW_BAND`
- `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=paper_fallback`
- `ACCURACY_TARGET_MODE=true` (required for AccBand ON under profile gate)
- Kept: band true, EntryFloor 66, `SHADOW_PULLBACK_PROBE_ENABLED=false`

### 2. Launcher harden (`scripts/launcher_supervisor.py`)
- Prefer `.env` over inherited env when resolving `PAPER_TRADING_PROFILE`
- Add `PAPER_TRADING_PROFILE` to `_PIN_KEYS`
- Re-coerce after pin so OBSERVATION cannot keep a research profile
- Tests extended in `tests/test_launcher_safety.py` (26 passed)

### 3. Attended restart
- `TradingBot-24x7` End → kill orphan `main.py` tree → Run (schtask End alone left orphan workers)
- `TradingBot-F1CarryPaper` Run once

### 4. Verify (ground truth = in-process banner + heartbeat)

**Boot banner 2026-07-31 23:08:38:**
```
Profile   : MAX_FLOW_BAND
EntryFloor: MCP_ENTRY_MIN_SCORE=66
AccBand   : ON (fracs buy=0.45/sell=0.35)
EconGate  : mode=paper_fallback
UniverseLoosen: ON
pullback-momentum probe skipped (enabled=false)
```

**Heartbeat:**
- `paper_trading_profile=MAX_FLOW_BAND`
- `effective_config.econ_gate_mode=paper_fallback`
- `effective_config.mcp_entry_min_score=66.0`
- `is_halted=false`

**F1 (fresh rows):** `feeds_fresh=true`; rejects remain economic/regime (`funding_rate <= 0`, `perp_mark < spot_mid`) — expected.

## Honesty bounds

1. AccBand under AccBand geometry is **research flow** (expectancy class ~−0.24R). It is not a CONFIRMED_GO edge. Dual-goal profit screen `30_*` remains CONFIRMED_NO_GO.
2. `paper_fallback` never fakes a promoted model — admits only when TP clears stressed costs.
3. Band ADX/vol veto stays ON — WR protection, not edge creation.
4. F1 is still the only ledger-validated profit family when funding/contango clear; idle under negative funding is correct.
5. Fills resume only when band + universe + cost gates pass; quiet chop can still block ALLOWs.

## Explicit non-goals (unchanged)

- No CONTROLLED_LIVE
- No AccBand ATR loosen / SCALP_MODE off for fake ALLOWs
- No F1 funding threshold loosening
- No new TA probe without dual-agree + hashed prereg

## Success criteria checklist

- [x] Heartbeat profile = `MAX_FLOW_BAND`, EconGate = `paper_fallback`, EntryFloor = 66
- [x] Launcher pin test green
- [x] Boot AccBand ON; pullback skipped
- [x] F1 `feeds_fresh` not permanently false on fresh rows; still fail-closed on negative funding
- [ ] Funnel `economic_gate_model_missing` → ~0 on **new** post-restart OPEN attempts (observe next AccBand cycles; pre-restart history still contains strict-era rejects)
