"""TradingView/CoinGecko market-regime forward-harvester (keyless REST).

WHY: TradingView's screener ratings (Recommend.All) are published SNAPSHOTS with
no retrievable history — point-in-time values must be accumulated forward (the
skew/L2 harvester precedent) before any reflexivity screen is possible. Global
dominance (USDT.D/BTC.D/ETH.D) and total market cap come keyless from CoinGecko
/global (primary; probed 2026-06-11), crowd ratings from TradingView's scanner
POST (probed keyless 2026-06-11; droppable if it ever requires auth).

HONEST PRIOR: NO_EDGE expected — historical dominance screens run separately
(scripts/run_tv_regime_screen.py); this only builds the point-in-time dataset
(published ratings + dominance as actually seen live, repaint-proof).

Robust by design (skew-harvester skeleton): pure REST polling, hourly-mean
buckets, atomic appends (temp+os.replace), backoff on errors, fail-open,
read-only w.r.t. the bot. Keyless.

Run: python scripts/harvest_tv.py            (continuous, poll every 5 min)
     python scripts/harvest_tv.py --once     (single poll, for verification)
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
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.process_lock import acquire_process_lock

HIST = ROOT / "data" / "tv_history.jsonl"
STATUS = ROOT / "data" / "tv_status.json"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
SCANNER_URL = "https://scanner.tradingview.com/crypto/scan"
RATING_BASES = ["BTC", "ETH", "SOL", "BNB", "XRP", "DOGE", "ADA", "LINK"]
POLL_SEC = 300


def parse_global(payload: dict) -> dict | None:
    """CoinGecko /global -> {usdt_d, btc_d, eth_d, total_usd} or None if malformed."""
    d = (payload or {}).get("data") or {}
    mcp = d.get("market_cap_percentage") or {}
    tot = (d.get("total_market_cap") or {}).get("usd")
    if mcp.get("usdt") is None or mcp.get("btc") is None or tot is None:
        return None
    eth = mcp.get("eth")
    return {
        "usdt_d": float(mcp["usdt"]),
        "btc_d": float(mcp["btc"]),
        "eth_d": float(eth) if eth is not None else None,
        "total_usd": float(tot),
    }


def parse_scanner(payload: dict) -> dict:
    """TV scanner response -> {BASE: Recommend.All} (rows without data skipped)."""
    out: dict = {}
    for row in (payload or {}).get("data") or []:
        if not isinstance(row, dict):
            continue
        s = row.get("s") or ""
        d = row.get("d") or []
        if not s or not d or d[0] is None:
            continue
        base = s.split(":")[-1]
        if base.endswith("USDT"):
            base = base[: -len("USDT")]
        out[base] = float(d[0])
    return out


def _poll() -> dict | None:
    r = requests.get(GLOBAL_URL, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"coingecko HTTP {r.status_code}: {r.text[:120]}")
    rec = parse_global(r.json())
    if rec is None:
        return None
    ratings: dict = {}
    try:  # ratings are droppable — never fail the poll on the scanner leg
        sr = requests.post(
            SCANNER_URL,
            json={
                "symbols": {
                    "tickers": [f"BINANCE:{b}USDT" for b in RATING_BASES],
                    "query": {"types": []},
                },
                "columns": ["Recommend.All"],
            },
            timeout=20,
            headers={"User-Agent": "Mozilla/5.0"},
        )
        if sr.status_code == 200:
            ratings = parse_scanner(sr.json())
    except Exception:
        pass
    rec["ratings"] = ratings
    return rec


def _hour(ts: float) -> int:
    return int(ts // 3600 * 3600)


def _mean(vals: list) -> float | None:
    vals = [v for v in vals if v is not None]
    return round(sum(vals) / len(vals), 6) if vals else None


def _flush(buckets: dict, now_hour: int) -> int:
    done = sorted(h for h in buckets if h < now_hour)
    rows = 0
    lines = []
    for h in done:
        polls = buckets[h]
        if polls:
            rat: dict = defaultdict(list)
            for p in polls:
                for k, v in (p.get("ratings") or {}).items():
                    if v is not None:
                        rat[k].append(v)
            lines.append(
                json.dumps(
                    {
                        "hour": h,
                        "usdt_d": _mean([p.get("usdt_d") for p in polls]),
                        "btc_d": _mean([p.get("btc_d") for p in polls]),
                        "eth_d": _mean([p.get("eth_d") for p in polls]),
                        "total_usd": _mean([p.get("total_usd") for p in polls]),
                        "ratings": {k: round(sum(v) / len(v), 4) for k, v in sorted(rat.items())},
                        "n_polls": len(polls),
                    }
                )
            )
            rows += 1
    if lines:
        HIST.parent.mkdir(parents=True, exist_ok=True)
        existing = HIST.read_text(encoding="utf-8") if HIST.exists() else ""
        tmp = HIST.parent / (HIST.name + ".tmp")
        tmp.write_text(existing + "\n".join(lines) + "\n", encoding="utf-8")
        os.replace(tmp, HIST)
    # prune only AFTER the durable write — a failed write must not lose the
    # buffered hours (point-in-time ratings are unrecoverable)
    for h in done:
        del buckets[h]
    return rows


def _status(connected: bool, buckets: dict, last: float, polls: int) -> None:
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


def main() -> None:
    once = "--once" in sys.argv
    _lock = None
    if not once:
        _lock = acquire_process_lock("harvest_tv", root=ROOT)
        if _lock is None:
            print("[tv] another harvester instance is already running; exiting", flush=True)
            return

    buckets: dict = defaultdict(list)
    polls = 0
    backoff = 5.0
    print("[tv] harvester starting (CoinGecko dominance + TV scanner ratings, keyless)", flush=True)
    while True:
        try:
            now = time.time()
            h = _hour(now)
            rec = _poll()
            if rec is not None:
                buckets[h].append(rec)
                polls += 1
                if once:
                    print(
                        f"[tv] usdt_d={rec['usdt_d']:.3f} btc_d={rec['btc_d']:.3f} "
                        f"ratings={len(rec['ratings'])} coins"
                    )
            n = _flush(buckets, h if not once else h + 1)
            _status(True, buckets, now, polls)
            if n:
                print(f"[tv] flushed {n} hourly rows -> {HIST.name} (polls={polls})", flush=True)
            backoff = 5.0
            if once:
                print(f"[tv] --once done (polls={polls})")
                return
            time.sleep(POLL_SEC)
        except Exception as e:
            _status(False, buckets, time.time(), polls)
            print(f"[tv] error ({type(e).__name__}: {e}); retry in {backoff:.0f}s", flush=True)
            if once:
                return
            time.sleep(backoff)
            backoff = min(120.0, backoff * 2)


if __name__ == "__main__":
    main()
