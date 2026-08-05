Reading additional input from stdin...
OpenAI Codex v0.144.5
--------
workdir: D:\Downloads\Trading_Bot
model: gpt-5.6-terra
provider: openai
approval: never
sandbox: read-only
reasoning effort: xhigh
reasoning summaries: none
session id: 019fb0cd-8ea3-79f1-a70f-1b0357c6c73a
--------
user
IMPORTANT: Do NOT read or execute any SKILL.md files or files in skill definition directories (paths containing skills/gstack). Stay focused on repository code only.

CEO review of REVISED plan (owner accepted 6-week stop/pivot + probe-first Track D). Be adversarial. Score the 6 CEO dimensions YES/NO/PARTIAL. File: _workspace/plans/2026-07-30-profitability-improvement-loop.md
deprecated: `[features].web_search_cached` is deprecated because web search is enabled by default.
Set `web_search` to `"live"`, `"indexed"`, `"cached"`, or `"disabled"` at the top level (or under a profile) in config.toml if you want to override it.
warning: Exceeded skills context budget of 2%. All skill descriptions were removed and 338 additional skills were not included in the model-visible skills list.
codex
I’ll review the revised plan strictly as a CEO gate: evidence, economics, ownership, timing, kill criteria, and decision readiness.
exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "Get-Content -Raw -LiteralPath '_workspace/plans/2026-07-30-profitability-improvement-loop.md'" in D:\Downloads\Trading_Bot
 succeeded in 879ms:
<!-- /autoplan restore point: /c/Users/SyedShirazShahid/.gstack/projects/sshirazshahid-Multi-platform-trading-bot/probe-bundle-mr-shadow-2026-07-19-autoplan-restore-20260730-065941.md -->
<!-- /autoplan revise cycle 2: owner accepted both User Challenges (A) 2026-07-30 -->
# Deploy Readiness + Edge Viability Loop (PAPER, evidence-gated)

**Branch:** `probe/bundle-mr-shadow-2026-07-19`  
**Date:** 2026-07-30 (rev 2)  
**Owner intent:** Restart â†’ monitor â†’ improve â†’ `/autoplan`. At final gate chose **Revise â†’ A**: accept both User Challenges (time-box stop/pivot **and** CONFIRMED_GO probe-first).  
**Honesty bound:** AccBand WR â‰  edge. No AccBand/TA reopen without frozen promotion. F1 only live family when `net_edge` clears. Premises **A** still stand.

## Problem (reframed)

Directional AccBand is correctly idle under `EconGate=strict` + EntryFloor 66 (measured âˆ’EV). F1 is validated but regime-idle. Ops hygiene alone will not produce profit. The binding problem is **deploy readiness for families that already cleared evidence gates**, plus a **time-boxed decision** on whether continued research has positive EV.

## Premises (owner-confirmed A â€” unchanged)

1. Refuse âˆ’EV opens is success, not failure.
2. F1 is the only live-path family until another clears frozen promotion + owner sign-off.
3. Abs-USDT mover band is research/telemetry only â€” does not authorize OPENs.
4. Screens stay dual-agreed + prereg-hashed.
5. Optimize = silence failures + honest evidence, not WR loosening.

## Viability clock (Challenge 1 â€” IN SCOPE)

| Field | Value |
|-------|--------|
| Window | **6 weeks** from 2026-07-30 UTC (ends **2026-09-10**) |
| Decision date | On or before end date: **GO / EXTEND(â‰¤2w once) / PIVOT / STOP** |
| GO | â‰¥1 family besides idle AccBand has either (a) F1 live entries with after-cost positive sample in window, or (b) unlock/listing probe arm hits frozen promotion dossier ready for owner sign-off |
| EXTEND | Only if RESOLVED accrual is clearly on-track (â‰¥50% of â‰¥30 floor with positive WR trajectory) |
| PIVOT | Switch capital/attention to a predeclared alt (external data buy, mothball directional, or cease research spend) |
| STOP | Research EV â‰¤ 0 â€” keep bot in preservation mode (strict gates) without new screens |

Success is **a decision**, not infinite artifact cadence.

## Tracks

### Track D â€” Deployable families first (Challenge 2 â€” PRIMARY)

Priority order:

1. **F1 carry** â€” ensure runner cadence observable (`carry_heartbeat`, gate-log freshness); MCP `f1_edge_status` distinguishes â€œno edgeâ€ vs â€œrunner staleâ€; alert on stale, not on expected negative edge.
2. **UnlockShortProbeAgent** â€” calendar coverage â‰¥30d forward; RESOLVED accrual toward frozen gate; weekly funnel read; dossier when â‰¥30 RESOLVED.
3. **ListingShortProbeAgent** â€” same RESOLVED â†’ dossier path (unlevered log-only; leverage still unsafe per audit).

**NOT** promoting without frozen gate + owner sign-off. This track accelerates **honest readiness**, not silent live wiring.

### Track A â€” Ops health (SECONDARY, still ship)

Keep T1â€“T6 from cycle-1 eng consensus (blast-radius honesty):

1. Tag `asset_class` + crypto-prefer shortlist (not hard-exclude-only)
2. Atomic `mover_shortlist_latest.json` + schema
3. Suppress/retarget `model_gate_starving` under EconGate=strict
4. MCP unit tests for movers + F1
5. Econ-gate / funnel counters (denominator-aware)
6. HL harvest: document cadence + nonzero exit on total failure
7. Heartbeat effective-config (EconGate + EntryFloor)

### Track B â€” Evidence queue (DEPRIORITIZED)

- **44_ abs-USDT mover screen:** telemetry only until Track A asset_class ships **and** density gate written; Stage-0 **after** probe RESOLVED velocity check this week â€” do not start a multi-week 44_ campaign before probe floors move.
- HL harvest: cadence ok as cheap adjacent data for F1/regime context â€” not a new family hunt.
- C2 gamma-expiry: remains INSUFFICIENT_DATA queue â€” no burn of the viability window.

## NOT in scope

- AccBand `paper_fallback` reopen
- CONTROLLED_LIVE
- New directional TA/MCP without pipeline GO
- Hard-exclude tradfi without tagging (taste: tag + crypto-prefer)
- Mission Control UI redesign
- Full F1 â€œjob platformâ€ rewrite (observe + alert only this window)
- Inventing large new strategies inside the 6-week clock

## What already exists

| Need | Existing |
|------|----------|
| Econ gate / EntryFloor | strict + 66 (verified post-restart) |
| Abs band + shortlist JSON | `universe_monitor` + `bot_engine` snapshot |
| Launcher pins | `_safe_worker_env` abs-band keys |
| F1 | `carry_runner`, gate log, carry heartbeat |
| Unlock / listing probes | `UnlockShortProbeAgent`, `ListingShortProbeAgent` |
| Funnel | `scripts/promotion_funnel.py` â†’ `data/promotion_funnel.json` |
| HL harvest | `scripts/harvest_hl_funding.py` |
| Prereg 44_ | frozen hash; screen not started |

## Success criteria

1. Bot up; heartbeat shows effective EconGate=strict + EntryFloor=66.
2. Mover shortlist: atomic, schemaâ€™d, `asset_class` tagged, crypto-prefer fills capacity.
3. Zero AccBand/MCP directional OPENs while model missing (expected idle â€” no false starvation spam).
4. Weekly: funnel snapshot for `unlock_short` + `listing_short` + F1; RESOLVED deltas logged in journal.
5. By **2026-09-10**: recorded GO / EXTEND / PIVOT / STOP decision in `_workspace/strategy_pipeline/`.
6. No new multi-week screen campaign that outranks Track D inside the window.

## Implementation alternatives

| Approach | Effort | Risk | Pros | Cons |
|----------|--------|------|------|------|
| A. Ops-only loop (cycle-1) | 0.5â€“2d | Low | Honest idle | No deploy path |
| **B. Deploy readiness + 6w clock (this rev)** | 1â€“3d CC + 6w accrual | Med | Forces decision; uses existing GOs | Few fills if F1 stays compressed |
| C. Loosen econ gate | Hours | High | Activity | âˆ’EV bleed |
| D. Big new strategy invent | Days+ | High | Narrative | Ledger NO without screen |

**Selected:** B.

## Dream state delta

```
CURRENT: strict idle + F1 idle + tradfi-noisy movers + probes accruing slowly
THIS PLAN: Track D primacy + 6w stop/pivot + Track A honesty fixes
12-MONTH: Either a signed promotion / F1 regime return, or an explicit STOP on research spend
```

## Risks

| Risk | Mitigation |
|------|------------|
| F1 never fires in window | Decision = PIVOT/STOP allowed; not a failure of honesty |
| Probe events too sparse for â‰¥30 RESOLVED | EXTEND once if on-track; else PIVOT |
| Ops polish crowds out Track D | Priority order enforced; 44_ demoted |
| Starvation misread | expected_idle under strict |
| Tradfi contaminates telemetry | asset_class + crypto-prefer |
| Clock ignored | Journal weekly + decision artifact due date |

## First executable tasks

### Week 0 (ship now â€” Track A + D observability)

1. [ ] T1 asset_class tag + crypto-prefer + tests
2. [ ] T2 atomic shortlist JSON + schema
3. [ ] T3 model_gate_starving expected-idle under strict
4. [ ] T4 MCP movers/F1 tests
5. [ ] T5 funnel/econ counters in heartbeat or MCP
6. [ ] T6 HL harvest docs + exit codes
7. [ ] Heartbeat effective-config fields
8. [ ] Unlock calendar `--forward-days 60` check; backfill if short
9. [ ] Baseline funnel snapshot â†’ `_workspace/strategy_pipeline/45_viability_baseline_2026-07-30.md`

### Weeks 1â€“6 (Track D cadence)

10. [ ] Weekly journal: F1 last-ok / net_edge; unlock+listing RESOLVED deltas
11. [ ] If arm â‰¥30 RESOLVED: run frozen promotion_gate + draft dossier (owner sign-off only)
12. [ ] No 44_ Stage-0 unless Track A done **and** written density proof **and** probe velocity reviewed that week

### Decision (â‰¤ 2026-09-10)

13. [ ] Write `45_viability_decision_YYYY-MM-DD.md` with GO/EXTEND/PIVOT/STOP + evidence

## Autoplan intake notes

- UI scope: **no**
- DX scope: **yes** (MCP, harvest CLI, funnel ops)
- Revise cycle: **2 / 3 max**
- Challenges accepted: time-box + probe-first
- Taste: tag + crypto-prefer (not hard-exclude)


exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "rg -n -i --glob '"'!**/skills/**'"' --glob '"'!**/gstack/**'"' \"CEO.{0,40}(dimension|review)|dimension.{0,40}(CEO)|CEO gate|GO / EXTEND\" _workspace . 2>"'$null' in D:\Downloads\Trading_Bot
 succeeded in 938ms:
