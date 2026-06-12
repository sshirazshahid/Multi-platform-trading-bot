# /decision

Log an owner decision as a lightweight ADR in `data/decisions/`. Use for calls that change bot behavior, risk posture, or research direction (mode switches, gate changes, blacklist policy, halt/resume, "pursue X / drop Y").

Usage: `/decision <topic>` — if the decision context is in the conversation, draft from it; otherwise ask for the decision in one question.

1. Create `data/decisions/<YYYY-MM-DD>-<kebab-slug>.md`.
2. Use exactly this format, under 30 lines:

```markdown
# <Title>

- **Date:** <YYYY-MM-DD>
- **Status:** open | decided | superseded (by <file>)
- **Decider:** owner | Claude (delegated)

## Context
<1-3 sentences: what prompted this>

## Decision
<the call that was made>

## Why
<1-3 sentences: evidence, tradeoff accepted>

## Revisit when
<concrete trigger: date, trade count, metric threshold — not "later">

## Links
<commit hashes, file:line, related decision files>
```

3. If it supersedes an earlier decision, update that file's Status to `superseded (by ...)`.
4. Decisions are append-only history: never delete a decision file, supersede it.
