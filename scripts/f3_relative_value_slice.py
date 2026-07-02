"""Phase D-F3 — market-neutral relative-value long/short F3 StrategySpec slice.

Extends the shipped S2 cross-sectional basket (``scripts/s2_basket_slice.py``)
into a MULTI-FEATURE ranked, dollar-neutral, beta-and-cluster-capped relative-
value book:

    align per-symbol daily candles -> point-in-time delisted-safe universe (reused
    from S2) -> composite cross-sectional RANK over multiple features (1/3/7/14d
    relative strength, short-EMA slope, volume trend, realized-vol penalty, plus
    forward-collect-gated OI / spread-depth / funding-drag slots) -> long top-q /
    short bottom-q, inverse-vol weighted, DOLLAR-NEUTRAL -> per-symbol +
    correlated-cluster weight caps -> BTC/ETH beta cap -> decide at closed bar t,
    fill at t+1 OPEN, hold to next rebalance -> after-cost net return per period ->
    walk-forward OOS -> accept() verdict + F3 pipeline gate -> EvidenceRegistry.

A correct NO_GO is success; the deliverable is pipeline correctness (survivorship
control, dollar-neutrality, beta/cluster caps, honest after-cost gate), NOT alpha.

FEATURE GATING (mirrors S2): only deep-history-backtestable features (price
momentum, EMA slope, volume trend, realized vol) carry weight. OI-change,
spread/depth and funding-drag are FORWARD-collect features listed in
``FORWARD_COLLECT_FEATURES`` and gated OUT (weight 0) so the backtest never
backfills a signal that did not exist historically. They are wired as optional
inputs so a live/forward run can turn them on without a code change.

Live is INFEASIBLE at $420: a dollar-neutral multi-name L/S book needs min-notional
on many simultaneous 2-sided legs, far exceeding the account. Research/PAPER only.
No network: reads data/ohlcv_cache/<SYM>-USDT_1d.parquet.

CLI:  venv/Scripts/python.exe scripts/f3_relative_value_slice.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.correlation_manager import CorrelationManager  # noqa: E402
from core.cost_model import round_trip_cost  # noqa: E402
from core.decision.promotion_loop import (  # noqa: E402
    ACTIVE_STRATEGIES_PATH,
    accept,
    register_evidence,
)
from core.walk_forward import WalkForward  # noqa: E402
from research.funding_carry_lab import pnl_concentration_ok  # noqa: E402
from scripts.s2_basket_slice import (  # noqa: E402
    CACHE,
    _load_daily,
    _realized_vol,
    _zscore,
    point_in_time_universe,
)

DEFAULT_UNIVERSE = ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA", "AVAX", "DOT", "LINK", "ATOM")
BETA_REFERENCE = "BTC"
LIVE_FEASIBILITY = "INFEASIBLE-AT-$420"
UNIVERSE_METHOD = "point_in_time_with_delisted_terminal_paths"

# Ranking feature weights. Deep-history features carry weight; forward-collect
# features are pinned to 0.0 (gated out) until enough forward data accrues.
DEFAULT_FEATURE_WEIGHTS = {
    "rs1": 0.5, "rs3": 1.0, "rs7": 1.0, "rs14": 0.5,
    "ema_slope": 0.5,
    "vol_trend": 0.3,
    "rvol_penalty": -0.3,          # higher realized vol -> lower score
    "oi_conf": 0.0,               # forward-collect (gated out)
    "spread_depth_penalty": 0.0,  # forward-collect (gated out)
    "funding_drag": 0.0,          # forward-collect (gated out)
}
FORWARD_COLLECT_FEATURES = ("oi_conf", "spread_depth_penalty", "funding_drag")

RS_LOOKBACKS = {"rs1": 1, "rs3": 3, "rs7": 7, "rs14": 14}
EMA_SPAN = 10
EMA_SLOPE_WIN = 5
VOL_TREND_WIN = 7
DEFAULT_VOL_LOOKBACK = 20
DEFAULT_LOOKBACK = 14        # deepest momentum horizon -> universe history floor

# Construction caps.
DEFAULT_MAX_SYMBOL_W = 0.20     # |weight| per name (fraction of the 1.0 gross book)
DEFAULT_MAX_CLUSTER_W = 0.50    # gross |weight| per correlated cluster
DEFAULT_BETA_CAP = 0.15         # net book beta to BTC

# F3 pipeline acceptance gate (recorded alongside accept()).
F3_MIN_REBALANCES = 200
F3_PF_FLOOR = 1.3
F3_MAX_CONCENTRATION_PCT = 40.0
F3_STRESS_COST_MULT = 2.0

_CM = CorrelationManager()


def load_panel_v(symbols=DEFAULT_UNIVERSE, cache: str = CACHE):
    """Align per-symbol daily closes / opens / volumes on a common ts index.

    Like S2's ``load_panel`` but also returns a volumes frame for the volume-trend
    feature. Missing cells are NaN (point-in-time not-listed / delisted signal).
    """
    close_cols, open_cols, vol_cols = {}, {}, {}
    for s in symbols:
        df = _load_daily(s, cache)
        if df is None:
            continue
        idx = pd.to_datetime(df["ts"], unit="s")
        close_cols[s] = pd.Series(df["close"].to_numpy(float), index=idx)
        open_cols[s] = pd.Series(df["open"].to_numpy(float), index=idx)
        vol_cols[s] = pd.Series(df["volume"].to_numpy(float), index=idx)
    if not close_cols:
        raise FileNotFoundError(f"no daily cache for any of {symbols} at {cache}")
    closes = pd.DataFrame(close_cols).sort_index().reset_index(drop=True)
    opens = pd.DataFrame(open_cols).sort_index().reset_index(drop=True)
    vols = pd.DataFrame(vol_cols).sort_index().reset_index(drop=True)
    return closes, opens, vols


def cluster_of(symbol: str):
    """Correlated-cluster name for a symbol (reuses CorrelationManager groups)."""
    return _CM.get_group(symbol)


# ── feature extraction + composite cross-sectional rank ──────────────────
def _ema(seg: np.ndarray, span: int) -> np.ndarray:
    if seg.size == 0:
        return seg
    alpha = 2.0 / (span + 1.0)
    out = np.empty_like(seg, dtype=float)
    out[0] = seg[0]
    for i in range(1, seg.size):
        out[i] = alpha * seg[i] + (1.0 - alpha) * out[i - 1]
    return out


def _symbol_features(closes, vols, sym, t, *, vol_lookback) -> dict:
    """Raw (un-z-scored) feature values for one symbol as of closed bar t."""
    feats: dict = {}
    c = closes[sym]
    for name, lb in RS_LOOKBACKS.items():
        if t - lb >= 0:
            now, past = c.iloc[t], c.iloc[t - lb]
            feats[name] = (float(now) / float(past) - 1.0) if (
                not math.isnan(now) and not math.isnan(past) and past != 0) else float("nan")
        else:
            feats[name] = float("nan")

    seg = c.iloc[max(0, t - (EMA_SPAN + EMA_SLOPE_WIN)):t + 1].to_numpy(float)
    seg = seg[~np.isnan(seg)]
    if seg.size > EMA_SLOPE_WIN:
        e = _ema(seg, EMA_SPAN)
        ref = e[-1 - EMA_SLOPE_WIN]
        feats["ema_slope"] = float(e[-1] / ref - 1.0) if ref != 0 else float("nan")
    else:
        feats["ema_slope"] = float("nan")

    v = vols[sym]
    recent = v.iloc[max(0, t - VOL_TREND_WIN + 1):t + 1].to_numpy(float)
    older = v.iloc[max(0, t - 2 * VOL_TREND_WIN + 1):max(0, t - VOL_TREND_WIN + 1)].to_numpy(float)
    recent, older = recent[~np.isnan(recent)], older[~np.isnan(older)]
    if recent.size and older.size and older.mean() > 0:
        feats["vol_trend"] = float(recent.mean() / older.mean() - 1.0)
    else:
        feats["vol_trend"] = float("nan")

    rv = _realized_vol(closes, sym, t, vol_lookback)
    feats["rvol_penalty"] = float(rv) if (rv and np.isfinite(rv)) else float("nan")
    return feats


def composite_rank(
    closes, vols, t, uni, *,
    oi: dict | None = None,
    funding: dict | None = None,
    spread_depth: dict | None = None,
    feature_weights: dict | None = None,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
):
    """Composite cross-sectional score per symbol (higher = more long).

    Each deep-history feature is cross-sectionally z-scored and combined with its
    ``feature_weights`` weight; forward-collect features (OI/spread-depth/funding)
    are wired but weight-0 by default so passing them does not move the default
    score. Returns ``(scores, breakdown)`` where breakdown[feat][sym] is the
    weighted contribution.
    """
    w = dict(DEFAULT_FEATURE_WEIGHTS)
    if feature_weights:
        w.update(feature_weights)
    if not uni:
        return {}, {}

    raw = {s: _symbol_features(closes, vols, s, t, vol_lookback=vol_lookback) for s in uni}
    # inject optional forward-collect features (default neutral / weight 0)
    for s in uni:
        raw[s]["oi_conf"] = float((oi or {}).get(s, 0.0))
        raw[s]["funding_drag"] = float((funding or {}).get(s, 0.0))
        raw[s]["spread_depth_penalty"] = float((spread_depth or {}).get(s, 0.0))

    feat_names = list(DEFAULT_FEATURE_WEIGHTS.keys())
    scores = {s: 0.0 for s in uni}
    breakdown: dict = {}
    for feat in feat_names:
        vals = np.array([raw[s].get(feat, float("nan")) for s in uni], dtype=float)
        filled = np.where(np.isfinite(vals), vals, np.nan)
        mean = np.nanmean(filled) if np.isfinite(filled).any() else 0.0
        filled = np.where(np.isfinite(filled), filled, mean)
        z = _zscore(filled)
        contrib = float(w.get(feat, 0.0)) * z
        breakdown[feat] = {s: float(contrib[i]) for i, s in enumerate(uni)}
        for i, s in enumerate(uni):
            scores[s] += float(contrib[i])
    return scores, breakdown


# ── weight construction: dollar-neutral + caps + beta cap ────────────────
def apply_caps(weights: dict, clusters: dict, *, max_symbol_w: float, max_cluster_w: float) -> dict:
    """Clip per-symbol |weight| to ``max_symbol_w`` and scale each correlated
    cluster's gross |weight| down to ``max_cluster_w``. Symbols with no cluster
    (``None``) are not cluster-capped. Preserves sign."""
    capped = {
        s: max(-max_symbol_w, min(max_symbol_w, float(v)))
        for s, v in weights.items()
    }
    gross: dict = {}
    for s, v in capped.items():
        cl = clusters.get(s)
        if cl is None:
            continue
        gross[cl] = gross.get(cl, 0.0) + abs(v)
    for cl, g in gross.items():
        if g > max_cluster_w and g > 0:
            scale = max_cluster_w / g
            for s in capped:
                if clusters.get(s) == cl:
                    capped[s] *= scale
    return capped


def apply_beta_cap(signed_weights: dict, betas: dict, *, beta_cap: float):
    """Cap the book's net beta (to BETA_REFERENCE) at ``beta_cap`` by uniformly
    scaling the book when the net exceeds it. Returns (weights, net_beta)."""
    net = sum(float(v) * float(betas.get(s, 1.0)) for s, v in signed_weights.items())
    if abs(net) > beta_cap and abs(net) > 0:
        scale = beta_cap / abs(net)
        out = {s: float(v) * scale for s, v in signed_weights.items()}
        return out, net * scale
    return dict(signed_weights), net


def _inv_vol_side(side_syms, closes, t, vol_lookback, gross=0.5) -> dict:
    inv = {}
    for s in side_syms:
        v = _realized_vol(closes, s, t, vol_lookback) if closes is not None else None
        inv[s] = (1.0 / v) if (v and np.isfinite(v)) else 1.0
    tot = sum(inv.values()) or 1.0
    return {s: gross * inv[s] / tot for s in side_syms}


def ls_weights(
    scores: dict, *,
    top_q: float = 0.2,
    bottom_q: float = 0.2,
    closes=None,
    t: int | None = None,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
    betas: dict | None = None,
    clusters: dict | None = None,
    max_symbol_w: float | None = None,
    max_cluster_w: float | None = None,
    beta_cap: float | None = None,
) -> dict:
    """Build a dollar-neutral L/S weight book from composite scores.

    Long top-q, short bottom-q; inverse-vol weighted (equal weight when no price
    frame is supplied), each side gross 0.5 so the book is dollar-neutral. Optional
    per-symbol + cluster caps and a BTC/ETH beta cap are applied when their
    parameters are supplied. Returns a dict with ``weights`` (signed) + diagnostics.
    """
    syms = list(scores.keys())
    if len(syms) < 2:
        return {"weights": {}, "long_syms": [], "short_syms": [], "net_beta": 0.0, "gross": 0.0}
    order = sorted(syms, key=lambda s: scores[s])  # ascending
    n_long = max(1, int(math.ceil(top_q * len(syms))))
    n_short = max(1, int(math.ceil(bottom_q * len(syms))))
    long_syms = list(reversed(order[-n_long:]))
    short_syms = order[:n_short]

    lw = _inv_vol_side(long_syms, closes, t, vol_lookback, gross=0.5)
    sw = _inv_vol_side(short_syms, closes, t, vol_lookback, gross=0.5)
    signed = {s: lw[s] for s in long_syms}
    for s in short_syms:
        signed[s] = -sw[s]

    if clusters is not None and max_symbol_w is not None and max_cluster_w is not None:
        signed = apply_caps(signed, clusters, max_symbol_w=max_symbol_w, max_cluster_w=max_cluster_w)
    net_beta = 0.0
    if betas is not None and beta_cap is not None:
        signed, net_beta = apply_beta_cap(signed, betas, beta_cap=beta_cap)
    elif betas is not None:
        net_beta = sum(float(v) * float(betas.get(s, 1.0)) for s, v in signed.items())

    return {
        "weights": signed,
        "long_syms": long_syms,
        "short_syms": short_syms,
        "net_beta": float(net_beta),
        "gross": float(sum(abs(v) for v in signed.values())),
    }


def _symbol_beta(closes, sym, ref, t, win) -> float:
    if sym == ref:
        return 1.0
    lo = max(0, t - win)
    a = closes[sym].iloc[lo:t + 1].to_numpy(float)
    b = closes[ref].iloc[lo:t + 1].to_numpy(float)
    m = ~(np.isnan(a) | np.isnan(b))
    a, b = a[m], b[m]
    if a.size < 3:
        return 1.0
    ra, rb = np.diff(a) / a[:-1], np.diff(b) / b[:-1]
    vb = float(np.var(rb, ddof=0))
    if vb <= 0:
        return 1.0
    return float(np.cov(ra, rb, ddof=0)[0, 1] / vb)


# ── replay ────────────────────────────────────────────────────────────────
def simulate_f3(
    closes, opens, vols, *,
    lookback: int = DEFAULT_LOOKBACK,
    vol_lookback: int = DEFAULT_VOL_LOOKBACK,
    rebalance_every: int = 1,
    top_q: float = 0.2,
    bottom_q: float = 0.2,
    venue: str = "binance",
    beta_ref: str = BETA_REFERENCE,
    max_symbol_w: float = DEFAULT_MAX_SYMBOL_W,
    max_cluster_w: float = DEFAULT_MAX_CLUSTER_W,
    beta_cap: float = DEFAULT_BETA_CAP,
    slip_mult: float = 1.0,
    tier_mult: float = 1.0,
) -> list[dict]:
    """Replay the ranked dollar-neutral L/S book; one RESOLVED record per rebalance."""
    n = len(closes)
    rt = round_trip_cost(venue, slip_mult=slip_mult, tier_mult=tier_mult)
    start = max(lookback, vol_lookback, 2 * VOL_TREND_WIN) + 1
    rebal = list(range(start, n - 1, max(1, int(rebalance_every))))
    periods: list[dict] = []

    for ri, t in enumerate(rebal):
        uni = point_in_time_universe(closes, t, lookback=lookback)
        if len(uni) < 2:
            continue
        scores, _ = composite_rank(closes, vols, t, uni, vol_lookback=vol_lookback)
        clusters = {s: cluster_of(s) for s in uni}
        betas = None
        if beta_ref in uni:
            betas = {s: _symbol_beta(closes, s, beta_ref, t, vol_lookback) for s in uni}
        book = ls_weights(
            scores, top_q=top_q, bottom_q=bottom_q, closes=closes, t=t,
            vol_lookback=vol_lookback, betas=betas, clusters=clusters,
            max_symbol_w=max_symbol_w, max_cluster_w=max_cluster_w, beta_cap=beta_cap,
        )
        signed = book["weights"]
        ei = t + 1
        t_next = rebal[ri + 1] if ri + 1 < len(rebal) else (n - 1)
        xi = min(t_next + 1, n - 1)

        legs, gross_pnl, cost = [], 0.0, 0.0
        for s, w in signed.items():
            if w == 0:
                continue
            entry_px = opens.iloc[ei][s]
            if math.isnan(entry_px):
                continue
            exit_px = opens.iloc[xi][s]
            if math.isnan(exit_px):
                path = closes[s].iloc[ei:xi + 1].to_numpy(float)
                path = path[~np.isnan(path)]
                if path.size == 0:
                    continue
                exit_px = float(path[-1])
            ret = float(exit_px) / float(entry_px) - 1.0
            leg_gross = w * ret            # w already carries sign (long +, short -)
            leg_cost = abs(w) * rt
            gross_pnl += leg_gross
            cost += leg_cost
            legs.append({
                "symbol": s, "side": "long" if w > 0 else "short",
                "weight": float(w), "entry_idx": int(ei), "entry_px": float(entry_px),
                "exit_idx": int(xi), "exit_px": float(exit_px), "gross_pnl": float(leg_gross),
            })
        if not legs:
            continue
        periods.append({
            "decision_idx": int(t), "entry_idx": int(ei), "exit_idx": int(xi),
            "n_legs": len(legs), "gross_pnl": float(gross_pnl), "cost": float(cost),
            "net_pnl": float(gross_pnl - cost), "net_beta": float(book["net_beta"]),
            "venue": venue, "label_status": "RESOLVED", "legs": legs,
        })
    return periods


def build_f3_spec(symbols=DEFAULT_UNIVERSE, venues=("binance",)):
    """Build the F3 market-neutral relative-value ``StrategySpec`` (declarative)."""
    from core.strategy_spec import StrategySpec

    return StrategySpec(
        id="F3",
        family="relative_value",
        market_type="perp_long_short",
        venues=list(venues),
        symbols=list(symbols),
        data_required=["ohlcv_1d", "volume", "funding_rate", "open_interest", "book_depth"],
        entry_rules={
            "type": "market_neutral_relative_value_long_short",
            "features": list(DEFAULT_FEATURE_WEIGHTS.keys()),
            "feature_weights": dict(DEFAULT_FEATURE_WEIGHTS),
            "features_gated_out": list(FORWARD_COLLECT_FEATURES),
            "top_q": 0.2, "bottom_q": 0.2, "timeframe": "1d",
        },
        exit_rules={"rebalance_every_bars": 1, "hold_to_next_rebalance": True},
        sizing={"weighting": "inverse_vol", "dollar_neutral": True, "gross_per_side": 0.5},
        risk_limits={
            "dollar_neutral": True,
            "beta_cap": DEFAULT_BETA_CAP,
            "beta_reference": BETA_REFERENCE,
            "max_symbol_weight": DEFAULT_MAX_SYMBOL_W,
            "max_cluster_weight": DEFAULT_MAX_CLUSTER_W,
            "min_rebalances": F3_MIN_REBALANCES,
            "live_feasibility": LIVE_FEASIBILITY,
        },
        validation_status="lab_sim",
        promotion_status="untested",
    )


def _profit_factor(arr: np.ndarray) -> float:
    gains = float(arr[arr > 0].sum())
    losses = float(-arr[arr < 0].sum())
    if losses <= 0:
        return float("inf") if gains > 0 else 0.0
    return gains / losses


def run_f3_slice(
    *,
    symbols=DEFAULT_UNIVERSE,
    venue: str = "binance",
    lookback: int = DEFAULT_LOOKBACK,
    rebalance_every: int = 1,
    top_q: float = 0.2,
    bottom_q: float = 0.2,
    n_splits: int = 4,
    registry_path=ACTIVE_STRATEGIES_PATH,
    cache: str = CACHE,
) -> dict:
    """End-to-end build-spec -> simulate -> walk-forward -> accept -> register (F3)."""
    closes, opens, vols = load_panel_v(symbols, cache)
    periods = simulate_f3(
        closes, opens, vols, lookback=lookback, rebalance_every=rebalance_every,
        top_q=top_q, bottom_q=bottom_q, venue=venue,
    )
    rets = [p["net_pnl"] for p in periods]
    arr = np.asarray(rets, dtype=float)

    fold_returns: list[list[float]] = []
    if arr.size >= n_splits + 1:
        for _train, test in WalkForward(n_splits=n_splits, embargo_bars=0).split(arr.size):
            fold_returns.append(arr[test].tolist())

    verdict = accept(
        returns=rets, strategy_class="swing", fold_returns=fold_returns,
        n_trials=1, trial_budget=1,
    )

    # ── F3 pipeline gate (recorded alongside accept()) ──
    rt_2x = round_trip_cost(venue) * F3_STRESS_COST_MULT
    net_2x = float(sum(p["gross_pnl"] - abs_cost for p, abs_cost in
                       ((p, sum(abs(leg["weight"]) for leg in p["legs"]) * rt_2x) for p in periods)))
    pnl_by_symbol: dict = {}
    pnl_by_day: dict = {}
    pnl_by_venue: dict = {}
    for p in periods:
        pnl_by_day[p["decision_idx"]] = p["net_pnl"]
        pnl_by_venue[p["venue"]] = pnl_by_venue.get(p["venue"], 0.0) + p["net_pnl"]
        for leg in p["legs"]:
            pnl_by_symbol[leg["symbol"]] = pnl_by_symbol.get(leg["symbol"], 0.0) + leg["gross_pnl"]
    conc_sym_ok, sym_share, _ = pnl_concentration_ok(pnl_by_symbol, max_pct=F3_MAX_CONCENTRATION_PCT)
    conc_day_ok, day_share, _ = pnl_concentration_ok(pnl_by_day, max_pct=F3_MAX_CONCENTRATION_PCT)
    conc_ven_ok, _vs, _vk = pnl_concentration_ok(pnl_by_venue, max_pct=100.0)  # single venue slice
    n_folds = len(fold_returns)
    n_pos_folds = sum(1 for f in fold_returns if f and float(np.mean(f)) > 0)
    f3_gate = {
        "rebalances_ok": bool(arr.size >= F3_MIN_REBALANCES),
        "n_rebalances": int(arr.size),
        "min_rebalances": F3_MIN_REBALANCES,
        "pf_ok": bool(_profit_factor(arr) >= F3_PF_FLOOR),
        "profit_factor": float(_profit_factor(arr)),
        "folds_majority_positive_ok": bool(n_folds >= 3 and n_pos_folds > n_folds / 2),
        "n_folds": n_folds, "n_pos_folds": n_pos_folds,
        "concentration_ok": bool(conc_sym_ok and conc_day_ok and conc_ven_ok),
        "worst_symbol_share_pct": float(sym_share),
        "worst_day_share_pct": float(day_share),
        "net_pnl_positive_2x_stress": bool(net_2x > 0),
        "net_2x_stress": net_2x,
    }

    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    oos_metrics = {
        "n_periods": int(arr.size),
        "win_rate": float((arr > 0).mean()) if arr.size else 0.0,
        "ev": float(arr.mean()) if arr.size else 0.0,
        "sum_net": float(arr.sum()) if arr.size else 0.0,
        "avg_win": float(wins.mean()) if wins.size else 0.0,
        "avg_loss": float(losses.mean()) if losses.size else 0.0,
        "max_abs_net_beta": float(max((abs(p["net_beta"]) for p in periods), default=0.0)),
        "accept": bool(verdict["accept"]),
        "live_feasibility": LIVE_FEASIBILITY,
    }

    promotion_status = "PROMOTED" if verdict["accept"] else "NO_EDGE"
    failure_reason = None
    if not verdict["accept"]:
        failed = [k for k, g in verdict["sub_gates"].items() if not g["pass"]]
        failure_reason = "failed sub-gates: " + ",".join(failed)

    spec = build_f3_spec(symbols=symbols, venues=(venue,))
    spec.promotion_status = promotion_status
    spec.register(registry_path=registry_path, new_info_source="F3_lab_slice")
    evidence = register_evidence(
        "F3",
        hypothesis=(
            "Multi-feature cross-sectional ranking (1/3/7/14d relative strength, "
            "EMA slope, volume trend, realized-vol penalty) long top-q / short "
            "bottom-q, inverse-vol dollar-neutral with beta + cluster caps, is "
            "market-neutral capital-preserving on liquid majors."
        ),
        oos_metrics=oos_metrics,
        honest_n=int(arr.size),
        promotion_status=promotion_status,
        failure_reason=failure_reason,
        cost_calibration_status="model_default",
        test_window={"bars": int(len(closes)), "timeframe": "1d", "n_folds": n_folds},
        universe_construction_method=UNIVERSE_METHOD,
        path=registry_path,
        new_info_source="F3_lab_slice",
    )

    return {
        "symbols": list(symbols),
        "venue": venue,
        "n_periods": int(arr.size),
        "periods": periods,
        "spec": spec.to_dict(),
        "verdict": verdict,
        "evidence": evidence,
        "oos_metrics": oos_metrics,
        "f3_gate": f3_gate,
        "live_feasibility": LIVE_FEASIBILITY,
    }


def main(argv: list[str] | None = None) -> int:
    rep = run_f3_slice()
    v, m, g = rep["verdict"], rep["oos_metrics"], rep["f3_gate"]
    print("=" * 68)
    print(f"F3 relative-value L/S — {','.join(rep['symbols'])} @ {rep['venue']}")
    print("=" * 68)
    print(f"rebalance periods : {rep['n_periods']}  (min {g['min_rebalances']})")
    print(f"WR={m['win_rate'] * 100:5.1f}%  EV={m['ev'] * 100:+.3f}%  "
          f"sum_net={m['sum_net'] * 100:+.1f}%  max|beta|={m['max_abs_net_beta']:.3f}")
    print(f"accept()          : {'GO' if v['accept'] else 'NO_GO'}")
    for name, sg in v["sub_gates"].items():
        print(f"  {'PASS' if sg['pass'] else 'fail'}  {name}")
    print("F3 pipeline gate:")
    for k in ("rebalances_ok", "pf_ok", "folds_majority_positive_ok",
              "concentration_ok", "net_pnl_positive_2x_stress"):
        print(f"  {k}: {g[k]}")
    print(f"evidence status   : {rep['evidence']['promotion_status']}")
    print(f"live feasibility  : {rep['live_feasibility']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
