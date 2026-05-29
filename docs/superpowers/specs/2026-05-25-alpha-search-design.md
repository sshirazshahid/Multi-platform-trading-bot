# Alpha-Search Falsification Experiment — Design

**Date:** 2026-05-25
**Branch:** feat/profitability-upgrade
**Status:** Approved (brainstorm) — pending implementation plan
**Topic owner:** systematic edge search (price/volume)

---

## 1. Purpose

Run one broad, honest, **pre-registered** search for a cross-sectional price/volume
edge on the bot's crypto perp universe, by porting a large formulaic-alpha library
(~450 alphas: Kakushadze-101, GTJA-191, Qlib158, Fama-French-style), screening each
through an information-coefficient (IC/IR) stage and a walk-forward Deflated-Sharpe /
PBO / false-discovery-rate stage, and producing a single verdict:

- **≥1 surviving alpha** → flag it for a **separate** integration project (its own
  brainstorm → spec → plan). This experiment does **not** wire anything into the live bot.
- **0 surviving alphas** → conclude **no exploitable price/volume edge** in this
  information class and **stop hand-crafting price signals**.

This is a **falsification experiment**, not a durable mining capability. The
deliverable is a research pipeline (run once, re-runnable) + a report. Minimal
permanent machinery.

## 2. Background & prior

A 5-agent forensic study and a scoring-engine rebuild attempt (both 2026-05-25)
proved the bot has **no demonstrable edge** on its current signal:

- 284 clean closed trades: WR 45.8% vs break-even 65.5%, win/loss ratio 0.526,
  `mcp_score` anti-monotonic.
- The ML ensemble that gates entries: LR AUC 0.586 / GBM AUC 0.681, **Deflated
  Sharpe = 0.000** under honest recomputation (embargo ≥ label horizon).
- Root cause: the `warehouse.features` table carries only 14 features — 13
  trend/momentum + 1 funding. The model trains on the same trend signals the
  rule-score uses, so it inherits the same noise.

The alpha zoo is a **broader, better-screened search over the same information
class (price/volume)** that the 14 features already failed on. The prior is
therefore weak — but 450 FDR-controlled, walk-forward-validated alphas is a far
more thorough test than 14 hand-picked features, and a clean null result is itself
a decision-grade outcome for this user (stop hand-crafting price signals).

This experiment is **complementary** to, and independent of, the microstructure
forward-collection build (open interest / depth / basis), which probes a *different*
information class and is gated separately by data accumulation.

## 3. Pre-registration (FROZEN before the search runs)

These constants are committed in this spec and hard-coded at the top of
`scripts/run_alpha_search.py`. They are recorded verbatim in every report header so
the search cannot be rationalized after the fact.

| Parameter | Value | Rationale |
|---|---|---|
| Panel timeframe | **1h** | Matches the bot's primary decision TF; 15m adds no degrees of freedom (autocorrelation), 4h thins the sample. |
| Forward-return horizon | **24 bars (24h)** | Single horizon (no horizon-shopping). Aligns with the bot's 24h triple-barrier label; avoids 1h microstructure noise. |
| In-sample / out-of-sample split | **time-split 60% / 40%** | Chronological, not random. Alphas + signs are frozen at the split before any OOS computation. |
| Embargo across split | **24 bars** | = forward horizon, so no forward-label window straddles the IS/OOS boundary. |
| Cross-section min width | **≥10 symbols at a bar** | Below this the per-bar IC is too noisy to use; the bar is dropped from IC. |
| Long-short quantile | **q = 0.20** (top/bottom quintile) | Stage-2 portfolio construction. ~6 longs / ~6 shorts at 32 symbols. |
| Stage-1 survivor bar | **\|IR_IS\| ≥ 0.50** | IR = meanIC / std(IC). Encodes sign stability (mean ≥ ½ std from zero). |
| Stage-2 DSR bar | **DSR ≥ 0.10**, `n_trials = N_eff` | DSR = Pr[true SR > 0], trials-deflated. Same convention/floor as the existing model-version gate. |
| Stage-2 PBO bar | **PBO ≤ 0.50** | CSCV overfit probability for the whole selection. |
| Stage-2 FDR bar | **Benjamini-Hochberg q = 0.05** | False-discovery control across Stage-1 survivors. |
| Trial count | **N_eff = 2 × N_computable** | Pays for in-sample sign-fitting (Alive **or** Reversed both allowed). |