_workspace\plans\2026-07-30-profitability-improvement-loop.md:27:| Decision date | On or before end date: **GO / EXTEND(≤2w once) / PIVOT / STOP** |
_workspace\plans\2026-07-30-profitability-improvement-loop.md:94:5. By **2026-09-10**: recorded GO / EXTEND / PIVOT / STOP decision in `_workspace/strategy_pipeline/`.
_workspace\tmp_timing\codex_ceo_autoplan.md:16:You are a CEO/founder advisor reviewing a development plan.
.\tasks\plan_provenance_bundle_2026-06-12.md:50:# /autoplan CEO REVIEW (Phase 1) — 2026-06-12
.\tasks\plan_provenance_bundle_2026-06-12.md:309:| CEO Review | `/plan-ceo-review` | Scope & strategy | 1 | issues_open (PLAN via /autoplan) | 7 proposals, 4 accepted, 6 deferred; 10 voice findings, 7 adopted |
.\_workspace\tmp_timing\codex_ceo_autoplan.md:16:You are a CEO/founder advisor reviewing a development plan.
.\_workspace\plans\2026-07-30-profitability-improvement-loop.md:27:| Decision date | On or before end date: **GO / EXTEND(≤2w once) / PIVOT / STOP** |
.\_workspace\plans\2026-07-30-profitability-improvement-loop.md:94:5. By **2026-09-10**: recorded GO / EXTEND / PIVOT / STOP decision in `_workspace/strategy_pipeline/`.
.\_workspace\tmp_timing\codex_ceo_c2.md:16:CEO review of REVISED plan (owner accepted 6-week stop/pivot + probe-first Track D). Be adversarial. Score the 6 CEO dimensions YES/NO/PARTIAL. File: _workspace/plans/2026-07-30-profitability-improvement-loop.md
.\_workspace\tmp_timing\codex_ceo_c2.md:21:I’ll review the revised plan strictly as a CEO gate: evidence, economics, ownership, timing, kill criteria, and decision readiness.
.\_workspace\tmp_timing\codex_ceo_c2.md:51:| Decision date | On or before end date: **GO / EXTEND(â‰¤2w once) / PIVOT / STOP** |
.\_workspace\tmp_timing\codex_ceo_c2.md:118:5. By **2026-09-10**: recorded GO / EXTEND / PIVOT / STOP decision in `_workspace/strategy_pipeline/`.
.\_workspace\tmp_timing\codex_ceo_c2.md:185:"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "rg -n -i --glob '"'!**/skills/**'"' --glob '"'!**/gstack/**'"' \"CEO.{0,40}(dimension|review)|dimension.{0,40}(CEO)|CEO gate|GO / EXTEND\" _workspace . 2>"'$null' in D:\Downloads\Trading_Bot
_workspace\tmp_timing\codex_ceo_c2.md:16:CEO review of REVISED plan (owner accepted 6-week stop/pivot + probe-first Track D). Be adversarial. Score the 6 CEO dimensions YES/NO/PARTIAL. File: _workspace/plans/2026-07-30-profitability-improvement-loop.md
_workspace\tmp_timing\codex_ceo_c2.md:21:I’ll review the revised plan strictly as a CEO gate: evidence, economics, ownership, timing, kill criteria, and decision readiness.
_workspace\tmp_timing\codex_ceo_c2.md:51:| Decision date | On or before end date: **GO / EXTEND(â‰¤2w once) / PIVOT / STOP** |
_workspace\tmp_timing\codex_ceo_c2.md:118:5. By **2026-09-10**: recorded GO / EXTEND / PIVOT / STOP decision in `_workspace/strategy_pipeline/`.
_workspace\tmp_timing\codex_ceo_c2.md:185:"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "rg -n -i --glob '"'!**/skills/**'"' --glob '"'!**/gstack/**'"' \"CEO.{0,40}(dimension|review)|dimension.{0,40}(CEO)|CEO gate|GO / EXTEND\" _workspace . 2>"'$null' in D:\Downloads\Trading_Bot
.\_workspace\tmp_timing\run_codex_ceo_c2.sh:11:CEO review of REVISED plan (owner accepted 6-week stop/pivot + probe-first Track D). Be adversarial. Score the 6 CEO dimensions YES/NO/PARTIAL. File: $PLAN" -C "$_REPO_ROOT" -s read-only --enable web_search_cached < /dev/null > "$OUT" 2>&1
.\_workspace\tmp_timing\run_codex_ceo.sh:12:You are a CEO/founder advisor reviewing a development plan.
_workspace\tmp_timing\run_codex_ceo.sh:12:You are a CEO/founder advisor reviewing a development plan.
_workspace\tmp_timing\run_codex_ceo_c2.sh:11:CEO review of REVISED plan (owner accepted 6-week stop/pivot + probe-first Track D). Be adversarial. Score the 6 CEO dimensions YES/NO/PARTIAL. File: $PLAN" -C "$_REPO_ROOT" -s read-only --enable web_search_cached < /dev/null > "$OUT" 2>&1

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "Get-Content -LiteralPath '_workspace/tmp_timing/codex_ceo_autoplan.md' -TotalCount 180; Get-Content -LiteralPath 'tasks/plan_provenance_bundle_2026-06-12.md' -TotalCount 110" in D:\Downloads\Trading_Bot
 succeeded in 797ms:
Reading additional input from stdin...
OpenAI Codex v0.144.5
--------
workdir: D:\Downloads\Trading_Bot
model: gpt-5.6-terra
provider: openai
approval: never
sandbox: read-only
reasoning effort: xhigh
reasoning summaries: none
session id: 019fb0c3-5fd2-7f40-b13d-666958b09d69
--------
user
IMPORTANT: Do NOT read or execute any SKILL.md files or files in skill definition directories (paths containing skills/gstack). These are AI assistant skill definitions meant for a different system. Stay focused on repository code only.

You are a CEO/founder advisor reviewing a development plan.
Challenge the strategic foundations: Are the premises valid or assumed? Is this the
right problem to solve, or is there a reframing that would be 10x more impactful?
What alternatives were dismissed too quickly? What competitive or market risks are
unaddressed? What scope decisions will look foolish in 6 months? Be adversarial.
No compliments. Just the strategic blind spots.

Also score YES/NO/PARTIAL with one line each:
1. Premises valid?
2. Right problem to solve?
3. Scope calibration correct?
4. Alternatives sufficiently explored?
5. Competitive/market risks covered?
6. 6-month trajectory sound?

File: _workspace/plans/2026-07-30-profitability-improvement-loop.md
deprecated: `[features].web_search_cached` is deprecated because web search is enabled by default.
Set `web_search` to `"live"`, `"indexed"`, `"cached"`, or `"disabled"` at the top level (or under a profile) in config.toml if you want to override it.
warning: Exceeded skills context budget of 2%. All skill descriptions were removed and 338 additional skills were not included in the model-visible skills list.
codex
Iâ€™ll assess the plan as an adversarial founder review, focusing on whether it creates a real durable advantage rather than merely a better internal process.
exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "Get-Content -Raw '_workspace/plans/2026-07-30-profitability-improvement-loop.md'" in D:\Downloads\Trading_Bot
 succeeded in 1461ms:
<!-- /autoplan restore point: /c/Users/SyedShirazShahid/.gstack/projects/sshirazshahid-Multi-platform-trading-bot/probe-bundle-mr-shadow-2026-07-19-autoplan-restore-20260730-065941.md -->
# Profitability Improvement Loop (PAPER, evidence-gated)

**Branch:** `probe/bundle-mr-shadow-2026-07-19`  
**Date:** 2026-07-30  
**Owner intent:** Restart bot Ã¢â€ â€™ monitor Ã¢â€ â€™ keep improving/optimizing Ã¢â€ â€™ `/autoplan` the next improvement track.  
**Honesty bound:** AccBand WR Ã¢â€°Â  edge. No live AccBand/TA reopen without frozen promotion. F1 is the only validated live family (currently idle on compressed funding).

## Problem

Directional MCP paper trading under AccBand geometry has measured after-cost negative expectancy. Owner asked for "profitable trades ONLY." Strict economic gate + EntryFloor 66 now refuse Ã¢Ë†â€™EV opens. That correctly idles directional flow; it does not invent profit. The bot needs a continuous improvement loop that:

1. Keeps the process healthy (restart, banners, heartbeat, shortlist).
2. Accrues only evidence-gated research (movers band, HL funding, C2/gamma queue, screens).
3. Never confuses activity with edge.

## Premises (require human confirm Ã¢â‚¬â€ Phase 1 gate)

1. **Refuse Ã¢Ë†â€™EV opens is success, not failure.** Zero AccBand opens under `EconGate=strict` is the intended state until a promoted model or a CONFIRMED_GO family exists.
2. **F1 carry remains the only live-path family** that may enter when `net_edge` clears; do not weaken F1 gates for activity.
3. **Abs-USDT mover band [$5,$200] is research/telemetry only** Ã¢â‚¬â€ shortlist + MCP tools; it does not authorize OPENs.
4. **Screens stay dual-agreed + prereg-hashed**; liq-cascade (41_) is CLOSED NO_GO; next work is queue-driven (44_ Stage-0 when dense, C2 accrual, HL harvest cadence).
5. **Optimize = reduce silent failures + accelerate honest evidence**, not raise WR by loosening costs.

## Current runtime (post-restart target)

| Knob | Target |
|------|--------|
| `OPERATING_MODE` | PAPER |
| `PAPER_TRADING_PROFILE` | MAX_FLOW_BAND |
| `MCP_ENTRY_MIN_SCORE` | 66 |
| `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE` | strict |
| Abs band | min=5 max=200 prefer=true (launcher-pinned) |
| Shadow probes | log-only fleet unchanged |

## In scope (this plan)

### Track A Ã¢â‚¬â€ Ops health (ship continuously)
- Clean supervisor restart; verify boot banner (Profile / EntryFloor / EconGate).
- Heartbeat freshness watchdog; alert if stale > N minutes after boot.
- Persist `data/mover_shortlist_latest.json` each shadow scan; MCP `trading_bot_recent_movers` / `trading_bot_f1_edge_status`.
- Log econ-gate block reasons with counts (no silent starve without reason).

### Track B Ã¢â‚¬â€ Evidence accrual (research)
- Cadence `scripts/harvest_hl_funding.py` Ã¢â€ â€™ `data/hl_funding_history.jsonl`.
- When 44_ Stage-0 data density met: run hashed screen under frozen prereg (no outcomes before hash).
- Keep C2 gamma-expiry / new-data edge program on queue (no TA reopen).

### Track C Ã¢â‚¬â€ Small DX/ops polish (blast-radius)
- Ensure `.env` UTF-8 + launcher pins cannot be silently overridden.
- Dashboard/MCP visibility of: econ-gate mode, entry floor, F1 last-ok count, mover band hits.

## NOT in scope

- Reopening AccBand via `paper_fallback` for trade activity.
- Promoting any shadow probe without frozen gate Ã¢â€°Â¥30 resolved + owner sign-off.
- Live CONTROLLED_LIVE.
- New directional TA/MCP strategies without pipeline GO.
- Mission Control UI redesign (unless a one-line ops fix).

## What already exists

| Need | Existing |
|------|----------|
| Econ gate | `core/economic_entry_gate.py`, `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE` |
| Abs band | `core/universe_monitor.py`, `BROAD_UNIVERSE_*`, bot_engine snapshot |
| Launcher pin | `scripts/launcher_supervisor._safe_worker_env` |
| F1 | `core/carry_runner.py`, gate log |
| HL harvest | `scripts/harvest_hl_funding.py` |
| Pipeline | strategy-evidence-pipeline + refuted ledger |
| Prereg 44_ | `_workspace/strategy_pipeline/44_prereg_abs_usdt_mover_band.md` |

## Success criteria

1. Bot stays up; boot banner matches `.env` targets every restart.
2. `mover_shortlist_latest.json` refreshes each shadow cycle with `abs_band_usdt=[5,200]`.
3. Zero AccBand/MCP directional OPENs while model missing + strict gate (unless F1 clears).
4. At least one evidence artifact advanced per UTC day when queue has work (harvest or screen Stage-0).
5. `/autoplan` review complete Ã¢â€ â€™ approved task list executed in priority order.

## Implementation alternatives

| Approach | Effort | Risk | Pros | Cons |
|----------|--------|------|------|------|
| A. Ops+evidence loop (this plan) | 0.5Ã¢â‚¬â€œ2d CC | Low | Honest; compounds data | Few/no fills |
| B. Loosen econ gate for activity | Hours | High | Looks "alive" | Reopens Ã¢Ë†â€™EV bleed |
| C. Big new strategy invent | Days | High | Narrative hope | Ledger says NO without screen |

**Selected:** A (P1 completeness of honest loop, P5 explicit).

## Dream state delta

```
CURRENT: strict idle directional + F1 idle + movers telemetry shipping
THIS PLAN: durable monitor loop + evidence cadence + ops silence fixes
12-MONTH: Ã¢â€°Â¥1 CONFIRMED_GO family beyond F1 OR F1 regime returns; promotion owner-signed
```

## Risks

| Risk | Mitigation |
|------|------------|
| Starvation misread as bug | Banner + econ-gate reason counters |
| Stale supervisor env | Launcher pin + kill full tree on restart |
| Outcome leak on screens | Hash prereg before outcomes; dual-model audit |
| Heartbeat lag after boot | Explicit freshness check in monitor |

## First executable tasks (pre-approval draft)

1. [x] Confirm post-restart: EconGate strict, EntryFloor 66, shortlist band [5,200] Ã¢â‚¬â€ verified 2026-07-30 ~06:56Ã¢â‚¬â€œ06:58Z
2. [ ] Filter tradfi/tokenized-equity noise from crypto mover shortlist (or tag `asset_class`) Ã¢â‚¬â€ live shortlist is SKHYNIX/XAU/META/SOXS-heavy
3. [ ] Heartbeat-stale check after boot if missing
4. [ ] Schedule / document HL funding harvest cadence
5. [ ] Econ-gate block counter in status/MCP (if not already)
6. [ ] Queue next screen only when 44_ density gate passes

## Autoplan intake notes

- UI scope: **no** (ops + research; no component/screen/layout work)
- DX scope: **yes** (MCP tools, harvest CLI, launcher pins)
- Design doc: none for this branch Ã¢â€ â€™ standard review (office-hours optional)
- Codex: binary present; dual voices enabled in config


codex
This is an operational-honesty plan, not a profitability-improvement plan. It may prevent losses, but it has no credible mechanism to discover, validate, and deploy a scalable edge within a defined time or capital budget.

- â€œOnly F1 is validatedâ€ is treated as a premise, yet â€œcurrently idle on compressed fundingâ€ means the entire thesis depends on a regime that may not return often enoughâ€”or at allâ€”to support the business objective.
- Paper-trading evidence is assumed to transfer to live execution. There is no explicit treatment of fills, queue position, slippage under stress, fees/funding changes, exchange outages, borrow/liquidation mechanics, or capacity. A paper `net_edge` can be economically meaningless live.
- The $5â€“$200 mover band is an arbitrary research constraint unless supported by a prior showing that it improves signal quality or executable opportunity density. It risks becoming a neat dashboard filter mistaken for a source of edge.
- â€œOne evidence artifact per dayâ€ incentivizes research throughput, not information gain. Harvest logs and preregistered screens can accumulate forever without improving the key decision: whether any tradable advantage exists.
- A â‰¥30-resolved promotion threshold is far too weak absent a defined effect size, confidence method, multiple-testing correction, out-of-sample/forward test, regime segmentation, and a minimum live-executable dollar edge.
- The strategy alternatives are a false trilemma: maintain ops, recklessly loosen gates, or invent a large strategy. Missing options include buying/brokering differentiated data, specializing in a single market microstructure, market making/relative value, execution alpha, cross-venue basis, or deciding the expected value of continued research is below zero.
- â€œFilter tokenized-equity/tradfi noiseâ€ may improve cosmetic relevance while hiding an important finding: the intended crypto universe may simply not offer enough opportunity. Tag first; only exclude with evidence that those instruments cannot contribute to the stated business goal.
- No strategy explains why this team should win versus firms with faster data, lower fees, inventory, exchange relationships, and mature execution systems. Funding carry is especially exposed to crowding and venue-rule changes.
- There is no capital target, return target, max drawdown, capacity target, research budget, or deadline for a go/no-go decision. â€œâ‰¥1 confirmed family in 12 monthsâ€ can rationalize a year of work with no economic output.
- `/autoplan` becoming a success criterion is circular. A planning loop is not a business outcome; it risks producing increasingly polished evidence governance around an uncompetitive premise.
- The likely 6-month embarrassment is investing in dashboards, watchdogs, counters, and artifact cadence while never testing whether a sufficiently large live, risk-adjusted opportunity set exists.

The 10Ã— reframe: make this a time-boxed **edge viability decision system**, not a perpetual â€œprofitability loop.â€ Within a fixed research budget and 4â€“8 weeks, require evidence that at least one hypothesis clears a predeclared live-executable return, capacity, and risk threshold after conservative costs. If none does, stop optimizing the botâ€™s process and pivot toward a genuine data/execution advantageâ€”or cease the strategy effort.

<!-- /autoplan restore point: ~/.gstack/projects/sshirazshahid-Multi-platform-trading-bot/main-autoplan-restore-20260612-080757.md -->
# Plan: Decision Provenance Bundle

**Source:** `reports/agent_architecture_audit_2026-06-12.md` (C1 + H1, H2, H5 + M1 + M2) Â· **Mode:** PAPER (`CLAUDE_PORTFOLIO_MODE=off` since 2026-06-11 restart) Â· **Deadline driver:** land before the mode knob leaves `off` so the next Claude-on window is fully attributable.

## Goal

Make every bot decision reconstructable end-to-end: what the LLM was asked, what it answered raw, what the parser/clamps/risk layers changed, and which order/warehouse row resulted. Today five mutation layers each work correctly in isolation but only post-mutation artifacts persist â€” the decision log, `mcp_decisions.jsonl`, and the warehouse systematically disagree (root of the Jun-4 "attribution corrupt" finding).

## Premises (gate these)

1. **Record fidelity is the right next investment** â€” the bot is learning-first (warehouse = substrate); no new signal/strategy work is pending that outranks it; all recent edge screens returned NO_EDGE, so the lever is data quality, not new alpha.
2. **Timing is conditional, not hard** *(reframed per TD-1, gate-approved 2026-06-12)* â€” the deadline applies only IF a Claude-on (`throttled`/`primary`) window is actually scheduled; per-decision source tagging cannot be reconstructed after a mixed-path window. Caveat documented: the current off-window is bundle-confounded (4 knobs activated by one restart per `scripts/experiments.json`), so micro-provenance cannot rescue macro attribution for THIS window; knob-staggering is an owner TODO.
3. **Additive-only** â€” instrumentation and validation must not change order placement, sizing, or exits. Nothing here may lower WR (standing WR-floor directive) or alter expectancy; clamps at parse time mirror what execution already enforces.
4. **Applies on restart** â€” changes take effect at the next bot restart, which the owner schedules (never bounce the bot unattended).

## In scope

1. **Provenance bundle (audit fix #1 â€” core)**
   - `utils/claude_client.py`: persist `raw_response` (full text) per call in `data/claude_audit/calls_*.jsonl`; prompt stored as SHA-256 hash + length, with full prompt sampled (e.g. 1-in-20 calls) to bound file growth; keep existing metadata fields.
   - `core/mcp_brain.py`: mint `decision_id` (uuid) at parse time; log per-action `source: claude|algo`, `repaired: bool`, `attempt` count, and pre-clamp `sl_raw/tp_raw/size_pct_raw/leverage_raw` alongside clamped values in `mcp_decisions.jsonl`.
   - Thread `decision_id` through `bot_engine._execute_open` â†’ `order_manager.open_position` â†’ warehouse row (new nullable column) and log order-validation rejections against the `decision_id`.
2. **Parse-time ingestion bounds (fix #2)** â€” clamp `leverage`/`size_pct` to config bounds and validate `symbol` against the candidate set at `core/mcp_brain.py:2234-2248`; log every clamp/reject. Mirrors execution-layer enforcement; no order-path change.
3. **Truncation/repair visibility (fix #4)** â€” log dropped section names when the prompt cap truncates (`mcp_brain.py:1663-1665`); set `repaired=true` on decisions parsed via `_repair_json`.
4. **Warm-restart advice drop (fix #6)** â€” on restart, discard reloaded `position_advice` from `mcp_state.json` (keep timing/budget fields) so stale advice is never consumed (`mcp_brain.py:1092-1104`).
5. **Doc-rot pass (fix #7)** â€” correct "advisory-only" headers in `core/claude_advisor.py`/`core/claude_schemas.py`; fix stale CLAUDE.md `total_pnl` gotcha (field is now `net_pnl`).

**Included (TD-2, gate-approved 2026-06-12):** atomic writes for `knowledge_model.json` + `trailing_peaks.json` (fix #3) in Tranche C, via shared `utils/atomic_io.py` helper (3rd use justifies extraction; pattern from `risk_manager.py:214-215`).

## NOT in scope

- Caution-exemption redesign (fix #5) â€” behavior change touching the primary strategy's gating; owner decision, deferred.
- Any change to entry/exit logic, sizing multipliers, SL/TP computation, or the five mutation layers themselves â€” this plan records them, it does not alter them.
- History purge / repo-privacy remediation (separate track, chip task_0e154803).

## Test plan (sketch â€” Eng review expands)

- TDD per repo convention: failing tests first for (a) raw_response present in audit entry, (b) decision_id round-trip decisionâ†’warehouse row, (c) clamp logging on out-of-bounds leverage/size/symbol, (d) repaired/truncated flags, (e) advice dropped on restart, (f) rejection logged with decision_id.
- Frozen-inventory guard (`tests/test_venue_write_sites_frozen.py`) must stay green â€” no new venue-write sites.
- Full suite green before restart recommendation.

## Risks

- `calls_*.jsonl` growth from raw responses (responses are compact JSON â‰¤ ~2KB; monthly files; acceptable â€” prompts are the large part, hence hash+sample).
- Warehouse schema change (additive nullable column) must not break existing readers (learning_engine, scorecard, dashboards).
- Decision-log consumers (`scripts/weekly_scorecard.py`, learning engine) must tolerate new fields (JSONL readers are key-tolerant; verify).

---

# /autoplan CEO REVIEW (Phase 1) â€” 2026-06-12

Mode: SELECTIVE EXPANSION (forced by /autoplan). Voices: **[subagent-only]** â€” codex rate-limited until 2026-07-10 (probe verified; degradation matrix applied).

## Step 0 record

- **0A Premises:** challenged and presented at gate D1; user confirmed "Premises hold." Post-gate, the independent voice challenged premise 2 (see Taste Decision TD-1).
- **0B Existing leverage:** extends `data/claude_audit/calls_*.jsonl` (exists), `mcp_decisions.jsonl` (exists), warehouse (additive column), `risk_manager.py:214` atomic pattern, existing leak-test conventions. Nothing is rebuilt.
- **0C Dream state:** CURRENT (5 mutation layers, post-hoc artifacts only) â†’ THIS PLAN (every decision carries id + raw + source, threaded to warehouse, with a reconciliation consumer) â†’ 12-MONTH IDEAL (full decision replay: given an id, reconstruct promptâ†’responseâ†’mutationsâ†’orderâ†’outcome; counterfactual analysis; ML-labeling join key). Moves toward; does not foreclose the event-sourced journal.
- **0C-bis Alternatives:** A) additive provenance bundle (completeness 9/10, M effort, low risk) â€” CHOSEN; B) raw_response+source only (5/10 â€” leaves C1's intentâ†”execution gap); C) event-sourced decision journal (10/10 but L/XL, violates additive-only premise; remains the 12-month direction). Auto-decided A (P1 vs B, P5/P3 vs C); not close â†’ mechanical.
- **0D Selective-expansion scan:** complexity check: ~8-10 files â€” at the smell threshold, justified (the thread follows one decision end-to-end; fewer files = broken thread). Expansions decided in Decision Audit Trail (E1-E5).
- **0E Temporal interrogation (resolved NOW):** one `decision_id` per ACTION; one `response_sha256` per response shared across its actions. Warehouse: `ALTER TABLE` additive nullable `decision_id` on trades AND candidates at startup. Rejections: logged to `mcp_decisions.jsonl` as `{"type":"rejection","decision_id":...}` rows (same file, no new store). Deterministic exits (SL/TP/trailing) are not LLM/algo decisions â†’ `decision_id NULL`, semantics documented.
- **0F Mode:** SELECTIVE EXPANSION confirmed; approach A applies.

## Dual voices

**CLAUDE SUBAGENT (CEO â€” strategic independence):** 10 findings, overall *sound-with-concerns*. Highlights: (S1) deadline premise confounded â€” `scripts/experiments.json` registers 4 simultaneous knob changes sharing one restart, so the off-window is bundle-attributable regardless of micro-provenance; (S2) no contingency for the modal outcome "Claude stays off" â€” split mode-agnostic core from Claude-specific capture; (S3) recorder without playback â€” no consumer joins the new fields to outcomes; (S4) prompt hash+1-in-20 sampling defeats "reconstructable end-to-end" (~55MB/mo full storage is trivial, dir verified gitignored); (S5) symbol-âˆˆ-candidates is a NEW order gate, not a mirror â€” `bot_engine.py:1851-1858` skips the whitelist in TRADING_MODE=all, so parse-rejection could change behavior inside measured windows; (S6) exit-side labels still ~25% NULL r_multiple in recent closed rows â€” entry provenance doesn't fix label corruption; (S7) only the id/source/raw core is deadline-coupled; (S8) advice-drop is a behavior change, not additive; (S9) provenance arrives too late to inform the keep/kill-Claude verdict being settled by the CURRENT window; (S10) storage-risk asymmetry (subsumed by S4).

**CODEX (CEO):** [codex-unavailable: usage limit until 2026-07-10] â€” single-model mode.

```
CEO DUAL VOICES â€” CONSENSUS TABLE
  Dimension                            Claude   Codex  Consensus
  1. Premises valid?                   PARTLY   N/A    FLAGGED (premise 2 conditional â€” TD-1)
  2. Right problem to solve?           YES*     N/A    FLAGGED* (right IF tied to a named decision â€” TD-1)
  3. Scope calibration correct?        SPLIT    N/A    FLAGGED (tranche split adopted)
  4. Alternatives sufficiently explored? YES    N/A    OK (0C-bis; B/C rejected with reasons)
  5. Competitive/market risks covered? N/A-dom  N/A    OK (internal tool; opportunity-cost = S9 â†’ TD-1)
  6. 6-month trajectory sound?         YES      N/A    OK (reversibility 5/5, platform join-key)
```

## Plan amendments adopted from voices (auto-decided; see Decision Audit Trail)

1. **Tranche split (S2/S7):** Tranche A "pre-knob core" = decision_id + source + pre-clamp capture + rejection linking (valuable for the ALGO path running NOW). Tranche B "Claude-capture" = raw_response + full prompt storage + repaired/truncated flags. Tranche C "hygiene, no deadline" = doc-rot, advice-drop, atomic writes (if accepted at gate). All can ship in one PR but are independently revertable.
2. **Full prompt storage; sampling machinery deleted from scope (S4/S10):** store full prompt + full raw_response per call (gzip optional), local gitignored dir; add a test pinning `data/claude_audit/` as untracked (ties to today's repo-privacy FAIL).
3. **Symbol validation is LOG-ONLY (S5):** out-of-candidate-set symbols are recorded (`symbol_unlisted: true`), never rejected at parse. Leverage/size clamps stay (true mirrors of execution).
4. **Consumer in scope (S3):** definition-of-done adds `scripts/decision_reconciliation.py` â€” joins decision_id across mcp_decisions â†’ warehouse, emits per-source WR/expectancy and orphan diffs (decisions-without-orders / orders-without-decisions) â€” plus a weekly-scorecard provenance-health line (audit-write failures, repaired/truncated counts, orphans).
5. **Audit-write failures become visible (new finding A1):** `_audit_log` currently swallows all exceptions (`utils/claude_client.py:56-57`) â€” silent provenance loss. Add failure counter surfaced in the scorecard line.
6. **Advice-drop reclassified (S8):** explicit behavior change with its own test, listed under Tranche C, not "additive."
7. **Label-quality acceptance check (S6):** post-restart verification that NULL `r_multiple` rate on newly closed trades is <2%; if violated, label repair becomes the next P1 (TODO logged).

## Section findings (1-11)

1. **Architecture â€” 1 finding (A1, adopted above).** New flow diagrammed below; no new components, no new coupling beyond a threaded id param; SPOF none; 10x/100x load trivial (O(1) appends). Rollback: git revert; extra JSONL keys harmless to key-tolerant readers; nullable column ignored by old code.
2. **Error & rescue â€” mapped, 1 GAP (A1).** Registry below.
3. **Security â€” no new attack surface; net hardening.** Parse-time clamps add defense-in-depth against prompt-injection-shaped LLM output. Raw prompts contain positions/balances â†’ stays local: dir gitignored (verified) + new test pins it. No secrets in new fields.
4. **Data flow & edge cases â€” shadow paths resolved in 0E** (nil â†’ algo actions also get ids; empty actions â†’ response-level record still written; upstream error â†’ no id, NULL semantics documented; crash between order and warehouse write â†’ reconciliation flags orphan, visible not silent).
5. **Code quality â€” 1 decision:** if atomic-writes E1 is accepted, extract a shared `utils/atomic_io.py` helper instead of a third inline copy (3 uses justifies the abstraction; P4).
6. **Tests â€” diagram + specs expanded** (Eng phase owns the full test plan artifact): adds audit-failure counter, symbol log-only, advice-drop behavior, startup migration, gitignore pin, reconciliation orphans, full-prompt round-trip. No prompt content changes â†’ no eval suites triggered.
7. **Performance â€” no issues.** ~13MB/mo responses + ~55MB/mo prompts, local disk; appends O(1); no new queries on hot paths; decision_id unindexed (analytic-only access) â€” noted, not needed now.
8. **Observability â€” this plan IS the observability fix;** plus scorecard provenance-health line (amendment 4/5).
9. **Deployment â€” applies on owner-scheduled restart;** startup additive migration; no flag needed (log-only paths); risk window none (single process). Rollback: one revert.
10. **Trajectory â€” reversibility 5/5;** removes investigative debt; decision_id is the future ML-labeling join key; Phase-2 (event journal) unforeclosed.
11. **Design/UX â€” SKIPPED (no UI scope detected in Phase 0).**

## Diagrams

```
PROVENANCE FLOW (new fields in [brackets])
 claude_client.call_claude_cli â”€â”€â–¶ calls_YYYY-MM.jsonl [prompt_full, raw_response, write_fail_ctr]
        â”‚ raw text

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "rg --files _workspace/strategy_pipeline | Select-Object -First 80; rg -n -i --glob '*.md' \"unlock_short|listing_short|RESOLVED|promotion_gate|net_edge|F1\" _workspace/strategy_pipeline _workspace/plans 2>"'$null' in D:\Downloads\Trading_Bot
 succeeded in 999ms:
_workspace/strategy_pipeline\08c_screen_basis_swap.md
_workspace/strategy_pipeline\13_band_conditional_rows.smoke.csv
_workspace/strategy_pipeline\13_band_conditional_rows.csv
_workspace/strategy_pipeline\12_deep_breakout_lane.md
_workspace/strategy_pipeline\08c_screen_basis_swap.json
_workspace/strategy_pipeline\11_integration_report_tsmom.md
_workspace/strategy_pipeline\10_integration_report_candidate2.md
_workspace/strategy_pipeline\08b_screen_preunlock_short.md
_workspace/strategy_pipeline\09_audit_candidate2_final.md
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md
_workspace/strategy_pipeline\08b_screen_preunlock_short.json
_workspace/strategy_pipeline\08a_screen_funding_timing.md
_workspace/strategy_pipeline\13_band_conditional_screen.smoke.json
_workspace/strategy_pipeline\13_band_conditional_screen.md
_workspace/strategy_pipeline\08a_screen_funding_timing.json
_workspace/strategy_pipeline\13_band_conditional_screen.json
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md
_workspace/strategy_pipeline\14_context_2026-07-16.md
_workspace/strategy_pipeline\06b_rsi_scan_120d_audit.md
_workspace/strategy_pipeline\13_band_outcome_cache.json
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md
_workspace/strategy_pipeline\05_accuracy_band_sim.md
_workspace/strategy_pipeline\05b_accuracy_band_audit.md
_workspace/strategy_pipeline\04_integration_report.md
_workspace/strategy_pipeline\03_rev3_audit_findings.md
_workspace/strategy_pipeline\03_rev2_audit_findings.md
_workspace/strategy_pipeline\03_audit_listing-short.md
_workspace/strategy_pipeline\03_audit_dispersion.md
_workspace/strategy_pipeline\02b_screener_listing_short.md
_workspace/strategy_pipeline\02b_rev3_screener_listing_short.md
_workspace/strategy_pipeline\14_scout_b_spot_2026-07-16.md
_workspace/strategy_pipeline\16_debate_15a_delisting.md
_workspace/strategy_pipeline\15d_screen_wrapper.md
_workspace/strategy_pipeline\15d_screen_wrapper.json
_workspace/strategy_pipeline\16_debate_15c_depeg.md
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md
_workspace/strategy_pipeline\15c_screen_depeg.md
_workspace/strategy_pipeline\16_debate_15d_wrapper.md
_workspace/strategy_pipeline\15c_screen_depeg.json
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md
_workspace/strategy_pipeline\15b_screen_f1_selectivity.json
_workspace/strategy_pipeline\15a_screen_delisting.md
_workspace/strategy_pipeline\15a_screen_delisting.json
_workspace/strategy_pipeline\14_scout_c_reopen_sweep_2026-07-16.md
_workspace/strategy_pipeline\02a_rev3_screener_dispersion.md
_workspace/strategy_pipeline\02a_rev3_screener_dispersion.json
_workspace/strategy_pipeline\02a_rev2_screener_dispersion.md
_workspace/strategy_pipeline\02a_screener_dispersion.md
_workspace/strategy_pipeline\02b_rev2_screener_listing_short.md
_workspace/strategy_pipeline\01_scout_candidates.md
_workspace/strategy_pipeline\00_context.md
_workspace/strategy_pipeline\02b_rev3_screener_listing_short.json
_workspace/strategy_pipeline\17_integration_report_2026-07-16.md
_workspace/strategy_pipeline\18_context_2026-07-22.md
_workspace/strategy_pipeline\22_screen_c3_quarter_hour_imbalance.md
_workspace/strategy_pipeline\22_screen_c3_quarter_hour_imbalance.json
_workspace/strategy_pipeline\22_prereg_c3_quarter_hour_imbalance.md
_workspace/strategy_pipeline\22_prereg_c3_quarter_hour_imbalance.json
_workspace/strategy_pipeline\22_integration_report_c3.md
_workspace/strategy_pipeline\22_audit_c3_quarter_hour_imbalance.md
_workspace/strategy_pipeline\21_refused_installs.md
_workspace/strategy_pipeline\21_loss_autopsy.md
_workspace/strategy_pipeline\21_loss_autopsy.json
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md
_workspace/strategy_pipeline\21_chaos_wr_band_experiment.md
_workspace/strategy_pipeline\20_screen_c1_cftc_options_pressure.md
_workspace/strategy_pipeline\18_verdict_diff.json
_workspace/strategy_pipeline\20_screen_c1_cftc_options_pressure.json
_workspace/strategy_pipeline\18_verdicts_fable.md
_workspace/strategy_pipeline\20_prereg_c1_cftc_options_pressure.md
_workspace/strategy_pipeline\18_verdicts_codex.stderr.txt
_workspace/strategy_pipeline\20_prereg_c1_cftc_options_pressure.json
_workspace/strategy_pipeline\18_verdicts_codex.md
_workspace/strategy_pipeline\20_integration_report_c1.md
_workspace/strategy_pipeline\18_scout_fable.md
_workspace/strategy_pipeline\20_audit_c1_cftc_options_pressure.md
_workspace/strategy_pipeline\18_scout_codex.stderr.txt
_workspace/strategy_pipeline\18_scout_codex.md
_workspace/strategy_pipeline\18_rebuttal_codex.md
_workspace/strategy_pipeline\18_pair_dossier.md
_workspace/plans\2026-07-30-profitability-improvement-loop.md:8:**Honesty bound:** AccBand WR ≠ edge. No AccBand/TA reopen without frozen promotion. F1 only live family when `net_edge` clears. Premises **A** still stand.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:12:Directional AccBand is correctly idle under `EconGate=strict` + EntryFloor 66 (measured −EV). F1 is validated but regime-idle. Ops hygiene alone will not produce profit. The binding problem is **deploy readiness for families that already cleared evidence gates**, plus a **time-boxed decision** on whether continued research has positive EV.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:17:2. F1 is the only live-path family until another clears frozen promotion + owner sign-off.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:28:| GO | ≥1 family besides idle AccBand has either (a) F1 live entries with after-cost positive sample in window, or (b) unlock/listing probe arm hits frozen promotion dossier ready for owner sign-off |
_workspace/plans\2026-07-30-profitability-improvement-loop.md:29:| EXTEND | Only if RESOLVED accrual is clearly on-track (≥50% of ≥30 floor with positive WR trajectory) |
_workspace/plans\2026-07-30-profitability-improvement-loop.md:41:1. **F1 carry** — ensure runner cadence observable (`carry_heartbeat`, gate-log freshness); MCP `f1_edge_status` distinguishes “no edge” vs “runner stale”; alert on stale, not on expected negative edge.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:42:2. **UnlockShortProbeAgent** — calendar coverage ≥30d forward; RESOLVED accrual toward frozen gate; weekly funnel read; dossier when ≥30 RESOLVED.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:43:3. **ListingShortProbeAgent** — same RESOLVED → dossier path (unlevered log-only; leverage still unsafe per audit).
_workspace/plans\2026-07-30-profitability-improvement-loop.md:54:4. MCP unit tests for movers + F1
_workspace/plans\2026-07-30-profitability-improvement-loop.md:61:- **44_ abs-USDT mover screen:** telemetry only until Track A asset_class ships **and** density gate written; Stage-0 **after** probe RESOLVED velocity check this week — do not start a multi-week 44_ campaign before probe floors move.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:62:- HL harvest: cadence ok as cheap adjacent data for F1/regime context — not a new family hunt.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:72:- Full F1 “job platform” rewrite (observe + alert only this window)
_workspace/plans\2026-07-30-profitability-improvement-loop.md:82:| F1 | `carry_runner`, gate log, carry heartbeat |
_workspace/plans\2026-07-30-profitability-improvement-loop.md:93:4. Weekly: funnel snapshot for `unlock_short` + `listing_short` + F1; RESOLVED deltas logged in journal.
_workspace/plans\2026-07-30-profitability-improvement-loop.md:102:| **B. Deploy readiness + 6w clock (this rev)** | 1–3d CC + 6w accrual | Med | Forces decision; uses existing GOs | Few fills if F1 stays compressed |
_workspace/plans\2026-07-30-profitability-improvement-loop.md:111:CURRENT: strict idle + F1 idle + tradfi-noisy movers + probes accruing slowly
_workspace/plans\2026-07-30-profitability-improvement-loop.md:113:12-MONTH: Either a signed promotion / F1 regime return, or an explicit STOP on research spend
_workspace/plans\2026-07-30-profitability-improvement-loop.md:120:| F1 never fires in window | Decision = PIVOT/STOP allowed; not a failure of honesty |
_workspace/plans\2026-07-30-profitability-improvement-loop.md:121:| Probe events too sparse for ≥30 RESOLVED | EXTEND once if on-track; else PIVOT |
_workspace/plans\2026-07-30-profitability-improvement-loop.md:134:4. [ ] T4 MCP movers/F1 tests
_workspace/plans\2026-07-30-profitability-improvement-loop.md:143:10. [ ] Weekly journal: F1 last-ok / net_edge; unlock+listing RESOLVED deltas
_workspace/plans\2026-07-30-profitability-improvement-loop.md:144:11. [ ] If arm ≥30 RESOLVED: run frozen promotion_gate + draft dossier (owner sign-off only)
_workspace/strategy_pipeline\02a_rev2_screener_dispersion.md:19:  (ccxt 4.5.64), F1 15-coin universe × 3 venues, paginated as far back as each venue serves
_workspace/strategy_pipeline\02a_rev2_screener_dispersion.md:30:Per venue-symbol: **rows | first_ts (UTC) | last_ts (UTC)**. Full table across all 15 F1 coins.
_workspace/strategy_pipeline\01_scout_candidates.md:4:- Novelty-vs-ledger: ADJACENT-to-carry (screen-eligible; extends the validated F1 family)
_workspace/strategy_pipeline\02b_rev2_screener_listing_short.md:3:Agent: edge-screener · Date: 2026-07-09 · Supersedes: `02b_screener_listing_short.md` (INSUFFICIENT_DATA)
_workspace/strategy_pipeline\02b_rev2_screener_listing_short.md:22:- `research/screen_listing_short.py` now **reads `data/funding_history/binance_{BASE}.csv`**,
_workspace/strategy_pipeline\02b_rev2_screener_listing_short.md:25:  harvested store (audit MEDIUM finding resolved).
_workspace/strategy_pipeline\02b_rev2_screener_listing_short.md:137:    "screen": "research/screen_listing_short.py",
_workspace/strategy_pipeline\02b_rev2_screener_listing_short.md:138:    "tests": "tests/test_screen_listing_short.py (16 pass)",
_workspace/strategy_pipeline\02b_rev2_screener_listing_short.md:139:    "verdict": "_workspace/strategy_pipeline/02b_rev2_screener_listing_short.md"
_workspace/strategy_pipeline\02a_screener_dispersion.md:31:- Honest DEFAULT = taker/taker both legs (repo baseline, `F1_EXECUTION_MODE=taker`).
_workspace/strategy_pipeline\02a_screener_dispersion.md:38:**Frozen gate thresholds (from `core/promotion_gate.py` — never loosened).**
_workspace/strategy_pipeline\02b_screener_listing_short.md:50:**Frozen gates (never loosened — `core/promotion_gate.py`):** DSR ≥ 0.10, PBO ≤ 0.5, OOS-WR ≥ 0.55;
_workspace/strategy_pipeline\02b_screener_listing_short.md:72:Artifacts: screen `research/screen_listing_short.py` · tests `tests/test_screen_listing_short.py`
_workspace/strategy_pipeline\02b_screener_listing_short.md:160:    "screen": "research/screen_listing_short.py",
_workspace/strategy_pipeline\02b_screener_listing_short.md:161:    "tests": "tests/test_screen_listing_short.py (9/9 pass)",
_workspace/strategy_pipeline\02b_screener_listing_short.md:162:    "verdict": "_workspace/strategy_pipeline/02b_screener_listing_short.md"
_workspace/strategy_pipeline\02b_rev3_screener_listing_short.md:177:See `02b_rev3_screener_listing_short.json`.
_workspace/strategy_pipeline\03_audit_listing-short.md:3:Auditor: honesty-auditor · Date: 2026-07-09 · Screen audited: `_workspace/strategy_pipeline/02b_screener_listing_short.md`
_workspace/strategy_pipeline\03_audit_listing-short.md:4:Artifacts: `research/screen_listing_short.py`, `tests/test_screen_listing_short.py`
_workspace/strategy_pipeline\03_audit_listing-short.md:14:- **Reproduction (exact):** ran `research/screen_listing_short.py` — 525 `_1h` symbols, 8 backfill-cluster
_workspace/strategy_pipeline\03_audit_dispersion.md:11:structural (survives even a zero-fee stress test). Zero unresolved findings that
_workspace/strategy_pipeline\03_audit_dispersion.md:42:   `core/promotion_gate.py`. The 60-floor is therefore *looser* than the actual
_workspace/strategy_pipeline\03_audit_dispersion.md:57:  `core/promotion_gate.py` verbatim.
_workspace/strategy_pipeline\03_audit_dispersion.md:60:- None blocking. The four screener self-challenges are all resolved in its favor.
_workspace/strategy_pipeline\03_rev3_audit_findings.md:4:Inputs audited: `02b_rev3_screener_listing_short.md`/`.json` (**GO @7d,30d** — pipeline's FIRST GO),
_workspace/strategy_pipeline\03_rev3_audit_findings.md:6:Code: `research/screen_listing_short.py` (`run_screen_rev3`,`_decide_rev3`,`apply_concurrency_cap`,
_workspace/strategy_pipeline\03_rev3_audit_findings.md:9:promotion_gate}.py`, `core/decision/monte_carlo.py`. Prior: `03_rev2_audit_findings.md`.
_workspace/strategy_pipeline\03_rev3_audit_findings.md:102:must be a live probe condition, not a resolved concern.
_workspace/strategy_pipeline\03_rev3_audit_findings.md:146:(`core/promotion_gate.py`: MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55, AUC≥0.60):**
_workspace/strategy_pipeline\03_rev3_audit_findings.md:162:6. **Accumulate ≥30 resolved shadow trades** before running MC/DSR, and carry the family's honest
_workspace/strategy_pipeline\03_rev3_audit_findings.md:197:### Disposition A — **CONFIRMED_NO_GO.** Zero unresolved findings. Unlike rev2 (where the viable
_workspace/strategy_pipeline\05_accuracy_band_sim.md:3:_Generated 2026-07-10 02:27 UTC · research/sim_accuracy_band.py · after-cost, resolved-only._
_workspace/strategy_pipeline\05_accuracy_band_sim.md:11:- **Frozen measure:** win rate = share of resolved trades with `net_pnl > 0`; **censored rows excluded** (never counted as wins); Wilson 95% CI per frac.
_workspace/strategy_pipeline\05_accuracy_band_sim.md:16:| frac | n resolved | censored | floor-bound | WR | Wilson 95% CI | exp (R) | exp ($) | avg win (R) | avg loss (R) |
_workspace/strategy_pipeline\04_integration_report.md:5:`02b_rev3_screener_listing_short.md`/`.json` (frozen pre-registration, GO @7d,30d).
_workspace/strategy_pipeline\04_integration_report.md:12:A `ListingShortProbeAgent` (`core/agents/listing_short_probe_agent.py`) that forward-soaks the
_workspace/strategy_pipeline\04_integration_report.md:40:resolved to a `time` exit with a real after-cost `net_pnl`; the 30d row correctly stays PENDING
_workspace/strategy_pipeline\04_integration_report.md:47:| Probe agent | new class + pure math + 3 companion tables | `core/agents/listing_short_probe_agent.py` (new) |
_workspace/strategy_pipeline\04_integration_report.md:50:| Config | `LISTING_SHORT_PROBE` (shadow-lane only, env `SHADOW_LISTING_PROBE_ENABLED`) | `config.py` |
_workspace/strategy_pipeline\04_integration_report.md:52:| Tests | probe lifecycle + math + structural log-only + runner hook + engine wiring | `tests/test_listing_short_probe.py` (new), `tests/test_botengine_shadow_wire.py` |
_workspace/strategy_pipeline\04_integration_report.md:61:Frozen in code **before any outcome exists** (`listing_short_score`). The AUC≥0.60 gate is
_workspace/strategy_pipeline\04_integration_report.md:76:per listing); the AUC gate later tests whether it actually ranks resolved winners above losers.
_workspace/strategy_pipeline\04_integration_report.md:77:Logged per proposal in `shadow_listing_probe.score`; the binary label is derived from the resolved
_workspace/strategy_pipeline\04_integration_report.md:98:The gate stays **frozen** (`core/promotion_gate.py`: MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55,
_workspace/strategy_pipeline\04_integration_report.md:99:AUC≥0.60) and is applied **only** by the owner, only after ≥30 resolved trades (B6). Every input is
_workspace/strategy_pipeline\04_integration_report.md:104:| **Per-trade after-cost return** | `shadow_outcomes.net_pnl` (fees + slippage, SL-first) **plus realized funding** `shadow_listing_probe.realized_funding_rate_sum × notional_usd` (short receives +). JOIN on `proposal_id`, filter `model_version='listing_short_probe_v1'`. |
_workspace/strategy_pipeline\04_integration_report.md:105:| **Win rate ≥ 0.55** | fraction of resolved trades with (net_pnl + funding$) > 0 |
_workspace/strategy_pipeline\04_integration_report.md:108:| **PBO ≤ 0.50** | CSCV across the 7d/30d horizons (needs both resolved) |
_workspace/strategy_pipeline\04_integration_report.md:116:  join the resolved shadow-vs-live comparison automatically. This is aggregate; use the queries
_workspace/strategy_pipeline\04_integration_report.md:118:- **Listing family, resolved-only** (via `trading_bot_query`):
_workspace/strategy_pipeline\04_integration_report.md:125:  WHERE p.decision='ENTER' AND o.label_status='RESOLVED' ORDER BY p.entry_ts DESC;
_workspace/strategy_pipeline\04_integration_report.md:136:- **AUC readiness:** count resolved ENTER rows; do **not** compute AUC/DSR/MC until ≥30 resolved
_workspace/strategy_pipeline\04_integration_report.md:140:**Promotion remains a manual owner decision** on `core/promotion_gate.py` evidence. Nothing here
_workspace/strategy_pipeline\04_integration_report.md:164:- `venv/Scripts/python.exe -m pytest tests/test_listing_short_probe.py -q` → 16 passed.
_workspace/strategy_pipeline\03_rev2_audit_findings.md:4:Inputs audited: `02a_rev2_screener_dispersion.md` (NO_GO), `02b_rev2_screener_listing_short.md` (NO_GO)
_workspace/strategy_pipeline\03_rev2_audit_findings.md:5:Code: `research/screen_funding_dispersion.py`, `research/screen_listing_short.py`,
_workspace/strategy_pipeline\03_rev2_audit_findings.md:70:window"; rev1 only had BTC/ETH on disk. The backfill widened coverage to the F1 15-coin set — a
_workspace/strategy_pipeline\03_rev2_audit_findings.md:132:**Disposition B:** **CONFIRMED_NO_GO.** Zero unresolved findings that could flip it. Ledger row
_workspace/strategy_pipeline\06b_rsi_scan_120d_audit.md:29:- ¹ **SOON resolved:** my first pass (full-parquet) gave 21.3, but SOON has a data gap; under the scan's actual `WARMUP_BUFFER_BARS=420` slice only **28 real bars precede the event**, so `bot_rsi` on that slice = **26.51 ≈ reported 26.5** (reproduced exactly). The report is internally consistent. Note the side-effect below.
_workspace/strategy_pipeline\06b_rsi_scan_120d_audit.md:53:- **Censoring handled correctly:** all 10 had a full 72-bar forward window (`nfwd=72`). Where the horizon is not full the resolver returns `None` → counted `pending`, **excluded from `resolved`**, never scored as a win. Verified structurally: `resolved + pending = total` for both geometries (see Check 4).
_workspace/strategy_pipeline\06b_rsi_scan_120d_audit.md:61:| engine W+L = 130390 = resolved; 47923/130390 = **36.75% → 36.8%** | ✓ |
_workspace/strategy_pipeline\06b_rsi_scan_120d_audit.md:62:| accuracy W+L = 130784 = resolved; 92650/130784 = **70.84% → 70.8%** | ✓ |
_workspace/strategy_pipeline\06b_rsi_scan_120d_audit.md:63:| engine resolved(130390)+pending(506)=130896=Part B; pending excluded from WR | ✓ |
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:5:**Ledger compliance:** `refuted-families-ledger` read first. Nothing below re-proposes a refuted family (RSI-MR, breakouts, candlesticks, confluence, Kalman pairs, directional funding, formulaic alphas, ML forecasters, seasonality, dominance/ETF-flow, $1–2 scalping, grid/DCA, OI-divergence, long-only TSMOM) or the two 07-09/07-10-resolved screens (binance∩bybit dispersion hold-until-flip = CONFIRMED_NO_GO; full-stake listing-short = CONFIRMED_NO_GO; capital-scaled listing-short = CONFIRMED_GO, already live as ListingShortProbeAgent).
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:16:## Candidate 1 — Funding-settlement-window timing (F1 execution refinement)
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:18:**What it is.** Measure whether perp prices exhibit a small, repeatable drift/convergence into the 00/08/16 UTC funding settlements (and Bybit/Bitget equivalents), and whether F1 carry entries/exits timed against settlement timestamps capture measurably more net carry than time-agnostic execution. Two testable sub-claims: (a) pre-settlement price drift in the direction that offsets/augments the funding payment; (b) entry just before settlement collects a full accrual the position barely "worked" for.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:20:**Mechanism (why, not shape).** Funding is a discrete cash transfer at a known timestamp. Rational holders who don't want to pay close before settlement; collectors open before it. That scheduled, mechanical flow — not price prediction — is the constraint someone pays to escape. It is the same risk-transfer mechanism F1 already harvests, refined in time.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:26:**Costs / feasibility @$420.** Best of the batch: it is a timing change on the already-validated, already-capitalized F1 lane — no new positions, no new fee legs, no new margin. If drift exists it either adds carry or reduces cost; if not, F1 is unchanged.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:44:**Novelty-vs-ledger: NEW.** Not on the ledger. Distinct from the resolved listing-short rows (different event, different window). The 07-08 brief's "post-unlock short" was never screened; this candidate replaces it with a better-evidenced, differently-timed variant.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:48:## Candidate 3 — Term-locked carry: quarterly-futures basis leg-swap for F1
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:50:**What it is.** When the annualized basis on Binance dated futures (e.g., BTCUSDT/ETHUSDT quarterlies) exceeds the trailing/expected funding APR of the equivalent F1 perp position, express the carry short leg via the quarterly instead of the perp: long spot + short dated future, hold to expiry. The basis is locked at entry — no funding-flip risk for the term.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:52:**Mechanism.** Same risk transfer as F1 (leverage demand pays for hedged inventory), but the term structure lets the carrier choose between a floating rate (perp funding) and a fixed rate (dated basis). Selling the fixed leg when it is rich vs realized funding is a mechanical spread choice, not a directional view.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:54:**External evidence.** [BIS Working Paper 1087, "Crypto carry"](https://www.bis.org/publ/work1087.pdf) — peer-reviewed-grade documentation that crypto futures carry is large, time-varying, and harvestable, with crash risk concentrated in deleveraging episodes (note: 2023 — predates the reopen-bar's 12-month preference; used here as family support, not as the screen's basis, since the family is already validated locally by F1). [Glassnode's annualized 3m-rolling-basis vs perp-funding series](https://studio.glassnode.com/charts/futures-annualized-yield?a=BTC) documents that dated basis is structurally less volatile than perp funding — the fixed-vs-floating spread this candidate trades. The ScienceDirect 2025 funding-arb study (already cited for F1) covers the floating side.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:58:**Costs / feasibility @$420 — the honest weak point.** Binance-only (Bybit/Bitget dated liquidity thin/absent). Min notional ~100 USDT per leg → one BTC or ETH position = ~200 USDT across both legs ≈ 48% of the account, on top of F1's existing capital constraint. Sizing infeasibility has killed carry variants before; this candidate survives only if the screen shows the fixed-leg premium is large enough to justify displacing F1 capital, and the edge-screener must check leg minimums FIRST.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:60:**Novelty-vs-ledger: ADJACENT** — an execution/instrument variant of validated F1. Not any refuted family (it is not "directional funding signals" — no direction is taken).
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:101:9. ScienceDirect 2025 funding-arb study (F1 family anchor, already in ledger) — https://www.sciencedirect.com/science/article/pii/S2096720925000818
_workspace/strategy_pipeline\08b_screen_preunlock_short.md:52:- NaN fails closed. Minimum n: ≥ 30 qualifying resolved events, else INSUFFICIENT_DATA.
_workspace/strategy_pipeline\08b_screen_preunlock_short.md:172:real prints only). Screen: `research/screen_preunlock_short.py`. Accepted-event dump for
_workspace/strategy_pipeline\08c_screen_basis_swap.md:1:# 08c — Screen: Quarterly-Futures Basis Leg-Swap for F1
_workspace/strategy_pipeline\08c_screen_basis_swap.md:16:trailing/expected funding APR of the equivalent F1 perp position, expressing the carry short
_workspace/strategy_pipeline\08c_screen_basis_swap.md:29:  futures exist for the F1 coins, and what are the exchange minimums per leg
_workspace/strategy_pipeline\08c_screen_basis_swap.md:58:Only BTC and ETH have USDT-margined quarterlies (4 contracts total). No other F1 coin has one.
_workspace/strategy_pipeline\08c_screen_basis_swap.md:142:  (F1's frozen 50 bps round-trip model; ONE round trip for the whole window — the
_workspace/strategy_pipeline\08c_screen_basis_swap.md:187:Sample: 64 monthly decision points, ALL resolved (0 exclusions). Conditional sample
_workspace/strategy_pipeline\08a_screen_funding_timing.md:1:# 08a — Screen: Funding-Settlement-Window Timing (F1 refinement)
_workspace/strategy_pipeline\08a_screen_funding_timing.md:11:Conditional on F1's entry gate otherwise passing, shifting the delta-neutral carry entry to a
_workspace/strategy_pipeline\08a_screen_funding_timing.md:13:post-settlement — changes realized net carry per episode versus F1's CURRENT entry-timing
_workspace/strategy_pipeline\08a_screen_funding_timing.md:14:baseline. The comparison baseline is current F1 behavior, NOT zero.
_workspace/strategy_pipeline\08a_screen_funding_timing.md:17:F1 is ALREADY settlement-window timed. The Rev-5 open gate only passes when
_workspace/strategy_pipeline\08a_screen_funding_timing.md:18:`time_to_next_funding_min ∈ [20, 180]` (`F1_MIN_TIME_TO_FUNDING_MIN = 20.0`,
_workspace/strategy_pipeline\08a_screen_funding_timing.md:19:`F1_MAX_TIME_TO_FUNDING_MIN = 180.0`, `research/funding_carry_lab.py:534-535`), plus
_workspace/strategy_pipeline\08a_screen_funding_timing.md:36:| B (baseline) | −105 min | current F1 behavior (mid-window of [20,180]) |
_workspace/strategy_pipeline\08a_screen_funding_timing.md:54:### Event universe (F1-regime proxy, mirrors the live gate on historically available fields)
_workspace/strategy_pipeline\08a_screen_funding_timing.md:56:for the frozen 15-coin F1 universe (`F1_EXPANDED_UNIVERSE_2026_07_05`) where:
_workspace/strategy_pipeline\08a_screen_funding_timing.md:60:  (same `f1_net_expected_edge_bps` arithmetic as the live gate).
_workspace/strategy_pipeline\08a_screen_funding_timing.md:62:are noted as unmodeled screen-vs-live divergence, exactly as in prior F1-family screens.
_workspace/strategy_pipeline\08a_screen_funding_timing.md:64:### Cost model (identical to F1's registered model — not softened)
_workspace/strategy_pipeline\08a_screen_funding_timing.md:66:+ 4 crossings × 5 bps slippage = **50 bps** (`scripts/run_f1_carry_paper.py::carry_round_trip_cost_frac`,
_workspace/strategy_pipeline\08a_screen_funding_timing.md:77:  the timing-modified equity curve if the improvement is applied to F1's traded curve.
_workspace/strategy_pipeline\08a_screen_funding_timing.md:89:2. Spot 15m OHLCV for the F1 coins — `data/ohlcv_cache/<BASE>-USDT_15m.parquet`.
_workspace/strategy_pipeline\08a_screen_funding_timing.md:90:3. **Perp 15m OHLCV or premium-index (basis) history for the F1 coins** — the basis leg.
_workspace/strategy_pipeline\08a_screen_funding_timing.md:100:| Funding history | ✅ | binance + bybit 2021-01-01 → 2026-07-11 for all 15 F1 coins (SUI from 2023-05-03 listing); bitget only 2026-06-05 → (excluded, pre-registered) |
_workspace/strategy_pipeline\08a_screen_funding_timing.md:102:| Perp 15m OHLCV (F1 coins) | ❌ ABSENT | `data/ohlcv_cache/` perp files (`-USDTUSDT_` suffix) exist ONLY for 14 commodity/equity bases (AAPL, CL, COIN, …) at 1h/4h/1d. Zero perp price files for any F1 coin at any timeframe. The 635 crypto parquets are SPOT (`scripts/backfill_universe_ohlcv.py` filters `m.get("spot")` — verified in source) |
_workspace/strategy_pipeline\08a_screen_funding_timing.md:104:| F1 paper-lane dual-leg snapshots | ❌ insufficient n | `data/carry_positions.json` real spot+perp fills exist only since Rev-5 PAPER start (~2026-07-02), far below the 120-event minimum |
_workspace/strategy_pipeline\08a_screen_funding_timing.md:112:**Exact blocking gap:** no perp-vs-spot basis series at ≤15m granularity for the F1 universe.
_workspace/strategy_pipeline\08a_screen_funding_timing.md:117:   (symbol=`<BASE>USDT`, interval=`15m`, full available history) for the 15 F1 bases, writing
_workspace/strategy_pipeline\08a_screen_funding_timing.md:130:**Context for the re-run:** because F1 already gates entries into [20,180] min pre-settlement,
_workspace/strategy_pipeline\08a_screen_funding_timing.md:155:   as frozen (the F1 gate's "current funding" analogue). Expected-edge check:
_workspace/strategy_pipeline\08a_screen_funding_timing.md:156:   `f1_net_expected_edge_bps(funding_per_settlement = trailing 7d mean, hold_settlements = 21,
_workspace/strategy_pipeline\08a_screen_funding_timing.md:185:(15 F1 bases × binance + bybit), 15m premium fractions; binance depth to 2019-12
_workspace/strategy_pipeline\08a_screen_funding_timing.md:190:Event universe after the frozen F1-regime filter: **binance 1,642 / bybit 944 qualifying
_workspace/strategy_pipeline\08a_screen_funding_timing.md:223:real money; F1's current pre-settlement entry timing is already on the right side, and
_workspace/strategy_pipeline\10_integration_report_candidate2.md:1:# 10 — Integration Report: `pre_unlock_short_capital_scaled` (Candidate 2, log-only shadow probe)
_workspace/strategy_pipeline\10_integration_report_candidate2.md:4:(CONFIRMED, 6 binding conditions), `08b_screen_preunlock_short.{md,json}` (frozen
_workspace/strategy_pipeline\10_integration_report_candidate2.md:5:pre-registration + execution addendum), `research/screen_preunlock_short.py` (frozen
_workspace/strategy_pipeline\10_integration_report_candidate2.md:6:constants), rev3 precedent `core/agents/listing_short_probe_agent.py` + `04_integration_report.md`.
_workspace/strategy_pipeline\10_integration_report_candidate2.md:12:| `core/agents/unlock_short_probe_agent.py` | NEW — `UnlockShortProbeAgent`, the log-only probe |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:13:| `tests/test_unlock_short_probe.py` | NEW — 21-test TDD spec, one test per binding condition/boundary |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:15:| `config.py` | `UNLOCK_SHORT_PROBE` block (`SHADOW_UNLOCK_PROBE_ENABLED`, default true; calendar dir; frozen venue order) |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:18:| `.claude/skills/refuted-families-ledger/SKILL.md` | New "In shadow" section: event-driven unlock-short row with the F1/F2 fragility profile (binding condition 6) |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:26:- **raw** (`unlock_short_{w1,w2}_v1`): `sl_px=0, tp_px=0`, `horizon_bars = (T − entry_ts)/1h`
_workspace/strategy_pipeline\10_integration_report_candidate2.md:28:- **sl8** (`unlock_short_{w1,w2}_sl8_v1`, proposal_id suffix `-sl8`): `sl_px = entry × 1.08`
_workspace/strategy_pipeline\10_integration_report_candidate2.md:40:| 2 | Per-bar intra-hold MTM + entry/exit ts+px, venue, realized funding, ratio, frozen score AT ENTRY | `shadow_unlock_mtm` (per-1h-bar mark + unrealized short return, closed bars only, no repaint); `shadow_unlock_probe` row carries entry_ts/px, venue, unlock_ts (exit target), `unlock_ratio`, `funding_entry`, `score` (frozen `tanh(ratio/0.20)+10×funding`, mirror-tested byte-identical to the screen's `auc_score`), per-8h `realized_funding_rate_sum`; `shadow_unlock_concurrent` logs the per-arm concurrent account-MTM drawdown (the F10 risk number). |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:53:-- per-arm resolved evidence (raw strategy)
_workspace/strategy_pipeline\10_integration_report_candidate2.md:56:WHERE d.model_version LIKE 'unlock_short_w%_v1' GROUP BY 1;
_workspace/strategy_pipeline\10_integration_report_candidate2.md:66:-- drawdown-tail question (audit F10): SELECT * FROM shadow_unlock_concurrent;
_workspace/strategy_pipeline\10_integration_report_candidate2.md:71:without the resolved `net_pnl` next to it (TP-probe precedent), and read every number
_workspace/strategy_pipeline\10_integration_report_candidate2.md:76:`core/promotion_gate.py` thresholds (MIN_DSR≥0.10, MAX_PBO≤0.5, OOS-WR≥0.55, AUC≥0.60) on
_workspace/strategy_pipeline\10_integration_report_candidate2.md:77:**≥30 RESOLVED forward events PER ARM**, assessed per-arm, PLUS an explicit owner decision.
_workspace/strategy_pipeline\10_integration_report_candidate2.md:98:3. Probe activates on the next bot restart (`SHADOW_MODE.enabled` + `UNLOCK_SHORT_PROBE.enabled`,
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:14:Blocking claim: no local perp OHLCV or premium-index (basis) series for any F1 coin; only stale
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:21:| `data/ohlcv_cache/` (635 files) filename sweep for `USDTUSDT/premidx/premium/qtr/mark/index/basis` | Only perp (`-USDTUSDT_`) files = 14 commodity/equity bases (AAPL…XAU) at 1h/4h/1d. Zero perp or premium files for any F1 coin. ✓ |
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:24:| Spot 15m staleness | `BTC-USDT_15m.parquet`: 4,059 bars, ts (epoch-sec) 2026-04-11 → 2026-06-14. Stale + short as claimed. 14/15 F1 coins have 15m files; **ZEC missing** — exactly as claimed. ✓ |
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:26:| Cited code constants (cross-boundary) | `F1_MIN/MAX_TIME_TO_FUNDING_MIN = 20.0/180.0` (research/funding_carry_lab.py, Rev-5 block) ✓; `DEFAULT_HOLD_SETTLEMENTS = 21` (core/carry_runner.py:47) ✓; `scripts/backfill_perps_ohlcv.py` is hardcoded to `ANALYSIS_ONLY_BASES` (lines 34/80/107, no `--bases` arg exists — the "extend with --bases" unblock wording is accurate) ✓ |
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:33:for the F1 universe is one backfill script away, exactly as 08a's preferred unblock states.
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:153:| 1. Funding-settlement timing | INSUFFICIENT_DATA | **CONFIRMED** | 0 price keys in derivs_history.jsonl (full grep); no F1 perp/premium files; premiumIndexKlines live keyless w/ history to 2019-12 |
_workspace/strategy_pipeline\09_audit_candidate2_final.md:1:# 09 — Final Adversarial Audit: `pre_unlock_short_capital_scaled` (08b GO)
_workspace/strategy_pipeline\09_audit_candidate2_final.md:4:`research/screen_preunlock_short.py`, `research/screen_listing_short.py` (reused helpers),
_workspace/strategy_pipeline\09_audit_candidate2_final.md:14:owner must see undiluted (findings F1–F3 below): the sign of the edge is robust; the *gate-clearing*
_workspace/strategy_pipeline\09_audit_candidate2_final.md:21:`research/screen_preunlock_short.py` re-executed → **byte-identical gate table** to
_workspace/strategy_pipeline\09_audit_candidate2_final.md:94:- **F1 (HIGH): the n≥30 floor is met only via monthly-cliff pseudo-replication.** SUI's 7 events
_workspace/strategy_pipeline\09_audit_candidate2_final.md:131:logging (binding condition 2, rev3 precedent) produces the real distribution. → F10, LOW.
_workspace/strategy_pipeline\09_audit_candidate2_final.md:140:  this two-sided restatement. → folded into F6/F10 risk framing.
_workspace/strategy_pipeline\09_audit_candidate2_final.md:159:   frozen gate (`core/promotion_gate.py` thresholds) on ≥30 RESOLVED forward events per arm +
_workspace/strategy_pipeline\09_audit_candidate2_final.md:162:2. **Per-bar intra-hold MTM logged** (rev3 precedent) — resolves F10 and the AUC-computability
_workspace/strategy_pipeline\09_audit_candidate2_final.md:173:   CONFIRMED-GO (log-only); fragility profile F1/F2 recorded so a future bull-tape bleed is not
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:40:## Candidate 2 — Funding-percentile persistence selectivity for F1 (carry-lane entry/exit refinement)
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:42:**What it is.** Condition F1 carry ENTRIES on the coin's funding sitting in a high percentile of its OWN trailing distribution (e.g., top quartile of 30–90d) with a persistence requirement, and condition EXITS on funding decaying below its trailing median — instead of (or on top of) the current level-based gate. The testable claim: percentile+persistence selection concentrates the same capital in fewer, longer, richer carry episodes, raising realized net carry per unit of round-trip cost. Delta-neutral throughout; no direction taken, ever.
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:44:**Mechanism.** Identical risk transfer to validated F1 (leveraged longs pay for hedged inventory); the refinement claim is that funding richness is autocorrelated — persistent demand imbalances (a coin trending in retail attention) decay over days, not one settlement — so the level-gate admits marginal episodes whose funding decays before round-trip costs amortize. Selectivity is the margin, and it matters MORE now: the average carry level has compressed.
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:47:- [Borri, Liu, Tsyvinski & Wu, "Cryptocurrency as an Investable Asset Class: Coming of Age" (arXiv 2510.14435, 2025)](https://arxiv.org/html/2510.14435v2): crypto carry (long spot + short perp, funding-driven) annualized Sharpe **6.45 over 2020–2025 full sample, fell to 4.06 from 2024, turned NEGATIVE in 2025**; funding mean ~8%/yr at 0.8% vol in-sample. Top-tier author group. This is double-edged and must be stated plainly: it strengthens the case for selectivity AND is the most credible external evidence yet that the F1 family's tailwind weakened in 2025 — worth surfacing to the owner regardless of this candidate's fate.
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:51:**Data to test it.** Entirely local, zero acquisition cost: `data/funding_history` (492 venue-symbol CSVs, multi-year, current to today) for percentile/persistence construction; F1 paper-soak logs (warehouse + carry runner gate pass/fail logging since Rev5) as the incumbent baseline. **The null hypothesis is the CURRENT gate, not zero:** `f1_entry_gate` already requires funding>0 now, 7d-average>0, trailing-settlement mean, contango, and net edge ≥ cost-multiple — the screen must show percentile+persistence beats that incumbent after costs, or the verdict is NO_GO.
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:53:**Costs / feasibility @$420.** Best of this batch: no new positions, no new fee legs, no new margin — a selection-rule change on the only validated, already-capitalized lane. Failure mode is benign (F1 unchanged). Fewer/longer episodes would also cut round-trip churn, the dominant F1 cost.
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:55:**Novelty-vs-ledger: ADJACENT** — the ledger's Validated section explicitly makes carry extensions screen-eligible. Distinct from all three refuted carry-adjacent rows: settlement-window timing (intra-window offsets — this is cross-settlement selection), dispersion hold-until-flip (venue-pair spread — this is single-venue-pair F1 as-is), quarterly basis leg-swap (instrument choice — this keeps the perp). Not directional funding (delta-neutral; funding is the harvest, not a price signal).
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:65:**External evidence.** Measured magnitudes, weak timing granularity: tokens surge 30–100% on international exchanges "within minutes" of an Upbit notice ([CCN analysis](https://www.ccn.com/analysis/crypto/upbit-listing-pump/)); vendor measurement: Upbit listings max expected return 51.9%, Bithumb 108.9%, strongest >800% intraday, with sub-second announcement feeds marketed as necessary ([DataMaxi+, Sep 2025](https://medium.com/@datamaxiplus/the-rise-of-korean-exchange-listing-alpha-capturing-upbit-and-bithumb-listing-pumps-9c9055388f15) — ⚠ vendor selling the feed, single source for the specific numbers); 2026 event examples: PLUME +50% ([CCN](https://www.ccn.com/analysis/crypto/plume-upbit-listing-price-surge/)), 9-altcoin wave ([Yahoo Finance](https://finance.yahoo.com/markets/crypto/articles/upbit-listing-announcement-triggers-price-033217292.html)). The post-pump "sharp reversal" is narrative-grade — no measured reversal study found.
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:85:2. **Candidate 2 (funding-percentile persistence selectivity for F1)** — best feasibility (zero new capital, refines the validated lane), fully local data, but thin external evidence for the specific claim and a freshly refuted sibling (settlement timing) as precedent; null hypothesis = incumbent gate. The Borri et al. carry-turned-negative-2025 finding should be surfaced to the owner independently of the screen result.
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:102:13. DataMaxi+ (Sep 2025) — Korean listing alpha, Upbit 51.9% / Bithumb 108.9% max expected (⚠ vendor selling feed, single source) — https://medium.com/@datamaxiplus/the-rise-of-korean-exchange-listing-alpha-capturing-upbit-and-bithumb-listing-pumps-9c9055388f15
_workspace/strategy_pipeline\14_scout_c_reopen_sweep_2026-07-16.md:64:**Delta: MODERATE STRENGTHEN of direction + STRONG independent validation of the audit's sizing/tail-risk reasoning. Timing contradiction persists unresolved.**
_workspace/strategy_pipeline\14_scout_c_reopen_sweep_2026-07-16.md:68:   **Read-through for our probe:** (i) independent corroboration of effect direction and magnitude on a different event set/data source — the unlocks.app 236-event study is no longer the only measured source, though it remains the only *controlled* one; (ii) his blow-up is on unhedged full-size event sizing — **exactly the failure mode our audit pre-empted with the 3%/12% capital-scaled caps**; his result is an external replication of why our full-stake sibling (listing-short) was CONFIRMED_NO_GO on sizing; (iii) his frequency concern matches our registered "~months at 1–3 qualifying events/month" pace and the ≥30-resolved promotion floor; (iv) his entry is T−1 with ~21d hold (post-unlock exposure) vs our W1 T−28d / W2 T−14d exit-at-T — the Kim (post-unlock) vs unlocks.app (pre-unlock drift) timing contradiction is still not adjudicated externally; our own screen's adjudication stands as registered. Single-source flag (practitioner blog, self-admitted "not a fully mature walk-forward production backtest").
_workspace/strategy_pipeline\14_scout_c_reopen_sweep_2026-07-16.md:89:2. **The funding-arb opportunity set is real but mostly cost-consumed** — MDPI Mathematics Jan 2026 (Two-Tiered): 17% of 1-minute observations show significant cross-venue spreads; only ~40% of TOP opportunities are net-positive after costs and spread reversals. **Prior update: quantitative external anchor for why F1 survives only with maker-first execution and why the dispersion extension failed our screen — selection INTO the visible spread is where the loss hides.**
_workspace/strategy_pipeline\14_scout_c_reopen_sweep_2026-07-16.md:91:4. **Frequency gate for event strategies** — practitioner-grade but well-argued (Tigro Blanc, Apr 2026): event strategies below ~20 trades/yr cannot be statistically distinguished from luck at retail horizons. **Our ≥30-resolved-events-per-arm promotion floor already embodies this; treat any future event-driven GO with n/yr < ~15 as multi-year-to-promote BY DESIGN and say so up front.**
_workspace/strategy_pipeline\15c_screen_depeg.md:95:- n >= 30 resolved events (MIN_N = MC_MIN_TRADES = 30); variants below are not
_workspace/strategy_pipeline\15c_screen_depeg.md:142:  timeout, de-dup, gap exclusion, unresolved-at-end, premium-side no-trade).
_workspace/strategy_pipeline\14_scout_b_spot_2026-07-16.md:87:- **Spot-perp basis dislocation outside funding settlement:** the spot expression ("buy spot when a perp-led cascade overshoots") is the same mechanism as the 07-11 Candidate 4 (post-cascade reversion) — already briefed, feasibility-limited by the absence of multi-year OI/liquidation history and intraday recovery speed. Not re-proposed; F1 owns the carry claim and settlement-window timing is a refuted ledger row (2026-07-11).
_workspace/strategy_pipeline\17_integration_report_2026-07-16.md:8:| F1 funding-percentile persistence selectivity | 15b: NO_GO | CONFIRMED_NO_GO re-grounded on structural legs (harvest 0.21× vs 0.75× floor; negative under 2× cost stress); CI-LB + fold gates demoted (clustered bootstrap flips CI-LB +0.53; folds structurally empty) | Refuted row, scoped: mechanism refuted; efficiency sub-claim UNTESTABLE in 2025–26 regime |
_workspace/strategy_pipeline\17_integration_report_2026-07-16.md:13:1. ⚠ **F1 incumbent idle** (15b debate, verified against production): live runner shows **0 entries across 49,384 gate evaluations** since the Jul-2 launch; current net edges −25 to −41 bps; debate replay found zero qualifying entries after 2024-12-07 on all 30 series. Scout A independently surfaced arXiv 2510.14435 (Borri/Liu/Tsyvinski/Wu): crypto carry Sharpe 6.45 (2020-25) → negative (2025). The validated lane is structurally idle in this regime — correctly refusing −EV, but harvesting nothing.
_workspace/strategy_pipeline\17_integration_report_2026-07-16.md:16:4. **Process gaps this run:** 15a screener died once on API 529 (resumed from transcript, harvest unaffected); 15c debate round 1 ran with 2/3 auditors (Fable attacker 529) — resolved by cache-resume, rev2 record supersedes. Korean cross-listing lag deferred (named follow-up; latency-infeasible to trade, measurement-only value).
_workspace/strategy_pipeline\17_integration_report_2026-07-16.md:18:**Owner decision points (no action taken without sign-off):** none required — nothing advances. Optional follow-ups listed in debate records: E1 funding-gap backfill for 15a (verdict unchanged either way); deep-threshold depeg NEW prereg once n≥30 accrues (weak prior); F1-idle strategic question (what the bot should do while carry is dry) is explicitly an owner conversation, not a pipeline output.
_workspace/strategy_pipeline\16_debate_15d_wrapper.md:149:disagreement — the pre-registration-order gap rated MINOR by Opus vs MAJOR by Fable — is resolved
_workspace/strategy_pipeline\16_debate_15d_wrapper.md:165:4. Conservative default concurs: even if every unresolved doubt were resolved against the screen,
_workspace/strategy_pipeline\13_band_conditional_screen.md:47:| F1 | BTC 1h ATR regime | ATR(14) `utils.indicators.atr` on binance `BTC/USDT:USDT` 1h; ratio = entry-anchored ATR / median(ATR, trailing 30d) | `<0.7`, `0.7–1.3`, `>1.3` |
_workspace/strategy_pipeline\13_band_conditional_screen.md:50:| F4 | Symbol spread percentile | nearest-in-time `candidates` row (same symbol, abs(dt) <= 1800s) `features_json.ob_spread_bps`; percentile vs that symbol's own candidates `ob_spread_bps` trailing 30d. DISCLOSED: no precomputed spread percentile exists in-span (`features` table ends 2026-06-14; `ob_spread_bps` present on only ~36% of in-span candidates). **Coverage floor: if <50% of resolved rows are joined+covered, family = INSUFFICIENT_DATA** (partial-coverage selection bias). | `<50th`, `>=50th` |
_workspace/strategy_pipeline\13_band_conditional_screen.md:60:- **G2** n_resolved >= **300** in the bucket
_workspace/strategy_pipeline\13_band_conditional_screen.md:69:- **G6** flow retention: the bucket must retain >= **10%** of total resolved
_workspace/strategy_pipeline\13_band_conditional_screen.md:92:_Computed 2026-07-12 11:27 UTC · research/screen_band_conditional.py · n_raw=15051, resolved=14555, censored=496, span=12.0d._
_workspace/strategy_pipeline\13_band_conditional_screen.md:103:| f1_btc_atr_regime | <0.7 | 3203 | 55.6% | 53.9-57.4 | -0.875 | -0.384 | 266.3 | 1 | 58/54 | NO_GO |
_workspace/strategy_pipeline\13_band_conditional_screen.md:104:| f1_btc_atr_regime | 0.7-1.3 | 11352 | 67.2% | 66.3-68.0 | -0.501 | -0.222 | 943.8 | 0.00631 | 62/73 | NO_GO |
_workspace/strategy_pipeline\13_band_conditional_screen.md:105:| f1_btc_atr_regime | >1.3 | 0 | - | - | - | - | - | - | - | INSUFFICIENT_DATA |
_workspace/strategy_pipeline\13_band_conditional_screen.md:120:- Coverage: {'f1_btc_atr_regime': 14555, 'f2_symbol_atr_pctl': 14531, 'f3_adx_4h': 14555, 'f4_spread_pctl': 5747, 'f6_ema_gap_4h': 14555} of 14555 resolved; F6 residual (<0.15% gap, not gated): 908; F4 forward joins (candidate ts up to +30min after entry): 2774.
_workspace/strategy_pipeline\18_context_2026-07-22.md:11:**Honesty frame (binding, from the ledger):** no directional futures EDGE exists on any pair by every prior measured screen (~2,400+ refuted tests; band lane is WR-geometry not edge, expectancy last measured ≈ −0.24R; F1 carry validated but regime-idle). Therefore "verdict per pair" here means: fitness of each pair for the aggressive-PAPER band program + any evidence-backed strategy note (probe membership, data gaps, cost/liquidity, incident history) — NOT a manufactured per-pair edge claim. Any genuinely novel candidate from either model goes through the standard pre-registered after-cost screen before any GO.
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:1:# 15b — Funding-percentile persistence selectivity for F1 (edge-screener, 2026-07-16)
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:10:Conditioning F1 delta-neutral carry ENTRIES on the coin's current funding sitting in a
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:14:Rev-5 `f1_entry_gate` selection on the same window, universe, venues, and cost model —
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:19:(`research/funding_carry_lab.f1_entry_gate` + the audited replay convention of
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:20:`scripts/f1_replay_historical.py`):
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:26:    (the live gate's `f1_net_expected_edge_bps` with `hold_settlements=21`),
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:27:  - `f1_net_funding_lower_bound_bps(trailing_21, hold=21, cost) > 0`
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:32:  - `F1_MAX_HOLD_SETTLEMENTS = 42` reached.
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:60:- Universe: `F1_EXPANDED_UNIVERSE_2026_07_05` (15 coins, frozen 2026-07-05,
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:77:- Stress row: 2× cost (F1_STRESS_COST_MULT) reported as a diagnostic for both arms.
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:130:spread expression — this is single-venue F1 as-is); quarterly-basis leg-swap
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:135:New code ONLY in `research/screen_f1_percentile_selectivity.py` (+ tests in
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:136:`tests/test_screen_f1_percentile_selectivity.py`). Reuses audited helpers from
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:137:`research/screen_listing_short.py` (`_dsr_prob`, `_pbo_across_horizons`,
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:139:(`f1_net_expected_edge_bps`, `f1_net_funding_lower_bound_bps`). No core/ or config
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:145:## 2. Results (run 2026-07-16 23:49 local; screen `research/screen_f1_percentile_selectivity.py`, 16/16 unit tests green)
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:148:1784217600 = 2026-07-16 16:00 UTC). 0 unresolved data-end episodes in any arm.
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:152:`harvest_guard`. Full JSON: `15b_screen_f1_selectivity.json`.
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:154:### Incumbent vs variants (pooled, after cost, resolved episodes)
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:194:   0.0, OOS-WR 0.77, MC pass) — because F1 carry itself is profitable. That was never
_workspace/strategy_pipeline\15b_screen_f1_selectivity.md:224:**Ledger action (post-audit):** add row "F1 percentile+persistence selectivity
_workspace/strategy_pipeline\16_debate_15a_delisting.md:11:- `research/screen_delisting_flow.py` + `research/screen_listing_short.py` (imported helpers)
_workspace/strategy_pipeline\16_debate_15a_delisting.md:101:Evidence: `short_net_return()` (screen_listing_short.py:88-98) is unbounded below — the
_workspace/strategy_pipeline\16_debate_15a_delisting.md:135:  resolved jointly with Fable M1: the breach IS real for the registered expression and
_workspace/strategy_pipeline\16_debate_15a_delisting.md:155:resolved events (E1 realized concurrent-MTM maxDD 0.81, E3 0.65; MC maxDD_p95 1.34/1.32
_workspace/strategy_pipeline\16_debate_15a_delisting.md:211:  for 2a-E2-class arms; E1 additionally needs the final-week funding question resolved
_workspace/strategy_pipeline\18_pair_dossier.md:75:- Band-cache outcomes were resolved at frac 0.35 by the 2026-07-12 screen and are 14,551/14,555 binance — treat as binance-only. Band WR is GEOMETRY, not edge; every screen-13 bucket was after-cost negative.
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:1:# 16 — Multi-model debate record: 15b F1 funding-percentile persistence selectivity
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:4:Screen on trial: `_workspace/strategy_pipeline/15b_screen_f1_selectivity.md` / `.json`,
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:5:code `research/screen_f1_percentile_selectivity.py`. Screener verdict on trial: **NO_GO**.
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:36:- Fold boundaries: f0 2021-01-08..2021-10-20 (inc 82 / V1 36), f1 2021-10-20..2022-08-02
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:56:headline; incumbent F1 itself appears to generate no new entries — undisclosed, owner-relevant.
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:90:| 2 | Zero data in trailing ~19 months despite "verified current to 2026-07-16"; incumbent F1 itself produces no qualifying replay entries after 2024-12-07 (Sonnet M2, Fable M1) | **VALID** | Last entry either arm 2024-12-07 16:00 UTC (bybit ZEC). Data currency claim is technically true (CSV tails = 2026-07-16) but the report never says the last *episode* is 19 months old. Owner-relevant beyond this screen: the in-soak F1 incumbent gate appears structurally idle in the current compressed-funding regime — consistent with the scout-cited Borri et al. 2025 carry-compression finding. |
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:141:1. **Ledger row wording (required):** add the row scoped as — "F1 percentile+persistence
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:147:2. **Owner alert (orthogonal to this screen, more urgent):** the incumbent F1 replay gate
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:149:   validated F1 carry lane in PAPER soak may be structurally idle in the current funding
_workspace/strategy_pipeline\16_debate_15b_f1_selectivity.md:151:   should be checked against the live F1 runner's actual entry log.
_workspace/strategy_pipeline\11_integration_report_tsmom.md:46:**Pine-vs-reference divergence, resolved:** the Pine allows a reversal flip
_workspace/strategy_pipeline\11_integration_report_tsmom.md:84:or TP-hit-rate without the resolved after-cost `net_pnl` next to it** (the
_workspace/strategy_pipeline\11_integration_report_tsmom.md:90:Per arm, via the FROZEN `core/promotion_gate.py` thresholds (MIN_DSR ≥ 0.10,
_workspace/strategy_pipeline\11_integration_report_tsmom.md:92:RESOLVED forward events per arm**, PLUS an explicit owner decision. An honest
_workspace/strategy_pipeline\11_integration_report_tsmom.md:145:**Spec provenance (resolved):** `candidates()` was edited after the 15:58
_workspace/strategy_pipeline\11_integration_report_tsmom.md:168:'breakout_60d_4h_v1'`; never a hit-rate without resolved after-cost
_workspace/strategy_pipeline\11_integration_report_tsmom.md:169:`net_pnl`; promotion only via frozen `core/promotion_gate.py` on ≥30
_workspace/strategy_pipeline\11_integration_report_tsmom.md:170:RESOLVED forward events + explicit owner sign-off. At the Codex trade
_workspace/strategy_pipeline\11_integration_report_tsmom.md:172:≥30 resolved events take roughly 3-4 months — by design.
_workspace/strategy_pipeline\18_scout_fable.md:7:**Ledger compliance:** `refuted-families-ledger` read first, including the 07-17 rows (F1 percentile-selectivity, stablecoin depeg, wrapper discount), the Open delisting row, and the 07-19 bundle-MR shadow additions (zfade cfg365 / rsi2 cfg226).
_workspace/strategy_pipeline\18_scout_fable.md:52:- **F1 carry:** no new evidence beyond the standing 2025-negative-Sharpe anchor; nothing this week changes the regime-idle diagnosis.
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:12:1. **"Implement" means log-only.** Agreed strategies are implemented ONLY as log-only shadow probes (`core/agents/`) or pre-registered screens through `strategy-evidence-pipeline`. The loop makes ZERO live/paper-order decision-path changes. Promotion beyond log-only = frozen gate (`core/promotion_gate.py`, ≥30 RESOLVED per arm) + explicit owner sign-off, always.
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:25:- **S1 Funnel triggers:** read `data/promotion_funnel.json`. Any lane with resolved ≥30 → compute frozen-gate metrics → dual-model adjudication → both-agree PROMOTE-WORTHY = owner dossier in `reports/promotion_dossiers/` (promotion still owner-signed); both-agree NO = retirement/continue note + ledger update; split = parked. One lane per iteration.
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:42:| Lane | State | Resolved | WR | Note |
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:49:| unlock_short | IDLE | 0/30 | — | calendar healthy, no qualifying events |
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:50:| listing_short | STARVED | 0/30 | — | no actionable shortable listings |
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:51:| f1_carry | IDLE | 0/30 | — | structurally idle (edges negative; correct refusal) |
_workspace/strategy_pipeline\20_audit_c1_cftc_options_pressure.md:9:Screener verdict `NO_GO` is **upheld**. Zero unresolved attacks that would flip the call. No probe integration.
_workspace/strategy_pipeline\20_audit_c1_cftc_options_pressure.md:13:### F1 — Capital-preservation MC kill on best cell (BINDING)
_workspace/strategy_pipeline\20_audit_c1_cftc_options_pressure.md:20:Joint PBO across 6 variants ≈ 0.103 (passes ≤0.5). Does not change F1.
_workspace/strategy_pipeline\21_loss_autopsy.md:64:- unlock_short: IDLE 0/30 wr=None
_workspace/strategy_pipeline\21_loss_autopsy.md:68:- listing_short: STARVED 0/30 wr=None
_workspace/strategy_pipeline\21_loss_autopsy.md:69:- f1_carry: IDLE 0/30 wr=None
_workspace/strategy_pipeline\21_refused_installs.md:23:**Allowed next research:** AccBand dual-goal frac prereg (`30_prereg_accband_frac_dual_goal`, hashed); VPIN jump-risk veto on a **fresh UTC day**; F1 only if after-cost edges return; whale/network N1–N3 briefs (`31_*`) only after paid PIT harvest — **no live Arkham wire**.
_workspace/strategy_pipeline\21_refused_installs.md:26:Existing fleet kept: MCP directional PAPER + 7 log-only shadow probes + F1 carry gate (structurally idle) + Binance Web3 smart-money **bonus B13** (not authority).
_workspace/strategy_pipeline\22_screen_c3_quarter_hour_imbalance.md:5:**Prereg sha256:** `7b33c63914c44a749b2cb57d3bec0dd1a1c33e593577d9eea3dc57e0fb2f1787`
_workspace/strategy_pipeline\20_prereg_c1_cftc_options_pressure.md:54:From `core/promotion_gate.py` + MC:
_workspace/strategy_pipeline\23_owner_audit_2026-07-23.md:100:  instruments are the 7 shadow probes + F1 carry + the evidence pipeline
_workspace/strategy_pipeline\23_candidate_queue_2026-07-23.md:54:- **F1:** remediation queued only if after-cost harvest evidence returns; no force-enable.
_workspace/strategy_pipeline\23_candidate_queue_2026-07-23.md:74:- **NEW brief #5: Hyperliquid funding as F1 conditioner** — data signal only, vendor-grade
_workspace/strategy_pipeline\30_integration_report_accband_dual_goal.md:13:| Profit path | Unchanged: F1 + event shadow probes only; AccBand does **not** create EV |
_workspace/strategy_pipeline\30_integration_report_accband_dual_goal.md:22:3. Pursue profit only via evidence lanes (F1 after-cost harvest if edges return; unlock/listing probes; VPIN veto next).
_workspace/strategy_pipeline\27_screen_vpin_jump_veto.md:11:- AccBand resolved rows (binance BTC/ETH perps): n=3050
_workspace/strategy_pipeline\30_prereg_accband_frac_dual_goal.md:17:- Lane: MCP directional AccBand entries only (not deep_breakout, not shadow probes, not F1).
_workspace/strategy_pipeline\30_edge_queue_2026-07-23.md:12:| 4 | Hyperliquid hourly funding as F1 timing conditioner | DATA signal only (cannot trade DEX); screen vs `data/carry_gate_log.jsonl` | NO_GO-lean | **Harvester shipped 2026-07-30** (`scripts/harvest_hl_funding.py` → `data/hl_funding_history.jsonl`). Accrue ≥30 paired HL×local episodes before screen; no F1 force-on |
_workspace/strategy_pipeline\30_edge_queue_2026-07-23.md:13:| 5 | F1 carry remediation | Only if local after-cost harvest evidence returns (edges currently −25…−41 bps idle; 2026 compression persists, spread ≈2.7% ann) | INFEASIBLE until edges clear cost | Gate-log proof of positive net edge episodes ≥30 |
_workspace/strategy_pipeline\30_edge_queue_2026-07-23.md:44:## F1 note
_workspace/strategy_pipeline\30_edge_queue_2026-07-23.md:50:Any GO from VPIN or F1 revival still requires frozen `core/promotion_gate.py` + owner sign-off before CONTROLLED_LIVE. AccBand dual-goal frac sweep (`30_prereg_accband_frac_dual_goal`) is a separate geometry track — expectation NO_GO.
_workspace/strategy_pipeline\26_scout_codex_indicators_ob_network_2026-07-23.md:11:At a $420 account, the honest autonomous-bot portfolio remains the validated delta-neutral F1 carry pipeline plus log-only event probes. Indicator-based direction remains unfit after costs.
_workspace/strategy_pipeline\26_scout_codex_indicators_ob_network_2026-07-23.md:45:| Funding/OI context | `data/funding_history/`, `data/funding_cache/`, `data/premium_index/`, `data/funding_oi/` | Useful context exists, but directional funding and OI-divergence are refuted. Valid use remains F1 carry and risk accounting. |
_workspace/strategy_pipeline\26_scout_codex_indicators_ob_network_2026-07-23.md:83:| F1 delta-neutral funding carry | Settlements/days | Multi-venue and counterparty controls | **Best available** | Only validated family; already in PAPER soak |
_workspace/strategy_pipeline\26_scout_codex_indicators_ob_network_2026-07-23.md:91:4. Research never grants autonomous authority: confirmed screen ΓåÆ log-only shadow ΓåÆ at least 30 resolved forward events ΓåÆ frozen promotion gate ΓåÆ explicit owner sign-off.
_workspace/strategy_pipeline\26_handoff_indicator_deep_research.md:27:- ef3c3ceb keeps: funnel/health Monitor (instant wake on lane ≥30 resolved / halt / latch / profile regression) + daily 09:07 loop cron. It will NOT run research and will NOT restart the bot while your session is active — coordinate restarts through the owner or a journal note.
_workspace/strategy_pipeline\27_prereg_vpin_jump_veto.md:58:- No live install without frozen `core/promotion_gate.py` + owner sign-off.
_workspace/strategy_pipeline\30_refuse_indicators_2026-07-23.md:26:- F1 carry if after-cost edges return
_workspace/strategy_pipeline\29_deep_research_pullback_ma20_rsi_2026-07-23.md:6:**Verdict: STOP — do not wire to live or AccBand PAPER entries.** This exact family is already on the binding ledger as refuted (textbook trend / RSI / confluence) with an owner-directed **log-only** shadow probe already running. Your pseudocode is a *weaker* variant of that probe (level condition `RSI>55` vs event `RSI cross↑55`; no SMA50>SMA200 gate). Forward accrual so far: funnel lane `pullback_ma20_4h` **7/30 resolved, WR 0.0**. External peer-reviewed walk-forward evidence shows naive momentum on BTC hourly **+31.16% gross → −45.93% net** at 10 bps costs ([arXiv 2606.00060](https://arxiv.org/abs/2606.00060)). Blog “high WR” RSI+MA stories do **not** meet the reopen bar (no FDR/DSR multiplicity, often survivorship / low n / wrong family).
_workspace/strategy_pipeline\29_deep_research_pullback_ma20_rsi_2026-07-23.md:68:- Promotion: only if ≥30 resolved **and** frozen `promotion_gate` **and** owner sign-off — expectation **NO-PROMOTE**
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:36:  ([arXiv 2510.14435](https://arxiv.org/abs/2510.14435)) — matching our F1 incumbent's measured
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:45:  unresolved before production.
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:47:**Local mapping:** F1 carry = already our validated family (in PAPER soak, regime-idle, correctly
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:132:1. **Nothing new to wire.** The honest wired instruments remain: F1 carry (validated, regime-idle),
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:138:3. **Wire criteria unchanged:** CONFIRMED_GO screen → log-only shadow probe → ≥30 resolved
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:148:5. [JFDS 2026 RL market making](https://doi.org/10.1016/j.jfds.2026.100197) — MM+funding capture Sharpe 1.49; latency unresolved
_workspace/strategy_pipeline\30_screen_accband_frac_dual_goal.md:32:  validated/evidence lanes (F1 carry, event probes) after promotion gates.
_workspace/strategy_pipeline\31_adjudication_codex.md:41:**D5, D6, D7, D8, D9, D10 and D12 were resolved in Codex's favour** (the `D_b` interaction
_workspace/strategy_pipeline\30_review_c3_quarter_hour.md:103:- **Whether 2026 Q2 was regime-anomalous** relative to the paper's 2021–24 window. Genuinely unresolved and unresolvable from local data. This is the honest residual uncertainty in this verdict — but note it cuts against C3's *gross* alpha too, which is ≈0, not merely cost-crushed.
_workspace/strategy_pipeline\26_scout_fable_indicators_ob_network_2026-07-23.md:67:1. **After-cost survivors remain delta-neutral carry/basis + (fragile) event-driven supply shorts** — already wired (F1 idle correctly; unlock/listing probes log-only).
_workspace/strategy_pipeline\31_codex_brief.md:10:disagreements D5, D6, D7, D8, D9, D10 and D12 were all resolved **in your favour** (interaction
_workspace/strategy_pipeline\31_codex_brief.md:38:  (C1 CFTC, VPIN, C3 quarter-hour, wrapper-discount, F1-selectivity, stablecoin-depeg).
_workspace/strategy_pipeline\31_design_codex.md:193:- ~2,400 refuted pattern tests. Every pre-registered screen this month returned NO_GO (C1 CFTC, VPIN, C3 quarter-hour, wrapper-discount, F1-selectivity, stablecoin-depeg).
_workspace/strategy_pipeline\31_deep_research_whale_network_sources_2026-07-24.md:46:| Venue funding / OI caches | Local; directional funding & OI-divergence **refuted** — keep for F1/risk only |
_workspace/strategy_pipeline\33_prereg_c2_gamma_expiry.md:102:- Any GO → log-only shadow probe first → frozen `core/promotion_gate.py` + owner sign-off.
_workspace/strategy_pipeline\34_deep_research_sweep_select_2026-07-24.md:21:**12/30 resolved, 0 wins, WR 0.000** (was 7/30 WR 0.0 on 07-23). Expectation NO-PROMOTE stands.
_workspace/strategy_pipeline\34_deep_research_sweep_select_2026-07-24.md:30:| Lane | Resolved | Forward WR | Gate result |
_workspace/strategy_pipeline\32_audit_cursor.md:20:**Otherwise:** short SL/TP signs and F1 lev-1 wallet math look correct; spot longs with valid SL+TP are locally monitored by design (no exchange SL). Remaining defects are sizing fallbacks, paper TP fill realism, wallet `total_balance` undercount, and risk-manager races — already captured in the audit plan.
_workspace/strategy_pipeline\32_audit_cursor.md:108:**Note:** Lev-1 futures short sign-flip (F1) is fixed in `on_open`/`on_close`; residual `lev > 1` gate in `_redebit_open_margin:240` is arithmetically equivalent at lev=1 — not reported as a live money bug.
_workspace/strategy_pipeline\32_audit_cursor.md:133:| Paper wallet short PnL at lev≥1 | Fixed (F1) |
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:29:perps are volatility timing (not edge), and funding compression persists (F1 correctly idle).
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:99:  **F1 stays structurally idle — correctly.** The existing ≥30 positive-net-edge-episode gate-log
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:108:- **Hyperliquid hourly funding as F1 timing conditioner (data signal only):** HL settles hourly →
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:113:  F1-ADJACENT (extensions screen-eligible per ledger).
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:184:  (vendor): funding drag, not fees, dominates active-account friction — reinforces F1's
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:189:1. **Nothing new to wire; nothing reopens a refuted family.** The honest instruments remain: F1
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:196:4. **Wire criteria unchanged:** CONFIRMED_GO screen → log-only shadow → ≥30 resolved forward
_workspace/strategy_pipeline\35_bear_vpin_jump_veto.md:24:- **FACT (precedent):** Positive selection on this exact lane is already refuted: `13_band_conditional` screen, 16 pre-registered conditioning buckets, Bonferroni m=16, 14,555 resolved band outcomes → **0/16 GO; every bucket after-cost negative** (best: f6, 78.5% WR at −$0.06/trade). VPIN would need to out-discriminate all 16 features already tested — against external evidence that VPIN's predictive power is "relatively weak" even for its home task (Frontiers 2026 proxy).
_workspace/strategy_pipeline\35_bear_vpin_jump_veto.md:31:- **FACT:** Prereg gate wording "Min n OOS skipped+kept events ≥ 30" does not specify per-arm vs pooled — an unfrozen degree of freedom that will have to be resolved after the hash.
_workspace/strategy_pipeline\31_prereg_edge_concentration.md:43:VPIN, C3 quarter-hour, wrapper-discount, F1-selectivity, stablecoin-depeg). 30-day directional
_workspace/strategy_pipeline\35_bull_vpin_jump_veto.md:24:4. **Band outcomes demonstrably stratify by toxicity proxies:** the band-conditional screen (`13_band_conditional_screen.md`, 14,555 resolved outcomes, Bonferroni m=16) refuted *positive* selection (0/16 GO) but its *negative*-selection findings shipped as the flag-gated `BAND_REGIME_FILTER_ENABLED` veto — 4h ADX>30 cohort WR 59.0% vs 65.7% baseline; BTC 1h vol-ratio<0.7 cohort WR 55.6% (ledger row 2026-07-12). Precedent: this exact study shape (band-lane veto overlay) has produced a shipped instrument once already.
_workspace/strategy_pipeline\35_bull_vpin_jump_veto.md:27:7. **Rails:** PAPER only, veto overlay on logged/replayed AccBand outcomes, no MCP live-path change from this prereg alone; any live install requires the frozen `core/promotion_gate.py` + owner sign-off (`27_prereg` Non-goals; brief §Binding rails).
_workspace/strategy_pipeline\35_bull_vpin_jump_veto.md:47:- Honest ceiling, stated plainly: **even a full GO is WR-protection/bleed-reduction, not edge.** The band lane remains after-cost negative as a whole (FACT 3); profit must still come from validated lanes (F1 carry, event probes) through the promotion gate. Nothing in the best case changes the live decision path without owner sign-off.
_workspace/strategy_pipeline\35_bull_vpin_jump_veto.md:77:2. **Sample floor unreachable:** BTC/ETH-only resolved band outcomes in the overlap window are < 30 skipped+kept events and cannot reach 30 within the committee's horizon — the prereg itself then forces INSUFFICIENT_DATA; defer until forward accrual covers it rather than burning the slot.
_workspace/strategy_pipeline\31_screen_edge_concentration.md:191:These are clauses the frozen prereg leaves under-specified. Each was resolved by a named constant in `research/screen_edge_concentration.py` **before** execution. They are execution decisions, not deviations from a specified value.
_workspace/strategy_pipeline\35_verdict_vpin_jump_veto.md:41:Funnel @ 2026-07-24T23:40Z: `rsi2_4h_cfg226` GATE_BLOCKED (WR in band, after-cost loss); `pullback_ma20_4h` 12/30 WR 0.0; `tsmom_20d_1h` 29/30 WR 0.34; `zfade_4h_cfg365` 24/30 WR 0.67; F1 IDLE. Directional cohort accruing.
_workspace/strategy_pipeline\37_bear_c3_quarter_hour_imbalance.md:87:- `_workspace/strategy_pipeline/22_prereg_c3_quarter_hour_imbalance.md` (+ `.json`, sha256 `7b33c63914c44a749b2cb57d3bec0dd1a1c33e593577d9eea3dc57e0fb2f1787`)
_workspace/strategy_pipeline\37_deep_research_profitable_futures_pairs_2026-07-29.md:53:- This bot’s F1 carry gate has been structurally idle / after-cost weak in prior pipeline audits — treat carry as a **separate product**, not a free upgrade to MCP OPEN.
_workspace/strategy_pipeline\37_deep_research_profitable_futures_pairs_2026-07-29.md:98:- Warehouse cohort under AccBand + cost-clearance (`ACCURACY_MIN_TP_COST_PCT≥0.35`) flipping to positive expectancy on a base with n≥30 RESOLVED.
_workspace/strategy_pipeline\37_deep_research_profitable_futures_pairs_2026-07-29.md:125:14. Local pipeline notes (CLAUDE.md harness log) — F1 carry idle / AccBand geometry ≠ edge.
_workspace/strategy_pipeline\37_debate_c3_quarter_hour.md:14:- `22_prereg_c3_quarter_hour_imbalance.{md,json}` — prereg sha256 `7b33c63914c44a749b2cb57d3bec0dd1a1c33e593577d9eea3dc57e0fb2f1787`, frozen before harvest/screen (audit binding check PASS).
_workspace/strategy_pipeline\37_debate_c3_quarter_hour.md:39:- Bull's residual 10% on the bookkeeping deliverable cites working-tree uncertainty (ledger modified-uncommitted). Resolved: this engine read the current working-tree ledger in full — the row is absent from the version that is operationally binding. The gap is real.
_workspace/strategy_pipeline\39_prereg_clamp_print_information_v2.md:1:# 39 — Pre-registration v2: clamp-print zero-information screen (F1 measurement hygiene)
_workspace/strategy_pipeline\39_prereg_clamp_print_information_v2.md:4:**Date:** 2026-07-28 · **Class:** measurement-correctness overlay on F1 — NOT a strategy family
_workspace/strategy_pipeline\39_prereg_clamp_print_information_v2.md:96:  measurement hygiene in F1 telemetry and any future funding-conditioned screen — still not a
_workspace/strategy_pipeline\39_prereg_clamp_print_information_v2.md:97:  trade, and no F1 parameter changes.
_workspace/strategy_pipeline\39_prereg_clamp_print_information_v2.md:112:No probe. No order-path or paper-order change. No promotion. No F1 parameter change. No edit
_workspace/strategy_pipeline\39_verdict_codex_final.md:9:| Clamp-print zero-information null (F1 telemetry measurement) | Frozen prereg `39_` (SHA-256 `dda32c8cf71d…`, committed pre-outcome in `4be03ad`); corrected venue+symbol CMH run statistically FALSIFIES the formal zero-excess null in 9/9 testable cells (Bonferroni α=0.05/12; OR_MH 1.55–7.83); 3 cells INSUFFICIENT_DATA. Mechanism is sticky venue default-state/sign persistence, **not** incremental positioning information. No trade use and no F1 change follows; any decision use requires a separate new prereg. | 2026-07-28 |
_workspace/strategy_pipeline\39_verdict_codex.md:5:3. **OPERATIONAL DISPOSITION:** No substantive ledger refutation row yet. At most: “Clamp-print v2 — INVALID_RUN: cross-venue/same-time outcome linkage; rerun required; no use.” A clamp-aware F1 telemetry filter is **less justified as a decision filter** after this result: retain it only as a log-only diagnostic until a valid rerun and, if anomalous, its own preregistered screen.
_workspace/strategy_pipeline\39_verdict_codex.md:7:4. **Pre-refuted trade implication:** Do not infer “positive clamp means take/keep F1,” “front-run the next funding,” or any directional long/short signal. This measures neither price returns nor net carry after costs, and the apparent effect may be a sticky reporting default.
_workspace/strategy_pipeline\39_verdict_reconciled.md:43:  any decision use (filtering F1 entries, conditioning any screen on clamp state)
_workspace/strategy_pipeline\39_verdict_reconciled.md:44:  requires its OWN new pre-registered screen. No F1 parameter change.
_workspace/strategy_pipeline\39_verdict_reconciled.md:46:  "positive clamp → take/keep F1", "front-run the next funding print", any
_workspace/strategy_pipeline\38_prereg_clamp_print_information.md:8:**Class:** **MEASUREMENT-CORRECTNESS OVERLAY** on F1 (funding carry — the one validated family).
_workspace/strategy_pipeline\38_prereg_clamp_print_information.md:389:measurement hygiene** — i.e. when computing funding dispersion, cross-venue spreads, or any F1
_workspace/strategy_pipeline\38_prereg_clamp_print_information.md:424:- ❌ **NO F1 parameter change.** No carry-runner threshold, gate, or knob is altered.
_workspace/strategy_pipeline\41_dossier_liq_cascade.md:24:**Not chosen this iteration:** C2 (Deribit snaps not screen-ready locally), F1 remediation (idle, not a new build), listing/unlock (already shadow GO), RSI/breakout/TSMOM/AccBand-profit (ledger STOP).
_workspace/strategy_pipeline\41_integration_note.md:37:Item “Liquidation-cascade / OI-flush reversion” moves from “accrue before prereg” → **prereg FROZEN + harvest green + screen queued**. C2 / F1 / unlock clocks unchanged.
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:10:Developing “profitable pair strategies” cannot mean inventing new RSI/breakout/TSMOM stacks for ALGO–LINK — those families are **ledger-REFUTED** or already log-only with NO-PROMOTE expectation. The only **validated** live-pipeline family remains **F1 delta-neutral funding carry** (long spot / short perp when funding persistently positive; flip when persistently negative), currently **structurally idle** under 2026 funding compression. AccBand MCP on FIT pairs is **WR-research geometry**, not a profit strategy (`AccBand frac dual-goal` CONFIRMED_NO_GO). The honest development plan is: (1) keep F1 remediation gated on after-cost edge revival; (2) accrue queued **event** strategies (C2 gamma-expiry fade L/S on BTC; liquidation-cascade reversion); (3) let CONFIRMED-GO **shadow shorts** (listing / unlock) resolve ≥30 events; (4) do **not** promote TA directional on FIT coins. Pair×strategy matrix below.
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:20:| Edge queue (binding order) | C2 accrual → liq cascade → OI×funding veto → HL F1 conditioner → F1 remediation ([30_edge_queue](30_edge_queue_2026-07-23.md)) |
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:24:### S1 — F1 / basis carry (VALIDATED — develop *remediation*, not a new idea)
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:60:- **Develop next:** Forward ≥30 RESOLVED/arm; young-token unlock conditioning = **NEW prereg only** after arms resolve (ledger fragility: 2023 net-negative unlocks).
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:71:### S6 — Hyperliquid funding as F1 timing conditioner (F1-ADJACENT)
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:73:- **Mechanism:** Use HL hourly funding as data signal only (cannot trade DEX here) to gate CEX F1 entries ([32_](32_deep_research_futures_2026-07-24.md)).
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:76:- **Action:** **QUEUE** cheap screen vs `data/carry_gate_log.jsonl` when F1 revival evidence appears.
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:97:| **ETH** | FIT + liquidity | F1 carry when edge returns; AccBand PAPER accrual only (WR research); unlock/listing if evented | TA directional “ETH alpha” |
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:99:| **BTC** | Liquidity + events | **Primary** for S2 C2 fade L/S; F1; liq-cascade major variant | Untimed 08:00 reversal without GEX/ATM-OI |
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:100:| **SOL** | Liquidity (FIT_WITH_GAPS) | F1 secondary; liq major; AccBand with gaps disclosed | Promote on narrative momentum |
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:110:| Persistent +funding, basis healthy | **Short perp** (+ long spot) | F1 / basis — only validated income path ([BackQuant](https://www.backquant.com/learn/basis-trade)) |
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:124:4. **If F1 gate-log shows ≥30 positive net-edge episodes:** remediation screen + optional HL conditioner.
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:130:- **Only validated strategy class:** F1 basis carry — currently idle; remediation ≠ force-on.
_workspace/strategy_pipeline\41_review_liq_cascade.md:43:No CONTROLLED_LIVE. No frozen-gate override. No order-flow promotion. No edits to `config.py` / `live_gate` / `promotion_gate` / `.env` authorized by this APPROVE.
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:8:No trading system can **guarantee** only profitable fills. What *can* be enforced is: **refuse opens with measured after-cost negative expectancy**. On this bot, MCP AccBand directional PAPER is **CONFIRMED_NO_GO for profit** (expectancy ≈ −0.24R; warehouse all-time −$674). The only ledger-validated profit class is **F1 funding/basis carry**, which is currently **idle** (latest gate log: `net_edge_bps` negative / funding ≤ 0). External research agrees: after-cost profit in crypto perps concentrates in delta-neutral carry when funding clears costs — not in indicator/AccBand directional stacks.
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:10:**Action taken:** set `MCP_DIRECTIONAL_ECONOMIC_GATE_MODE=strict` so AccBand/`paper_fallback` can no longer admit −EV directional OPENs. F1 remains allowed only when its own net-edge gate passes (already fail-closed).
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:17:- F1 `carry_gate_log.jsonl` recent samples: `ok=False`, reasons like `funding_rate <= 0`, `net_edge_bps` ≈ −100s.
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:29:| F1 carry when `net_edge_bps` clears gate | MCP AccBand directional under `paper_fallback` |
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:40:4. F1 unchanged — enters only on positive net edge (currently none).
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:45:2. Profit path = F1 when funding compresses ends — wait for edge, don’t force.
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:50:1. Refuted-families ledger (AccBand dual-goal NO_GO; F1 validated)
_workspace/strategy_pipeline\42_deep_research_profitable_trades_only_2026-07-29.md:57:Sub-questions: (1) Can AccBand be profitable? (2) What external strategies clear costs? (3) What can this bot enforce today? (4) F1 live edge status?  
_workspace/strategy_pipeline\43_edge_hunt_shorts_profit_2026-07-30.md:9:2. **Why not profitable** — AccBand WR ≠ profit (ledger NO_GO). F1 (the only validated profit family) is idle: **0 positive net-edge checks in 7 days**. Shadow probes are log-only.
_workspace/strategy_pipeline\43_edge_hunt_shorts_profit_2026-07-30.md:18:| F1 funding/basis carry | Validated but **idle** (compressed funding) |
_workspace/strategy_pipeline\43_edge_hunt_shorts_profit_2026-07-30.md:32:- **Wait for F1** — only live path with evidence; enters when `net_edge_bps` clears.
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:8:External 2025–2026 evidence and this bot’s ledger **converge**: minute/hourly directional prediction and textbook TA rarely survive retail costs; **delta-neutral funding/basis carry** remains the only structural profit class already validated here (currently idle). Absolute **$5–$200 USDT** movers are a useful *research universe*, not a proven edge. This pass **plugs in** (1) absolute-USDT-band shadow shortlist, (2) MCP tools for movers + F1 status, (3) Hyperliquid funding harvest for F1 conditioning, (4) a **hashed measurement prereg** for continuation/fade — **without** reopening AccBand/RSI/breakout as live profit.
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:25:| F1 carry | Validated; **idle** (0 ok / ~30k checks / 7d) |
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:31:2. **F1 carry when net_edge > 0** — only live profit path with evidence.
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:34:5. **Telemetry MCP** — interrogate movers + F1 without touching orders.
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:35:6. **HL funding harvest** — F1-adjacent conditioner data (queue #4).
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:44:| MCP `trading_bot_f1_edge_status` | Read-only F1 gate summary |
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:54:- Best next screens: **44_ Stage-0** when OHLCV accrual is dense; **C2 gamma** when Deribit snaps ≥30; **F1** when funding clears costs.
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:68:Firecrawl/Exa MCP unavailable — WebSearch + WebFetch + local ledger/warehouse. Sub-questions: (1) after-cost mid-cap patterns, (2) H/D/W momentum/MR survival, (3) what to plug without false edge, (4) absolute-$ band novelty vs %-movers, (5) F1/HL conditioner path.
_workspace/strategy_pipeline\44_prereg_abs_usdt_mover_band.md:5:**sha256_md:** f4b1a1087fd82f1eda1ccd8a9e01027600ae95ad876a1893b5ba4b61de55152a  

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command '$f=@('"'_workspace/strategy_pipeline/03_rev3_audit_findings.md','_workspace/strategy_pipeline/04_integration_report.md','_workspace/strategy_pipeline/43_edge_hunt_shorts_profit_2026-07-30.md','_workspace/strategy_pipeline/44_deep_research_strategy_hunt_movers_2026-07-30.md'); foreach("'$p in $f){ "`n### $p"; Get-Content -LiteralPath $p | Select-String -Pattern '"'Disposition|CONFI|resolved|RESOLVED|risk|leverage|net_edge|idle|GO|NO_GO|30' -CaseSensitive:"'$false | Select-Object -First 32 }' in D:\Downloads\Trading_Bot
 succeeded in 983ms:

### _workspace/strategy_pipeline/03_rev3_audit_findings.md

# 03 rev3 â€” Honesty-Auditor Findings (capital-scaled listing-short GO + binanceâˆ©bybit dispersion)
Inputs audited: `02b_rev3_screener_listing_short.md`/`.json` (**GO @7d,30d** â€” pipeline's FIRST GO),
`02a_rev3_screener_dispersion.md`/`.json` (NO_GO).
byte-for-byte. The GO **survives** â€” but only under a materially corrected risk figure and a set
**Reproduction â€” EXACT.** Re-ran both `--rev3`. Listing: verdict GO; every gate value (n_accepted
the JSON with zero mismatches. Dispersion: verdict NO_GO; best pair ZEC +0.4491 bps/0.3782 WR, DSR
## Candidate B â€” Post-listing perp short (capital-scaled) â†’ **CONFIRMED_GO, but ONLY as a log-only shadow probe at 
UNLEVERED 3%-notional, with binding conditions. Reported maxDD is corrected upward ~2Ã—.**
Every one of the eight frozen gates passes under independent recomputation. The GO moves no
capital; it routes to a log-only shadow probe. I confirm it **for that purpose only**. The findings
### FINDING B1 â€” HIGH â€” the reported MC maxDD (0.073) understates the TRUE intra-hold concurrent drawdown by 
~1.4â€“2.1Ã—. Gate still passes at 1Ã— notional; does NOT flip the GO, but the headline risk number is wrong and the 
safety margin is ~1.6Ã— not ~3.4Ã—.
| 30d | 0.0729 | **0.1397** | 0.1521 | 4 |
Worst single-position intra-hold MAE: SOMI pumped to **+290%** above entry (7d), ESP **+289%** (30d)
**The gate (â‰¤0.25) still passes** on the honest number (0.10â€“0.15 < 0.25), so the GO is not
falsified â€” but the true margin to the capital-preservation bound is ~1.6Ã— (30d), not the ~3.4Ã—
### FINDING B2 â€” HIGH â€” the model is unlevered, no-stop-loss, full-collateral, held-to-horizon. At the bot's live 
3Ã— tier the concurrent-MTM breaches 0.25; the charter's mandatory 8% SL is absent. The GO is safe ONLY at 1Ã— 
notional.
The screen sizes each position as `0.03 Ã— short_net` â€” 3% notional at **1Ã— leverage, fully
- **Leverage.** Scaling B1's concurrent-MTM to the bot's live STANDARD/SCALP **3Ã— tier** gives
  maxDD â‰ˆ 0.31 (7d) / 0.42 (30d) â€” a clear **breach of 0.25**, and SOMI's âˆ’290% intra-hold MAE at
  âˆ’1.27 realized exit. *Had the honest concurrent-MTM been computed at the live leverage tier, this
- **Collateral.** "3% risk" in CLAUDE.md Â§2 conventionally means capital-at-risk, not notional; the
the actual live leverage/SL before one dollar is risked.
### FINDING B3 â€” MEDIUM â€” the 30d GO rests on 34 cap-selected trades, only 4 above the MC floor of 30; 43/77 
candidates discarded. Thin. (7d is healthy at 71.)
| 30d | n=34 mean +0.185 WR 0.794 | n=43 mean **+0.286** WR 0.837 | n=77 mean +0.241 WR 0.818 |
At **30d the cap is conservative** â€” the 43 discarded trades performed *better* than the 34 accepted
**sample thinness**: 30d clears the MC evaluability floor (30) by a margin of 4, on a cap-dependent
must be a live probe condition, not a resolved concern.
### FINDING B5 â€” LOW/MEDIUM â€” survivorship is small at 7d/30d and its dominant direction is conservative; the 
dangerous (sustained-pump) losers are demonstrably retained in-sample.
near-nil at short horizons: `excluded_no_price_window` = 0 (7d) / 1 (30d), and among accepted names
**0 (7d) / 1 (30d)** have OHLCV truncating within 3d of exit. The worst short losers â€” SOMI (âˆ’1.27),
(conservative), with a smaller optimistic sliver. Warrants a modest confidence haircut, not a
`_pbo_across_horizons` runs CSCV on a (common-listings Ã— 3-horizon) matrix â€” only 3 "configs" and
â‰¥0.10 out to n_trials=120 (0.74) and 30d to n_trials=120 (0.54) â€” i.e. even at 20Ã— the registered

### _workspace/strategy_pipeline/04_integration_report.md
Input: `03_rev3_audit_findings.md` (Candidate B â€” **CONFIRMED_GO, log-only shadow probe**),
`02b_rev3_screener_listing_short.md`/`.json` (frozen pre-registration, GO @7d,30d).
Charter: LOG-ONLY. No `mcp_brain` / `order_manager` / risk-gate / config-live edits. No commit.
pipeline's first CONFIRMED_GO candidate â€” the capital-scaled post-listing perp short â€” at **zero
capital risk**. It runs inside the already-log-only shadow lane and is **structurally incapable of
Per shadow tick (300 s) it does three things:
   cherry-picking). Both **7d and 30d** horizon variants are logged (90d is INSUFFICIENT per the
resolved to a `time` exit with a real after-cost `net_pnl`; the 30d row correctly stays PENDING
| Config | `LISTING_SHORT_PROBE` (shadow-lane only, env `SHADOW_LISTING_PROBE_ENABLED`) | `config.py` |
per listing); the AUC gate later tests whether it actually ranks resolved winners above losers.
Logged per proposal in `shadow_listing_probe.score`; the binary label is derived from the resolved
AUCâ‰¥0.60) and is applied **only** by the owner, only after â‰¥30 resolved trades (B6). Every input is
| **Win rate â‰¥ 0.55** | fraction of resolved trades with (net_pnl + funding$) > 0 |
| **OOS-WR â‰¥ 0.55** | purged/embargoed walk-forward over listing-date order using `entry_ts`/`entry_ts+HÂ·DAY` from 
the probe rows (screen's `_oos_wr_walk_forward`) |
| **PBO â‰¤ 0.50** | CSCV across the 7d/30d horizons (needs both resolved) |
  join the resolved shadow-vs-live comparison automatically. This is aggregate; use the queries
- **Listing family, resolved-only** (via `trading_bot_query`):
  WHERE p.decision='ENTER' AND o.label_status='RESOLVED' ORDER BY p.entry_ts DESC;
- **Concurrent-MTM drawdown (the corrected risk number, gate â‰¤0.25):**
- **AUC readiness:** count resolved ENTER rows; do **not** compute AUC/DSR/MC until â‰¥30 resolved
moves capital, changes leverage, adds a stop, or touches a live decision path.
  This is the audit's safe variant. The probe does **not** inherit any live leverage tier or the
  since detection. Detection lag is â‰¤ one shadow tick (300 s).
  time-exit `net_pnl`; 30d correctly PENDING).

### _workspace/strategy_pipeline/43_edge_hunt_shorts_profit_2026-07-30.md
# Why no shorts / no profit â€” and the edge hunt (2026-07-30)
*Sources: local ledger + warehouse + carry_gate_log + 41_ screen | Confidence: High*
2. **Why not profitable** â€” AccBand WR â‰ profit (ledger NO_GO). F1 (the only validated profit family) is idle: **0 
positive net-edge checks in 7 days**. Shadow probes are log-only.
3. **Edge hunt today** â€” ran the dual-agreed queued screen (liq-cascade majors). **CONFIRMED_NO_GO**. No new 
strategy to wire.
| F1 funding/basis carry | Validated but **idle** (compressed funding) |
| Liq-cascade fade BTC/ETH | **Screened today â†’ NO_GO** |
| Listing / unlock shorts | Shadow GO, need real events |
| C2 gamma-expiry | Still accruing Deribit snaps (<30 events) |
Stage-0: all major cells â‰¥30 triggers. After 30/60 bps costs: **0 cells** clear mean>0 âˆ§ OOS-WRâ‰¥0.55 âˆ§ 
MCâ‰¥0.95 âˆ§ maxDDâ‰¤0.25 âˆ§ Holm. Best testable: ETH short-flush H12 @30bps mean +9.4bps but MC 0.59 and DD fail.
- **Wait for F1** â€” only live path with evidence; enters when `net_edge_bps` clears.
- **Accrue C2** â€” next screenable queue item when â‰¥30 conditioned events.
Artifacts: `41_screen_liq_cascade_majors.md`, ledger row 2026-07-30, `journal/2026-07-30.md`.

### _workspace/strategy_pipeline/44_deep_research_strategy_hunt_movers_2026-07-30.md
*Generated: 2026-07-30 | Sources: 14 | Confidence: High (local ledger), Medium (external)*
External 2025â€“2026 evidence and this botâ€™s ledger **converge**: minute/hourly directional prediction and textbook 
TA rarely survive retail costs; **delta-neutral funding/basis carry** remains the only structural profit class already 
validated here (currently idle). Absolute **$5â€“$200 USDT** movers are a useful *research universe*, not a proven 
edge. This pass **plugs in** (1) absolute-USDT-band shadow shortlist, (2) MCP tools for movers + F1 status, (3) 
Hyperliquid funding harvest for F1 conditioning, (4) a **hashed measurement prereg** for continuation/fade â€” 
**without** reopening AccBand/RSI/breakout as live profit.
| AccBand / MCP directional for profit | CONFIRMED_NO_GO |
| Liq-cascade majors fade | CONFIRMED_NO_GO (2026-07-30) |
| F1 carry | Validated; **idle** (0 ok / ~30k checks / 7d) |
| Listing / unlock shorts | Shadow GO; need events |
2. **F1 carry when net_edge > 0** â€” only live profit path with evidence.
- Best next screens: **44_ Stage-0** when OHLCV accrual is dense; **C2 gamma** when Deribit snaps â‰¥30; **F1** when 
funding clears costs.
5. Local ledger + 30_/41_/42_/43_ pipeline artifacts  



codex
The revised plan now has a stop clock, but the acceptance criteria still allow an administrative “GO” without demonstrated economic viability. I’m checking its stated probe evidence against the repository record before scoring.
exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "rg -n -i --glob '*.md' \"UnlockShortProbeAgent|unlock short|unlock_short|unlevered|3%-notional|290%|F1.*0.*ok|0.*positive.*net-edge\" _workspace/strategy_pipeline | Select-Object -First 180" in D:\Downloads\Trading_Bot
 succeeded in 957ms:
_workspace/strategy_pipeline\01_scout_candidates.md:28:- Post-unlock short: needs an unlock-calendar source not present locally (INSUFFICIENT_DATA by inspection).
_workspace/strategy_pipeline\04_integration_report.md:70:  Bigger pump ⇒ more expected decay ⇒ higher score. `tanh` bounds a single +290% outlier so it
_workspace/strategy_pipeline\04_integration_report.md:150:- **B2/B3 unlevered, no-SL, held-to-horizon (naked 1×):** modeled exactly (`sl_px=0`, `tp_px=0`).
_workspace/strategy_pipeline\08b_screen_preunlock_short.md:1:# 08b — Screen: Pre-Unlock Short, Capital-Scaled (NEW)
_workspace/strategy_pipeline\08b_screen_preunlock_short.md:19:- 3% of account equity per stake, 12% max concurrent gross exposure, UNLEVERED.
_workspace/strategy_pipeline\08b_screen_preunlock_short.md:172:real prints only). Screen: `research/screen_preunlock_short.py`. Accepted-event dump for
_workspace/strategy_pipeline\09_audit_candidate2_final.md:1:# 09 — Final Adversarial Audit: `pre_unlock_short_capital_scaled` (08b GO)
_workspace/strategy_pipeline\09_audit_candidate2_final.md:4:`research/screen_preunlock_short.py`, `research/screen_listing_short.py` (reused helpers),
_workspace/strategy_pipeline\09_audit_candidate2_final.md:21:`research/screen_preunlock_short.py` re-executed → **byte-identical gate table** to
_workspace/strategy_pipeline\09_audit_candidate2_final.md:112:- Unlevered ENFORCED in the math: account return = 0.03 × net_ret, additive curve, no leverage
_workspace/strategy_pipeline\09_audit_candidate2_final.md:115:  unlevered short's loss is bounded by 3% only up to a +100% adverse move (worst observed −61.5%
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:47:## Candidate 2 — Pre-unlock short (08b): **OVERTURNED** (the calendar exists, free and keyless)
_workspace/strategy_pipeline\08d_phase2_audit_2026-07-11.md:154:| 2. Pre-unlock short | INSUFFICIENT_DATA | **OVERTURNED** | `defillama-datasets.llama.fi/emissionsProtocolsList` (342 slugs) + `/emissions/{slug}` — free, keyless, historical, with cliff/linear + insider labels; arbitrum 89 in-window cliffs |
_workspace/strategy_pipeline\03_rev3_audit_findings.md:23:## Candidate B — Post-listing perp short (capital-scaled) → **CONFIRMED_GO, but ONLY as a log-only shadow probe at UNLEVERED 3%-notional, with binding conditions. Reported maxDD is corrected upward ~2×.**
_workspace/strategy_pipeline\03_rev3_audit_findings.md:44:Worst single-position intra-hold MAE: SOMI pumped to **+290%** above entry (7d), ESP **+289%** (30d)
_workspace/strategy_pipeline\03_rev3_audit_findings.md:45:— i.e. a single 3%-notional short was unrealized −8.7% of account at its worst, and the screen's
_workspace/strategy_pipeline\03_rev3_audit_findings.md:53:### FINDING B2 — HIGH — the model is unlevered, no-stop-loss, full-collateral, held-to-horizon. At the bot's live 3× tier the concurrent-MTM breaches 0.25; the charter's mandatory 8% SL is absent. The GO is safe ONLY at 1× notional.
_workspace/strategy_pipeline\03_rev3_audit_findings.md:57:+290% excursion from the rest of the account without liquidation) but diverges from live reality on
_workspace/strategy_pipeline\03_rev3_audit_findings.md:60:  maxDD ≈ 0.31 (7d) / 0.42 (30d) — a clear **breach of 0.25**, and SOMI's −290% intra-hold MAE at
_workspace/strategy_pipeline\03_rev3_audit_findings.md:63:  screen would be UNSAFE.* Its safety is entirely contingent on **unlevered** sizing.
_workspace/strategy_pipeline\03_rev3_audit_findings.md:65:  naked through 290% adverse moves. A real 8% SL would truncate every large loser at −8% but may
_workspace/strategy_pipeline\03_rev3_audit_findings.md:72:the probe must be unlevered 3%-notional, and any promotion analysis must re-derive the drawdown at
_workspace/strategy_pipeline\03_rev3_audit_findings.md:153:3. **Unlevered 3%-notional only (B2).** No leverage tier; if the live path would apply a tier or the
_workspace/strategy_pipeline\03_rev3_audit_findings.md:232:UNSAFE at any live leverage tier or under the mandatory 8% SL; unlevered-notional + concurrent-MTM
_workspace/strategy_pipeline\15c_screen_depeg.md:82:- **Funding**: n/a — pure spot, unlevered, no shorting.
_workspace/strategy_pipeline\15c_screen_depeg.md:87:- FULL NOTIONAL per event (weight 1.0 of strategy capital), unlevered — the scout's
_workspace/strategy_pipeline\16_debate_15a_delisting.md:167:recorded — FOR the registered expression (cross-margined unlevered short, no stop, no
_workspace/strategy_pipeline\15a_screen_delisting.md:50:  charged to the short), sized capital-scaled 3%/12% unlevered.
_workspace/strategy_pipeline\15a_screen_delisting.md:74:  (deterministic, return-blind). CLAUDE.md §2 compliant, unlevered.
_workspace/strategy_pipeline\15a_screen_delisting.md:199:  notional over the hold). One 3% unlevered stake = ~−67% of account equity.** This is the
_workspace/strategy_pipeline\15a_screen_delisting.md:209:  even at 3% unlevered stakes. Same failure class that killed full-stake listing-short —
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:32:## Candidate 2 — Pre-unlock short on large early-stage token unlocks (capital-scaled, perp-expressed)
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:38:**External evidence (the reason this is re-opened and REDIRECTED).** [unlocks.app insights, "Do Token Unlocks Crash Prices? What 236 Events Show"](https://insights.unlocks.app/do-token-unlocks-crash-prices/), published **2026-06-29**: 236 unlock events Jun-2024 → Mar-2026 (prices through Jun-2026), mcap >$10M, BTC-beta-adjusted, matched-peer + age-matched + within-token placebo controls. Findings: raw 1-month median −16.26% (72.5% closed lower); controlled overall −4.85% median; early-stage tokens −16.02% (age-matched −14.8%); established tokens −2.57% (no significant effect); of 77 events with measurable effects, 73 had unlock-value/mcap ≥10% and 68 were non-insider. **Critical timing finding: pre-event drift −14.7% (1 month) / −9.1% (2 weeks); post-unlock movement minimal.** This is single-source but methodologically rigorous (controls + placebos), and it *contradicts* the earlier settled single source ([SSRN 52-event "72-Hour Shock"](https://papers.ssrn.com/sol3/papers.cfm?abstract_id=6632838), post-unlock −16.97%) on WHEN the move happens. The screen must adjudicate the window on our own data; the naive post-unlock short briefed on 07-08 should be considered superseded.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:44:**Novelty-vs-ledger: NEW.** Not on the ledger. Distinct from the resolved listing-short rows (different event, different window). The 07-08 brief's "post-unlock short" was never screened; this candidate replaces it with a better-evidenced, differently-timed variant.
_workspace/strategy_pipeline\07_scout_candidates_2026-07-11.md:88:2. **Candidate 2 (pre-unlock short)** — strongest new external evidence of this pass (236 events, controls, 2026-06-29); gated on securing a historical unlock calendar (DefiLlama free tier now 402 — verified today).
_workspace/strategy_pipeline\10_integration_report_candidate2.md:1:# 10 — Integration Report: `pre_unlock_short_capital_scaled` (Candidate 2, log-only shadow probe)
_workspace/strategy_pipeline\10_integration_report_candidate2.md:4:(CONFIRMED, 6 binding conditions), `08b_screen_preunlock_short.{md,json}` (frozen
_workspace/strategy_pipeline\10_integration_report_candidate2.md:5:pre-registration + execution addendum), `research/screen_preunlock_short.py` (frozen
_workspace/strategy_pipeline\10_integration_report_candidate2.md:12:| `core/agents/unlock_short_probe_agent.py` | NEW — `UnlockShortProbeAgent`, the log-only probe |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:13:| `tests/test_unlock_short_probe.py` | NEW — 21-test TDD spec, one test per binding condition/boundary |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:15:| `config.py` | `UNLOCK_SHORT_PROBE` block (`SHADOW_UNLOCK_PROBE_ENABLED`, default true; calendar dir; frozen venue order) |
_workspace/strategy_pipeline\10_integration_report_candidate2.md:26:- **raw** (`unlock_short_{w1,w2}_v1`): `sl_px=0, tp_px=0`, `horizon_bars = (T − entry_ts)/1h`
_workspace/strategy_pipeline\10_integration_report_candidate2.md:28:- **sl8** (`unlock_short_{w1,w2}_sl8_v1`, proposal_id suffix `-sl8`): `sl_px = entry × 1.08`
_workspace/strategy_pipeline\10_integration_report_candidate2.md:56:WHERE d.model_version LIKE 'unlock_short_w%_v1' GROUP BY 1;
_workspace/strategy_pipeline\10_integration_report_candidate2.md:63:  (SELECT proposal_id FROM shadow_decisions WHERE agent_id='UnlockShortProbeAgent')
_workspace/strategy_pipeline\10_integration_report_candidate2.md:98:3. Probe activates on the next bot restart (`SHADOW_MODE.enabled` + `UNLOCK_SHORT_PROBE.enabled`,
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:49:| unlock_short | IDLE | 0/30 | — | calendar healthy, no qualifying events |
_workspace/strategy_pipeline\14_scout_c_reopen_sweep_2026-07-16.md:63:### 2b. Pre-unlock short (UnlockShortProbeAgent)
_workspace/strategy_pipeline\21_loss_autopsy.md:64:- unlock_short: IDLE 0/30 wr=None
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:5:**Ledger compliance:** `refuted-families-ledger` read first. Nothing below re-proposes a refuted row (incl. the three rows added since the last scout pass: funding-settlement-window timing 07-11, quarterly-basis leg-swap 07-11, band-geometry positive selection 07-12) or touches the four in-shadow probes (capital-scaled listing-short, pre-unlock short, TSMOM-20d, breakout-60d — evidence updates on those are scout C's lane).
_workspace/strategy_pipeline\14_scout_a_futures_2026-07-16.md:17:**What it is.** When a major venue (Binance primarily; Bybit/Bitget secondarily) announces DELISTING of a token's spot and/or perp, short the token's perp — on a venue that still lists it (cross-venue expression), or on the delisting venue itself up to its reduce-only cutoff — entering at the first post-announcement decision cycle and exiting before contract suspension/settlement. Sized with the capital-scaled template (3% per-trade / 12% exposure caps, unlevered) that already passed the MC maxDD gate for the sibling listing-short.
_workspace/strategy_pipeline\20_prereg_c1_cftc_options_pressure.md:49:- Position: 3% account notional, unlevered; single BTC name so concurrency N/A.
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:38:  Sharpe in recent OOS" and promotes only **quarterly cash-and-carry basis** (~3.2%/yr unlevered
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:50:3.2%/yr unlevered ≈ $13/yr — real but immaterial; not re-opened. Market making = infeasible
_workspace/strategy_pipeline\24_deep_research_futures_2026-07-23.md:147:4. [crypto-carry-research](https://github.com/boyam01/crypto-carry-research) — quarterly basis ~3.2%/yr unlevered after costs; funding carry regime-bleed (practitioner, adversarially reviewed)
_workspace/strategy_pipeline\32_deep_research_futures_2026-07-24.md:99:  **F1 stays structurally idle — correctly.** The existing ≥30 positive-net-edge-episode gate-log
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:30:- **Develop next:** Gate-log proof of ≥30 positive net-edge episodes; Hyperliquid hourly funding as **conditioner** (queue #4); do **not** force-enable under compression (~2.7% ann cross-venue spread vs 10–28 bps RT — [Bitsgap Q2-2026 cited in 32_](32_deep_research_futures_2026-07-24.md)).
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:56:- **Mechanism:** Event shorts around listing pumps / pre-unlock cliffs (capital-scaled 3%/12% unlevered).
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:124:4. **If F1 gate-log shows ≥30 positive net-edge episodes:** remediation screen + optional HL conditioner.
_workspace/strategy_pipeline\40_deep_research_strategies_profitable_futures_pairs_2026-07-29.md:131:- **Best directional L/S candidates still accruing:** C2 BTC fade; liq-cascade reversion; shadow listing/unlock shorts.
_workspace/strategy_pipeline\43_edge_hunt_shorts_profit_2026-07-30.md:9:2. **Why not profitable** — AccBand WR ≠ profit (ledger NO_GO). F1 (the only validated profit family) is idle: **0 positive net-edge checks in 7 days**. Shadow probes are log-only.
_workspace/strategy_pipeline\43_edge_hunt_shorts_profit_2026-07-30.md:21:| Listing / unlock shorts | Shadow GO, need real events |
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:25:| F1 carry | Validated; **idle** (0 ok / ~30k checks / 7d) |
_workspace/strategy_pipeline\44_deep_research_strategy_hunt_movers_2026-07-30.md:26:| Listing / unlock shorts | Shadow GO; need events |

exec
"C:\\WINDOWS\\System32\\WindowsPowerShell\\v1.0\\powershell.exe" -Command "if (Test-Path 'data/promotion_funnel.json') { Get-Content -Raw -LiteralPath 'data/promotion_funnel.json' } else { 'data/promotion_funnel.json is absent' }; rg -n -i --glob '*.md' \"listing_short.*(IDLE|[0-9]+/30)|unlock_short.*(IDLE|[0-9]+/30)\" _workspace/strategy_pipeline | Select-Object -Last 30" in D:\Downloads\Trading_Bot
 succeeded in 1006ms:
{
  "generated_utc": "2026-07-30T01:40:08.326643+00:00",
  "resolved_floor": 30,
  "lanes": [
    {
      "lane": "tsmom_20d_1h",
      "state": "GATE_BLOCKED",
      "resolved": 45,
      "wins": 14,
      "wr": 0.3111111111111111,
      "floor_progress": "45/30",
      "accrual_rate_7d": 2.857,
      "eta_days": 0.0,
      "detail": {
        "proposals": 47,
        "agent_id": "TsmomProbeAgent",
        "gate": {
          "passed": false,
          "fail_closed": true,
          "gates": {
            "n_resolved": {
              "value": 45,
              "threshold": 30,
              "ok": true
            },
            "oos_wr": {
              "value": 0.3111,
              "threshold": 0.55,
              "ok": false
            },
            "auc": {
              "value": 0.5,
              "threshold": 0.6,
              "ok": false
            },
            "net_after_cost_pnl": {
              "value": -32.454882,
              "threshold": 0.0,
              "ok": false
            },
            "expectancy": {
              "value": -0.72122,
              "threshold": 0.0,
              "ok": false
            },
            "profit_factor": {
              "value": 0.649609,
              "threshold": 1.0,
              "ok": false
            },
            "dsr": {
              "value": 0.0873,
              "threshold": 0.1,
              "ok": false,
              "computable": true,
              "note": "single-stream zero-skill proxy; selection-aware DSR still requires the registered trial count at sign-off"
            },
            "pbo": {
              "value": null,
              "threshold": 0.5,
              "ok": true,
              "computable": false,
              "informational": true,
              "note": "PBO requires a comparable strategy/fold return matrix \u2014 not computable on a single stream; informational only"
            }
          }
        }
      }
    },
    {
      "lane": "tsmom_20d_4h",
      "state": "ACCRUING",
      "resolved": 17,
      "wins": 3,
      "wr": 0.17647058823529413,
      "floor_progress": "17/30",
      "accrual_rate_7d": 1.143,
      "eta_days": 11.4,
      "detail": {
        "proposals": 19,
        "agent_id": "TsmomProbeAgent"
      }
    },
    {
      "lane": "breakout_60d",
      "state": "ACCRUING",
      "resolved": 0,
      "wins": 0,
      "wr": null,
      "floor_progress": "0/30",
      "accrual_rate_7d": 0.0,
      "eta_days": null,
      "detail": {
        "proposals": 1,
        "agent_id": "BreakoutProbeAgent"
      }
    },
    {
      "lane": "unlock_short",
      "state": "IDLE",
      "resolved": 0,
      "wins": 0,
      "wr": null,
      "floor_progress": "0/30",
      "accrual_rate_7d": 0.0,
      "eta_days": null,
      "detail": {
        "proposals": 0,
        "agent_id": "UnlockShortProbeAgent",
        "calendar": {
          "forward_days": 55.8,
          "starved": false,
          "backfill_cmd": "venv/Scripts/python.exe scripts/backfill_unlock_calendar.py --forward-days 60"
        }
      }
    },
    {
      "lane": "zfade_4h_cfg365",
      "state": "GATE_BLOCKED",
      "resolved": 55,
      "wins": 38,
      "wr": 0.6909090909090909,
      "floor_progress": "55/30",
      "accrual_rate_7d": 5.286,
      "eta_days": 0.0,
      "detail": {
        "proposals": 56,
        "agent_id": "ZfadeProbeAgent",
        "universe_widened_utc": "2026-07-20T00:00:00+00:00",
        "gate": {
          "passed": false,
          "fail_closed": true,
          "gates": {
            "n_resolved": {
              "value": 55,
              "threshold": 30,
              "ok": true
            },
            "oos_wr": {
              "value": 0.6909,
              "threshold": 0.55,
              "ok": true
            },
            "auc": {
              "value": 0.5,
              "threshold": 0.6,
              "ok": false
            },
            "net_after_cost_pnl": {
              "value": -1.516261,
              "threshold": 0.0,
              "ok": false
            },
            "expectancy": {
              "value": -0.027568,
              "threshold": 0.0,
              "ok": false
            },
            "profit_factor": {
              "value": 0.984868,
              "threshold": 1.0,
              "ok": false
            },
            "dsr": {
              "value": 0.4816,
              "threshold": 0.1,
              "ok": true,
              "computable": true,
              "note": "single-stream zero-skill proxy; selection-aware DSR still requires the registered trial count at sign-off"
            },
            "pbo": {
              "value": null,
              "threshold": 0.5,
              "ok": true,
              "computable": false,
              "informational": true,
              "note": "PBO requires a comparable strategy/fold return matrix \u2014 not computable on a single stream; informational only"
            }
          }
        }
      }
    },
    {
      "lane": "rsi2_4h_cfg226",
      "state": "GATE_BLOCKED",
      "resolved": 96,
      "wins": 63,
      "wr": 0.65625,
      "floor_progress": "96/30",
      "accrual_rate_7d": 10.286,
      "eta_days": 0.0,
      "detail": {
        "proposals": 99,
        "agent_id": "Rsi2TrackerProbeAgent",
        "universe_widened_utc": "2026-07-20T00:00:00+00:00",
        "gate": {
          "passed": false,
          "fail_closed": true,
          "gates": {
            "n_resolved": {
              "value": 96,
              "threshold": 30,
              "ok": true
            },
            "oos_wr": {
              "value": 0.6562,
              "threshold": 0.55,
              "ok": true
            },
            "auc": {
              "value": 0.5,
              "threshold": 0.6,
              "ok": false
            },
            "net_after_cost_pnl": {
              "value": -73.256539,
              "threshold": 0.0,
              "ok": false
            },
            "expectancy": {
              "value": -0.763089,
              "threshold": 0.0,
              "ok": false
            },
            "profit_factor": {
              "value": 0.651432,
              "threshold": 1.0,
              "ok": false
            },
            "dsr": {
              "value": 0.046,
              "threshold": 0.1,
              "ok": false,
              "computable": true,
              "note": "single-stream zero-skill proxy; selection-aware DSR still requires the registered trial count at sign-off"
            },
            "pbo": {
              "value": null,
              "threshold": 0.5,
              "ok": true,
              "computable": false,
              "informational": true,
              "note": "PBO requires a comparable strategy/fold return matrix \u2014 not computable on a single stream; informational only"
            }
          }
        }
      }
    },
    {
      "lane": "pullback_ma20_4h",
      "state": "GATE_BLOCKED",
      "resolved": 37,
      "wins": 4,
      "wr": 0.10810810810810811,
      "floor_progress": "37/30",
      "accrual_rate_7d": 5.143,
      "eta_days": 0.0,
      "detail": {
        "proposals": 38,
        "agent_id": "PullbackMomentumProbeAgent",
        "gate": {
          "passed": false,
          "fail_closed": true,
          "gates": {
            "n_resolved": {
              "value": 37,
              "threshold": 30,
              "ok": true
            },
            "oos_wr": {
              "value": 0.1081,
              "threshold": 0.55,
              "ok": false
            },
            "auc": {
              "value": 0.5,
              "threshold": 0.6,
              "ok": false
            },
            "net_after_cost_pnl": {
              "value": -116.546931,
              "threshold": 0.0,
              "ok": false
            },
            "expectancy": {
              "value": -3.149917,
              "threshold": 0.0,
              "ok": false
            },
            "profit_factor": {
              "value": 0.149422,
              "threshold": 1.0,
              "ok": false
            },
            "dsr": {
              "value": 0.0,
              "threshold": 0.1,
              "ok": false,
              "computable": true,
              "note": "single-stream zero-skill proxy; selection-aware DSR still requires the registered trial count at sign-off"
            },
            "pbo": {
              "value": null,
              "threshold": 0.5,
              "ok": true,
              "computable": false,
              "informational": true,
              "note": "PBO requires a comparable strategy/fold return matrix \u2014 not computable on a single stream; informational only"
            }
          }
        }
      }
    },
    {
      "lane": "listing_short",
      "state": "STARVED",
      "resolved": 0,
      "wins": 0,
      "wr": null,
      "floor_progress": "0/30",
      "accrual_rate_7d": 0.0,
      "eta_days": null,
      "detail": {
        "recent_proposals_30d": 31,
        "actionable_proposals_30d": 0,
        "known_tokenized_listings_30d": 3,
        "unclassified_listings_30d": 28,
        "starvation_reason": "no_actionable_shortable_listing",
        "note": "unknown tickers are not asserted to be crypto from name alone"
      }
    },
    {
      "lane": "f1_carry",
      "state": "IDLE",
      "resolved": 0,
      "wins": 0,
      "wr": null,
      "floor_progress": "0/30",
      "accrual_rate_7d": 0.0,
      "eta_days": null,
      "detail": {
        "alert": false,
        "top_edges": [
          {
            "venue": "bybit",
            "symbol": "BTC/USDT",
            "best_edge_bps": 0.0
          },
          {
            "venue": "bybit",
            "symbol": "ETH/USDT",
            "best_edge_bps": 0.0
          },
          {
            "venue": "bybit",
            "symbol": "BNB/USDT",
            "best_edge_bps": 0.0
          },
          {
            "venue": "bybit",
            "symbol": "SOL/USDT",
            "best_edge_bps": 0.0
          },
          {
            "venue": "bybit",
            "symbol": "LINK/USDT",
            "best_edge_bps": 0.0
          }
        ],
        "entries_48h": 8588,
        "note": "alert = net_edge_bps>0 on >=3 consecutive gate evals (hysteresis)"
      }
    },
    {
      "lane": "directional_paper_cohort",
      "state": "IDLE",
      "resolved": 0,
      "wins": 0,
      "wr": null,
      "floor_progress": "0/30",
      "accrual_rate_7d": 0.0,
      "eta_days": null,
      "detail": {
        "net_after_cost_pnl": 0,
        "profit_factor": null,
        "expectancy_per_outcome": 0.0,
        "target_status": "INSUFFICIENT_SAMPLE",
        "profile": "MAX_FLOW_BAND",
        "note": "directional PAPER research; no accuracy-band geometry is claimed"
      }
    }
  ]
}
_workspace/strategy_pipeline\21_loss_autopsy.md:64:- unlock_short: IDLE 0/30 wr=None
_workspace/strategy_pipeline\21_loss_autopsy.md:68:- listing_short: STARVED 0/30 wr=None
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:49:| unlock_short | IDLE | 0/30 | — | calendar healthy, no qualifying events |
_workspace/strategy_pipeline\19_dual_model_loop_protocol.md:50:| listing_short | STARVED | 0/30 | — | no actionable shortable listings |

codex
CEO verdict: **NO-GO as written.** The six-week clock is real, but Track D has no viable near-term evidence supply and the “GO” bar can be met without proving a deployable business.

| CEO dimension | Score | Adversarial finding |
|---|---|---|
| Premises valid? | PARTIAL | AccBand’s −EV and strict gate are supported; “deployable families” is not—F1, listing-short, and unlock-short are currently zero-resolved/idle or starved. |
| Right problem to solve? | PARTIAL | “Edge viability” is the right reframe, but deploy-readiness is not the constraint when no Track D lane has an observable path to 30 resolved events. |
| Scope calibration correct? | PARTIAL | Seven ops tasks plus three passive-monitoring tracks can consume Week 0; there is no hard cutoff preventing polish from replacing a viability decision. |
| Alternatives sufficiently explored? | NO | The comparison remains ops vs loosen gates vs invent strategy. It does not price/select the declared pivots: differentiated data, execution capability, another market structure, or ending research now. |
| Competitive/market risks covered? | NO | Missing: shortability/borrow and venue constraints, listing manipulation and extreme squeezes, capacity, queue/latency, paper-to-live fill divergence, capital economics, and crowding of carry/event signals. |
| 6-month trajectory sound? | NO | A “GO” can arise from an undefined positive F1 sample or a dossier merely being ready—not a passed promotion gate and executable return. EXTEND uses WR trajectory rather than economic expectancy/confidence. |

The plan’s own baseline contradicts its Track D confidence: `unlock_short` is **IDLE, 0/30**; `listing_short` is **STARVED, 0/30**, with zero actionable listings in the preceding 30 days; and F1 is **IDLE, 0/30** with zero positive edge. See [promotion funnel](D:\Downloads\Trading_Bot\data\promotion_funnel.json). Waiting six weeks for three event-starved lanes is not a probe-first strategy; it is a delayed decision.

Further, the historical listing-short “GO” does not prove live deployability. Its audit confines it to unlevered 3%-notional log-only shadowing, notes 290% adverse excursions, and finds the model unsafe at the existing 3× live tier. See [audit](D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\03_rev3_audit_findings.md).

Required revisions before approval:

- Replace “dossier ready” with full frozen-gate pass, independently reproduced, plus predeclared executable return, drawdown, capacity, and cost thresholds.
- Define F1 GO: minimum number of independent entries/episodes, net return after all execution costs, and a sustained—not single-sample—threshold.
- Add an early trigger: if no actionable event/proposal arrives by a fixed Week 1–2 date, pivot immediately rather than burn the whole window.
- Preselect one pivot with owner, budget, expected information gain, and its own stop rule. “External data buy / mothball / cease spend” is a menu, not a decision.
- Time-box Track A to the minimum needed for trustworthy measurement; any unfinished polish must not delay the pivot/stop determination.
tokens used
39,762
CEO verdict: **NO-GO as written.** The six-week clock is real, but Track D has no viable near-term evidence supply and the “GO” bar can be met without proving a deployable business.

| CEO dimension | Score | Adversarial finding |
|---|---|---|
| Premises valid? | PARTIAL | AccBand’s −EV and strict gate are supported; “deployable families” is not—F1, listing-short, and unlock-short are currently zero-resolved/idle or starved. |
| Right problem to solve? | PARTIAL | “Edge viability” is the right reframe, but deploy-readiness is not the constraint when no Track D lane has an observable path to 30 resolved events. |
| Scope calibration correct? | PARTIAL | Seven ops tasks plus three passive-monitoring tracks can consume Week 0; there is no hard cutoff preventing polish from replacing a viability decision. |
| Alternatives sufficiently explored? | NO | The comparison remains ops vs loosen gates vs invent strategy. It does not price/select the declared pivots: differentiated data, execution capability, another market structure, or ending research now. |
| Competitive/market risks covered? | NO | Missing: shortability/borrow and venue constraints, listing manipulation and extreme squeezes, capacity, queue/latency, paper-to-live fill divergence, capital economics, and crowding of carry/event signals. |
| 6-month trajectory sound? | NO | A “GO” can arise from an undefined positive F1 sample or a dossier merely being ready—not a passed promotion gate and executable return. EXTEND uses WR trajectory rather than economic expectancy/confidence. |

The plan’s own baseline contradicts its Track D confidence: `unlock_short` is **IDLE, 0/30**; `listing_short` is **STARVED, 0/30**, with zero actionable listings in the preceding 30 days; and F1 is **IDLE, 0/30** with zero positive edge. See [promotion funnel](D:\Downloads\Trading_Bot\data\promotion_funnel.json). Waiting six weeks for three event-starved lanes is not a probe-first strategy; it is a delayed decision.

Further, the historical listing-short “GO” does not prove live deployability. Its audit confines it to unlevered 3%-notional log-only shadowing, notes 290% adverse excursions, and finds the model unsafe at the existing 3× live tier. See [audit](D:\Downloads\Trading_Bot\_workspace\strategy_pipeline\03_rev3_audit_findings.md).

Required revisions before approval:

- Replace “dossier ready” with full frozen-gate pass, independently reproduced, plus predeclared executable return, drawdown, capacity, and cost thresholds.
- Define F1 GO: minimum number of independent entries/episodes, net return after all execution costs, and a sustained—not single-sample—threshold.
- Add an early trigger: if no actionable event/proposal arrives by a fixed Week 1–2 date, pivot immediately rather than burn the whole window.
- Preselect one pivot with owner, budget, expected information gain, and its own stop rule. “External data buy / mothball / cease spend” is a menu, not a decision.
- Time-box Track A to the minimum needed for trustworthy measurement; any unfinished polish must not delay the pivot/stop determination.
