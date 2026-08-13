"""Screen 70 — hourly L2 book-imbalance -> forward drift.

Implements _workspace/strategy_pipeline/70_prereg_l2_imbalance_drift.md
EXACTLY (sha256 88ca0168c888…, commit 5c847b8, hashed BEFORE outcomes).
Verifies the hash at startup; refuses to run if the spec was edited.

Read-only over data/. Never imported by the bot; no config change on any
result. Run: venv/Scripts/python.exe research/screen_l2_imbalance_drift.py
"""

from __future__ import annotations

import hashlib
import json
import pathlib
import random
import statistics as st

ROOT = pathlib.Path(__file__).resolve().parents[1]
PREREG = ROOT / "_workspace/strategy_pipeline/70_prereg_l2_imbalance_drift.md"
PREREG_SHA = "88ca0168c88885bcd52b9a2fbdf423a9653ee384297a1a986ab8f866938e3d77"
L2 = ROOT / "data/l2_history.jsonl"
CACHE = ROOT / "data/ohlcv_cache"

COST_FRAC = 22.0 / 10_000.0          # 22 bps taker round trip (frozen)
THETAS = (0.2, 0.3)
HORIZONS = (1, 4)
SCOPES = ("all", "majors")           # pooled 15 vs BTC/ETH only
M = len(THETAS) * len(HORIZONS) * len(SCOPES)  # 8
MIN_N = 100
MIN_POLLS = 30
SPLIT = 0.70
BOOT = 1000


def verify_prereg() -> None:
    actual = hashlib.sha256(PREREG.read_bytes()).hexdigest()
    if actual != PREREG_SHA:
        raise SystemExit(f"PREREG HASH MISMATCH: {actual} != {PREREG_SHA}")
    print(f"prereg hash OK: {actual[:16]}...")


def load_prices() -> dict:
    import pandas as pd

    out = {}
    for p in CACHE.glob("*-USDT_1h.parquet"):
        sym = p.name.split("-USDT_1h")[0]
        try:
            df = pd.read_parquet(p)[["ts", "close"]].dropna()
        except Exception:
            continue
        s = {}
        for ts, close in zip(df["ts"], df["close"]):
            t = int(ts)
            if t > 10**12:
                t //= 1000
            s[t - (t % 3600)] = float(close)
        out[sym] = s
    return out


def load_signal() -> list:
    rows = []
    with open(L2, encoding="utf-8", errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if (d.get("n_polls") or 0) < MIN_POLLS:
                continue
            v = d.get("imbalance")
            if isinstance(v, (int, float)):
                rows.append((int(d["hour"]), str(d["symbol"]), float(v)))
    rows.sort()
    return rows


def build_events(rows, prices, theta, h, scope) -> list:
    """De-overlapped events: (symbol_day, after-cost signed return)."""
    busy: dict = {}
    evs = []
    for hour, sym, imb in rows:
        if scope == "majors" and sym not in ("BTC", "ETH"):
            continue
        if abs(imb) < theta:
            continue
        if busy.get(sym, 0) > hour:
            continue
        px = prices.get(sym)
        if not px:
            continue
        p0, p1 = px.get(hour), px.get(hour + h * 3600)
        if not p0 or not p1:
            continue
        sign = 1.0 if imb > 0 else -1.0
        ret = sign * (p1 - p0) / p0 - COST_FRAC
        evs.append((f"{sym}:{hour // 86400}", ret))
        busy[sym] = hour + h * 3600  # non-overlapping, first-fire wins
    return evs


def cluster_boot_p(evs) -> float:
    """One-sided p(mean<=0) via symbol-day cluster bootstrap."""
    clusters: dict = {}
    for cd, r in evs:
        clusters.setdefault(cd, []).append(r)
    keys = list(clusters)
    rng = random.Random(70)
    neg = 0
    for _ in range(BOOT):
        sample = [r for k in (rng.choice(keys) for _ in keys) for r in clusters[k]]
        if st.mean(sample) <= 0:
            neg += 1
    return neg / BOOT


def main() -> None:
    verify_prereg()
    prices = load_prices()
    rows = load_signal()
    syms = sorted({s for _, s, _ in rows})
    missing = [s for s in syms if s not in prices]
    print(f"signal rows={len(rows):,} symbols={len(syms)} missing_price={missing}")

    cells = []
    for theta in THETAS:
        for h in HORIZONS:
            for scope in SCOPES:
                evs = build_events(rows, prices, theta, h, scope)
                n = len(evs)
                cell = {"theta": theta, "H": h, "scope": scope, "n": n}
                if n < MIN_N:
                    cell["verdict"] = "INSUFFICIENT_DATA"
                    cells.append(cell)
                    continue
                rets = [r for _, r in evs]
                mean = st.mean(rets)
                wr = sum(1 for r in rets if r > 0) / n
                cut = int(SPLIT * n)
                oos = rets[cut:]
                oos_wr = (sum(1 for r in oos if r > 0) / len(oos)) if oos else 0.0
                p = cluster_boot_p(evs)
                # MC P(total>0): resample events iid
                rng = random.Random(170)
                pos = sum(
                    1 for _ in range(BOOT)
                    if sum(rng.choice(rets) for _ in range(n)) > 0
                )
                mc = pos / BOOT
                cell.update(mean_bps=mean * 1e4, wr=wr, oos_wr=oos_wr,
                            p_raw=p, mc_p=mc)
                cells.append(cell)

    # Holm over the tested cells (raw p ascending)
    tested = [c for c in cells if "p_raw" in c]
    tested.sort(key=lambda c: c["p_raw"])
    for i, c in enumerate(tested):
        c["p_holm"] = min(1.0, c["p_raw"] * (M - i))
    for c in cells:
        if "p_raw" not in c:
            continue
        ok = (c["mean_bps"] > 0 and c["p_holm"] < 0.05 and c["oos_wr"] >= 0.55
              and c["mc_p"] >= 0.95 and c["n"] >= MIN_N)
        c["verdict"] = "PASS" if ok else "NO_GO"

    print(f"\n{'theta':>5} {'H':>2} {'scope':>7} {'n':>5} {'mean_bps':>9} "
          f"{'WR':>5} {'oosWR':>6} {'p_holm':>7} {'mc':>5}  verdict")
    for c in cells:
        if "mean_bps" in c:
            print(f"{c['theta']:5.1f} {c['H']:2d} {c['scope']:>7} {c['n']:5d} "
                  f"{c['mean_bps']:+9.1f} {c['wr']:5.2f} {c['oos_wr']:6.2f} "
                  f"{c['p_holm']:7.3f} {c['mc_p']:5.2f}  {c['verdict']}")
        else:
            print(f"{c['theta']:5.1f} {c['H']:2d} {c['scope']:>7} {c['n']:5d} "
                  f"{'—':>9} {'—':>5} {'—':>6} {'—':>7} {'—':>5}  {c['verdict']}")

    passes = [c for c in cells if c.get("verdict") == "PASS"]
    verdict = "CANDIDATE_PASS (audit required)" if passes else "NO_GO"
    print(f"\nVERDICT: {verdict}")
    out = ROOT / "_workspace/strategy_pipeline/70_screen_l2_imbalance_drift.json"
    out.write_text(json.dumps({"prereg_sha256": PREREG_SHA, "cells": cells,
                               "verdict": verdict}, indent=1), encoding="utf-8")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