**Survivor definition (conjunctive — all four must hold):**

```
SURVIVOR(alpha) :=  |IR_IS| ≥ 0.50           (Stage 1)
                 ∧  DSR_OOS ≥ 0.10            (Stage 2, n_trials = N_eff)
                 ∧  PBO ≤ 0.50                (Stage 2, search-wide)
                 ∧  BH-FDR-pass at q = 0.05   (Stage 2, across Stage-1 survivors)
```

**Decision rule:** `len(survivors) ≥ 1` → report + flag for separate integration
project. `len(survivors) == 0` → "no price/volume edge" verdict, stop.

> **Note on conservatism (intentional):** DSR (per-alpha, trials-deflated) and
> BH-FDR (across survivors, raw-p) are *deliberately redundant* multiple-testing
> guards, and IR ≥ 0.50 is a heroic OOS bar. The conjunction is biased toward the
> null **on purpose** — the decision criterion is "stop if null," so a false
> negative is cheap and a false positive is expensive. This is recorded so the
> conjunction is not later misread as a bug.

## 4. Scope

**In scope**
- One-time 1h history backfill to ~2-3 years for the 32 cached symbols.
- Operator library + ~450 alpha definitions + computability tagging.
- Cross-sectional panel construction + 24h forward labels + 60/40 time-split.
- Stage-1 IC/IR screen + Alive/Reversed/Dead categorization.
- Stage-2 walk-forward DSR / PBO / BH-FDR on Stage-1 survivors (reusing
  `core.stat_tests`).
- A markdown + JSON report and the binary verdict.
- Full test suite (operators, lookahead sentinel, IC/FDR math, synthetic
  end-to-end).

**Out of scope (explicitly deferred)**
- Any wiring of a surviving alpha into `features.py`, the feature store, the model,
  `mcp_brain`, or live trading. That is a separate project.
- Microstructure features (separate build).
- Multi-timeframe alpha evaluation, intraday seasonality factors, alpha
  *combination*/ensembling (a survivor is evaluated standalone).
- Any change to live thresholds, gates, sizing, or runtime behavior.

## 5. Architecture & data flow

```
[Stage 0] Backfill 1h via ccxt → extend data/ohlcv_cache/*.parquet to ~2-3yr
          (32 symbols; newer listings = max available; idempotent, dedup on ts)
   │
   ▼
[Panel]   load cache → dict{field → (T bars × N symbols) DataFrame}
          fields: open, high, low, close, volume, vwap≈(h+l+c)/3,
                  adv{d}=rolling mean(close·volume), returns=close.pct_change()
          forward label: fwd_ret_t = close[t+24]/close[t] − 1
          chronological split at 60% → (panel_IS, panel_OOS), embargo 24 bars
   │
   ▼
[Stage 1 · in-sample, first 60%]   for each computable alpha a:
   signal_a = a(panel_IS)                              # cross-sectional, backward-only
   IC_t     = spearman(signal_a[t, :], fwd_ret[t, :])   # per bar, over ≥10 symbols
   meanIC   = mean_t(IC_t);  IR_IS = meanIC / std_t(IC_t)
   sign_a   = sign(meanIC)                              # direction FIXED in-sample
   category = Alive (IR_IS ≥ 0.5) | Reversed (IR_IS ≤ −0.5) | Dead (|IR_IS| < 0.5)
   Stage-1 survivors = { a : |IR_IS| ≥ 0.5 }            # alphas + signs FROZEN here
   │
   ▼
[Stage 2 · out-of-sample, held-out 40%]   for each Stage-1 survivor a:
   rank symbols at each OOS bar by sign_a · signal_a
   port_ret_t = mean(fwd_ret of top-q) − mean(fwd_ret of bottom-q)     # long-short
   SR_a   = sharpe(port_ret_OOS)                                       # core.stat_tests
   DSR_a  = deflated_sharpe(SR_a, n_trials=N_eff, n_obs, skew, kurt)   # Pr[SR>0]
   p_a    = 1 − Φ( SR_a · √n_obs )   # one-sided Sharpe-significance p (normal approx), for FDR
   PBO    = pbo( T×K matrix: ALL computable alphas' OOS port returns, each w/ its IS sign )  # search-wide
   FDR    = Benjamini-Hochberg({p_a}, q=0.05)                          # across survivors
   SURVIVOR = |IR_IS|≥0.5 ∧ DSR_a≥0.10 ∧ PBO≤0.5 ∧ a ∈ FDR-pass
   │
   ▼
[Report]  reports/alpha_search_<date>.{md,json}
          - frozen pre-registration header (every constant above + N_computable, N_eff)
          - full ranking of EVERY alpha: source, computable, meanIC, IR_IS,
            category, OOS Sharpe, DSR, FDR p, pass/fail per gate
          - survivor list + binary verdict
```

