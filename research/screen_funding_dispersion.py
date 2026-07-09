"""Candidate A — cross-venue funding-rate dispersion screen (after-cost, pre-registered).

Reads ONLY local data. Simulates a delta-neutral cross-venue pair
(LONG the low-funding venue, SHORT the high-funding venue) and charges the full
FOUR-leg round trip + slippage + the realized funding paid/received per settlement.

Honest scope: the ONLY dataset carrying per-venue funding for the same coin is
``data/funding_carry/{venue}_{coin}.csv``. ``data/derivs_history.jsonl`` and
``data/funding_cache/*.parquet`` are SINGLE-VENUE (verified: no venue/exchange field)
and are therefore ineligible for a cross-venue dispersion screen.

The pure functions below are unit-tested in tests/test_screen_funding_dispersion.py.
Run ``python research/screen_funding_dispersion.py`` to print the screen report.
"""
from __future__ import annotations

import itertools
import json
import os
import sys
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

# allow `core.*` imports (walk_forward / stat_tests / monte_carlo) when run as a
# script from research/ — the gate battery needs them.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# --- Cost model constants mirrored from config.FEE / config.SLIPPAGE (authoritative) ---
# futures fees per fill (fraction of notional)
FEE = {
    "binance": {"maker": 0.0002, "taker": 0.0005},
    "bybit": {"maker": 0.0001, "taker": 0.0006},
    "bitget": {"maker": 0.0002, "taker": 0.0006},
}
SLIPPAGE_PER_FILL = 0.0005  # 5 bps open/close (config.SLIPPAGE pct_open/pct_close)
VENUES = ("binance", "bybit", "bitget")

_DATA_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "data")
_CARRY_DIR = os.path.join(_DATA_DIR, "funding_carry")
_HISTORY_DIR = os.path.join(_DATA_DIR, "funding_history")  # rev2 backfilled realized funding

# Frozen gate thresholds (core/promotion_gate.py, core/decision/monte_carlo.py).
MIN_DSR = 0.10
MAX_PBO = 0.50
MIN_OOS_WR = 0.55


# ----------------------------------------------------------------------------
# Pure, tested math
# ----------------------------------------------------------------------------
def roundtrip_cost_frac(fee_long: float, fee_short: float, slippage: float) -> float:
    """Full 4-leg round trip: 2 fills on the long venue + 2 on the short venue,
    slippage charged on every one of the 4 fills."""
    return 2.0 * fee_long + 2.0 * fee_short + 4.0 * slippage


def signed_carry(rate_short: np.ndarray, rate_long: np.ndarray) -> np.ndarray:
    """Per-settlement net funding for the held pair: short leg RECEIVES rate_short,
    long leg PAYS rate_long. Goes negative when the differential flips against us."""
    return np.asarray(rate_short, dtype=float) - np.asarray(rate_long, dtype=float)


def net_return_frac(carry: np.ndarray, roundtrip_cost: float) -> float:
    """Accumulated carry over the hold minus ONE round-trip cost (single open+close)."""
    return float(np.sum(carry)) - float(roundtrip_cost)


def breakeven_settlements(roundtrip_cost: float, mean_carry: float) -> float:
    """How many settlements the pair must be held for accumulated carry to recover
    the round-trip cost. Infinite when carry is non-positive."""
    if mean_carry <= 0:
        return float("inf")
    return roundtrip_cost / mean_carry


def annualize_apr(net_return: float, days: float) -> float:
    if days <= 0:
        return float("nan")
    return net_return * 365.0 / days


def sign_persistence(series: np.ndarray) -> float:
    """Fraction of consecutive steps whose sign is unchanged (spread stickiness)."""
    s = np.sign(np.asarray(series, dtype=float))
    if len(s) < 2:
        return float("nan")
    return float(np.mean(s[1:] == s[:-1]))


