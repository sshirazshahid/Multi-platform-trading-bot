# 35 — Bull Thesis: VPIN jump-risk veto (`vpin_jump_veto_v1`)

**Author:** bull-researcher (investment committee)
**Date:** 2026-07-25
**Candidate:** `vpin_jump_veto_v1` — veto overlay on AccBand PAPER entries, BTC/ETH Binance USDT-M. NOT directional. NOT a live install.
**Committee question:** proceed with the pre-registered screen (harvest → after-cost ΔEV) vs park/defer.
**Ledger check (done first):** family is ADJACENT — no refuted row covers the veto expression. Directional VPIN is anchored-adverse (STOP), formulaic alphas / QH-imbalance / band-geometry *positive* selection are REFUTED; none of those verdicts are reopened by this candidate, and this thesis does not rely on them. Negative-selection band-lane vetoes have a shipped precedent (`BAND_REGIME_FILTER_ENABLED`, ledger row 2026-07-12).

---

## Thesis

Run the frozen screen now: the VPIN jump-risk veto is the queue's best-scoped candidate because it aims the only mechanism class this program has ever shipped (band-lane negative selection) at the bot's largest measured loss channel (≈ −0.24R/trade after-cost band bleed), using peer-reviewed 2026 evidence that VPIN predicts precisely the loss mode (jumps/toxicity) that dominates the lane's asymmetric loss tail — at near-zero incremental cost, on free self-harvested data, with a prereg already frozen and hashed, and with bounded downside (worst case = a NO_GO ledger row that permanently retires the family cheaply).

This is a bull case for **information value**, not for edge: the prereg's own expectation is NO_GO, and no screen result exists yet. The claim defended here is that running the screen today strictly dominates parking it.

## Evidence

### FACT (timestamped, sourced)

