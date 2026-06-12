<!-- /autoplan restore point: ~/.gstack/projects/sshirazshahid-Multi-platform-trading-bot/main-autoplan-restore-20260612-080757.md -->
# Plan: Decision Provenance Bundle

**Source:** `reports/agent_architecture_audit_2026-06-12.md` (C1 + H1, H2, H5 + M1 + M2) · **Mode:** PAPER (`CLAUDE_PORTFOLIO_MODE=off` since 2026-06-11 restart) · **Deadline driver:** land before the mode knob leaves `off` so the next Claude-on window is fully attributable.

## Goal

Make every bot decision reconstructable end-to-end: what the LLM was asked, what it answered raw, what the parser/clamps/risk layers changed, and which order/warehouse row resulted. Today five mutation layers each work correctly in isolation but only post-mutation artifacts persist — the decision log, `mcp_decisions.jsonl`, and the warehouse systematically disagree (root of the Jun-4 "attribution corrupt" finding).

## Premises (gate these)

1. **Record fidelity is the right next investment** — the bot is learning-first (warehouse = substrate); no new signal/strategy work is pending that outranks it; all recent edge screens returned NO_EDGE, so the lever is data quality, not new alpha.
2. **Timing is conditional, not hard** *(reframed per TD-1, gate-approved 2026-06-12)* — the deadline applies only IF a Claude-on (`throttled`/`primary`) window is actually scheduled; per-decision source tagging cannot be reconstructed after a mixed-path window. Caveat documented: the current off-window is bundle-confounded (4 knobs activated by one restart per `scripts/experiments.json`), so micro-provenance cannot rescue macro attribution for THIS window; knob-staggering is an owner TODO.
3. **Additive-only** — instrumentation and validation must not change order placement, sizing, or exits. Nothing here may lower WR (standing WR-floor directive) or alter expectancy; clamps at parse time mirror what execution already enforces.
4. **Applies on restart** — changes take effect at the next bot restart, which the owner schedules (never bounce the bot unattended).

## In scope

