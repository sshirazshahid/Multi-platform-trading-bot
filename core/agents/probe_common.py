"""probe_common — shared machinery for the LOG-ONLY shadow probe agents.

Single home for the code that was line-for-line duplicated across
listing_short / unlock_short / tsmom / breakout probe agents (and
cross-imported between them): pure indicator math, the Codex notational
sizing model, warehouse row writers (shadow_decisions + generic inserts),
and the barrier-probe tick/monitor skeleton shared by the tsmom and
breakout probes.

Everything here is strategy-NEUTRAL: no signal math, no frozen per-strategy
constants (stops, targets, score scales stay in each agent), and no order
path — the probes remain structurally incapable of placing an order.

Efficiency (2026-07-13): the eval/monitor helpers carry the expected-bar
no-op guard ported from core/deep_breakout_lane.py (_last_seen pattern):
per (symbol, timeframe), the OHLCV fetch is skipped while no NEW bar has
closed since the last evaluation. This only skips redundant work — closed
bars cannot change mid-bar, so every recorded output is identical.
"""

from __future__ import annotations

import bisect
from typing import Optional

from loguru import logger

from core.funding_history import load_realized_settlements

# ── Shared frozen constants (single source; agents re-export their own) ─────
FUNDING_SETTLE_S = 8 * 3600  # legacy 8h bucket width (listing/unlock probes only)
# Below this, a persisted last_funding_bucket is a pre-2026-07-28 8h bucket
# INDEX (~6.2e4), not a settlement timestamp (~1.78e9): ~28,800x apart.
_LEGACY_BUCKET_CEILING = 1e9
_FUNDING_HISTORY_WARNED: set = set()  # warn once per (venue, coin)
ATR_LEN = 14  # ta.atr(14) — Wilder RMA
RISK_PCT = 0.01  # Codex risk model: 1% equity risk (NOTATIONAL)
MAX_NOTIONAL_MULTIPLE = 2.0  # Codex cap: notional <= 2x equity (NOTATIONAL)


# ── Pure indicator / conversion math ─────────────────────────────────────────
def wilder_atr_last(candles, n: int = ATR_LEN) -> Optional[float]:
    """Last Wilder ATR(n) — TR(prev-close) smoothed by pandas
    ewm(alpha=1/n, adjust=False, min_periods=n). None until warm-up."""
    if n <= 0 or len(candles) < n:
        return None
    prev_close: Optional[float] = None
    rma: Optional[float] = None
    for c in candles:
        try:
            high, low, close = float(c[2]), float(c[3]), float(c[4])
        except (IndexError, TypeError, ValueError):
            return None
        tr = high - low
        if prev_close is not None:
            tr = max(tr, abs(high - prev_close), abs(low - prev_close))
        rma = tr if rma is None else rma + (tr - rma) / float(n)
        prev_close = close
    return rma


def codex_position_units(equity: float, entry_px: float, risk_distance: float) -> float:
    """Codex sizing (NOTATIONAL): min(1%-risk units, 2x-notional cap units)."""
    if equity <= 0 or entry_px <= 0 or risk_distance <= 0:
        return 0.0
    return min(equity * RISK_PCT / risk_distance, equity * MAX_NOTIONAL_MULTIPLE / entry_px)


