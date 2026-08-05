# Unlock 0-proposal audit — 2026-07-30 (D4)

**Lane context:** autoplan Week-0 D4 · UnlockShortProbeAgent (W1 T-28d / W2 T-14d)  
**Frozen floor:** `RATIO_MIN = 0.10` (unlock tokens / documented circ)  
**Question:** Is IDLE / 0 proposals calendar starvation, or no qualifying cliffs?

## Verdict

**Not calendar starvation.** Forward coverage is healthy; **zero future events clear the frozen ≥10% ratio floor**, so both arms correctly stay silent.

## Evidence

| Check | Result |
|-------|--------|
| Calendar files | 152 under `data/unlock_calendar/` |
| Funnel / `unlock_calendar_coverage` | `forward_days ≈ 55.7`, `starved=False` |
| Max unlock timestamp | `2026-09-23T19:48:46Z` (~55.7d ahead) |
| Future events (any) | 185 |
| Future with `tokens > 0` | 141 |
| Future qualifying (`tokens > 0` ∧ `ratio ≥ 0.10`) | **0** |
| Max future ratio | **0.0908** (GUN) — below floor |
| Near-miss (`0.05 ≤ ratio < 0.10`) | 7 (top: GUN 9.08%, KAITO 6.77%, OPN 5.94%) |
| Past qualifying (`ratio ≥ 0.10`, unlock already passed) | 208 (historical only; probe does not retro-ENTER — `SKIP_LATE`) |
| W1/W2 windows opening in next 7d on qualifying events | 0 / 0 |
| `shadow_decisions` for `UnlockShortProbeAgent` | **0 all-time** (neither arm) |
| Live heartbeat (2026-07-30 log) | `future_qualifying=0`, `events_pending=0`, `entered=0` every tick |

## Interpretation

1. **IDLE is honest:** no W1/W2 entry windows open on events that pass binding filters.
2. **Do not reopen the ratio floor** to force accrual — that would burn the frozen screen / reopen the family without a new prereg.
3. **Week-2 early check (2026-08-13):** if still `future_qualifying=0` and both arms `proposals=0`, treat unlock as **market-idle under frozen spec**, not an engineering defect.
4. Funnel now reports **per-arm** lanes `unlock_short_w1` / `unlock_short_w2` (D3) so a future single-arm burst cannot hide under a pooled count.

## Non-actions

- No ratio/window parameter change
- No live-path / MCP change
- No promotion claim
