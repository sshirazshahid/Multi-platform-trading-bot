"""15c — Stablecoin depeg micro-reversion screen (scout B 2026-07-16 candidate 1).

Pre-registered in _workspace/strategy_pipeline/15c_screen_depeg.md (frozen
2026-07-16 BEFORE this ran). Read that file first; constants here implement it.

Frozen design (summary — the .md is authoritative):
  * Universe: Binance spot USDC/USDT + FDUSD/USDT, pooled (one strategy).
  * Sample: 2024-07-16 -> 2026-07-15 UTC, 1m klines from data.binance.vision.
  * Entry: first ARMED 1m close with dev >= theta bps below 1.0 -> buy next bar
    OPEN. Exits (fill next bar open): REPEG close>=0.9998 | STOP close<=fill*0.92
    (10 bps stop slip) | TIMEOUT 48h. Re-arm only after close>=0.9998.
  * Variants: theta in {10, 20, 30} bps -> n_trials=3. Nothing else searched.
  * Binding costs: fee 0 bps/side (LIVE-PULLED 2026-07-16, zero-fee promo real,
    both pairs) + repo slippage 5 bps/side (10 bps stop side). Sensitivities
    (non-binding): std 10 bps taker; 1 bp slip.
  * MC/tail sizing: FULL NOTIONAL per event, unlevered (binding tail test).
  * Gates: n>=30, mean>0, WR>=0.55, OOS-WR>=0.55, DSR>=0.10 (n_trials=3),
    PBO<=0.5 (weekly matrix across the 3 variants), AUC>=0.60 of frozen score
    tanh(dev_trigger/(2*theta)), MC P>0>=0.95 & maxDD p95<=0.25. NaN fails closed.

Usage (venv/Scripts/python.exe):
  research/screen_stablecoin_depeg.py --harvest    # download 1m klines -> parquet
  research/screen_stablecoin_depeg.py --selftest   # synthetic-bar engine checks
  research/screen_stablecoin_depeg.py              # run screen, write JSON verdict
"""
from __future__ import annotations

import io
import json
import math
import os
import sys
import zipfile
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
import requests

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config  # noqa: E402  (FEE / SLIPPAGE are authoritative)
from research.screen_listing_short import (  # noqa: E402  (audited helpers, reused)
    MAX_PBO,
    MC_MIN_TRADES,
    MIN_DSR,
    MIN_OOS_WR,
    _dsr_prob,
    _monte_carlo,
    _oos_wr_walk_forward,
)
from research.screen_preunlock_short import max_drawdown, rank_auc  # noqa: E402

# ── Frozen constants (15c pre-registration) ──────────────────────────────────
PAIRS = ("USDC", "FDUSD")  # -USDT Binance spot
# MOVED (2026-07-18): future --harvest runs write research parquets to
# data/research_cache/ so research artifacts never mingle with the live bot's
# data/ohlcv_cache/. The two parquets the CLOSED 2026-07-16 screen used remain
# in data/ohlcv_cache/ (left in place so the closed verdict still reproduces);
# load_pair() checks research_cache first, then falls back to ohlcv_cache.
OHLCV_DIR = "data/ohlcv_cache"       # legacy location (closed-screen parquets)
RESEARCH_CACHE = "data/research_cache"  # harvest output from 2026-07-18 onward
OUT_JSON = "_workspace/strategy_pipeline/15c_screen_depeg.json"
WIN_LO = int(datetime(2024, 7, 16, tzinfo=timezone.utc).timestamp())
WIN_HI = int(datetime(2026, 7, 16, tzinfo=timezone.utc).timestamp())
THETAS_BPS = (10.0, 20.0, 30.0)
N_TRIALS = 3
MIN_N = 30
REPEG_EXIT = 0.9998
STOP_FRAC = 0.92          # charter 8% stop-loss guardian
TIMEOUT_SEC = 48 * 3600
FILL_GAP_MAX = 3600       # no fill bar within 60 min -> event EXCLUDED
MIN_AUC = 0.60
# Binding cost model (see pre-registration: live-pulled zero fee + repo slippage)
FEE_SIDE_BINDING = 0.0        # sapi/v1/asset/tradeFee pull 2026-07-16: 0.0 both pairs
SLIP_SIDE_BINDING = config.SLIPPAGE["pct_open"]       # repo convention 5 bps/side
STOP_SLIP_BINDING = config.SLIPPAGE["pct_stop_loss"]  # repo convention 10 bps on stops
# Non-binding sensitivities (reported only)
FEE_SIDE_STD = config.FEE["spot_taker"]  # standard taker if the promo ends
SLIP_SIDE_1BP = 0.0001


