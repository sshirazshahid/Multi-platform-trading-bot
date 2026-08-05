<!-- /autoplan restore point: /c/Users/SyedShirazShahid/.gstack/projects/sshirazshahid-Multi-platform-trading-bot/probe-bundle-mr-shadow-2026-07-19-autoplan-restore-20260730-065941.md -->
<!-- /autoplan revise cycle 2: owner accepted both User Challenges (A) 2026-07-30 -->
<!-- /autoplan APPROVED 2026-07-30T02:30Z owner choice A (final gate c2) -->
# Deploy Readiness + Edge Viability Loop (PAPER, evidence-gated)

**Status:** APPROVED (autoplan cycle 2) — Week-0 implementation in progress  
**Branch:** `probe/bundle-mr-shadow-2026-07-19`  
**Date:** 2026-07-30 (rev 2)  
**Owner intent:** Restart → monitor → improve → `/autoplan`. At final gate chose **Revise → A**: accept both User Challenges (time-box stop/pivot **and** CONFIRMED_GO probe-first). Final gate **A** approved.  
**Honesty bound:** AccBand WR ≠ edge. No AccBand/TA reopen without frozen promotion. F1 only live family when `net_edge` clears. Premises **A** still stand.

## Problem (reframed)

Directional AccBand is correctly idle under `EconGate=strict` + EntryFloor 66 (measured −EV). F1 is validated but regime-idle. Ops hygiene alone will not produce profit. The binding problem is **deploy readiness for families that already cleared evidence gates**, plus a **time-boxed decision** on whether continued research has positive EV.

## Premises (owner-confirmed A — unchanged)

1. Refuse −EV opens is success, not failure.
2. F1 is the only live-path family until another clears frozen promotion + owner sign-off.
3. Abs-USDT mover band is research/telemetry only — does not authorize OPENs.
4. Screens stay dual-agreed + prereg-hashed.
5. Optimize = silence failures + honest evidence, not WR loosening.

## Viability clock (Challenge 1 — IN SCOPE)

| Field | Value |
|-------|--------|
| Window | **6 weeks** from 2026-07-30 UTC (ends **2026-09-10**) |
| Early check | **2026-08-13** (Week 2): if unlock proposals still 0 **and** listing still STARVED/0 actionable **and** F1 still 0 positive-edge entries → mandatory owner call: continue wait / **PIVOT now** / **STOP** (do not silently burn weeks 3–6) |
| Decision date | On or before end date (or at early check): **GO / EXTEND(≤2w once) / PIVOT / STOP** |
| GO | **(a)** F1: ≥5 independent after-cost-positive carry episodes in window (documented from gate log + positions), **or (b)** unlock **or** listing: **frozen `promotion_gate` PASS** on a **named arm** (not pooled) + dossier staged + owner sign-off pending — “dossier ready” alone is **not** GO |
| EXTEND | Once only (`extend_used` in baseline artifact). Requires `accrual_rate_7d * days_remaining ≥ floor_gap` **and** expectancy (not only WR) non-worse vs prior weekly snapshot |
| PIVOT (preselected default) | **Preservation mode**: keep EconGate=strict + EntryFloor≥66; cease new screen campaigns; demote shadow research spend; optional owner fork later to “buy differentiated data” as a **new** plan — not a menu at decision time |
| STOP | Same as PIVOT plus freeze Track A beyond security/correctness hotfixes |

Success is **a decision with an early exit**, not infinite artifact cadence.

**Honesty on physics:** unlock ~1–3 events/mo and listing STARVED today mean **STOP/PIVOT is the base-rate outcome**. The clock exists to force that call, not to pretend ≥30 RESOLVED is likely.

## Tracks

### Track D — Deployable families first (Challenge 2 — PRIMARY)

Priority order (Week 0 must **unblock**, not only observe):

1. **ListingShortProbeAgent** — diagnose STARVED (shortability / ticker classification); ship minimal fix so proposals can become actionable; wire `promotion_funnel` **gate+dossier for `listing_short`** (today: GATE_READY excluded from `PROBE_LANES` dossier loop). Unlevered log-only; 3× still unsafe per rev3 audit.
2. **UnlockShortProbeAgent** — event-qualify audit (why 0 proposals despite calendar); funnel **per-arm** (`w1`/`w2`/…) not pooled; calendar forward ≥30d is necessary but not sufficient.
3. **F1 carry** — `carry_heartbeat` freshness alert; MCP distinguishes no-edge vs runner-stale; **no** threshold loosening.

