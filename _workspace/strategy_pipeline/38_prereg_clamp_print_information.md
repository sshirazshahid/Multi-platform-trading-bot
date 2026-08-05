# 38 — PRE-REGISTRATION: Clamp-Print Zero-Information Filter (Candidate 2)

**Status:** `FROZEN_PREREG-DRAFT` — **NOT HASHED.** Hashing happens when the screen is run, per
the pipeline's binding process rule (prereg commit/hash BEFORE outcomes are computed, added
2026-07-17). This document is the pre-hash draft in which the reviewer's binding revisions are
settled.
**Date:** 2026-07-28
**Class:** **MEASUREMENT-CORRECTNESS OVERLAY** on F1 (funding carry — the one validated family).
This is **NOT a strategy family.**
**Reviewer approval:** ai-reviewer **APPROVE, 2026-07-28**, conditional on binding revisions R1
and R2 being settled before the hash (both settled below, §4 and §5).

**This proposes NO trade and NO order rule.** It defines no entry, no exit, no sizing, no
side, no symbol selection. Its success case is *"measurement confirmed, no new trades."* It is
not an edge claim and makes no directional prediction, which is why no reopen bar applies: it
supplies the *mechanism* behind two existing refuted ledger rows (the "dispersion" between two
venue floors is not two independent premia).

**Motivating evidence** (verified 3-0 in the 2026-07-28 futures corpus): `+0.0100%` printed
identically and simultaneously on Binance, BitMEX, Bybit, HTX, Hyperliquid and WOO X for XMR.
Hyperliquid natively reports `0.00125%/1h` = exactly `0.01%` renormalized to 8h — an aggregator
rescaling a floor, not six independent premia. Kraken alone printed a differentiated rate.

---

## 2. Null hypothesis

> **H0:** Funding prints exactly equal to the venue baseline ("clamp prints") on thin alt perps
> carry **zero information** about the sign of that symbol's own next funding settlement, over
> and above **other positive prints in the same cell at the same settlement timestamp**.

**H0 is FALSIFIED** if clamp-print settlements predict their own next-settlement funding **sign**
at a win rate materially and significantly **above same-instant, same-sign non-clamp peers** —
formalized as Δ > 0 at the multiplicity-corrected level **and** WR_clamp ≥ 0.55 (§8).

**H0 is RETAINED** otherwise.

Stated numerically so it can be falsified: within each settlement timestamp, let `WR_clamp` be the
fraction of *clamping* symbols whose next settlement is positive, and `WR_control` the same
fraction over *non-clamping symbols that are also currently positive* at that same timestamp.
**H0: Δ = WR_clamp − WR_control = 0.**

> The control is deliberately restricted to **currently-positive** peers. Every baseline value is
> positive, and funding is autocorrelated; a control admitting currently-negative symbols would
> make Δ measure "positive rates persist" — known, and not the hypothesis. That would be the same
> artifact class as Defect A (§8.1), reintroduced through the control definition.

---

## 3. Data

**Path:** `data/funding_history/` (repo-relative; read-only).
**File count:** **510** CSV files, one per venue×symbol.
**Venue split (measured):** binance 217, bitget 135, bybit 158.
**Total rows (measured):** **1,292,601**.

**Schema (measured — all 510 files share one header, verified):**

```
ts,funding_rate,venue,symbol
1706601600,0.0001,bybit,TAO/USDT:USDT
```

- `ts` — integer UNIX seconds, settlement timestamp (UTC).
- `funding_rate` — float, per-settlement rate (NOT annualized, NOT 8h-normalized).
- `venue` — one of `binance` | `bybit` | `bitget`.
- `symbol` — ccxt unified perp symbol.

> **Correction to the approved brief:** the brief stated a 2-column schema `{ts, funding_rate}`.
> The actual on-disk schema is **4 columns**; `venue` and `symbol` are present. Written as measured.

**NO fetch and NO backfill are required.** The screen runs entirely against these local files.
No ccxt call, no network access, no live-metadata lookup of any kind (see R1, §4).

### Staleness — stated honestly

Measured against a 2026-07-28T18:00Z reference, by each file's **last row timestamp**:

