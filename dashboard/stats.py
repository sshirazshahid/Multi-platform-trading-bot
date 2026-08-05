"""Trade statistics helpers for the dashboard package."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta, timezone

from dashboard.state import DIM, GREEN, RED
from dashboard.fetcher import LiveFetcher

def calc_unrealized(open_pos: list, fetcher: LiveFetcher) -> dict:
    result = {}
    for pos in open_pos:
        pid    = pos.get("id", "")
        sym    = pos.get("symbol", "")
        side   = pos.get("side", "buy")
        entry  = float(pos.get("entry_price", 0) or 0)
        size   = float(pos.get("size", 0) or 0)
        lev    = int(pos.get("leverage", 1) or 1)
        mtype  = pos.get("market_type", "spot")
        exname = (pos.get("exchange") or "").lower()

        live_price = fetcher.get_price(exname, sym)
        if not live_price or not size:
            # Still check for exchange-provided uPnL (e.g., Bybit/Binance futures)
            live_upnl = pos.get("_live_upnl")
            if live_upnl is not None and live_upnl != 0:
                result[pid] = {"price": 0.0, "upnl": float(live_upnl),
                               "upnl_pct": None, "move_pct": 0.0}
            else:
                result[pid] = {"price": 0.0, "upnl": None, "upnl_pct": None, "move_pct": 0.0}
            continue

        # Exchange-provided uPnL is authoritative for live futures positions
        live_upnl = pos.get("_live_upnl")
        if live_upnl is not None and pos.get("_from_exchange") and mtype == "futures":
            upnl = float(live_upnl)
            move_pct = (live_price - entry) / entry * 100 if entry > 0 else 0
            margin = (size * entry) / max(lev, 1) if entry > 0 else 1
            upnl_pct = (upnl / max(margin, 0.0001)) * 100 if margin > 0 else 0
            result[pid] = {"price": live_price, "upnl": upnl, "upnl_pct": upnl_pct,
                           "move_pct": move_pct}
            continue

        # Holdings with unknown entry: estimate value but no PnL
        if not entry:
            value = live_price * size
            result[pid] = {"price": live_price, "upnl": None, "upnl_pct": None,
                           "move_pct": 0.0, "value": value}
            continue

        move_pct = (live_price - entry) / entry * 100
        if side == "buy":
            upnl = (live_price - entry) * size
        else:
            upnl = (entry - live_price) * size

        # NOTE: entry_fee is already deducted from exchange balance at open time.
        # Unrealized PnL shows raw price movement only — fees are NOT subtracted
        # because entry fee is already paid (double-counting) and exit fee is
        # not yet incurred.  Net PnL after fees is shown on close.

        margin   = (size * entry) / max(lev, 1)
        upnl_pct = (upnl / max(margin, 0.0001)) * 100
        result[pid] = {"price": live_price, "upnl": upnl, "upnl_pct": upnl_pct, "move_pct": move_pct}
    return result


# 2026-05-04: shared filter for bot-initiated trades. Excludes:
#   - id-prefix "MANUAL-": user-imported positions via sync_with_exchanges
#   - strategy=="manual": same
#   - close_reason in {reconciled_from_exchange, reconciled_no_context}:
#     positions imported via sync, never closed by the bot
#
# NOT excluded (these ARE bot trades — bot OPENED them, closed via
# exchange-side mechanism such as SL fill or manual exchange close):
#   - ghost_sync / ghost_reconciled / ghost_force_close
#
# Used by every PnL/trade-count panel so all "Today/Yesterday/Daily/
# Weekly/AllTime" cells show the same definition of "the bot's trade".
_DASH_RECONCILE_REASONS = frozenset({
    "reconciled_from_exchange",
    "reconciled_no_context",
})


def _is_bot_trade(t: dict) -> bool:
    """True if this closed-trade row is bot-initiated (vs sync-imported).

    Phase 24 (2026-05-05): also filter RECONCILE-prefix id and
    'reconcile' strategy — those are leftover-size re-imports that the
    bot didn't actually open, just protected with SL/TP after a partial
    close. Same treatment as MANUAL for stats purposes.
    """
    pid = (t.get("id") or "")
    if pid.startswith("MANUAL-") or pid.startswith("RECONCILE-"):
        return False
    strat = (t.get("strategy") or "").lower()
    if strat in ("manual", "reconcile"):
        return False
    cr = t.get("close_reason") or ""
    if cr in _DASH_RECONCILE_REASONS:
        return False
    return True


def _is_real_trade(t: dict) -> bool:
    """True if this closed-trade row represents real capital at risk.

    Rules (2026-05-20 fix — replaced over-narrow whitelist):
    - Pure import records (close_reason in _DASH_RECONCILE_REASONS): excluded.
    - RECONCILE-prefix / strategy="reconcile" positions: INCLUDED when the
      close reason is NOT a pure-import reason. The old whitelist
      (_BOT_CLOSE_REASONS) missed Claude's descriptive reasons such as
      "4h RSI=32.2 near oversold, lock +1.4%" — those are still real bot
      exits that carry real P&L (e.g. AAVE +$1.23, BCH -$0.39 on 2026-05-20).
      By the time we reach the is_reconcile block the top-level
      _DASH_RECONCILE_REASONS check has already excluded pure-import closes,
      so returning True for everything that remains is correct.
    - MANUAL-prefix / strategy="manual": INCLUDED — bot manages SL/TP.
    - All other bot-opened positions: INCLUDED.

    This definition matches risk_state.daily_pnl (accumulated via
    record_trade_pnl), so PERFORMANCE, Daily, Weekly, and Heatmap panels
    agree with the daily email report.
    """
    cr = t.get("close_reason") or ""
    # Pure import — bot never touched it
    if cr in _DASH_RECONCILE_REASONS:
        return False
    pid   = (t.get("id") or "")
    strat = (t.get("strategy") or "").lower()
    is_reconcile = pid.startswith("RECONCILE-") or strat in ("reconcile", "reconciled_exchange")
    if is_reconcile:
        # cr is NOT a pure-import reason (already caught above).
        # Any other reason — including Claude's descriptive reasons — means
        # the bot actively decided to close. Count the P&L.
        return True
    return True


def _filter_bot_trades(closed):
    """Apply _is_bot_trade to a list of closed positions (bot-originated only)."""
    return [t for t in closed if _is_bot_trade(t)]


def _filter_real_trades(closed):
    """Apply _is_real_trade to a list of closed positions (all managed positions)."""
    return [t for t in closed if _is_real_trade(t)]


def _whole_pnl(t) -> float:
    """Whole-trade PnL: runner-leg net pnl + any partial-TP leg already banked
    (realized_partial_pnl). The runner-only `pnl` field under-reports profit on
    every trade that took a partial (+22.49 missing across the 500-row window
    at the 2026-06-11 audit) — all PnL panels must sum the whole trade."""
    return (t.get("pnl", 0) or 0) + (t.get("realized_partial_pnl") or 0)


def calc_stats(closed):
    # UTC day boundary (2026-06-11): the engine (risk manager, hour gates,
    # heatmap) lives on UTC days; bucketing this panel by LOCAL date made
    # "Today 117 / -49.81" disagree with the risk panel's UTC counters.
    today     = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)
    # Use the broad "real trade" filter so PERFORMANCE matches email
    # notifications: MANUAL-prefix positions (already open on exchange at
    # startup) are still bot-managed and their P&L is real. Only RECONCILE
    # size-adjustments are excluded. Strategy breakdown uses _filter_bot_trades.
    closed = _filter_real_trades(closed)
    t_pnl = t_gross = t_fees = 0.0
    t_n   = t_wins  = 0
    y_pnl = y_gross = y_fees = 0.0
    y_n   = y_wins  = 0
    a_pnl = a_gross = a_fees = 0.0
    a_wins = 0
    # Phase 43: use None sentinels so all-losing or all-winning sets don't
    # silently bottom-out at 0.0 (would hide the actual best/worst pnl).
    a_best = None; a_worst = None
    win_amounts = []; loss_amounts = []
    # Phase 45: track all-time best/worst DAY (cumulative daily PnL).
    # User wants "highest numbers achieved since bot started" — best
    # single trade alone doesn't capture that. Best/worst day all-time
    # is the high-water mark of bot's daily performance.
    daily_totals: dict = {}
    # Phase 47: track tie count + date range for clarity.
    a_ties = 0
    earliest_ts = None
    latest_ts = None

    for t in closed:
        pnl   = _whole_pnl(t)
        gross = t.get("gross_pnl", pnl) or pnl
        fees  = t.get("total_fees", 0) or 0
        ct    = t.get("close_time", 0) or 0
        a_pnl += pnl; a_gross += gross; a_fees += fees
        a_best  = pnl if a_best  is None else max(a_best,  pnl)
        a_worst = pnl if a_worst is None else min(a_worst, pnl)
        # Phase 47: track ties (pnl == 0) explicitly + date range
        if pnl == 0:
            a_ties += 1
        if ct:
            if earliest_ts is None or ct < earliest_ts:
                earliest_ts = ct
            if latest_ts is None or ct > latest_ts:
                latest_ts = ct
        # Phase 45: accumulate per-day totals (UTC days)
        if ct:
            try:
                _d = datetime.fromtimestamp(ct, tz=timezone.utc).date()
                daily_totals[_d] = daily_totals.get(_d, 0.0) + pnl
            except (OSError, ValueError, OverflowError):
                pass
        if pnl > 0:
            a_wins += 1
            win_amounts.append(pnl)
        elif pnl < 0:
            loss_amounts.append(abs(pnl))
        if ct:
            try:
                d = datetime.fromtimestamp(ct, tz=timezone.utc).date()
            except (OSError, ValueError, OverflowError):
                d = None
            if d == today:
                t_pnl += pnl; t_gross += gross; t_fees += fees; t_n += 1
                if pnl > 0: t_wins += 1
            elif d == yesterday:
                y_pnl += pnl; y_gross += gross; y_fees += fees; y_n += 1
                if pnl > 0: y_wins += 1

    total_n = len(closed)

    # Profit factor: gross wins / gross losses
    total_wins  = sum(win_amounts) if win_amounts else 0
    total_losses = sum(loss_amounts) if loss_amounts else 0
    pf = total_wins / total_losses if total_losses > 0 else (999.0 if total_wins > 0 else 0.0)

    # Current streak
    streak = 0
    streak_type = ""
    for t in closed:  # already sorted by close_time desc
        p = _whole_pnl(t)
        if p > 0:
            if streak_type == "" or streak_type == "W":
                streak += 1; streak_type = "W"
            else:
                break
        elif p < 0:
            if streak_type == "" or streak_type == "L":
                streak += 1; streak_type = "L"
            else:
                break
        else:
            break

    return {
        "today_pnl": t_pnl, "today_n": t_n, "today_wins": t_wins,
        "today_wr":  (t_wins / t_n * 100) if t_n else 0,
        "yesterday_pnl":  y_pnl, "yesterday_n": y_n, "yesterday_wins": y_wins,
        "yesterday_wr":   (y_wins / y_n * 100) if y_n else 0,
        "all_pnl":   a_pnl, "all_gross": a_gross, "all_fees": a_fees,
        "total_n":   total_n, "all_wins": a_wins,
        "all_wr":    (a_wins / total_n * 100) if total_n else 0,
        "all_best":  a_best if a_best is not None else 0.0,
        "all_worst": a_worst if a_worst is not None else 0.0,
        # Phase 47: classic WR (excludes ties from denominator) + tie count
        "all_ties":  a_ties,
        "all_losses": (total_n - a_wins - a_ties),
        "all_wr_classic": (a_wins / (a_wins + total_n - a_wins - a_ties) * 100)
                          if (a_wins + total_n - a_wins - a_ties) > 0 else 0,
        "earliest_ts": earliest_ts,
        "latest_ts":   latest_ts,
        # Phase 45: best/worst DAY all-time (the bot's high-water marks).
        "best_day_alltime":   max(daily_totals.values()) if daily_totals else 0.0,
        "worst_day_alltime":  min(daily_totals.values()) if daily_totals else 0.0,
        "best_day_date": (max(daily_totals.items(), key=lambda x: x[1])[0]
                          if daily_totals else None),
        "worst_day_date": (min(daily_totals.items(), key=lambda x: x[1])[0]
                           if daily_totals else None),
        "profit_factor": pf,
        "avg_win":   (total_wins / len(win_amounts)) if win_amounts else 0,
        "avg_loss":  (total_losses / len(loss_amounts)) if loss_amounts else 0,
        "streak":    streak, "streak_type": streak_type,
    }


def calc_exchange_stats(closed, open_pos):
    # Use broad real-trade filter to match PERFORMANCE / Daily / Weekly cells.
    closed = _filter_real_trades(closed)
    ex_stats = defaultdict(lambda: {
        "pnl": 0.0, "n": 0, "wins": 0, "fees": 0.0, "open": 0,
        "spot_pnl": 0.0, "spot_n": 0, "spot_wins": 0,
        "futures_pnl": 0.0, "futures_n": 0, "futures_wins": 0,
    })
    for t in closed:
        ex  = (t.get("exchange") or "unknown").lower()
        pnl = _whole_pnl(t)
        fee = t.get("total_fees", 0) or 0
        mtype = t.get("market_type", "spot")
        ex_stats[ex]["pnl"]  += pnl
        ex_stats[ex]["fees"] += fee
        ex_stats[ex]["n"]    += 1
        if pnl > 0: ex_stats[ex]["wins"] += 1
        # Per market type
        key_pnl  = "{}_pnl".format(mtype)
        key_n    = "{}_n".format(mtype)
        key_wins = "{}_wins".format(mtype)
        ex_stats[ex][key_pnl]  = ex_stats[ex].get(key_pnl, 0.0) + pnl
        ex_stats[ex][key_n]    = ex_stats[ex].get(key_n, 0) + 1
        if pnl > 0:
            ex_stats[ex][key_wins] = ex_stats[ex].get(key_wins, 0) + 1
    for p in open_pos:
        ex = (p.get("exchange") or "unknown").lower()
        ex_stats[ex]["open"] += 1
    return ex_stats


def calc_strategy_stats(closed):
    """Breakdown PnL and win rate by strategy."""
    # Phase 21 (2026-05-04): apply shared bot-trade filter so the strategy
    # breakdown agrees with All-Time count from calc_stats.
    closed = _filter_bot_trades(closed)
    strat_stats = defaultdict(lambda: {"pnl": 0.0, "n": 0, "wins": 0})
    for t in closed:
        raw  = t.get("strategy", "unknown") or "unknown"
        name = raw.split("|")[0].strip()
        pnl  = _whole_pnl(t)
        strat_stats[name]["pnl"] += pnl
        strat_stats[name]["n"]   += 1
        if pnl > 0:
            strat_stats[name]["wins"] += 1
    return strat_stats


def calc_hourly_heatmap(closed):
    """PnL by hour of day (UTC) — identifies best/worst trading hours.

    Phase 43 (2026-05-10): apply real-trade filter so heatmap is consistent
    with PERFORMANCE/Daily/Weekly cells. Without this, MANUAL/RECONCILE
    trades contaminated the hourly buckets, making the heatmap show
    different "best/worst hours" than what the bot actually traded.
    """
    closed = _filter_real_trades(closed)
    hours = defaultdict(lambda: {"pnl": 0.0, "n": 0})
    for t in closed:
        ct = t.get("close_time", 0)
        if not ct:
            continue
        hour = datetime.fromtimestamp(ct, tz=timezone.utc).hour
        pnl  = _whole_pnl(t)
        hours[hour]["pnl"] += pnl
        hours[hour]["n"]   += 1
    return hours


def calc_daily_pnl(closed, days=7):
    # Phase 21 (2026-05-04): apply shared bot-trade filter so the
    # Use broad real-trade filter so heatmap matches Performance > Today/Yesterday.
    closed = _filter_real_trades(closed)
    buckets = defaultdict(lambda: {"pnl": 0.0, "trades": 0, "wins": 0})
    # UTC day buckets to match calc_stats / heatmap / engine counters.
    today   = datetime.now(timezone.utc).date()
    for t in closed:
        ct = t.get("close_time", 0)
        if not ct: continue
        day = datetime.fromtimestamp(ct, tz=timezone.utc).date()
        if (today - day).days >= days: continue
        pnl = _whole_pnl(t)
        buckets[day]["pnl"]    += pnl
        buckets[day]["trades"] += 1
        if pnl > 0: buckets[day]["wins"] += 1
    result = []
    for i in range(days - 1, -1, -1):
        day  = today - timedelta(days=i)
        data = buckets.get(day, {"pnl": 0.0, "trades": 0, "wins": 0})
        result.append({"date": day, **data})
    return result


def _bucket_stats(trades):
    """Aggregate stats over an arbitrary slice of closed trades."""
    if not trades:
        return {"n": 0, "wins": 0, "losses": 0, "wr": 0.0, "pnl": 0.0,
                "pf": 0.0, "avg_win": 0.0, "avg_loss": 0.0,
                "best": 0.0, "worst": 0.0, "fees": 0.0}
    wins = [_whole_pnl(t) for t in trades if _whole_pnl(t) > 0]
    losses = [abs(_whole_pnl(t)) for t in trades if _whole_pnl(t) < 0]
    pnls = [_whole_pnl(t) for t in trades]
    wsum = sum(wins); lsum = sum(losses)
    return {
        "n":       len(trades),
        "wins":    len(wins),
        "losses":  len(losses),
        "wr":      (len(wins) / len(trades) * 100) if trades else 0.0,
        "pnl":     sum(pnls),
        "pf":      (wsum / lsum) if lsum > 0 else (999.0 if wsum > 0 else 0.0),
        "avg_win": (wsum / len(wins)) if wins else 0.0,
        "avg_loss":(lsum / len(losses)) if losses else 0.0,
        "best":    max(pnls) if pnls else 0.0,
        "worst":   min(pnls) if pnls else 0.0,
        "fees":    sum(t.get("total_fees", 0) or 0 for t in trades),
    }


def calc_weekly_stats(closed):
    """This-week / last-week buckets + a per-day breakdown for the current week.

    Returns dict with:
      - this_week:     stats for trades closed in the last 7 days
      - last_week:     stats for trades closed 7-14 days ago
      - best_day:      (date, pnl, n) — best day in the current week
      - worst_day:     (date, pnl, n) — worst day in the current week
      - active_days:   number of days in the current week with at least 1 trade
    """
    # Use broad real-trade filter so weekly stats match Performance + Daily PnL.
    closed = _filter_real_trades(closed)
    now_ts = time.time()
    cutoff_this = now_ts - 7 * 86400
    cutoff_prev = now_ts - 14 * 86400

    this_trades = []
    prev_trades = []
    for t in closed:
        ct = t.get("close_time", 0) or 0
        if ct >= cutoff_this:
            this_trades.append(t)
        elif ct >= cutoff_prev:
            prev_trades.append(t)

    daily = calc_daily_pnl(closed, days=7)
    active = [d for d in daily if d["trades"] > 0]
    best_day = max(active, key=lambda d: d["pnl"]) if active else None
    worst_day = min(active, key=lambda d: d["pnl"]) if active else None

    return {
        "this_week":  _bucket_stats(this_trades),
        "last_week":  _bucket_stats(prev_trades),
        "best_day":   best_day,
        "worst_day":  worst_day,
        "active_days": len(active),
    }


def sparkline(values):
    if not values: return ""
    max_abs = max(abs(v) for v in values) or 1
    bars = []
    for v in values:
        if v > 0:
            bars.append(col("▲" * max(1, int(v / max_abs * 4)), GREEN))
        elif v < 0:
            bars.append(col("▼" * max(1, int(abs(v) / max_abs * 4)), RED))
        else:
            bars.append(col("·", DIM))
    return "  ".join(bars)
