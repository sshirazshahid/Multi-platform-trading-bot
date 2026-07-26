# 16 — Multi-Model Debate Record: 15a Delisting Forced-Flow Screen (2026-07-17)

Reconciler: Fable 5 (this document). Auditors (independent, array order): Sonnet 4.6,
Opus 4.8, Fable 5. Verdict on trial: **INSUFFICIENT_DATA** for both sub-claims (2a
capital-scaled perp short; 2b survivor-venue spot bounce), frozen 30-event MC floor,
with affirmatively adverse point evidence.

Primary artifacts adjudicated against:
- `_workspace/strategy_pipeline/15a_screen_delisting.md` (pre-registration + results)
- `_workspace/strategy_pipeline/15a_screen_delisting.json` (run output)
- `research/screen_delisting_flow.py` + `research/screen_listing_short.py` (imported helpers)
- `data/delisting_screen/` harvest cache (read-only)

Reconciler re-verification (all read-only, `venv/Scripts/python.exe`, 2026-07-17):
1. **Full offline reproduction from cache: EXACT** — n_events / event_mean / win_rate /
   realized_concurrent_mtm_max_dd match the JSON to float precision on all 6 variants.
2. **ALPACA E1 leg reproduces to the digit**: entry 0.05296 → exit 1.19000 = +2,147.0%
   against the short, funding sum −0.962, net −22.434× stake. (Third independent
   confirmation; Sonnet additionally verified the per-second announcement timestamp.)
3. Bitget cache tally: 138 legs = 39 `.notlisted` + 73 header-only CSVs + 26 real-data
   CSVs; 39+73 = 112 = the reported `2b_not_on_bitget` exclusion exactly.
4. Live Bitget re-query (5 symbols): REN/ANT/UNFI → HTTP 400 code **40309 "The symbol
   has been removed"** even with the harvest's historical endTime; AION → 40034
   (never listed); POND → 200 + data (**still listed today**).
5. A1 recount from `announcements.json`: archive complete from 2022-02-17 03:47 UTC;
   original 2023-01-01 floor → **28** qualifying events; A1 window → **34**.
6. E1-vs-E2 coverage: E2 covers 25 events, E1 17; **E2−E1 = exactly the 8 newest events**
   (2025-12-17, 2026-02-13, 2026-04-01, 2026-04-23, 2026-04-28, 2026-05-27, 2026-06-19,
   2026-07-10). Per-leg diagnostics: vision funding ends **7–8 days before removal,
   mid-month** (KDA 11-06 vs removal 11-12; CHESS 02-06 vs 02-13; MLN 05-19 vs 05-27;
   HIGH 06-11 vs 06-19) while klines continue to removal+2d. Local funding CSVs rescued 0.
7. Isolated-margin liquidation-cap re-run (short liquidated at high ≥ 1.95× entry, loss
   capped at −100% of stake): E1 event mean −8.8% / WR 0.471 / MC P>0 0.367 / maxDD p95
   0.129; **E2 +0.70% / MC P>0 0.959 / maxDD p95 0.066**; E3 −1.15% / 0.876 / 0.089;
   liquidated legs 4/2/2. Matches the Fable auditor's re-run within MC noise.

---

## Per-model attack summaries

**Sonnet 4.6** (verdict stands, confidence medium): attacked the 2b methodology
narrative — live re-queried Bitget's endpoint and proved the "Bitget bounds
survivorship, unlike Bybit" pre-registration claim is empirically false (unhandled
error 40309 silently censors delisted-on-Bitget symbols into the empty bucket);
flagged Amendment A1 as count-triggered ("count-blind ≠ outcome-blind"), a
garden-of-forking-paths risk, though harmless (rescued no variant); did not verify
the 03:00-UTC removal-hour convention (time-boxed, immaterial). Confirmed the ALPACA
tail with the exact per-second announcement timestamp.

