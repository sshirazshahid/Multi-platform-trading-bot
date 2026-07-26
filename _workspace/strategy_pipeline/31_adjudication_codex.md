# 31 — INDEPENDENT ADJUDICATION (CODEX-SOL-5.6): **NOT OBTAINED**

**Status:** `ADJUDICATION_UNAVAILABLE` — the external adjudicator returned **no verdict**.
**Attempted:** 2026-07-26, two invocations, both failed at the provider before any model output.
**Adjudicated artifact:** run `31_screen_edge_concentration.{md,json}` against frozen prereg
`31_prereg_edge_concentration.md` (SHA-256 `48a2bab1e287769e5e74f4dd360a626f876b08e03a3595db7aa6cff339f89bb1`,
commit `9e52bd92eacef7a52ef04dd03a1449ff0867461d`).

> **This file contains NO adjudication verdict.** No independent judgement was rendered on the
> run's `INDETERMINATE` state, on its operational reading, or on the schema mapping. Nothing in
> this file may be cited as a second opinion. The bridge agent did **not** substitute its own
> judgement, per its binding instruction.

---

## 1. What was attempted

A brief was prepared containing, verbatim where load-bearing:

- the question (UNIFORM vs CONCENTRATED on the 0.031 OOS AUC excess) and its binding context
  (~2,400 refuted tests, −0.24R 30-day expectancy, the three gate-blocked 30-outcome probes, the
  clustered-SE rule, the frozen arithmetic gate);
- the central mass-variant-mining trap the design had to defeat;
- the frozen pre-registration: hash, commit, partition, data freeze, floors, statistics,
  inference scheme, multiplicity (m=16), **§8 STATE 1–4 verbatim**, the **binding interpretation
  clause verbatim**, the **§12 INVALID list verbatim**, and the **§9 acknowledged-boundary clause
  verbatim**;
- the full run result: all four gates, measured geometry, the six-row bucket-floor table, the
  six-row per-bucket statistics table (`A_b`, `A_not_b`, `D_b`, `LCB`, `p(A)`, `p(D)`, 99.375% CI),
  both omnibus rows, the power table, the exploratory decile table, the Simpson check, the
  "protocol deviations: NONE" statement, fidelity notes 1–4, **SG1–SG7**, and **SG8 verbatim**
  (the i.i.d.-injection disclosure making MDE 0.65 a lower bound, plus the run's stated reason for
  not re-running with a clustered injection);
- the run's two-part conclusion, separated explicitly into (a) mechanical `INDETERMINATE` and
  (b) the operational reading "closed operationally; uniformity NOT proven";
- absolute paths to the prereg, sidecar, screen `.md`/`.json`, `31_run.log`, and
  `research/screen_edge_concentration.py` for further read-only inspection.

**Disclosed in the brief, on the record:** Codex is **not a blind adjudicator here**. It
co-authored the pre-registration (`31_design_codex.md`); prereg §4 records that disagreements
**D5, D6, D7, D8, D9, D10 and D12 were resolved in Codex's favour** (the `D_b` interaction
statistic, the `A_b>0.560`/`D_b>0.030` thresholds, the panel-synchronous circular moving-block
bootstrap, m=16, UNIFORM-as-equivalence, the four terminal states replacing the binary framing,
and the ≤25% narrowness cap). It would have been adjudicating the execution of substantially its
own design. The brief stated this as fact and invited maximal harshness rather than defence.

Six questions were posed: Q1 verdict in the prereg's own five-token vocabulary; Q2 **Codex's own**
mapping into the caller's four-token schema (with explicit instruction that the bridge would
transcribe, not substitute); Q3 agree/disagree against the conclusion **as a whole** with the
dissenting part named; Q4 the steelmanned strongest argument against (seeded with the
`P2_MAJOR` point-`D_b`-exceeds-materiality attack, SG8, the m=16-over-30-clusters
pre-committed-to-negative attack, the untested D2 axes, and the single-regime/blend-parameter/seed
caveats) plus whether it changes Q1/Q2; Q5 scope of closure and whether the proposed ledger
language over- or under-claims; Q6 discrete issues.