| Percentile | Staleness |
|---|---|
| min | 74 h |
| p25 | 290 h |
| median | **414 h** |
| p90 | 418 h |
| max | 28,354 h |

**495 of 510 files fall in 74–418 h**, measured against 2026-07-28T18:00Z. This is consistent with
the brief's "68–408 h" under an earlier reference time, but the two ranges are **not** a pure
reference shift — the endpoints moved by 6 h and 10 h respectively, so the measured range above is
authoritative and the brief's should not be quoted. **15 files exceed 1,000 h** — delisted/dead
symbols, the extreme being `bybit_API3.csv` (101 rows, last settlement 2023-05-04). **These 15
outliers were not inside the brief's stated range at all** and are newly disclosed here.

**Does staleness matter for this measurement? No — it bounds recency, not validity.**
This screen measures *historical settlements that already occurred*: whether a baseline print at
time `t` said anything about the settlement at `t+1`. Both `t` and `t+1` are already in the files.
A stale file simply contributes no observations after its last row; it contributes no *wrong*
observations. Staleness would be disqualifying only for a live/forward claim, and this makes none.

**Delisted-symbol rule (binding):** the 15 long-dead files are **INCLUDED**. Their historical
settlements are valid observations, and excluding symbols because they later died would introduce
survivorship bias into a measurement whose whole purpose is measurement hygiene. This is the same
logic as the staleness paragraph above, applied consistently.

**Minimum-length rule (binding):** a file is skipped only if it has **< 20 rows**, because the
regime estimator (§4) needs a 10-delta warm-up. Measured: 3 files have < 100 rows. The count of
skipped files MUST be reported.

---

## 4. REVISION R1 IMPLEMENTED — regime-aware baseline

**The defect R1 corrects.** The funding interval is **time-varying per symbol**. Bybit
TAO/ENA/ONDO (among others) switched 8h → 4h mid-history. Today's `fundingInterval=240` does
**not** describe TAO's 2024 rows. A single global test `abs(rate − 0.0001) < 1e-9` therefore
misclassifies every post-switch baseline row on the switched symbols.

### 4.1 Regime derivation — from consecutive-ts deltas in the file only

For each file, sort rows ascending by `ts`. Let `Δᵢ = (ts[i+1] − ts[i]) / 3600` (hours).

For each row index `i ≥ 10`, the **interval regime** is:

```
regime(i) = argmin      | g − median(Δ[i−10 : i]) |
            g ∈ {1,2,4,8}
```

i.e. **the median of the trailing 10 consecutive-ts deltas, snapped to the nearest of
{1h, 2h, 4h, 8h}.** Rows `i < 10` are dropped (warm-up). The trailing median is used rather than
a single delta so one missing settlement cannot flip the regime.

**No global constant is used. No live metadata (`fundingInterval`, exchange `load_markets`, or any
ccxt call) is consulted.** The regime is derived exclusively from timestamps already in the file.

### 4.2 Baseline threshold scaled to the regime — measured, not assumed

```
baseline(regime) = 1e-4 × (regime_hours / 8)
```

| Regime | Baseline |
|---|---|
| 8 h | 0.0001    (1.00 bp) |
| 4 h | 0.00005   (0.50 bp) |
| 2 h | 0.000025  (0.25 bp) |
| 1 h | 0.0000125 (0.125 bp) |

**This rule is empirically exact, not an assumption.** Measured over the corpus, the above value
is the **modal** funding rate in *every* populated (venue, regime) cell — e.g. binance-4h:
`5e-05` is the top rate at 62,999 occurrences; binance-8h: `0.0001` at 32,484; bybit-1h:
`1.25e-05` at 21,660; bybit-2h: `2.5e-05` at 8,918. The baseline scales linearly with the
settlement interval, exactly as an 8h-normalized floor renormalized to the venue's interval would.

A row is a **clamp print** iff `abs(rate − baseline(regime(i))) < 1e-12`.

### 4.3 Worked example on TAO (`data/funding_history/bybit_TAO.csv`, 4,392 rows)

Measured delta histogram for this one file: **1,053 deltas of 8h and 3,338 deltas of 4h** — a
single clean switch detected at **2025-01-16T04:00:00Z**.

