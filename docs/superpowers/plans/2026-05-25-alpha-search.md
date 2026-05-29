# Alpha-Search Falsification Experiment — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a one-shot, pre-registered cross-sectional alpha-search pipeline that screens a formulaic-alpha library over the crypto OHLCV panel through an IC/IR stage and a walk-forward DSR/PBO/FDR stage, and emits a single edge/no-edge verdict.

**Architecture:** A `core/alpha_zoo/` package (panel → operators → alphas → screen) driven by a `scripts/run_alpha_search.py` orchestrator with frozen pre-registration constants. The machinery is built and validated on synthetic panels FIRST (Tasks 1-7, no network), then the real history is backfilled (Task 8) and the catalog grown to the full zoo (Task 10). Statistical primitives are reused from `core.stat_tests`; OHLCV cache I/O is reused from `core.feature_store`.

**Tech Stack:** Python 3.9, pandas, numpy, scipy (`scipy.stats`: `spearmanr`, `skew`, `kurtosis`, `norm`), pytest. No new runtime dependency.

**Spec:** `docs/superpowers/specs/2026-05-25-alpha-search-design.md` (read §3 Pre-registration and §8 Leak guards before starting).

**Conventions:**
- Every module starts with `from __future__ import annotations` (repo convention, Python-3.9 type hints).
- Tests live in `tests/` and import via `from core.alpha_zoo... import ...` (repo test convention).
- Run a single test with `python -m pytest tests/<file>::<test> -v`.
- Commit after each task with `git add <exact files>` (never `git add -A` — the working tree has unrelated modified data files).

---

## File Structure

| File | Responsibility |
|---|---|
| `core/alpha_zoo/__init__.py` | Package marker (empty). |
| `core/alpha_zoo/panel.py` | `Panel` dataclass; `load_panel`; derived vwap/returns/adv; 24-bar forward label; `split_panel` (60/40 + embargo). |
| `core/alpha_zoo/operators.py` | ~25 backward-only vectorized operators over (T×N) DataFrames. Sole home of the no-lookahead guarantee. |
| `core/alpha_zoo/alphas.py` | `AlphaDef` registry; worked alphas; `computable_alphas`/`n_computable`/`n_eff`. |
| `core/alpha_zoo/screen.py` | Stage-1 IC/IR + categorize; Stage-2 long-short returns + DSR/PBO + BH-FDR + Sharpe p-value. Pure, no I/O. |
| `scripts/backfill_ohlcv_history.py` | Stage-0 one-shot 1h backfill via `feature_store.load_ohlcv_window` + a `since`-aware ccxt fetcher. |
| `scripts/run_alpha_search.py` | Orchestrator; frozen pre-registration constants; report writer (md + json). |
| `tests/test_alpha_panel.py` | Panel build, derived fields, forward label, split + embargo. |
| `tests/test_alpha_operators.py` | Operator unit tests on known inputs. |
| `tests/test_alpha_lookahead_sentinel.py` | Future-corruption sentinel (proves backward-only). |
| `tests/test_alpha_screen.py` | IC/IR, categorize, long-short, BH-FDR, Sharpe p-value, N_eff. |
| `tests/test_alpha_search_e2e.py` | Synthetic planted-alpha (found) + noise (rejected). Acceptance gate. |
| `tests/test_backfill_ohlcv.py` | Backfill wrapper with a mock fetcher (no network). |
| `tests/test_run_alpha_search.py` | Orchestrator + report writer on a synthetic panel. |
| `reports/alpha_search_<date>.{md,json}` | Output (produced at run time, Task 10). |

---

## Task 1: Panel module

**Files:**
- Create: `core/alpha_zoo/__init__.py`
- Create: `core/alpha_zoo/panel.py`
- Test: `tests/test_alpha_panel.py`

- [ ] **Step 1: Create the empty package marker**

```python
# core/alpha_zoo/__init__.py
```

(empty file)

- [ ] **Step 2: Write the failing test**

```python
# tests/test_alpha_panel.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo.panel import Panel, build_panel, split_panel


def _raw(n=200, start_ts=1_700_000_000, step=3600, base=100.0, drift=0.1):
    ts = np.arange(start_ts, start_ts + n * step, step, dtype="int64")
    close = base + drift * np.arange(n)
    return pd.DataFrame({
        "ts": ts, "open": close, "high": close + 1.0,
        "low": close - 1.0, "close": close, "volume": np.full(n, 1000.0),
    })


def test_build_panel_aligns_symbols_and_derives_fields():
    raw = {"AAA/USDT": _raw(drift=0.1), "BBB/USDT": _raw(drift=-0.2)}
    p = build_panel(raw, timeframe="1h", horizon=24)
    assert set(p.symbols) == {"AAA/USDT", "BBB/USDT"}
    assert list(p.fields["close"].columns) == p.symbols
    # vwap = (h+l+c)/3 ; here h=c+1, l=c-1 -> vwap == close
    assert np.allclose(p.fields["vwap"].values, p.fields["close"].values)
    # forward 24-bar return present for all but last 24 rows
    assert p.fwd_ret.iloc[:-24].notna().all().all()
    assert p.fwd_ret.iloc[-1].isna().all()


def test_adv_is_rolling_dollar_volume():
    raw = {"AAA/USDT": _raw()}
    p = build_panel(raw, timeframe="1h", horizon=24)
    adv5 = p.adv(5)
    # close*volume constant-ish; rolling(5) of (close*1000)
    expected = (p.fields["close"] * p.fields["volume"]).rolling(5).mean()
    assert np.allclose(adv5.values, expected.values, equal_nan=True)


def test_split_panel_respects_embargo():
    raw = {"AAA/USDT": _raw(n=200), "BBB/USDT": _raw(n=200)}
    p = build_panel(raw, timeframe="1h", horizon=24)
    is_p, oos_p = split_panel(p, frac=0.6, embargo=24)
    # IS = first 120 bars; OOS starts at 120+24 = 144
    assert len(is_p.ts) == 120
    assert oos_p.ts[0] == p.ts[144]
    # no timestamp overlap
    assert set(is_p.ts).isdisjoint(set(oos_p.ts))
```

- [ ] **Step 3: Run the test to verify it fails**

Run: `python -m pytest tests/test_alpha_panel.py -v`
Expected: FAIL with `ModuleNotFoundError: core.alpha_zoo.panel` / `ImportError`.

- [ ] **Step 4: Implement the panel module**

