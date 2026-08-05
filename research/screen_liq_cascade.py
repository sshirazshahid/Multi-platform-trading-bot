"""Liquidation-cascade / OI-flush reversion screen — prereg 41_.

Frozen prereg: _workspace/strategy_pipeline/41_prereg_liq_cascade.md
sha256_md: 13ee84e40f2604b660d763082f2681200532c1f6bd55cbbb21f4c9491085afaf

Majors arm ONLY this run (BTC, ETH). FIT-alt is fail-closed separately and
not scored here. Stage-0 stopping rule: every cell triggers < 30 →
INSUFFICIENT_DATA (no NO_GO ledger row). After Stage-0 pass: after-cost
gates with Holm multiplicity (prereg §Gates).

CLI:
  ./venv/Scripts/python.exe research/screen_liq_cascade.py
  ./venv/Scripts/python.exe research/screen_liq_cascade.py --stage0-only
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.decision.monte_carlo import monte_carlo_trade_sequence  # noqa: E402

LIQ = ROOT / "data" / "liquidations_history.jsonl"
CACHE = ROOT / "data" / "ohlcv_cache"
WS = ROOT / "_workspace" / "strategy_pipeline"
PREREG_PATH = WS / "41_prereg_liq_cascade.md"
PREREG_SHA256 = (
    "13ee84e40f2604b660d763082f2681200532c1f6bd55cbbb21f4c9491085afaf"
)

MAJORS = ("BTC", "ETH")
THETAS = (1_000_000.0, 5_000_000.0)
HORIZONS = (4, 12)
COSTS_BPS = (30.0, 60.0)
Z_WIN = 168
Z_K = 2.5
THETA_MIN_Z = 1_000_000.0
MIN_N = 30
FUNDING_BPS_PER_8H = 1.0  # conservative default when history absent
N_MC = 2000
SEED = 41


def verify_prereg() -> None:
    """Hash body with sha256_md value blanked (canonical freeze method 2026-07-29)."""
    text = PREREG_PATH.read_text(encoding="utf-8")
    lines = []
    for line in text.splitlines(True):
        if line.startswith("**sha256_md:**"):
            lines.append("**sha256_md:**\n")
        else:
            lines.append(line)
    digest = hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()
    if digest != PREREG_SHA256:
        raise SystemExit(
            f"PREREG HASH MISMATCH: got {digest}, expected {PREREG_SHA256}. "
            "Aborting — outcomes must not run against a drifted prereg."
        )


def load_liq(symbols: tuple[str, ...]) -> dict[str, pd.DataFrame]:
    by: dict[str, list] = defaultdict(list)
    with LIQ.open(encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except json.JSONDecodeError:
                continue
            sym = str(r.get("symbol", "")).upper()
            if sym not in symbols:
                continue
            by[sym].append(
                (
                    int(r["hour"]),
                    float(r.get("long_usd") or 0.0),
                    float(r.get("short_usd") or 0.0),
                )
            )
    out: dict[str, pd.DataFrame] = {}
    for sym, rows in by.items():
        df = (
            pd.DataFrame(rows, columns=["hour", "long_usd", "short_usd"])
            .drop_duplicates("hour")
            .sort_values("hour")
            .reset_index(drop=True)
        )
        out[sym] = df
    return out


def load_price_1h(coin: str) -> pd.DataFrame | None:
    p = CACHE / f"{coin}-USDT_1h.parquet"
    if not p.exists():
        return None
    df = pd.read_parquet(p)
    if not {"ts", "close"}.issubset(df.columns):
        return None
    df = (
        df[["ts", "close"]]
        .dropna()
        .sort_values("ts")
        .drop_duplicates("ts")
        .reset_index(drop=True)
    )
    ts = df["ts"].astype("int64")
    div = 1000 if int(ts.iloc[-1]) > 1_000_000_000_000 else 1
    df["hour"] = (ts // div // 3600 * 3600).astype(int)
    return df[["hour", "close"]].drop_duplicates("hour")


def zscore_past(x: np.ndarray, win: int) -> np.ndarray:
    z = np.full(len(x), np.nan)
    for t in range(win, len(x)):
        w = x[t - win : t]
        s = w.std()
        if s > 0:
            z[t] = (x[t] - w.mean()) / s
    return z


def funding_cost_frac(hold_h: int) -> float:
    """Default settlement charge when local funding history is unused."""
    settlements = math.ceil(hold_h / 8.0)
    return settlements * (FUNDING_BPS_PER_8H / 1e4)


def stage0(liq: dict[str, pd.DataFrame]) -> list[dict]:
    rows = []
    for sym, df in liq.items():
        L = df["long_usd"].to_numpy(dtype=float)
        S = df["short_usd"].to_numpy(dtype=float)
        zL = zscore_past(L, Z_WIN)
        zS = zscore_past(S, Z_WIN)
        for th in THETAS:
            n_long = int(((L >= th) & (L >= S)).sum())
            n_short = int(((S >= th) & (S >= L)).sum())
            rows.append(
                {
                    "arm": "majors_btc_eth",
                    "symbol": sym,
                    "cell": f"abs_th_{int(th)}",
                    "side": "long_flush",
                    "n_triggers": n_long,
                    "stage0_ok": n_long >= MIN_N,
                }
            )
            rows.append(
                {
                    "arm": "majors_btc_eth",
                    "symbol": sym,
                    "cell": f"abs_th_{int(th)}",
                    "side": "short_flush",
                    "n_triggers": n_short,
                    "stage0_ok": n_short >= MIN_N,
                }
            )
        n_zl = int(((zL >= Z_K) & (L >= THETA_MIN_Z) & (L >= S)).sum())
        n_zs = int(((zS >= Z_K) & (S >= THETA_MIN_Z) & (S >= L)).sum())
        rows.append(
            {
                "arm": "majors_btc_eth",
                "symbol": sym,
                "cell": "z_overlay_2.5_thmin_1e6",
                "side": "long_flush",
                "n_triggers": n_zl,
                "stage0_ok": n_zl >= MIN_N,
            }
        )
        rows.append(
            {
                "arm": "majors_btc_eth",
                "symbol": sym,
                "cell": "z_overlay_2.5_thmin_1e6",
                "side": "short_flush",
                "n_triggers": n_zs,
                "stage0_ok": n_zs >= MIN_N,
            }
        )
    return rows


def event_returns(
    liq: pd.DataFrame,
    price: pd.DataFrame,
    *,
    mode: str,
    theta: float | None,
    horizon: int,
    cost_bps: float,
) -> np.ndarray:
    """Signed net returns (fraction) for non-overlapping events."""
    m = (
        liq.merge(price, on="hour", how="inner")
        .sort_values("hour")
        .reset_index(drop=True)
    )
    if len(m) < Z_WIN + horizon + MIN_N:
        return np.array([], dtype=float)

    hour = m["hour"].to_numpy(dtype=int)
    close = m["close"].to_numpy(dtype=float)
    L = m["long_usd"].to_numpy(dtype=float)
    S = m["short_usd"].to_numpy(dtype=float)
    zL = zscore_past(L, Z_WIN)
    zS = zscore_past(S, Z_WIN)

    if mode == "abs_long":
        assert theta is not None
        mask = (L >= theta) & (L >= S)
        direction = +1
    elif mode == "abs_short":
        assert theta is not None
        mask = (S >= theta) & (S >= L)
        direction = -1
    elif mode == "z_long":
        mask = (zL >= Z_K) & (L >= THETA_MIN_Z) & (L >= S)
        direction = +1
    elif mode == "z_short":
        mask = (zS >= Z_K) & (S >= THETA_MIN_Z) & (S >= L)
        direction = -1
    else:
        raise ValueError(mode)

    cost = cost_bps / 1e4 + funding_cost_frac(horizon)
    rets: list[float] = []
    next_free = -1
    for t in range(len(m) - horizon):
        if not mask[t] or t < next_free:
            continue
        raw = direction * (close[t + horizon] / close[t] - 1.0)
        rets.append(float(raw - cost))
        next_free = t + horizon  # non-overlap: skip until flat
    return np.asarray(rets, dtype=float)


def walk_forward_oos_wr(rets: np.ndarray, n_folds: int = 5) -> float:
    """Simple chronological fold WR on the held-out last 40% as OOS proxy.

    Prereg asks OOS-WR ≥ 0.55. With event series we embargo the first 60%
    as IS and score WR on the trailing 40%.
    """
    if len(rets) < MIN_N:
        return float("nan")
    cut = max(MIN_N // 2, int(len(rets) * 0.6))
    oos = rets[cut:]
    if len(oos) < 5:
        oos = rets[len(rets) // 2 :]
    if len(oos) == 0:
        return float("nan")
    return float((oos > 0).mean())


def holm_adjust(pvals: list[float]) -> list[float]:
    m = len(pvals)
    order = sorted(range(m), key=lambda i: pvals[i])
    adj = [1.0] * m
    running = 0.0
    for rank, i in enumerate(order):
        factor = m - rank
        val = min(1.0, pvals[i] * factor)
        running = max(running, val)
        adj[i] = running
    return adj


def one_sided_mean_pvalue(rets: np.ndarray) -> float:
    """Normal approx one-sided p that mean ≤ 0."""
    if len(rets) < 2:
        return 1.0
    s = rets.std(ddof=1)
    if s <= 0:
        return 0.0 if rets.mean() > 0 else 1.0
    t = rets.mean() / (s / math.sqrt(len(rets)))
    # 1 - Φ(t) via erfc
    return 0.5 * math.erfc(t / math.sqrt(2.0))


def screen_majors(liq: dict[str, pd.DataFrame]) -> list[dict]:
    prices = {s: load_price_1h(s) for s in MAJORS}
    missing = [s for s, p in prices.items() if p is None]
    if missing:
        raise SystemExit(f"missing 1h OHLCV for {missing}")

    cells: list[dict] = []
    for sym in MAJORS:
        for th in THETAS:
            for side, mode in (("long_flush", "abs_long"), ("short_flush", "abs_short")):
                for H in HORIZONS:
                    for cost in COSTS_BPS:
                        cells.append(
                            {
                                "symbol": sym,
                                "signal": f"abs_th_{int(th)}",
                                "side": side,
                                "mode": mode,
                                "theta": th,
                                "horizon": H,
                                "cost_bps": cost,
                            }
                        )
        for side, mode in (("long_flush", "z_long"), ("short_flush", "z_short")):
            for H in HORIZONS:
                for cost in COSTS_BPS:
                    cells.append(
                        {
                            "symbol": sym,
                            "signal": "z_overlay_2.5_thmin_1e6",
                            "side": side,
                            "mode": mode,
                            "theta": None,
                            "horizon": H,
                            "cost_bps": cost,
                        }
                    )

    results = []
    raw_p = []
    for cell in cells:
        rets = event_returns(
            liq[cell["symbol"]],
            prices[cell["symbol"]],
            mode=cell["mode"],
            theta=cell["theta"],
            horizon=cell["horizon"],
            cost_bps=cell["cost_bps"],
        )
        n = int(len(rets))
        mean = float(rets.mean()) if n else float("nan")
        wr = float((rets > 0).mean()) if n else float("nan")
        oos_wr = walk_forward_oos_wr(rets) if n >= MIN_N else float("nan")
        p = one_sided_mean_pvalue(rets) if n >= MIN_N else 1.0
        mc = None
        if n >= MIN_N:
            mc = monte_carlo_trade_sequence(
                rets,
                block_len=max(2, min(8, n // 5)),
                n_resamples=N_MC,
                seed=SEED,
            )
        row = {
            **{k: cell[k] for k in ("symbol", "signal", "side", "horizon", "cost_bps")},
            "theta": cell["theta"],
            "n": n,
            "mean_net": mean,
            "wr": wr,
            "oos_wr": oos_wr,
            "p_mean_le_0": p,
            "mc_p_pos": None if mc is None else float(mc.p_total_positive),
            "mc_maxdd_p95": None if mc is None else float(mc.max_drawdown_p95),
        }
        results.append(row)
        raw_p.append(p)

    adj = holm_adjust(raw_p)
    for row, p_adj in zip(results, adj):
        row["p_holm"] = p_adj
        n = row["n"]
        go = (
            n >= MIN_N
            and row["mean_net"] > 0
            and (row["oos_wr"] or 0) >= 0.55
            and (row["mc_p_pos"] or 0) >= 0.95
            and (row["mc_maxdd_p95"] or 1) <= 0.25
            and p_adj <= 0.05
        )
        row["verdict"] = (
            "INSUFFICIENT_DATA"
            if n < MIN_N
            else ("GO" if go else "NO_GO")
        )
        fails = []
        if n < MIN_N:
            fails.append("n<30")
        else:
            if not (row["mean_net"] > 0):
                fails.append("mean<=0")
            if not ((row["oos_wr"] or 0) >= 0.55):
                fails.append(f"oos_wr={row['oos_wr']:.3f}<0.55")
            if not ((row["mc_p_pos"] or 0) >= 0.95):
                fails.append(f"mc_p={row['mc_p_pos']:.3f}<0.95")
            if not ((row["mc_maxdd_p95"] or 1) <= 0.25):
                fails.append(f"maxdd_p95={row['mc_maxdd_p95']:.3f}>0.25")
            if not (p_adj <= 0.05):
                fails.append(f"holm_p={p_adj:.3f}>0.05")
        row["fail_reasons"] = fails
    return results


def write_artifacts(stage0_rows: list[dict], screen_rows: list[dict] | None) -> None:
    stage0_ok_any = any(r["stage0_ok"] for r in stage0_rows)
    payload = {
        "candidate": "liq_cascade_oi_flush_reversion",
        "prereg": str(PREREG_PATH.relative_to(ROOT)),
        "prereg_sha256": PREREG_SHA256,
        "arm": "majors_btc_eth",
        "stage0": stage0_rows,
        "stage0_any_ok": stage0_ok_any,
        "screen": screen_rows,
    }
    if screen_rows is None:
        payload["verdict"] = "INSUFFICIENT_DATA" if not stage0_ok_any else "STAGE0_PASS"
    else:
        gos = [r for r in screen_rows if r["verdict"] == "GO"]
        payload["verdict"] = "GO" if gos else "NO_GO"
        payload["n_go_cells"] = len(gos)
        payload["n_cells"] = len(screen_rows)

    out_json = WS / "41_screen_liq_cascade_majors.json"
    out_md = WS / "41_screen_liq_cascade_majors.md"
    out_json.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = [
        "# 41 — Screen: Liquidation-cascade majors (BTC/ETH)",
        f"*Prereg sha256 `{PREREG_SHA256[:16]}…` | Arm: majors_btc_eth*",
        "",
        f"## Verdict: **{payload['verdict']}**",
        "",
        "## Stage-0",
        "",
        "| Symbol | Cell | Side | Triggers | OK |",
        "|--------|------|------|----------|----|",
    ]
    for r in stage0_rows:
        lines.append(
            f"| {r['symbol']} | {r['cell']} | {r['side']} | {r['n_triggers']} | "
            f"{'Y' if r['stage0_ok'] else 'N'} |"
        )
    if screen_rows is not None:
        lines += [
            "",
            "## After-cost cells",
            "",
            "| Sym | Signal | Side | H | Cost | n | Mean | OOS-WR | MC P>0 | maxDD p95 | Verdict | Fails |",
            "|-----|--------|------|---|------|---|------|--------|--------|-----------|---------|-------|",
        ]
        for r in sorted(screen_rows, key=lambda x: (x["verdict"] != "GO", -(x["mean_net"] or -9))):
            lines.append(
                f"| {r['symbol']} | {r['signal']} | {r['side']} | {r['horizon']} | "
                f"{r['cost_bps']:.0f}bps | {r['n']} | "
                f"{(r['mean_net'] or 0)*1e4:.1f}bps | "
                f"{(r['oos_wr'] or float('nan')):.3f} | "
                f"{(r['mc_p_pos'] if r['mc_p_pos'] is not None else float('nan')):.3f} | "
                f"{(r['mc_maxdd_p95'] if r['mc_maxdd_p95'] is not None else float('nan')):.3f} | "
                f"{r['verdict']} | {','.join(r['fail_reasons']) or '—'} |"
            )
        lines += [
            "",
            "## Honest read",
            "- GO requires ALL frozen gates jointly (n, mean>0, OOS-WR≥0.55, MC, maxDD, Holm).",
            "- Prior ~25%. Undercount on Binance forceOrder is binding measurement error.",
            "- No probe / no MCP unless owner-signed CONFIRMED_GO after audit.",
        ]
    out_md.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {out_json}")
    print(f"wrote {out_md}")
    print(f"VERDICT={payload['verdict']}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--stage0-only", action="store_true")
    args = ap.parse_args()

    verify_prereg()
    liq = load_liq(MAJORS)
    if not liq:
        raise SystemExit("no BTC/ETH rows in liquidations_history.jsonl")
    s0 = stage0(liq)
    if args.stage0_only or not any(r["stage0_ok"] for r in s0):
        write_artifacts(s0, None)
        return 0
    rows = screen_majors(liq)
    write_artifacts(s0, rows)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