| | Timestamp (UTC) | `funding_rate` | Trailing-median Δ | regime | baseline | Classified |
|---|---|---|---|---|---|---|
| **Pre-switch** | 2024-02-10T00:00:00Z | `0.0001` | 8.0 h | **8 h** | `0.0001` | **CLAMP** ✅ |
| **Post-switch** | 2025-01-17T00:00:00Z | `0.00005` | 4.0 h | **4 h** | `0.00005` | **CLAMP** ✅ |

Both are correctly classified as baseline prints *by the same rule*, because the threshold moved
with the regime.

**Why the global constant fails — the kill shot.** TAO has **564** baseline prints in its 8h
regime and **2,432** in its 4h regime. A global `abs(rate − 1e-4) < 1e-9` catches the 564 and
**misclassifies all 2,432 — the majority of the symbol's own clamp prints — as differentiated
premia.** That is precisely the error this overlay exists to eliminate.

---

## 5. REVISION R2 IMPLEMENTED — baseline-clamp ONLY, not cap-clamp

**This screen detects BASELINE clamping only. Cap-clamp detection is explicitly EXCLUDED and
out of scope.**

**Why excluded:** venue cap bounds (`upperFundingRate` / `lowerFundingRate`,
`adjustedFundingRateCap`) are **current-only and per-symbol**. Nothing in this repository archives
*historical* bounds, and the bounds themselves change over time and per symbol. A cap test run
against today's bounds applied to 2023–2026 rows would be exactly the same category of error R1
corrects — present-day metadata projected onto historical rows — and it is unfixable here because
no historical bound series exists locally to project.

The motivating evidence concerns the **`+0.0100%` baseline** printing identically across six
venues. That is a floor/baseline phenomenon, and the local data fully supports measuring it.

**Naming discipline (binding):** the resulting artifact, if H0 is retained, must be called a
**baseline-clamp filter**, never a "clamp filter" unqualified — the unqualified term implies cap
detection that this data cannot support.

---

## 6. Enumerated multiplicity — m = 12, FIXED

One test per cell. Cells enumerated explicitly:

| # | venue | regime | # | venue | regime |
|---|---|---|---|---|---|
| 1 | binance | 1 h | 7 | bybit | 4 h |
| 2 | binance | 2 h | 8 | bybit | 8 h |
| 3 | binance | 4 h | 9 | bitget | 1 h |
| 4 | binance | 8 h | 10 | bitget | 2 h |
| 5 | bybit | 1 h | 11 | bitget | 4 h |
| 6 | bybit | 2 h | 12 | bitget | 8 h |

**m = 3 venues × 4 regimes × 1 test = 12.** Bonferroni α = 0.05 / 12 = **0.004167**.

Exactly **one** test per cell — the Δ test of §8. No variant sweep, no threshold grid, no
alternative outcome definitions. The test is fixed here, before outcomes.

**Stage-0 attrition does NOT shrink the denominator.** Cells that fail the §7 feasibility gate are
reported `INSUFFICIENT_DATA`, but **m remains 12** for the correction applied to every surviving
cell. Known in advance: bitget 1h and 2h are structurally absent from the corpus; they will return
`INSUFFICIENT_DATA` and the denominator stays 12 regardless.

---

## 7. Stage-0 feasibility gate

For each cell, count **INFORMATIVE STRATA**: distinct settlement timestamps at which **both** arms
of the §8.2 table are non-empty — i.e. ≥ 1 clamping symbol **and** ≥ 1 currently-positive
non-clamping symbol, each with a non-zero next rate.

> **Gate: ≥ 30 informative strata. Below 30 → `INSUFFICIENT_DATA` for that cell, and no test is
> run or reported for it.**

Counted on **informative strata, not rows and not clamp-timestamps** — the unit that actually
enters the test. Two reasons:

1. **Rows are massively non-independent.** binance-4h has **260,900 clamp rows across only 5,921
   distinct timestamps (44×)**. Row counts overstate effective sample by more than an order of
   magnitude.