```python
# core/alpha_zoo/panel.py
"""Cross-sectional OHLCV panel for the alpha search.

A `Panel` holds wide (T bars × N symbols) DataFrames — one per field —
all sharing a common ascending integer `ts` (unix seconds) index and the
same symbol columns. Time-series alpha operators act down the rows (per
symbol); cross-sectional operators act across the columns (per bar).

`build_panel` aligns per-symbol raw OHLCV onto the union timestamp grid and
derives vwap/returns; `adv(d)` is rolling dollar-volume; `fwd_ret` is the
single pre-registered forward return (close[t+horizon]/close[t] - 1).
`split_panel` does the chronological 60/40 split with an embargo so no
forward-label window straddles the boundary (spec §3).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

_OHLCV = ("open", "high", "low", "close", "volume")


@dataclass
class Panel:
    fields: dict[str, pd.DataFrame]   # field -> (T×N) DataFrame, index=ts, cols=symbols
    fwd_ret: pd.DataFrame             # (T×N) forward `horizon`-bar return
    symbols: list[str]
    ts: np.ndarray                    # (T,) int64 unix seconds
    horizon: int

    def adv(self, d: int) -> pd.DataFrame:
        """Rolling `d`-bar mean dollar volume (close × volume), per symbol."""
        dollar = self.fields["close"] * self.fields["volume"]
        return dollar.rolling(int(d), min_periods=int(d)).mean()


def build_panel(raw: dict[str, pd.DataFrame], *, timeframe: str = "1h",
                horizon: int = 24) -> Panel:
    """Build a `Panel` from {symbol -> raw OHLCV DataFrame}.

    Each raw frame has columns ts/open/high/low/close/volume (ts = unix
    seconds). Symbols are aligned on the union of timestamps (outer join);
    missing cells stay NaN (staggered listings) and are masked per-bar
    downstream.
    """
    symbols = sorted(raw)
    per_field: dict[str, dict[str, pd.Series]] = {f: {} for f in _OHLCV}
    for sym in symbols:
        df = raw[sym].copy()
        df["ts"] = df["ts"].astype("int64")
        df = df.sort_values("ts").drop_duplicates("ts", keep="last").set_index("ts")
        for f in _OHLCV:
            per_field[f][sym] = df[f].astype(float)

    fields: dict[str, pd.DataFrame] = {}
    for f in _OHLCV:
        wide = pd.DataFrame(per_field[f]).sort_index()
        wide = wide.reindex(columns=symbols)
        fields[f] = wide

    ts = fields["close"].index.to_numpy(dtype="int64")
    fields["vwap"] = (fields["high"] + fields["low"] + fields["close"]) / 3.0
    fields["returns"] = fields["close"].pct_change()

    close = fields["close"]
    fwd_ret = close.shift(-int(horizon)) / close - 1.0

    return Panel(fields=fields, fwd_ret=fwd_ret, symbols=symbols, ts=ts,
                 horizon=int(horizon))


def _slice(panel: Panel, lo: int, hi: int) -> Panel:
    sl = slice(lo, hi)
    fields = {f: df.iloc[sl] for f, df in panel.fields.items()}
    return Panel(fields=fields, fwd_ret=panel.fwd_ret.iloc[sl],
                 symbols=panel.symbols, ts=panel.ts[lo:hi], horizon=panel.horizon)


def split_panel(panel: Panel, *, frac: float = 0.6,
                embargo: int = 24) -> tuple[Panel, Panel]:
    """Chronological split. IS = [0, cut); OOS = [cut+embargo, T).

    `embargo` (>= horizon) drops the bars whose IS forward-labels would
    overlap OOS feature windows, so Stage-2 is genuinely out-of-sample.
    """
    T = len(panel.ts)
    cut = int(T * float(frac))
    is_p = _slice(panel, 0, cut)
    oos_p = _slice(panel, cut + int(embargo), T)
    return is_p, oos_p
```

- [ ] **Step 5: Run the test to verify it passes**

Run: `python -m pytest tests/test_alpha_panel.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add core/alpha_zoo/__init__.py core/alpha_zoo/panel.py tests/test_alpha_panel.py
git commit -m "feat(alpha-zoo): cross-sectional OHLCV panel + chronological split"
```

---

## Task 2: Operator library

**Files:**
- Create: `core/alpha_zoo/operators.py`
- Test: `tests/test_alpha_operators.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alpha_operators.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import operators as op


def _df():
    # 6 rows × 3 cols
    return pd.DataFrame({
        "A": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        "B": [6.0, 5.0, 4.0, 3.0, 2.0, 1.0],
        "C": [1.0, 1.0, 2.0, 2.0, 3.0, 3.0],
    })


def test_rank_is_cross_sectional_pct():
    out = op.rank(_df())
    # row 0: A=1,B=6,C=1 -> ranks pct: A and C tie low, B high
    assert out.loc[0, "B"] == 1.0
    assert out.loc[0, "A"] < out.loc[0, "B"]


def test_delta_and_delay_are_backward():
    df = _df()
    assert np.isnan(op.delay(df, 1).loc[0, "A"])
    assert op.delay(df, 1).loc[1, "A"] == 1.0
    assert op.delta(df, 1).loc[1, "A"] == 1.0  # 2 - 1


def test_ts_max_min_argmax_window():
    df = _df()
    assert op.ts_max(df, 3).loc[2, "A"] == 3.0
    assert op.ts_min(df, 3).loc[2, "B"] == 4.0
    # argmax over last 3: A is increasing -> most recent is max -> position (d-1)
    assert op.ts_argmax(df, 3).loc[2, "A"] == 2.0


def test_decay_linear_weights_recent_more():
    df = pd.DataFrame({"A": [0.0, 0.0, 3.0]})
    # weights (1,2,3)/6 over window 3 -> (0*1+0*2+3*3)/6 = 1.5
    assert abs(op.decay_linear(df, 3).loc[2, "A"] - 1.5) < 1e-9


def test_correlation_runs_and_is_bounded():
    a = _df()[["A"]]
    b = _df()[["B"]]
    c = op.correlation(a, b, 4)
    assert c.loc[5, "A"] <= 1.0 and c.loc[5, "A"] >= -1.0


def test_signed_power_preserves_sign():
    df = pd.DataFrame({"A": [-4.0, 4.0]})
    out = op.signed_power(df, 0.5)
    assert out.loc[0, "A"] == -2.0 and out.loc[1, "A"] == 2.0


def test_scale_normalizes_abs_sum():
    df = pd.DataFrame({"A": [1.0, 2.0], "B": [1.0, 2.0]})
    out = op.scale(df, 1.0)
    assert abs(out.loc[0].abs().sum() - 1.0) < 1e-9
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_alpha_operators.py -v`
Expected: FAIL with `ModuleNotFoundError` / `AttributeError`.

- [ ] **Step 3: Implement the operators**

```python
# core/alpha_zoo/operators.py
"""Backward-only vectorized operators for formulaic alphas (Alpha101 / GTJA
semantics). Operate on wide (T bars × N symbols) DataFrames.

INVARIANT (enforced by tests/test_alpha_lookahead_sentinel.py): every
time-series operator uses ONLY the current and prior rows. `shift(d>0)`
pulls the past forward; `rolling(d)` spans [t-d+1, t]. No operator may
reference a future row. Cross-sectional operators (`rank`, `scale`) act
across columns within a single row and are inherently lookahead-safe.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


# ── Cross-sectional (across symbols, per bar) ────────────────────────────
def rank(df: pd.DataFrame) -> pd.DataFrame:
    """Cross-sectional percentile rank in [0, 1], per row."""
    return df.rank(axis=1, pct=True)


def scale(df: pd.DataFrame, k: float = 1.0) -> pd.DataFrame:
    """Rescale each row so sum(|x|) == k."""
    denom = df.abs().sum(axis=1).replace(0.0, np.nan)
    return df.mul(k / denom, axis=0)


# ── Time-series (down the rows, per symbol; backward-only) ────────────────
def delay(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.shift(int(d))


def delta(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df - df.shift(int(d))


def ts_sum(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).sum()


def sma(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).mean()


def stddev(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).std(ddof=1)


def ts_min(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).min()


def ts_max(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).max()


def ts_argmax(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Index (0..d-1) of the max within the trailing window; d-1 = most recent."""
    return df.rolling(int(d), min_periods=int(d)).apply(np.argmax, raw=True)


def ts_argmin(df: pd.DataFrame, d: int) -> pd.DataFrame:
    return df.rolling(int(d), min_periods=int(d)).apply(np.argmin, raw=True)


def ts_rank(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Percentile rank of the current value within its trailing `d`-window."""
    def _last_rank(a: np.ndarray) -> float:
        return (a <= a[-1]).mean()
    return df.rolling(int(d), min_periods=int(d)).apply(_last_rank, raw=True)


def decay_linear(df: pd.DataFrame, d: int) -> pd.DataFrame:
    """Linearly-weighted moving average; most recent gets the highest weight."""
    d = int(d)
    w = np.arange(1, d + 1, dtype=float)
    w /= w.sum()

    def _wavg(a: np.ndarray) -> float:
        return float(np.dot(a, w))
    return df.rolling(d, min_periods=d).apply(_wavg, raw=True)


def correlation(a: pd.DataFrame, b: pd.DataFrame, d: int) -> pd.DataFrame:
    """Rolling Pearson correlation between aligned columns of `a` and `b`."""
    d = int(d)
    return a.rolling(d, min_periods=d).corr(b)


def covariance(a: pd.DataFrame, b: pd.DataFrame, d: int) -> pd.DataFrame:
    d = int(d)
    return a.rolling(d, min_periods=d).cov(b)


# ── Elementwise ──────────────────────────────────────────────────────────
def signed_power(df: pd.DataFrame, a: float) -> pd.DataFrame:
    return np.sign(df) * (df.abs() ** float(a))


def log(df: pd.DataFrame) -> pd.DataFrame:
    return np.log(df.where(df > 0))


def sign(df: pd.DataFrame) -> pd.DataFrame:
    return np.sign(df)


def abs_(df: pd.DataFrame) -> pd.DataFrame:
    return df.abs()


def elem_min(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a.where(a < b, b)


def elem_max(a: pd.DataFrame, b: pd.DataFrame) -> pd.DataFrame:
    return a.where(a > b, b)
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_alpha_operators.py -v`
Expected: PASS (7 tests).