1. **Provenance bundle (audit fix #1 — core)**
   - `utils/claude_client.py`: persist `raw_response` (full text) per call in `data/claude_audit/calls_*.jsonl`; prompt stored as SHA-256 hash + length, with full prompt sampled (e.g. 1-in-20 calls) to bound file growth; keep existing metadata fields.
   - `core/mcp_brain.py`: mint `decision_id` (uuid) at parse time; log per-action `source: claude|algo`, `repaired: bool`, `attempt` count, and pre-clamp `sl_raw/tp_raw/size_pct_raw/leverage_raw` alongside clamped values in `mcp_decisions.jsonl`.
   - Thread `decision_id` through `bot_engine._execute_open` → `order_manager.open_position` → warehouse row (new nullable column) and log order-validation rejections against the `decision_id`.
2. **Parse-time ingestion bounds (fix #2)** — clamp `leverage`/`size_pct` to config bounds and validate `symbol` against the candidate set at `core/mcp_brain.py:2234-2248`; log every clamp/reject. Mirrors execution-layer enforcement; no order-path change.
3. **Truncation/repair visibility (fix #4)** — log dropped section names when the prompt cap truncates (`mcp_brain.py:1663-1665`); set `repaired=true` on decisions parsed via `_repair_json`.
4. **Warm-restart advice drop (fix #6)** — on restart, discard reloaded `position_advice` from `mcp_state.json` (keep timing/budget fields) so stale advice is never consumed (`mcp_brain.py:1092-1104`).
5. **Doc-rot pass (fix #7)** — correct "advisory-only" headers in `core/claude_advisor.py`/`core/claude_schemas.py`; fix stale CLAUDE.md `total_pnl` gotcha (field is now `net_pnl`).

**Included (TD-2, gate-approved 2026-06-12):** atomic writes for `knowledge_model.json` + `trailing_peaks.json` (fix #3) in Tranche C, via shared `utils/atomic_io.py` helper (3rd use justifies extraction; pattern from `risk_manager.py:214-215`).

## NOT in scope

- Caution-exemption redesign (fix #5) — behavior change touching the primary strategy's gating; owner decision, deferred.
- Any change to entry/exit logic, sizing multipliers, SL/TP computation, or the five mutation layers themselves — this plan records them, it does not alter them.
- History purge / repo-privacy remediation (separate track, chip task_0e154803).

## Test plan (sketch — Eng review expands)

- TDD per repo convention: failing tests first for (a) raw_response present in audit entry, (b) decision_id round-trip decision→warehouse row, (c) clamp logging on out-of-bounds leverage/size/symbol, (d) repaired/truncated flags, (e) advice dropped on restart, (f) rejection logged with decision_id.
- Frozen-inventory guard (`tests/test_venue_write_sites_frozen.py`) must stay green — no new venue-write sites.
- Full suite green before restart recommendation.

## Risks

- `calls_*.jsonl` growth from raw responses (responses are compact JSON ≤ ~2KB; monthly files; acceptable — prompts are the large part, hence hash+sample).
- Warehouse schema change (additive nullable column) must not break existing readers (learning_engine, scorecard, dashboards).
- Decision-log consumers (`scripts/weekly_scorecard.py`, learning engine) must tolerate new fields (JSONL readers are key-tolerant; verify).

---

# /autoplan CEO REVIEW (Phase 1) — 2026-06-12

Mode: SELECTIVE EXPANSION (forced by /autoplan). Voices: **[subagent-only]** — codex rate-limited until 2026-07-10 (probe verified; degradation matrix applied).

## Step 0 record

- **0A Premises:** challenged and presented at gate D1; user confirmed "Premises hold." Post-gate, the independent voice challenged premise 2 (see Taste Decision TD-1).
- **0B Existing leverage:** extends `data/claude_audit/calls_*.jsonl` (exists), `mcp_decisions.jsonl` (exists), warehouse (additive column), `risk_manager.py:214` atomic pattern, existing leak-test conventions. Nothing is rebuilt.
- **0C Dream state:** CURRENT (5 mutation layers, post-hoc artifacts only) → THIS PLAN (every decision carries id + raw + source, threaded to warehouse, with a reconciliation consumer) → 12-MONTH IDEAL (full decision replay: given an id, reconstruct prompt→response→mutations→order→outcome; counterfactual analysis; ML-labeling join key). Moves toward; does not foreclose the event-sourced journal.
- **0C-bis Alternatives:** A) additive provenance bundle (completeness 9/10, M effort, low risk) — CHOSEN; B) raw_response+source only (5/10 — leaves C1's intent↔execution gap); C) event-sourced decision journal (10/10 but L/XL, violates additive-only premise; remains the 12-month direction). Auto-decided A (P1 vs B, P5/P3 vs C); not close → mechanical.
- **0D Selective-expansion scan:** complexity check: ~8-10 files — at the smell threshold, justified (the thread follows one decision end-to-end; fewer files = broken thread). Expansions decided in Decision Audit Trail (E1-E5).
- **0E Temporal interrogation (resolved NOW):** one `decision_id` per ACTION; one `response_sha256` per response shared across its actions. Warehouse: `ALTER TABLE` additive nullable `decision_id` on trades AND candidates at startup. Rejections: logged to `mcp_decisions.jsonl` as `{"type":"rejection","decision_id":...}` rows (same file, no new store). Deterministic exits (SL/TP/trailing) are not LLM/algo decisions → `decision_id NULL`, semantics documented.
- **0F Mode:** SELECTIVE EXPANSION confirmed; approach A applies.

## Dual voices

**CLAUDE SUBAGENT (CEO — strategic independence):** 10 findings, overall *sound-with-concerns*. Highlights: (S1) deadline premise confounded — `scripts/experiments.json` registers 4 simultaneous knob changes sharing one restart, so the off-window is bundle-attributable regardless of micro-provenance; (S2) no contingency for the modal outcome "Claude stays off" — split mode-agnostic core from Claude-specific capture; (S3) recorder without playback — no consumer joins the new fields to outcomes; (S4) prompt hash+1-in-20 sampling defeats "reconstructable end-to-end" (~55MB/mo full storage is trivial, dir verified gitignored); (S5) symbol-∈-candidates is a NEW order gate, not a mirror — `bot_engine.py:1851-1858` skips the whitelist in TRADING_MODE=all, so parse-rejection could change behavior inside measured windows; (S6) exit-side labels still ~25% NULL r_multiple in recent closed rows — entry provenance doesn't fix label corruption; (S7) only the id/source/raw core is deadline-coupled; (S8) advice-drop is a behavior change, not additive; (S9) provenance arrives too late to inform the keep/kill-Claude verdict being settled by the CURRENT window; (S10) storage-risk asymmetry (subsumed by S4).

**CODEX (CEO):** [codex-unavailable: usage limit until 2026-07-10] — single-model mode.

```
CEO DUAL VOICES — CONSENSUS TABLE
  Dimension                            Claude   Codex  Consensus
  1. Premises valid?                   PARTLY   N/A    FLAGGED (premise 2 conditional — TD-1)
  2. Right problem to solve?           YES*     N/A    FLAGGED* (right IF tied to a named decision — TD-1)
  3. Scope calibration correct?        SPLIT    N/A    FLAGGED (tranche split adopted)
  4. Alternatives sufficiently explored? YES    N/A    OK (0C-bis; B/C rejected with reasons)
  5. Competitive/market risks covered? N/A-dom  N/A    OK (internal tool; opportunity-cost = S9 → TD-1)
  6. 6-month trajectory sound?         YES      N/A    OK (reversibility 5/5, platform join-key)
```

## Plan amendments adopted from voices (auto-decided; see Decision Audit Trail)

1. **Tranche split (S2/S7):** Tranche A "pre-knob core" = decision_id + source + pre-clamp capture + rejection linking (valuable for the ALGO path running NOW). Tranche B "Claude-capture" = raw_response + full prompt storage + repaired/truncated flags. Tranche C "hygiene, no deadline" = doc-rot, advice-drop, atomic writes (if accepted at gate). All can ship in one PR but are independently revertable.
2. **Full prompt storage; sampling machinery deleted from scope (S4/S10):** store full prompt + full raw_response per call (gzip optional), local gitignored dir; add a test pinning `data/claude_audit/` as untracked (ties to today's repo-privacy FAIL).
3. **Symbol validation is LOG-ONLY (S5):** out-of-candidate-set symbols are recorded (`symbol_unlisted: true`), never rejected at parse. Leverage/size clamps stay (true mirrors of execution).
4. **Consumer in scope (S3):** definition-of-done adds `scripts/decision_reconciliation.py` — joins decision_id across mcp_decisions → warehouse, emits per-source WR/expectancy and orphan diffs (decisions-without-orders / orders-without-decisions) — plus a weekly-scorecard provenance-health line (audit-write failures, repaired/truncated counts, orphans).
5. **Audit-write failures become visible (new finding A1):** `_audit_log` currently swallows all exceptions (`utils/claude_client.py:56-57`) — silent provenance loss. Add failure counter surfaced in the scorecard line.
6. **Advice-drop reclassified (S8):** explicit behavior change with its own test, listed under Tranche C, not "additive."
7. **Label-quality acceptance check (S6):** post-restart verification that NULL `r_multiple` rate on newly closed trades is <2%; if violated, label repair becomes the next P1 (TODO logged).

## Section findings (1-11)

1. **Architecture — 1 finding (A1, adopted above).** New flow diagrammed below; no new components, no new coupling beyond a threaded id param; SPOF none; 10x/100x load trivial (O(1) appends). Rollback: git revert; extra JSONL keys harmless to key-tolerant readers; nullable column ignored by old code.
2. **Error & rescue — mapped, 1 GAP (A1).** Registry below.
3. **Security — no new attack surface; net hardening.** Parse-time clamps add defense-in-depth against prompt-injection-shaped LLM output. Raw prompts contain positions/balances → stays local: dir gitignored (verified) + new test pins it. No secrets in new fields.
4. **Data flow & edge cases — shadow paths resolved in 0E** (nil → algo actions also get ids; empty actions → response-level record still written; upstream error → no id, NULL semantics documented; crash between order and warehouse write → reconciliation flags orphan, visible not silent).
5. **Code quality — 1 decision:** if atomic-writes E1 is accepted, extract a shared `utils/atomic_io.py` helper instead of a third inline copy (3 uses justifies the abstraction; P4).
6. **Tests — diagram + specs expanded** (Eng phase owns the full test plan artifact): adds audit-failure counter, symbol log-only, advice-drop behavior, startup migration, gitignore pin, reconciliation orphans, full-prompt round-trip. No prompt content changes → no eval suites triggered.
7. **Performance — no issues.** ~13MB/mo responses + ~55MB/mo prompts, local disk; appends O(1); no new queries on hot paths; decision_id unindexed (analytic-only access) — noted, not needed now.
8. **Observability — this plan IS the observability fix;** plus scorecard provenance-health line (amendment 4/5).
9. **Deployment — applies on owner-scheduled restart;** startup additive migration; no flag needed (log-only paths); risk window none (single process). Rollback: one revert.
10. **Trajectory — reversibility 5/5;** removes investigative debt; decision_id is the future ML-labeling join key; Phase-2 (event journal) unforeclosed.
11. **Design/UX — SKIPPED (no UI scope detected in Phase 0).**

## Diagrams

```
PROVENANCE FLOW (new fields in [brackets])
 claude_client.call_claude_cli ──▶ calls_YYYY-MM.jsonl [prompt_full, raw_response, write_fail_ctr]
        │ raw text
        ▼
 mcp_brain parse ──▶ mint [decision_id, response_sha256, source, repaired, attempt]
        │ actions (clamped; [/*_raw pre-clamp*/, symbol_unlisted])
        ▼            └──▶ mcp_decisions.jsonl [all of the above; truncated_sections]
 bot_engine._execute_open ──▶ order_manager.open_position
        │                          │ reject ──▶ mcp_decisions.jsonl [{"type":"rejection",decision_id}]
        ▼                          ▼
   (risk multipliers)         exchange / sim
                                   │
                                   ▼
                       warehouse row [decision_id NULLABLE]
                                   ▲
 scripts/decision_reconciliation.py ──▶ per-source WR/expectancy + orphan diff
 SHADOW PATHS: CLI error→no id (NULL, documented) · empty actions→response row only
               · crash pre-warehouse→orphan flagged by reconciliation · audit write fail→counter
```

## Error & Rescue Registry

| Codepath | What can go wrong | Exception | Rescued? | Action | Visible? |
|---|---|---|---|---|---|
| `_audit_log` write | disk full / IO error | OSError | Y (today: swallowed) | **GAP→fix: count + scorecard line** | today: SILENT ← A1 |
| warehouse startup migration | old schema, locked db | sqlite3.OperationalError | Y | additive ALTER, idempotent, abort-safe | log line |
| mcp_decisions append | IO error | OSError | Y | log-and-continue + counter | counter |
| scorecard/learning readers | unknown new keys | KeyError (if strict) | Y | verified key-tolerant; test added | n/a |
| reconciliation | orphan ids both directions | (data condition) | Y | reported, not raised | report table |

## Failure Modes Registry

| Codepath | Failure mode | Rescued? | Test? | User sees? | Logged? |
|---|---|---|---|---|---|
| audit write | silent provenance loss | Y(fix) | Y(new) | scorecard line | Y |
| parse clamp | out-of-bounds values | Y | Y(new) | decision row | Y |
| symbol unlisted | flagged not rejected | Y | Y(new) | decision row | Y |
| id threading | order w/o decision row | Y | Y(new) | reconciliation orphan | Y |
| advice reload | stale advice consumed | Y(drop) | Y(new) | n/a (removed) | Y |

No row is RESCUED=N — zero CRITICAL GAPS after amendments.

## NOT in scope (CEO)

- Caution-exemption redesign (audit fix #5) — behavior change on primary strategy gating; owner decision.
- Any change to the five mutation layers' logic — recorded, not altered.
- mcp_decisions.jsonl monthly rotation — consumer-coupled; TODO.
- Dashboard provenance panel — outside radius; TODO.
- Deep exit-side label repair (S6) — separate P1 candidate; acceptance check only here.
- Experiment-knob staggering for attribution (S1) — owner operational decision; TODO.
- History purge / repo privacy — separate track (untrack commit 0c9f44d already landed).

## What already exists

`calls_*.jsonl` audit infra (extend) · `mcp_decisions.jsonl` (extend) · warehouse SQLite (additive column) · atomic-write pattern `risk_manager.py:214` · leak-test conventions (`test_paper_no_live_writes.py`) · `weekly_scorecard.py` (extend with provenance-health + per-source lines).

## Dream state delta

After this plan: every LLM/algo decision is reconstructable and joined to outcomes; the remaining gap to the 12-month ideal is event-sourcing the mutation layers themselves (deferred, unforeclosed) and exit-side label completeness (S6 TODO).

## CEO Completion Summary

| Item | Result |
|---|---|
| Mode | SELECTIVE EXPANSION (forced) |
| Step 0 | premises gated (user-confirmed; TD-1 raised post-hoc), approach A |
| S1 Arch | 1 issue (A1) |
| S2 Errors | 5 paths mapped, 1 GAP (A1, fixed in scope) |
| S3 Security | 0 issues, net hardening + gitignore pin test |
| S4 Data/edge | 4 shadow paths resolved |
| S5 Quality | 1 decision (shared atomic helper if E1) |
| S6 Tests | diagram expanded, 7 new test specs |
| S7 Perf | 0 issues |
| S8 Observability | 2 additions (scorecard line, failure counter) |
| S9 Deploy | 0 risks beyond restart timing |
| S10 Trajectory | reversibility 5/5 |
| S11 Design | SKIPPED (no UI scope) |
| Voices | subagent ran (10 findings); codex unavailable |
| Unresolved | TD-1 (premise-2 framing) + E1 (atomic writes) → final gate |

---

# /autoplan ENG REVIEW (Phase 3) — 2026-06-12

Voices: **[subagent-only]** (codex rate-limited). Phase 2 (Design) skipped — no UI scope. Deviation note: eng SKILL.md boilerplate not re-read (byte-identical to CEO preamble already loaded); executed from /autoplan's embedded Phase-3 checklist + the carved review-sections template at full depth.

## Scope challenge (code-grounded, via independent subagent with repo access)

Threading is **feasible exactly as sketched**: `action` is a plain dict end-to-end; `bot_engine.py:2891-2900` already forwards `candidate_id`/`mcp_score`/`model_version` into `order_manager.open_position` keyword params (`order_manager.py:572-577`); `record_trade_open` is keyword-only (`warehouse.py:349-368`); warehouse already has an idempotent additive ALTER block (`warehouse.py:213-286`) with per-thread connections + WAL. `decision_id` is one more optional kwarg + nullable column.

## ENG DUAL VOICES — CONSENSUS TABLE

```
  Dimension                       Claude              Codex  Consensus
  1. Architecture sound?          YES (w/ contract)   N/A    OK* (E-2 contract spec added)
  2. Test coverage sufficient?    NO → 9 added        N/A    FLAGGED→fixed (specs (g)-(o))
  3. Performance risks addressed? PARTLY              N/A    FLAGGED→fixed (E-11 streaming)
  4. Security threats covered?    YES + 1 pin         N/A    OK (mcp_decisions pin added)
  5. Error paths handled?         NO → rotation       N/A    FLAGGED→fixed (E-1 CRITICAL)
  6. Deployment risk manageable?  YES                 N/A    OK (restart-applied, additive)
```

## Eng findings adopted (auto-decided; full evidence in subagent record)

- **E-1 CRITICAL — destructive decision-log rotation (`mcp_brain.py:4020-4024`):** active file truncates to last 500 lines at 2MB; with bigger/more frequent rows the week's history self-erases and the reconciler reports phantom orphans. **Fix in scope:** rotation becomes archive-rename (`mcp_decisions.YYYYMMDD.jsonl`) instead of truncate — filename preserved for existing consumers (`health_watchdog.py:56` tails it; `import_history.py:179` schema-fluid), history bounded but never destroyed.
- **E-2 HIGH — return-contract change named:** `_call_claude` (`mcp_brain.py:1674-1709`) returns dict only; raw text/repair/attempt discarded. **Fix:** optional `meta_out: dict` out-param on `call_claude_cli` (other 4 call sites untouched — advisor/analyst/trader/prediction_agent); `_call_claude` returns `(dict, meta)`; `response_sha256` computed from the same raw string in both `_audit_log` and the parse site.
- **E-3 HIGH — ~46 rejection exits in `_execute_open` (`bot_engine.py:1764-2920`) + ~8 in `open_position`:** **strategy chosen:** reason-stash one-liners (`action["reject_reason"]=...`) at each exit, single log row at the caller loop (`bot_engine.py:1610-1618`); unset reason logs as `"unspecified"` (visible, improvable). Largest diff item — surfaced at gate as TD-3. `order_manager.mcp_brain` may be `None` (`order_manager.py:589`) → rejection logging never routes through it.
- **E-4 HIGH — cycle-cap drops (`actions[:MAX_ACTIONS_PER_CYCLE]`, `bot_engine.py:1607`):** capped actions get `{"type":"rejection","reason":"cycle_cap"}` rows.
- **E-5 — orphan taxonomy + exit attribution:** reconciler exempts non-OPEN advice (monitor HOLD/TIGHTEN/… ~95% of rows by design), cycle-capped, `reconciled_exchange`/manual/DCA families. **Added small scope:** `exit_decision_id` nullable column via `record_trade_close` (`warehouse.py:393-434`) so CLOSE decisions join too.
- **E-6 — three `record_trade_open` sites** (`order_manager.py:1018`; `position_tracker.py:718`, `:1163`): NULL-legitimate classes whitelisted by `strategy_family`; tests cover each.
- **E-7 — pre-existing kwarg-drop bug fixed in scope:** spot-fallback retry (`order_manager.py:976-978`) silently drops `candidate_id`/`mcp_score`/`model_version` today; will forward all provenance kwargs + test. Also test which row wins under `INSERT OR IGNORE` (`warehouse.py:372-388`).
- **E-8 — premise-3 reframed:** `_execute_open` IGNORES Claude's leverage/size (`bot_engine.py:1780-1781`, tier selector assigns) — parse-clamped values are a third value, recorded as such; reconciler compares knowing executed leverage comes from the tier. "Mirror" language removed.
- **E-9 — concurrency:** module-level `threading.Lock` around `_audit_log` (shadow-predictor thread + 4 other callers write the same monthly file; multi-KB lines exceed Windows append-atomicity). Decision-log writes remain single-writer (main loop) + rotation is read-rewrite, documented.
- **E-10 — truncation promise downgraded (honest version):** log `(orig_len, cut_len, truncated=true, tail_80)`; section-name attribution needs a prompt-builder restructure → TODO.
- **E-11 — storage corrected:** realistic 150-500MB/mo (monitor 90s→30s adaptive cadence + 5 caller modules), local-only; `weekly_scorecard._calls_per_day` (`:189-191`) switches to line-streaming; retention = zip >3 months (TODO note).
- **E-12 — candidates linkage scoped:** `candidates.decision_id` populated on the algo path only (Claude-path candidates structurally NULL; post-parse UPDATE deferred).
- **E-15 — security:** raw prompts persist balances/positions plaintext → same class as `positions.json`; acceptable local-only **because** gitignore pins are tested: add pin test for `data/mcp_decisions*` (`.gitignore:82`) alongside `data/claude_audit/` (`.gitignore:122`). No keys in prompts (stripped, `claude_client.py:135`).

## Test diagram (new codepaths → coverage)

```
NEW DATA FLOWS                          TEST (type)
 raw prompt/response → calls_*.jsonl     unit: round-trip + lock-protected concurrent append (c)
 decision_id mint (claude|algo)          unit: source tagging; algo fallback fresh ids, no reuse (f)
 pre-clamp capture + clamp log           unit: out-of-bounds leverage/size; symbol_unlisted log-only
 rejection rows (46 exits + cycle_cap)   unit: sampled reasons + cap row (a,e per gate list)
 decision_id → warehouse (open/close)    integration: round-trip incl. exit_decision_id; NULL paths (i)
 spot-fallback kwarg forwarding          unit: regression for today's drop (b)
 archive-rotation                        unit: rotation preserves rows; reconciler reads archives (a)
 reconciliation orphan taxonomy          unit: exemption classes; true orphan detected (d)
 scorecard streaming + provenance line   unit: multi-KB lines; failure counters surface (h)
 startup ALTER migration                 unit: idempotent on old+new schema
 gitignore pins                          unit: claude_audit/ + mcp_decisions* untracked
 advice-drop on restart                  unit: behavior change pinned (Tranche C)
NEW BACKGROUND/ASYNC: none · NEW UX FLOWS: none · NEW EXTERNAL CALLS: none
2am-Friday test: kill bot mid-cycle after order, before warehouse write → orphan visible in reconciler, not silent.
Hostile-QA test: malformed repaired-JSON with trailing actions dropped → repaired=true + action-count delta logged.
Flakiness: no time/randomness deps (ids are uuid4 — assert format not value).
```

Frozen-inventory guard (`tests/test_venue_write_sites_frozen.py`) stays green — plan adds zero ccxt write sites (verified: logging only).

## Eng completion summary

| Item | Result |
|---|---|
| Scope challenge | feasible; kwargs precedent verified at 3 layers |
| Architecture | diagram (Phase 1) + E-1/E-5 deltas; 1 CRITICAL fixed in scope |
| Tests | 12-row diagram; 9 specs added beyond plan's list; artifact on disk |
| Performance | E-11 fixed (streaming); appends O(1) |
| Security | 2 gitignore pin tests; plaintext-locality documented |
| Failure modes | registry updated; zero RESCUED=N rows |
| Voices | subagent (15 findings); codex unavailable |
| jq | absent → per-phase tasks JSONL skipped (install jq to enable /autoplan aggregation) |

---

# DECISION AUDIT TRAIL

| # | Phase | Decision | Class | Principle | Rationale | Rejected |
|---|---|---|---|---|---|---|
| 1 | 0 | Plan target = provenance bundle from audit fix plan | mechanical | P6 | only pending plan-shaped work; deadline-coupled | other audit fixes as primary |
| 2 | 0 | UI scope NO (1 weak match), DX scope NO (0) | mechanical | — | grep thresholds not met | running design/DX phases |
| 3 | 1 | Approach A (additive bundle) over B (minimal) / C (event journal) | mechanical | P1,P5 | B leaves C1 gap; C violates additive premise | B, C |
| 4 | 1 | Cross-project learnings: enable | mechanical | P6 | local-only, solo dev, recommended default | project-scoped |
| 5 | 1 | Full prompt storage; delete sampling (S4) | mechanical | P1,P5 | sampling defeats stated goal; 150-500MB/mo local is cheap | hash+1-in-20 |
| 6 | 1 | Tranche split A/B/C (S2,S7) | mechanical | P3,P5 | mode-agnostic core decoupled from Claude-capture | monolith bundle |
| 7 | 1 | Symbol validation LOG-ONLY (S5) | mechanical | P5 | rejection = new order gate inside measured windows | parse-time reject |
| 8 | 1 | Reconciliation consumer + scorecard line in scope (S3) | mechanical (P2 auto-approve) | P2 | in blast radius, <1d CC, makes data queryable | fields-only recorder |
| 9 | 1 | Audit-write failure counter (A1) | mechanical | P1 | silent provenance loss otherwise | leave swallow |
| 10 | 1 | Advice-drop = declared behavior change (S8) | mechanical | P5 | honesty in scope labeling | "additive" framing |
| 11 | 1 | r_multiple NULL <2% acceptance check (S6) | mechanical | P1 | provenance useless if labels corrupt | ignore exit side |
| 12 | 1 | E2 jsonl rotation → deferred TODO; E4 dashboard → TODO; E5 fold; delight (3) schema_version fold; (4,5) defer | mechanical | P3 | consumer-coupled / outside radius | in-scope now |
| 13 | 3 | Rotation → archive-rename, filename kept (E-1) | mechanical | P1,P5 | consumers tail the filename; truncation destroys deliverable | monthly-named files; leave truncate |
| 14 | 3 | meta_out out-param contract (E-2) | mechanical | P5,P3 | 4 other call sites untouched | envelope return refactor |
| 15 | 3 | exit_decision_id column (E-5) | mechanical (P2 auto-approve) | P2 | tiny, same pattern, completes close attribution | entry-only joins |
| 16 | 3 | Spot-fallback kwarg fix in scope (E-7) | mechanical | P1 | fixes pre-existing data loss on same thread | defer |
| 17 | 3 | _audit_log threading.Lock (E-9) | mechanical | P1 | real multi-thread writers verified | per-line fsync; ignore |
| 18 | 3 | Truncation promise downgraded (E-10) | mechanical | P5 | honest log beats heuristic section scan | header-scan heuristic |
| 19 | 3 | Scorecard streaming (E-11) | mechanical | P3 | read_text() on 100s-of-MB files | leave whole-file loads |
| 20 | 1+3 | TD-1 premise-2 framing | TASTE → **USER: adopt reframe** (2026-06-12) | P5 | conditional deadline + confound documented | hard deadline |
| 21 | 1+3 | TD-2 atomic writes + shared helper (E1) | TASTE → **USER: include** (2026-06-12) | P1,P4 | closes audit H4 same restart; helper extracted | defer to TODOS |
| 22 | 3 | TD-3 rejection instrumentation breadth | TASTE → **USER: all 46 sites** (2026-06-12) | P1 | complete taxonomy day one; mechanical one-line stashes | top-10 + generic |

## TODOS (deferred, for tasks/todo.md)

- P2 mcp_decisions archive retention policy (zip >3mo) + calls_*.jsonl retention — S effort.
- P2 dashboard provenance panel (source/repaired/orphan %) — S-M.
- P1-candidate deep exit-side label repair if NULL r_multiple check fails post-restart — M.
- P2 owner: stagger experiment knobs (one-at-a-time reversion) for clean attribution — operational.
- P3 prompt-builder section-structure for named truncation attribution — M.
- P3 Claude-path candidates.decision_id post-parse UPDATE linkage — S.

## Unresolved Decisions

None. The 2026-06-12 gate was initially dismissed, then answered by the owner the same day: **D2 = APPROVED**, TD-1 = adopt reframe, TD-2 = include, TD-3 = all 46 sites. Plan status: **APPROVED — ready for implementation** (TDD per test-plan artifact; applies on owner-scheduled restart).

## GSTACK REVIEW REPORT

| Review | Trigger | Why | Runs | Status | Findings |
|--------|---------|-----|------|--------|----------|
| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_open (PLAN via /autoplan) | 7 proposals, 4 accepted, 6 deferred; 10 voice findings, 7 adopted |
| Codex Review | `/codex review` | Independent 2nd opinion | 0 | UNAVAILABLE (usage limit until 2026-07-10) | — |
| Eng Review | `/plan-eng-review` | Architecture & tests (required) | 1 | issues_open (PLAN via /autoplan) | 15 issues (1 CRITICAL fixed in scope), 0 critical gaps remaining |
| Design Review | `/plan-design-review` | UI/UX gaps | 0 | SKIPPED (no UI scope) | — |
| DX Review | `/plan-devex-review` | Developer experience gaps | 0 | SKIPPED (no DX scope) | — |

- **CROSS-MODEL:** single-model run ([subagent-only]); cross-model consensus unavailable — no User Challenges could trigger.
- **UNRESOLVED:** 0 — gate answered 2026-06-12 (D2 approved; TD-1 adopt, TD-2 include, TD-3 all-46).
- **VERDICT:** CEO + ENG CLEARED — plan APPROVED, ready to implement.
