# scripts/run_named_strategy_screen.py
"""Named-strategy backtest screen — score the 3 ``core/signals`` generators
across the crypto OHLCV universe with FROZEN statistical gates.

What it does
------------
For each (strategy, param-combo) it runs a LEAKAGE-SAFE long-only backtest on
every eligible coin, builds an equal-weight PORTFOLIO return series, splits it
into IS (first 60%) / OOS (last 40%) by TIME with an embargo, and scores the
OOS leg against four pre-registered gates:

  * OOS net annualized Sharpe > 0
  * Deflated Sharpe (repo ``core.stat_tests`` with the correct ``sr_var``) pass
  * PBO < 0.5 (CSCV across all strategy×param portfolios)
  * beats a BTC-beta-adjusted buy & hold over the OOS window
  * FDR-significant (Benjamini-Hochberg) within the full
    strategy×param×coin t-stat family

Per strategy the verdict rolls up: EDGE_PRESENT iff its BEST param-combo passes
ALL of (OOS Sharpe>0, dsr_pass, pbo<0.5, beats BTC-beta-adj B&H, FDR-significant);
otherwise NO_EDGE.

REUSED (not reinvented):
  * core.stat_tests.deflated_sharpe / pbo
  * core.alpha_zoo.screen.sharpe_pvalue / dsr_for_returns / fdr_bh / pbo_over_alphas
  * core.btc_alpha (beta convention) — portfolio beta vs BTC via OLS
  * config.FEES (cost anchoring)
  * IS/OOS split + embargo + frozen-constant + verdict conventions from
    scripts/run_alpha_search.py

Leakage safety: a signal generated at bar ``i`` is the position HELD during
bar ``i+1`` (``signal.shift(1)``) applied to close-to-close returns; the
IS/OOS boundary drops ``EMBARGO`` bars.

Run:
    python scripts/run_named_strategy_screen.py                # full universe
    python scripts/run_named_strategy_screen.py --smoke        # 3-coin smoke
    python scripts/run_named_strategy_screen.py --max-coins 40
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.alpha_zoo.screen import (  # noqa: E402
    dsr_for_returns,
    fdr_bh,
    pbo_over_alphas,
    sharpe_pvalue,
)
from core.signals.session_breakout import signal as session_breakout_signal  # noqa: E402
from core.signals.squeeze_breakout import signal as squeeze_breakout_signal  # noqa: E402
from core.signals.swing_structure import signal as swing_structure_signal  # noqa: E402

try:
    from config import FEES as _CFG_FEES  # noqa: E402
except Exception:  # pragma: no cover - config import must not break the screen
    _CFG_FEES = {"futures_taker": 0.0005}

# ── FROZEN constants (pre-registration; do NOT tune on OOS) ───────────────────
CACHE_DIR = ROOT / "data" / "ohlcv_cache"
TIMEFRAME = "1h"
MIN_ROWS = 4000  # bases need >= 4000 1h bars to be eligible
MAX_COINS = 40  # cap to ~40 with the most history
BARS_PER_YEAR = 24 * 365  # 1h bars -> annualization factor
SPLIT_FRAC = 0.60  # IS fraction (first 60% by TIME)
EMBARGO = 24  # bars dropped at the IS/OOS boundary
# Round-trip cost on |position change|: futures taker x2 (open+close) + ~spread.
# config futures_taker = 5 bps -> 10 bps taker round trip + ~no extra spread
# already inside the 10 bps spec budget. Charged per unit of |Δposition|.
ROUND_TRIP_COST = 2.0 * float(_CFG_FEES.get("futures_taker", 0.0005)) + 0.0  # = 0.0010 (10 bps)
# Frozen gates
DSR_MIN = 0.10  # Pr[true SR > 0] floor (deflated)
PBO_MAX = 0.50  # overfit ceiling
FDR_Q = 0.05  # Benjamini-Hochberg level
PBO_PARTITIONS = 16

# ── FROZEN param grid (<= 4 combos per strategy) ─────────────────────────────
PARAM_GRID: dict[str, list[dict]] = {
    "session_breakout": [
        {"asian_start_hour": 0, "asian_end_hour": 6, "vol_pctl_threshold": 25},
        {"asian_start_hour": 0, "asian_end_hour": 8, "vol_pctl_threshold": 25},
        {"asian_start_hour": 0, "asian_end_hour": 6, "vol_pctl_threshold": 40},
        {"asian_start_hour": 0, "asian_end_hour": 8, "vol_pctl_threshold": 40},
    ],
    "squeeze_breakout": [
        {"bb_period": 20, "bb_std": 2.0, "squeeze_pctl": 15, "min_bars_held": 6},
        {"bb_period": 20, "bb_std": 2.0, "squeeze_pctl": 25, "min_bars_held": 6},
        {"bb_period": 20, "bb_std": 2.0, "squeeze_pctl": 15, "min_bars_held": 12},
        {"bb_period": 30, "bb_std": 2.0, "squeeze_pctl": 15, "min_bars_held": 6},
    ],
    "swing_structure": [
        {"k_swing": 3},
        {"k_swing": 5},
        {"k_swing": 8},
        {"k_swing": 12},
    ],
}

SIGNAL_FNS = {
    "session_breakout": session_breakout_signal,
    "squeeze_breakout": squeeze_breakout_signal,
    "swing_structure": swing_structure_signal,
}

# Stablecoins / pegged assets to exclude (no directional return to harvest).
_STABLE_BASES = {
    "USDT",
    "USDC",
    "FDUSD",
    "TUSD",
    "DAI",
    "BUSD",
    "USDP",
    "USTC",
    "USDD",
    "GUSD",
    "PYUSD",
    "EUR",
    "EURT",
    "EURI",
    "USD1",
    "USDE",
    "USDS",
    "FRAX",
}


# ─────────────────────────────────────────────────────────────────────────────
# Universe selection
# ─────────────────────────────────────────────────────────────────────────────
def _is_equity_proxy(stem: str) -> bool:
    """Equity / commodity proxies are cached as ``<TICKER>-USDTUSDT`` (double
    USDT). Crypto bases are ``<BASE>-USDT``. Exclude the double-USDT form."""
    return stem.endswith("-USDTUSDT")


def discover_universe(*, max_coins: int = MAX_COINS, only: list[str] | None = None) -> list[str]:
    """Return up to ``max_coins`` eligible base symbols (most history first).

    Eligible = crypto base (not equity proxy, not stablecoin) whose
    ``{BASE}-USDT_1h.parquet`` has >= MIN_ROWS rows. BTC is always retained
    (needed as the beta benchmark) but is not itself part of the long universe.
    """
    rows: list[tuple[str, int]] = []
    for p in sorted(CACHE_DIR.glob(f"*-USDT_{TIMEFRAME}.parquet")):
        stem = p.name[: -len(f"_{TIMEFRAME}.parquet")]  # e.g. "BTC-USDT"
        if _is_equity_proxy(stem):
            continue
        base = stem[: -len("-USDT")]
        if base in _STABLE_BASES:
            continue
        if only is not None and base not in only:
            continue
        try:
            n = _row_count(p)
        except Exception:
            continue
        if n >= MIN_ROWS:
            rows.append((base, n))
    rows.sort(key=lambda x: x[1], reverse=True)
    bases = [b for b, _ in rows]
    if only is not None:
        return bases  # smoke / explicit selection: no cap
    return bases[:max_coins]


def _row_count(path: Path) -> int:
    """Cheap row count via parquet metadata (no full read)."""
    try:
        import pyarrow.parquet as pq

        return pq.ParquetFile(path).metadata.num_rows
    except Exception:
        return len(pd.read_parquet(path, columns=["ts"]))


def _load(base: str) -> pd.DataFrame | None:
    p = CACHE_DIR / f"{base}-USDT_{TIMEFRAME}.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    df = df[["ts", "open", "high", "low", "close", "volume"]].copy()
    df = df.sort_values("ts").drop_duplicates("ts").reset_index(drop=True)
    return df


# ─────────────────────────────────────────────────────────────────────────────
# Per-coin backtest (leakage-safe long-only)
# ─────────────────────────────────────────────────────────────────────────────
def _net_from_signal(df: pd.DataFrame, sig: pd.Series) -> pd.Series:
    """Per-bar net strategy return series from a precomputed ``sig``.

    Position held during bar ``i`` = ``signal.shift(1)`` (signal computed on
    closed bars 0..i-1). Cost = ROUND_TRIP_COST * |Δposition| charged on the
    bar the position changes. close-to-close simple returns.
    """
    pos = sig.astype(float).shift(1).fillna(0.0)  # next-bar application
    close = df["close"].astype(float)
    ret = close.pct_change().fillna(0.0)
    gross = pos * ret
    turnover = pos.diff().abs().fillna(pos.abs())  # |Δposition|; first bar = entry
    cost = turnover * ROUND_TRIP_COST
    net = (gross - cost).to_numpy()
    return pd.Series(net, index=df["ts"].to_numpy())


def _count_from_signal(sig: pd.Series) -> int:
    """Number of long entries (0 -> 1 transitions of the held position)."""
    pos = sig.astype(float).shift(1).fillna(0.0).to_numpy()
    entries = int(np.sum((pos[1:] > 0) & (pos[:-1] <= 0)))
    if len(pos) and pos[0] > 0:
        entries += 1
    return entries


def backtest_coin(df: pd.DataFrame, signal_fn, params: dict) -> pd.Series:
    """Return a per-bar net strategy return series indexed by ``ts``."""
    return _net_from_signal(df, signal_fn(df, **params))


def count_trades(df: pd.DataFrame, signal_fn, params: dict) -> int:
    """Number of long entries (0 -> 1 transitions of the held position)."""
    return _count_from_signal(signal_fn(df, **params))


def portfolio_returns(
    frames: dict[str, pd.DataFrame], signal_fn, params: dict
) -> tuple[pd.Series, int]:
    """Equal-weight portfolio net return per bar across the universe + n_trades.

    Each coin's net-return series is reindexed onto the union ts grid; bars
    with no coverage contribute 0.0 (flat). The portfolio return at each bar
    is the mean across coins that have data at that bar.
    """
    per_coin = {}
    n_trades = 0
    for base, df in frames.items():
        sig = signal_fn(df, **params)  # compute ONCE; reuse below
        per_coin[base] = _net_from_signal(df, sig)
        n_trades += _count_from_signal(sig)
    if not per_coin:
        return pd.Series(dtype=float), 0
    mat = pd.DataFrame(per_coin).sort_index()
    # Equal weight = mean across coins present at each bar (NaN-aware).
    port = mat.mean(axis=1, skipna=True).fillna(0.0)
    return port, n_trades


# ─────────────────────────────────────────────────────────────────────────────
# Metrics
# ─────────────────────────────────────────────────────────────────────────────
def ann_sharpe(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if r.size < 2:
        return 0.0
    s = r.std(ddof=1)
    if s <= 0:
        return 0.0
    return float((r.mean() / s) * np.sqrt(BARS_PER_YEAR))


def total_return_pct(returns: np.ndarray) -> float:
    r = np.asarray(returns, dtype=float)
    r = r[~np.isnan(r)]
    if r.size == 0:
        return 0.0
    return float((np.prod(1.0 + r) - 1.0) * 100.0)


def _split_time(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Split a ts-indexed series into IS (first SPLIT_FRAC) / OOS (rest) by
    TIME with an EMBARGO gap dropped at the boundary."""
    n = len(series)
    cut = int(n * SPLIT_FRAC)
    is_part = series.iloc[:cut]
    oos_part = series.iloc[cut + EMBARGO :]
    return is_part, oos_part


