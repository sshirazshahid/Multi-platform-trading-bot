# 41 — Policed rebuttal round (liq-cascade)

*Evidence-only. No outcome computation. Vocabulary: ACCRUE_ONLY | SCREEN_NOW | STOP*

## Fable rebuttal to Codex (`SCREEN_NOW`)

Codex correctly notes majors Stage-0 trigger counts clear n≥30 at Θ=1e6 and that FIT must stay fail-closed cell-by-cell. That does **not** force the label `SCREEN_NOW` **this UTC day**:

1. **Protocol budget:** max one heavy stage per UTC day. This iteration’s heavy work is the dual-model agree + hashed prereg. Burning the screen engine (even “schedule now”) under the same day label risks collapsing “eligibility” into “authorized screen run” without a dedicated harvest-integrity check after the 2026-07-25 WS path fix.
2. **Codex’s own implement section** says: *“No implementation or outcome computation is recommended on this verdict day.”* That operational content is **`ACCRUE_ONLY`**, not `SCREEN_NOW`. Using `SCREEN_NOW` while forbidding same-day screen creates vocabulary drift (18_ debate policing precedent).
3. **Undercount + 30/60 bps** remain the prior kill; Stage-0 pass is necessary not sufficient. Passive accrual of clean hours while verifying the harvester still improves the forward sample the eventual screen will use.
4. **FIT arm:** Codex and Fable agree it is weak. A majors-only screen day is still a *future* heavy stage, not a reason to label this iteration `SCREEN_NOW`.

**Fable post-rebuttal label:** `ACCRUE_ONLY`  
**Addendum Fable can accept without changing label:** queue a **separate** UTC-day majors Stage-0→after-cost screen under hash `13ee84e40f2604b6…` once harvest verify is green — that queue is bookkeeping, not `SCREEN_NOW` today.

## Codex-visible challenge (for Codex rebuttal)

If Codex insists on `SCREEN_NOW`, it must answer: what *same-day* action differs from ACCRUE_ONLY’s harvest verify, given Codex already forbids outcome computation today? If none, labels must MATCH on `ACCRUE_ONLY`.