**Operator axes (standard Alpha101 semantics, stated to remove ambiguity):**
- **Time-series operators** (`ts_rank`, `ts_min`, `ts_max`, `ts_argmin`, `ts_argmax`,
  `delta`, `delay`, `decay_linear`, `correlation`, `covariance`, `stddev`, `sma`,
  `sum`, `product`) act **per symbol along the time axis**, looking **backward only**.
- **Cross-sectional operators** (`rank`, `scale`, `indneutralize`*) act **across
  symbols at a single bar**. `*indneutralize` has no clean crypto industry map → see §7.

## 6. Components (files)

| File | Responsibility |
|---|---|
| `scripts/backfill_ohlcv_history.py` | Stage 0. One-shot, idempotent 1h backfill to ~2-3yr via ccxt `since`-pagination (Binance primary; per-symbol fallback to Bybit/Bitget if Binance lacks the listing). Extends existing parquet files, dedups on `ts`. |
| `core/alpha_zoo/__init__.py` | Package marker. |
| `core/alpha_zoo/panel.py` | Cache → `dict{field → (T×N) DataFrame}`; derive vwap/adv/returns; 24-bar forward label; 60/40 chronological split with 24-bar embargo; common-grid alignment + per-bar valid-symbol masking. |
| `core/alpha_zoo/operators.py` | ~35 backward-only vectorized operators over (T×N) DataFrames. The single place the no-lookahead guarantee lives. |
| `core/alpha_zoo/alphas.py` | ~450 alpha definitions as functions of the panel, each tagged `{id, source, computable, needs, reason_if_dropped}`. Non-computable alphas (indneutralize / fundamental / book-value) carry an explicit reason and are excluded from `N_computable`. |
| `core/alpha_zoo/screen.py` | Stage-1 IC/IR + categorization; Stage-2 long-short portfolio returns; calls into `core.stat_tests` for Sharpe/DSR/PBO; BH-FDR implementation. Pure functions, no I/O. |
| `scripts/run_alpha_search.py` | Orchestrator: Stage0(optional) → panel → Stage1 → Stage2 → report. **Frozen pre-registration constants at top.** Writes `reports/alpha_search_<date>.{md,json}`. |
| `tests/test_alpha_operators.py` | Operator unit tests on known inputs. |
| `tests/test_alpha_lookahead_sentinel.py` | Corrupt the last K rows of every input field; assert each operator's earlier-row outputs are byte-identical (proves backward-only). |
| `tests/test_alpha_panel.py` | Panel build, vwap/adv/returns, forward label alignment, split + embargo, valid-symbol masking. |
| `tests/test_alpha_screen.py` | IC/IR math, categorization thresholds, long-short portfolio, BH-FDR math, N_eff accounting. |
| `tests/test_alpha_search_e2e.py` | Synthetic panel with a **planted** alpha (must be found) + a **pure-noise** alpha (must be rejected) → proves the pipeline discriminates. |

## 7. Computability tagging (locking N)

Each alpha is tagged at definition time:
- **computable** — uses only OHLCV + derived vwap/adv/returns. Counts toward
  `N_computable`.
- **non-computable** — needs `indneutralize` (no clean crypto industry/sector map),
  fundamentals, or book values. Excluded, with `reason_if_dropped` recorded.

`indneutralize(x, group)` handling: alphas whose signal is *only* an industry
neutralization are dropped (non-computable). Where `indneutralize` is one step inside
a larger price/volume formula, it degrades to **identity** (no neutralization) and the
alpha stays computable, with the degradation noted in `needs`. This is documented per
alpha so the count is auditable.

`N_computable` is **locked after the port** (counted from the tagged definitions, not
assumed to be 450). `N_eff = 2 × N_computable` and is the `n_trials` argument to every
DSR call. Both numbers are printed in the report header.

