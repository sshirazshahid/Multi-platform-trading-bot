"""L2 order-book imbalance forward-harvester — Binance futures depth (free, keyless REST).

WHY: top-of-book depth imbalance ((bid_qty - ask_qty)/(bid_qty + ask_qty)) is the
retail-accessible part of order-flow microstructure. Binance does not dump L2 book history
free, so it MUST be accumulated forward — this polls depth for the named coins, computes the
imbalance per poll, and appends HOURLY means to data/l2_history.jsonl for a future frozen-gate
screen. (Note: trade-flow ob_imbalance was ALREADY in the model and added no edge; this
captures the standing-book version. Honest prior: NO_EDGE.)

Robust by design: pure REST polling, atomic writes (temp+os.replace), catches all errors and
retries, never hard-crashes. Read-only w.r.t. the bot. Keyless. NO real-order path.

Run: python scripts/harvest_l2.py            (continuous, poll every 30s)
     python scripts/harvest_l2.py --once     (single poll per symbol, for verification)
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "data" / "l2_history.jsonl"
STATUS = ROOT / "data" / "l2_status.json"
DEPTH = "https://fapi.binance.com/fapi/v1/depth"
COINS = [
    "BTC",
    "ETH",
    "BNB",
    "SOL",
    "LINK",
    "TRX",
    "SUI",
    "ATOM",
    "DOGE",
    "ALGO",
    "ZEC",
    "ADA",
    "XRP",
    "LTC",
    "GRT",
]
LEVELS = 20
POLL_SEC = 30
FLUSH_EVERY_SEC = 300


def _imbalance(coin):
    sym = f"{coin}USDT"
    r = requests.get(DEPTH, params={"symbol": sym, "limit": LEVELS}, timeout=15)
    if r.status_code != 200:
        return None
    j = r.json()
    bids = j.get("bids") or []
    asks = j.get("asks") or []
    bid_q = sum(float(q) for _, q in bids)
    ask_q = sum(float(q) for _, q in asks)
    if bid_q + ask_q <= 0:
        return None
    return (bid_q - ask_q) / (bid_q + ask_q)


def _hour(ts):
    return int(ts // 3600 * 3600)


def _flush(buckets, now_hour):
    done = sorted(h for h in buckets if h < now_hour)
    rows = 0
    if done:
        HIST.parent.mkdir(parents=True, exist_ok=True)
        existing = HIST.read_text(encoding="utf-8") if HIST.exists() else ""
        lines = []
        for h in done:
            for coin, vals in buckets[h].items():
                v = [x for x in vals if x is not None]
                if not v:
                    continue
                lines.append(
                    json.dumps(
                        {
                            "hour": h,
                            "symbol": coin,
                            "imbalance": round(sum(v) / len(v), 6),
                            "n_polls": len(v),
                        }
                    )
                )
                rows += 1
            del buckets[h]
        if lines:
            tmp = HIST.parent / (HIST.name + ".tmp")
            tmp.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")
            os.replace(tmp, HIST)
    return rows


def _status(connected, buckets, last, polls):
    try:
        STATUS.write_text(
            json.dumps(
                {
                    "updated": last,
                    "connected": connected,
                    "open_hours": len(buckets),
                    "total_polls": polls,
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


def main():
    once = "--once" in sys.argv
    buckets = defaultdict(lambda: defaultdict(list))
    polls = 0
    last_flush = 0.0
    backoff = 5.0
    print("[l2] harvester starting (Binance depth imbalance, keyless REST)", flush=True)
    while True:
        try:
            now = time.time()
            h = _hour(now)
            for coin in COINS:
                imb = _imbalance(coin)
                if imb is not None:
                    buckets[h][coin].append(imb)
                    polls += 1
                    if once:
                        print(f"[l2] {coin}: imbalance={imb:+.4f}")
            if once or now - last_flush >= FLUSH_EVERY_SEC:
                n = _flush(buckets, h if not once else h + 1)
                _status(True, buckets, now, polls)
                if n:
                    print(
                        f"[l2] flushed {n} hourly rows -> {HIST.name} (polls={polls})", flush=True
                    )
                last_flush = now
            backoff = 5.0
            if once:
                print(f"[l2] --once done (polls={polls})")
                return
            time.sleep(POLL_SEC)
        except Exception as e:
            _status(False, buckets, time.time(), polls)
            print(f"[l2] error ({type(e).__name__}: {e}); retry in {backoff:.0f}s", flush=True)
            if once:
                return
            time.sleep(backoff)
            backoff = min(120.0, backoff * 2)


if __name__ == "__main__":
    main()