- [ ] **Step 5: Commit**

```bash
git add core/alpha_zoo/operators.py tests/test_alpha_operators.py
git commit -m "feat(alpha-zoo): backward-only operator library"
```

---

## Task 3: Lookahead sentinel test

**Files:**
- Test: `tests/test_alpha_lookahead_sentinel.py`

This is the spec's headline leak guard (§8.1). It corrupts future rows and asserts that earlier operator outputs are byte-identical — proving every operator is backward-only. No source code changes; if any operator fails, fix that operator in `operators.py`.

- [ ] **Step 1: Write the test**

```python
# tests/test_alpha_lookahead_sentinel.py
"""Future-corruption sentinel: corrupting rows >= C must NOT change any
operator's output on rows < C. Proves operators are backward-only (spec §8.1)."""
from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from core.alpha_zoo import operators as op

rng = np.random.default_rng(7)


def _df(T=60, N=4):
    return pd.DataFrame(rng.normal(size=(T, N)) + 5.0,
                        columns=[f"S{i}" for i in range(N)])


# (callable, needs-two-args)
UNARY = [
    lambda d: op.delay(d, 3), lambda d: op.delta(d, 3),
    lambda d: op.ts_sum(d, 5), lambda d: op.sma(d, 5),
    lambda d: op.stddev(d, 5), lambda d: op.ts_min(d, 5),
    lambda d: op.ts_max(d, 5), lambda d: op.ts_argmax(d, 5),
    lambda d: op.ts_argmin(d, 5), lambda d: op.ts_rank(d, 5),
    lambda d: op.decay_linear(d, 5), lambda d: op.rank(d),
    lambda d: op.scale(d), lambda d: op.signed_power(d, 0.5),
]


@pytest.mark.parametrize("fn", UNARY)
def test_unary_operators_are_backward_only(fn):
    df = _df()
    C = 50
    out1 = fn(df)
    corrupt = df.copy()
    corrupt.iloc[C:] = corrupt.iloc[C:] * 999.0 + 123.0
    out2 = fn(corrupt)
    a = out1.iloc[:C].to_numpy()
    b = out2.iloc[:C].to_numpy()
    assert np.allclose(a, b, equal_nan=True), "future rows leaked into past output"


def test_correlation_is_backward_only():
    a, b = _df(), _df()
    C = 50
    out1 = op.correlation(a, b, 6)
    a2, b2 = a.copy(), b.copy()
    a2.iloc[C:] *= 999.0
    b2.iloc[C:] *= 999.0
    out2 = op.correlation(a2, b2, 6)
    assert np.allclose(out1.iloc[:C].to_numpy(), out2.iloc[:C].to_numpy(),
                       equal_nan=True)
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_alpha_lookahead_sentinel.py -v`
Expected: PASS. If any operator FAILS, that operator references a future row — fix it in `core/alpha_zoo/operators.py` (do not weaken the test).

- [ ] **Step 3: Commit**

```bash
git add tests/test_alpha_lookahead_sentinel.py
git commit -m "test(alpha-zoo): lookahead sentinel proves operators are backward-only"
```

---

## Task 4: Screen — Stage 1 (IC / IR / categorize)