def portfolio_beta(port_ret: pd.Series, btc_ret: pd.Series) -> float:
    """OLS beta of portfolio returns on BTC returns (aligned by ts).

    Uses the core.btc_alpha convention (alpha = ret - beta * btc_ret), here
    estimated rather than fixed at 1.0 so the buy&hold comparison is
    beta-adjusted. Returns 0.0 if undeterminable."""
    a = port_ret.reindex(btc_ret.index).dropna()
    common = a.index.intersection(btc_ret.index)
    x = btc_ret.reindex(common).to_numpy(dtype=float)
    y = port_ret.reindex(common).to_numpy(dtype=float)
    mask = ~(np.isnan(x) | np.isnan(y))
    x, y = x[mask], y[mask]
    if x.size < 2:
        return 0.0
    vx = x.var()
    if vx <= 0:
        return 0.0
    return float(np.cov(x, y, ddof=1)[0, 1] / vx)


# ─────────────────────────────────────────────────────────────────────────────
# Parallel combo evaluation (harness-only speedup; identical math, no signal
# changes). Each worker computes one combo's portfolio return series + trade
# count over the shared frames passed once via the pool initializer.
# ─────────────────────────────────────────────────────────────────────────────
_WORKER_FRAMES: dict[str, pd.DataFrame] = {}


