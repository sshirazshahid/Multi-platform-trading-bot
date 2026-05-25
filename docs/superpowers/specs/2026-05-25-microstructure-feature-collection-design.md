# Microstructure Feature Forward-Collection — Design

**Date:** 2026-05-25
**Status:** Design (approved in brainstorm; pending spec review)
**Author:** Claude (Opus 4.7) + operator

## Problem

Two same-day findings (memory: `no-edge-forensics-2026-05-25`) established that
the bot has no predictive edge from any current signal:

- The rule score is anti-monotonic; the ML ensemble's 0.80 OOS-WR was an
  artifact (clean re-eval: AUC 0.59-0.68, **Deflated Sharpe 0.000**).
- **Root cause:** the `warehouse.features` store has 14 features — 13
  trend/momentum (EMA, RSI, MACD, ADX, ATR, GARCH-vol, regime, BTC-corr) +
  1 funding (`funding_zscore_7d`). It has **NO open interest, NO orderbook
  imbalance, NO depth, NO basis.** The model learns the same trend noise the
  rule score uses.

The only untested edge hypothesis is genuine **futures microstructure** —
the signals that actually matter at a 1-2% scalp horizon. This spec builds
the infrastructure to capture them so a future retrain can test whether they
carry edge.

## Feasibility constraint (shapes the whole approach)

- **Orderbook imbalance / depth have no free historical API** — real-time
  only. They cannot be backfilled for existing candidates.
- **OI history is ~30 days** (Binance `openInterestHist`); the existing labels
  predate it.
- **The labels are stale:** all 5,582 are from 2026-04-14..04-26;
  `scripts/build_labels.py` hasn't run since. Candidates keep logging
  (23,948, Mar 29..May 25) but go unlabeled.

Therefore: the high-value features are **forward-collection only**. This is a
multi-week "plant now, harvest later" effort — capture microstructure on every
new candidate, resume label generation, accumulate, then let the weekly
retrain test for edge.

## Goal

Persist a defined set of microstructure features into every new candidate's
`features_json`, resume label generation, and let the existing weekly retrain
+ the (already-tightened) honest promotion gate determine whether the new
features produce a deployable model. Make the edge test *possible*; do not
presume its outcome.

## Non-goals

- Backfilling microstructure onto historical candidates (impossible for
  orderbook/depth; out of scope for OI/basis).
- Changing live entry decisions (this is pure data capture).
- Building a new training trigger (the weekly retrain already consumes
  `FEATURE_KEYS`).
- Promoting any model (deferred until data accumulates and the honest gate
  passes — likely weeks out, no guarantee).

## Feature set

Derived from data already fetched each cycle in `mcp_brain` (`fetch_funding_rates`
gives mark/index; `fetch_orderbook_depth` gives imbalance/depth;
`fetch_open_interest` gives OI). Named per the `feature_name` convention:

| Feature | Source | Meaning |
|---|---|---|
| `oi_delta_6h` | `data["oi"][coin].oi_delta_pct` | OI 6h change — new money vs short-cover |
| `ob_imbalance` | `data["orderbook"][coin].imbalance` | (bid_vol−ask_vol)/total at top 10 |
| `depth_ratio` | `bid_depth_usd / ask_depth_usd` | directional book pressure |
| `basis_bps` | `(mark−index)/index × 1e4` | perp premium/discount |
| `funding_rate` | `data["funding"][coin].funding_rate` | carry (complements funding_zscore_7d) |

## Design / components

### 1. `_microstructure_features(coin, data) -> dict`
Pure helper in `core/mcp_brain.py`. Reads `data["funding"]`, `data["orderbook"]`,
`data["oi"]` for `coin`; returns a dict of the 5 named features. **Fail-safe:**
any missing source → that key is omitted (NOT a fabricated 0), so the absence
is honest and `load_dataset` imputes it as neutral.

### 2. Wire into candidate logging
At `core/mcp_brain.py:2883` (`feat = dict(model_input)`), merge
`_microstructure_features(coin, data)` into `feat` before `record_candidate`.
Every new candidate captures the microstructure features in `features_json`.
No effect on `gate_pass` / entry decisions.

### 3. Training integration
Append the 5 names to `FEATURE_KEYS` (`scripts/train_models.py:50`).
`load_dataset` must tolerate missing keys (historical candidates lack them):
parse with `feats.get(k, np.nan)` and let the existing imputation handle NaN.
Only candidates logged after this ships carry real values.

### 4. Label resume + accumulation accounting
- Run/schedule `scripts/build_labels.py` so labels flow for new candidates
  (it was last run ~April 26).
- A small read-only accounting query (script or function): count labeled
  candidates whose `features_json` contains a non-null `ob_imbalance` (the
  forward-only marker). Target ≥ ~500-1000 before a retrain is worth running.

### 5. Validation (deferred, automatic)
No new trigger. Once microstructure-bearing labeled rows accumulate, the
existing weekly retrain (`scripts/retrain_weekly.ps1` → `train_models.py`)
includes the new `FEATURE_KEYS`, and the honest gate (MAX_PBO 0.5,
MIN_DSR 0.10 — shipped 2026-05-25) decides promotion. If the microstructure
features carry edge, a model clears the gate; if not, it HELDs and we have a
clean null result on the last untested hypothesis.

## Components & boundaries

| Unit | Responsibility | File |
|---|---|---|
| `_microstructure_features` | Extract 5 features from fetched data, fail-safe | `core/mcp_brain.py` |
| candidate-logging merge | Persist features into `features_json` | `core/mcp_brain.py:2883` |
| `FEATURE_KEYS` + NaN-tolerant load | Training consumes new features | `scripts/train_models.py` |
| label resume | Keep labels flowing | `scripts/build_labels.py` (run/schedule) |
| accumulation check | Know when to train | new `scripts/microstructure_readiness.py` |

## Error handling / fail-safe

- Missing microstructure source for a coin → omit that key (no fabricated 0).
- `_microstructure_features` never raises — wrapped; on any error returns `{}`.
- `load_dataset` treats absent keys as `np.nan` → existing imputation; never
  crashes on old candidates lacking the keys.
- Pure data capture: a bug here cannot change a live entry/exit decision.

## Testing (TDD)

- `_microstructure_features`: correct extraction from a populated `data` dict;
  returns `{}` / omits keys on missing sources; never raises.
- Wiring: a recorded candidate's `features_json` contains the 5 keys when data
  is present.
- `FEATURE_KEYS`: the 5 names are present; `load_dataset` tolerates candidates
  missing them (NaN, no crash).

## Success / stop criteria

- **Success (this build):** new candidates carry microstructure features;
  labels resume; accumulation counter climbs. (Training success is a separate,
  later event judged by the honest gate.)
- **Honest stop (weeks later):** if, after ≥500-1000 microstructure-labeled
  rows, no model clears the honest gate, the verdict is "microstructure
  features also lack edge" — the last untested hypothesis, cleanly answered.

## Risks

- **No guarantee of edge.** Orderbook/OI may also be noise at this horizon and
  capital scale. This build makes the test possible; it doesn't ensure a win.
- **Requires the bot running continuously** to accumulate forward data.
- **Single-exchange microstructure** (Binance REST) — cross-venue differences
  unmodeled; acceptable for a first test.
- Multi-week latency to any result — set expectations accordingly.

## Rollback

- Pure additive data capture. To stop: remove the merge line (features simply
  stop being written); no live-decision impact. `FEATURE_KEYS` entries are
  NaN-safe, so reverting capture doesn't break training.