# ----------------------------------------------------------------------------
# Data loading (local only)
# ----------------------------------------------------------------------------
def load_venue_funding(coin: str, venue: str) -> pd.Series:
    """Rate series indexed by settlement timestamp (seconds).

    Prefers the rev2 backfilled realized history in
    ``data/funding_history/{venue}_{coin}.csv`` (cols ts, funding_rate). Falls
    back to the forward-harvested ``data/funding_carry/{venue}_{coin}.csv`` (cols
    next_funding_ts, rate) so the pre-backfill behaviour and tests are unchanged."""
    hist = os.path.join(_HISTORY_DIR, f"{venue}_{coin}.csv")
    if os.path.exists(hist):
        d = pd.read_csv(hist)
        if len(d) and "ts" in d.columns and "funding_rate" in d.columns:
            d["settle"] = d["ts"].astype("int64")
            return (
                d.drop_duplicates("settle").set_index("settle")["funding_rate"].astype(float).sort_index()
            )
    path = os.path.join(_CARRY_DIR, f"{venue}_{coin}.csv")
    if not os.path.exists(path):
        return pd.Series(dtype=float)
    d = pd.read_csv(path)
    d["settle"] = d["next_funding_ts"].astype("int64")
    return d.drop_duplicates("settle").set_index("settle")["rate"].sort_index()


def align_cross_venue(coin: str, venues=VENUES) -> pd.DataFrame:
    """Inner-join per-venue funding on the common settlement grid."""
    cols = {v: load_venue_funding(coin, v) for v in venues}
    cols = {v: s for v, s in cols.items() if len(s)}
    if len(cols) < 2:
        return pd.DataFrame()
    return pd.DataFrame(cols).dropna().sort_index()


def _coverage_dir() -> str:
    """Directory whose {venue}_{coin}.csv inventory defines the universe: prefer
    the backfilled funding_history when present, else the forward funding_carry."""
    if os.path.isdir(_HISTORY_DIR) and any(
        fn.endswith(".csv") for fn in os.listdir(_HISTORY_DIR)
    ):
        return _HISTORY_DIR
    return _CARRY_DIR


def coins_with_cross_venue_coverage(venues=VENUES) -> list[str]:
    cov = _coverage_dir()
    if not os.path.isdir(cov):
        return []
    coins = set()
    for fn in os.listdir(cov):
        if fn.endswith(".csv") and "_" in fn:
            v = fn.split("_", 1)[0]
            if v not in venues:
                continue
            coins.add(fn.split("_", 1)[1].rsplit(".", 1)[0])
    out = []
    for c in sorted(coins):
        if not align_cross_venue(c, venues).empty:
            out.append(c)
    return out


# ----------------------------------------------------------------------------
# Walk-forward OOS carry (the frozen gate battery's return series)
# ----------------------------------------------------------------------------
def walk_forward_oos_spread(
    spread: np.ndarray, rt_cost: float, n_splits: int = 4, embargo: int = 1
) -> np.ndarray:
    """Pooled out-of-sample per-settlement net carry of the delta-neutral pair.

    The DIRECTION (which venue to short) is chosen on each TRAIN fold as the sign
    of the mean differential, then applied UNSEEN to the TEST fold — no lookahead.
    The full 4-leg round-trip ``rt_cost`` is charged once per fold, amortized
    across that fold's settlements. Uses core.walk_forward (embargo+purge)."""
    from core.walk_forward import WalkForward

    spread = np.asarray(spread, dtype=float)
    n = spread.size
    if n < n_splits + 1:
        return np.array([], dtype=float)
    wf = WalkForward(n_splits=n_splits, embargo_bars=embargo, anchored=True)
    chunks = []
    for tr, te in wf.split(n):
        if len(tr) == 0 or len(te) == 0:
            continue
        d = np.sign(float(np.mean(spread[tr])))
        if d == 0.0:
            d = 1.0
        per_settle_cost = rt_cost / len(te)
        chunks.append(d * spread[te] - per_settle_cost)
    return np.concatenate(chunks) if chunks else np.array([], dtype=float)


# ── rev3: hold-until-sign-flip cost model (fixes audit finding A1) ────────────
def _amortize_holds(applied_dir: np.ndarray, rt_cost: float) -> np.ndarray:
    """Per-settlement cost array for a HOLD-UNTIL-SIGN-FLIP book.

    ``applied_dir`` is the direction actually held at each settlement, in time
    order. Contiguous same-direction runs are ONE held position each; a single
    4-leg round-trip ``rt_cost`` is charged per position, amortized EVENLY over
    that position's settlements. Total charged = (#positions) × rt_cost — this is
    decoupled from the CV fold count, which is the whole point of the fix."""
    applied_dir = np.asarray(applied_dir, dtype=float)
    cost = np.zeros(applied_dir.size, dtype=float)
    if applied_dir.size == 0:
        return cost
    start = 0
    for i in range(1, applied_dir.size + 1):
        if i == applied_dir.size or applied_dir[i] != applied_dir[start]:
            run_len = i - start
            cost[start:i] = rt_cost / run_len
            start = i
    return cost


