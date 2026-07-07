# First-Principles Rebuild / Restructure — Decision & Plan (2026-06-18)

## Request
"Ultrathink how you'd build this bot from scratch across every dimension, then implement it.
Keep what helps, delete the rest, restructure the whole program if needed."

## Ground truth (verified this session, not from memory)
- Mode: PAPER, live disabled. Bot running safely. Will not touch the live process.
- Signal: SIGNAL_SOURCE=tsmom (long-only capital-preservation), CLAUDE_PORTFOLIO_MODE=throttled.
- Live paper tape: recent ~20 trades mostly small losses (-0.5, -0.5, -1.18, ...), recent WR ~= 35%.
- Code: core/ 70 modules / 36.5k LOC; +92 scripts, +30 root, 51 skills, 221 tests; duplicate/dead trees.
- Months of recorded evidence: every entry-edge tested -> NO_EDGE after costs (TA, candlesticks,
  Kalman pairs, funding carry, ETF flows, Kronos, SMA, scalp, OI/dominance). Loss is intrinsic, not beta.

## The machine's conclusion
A from-scratch rebuild reproduces the SAME architecture (it's already near-textbook) and the SAME
NO_EDGE outcome, because the binding constraint is edge x cost x capital, not engineering.
Therefore the correct "rebuild" is NOT new alpha machinery. It is:
1. Converge the program onto its one evidence-backed posture: capital preservation (long-only TSMOM).
2. Prune everything falsified / dead / duplicated so the truth is legible.
3. Keep the irreplaceable assets: warehouse (learning substrate), risk circuit-breakers, provenance,
   PAPER safety, the validated TSMOM signal.

## What I will NOT do (and why)
- Will NOT delete the warehouse, knowledge_model, risk rails, or provenance — irreplaceable + protective.
- Will NOT touch the running bot / live accounts — PAPER and safe; restart is the owner's call.
- Will NOT rebuild a new alpha engine — that is re-deriving NO_EDGE with extra steps.
- Will NOT build toward an income premise — at this capital, the honest goal is preservation, not income.

## Restructure plan (safe, git-reversible, PAPER-only)
Phase 0 — Snapshot: branch + record current tests-green count before any change.
Phase 1 — Dead/duplicate tree removal (verify each is import-unreferenced from the live path first):
  - Collapse pine-strategies/ vs pine_strategies/ duplication.
  - Quarantine-then-remove modules proven NO_EDGE and not imported by bot_engine/main path.
  - Remove __pycache__, stale logs; confirm .gitignore covers them.
Phase 2 — Consolidate the live decision path to a single documented spine (signal->risk->exec->warehouse).
Phase 3 — Make the honesty legible: one ARCHITECTURE.md describing the real (preservation-first) design.
Phase 4 — Verify: full pytest green before/after; diff the live decision path behavior = unchanged.

## Success criteria
- pytest count >= pre-change green count (no regressions).
- Live decision path (tsmom -> risk -> paper exec -> warehouse) behavior unchanged.
- core/ LOC and module count materially reduced; zero falsified-strategy modules on the live import graph.
- One ARCHITECTURE.md that tells the truth: this is a capital-preservation system, not an alpha engine.

## Open decision for owner
Scope of deletion: (A) conservative prune (dead/duplicate only) [RECOMMENDED] vs
(B) aggressive prune (also strip all research/backtest scaffolding to a lean runtime-only repo).
Default = A unless told otherwise.