# ── Pure functions (selftested) ──────────────────────────────────────────────
def net_return(entry_open: float, exit_open: float, fee_side: float,
               slip_entry: float, slip_exit: float) -> float:
    """After-cost net return of buy-at-entry_open, sell-at-exit_open."""
    cost = entry_open * (1.0 + fee_side + slip_entry)
    proceeds = exit_open * (1.0 - fee_side - slip_exit)
    return proceeds / cost - 1.0


def frozen_score(dev_trigger_bps: float, theta_bps: float) -> float:
    """Frozen pre-outcome discriminating score (15c pre-registration)."""
    return float(math.tanh(dev_trigger_bps / (2.0 * theta_bps)))


def extract_events(ts: np.ndarray, opens: np.ndarray, closes: np.ndarray,
                   theta_bps: float) -> tuple[list[dict], dict]:
    """Run the frozen 15c state machine over one pair's 1m bars.

    Returns (events, exclusions). Events carry RAW entry/exit opens so any cost
    model can be applied afterwards; triggers are cost-independent.
    """
    trigger_px = 1.0 - theta_bps / 1e4
    events: list[dict] = []
    excl = {"entry_fill_gap": 0, "exit_fill_gap": 0, "unresolved_at_end": 0}
    n = len(ts)
    armed = True
    pos: dict | None = None
    for i in range(n):
        c = float(closes[i])
        if pos is not None:
            reason = None
            if c >= REPEG_EXIT:
                reason = "REPEG"
            elif c <= pos["fill_px"] * STOP_FRAC:
                reason = "STOP"
            elif int(ts[i]) >= pos["fill_ts"] + TIMEOUT_SEC:
                reason = "TIMEOUT"
            if reason is None:
                continue
            j = i + 1
            if j >= n:
                excl["unresolved_at_end"] += 1
                pos, armed = None, False
                continue
            if int(ts[j]) - int(ts[i]) > FILL_GAP_MAX:
                excl["exit_fill_gap"] += 1
                pos, armed = None, False
                continue
            events.append({
                "entry_trigger_ts": pos["trigger_ts"],
                "entry_fill_ts": pos["fill_ts"],
                "entry_open": pos["fill_px"],
                "dev_trigger_bps": pos["dev_bps"],
                "exit_reason": reason,
                "exit_trigger_ts": int(ts[i]),
                "exit_fill_ts": int(ts[j]),
                "exit_open": float(opens[j]),
                "hold_min": (int(ts[j]) - pos["fill_ts"]) / 60.0,
                "score": frozen_score(pos["dev_bps"], theta_bps),
            })
            pos, armed = None, False
            continue
        if not armed:
            armed = c >= REPEG_EXIT
            continue
        if c <= trigger_px:
            j = i + 1
            if j >= n:
                excl["unresolved_at_end"] += 1
                armed = False
                continue
            if int(ts[j]) - int(ts[i]) > FILL_GAP_MAX:
                excl["entry_fill_gap"] += 1
                armed = False  # episode consumed; re-arm on repeg
                continue
            pos = {"trigger_ts": int(ts[i]), "fill_ts": int(ts[j]),
                   "fill_px": float(opens[j]),
                   "dev_bps": (1.0 - c) * 1e4}
            armed = False
    if pos is not None:
        excl["unresolved_at_end"] += 1
    return events, excl


