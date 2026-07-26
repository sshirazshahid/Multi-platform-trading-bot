# 18 — Fable (honesty-auditor) Verdicts — 2026-07-22

Role: Fable voice in the dual-model Phase-3 adjudication (context: `18_context_2026-07-22.md`).
Independence attestation: `18_verdicts_codex.md` was NOT read. Inputs consumed: `18_scout_codex.md`,
`18_pair_dossier.md`, `18_pair_dossier.json`, `.claude/skills/refuted-families-ledger/SKILL.md`,
plus two targeted code cross-checks (`config.py` F3 gate, `tests/test_f3_profile_gated_research_knobs.py`).

---

## PART 1 — Adversarial evaluation of Codex's research (18_scout_codex.md)

### C1 — CFTC asset-manager options-position pressure → BTC perp weekly hold (Shen/Li/Luo, FRL 2026)

**Verdict: SCREEN** — narrowly, with the hardened pre-registration below. Expectation NO_GO.

Attacks, strongest first:

1. **Delta-drift may BE the signal (sharpest attack, not raised by Codex).** The options-only position
   is derived by subtracting the futures-only report from the futures-and-options-COMBINED report, and
   the combined report folds options in on a CFTC **delta-adjusted** basis. When BTC rallies, call deltas
   rise and the delta-adjusted "position" of an unchanged option book grows with zero trading. This
   mechanically makes returns lead position changes — which is exactly what the authors' own Granger
   tests find — and injects lagged-return content into the signal BY CONSTRUCTION. If the paper's
   "non-momentum residualization" does not explicitly strip contemporaneous-return delta drift, the
   residual is a repackaged lagged-return factor, i.e. the ledger's refuted formulaic-alpha family.
   Binding prereg kill: residualize against contemporaneous AND 1–8-week lagged BTC returns as the null;
   the signal must beat that null or it is dropped under the existing ledger rows.
2. **The tradeable claim is well-formed ONLY because of horizon selection — and that is suspicious.**
   Positions are as-of Tuesday, released Friday 15:30 ET. Week t+1 (Tue→Tue) is ~60% elapsed at release —
   untradeable. Week t+2 starts 4 days post-release — tradeable. So the paper's second-week-ahead result
   is conveniently the first tradeable horizon. Mechanistically, hedging-pressure absorption should decay
   from the position date, not skip a week and peak in week two. A horizon that skips the untradeable
   week is a selected-horizon red flag in a letters-format paper with no visible DSR/FDR control (Codex's
   own flag). Prereg must measure t+1, t+2, t+3 jointly; a t+2-only result with dead neighbors is treated
   as horizon-mined unless the paper's mechanism can explain the gap.
3. **Structural regime break mid-sample.** The paper's window (2020-01→2025-03) straddles the Jan-2024
   spot-ETF launch, after which CME asset-manager futures positioning became dominated by delta-neutral
   ETF basis shorts — the meaning of "asset-manager hedging pressure" changed mid-sample. The local
   untouched OOS window (2025-03→now, ~16 months, ~70 weekly observations) is entirely post-break. The
   screen must report the pre/post-2024 split; single-regime profit is a known binding-caveat pattern
   (unlock-short precedent), and the high-downside-risk-state conditioning shrinks effective n well below
   70 — if the conditioned cell has n<30 the conditioned claim is INSUFFICIENT_DATA, not GO.
4. **Codex's fail-closed rules are necessary but incomplete.** The lag rule and the lagged-return rule
   are right; add (i) actual historical CFTC release timestamps, not assumed Fridays (holiday delays occur
   several times/year — Codex mentioned holidays, credit given), (ii) archived-vintage == published-vintage
   assumption stated explicitly (CFTC occasionally corrects reports), (iii) correct report family (TFF, not
   the disaggregated commodity report), (iv) prereg commit+hash BEFORE the run per the 2026-07-17 process rule.
5. **Why still SCREEN and not DROP:** the data is genuinely novel to the ledger (regulated trader-class
   positioning — no refuted row covers it), CFTC archives are freely harvestable, the local BTC 1h parquet
   + 3-venue funding CSVs cover the full hold window (BTC row: 1149d OHLCV, 2022/2022/41d funding), the
   paper is peer-reviewed 2026 with claimed OOS — which meets the reopen-bar *form* for a SCREEN (never a
   build) — and the screen is cheap. Cost realism at $420 is survivable at 1× (11–12 bps taker round trip
   + ~21 funding settlements/week ≈ 30–40 bps all-in vs a weekly signal): the screen charges it all.