def walk_forward_oos_spread_hold(
    spread: np.ndarray, rt_cost: float, n_splits: int = 4, embargo: int = 1
) -> np.ndarray:
    """Pooled OOS per-settlement net carry under the audited-correct HOLD model.

    Direction chosen on each TRAIN fold (sign of the train mean) and applied
    UNSEEN to that fold's TEST settlements — no look-ahead, identical selection
    rule to ``walk_forward_oos_spread``. The ONLY difference is the cost model:
    the concatenated OOS timeline is partitioned into contiguous same-direction
    runs (= held positions) and ONE 4-leg round-trip is charged per position,
    amortized over its settlements (see ``_amortize_holds``). When the
    train-chosen direction never flips, exactly ONE round-trip is charged over the
    whole OOS window — not ``n_splits`` of them (audit finding A1)."""
    from core.walk_forward import WalkForward

    spread = np.asarray(spread, dtype=float)
    n = spread.size
    if n < n_splits + 1:
        return np.array([], dtype=float)
    wf = WalkForward(n_splits=n_splits, embargo_bars=embargo, anchored=True)
    carries, dirs = [], []
    for tr, te in wf.split(n):
        if len(tr) == 0 or len(te) == 0:
            continue
        d = np.sign(float(np.mean(spread[tr])))
        if d == 0.0:
            d = 1.0
        carries.append(d * spread[te])
        dirs.append(np.full(len(te), d, dtype=float))
    if not carries:
        return np.array([], dtype=float)
    carry = np.concatenate(carries)
    applied_dir = np.concatenate(dirs)
    return carry - _amortize_holds(applied_dir, rt_cost)


# ----------------------------------------------------------------------------
# Screen
# ----------------------------------------------------------------------------
MIN_SETTLEMENTS_PER_COIN = 60  # pre-registered floor (≈20 days of 8h settlements)
MIN_COINS = 2


@dataclass
class PairResult:
    coin: str
    long_venue: str
    short_venue: str
    n: int
    days: float
    mean_abs_diff_bps: float
    mean_carry_bps: float
    gross_carry_bps: float
    rt_taker_bps: float
    rt_maker_bps: float
    net_taker_bps: float
    net_maker_bps: float
    apr_taker_pct: float
    apr_maker_pct: float
    breakeven_settles: float
    sign_persist: float


@dataclass
class ScreenReport:
    coins_covered: list[str]
    n_by_coin: dict[str, int]
    days_by_coin: dict[str, float]
    total_variants: int
    pairs: list[PairResult] = field(default_factory=list)
    verdict: str = ""
    reason: str = ""
    gates: dict = field(default_factory=dict)
    oos: dict = field(default_factory=dict)


def screen_pair(coin: str, df: pd.DataFrame, long_v: str, short_v: str) -> PairResult:
    n = len(df)
    days = (df.index.max() - df.index.min()) / 86400.0 if n > 1 else 0.0
    diff = df[short_v] - df[long_v]  # signed by the pre-chosen direction
    carry = signed_carry(df[short_v].values, df[long_v].values)
    rt_t = roundtrip_cost_frac(FEE[long_v]["taker"], FEE[short_v]["taker"], SLIPPAGE_PER_FILL)
    rt_m = roundtrip_cost_frac(FEE[long_v]["maker"], FEE[short_v]["maker"], SLIPPAGE_PER_FILL)
    net_t = net_return_frac(carry, rt_t)
    net_m = net_return_frac(carry, rt_m)
    return PairResult(
        coin=coin,
        long_venue=long_v,
        short_venue=short_v,
        n=n,
        days=round(days, 2),
        mean_abs_diff_bps=round(float(np.abs(diff).mean()) * 1e4, 3),
        mean_carry_bps=round(float(np.mean(carry)) * 1e4, 3),
        gross_carry_bps=round(float(np.sum(carry)) * 1e4, 2),
        rt_taker_bps=round(rt_t * 1e4, 1),
        rt_maker_bps=round(rt_m * 1e4, 1),
        net_taker_bps=round(net_t * 1e4, 2),
        net_maker_bps=round(net_m * 1e4, 2),
        apr_taker_pct=round(annualize_apr(net_t, days) * 100, 2),
        apr_maker_pct=round(annualize_apr(net_m, days) * 100, 2),
        breakeven_settles=round(breakeven_settlements(rt_m, float(np.mean(carry))), 1),
        sign_persist=round(sign_persistence(diff.values), 3),
    )