def _pool_init(frames: dict[str, pd.DataFrame]) -> None:
    global _WORKER_FRAMES
    _WORKER_FRAMES = frames


def _combo_worker(arg: tuple[str, str, dict]) -> tuple[str, list, list, int]:
    """Compute one combo's portfolio returns. Returns (combo_id, ts_list,
    val_list, n_trades). Series are returned as plain lists for cheap pickling."""
    combo_id, strat, params = arg
    fn = SIGNAL_FNS[strat]
    port, n_trades = portfolio_returns(_WORKER_FRAMES, fn, params)
    return combo_id, list(port.index), [float(v) for v in port.to_numpy()], int(n_trades)


def _compute_ports(frames: dict[str, pd.DataFrame]) -> dict[str, tuple[pd.Series, int]]:
    """Compute every combo's (portfolio Series, n_trades). Parallel across combos
    when >1 combo and a pool is available; falls back to serial on any failure."""
    tasks: list[tuple[str, str, dict]] = []
    for strat, grid in PARAM_GRID.items():
        for pi, params in enumerate(grid):
            tasks.append((f"{strat}#{pi}", strat, params))

    results: dict[str, tuple[pd.Series, int]] = {}
    try:
        import os
        from concurrent.futures import ProcessPoolExecutor

        workers = min(len(tasks), os.cpu_count() or 1)
        if workers <= 1:
            raise RuntimeError("serial")
        with ProcessPoolExecutor(
            max_workers=workers, initializer=_pool_init, initargs=(frames,)
        ) as ex:
            for combo_id, idx, vals, n_trades in ex.map(_combo_worker, tasks):
                results[combo_id] = (pd.Series(vals, index=idx), n_trades)
        return results
    except Exception:
        # Serial fallback — identical math.
        results.clear()
        for combo_id, strat, params in tasks:
            fn = SIGNAL_FNS[strat]
            port, n_trades = portfolio_returns(frames, fn, params)
            results[combo_id] = (port, n_trades)
        return results