### C2 — Deribit negative-gamma expiry reversal (Weiss et al. FRL 2026, contradicted by Lachowicz 2025)

**Verdict: DATA_GATED** — record in the ledger's Open/INSUFFICIENT_DATA class; nothing is screenable today.

1. **The conditioning variables cannot be constructed.** Historical Deribit chains (strike/expiry/OI/Greeks)
   are not freely available — Codex concedes this. Without them, ATM-OI and cumulative dealer gamma do not
   exist for any historical date. "Request the authors' data" is not an executable plan; forward collection
   at maybe 12–20 qualifying events/year puts ≥30 conditioned events ~1.5–2.5 years out — the same class as
   the delisting forced-flow Open row.
2. **The only test runnable today is a refuted family.** An unconditioned post-08:00-UTC reversal test on the
   local 15m parquet (provenance itself unconfirmed, per Codex) is hour-of-day seasonality — refuted 2026-06-02,
   0 survivors OOS. It must NOT be run as a substitute; a positive would be uninterpretable and a negative
   would say nothing about the conditioned claim.
3. **Dealer-gamma sign conventions are a researcher degree of freedom.** "Dealer" positioning on Deribit is
   unobservable; multiple GEX conventions exist and picking the one that works is multiplicity. The accessible
   text does not disclose the convention (Codex's own admission), so it cannot even be frozen yet. Forward
   collection, if ever started, must freeze convention + ATM cutoff BEFORE the first observed outcome.
4. **The literature is in direct contradiction, and the sizes agree with the skeptic.** Lachowicz 2025 puts BTC
   gamma exposure at 0.025% of daily option volume — too small to move spot. Weiss et al.'s own ~$50M/yr total
   wealth-transfer estimate, spread across all expiries against BTC's tens of billions in daily volume, implies
   per-event effects in the low bps — below the ~10–12 bps retail taker round trip. Even if real, this looks
   harvestable by market makers inside the 07:30–08:00 settlement window, not by a 5-minute-cycle retail bot
   entering after 08:00.

Reopen condition (write into the ledger if adopted): a timestamped historical Deribit chain archive with OI+Greeks,
OR ≥30 forward-collected qualifying expiry events under a pre-frozen GEX convention — then a standard prereg screen.

---

## PART 2 — Per-pair program-fitness verdicts (44 pairs)

**Binding frame:** no pair has directional EDGE (ledger; ~2,400+ refuted tests; band lane is WR-geometry,
expectancy last measured ≈ −0.24R). These are fitness verdicts for the aggressive-PAPER band lane only.

### Dossier-level audit findings (condition every verdict below)

- **A1 — The band lane is NOT currently running in band configuration.** Heartbeat profile at dossier
  generation is `AGGRESSIVE_RESEARCH` (epoch 2026-07-21T20:24:08Z). Cross-check against code:
  `config.py:568/619` gate the accuracy-band geometry and F3 research knobs to `PAPER + MAX_FLOW_BAND`
  ONLY, and `tests/test_f3_profile_gated_research_knobs.py:22` pins `g("PAPER","AGGRESSIVE_RESEARCH") is False`.
  Under the current profile the band TP-compression geometry, entry-floor 50, SL-cooldown disable, and the
  econ-gate paper_fallback are all OFF (fallback reversion likely re-blocks entries with
  `economic_gate_model_missing`). Every FIT_* verdict below is **conditional on restoring
  PAPER+MAX_FLOW_BAND (or an owner-approved gate extension)** — as of generation time there is no band
  cohort accruing, which is also why the profile cohort is empty for all 44 pairs.
- **A2 — Band cache is binance-only (14,551/14,555), frac 0.35, one 12-day window (2026-07-12 screen).**
  Per-pair band WRs are single-venue, single-window estimates. Screen-13's global verdict stands: every
  bucket after-cost NEGATIVE.
- **A3 — 44-pair order statistics.** With 44 pairs at n≈70–1,583, the extremes (ETC 0.839 high, SOL 0.127
  low) are partly expected extremes of a 44-draw sample. ETC's meanR +0.112 — the only positive in the
  table — is a 1-of-44 selection on a binance-only 12-day cache and must NOT be read as edge; any use of it
  is a NEW prereg screen. Symmetrically, per-pair frac retunes fitted to these observed WRs would be
  in-sample tuning — retune maps must be pre-registered and validated on FORWARD accrual only.
- **A4 — Warehouse all-time WR/PnL aggregates all historical engines** (honest all-history measure, not a
  current-lane claim); recent_14d is the only recency evidence, and it is thin (0–21 trades/pair).

### Decision rules applied (stated so Codex can attack them)

- **COST_UNFIT**: move/cost proxy <1.0 AND measured band outcomes absent or consistent with cost dominance.
  BTC (0.98, n=1471 near-band) is deliberately NOT cost-unfit — robust measured outcomes override the proxy;
  the proxy becomes a named gap instead.
- **DATA_STARVED**: no band-cache outcomes AND negligible warehouse history — fitness is not assessable;
  the missing store is named. (Pairs WITH band outcomes but stale/gapped research stores are assessable →
  FIT_WITH_GAPS, because the live lane trades on exchange data, not the parquet.)
- **FIT_WITH_GAPS** also covers robust-n geometry failures (n≥100 and band WR <0.55): the gap is the
  geometry itself, requiring a forward-validated frac/SL retune before further accrual is meaningful.
- **FIT_BAND_PAPER**: full coverage, no structural cost problem, band WR in/near 63–67 at robust or
  explainable n. **EXCLUDE**: none warranted — quarantine list is AXS-only (not in universe); FET's dead
  bybit route is a gap, not an exclusion (binance/bitget routes remain).

### Band-WR outliers vs 63–67 target (geometry-tuning notes, robust n only)

- **High (frac/TP retune candidates):** ETC 0.839/n=112 (+ only positive meanR — see A3), FIL 0.799/n=234,
  INJ 0.764/n=742, UNI 0.755/n=789, DOT 0.742/n=407, TIA 0.697/n=706, AAVE 0.695/n=789.
- **Low (geometry broken or mis-tuned):** SOL 0.127/n=158 (meanR −1.117, worst all-time bleed −$81 —
  halt band accrual on SOL until retuned), SUI 0.392/n=120, VET 0.405/n=294, ADA 0.470/n=298,
  APT 0.503/n=356, OP 0.532/n=402, SEI 0.599/n=394, BTC 0.612/n=1471 (marginal).

### Per-pair table

| pair | verdict | note |
|---|---|---|
| 1INCH | DATA_STARVED | missing: fresh 1h OHLCV (51.6d stale), funding all 3 venues, zero band/warehouse outcomes |
| AAVE | FIT_WITH_GAPS | gap: funding history absent all venues; band 0.695/n=789 top-of-band; recent flow live (n14=15) |
| ADA | FIT_WITH_GAPS | gap: geometry — band 0.470/n=298 well below band; coverage full; forward retune only |
| ALGO | FIT_BAND_PAPER | full coverage; band 0.652/n=141 in band |
| APT | FIT_WITH_GAPS | gap: geometry — band 0.503/n=356 below band; coverage full |
| ARB | FIT_BAND_PAPER | full coverage; band 0.663/n=655 in band; most recent flow (n14=19) |
| ATOM | FIT_BAND_PAPER | full coverage; band 0.622/n=90 just below band, thin n — note only |
| AVAX | FIT_BAND_PAPER | full coverage; band 0.630/n=479 just below band |
| BCH | FIT_WITH_GAPS | gap: funding absent all venues; band 0.638/n=340 in band |
| BNB | COST_UNFIT | move/cost 0.96 AND band 0/11 meanR −1.575 — measured outcomes agree with cost dominance |
| BTC | FIT_WITH_GAPS | gap: cost margin — move/cost 0.98 (median bar < roundtrip); band 0.612/n=1471 marginally below; anchor pair |
| COMP | DATA_STARVED | missing: fresh OHLCV (51.6d stale) + any band/warehouse outcomes; funding OK |
| CRV | DATA_STARVED | missing: fresh OHLCV, funding all venues, zero outcomes |
| DASH | DATA_STARVED | missing: fresh OHLCV, funding all venues, zero outcomes |
| DOT | FIT_WITH_GAPS | gap: funding absent all venues; band 0.742/n=407 above band — outlier-high retune note |
| ENA | DATA_STARVED | missing: fresh OHLCV, funding all venues, zero outcomes |
| ETC | FIT_WITH_GAPS | gap: funding absent; band 0.839/n=112 + sole positive meanR = 1-of-44 order-stat, NOT edge (A3) |
| ETH | FIT_BAND_PAPER | full coverage; largest cache n=1583, 0.660 in band; move/cost 1.16 thin but clear |
| FET | FIT_WITH_GAPS | gap: funding absent + bybit route dead at 07-20 boot (probe skipped) — verify route liveness |
| FIL | FIT_WITH_GAPS | gap: funding absent; band 0.799/n=234 above band; meanR −0.033 near flat |
| GALA | DATA_STARVED | missing: fresh OHLCV, funding all venues, zero outcomes |
| GRT | DATA_STARVED | missing: band outcomes + fresh OHLCV tail; funding OK; warehouse n=5 |
| HBAR | DATA_STARVED | missing: band outcomes; warehouse n=4 (0 wins); funding OK |
| INJ | FIT_BAND_PAPER | full coverage; band 0.764/n=742 above band (retune note); first econ-gate fill pair 07-21; n14=21 |
| JUP | DATA_STARVED | missing: band outcomes; funding OK (891d); warehouse n=16 thin |
| LINK | FIT_BAND_PAPER | full coverage; band 0.646/n=246 in band; −$59.8 all-time is legacy engines, not the lane |
| LTC | FIT_BAND_PAPER | full coverage; band 0.513/n=78 below band but sub-robust n — note only |
| MANA | FIT_WITH_GAPS | gap: funding absent all venues; band 0.678/n=541 at band top |
| NEAR | FIT_WITH_GAPS | gaps: stale OHLCV + funding absent; band 0.650/n=514 in band; zero warehouse trades |
| ONDO | DATA_STARVED | missing: fresh OHLCV + band outcomes; funding OK; best move/cost 3.80 once data lands |
| OP | FIT_WITH_GAPS | gaps: stale OHLCV; band 0.532/n=402 below band (geometry note); funding OK |
| RENDER | FIT_WITH_GAPS | gaps: stale OHLCV + funding absent; band 0.686/n=70 thin |
| SAND | FIT_WITH_GAPS | gaps: stale OHLCV + funding absent; band 0.685/n=257 top-of-band |
| SEI | FIT_WITH_GAPS | gap: stale OHLCV; band 0.599/n=394 below band; funding OK |
| SNX | DATA_STARVED | missing: fresh OHLCV, funding all venues, zero outcomes |
| SOL | FIT_WITH_GAPS | gap: geometry BROKEN — 0.127/n=158, meanR −1.117, worst bleed −$81; halt accrual until forward retune |
| SUI | FIT_WITH_GAPS | gap: geometry — band 0.392/n=120 far below band; coverage full |
| TAO | DATA_STARVED | missing: fresh OHLCV, funding all venues, zero outcomes |
| TIA | FIT_WITH_GAPS | gap: stale OHLCV; band 0.697/n=706 marginally above band; funding OK |
| TRX | COST_UNFIT | move/cost 0.46 — median 1h bar under half the roundtrip cost; structurally unfit at 1h scale |
| UNI | FIT_WITH_GAPS | gaps: stale OHLCV + funding absent; band 0.755/n=789 above band (outlier-high) |
| VET | FIT_WITH_GAPS | gaps: geometry (0.405/n=294 far below band) + stale OHLCV + funding absent |
| XRP | FIT_WITH_GAPS | gap: band evidence n=1 — frac-0.35 cache never sampled XRP; full coverage otherwise |
| ZEC | DATA_STARVED | missing: fresh OHLCV + band outcomes; funding OK; move/cost 3.40 attractive once data lands |

**Distribution:** FIT_BAND_PAPER 8 · FIT_WITH_GAPS 21 · DATA_STARVED 13 · COST_UNFIT 2 · EXCLUDE 0.

### Top disagreement-risk pairs (for the rebuttal round)

1. **BTC** — proxy says cost-unfit (0.98), measured n=1471 says near-band; I ruled measured-over-proxy → FIT_WITH_GAPS.
2. **BNB** — same proxy zone as BTC but n=11 catastrophic outcomes; I ruled COST_UNFIT. The BTC/BNB asymmetry is the rule most worth attacking.
3. **SOL** — FIT_WITH_GAPS(geometry) vs EXCLUDE; I kept it in-program because the failure is tuning, not structure.
4. **ETC** — the positive-meanR temptation; any GO-flavored reading is an A3 multiplicity violation.
5. **XRP** — n=1 band evidence on a full-coverage major; DATA_STARVED would also be defensible (I ruled the missing store is band sampling, not data).
6. **NEAR/TIA/UNI/SEI boundary** — band outcomes exist but research stores are stale; I ruled assessable → FIT_WITH_GAPS, not DATA_STARVED.
7. **FET** — dead bybit route: gap vs exclusion.
8. **A1 profile finding** — if Codex issued unconditional FIT verdicts, they describe a lane that is not currently running in band configuration.

```json
{
  "pairs": {
    "1INCH/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh 1h OHLCV (51.6d stale), funding all 3 venues, zero band/warehouse outcomes"},
    "AAVE/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: funding history absent all venues; band 0.695/n=789 top-of-band; recent flow n14=15"},
    "ADA/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: geometry — band 0.470/n=298 well below 63-67; coverage full; forward retune only"},
    "ALGO/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; band 0.652/n=141 in band"},
    "APT/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: geometry — band 0.503/n=356 below band; coverage full"},
    "ARB/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; band 0.663/n=655 in band; most recent flow n14=19"},
    "ATOM/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; band 0.622/n=90 just below band, thin n — note only"},
    "AVAX/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; band 0.630/n=479 just below band"},
    "BCH/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: funding absent all venues; band 0.638/n=340 in band"},
    "BNB/USDT:USDT": {"verdict": "COST_UNFIT", "note": "move/cost 0.96 AND band 0/11 meanR -1.575 — outcomes agree with cost dominance"},
    "BTC/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: cost margin move/cost 0.98; band 0.612/n=1471 marginally below band; anchor pair"},
    "COMP/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV (51.6d stale) + any band/warehouse outcomes; funding OK"},
    "CRV/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV, funding all venues, zero outcomes"},
    "DASH/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV, funding all venues, zero outcomes"},
    "DOT/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: funding absent all venues; band 0.742/n=407 above band — outlier-high"},
    "ENA/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV, funding all venues, zero outcomes"},
    "ETC/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: funding absent; band 0.839/n=112 + sole positive meanR = 1-of-44 order-stat, NOT edge"},
    "ETH/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; largest cache n=1583, 0.660 in band; move/cost 1.16"},
    "FET/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: funding absent + bybit route dead at 07-20 boot — verify route liveness"},
    "FIL/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: funding absent; band 0.799/n=234 above band; meanR -0.033 near flat"},
    "GALA/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV, funding all venues, zero outcomes"},
    "GRT/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing band outcomes + fresh OHLCV tail; funding OK; warehouse n=5"},
    "HBAR/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing band outcomes; warehouse n=4 (0 wins); funding OK"},
    "INJ/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; band 0.764/n=742 above band (retune note); first econ-gate fill 07-21"},
    "JUP/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing band outcomes; funding OK (891d); warehouse n=16 thin"},
    "LINK/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; band 0.646/n=246 in band; -$59.8 all-time is legacy engines"},
    "LTC/USDT:USDT": {"verdict": "FIT_BAND_PAPER", "note": "Full coverage; band 0.513/n=78 below band but sub-robust n — note only"},
    "MANA/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: funding absent all venues; band 0.678/n=541 at band top"},
    "NEAR/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gaps: stale OHLCV + funding absent; band 0.650/n=514 in band; zero warehouse trades"},
    "ONDO/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV + band outcomes; funding OK; best move/cost 3.80"},
    "OP/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gaps: stale OHLCV; band 0.532/n=402 below band; funding OK"},
    "RENDER/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gaps: stale OHLCV + funding absent; band 0.686/n=70 thin"},
    "SAND/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gaps: stale OHLCV + funding absent; band 0.685/n=257 top-of-band"},
    "SEI/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: stale OHLCV; band 0.599/n=394 below band; funding OK"},
    "SNX/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV, funding all venues, zero outcomes"},
    "SOL/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: geometry BROKEN — 0.127/n=158, meanR -1.117, worst bleed -$81; halt until retune"},
    "SUI/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: geometry — band 0.392/n=120 far below band; coverage full"},
    "TAO/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV, funding all venues, zero outcomes"},
    "TIA/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: stale OHLCV; band 0.697/n=706 marginally above band; funding OK"},
    "TRX/USDT:USDT": {"verdict": "COST_UNFIT", "note": "move/cost 0.46 — median 1h bar under half roundtrip cost; unfit at 1h scale"},
    "UNI/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gaps: stale OHLCV + funding absent; band 0.755/n=789 above band"},
    "VET/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gaps: geometry (0.405/n=294 far below band) + stale OHLCV + funding absent"},
    "XRP/USDT:USDT": {"verdict": "FIT_WITH_GAPS", "note": "Gap: band evidence n=1 — cache never sampled XRP; full coverage otherwise"},
    "ZEC/USDT:USDT": {"verdict": "DATA_STARVED", "note": "Missing fresh OHLCV + band outcomes; funding OK; move/cost 3.40"}
  },
  "codex_c1_verdict": "SCREEN",
  "codex_c2_verdict": "DATA_GATED"
}
```
