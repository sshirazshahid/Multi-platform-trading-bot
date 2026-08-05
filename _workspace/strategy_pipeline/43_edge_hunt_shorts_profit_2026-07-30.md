# Why no shorts / no profit — and the edge hunt (2026-07-30)

*Sources: local ledger + warehouse + carry_gate_log + 41_ screen | Confidence: High*

## Executive answer

1. **No shorts when the market is “down”** — not because shorts are banned. `SHORTS_REQUIRE_BTC_BEAR=false`. BTC is **neutral**. Directional MCP (long *and* short) is blocked by the **strict economic gate** you asked for (“profitable only”): no promoted model → refuse −EV AccBand opens. Re-enabling shorts via `paper_fallback` would resume the bleed (~−0.24R), not create edge.

2. **Why not profitable** — AccBand WR ≠ profit (ledger NO_GO). F1 (the only validated profit family) is idle: **0 positive net-edge checks in 7 days**. Shadow probes are log-only.

3. **Edge hunt today** — ran the dual-agreed queued screen (liq-cascade majors). **CONFIRMED_NO_GO**. No new strategy to wire.

## What we tested (make it happen)

| Candidate | Status |
|-----------|--------|
| AccBand / MCP directional L/S | Already STOP for profit; gate strict |
| F1 funding/basis carry | Validated but **idle** (compressed funding) |
| Liq-cascade fade BTC/ETH | **Screened today → NO_GO** |
| RSI / breakout / TSMOM / pullback | Ledger REFUTED (shadows only) |
| Listing / unlock shorts | Shadow GO, need real events |
| C2 gamma-expiry | Still accruing Deribit snaps (<30 events) |
| Market making / DEX zero-fee MM | External literature; not this bot’s stack |

## Liq-cascade result (detail)

Stage-0: all major cells ≥30 triggers. After 30/60 bps costs: **0 cells** clear mean>0 ∧ OOS-WR≥0.55 ∧ MC≥0.95 ∧ maxDD≤0.25 ∧ Holm. Best testable: ETH short-flush H12 @30bps mean +9.4bps but MC 0.59 and DD fail.

## What “make it happen” can honestly mean now

- **Keep fail-closed** — flat book beats −EV activity.
- **Wait for F1** — only live path with evidence; enters when `net_edge_bps` clears.
- **Accrue C2** — next screenable queue item when ≥30 conditioned events.
- **Do not** force MCP shorts on a down day — that is the path that already lost money.

Artifacts: `41_screen_liq_cascade_majors.md`, ledger row 2026-07-30, `journal/2026-07-30.md`.