vwap/adv substitutions (standard, documented): `vwap ≈ (high+low+close)/3` (typical
price; no native vwap column); `adv{d} = rolling_mean(close·volume, d)` (dollar
volume); `returns = close.pct_change()`.

## 8. Leak & correctness guards

The single failure mode that would hollow out the experiment is lookahead / selection
leakage. Guards, in priority order:

1. **Backward-only operators**, enforced by `test_alpha_lookahead_sentinel.py`
   (corrupt future rows → assert past outputs unchanged). This is the
   AST-purity/300-row-sentinel idea from the Alpha Zoo, implemented as a test.
2. **Chronological 60/40 split with 24-bar embargo**, signs frozen at the split →
   Stage-2 is genuinely OOS → kills Stage-1→Stage-2 selection bias (the classic
   factor-zoo "we found something" failure).
3. **N_eff = 2×N_computable** baked into DSR → pays for the in-sample sign choice.
4. **PBO on the full T×K matrix** → measures overfit of the *selection procedure*,
   not just individual alphas.
5. **Planted-alpha + noise e2e test** → proves the pipeline finds real signal and
   rejects noise before trusting any live verdict.

## 9. Report format

`reports/alpha_search_<date>.json` (machine) and `.md` (human). Both contain:
- **Header:** every frozen pre-registration constant, `N_computable`, `N_eff`,
  panel coverage (date range, bar count, symbol count), IS/OOS bar counts.
- **Full table:** one row per alpha — `id, source, computable, reason_if_dropped,
  meanIC, IR_IS, category, oos_sharpe, dsr, fdr_p, gate flags (ir/dsr/pbo/fdr)`.
- **Search-wide:** the single PBO value.
- **Verdict:** survivor list (possibly empty) + the binary decision sentence.

## 10. Testing strategy

TDD per project convention: write the failing test, see it fail, implement minimally,
see it pass, commit. Order: operators → lookahead sentinel → panel → screen math →
synthetic end-to-end. The end-to-end planted-signal/noise test is the acceptance gate
for trusting the pipeline. No live-data dependence in tests (synthetic panels only) so
the suite is deterministic and CI-safe.

## 11. Limitations (recorded, not bugs)

- **IR ≥ 0.50 is heroic.** Real equity factors deliver OOS IR ≈ 0.05-0.20. The bar is
  intentionally high → the test is biased toward the null. Acceptable given the
  "stop if null" decision criterion; a survivor at this bar is genuinely strong.
- **Thin cross-section.** 32 names → per-bar IC standard error ≈ 1/√32 ≈ 0.18.
  Kakushadze assumed ~3000 names. Time-averaging via IR over thousands of bars is
  what makes the screen workable; the report states the breadth caveat.
- **Same information class** (price/volume) the 14 features already failed on → weak
  prior. The value is breadth + honest FDR control, not a fresh information source.
- **Unbalanced panel** from staggered listings (newer coins absent early). Handled by
  per-bar valid-symbol masking + the ≥10-symbol minimum; older bars rank across fewer
  names, which the report notes.
- **Standalone evaluation.** Alphas are screened individually; no alpha-combination
  search (would multiply the trial count and overfitting risk).

## 12. Dependencies & reuse

- **Reuse:** the existing OHLCV cache loader/writer (`core/feature_store.py` /
  `scripts/agent_backtest.py`); `core.stat_tests.{sharpe, deflated_sharpe, pbo,
  bootstrap_ci}` (verified: `deflated_sharpe` already accepts `n_trials`); existing
  ccxt exchange clients for the backfill.
- **No new runtime dependency.** Panel is `pandas` DataFrames (no xarray). `scipy`
  (already a dev dependency) supplies Spearman / BH-FDR helpers if useful; otherwise
  hand-rolled with numpy.
- **No promotion_gate extension needed** — the screen calls `stat_tests` primitives
  directly, since the gate wrappers assume shadow-vs-live / model-row semantics.

## 13. Success criteria

The **experiment** succeeds (regardless of verdict) when:
1. The pipeline runs end-to-end on the backfilled panel and emits the report.
2. The synthetic planted-alpha/noise test passes (pipeline provably discriminates).
3. The verdict is produced under the frozen pre-registration with `N_eff` correctly
   deflating the DSR.

The **search outcome** is either: ≥1 survivor (→ separate integration project) or 0
survivors (→ "no price/volume edge", stop hand-crafting price signals). Both are
decision-grade; neither is a failure of the experiment.
