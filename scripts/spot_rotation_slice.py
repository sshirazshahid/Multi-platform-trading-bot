"""Phase 6 — Spot S2 relative-strength rotation vertical slice (research/PAPER).

Long-only spot rotation gated by the Phase-5 regime:

    rank liquid spot symbols by z-scored 30/14/7d relative return + EMA50 slope
    + volume confirmation - realized-vol penalty - spread penalty -> hold the
    strongest 2-4 under the daily SMA200/ATR BTC+ETH regime (partly USDT when
    REDUCED, fully USDT when DEFENSIVE) -> rebalance on schedule with a
    cost-aware skip -> decide at closed bar t, fill at t+1 OPEN -> after-cost
    net return per period -> walk-forward OOS -> accept() verdict ->
    EvidenceRegistry record + StrategySpec id=SPOT_S2_ROTATION (research).

A correct NO_GO is success; the deliverable is pipeline honesty, not alpha.
No leverage, no shorting — weights >= 0 and sum <= 1 by construction. Reads
data/ohlcv_cache/<SYM>-USDT_1d.parquet only; never touches an exchange.

CLI:  venv/Scripts/python.exe scripts/spot_rotation_slice.py
"""
from __future__ import annotations

import math
import os
import sys

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.cost_model import round_trip_cost  # noqa: E402
from core.decision.promotion_loop import (  # noqa: E402
    ACTIVE_STRATEGIES_PATH,
    accept,
    register_evidence,
)
from core.spot_regime import (  # noqa: E402
    DEFAULT_BENEFIT_RATE,
    REGIME_DEFENSIVE,
    REGIME_NORMAL,
    REGIME_REDUCED,
    daily_ema200_regime_hysteresis,
    daily_sma200_atr_regime,
)
from core.walk_forward import WalkForward  # noqa: E402
from scripts.s2_basket_slice import point_in_time_universe  # noqa: E402

CACHE = "data/ohlcv_cache"
# Liquid spot majors with deep daily cache (point-in-time universe prunes gaps).
DEFAULT_UNIVERSE = ("BTC", "ETH", "SOL", "BNB", "XRP", "ADA",
                    "DOGE", "DOT", "AVAX", "LINK", "LTC", "ATOM")
RETURN_WINDOWS = (30, 14, 7)      # relative-return horizons (bars)
EMA_SLOPE_PERIOD = 50             # EMA50 slope confirmation
EMA_SLOPE_LOOKBACK = 5            # bars over which the EMA slope is measured
VOL_LOOKBACK = 20                 # realized-vol penalty window
VOLUME_LOOKBACK = 20              # volume-confirmation window
DEFAULT_SPREAD_BPS = 5.0          # conservative modeled spot spread penalty
DEFAULT_TOP_N = 3                 # hold strongest 2-4
MIN_TOP_N, MAX_TOP_N = 2, 4
DEFAULT_REBALANCE_EVERY = 7       # bars between scheduled rebalances
MIN_TURNOVER_FRAC = 0.05          # cost-aware skip floor
ADMISSION_RETURN_WINDOW = 28      # raw abs-return admission horizon
MIN_ADMITTED = 2                  # fewer admitted symbols -> 100% USDT
S2_MIN_DOLLAR_VOLUME_USD = 50_000_000.0
S2_DOLLAR_VOLUME_LOOKBACK = 30
S2_MAX_SPREAD_BPS = 20.0
S2_TREND_SMA_PERIOD = 50
# Regime -> total coin exposure (rest is USDT). Long-only by construction.
REGIME_EXPOSURE = {REGIME_NORMAL: 1.0, REGIME_REDUCED: 0.5, REGIME_DEFENSIVE: 0.0}
LOOKBACK = max(max(RETURN_WINDOWS), EMA_SLOPE_PERIOD + EMA_SLOPE_LOOKBACK, VOL_LOOKBACK)