**NOT** promoting without frozen gate + owner sign-off. Listing historical GO ≠ live deployable.

### Track A — Ops health (SECONDARY — time-boxed)

Must not delay Week-2 early check. Ship **measurement trust** first, polish later:

**Week-0 must (blocks trustworthy idle/read):**
1. Suppress/retarget `model_gate_starving` under EconGate=strict
2. Heartbeat `effective_config` (EconGate + EntryFloor + profile)
3. Atomic shortlist JSON + schema
4. `asset_class` tag + crypto-prefer (44_ / movers honesty)

**Week-0 should (if time):**
5. MCP unit tests movers + F1
6. Econ/funnel counters (denominator-aware)
7. HL harvest docs + nonzero exit

Unfinished 5–7 **must not** slip the Week-2 owner call.

### Track B — Evidence queue (DEPRIORITIZED)

- **44_ abs-USDT mover screen:** telemetry only until Track A asset_class ships **and** density gate written; Stage-0 **after** probe RESOLVED velocity check this week — do not start a multi-week 44_ campaign before probe floors move.
- HL harvest: cadence ok as cheap adjacent data for F1/regime context — not a new family hunt.
- C2 gamma-expiry: remains INSUFFICIENT_DATA queue — no burn of the viability window.

## NOT in scope

- AccBand `paper_fallback` reopen
- CONTROLLED_LIVE
- New directional TA/MCP without pipeline GO
- Hard-exclude tradfi without tagging (taste: tag + crypto-prefer)
- Mission Control UI redesign
- Full F1 “job platform” rewrite (observe + alert only this window)
- Inventing large new strategies inside the 6-week clock

## What already exists

| Need | Existing |
|------|----------|
| Econ gate / EntryFloor | strict + 66 (verified post-restart) |
| Abs band + shortlist JSON | `universe_monitor` + `bot_engine` snapshot |
| Launcher pins | `_safe_worker_env` abs-band keys |
| F1 | `carry_runner`, gate log, carry heartbeat |
| Unlock / listing probes | `UnlockShortProbeAgent`, `ListingShortProbeAgent` |
| Funnel | `scripts/promotion_funnel.py` → `data/promotion_funnel.json` |
| HL harvest | `scripts/harvest_hl_funding.py` |
| Prereg 44_ | frozen hash; screen not started |

## Success criteria

1. Heartbeat exposes effective EconGate=strict + EntryFloor=66 (not banner-only).
2. Mover shortlist: atomic, schema’d, `asset_class` tagged, crypto-prefer fills capacity.
3. Zero AccBand/MCP directional OPENs while model missing; **no** `model_gate_starving` spam under strict.
4. Weekly journal: F1 edge/stale; **per-arm** unlock RESOLVED; listing actionable+RESOLVED; funnel ERROR lanes noted.
5. **2026-08-13** early check recorded; **≤2026-09-10** GO/EXTEND/PIVOT/STOP artifact written.
6. Listing funnel can stage dossier when GATE_READY; unlock reported per-arm.
7. No new multi-week screen campaign that outranks Track D inside the window.

## First executable tasks

### Week 0 — Track D unblock + measurement trust

1. [ ] Baseline funnel → `45_viability_baseline_2026-07-30.md` (`extend_used: false`, per-arm zeros, listing STARVED detail)
2. [ ] Listing STARVED root-cause + minimal classification/shortability fix (TDD)
3. [ ] Funnel: enable gate+dossier for `listing_short`
4. [ ] Funnel: unlock **per-arm** lanes or journal split by `model_version`
5. [ ] Unlock: event-qualify audit (0 proposals) + calendar forward check
6. [ ] T3 `model_gate_starving` expected-idle under strict
7. [ ] Heartbeat `effective_config`
8. [ ] T2 atomic shortlist + schema
9. [ ] T1 asset_class + crypto-prefer + tests
10. [x] T4/T5/T6 if time — else defer past Week-2 without blocking early check
    - T4 MCP movers/F1 tests + F1 status taxonomy
    - T5 `trading_bot_open_funnel` denominator-aware counters
    - T6 HL harvest docs/dedup/nonzero exit

### Weeks 1–6

11. [ ] Weekly journal deltas; Week-2 early owner call if still event-starved
12. [ ] Frozen gate + dossier only when arm floor met; owner sign-off separate

### Decision

13. [ ] `45_viability_decision_YYYY-MM-DD.md` with GO/EXTEND/PIVOT/STOP + evidence

## Autoplan intake notes