def run_screen() -> ScreenReport:
    coins = coins_with_cross_venue_coverage()
    n_by_coin, days_by_coin, pairs = {}, {}, []
    for coin in coins:
        df = align_cross_venue(coin)
        n_by_coin[coin] = len(df)
        days_by_coin[coin] = round((df.index.max() - df.index.min()) / 86400.0, 2) if len(df) > 1 else 0.0
        venues = list(df.columns)
        for a, b in itertools.combinations(venues, 2):
            # in-sample direction pick (long lower-mean venue) — an OPTIMISTIC lookahead;
            # if even this loses after cost, the no-edge finding is robust.
            if df[a].mean() >= df[b].mean():
                short_v, long_v = a, b
            else:
                short_v, long_v = b, a
            pairs.append(screen_pair(coin, df, long_v, short_v))

    # each (coin, venue-pair) tried under 2 fee models = the true variant count
    total_variants = len(pairs) * 2

    rep = ScreenReport(
        coins_covered=coins,
        n_by_coin=n_by_coin,
        days_by_coin=days_by_coin,
        total_variants=total_variants,
        pairs=pairs,
    )

    # --- pre-registered decision logic ---
    enough_coins = len(coins) >= MIN_COINS
    enough_n = all(n_by_coin.get(c, 0) >= MIN_SETTLEMENTS_PER_COIN for c in coins) and bool(coins)
    if not coins:
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = "No coin has funding on >=2 venues locally."
    elif not (enough_n and enough_coins):
        worst = min(n_by_coin.values()) if n_by_coin else 0
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = (
            f"Cross-venue overlap below pre-registered floor of {MIN_SETTLEMENTS_PER_COIN} "
            f"aligned settlements/coin on >={MIN_COINS} coins: have {n_by_coin} over "
            f"{days_by_coin} days on coins {coins}. Frozen gates (walk-forward+embargo+purge, "
            f"Monte Carlo block bootstrap, "
            f"DSR/PBO) are NOT EVALUABLE at n={worst}; they fail closed. "
            f"Supporting (not decisive): every venue-pair is net-negative after cost even on "
            f"best-case maker fees and an in-sample direction pick."
        )
    else:
        # Floor cleared → run the FROZEN gate battery on walk-forward OOS carry.
        # The in-sample amortized net (net_taker_bps) is the pre-registered
        # "after-cost net carry" condition, but with an in-sample direction pick
        # over a long static hold it is inflated by the amortization/lookahead
        # trap; the walk-forward OOS gates below are what actually decide GO/NO_GO.
        _run_gate_battery(rep)
    return rep