# ── panel loading ──────────────────────────────────────────────────────
def load_panel(symbols=DEFAULT_UNIVERSE, cache: str = CACHE):
    """(closes, opens, highs, lows, volumes) aligned on a common daily ts index."""
    close_c, open_c, high_c, low_c, vol_c = {}, {}, {}, {}, {}
    for s in symbols:
        p = os.path.join(cache, f"{s}-USDT_1d.parquet")
        if not os.path.exists(p):
            continue
        df = pd.read_parquet(p).sort_values("ts").reset_index(drop=True)
        idx = pd.to_datetime(df["ts"], unit="s")
        close_c[s] = pd.Series(df["close"].to_numpy(float), index=idx)
        open_c[s] = pd.Series(df["open"].to_numpy(float), index=idx)
        high_c[s] = pd.Series(df["high"].to_numpy(float), index=idx)
        low_c[s] = pd.Series(df["low"].to_numpy(float), index=idx)
        vol_c[s] = pd.Series(df["volume"].to_numpy(float), index=idx)
    if not close_c:
        raise FileNotFoundError(f"no daily cache for any of {symbols} at {cache}")
    closes = pd.DataFrame(close_c).sort_index().reset_index(drop=True)
    opens = pd.DataFrame(open_c).sort_index().reset_index(drop=True)
    highs = pd.DataFrame(high_c).sort_index().reset_index(drop=True)
    lows = pd.DataFrame(low_c).sort_index().reset_index(drop=True)
    volumes = pd.DataFrame(vol_c).sort_index().reset_index(drop=True)
    return closes, opens, highs, lows, volumes


# ── ranking ────────────────────────────────────────────────────────────
def _zscore(values: np.ndarray) -> np.ndarray:
    sd = values.std(ddof=0)
    if sd <= 0 or not np.isfinite(sd):
        return np.zeros_like(values)
    return (values - values.mean()) / sd


def _ema(seg: np.ndarray, period: int) -> float:
    mult = 2.0 / (period + 1)
    ema = float(seg[:period].mean())
    for v in seg[period:]:
        ema = (v - ema) * mult + ema
    return ema


def rank_scores(
    closes: pd.DataFrame,
    volumes: pd.DataFrame,
    t: int,
    universe: list,
    spread_bps: dict | None = None,
) -> dict:
    """Deterministic composite score per symbol at closed bar ``t``.

    z(30/14/7d relative return) + z(EMA50 slope) + z(volume confirmation)
    - z(realized vol) - spread penalty. Returns an ordered dict
    strongest-first; ties break alphabetically by symbol.
    """
    uni = sorted(universe)
    feats: dict[str, list] = {s: [] for s in uni}
    # relative returns per horizon
    for w in RETURN_WINDOWS:
        rets = np.array([
            float(closes[s].iloc[t] / closes[s].iloc[t - w] - 1.0) for s in uni
        ])
        z = _zscore(rets)
        for i, s in enumerate(uni):
            feats[s].append(z[i])
    # EMA50 slope over the last EMA_SLOPE_LOOKBACK bars
    slopes = []
    for s in uni:
        seg = closes[s].iloc[: t + 1].to_numpy(float)
        now = _ema(seg, EMA_SLOPE_PERIOD)
        then = _ema(seg[:-EMA_SLOPE_LOOKBACK], EMA_SLOPE_PERIOD)
        slopes.append(now / then - 1.0)
    z = _zscore(np.array(slopes))
    for i, s in enumerate(uni):
        feats[s].append(z[i])
    # volume confirmation: last-bar volume vs its own trailing mean
    vconf = []
    for s in uni:
        seg = volumes[s].iloc[max(0, t - VOLUME_LOOKBACK): t + 1].to_numpy(float)
        mean = float(seg.mean()) if seg.size else 0.0
        vconf.append(float(seg[-1]) / mean - 1.0 if mean > 0 else 0.0)
    z = _zscore(np.array(vconf))
    for i, s in enumerate(uni):
        feats[s].append(z[i])
    # realized-vol penalty
    vols = []
    for s in uni:
        seg = closes[s].iloc[max(0, t - VOL_LOOKBACK): t + 1].to_numpy(float)
        rets = np.diff(seg) / seg[:-1]
        vols.append(float(np.std(rets, ddof=0)) if rets.size >= 2 else 0.0)
    z = _zscore(np.array(vols))
    for i, s in enumerate(uni):
        feats[s].append(-z[i])
    # spread penalty (bps -> return units, scaled onto the z landscape)
    for s in uni:
        bps = float((spread_bps or {}).get(s, DEFAULT_SPREAD_BPS))
        feats[s].append(-bps / 100.0)

    scores = {s: float(sum(feats[s])) for s in uni}
    # strongest-first; deterministic alphabetical tie-break
    ordered = sorted(scores.items(), key=lambda kv: (-kv[1], kv[0]))
    return dict(ordered)


