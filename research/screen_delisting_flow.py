"""DELISTING FORCED-FLOW after-cost screen (edge-screener, 2026-07-16).

Pre-registered in _workspace/strategy_pipeline/15a_screen_delisting.md BEFORE this
file was written. Two sub-claims from the merged scout brief (14_scout_a candidate 1
+ 14_scout_b candidate 2b):

  2a — capital-scaled (3%/12%, unlevered) Binance-perp SHORT on the tokens named in a
       "Binance Will Delist ... on YYYY-MM-DD" announcement, entered at the close of
       the first full 1h bar after the announcement, exited before the pre-removal /
       pre-settlement cutoff (E1 primary; E2 +3d / E3 +7d robustness).
  2b — survivor-venue SPOT BOUNCE: long the same tokens on Bitget spot (the one venue
       whose history-candles endpoint still serves later-delisted symbols, bounding
       survivorship) at the same entry bar, exits +1d/+3d/+7d.

Event independence: one announcement = ONE event (batch waves are one market event).
All gate statistics run per-event; token counts are diagnostics only.

Costs charged (ALL of them): config.FEE taker per venue, config.SLIPPAGE 5bps/side,
realized funding to the short from data.binance.vision fundingRate dumps (fallback
data/funding_history CSVs), and a pre-registered 25bps/side survivor-book half-spread
haircut on 2b. Missing data is excluded-and-counted, never guessed.

Frozen gates: DSR>=0.10 (n_trials=6), PBO<=0.5, OOS-WR>=0.55, MC P(total>0)>=0.95 &
maxDD p95<=0.25 (min 30 events), realized concurrent-MTM account maxDD<=0.25,
beats-control. NaN fails closed.

Run:  venv/Scripts/python.exe research/screen_delisting_flow.py [--offline]
Harvest cache: data/delisting_screen/ (gitignored; PUBLIC repo — never commit data).
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import zipfile
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from research.screen_listing_short import (  # noqa: E402  (reuse unit-tested precedent)
    MAX_CONCURRENT,
    MAX_CONCURRENT_EXPOSURE,  # noqa: F401  (re-exported frozen constant)
    MAX_PBO,
    MC_MIN_TRADES,
    MIN_DSR,
    MIN_OOS_WR,
    STAKE_FRAC,
    _dsr_prob,
    _monte_carlo,
    _oos_wr_walk_forward,
    _pbo_across_horizons,
    apply_concurrency_cap,
    funding_sum_in_window,
    load_funding_history,
    price_at,
    short_net_return,
    window_funding_covered,
)

# ── Pre-registered constants (frozen; see 15a_screen_delisting.md) ───────────
CACHE_DIR = "data/delisting_screen"
# WIN_LO amended 2023-01-01 -> 2022-03-01 BEFORE any price/funding harvest or
# return computation (event-count-only observation: 28 events < the frozen MC floor
# of 30; the CMS archive is complete from 2022-02-17). See 15a md, Amendment A1.
WIN_LO = int(datetime(2022, 3, 1, tzinfo=timezone.utc).timestamp())
WIN_HI = int(datetime(2026, 7, 1, tzinfo=timezone.utc).timestamp())
REMOVAL_HOUR_UTC = 3
DAY = 86400
ENTRY_MAX_LAG = 3 * 3600  # entry bar must begin within 3h of the announcement
EXIT_TOL = 12 * 3600  # exit bar must exist within 12h of its target
# MIN_DSR / MAX_PBO / MIN_OOS_WR / MC_MIN_TRADES / STAKE_FRAC /
# MAX_CONCURRENT_EXPOSURE / MAX_CONCURRENT are imported from
# research.screen_listing_short (identical frozen values; pinned by
# tests/test_screen_delisting_flow.py::test_gate_thresholds_frozen).
N_TRIALS_DSR = 6  # TRUE variant count: 2 sub-claims x 3 exits (1st family registration)
HALF_SPREAD_2B = 0.0025  # pre-registered survivor-book half-spread per side
HALF_SPREAD_SENSITIVITY = (0.0, 0.01)  # reporting-only, non-gating
EXITS_2A = ("E1_precutoff", "E2_3d", "E3_7d")
HORIZONS_2B_D = (1, 3, 7)
DEDUP_LOOKBACK_D = 21  # re-announcement of already-announced bases = same event
MAX_HARD_ERRORS = 20

CMS_URL = ("https://www.binance.com/bapi/apex/v1/public/apex/cms/article/list/query"
           "?type=1&pageNo={page}&pageSize=50&catalogId=161")
BYBIT_ANN_URL = ("https://api.bybit.com/v5/announcements/index"
                 "?locale=en-US&type=delistings&limit=100&page={page}")
VISION = "https://data.binance.vision/data"
BITGET_HIST = ("https://api.bitget.com/api/v2/spot/market/history-candles"
               "?symbol={sym}&granularity=1h&endTime={end_ms}&limit=200")
UA = {"User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                     "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")}


# ═══════════════════════ Pure functions (unit-tested) ════════════════════════
def classify_title(title: str) -> str:
    """Bucket a delisting-category announcement title. Order matters."""
    t = title.strip()
    if "Monitoring Tag" in t:
        return "monitoring"
    if "Removal of" in t and "Trading Pairs" in t:
        return "pair_removal"
    if "Binance Alpha" in t:
        return "alpha_removal"
    if "Futures Will Delist" in t:
        return "futures_delist"
    if "Margin" in t and "Delist" in t:
        return "margin_removal"
    if t.startswith("Binance Will Delist"):
        return "spot_delist"
    return "other"


def parse_delist_title(title: str) -> tuple[list[str], str | None]:
    """('Binance Will Delist A, B & C on YYYY-MM-DD') -> (['A','B','C'], date)."""
    m = re.search(r"\bon\s+(\d{4}-\d{2}-\d{2})", title)
    date = m.group(1) if m else None
    seg = title
    if seg.startswith("Binance Will Delist"):
        seg = seg[len("Binance Will Delist"):]
    seg = seg.split(" on ")[0]
    bases = []
    for part in re.split(r",|&|\band\b", seg):
        tok = part.strip().strip(".")
        if re.fullmatch(r"[A-Z0-9]{1,10}", tok) and tok not in {"USDT", "USDC", "BUSD"}:
            bases.append(tok)
    return bases, date


def removal_ts_from_date(date_str: str) -> int:
    """Removal date at 03:00 UTC (Binance's standard delisting hour)."""
    d = datetime.strptime(date_str, "%Y-%m-%d").replace(
        hour=REMOVAL_HOUR_UTC, tzinfo=timezone.utc)
    return int(d.timestamp())


def ts_to_seconds(v) -> int:
    """Sniff s/ms/us (vision spot dumps switched ms->us in 2025)."""
    v = int(v)
    if v >= 10**14:
        return v // 10**6
    if v >= 10**11:
        return v // 10**3
    return v


def parse_vision_klines_csv(text: str) -> pd.DataFrame:
    """Vision kline CSV (header optional, ts in ms or us) -> ts/o/h/l/c/volume."""
    skip = 1 if text[:20].lower().startswith("open_time") else 0
    df = pd.read_csv(io.StringIO(text), header=None, skiprows=skip, usecols=range(6),
                     names=["ts", "open", "high", "low", "close", "volume"])
    df["ts"] = df["ts"].map(ts_to_seconds).astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def parse_vision_funding_csv(text: str) -> pd.Series:
    """Vision fundingRate CSV -> Series(index=settlement ts sec, value=rate)."""
    first = text.split("\n", 1)[0]
    has_header = any(ch.isalpha() for ch in first)
    df = pd.read_csv(io.StringIO(text), header=0 if has_header else None)
    if has_header:
        tcol = next((c for c in df.columns if "calc_time" in str(c)), df.columns[0])
        rcol = next((c for c in reversed(df.columns) if "funding_rate" in str(c)),
                    df.columns[-1])
    else:
        tcol, rcol = df.columns[0], df.columns[-1]
    ts = df[tcol].map(ts_to_seconds).astype("int64")
    ser = pd.Series(df[rcol].astype(float).values, index=ts.values)
    return ser[~ser.index.duplicated()].sort_index()


def parse_bitget_candles(rows: list) -> pd.DataFrame:
    """Bitget history-candles rows -> ts(sec)/o/h/l/c/volume(base), asc, deduped."""
    if not rows:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.DataFrame(rows).iloc[:, :6]
    df.columns = ["ts", "open", "high", "low", "close", "volume"]
    df["ts"] = df["ts"].map(ts_to_seconds).astype("int64")
    for c in ("open", "high", "low", "close", "volume"):
        df[c] = df[c].astype(float)
    return df.drop_duplicates("ts").sort_values("ts").reset_index(drop=True)


def long_net_return(entry_price: float, exit_price: float, fee_per_side: float,
                    slip_per_side: float, half_spread: float) -> float:
    """After-cost return of a spot LONG with a per-side half-spread haircut."""
    gross = (exit_price - entry_price) / entry_price
    return float(gross - 2.0 * (fee_per_side + slip_per_side + half_spread))


def event_mean(token_rets: dict) -> float:
    """Equal-weighted per-event mean (the independence unit). NaN when empty."""
    if not token_rets:
        return float("nan")
    return float(np.mean(list(token_rets.values())))


def months_spanning(lo_ts: int, hi_ts: int) -> list[str]:
    """['YYYY-MM', ...] covering [lo_ts, hi_ts] inclusive."""
    lo = datetime.fromtimestamp(lo_ts, tz=timezone.utc)
    hi = datetime.fromtimestamp(hi_ts, tz=timezone.utc)
    out, y, m = [], lo.year, lo.month
    while (y, m) <= (hi.year, hi.month):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def concurrent_mtm_max_dd(positions: list[dict]) -> float:
    """Max drawdown of the hourly-marked ACCOUNT equity curve of all positions
    held simultaneously (start equity 1.0).

    Each position: {side:+1/-1, stake, entry_ts, exit_ts, entry_px,
    series: DataFrame(ts, close), terminal_extra: return-on-stake applied at exit
    (funding sum minus round-trip costs)}.

    APPROXIMATION (documented 2026-07-18): ``terminal_extra`` is applied as a
    single lump AT EXIT rather than accrued per settlement/bar. Bias direction:
    when terminal_extra > 0 (e.g. a short collecting positive funding) the
    intra-hold equity is understated, so the reported maxDD is OVERSTATED
    (conservative); when terminal_extra < 0 the intra-hold maxDD can be
    UNDERSTATED (optimistic). Event-level net returns are exact either way —
    only the intra-hold equity path is approximate.
    """
    if not positions:
        return float("nan")
    pos = []
    all_ts = set()
    for p in positions:
        s = p["series"]
        sl = s[(s["ts"] >= p["entry_ts"]) & (s["ts"] <= p["exit_ts"])]
        ser = pd.Series(sl["close"].values, index=sl["ts"].values.astype("int64"))
        pos.append({**p, "_ser": ser})
        all_ts.update(int(t) for t in ser.index)
        all_ts.add(int(p["exit_ts"]))
    timeline = sorted(all_ts)

    def px_at(pp, t):
        ser = pp["_ser"]
        idx = ser.index[ser.index <= t]
        return float(ser[idx[-1]]) if len(idx) else float(pp["entry_px"])

    peak, max_dd = 1.0, 0.0
    for t in timeline:
        eq = 1.0
        for pp in pos:
            if t < pp["entry_ts"]:
                continue
            if t >= pp["exit_ts"]:
                exit_px = px_at(pp, pp["exit_ts"])
                gross = pp["side"] * (exit_px - pp["entry_px"]) / pp["entry_px"]
                eq += pp["stake"] * (gross + pp["terminal_extra"])
            else:
                mtm = pp["side"] * (px_at(pp, t) - pp["entry_px"]) / pp["entry_px"]
                eq += pp["stake"] * mtm
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak if peak > 0 else 0.0)
    return float(max_dd)