1. **Prereg is frozen and hashed before any outcome computation** (2026-07-24): treatment = veto AccBand OPEN when VPIN_t > θ ∈ {0.55, 0.60, 0.65, 0.70}; primary metric = after-cost Δ mean R (veto − baseline); Holm/Bonferroni on n_trials=4; hard gates ΔEV>0 OOS, MC P(total>0) ≥ 0.95, maxDD p95 ≤ 0.25, n ≥ 30 skipped+kept events; adjacent-θ same-sign anti-mining rule; explicit bleed-mask rule (WR-up-EV-down = NO_GO). Source: `27_prereg_vpin_jump_veto.md` + `.json` (sha256 recorded).
2. **Mechanism is peer-reviewed for the veto use, not the directional use:** RIBAF Jan 2026 supports VPIN as a **jump/volatility predictor** in crypto ([S0275531925004192](https://www.sciencedirect.com/science/article/pii/S0275531925004192)), relayed via scout reports `24_deep_research_futures_2026-07-23.md` §5 and `32_deep_research_futures_2026-07-24.md`. The same scout pass records the adverse directional anchor — practitioner walk-forward decay to **−15.6 bps net (t=0.96), 2026, BTC-only** ([MEXC study](https://www.mexc.co/news/1002105), single-source) — which is why the brief was narrowed to veto-only **before** this committee convened. The candidate survived adverse-evidence narrowing; it was not created by ignoring it.
3. **The target lane's bleed is measured on our own data:** AccBand dual-goal screen `30_screen_accband_frac_dual_goal.md` (CONFIRMED_NO_GO, prereg hash OK, n≈8,700 sim): **every** frac cell has expectancy ≈ **−0.242 to −0.246R** and breakeven WR ≥ 0.68 vs achieved WR ≤ 0.657. The lane loses after costs at all tested geometries; W/L per trade is 0.22–0.47, i.e. the average loss is ~2–4.5× the average win.
4. **Band outcomes demonstrably stratify by toxicity proxies:** the band-conditional screen (`13_band_conditional_screen.md`, 14,555 resolved outcomes, Bonferroni m=16) refuted *positive* selection (0/16 GO) but its *negative*-selection findings shipped as the flag-gated `BAND_REGIME_FILTER_ENABLED` veto — 4h ADX>30 cohort WR 59.0% vs 65.7% baseline; BTC 1h vol-ratio<0.7 cohort WR 55.6% (ledger row 2026-07-12). Precedent: this exact study shape (band-lane veto overlay) has produced a shipped instrument once already.
5. **Data and tooling are essentially free:** Binance USDT-M aggTrades are public; the harvester pattern is already proven in-repo (`scripts/harvest_binance_aggtrades_qh.py`, built for the C3 screen 2026-07-23). The recorded program decision is no vendor spend (`32_deep_research_futures_2026-07-24.md`). The VPIN-specific harvest (`data/aggtrades_vpin/`) **does not exist yet** — verified missing this session — and is the single blocking gap for the screen (not for this debate).
6. **Queue position is binding and scheduled:** `32_deep_research_futures_2026-07-24.md` Key takeaway 3: "Queue order is binding: VPIN veto screen first (prereg `27_*` already hashed; fresh UTC day 2026-07-25)"; the liquidation-cascade prereg and the OI×funding veto-refinement study are explicitly queued **behind** VPIN closing.
7. **Rails:** PAPER only, veto overlay on logged/replayed AccBand outcomes, no MCP live-path change from this prereg alone; any live install requires the frozen `core/promotion_gate.py` + owner sign-off (`27_prereg` Non-goals; brief §Binding rails).

### INFERENCE (labeled)

8. **The veto targets the loss tail with mechanical leverage.** With W/L ≈ 0.28 at the operating frac (FACT 3), one avoided full-loss pays for ~3.5 forgone wins. Jumps are the canonical cause of the worst band-lane losses (SL gap-through with wick-triggered fills + SL-side slippage in the sim). A predictor of jumps (FACT 2) therefore conditions on the *dominant* term of the EV equation, not a side effect. A modestly informative jump signal can move ΔEV more than an equally informative direction signal could — this asymmetry is why the veto expression can be alive while the directional expression is dead.
9. **The prereg is immunized against the degenerate "veto everything" win.** On a −0.24R lane, *any* random veto shrinks total bleed; the prereg's primary metric is Δ **mean** R, which a random veto leaves at ≈0. Only genuinely informative conditioning can pass. This is a *harder* bar than the shipped BAND_REGIME_FILTER precedent ever cleared (that veto was certified on WR stratification, not ΔEV) — passing it would produce a strictly stronger instrument than the one already in production.
10. **VPIN is a sharper clock than the shipped regime proxies.** ADX(4h) and 30-day vol-ratio are slow, price-derived regime flags; VPIN's volume-clock updates intra-day from order-flow (aggressor-side imbalance), i.e. it can flag toxicity *between* 4h closes. If band outcomes stratify on the slow proxies (FACT 4), a faster toxicity measure plausibly stratifies at least as well. (Counter-consideration acknowledged: the same data class failed directionally — C3 quarter-hour imbalance, 0/8 hypotheses, `22_screen_c3_quarter_hour_imbalance.md` — but directional failure does not bear on conditioning value; that is exactly the distinction between the refuted rows and this prereg.)
11. **Either outcome compounds knowledge.** GO → a candidate bleed-cutting veto for the band lane plus reusable VPIN infrastructure (volume buckets, Lee-Ready aggressor labeling) that the queued liquidation-cascade study needs anyway. NO_GO → a ledger row that retires the VPIN-veto family for the cost of one heavy-screen day, preventing indefinite re-litigation of the highest-citation microstructure indicator in the literature.

## Catalysts

- **The heavy-screen slot is scheduled for today** (UTC 2026-07-25, brief §FACT 4; queue order binding per `32_*`). Deferring burns the slot and delays the entire queue behind it (liquidation-cascade prereg, OI×funding veto study).
- **The current PAPER cohort is maximally informative for this exact question:** `BAND_REGIME_FILTER_ENABLED=false` since 2026-07-20 (owner directive, aggressive accrual), so the band lane is currently running with **no** regime veto — a clean no-veto baseline is accruing right now, and any validated veto has an immediate application surface when the owner re-enables protection.
- **Harvest is the only missing input** and is self-serve (aggTrades REST/public dumps; proven harvester pattern in-repo). No external dependency, no vendor spend, no waiting on third parties.

## BestCase

- ≥1 θ passes all frozen gates with adjacent-θ same-sign → the bot gains a second flag-gated, band-lane-only veto (sibling to `BAND_REGIME_FILTER`), this time certified on **after-cost ΔEV** rather than WR alone — cutting the ≈ −0.24R/trade bleed on BTC/ETH band flow while preserving the owner's WR-band objective. Magnitude of the EV lift is unknowable pre-screen and is deliberately not estimated here; it comes only from `27_screen_vpin_jump_veto.{md,json}` when the screen runs.
- Scope extension (more symbols, other lanes) would require a NEW prereg — correctly so; the best case stays inside the frozen pilot.
- Secondary win either way: first VPIN computation stack on own data, directly reusable by the queued liquidation-cascade and OI×funding studies (same microstructure data class).
- Honest ceiling, stated plainly: **even a full GO is WR-protection/bleed-reduction, not edge.** The band lane remains after-cost negative as a whole (FACT 3); profit must still come from validated lanes (F1 carry, event probes) through the promotion gate. Nothing in the best case changes the live decision path without owner sign-off.

## EvidenceQuality

| Evidence leg | Quality | Notes |
|---|---|---|
| Prereg discipline (frozen, hashed, multiplicity-pinned, bleed-mask rule) | **High** | Process artifact verifiable in-repo; hash recorded pre-outcome |
| Internal bleed measurement (−0.24R, BE_WR≥0.68) | **High** | Own warehouse, n≈8,700, prereg-hashed screen `30_*` |
| Veto-class precedent (band outcomes stratify on toxicity proxies) | **Moderate-High** | 14,555 outcomes, Bonferroni-controlled (`13_*`); but certified on WR stratification, not ΔEV — the very gap this prereg closes |
| VPIN→jump mechanism | **Moderate** | One peer-reviewed anchor (RIBAF Jan 2026) relayed via scout report — not independently re-verified here; a second study (Frontiers proxy) is graded "relatively weak" by the same scouts. Mixed, single strong anchor — flagged |
| Adverse directional anchor (−15.6 bps net) | **Single-source, practitioner** — flagged | Works *for* the bull case only in the sense that the scope was honestly narrowed on it |
| Screen outcome evidence | **None — does not exist** | `data/aggtrades_vpin/` verified missing 2026-07-25; nothing in this thesis presumes any screen result |

Net: the case to *run the screen* rests on high-quality internal artifacts; the case that the screen will *pass* rests on moderate external mechanism evidence — which is exactly why the deliverable is a screen, not a build.

## Confidence

**68 / 100** — confidence that proceeding with the pre-registered screen now is the correct committee action.

Decomposition (kept separate to avoid conflation):

- P(running the screen is +EV in information terms | bounded cost, binding queue, frozen prereg): high — drives the 68.
- P(screen returns GO): **low, est. ~15–25%** — the prereg's own expectation is NO_GO; the pipeline's historical base rate is ~2 CONFIRMED_GO across 15+ screens; the adverse directional anchor and the C3 same-data-class failure both push down. A NO_GO outcome would **not** make proceeding wrong.
- Confidence is deliberately capped below 75 because the mechanism leg is a single relayed peer-reviewed anchor and the BTC/ETH-only band-outcome sample may prove thin (see InvalidateIf 2).

## InvalidateIf

This bull thesis (= proceed with the screen now) is invalidated by any of:

1. **Harvest infeasibility:** Binance aggTrades cannot be assembled to cover the AccBand outcome window on BTC/ETH with no-lookahead bucket closure (prereg §Signal 5), or the harvest cost proves materially non-trivial (vendor spend required) — park, per the recorded no-vendor-spend decision.
2. **Sample floor unreachable:** BTC/ETH-only resolved band outcomes in the overlap window are < 30 skipped+kept events and cannot reach 30 within the committee's horizon — the prereg itself then forces INSUFFICIENT_DATA; defer until forward accrual covers it rather than burning the slot.
3. **Process integrity break:** prereg hash mismatch, or any post-hoc touch of the θ grid / gates / VPIN construction — kill the run outright (never loosen frozen gates).
4. **Application surface vanishes:** owner retires the MAX_FLOW_BAND band-lane cohort (the veto has no host lane) — the screen's decision value collapses; park until a successor lane exists.
5. **Queue displacement with owner sign-off:** a strictly higher-value candidate takes the heavy-screen slot by explicit owner re-order (queue order is otherwise binding).
6. **New rigorous adverse evidence on the veto expression itself:** a 2025+ multiplicity-controlled, after-cost study showing VPIN-conditioned *filtering* (not direction) adds nothing OOS on liquid crypto at retail granularity — quote it verbatim, add the ledger row, stand down without running.

## Sources

1. `_workspace/strategy_pipeline/27_prereg_vpin_jump_veto.md` (+ `.json`, sha256) — frozen prereg, gates, θ grid
2. `_workspace/strategy_pipeline/35_research_brief_vpin_jump_veto.md` — committee brief, missing-data disclosure
3. `_workspace/strategy_pipeline/24_deep_research_futures_2026-07-23.md` §5, sources 16–17 — [RIBAF Jan 2026 VPIN/jumps](https://www.sciencedirect.com/science/article/pii/S0275531925004192) (peer-reviewed anchor); [MEXC practitioner WF](https://www.mexc.co/news/1002105) (adverse directional, single-source)
4. `_workspace/strategy_pipeline/32_deep_research_futures_2026-07-24.md` — binding queue order, no-vendor-spend decision, OI×funding study queued behind VPIN
5. `_workspace/strategy_pipeline/30_screen_accband_frac_dual_goal.md` — band-lane bleed ≈ −0.24R, BE_WR ≥ 0.68, W/L 0.22–0.47 (prereg-hashed)
6. `_workspace/strategy_pipeline/13_band_conditional_screen.md` + refuted-families ledger row 2026-07-12 — positive selection refuted; `BAND_REGIME_FILTER` negative-selection precedent (ADX>30: WR 59.0% vs 65.7%; vol-ratio<0.7: 55.6%)
7. `_workspace/strategy_pipeline/22_screen_c3_quarter_hour_imbalance.md` — same-data-class directional NO_GO (0/8), harvester provenance
8. `scripts/harvest_binance_aggtrades_qh.py` — proven aggTrades harvester pattern (C3)
9. `data/aggtrades_vpin/` — verified MISSING 2026-07-25 (this session) — blocking gap for the screen, disclosed
10. `.claude/skills/refuted-families-ledger/SKILL.md` — family-status check (ADJACENT; directional VPIN adverse; no row covers the veto expression)
11. `CLAUDE.md` (repo) — `BAND_REGIME_FILTER_ENABLED=false` since 2026-07-20 (clean no-veto baseline currently accruing); PAPER/MAX_FLOW_BAND cohort state

*No screen results are cited anywhere in this document because none exist. Expectation on file remains NO_GO; this thesis argues the screen should run, not that it will pass.*