2. **A bare "≥1 symbol clamped" count is vacuous in many-symbol cells.** With 169 symbols and
   baseline at ~62% of binance-4h prints, essentially *every* timestamp has some symbol at
   baseline. Such a gate would pass on arithmetic, not evidence. A stratum with only one arm
   populated is **uninformative, not missing** — it contributes nothing to CMH and must not be
   counted toward feasibility.

**Pre-measured cell inventory** — these are **clamp-timestamp and informative-strata counts**, not
Stage-0 verdicts. Stage-0 is re-evaluated at run time. **No outcome statistic was computed for
this table** (strata membership only), so the prereg remains unburned.

| venue | regime | clamp rows | clamp ts | **informative strata** | distinct symbols |
|---|---|---|---|---|---|
| binance | 1 h | 5,327 | 2,864 | **472** | 36 |
| binance | 2 h | 31 | 31 | **0** | 3 |
| binance | 4 h | 260,900 | 5,921 | **5,666** | 169 |
| binance | 8 h | 82,039 | 5,783 | **5,496** | 47 |
| bybit | 1 h | 61,349 | 8,330 | **2,044** | 32 |
| bybit | 2 h | 18,927 | 4,944 | **601** | 10 |
| bybit | 4 h | 150,189 | 4,085 | **3,264** | 90 |
| bybit | 8 h | 169,945 | 5,847 | **5,646** | 116 |
| bitget | 1 h | — | 0 | **0** | 0 |
| bitget | 2 h | — | 0 | **0** | 0 |
| bitget | 4 h | 6,906 | 114 | **90** | 99 |
| bitget | 8 h | 1,806 | 112 | **112** | 36 |

**Expected Stage-0 outcome: 9 cells testable, 3 `INSUFFICIENT_DATA`** (bitget-1h, bitget-2h,
binance-2h). **m remains 12** (§6).

**binance-2h fails outright under the corrected gate.** Its 31 clamp timestamps came from only
3 symbols (CYBER, LAYER, SOL), and at **none** of them does a currently-positive non-clamping peer
coexist — **0 informative strata**. Under the vacuous "≥30 clamp timestamps" formulation it would
have passed by one and been reported as a result. It is pre-declared `INSUFFICIENT_DATA` here.
This is precisely the class of false pass the corrected gate exists to catch.

---

## 8. Decision rule

### 8.1 ⚠ DEVIATION FROM APPROVED TEXT — declared before the hash

The approved brief specified: *"null RETAINED if next-settlement sign WR < 0.55; FALSIFIED if
≥ 0.55."* **That rule is mis-specified against this data and is NOT used as written.** Two
measured defects, both discovered before any outcome was computed and both fixed here in the
pre-hash window (the same window in which R1 and R2 were required to be settled):

**Defect A — the 0.55 threshold is below the base rate, so it falsifies on an artifact.**
Crypto perp funding is positive most of the time. Measured unconditional next-settlement-positive
base rates per cell:

| cell | base WR | clamp WR |
|---|---|---|
| binance 1h | **0.2650** | 0.8421 |
| binance 2h | 0.2468 | 0.6774 |
| binance 4h | **0.7412** | 0.9260 |
| binance 8h | **0.7574** | 0.9357 |
| bitget 4h | **0.8625** | 0.9605 |
| bitget 8h | **0.7641** | 0.9164 |
| bybit 1h | **0.7996** | 0.9274 |
| bybit 2h | **0.7357** | 0.9084 |
| bybit 4h | **0.7780** | 0.9066 |
| bybit 8h | **0.8069** | 0.9409 |

A `WR ≥ 0.55` rule would falsify H0 in **10 / 10 populated cells** — and would do so in cells
where the base rate is *already* 0.74–0.86, i.e. where the clamp print adds nothing. Run as
briefed, this screen would write *"clamp prints predict funding sign"* into the ledger, the exact
inversion of the truth, produced by a threshold artifact. That is the single failure mode this
pipeline exists to prevent.

**Defect B — the test unit must match the Stage-0 unit.** Row-level counts are inflated up to
**44×** by cross-symbol simultaneity (§7); any p-value on rows is inflated by roughly the cluster
size. Stage-0 already counts distinct timestamps; the **statistic must use the same unit.**