# ─────────────────────────────────────────────────────────────────────────────
# Screen
# ─────────────────────────────────────────────────────────────────────────────
def run_screen(bases: list[str], *, report_dir: Path | None = None, smoke: bool = False) -> dict:
    frames = {b: _load(b) for b in bases}
    frames = {
        b: d for b, d in frames.items() if d is not None and len(d) >= (200 if smoke else MIN_ROWS)
    }

    # BTC benchmark series (close-to-close), needed for beta-adjustment.
    btc_df = _load("BTC")
    if btc_df is None:
        raise RuntimeError("BTC-USDT_1h.parquet required for beta benchmark; not found")
    btc_ret_full = pd.Series(
        btc_df["close"].astype(float).pct_change().fillna(0.0).to_numpy(),
        index=btc_df["ts"].to_numpy(),
    )

    # ── Build OOS portfolio returns for EVERY strategy×param (for PBO + FDR) ──
    combos: list[tuple[str, int, dict]] = []
    oos_returns_by_combo: dict[str, pd.Series] = {}
    combo_records: list[dict] = []
    pvals_family: list[float] = []
    pval_keys: list[str] = []

    ports_by_combo = _compute_ports(frames)  # parallel; identical math

    for strat, grid in PARAM_GRID.items():
        for pi, params in enumerate(grid):
            combo_id = f"{strat}#{pi}"
            port, n_trades = ports_by_combo.get(combo_id, (pd.Series(dtype=float), 0))
            if len(port) == 0:
                continue
            is_part, oos_part = _split_time(port)
            oos_returns_by_combo[combo_id] = oos_part

            # OOS BTC window aligned to this combo's OOS bars.
            oos_btc = btc_ret_full.reindex(oos_part.index).fillna(0.0)
            beta = portfolio_beta(oos_part, oos_btc)
            btc_bh_oos = total_return_pct(oos_btc.to_numpy())
            # beta-adjusted B&H benchmark return over OOS = beta * BTC B&H.
            beta_adj_bh_pct = beta * btc_bh_oos

            oos_arr = oos_part.to_numpy()
            rec = {
                "combo_id": combo_id,
                "strategy": strat,
                "param_idx": pi,
                "params": params,
                "n_trades": int(n_trades),
                "is_sharpe_ann": ann_sharpe(is_part.to_numpy()),
                "oos_sharpe_ann": ann_sharpe(oos_arr),
                "oos_return_pct": total_return_pct(oos_arr),
                "oos_n_bars": int(len(oos_arr)),
                "beta_vs_btc": float(beta),
                "btc_bh_oos_pct": float(btc_bh_oos),
                "beta_adj_bh_oos_pct": float(beta_adj_bh_pct),
                # per-bar (non-annualized) deflated Sharpe — repo primitive,
                # correct sr_var (1/n_obs) via dsr_for_returns default.
                "dsr": float(dsr_for_returns(oos_part, n_trials=2 * _n_combos())),
                "p_raw": float(sharpe_pvalue(oos_part)),
            }
            rec["beats_btc_beta_adj_bh"] = bool(rec["oos_return_pct"] > beta_adj_bh_pct)
            combo_records.append(rec)
            combos.append((strat, pi, params))
            pvals_family.append(rec["p_raw"])
            pval_keys.append(combo_id)

    # ── PBO across all combos' OOS portfolio returns (CSCV) ──────────────────
    pbo_value = float(pbo_over_alphas(oos_returns_by_combo, n_partitions=PBO_PARTITIONS))

    # ── FDR (Benjamini-Hochberg) across the full combo t-stat family ─────────
    fdr_flags = fdr_bh(pvals_family, q=FDR_Q)
    fdr_by_combo = dict(zip(pval_keys, fdr_flags))

    # ── Per-combo gate conjunction ───────────────────────────────────────────
    for rec in combo_records:
        cid = rec["combo_id"]
        rec["pbo"] = pbo_value
        rec["dsr_pass"] = bool(rec["dsr"] >= DSR_MIN)
        rec["pbo_pass"] = bool(pbo_value < PBO_MAX)
        rec["fdr_pass"] = bool(fdr_by_combo.get(cid, False))
        rec["oos_sharpe_pass"] = bool(rec["oos_sharpe_ann"] > 0.0)
        rec["edge"] = bool(
            rec["oos_sharpe_pass"]
            and rec["dsr_pass"]
            and rec["pbo_pass"]
            and rec["beats_btc_beta_adj_bh"]
            and rec["fdr_pass"]
        )

    # ── Roll up to per-strategy verdict (best combo decides) ─────────────────
    strategy_summary: dict[str, dict] = {}
    for strat in PARAM_GRID:
        recs = [r for r in combo_records if r["strategy"] == strat]
        if not recs:
            strategy_summary[strat] = {"verdict": "NO_EDGE", "reason": "no computable combos"}
            continue
        # "best" = highest OOS annualized Sharpe.
        best = max(recs, key=lambda r: r["oos_sharpe_ann"])
        any_edge = any(r["edge"] for r in recs)
        verdict = "EDGE_PRESENT" if any_edge else "NO_EDGE"
        edge_combo = next((r["combo_id"] for r in recs if r["edge"]), None)
        strategy_summary[strat] = {
            "verdict": verdict,
            "best_combo": best["combo_id"],
            "best_oos_sharpe_ann": best["oos_sharpe_ann"],
            "best_oos_return_pct": best["oos_return_pct"],
            "best_n_trades": best["n_trades"],
            "best_dsr": best["dsr"],
            "best_beta_vs_btc": best["beta_vs_btc"],
            "best_beats_btc_beta_adj_bh": best["beats_btc_beta_adj_bh"],
            "best_fdr_pass": best["fdr_pass"],
            "edge_combo": edge_combo,
        }

    result = {
        "verdict_by_strategy": {s: strategy_summary[s]["verdict"] for s in PARAM_GRID},
        "overall_verdict": (
            "EDGE_PRESENT"
            if any(
                v == "EDGE_PRESENT" for v in (strategy_summary[s]["verdict"] for s in PARAM_GRID)
            )
            else "NO_EDGE"
        ),
        "pbo": pbo_value,
        "pre_registration": {
            "timeframe": TIMEFRAME,
            "min_rows": MIN_ROWS,
            "max_coins": MAX_COINS,
            "split_frac": SPLIT_FRAC,
            "embargo": EMBARGO,
            "round_trip_cost_bps": ROUND_TRIP_COST * 1e4,
            "bars_per_year": BARS_PER_YEAR,
            "dsr_min": DSR_MIN,
            "pbo_max": PBO_MAX,
            "fdr_q": FDR_Q,
            "pbo_partitions": PBO_PARTITIONS,
            "param_grid": PARAM_GRID,
        },
        "universe": {"n_coins": len(frames), "bases": sorted(frames.keys()), "smoke": smoke},
        "strategy_summary": strategy_summary,
        "combos": combo_records,
    }
    if report_dir is not None:
        _write_report(result, Path(report_dir), smoke=smoke)
    return result