# ═══════════════════════ Harvest layer (network + cache) ═════════════════════
_session = None
_hard_errors = 0
NOT_FOUND = object()  # sentinel: genuine HTTP 404 — the ONLY cacheable absence


def _sess():
    global _session
    if _session is None:
        import requests

        _session = requests.Session()
        _session.headers.update(UA)
    return _session


def _get(url: str, timeout: int = 30, allow_body_4xx: bool = False):
    """GET with hard-error accounting and 404-vs-transient discrimination.

    Returns (cache-poisoning fix, 2026-07-18):
      * Response on 200;
      * Response on non-429 4xx when ``allow_body_4xx`` is set (Bitget
        signals unknown symbols via HTTP 400 + body code 40034 — callers
        inspect the body);
      * NOT_FOUND on genuine HTTP 404 — the only outcome callers may turn
        into a permanent cache marker (.missing sentinel);
      * None on transient failure: exception, 429, 5xx — and any non-404 4xx
        when ``allow_body_4xx`` is unset (a systematic 403/451 on
        data.binance.vision must surface as a hard error, not silence).
        Callers must NEVER write permanent cache entries for None.
    Exceptions and bad statuses count toward the hard-error abort."""
    global _hard_errors
    try:
        r = _sess().get(url, timeout=timeout)
    except Exception as e:  # network failure — never silently a verdict
        _hard_errors += 1
        if _hard_errors > MAX_HARD_ERRORS:
            raise RuntimeError(f"harvest aborted: >{MAX_HARD_ERRORS} network errors "
                               f"(last: {url} -> {e})") from e
        return None
    if r.status_code == 200:
        return r
    if r.status_code == 404:
        return NOT_FOUND
    if 400 <= r.status_code < 500 and r.status_code != 429 and allow_body_4xx:
        return r
    _hard_errors += 1
    if _hard_errors > MAX_HARD_ERRORS:
        raise RuntimeError(f"harvest aborted: >{MAX_HARD_ERRORS} bad statuses "
                           f"(last: {url} -> {r.status_code})")
    return None


