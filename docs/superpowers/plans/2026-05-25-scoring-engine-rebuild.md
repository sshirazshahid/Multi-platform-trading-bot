# Scoring-Engine Rebuild Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix the ML validation pipeline (label-leakage embargo + honest promotion gate), re-train, and — only if a model clears an honest gate — promote it from entry-augmenter to the sole primary entry signal behind a kill-switch.

**Architecture:** The ML substrate already exists (feature/label store, walk-forward trainer with PBO+DSR, LR+GBM ensemble, isotonic calibration, `MODEL_GATE` entry wiring, `MODEL_GATE_SHADOW` shadow toggle). The rebuild fixes two integrity bugs (embargo < label horizon; promotion gate permits PBO=1.0/DSR=0), re-trains honestly, validates in shadow, then flips one flag so the model *replaces* the anti-monotonic rule score rather than augmenting it.

**Tech Stack:** Python, scikit-learn (LR/GBM), SQLite warehouse, pytest. Key files: `core/walk_forward.py`, `core/promotion_gate.py`, `scripts/train_models.py`, `core/mcp_brain.py`, `core/bot_engine.py`, `config.py`.

---

## Context the executor needs

- **Label:** `warehouse.labels` has binary `y` over `label_horizon_bars=96` (96 forward bars).
- **The bug:** the walk-forward CV is invoked with `embargo_bars=24` (< 96). de Prado purging requires embargo ≥ label horizon, else training rows within 96 bars of a test fold carry labels that peek into the test window → leakage. This inflates OOS-WR to 0.80 while PBO=1.0 correctly flags overfit.
- **The gate:** `core/promotion_gate.py` thresholds include `max_pbo: 1.0`, `min_dsr: 0.0` — permissive enough to promote a maximally-overfit model.
- **Entry wiring (already present):** `config.py:246 MODEL_GATE` with `enabled` (default true), `shadow_only` (default false), `threshold_futures` (0.55). When enabled & not shadow, `mcp_brain` blocks candidates with `p_win_ensemble < threshold` (~`mcp_brain.py:2509-2526`). The rule gate (`result["score"] >= 66 and layers_ok >= 6`, ~`mcp_brain.py:2796`) ALSO applies — so today entry needs BOTH (augment). "Replace" = bypass the rule gate when the new flag is on.
- **Run tests with** `PYTHONIOENCODING=utf-8` prefix on Windows.

## File Structure

| File | Create/Modify | Responsibility |
|---|---|---|
| `scripts/leak_check_embargo.py` | Create | Phase A: re-run OOS-WR at embargo 24 vs ≥96, emit verdict |
| `tests/test_walk_forward_embargo.py` | Create | Prove embargo<horizon leaks, embargo≥horizon purges |
| `core/walk_forward.py` | Modify | (only if split() embargo logic is wrong — likely fine) |
| `scripts/train_models.py` | Modify | Derive label_horizon, set `embargo_bars = max(arg, horizon)` |
| `core/promotion_gate.py` | Modify | Tighten `max_pbo 1.0→0.5`, `min_dsr 0.0→0.10` |
| `tests/test_promotion_gate_honest.py` | Create | Gate rejects PBO=1.0 / DSR=0 |
| `scripts/shadow_calibration_check.py` | Create | Phase C: realized-vs-predicted WR over shadow window |
| `config.py` | Modify | Add `MODEL_PRIMARY_ENTRY_ENABLED` flag |
| `core/mcp_brain.py` | Modify | When flag on, bypass rule gate; p_win is sole authority |
| `tests/test_model_primary_entry.py` | Create | Replace-behavior + kill-switch + fail-safe fallback |

---

## PHASE A — Leakage check (read-only verdict)

### Task 1: Leak-check harness + leakage unit test