# ── absolute-return admission gate ─────────────────────────────────────
def admit_universe(
    closes: pd.DataFrame,
    t: int,
    universe: list,
    *,
    window: int = ADMISSION_RETURN_WINDOW,
    volumes: pd.DataFrame | None = None,
    spread_bps: dict | None = None,
    min_dollar_volume_usd: float = S2_MIN_DOLLAR_VOLUME_USD,
    dollar_volume_lookback: int = S2_DOLLAR_VOLUME_LOOKBACK,
    max_spread_bps: float = S2_MAX_SPREAD_BPS,
    trend_sma_period: int = S2_TREND_SMA_PERIOD,
) -> list:
    """Admission gate for S2 rotation.

    A symbol must have positive raw return over ``window``, median trailing
    dollar volume >= ``min_dollar_volume_usd``, spread <= ``max_spread_bps``,
    and close above SMA50. When ``volumes`` is omitted, legacy callers get the
    old return-only behavior; replay passes volumes and therefore binds all
    gates.
    """
    if t - window < 0 or t - trend_sma_period + 1 < 0:
        return []
    admitted = []
    for s in sorted(universe):
        now = float(closes[s].iloc[t])
        past = float(closes[s].iloc[t - window])
        if math.isnan(now) or math.isnan(past):
            continue
        if now / past - 1.0 <= 0.0:
            continue
        if volumes is not None:
            v = float(volumes[s].iloc[t])
            if math.isnan(v):
                continue
            lo = max(0, t - int(dollar_volume_lookback) + 1)
            dollar = closes[s].iloc[lo:t + 1] * volumes[s].iloc[lo:t + 1]
            median_dollar = float(dollar.median(skipna=True))
            if math.isnan(median_dollar) or median_dollar < float(min_dollar_volume_usd):
                continue
        bps = float((spread_bps or {}).get(s, DEFAULT_SPREAD_BPS))
        if math.isnan(bps) or bps > float(max_spread_bps):
            continue
        sma50 = float(closes[s].iloc[t - trend_sma_period + 1:t + 1].mean())
        if math.isnan(sma50) or now <= sma50:
            continue
        admitted.append(s)
    return admitted


# ── regime-gated selection ─────────────────────────────────────────────
def select_targets(scores: dict, regime: str, *, top_n: int = DEFAULT_TOP_N) -> dict:
    """Target weights (incl. USDT) from ranked scores under the regime gate.

    DEFENSIVE -> fully USDT. REDUCED -> half exposure. NORMAL -> full exposure
    equally split across the strongest ``top_n`` (clamped 2-4). Long-only.
    """
    if regime not in REGIME_EXPOSURE:
        raise ValueError(f"unknown regime {regime!r}")
    exposure = REGIME_EXPOSURE[regime]
    if exposure <= 0.0 or not scores:
        return {"USDT": 1.0}
    n = min(max(int(top_n), MIN_TOP_N), MAX_TOP_N, len(scores))
    held = list(scores.keys())[:n]  # rank_scores is ordered strongest-first
    w = exposure / n
    targets = {s: w for s in held}
    usdt = 1.0 - exposure
    if usdt > 1e-12:
        targets["USDT"] = usdt
    return targets