Brief file, persisted for reuse:
`D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\31_codex_brief.md` (26,560 B).

## 2. How it failed

| # | Command | Result |
|---|---|---|
| 1 | `codex exec < brief` (config default `gpt-5.6-terra`, `model_reasoning_effort=xhigh`) | exit 1 — provider quota error |
| 2 | `codex exec -m gpt-5.6-sol < brief` (the model named in the task) | exit 1 — same provider quota error |

Both runs echoed the full brief and then terminated at the provider, twice, with:

```
ERROR: You've hit your usage limit. Upgrade to Pro (…), visit https://chatgpt.com/codex/settings/usage
to purchase more credits or try again at Aug 2nd, 2026 1:58 PM.
```

`codex-cli 0.144.5`, auth present, project `d:\downloads\trading_bot` trusted, sandbox
`danger-full-access`. The failure is an **account-level usage limit, not a configuration,
timeout, stdin-piping, or trust problem** — the brief was transmitted in full both times and the
limit fired identically on two different model slugs. **Reset: 2026-08-02 13:58.**

Raw captures (temp): `…\scratchpad\31_codex_raw.txt` (27,562 B, terra),
`…\scratchpad\31_codex_raw_sol.txt` (27,403 B, sol). **Verified** by diffing each capture against
the 26,560 B brief: the only non-brief lines in either file are the CLI banner (`Reading prompt
from stdin`, version, workdir, model, provider, approval, sandbox, reasoning effort, session id),
the skills-context-budget warning, and the two quota `ERROR:` lines. **Zero model-authored
content exists in either capture.**

## 3. Consequences

1. **The dual-model both-agree rule cannot be satisfied for this run.** Per
   `_workspace/strategy_pipeline/19_dual_model_loop_protocol.md`, any action requires both models
   to agree. One model has spoken; the second is unavailable until 2026-08-02.
2. **No ledger row should be written on the strength of an independent adjudication**, because
   there was none. Whether the run's own self-reported `INDETERMINATE` plus its self-imposed
   ledger language ("closed operationally; uniformity NOT proven") is sufficient on its own is an
   **owner decision**, not something this bridge may decide.
3. **Nothing about the run changed.** This is a bridge failure, not a finding. The run's files,
   hash verification, and stated verdict stand exactly as they were.
4. **The brief is reusable verbatim.** It is self-contained and outcome-complete; re-running
   `codex exec -m gpt-5.6-sol < 31_codex_brief.md` on or after 2026-08-02 (or from any account
   with quota) yields the adjudication with no re-preparation. The brief should be copied out of
   the session scratchpad if it is to survive.

## 4. What a future adjudication must still be asked

Unchanged from the brief — reproduced here so the questions survive the scratchpad:

- **Q1** verdict in `{CONCENTRATED, NON_UNIFORM_NOT_NARROW, UNIFORM, INDETERMINATE, NO_ANSWER}`.
- **Q2** the adjudicator's **own** mapping into `{CONCENTRATED, UNIFORM, INSUFFICIENT_DATA,
  INVALID_RUN}`, stating explicitly whether `UNIFORM` is available at all given STATE 3's
  equivalence requirement (four of six `D_b` intervals exceed ±0.030) and whether `INVALID_RUN` is
  triggered by SG7's seed, SG8's post-hoc power disclosure, or any §12 INVALID item.
- **Q3** agree/disagree with the conclusion **as a whole**, naming the dissenting part.
- **Q4** the strongest steelmanned argument against, and whether it moves Q1/Q2.
- **Q5** what is actually closed — universally, or only over this frozen six-bucket family / this
  signal / this 30-day window — and whether the proposed ledger language over- or under-claims.
- **Q6** discrete issues, one per line.

---

*Prepared by the bridge agent. No independent verdict is recorded in this file because none was
produced. Read-only throughout: no `core/`, `config.py`, `.env`, `data/` or running process was
touched.*