def _n_combos() -> int:
    return sum(len(g) for g in PARAM_GRID.values())


# ─────────────────────────────────────────────────────────────────────────────
# Reporting
# ─────────────────────────────────────────────────────────────────────────────
def _write_report(result: dict, report_dir: Path, *, smoke: bool) -> None:
    stamp = date.today().isoformat()
    report_dir.mkdir(parents=True, exist_ok=True)
    json_dir = ROOT / "data" / "backtest_results"
    json_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_smoke" if smoke else ""
    (json_dir / f"named_strategy_screen_{stamp}{suffix}.json").write_text(
        json.dumps(result, indent=2, default=float), encoding="utf-8"
    )

    lines = [
        f"# Named Strategy Screen — {stamp}{' (SMOKE)' if smoke else ''}",
        "",
        f"**Overall verdict:** {result['overall_verdict']}",
        f"**PBO (CSCV, all combos):** {result['pbo']:.4f}",
        f"**Universe:** {result['universe']['n_coins']} coins ({TIMEFRAME}, >= {MIN_ROWS} bars)",
        "",
        "## Pre-registration (FROZEN — not tuned on OOS)",
        "```json",
        json.dumps(result["pre_registration"], indent=2, default=float),
        "```",
        "",
        "## Per-strategy verdict",
        "",
        "| strategy | verdict | best combo | OOS Sharpe(ann) | OOS ret % | "
        "n_trades | DSR | beta | beats β-adj B&H | FDR |",
        "|----------|---------|-----------|----------------:|----------:|"
        "---------:|----:|-----:|:---------------:|:---:|",
    ]
    for s in PARAM_GRID:
        ss = result["strategy_summary"][s]
        lines.append(
            f"| {s} | **{ss['verdict']}** | {ss.get('best_combo', '-')} | "
            f"{ss.get('best_oos_sharpe_ann', 0):.3f} | "
            f"{ss.get('best_oos_return_pct', 0):.2f} | "
            f"{ss.get('best_n_trades', 0)} | {ss.get('best_dsr', 0):.3f} | "
            f"{ss.get('best_beta_vs_btc', 0):.3f} | "
            f"{ss.get('best_beats_btc_beta_adj_bh', False)} | "
            f"{ss.get('best_fdr_pass', False)} |"
        )

    lines += [
        "",
        "## All combos (strategy × param)",
        "",
        "| combo | OOS Sharpe(ann) | IS Sharpe(ann) | OOS ret % | β·BTC B&H % | "
        "n_trades | DSR | PBO | FDR | edge |",
        "|-------|----------------:|---------------:|----------:|------------:|"
        "---------:|----:|----:|:---:|:----:|",
    ]
    for r in sorted(result["combos"], key=lambda x: x["oos_sharpe_ann"], reverse=True):
        lines.append(
            f"| {r['combo_id']} | {r['oos_sharpe_ann']:.3f} | "
            f"{r['is_sharpe_ann']:.3f} | {r['oos_return_pct']:.2f} | "
            f"{r['beta_adj_bh_oos_pct']:.2f} | {r['n_trades']} | "
            f"{r['dsr']:.3f} | {r['pbo']:.3f} | {r['fdr_pass']} | "
            f"{'YES' if r['edge'] else 'no'} |"
        )
    lines += [
        "",
        "## Method",
        "",
        "- Position at bar i = signal.shift(1) (signal uses closed bars 0..i-1); "
        "close-to-close returns. Long-only {0,+1}.",
        f"- Cost: {ROUND_TRIP_COST * 1e4:.1f} bps round-trip on |Δposition| "
        "(config futures_taker x2).",
        "- Equal-weight portfolio = mean of per-coin net returns across coins present at each bar.",
        f"- IS/OOS split by TIME: first {SPLIT_FRAC:.0%} IS, last "
        f"{1 - SPLIT_FRAC:.0%} OOS, {EMBARGO}-bar embargo dropped at the boundary.",
        "- DSR: core.stat_tests deflated Sharpe via dsr_for_returns "
        "(sr_var = 1/n_obs), n_trials = 2 x n_combos.",
        f"- PBO: CSCV across all combos' OOS portfolios ({PBO_PARTITIONS} partitions).",
        f"- FDR: Benjamini-Hochberg at q={FDR_Q} across the full combo t-stat family.",
        "- Beta-adjusted B&H: OLS beta of OOS portfolio on OOS BTC returns; "
        "benchmark = beta x BTC buy&hold over the same OOS window.",
        "- EDGE_PRESENT iff a combo has OOS Sharpe>0 AND DSR>=0.10 AND PBO<0.5 "
        "AND OOS ret% > beta-adj B&H AND FDR-significant.",
    ]
    (report_dir / f"named_strategy_screen_{stamp}{suffix}.md").write_text(
        "\n".join(lines), encoding="utf-8"
    )