**Files:**
- Create: `tests/test_walk_forward_embargo.py`
- Create: `scripts/leak_check_embargo.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_walk_forward_embargo.py
"""Embargo must be >= label horizon or the walk-forward leaks future labels."""
from __future__ import annotations
import numpy as np
from core.walk_forward import WalkForward


def _leaky_series(n=400, horizon=96, seed=0):
    """Build X,y where y[i] is determined by X[i+horizon] (pure forward leak).
    A model can only 'predict' y if training rows leak across the test boundary.
    """
    rng = np.random.default_rng(seed)
    x = rng.normal(size=(n, 1))
    y = np.zeros(n, dtype=int)
    for i in range(n - horizon):
        y[i] = int(x[i + horizon, 0] > 0)   # label peeks `horizon` ahead
    return x, y


def test_embargo_below_horizon_leaks_train_into_test():
    """With embargo < horizon, the last train row's label window overlaps
    the test fold (leakage). Assert the overlap exists."""
    x, _ = _leaky_series()
    wf = WalkForward(n_splits=5, embargo_bars=24, anchored=True)
    horizon = 96
    leaked = False
    for tr, te in wf.split(x):
        if len(tr) == 0 or len(te) == 0:
            continue
        gap = te[0] - tr[-1]          # bars between last train and first test
        if gap <= horizon:            # label window of tr[-1] reaches into test
            leaked = True
    assert leaked, "embargo=24 < horizon=96 must produce an overlapping fold"


def test_embargo_at_or_above_horizon_purges():
    """With embargo >= horizon, no train row's label window reaches the test
    fold."""
    x, _ = _leaky_series()
    wf = WalkForward(n_splits=5, embargo_bars=96, anchored=True)
    horizon = 96
    for tr, te in wf.split(x):
        if len(tr) == 0 or len(te) == 0:
            continue
        gap = te[0] - tr[-1]
        assert gap > horizon - 1, (
            f"embargo>=horizon must purge; got gap={gap} <= horizon={horizon}")
```

- [ ] **Step 2: Run to verify behaviour**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_walk_forward_embargo.py -v`
Expected: `test_embargo_below_horizon_leaks_train_into_test` PASS (leak exists), `test_embargo_at_or_above_horizon_purges` PASS. If the second FAILS, `WalkForward.split` embargo logic is buggy — fix `core/walk_forward.py:47+` so trailing train rows within `embargo_bars` of test are dropped, then re-run.

- [ ] **Step 3: Write the leak-check harness**

```python
# scripts/leak_check_embargo.py
"""Phase A leak-check: re-run the ensemble OOS-WR at embargo=24 vs embargo>=96.
If the 0.80 OOS-WR collapses toward base rate under embargo>=horizon, the
claimed edge was label leakage. Read-only. Writes a verdict to reports/.
"""
from __future__ import annotations
import sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.train_models import _load_market_xy, _walk_forward_oos, _build_lr_gbm_ensemble  # type: ignore


def _oos_wr(market: str, embargo: int) -> dict:
    X, y, meta = _load_market_xy(market)            # existing loader
    oos, folds, _mat = _walk_forward_oos(
        X, y, n_splits=5, embargo_bars=embargo,
        build_model=_build_lr_gbm_ensemble)
    mask = ~_is_nan(oos)
    pred = (oos[mask] >= 0.5).astype(int)
    yy = y[mask]
    wr = float((pred[yy == 1] == 1).mean()) if (yy == 1).any() else 0.0
    return {"embargo": embargo, "n_oos": int(mask.sum()),
            "base_rate": float(yy.mean()), "wr_at_0.5": wr}


def _is_nan(a):
    import numpy as np
    return np.isnan(a)