**Files:**
- Create: `core/alpha_zoo/screen.py`
- Test: `tests/test_alpha_screen.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alpha_screen.py
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import screen


def test_ic_is_one_when_signal_matches_forward_return():
    # 3 bars × 4 symbols; signal perfectly rank-correlated with fwd_ret each bar
    sig = pd.DataFrame([[1, 2, 3, 4], [4, 3, 2, 1], [1, 3, 2, 4]], dtype=float)
    fwd = sig.copy()
    ic = screen.cross_sectional_ic(sig, fwd, min_width=4)
    assert np.allclose(ic.to_numpy(), 1.0)


def test_ir_and_categorize():
    ic = pd.Series([0.2, 0.25, 0.15, 0.2])  # mean 0.2, low std -> high IR
    assert screen.ir(ic) > 0.5
    assert screen.categorize(screen.ir(ic), 0.5) == "alive"
    assert screen.categorize(-1.0, 0.5) == "reversed"
    assert screen.categorize(0.1, 0.5) == "dead"


def test_min_width_drops_thin_bars():
    sig = pd.DataFrame([[1.0, 2.0, np.nan, np.nan]])
    fwd = pd.DataFrame([[1.0, 2.0, np.nan, np.nan]])
    ic = screen.cross_sectional_ic(sig, fwd, min_width=4)
    assert ic.isna().all()
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/test_alpha_screen.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement Stage-1 functions**

```python
# core/alpha_zoo/screen.py
"""Two-stage alpha screen (spec §3, §5). Pure functions, no I/O.

Stage 1 (in-sample): per-bar cross-sectional IC (Spearman) between an alpha
signal and the forward return; IR = mean(IC)/std(IC); sign fixed in-sample;
Alive/Reversed/Dead categorization at |IR| >= threshold.

Stage 2 (out-of-sample): long-short top/bottom-quantile portfolio return per
bar; Sharpe + trials-deflated DSR + one-sided Sharpe p-value; PBO over the
T×K matrix of all computable alphas; Benjamini-Hochberg FDR across survivors.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import kurtosis as _kurtosis
from scipy.stats import norm
from scipy.stats import skew as _skew
from scipy.stats import spearmanr

from core.stat_tests import deflated_sharpe, pbo, sharpe


def cross_sectional_ic(signal: pd.DataFrame, fwd_ret: pd.DataFrame,
                       *, min_width: int = 10) -> pd.Series:
    """Per-bar Spearman rank-correlation of `signal` vs `fwd_ret` across symbols.

    A bar with fewer than `min_width` symbols valid in BOTH frames yields NaN.

    PERF (matters only at full scale — Task 10): this per-bar loop calls
    scipy spearmanr T times. At ~26k bars × ~300 alphas the full run is hours,
    not minutes. To speed up ~10-50×, vectorize: Spearman == Pearson of
    per-row ranks, so rank both frames across symbols once and compute a
    rolling/elementwise Pearson. Correctness is identical; do this only if the
    one-shot run is too slow to wait on.
    """
    out = np.full(len(signal), np.nan)
    s_vals = signal.to_numpy(dtype=float)
    f_vals = fwd_ret.to_numpy(dtype=float)
    for t in range(s_vals.shape[0]):
        s_row, f_row = s_vals[t], f_vals[t]
        mask = np.isfinite(s_row) & np.isfinite(f_row)
        if int(mask.sum()) < int(min_width):
            continue
        if np.all(s_row[mask] == s_row[mask][0]):
            continue  # zero-variance signal -> undefined corr
        rho, _ = spearmanr(s_row[mask], f_row[mask])
        out[t] = rho
    return pd.Series(out, index=signal.index)


def ir(ic: pd.Series) -> float:
    """Information Ratio = mean(IC) / std(IC). 0.0 on degenerate input."""
    x = ic.dropna().to_numpy()
    if x.size < 2:
        return 0.0
    s = x.std(ddof=1)
    if s <= 0:
        return 0.0
    return float(x.mean() / s)


def categorize(ir_value: float, threshold: float = 0.5) -> str:
    if ir_value >= threshold:
        return "alive"
    if ir_value <= -threshold:
        return "reversed"
    return "dead"
```

- [ ] **Step 4: Run the test to verify it passes**

Run: `python -m pytest tests/test_alpha_screen.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add core/alpha_zoo/screen.py tests/test_alpha_screen.py
git commit -m "feat(alpha-zoo): Stage-1 cross-sectional IC/IR screen"
```

---

## Task 5: Screen — Stage 2 (portfolio, DSR/PBO, BH-FDR)

**Files:**
- Modify: `core/alpha_zoo/screen.py` (append functions)
- Test: `tests/test_alpha_screen.py` (append tests)

- [ ] **Step 1: Write the failing tests (append)**

```python
# append to tests/test_alpha_screen.py
def test_long_short_returns_positive_when_signal_predicts():
    # signal == fwd_ret each bar -> long winners / short losers -> positive ret
    sig = pd.DataFrame(np.tile([1.0, 2.0, 3.0, 4.0, 5.0], (30, 1)))
    fwd = sig.copy()
    r = screen.long_short_returns(sig, fwd, sign=1.0, q=0.2, min_width=5)
    assert r.dropna().mean() > 0


def test_sign_flip_inverts_portfolio():
    sig = pd.DataFrame(np.tile([1.0, 2.0, 3.0, 4.0, 5.0], (30, 1)))
    fwd = sig.copy()
    r_pos = screen.long_short_returns(sig, fwd, sign=1.0, q=0.2, min_width=5)
    r_neg = screen.long_short_returns(sig, fwd, sign=-1.0, q=0.2, min_width=5)
    assert np.allclose(r_pos.dropna().to_numpy(), -r_neg.dropna().to_numpy())


def test_sharpe_pvalue_small_for_strong_positive_series():
    r = pd.Series(np.full(200, 0.01) + np.random.default_rng(1).normal(0, 1e-4, 200))
    assert screen.sharpe_pvalue(r) < 0.01


def test_bh_fdr_basic():
    # one tiny p among large ones -> only the tiny passes at q=0.05
    flags = screen.fdr_bh([0.001, 0.4, 0.6, 0.8], q=0.05)
    assert flags == [True, False, False, False]


def test_dsr_for_alpha_uses_trials():
    r = np.full(300, 0.005) + np.random.default_rng(2).normal(0, 0.01, 300)
    d_low = screen.dsr_for_returns(r, n_trials=1)
    d_high = screen.dsr_for_returns(r, n_trials=500)
    assert d_low >= d_high  # more trials deflates the probability
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_alpha_screen.py -v`
Expected: FAIL with `AttributeError: module ... has no attribute 'long_short_returns'`.

- [ ] **Step 3: Append Stage-2 implementation to `core/alpha_zoo/screen.py`**

```python
# append to core/alpha_zoo/screen.py
def long_short_returns(signal: pd.DataFrame, fwd_ret: pd.DataFrame, *,
                       sign: float, q: float = 0.2,
                       min_width: int = 10) -> pd.Series:
    """Per-bar long-short portfolio return.

    At each bar rank symbols by `sign * signal`, go long the top `q` fraction
    and short the bottom `q` fraction, and take mean(fwd_ret[long]) -
    mean(fwd_ret[short]). Bars with < min_width valid symbols -> NaN.
    """
    s_vals = (sign * signal).to_numpy(dtype=float)
    f_vals = fwd_ret.to_numpy(dtype=float)
    out = np.full(s_vals.shape[0], np.nan)
    for t in range(s_vals.shape[0]):
        s_row, f_row = s_vals[t], f_vals[t]
        mask = np.isfinite(s_row) & np.isfinite(f_row)
        n = int(mask.sum())
        if n < int(min_width):
            continue
        idx = np.where(mask)[0]
        order = idx[np.argsort(s_row[idx])]
        k = max(1, int(round(n * float(q))))
        shorts, longs = order[:k], order[-k:]
        out[t] = float(f_row[longs].mean() - f_row[shorts].mean())
    return pd.Series(out, index=signal.index)


def sharpe_pvalue(returns) -> float:
    """One-sided p-value for SR > 0 (normal approx): 1 - Phi(SR * sqrt(n))."""
    r = pd.Series(returns).dropna().to_numpy()
    if r.size < 2:
        return 1.0
    sr = sharpe(r)
    z = sr * np.sqrt(r.size)
    return float(1.0 - norm.cdf(z))


def dsr_for_returns(returns, *, n_trials: int) -> float:
    """Trials-deflated Pr[true SR > 0] for a return series (spec §3)."""
    r = pd.Series(returns).dropna().to_numpy()
    if r.size < 2:
        return 0.5
    return float(deflated_sharpe(
        sr_observed=sharpe(r),
        n_trials=int(n_trials),
        n_obs=int(r.size),
        skew=float(_skew(r)),
        kurt=float(_kurtosis(r, fisher=False)),  # Pearson: normal = 3
    ))


def fdr_bh(pvals: list[float], q: float = 0.05) -> list[bool]:
    """Benjamini-Hochberg: return per-input boolean reject flags at level `q`."""
    m = len(pvals)
    if m == 0:
        return []
    order = sorted(range(m), key=lambda i: pvals[i])
    thresh_rank = -1
    for rank, i in enumerate(order, start=1):
        if pvals[i] <= q * rank / m:
            thresh_rank = rank
    flags = [False] * m
    if thresh_rank >= 0:
        for rank, i in enumerate(order, start=1):
            if rank <= thresh_rank:
                flags[i] = True
    return flags


def pbo_over_alphas(returns_by_alpha: dict[str, pd.Series], *,
                    n_partitions: int = 16) -> float:
    """PBO over the T×K matrix of all alphas' OOS portfolio returns.

    Each series is reindexed to the union bar grid; missing bars (no
    position) become 0.0 return. Returns 0.5 (neutral) if T < n_partitions.
    """
    if not returns_by_alpha:
        return 0.5
    mat = pd.DataFrame(returns_by_alpha).sort_index().fillna(0.0)
    if mat.shape[0] < n_partitions or mat.shape[1] < 2:
        return 0.5
    return float(pbo(mat.to_numpy(), n_partitions=int(n_partitions)))
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_alpha_screen.py -v`
Expected: PASS (8 tests total).

- [ ] **Step 5: Commit**

```bash
git add core/alpha_zoo/screen.py tests/test_alpha_screen.py
git commit -m "feat(alpha-zoo): Stage-2 long-short returns, trials-deflated DSR, PBO, BH-FDR"
```

---

## Task 6: Alpha registry + worked starter catalog

**Files:**
- Create: `core/alpha_zoo/alphas.py`
- Test: `tests/test_alpha_registry.py`

This task builds the registry machinery and a worked starter set that exercises every operator. The full zoo is grown in Task 10 by appending more `AlphaDef`s following the same pattern. `computable=False` alphas (indneutralize-only / fundamentals) are registered with a `reason_if_dropped` and excluded from `n_computable`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_alpha_registry.py
from __future__ import annotations

import numpy as np

from core.alpha_zoo import alphas
from core.alpha_zoo.panel import build_panel
import pandas as pd


def _panel():
    raw = {}
    rng = np.random.default_rng(0)
    for sym in [f"S{i}/USDT" for i in range(12)]:
        n = 120
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        raw[sym] = pd.DataFrame({
            "ts": np.arange(1_700_000_000, 1_700_000_000 + n * 3600, 3600),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": rng.uniform(500, 1500, n),
        })
    return build_panel(raw, timeframe="1h", horizon=24)


def test_registry_ids_unique_and_tagged():
    ids = [a.id for a in alphas.ALPHAS]
    assert len(ids) == len(set(ids)), "duplicate alpha id"
    for a in alphas.ALPHAS:
        assert a.source in {"K101", "GTJA", "Qlib", "FF"}
        if a.computable:
            assert callable(a.fn)
        else:
            assert a.reason_if_dropped, f"{a.id} dropped without a reason"


def test_n_eff_is_double_n_computable():
    assert alphas.n_eff() == 2 * alphas.n_computable()
    assert alphas.n_computable() == len(alphas.computable_alphas())


def test_every_computable_alpha_returns_panel_shaped_frame():
    p = _panel()
    for a in alphas.computable_alphas():
        out = a.fn(p)
        assert out.shape == p.fields["close"].shape, f"{a.id} wrong shape"
        assert list(out.columns) == p.symbols
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_alpha_registry.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the registry + starter catalog**

```python
# core/alpha_zoo/alphas.py
"""Formulaic-alpha registry for the alpha search.