def harvest_announcements(offline: bool = False) -> list[dict]:
    """Full Binance delisting-category archive -> cached announcements.json.

    Cache format (2026-07-18+): {"archive_truncated": bool, "articles": [...]}.
    A legacy plain-list cache (pre-flag) is still readable. If the 30-page cap
    is hit with the final chunk still full, the archive is TRUNCATED — the flag
    is recorded in the cache and a warning printed on every load."""
    os.makedirs(CACHE_DIR, exist_ok=True)
    path = os.path.join(CACHE_DIR, "announcements.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            cached = json.load(fh)
        if isinstance(cached, dict):
            if cached.get("archive_truncated"):
                print("WARNING: cached announcements archive is TRUNCATED at the "
                      "page cap — older delisting events are absent")
            return cached["articles"]
        return cached  # legacy list format (no truncation flag recorded)
    if offline:
        raise RuntimeError(
            "announcements.json missing and --offline set. Harvest command: "
            "venv/Scripts/python.exe research/screen_delisting_flow.py --harvest-announcements")
    arts, page, truncated = [], 1, False
    while True:
        r = _get(CMS_URL.format(page=page))
        if r is None or r is NOT_FOUND:
            raise RuntimeError(f"Binance CMS archive fetch failed at page {page}: {CMS_URL}")
        chunk = r.json()["data"]["catalogs"][0]["articles"]
        arts.extend({"id": a["id"], "title": a["title"], "releaseDate": a["releaseDate"]}
                    for a in chunk)
        if len(chunk) < 50:
            break
        if page >= 30:  # cap reached with a still-full chunk -> archive truncated
            truncated = True
            print(f"WARNING: announcements page cap ({page}) reached with a full "
                  f"final chunk — archive is TRUNCATED (flag recorded in cache)")
            break
        page += 1
        time.sleep(1.0)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump({"archive_truncated": truncated, "articles": arts}, fh,
                  ensure_ascii=False, indent=1)
    return arts


def harvest_bybit_archive(offline: bool = False) -> list[dict]:
    """Bybit delistings archive (coverage cross-check ONLY — not gated this pass).

    Caches ONLY when pagination completed normally (empty page, or the full
    10-page cap fetched successfully). A transient failure returns the partial
    in-memory result UNCACHED so a later run retries (cache-poisoning fix,
    2026-07-18)."""
    path = os.path.join(CACHE_DIR, "bybit_announcements.json")
    if os.path.exists(path):
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    if offline:
        return []
    out, page, complete = [], 1, False
    while page <= 10:
        r = _get(BYBIT_ANN_URL.format(page=page))
        if r is None or r is NOT_FOUND:
            break  # transient/abnormal -> do NOT cache the partial result
        lst = r.json().get("result", {}).get("list", [])
        if not lst:
            complete = True  # ran off the end of the archive: normal completion
            break
        out.extend({"title": x["title"], "publishTime": x.get("publishTime")} for x in lst)
        page += 1
        time.sleep(0.3)
    else:
        complete = True  # all 10 pages fetched successfully (archive page cap)
    if complete:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(out, fh, ensure_ascii=False, indent=1)
    return out


def _vision_csv(kind_path: str, cache_key: str, offline: bool) -> str | None:
    """Download+cache one vision zip -> CSV text.

    The permanent ``.missing`` sentinel is written ONLY on a genuine HTTP 404
    (NOT_FOUND). Transient failures (timeout/429/5xx) return None WITHOUT
    caching anything, so a later run retries — a transient blip must never
    become a permanent coverage hole (cache-poisoning fix, 2026-07-18)."""
    fdir = os.path.join(CACHE_DIR, "vision", os.path.dirname(cache_key))
    os.makedirs(fdir, exist_ok=True)
    fpath = os.path.join(CACHE_DIR, "vision", cache_key)
    if os.path.exists(fpath):
        with open(fpath, encoding="utf-8") as fh:
            return fh.read()
    if os.path.exists(fpath + ".missing"):
        return None
    if offline:
        return None
    r = _get(f"{VISION}/{kind_path}")
    time.sleep(0.05)
    if r is NOT_FOUND:  # genuine 404: the file does not exist on vision
        open(fpath + ".missing", "w").close()
        return None
    if r is None:  # transient failure — retry next run, cache NOTHING
        return None
    with zipfile.ZipFile(io.BytesIO(r.content)) as zf:
        text = zf.read(zf.namelist()[0]).decode("utf-8", errors="replace")
    with open(fpath, "w", encoding="utf-8") as fh:
        fh.write(text)
    return text


def _month_is_current_or_future(month: str) -> bool:
    now = datetime.now(timezone.utc)
    return month >= f"{now.year:04d}-{now.month:02d}"


def load_vision_klines(market: str, sym: str, lo_ts: int, hi_ts: int,
                       offline: bool = False) -> pd.DataFrame:
    """Concatenated 1h klines from vision monthly zips (daily zips for the current
    month). market in {'spot','futures/um'}."""
    frames = []
    for month in months_spanning(lo_ts, hi_ts):
        if _month_is_current_or_future(month):
            d = datetime.strptime(month + "-01", "%Y-%m-%d").replace(tzinfo=timezone.utc)
            last = min(datetime.now(timezone.utc) - timedelta(days=2),
                       datetime.fromtimestamp(hi_ts, tz=timezone.utc))
            while d <= last:
                day = d.strftime("%Y-%m-%d")
                text = _vision_csv(
                    f"{market}/daily/klines/{sym}/1h/{sym}-1h-{day}.zip",
                    f"{market.replace('/', '_')}/{sym}/{day}.csv", offline)
                if text:
                    frames.append(parse_vision_klines_csv(text))
                d += timedelta(days=1)
        else:
            text = _vision_csv(
                f"{market}/monthly/klines/{sym}/1h/{sym}-1h-{month}.zip",
                f"{market.replace('/', '_')}/{sym}/{month}.csv", offline)
            if text:
                frames.append(parse_vision_klines_csv(text))
    if not frames:
        return pd.DataFrame(columns=["ts", "open", "high", "low", "close", "volume"])
    df = pd.concat(frames).drop_duplicates("ts").sort_values("ts").reset_index(drop=True)
    return df[(df["ts"] >= lo_ts) & (df["ts"] <= hi_ts)].reset_index(drop=True)


def load_vision_funding(sym: str, lo_ts: int, hi_ts: int,
                        offline: bool = False) -> pd.Series:
    """Realized funding from vision monthly fundingRate zips (no daily dumps exist;
    the current month is uncoverable from vision — local CSVs are the fallback)."""
    parts = []
    for month in months_spanning(lo_ts, hi_ts):
        if _month_is_current_or_future(month):
            continue
        text = _vision_csv(
            f"futures/um/monthly/fundingRate/{sym}/{sym}-fundingRate-{month}.zip",
            f"funding/{sym}/{month}.csv", offline)
        if text:
            parts.append(parse_vision_funding_csv(text))
    if not parts:
        return pd.Series(dtype=float)
    ser = pd.concat(parts)
    return ser[~ser.index.duplicated()].sort_index()


def load_bitget_spot(sym: str, lo_ts: int, hi_ts: int,
                     offline: bool = False) -> pd.DataFrame | None:
    """Bitget spot 1h candles via history-candles (serves delisted symbols too).
    None => symbol not listed on Bitget spot (counted, bounding survivorship).

    The cache CSV is written ONLY when pagination completed normally (reached
    lo_ts, or the venue ran out of history — an empty page with code 00000).
    On an abnormal break (transient _get failure, bad JSON, unexpected code,
    page-cap exhaustion) the in-memory df is returned UNCACHED so a later run
    retries (cache-poisoning fix, 2026-07-18). 40034 -> .notlisted kept;
    40309 ("The symbol has been removed": WAS listed, later removed — distinct
    semantic from never-listed) -> .removed marker, added 2026-07-18."""
    os.makedirs(os.path.join(CACHE_DIR, "bitget"), exist_ok=True)
    key = os.path.join(CACHE_DIR, "bitget", f"{sym}_{lo_ts}_{hi_ts}")
    if os.path.exists(key + ".csv"):
        return pd.read_csv(key + ".csv")
    if os.path.exists(key + ".notlisted") or os.path.exists(key + ".removed"):
        return None
    if offline:
        return None
    rows, end_ms, pages = [], (hi_ts + 3600) * 1000, 0
    complete = False
    while pages < 12:
        r = _get(BITGET_HIST.format(sym=sym, end_ms=end_ms), allow_body_4xx=True)
        time.sleep(0.15)
        if r is None or r is NOT_FOUND:
            break  # transient/abnormal -> return uncached
        try:
            j = r.json()
        except Exception:
            break
        if str(j.get("code")) == "40034":  # symbol does not exist on bitget (HTTP 400)
            open(key + ".notlisted", "w").close()
            return None
        if str(j.get("code")) == "40309":  # "The symbol has been removed" (HTTP 400)
            # Permanent: the history endpoint no longer serves this symbol.
            # Returning None lands in the SAME 2b exclusion bucket
            # (2b_not_on_bitget, survivorship-bounding) that these symbols'
            # previous header-only/empty cached CSVs landed in via the
            # `bg is None or len(bg) == 0` check — so re-run coverage numbers
            # stay comparable to the published 15a JSON.
            open(key + ".removed", "w").close()
            return None
        if str(j.get("code")) != "00000":
            break
        data = j.get("data") or []
        if not data:
            complete = True  # venue has no earlier history: normal completion
            break
        rows = data + rows
        min_ts = min(ts_to_seconds(x[0]) for x in data)
        if min_ts <= lo_ts:
            complete = True  # window start reached: full coverage
            break
        end_ms = min_ts * 1000
        pages += 1
    df = parse_bitget_candles(rows)
    df = df[(df["ts"] >= lo_ts) & (df["ts"] <= hi_ts)].reset_index(drop=True)
    if complete:
        df.to_csv(key + ".csv", index=False)
    return df


# ═══════════════════════ Event construction ══════════════════════════════════
def build_events(articles: list[dict]) -> tuple[list[dict], dict]:
    """Qualifying spot-delist events in-window, deduped (re-announcement of bases
    already announced within DEDUP_LOOKBACK_D = same market event)."""
    counts = {}
    events = []
    for a in articles:
        cls = classify_title(a["title"])
        counts[cls] = counts.get(cls, 0) + 1
        if cls != "spot_delist":
            continue
        ann_ts = int(a["releaseDate"]) // 1000
        if not (WIN_LO <= ann_ts < WIN_HI):
            counts["spot_delist_out_of_window"] = counts.get("spot_delist_out_of_window", 0) + 1
            continue
        bases, date = parse_delist_title(a["title"])
        if not bases or not date:
            counts["spot_delist_unparseable"] = counts.get("spot_delist_unparseable", 0) + 1
            continue
        events.append({"ann_ts": ann_ts, "removal_ts": removal_ts_from_date(date),
                       "date": date, "bases": bases, "title": a["title"]})
    events.sort(key=lambda e: e["ann_ts"])
    deduped, dropped = [], 0
    for ev in events:
        prior = set()
        for p in deduped:
            if ev["ann_ts"] - p["ann_ts"] <= DEDUP_LOOKBACK_D * DAY:
                prior |= set(p["bases"])
        if set(ev["bases"]) <= prior:
            dropped += 1
            continue
        deduped.append(ev)
    counts["duplicate_events_dropped"] = dropped
    return deduped, counts


# ═══════════════════════ Screen orchestration ════════════════════════════════
def run_screen(offline: bool = False, limit_events: int | None = None) -> dict:
    import config

    fee_fut = float(config.FEE["futures_taker"])  # binance perp leg
    fee_spot_bitget = float(config.FEE["bitget_spot_taker"])
    slip = (float(config.SLIPPAGE["pct_open"]) + float(config.SLIPPAGE["pct_close"])) / 2.0

    articles = harvest_announcements(offline)
    bybit_arch = harvest_bybit_archive(offline)
    events, title_counts = build_events(articles)
    if limit_events:
        events = events[:limit_events]

    # control: BTC+ETH binance spot over each event window
    ctrl_cache: dict[str, pd.DataFrame] = {}

    def control_df(sym):
        """Full-window control series (WIN_LO-2d .. now), loaded once per
        symbol. Deliberately takes NO lo/hi: control_return slices the window
        itself via price_at (signature honesty fix, 2026-07-18)."""
        if sym not in ctrl_cache:
            ctrl_cache[sym] = load_vision_klines(
                "spot", sym, WIN_LO - 2 * DAY,
                int(datetime.now(timezone.utc).timestamp()), offline)
        return ctrl_cache[sym]

    def control_return(lo, hi):
        rets = []
        for sym in ("BTCUSDT", "ETHUSDT"):
            df = control_df(sym)
            p0, _ = price_at(df, lo)
            p1, _ = price_at(df, hi)
            if p0 and p1 and p0 > 0:
                rets.append((p1 - p0) / p0)
        return float(np.mean(rets)) if rets else None

    excl = {k: 0 for k in (
        "2a_no_perp", "2a_no_entry_bar", "2a_window_too_short", "2a_no_exit_bar",
        "2a_no_funding_window", "2b_not_on_bitget", "2b_no_entry_bar",
        "2b_entry_zero_volume", "2b_no_exit_bar", "2b_exit_zero_volume",
        "2b_dead_book_window")}

    # per-token records: {variant: list of dicts}
    recs_2a = {v: [] for v in EXITS_2A}
    recs_2b = {h: [] for h in HORIZONS_2B_D}
    sens_2b = {hs: {h: [] for h in HORIZONS_2B_D} for hs in HALF_SPREAD_SENSITIVITY}
    funding_sources = {"vision": 0, "local_csv": 0}

    for ev in events:
        ann, removal = ev["ann_ts"], ev["removal_ts"]
        lo = ann - 2 * DAY
        hi = max(removal, ann + 8 * DAY) + 2 * DAY
        for base in ev["bases"]:
            sym = f"{base}USDT"
            eid = f"{ev['date']}|{ev['ann_ts']}"

            # ── 2a: Binance perp short ──────────────────────────────────────
            perp = load_vision_klines("futures/um", sym, lo, hi, offline)
            if len(perp) == 0:
                excl["2a_no_perp"] += 1
            else:
                p0, a0 = price_at(perp, ann)
                if p0 is None or (a0 - ann) > ENTRY_MAX_LAG:
                    excl["2a_no_entry_bar"] += 1
                else:
                    fser = load_vision_funding(sym, lo, hi, offline)
                    fsrc = "vision"
                    if len(fser) == 0:
                        fser = load_funding_history(base, "binance")
                        fsrc = "local_csv"
                    perp_end = int(perp["ts"].iloc[-1])
                    for v in EXITS_2A:
                        if v == "E1_precutoff":
                            # CAVEAT (documented 2026-07-18): anchoring on
                            # perp_end (the last bar the perp ever printed)
                            # conditions the E1 exit on the REALIZED lifetime
                            # of the contract — ex-post information whenever
                            # the perp delists before the spot removal date.
                            # A mild exit-timing look-ahead; the 15a verdict
                            # (INSUFFICIENT_DATA) did not rest on it, but any
                            # re-run that reaches the gates must revisit this.
                            target = min(removal, perp_end + 3600) - DAY
                        elif v == "E2_3d":
                            target = a0 + 3 * DAY
                        else:
                            target = a0 + 7 * DAY
                        if target <= a0:
                            excl["2a_window_too_short"] += 1
                            continue
                        p1, a1 = price_at(perp, target)
                        if p1 is None or (a1 - target) > EXIT_TOL:
                            excl["2a_no_exit_bar"] += 1
                            continue
                        if not window_funding_covered(fser, a0, a1):
                            excl["2a_no_funding_window"] += 1
                            continue
                        fsum = funding_sum_in_window(fser, a0, a1)
                        net = short_net_return(p0, p1, fsum, fee_fut, slip)
                        recs_2a[v].append({
                            "event": eid, "base": base, "entry_ts": a0, "exit_ts": a1,
                            "entry_px": p0, "exit_px": p1, "net": net, "fsum": fsum,
                            "gross": (p1 - p0) / p0, "series": perp,
                            "terminal_extra": fsum - 2 * (fee_fut + slip),
                        })
                        if fsrc == "vision":
                            funding_sources["vision"] += 1
                        else:
                            funding_sources["local_csv"] += 1

            # ── 2b: Bitget spot bounce ──────────────────────────────────────
            bg = load_bitget_spot(sym, lo, hi, offline)
            if bg is None or len(bg) == 0:
                excl["2b_not_on_bitget"] += 1
                continue
            p0, a0 = price_at(bg, ann)
            if p0 is None or (a0 - ann) > ENTRY_MAX_LAG:
                excl["2b_no_entry_bar"] += 1
                continue
            entry_row = bg[bg["ts"] == a0].iloc[0]
            if float(entry_row["volume"]) <= 0:
                excl["2b_entry_zero_volume"] += 1
                continue
            for h in HORIZONS_2B_D:
                target = a0 + h * DAY
                p1, a1 = price_at(bg, target)
                if p1 is None or (a1 - target) > EXIT_TOL:
                    excl["2b_no_exit_bar"] += 1
                    continue
                exit_row = bg[bg["ts"] == a1].iloc[0]
                if float(exit_row["volume"]) <= 0:
                    excl["2b_exit_zero_volume"] += 1
                    continue
                win = bg[(bg["ts"] >= a0) & (bg["ts"] <= a1)]
                if len(win) == 0 or float((win["volume"] > 0).mean()) < 0.5:
                    excl["2b_dead_book_window"] += 1
                    continue
                net = long_net_return(p0, p1, fee_spot_bitget, slip, HALF_SPREAD_2B)
                recs_2b[h].append({
                    "event": eid, "base": base, "entry_ts": a0, "exit_ts": a1,
                    "entry_px": p0, "exit_px": p1, "net": net,
                    "gross": (p1 - p0) / p0, "series": bg,
                    "terminal_extra": -2 * (fee_spot_bitget + slip + HALF_SPREAD_2B),
                })
                for hs in HALF_SPREAD_SENSITIVITY:
                    sens_2b[hs][h].append(
                        long_net_return(p0, p1, fee_spot_bitget, slip, hs))

    # ═══ Gate battery per sub-claim ═══════════════════════════════════════════
    def variant_stats(recs: list[dict], side: int) -> tuple[dict, dict]:
        """Per-EVENT gate statistics + capital-scaled account series + MTM dd.

        Returns ``(stats, ev_ret_by_id)`` — the per-event return map feeds
        pbo_for and is never serialized into the verdict JSON."""
        by_ev: dict[str, list[dict]] = {}
        for r in recs:
            by_ev.setdefault(r["event"], []).append(r)
        ev_ids = sorted(by_ev, key=lambda e: min(r["entry_ts"] for r in by_ev[e]))
        ev_rets, ev_pairs, ev_under = [], [], []
        for e in ev_ids:
            toks = by_ev[e]
            ev_rets.append(event_mean({r["base"]: r["net"] for r in toks}))
            lo_e = min(r["entry_ts"] for r in toks)
            hi_e = max(r["exit_ts"] for r in toks)
            ev_pairs.append((lo_e, hi_e))
            cr = control_return(lo_e, hi_e)
            if cr is not None:
                g = float(np.mean([r["gross"] for r in toks]))
                ev_under.append((cr - g) if side < 0 else (g - cr))
        arr = np.array(ev_rets, dtype=float)
        n_ev = int(arr.size)

        # capital-scaled account series under the 3%/12% concurrency cap
        allrecs = sorted(recs, key=lambda r: (r["entry_ts"], r["base"]))
        ee = [(i, r["entry_ts"], r["exit_ts"]) for i, r in enumerate(allrecs)]
        acc_idx, capped_idx, peak = apply_concurrency_cap(ee, MAX_CONCURRENT)
        accepted = [allrecs[i] for i in acc_idx]
        acct_by_ev: dict[str, float] = {}
        for r in accepted:
            acct_by_ev[r["event"]] = acct_by_ev.get(r["event"], 0.0) + STAKE_FRAC * r["net"]
        acct_seq = np.array([acct_by_ev[e] for e in ev_ids if e in acct_by_ev], dtype=float)
        positions = [{"side": side, "stake": STAKE_FRAC, "entry_ts": r["entry_ts"],
                      "exit_ts": r["exit_ts"], "entry_px": r["entry_px"],
                      "series": r["series"], "terminal_extra": r["terminal_extra"]}
                     for r in accepted]
        mtm_dd = concurrent_mtm_max_dd(positions) if positions else float("nan")

        out = {
            "n_events": n_ev, "n_tokens": len(recs),
            "n_accepted_tokens": len(accepted), "n_capped_out": len(capped_idx),
            "peak_concurrent": peak,
        }
        if n_ev:
            out.update({
                "event_mean": float(np.mean(arr)),
                "event_median": float(np.median(arr)),
                "win_rate": float(np.mean(arr > 0)),
                "oos_wr_walk_forward": _oos_wr_walk_forward(arr, ev_pairs),
                "dsr_prob": _dsr_prob(arr, n_trials=N_TRIALS_DSR),
                "monte_carlo": _monte_carlo(acct_seq),
                "acct_event_mean": float(np.mean(acct_seq)) if acct_seq.size else None,
                "acct_total_return": float(np.sum(acct_seq)) if acct_seq.size else None,
                "realized_concurrent_mtm_max_dd": mtm_dd,
                "underperf_vs_ctrl_mean": float(np.mean(ev_under)) if ev_under else None,
                "beats_control": bool(ev_under and float(np.mean(ev_under)) > 0),
            })
        return out, dict(zip(ev_ids, ev_rets))

    stats_2a, ev_2a = {}, {}
    for v in EXITS_2A:
        stats_2a[v], ev_2a[v] = variant_stats(recs_2a[v], side=-1)
    stats_2b, ev_2b = {}, {}
    for h in HORIZONS_2B_D:
        stats_2b[f"{h}d"], ev_2b[f"{h}d"] = variant_stats(recs_2b[h], side=+1)

    def pbo_for(ev_maps: dict) -> float:
        common = None
        for m in ev_maps.values():
            ids = set(m)
            common = ids if common is None else (common & ids)
        common = sorted(common or [])
        if len(common) < 4:
            return float("nan")
        matrix = np.array([[m[e] for m in ev_maps.values()] for e in common],
                          dtype=float)
        return _pbo_across_horizons(matrix)

    pbo_2a, pbo_2b = pbo_for(ev_2a), pbo_for(ev_2b)

    def decide(stats: dict, pbo_value: float) -> tuple[str, str, dict]:
        ns = {v: s["n_events"] for v, s in stats.items()}
        if max(ns.values() or [0]) == 0:
            return ("INSUFFICIENT_DATA", "zero covered events on every variant",
                    {"n_events_by_variant": ns})
        evaluable = {v: ns[v] >= MC_MIN_TRADES for v in stats}
        detail = {"n_events_by_variant": ns, "evaluable": evaluable, "pbo": pbo_value}
        if not any(evaluable.values()):
            return ("INSUFFICIENT_DATA",
                    f"no variant reaches the frozen MC floor of {MC_MIN_TRADES} "
                    f"independent events (n={ns}); gates not evaluable -> fail closed",
                    detail)
        go, nogo = [], []
        for v, s in stats.items():
            if not evaluable[v]:
                continue
            mc = s.get("monte_carlo", {})
            oos = s.get("oos_wr_walk_forward")
            dsr = s.get("dsr_prob")
            mtm = s.get("realized_concurrent_mtm_max_dd")
            checks = {
                "event_mean>0": s.get("event_mean") is not None and s["event_mean"] > 0,
                "wr>=0.55": s.get("win_rate") is not None and s["win_rate"] >= MIN_OOS_WR,
                "oos_wr>=0.55": oos is not None and np.isfinite(oos) and oos >= MIN_OOS_WR,
                "dsr>=0.10": dsr is not None and np.isfinite(dsr) and dsr >= MIN_DSR,
                "pbo<=0.5": np.isfinite(pbo_value) and pbo_value <= MAX_PBO,
                "mc_P>0>=0.95": mc.get("p_total_positive") is not None
                                and mc["p_total_positive"] >= 0.95,
                "mc_maxDD_p95<=0.25": mc.get("max_drawdown_p95") is not None
                                      and mc["max_drawdown_p95"] <= 0.25,
                "realized_mtm_dd<=0.25": mtm is not None and np.isfinite(mtm) and mtm <= 0.25,
                "beats_control": bool(s.get("beats_control")),
            }
            detail[v] = checks
            passed = all(checks.values())
            if passed:
                go.append(v)
            else:
                nogo.append(f"{v}: failed {[k for k, ok in checks.items() if not ok]}")
        if go:
            return ("GO", f"all frozen gates pass at {go}", detail)
        return ("NO_GO", "no variant clears all frozen gates. " + " | ".join(nogo), detail)

    v2a, r2a, d2a = decide(stats_2a, pbo_2a)
    v2b, r2b, d2b = decide(stats_2b, pbo_2b)

    sens_summary = {
        f"half_spread_{hs:g}": {f"{h}d": {
            "n_tokens": len(sens_2b[hs][h]),
            "token_mean": float(np.mean(sens_2b[hs][h])) if sens_2b[hs][h] else None,
        } for h in HORIZONS_2B_D} for hs in HALF_SPREAD_SENSITIVITY}

    return {
        "screen": "delisting-forced-flow (2026-07-16)",
        "preregistration": "_workspace/strategy_pipeline/15a_screen_delisting.md",
        "sample_period_announcements": [
            datetime.fromtimestamp(WIN_LO, tz=timezone.utc).strftime("%Y-%m-%d"),
            datetime.fromtimestamp(WIN_HI, tz=timezone.utc).strftime("%Y-%m-%d")],
        "archive": {"binance_articles": len(articles),
                    "bybit_delisting_articles_crosscheck_only": len(bybit_arch),
                    "title_class_counts": title_counts},
        "events_qualifying": len(events),
        "event_list": [{"date": e["date"], "ann_utc": datetime.fromtimestamp(
            e["ann_ts"], tz=timezone.utc).strftime("%Y-%m-%d %H:%M"),
            "n_tokens": len(e["bases"]), "bases": e["bases"]} for e in events],
        "costs": {"fee_futures_taker_per_side": fee_fut,
                  "fee_bitget_spot_taker_per_side": fee_spot_bitget,
                  "slip_per_side": slip, "half_spread_2b_per_side": HALF_SPREAD_2B,
                  "roundtrip_2a": 2 * (fee_fut + slip),
                  "roundtrip_2b": 2 * (fee_spot_bitget + slip + HALF_SPREAD_2B)},
        "funding_source_counts": funding_sources,
        "exclusions": excl,
        "sizing": {"stake_frac": STAKE_FRAC, "max_concurrent": MAX_CONCURRENT},
        "n_trials_dsr": N_TRIALS_DSR,
        "sub_claims": {
            "2a_perp_short": {"stats_by_variant": stats_2a, "pbo": pbo_2a,
                              "verdict": v2a, "reason": r2a, "gate_detail": d2a},
            "2b_spot_bounce": {"stats_by_variant": stats_2b, "pbo": pbo_2b,
                               "verdict": v2b, "reason": r2b, "gate_detail": d2b,
                               "spread_sensitivity_nongating": sens_summary},
        },
        "thresholds": {"MIN_DSR": MIN_DSR, "MAX_PBO": MAX_PBO, "MIN_OOS_WR": MIN_OOS_WR,
                       "MC_MIN_TRADES": MC_MIN_TRADES, "MC_P>0>=": 0.95,
                       "maxDD_p95<=": 0.25, "realized_mtm_dd<=": 0.25},
    }


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, pd.DataFrame):
        return f"<df:{len(o)}>"
    return str(o)


if __name__ == "__main__":
    offline = "--offline" in sys.argv
    if "--harvest-announcements" in sys.argv:
        arts = harvest_announcements(offline)
        print(f"harvested {len(arts)} articles")
        sys.exit(0)
    limit = None
    for i, a in enumerate(sys.argv):
        if a == "--limit-events" and i + 1 < len(sys.argv):
            limit = int(sys.argv[i + 1])
    out = run_screen(offline=offline, limit_events=limit)
    print(json.dumps(out, indent=2, default=_json_default))