def main() -> int:
    market = "futures"
    lo = _oos_wr(market, 24)
    hi = _oos_wr(market, 96)
    verdict = ("LEAKAGE CONFIRMED" if (lo["wr_at_0.5"] - hi["wr_at_0.5"]) > 0.10
               else "no large embargo effect")
    out = Path("reports/leak_check_2026-05-25.md")
    out.parent.mkdir(exist_ok=True)
    out.write_text(
        f"# Leak check {market}\n\n"
        f"- embargo=24: WR@0.5={lo['wr_at_0.5']:.3f} n={lo['n_oos']} base={lo['base_rate']:.3f}\n"
        f"- embargo=96: WR@0.5={hi['wr_at_0.5']:.3f} n={hi['n_oos']} base={hi['base_rate']:.3f}\n"
        f"- delta: {lo['wr_at_0.5'] - hi['wr_at_0.5']:+.3f}\n\n"
        f"**Verdict: {verdict}**\n", encoding="utf-8")
    print(out.read_text(encoding="utf-8"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

NOTE: `_load_market_xy`, `_walk_forward_oos`, `_build_lr_gbm_ensemble` are the existing helpers in `scripts/train_models.py`. If their names differ, grep `scripts/train_models.py` for the loader + walk-forward fn and adjust the import. Do NOT reimplement them.

- [ ] **Step 4: Commit**

```bash
git add tests/test_walk_forward_embargo.py scripts/leak_check_embargo.py
git commit -m "feat(leak-check): embargo>=horizon harness + walk-forward leak tests"
```

### Task 2: Run the leak-check, record the verdict

- [ ] **Step 1: Run it**

Run: `PYTHONIOENCODING=utf-8 python scripts/leak_check_embargo.py`
Expected: prints the verdict block; writes `reports/leak_check_2026-05-25.md`. Likely outcome: `LEAKAGE CONFIRMED` (embargo=96 WR collapses toward base_rate ~0.14).

- [ ] **Step 2: Commit the verdict**

```bash
git add reports/leak_check_2026-05-25.md
git commit -m "docs(leak-check): Phase A verdict on the 0.80 OOS-WR claim"
```

**DECISION GATE 1:** If verdict = leakage → proceed to Phase B (fix embargo). If verdict = no effect → the contradiction is elsewhere (PBO computation / label join); STOP and report before re-training.

---

## PHASE B — Embargo fix + honest gate + retrain

### Task 3: Enforce embargo >= label horizon in the trainer

**Files:**
- Modify: `scripts/train_models.py` (the `train_one_market` fn / embargo arg)
- Test: extend `tests/test_walk_forward_embargo.py`

- [ ] **Step 1: Write the failing test**

```python
# append to tests/test_walk_forward_embargo.py
def test_trainer_clamps_embargo_to_label_horizon():
    """train_models must not run CV with embargo < label_horizon_bars."""
    from scripts.train_models import _effective_embargo
    # horizon 96, requested 24 -> clamp to 96
    assert _effective_embargo(requested=24, label_horizon=96) == 96
    # requested already above horizon -> keep
    assert _effective_embargo(requested=120, label_horizon=96) == 120
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_walk_forward_embargo.py::test_trainer_clamps_embargo_to_label_horizon -v`
Expected: FAIL — `_effective_embargo` not defined.

- [ ] **Step 3: Implement `_effective_embargo` + use it**

```python
# scripts/train_models.py — add helper near the top-level functions
def _effective_embargo(requested: int, label_horizon: int) -> int:
    """Embargo must be >= label horizon (de Prado purging) or the
    walk-forward leaks forward labels across the test boundary."""
    return max(int(requested), int(label_horizon))
```

Then in `train_one_market`, BEFORE calling `_walk_forward_oos`, derive the label horizon from the loaded labels (the labels carry `label_horizon_bars`; if the loader doesn't expose it, read `SELECT MAX(label_horizon_bars) FROM labels` once) and clamp:

```python
    label_horizon = _load_label_horizon(market)          # MAX(label_horizon_bars), default 96
    embargo_bars = _effective_embargo(embargo_bars, label_horizon)
    logger.info(f"[train] {market}: embargo clamped to {embargo_bars} (horizon={label_horizon})")
```

Add `_load_label_horizon`:

```python
def _load_label_horizon(market: str) -> int:
    import sqlite3
    try:
        c = sqlite3.connect("data/warehouse.sqlite")
        row = c.execute(
            "SELECT MAX(label_horizon_bars) FROM labels WHERE market_type=?",
            (market,)).fetchone()
        c.close()
        return int(row[0]) if row and row[0] else 96
    except Exception:
        return 96
```

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_walk_forward_embargo.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add scripts/train_models.py tests/test_walk_forward_embargo.py
git commit -m "fix(train): clamp walk-forward embargo to label horizon (96)"
```

### Task 4: Tighten the promotion gate to honest thresholds

**Files:**
- Modify: `core/promotion_gate.py`
- Test: `tests/test_promotion_gate_honest.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_promotion_gate_honest.py
"""Honest gate must REJECT the overfit profile that was previously promoted."""
from core.promotion_gate import _passes_gate  # the pure predicate


def _diag(**kw):
    base = dict(oos_wr=0.80, auc_ensemble=0.76, n_oos=4650,
                wr_uplift=5.7, deflated_sharpe=0.0008, pbo=1.0)
    base.update(kw)
    return base


def test_rejects_max_pbo():
    assert _passes_gate(_diag(pbo=1.0)) is False


def test_rejects_zero_dsr():
    assert _passes_gate(_diag(deflated_sharpe=0.0, pbo=0.3)) is False


def test_accepts_honest_strong_model():
    assert _passes_gate(_diag(pbo=0.2, deflated_sharpe=0.4,
                              oos_wr=0.58, auc_ensemble=0.64)) is True
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_promotion_gate_honest.py -v`
Expected: FAIL — either `_passes_gate` not importable, or the current permissive thresholds let PBO=1.0 through.

- [ ] **Step 3: Tighten thresholds + expose the predicate**

In `core/promotion_gate.py`, locate the thresholds dict (the one mirrored into the ensemble diag: `min_oos, min_oos_wr, min_auc, min_wr_uplift, min_dsr, max_pbo`). Change:
- `max_pbo`: `1.0` → `0.5`
- `min_dsr`: `0.0` → `0.10`
(keep `min_oos 200`, `min_oos_wr 0.55`, `min_auc 0.60`, `min_wr_uplift 1.5`)

Ensure a pure predicate exists (extract if the logic is inline in `promote_if_eligible`):

```python
GATE = {"min_oos": 200, "min_oos_wr": 0.55, "min_auc": 0.60,
        "min_wr_uplift": 1.5, "min_dsr": 0.10, "max_pbo": 0.5}

def _passes_gate(diag: dict) -> bool:
    return (
        diag.get("n_oos", 0)          >= GATE["min_oos"]
        and diag.get("oos_wr", 0)     >= GATE["min_oos_wr"]
        and diag.get("auc_ensemble", 0) >= GATE["min_auc"]
        and diag.get("wr_uplift", 0)  >= GATE["min_wr_uplift"]
        and diag.get("deflated_sharpe", 0) >= GATE["min_dsr"]
        and diag.get("pbo", 1.0)      <= GATE["max_pbo"]
    )
```

`promote_if_eligible` should call `_passes_gate(diag)` for its decision.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_promotion_gate_honest.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add core/promotion_gate.py tests/test_promotion_gate_honest.py
git commit -m "fix(gate): honest promotion thresholds (max_pbo 0.5, min_dsr 0.10)"
```

### Task 5: Re-train under the fixed pipeline (DECISION GATE 2)

- [ ] **Step 1: Retrain**

Run: `PYTHONIOENCODING=utf-8 python scripts/train_models.py --auto-promote`
Expected: logs the clamped embargo (96) and the honest diag (PBO, DSR, OOS-WR). The gate prints `PROMOTED` or `HELD`.

- [ ] **Step 2: Record the outcome**

Capture the diag of the newly-trained model (`data/models/ensemble_futures_latest.json` if promoted, else the trainer stdout).

**DECISION GATE 2 — the honest fork:**
- **Model PROMOTED** (cleared honest gate: PBO≤0.5, DSR≥0.10, OOS-WR≥0.55) → proceed to Phase C.
- **Model HELD** (no model clears the honest gate — the likely outcome once leakage is removed) → **STOP. Report the null result:** "Once label leakage is removed, no model clears an honest gate — the current features lack validated edge. Promoting microstructure/OI to scored features is the next lever (separate effort)." Do NOT proceed to C/D. Leave the rule-score path live.

---

## PHASE C — Shadow validation (only if Gate 2 PROMOTED)

### Task 6: Shadow-calibration check script

**Files:**
- Create: `scripts/shadow_calibration_check.py`

- [ ] **Step 1: Write the script**

```python
# scripts/shadow_calibration_check.py
"""Phase C: over the shadow window, compare the model's predicted p_win to
realized WR of the trades it WOULD have entered. Calibration holds if
|realized - predicted| < 0.10 over >= 30 decisions. Read-only.

Run the bot first with: MODEL_GATE_SHADOW=true python main.py  (>=30 decisions)
"""
from __future__ import annotations
import sqlite3, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    c = sqlite3.connect(str(ROOT / "data" / "warehouse.sqlite"))
    c.row_factory = sqlite3.Row
    rows = c.execute(
        "SELECT s.p_win, t.realized_pnl FROM shadow_decisions s "
        "JOIN trades t ON s.candidate_id = t.candidate_id "
        "WHERE s.would_enter=1 AND t.status='CLOSED' "
        "AND t.realized_pnl IS NOT NULL").fetchall()
    n = len(rows)
    if n < 30:
        print(f"insufficient shadow window: {n} matched decisions (<30). "
              f"Run MODEL_GATE_SHADOW=true longer.")
        return 1
    predicted = sum(r["p_win"] for r in rows) / n
    realized = sum(1 for r in rows if r["realized_pnl"] > 0) / n
    gap = abs(realized - predicted)
    print(f"shadow decisions: {n}")
    print(f"  predicted p_win avg: {predicted:.3f}")
    print(f"  realized WR:         {realized:.3f}")
    print(f"  |gap|:               {gap:.3f}")
    print("VERDICT:", "CALIBRATED — ok to promote" if gap < 0.10
          else "MISCALIBRATED — do NOT promote")
    return 0 if gap < 0.10 else 2


if __name__ == "__main__":
    raise SystemExit(main())
```

NOTE: confirm the `shadow_decisions` columns (`p_win`, `would_enter`, `candidate_id`) via `PRAGMA table_info(shadow_decisions)`; adjust names if needed. If `shadow_decisions` lacks `p_win`/`would_enter`, add them to the shadow-logging write in `core/mcp_brain` (the model-gate path) as a prerequisite sub-task.

- [ ] **Step 2: Commit**

```bash
git add scripts/shadow_calibration_check.py
git commit -m "feat(shadow): calibration check (realized vs predicted WR)"
```

- [ ] **Step 3: Operator runs shadow window**

Operator: `MODEL_GATE_SHADOW=true python main.py` for ≥30 model decisions, then `python scripts/shadow_calibration_check.py`.

**DECISION GATE 3:** CALIBRATED (gap<0.10) → Phase D. MISCALIBRATED → STOP, report; do not promote.

---

## PHASE D — Replace rule score + kill-switch (only if Gate 3 CALIBRATED)

### Task 7: `MODEL_PRIMARY_ENTRY_ENABLED` — model becomes sole entry authority

**Files:**
- Modify: `config.py`
- Modify: `core/mcp_brain.py` (rule-gate bypass when flag on)
- Test: `tests/test_model_primary_entry.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_model_primary_entry.py
"""When MODEL_PRIMARY_ENTRY_ENABLED, the model p_win is the SOLE entry gate;
the anti-monotonic rule score is bypassed. Kill-switch reverts. Fail-safe:
model unavailable -> fall back to rule gate (never naked)."""
import importlib, sys


def _reload_config(monkeypatch, val):
    monkeypatch.setenv("MODEL_PRIMARY_ENTRY_ENABLED", val)
    sys.modules.pop("config", None)
    return importlib.import_module("config")


def test_flag_default_false(monkeypatch):
    cfg = _reload_config(monkeypatch, "false")
    assert cfg.MODEL_PRIMARY_ENTRY_ENABLED is False


def test_entry_decision_replace_semantics():
    """Pure predicate: when primary-entry on, a high p_win with a FAILING
    rule score still enters; with primary-entry off, both must pass."""
    from core.mcp_brain import _entry_allowed
    # primary on: p_win passes, rule fails -> ENTER (replace)
    assert _entry_allowed(p_win=0.62, thr=0.55, rule_ok=False,
                          primary=True, model_ok=True) is True
    # primary off: rule fails -> NO ENTER (augment)
    assert _entry_allowed(p_win=0.62, thr=0.55, rule_ok=False,
                          primary=False, model_ok=True) is False
    # primary on but model unavailable -> fall back to rule gate
    assert _entry_allowed(p_win=float("nan"), thr=0.55, rule_ok=True,
                          primary=True, model_ok=False) is True
    assert _entry_allowed(p_win=float("nan"), thr=0.55, rule_ok=False,
                          primary=True, model_ok=False) is False
```

- [ ] **Step 2: Run to verify it fails**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_model_primary_entry.py -v`
Expected: FAIL — `MODEL_PRIMARY_ENTRY_ENABLED` / `_entry_allowed` not defined.

- [ ] **Step 3: Add the flag + the pure predicate + wire it**

```python
# config.py — near MODEL_GATE
MODEL_PRIMARY_ENTRY_ENABLED = (
    os.getenv("MODEL_PRIMARY_ENTRY_ENABLED", "false").lower() == "true"
)
```

```python
# core/mcp_brain.py — pure predicate (module level)
def _entry_allowed(*, p_win, thr, rule_ok, primary, model_ok) -> bool:
    """Decide entry. primary=True -> model is sole authority (rule bypassed)
    UNLESS the model is unavailable, in which case fall back to the rule gate
    (fail-safe: never trade naked, never block-all on a missing model)."""
    if primary and model_ok:
        return bool(p_win >= thr)
    if primary and not model_ok:
        return bool(rule_ok)          # fail-safe fallback
    return bool(rule_ok and model_ok and p_win >= thr)   # augment (legacy)
```

Then at the existing model-gate block (~`core/mcp_brain.py:2509-2526`) replace the inline `rule_ok AND p_win>=thr` decision with a call to `_entry_allowed(...)`, passing `primary=config.MODEL_PRIMARY_ENTRY_ENABLED` and `model_ok=(mscore["model_version"] is not None)`.

- [ ] **Step 4: Run to verify it passes**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/test_model_primary_entry.py -v`
Expected: all PASS.

- [ ] **Step 5: Commit**

```bash
git add config.py core/mcp_brain.py tests/test_model_primary_entry.py
git commit -m "feat(entry): MODEL_PRIMARY_ENTRY_ENABLED — model replaces rule score (kill-switch)"
```

### Task 8: Full-suite regression + kill-switch verification

- [ ] **Step 1: Kill-switch sanity**

Run: `PYTHONIOENCODING=utf-8 MODEL_PRIMARY_ENTRY_ENABLED=false python -c "import config; assert config.MODEL_PRIMARY_ENTRY_ENABLED is False; print('kill-switch OFF ok')"`
Then: `MODEL_PRIMARY_ENTRY_ENABLED=true python -c "import config; assert config.MODEL_PRIMARY_ENTRY_ENABLED is True; print('primary ON ok')"`

- [ ] **Step 2: Full suite**

Run: `PYTHONIOENCODING=utf-8 python -m pytest tests/ --tb=short -q`
Expected: all green (prior baseline 1125 + new tests).

- [ ] **Step 3: Final commit**

```bash
git add -A
git commit -m "test: full-suite green after scoring-engine validation rebuild"
```

---

## Self-review notes

- Phases C and D are explicitly gated behind DECISION GATE 2 (a model must clear the honest gate). If it doesn't — the likely outcome — the plan terminates at Task 5 with a null-result report, and the rule-score path stays live. This is by design (spec success/stop criteria).
- Kill-switch: `MODEL_PRIMARY_ENTRY_ENABLED=false` (default) reverts to augment/legacy; `MODEL_GATE_ENABLED=false` disables the model gate entirely. Two independent backstops.
- Fail-safe: model unavailable → `_entry_allowed` falls back to the rule gate; never trades naked, never blocks-all.