**Opus 4.8** (verdict stands, confidence high): verified bit-for-bit reproduction from
cache and the ALPACA tail read-only; argued the INSUFFICIENT_DATA label is *softer than
the evidence warrants* (realized MTM maxDD 0.81/0.65 and MC maxDD p95 1.34/1.32 vs the
0.25 gate already demonstrate a capital-preservation breach) and asked that the breach
be recorded explicitly; graded A1 defensible (outcome-blind, power-increasing,
disclosed, bias favored the short yet result stayed adverse, and verified immaterial —
pre-2023 events added ~0 covered perp legs); noted the disclosed E2/E3 optimistic
survivorship (early-dead perps excluded).

**Fable 5 auditor** (verdict stands, confidence high): attacked the *generalizations*,
not the verdict — (M1) the "tail fatal / gates unmeetable at any sizing" framing is
model-scoped to a cross-margined, no-liquidation, no-stop short; under isolated-margin
1× with liquidation capping, E2 flips to MC-passing point estimates; (M2) the funding
exclusion is mis-described — E1 silently lost its 8 most recent events to a systematic
final-week funding gap on delisting-bound perps, not to "vision publishes monthly
(July-2026 windows)"; plus minors: 2b "cost-free" sensitivity mislabeled (still charges
30 bps fees+slip), 2b exclusions not "mostly pre-2024", perp_data_end exit proxy is a
mild look-ahead, entry-lag "conservatism" not uniform (amplifies the tail multiple),
A1 verified legitimate.

---

## Adjudications — FATAL/MAJOR findings (none FATAL)

### 1. Sonnet MAJOR — "Bitget bounds survivorship" is factually false → **VALID**
Evidence: `load_bitget_spot()` (screen_delisting_flow.py:456-459) branches ONLY on code
40034 → `.notlisted`; any other error (40309) breaks the paging loop → header-only CSV
→ counted in `2b_not_on_bitget`, indistinguishable from never-listed. Live re-query
2026-07-17: RENUSDT/ANTUSDT/UNFIUSDT → 40309 at the harvest's own historical endTime;
PONDUSDT serves data only because POND is still listed on Bitget today. Cache tally
(39 notlisted + 73 empty = 112) matches the exclusion accounting exactly.
Consequence: the 2b n=11 sample IS conditioned on surviving on Bitget — the opposite of
the stated design goal; the Bybit-vs-Bitget comparison in the pre-registration is wrong.
Direction: censoring the most-distressed names flatters the retained sample upward, yet
the retained sample is still uniformly after-cost negative — the adverse 2b read holds
and is plausibly understated. **Verdict unaffected (n=11 ≪ 30 either way); record
correction is mandatory.**

### 2. Sonnet MAJOR — Amendment A1 was count-triggered (peeking-adjacent) → **PARTIALLY-VALID**
Facts confirmed by recount: 28 events under the original 2023-01-01 floor, 34 under A1,
archive complete from 2022-02-17; the amendment was made after observing the qualifying
event COUNT fall below the frozen floor, before any price/funding data existed. That is
a real sample-size-contingent researcher degree of freedom and the process precedent
stands: future window amendments should be committed before observing even preliminary
counts. However it was outcome-blind, disclosed pre-results in the frozen section,
power-increasing, its bias direction favored the strategy yet results stayed adverse,
and it was verified immaterial — it rescued no variant (max covered n = 25 < 30) and
the ALPACA tail is a 2025 event independent of A1. Valid observation; MAJOR severity
overweighted (Opus and Fable graded the same facts MINOR). No verdict impact.