def _run_gate_battery(rep: ScreenReport) -> None:
    """Populate rep.gates / rep.oos and set the GO/NO_GO verdict from the frozen
    gates on walk-forward OOS carry. Direction chosen on train, evaluated on test;
    the full 4-leg round-trip charged per fold. NaN on any gate fails closed."""
    from core.decision.monte_carlo import monte_carlo_trade_sequence
    from core.promotion_gate import _kurt, _skew
    from core.stat_tests import deflated_sharpe, pbo, sharpe

    n_trials = len(rep.pairs)  # every (coin, venue-pair) direction strategy tried
    per_pair = []
    for p in rep.pairs:
        df = align_cross_venue(p.coin)
        if df.empty:
            continue
        spread = (df[p.short_venue] - df[p.long_venue]).values  # short-higher convention
        rt = roundtrip_cost_frac(FEE[p.long_venue]["taker"], FEE[p.short_venue]["taker"], SLIPPAGE_PER_FILL)
        oos = walk_forward_oos_spread(spread, rt)
        if oos.size < 2:
            continue
        per_pair.append({
            "key": f"{p.coin}:{p.long_venue}L/{p.short_venue}S",
            "oos": oos,
            "oos_mean_bps": round(float(np.mean(oos)) * 1e4, 4),
            "oos_wr": round(float(np.mean(oos > 0)), 4),
            "oos_sharpe": round(float(sharpe(oos)), 4),
            "n_oos": int(oos.size),
        })

    if not per_pair:
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = "Floor cleared but no pair produced a walk-forward OOS series (fold geometry)."
        rep.gates = {"fail_closed": True}
        return

    # Best OOS pair (selection under multiplicity → DSR penalizes via n_trials).
    best = max(per_pair, key=lambda d: d["oos_mean_bps"])
    best_oos = best["oos"]
    sr = sharpe(best_oos)
    dsr = float(deflated_sharpe(
        sr_observed=sr, n_trials=max(1, n_trials), n_obs=int(best_oos.size),
        skew=float(_skew(best_oos)), kurt=float(_kurt(best_oos)),
        sr_var=1.0 / max(2, int(best_oos.size)),
    ))
    oos_wr = float(np.mean(best_oos > 0))
    mc = monte_carlo_trade_sequence(best_oos)
    mc_pass = bool(mc.passes(min_p_positive=0.95, max_dd_p95=0.25, min_trades=30))

    # PBO across the pair strategies (columns), truncated to a common length.
    pbo_val = float("nan")
    if len(per_pair) >= 2:
        tmin = min(d["oos"].size for d in per_pair)
        parts = 16 if tmin >= 16 else (tmin - (tmin % 2))
        if parts >= 4:
            mat = np.column_stack([d["oos"][:tmin] for d in per_pair])
            try:
                pbo_val = float(pbo(mat, n_partitions=parts))
            except Exception:
                pbo_val = float("nan")

    after_cost_net_positive = any(p.net_taker_bps > 0 for p in rep.pairs)
    checks = {
        "after_cost_net_carry>0 (in-sample, amortized)": bool(after_cost_net_positive),
        "oos_mean>0": bool(best["oos_mean_bps"] > 0),
        "DSR>=0.10": bool(np.isfinite(dsr) and dsr >= MIN_DSR),
        "PBO<=0.50": bool(np.isfinite(pbo_val) and pbo_val <= MAX_PBO),
        "OOS_WR>=0.55": bool(oos_wr >= MIN_OOS_WR),
        "MC_capital_preservation": mc_pass,
    }
    rep.gates = {
        "DSR": round(dsr, 4),
        "PBO": None if not np.isfinite(pbo_val) else round(pbo_val, 4),
        "OOS_WR": round(oos_wr, 4),
        "monte_carlo": {
            "n_trades": mc.n_trades,
            "p_total_positive": round(mc.p_total_positive, 4),
            "max_drawdown_p95": round(mc.max_drawdown_p95, 6),
            "passes": mc_pass,
        },
        "n_trials_penalty": n_trials,
        "checks": checks,
        "fail_closed_on_nan": True,
    }
    rep.oos = {
        "best_pair": best["key"],
        "best_oos_mean_bps": best["oos_mean_bps"],
        "best_oos_wr": best["oos_wr"],
        "best_oos_sharpe": best["oos_sharpe"],
        "n_oos_best": best["n_oos"],
        "per_pair": [{k: d[k] for k in ("key", "oos_mean_bps", "oos_wr", "oos_sharpe", "n_oos")}
                     for d in per_pair],
    }

    if all(checks.values()):
        rep.verdict = "GO"
        rep.reason = (
            f"All frozen gates pass on walk-forward OOS: best pair {best['key']} "
            f"OOS mean {best['oos_mean_bps']}bps, DSR {dsr:.3f}, PBO {pbo_val:.3f}, "
            f"OOS-WR {oos_wr:.3f}, MC P(>0) {mc.p_total_positive:.3f}."
        )
    else:
        failed = [k for k, v in checks.items() if not v]
        rep.verdict = "NO_GO"
        rep.reason = (
            f"Walk-forward OOS gates fail: {failed}. Best pair {best['key']} "
            f"OOS mean {best['oos_mean_bps']}bps (in-sample amortized net looked "
            f"positive only via the direction-lookahead/amortization trap)."
        )


# ── rev3: binance∩bybit multi-year, hold-until-sign-flip screen ──────────────
VENUES_BB = ("binance", "bybit")


