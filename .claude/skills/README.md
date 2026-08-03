# Governance skills

These are the skills that govern how strategy work is allowed to proceed in
this repository. They are **binding process**, not reference material — read
the ledger before proposing any strategy idea.

| Skill | Role |
|---|---|
| [refuted-families-ledger](./refuted-families-ledger/) | The binding record of strategy families already refuted on this bot's own data, the one validated family, and the bar to reopen anything. **Consult first** for any strategy request. |
| [strategy-evidence-pipeline](./strategy-evidence-pipeline/) | Orchestrates research → screen → audit → shadow-probe for a candidate. |
| [after-cost-screening](./after-cost-screening/) | How a pre-registered screen is run and judged against the frozen gates. |
| [investment-committee](./investment-committee/) | Bull/bear/debate/verdict roles for adjudicating a candidate. |
| [shadow-probe-integration](./shadow-probe-integration/) | How a CONFIRMED-GO candidate is wired in as a **log-only** probe. |

`refuted-families-ledger/SKILL.md` is parsed programmatically by
`mission_control/state.py` — its path and table structure must stay stable.
Run `load_refuted_ledger()` after editing it.

Trading skills (exchange connectivity, TP precision, risk management,
monitoring, backtest validation, universe research, Windows deployment) live in
the top-level [`skills/`](../../skills/) directory. Committee agent definitions
live in [`.claude/agents/`](../agents/).

## History

Until 2026-08-04 this directory also held a vendored 500-skill general-purpose
pack (DevOps, frontend, ML, AWS/GCP, docs, and so on) inherited from an
unrelated project. None of it was used by the bot, it duplicated a second copy
under `.agents/skills/`, and together they cost ~31 MB and dominated CI. Both
trees were removed in the de-emotion overhaul; only the skills above and the
trading skills survive.