- UI scope: **no**
- DX scope: **yes**
- Revise cycle: **2 / 3 max** (challenges accepted; critical GO/priority fixes baked from c2 voices)
- Taste: tag + crypto-prefer; default PIVOT = preservation mode
- Base-rate honesty: STOP/PIVOT expected unless events appear

---

## GSTACK REVIEW REPORT (cycle 2)

### Premises
Owner A unchanged. Challenges A accepted (time-box + probe-first). Critical c2 fixes auto-merged into plan (GO bar, Week-2 early exit, preselected PIVOT, Track D unblock, Week-0 priority).

### CEO dual voices (c2)

**Claude:** Right problem PARTIAL; Week-0 still ops-heavy vs Track D; GO ≥30 vs event physics mismatch; PIVOT not precommitted; listing unblock missing.  
**Codex:** NO-GO as written pre-fix; lanes 0/30 IDLE/STARVED; dossier≠GO; need early Week 1–2 trigger + preselected pivot + stricter F1 GO.  

**Post-bake:** Plan updated for those CRITICAL items. Remaining: market-risk essay depth (deferred TODOS — not blocking Week-0).

**CEO consensus (c2, post-bake intent):**

| Dimension | Consensus |
|-----------|-----------|
| Premises valid? | PARTIAL→YES on honesty; PARTIAL on “deployable soon” |
| Right problem? | YES (edge viability + early exit) |
| Scope calibration? | PARTIAL→improved (Track D unblock first) |
| Alternatives? | PARTIAL (PIVOT preselected; priced alts deferred) |
| Market risks? | PARTIAL (listing/unlock risks named; crowding thin) |
| Trajectory? | PARTIAL (STOP/PIVOT base-rate explicit — honest) |

### Design
Skipped (no UI).

### Eng dual voices (c2)

**Claude eng:** listing dossier gap; unlock arm pool; starvation spam; heartbeat effective_config missing; tests NO for T1–T7.  
**Codex eng (c1 retained + c2 CEO):** same control-plane themes.

**Eng consensus:**

| Dimension | Consensus |
|-----------|-----------|
| Architecture | PARTIAL (fixes now in task list) |
| Tests | NO until Week-0 pack ships |
| Perf | YES |
| Security | YES (log-only) |
| Errors | PARTIAL |
| Deploy | PARTIAL (supervisor restart discipline) |

### Cross-phase themes (c2)
1. Event starvation ≠ ops failure  
2. Dossier ≠ promotion  
3. Track D must unblock STARVED, not only watch  
4. Early exit beats polite 6-week wait  

### Decision Audit Trail (c2 additions)

| # | Phase | Decision | Classification | Principle | Rationale | Rejected |
|---|-------|----------|----------------|-----------|-----------|----------|
| 16 | CEO | Accept revise A (both challenges) | User | — | Owner D→A | Keep cycle-1 plan |
| 17 | CEO | GO = frozen gate PASS + F1≥5 episodes | Mechanical | P5 | Codex+Claude: dossier-ready too weak | Dossier-only GO |
| 18 | CEO | Week-2 early exit | Mechanical | P1 | Event-starved base rate | Blind 6w wait |
| 19 | CEO | Default PIVOT = preservation | Mechanical | P5 | Menu≠decision | Defer pivot choice |
| 20 | Eng | listing dossier + unlock per-arm in Week 0 | Mechanical | P2 | Code gaps block Track D | Observe-only |
| 21 | Eng | Track A must/should split | Mechanical | P3 | Don’t delay Week-2 | All T1–T7 blocking |

### Deferred to TODOS.md (updated)
- Priced “buy data / new microstructure” pivot fork (separate plan)
- Crowding/capacity competitive deep-dive
- Per-venue UniverseMonitor timeouts
- Full F1 job platform

### Implementation Tasks (aggregated)

- [ ] **D0** (P1) — viability baseline artifact  
- [ ] **D1** (P1) — listing STARVED unblock + tests  
- [ ] **D2** (P1) — funnel listing dossier path  
- [ ] **D3** (P1) — unlock per-arm funnel/journal  
- [ ] **D4** (P1) — unlock 0-proposal event audit  
- [ ] **T3** (P1) — expected-idle starvation suppress  
- [ ] **T7** (P1) — heartbeat effective_config  
- [ ] **T2** (P1) — atomic shortlist  
- [ ] **T1** (P1) — asset_class + crypto-prefer  
- [x] **T4/T5/T6** (P2) — MCP tests / counters / HL docs (non-blocking for Week-2)  