# ── cost-aware rebalance skip ──────────────────────────────────────────
def should_rebalance(
    current_weights: dict,
    target_weights: dict,
    *,
    portfolio_value: float,
    venue: str = "binance",
    benefit_rate: float = DEFAULT_BENEFIT_RATE,
    min_turnover_frac: float = MIN_TURNOVER_FRAC,
) -> dict:
    """Skip a scheduled rebalance when it is not worth its costs.

    Acts only when one-way turnover >= ``min_turnover_frac`` AND the expected
    benefit (``benefit_rate`` x turnover notional) exceeds the per-side spot
    fee+slippage cost from ``core.cost_model``. Advisory — never places orders.
    """
    coins = {c for c in list(current_weights) + list(target_weights) if c != "USDT"}
    deltas = {
        c: float(target_weights.get(c, 0.0)) - float(current_weights.get(c, 0.0))
        for c in coins
    }
    turnover_frac = sum(abs(d) for d in deltas.values()) / 2.0
    turnover = turnover_frac * portfolio_value
    per_side_cost = round_trip_cost(venue, market_type="spot") / 2.0
    est_cost = 2.0 * turnover * per_side_cost  # sells + buys
    est_benefit = turnover * benefit_rate
    base = {"turnover_frac": turnover_frac, "est_cost": est_cost,
            "est_benefit": est_benefit, "deltas": deltas}
    if turnover_frac < min_turnover_frac:
        return {**base, "action": "SKIP",
                "reason": f"turnover {turnover_frac:.1%} < {min_turnover_frac:.0%}"}
    if est_benefit <= est_cost:
        return {**base, "action": "SKIP",
                "reason": f"benefit {est_benefit:.4f} <= cost {est_cost:.4f}"}
    return {**base, "action": "REBALANCE",
            "reason": f"turnover {turnover_frac:.1%}, benefit > cost"}


# ── backtest ───────────────────────────────────────────────────────────
def compute_regimes(
    closes: pd.DataFrame,
    highs: pd.DataFrame | None = None,
    lows: pd.DataFrame | None = None,
    *,
    ema_period: int = 200,
    regime_mode: str = "sma_atr_3close",
) -> list:
    """Per-bar regime from closed daily closes (DEFENSIVE until enough history).

    Default path uses Rev5.2 SMA200 +/- ATR14 with 3-close confirmation when
    highs/lows are supplied. ``legacy_ema_hysteresis`` retains the Rev5.1 path.
    """
    n = len(closes)
    out = []
    btc = closes["BTC"].to_numpy(float) if "BTC" in closes else None
    eth = closes["ETH"].to_numpy(float) if "ETH" in closes else None
    prev_states = None
    for t in range(n):
        if btc is None or eth is None or t + 1 < ema_period:
            out.append(REGIME_DEFENSIVE)
            prev_states = None
            continue
        b, e = btc[: t + 1], eth[: t + 1]
        if np.isnan(b).any() or np.isnan(e).any():
            out.append(REGIME_DEFENSIVE)
            prev_states = None
            continue
        if regime_mode == "sma_atr_3close" and highs is not None and lows is not None:
            try:
                btc_rows = [
                    {"ts": i, "high": float(highs["BTC"].iloc[i]),
                     "low": float(lows["BTC"].iloc[i]), "close": float(closes["BTC"].iloc[i])}
                    for i in range(t + 1)
                ]
                eth_rows = [
                    {"ts": i, "high": float(highs["ETH"].iloc[i]),
                     "low": float(lows["ETH"].iloc[i]), "close": float(closes["ETH"].iloc[i])}
                    for i in range(t + 1)
                ]
                if any(
                    not np.isfinite(x["high"]) or not np.isfinite(x["low"])
                    for x in btc_rows + eth_rows
                ):
                    raise ValueError("non-finite high/low")
                regime, prev_states = daily_sma200_atr_regime(
                    btc_rows, eth_rows, prev_states=prev_states)
            except ValueError:
                regime, prev_states = REGIME_DEFENSIVE, None
        else:
            regime, prev_states = daily_ema200_regime_hysteresis(
                list(b), list(e), ema_period=ema_period, prev_states=prev_states)
        out.append(regime)
    return out