### 3. Fable MAJOR — adverse framing is scoped to cross-margin/no-liquidation → **VALID**
Evidence: `short_net_return()` (screen_listing_short.py:88-98) is unbounded below — the
ALPACA leg books −22.43× stake, only possible cross-margined with no liquidation and no
stop; that IS the registered expression, so the screen computed it honestly, but the
report's generalizations ("capital gates unmeetable at ANY sizing", "nothing available
today would change the verdict") overreach. Reconciler re-run with isolated-margin 1×
liquidation capping (high ≥ 1.95× entry → −100% of stake): E2 event mean +0.70%,
MC P>0 0.959, maxDD p95 0.066 — the capital-preservation MC passes on point estimates
(E1 still fails: mean −8.8%, MC P>0 0.367; E3 fails: MC P>0 0.876); matches the
auditor's independent numbers (E2 +0.5%/0.958/0.068) within MC noise. E2 would still
fail beats_control and DSR, and n<30 everywhere, so no verdict change — but any
pipeline-log/ledger record MUST scope "tail fatal" to the registered cross-margin
no-stop expression, or it improperly pre-blocks a legitimately distinct
isolated-margin/stop-overlay pre-registration.

### 4. Fable MAJOR — funding exclusion mis-described; E1 lost the 8 newest events → **VALID**
Evidence (reconciler replication): E2−E1 = exactly the 8 newest events; per-leg
diagnostics show vision fundingRate series end 7–8 days before removal MID-MONTH
(CHESS funding ends 2026-02-06 inside a published February dump, removal 2026-02-13)
while klines continue to removal+2d — so the md's stated reason ("includes all
July-2026 windows — vision publishes fundingRate monthly") is not the operative
mechanism; the operative mechanism is a systematic final-week funding gap on
delisting-bound perps (suspension vs truncated dumps: locally undecidable). Local
funding CSVs rescued 0 legs (confirmed: `funding_source_counts.local_csv = 0`).
Consequences: (a) E1's adverse mean is computed on a sample that excludes the entire
post-Nov-2025 regime — including the report's own "recent squeeze regime" examples
CHESS/RDNT/MLN, which appear only in E2/E3; (b) "structural ceiling / nothing available
today" is overstated — confirming funding suspension (rate 0 would then be the TRUE
value, not a guess) or a REST funding backfill could lift E1 to ~25 events today
(still < 30; verdict unchanged). The exclusion itself was protocol-compliant
(never guess funding).

## Adjudications — MINOR findings (brief)

- **Opus: label softer than evidence (record the maxDD breach)** → PARTIALLY-VALID,
  resolved jointly with Fable M1: the breach IS real for the registered expression and
  should be recorded, but scoped — see dissent resolution below.
- **Opus: E2/E3 optimistic survivorship** → VALID but pre-disclosed in the registration
  (E1 declared the only verdict-bearing arm); no action beyond the existing disclosure.
- **Fable: 2b "cost-free sensitivity" mislabeled** → VALID, verified arithmetically:
  the quoted −1.5/−7.9/−19.7% are the half_spread=0 arm which still charges 30 bps
  fees+slip; true zero-cost means are −1.24/−7.64/−19.39% (JSON means + 0.0030 exactly).
  All still negative — conclusion holds, label must be corrected.
- **Fable: 2b exclusions not "mostly pre-2024" / covered sample single-regime-2026** →
  VALID per cache tally (22 of 26 servable legs are 2026 events); direction only
  strengthens the adverse 2b read.
- **Fable: perp_data_end exit proxy (mild look-ahead) + entry-lag not uniformly
  conservative (amplifies tail multiple)** → PLAUSIBLE-VALID, immaterial to the n<30
  disposition; the "strictly conservative" entry-lag language should not be reused.