def apply_costs(ev: dict, fee_side: float, slip_side: float, stop_slip: float) -> float:
    slip_exit = stop_slip if ev["exit_reason"] == "STOP" else slip_side
    return net_return(ev["entry_open"], ev["exit_open"], fee_side, slip_side, slip_exit)


def _sensitivity(events: list[dict], key: str) -> dict:
    """Non-binding cost-sensitivity summary over pre-computed per-event returns."""
    vals = np.array([e[key] for e in events], dtype=float)
    return {"mean_net": float(np.mean(vals)), "win_rate": float(np.mean(vals > 0))}


def independent_episode_count(entry_ts_sorted: list[int], link_sec: int = 24 * 3600) -> int:
    """Cluster events (both pairs pooled) whose entries chain within link_sec."""
    if not entry_ts_sorted:
        return 0
    clusters = 1
    for a, b in zip(entry_ts_sorted, entry_ts_sorted[1:]):
        if b - a > link_sec:
            clusters += 1
    return clusters


def overlap_count(events_a: list[dict], events_b: list[dict]) -> int:
    """Events in A whose [entry_fill, exit_fill] intersects any event of B."""
    cnt = 0
    for ea in events_a:
        for eb in events_b:
            if ea["entry_fill_ts"] <= eb["exit_fill_ts"] and eb["entry_fill_ts"] <= ea["exit_fill_ts"]:
                cnt += 1
                break
    return cnt


# ── Harvest (keyless public bucket; no synthesis, gaps recorded) ─────────────
BASE_URL = "https://data.binance.vision/data/spot"


def _parse_kline_csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None, usecols=range(6),
                     names=["ot", "open", "high", "low", "close", "volume"])
    df = df[pd.to_numeric(df["ot"], errors="coerce").notna()].copy()
    for col in df.columns:
        df[col] = pd.to_numeric(df[col])
    ot = df["ot"].astype("int64")
    # unit auto-detect: us / ms / s by magnitude
    if (ot > 10**15).any():
        ot = ot // 1_000_000
    elif (ot > 10**12).any():
        ot = ot // 1_000
    df["ts"] = ot.astype("int64")
    return df[["ts", "open", "high", "low", "close", "volume"]]


def harvest() -> None:
    os.makedirs(RESEARCH_CACHE, exist_ok=True)
    months = pd.period_range("2024-07", "2026-06", freq="M")
    days = [date(2026, 7, 1) + timedelta(days=k) for k in range(15)]
    for base in PAIRS:
        sym = f"{base}USDT"
        frames, missing = [], []
        for m in months:
            url = f"{BASE_URL}/monthly/klines/{sym}/1m/{sym}-1m-{m}.zip"
            _fetch_into(url, f"{m}", frames, missing)
        for d in days:
            url = f"{BASE_URL}/daily/klines/{sym}/1m/{sym}-1m-{d.isoformat()}.zip"
            _fetch_into(url, d.isoformat(), frames, missing)
        _write_missing_windows(base, missing)
        if not frames:
            print(f"{sym}: NOTHING harvested; missing={missing}")
            continue
        df = pd.concat(frames).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
        out = os.path.join(RESEARCH_CACHE, f"{base}-USDT_1m.parquet")
        df.to_parquet(out, index=False)
        lo = datetime.fromtimestamp(int(df.ts.iloc[0]), tz=timezone.utc)
        hi = datetime.fromtimestamp(int(df.ts.iloc[-1]), tz=timezone.utc)
        print(f"{sym}: {len(df)} bars {lo:%Y-%m-%d %H:%M} -> {hi:%Y-%m-%d %H:%M} "
              f"-> {out}; missing_files={missing or 'none'}")