def simulate_rotation(
    closes: pd.DataFrame,
    opens: pd.DataFrame,
    volumes: pd.DataFrame,
    regimes: list,
    *,
    top_n: int = DEFAULT_TOP_N,
    rebalance_every: int = DEFAULT_REBALANCE_EVERY,
    venue: str = "binance",
    spread_bps: dict | None = None,
    admission_window: int | None = ADMISSION_RETURN_WINDOW,
) -> list[dict]:
    """Replay the regime-gated rotation; one RESOLVED record per period.

    Rank at closed bar ``t`` (regime known at ``t``), fill at ``t+1`` OPEN,
    hold to the next scheduled bar's t+1 open. A scheduled rebalance is
    SKIPPED (holdings carried, no cost) when ``should_rebalance`` says so.
    When ``admission_window`` is set (default), only symbols with a positive
    raw absolute return over that window are ranked; fewer than
    ``MIN_ADMITTED`` admitted -> 100% USDT. ``admission_window=None``
    reproduces the exact legacy behavior. Deterministic; long-only.
    """
    n = len(closes)
    per_side = round_trip_cost(venue, market_type="spot") / 2.0
    start = LOOKBACK + 1
    rebal = list(range(start, n - 1, max(1, int(rebalance_every))))
    held: dict[str, float] = {}
    periods: list[dict] = []

    for ri, t in enumerate(rebal):
        uni = point_in_time_universe(closes, t, lookback=LOOKBACK)
        regime = regimes[t]
        admitted = None
        if admission_window is None:
            ranked = uni
        else:
            admitted = admit_universe(
                closes, t, uni, window=admission_window,
                volumes=volumes, spread_bps=spread_bps)
            ranked = admitted if len(admitted) >= MIN_ADMITTED else []
        scores = rank_scores(closes, volumes, t, ranked, spread_bps) if ranked else {}
        targets = select_targets(scores, regime, top_n=top_n)
        coin_targets = {c: w for c, w in targets.items() if c != "USDT"}
        decision = should_rebalance(held, targets, portfolio_value=1.0, venue=venue)
        cost = 0.0
        if decision["action"] == "REBALANCE":
            cost = decision["est_cost"]
            held = coin_targets
        weights = dict(held)

        ei = t + 1
        t_next = rebal[ri + 1] if ri + 1 < len(rebal) else (n - 1)
        xi = min(t_next + 1, n - 1)
        gross = 0.0
        legs = []
        for s, w in sorted(weights.items()):
            entry_px = float(opens.iloc[ei][s])
            exit_px = float(opens.iloc[xi][s])
            if math.isnan(exit_px):  # delisted mid-hold: terminal price path
                path = closes[s].iloc[ei: xi + 1].to_numpy(float)
                path = path[~np.isnan(path)]
                if path.size == 0 or math.isnan(entry_px):
                    continue
                exit_px = float(path[-1])
            if math.isnan(entry_px):
                continue
            r = exit_px / entry_px - 1.0
            gross += w * r
            legs.append({"symbol": s, "weight": float(w), "entry_idx": int(ei),
                         "entry_px": entry_px, "exit_idx": int(xi),
                         "exit_px": exit_px, "gross_pnl": float(r)})
        period = {
            "decision_idx": int(t), "entry_idx": int(ei), "exit_idx": int(xi),
            "regime": regime, "action": decision["action"],
            "weights": {k: float(v) for k, v in weights.items()},
            "n_legs": len(legs), "legs": legs,
            "gross_pnl": float(gross), "cost": float(cost),
            "net_pnl": float(gross - cost),
            "fees_slippage": float(per_side * 2.0),
            "label_status": "RESOLVED",
        }
        if admitted is not None:
            period["n_admitted"] = len(admitted)
            period["n_universe"] = len(uni)
        periods.append(period)
    return periods


# ── StrategySpec ───────────────────────────────────────────────────────
def build_spot_s2_spec():
    """Declarative research-status spec (never enters the live scorer)."""
    from core.strategy_spec import StrategySpec

    return StrategySpec(
        id="SPOT_S2_ROTATION",
        family="spot_rotation",
        market_type="spot",
        venues=["binance"],
        symbols=[f"{c}/USDT" for c in DEFAULT_UNIVERSE],
        data_required=["ohlcv_1d", "volume", "fees"],
        entry_rules={
            "type": "relative_strength_rotation",
            "rank": "z(ret30/14/7)+z(ema50_slope)+z(volume_confirm)-z(realized_vol)-spread",
            "hold_top_n": [MIN_TOP_N, MAX_TOP_N],
            "regime_gate": "daily_sma200_atr14_3close_btc_eth",
            "admission": (
                "raw 28d abs return > 0; median 30d dollar volume >= $50M; "
                "spread <= 20 bps; close > SMA50; <2 admitted -> 100% USDT"
            ),
        },
        exit_rules={
            "scheduled_rebalance_bars": DEFAULT_REBALANCE_EVERY,
            "cost_aware_skip": True,
        },
        sizing={"long_only": True, "equal_weight": True, "weights_sum_max": 1.0},
        risk_limits={"max_leverage": 1.0, "shorting": False},
        validation_status="backtested",
        promotion_status="research",
    )