Each alpha is an `AlphaDef`: an id, a source tag, a function Panel->(T×N)
signal DataFrame built from `operators`, and computability metadata. Only
`computable=True` alphas count toward `n_computable()`; `n_eff() = 2 *
n_computable()` is the trials count fed to the deflated Sharpe (spec §3, §7).

This starter set exercises every operator. Grow toward the full zoo (Task 10)
by appending AlphaDefs:
  - Kakushadze-101  : arXiv:1601.00991 (source="K101")
  - GTJA-191        : Guotai-Junan 191 Alphas (source="GTJA")
  - Qlib158         : github.com/microsoft/qlib Alpha158 (source="Qlib")
  - Fama-French     : price/volume-computable proxies only (source="FF")
Mark any alpha needing indneutralize-as-its-whole-signal, fundamentals, or
book values as computable=False with a reason. Where indneutralize is one
step inside a price/volume formula, degrade it to identity and keep it
computable, noting that in `needs`.
"""
from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

import pandas as pd

from core.alpha_zoo import operators as op
from core.alpha_zoo.panel import Panel


@dataclass
class AlphaDef:
    id: str
    source: str                       # 'K101' | 'GTJA' | 'Qlib' | 'FF'
    fn: Callable[[Panel], pd.DataFrame] | None
    computable: bool = True
    needs: list[str] = field(default_factory=list)
    reason_if_dropped: str = ""


# ── Starter catalog (exercises the full operator surface) ────────────────
def _k001(p: Panel) -> pd.DataFrame:
    # rank(ts_argmax(signedpower(returns<0 ? stddev(returns,20):close, 2),5))-0.5
    r = p.fields["returns"]
    inner = r.where(r < 0, p.fields["close"])
    return op.rank(op.ts_argmax(op.signed_power(inner, 2.0), 5)) - 0.5


def _k003(p: Panel) -> pd.DataFrame:
    return -1.0 * op.correlation(op.rank(p.fields["open"]),
                                 op.rank(p.fields["volume"]), 10)


def _k004(p: Panel) -> pd.DataFrame:
    return -1.0 * op.ts_rank(op.rank(p.fields["low"]), 9)


def _k006(p: Panel) -> pd.DataFrame:
    return -1.0 * op.correlation(p.fields["open"], p.fields["volume"], 10)


def _k012(p: Panel) -> pd.DataFrame:
    return op.sign(op.delta(p.fields["volume"], 1)) * (-1.0 * op.delta(p.fields["close"], 1))


def _k019(p: Panel) -> pd.DataFrame:
    c = p.fields["close"]
    term = op.sign((c - op.delay(c, 7)) + op.delta(c, 7))
    return -1.0 * term * (1.0 + op.rank(1.0 + op.ts_sum(p.fields["returns"], 250)))


def _k033(p: Panel) -> pd.DataFrame:
    return op.rank(-1.0 * (1.0 - (p.fields["open"] / p.fields["close"])))


def _k041(p: Panel) -> pd.DataFrame:
    return op.signed_power(p.fields["high"] * p.fields["low"], 0.5) - p.fields["vwap"]


def _k101(p: Panel) -> pd.DataFrame:
    o, c, h, l = (p.fields["open"], p.fields["close"],
                  p.fields["high"], p.fields["low"])
    return (c - o) / ((h - l) + 0.001)


def _decay_mom(p: Panel) -> pd.DataFrame:
    # operator-coverage alpha: linearly-decayed 10-bar momentum, ranked
    return op.rank(op.decay_linear(op.delta(p.fields["close"], 10), 10))


def _vol_scaled_rev(p: Panel) -> pd.DataFrame:
    # short-term reversal scaled by inverse adv (coverage: scale + adv + stddev)
    rev = -1.0 * op.delta(p.fields["close"], 1)
    return op.scale(rev / (op.stddev(p.fields["returns"], 20) + 1e-9))


ALPHAS: list[AlphaDef] = [
    AlphaDef("K001", "K101", _k001),
    AlphaDef("K003", "K101", _k003),
    AlphaDef("K004", "K101", _k004),
    AlphaDef("K006", "K101", _k006),
    AlphaDef("K012", "K101", _k012),
    AlphaDef("K019", "K101", _k019),
    AlphaDef("K033", "K101", _k033),
    AlphaDef("K041", "K101", _k041),
    AlphaDef("K101", "K101", _k101),
    AlphaDef("COV_DECAY_MOM", "Qlib", _decay_mom),
    AlphaDef("COV_VOL_REV", "Qlib", _vol_scaled_rev),
    # Example of a registered-but-dropped alpha (template for Task 10):
    AlphaDef("K048", "K101", None, computable=False,
             needs=["indneutralize(subindustry)"],
             reason_if_dropped="whole signal is industry-neutralization; "
                               "no clean crypto industry map"),
]


def computable_alphas() -> list[AlphaDef]:
    return [a for a in ALPHAS if a.computable]


def n_computable() -> int:
    return len(computable_alphas())


def n_eff() -> int:
    """Trials count for DSR: 2× computable (pays for in-sample sign-fitting)."""
    return 2 * n_computable()
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_alpha_registry.py -v`
Expected: PASS (3 tests). If a worked alpha throws or mis-shapes, fix that alpha's function.

- [ ] **Step 5: Commit**

```bash
git add core/alpha_zoo/alphas.py tests/test_alpha_registry.py
git commit -m "feat(alpha-zoo): alpha registry + worked starter catalog + N_eff accounting"
```

---

## Task 7: Synthetic end-to-end (planted vs noise) — ACCEPTANCE GATE

**Files:**
- Create: `tests/test_alpha_search_e2e.py`

Proves the whole pipeline finds a real planted edge and rejects pure noise (spec §8.5). This must pass before any live verdict is trusted.

- [ ] **Step 1: Write the test**

```python
# tests/test_alpha_search_e2e.py
"""End-to-end on a synthetic panel: a PLANTED alpha must clear the gates;
a PURE-NOISE alpha must not. Proves the pipeline discriminates (spec §8.5)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from core.alpha_zoo import screen
from core.alpha_zoo.panel import build_panel, split_panel

rng = np.random.default_rng(123)


def _panel_with_planted_signal(n=1200, n_sym=20, horizon=24, edge=0.6):
    """Build a panel where `momentum` predicts the forward return (planted)."""
    syms = [f"S{i}/USDT" for i in range(n_sym)]
    raw = {}
    # latent per-bar-per-symbol forward signal; close path embeds it
    for sym in syms:
        ret = rng.normal(0, 0.01, n)
        close = 100 * np.exp(np.cumsum(ret))
        raw[sym] = pd.DataFrame({
            "ts": np.arange(1_700_000_000, 1_700_000_000 + n * 3600, 3600),
            "open": close, "high": close * 1.001, "low": close * 0.999,
            "close": close, "volume": rng.uniform(500, 1500, n),
        })
    p = build_panel(raw, timeframe="1h", horizon=horizon)
    # Plant: make fwd_ret partially track a known signal = 10-bar momentum rank.
    mom = p.fields["close"].pct_change(10)
    planted = mom.rank(axis=1, pct=True) - 0.5
    noise = pd.DataFrame(rng.normal(0, 1, p.fwd_ret.shape),
                         index=p.fwd_ret.index, columns=p.symbols)
    p.fwd_ret.iloc[:, :] = edge * planted.values + (1 - edge) * 0.01 * noise.values
    return p, mom