def _write_missing_windows(base: str, missing: list) -> None:
    """Persist permanently-failed harvest windows next to the parquet (loud
    artifact — never a silent list that dies with the process; 2026-07-18)."""
    path = os.path.join(RESEARCH_CACHE, f"{base}-USDT_1m_missing_windows.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"pair": f"{base}/USDT", "missing_windows": missing}, fh, indent=1)
    if missing:
        print(f"WARNING {base}USDT: {len(missing)} harvest windows permanently "
              f"failed -> recorded in {path}")


def _fetch_into(url: str, tag: str, frames: list, missing: list) -> None:
    """Fetch one kline zip into ``frames``; mutates ``frames``/``missing`` in place."""
    for attempt in range(3):
        try:
            r = requests.get(url, timeout=60)
            if r.status_code == 404:
                missing.append(tag)
                return
            r.raise_for_status()
            with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                raw = z.read(z.namelist()[0])
            frames.append(_parse_kline_csv(raw))
            return
        except Exception as e:  # noqa: BLE001
            if attempt == 2:
                print(f"WARNING: {url} permanently failed after 3 attempts "
                      f"({type(e).__name__}) — recorded in missing_windows")
                missing.append(f"{tag} (ERROR {type(e).__name__})")


# ── Screen orchestration ─────────────────────────────────────────────────────
def load_pair(base: str) -> pd.DataFrame | None:
    # research_cache first (post-2026-07-18 harvests), then the legacy
    # ohlcv_cache location where the closed 2026-07-16 screen's parquets live.
    for d in (RESEARCH_CACHE, OHLCV_DIR):
        p = os.path.join(d, f"{base}-USDT_1m.parquet")
        if os.path.exists(p):
            break
    else:
        return None
    df = pd.read_parquet(p, columns=["ts", "open", "close"])
    df = df[(df.ts >= WIN_LO) & (df.ts < WIN_HI)].reset_index(drop=True)
    return df


def run_screen() -> dict:
    data = {b: load_pair(b) for b in PAIRS}
    missing = [b for b, df in data.items() if df is None or len(df) == 0]
    if missing:
        return {
            "candidate": "15c stablecoin depeg micro-reversion",
            "verdict": "INSUFFICIENT_DATA",
            "reason": f"1m parquet missing/empty for {missing}. Harvest with: "
                      f"venv/Scripts/python.exe research/screen_stablecoin_depeg.py --harvest",
        }

    coverage = {}
    for b, df in data.items():
        span_days = (int(df.ts.iloc[-1]) - int(df.ts.iloc[0])) / 86400.0
        coverage[b] = {
            "bars": int(len(df)),
            "first": datetime.fromtimestamp(int(df.ts.iloc[0]), tz=timezone.utc).isoformat(),
            "last": datetime.fromtimestamp(int(df.ts.iloc[-1]), tz=timezone.utc).isoformat(),
            "bar_completeness": round(len(df) / (span_days * 1440.0), 4),
            "min_close": float(df["close"].min()),
        }

    variants: dict[str, dict] = {}
    weekly_cols: dict[float, pd.Series] = {}
    for theta in THETAS_BPS:
        per_pair_events: dict[str, list[dict]] = {}
        excl_total = {"entry_fill_gap": 0, "exit_fill_gap": 0, "unresolved_at_end": 0}
        for b, df in data.items():
            ev, ex = extract_events(df.ts.values, df.open.values, df.close.values, theta)
            for e in ev:
                e["pair"] = b
            per_pair_events[b] = ev
            for k in excl_total:
                excl_total[k] += ex[k]
        pooled = sorted((e for evs in per_pair_events.values() for e in evs),
                        key=lambda e: e["entry_fill_ts"])
        for e in pooled:
            e["net_binding"] = apply_costs(e, FEE_SIDE_BINDING, SLIP_SIDE_BINDING, STOP_SLIP_BINDING)
            e["net_stdfee"] = apply_costs(e, FEE_SIDE_STD, SLIP_SIDE_BINDING, STOP_SLIP_BINDING)
            e["net_1bpslip"] = apply_costs(e, FEE_SIDE_BINDING, SLIP_SIDE_1BP, SLIP_SIDE_1BP * 2)
        rets = np.array([e["net_binding"] for e in pooled], dtype=float)
        n = int(rets.size)
        entry = {
            "theta_bps": theta,
            "n_events": n,
            "n_by_pair": {b: len(per_pair_events[b]) for b in PAIRS},
            "exclusions": excl_total,
            "exit_reasons": {r: sum(1 for e in pooled if e["exit_reason"] == r)
                             for r in ("REPEG", "STOP", "TIMEOUT")},
            "cross_pair_overlap_events": overlap_count(per_pair_events[PAIRS[0]],
                                                       per_pair_events[PAIRS[1]]),
            "independent_market_episodes_24h": independent_episode_count(
                [e["entry_fill_ts"] for e in pooled]),
        }
        if n:
            eq = np.cumprod(1.0 + rets)
            entry.update({
                "mean_net": float(np.mean(rets)),
                "median_net": float(np.median(rets)),
                "win_rate": float(np.mean(rets > 0)),
                "worst_event": float(np.min(rets)),
                "best_event": float(np.max(rets)),
                "total_compounded": float(eq[-1] - 1.0),
                "realized_maxdd_descriptive": max_drawdown(eq),
                "median_hold_min": float(np.median([e["hold_min"] for e in pooled])),
                "dsr_prob": _dsr_prob(rets, n_trials=N_TRIALS),
                "oos_wr_walk_forward": _oos_wr_walk_forward(
                    rets, [(e["entry_fill_ts"], e["exit_fill_ts"]) for e in pooled]),
                "auc_frozen_score": rank_auc([e["score"] for e in pooled], rets > 0),
                "monte_carlo": _monte_carlo(rets),
                "sensitivity_nonbinding": {
                    "std_fee_10bps": _sensitivity(pooled, "net_stdfee"),
                    "slip_1bp_zero_fee": _sensitivity(pooled, "net_1bpslip"),
                },
            })
            wk = pd.Series(
                rets,
                index=pd.to_datetime([e["entry_fill_ts"] for e in pooled], unit="s", utc=True),
            ).resample("W").sum()
            weekly_cols[theta] = wk
        variants[f"theta_{int(theta)}bps"] = entry

    pbo_value = _pbo_weekly(weekly_cols)
    verdict, reason, detail = _decide(variants, pbo_value)
    return {
        "candidate": "15c stablecoin depeg micro-reversion (USDC/USDT, FDUSD/USDT Binance spot)",
        "hypothesis": "long the discounted stable leg beyond a fixed deviation threshold; "
                      "exit at re-peg / -8% stop / 48h timeout; after-cost positive at retail",
        "prereg": "_workspace/strategy_pipeline/15c_screen_depeg.md (frozen before run)",
        "sample": {"utc": ["2024-07-16", "2026-07-16"], "grid": "1m closes, next-bar-open fills"},
        "coverage": coverage,
        "fee_truth": {
            "source": "authenticated sapi/v1/asset/tradeFee via ccxt, pulled 2026-07-16",
            "USDC/USDT": {"maker": 0.0, "taker": 0.0},
            "FDUSD/USDT": {"maker": 0.0, "taker": 0.0},
            "binding_fee_per_side": FEE_SIDE_BINDING,
            "condition": "zero-fee promo is revocable; GO would be conditional on re-pull",
        },
        "costs_binding": {"fee_side": FEE_SIDE_BINDING, "slip_side": SLIP_SIDE_BINDING,
                          "stop_slip": STOP_SLIP_BINDING},
        "sizing": {"per_event_weight": 1.0, "leverage": "none (spot)"},
        "n_trials_dsr": N_TRIALS,
        "variants": variants,
        "pbo_weekly_across_variants": pbo_value,
        "gate_detail": detail,
        "verdict": verdict,
        "reason": reason,
        "thresholds": {"MIN_N": MIN_N, "MIN_DSR": MIN_DSR, "MAX_PBO": MAX_PBO,
                       "MIN_OOS_WR": MIN_OOS_WR, "MIN_AUC": MIN_AUC,
                       "MC_P>0>=": 0.95, "MC_maxDD_p95<=": 0.25,
                       "MC_MIN_TRADES": MC_MIN_TRADES},
    }


def _pbo_weekly(weekly_cols: dict[float, pd.Series]) -> float:
    """CSCV PBO on the weekly-bucket matrix across theta variants (prereg)."""
    from core.stat_tests import pbo

    if len(weekly_cols) < 2:
        return float("nan")
    mat = pd.DataFrame(weekly_cols).fillna(0.0)
    if mat.shape[0] < 16:
        return float("nan")
    try:
        return float(pbo(mat.values, n_partitions=16))
    except Exception:  # noqa: BLE001
        return float("nan")


def _decide(variants: dict, pbo_value: float) -> tuple[str, str, dict]:
    """Frozen 15c decision tree. NaN fails closed."""
    ns = {k: v.get("n_events", 0) for k, v in variants.items()}
    detail: dict = {"n_by_variant": ns, "pbo_weekly_across_variants": pbo_value}
    if not any(n >= MIN_N for n in ns.values()):
        return ("INSUFFICIENT_DATA",
                f"No theta variant reaches the frozen minimum n={MIN_N} resolved "
                f"events (n={ns}). More history or forward accrual required; "
                f"re-harvest: research/screen_stablecoin_depeg.py --harvest", detail)
    go, nogo = [], []
    for k, c in variants.items():
        if ns[k] < MIN_N:
            detail[k] = {"evaluable": False}
            continue
        mc = c.get("monte_carlo", {})
        oos = c.get("oos_wr_walk_forward")
        dsr = c.get("dsr_prob")
        auc = c.get("auc_frozen_score")
        checks = {
            "mean>0": bool(c.get("mean_net") is not None and c["mean_net"] > 0),
            "wr>=0.55": bool(c.get("win_rate") is not None and c["win_rate"] >= MIN_OOS_WR),
            "oos_wr>=0.55": bool(oos is not None and np.isfinite(oos) and oos >= MIN_OOS_WR),
            "dsr>=0.10": bool(dsr is not None and np.isfinite(dsr) and dsr >= MIN_DSR),
            "pbo<=0.5": bool(np.isfinite(pbo_value) and pbo_value <= MAX_PBO),
            "auc>=0.60": bool(auc is not None and np.isfinite(auc) and auc >= MIN_AUC),
            "mc_P>0>=0.95": bool(mc.get("p_total_positive") is not None
                                 and mc["p_total_positive"] >= 0.95),
            "mc_maxDD_p95<=0.25": bool(mc.get("max_drawdown_p95") is not None
                                       and mc["max_drawdown_p95"] <= 0.25),
        }
        detail[k] = checks
        ok = all(checks.values())
        if ok:
            go.append(k)
        else:
            nogo.append(f"{k}: failed {[x for x, v in checks.items() if not v]}")
    if go:
        return ("GO", f"All frozen gates pass at {go}. GO is CONDITIONAL on the "
                      f"zero-fee promo persisting (re-pull fees before any probe).", detail)
    return ("NO_GO", "No theta variant clears all frozen gates. " + " | ".join(nogo), detail)


# ── Selftest (synthetic bars validate CODE ONLY, never verdict evidence) ─────
def selftest() -> None:
    M = 60

    def bars(closes, opens=None, t0=1_700_000_000, step=M):
        ts = np.arange(t0, t0 + step * len(closes), step, dtype="int64")
        o = np.array(opens if opens is not None else closes, dtype=float)
        return ts, o, np.array(closes, dtype=float)

    # A: entry + REPEG exit, exact cost math
    ts, o, c = bars([1.0, 0.9975, 0.9980, 0.9999, 1.0],
                    [1.0, 0.9990, 0.9978, 0.9985, 0.99995])
    ev, ex = extract_events(ts, o, c, 20.0)
    assert len(ev) == 1 and ev[0]["exit_reason"] == "REPEG", ev
    assert abs(ev[0]["dev_trigger_bps"] - 25.0) < 1e-9
    assert ev[0]["entry_open"] == 0.9978 and ev[0]["exit_open"] == 0.99995
    want = (0.99995 * (1 - 0.0005)) / (0.9978 * (1 + 0.0005)) - 1
    got = apply_costs(ev[0], 0.0, 0.0005, 0.0010)
    assert abs(got - want) < 1e-12, (got, want)

    # B: STOP exit uses stop slippage; loss can exceed 8% on gap
    ts, o, c = bars([1.0, 0.9970, 0.9100, 0.9000, 0.9998, 1.0],
                    [1.0, 0.9980, 0.9975, 0.9050, 0.9990, 1.0])
    ev, ex = extract_events(ts, o, c, 20.0)
    assert len(ev) == 1 and ev[0]["exit_reason"] == "STOP", ev
    assert ev[0]["exit_open"] == 0.9050
    r = apply_costs(ev[0], 0.0, 0.0005, 0.0010)
    assert r < -0.09, r  # realized worse than -8%: honest gap handling

    # C: TIMEOUT after 48h with no repeg
    n_bars = 48 * 60 + 5
    closes = [1.0, 0.9970] + [0.9980] * (n_bars - 2)
    ts, o, c = bars(closes)
    ev, ex = extract_events(ts, o, c, 20.0)
    assert len(ev) == 1 and ev[0]["exit_reason"] == "TIMEOUT", (len(ev), ev and ev[0]["exit_reason"])

    # D: de-dup — no re-entry until re-peg; then a new dip re-enters
    ts, o, c = bars([1.0, 0.9970, 0.9999, 0.9970, 0.9970, 0.9999, 1.0, 0.9975, 0.9980, 0.9999, 1.0])
    ev, ex = extract_events(ts, o, c, 20.0)
    # event1: trigger@1 fill@2 repeg-exit trigger@2? close[2]=0.9999>=REPEG so exit trigger
    # at bar2, fill@3. disarmed; closes 0.9970,0.9970 no re-entry; re-arm at bar5 (0.9999);
    # trigger@7 (0.9975), fill@8, exit trigger@9, fill@10.
    assert len(ev) == 2, [e["entry_trigger_ts"] for e in ev]
    assert ev[1]["entry_trigger_ts"] == int(ts[7])

    # E: entry fill gap -> excluded, episode consumed
    t0 = 1_700_000_000
    ts = np.array([t0, t0 + M, t0 + M + 7200, t0 + M + 7260], dtype="int64")
    o = np.array([1.0, 0.9970, 0.9980, 0.9990])
    c = np.array([1.0, 0.9970, 0.9970, 0.9990])
    ev, ex = extract_events(ts, o, c, 20.0)
    assert len(ev) == 0 and ex["entry_fill_gap"] == 1, (ev, ex)

    # F: unresolved at sample end
    ts, o, c = bars([1.0, 0.9970, 0.9980, 0.9980])
    ev, ex = extract_events(ts, o, c, 20.0)
    assert len(ev) == 0 and ex["unresolved_at_end"] == 1, (ev, ex)

    # G: premium side never trades
    ts, o, c = bars([1.0, 1.0030, 1.0050, 1.0])
    ev, ex = extract_events(ts, o, c, 20.0)
    assert len(ev) == 0 and ex == {"entry_fill_gap": 0, "exit_fill_gap": 0,
                                   "unresolved_at_end": 0}

    print("SELFTEST OK (7 cases)")


if __name__ == "__main__":
    if "--selftest" in sys.argv:
        selftest()
    elif "--harvest" in sys.argv:
        harvest()
    else:
        result = run_screen()
        os.makedirs(os.path.dirname(OUT_JSON), exist_ok=True)
        with open(OUT_JSON, "w", encoding="utf-8") as fh:
            json.dump(result, fh, indent=2, default=str)
        print(json.dumps(result, indent=2, default=str))