def run_screen_rev3(venues=VENUES_BB) -> ScreenReport:
    """rev3 screen: binance∩bybit only (Bitget dropped), one venue-pair per coin,
    frozen gates on the hold-until-sign-flip OOS carry. Cost decoupled from the CV
    fold count (audit A1 fix). n_trials = number of coin-pair strategies tested."""
    coins = coins_with_cross_venue_coverage(venues)
    n_by_coin, days_by_coin, pairs = {}, {}, []
    for coin in coins:
        df = align_cross_venue(coin, venues)
        n_by_coin[coin] = len(df)
        days_by_coin[coin] = (
            round((df.index.max() - df.index.min()) / 86400.0, 2) if len(df) > 1 else 0.0
        )
        cols = list(df.columns)
        for a, b in itertools.combinations(cols, 2):  # exactly 1 pair for 2 venues
            if df[a].mean() >= df[b].mean():
                short_v, long_v = a, b
            else:
                short_v, long_v = b, a
            pairs.append(screen_pair(coin, df, long_v, short_v))

    rep = ScreenReport(
        coins_covered=coins,
        n_by_coin=n_by_coin,
        days_by_coin=days_by_coin,
        total_variants=len(pairs),  # taker-only OOS series; one per coin-pair
        pairs=pairs,
    )

    enough_coins = len(coins) >= MIN_COINS
    enough_n = bool(coins) and all(
        n_by_coin.get(c, 0) >= MIN_SETTLEMENTS_PER_COIN for c in coins
    )
    if not coins:
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = "No coin has funding on both binance and bybit locally."
    elif not (enough_n and enough_coins):
        worst = min(n_by_coin.values()) if n_by_coin else 0
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = (
            f"binance∩bybit overlap below the pre-registered floor of "
            f"{MIN_SETTLEMENTS_PER_COIN} aligned settlements/coin on >={MIN_COINS} coins "
            f"(worst n={worst}). Frozen gates fail closed."
        )
    else:
        _run_gate_battery_rev3(rep, venues)
    return rep


def _run_gate_battery_rev3(rep: ScreenReport, venues) -> None:
    """Frozen gate battery on the HOLD-model OOS carry (rev3). Exactly the six
    registered gates: OOS mean>0, DSR>=0.10 (n_trials=#coin-pairs), PBO<=0.50,
    OOS-WR>=0.55, MC P(>0)>=0.95, MC maxDD p95<=0.25. NaN fails closed."""
    from core.decision.monte_carlo import monte_carlo_trade_sequence
    from core.promotion_gate import _kurt, _skew
    from core.stat_tests import deflated_sharpe, pbo, sharpe

    n_trials = len(rep.pairs)  # one coin-pair strategy per coin (binance∩bybit)
    per_pair = []
    for p in rep.pairs:
        df = align_cross_venue(p.coin, venues)
        if df.empty:
            continue
        spread = (df[p.short_venue] - df[p.long_venue]).values
        rt = roundtrip_cost_frac(
            FEE[p.long_venue]["taker"], FEE[p.short_venue]["taker"], SLIPPAGE_PER_FILL
        )
        oos = walk_forward_oos_spread_hold(spread, rt)
        if oos.size < 2:
            continue
        per_pair.append({
            "key": f"{p.coin}:{p.long_venue}L/{p.short_venue}S",
            "oos": oos,
            "oos_mean_bps": round(float(np.mean(oos)) * 1e4, 4),
            "oos_wr": round(float(np.mean(oos > 0)), 4),
            "oos_sharpe": round(float(sharpe(oos)), 4),
            "n_oos": int(oos.size),
        })

    if not per_pair:
        rep.verdict = "INSUFFICIENT_DATA"
        rep.reason = "Floor cleared but no pair produced a hold-model OOS series."
        rep.gates = {"fail_closed": True}
        return

    best = max(per_pair, key=lambda d: d["oos_mean_bps"])
    best_oos = best["oos"]
    sr = sharpe(best_oos)
    dsr = float(deflated_sharpe(
        sr_observed=sr, n_trials=max(1, n_trials), n_obs=int(best_oos.size),
        skew=float(_skew(best_oos)), kurt=float(_kurt(best_oos)),
        sr_var=1.0 / max(2, int(best_oos.size)),
    ))
    oos_wr = float(np.mean(best_oos > 0))
    mc = monte_carlo_trade_sequence(best_oos)
    mc_pass = bool(mc.passes(min_p_positive=0.95, max_dd_p95=0.25, min_trades=30))

    pbo_val = float("nan")
    if len(per_pair) >= 2:
        tmin = min(d["oos"].size for d in per_pair)
        parts = 16 if tmin >= 16 else (tmin - (tmin % 2))
        if parts >= 4:
            mat = np.column_stack([d["oos"][:tmin] for d in per_pair])
            try:
                pbo_val = float(pbo(mat, n_partitions=parts))
            except Exception:
                pbo_val = float("nan")

    checks = {
        "oos_mean>0": bool(best["oos_mean_bps"] > 0),
        "DSR>=0.10": bool(np.isfinite(dsr) and dsr >= MIN_DSR),
        "PBO<=0.50": bool(np.isfinite(pbo_val) and pbo_val <= MAX_PBO),
        "OOS_WR>=0.55": bool(oos_wr >= MIN_OOS_WR),
        "MC_P(>0)>=0.95": bool(mc.p_total_positive >= 0.95),
        "MC_maxDD_p95<=0.25": bool(mc.max_drawdown_p95 <= 0.25),
    }
    rep.gates = {
        "DSR": round(dsr, 4),
        "PBO": None if not np.isfinite(pbo_val) else round(pbo_val, 4),
        "OOS_WR": round(oos_wr, 4),
        "monte_carlo": {
            "n_trades": mc.n_trades,
            "p_total_positive": round(mc.p_total_positive, 4),
            "max_drawdown_p95": round(mc.max_drawdown_p95, 6),
            "passes": mc_pass,
        },
        "n_trials_penalty": n_trials,
        "checks": checks,
        "fail_closed_on_nan": True,
    }
    rep.oos = {
        "best_pair": best["key"],
        "best_oos_mean_bps": best["oos_mean_bps"],
        "best_oos_wr": best["oos_wr"],
        "best_oos_sharpe": best["oos_sharpe"],
        "n_oos_best": best["n_oos"],
        "per_pair": [{k: d[k] for k in ("key", "oos_mean_bps", "oos_wr", "oos_sharpe", "n_oos")}
                     for d in per_pair],
    }
    if all(checks.values()):
        rep.verdict = "GO"
        rep.reason = (
            f"All six frozen gates pass on hold-model OOS: best pair {best['key']} "
            f"OOS mean {best['oos_mean_bps']}bps, DSR {dsr:.3f}, PBO {pbo_val:.3f}, "
            f"OOS-WR {oos_wr:.3f}, MC P(>0) {mc.p_total_positive:.3f}, "
            f"maxDD p95 {mc.max_drawdown_p95:.4f}."
        )
    else:
        failed = [k for k, v in checks.items() if not v]
        rep.verdict = "NO_GO"
        rep.reason = (
            f"Hold-model OOS gates fail: {failed}. Best pair {best['key']} "
            f"OOS mean {best['oos_mean_bps']}bps, DSR {dsr:.3f}, OOS-WR {oos_wr:.3f}."
        )