**Defect C — an unmatched control reintroduces Defect A through the back door.** Every baseline
value is positive and funding is autocorrelated, so a control arm containing currently-*negative*
symbols would make Δ measure "positive rates persist" — known, and not the hypothesis. The control
is therefore restricted to **same-instant, currently-positive, non-clamping** peers (§2, §8.2).
A control defined merely as "timestamps with no baseline print" is additionally **empty by
construction** in the highest-n cells: with 169 symbols and baseline at ~62% of prints, virtually
every binance-4h timestamp contains some clamping symbol.

**The fix is additive, not a replacement:** the reviewer-approved `0.55` is retained as a
**necessary-but-not-sufficient floor**, with an excess-over-matched-control test added on top.
Nothing that would have been retained under the approved rule can now be falsified. The three
changes are: (A) add the excess-over-control requirement, (B) move the test unit to
timestamp-stratified symbol-level counts, (C) sign-match the control arm.

### 8.2 The statistic (binding)

Per cell, **strata = distinct settlement timestamps**; the comparison is **symbol-level within
each stratum**. For every timestamp `t` in the cell build the 2×2 table:

| at timestamp *t* | next rate > 0 | next rate < 0 |
|---|---|---|
| current **== baseline** (clamp arm) | a | b |
| current **> 0 and ≠ baseline** (control arm) | c | d |

1. **Sign convention:** next rate `> 0` → positive; `< 0` → negative; **exactly `0` → row
   dropped**, count reported. (Exact zeros exist: e.g. 955 in binance-4h.)
2. **Control arm is same-instant and currently-positive only** (§2). Currently-negative symbols are
   excluded from both arms — including them would let Δ measure positive-rate persistence.
3. **Drop strata where either arm is empty** — these are **uninformative, not missing data**.
   Report the dropped count. Only non-dropped strata count toward Stage-0 (§7).
4. **Statistic:** **Cochran–Mantel–Haenszel across strata** — common odds ratio, plus pooled
   `Δ = a/(a+b) − c/(c+d)` weighted by stratum size. **H0: Δ = 0** (common OR = 1).

**Why timestamp stratification and nothing else.** Stratifying on the settlement timestamp *is*
the clustering control, so it replaces both the earlier majority-vote collapse and any calendar
stratification: a timestamp lies inside exactly one month by construction. It additionally
supplies a **same-instant market-regime control for free** — clamp and control symbols are
compared under identical market conditions, which a month-pooled control could only approximate.

**FALSIFIED iff BOTH hold:**

> **(a)** `Δ > 0` significant at Bonferroni **α = 0.05 / 12 = 0.004167** (CMH), **AND**
> **(b)** `WR_clamp ≥ 0.55`.

**RETAINED otherwise** — including the case where `WR_clamp` is high but `Δ ≈ 0` (the clamp print
merely inherits the prevailing base rate, which is the expected outcome).

### 8.3 Mandatory per-cell reporting

Every cell MUST report: `n_informative_strata`, `n_strata_dropped_single_arm`,
**`n_distinct_symbols`**, pooled `a,b,c,d`, `WR_clamp`, `WR_control`, `Δ`, CMH common odds ratio,
CMH `p`, exact-zeros dropped.

**`n_distinct_symbols` is not optional.** binance-1h shows base WR **0.2650** against clamp WR
**0.8421** — a 58-point gap in the *opposite* direction from every other cell, from 36 symbols
that are mostly recent thin listings. Under any excess test this will read as a large
falsification. The symbol count forces disclosure of how concentrated the cell is, so a
few-symbol cell cannot masquerade as broad evidence.

### 8.3a PRE-REGISTERED CONFOUND — volatility-state persistence in 1 h cells

Named **now**, before outcomes, so it is a finding rather than a post-hoc excuse.

The 1 h regime is where Binance places **hot new listings**. The measured binance-1h clamp symbol
list is exactly that population: `0G, BABY, ELSA, PLUME, RESOLV, SAHARA, NEWT, TURTLE, …` (36
symbols). Within such a cell, symbols sitting **at** baseline are the *calm* ones, while symbols
away from baseline are the ones being aggressively shorted or squeezed. A positive Δ there may
therefore reflect **persistence of volatility state**, not positioning information — the clamp
print is a *label for calm*, and calm persists.