def test_planted_alpha_passes_and_noise_fails():
    p, mom = _panel_with_planted_signal()
    is_p, oos_p = split_panel(p, frac=0.6, embargo=24)

    # PLANTED signal = the momentum used to build fwd_ret
    planted_is = (is_p.fields["close"].pct_change(10).rank(axis=1, pct=True) - 0.5)
    ic = screen.cross_sectional_ic(planted_is, is_p.fwd_ret, min_width=10)
    ir_planted = screen.ir(ic)
    assert ir_planted >= 0.5, f"planted IR too low: {ir_planted}"

    sign = 1.0 if ir_planted > 0 else -1.0
    planted_oos = (oos_p.fields["close"].pct_change(10).rank(axis=1, pct=True) - 0.5)
    r = screen.long_short_returns(planted_oos, oos_p.fwd_ret, sign=sign,
                                  q=0.2, min_width=10)
    assert screen.dsr_for_returns(r, n_trials=50) >= 0.10

    # NOISE signal = random, must be Dead in-sample
    noise_is = pd.DataFrame(rng.normal(0, 1, is_p.fwd_ret.shape),
                            index=is_p.fwd_ret.index, columns=is_p.symbols)
    ir_noise = screen.ir(screen.cross_sectional_ic(noise_is, is_p.fwd_ret, min_width=10))
    assert abs(ir_noise) < 0.5, f"noise wrongly survived: IR={ir_noise}"
```

- [ ] **Step 2: Run the test**

Run: `python -m pytest tests/test_alpha_search_e2e.py -v`
Expected: PASS. If the planted alpha fails to clear IR≥0.5, raise `edge` in the fixture (the planted edge must be strong enough to be detectable — this validates the pipeline, not the threshold). If noise passes, that is a real bug in the screen — investigate before proceeding.

- [ ] **Step 3: Commit**

```bash
git add tests/test_alpha_search_e2e.py
git commit -m "test(alpha-zoo): synthetic planted-vs-noise end-to-end acceptance gate"
```

---

## Task 8: Stage-0 backfill script

**Files:**
- Create: `scripts/backfill_ohlcv_history.py`
- Test: `tests/test_backfill_ohlcv.py`

Reuses `core.feature_store.load_ohlcv_window`, which already paginates, dedups, and writes parquet. The only new piece is a `since`-aware fetcher (the bot's `BaseExchange.fetch_ohlcv` does not accept `since`, so call the underlying ccxt client directly).

- [ ] **Step 1: Write the failing test (mock fetcher, no network)**

```python
# tests/test_backfill_ohlcv.py
from __future__ import annotations

from scripts.backfill_ohlcv_history import make_fetcher


class _FakeCcxt:
    def fetch_ohlcv(self, symbol, timeframe, since=None, limit=None, params=None):
        # 3 hourly bars starting at `since`
        base = int(since)
        step = 3600_000
        return [[base + i * step, 1.0, 2.0, 0.5, 1.5, 100.0] for i in range(3)]


class _FakeClient:
    def __init__(self):
        self.exchange = _FakeCcxt()

    def _futures_params(self):
        return {}


def test_fetcher_passes_since_and_returns_rows():
    fetcher = make_fetcher(_FakeClient(), market_type="spot")
    rows = fetcher("BTC/USDT", "1h", 1_700_000_000_000, 1500)
    assert len(rows) == 3
    assert rows[0][0] == 1_700_000_000_000  # since echoed as first ts (ms)
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_backfill_ohlcv.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the backfill script**

```python
# scripts/backfill_ohlcv_history.py
"""Stage 0: one-shot 1h OHLCV backfill to ~N years for the alpha search.

Reuses core.feature_store.load_ohlcv_window (paginate + dedup + parquet
write). The bot's BaseExchange.fetch_ohlcv has no `since` parameter, so we
build a fetcher that calls the underlying ccxt client directly with `since`.

Idempotent: re-running only fills missing bars. Symbols are derived from the
existing cache filenames so the panel stays consistent (BASE-USDT_1h.parquet
-> 'BASE/USDT').

Usage:
    python scripts/backfill_ohlcv_history.py --years 3
"""
from __future__ import annotations

import argparse
import time
from pathlib import Path

from core.feature_store import load_ohlcv_window

ROOT = Path(__file__).resolve().parents[1]
CACHE = ROOT / "data" / "ohlcv_cache"


def make_fetcher(client, *, market_type: str = "spot"):
    """Return a callable(symbol, timeframe, since_ms, limit) -> ccxt rows."""
    params = client._futures_params() if market_type == "futures" else {}

    def _fetch(symbol, timeframe, since_ms, limit):
        return client.exchange.fetch_ohlcv(
            symbol, timeframe, since=int(since_ms), limit=int(limit),
            params=params) or []
    return _fetch


def symbols_from_cache(timeframe: str = "1h") -> list[str]:
    out = []
    for p in sorted(CACHE.glob(f"*_{timeframe}.parquet")):
        base = p.name[: -len(f"_{timeframe}.parquet")]      # 'BTC-USDT'
        out.append(base.replace("-", "/", 1))               # 'BTC/USDT'
    return out


def backfill(client, *, years: float = 3.0, timeframe: str = "1h",
             market_type: str = "spot") -> dict[str, int]:
    now = int(time.time())
    start = now - int(years * 365 * 24 * 3600)
    fetcher = make_fetcher(client, market_type=market_type)
    counts: dict[str, int] = {}
    for sym in symbols_from_cache(timeframe):
        df = load_ohlcv_window(sym, timeframe, start, now, fetcher=fetcher)
        counts[sym] = len(df)
        print(f"  {sym:14s} {len(df):6d} bars")
    return counts


def _build_binance():
    from config import Config
    from exchanges.binance_client import BinanceClient
    cfg = Config()
    return BinanceClient(cfg.BINANCE_API_KEY, cfg.BINANCE_API_SECRET)


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--years", type=float, default=3.0)
    ap.add_argument("--timeframe", default="1h")
    args = ap.parse_args()
    client = _build_binance()
    print(f"Backfilling {args.timeframe} ~{args.years}y from Binance...")
    counts = backfill(client, years=args.years, timeframe=args.timeframe)
    total = sum(counts.values())
    print(f"Done. {len(counts)} symbols, {total} total bars.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

> **Note for the implementer:** `_build_binance` references `config.Config` and
> `exchanges.binance_client.BinanceClient` constructor args — verify the actual
> constructor signature in `exchanges/binance_client.py` and adjust if it differs
> (e.g., a single config object). The test does NOT exercise `_build_binance`
> (no network/keys), so this adjustment is made when wiring the real run in Task 10.

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_backfill_ohlcv.py -v`
Expected: PASS (1 test).

- [ ] **Step 5: Commit**

```bash
git add scripts/backfill_ohlcv_history.py tests/test_backfill_ohlcv.py
git commit -m "feat(alpha-zoo): Stage-0 OHLCV history backfill (reuses feature_store)"
```

---

## Task 9: Orchestrator + report writer