- **Sonnet: 03:00-UTC removal-hour unverified** → NOTED, self-flagged by the screener,
  immaterial (affects only E1's target timestamp by hours over a multi-day hold).

## Material dissents (verbatim)

Opus (too_lenient): *"the capital-preservation breach is already demonstrable from
resolved events (E1 realized concurrent-MTM maxDD 0.81, E3 0.65; MC maxDD_p95 1.34/1.32
vs the 0.25 gate), driven by a single ALPACA-class squeeze, so the family is fatally
capital-unsafe regardless of the n<30 floor."*

Fable auditor (unfair_to_strategy): *"Under the realistic isolated-margin 1x expression
the tail is mechanically capped at the stake, and the adverse read largely evaporates
on point estimates. … any pipeline-log/ledger record of the squeeze-tail finding MUST
scope 'tail fatal' to the registered cross-margin no-stop expression, or it will
improperly pre-block a legitimately distinct isolated-margin/stop-overlay
pre-registration."*

**Resolution:** both are right about different objects. The breach is real and must be
recorded — FOR the registered expression (cross-margined unlevered short, no stop, no
liquidation modeling, the literal scout hypothesis). The generalization to "any sizing/
any expression" is falsified by the liquidation-cap re-run and must be dropped. The
record therefore says: *"squeeze tail fatal for the registered cross-margin no-stop
expression (MC maxDD p95 1.34 vs 0.25 gate; ALPACA −22.4× stake); an isolated-margin or
stop-overlay expression is NOT pre-blocked but requires a NEW pre-registration with
liquidation/fill realism inside squeezes (touch ≠ fill), and E2-style point estimates
under liquidation capping still fail mean-vs-control and DSR."*

---

## FINAL STATUS: **CONFIRMED_INSUFFICIENT_DATA** (both sub-claims)

Reasoning:
- All three auditors independently affirm the verdict; the reconciler's offline re-run
  reproduces the JSON exactly; every gated variant sits below the frozen 30-event floor
  (2a: 17/25/18; 2b: 11/11/11). The floor was pre-registered; forcing an upgrade to
  NO_GO on sub-floor point estimates would itself be a post-hoc verdict-definition
  change — exactly the class of move this pipeline exists to prevent.
- UPGRADED_NO_GO is additionally blocked on the merits: the strongest adverse statistic
  (maxDD breach) is expression-scoped (VALID Fable M1), and the most adverse arm (E1)
  is computed on a sample missing the entire post-Nov-2025 regime (VALID Fable M2) —
  adverse, but not "decisive at any honest sizing."
- OVERTURNED_RESCREEN is not warranted: no VALID finding breaks the screen's
  computation (reproduction exact, costs honestly charged, exclusions
  protocol-compliant); the VALID findings break *narrative claims in the record*, which
  are corrected here, and a rescreen today cannot manufacture events.
- Conservative default satisfied: INSUFFICIENT_DATA advances nothing. No shadow probe,
  no ledger NO_GO row (verdict is not NO_GO), no integration.

**Binding record corrections (this document is the correction of record):**
1. 2b was NOT survivorship-bounded — Bitget censors its own delisted symbols (40309);
   the n=11 sample is survivor-conditioned and the measured bounce is plausibly
   flattered upward (still uniformly negative).
2. Squeeze-tail finding re-scoped per the dissent resolution above.
3. E1 funding exclusions re-attributed to the final-week funding gap on
   delisting-bound perps; E1 excludes the 8 newest events; "nothing available today"
   softened to "a funding-suspension confirmation or REST backfill could lift E1 to
   ~25 events (still < 30)".
4. 2b "cost-free" sensitivity relabeled (half_spread=0 arm still charges 30 bps);
   true zero-cost means −1.24/−7.64/−19.39%.

**Forward-accrual path / what would reopen:**
- Forward accrual ~8–12 qualifying events/yr → 30-event floor reachable in ~1–2 years
  for 2a-E2-class arms; E1 additionally needs the final-week funding question resolved
  (confirm suspension → funding 0 is the true value, or REST backfill) — queue with the
  existing funding-history backfill tooling (`scripts/backfill_funding_history.py`).
- A NEW pre-registration for an isolated-margin 1× or stop-overlay delisting-short
  expression is legitimate (not pre-blocked), and must model liquidation mechanics and
  fill realism inside squeezes; note from this debate's re-run that even then E2 point
  estimates fail mean-vs-control and DSR.
- 2b can only be extended by non-bot venues (MEXC/Gate archives) — evidentially weak
  for our expression; forward accrual otherwise.
- **Process precedent (A1):** commit window amendments before observing even
  preliminary event counts.

*Reconciler: Fable 5, 2026-07-17. Verification scripts: session scratchpad
(read-only; cache untouched, no live-path files modified).*
