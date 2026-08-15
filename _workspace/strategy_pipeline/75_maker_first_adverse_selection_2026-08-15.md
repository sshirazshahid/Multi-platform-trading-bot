# 75 — Maker-first: already on, worth ~1/4 of my estimate, and adversely selected

**Type:** MEASUREMENT. Read-only. **No config changed.**

**Trigger:** owner picked option 3 — "build maker-first execution" — from the
74_ diagnosis, where I claimed it was worth **+10.4pp** of breakeven margin.

## Three corrections to my own recommendation

### 1. Maker-first was ALREADY BUILT and ENABLED
`MAKER_FIRST_PAPER_ENABLED=true` in `.env`; the mixin lives at
`core/order_mgmt/maker_first.py`. The cohort's fills:

| fill_type | n | effective round-trip fee |
|---|---|---|
| maker | 12 (33%) | **7.0 bps** |
| taker_fallback | 24 (67%) | **10.0 bps** |

So the 0.371 gross / 0.243 net figures in 74_ **already include** partial
maker capture. There was nothing to build.

### 2. The prize is ~4x smaller than I stated
My +10.4pp was computed at **zero fees** — unreachable. Correct arithmetic:

| scenario | RT fee | net PnL (n=36) | breakeven WR | margin vs 83.3% |
|---|---|---|---|---|
| current mix | 8.9 bps | +1.113 | 80.4% | **+2.9pp** |
| **100% maker** | 7.0 bps | +1.743 | 78.8% | **+4.5pp** |
| zero fee (unreachable) | 0.0 | — | 73.0% | +10.3pp |

Full maker capture is worth **+1.6pp of margin and ~$0.63 over 36 trades** —
inside the noise of a cohort whose CI already includes zero.

### 3. Raising the timeout would be ACTIVELY HARMFUL

The obvious knob is `MAKER_FIRST_PAPER_TIMEOUT_SEC` (45s). Two reasons not to
touch it:

**(a) Adverse selection — measured:**

| fill_type | n | WR | mean gross move | gross payoff |
|---|---|---|---|---|
| maker | 12 | **75.0%** | **-5.25 bps** | 0.272 |
| taker_fallback | 24 | **87.5%** | **+14.88 bps** | 0.317 |

Rested orders fill *worse on every axis*: lower win rate, negative mean gross
move, worse payoff. The fills we win are the ones the market was moving
through — textbook adverse selection. Raising the timeout imports more of it.
(n=12: directional, not conclusive — but the sign is what matters, and it
points against the change.)

**(b) It would manufacture the result in the simulator.** The MCP lane credits
maker fills from a **virtual** post-only order resolved by strict-trade-through
polling, not by a fill event. `carry_runner.py:72-75` states the standing rule:
*"maker economics must not be credited until an event-driven simulator proves
that a post-only order actually filled before the hedge timeout."* Raising the
timeout makes the sim ASSUME more maker fills — the same class of error as
lowering `FEE_BPS_PER_SIDE`, correctly refused in 74_.

## Conclusion

**No change made.** The lever I recommended is already pulled as far as it
honestly can be. The remaining 67% is not recoverable by configuration; it
would require an **event-driven fill simulator** (queue position, trade
events) — a build, not a knob, and one that would reset the cohort.

Net effect on the live-switch question: unchanged. Even at 100% maker the
margin is +4.5pp at n=36, which the 2026-08-14 power arithmetic says does not
resolve at n=100/140/200.