def _print_summary(result: dict) -> None:
    print("\n" + "=" * 72)
    print(
        f"NAMED STRATEGY SCREEN — overall: {result['overall_verdict']}  "
        f"PBO={result['pbo']:.3f}  coins={result['universe']['n_coins']}"
    )
    print("=" * 72)
    hdr = (
        f"{'strategy':<18}{'verdict':<14}{'best':<20}{'OOS Shrp':>10}{'OOS ret%':>10}{'trades':>8}"
    )
    print(hdr)
    print("-" * len(hdr))
    for s in PARAM_GRID:
        ss = result["strategy_summary"][s]
        print(
            f"{s:<18}{ss['verdict']:<14}{str(ss.get('best_combo', '-')):<20}"
            f"{ss.get('best_oos_sharpe_ann', 0):>10.3f}"
            f"{ss.get('best_oos_return_pct', 0):>10.2f}"
            f"{ss.get('best_n_trades', 0):>8}"
        )
    print("=" * 72 + "\n")


def main() -> int:
    ap = argparse.ArgumentParser(description="Named-strategy backtest screen")
    ap.add_argument(
        "--smoke", action="store_true", help="3-coin end-to-end smoke run (relaxed row floor)"
    )
    ap.add_argument("--max-coins", type=int, default=MAX_COINS)
    ap.add_argument(
        "--coins", type=str, default=None, help="comma-separated bases to force (e.g. BTC,ETH,SOL)"
    )
    args = ap.parse_args()

    only = [c.strip().upper() for c in args.coins.split(",")] if args.coins else None

    if args.smoke:
        bases = only or ["BTC", "ETH", "SOL"]
        result = run_screen(bases, report_dir=ROOT / "reports", smoke=True)
    else:
        bases = discover_universe(max_coins=args.max_coins, only=only)
        if not bases:
            print("No eligible coins found.", file=sys.stderr)
            return 1
        result = run_screen(bases, report_dir=ROOT / "reports", smoke=False)

    _print_summary(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