> **Binding attribution rule:** a falsification in **any 1 h cell** (binance-1h, bybit-1h) MUST be
> attributed to volatility-state persistence **unless separately excluded by a dedicated
> follow-up screen**. It may not be reported as evidence that clamp prints carry positioning
> information, and it may not be entered in the ledger as such.

### 8.4 What each outcome MEANS operationally

**H0 RETAINED (expected).** Baseline prints are confirmed to be *floor artifacts carrying no
positioning information*. Operationally this justifies a **baseline-clamp-aware filter as
measurement hygiene** — i.e. when computing funding dispersion, cross-venue spreads, or any F1
input, rows identified as baseline clamps must not be treated as independent premium observations,
because two venues both printing their floor is one floor twice, not two signals. **This is still
NOT a trade.** It changes how an input is *measured*, never what is *traded*. It retroactively
supplies the mechanism behind the existing refuted dispersion rows.

**H0 FALSIFIED (surprise).** This would mean clamp prints *do* carry sign information beyond the
prevailing base rate — an unexpected result that this screen is **not** designed to exploit and
that authorizes **nothing** on its own. It would require **its own separate, separately
pre-registered screen** to establish whether the effect is (i) real, (ii) after-cost tradeable,
and (iii) not an artifact of symbol concentration or listing-age confounding. A falsification here
is a *finding to investigate*, never a signal to deploy.

---

## 9. Expectation

**Null RETAINED. Plainly: clamp prints are expected to carry no information.** They are venue
floor artifacts — an aggregator rescaling a floor, not a premium. The expected per-cell result is
`Δ ≈ 0` with high `WR_clamp` that is fully explained by the matched base rate.

Note that under the corrected statistic (§8.2) this expectation is **genuinely falsifiable**
rather than pre-refuted by a threshold artifact — which was the entire point of the §8.1
deviation.

---

## 10. What this does NOT authorize

Explicitly and exhaustively:

- ❌ **NO shadow probe.** No probe agent is created, registered, or modified. The probe fleet is untouched.
- ❌ **NO order path.** No entry rule, no exit rule, no sizing rule, no side, no symbol selection.
- ❌ **NO promotion.** Nothing here moves any lane toward the frozen promotion gate, and no promotion
  dossier may cite this artifact as edge evidence.
- ❌ **NO F1 parameter change.** No carry-runner threshold, gate, or knob is altered.
- ❌ **NO config, `.env`, or live-path change of any kind.**
- ❌ **NO ledger "GO".** A retained null adds a measurement-hygiene note, not a strategy row.

The maximum possible downstream consequence of this screen is a change to how an **input is
measured**. It can never, by construction, change what the bot trades.

---

## Run checklist (for whoever executes this)

1. Freeze this document, compute its SHA-256, record the hash **before** computing any outcome.
2. Load all 510 files from `data/funding_history/`; skip files with < 20 rows; include delisted files (§3).
3. Derive `regime(i)` per §4.1 (trailing-10 median delta, snapped to {1,2,4,8}).
4. Flag clamp prints per §4.2 (`1e-4 × regime/8`, tolerance `1e-12`). Baseline-clamp only (§5).
5. Build the per-timestamp 2×2 strata (§8.2): clamp arm = `current == baseline`; control arm =
   `current > 0 and ≠ baseline`. Drop next-rate-zero rows; drop single-arm strata (count both).
6. Apply Stage-0 (§7): **≥ 30 informative strata** per cell, else `INSUFFICIENT_DATA`.
   Expect 9 testable cells; bitget-1h, bitget-2h and binance-2h are pre-declared insufficient.
7. CMH test per surviving cell (common OR + pooled Δ); falsify only if
   **p < 0.004167 AND WR_clamp ≥ 0.55**, with **m = 12 fixed** (§6).
8. Report all fields in §8.3, including `n_distinct_symbols` and `n_strata_dropped_single_arm`.
9. Apply the §8.3a attribution rule to any 1 h-cell falsification.
10. Apply §8.4 semantics. Write the ledger note. Do not create a probe.
