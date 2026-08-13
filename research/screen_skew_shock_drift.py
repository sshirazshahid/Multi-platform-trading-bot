"""Screen 71 — Deribit rr25 skew-shock -> forward perp drift (BTC/ETH).

Implements _workspace/strategy_pipeline/71_prereg_skew_shock_drift.md EXACTLY
(sha256 30953f5a95d7…, commit 630d9d1, hashed BEFORE outcomes). Verifies the
hash at startup; refuses to run if edited.

Read-only; never imported by the bot; no config change on any result.
Run: venv/Scripts/python.exe research/screen_skew_shock_drift.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import random
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
PREREG = ROOT / "_workspace/strategy_pipeline/71_prereg_skew_shock_drift.md"
PREREG_SHA = "30953f5a95d7d30b6cd95a7a384d21d384702c474509489e4c12111c6de11a2f"

COST = 22.0 / 10_000.0
THETAS = (0.5, 1.0)
HORIZONS = (4, 12)
CURS = ("BTC", "ETH")
M = 8
MIN_N = 30
BOOT = 1000


def verify() -> None:
    a = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if a != PREREG_SHA:
        raise SystemExit(f"PREREG HASH MISMATCH: {a}")
    print(f"prereg hash OK: {a[:16]}...")


def prices(cur: str) -> dict:
    import pandas as pd

    df = pd.read_parquet(ROOT / f"data/ohlcv_cache/{cur}-USDT_1h.parquet")[["ts", "close"]].dropna()
    out = {}
    for ts, close in zip(df["ts"], df["close"]):
        t = int(ts)
        if t > 10**12:
            t //= 1000
        out[t - (t % 3600)] = float(close)
    return out


def skew() -> dict:
    rows = {c: [] for c in CURS}
    with open(ROOT / "data/skew_history.jsonl", encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            c = d.get("currency")
            if c in rows and isinstance(d.get("rr25"), (int, float)) and (d.get("n_polls") or 0) >= 6:
                rows[c].append((int(d["hour"]), float(d["rr25"])))
    for c in rows:
        rows[c].sort()
    return rows


def events(sk, px, theta, h):
    """(day, aligned_ret, contrarian_ret) de-overlapped."""
    busy = 0
    out = []
    for i in range(1, len(sk)):
        t0, v0 = sk[i - 1]
        t1, v1 = sk[i]
        if t1 - t0 != 3600 or abs(v1 - v0) < theta or busy > t1:
            continue
        p0, p1 = px.get(t1), px.get(t1 + h * 3600)
        if not p0 or not p1:
            continue
        sign = 1.0 if v1 > v0 else -1.0
        raw = (p1 - p0) / p0
        out.append((t1 // 86400, sign * raw - COST, -sign * raw - COST))
        busy = t1 + h * 3600
    return out


def boot_p(evs, idx) -> float:
    cl: dict = {}
    for e in evs:
        cl.setdefault(e[0], []).append(e[idx])
    keys = list(cl)
    rng = random.Random(71)
    neg = 0
    for _ in range(BOOT):
        s = [r for k in (rng.choice(keys) for _ in keys) for r in cl[k]]
        if st.mean(s) <= 0:
            neg += 1
    return neg / BOOT


def main() -> None:
    verify()
    sk = skew()
    px = {c: prices(c) for c in CURS}
    cells = []
    for theta in THETAS:
        for h in HORIZONS:
            for cur in CURS:
                evs = events(sk[cur], px[cur], theta, h)
                n = len(evs)
                cell = {"theta": theta, "H": h, "cur": cur, "n": n}
                if n < MIN_N:
                    cell["verdict"] = "INSUFFICIENT_DATA"
                    cells.append(cell)
                    continue
                al = [e[1] for e in evs]
                co = [e[2] for e in evs]
                mean = st.mean(al)
                wr = sum(1 for r in al if r > 0) / n
                cut = int(0.70 * n)
                oos = al[cut:]
                oos_wr = sum(1 for r in oos if r > 0) / len(oos) if oos else 0.0
                p = boot_p(evs, 1)
                rng = random.Random(171)
                mc = sum(1 for _ in range(BOOT)
                         if sum(rng.choice(al) for _ in range(n)) > 0) / BOOT
                cell.update(mean_bps=mean * 1e4, wr=wr, oos_wr=oos_wr,
                            p_raw=p, mc_p=mc, contra_mean_bps=st.mean(co) * 1e4)
                cells.append(cell)

    tested = sorted([c for c in cells if "p_raw" in c], key=lambda c: c["p_raw"])
    for i, c in enumerate(tested):
        c["p_holm"] = min(1.0, c["p_raw"] * (M - i))
    for c in cells:
        if "p_raw" in c:
            ok = (c["mean_bps"] > 0 and c["p_holm"] < 0.05
                  and c["oos_wr"] >= 0.55 and c["mc_p"] >= 0.95)
            c["verdict"] = "PASS" if ok else "NO_GO"

    print(f"\n{'th':>4} {'H':>3} {'cur':>4} {'n':>4} {'mean_bps':>9} {'WR':>5} "
          f"{'oosWR':>6} {'p_holm':>7} {'mc':>5} {'contra':>8}  verdict")
    for c in cells:
        if "mean_bps" in c:
            print(f"{c['theta']:4.1f} {c['H']:3d} {c['cur']:>4} {c['n']:4d} "
                  f"{c['mean_bps']:+9.1f} {c['wr']:5.2f} {c['oos_wr']:6.2f} "
                  f"{c['p_holm']:7.3f} {c['mc_p']:5.2f} {c['contra_mean_bps']:+8.1f}  {c['verdict']}")
        else:
            print(f"{c['theta']:4.1f} {c['H']:3d} {c['cur']:>4} {c['n']:4d} "
                  f"{'—':>9} {'—':>5} {'—':>6} {'—':>7} {'—':>5} {'—':>8}  {c['verdict']}")

    verdict = ("CANDIDATE_PASS (audit required)"
               if any(c.get("verdict") == "PASS" for c in cells) else "NO_GO")
    print(f"\nVERDICT: {verdict}")
    out = ROOT / "_workspace/strategy_pipeline/71_screen_skew_shock_drift.json"
    out.write_text(json.dumps({"prereg_sha256": PREREG_SHA, "cells": cells,
                               "verdict": verdict}, indent=1), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
