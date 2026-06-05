"""Beta-adjusted alpha-vs-BTC labeling for closed trades.

Separates genuine entry alpha from BTC market beta so the learning substrate
doesn't mistake a net-short book riding a BTC dump for "edge" — the documented
2026-06-04 failure mode where a profitable short was pure beta during a -13% BTC
week. Pure functions only; no DB or live-account contact (callers supply the BTC
series and warehouse rows).

Formula (per trade), honoring this codebase's conventions:
    whole_pnl  = realized_pnl + (partial_realized_pnl or 0)   # net of fees
    notional   = entry_px * size                              # unleveraged USD
    trade_ret  = whole_pnl / notional
    btc_ret    = btc_exit / btc_entry - 1                     # over [entry, exit]
    side_sign  = +1 long / -1 short
    alpha_ret  = trade_ret - side_sign * beta * btc_ret
    alpha_usd  = alpha_ret * notional                         # stored, $-comparable

Conventions (see core/position_tracker.py): `realized_pnl` is the runner leg's net
PnL (gross - fees); the partial-TP leg is booked separately in
`partial_realized_pnl`, so whole-trade economics require summing both. `size` is the
already-leveraged position quantity, so `entry_px * size` is the *unleveraged* USD
notional — directly comparable to btc_ret. beta defaults to 1.0 (simple market
excess); `beta_used` is stored so a v2 rolling-regression beta is swappable later.
"""
from __future__ import annotations

import bisect
from typing import Optional


def compute_alpha_vs_btc(
    *,
    realized_pnl,
    partial_realized_pnl,
    entry_px,
    size,
    side,
    btc_entry,
    btc_exit,
    beta: float = 1.0,
) -> Optional[tuple[float, float, float]]:
    """Return (alpha_vs_btc_usd, btc_ret_window, beta_used), or None if uncomputable.

    None (not 0.0) is returned on missing BTC data or a degenerate notional so the
    label stays NULL and the periodic labeler retries — 0.0 is a valid alpha value.
    """
    if entry_px is None or size is None or not entry_px or not size:
        return None
    if btc_entry is None or btc_exit is None:
        return None
    try:
        btc_entry = float(btc_entry)
        btc_exit = float(btc_exit)
    except (TypeError, ValueError):
        return None
    if btc_entry <= 0:
        return None
    notional = float(entry_px) * float(size)
    if notional <= 0:
        return None

    whole_pnl = float(realized_pnl or 0.0) + float(partial_realized_pnl or 0.0)
    trade_ret = whole_pnl / notional
    btc_ret = btc_exit / btc_entry - 1.0
    side_sign = 1.0 if str(side).lower() in ("buy", "long") else -1.0
    alpha_ret = trade_ret - side_sign * float(beta) * btc_ret
    return alpha_ret * notional, btc_ret, float(beta)


def interp_btc_price(btc_df, ts) -> Optional[float]:
    """Linear-interpolated BTC close at unix-seconds `ts` from an ascending OHLCV
    frame (columns 'ts' unix-sec and 'close'). Returns None before the first bar
    or on empty data; clamps to the last close at/after the final bar.

    A trade hold is minutes-to-hours, so sub-bar interpolation matters — mapping a
    20-min hold onto whole 1h/15m bars would zero out btc_ret and destroy the label.
    """
    if btc_df is None or ts is None:
        return None
    try:
        n = len(btc_df)
    except TypeError:
        return None
    if n == 0:
        return None
    tss = [float(x) for x in btc_df["ts"].tolist()]
    closes = [float(x) for x in btc_df["close"].tolist()]
    ts = float(ts)
    idx = bisect.bisect_right(tss, ts) - 1
    if idx < 0:
        return None  # before the first available bar -> leave unlabeled
    if idx >= n - 1:
        return closes[-1]  # at/after the last bar -> clamp to last close
    t0, t1 = tss[idx], tss[idx + 1]
    c0, c1 = closes[idx], closes[idx + 1]
    if t1 == t0:
        return c0
    frac = (ts - t0) / (t1 - t0)
    return c0 + (c1 - c0) * frac


def _get(row, key, default=None):
    """Read a column from a dict or sqlite3.Row; None/missing -> default."""
    try:
        v = row[key]
    except (KeyError, IndexError, TypeError):
        return default
    return default if v is None else v


def label_trades(rows, btc_df, beta: float = 1.0) -> list[tuple]:
    """Glue over the pure functions: for each warehouse row (needs ts_entry, ts_exit,
    entry_px, size, side, realized_pnl, partial_realized_pnl, id), compute the label.
    Returns [(id, alpha_vs_btc, btc_ret_window, beta_used), ...] for labelable rows
    (rows with no BTC coverage are skipped, left NULL for a later pass)."""
    out: list[tuple] = []
    for r in rows:
        btc_entry = interp_btc_price(btc_df, _get(r, "ts_entry"))
        btc_exit = interp_btc_price(btc_df, _get(r, "ts_exit"))
        res = compute_alpha_vs_btc(
            realized_pnl=_get(r, "realized_pnl", 0.0),
            partial_realized_pnl=_get(r, "partial_realized_pnl", 0.0),
            entry_px=_get(r, "entry_px"),
            size=_get(r, "size"),
            side=_get(r, "side"),
            btc_entry=btc_entry,
            btc_exit=btc_exit,
            beta=beta,
        )
        if res is None:
            continue
        alpha, btc_ret, beta_used = res
        out.append((_get(r, "id"), alpha, btc_ret, beta_used))
    return out


def backfill_unlabeled(warehouse, load_window, *, beta: float = 1.0, limit: int = 5000):
    """Label closed trades that still have NULL alpha_vs_btc, using cache-only BTC OHLCV.

    Designed for the IN-PROCESS learning cycle (the bot's own warehouse connection — no
    cross-process SQLITE_BUSY contention, unlike an external script writing the live DB).
    `warehouse` needs `.query(sql, params) -> rows` and `.update_btc_alpha(updates) -> int`;
    `load_window(symbol, tf, start_ts, end_ts)` returns a cache-only OHLCV DataFrame.
    Returns (n_written, n_candidates). Trades beyond the cached BTC window stay NULL for a
    later pass — the intended two-phase behavior.
    """
    rows = warehouse.query(
        "SELECT id, ts_entry, ts_exit, entry_px, exit_px, size, side, "
        "realized_pnl, partial_realized_pnl FROM trades "
        "WHERE status='CLOSED' AND alpha_vs_btc IS NULL "
        "AND ts_entry IS NOT NULL AND ts_exit IS NOT NULL "
        "AND entry_px IS NOT NULL AND exit_px IS NOT NULL "
        "ORDER BY ts_entry LIMIT ?",
        (limit,),
    )
    if not rows:
        return 0, 0
    min_ts = min(float(r["ts_entry"]) for r in rows) - 900
    max_ts = max(float(r["ts_exit"]) for r in rows) + 900
    btc = load_window("BTC/USDT", "15m", int(min_ts), int(max_ts))
    if btc is None or len(btc) == 0:
        return 0, len(rows)
    labeled = label_trades(rows, btc, beta=beta)
    updates = [(a, br, b, tid) for (tid, a, br, b) in labeled]
    return warehouse.update_btc_alpha(updates), len(rows)