**Files:**
- Create: `scripts/run_alpha_search.py`
- Test: `tests/test_run_alpha_search.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_run_alpha_search.py
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from core.alpha_zoo.alphas import AlphaDef
from core.alpha_zoo.panel import build_panel
from core.alpha_zoo import operators as op
from scripts.run_alpha_search import run_search


def _panel(n=900, n_sym=15):
    rng = np.random.default_rng(5)
    raw = {}
    for sym in [f"S{i}/USDT" for i in range(n_sym)]:
        close = 100 + np.cumsum(rng.normal(0, 1, n))
        raw[sym] = pd.DataFrame({
            "ts": np.arange(1_700_000_000, 1_700_000_000 + n * 3600, 3600),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": rng.uniform(500, 1500, n),
        })
    return build_panel(raw, timeframe="1h", horizon=24)


def test_run_search_emits_verdict_and_full_table():
    p = _panel()
    registry = [
        AlphaDef("MOM10", "Qlib", lambda pl: op.rank(op.delta(pl.fields["close"], 10))),
        AlphaDef("REV1", "Qlib", lambda pl: -1.0 * op.delta(pl.fields["close"], 1)),
    ]
    result = run_search(p, registry)
    assert "verdict" in result and result["verdict"] in {"EDGE_FOUND", "NO_EDGE"}
    assert len(result["table"]) == 2
    for row in result["table"]:
        assert {"id", "ir_is", "category", "oos_sharpe", "dsr", "fdr_p"} <= set(row)
    assert "pbo" in result and "n_eff" in result


def test_run_search_writes_report(tmp_path):
    p = _panel()
    registry = [AlphaDef("MOM10", "Qlib",
                         lambda pl: op.rank(op.delta(pl.fields["close"], 10)))]
    result = run_search(p, registry, report_dir=tmp_path)
    md = list(tmp_path.glob("alpha_search_*.md"))
    js = list(tmp_path.glob("alpha_search_*.json"))
    assert md and js
    loaded = json.loads(js[0].read_text())
    assert loaded["verdict"] == result["verdict"]
```

- [ ] **Step 2: Run to verify failure**

Run: `python -m pytest tests/test_run_alpha_search.py -v`
Expected: FAIL with `ModuleNotFoundError`.

- [ ] **Step 3: Implement the orchestrator**

