"""Options SKEW forward-harvester — Deribit 25-delta risk-reversal (free, keyless REST).

WHY: 25d risk-reversal (IV of the 25-delta call minus the 25-delta put) is a directional
options-positioning signal orthogonal to spot price/flow. Deribit's public API serves only
the CURRENT option chain (no free history), so history MUST be accumulated forward — this
polls the chain, computes RR25 + ATM IV per poll via Black-Scholes delta, and appends HOURLY
means to data/skew_history.jsonl so a frozen-gate screen becomes possible after weeks accrue.

Robust by design (learning from the liquidations WS harvester that crashed on a websockets
shutdown bug): pure REST polling, no event loop; atomic writes (temp+os.replace); catches all
errors and retries with backoff; never hard-crashes. Read-only w.r.t. the bot. Keyless.

HONEST PRIOR: NO_EDGE expected (skew is largely priced); this only builds the dataset.

Run: python scripts/harvest_skew.py            (continuous, poll every 5 min)
     python scripts/harvest_skew.py --once     (single poll, for verification)
"""

from __future__ import annotations

import datetime as dt
import json
import math
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

HIST = ROOT / "data" / "skew_history.jsonl"
STATUS = ROOT / "data" / "skew_status.json"
SUMMARY = "https://www.deribit.com/api/v2/public/get_book_summary_by_currency"
CCYS = ["BTC", "ETH"]
POLL_SEC = 300
MONTHS = {
    "JAN": 1,
    "FEB": 2,
    "MAR": 3,
    "APR": 4,
    "MAY": 5,
    "JUN": 6,
    "JUL": 7,
    "AUG": 8,
    "SEP": 9,
    "OCT": 10,
    "NOV": 11,
    "DEC": 12,
}


def _ncdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _bs_delta(is_call, s, k, t_years, iv):
    if s <= 0 or k <= 0 or t_years <= 0 or iv <= 0:
        return None
    d1 = (math.log(s / k) + 0.5 * iv * iv * t_years) / (iv * math.sqrt(t_years))
    nd1 = _ncdf(d1)
    return nd1 if is_call else nd1 - 1.0


def _parse_instrument(name):
    # "BTC-27JUN25-60000-C" -> (expiry_unix, strike, is_call)
    try:
        _, expiry, strike, kind = name.split("-")
        day = int(expiry[:-5])
        mon = MONTHS[expiry[-5:-2]]
        yr = 2000 + int(expiry[-2:])
        exp = dt.datetime(yr, mon, day, 8, 0, 0, tzinfo=dt.timezone.utc)
        return exp.timestamp(), float(strike), (kind == "C")
    except Exception:
        return None


def _interp_iv_at_delta(pairs, target):
    if len(pairs) < 2:
        return None
    pairs = sorted(pairs, key=lambda p: p[0])
    deltas = [p[0] for p in pairs]
    if target <= deltas[0]:
        return pairs[0][1]
    if target >= deltas[-1]:
        return pairs[-1][1]
    for i in range(1, len(pairs)):
        if deltas[i] >= target:
            d0, iv0 = pairs[i - 1]
            d1, iv1 = pairs[i]
            return iv1 if d1 == d0 else iv0 + (target - d0) / (d1 - d0) * (iv1 - iv0)
    return pairs[-1][1]


def _compute_skew(ccy, now):
    r = requests.get(SUMMARY, params={"currency": ccy, "kind": "option"}, timeout=30)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    data = (r.json() or {}).get("result") or []
    by_exp = defaultdict(list)
    und = None
    for it in data:
        parsed = _parse_instrument(it.get("instrument_name", ""))
        iv = it.get("mark_iv")
        u = it.get("underlying_price") or it.get("estimated_delivery_price")
        if parsed is None or iv is None or not u:
            continue
        und = float(u)
        exp_ts, strike, is_call = parsed
        by_exp[exp_ts].append((strike, is_call, float(iv) / 100.0))  # mark_iv in %
    if not by_exp or not und:
        return None
    cand = sorted(e for e in by_exp if 2 * 86400 <= (e - now) <= 45 * 86400 and len(by_exp[e]) >= 6)
    if not cand:
        return None
    exp = cand[0]
    t_years = (exp - now) / (365.0 * 86400.0)
    calls, puts, atm = [], [], []
    for strike, is_call, iv in by_exp[exp]:
        d = _bs_delta(is_call, und, strike, t_years, iv)
        if d is None:
            continue
        (calls if is_call else puts).append((d, iv))
        if 0.9 * und <= strike <= 1.1 * und:
            atm.append(iv)
    iv_25c = _interp_iv_at_delta(calls, 0.25)
    iv_25p = _interp_iv_at_delta(puts, -0.25)
    if iv_25c is None or iv_25p is None:
        return None
    rr25 = (iv_25c - iv_25p) * 100.0
    atm_iv = (sum(atm) / len(atm) * 100.0) if atm else None
    return rr25, atm_iv


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
            for ccy, vals in buckets[h].items():
                rr = [v[0] for v in vals if v[0] is not None]
                at = [v[1] for v in vals if v[1] is not None]
                if not rr:
                    continue
                lines.append(
                    json.dumps(
                        {
                            "hour": h,
                            "currency": ccy,
                            "rr25": round(sum(rr) / len(rr), 4),
                            "atm_iv": round(sum(at) / len(at), 4) if at else None,
                            "n_polls": len(rr),
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
    _lock = None
    if not once:
        _lock = acquire_process_lock("harvest_skew", root=ROOT)
        if _lock is None:
            print("[skew] another harvester instance is already running; exiting", flush=True)
            return

    buckets = defaultdict(lambda: defaultdict(list))
    polls = 0
    backoff = 5.0
    print("[skew] harvester starting (Deribit RR25, keyless REST)", flush=True)
    while True:
        try:
            now = time.time()
            h = _hour(now)
            for ccy in CCYS:
                res = _compute_skew(ccy, now)
                if res is not None:
                    buckets[h][ccy].append(res)
                    polls += 1
                    if once:
                        print(f"[skew] {ccy}: RR25={res[0]:+.2f}  ATM_IV={res[1]}")
            n = _flush(buckets, h if not once else h + 1)
            _status(True, buckets, now, polls)
            if n:
                print(f"[skew] flushed {n} hourly rows -> {HIST.name} (polls={polls})", flush=True)
            backoff = 5.0
            if once:
                print(f"[skew] --once done (polls={polls})")
                return
            time.sleep(POLL_SEC)
        except Exception as e:
            _status(False, buckets, time.time(), polls)
            print(f"[skew] error ({type(e).__name__}: {e}); retry in {backoff:.0f}s", flush=True)
            if once:
                return
            time.sleep(backoff)
            backoff = min(120.0, backoff * 2)


if __name__ == "__main__":
    main()