def _pos_float(v) -> Optional[float]:
    """Coerce to a positive float, else None."""
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def iso_utc(ts) -> str:
    """Epoch seconds -> compact UTC stamp for heartbeat lines; 'none' if absent."""
    if ts is None:
        return "none"
    try:
        from datetime import datetime, timezone

        return datetime.fromtimestamp(int(ts), timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    except (OSError, OverflowError, TypeError, ValueError):
        return "none"


def unrealized_short_return(entry_px: float, mark_px: float) -> float:
    """Unrealized return of a SHORT marked at ``mark_px``: (entry - mark) / entry.
    Positive when price falls (short in profit), negative on a pump against it."""
    if not entry_px or entry_px <= 0:
        return 0.0
    return (float(entry_px) - float(mark_px)) / float(entry_px)


def concurrent_account_mtm(bars_by_pos: dict, stake_frac: float = 0.03):
    """Calendar-time concurrent account-MTM curve + max drawdown (auditor B1/B2).

    ``bars_by_pos`` maps proposal_id -> [(bar_ts, unrealized_short_ret), ...].
    At each calendar bar the account MTM is the sum, over positions OPEN at that
    bar (forward-filled from their last observed bar), of
    ``stake_frac * unrealized_short_ret`` — NOT the realized-return cumsum the
    frozen MC used (which understated the true drawdown ~2x). Drawdown is
    peak-to-trough of the equity curve (1 + MTM), with the pre-trade baseline
    equity 1.0 as the initial peak.

    Returns (series, max_drawdown) where series is [(bar_ts, account_mtm), ...].
    The default stake_frac mirrors the charter 3% per-event cap both
    short-probe callers pass explicitly.
    """
    # Per-position sorted bar timestamps + values, and active windows.
    prepared = {}
    all_ts: set = set()
    for pid, bars in bars_by_pos.items():
        if not bars:
            continue
        sb = sorted((int(t), float(v)) for t, v in bars)
        ts_list = [t for t, _ in sb]
        val_list = [v for _, v in sb]
        prepared[pid] = (ts_list, val_list, ts_list[0], ts_list[-1])
        all_ts.update(ts_list)
    if not all_ts:
        return [], 0.0

    series = []
    peak_equity = 1.0
    max_dd = 0.0
    for ts in sorted(all_ts):
        mtm = 0.0
        for ts_list, val_list, first_ts, last_ts in prepared.values():
            if ts < first_ts or ts > last_ts:
                continue  # position not open at this calendar bar
            idx = bisect.bisect_right(ts_list, ts) - 1  # last bar <= ts (forward-fill)
            if idx >= 0:
                mtm += stake_frac * val_list[idx]
        equity = 1.0 + mtm
        peak_equity = max(peak_equity, equity)
        dd = (peak_equity - equity) / peak_equity if peak_equity > 0 else 0.0
        max_dd = max(max_dd, dd)
        series.append((ts, mtm))
    return series, max_dd


# ── Warehouse writers ────────────────────────────────────────────────────────
def ensure_schema(warehouse, schema_sql: str) -> None:
    """Apply a probe's CREATE TABLE IF NOT EXISTS script."""
    conn = warehouse._conn()
    conn.executescript(schema_sql)
    conn.commit()


def _insert(conn, table: str, row: dict, *, replace: bool = False) -> None:
    """Emit one INSERT on an EXISTING connection — no commit, so callers can
    compose several writes into one transaction (see write_entry_pair)."""
    cols = ", ".join(row.keys())
    ph = ", ".join("?" * len(row))
    verb = "INSERT OR REPLACE" if replace else "INSERT"
    conn.execute(f"{verb} INTO {table} ({cols}) VALUES ({ph})", tuple(row.values()))


def insert_row(warehouse, table: str, row: dict, *, replace: bool = False) -> None:
    """Insert one dict-shaped row (column order = dict order). ``replace``
    selects INSERT OR REPLACE for the probes whose rows may be re-written.

    Self-committing — correct for a STANDALONE row (an MTM bar, a probe-only
    SKIP row). An ENTRY writes two coupled rows and must use write_entry_pair
    instead, or a failure between them leaves an orphan decision."""
    conn = warehouse._conn()
    _insert(conn, table, row, replace=replace)
    conn.commit()


def _shadow_decision_row(*, ts, model_version, symbol, side, agent_id,
                         proposal_id, notional, entry_px, sl_px, tp_px, venue,
                         timeframe, horizon_bars) -> dict:
    """Build (never write) the shadow_decisions row — the single definition
    shared by write_shadow_decision and the transactional write_entry_pair."""
    return {
        "ts": int(ts),
        "model_version": model_version,
        "symbol": symbol,
        "side": side,
        "decision": "ALLOW",
        "p_win": None,
        "sim_pnl": None,
        "sim_r_multiple": None,
        "agent_id": agent_id,
        "proposal_id": proposal_id,
        "proposed_at": int(ts),
        "vetoed_by": None,
        "veto_reason": None,
        "projected_notional_current": float(notional),
        "projected_notional_alt": float(notional),
        "projected_pnl": None,
        "projected_fee": None,
        "entry_px": float(entry_px),
        "sl_px": float(sl_px),
        "tp_px": float(tp_px),
        "venue": venue,
        "timeframe": str(timeframe),
        "horizon_bars": int(horizon_bars),
        "label_status": "PENDING",
    }


def write_shadow_decision(warehouse, **kwargs) -> None:
    """One shadow_decisions row the keystone resolver (core/shadow_resolver.py)
    replays into shadow_outcomes. sim_pnl stays NULL — a probe does no PnL
    projection; the resolver owns the after-cost net. sl_px/tp_px of 0.0 mean
    "no barrier" (the resolver's sl>0/tp>0 checks never fire); the time exit
    lands at the horizon bar.

    Standalone write. An ENTRY pairs this row with a probe-table row and must
    go through write_entry_pair so a failure cannot orphan the decision."""
    insert_row(warehouse, "shadow_decisions", _shadow_decision_row(**kwargs))


def write_entry_pair(warehouse, *, probe_table: str, probe_row: dict,
                     decision: Optional[dict] = None,
                     decisions: Optional[list] = None,
                     replace: bool = False) -> None:
    """Write a probe ENTRY's rows ATOMICALLY — all of them or none.

    A probe entry spans two tables: the shadow_decisions row(s) the resolver
    replays into shadow_outcomes, and the probe-table row that holds the
    one-position occupancy slot. Written separately (each self-committing) a
    crash / lock / IntegrityError between them leaves an ORPHAN decision: the
    resolver still resolves it — so it counts toward the >= 30-RESOLVED
    promotion gate — while no probe row holds the slot, so the probe re-enters
    the same signal. That is evidence corruption, not just a lost row, which
    is why the write is transactional rather than merely reordered.

    ``decision`` is one write_shadow_decision kwargs dict (minus the
    warehouse); ``decisions`` takes several for a probe whose entry logs more
    than one row (the unlock probe's raw arm + its SL-guardian counterfactual
    must stand or fall together, or the arms disagree on what happened).
    ``probe_row`` is the probe table's dict-shaped row and ``replace`` mirrors
    insert_row's flag. Exceptions propagate after rollback — probe_tick logs
    and the bar is retried, which is correct: no half-written entry is left
    behind to be resolved.
    """
    rows = list(decisions or [])
    if decision is not None:
        rows.insert(0, decision)
    conn = warehouse._conn()
    # isolation_level=None -> autocommit; BEGIN IMMEDIATE takes the write lock
    # up front so the write cannot interleave with another writer.
    conn.execute("BEGIN IMMEDIATE")
    try:
        for d in rows:
            _insert(conn, "shadow_decisions", _shadow_decision_row(**d))
        _insert(conn, probe_table, probe_row, replace=replace)
        conn.commit()
    except BaseException:
        conn.rollback()
        raise


# ── Barrier-probe skeleton (tsmom / breakout) ────────────────────────────────
def closed_candles(ohlcv, venue: str, symbol: str, tf: str, bar_s: int,
                   need: int, now: int) -> list:
    """CLOSED bars for (symbol, timeframe), oldest-first. The forming bar is
    dropped so a signal never repaints."""
    since_ms = (now - need * bar_s) * 1000
    raw = ohlcv(venue, symbol, tf, since_ms) or []
    out = []
    for c in raw:
        try:
            bar_ts = int(c[0]) // 1000
            close = float(c[4])
        except (IndexError, TypeError, ValueError):
            continue
        if bar_ts + bar_s <= now and close > 0:
            out.append(c)
    out.sort(key=lambda c: c[0])
    return out


def probe_tick(agent, units, *, log_tag: str) -> dict:
    """One probe cycle: update MTM/hints on open holds FIRST (a barrier hit
    this bar frees the slot honestly), then evaluate the latest closed bar per
    unit. Returns a stats dict. NEVER raises upward. ``units`` is a list of
    (label, eval_args) pairs fed to agent._eval_entry(*eval_args, now)."""
    stats = {"evaluated": 0, "entered": 0, "mtm_rows": 0}
    now = int(agent._now())
    try:
        stats["mtm_rows"] = agent._monitor_open(now)
    except Exception as e:
        logger.debug(f"[{log_tag}] monitor error: {e}")
    for label, args in units:
        try:
            stats["entered"] += agent._eval_entry(*args, now)
            stats["evaluated"] += 1
        except Exception as e:
            logger.debug(f"[{log_tag}] {label} eval error: {e}")
    # ── Heartbeat (2026-07-20): probe silence must be diagnosable from the ──
    # boot log alone (breakout/unlock sat at 0 decision rows for days with no
    # way to tell "wiring broken" from "no trigger"). ONE line per tick.
    try:
        hb = getattr(agent, "_hb_totals", None)
        if hb is None:
            hb = agent._hb_totals = {"ticks": 0, "evaluated": 0, "entered": 0}
        hb["ticks"] += 1
        hb["evaluated"] += stats["evaluated"]
        hb["entered"] += stats["entered"]
        last_bar = max(agent._bar_seen.values(), default=None)
        logger.info(
            f"[{log_tag}] heartbeat: units={len(units)} "
            f"evaluated={stats['evaluated']} entered={stats['entered']} "
            f"mtm_rows={stats['mtm_rows']} totals(ticks={hb['ticks']} "
            f"evaluated={hb['evaluated']} entered={hb['entered']}) "
            f"last_bar_ts={iso_utc(last_bar)}")
    except Exception as e:  # the lane never raises for diagnostics
        logger.debug(f"[{log_tag}] heartbeat error: {e}")
    return stats


def eval_gate(agent, *, symbol: str, tf: str, bar_s: int, fetch_bars: int,
              min_bars: int, last_sql: str, last_params: tuple, hold_col: str,
              now: int) -> Optional[tuple]:
    """Shared pre-signal gate for the barrier probes. Returns
    (candles, latest_ts) when a NEW closed bar is ready to evaluate, else None.

    Order of checks (all outcome-identical to the pre-refactor inline code,
    just cheaper):
      1. slot occupancy on the candle-INDEPENDENT time bound — checked before
         any fetch, so a held slot costs one local query, no REST call;
      2. the expected-bar no-op guard (core/deep_breakout_lane.py pattern):
         skip the fetch while this (symbol, timeframe) was already evaluated
         at the current bar boundary; a venue that has not yet served the new
         closed bar is NOT marked, so it retries next tick;
      3. the already-acted / re-entry rules on the fetched latest closed bar.
    """
    last = agent._wh.query(last_sql, last_params)
    row = last[0] if last else None
    if row is not None:
        # ONE position per slot — the reference backtest's no-overlap rule.
        # Time bound: the position exits at the close of bar entry+hold, i.e.
        # signal_bar_ts + (hold+1) bars.
        time_free = int(row["signal_bar_ts"]) + (int(row[hold_col]) + 1) * bar_s
        if row["closed_hint_ts"] is None and now < time_free:
            return None  # occupied — never fetch for a held slot

    seen = agent._bar_seen
    key = (symbol, tf)
    # Open ts of the last completed bar on the epoch grid (deep_breakout_lane
    # expected-bar pattern).
    expected_bar = (now // bar_s - 1) * bar_s
    if seen.get(key, -1) >= expected_bar:
        return None  # this boundary already evaluated — cheap no-op

    candles = closed_candles(agent._ohlcv, agent._venue, symbol, tf, bar_s,
                             fetch_bars, now)
    if not candles:
        # The venue served NOTHING (transient error / empty response). This is
        # not evidence about history length, so the boundary stays unmarked and
        # the bar is retried next tick — marking it here silently DROPS a
        # signal bar until the next boundary. Same convention as
        # monitor_open_barriers' `if candles:` fetch gate below.
        return None
    if len(candles) < min_bars:
        # genuinely insufficient history — never guess a signal; it cannot
        # lengthen within this bar, so mark the boundary evaluated
        seen[key] = expected_bar
        return None
    latest_ts = int(candles[-1][0]) // 1000
    if seen.get(key, -1) >= latest_ts:
        return None  # venue hasn't served a NEW closed bar yet — retry
    seen[key] = latest_ts

    if row is not None:
        if latest_ts <= int(row["signal_bar_ts"]):
            return None  # this bar was already acted on — never re-signal it
        hint_ts = row["closed_hint_ts"]
        if hint_ts is not None and latest_ts <= int(hint_ts):
            return None  # re-entry earliest one bar AFTER the exit-hint bar
    return candles, latest_ts


def monitor_open_barriers(agent, now: int, *, probe_table: str, mtm_table: str,
                          rows_sql: str, frame_of) -> int:
    """Shared _monitor_open body for the barrier probes: per closed bar write
    an MTM row; flag an SL-first occupancy hint on a barrier touch; time-hint
    after the max-hold bar closes; else accrue funding. ``frame_of(row)``
    returns (timeframe, bar_s, hold_bars) for a row, or None to skip it.

    The OHLCV fetch is gated per (symbol, timeframe) by the expected-bar
    pattern (mid-bar fetches can only return the forming bar, which the
    no-repaint check drops anyway); the hint/funding logic below the fetch
    still runs every tick."""
    rows = agent._wh.query(rows_sql)
    written = 0
    conn = agent._wh._conn()
    for r in rows:
        frame = frame_of(r)
        if frame is None:
            continue
        tf, bar_s, hold_bars = frame
        entry_ts = int(r["signal_bar_ts"])
        entry_px = float(r["entry_px"])
        is_buy = r["side"] == "buy"
        sl, tp = float(r["sl_px"]), float(r["tp_px"])
        cap_open = entry_ts + int(hold_bars) * bar_s
        hinted = False

        last = agent._wh.query(
            f"SELECT MAX(bar_ts) AS m FROM {mtm_table} WHERE proposal_id=?",
            (r["proposal_id"],),
        )
        since = (int(last[0]["m"]) + 1) if last and last[0]["m"] is not None else entry_ts + 1
        expected_bar = (now // bar_s - 1) * bar_s
        gate_key = (r["symbol"], tf)
        if since <= min(now, cap_open) and agent._mon_seen.get(gate_key, -1) < expected_bar:
            candles = agent._ohlcv(agent._venue, r["symbol"], tf, since * 1000) or []
            if candles:
                agent._mon_seen[gate_key] = expected_bar
            for c in sorted(candles, key=lambda c: c[0]):
                try:
                    bar_ts = int(c[0]) // 1000
                    high, low, mark = float(c[2]), float(c[3]), float(c[4])
                except (IndexError, TypeError, ValueError):
                    continue
                if bar_ts <= entry_ts or bar_ts > cap_open:
                    continue
                if bar_ts + bar_s > now:
                    continue  # forming/unclosed bar — no repaint
                ur = (mark - entry_px) / entry_px if is_buy else (entry_px - mark) / entry_px
                conn.execute(
                    f"INSERT OR REPLACE INTO {mtm_table} "
                    "(proposal_id, bar_ts, mark_px, unrealized_ret) "
                    "VALUES (?,?,?,?)",
                    (r["proposal_id"], bar_ts, mark, ur),
                )
                written += 1
                # Occupancy hint, SL-FIRST on a both-barrier bar — the same
                # conservative tie-break the resolver applies. The resolved
                # outcome still comes ONLY from core/shadow_resolver.py.
                hit_sl = (low <= sl) if is_buy else (high >= sl)
                hit_tp = (high >= tp) if is_buy else (low <= tp)
                if hit_sl or hit_tp:
                    set_hint(
                        agent._wh, probe_table, r["proposal_id"], bar_ts,
                        "stop_loss" if hit_sl else "take_profit",
                    )
                    hinted = True
                    break
        if not hinted and now >= cap_open + bar_s:
            # max-hold bar has closed: the reference's time exit
            set_hint(agent._wh, probe_table, r["proposal_id"], cap_open, "time")
            hinted = True
        if not hinted:
            agent._accrue_funding(r, now)
    if written:
        conn.commit()
    return written


def set_hint(warehouse, table: str, pid: str, bar_ts: int, reason: str) -> None:
    """Occupancy bookkeeping ONLY (frees the one-position slot); the resolved
    outcome stays with core/shadow_resolver.py."""
    conn = warehouse._conn()
    conn.execute(
        f"UPDATE {table} SET closed_hint_ts=?, closed_hint_reason=? "
        "WHERE proposal_id=? AND closed_hint_ts IS NULL",
        (int(bar_ts), reason, pid),
    )
    conn.commit()


def accrue_funding(warehouse, *, table: str, market_data, venue: str,
                   row: dict, now: int, history_dir=None) -> None:
    """Book each REALIZED venue settlement for an open probe row exactly once.

    Settlements are replayed from the venue's realized history
    (``core.funding_history``), bounded below by the row's entry bar and above
    by ``now``. Nothing is inferred: the live funding print is not a settlement,
    and no settlement interval is assumed — the perp funding interval is
    time-varying per symbol (bases have switched 8h -> 4h mid-history), so no
    constant is correct. When the realized history is unavailable or has not yet
    caught up, nothing is booked and the cursor is left untouched, so the
    settlement is picked up on a later pass.

    ``last_funding_bucket`` now holds the last booked settlement TIMESTAMP.
    Rows written before this change hold an 8h bucket INDEX (~6.2e4 vs ~1.78e9);
    those are treated as unset, so accrual resumes bounded by the row's entry
    instead of re-booking or skipping history. ``market_data`` is retained for
    call-site compatibility and deliberately unused.
    """
    del market_data  # the live print is not an authority on settled funding
    symbol = str(row.get("symbol") or "")
    coin = symbol.split("/", 1)[0].strip().upper()
    if not coin:
        return
    try:
        since = float(row["signal_bar_ts"])
        end = float(now)
    except (KeyError, TypeError, ValueError):
        return

    cursor = row.get("last_funding_bucket")
    if cursor is not None:
        try:
            cursor_f = float(cursor)
        except (TypeError, ValueError):
            cursor_f = 0.0
        if cursor_f >= _LEGACY_BUCKET_CEILING:  # a real settlement timestamp
            since = max(since, cursor_f)

    settlements = load_realized_settlements(
        venue, coin, start_ts=since, end_ts=end,
        **({"base_dir": history_dir} if history_dir is not None else {}),
    )
    if settlements is None:
        key = (venue, coin)
        if key not in _FUNDING_HISTORY_WARNED:
            _FUNDING_HISTORY_WARNED.add(key)
            logger.warning(
                f"[probe] {venue} {coin}: no usable realized funding history — "
                "probe funding accrual DEFERRED (run "
                "scripts/backfill_funding_history.py). Nothing was guessed."
            )
        return
    new = [s for s in settlements if s.settlement_ts > since]
    if not new:
        return

    new_sum = float(row["realized_funding_rate_sum"] or 0.0) + sum(
        float(s.rate) for s in new
    )
    conn = warehouse._conn()
    conn.execute(
        f"UPDATE {table} SET realized_funding_rate_sum=?, "
        "last_funding_bucket=? WHERE proposal_id=?",
        (new_sum, int(new[-1].settlement_ts), row["proposal_id"]),
    )
    conn.commit()