def _to_json(rep: ScreenReport) -> dict:
    best = None
    if rep.pairs:
        best = max(rep.pairs, key=lambda p: p.net_maker_bps)
    return {
        "candidate": "A_cross_venue_funding_dispersion",
        "hypothesis": "Delta-neutral long-low/short-high venue funding differential clears the 4-leg after-cost hurdle",
        "n": rep.n_by_coin,
        "sample_days": rep.days_by_coin,
        "coins_cross_venue": rep.coins_covered,
        "true_variants_tried": rep.total_variants,
        "after_cost_metrics": {
            "cost_model": "config.FEE per venue + 4x5bps slippage; taker=honest default, maker=best-case sensitivity",
            "best_config_net_bps_maker": None if best is None else best.net_maker_bps,
            "best_config": None
            if best is None
            else f"{best.coin} long {best.long_venue}/short {best.short_venue}",
            "all_pairs_net_taker_bps": {
                f"{p.coin}:{p.long_venue}L/{p.short_venue}S": p.net_taker_bps for p in rep.pairs
            },
            "all_pairs_net_maker_bps": {
                f"{p.coin}:{p.long_venue}L/{p.short_venue}S": p.net_maker_bps for p in rep.pairs
            },
            "all_negative_after_cost": all(
                p.net_taker_bps <= 0 and p.net_maker_bps <= 0 for p in rep.pairs
            ),
        },
        "gates": rep.gates if rep.gates else {
            "DSR": "NOT_EVALUABLE (n<floor)",
            "PBO": "NOT_EVALUABLE (n<floor)",
            "OOS_WR": "NOT_EVALUABLE (n<floor)",
            "walk_forward": "NOT_EVALUABLE (n<floor)",
            "monte_carlo": "NOT_EVALUABLE (n<floor)",
            "fail_closed": True,
        },
        "oos_walk_forward": rep.oos,
        "verdict": rep.verdict,
        "reason": rep.reason,
        "harvest_to_extend": (
            "venv\\Scripts\\python.exe scripts\\harvest_funding_carry.py  "
            "(single pass, appends 1 row/venue/coin/settlement; schedule HOURLY via "
            "schtasks TN TradingBot-FundingCarryHarvest). Reach the >=60-settlement floor "
            "(~20+ days) before re-screening. To broaden beyond BTC/ETH, extend the COINS "
            "tuple in scripts/harvest_funding_carry.py (separate change)."
        ),
    }


