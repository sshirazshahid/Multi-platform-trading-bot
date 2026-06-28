"""Decision-core package: the seam behind which the signal/decision layer evolves.

This package adds the *validation + autonomous-promotion* machinery the bot was
missing, while reusing the existing (working) signal engines, agents, gate, and
self-healing loop. Nothing here can place a real order or flip OPERATING_MODE:
every autonomous write routes through ``guardrails.assert_paper_self_improve``,
which enforces the immutable safety kernel + a hard PAPER latch.

Modules:
  * guardrails    — the single PAPER+kernel chokepoint for all autonomous writes
  * monte_carlo   — trade-sequence block-bootstrap (the missing validation leg)
  * promotion_loop— mutate/shadow/active-paper promotion orchestrator (PAPER-only)
"""
