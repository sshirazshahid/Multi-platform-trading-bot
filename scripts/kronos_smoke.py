"""Phase 0 smoke test — stand up Kronos-mini, run+time ONE predict() on the
local BTC-USDT 15m cache, and validate the directional read end-to-end.

This is the compute feasibility gate: it prints per-inference wall-clock so the
Phase 2/3 backtest budget can be computed before building anything bigger.

No network needed (OHLCV cache is present locally). Run:
    python scripts/kronos_smoke.py
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
VENDOR = ROOT / "vendor" / "kronos"
sys.path.insert(0, str(VENDOR))

from model import Kronos, KronosPredictor, KronosTokenizer  # noqa: E402

TOK_DIR = ROOT / "models" / "kronos" / "Kronos-Tokenizer-2k"
MDL_DIR = ROOT / "models" / "kronos" / "Kronos-mini"
CACHE = ROOT / "data" / "ohlcv_cache"
LOOKBACK = 400  # context bars fed to the model
HORIZON = 4  # predict 4x 15m bars ahead (= 1h)
CUTOFF = pd.Timestamp("2025-09-01")


def main() -> None:
    import torch

    dev = "cuda:0" if torch.cuda.is_available() else "cpu"
    print(f"[device] {dev}" + (f"  ({torch.cuda.get_device_name(0)})" if dev != "cpu" else ""))

    t0 = time.perf_counter()
    tokenizer = KronosTokenizer.from_pretrained(str(TOK_DIR))
    model = Kronos.from_pretrained(str(MDL_DIR))
    predictor = KronosPredictor(model, tokenizer, device=dev, max_context=512)
    print(f"[load] tokenizer+model loaded in {time.perf_counter() - t0:.1f}s")

    df = pd.read_parquet(CACHE / "BTC-USDT_15m.parquet").reset_index(drop=True)
    df["dt"] = pd.to_datetime(df["ts"], unit="s")
    assert df["dt"].min() >= CUTOFF, f"LEAKAGE: cache starts {df['dt'].min()} < {CUTOFF}"
    print(
        f"[data] BTC 15m rows={len(df)} range {df['dt'].min()} -> {df['dt'].max()} (all post-cutoff)"
    )

    # decision bar i: context = [i-LOOKBACK+1 .. i]; forecast next HORIZON bars
    i = len(df) - HORIZON - 1
    ctx = df.iloc[i - LOOKBACK + 1 : i + 1]
    fut = df.iloc[i + 1 : i + 1 + HORIZON]
    x_df = ctx[["open", "high", "low", "close", "volume"]].reset_index(drop=True)
    x_ts = ctx["dt"].reset_index(drop=True)
    y_ts = fut["dt"].reset_index(drop=True)
    entry_close = float(ctx["close"].iloc[-1])

    for sc in (1, 5):
        t1 = time.perf_counter()
        pred = predictor.predict(
            df=x_df,
            x_timestamp=x_ts,
            y_timestamp=y_ts,
            pred_len=HORIZON,
            T=1.0,
            top_p=0.9,
            sample_count=sc,
            verbose=False,
        )
        dt_s = time.perf_counter() - t1
        pred_close = float(pred["close"].iloc[-1])
        exp_ret = pred_close / entry_close - 1.0
        realized = float(fut["close"].iloc[-1]) / entry_close - 1.0
        print(
            f"[predict sample_count={sc}] {dt_s * 1000:.0f} ms  "
            f"entry={entry_close:.1f} pred={pred_close:.1f} "
            f"exp_ret={exp_ret * 100:+.3f}%  realized={realized * 100:+.3f}%  "
            f"dir_match={'Y' if np.sign(exp_ret) == np.sign(realized) else 'n'}"
        )

    # budget estimate for the IC probe / bracket backtest
    per_inf_ms = dt_s * 1000
    for n in (2000, 10000, 30000):
        print(
            f"[budget] {n} inferences @ ~{per_inf_ms:.0f} ms = ~{n * per_inf_ms / 1000 / 60:.1f} min"
        )


if __name__ == "__main__":
    main()
