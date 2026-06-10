"""TradingView public chart-websocket OHLCV client (keyless, read-only).

WHY: the TradingView MCP server (Node, session-side tooling for Claude) cannot
page history — its get_ohlcv returns only the latest N bars and a deep pull
would have to transit the LLM context. This client speaks the same public chart
websocket TradingView's own anonymous web UI uses, so research scripts can pull
deep history (verified 2,600+ daily bars for CRYPTOCAP aggregates, 2026-06-11
probe) straight to parquet. Read-only market data; no account, no orders; one
short-lived connection per fetch. RESEARCH-ONLY: nothing in the live bot path
imports this module.

Protocol: frames are "~m~<len>~m~<json>"; heartbeats "~m~<len>~m~~h~<n>" must
be echoed back verbatim. Flow: set_auth_token(anonymous) ->
chart_create_session -> resolve_symbol -> create_series(countback) -> collect
bars from timescale_update/du messages until series_completed.

Cache: data/tv_cache/{SYMBOL ':'->'_'}_{resolution}.parquet with the house
OHLCV schema [ts unix-sec int64, open, high, low, close, volume] (volume 0.0
where TradingView omits it, e.g. some index series). The developing (partial)
final bar is dropped by fetch_ohlcv — TV serves it mid-period.

ToS note: keyless public endpoints with browser-shaped headers; TV may block
the IP, in which case fetches fail loudly and the forward harvester degrades
to CoinGecko-only. Cadence is polite (manual backfills at 1 fetch/sec).
"""

from __future__ import annotations

import asyncio
import json
import random
import re
import string
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
CACHE_DIR = ROOT / "data" / "tv_cache"
WS_URL = "wss://data.tradingview.com/socket.io/websocket"
ANON_TOKEN = "unauthorized_user_token"  # noqa: S105 — TradingView's public anonymous token, not a secret
COLS = ["ts", "open", "high", "low", "close", "volume"]
# TV's create_series wants intraday resolutions as bare minutes ("60", not "1h")
_TV_RES = {
    "1m": "1",
    "3m": "3",
    "5m": "5",
    "15m": "15",
    "30m": "30",
    "45m": "45",
    "1h": "60",
    "2h": "120",
    "3h": "180",
    "4h": "240",
}

_FRAME_SPLIT = re.compile(r"~m~\d+~m~")


def tv_resolution(resolution: str) -> str:
    return _TV_RES.get(resolution, resolution)


def _res_seconds(resolution: str) -> int | None:
    if resolution in _TV_RES:
        return int(_TV_RES[resolution]) * 60
    if resolution.isdigit():
        return int(resolution) * 60
    return {"1D": 86400, "D": 86400, "1W": 604800}.get(resolution)


def drop_partial_last(df: pd.DataFrame, resolution: str, now_ts: float | None = None):
    """Drop the final bar when its period hasn't completed — TV serves the
    developing bar, whose 'close' is just the live price mid-period."""
    secs = _res_seconds(resolution)
    if df is None or not len(df) or secs is None:
        return df
    now_ts = time.time() if now_ts is None else now_ts
    if int(df["ts"].iloc[-1]) + secs > now_ts:
        return df.iloc[:-1].reset_index(drop=True)
    return df


def frame_msg(obj: dict) -> str:
    s = json.dumps(obj, separators=(",", ":"))
    return f"~m~{len(s)}~m~{s}"


def split_frames(raw: str) -> list[str]:
    return [p for p in _FRAME_SPLIT.split(raw) if p]


def is_heartbeat(part: str) -> bool:
    return part.startswith("~h~")