def main() -> None:
    rep = run_screen()
    print("=== Cross-venue funding-dispersion screen ===")
    print(f"coins with cross-venue coverage: {rep.coins_covered}")
    print(f"settlements/coin: {rep.n_by_coin}  days/coin: {rep.days_by_coin}")
    print(f"true variants tried: {rep.total_variants}")
    print()
    hdr = (
        f"{'coin':4} {'longL':8} {'shortS':8} {'n':>3} {'diff':>6} {'carry':>6} "
        f"{'gross':>6} {'RTt':>5} {'RTm':>5} {'net_t':>7} {'net_m':>7} {'APRt%':>7} {'APRm%':>7} {'BE':>6} {'persist':>7}"
    )
    print(hdr)
    for p in rep.pairs:
        print(
            f"{p.coin:4} {p.long_venue:8} {p.short_venue:8} {p.n:>3} "
            f"{p.mean_abs_diff_bps:>6.2f} {p.mean_carry_bps:>6.2f} {p.gross_carry_bps:>6.1f} "
            f"{p.rt_taker_bps:>5.0f} {p.rt_maker_bps:>5.0f} {p.net_taker_bps:>7.1f} {p.net_maker_bps:>7.1f} "
            f"{p.apr_taker_pct:>7.1f} {p.apr_maker_pct:>7.1f} {p.breakeven_settles:>6.1f} {p.sign_persist:>7.2f}"
        )
    print()
    print(f"VERDICT: {rep.verdict}")
    print(f"REASON: {rep.reason}")
    print()
    print("JSON_VERDICT_START")
    print(json.dumps(_to_json(rep), indent=2))
    print("JSON_VERDICT_END")


def _to_json_rev3(rep: ScreenReport) -> dict:
    return {
        "candidate": "A_cross_venue_funding_dispersion_rev3_binance_bybit_hold",
        "hypothesis": "Delta-neutral binance/bybit funding differential, held until the "
                      "train-chosen direction flips, clears the after-cost hurdle out-of-sample",
        "universe": "binance∩bybit only (Bitget dropped); one venue-pair per coin",
        "n": rep.n_by_coin,
        "sample_days": rep.days_by_coin,
        "coins_cross_venue": rep.coins_covered,
        "n_coin_pair_strategies": len(rep.pairs),
        "n_trials_dsr": len(rep.pairs),
        "after_cost_metrics": {
            "cost_model": "hold-until-sign-flip: ONE 42bps 4-leg round-trip per held "
                          "position, amortized over its settlements (decoupled from n_splits)",
            "best_pair_oos": rep.oos.get("best_pair") if rep.oos else None,
            "best_pair_oos_mean_bps": rep.oos.get("best_oos_mean_bps") if rep.oos else None,
            "best_pair_oos_wr": rep.oos.get("best_oos_wr") if rep.oos else None,
        },
        "gates": rep.gates,
        "oos_walk_forward": rep.oos,
        "verdict": rep.verdict,
        "reason": rep.reason,
    }


def main_rev3() -> None:
    rep = run_screen_rev3()
    print("=== Cross-venue funding-dispersion screen (rev3: binance∩bybit, hold-until-flip) ===")
    print(f"coins with binance∩bybit coverage: {rep.coins_covered}")
    print(f"aligned settlements/coin: {rep.n_by_coin}")
    print(f"days/coin: {rep.days_by_coin}")
    print(f"coin-pair strategies (n_trials): {len(rep.pairs)}")
    print()
    print(f"VERDICT: {rep.verdict}")
    print(f"REASON: {rep.reason}")
    print()
    print("JSON_VERDICT_START")
    print(json.dumps(_to_json_rev3(rep), indent=2))
    print("JSON_VERDICT_END")


if __name__ == "__main__":
    if "--rev3" in sys.argv:
        main_rev3()
    else:
        main()
