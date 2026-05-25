# Microstructure Feature Collection Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Persist 5 microstructure features into every new candidate's `features_json` so the weekly retrain can test whether they carry edge, and resume the stalled label pipeline.

**Architecture:** Pure additive data capture. The microstructure data is already fetched each cycle (`fetch_funding_rates` → mark/index; `fetch_orderbook_depth` → imbalance/depth; `fetch_open_interest` → OI). A new `_microstructure_features` helper extracts 5 named features and they're merged into the candidate `feat` dict at logging time. `FEATURE_KEYS` gains the 3 truly-missing names so training consumes them once accumulated. No live entry/exit decision changes.

**Tech Stack:** Python, SQLite warehouse, pytest. Files: `core/mcp_brain.py`, `scripts/train_models.py`, `scripts/build_labels.py`, `scripts/microstructure_readiness.py` (new).

---

## Context the executor needs

- **Candidate logging:** `core/mcp_brain.py:~2883` builds `feat = dict(model_input)` then calls `wh.record_candidate(..., features=feat)`. The microstructure data for the coin lives in the `data` dict in scope: `data["funding"][coin]` (`funding_rate`, `mark_price`, `index_price`), `data["orderbook"][coin]` (`imbalance`, `bid_depth_usd`, `ask_depth_usd`), `data["oi"][coin]` (`oi_delta_pct`).
- **Training read:** `scripts/train_models.py:152` builds the vector `[_coerce(feats.get(k)) for k in FEATURE_KEYS]`. `_coerce(None) == 0.0`, so missing keys are already tolerated as 0.0 — NO load_dataset change needed.
- **FEATURE_KEYS (line 50-66)** ALREADY contains `funding_rate` and `ob_imbalance` (but they're unpopulated → constant 0). Add only the 3 missing: `oi_delta_6h`, `depth_ratio`, `basis_bps`.
- **0.0 must be the neutral missing-value** for every new feature (because absent → 0.0). So `depth_ratio` is a *log-ratio* (0 = balanced), not raw bid/ask (which would make 0 mean "infinite ask pressure").
- **Labels stale:** `scripts/build_labels.py` last ran ~April 26. Re-running extends labels for candidates whose 96-bar forward window has data.
- Run tests with `PYTHONIOENCODING=utf-8` prefix on Windows.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `core/mcp_brain.py` | Modify | `_microstructure_features` helper + merge into `feat` |
| `scripts/train_models.py` | Modify | Add 3 keys to `FEATURE_KEYS` |
| `tests/test_microstructure_features.py` | Create | Extractor + wiring-shape + FEATURE_KEYS tests |
| `scripts/microstructure_readiness.py` | Create | Count labeled candidates with populated microstructure |
| `scripts/build_labels.py` | Run (operational) | Resume label generation |

---

### Task 1: `_microstructure_features` extractor

**Files:**
- Create: `tests/test_microstructure_features.py`
- Modify: `core/mcp_brain.py` (add module-level helper near the other `fetch_*` functions, e.g. after `fetch_open_interest`)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_microstructure_features.py
"""Microstructure feature extraction (2026-05-25). 0.0 must be the neutral
missing-value for every feature (absent source -> key omitted -> _coerce 0.0)."""
from __future__ import annotations

import math

from core.mcp_brain import _microstructure_features


def _data(**coin_blocks):
    """Build a `data`-shaped dict for one coin 'BTC'."""
    d = {"funding": {}, "orderbook": {}, "oi": {}}
    for k, v in coin_blocks.items():
        d[k]["BTC"] = v
    return d


def test_extracts_all_five_when_present():
    data = _data(
        funding={"funding_rate": 0.0001, "mark_price": 101.0, "index_price": 100.0},
        orderbook={"imbalance": 0.20, "bid_depth_usd": 8000.0, "ask_depth_usd": 4000.0},
        oi={"oi_delta_pct": 0.03},
    )
    f = _microstructure_features("BTC", data)
    assert f["oi_delta_6h"] == 0.03
    assert f["ob_imbalance"] == 0.20
    assert f["funding_rate"] == 0.0001
    # basis_bps = (mark-index)/index * 1e4 = (1/100)*1e4 = 100
    assert abs(f["basis_bps"] - 100.0) < 1e-6
    # depth_ratio = log(bid/ask) = log(2) ~ 0.693
    assert abs(f["depth_ratio"] - math.log(2.0)) < 1e-6


def test_balanced_book_gives_zero_depth_ratio():
    data = _data(orderbook={"imbalance": 0.0, "bid_depth_usd": 5000.0,
                            "ask_depth_usd": 5000.0})
    f = _microstructure_features("BTC", data)
    assert f["depth_ratio"] == 0.0   # log(1) = 0 — neutral


def test_missing_sources_omit_keys_never_raises():
    f = _microstructure_features("BTC", {"funding": {}, "orderbook": {}, "oi": {}})
    assert f == {}                    # nothing present -> empty, no fabricated 0
    # totally absent data dict must also be safe
    assert _microstructure_features("BTC", {}) == {}


def test_zero_depth_is_safe():
    data = _data(orderbook={"imbalance": 0.1, "bid_depth_usd": 0.0,
                            "ask_depth_usd": 5000.0})
    f = _microstructure_features("BTC", data)
    # cannot take log(0/ask); depth_ratio omitted, but imbalance still present
    assert "depth_ratio" not in f
    assert f["ob_imbalance"] == 0.1
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_microstructure_features.py -v`
Expected: FAIL — `_microstructure_features` not defined.

- [ ] **Step 3: Implement the helper**

```python
# core/mcp_brain.py — module-level, after fetch_open_interest
def _microstructure_features(coin: str, data: dict) -> dict:
    """Extract scalp-relevant microstructure features for `coin` from the
    already-fetched `data` dict. Returns only keys whose source is present
    (absent -> omitted, so load_dataset's _coerce maps them to a neutral
    0.0). Every feature is defined so 0.0 is its neutral/missing value.
    Never raises."""
    import math as _math
    out: dict = {}
    try:
        fr = (data.get("funding") or {}).get(coin) or {}
        if "funding_rate" in fr:
            out["funding_rate"] = float(fr["funding_rate"])
        mark = float(fr.get("mark_price", 0) or 0)
        index = float(fr.get("index_price", 0) or 0)
        if mark > 0 and index > 0:
            out["basis_bps"] = (mark - index) / index * 1e4

        ob = (data.get("orderbook") or {}).get(coin) or {}
        if "imbalance" in ob:
            out["ob_imbalance"] = float(ob["imbalance"])
        bid = float(ob.get("bid_depth_usd", 0) or 0)
        ask = float(ob.get("ask_depth_usd", 0) or 0)
        if bid > 0 and ask > 0:
            out["depth_ratio"] = _math.log(bid / ask)   # 0 = balanced

        oi = (data.get("oi") or {}).get(coin) or {}
        if "oi_delta_pct" in oi:
            out["oi_delta_6h"] = float(oi["oi_delta_pct"])
    except Exception:
        return {}
    return out
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_microstructure_features.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add tests/test_microstructure_features.py core/mcp_brain.py
git commit -m "feat(features): _microstructure_features extractor (OI/OB/basis/funding)"
```

### Task 2: Wire the features into candidate logging

**Files:**
- Modify: `core/mcp_brain.py:~2883` (the `feat = dict(model_input)` block)
- Test: extend `tests/test_microstructure_features.py`

- [ ] **Step 1: Write the failing test (wiring shape)**

```python
# append to tests/test_microstructure_features.py
def test_feat_dict_merges_microstructure():
    """Replica of the candidate-logging merge: feat must carry the
    microstructure keys when data is present."""
    model_input = {"score": 70, "rsi_1h": 55}
    data = _data(
        funding={"funding_rate": 0.0002, "mark_price": 200.0, "index_price": 199.0},
        orderbook={"imbalance": -0.1, "bid_depth_usd": 3000.0, "ask_depth_usd": 6000.0},
        oi={"oi_delta_pct": -0.02},
    )
    feat = dict(model_input)
    feat.update(_microstructure_features("BTC", data))   # the production merge
    assert feat["score"] == 70                            # original preserved
    assert feat["oi_delta_6h"] == -0.02
    assert feat["ob_imbalance"] == -0.1
    assert "basis_bps" in feat and "depth_ratio" in feat
```

- [ ] **Step 2: Run to verify it passes against the helper**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_microstructure_features.py::test_feat_dict_merges_microstructure -v`
Expected: PASS (it exercises the same merge the production code will do).

- [ ] **Step 3: Add the merge in production**

At `core/mcp_brain.py`, in the `if get_warehouse is not None:` block where `feat = dict(model_input)` is built (around line 2883), add ONE line after the `feat["model_version"] = ...` assignments and before `record_candidate`:

```python
                    feat["model_version"]  = mscore["model_version"]
                    feat.update(_microstructure_features(coin, data))  # 2026-05-25
```

- [ ] **Step 4: Verify the module imports + a candidate-shaped smoke**

Run: `PYTHONIOENCODING=utf-8 python -c "import core.mcp_brain; print('import ok')"`
Expected: `import ok` (no syntax/reference error).

- [ ] **Step 5: Commit**

```bash
git add core/mcp_brain.py tests/test_microstructure_features.py
git commit -m "feat(features): capture microstructure into candidate features_json"
```

### Task 3: Add the 3 missing keys to FEATURE_KEYS

**Files:**
- Modify: `scripts/train_models.py:50-66` (FEATURE_KEYS)
- Test: extend `tests/test_microstructure_features.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_microstructure_features.py
def test_feature_keys_include_microstructure():
    from scripts.train_models import FEATURE_KEYS
    for k in ("oi_delta_6h", "depth_ratio", "basis_bps",
              "ob_imbalance", "funding_rate"):
        assert k in FEATURE_KEYS, f"{k} missing from FEATURE_KEYS"


def test_load_dataset_tolerates_missing_microstructure():
    """A candidate features_json lacking the new keys must coerce to 0.0,
    not crash (proves forward-collection back-compat for old candidates)."""
    from scripts.train_models import FEATURE_KEYS, _coerce
    old_feats = {"score": 66, "rsi_1h": 50}  # pre-microstructure candidate
    vec = [_coerce(old_feats.get(k)) for k in FEATURE_KEYS]
    assert len(vec) == len(FEATURE_KEYS)
    # the microstructure slots are 0.0 (neutral), not errors
    assert vec[FEATURE_KEYS.index("oi_delta_6h")] == 0.0
    assert vec[FEATURE_KEYS.index("depth_ratio")] == 0.0
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_microstructure_features.py::test_feature_keys_include_microstructure -v`
Expected: FAIL — `oi_delta_6h` not in FEATURE_KEYS.

- [ ] **Step 3: Add the 3 keys**

In `scripts/train_models.py`, extend `FEATURE_KEYS` (funding_rate + ob_imbalance already present — add the 3 truly missing):

```python
    "funding_rate",
    "ob_imbalance",
    # 2026-05-25 — microstructure forward-collection (no-edge-forensics).
    # Populated from this date forward via mcp_brain._microstructure_features;
    # historical candidates coerce to 0.0 (neutral).
    "oi_delta_6h",
    "depth_ratio",
    "basis_bps",
]
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_microstructure_features.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/train_models.py tests/test_microstructure_features.py
git commit -m "feat(train): add OI/depth/basis to FEATURE_KEYS (forward-collected)"
```

### Task 4: Microstructure readiness counter

**Files:**
- Create: `scripts/microstructure_readiness.py`

- [ ] **Step 1: Write the script**

```python
# scripts/microstructure_readiness.py
"""How many LABELED candidates carry forward-collected microstructure yet?

Microstructure features only populate from 2026-05-25 forward. Training is
worth running once enough labeled candidates have a non-null `ob_imbalance`
(the forward-only marker). Target >= ~500-1000. Read-only.
"""
from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = 500


def main() -> int:
    c = sqlite3.connect(str(ROOT / "data" / "warehouse.sqlite"))
    # candidates whose features_json has a populated ob_imbalance AND are labeled.
    n = c.execute(
        "SELECT COUNT(*) FROM candidates cd JOIN labels l "
        "  ON l.candidate_id = cd.id "
        "WHERE cd.features_json LIKE '%\"ob_imbalance\"%' "
        "  AND cd.features_json NOT LIKE '%\"ob_imbalance\": 0.0%'",
    ).fetchone()[0]
    total_micro = c.execute(
        "SELECT COUNT(*) FROM candidates "
        "WHERE features_json LIKE '%\"oi_delta_6h\"%'").fetchone()[0]
    c.close()
    print(f"candidates with microstructure logged: {total_micro}")
    print(f"LABELED candidates with populated ob_imbalance: {n} / target {TARGET}")
    print("READY to retrain on microstructure" if n >= TARGET
          else f"NOT READY — need {TARGET - n} more (keep bot running + labels flowing)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 2: Run it (smoke)**

Run: `PYTHONIOENCODING=utf-8 python scripts/microstructure_readiness.py`
Expected: prints counts (likely `0 / 500` immediately after shipping — that's correct; data accumulates forward).

- [ ] **Step 3: Commit**

```bash
git add scripts/microstructure_readiness.py
git commit -m "feat(features): microstructure accumulation readiness counter"
```

### Task 5: Resume label generation + full-suite verify

- [ ] **Step 1: Resume labels**

Run: `PYTHONIOENCODING=utf-8 python scripts/build_labels.py`
Expected: inserts labels for candidates whose 96-bar forward window has price data. Confirm the labels max-date advances past 2026-04-26:
`PYTHONIOENCODING=utf-8 python -c "import sqlite3,datetime as dt; c=sqlite3.connect('data/warehouse.sqlite'); r=c.execute('SELECT MAX(ts),COUNT(*) FROM labels').fetchone(); print('labels max', dt.datetime.fromtimestamp(r[0],dt.UTC).date(), 'n', r[1])"`

NOTE: if `build_labels.py` requires args or a date range, run `python scripts/build_labels.py --help` and follow it. If it needs cached OHLCV that's absent for old candidates, it will label only what it can — that's fine; the goal is to resume forward labeling.

- [ ] **Step 2: Full suite**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/ --tb=short -q`
Expected: all green (prior baseline 1132 + new microstructure tests).

- [ ] **Step 3: Commit any label-pipeline fix**

If Step 1 required a code fix to `build_labels.py` to resume, commit it:

```bash
git add scripts/build_labels.py
git commit -m "fix(labels): resume label generation for forward candidates"
```

---

## Self-review notes

- **Spec coverage:** §Feature-set → Task 1; §Extraction+wiring → Task 2; §Training-integration → Task 3 (corrected: 2 keys already present, add 3); §Label-resume + accumulation → Tasks 4-5; §Validation deferred → no task (weekly retrain + honest gate, already shipped). ✓
- **Correction vs spec:** spec said "append 5 names to FEATURE_KEYS"; reality is `funding_rate` + `ob_imbalance` already present (unpopulated), so only 3 added. spec said "load_dataset must treat missing as NaN"; reality is `_coerce(None)=0.0` already tolerant — no load_dataset change. Both reflected above.
- **0.0-neutrality:** every feature defined so 0.0 is neutral (depth_ratio = log-ratio, basis/oi_delta/funding/imbalance all 0-centered). This makes the existing `_coerce(None)=0.0` impute correct for historical candidates.
- **Naming consistency:** `oi_delta_6h`, `ob_imbalance`, `depth_ratio`, `basis_bps`, `funding_rate` identical across helper, FEATURE_KEYS, tests, readiness counter.
- **Pure capture:** the only production change to a live path is one `feat.update(...)` line after the decision is already made — cannot affect `gate_pass`.