# ── end-to-end slice ───────────────────────────────────────────────────
def run_spot_rotation_slice(
    *,
    symbols=DEFAULT_UNIVERSE,
    venue: str = "binance",
    top_n: int = DEFAULT_TOP_N,
    rebalance_every: int = DEFAULT_REBALANCE_EVERY,
    n_splits: int = 4,
    registry_path=ACTIVE_STRATEGIES_PATH,
    spec_dir=None,
    cache: str = CACHE,
) -> dict:
    """Load -> simulate -> walk-forward -> accept() -> register evidence + spec."""
    from core.strategy_spec import SPEC_DIR, save_spec

    closes, opens, highs, lows, volumes = load_panel(symbols, cache)
    regimes = compute_regimes(closes, highs, lows)
    periods = simulate_rotation(
        closes, opens, volumes, regimes,
        top_n=top_n, rebalance_every=rebalance_every, venue=venue,
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

    oos_metrics = {
        "n_periods": int(arr.size),
        "win_rate": float((arr > 0).mean()) if arr.size else 0.0,
        "ev": float(arr.mean()) if arr.size else 0.0,
        "sum_net": float(arr.sum()) if arr.size else 0.0,
        "n_rebalances": sum(1 for p in periods if p["action"] == "REBALANCE"),
        "n_skips": sum(1 for p in periods if p["action"] == "SKIP"),
        "accept": bool(verdict["accept"]),
    }
    failure_reason = None
    if not verdict["accept"]:
        failed = [k for k, g in verdict["sub_gates"].items() if not g["pass"]]
        failure_reason = "failed sub-gates: " + ",".join(failed)

    spec = build_spot_s2_spec()
    spec_path = save_spec(spec, directory=spec_dir or SPEC_DIR)

    evidence = register_evidence(
        spec.id,
        hypothesis=(
            "Regime-gated long-only relative-strength rotation (hold strongest "
            "2-4 spot majors only in NORMAL regime, USDT otherwise, cost-aware "
            "rebalance) preserves capital vs holding through drawdowns."
        ),
        rules=spec.entry_rules | {"exit": spec.exit_rules, "sizing": spec.sizing},
        data_sources=[f"ohlcv_cache:{s}-USDT_1d" for s in symbols] + [f"venue:{venue}"],
        data_depth=int(len(closes)),
        cost_model={"round_trip_cost": round_trip_cost(venue, market_type="spot"),
                    "venue": venue, "market_type": "spot"},
        cost_calibration_status="model_default",
        test_window={"bars": int(len(closes)), "timeframe": "1d",
                     "n_folds": len(fold_returns)},
        oos_metrics=oos_metrics,
        honest_n=int(arr.size),
        promotion_status="research",
        failure_reason=failure_reason,
        universe_construction_method="point_in_time_with_delisted_terminal_paths",
        path=registry_path,
    )

    return {
        "symbols": list(symbols), "venue": venue,
        "n_periods": int(arr.size), "periods": periods,
        "verdict": verdict, "evidence": evidence,
        "oos_metrics": oos_metrics, "spec_path": str(spec_path),
    }


def main(argv: list[str] | None = None) -> int:
    rep = run_spot_rotation_slice()
    v = rep["verdict"]
    m = rep["oos_metrics"]
    print("=" * 68)
    print(f"SPOT_S2_ROTATION — {','.join(rep['symbols'])} @ {rep['venue']}")
    print("=" * 68)
    print(f"periods={m['n_periods']}  rebalances={m['n_rebalances']}  skips={m['n_skips']}")
    print(f"WR={m['win_rate'] * 100:5.1f}%  EV={m['ev'] * 100:+.3f}%  "
          f"sum_net={m['sum_net'] * 100:+.1f}%")
    print(f"accept()          : {'GO' if v['accept'] else 'NO_GO'}")
    for name, g in v["sub_gates"].items():
        print(f"  {'PASS' if g['pass'] else 'fail'}  {name}")
    print(f"evidence status   : {rep['evidence']['promotion_status']}")
    print(f"spec              : {rep['spec_path']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
