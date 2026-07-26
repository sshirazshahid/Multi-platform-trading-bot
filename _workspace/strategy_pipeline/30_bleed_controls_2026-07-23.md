# 30 — Bleed controls (Phase B)
*Date: 2026-07-23 | Implements WR-band plan Phase B*

## Changes applied

| Control | Action | Status |
|---------|--------|--------|
| `BAND_REGIME_FILTER_ENABLED` | `false` → **`true`** in `.env` | Applied; **restart TradingBot-24x7 / launcher_supervisor** required for in-process banner |
| Maker-first PAPER | Keep `MAKER_FIRST_PAPER_ENABLED=true` | Already on — do not disable |
| Entry floor / econ gate | No loosening | Binding — fake WR forbidden |
| AccBand frac | Unchanged pending dual-goal prereg screen | See `30_prereg_accband_frac_dual_goal.md` |

## What the regime filter does

Band-lane only veto inside AccBand carve-out when:

- 4h ADX > 30 (screen-13 WR 59.0% vs 65.7% baseline), or
- BTC 1h ATR / 30d median < 0.7 (WR 55.6%)

Fail-open on missing ADX/ratio. Reject reasons: `band_regime_filter:*`.

**Honesty:** WR-band protection + bleed reduction only. Every screen-13 bucket stayed after-cost negative. This does **not** create profit edge.

## Maker discipline (standing)

- Prefer maker fills; taker_fallback only after timeout.
- Do not raise chase / shorten abandon threshold to force fills for WR cosmetics.
- Prior autopsy: taker_fallback bled more than maker on a 7d window — keep maker-first.

## Explicitly forbidden (this phase)

- Lowering `MCP_ENTRY_MIN_SCORE` to print more wins.
- Switching economic gate to admit stressed-breakeven losers.
- Disabling ExecutionGuard / StrategySpec route checks.
- Claiming success when WR enters band but PF ≤ 1 or EV ≤ 0.

## Verify after restart

Boot **2026-07-23 23:01:27** (epoch=`1784829684.93`):

```text
Profile   : MAX_FLOW_BAND
EntryFloor: MCP_ENTRY_MIN_SCORE=50
AccBand   : ON (fracs buy=0.35/sell=0.3)
BandRegime: ON (ADX>30 / BTC vol<0.7 veto)
EconGate  : mode=paper_fallback
```

`.env` + config load: `BAND_REGIME_FILTER_ENABLED=True`, maker-first True.
