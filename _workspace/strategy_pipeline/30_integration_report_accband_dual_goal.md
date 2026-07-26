# 30 — Integration report: AccBand dual-goal (NO-OP live)

*Date: 2026-07-24 | Screen: CONFIRMED_NO_GO*

## Action taken

| Item | Result |
|------|--------|
| Dual-goal frac screen | **CONFIRMED_NO_GO** (0/12 cells) — `30_screen_accband_frac_dual_goal.*` |
| Ledger | Row added under Refuted |
| Live/PAPER order path | **No new strategy** |
| AccBand knobs | Restored to mid-band **WR research** geometry: `FRAC=0.50`, buy `0.45`, sell `0.35` (was tighter 0.40/0.35/0.30). BandRegime stays ON. |
| Profit path | Unchanged: F1 + event shadow probes only; AccBand does **not** create EV |

## Why this is the best available move

Creating a **59–67% WR and profitable** AccBand book is **impossible** on the measured no-edge MCP path: every geometry cell that lands WR in/near band has BE_WR ≥ 0.68 and EV ≈ −0.24R. Widening TP enough for BE_WR ≤ 0.59 pushes WR below the band and still leaves EV negative.

Therefore:
1. Stop chasing frac retunes for “profit band.”
2. Keep AccBand + BandRegime for **accuracy research** at mid-band fracs.
3. Pursue profit only via evidence lanes (F1 after-cost harvest if edges return; unlock/listing probes; VPIN veto next).

## Restart required

Supervisor/main must recycle to load new `.env` fracs (same as BandRegime gotcha).
