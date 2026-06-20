"""Liquidation-cascade forward-harvester (WS3c) — keyless Binance force-order stream.

WHY: liquidation cascades are a non-price, bursty market-structure signal (forced
deleveraging) that no prior screen covered. There is NO public REST endpoint for
aggregated liquidations (Binance/Bybit removed them) and we hold no CoinGlass/Coinalyze
key, so the only free source is the live websocket force-order stream. History therefore
MUST be accumulated forward — this runs continuously and appends HOURLY aggregates to
data/liquidations_history.jsonl so a frozen-gate screen ("do liquidation spikes predict
forward returns / mean-reversion?") becomes possible after enough OOS accrues (months).

Side mapping (Binance): a force order with side SELL liquidates LONGs; side BUY liquidates
SHORTs. usd = qty * price. Read-only w.r.t. the bot; touches no order path. Fail-safe:
reconnects with backoff, never hard-crashes, flushes completed hours periodically.

HONEST PRIOR: expected NO_EDGE (use as a vol/timing GATE at best, not alpha), capital-capped
at ~$1,300. This only builds the dataset; nothing is wired to the live bot.

Run (continuous): python scripts/harvest_liquidations.py
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HIST = ROOT / "data" / "liquidations_history.jsonl"
STATUS = ROOT / "data" / "liquidations_status.json"
WS_URL = "wss://fstream.binance.com/ws/!forceOrder@arr"
FLUSH_EVERY_SEC = 120  # check for completed hours this often
RECONNECT_MAX_BACKOFF = 60.0


def _hour_key(ts_ms: float) -> int:
    """UTC hour bucket (unix seconds at the top of the hour)."""
    return int(ts_ms // 1000 // 3600 * 3600)


class _Buckets:
    """hour -> symbol -> {long_usd, short_usd, count}; plus an ALL market-wide aggregate."""

    def __init__(self) -> None:
        self.data: dict[int, dict[str, dict[str, float]]] = defaultdict(
            lambda: defaultdict(lambda: {"long_usd": 0.0, "short_usd": 0.0, "count": 0.0})
        )
        self.total_events = 0

    def add(self, hour: int, symbol: str, side: str, usd: float) -> None:
        for key in (symbol, "ALL"):
            cell = self.data[hour][key]
            if side == "SELL":  # long position liquidated
                cell["long_usd"] += usd
            else:  # BUY -> short position liquidated
                cell["short_usd"] += usd
            cell["count"] += 1
        self.total_events += 1

    def flush_completed(self, now_hour: int) -> int:
        """Append rows for every hour strictly before now_hour; drop them. Returns rows written."""
        done = sorted(h for h in self.data if h < now_hour)
        rows = 0
        if done:
            with HIST.open("a", encoding="utf-8") as f:
                for h in done:
                    for sym, cell in self.data[h].items():
                        f.write(
                            json.dumps(
                                {
                                    "hour": h,
                                    "symbol": sym,
                                    "long_usd": round(cell["long_usd"], 2),
                                    "short_usd": round(cell["short_usd"], 2),
                                    "count": int(cell["count"]),
                                }
                            )
                            + "\n"
                        )
                        rows += 1
                    del self.data[h]
        return rows


def _write_status(connected: bool, buckets: _Buckets, last_evt_ts: float) -> None:
    try:
        STATUS.write_text(
            json.dumps(
                {
                    "updated": time.time(),
                    "connected": connected,
                    "open_hours": len(buckets.data),
                    "total_events": buckets.total_events,
                    "last_event_ts": last_evt_ts,
                }
            ),
            encoding="utf-8",
        )
    except Exception:
        pass


async def _run_once(buckets: _Buckets) -> None:
    import websockets

    async with websockets.connect(WS_URL, ping_interval=20, ping_timeout=20) as ws:
        print(f"[liq] connected {WS_URL}", flush=True)
        last_flush = time.time()
        last_evt = 0.0
        _write_status(True, buckets, last_evt)
        while True:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=FLUSH_EVERY_SEC)
                msg = json.loads(raw)
                o = msg.get("o") or {}
                sym = o.get("s")
                side = o.get("S")
                qty = float(o.get("q", 0) or 0)
                # average fill price preferred; fall back to order price
                px = float(o.get("ap") or o.get("p") or 0) or 0.0
                ts = float(o.get("T") or msg.get("E") or time.time() * 1000)
                if sym and side and qty > 0 and px > 0:
                    buckets.add(_hour_key(ts), sym, side, qty * px)
                    last_evt = ts / 1000.0
            except asyncio.TimeoutError:
                pass  # no liquidations in the window — normal during calm
            now = time.time()
            if now - last_flush >= FLUSH_EVERY_SEC:
                n = buckets.flush_completed(_hour_key(now * 1000))
                if n:
                    print(
                        f"[liq] flushed {n} hourly rows -> {HIST.name} "
                        f"(events so far={buckets.total_events})",
                        flush=True,
                    )
                _write_status(True, buckets, last_evt)
                last_flush = now


async def main() -> None:
    HIST.parent.mkdir(parents=True, exist_ok=True)
    buckets = _Buckets()
    backoff = 2.0
    print("[liq] harvester starting (Ctrl-C to stop)", flush=True)
    while True:
        try:
            await _run_once(buckets)
            backoff = 2.0
        except Exception as e:  # noqa: BLE001 — fail-safe: log + reconnect, never die
            _write_status(False, buckets, 0.0)
            print(
                f"[liq] disconnected ({type(e).__name__}: {e}); reconnecting in {backoff:.0f}s",
                flush=True,
            )
            try:
                buckets.flush_completed(_hour_key(time.time() * 1000))
            except Exception:
                pass
            await asyncio.sleep(backoff)
            backoff = min(RECONNECT_MAX_BACKOFF, backoff * 2)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("[liq] stopped", flush=True)