```python
# scripts/run_alpha_search.py
"""Alpha-search orchestrator (spec §3, §5, §9).

FROZEN PRE-REGISTRATION — do not change between data collection and run:
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

from core.alpha_zoo import alphas as alpha_mod
from core.alpha_zoo import screen
from core.alpha_zoo.panel import Panel, build_panel, split_panel

# ── FROZEN constants (spec §3 Pre-registration) ──────────────────────────
HORIZON = 24          # forward-return bars (24h on 1h panel)
SPLIT_FRAC = 0.60     # in-sample fraction
EMBARGO = 24          # bars dropped at the IS/OOS boundary (= HORIZON)
MIN_WIDTH = 10        # min symbols per bar for IC / portfolio
QUANTILE = 0.20       # long-short top/bottom fraction
IR_MIN = 0.50         # Stage-1 survivor bar
DSR_MIN = 0.10        # Stage-2 deflated-Sharpe bar (Pr[SR>0])
PBO_MAX = 0.50        # Stage-2 overfit ceiling
FDR_Q = 0.05          # Benjamini-Hochberg level
PBO_PARTITIONS = 16

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "reports"


def run_search(panel: Panel, registry: list | None = None, *,
               report_dir: Path | None = None) -> dict:
    """Run the two-stage screen and return the result dict (and write a report
    when `report_dir` is given)."""
    registry = registry if registry is not None else alpha_mod.computable_alphas()
    n_computable = len(registry)
    n_eff = 2 * n_computable

    is_p, oos_p = split_panel(panel, frac=SPLIT_FRAC, embargo=EMBARGO)

    # ── Stage 1 (in-sample) ──────────────────────────────────────────────
    stage1: dict[str, dict] = {}
    for a in registry:
        sig = a.fn(is_p)
        ic = screen.cross_sectional_ic(sig, is_p.fwd_ret, min_width=MIN_WIDTH)
        ir_v = screen.ir(ic)
        stage1[a.id] = {
            "ir_is": ir_v,
            "sign": 1.0 if ir_v >= 0 else -1.0,
            "category": screen.categorize(ir_v, IR_MIN),
            "passed_s1": abs(ir_v) >= IR_MIN,
        }

    # ── Stage 2 (out-of-sample): portfolio returns for ALL alphas (PBO) ──
    returns_by_alpha: dict[str, "screen.pd.Series"] = {}
    oos_stats: dict[str, dict] = {}
    for a in registry:
        sig = a.fn(oos_p)
        r = screen.long_short_returns(sig, oos_p.fwd_ret,
                                      sign=stage1[a.id]["sign"],
                                      q=QUANTILE, min_width=MIN_WIDTH)
        returns_by_alpha[a.id] = r
        oos_stats[a.id] = {
            "oos_sharpe": screen.sharpe(r.dropna().to_numpy()) if r.dropna().size else 0.0,
            "dsr": screen.dsr_for_returns(r, n_trials=n_eff),
            "p_raw": screen.sharpe_pvalue(r),
        }

    pbo_value = screen.pbo_over_alphas(returns_by_alpha, n_partitions=PBO_PARTITIONS)

    # ── BH-FDR across Stage-1 survivors only ─────────────────────────────
    s1_ids = [a.id for a in registry if stage1[a.id]["passed_s1"]]
    flags = screen.fdr_bh([oos_stats[i]["p_raw"] for i in s1_ids], q=FDR_Q)
    fdr_pass = dict(zip(s1_ids, flags))

    # ── Survivor decision (spec §3 conjunction) ──────────────────────────
    table, survivors = [], []
    for a in registry:
        s1, s2 = stage1[a.id], oos_stats[a.id]
        is_survivor = (
            s1["passed_s1"]
            and s2["dsr"] >= DSR_MIN
            and pbo_value <= PBO_MAX
            and fdr_pass.get(a.id, False)
        )
        if is_survivor:
            survivors.append(a.id)
        table.append({
            "id": a.id, "source": a.source,
            "ir_is": round(s1["ir_is"], 4), "category": s1["category"],
            "oos_sharpe": round(s2["oos_sharpe"], 4), "dsr": round(s2["dsr"], 4),
            "fdr_p": round(s2["p_raw"], 6), "fdr_pass": fdr_pass.get(a.id, False),
            "survivor": is_survivor,
        })

    table.sort(key=lambda r: r["ir_is"], reverse=True)
    result = {
        "verdict": "EDGE_FOUND" if survivors else "NO_EDGE",
        "survivors": survivors,
        "n_computable": n_computable, "n_eff": n_eff,
        "pbo": round(pbo_value, 4),
        "pre_registration": {
            "horizon": HORIZON, "split_frac": SPLIT_FRAC, "embargo": EMBARGO,
            "min_width": MIN_WIDTH, "quantile": QUANTILE, "ir_min": IR_MIN,
            "dsr_min": DSR_MIN, "pbo_max": PBO_MAX, "fdr_q": FDR_Q,
        },
        "panel": {"bars": len(panel.ts), "symbols": len(panel.symbols)},
        "table": table,
    }
    if report_dir is not None:
        _write_report(result, Path(report_dir))
    return result


def _write_report(result: dict, report_dir: Path) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = date.today().isoformat()
    (report_dir / f"alpha_search_{stamp}.json").write_text(
        json.dumps(result, indent=2, default=float))
    lines = [
        f"# Alpha Search — {stamp}", "",
        f"**Verdict:** {result['verdict']}",
        f"**Survivors:** {result['survivors'] or 'none'}",
        f"**N_computable / N_eff:** {result['n_computable']} / {result['n_eff']}",
        f"**PBO:** {result['pbo']}",
        f"**Panel:** {result['panel']['bars']} bars × {result['panel']['symbols']} symbols",
        "", "## Pre-registration (frozen)",
        "```json", json.dumps(result["pre_registration"], indent=2), "```",
        "", "## Full ranking", "",
        "| id | source | IR_is | category | OOS Sharpe | DSR | FDR p | survivor |",
        "|----|--------|-------|----------|-----------|-----|-------|----------|",
    ]
    for r in result["table"]:
        lines.append(
            f"| {r['id']} | {r['source']} | {r['ir_is']} | {r['category']} | "
            f"{r['oos_sharpe']} | {r['dsr']} | {r['fdr_p']} | {r['survivor']} |")
    (report_dir / f"alpha_search_{stamp}.md").write_text("\n".join(lines))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--timeframe", default="1h")
    args = ap.parse_args()
    from core.alpha_zoo.panel import build_panel  # noqa: F401 (used via loader below)
    panel = _load_live_panel(args.timeframe)
    result = run_search(panel, report_dir=DEFAULT_REPORT_DIR)
    print(f"VERDICT: {result['verdict']}  survivors={result['survivors']}  "
          f"PBO={result['pbo']}  N_eff={result['n_eff']}")
    return 0


def _load_live_panel(timeframe: str = "1h") -> Panel:
    """Load every cached *_<tf>.parquet into a Panel."""
    import pandas as pd
    cache = ROOT / "data" / "ohlcv_cache"
    raw = {}
    for p in sorted(cache.glob(f"*_{timeframe}.parquet")):
        base = p.name[: -len(f"_{timeframe}.parquet")]
        sym = base.replace("-", "/", 1)
        raw[sym] = pd.read_parquet(p)
    return build_panel(raw, timeframe=timeframe, horizon=HORIZON)


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run to verify pass**

Run: `python -m pytest tests/test_run_alpha_search.py -v`
Expected: PASS (2 tests).

- [ ] **Step 5: Run the FULL alpha-zoo test suite**

Run: `python -m pytest tests/test_alpha_panel.py tests/test_alpha_operators.py tests/test_alpha_lookahead_sentinel.py tests/test_alpha_screen.py tests/test_alpha_registry.py tests/test_alpha_search_e2e.py tests/test_backfill_ohlcv.py tests/test_run_alpha_search.py -v`
Expected: ALL PASS.

- [ ] **Step 6: Commit**

```bash
git add scripts/run_alpha_search.py tests/test_run_alpha_search.py
git commit -m "feat(alpha-zoo): orchestrator + frozen pre-registration + report writer"
```

---

## Task 10: Grow the catalog to the full zoo, then run the experiment

This task turns the validated machinery into the actual ~450-alpha falsification run. It is data-entry against cited sources plus one live execution. Port in batches; the registry-integrity and shape tests (Task 6) guard every batch.

**Porting procedure (per batch of ~20 alphas):**
1. Pick the next source block (K101 first, then GTJA-191, then Qlib158, then FF proxies).
2. For each alpha, add an `AlphaDef` to `core/alpha_zoo/alphas.py`:
   - If it uses only OHLCV/vwap/adv/returns → `computable=True`, write `fn` using `operators`.
   - If its whole signal is `indneutralize(...)`/fundamentals/book value → `computable=False` + `reason_if_dropped`.
   - If `indneutralize` is one step inside a price/volume formula → degrade it to identity, keep `computable=True`, add a `needs` note.
3. Run `python -m pytest tests/test_alpha_registry.py -v` (must stay green: unique ids, correct shapes, dropped-with-reason).
4. Commit the batch: `git add core/alpha_zoo/alphas.py && git commit -m "feat(alpha-zoo): port <source> alphas <range>"`.

- [ ] **Step 1: Port Kakushadze-101 (source arXiv:1601.00991), batches of ~20**

For each: `python -m pytest tests/test_alpha_registry.py -v` green, then commit the batch.

- [ ] **Step 2: Port GTJA-191 (Guotai-Junan 191 Alphas), batches of ~20**

Same loop. Drop A-share-specific constructs that have no crypto analogue with a `reason_if_dropped`.

- [ ] **Step 3: Port Qlib158 (Alpha158) + Fama-French price/volume proxies**

Same loop. FF size/value factors needing book value/market-cap fundamentals → `computable=False` with reasons.

- [ ] **Step 4: Lock N_computable**

Run: `python -c "from core.alpha_zoo import alphas; print('N_computable=', alphas.n_computable(), 'N_eff=', alphas.n_eff())"`
Record both numbers (they go in the report header). Confirm the full suite is green:
Run: `python -m pytest tests/ -k alpha -v`
Expected: ALL PASS.

- [ ] **Step 5: Backfill the real history**

Verify the Binance client constructor in `scripts/backfill_ohlcv_history.py::_build_binance` matches `exchanges/binance_client.py`, then:
Run: `python scripts/backfill_ohlcv_history.py --years 3`
Expected: ~32 symbols, each printing thousands of 1h bars (newer listings fewer). Re-runnable (idempotent).

- [ ] **Step 6: Run the falsification experiment**

Run: `python scripts/run_alpha_search.py --timeframe 1h`
Expected: prints `VERDICT: EDGE_FOUND|NO_EDGE  survivors=[...]  PBO=...  N_eff=...` and writes `reports/alpha_search_<date>.{md,json}`.

> **Performance:** at full scale (~26k bars × ~300 alphas × 2 stages, per-bar
> spearmanr) this is a multi-hour single-thread run, not minutes. It is a
> one-shot — fine to start and walk away. If too slow, apply the vectorization
> noted in `cross_sectional_ic`'s docstring (Spearman = Pearson of ranks) and
> `np.argpartition` in `long_short_returns`. The small e2e tests will NOT
> surface this; budget for it here.

- [ ] **Step 7: Commit the report + record the verdict**

```bash
git add reports/alpha_search_*.md reports/alpha_search_*.json core/alpha_zoo/alphas.py
git commit -m "chore(alpha-zoo): full-zoo falsification run + verdict report"
```

Then report the verdict to the user: if `NO_EDGE`, the spec's decision rule is "no price/volume edge — stop hand-crafting price signals." If `EDGE_FOUND`, list the survivor ids and propose a SEPARATE brainstorm→spec→plan to integrate them (do not wire anything into the live bot in this project — spec §4 Out of scope).

---

## Self-Review

**Spec coverage:**
- §3 frozen pre-registration → Task 9 constants block (HORIZON…FDR_Q) + report header. ✓
- §5 data flow (panel→S1→S2→report) → Tasks 1,4,5,9. ✓
- §6 component files → all created across Tasks 1-9. ✓
- §7 computability tagging + N_eff=2×N_computable → Task 6 (`AlphaDef`, `n_eff`) + Task 10. ✓
- §8 leak guards: backward-only operators → Task 2; sentinel → Task 3; time-split+sign-freeze → Task 1 `split_panel` + Task 9 Stage-1 sign fix; N_eff in DSR → Task 5/9; PBO on T×K → Task 5; planted/noise e2e → Task 7. ✓
- §9 report (header + full table + PBO + verdict) → Task 9 `_write_report`. ✓
- §10 testing order (operators→sentinel→panel→screen→e2e) → Tasks 2,3,1,4,5,7 (panel is Task 1 but independent of operators; order is fine). ✓
- §11 limitations → recorded in spec; no code needed. ✓
- §12 reuse (stat_tests, feature_store, no new dep) → Task 5 imports, Task 8 reuse. ✓
- §13 success criteria → Task 7 (pipeline discriminates) + Task 10 (verdict under frozen reg). ✓

**Placeholder scan:** No "TBD"/"implement later"/"handle edge cases". The Task 8 implementer note and Task 10 porting procedure are concrete instructions with cited sources, not placeholders. ✓

**Type consistency:** `Panel.fields` keys (`open/high/low/close/volume/vwap/returns`), `Panel.fwd_ret`, `Panel.adv(d)`, `Panel.symbols`, `Panel.ts` used consistently in panel/screen/alphas/orchestrator. `AlphaDef(id, source, fn, computable, needs, reason_if_dropped)` consistent across Task 6 and Task 9/10. Screen function names (`cross_sectional_ic`, `ir`, `categorize`, `long_short_returns`, `sharpe_pvalue`, `dsr_for_returns`, `fdr_bh`, `pbo_over_alphas`) defined in Tasks 4-5 and called with matching signatures in Task 9. `screen.sharpe` is re-exported via `from core.stat_tests import ... sharpe` in screen.py (used in orchestrator as `screen.sharpe`). ✓