class SeriesCollector:
    """Accumulates OHLCV rows from chart-session messages (pure, testable)."""

    def __init__(self, series_id: str = "sds_1"):
        self.bars: list[list[float]] = []
        self.done = False
        self.error: str | None = None
        self._sid = series_id

    def feed(self, part: str) -> None:
        try:
            msg = json.loads(part)
        except (ValueError, TypeError):
            return
        if not isinstance(msg, dict):
            return
        m = msg.get("m")
        if m in ("timescale_update", "du"):
            try:
                rows = (msg.get("p") or [None, {}])[1].get(self._sid, {}).get("s", [])
            except (AttributeError, IndexError, TypeError):
                rows = []
            for row in rows:
                v = row.get("v") if isinstance(row, dict) else None
                # null PRICE fields would silently become 0.0 closes downstream;
                # only the volume slot (v[5]) may legitimately be absent/None
                if not v or len(v) < 5 or any(x is None for x in v[:5]):
                    continue
                bar = [float(x) for x in v[:5]]
                bar.append(float(v[5]) if len(v) > 5 and v[5] is not None else 0.0)
                self.bars.append(bar)
        elif m == "series_completed":
            self.done = True
        elif m in ("symbol_error", "series_error", "critical_error", "protocol_error"):
            self.error = part[:300]
            self.done = True


def to_frame(bars: list[list[float]]) -> pd.DataFrame:
    if not bars:
        return pd.DataFrame(columns=COLS)
    df = pd.DataFrame(bars, columns=COLS)
    df["ts"] = df["ts"].astype("int64")
    # keep the LAST-received version of a bar (du updates supersede the initial
    # timescale_update snapshot), then stable-sort by time
    df = df.drop_duplicates("ts", keep="last")
    return df.sort_values("ts", kind="stable").reset_index(drop=True)


def cache_path(symbol: str, resolution: str) -> Path:
    safe = symbol.replace(":", "_").replace("/", "-")
    return CACHE_DIR / f"{safe}_{resolution}.parquet"


def save_cache(df: pd.DataFrame, symbol: str, resolution: str) -> Path:
    p = cache_path(symbol, resolution)
    p.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(p, index=False)
    return p


def load_cache(symbol: str, resolution: str) -> pd.DataFrame | None:
    p = cache_path(symbol, resolution)
    return pd.read_parquet(p) if p.exists() else None


async def _fetch_async(symbol: str, resolution: str, countback: int, timeout: float) -> list:
    import websockets

    hdrs = {"Origin": "https://www.tradingview.com"}
    try:
        ws = await websockets.connect(WS_URL, extra_headers=hdrs, max_size=None)
    except TypeError:  # websockets>=14 renamed extra_headers -> additional_headers
        ws = await websockets.connect(WS_URL, additional_headers=hdrs, max_size=None)
    coll = SeriesCollector()
    try:
        cs = "cs_" + "".join(random.choices(string.ascii_lowercase + string.digits, k=12))

        async def send(m: str, p: list) -> None:
            await ws.send(frame_msg({"m": m, "p": p}))

        await send("set_auth_token", [ANON_TOKEN])
        await send("chart_create_session", [cs, ""])
        spec = "=" + json.dumps({"symbol": symbol, "adjustment": "splits"})
        await send("resolve_symbol", [cs, "sds_sym_1", spec])
        await send(
            "create_series",
            [cs, "sds_1", "s1", "sds_sym_1", tv_resolution(resolution), int(countback), ""],
        )

        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while not coll.done and loop.time() < deadline:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=10)
            except asyncio.TimeoutError:
                break
            text = raw if isinstance(raw, str) else raw.decode()
            for part in split_frames(text):
                if is_heartbeat(part):
                    await ws.send(f"~m~{len(part)}~m~{part}")
                else:
                    coll.feed(part)
    finally:
        await ws.close()
    if coll.error:
        raise RuntimeError(f"TV series error for {symbol}: {coll.error}")
    if not coll.done:
        raise RuntimeError(
            f"TV fetch incomplete for {symbol}: {len(coll.bars)} bars, no series_completed"
        )
    return coll.bars


def fetch_ohlcv(
    symbol: str, resolution: str = "1D", countback: int = 300, timeout: float = 40.0
) -> pd.DataFrame:
    """Fetch the latest `countback` bars for an EXCHANGE:TICKER symbol."""
    if ":" not in symbol:
        raise ValueError(f"symbol must be EXCHANGE:TICKER, got {symbol!r}")
    if countback <= 0:
        raise ValueError(f"countback must be positive, got {countback}")
    bars = asyncio.run(_fetch_async(symbol, resolution, countback, timeout))
    return drop_partial_last(to_frame(bars), resolution)
