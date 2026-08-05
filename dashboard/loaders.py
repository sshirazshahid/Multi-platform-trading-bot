"""JSON / warehouse data loaders for the dashboard package."""
from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from loguru import logger

from dashboard.fetcher import LiveFetcher

from dashboard.state import (
    BOLD,
    DIM,
    GOLD,
    GREEN,
    RED,
    YELLOW,
    _file_cache,
)

def load_positions(fetcher: "LiveFetcher" = None):
    all_open   = []
    all_closed = []
    seen_ids   = set()
    is_live    = not load_mode()  # True when DRY_RUN=false (LIVE mode)

    def _ingest(data):
        if not data:
            return
        for pos in data.get("open", []):
            pid = pos.get("id", "")
            if pid in seen_ids:
                continue
            # LIVE mode: skip paper trades — only show real positions
            # Default False: positions missing the key are assumed real (not paper)
            if is_live and pos.get("paper_trade", False):
                continue
            seen_ids.add(pid)
            all_open.append(pos)
        for pos in data.get("closed", []):
            pid = pos.get("id", "")
            if pid in seen_ids:
                continue
            # LIVE mode: skip paper trades in history too
            if is_live and pos.get("paper_trade", False):
                continue
            seen_ids.add(pid)
            all_closed.append(pos)

    _ingest(_file_cache.load("data/positions.json"))
    # 2026-05-31: in PAPER, scope the dashboard to the single active paper
    # account (data/positions.json only). The profile sims, the operator
    # positions_extra warehouse rows, and the real-exchange snapshot below are
    # merged ONLY in LIVE — in PAPER they polluted the stats (showed All-Time
    # +$77 / 187 trades vs the true paper -$31.27 / 25) and rendered the 15 real
    # spot holdings as phantom open positions on a paper account.
    if is_live:
        for profile in ("conservative", "moderate", "aggressive"):
            _ingest(_file_cache.load("data/profiles/{}/positions.json".format(profile)))

    # 2026-04-30: external "extras" feed for closed trades that the bot's
    # tracker doesn't have in memory (e.g. closes that happened while the
    # bot was running but the runtime path didn't reach tracker._closed,
    # so tracker._save() overwrites direct positions.json edits within
    # seconds). The bot NEVER writes to data/positions_extra.json — it's
    # operator-owned.
    #
    # Format: same as positions.json — `{"open": [...], "closed": [...]}`.
    # Only `closed` is read here; `open` is ignored to prevent confusion
    # about non-existent positions.
    #
    # Dedup is by (exchange, symbol, side, close_time within ±60s) — NOT
    # by id — because the extras file is typically built from the
    # warehouse, which assigns synthetic IDs (e.g. WAREHOUSE-binance-...)
    # while positions.json uses the exchange's order_id. The plain
    # `seen_ids` dedup would let the SAME trade through both files under
    # different IDs and double-count totals.
    extras = _file_cache.load("data/positions_extra.json") if is_live else None  # 2026-05-31: PAPER shows only the active paper account
    if extras and isinstance(extras, dict):
        # Build a (exchange_lower, symbol, side, close_time_bucket) set
        # from already-loaded closed records. Bucket = close_time // 60
        # (1-minute bucket gives ±30s collision tolerance — tight enough
        # that two genuinely-different intraday round-trips of the same
        # symbol/side don't dedup, loose enough that a +/- few seconds
        # of timestamp drift between warehouse and positions.json doesn't
        # bypass the dedup).
        existing_close_keys = set()
        for p in all_closed:
            ct = p.get("close_time") or 0
            if not ct:
                continue
            existing_close_keys.add((
                (p.get("exchange") or "").lower(),
                p.get("symbol", ""),
                p.get("side", ""),
                int(float(ct) // 60),
            ))
        skipped_dup = 0
        ingested_new = 0
        for pos in (extras.get("closed", []) or []):
            ct = pos.get("close_time") or 0
            key = (
                (pos.get("exchange") or "").lower(),
                pos.get("symbol", ""),
                pos.get("side", ""),
                int(float(ct) // 60) if ct else None,
            )
            # Also fuzzy-check the adjacent buckets to absorb close_time
            # drift across the bucket boundary. (60s bucket size + ±1
            # bucket = up to ±60s real-world tolerance.)
            adjacent = {key}
            if ct:
                bucket = int(float(ct) // 60)
                adjacent.add((key[0], key[1], key[2], bucket - 1))
                adjacent.add((key[0], key[1], key[2], bucket + 1))
            if any(k in existing_close_keys for k in adjacent):
                skipped_dup += 1
                continue
            pid = pos.get("id", "")
            if pid in seen_ids:
                skipped_dup += 1
                continue
            seen_ids.add(pid)
            all_closed.append(pos)
            existing_close_keys.add(key)
            ingested_new += 1
        if ingested_new or skipped_dup:
            logger.debug(
                f"[Dashboard] positions_extra: +{ingested_new} new, "
                f"{skipped_dup} duplicates skipped")

    tracked_syms = set()
    for p in all_open:
        ex = (p.get("exchange") or "").lower()
        sym = p.get("symbol", "")
        side = p.get("side", "")
        tracked_syms.add((ex, sym, side))

    # 2026-04-16: Merge bot engine's universal exchange snapshot.
    # This is the authoritative cross-exchange view written by
    # bot_engine._fetch_all_exchange_positions(). It surfaces manual
    # positions even when the dashboard's own LiveFetcher failed to
    # initialize (e.g. Bybit API error at startup).
    try:
        ex_snap = _file_cache.load("data/exchange_positions.json") or {}
        snap_ts = float(ex_snap.get("ts") or 0)
        # Treat as fresh if written within last 5 minutes
        # 2026-05-31: merge the REAL exchange snapshot only in LIVE. In PAPER the
        # 15 real spot holdings must NOT appear as open positions on the paper account.
        if is_live and snap_ts and (time.time() - snap_ts) < 300:
            for ep in ex_snap.get("positions", []):
                ex = (ep.get("exchange") or "").lower()
                sym = ep.get("symbol", "")
                side = ep.get("side", "")
                if not ex or not sym or not side:
                    continue
                if (ex, sym, side) in tracked_syms:
                    continue
                # Normalize to dashboard position shape
                mapped = {
                    "id": ep.get("id") or "ENG-{}-{}-{}".format(ex, sym, side),
                    "exchange": ex.capitalize(),
                    "symbol": sym,
                    "side": side,
                    "market_type": ep.get("market_type", "futures"),
                    "strategy": "manual",
                    "entry_price": float(ep.get("entry_price") or 0),
                    "current_price": float(ep.get("current_price") or 0),
                    "size": float(ep.get("size") or 0),
                    "stop_loss": float(ep.get("stop_loss") or ep.get("liquidation_price") or 0),
                    "take_profit": float(ep.get("take_profit") or 0),
                    "leverage": int(ep.get("leverage") or 1),
                    "open_time": time.time(),
                    "paper_trade": False,
                    "_live_upnl": float(ep.get("pnl") or 0),
                    "_from_exchange": True,
                }
                all_open.append(mapped)
                tracked_syms.add((ex, sym, side))
    except Exception as _e:
        logger.debug(f"[Dashboard] exchange_positions snapshot merge: {_e}")

    # Merge live exchange positions not already tracked locally (LiveFetcher path)
    if fetcher:
        live_pos = fetcher.get_live_positions()
        for lp in live_pos:
            ex = (lp.get("exchange") or "").lower()
            sym = lp.get("symbol", "")
            side = lp.get("side", "")
            if (ex, sym, side) not in tracked_syms:
                all_open.append(lp)
                tracked_syms.add((ex, sym, side))

    all_closed.sort(key=lambda x: x.get("close_time", 0), reverse=True)
    return all_open, all_closed


def load_news():       return _file_cache.load("data/news_cache.json")
def load_auto_mut():   return _file_cache.load("data/auto_mutations.json")
def load_post_mortem():return _file_cache.load("data/post_mortem.json")
def load_risk_state(): return _file_cache.load("data/risk_state.json")


def load_warehouse_stats() -> dict:
    """Warehouse-backed stats for the dashboard.

    Reads data/warehouse.sqlite directly (no ORM, no core/__init__).
    Returns {} if the DB doesn't exist or an error occurs.

    2026-06-11: every query is scoped to the CURRENT operating mode — the
    trades table mixes PAPER and CONTROLLED_LIVE rows, and a PAPER dashboard
    must not blend live-era losses into PER-SYMBOL EDGE / LOSS-CLUSTER /
    SLIPPAGE (and vice versa when switching to LIVE). Also returns the
    mode's whole-trade net (realized + partial-TP legs) so the balances
    panel can cross-check the sim wallet against trade-history truth.
    """
    out: dict = {"per_symbol": [], "per_family": [], "slippage": {}}
    try:
        from config import DRY_RUN as _dr
        wh_mode = "PAPER" if _dr else "CONTROLLED_LIVE"
    except Exception:
        wh_mode = "PAPER"
    out["mode"] = wh_mode
    db_path = Path("data/warehouse.sqlite")
    if not db_path.exists():
        return out
    try:
        import sqlite3
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row

        # Per-symbol expectancy + PF (closed trades only, current mode).
        # Whole-trade PnL = realized_pnl + banked partial-TP leg, so partial-taken
        # winners are classified/summed correctly (item 2, 2026-07-07 backlog).
        rows = conn.execute(
            """SELECT symbol,
                      COUNT(*) AS n,
                      SUM(realized_pnl + COALESCE(partial_realized_pnl,0)) AS net,
                      SUM(CASE WHEN realized_pnl + COALESCE(partial_realized_pnl,0) > 0
                               THEN realized_pnl + COALESCE(partial_realized_pnl,0) ELSE 0 END) AS gw,
                      SUM(CASE WHEN realized_pnl + COALESCE(partial_realized_pnl,0) < 0
                               THEN -(realized_pnl + COALESCE(partial_realized_pnl,0)) ELSE 0 END) AS gl,
                      SUM(CASE WHEN realized_pnl + COALESCE(partial_realized_pnl,0) > 0
                               THEN 1 ELSE 0 END) AS wins
                 FROM trades
                WHERE status='CLOSED' AND mode = ?
                GROUP BY symbol
                ORDER BY n DESC
                LIMIT 8""", (wh_mode,)
        ).fetchall()
        out["per_symbol"] = [dict(r) for r in rows]

        # Current-boot cohort (2026-07-10): WR/n/pnl of trades ENTERED since
        # the live process boot (fallback: last 6h) — the 30d/all-time
        # aggregates blend dead-regime cohorts and understate the current
        # geometry's WR. Reuses the goal-progress report's boot detection.
        try:
            import time as _t

            from scripts.report_goal_progress import _boot_epoch
            _boot = _boot_epoch(Path(".").resolve())
            _since = _boot if _boot is not None else _t.time() - 6 * 3600
            # strategy_family='deep_breakout' excluded (2026-07-11): the
            # active 3R lane is ~33% WR by design — a separate cohort that
            # must not pollute the THIS-BOOT accuracy-band read.
            cb = conn.execute(
                """SELECT COUNT(*) AS n,
                          SUM(CASE WHEN realized_pnl + COALESCE(partial_realized_pnl,0) > 0
                                   THEN 1 ELSE 0 END) AS wins,
                          SUM(realized_pnl + COALESCE(partial_realized_pnl,0)) AS net
                     FROM trades
                    WHERE status='CLOSED' AND mode = ? AND ts_entry >= ?
                      AND COALESCE(strategy_family,'') <> 'deep_breakout'""",
                (wh_mode, _since)).fetchone()
            out["current_boot"] = {
                "n": int(cb["n"] or 0),
                "wins": int(cb["wins"] or 0),
                "net": float(cb["net"] or 0.0),
                "src": "boot" if _boot is not None else "last 6h",
            }
        except Exception:
            pass

        # Per-strategy-family whole-trade net PnL + count (current mode)
        fam = conn.execute(
            """SELECT COALESCE(strategy_family,'unknown') AS fam,
                      COUNT(*) AS n,
                      SUM(realized_pnl + COALESCE(partial_realized_pnl,0)) AS net,
                      SUM(CASE WHEN realized_pnl + COALESCE(partial_realized_pnl,0) > 0
                               THEN 1 ELSE 0 END) AS wins
                 FROM trades
                WHERE status='CLOSED' AND mode = ?
                GROUP BY fam
                ORDER BY n DESC
                LIMIT 6""", (wh_mode,)
        ).fetchall()
        out["per_family"] = [dict(r) for r in fam]

        # Slippage vs fee ratio — only rows where both are non-null (current mode)
        slip = conn.execute(
            """SELECT AVG(slippage) AS avg_slip,
                      AVG(fee)      AS avg_fee,
                      COUNT(*)      AS n
                 FROM trades
                WHERE status='CLOSED' AND slippage IS NOT NULL AND fee IS NOT NULL
                  AND mode = ?""", (wh_mode,)
        ).fetchone()
        if slip:
            out["slippage"] = dict(slip)

        # Counts for header line (current mode) + whole-trade net for the
        # balances-panel cross-check (realized + partial-TP legs)
        tot = conn.execute(
            """SELECT COUNT(*) AS c,
                      SUM(realized_pnl + COALESCE(partial_realized_pnl, 0)) AS net
                 FROM trades WHERE status='CLOSED' AND mode = ?""", (wh_mode,)
        ).fetchone()
        cnd = conn.execute(
            "SELECT COUNT(*) AS c FROM candidates"
        ).fetchone()
        out["total_trades"] = int(tot["c"]) if tot else 0
        out["mode_net"] = float(tot["net"] or 0.0) if tot else 0.0
        out["total_candidates"] = int(cnd["c"]) if cnd else 0
        conn.close()
    except Exception as e:
        out["_error"] = str(e)
    return out


def load_block_reasons(tail_lines: int = 800) -> dict:
    """Parse today's bot log tail to count recent block reasons.

    Returns {"BLACKLIST": N, "DYN_BL": N, "UNIVERSE": N, "RISK": N,
             "HOUR": N, "TIER": N, "RR": N, "OTHER": N, "total": N,
             "signal_empty": N, "algo_zero": N}

    Used by the dashboard "WHY NO TRADES" panel to expose why the bot
    is idle so the user doesn't have to tail the log manually.
    """
    out = {
        "BLACKLIST": 0, "DYN_BL": 0, "UNIVERSE": 0, "RISK": 0,
        "HOUR": 0, "TIER": 0, "RR": 0, "SHORTS": 0, "LEV": 0,
        "OTHER": 0, "total": 0,
        "signal_empty": 0, "algo_zero": 0, "actions": 0,
    }
    try:
        log_path = Path("logs") / ("bot_" + datetime.now().strftime("%Y-%m-%d") + ".log")
        if not log_path.exists():
            return out
        with log_path.open("r", encoding="utf-8", errors="replace") as f:
            # Read last ~tail_lines lines cheaply
            try:
                f.seek(0, 2)
                size = f.tell()
                # ~200 chars per line avg → read that many bytes
                back = min(size, tail_lines * 260)
                f.seek(max(0, size - back))
                f.readline()  # discard partial line
                lines = f.readlines()[-tail_lines:]
            except Exception:
                f.seek(0)
                lines = f.readlines()[-tail_lines:]
        for ln in lines:
            if "BLOCKED by" in ln or "BLOCKED:" in ln or "blocked by" in ln.lower():
                out["total"] += 1
                lnl = ln.lower()
                if "blacklist_hard" in lnl or "hard blacklist" in lnl:
                    out["BLACKLIST"] += 1
                elif "dyn" in lnl and "blacklist" in lnl:
                    out["DYN_BL"] += 1
                elif "universe" in lnl or "spread" in lnl or "atr" in lnl or "depth" in lnl:
                    out["UNIVERSE"] += 1
                elif "risk" in lnl or "halt" in lnl or "daily loss" in lnl:
                    out["RISK"] += 1
                elif "hour" in lnl or "blocked hour" in lnl:
                    out["HOUR"] += 1
                elif "tier" in lnl or "leverage tier" in lnl:
                    out["TIER"] += 1
                elif "r:r" in lnl or "risk/reward" in lnl or "rr<" in lnl:
                    out["RR"] += 1
                elif "short" in lnl:
                    out["SHORTS"] += 1
                elif "leverage cap" in lnl:
                    out["LEV"] += 1
                else:
                    out["OTHER"] += 1
            if ("Claude returned empty" in ln or "Signal returned empty" in ln
                    or "No trades:" in ln or '"actions":[]' in ln):
                out["signal_empty"] += 1
            if "Algorithmic fallback: 0 actions" in ln:
                out["algo_zero"] += 1
            if "Algorithmic fallback:" in ln and " actions" in ln:
                try:
                    n = int(ln.split("Algorithmic fallback:")[1].split(" actions")[0].strip())
                    out["actions"] += n
                except Exception:
                    pass
    except Exception:
        pass
    return out


def _hour_class(hour: int) -> tuple:
    """Classify a UTC hour against config trading gates.
    Returns (label, ansi color)."""
    try:
        from config import (
            ALLOWED_HOURS_UTC, PEAK_HOURS_UTC,
            WARMUP_HOURS_UTC, BLOCKED_HOURS_UTC,
        )
    except Exception:
        return ("?", DIM)
    if hour in BLOCKED_HOURS_UTC:
        return ("BLOCKED", RED)
    # 2026-06-11 profit-only hour gate — mirror bot_engine._classify_hour
    # so the operator sees WHY the bot isn't entering during most hours.
    try:
        from config import HOUR_GATE_PROFIT_ONLY
        if HOUR_GATE_PROFIT_ONLY:
            p = Path("data/hour_gate_evidence.json")
            if p.exists() and (time.time() - p.stat().st_mtime) / 86400 <= 14:
                _prof = {int(h) for h in (
                    json.loads(p.read_text(encoding="utf-8")).get("profitable") or [])}
                if _prof and hour not in _prof:
                    return ("BLOCKED (hour not profitable)", RED)
    except Exception:
        pass
    if hour in PEAK_HOURS_UTC:
        return ("PEAK",    GOLD + BOLD)
    if hour in WARMUP_HOURS_UTC:
        return ("WARMUP",  YELLOW)
    # Default: allowed (any hour not explicitly blocked/warmup/peak)
    return ("ALLOWED", GREEN)


def load_mode():
    try:
        from config import DRY_RUN
        return DRY_RUN
    except Exception:
        return True
