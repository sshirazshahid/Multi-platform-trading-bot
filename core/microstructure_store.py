"""Compact per-bar microstructure feature store (LOG-ONLY, research-grade).

Owner-approved 2026-07-24 ("Compact per-bar features"); the 2026-07-26 sweep
added liquidity-STATE columns alongside signed flow (per arXiv 2607.09230 —
schema decided BEFORE the first write; changes after accrual are expensive).

This module is pure feature math + warehouse persistence. It holds NO
reference to any order/risk/live path, produces NO signal, and writes ONLY
the ``microstructure_features`` table in data/warehouse.sqlite. It is the
substrate for future pre-registered screens joining probe outcomes on
(symbol, venue, bar_ts) — nothing here reaches a live decision.

HONEST END-OF-BAR-WINDOW SEMANTICS (read before interpreting any row):
- The harvester runs ~5 minutes AFTER each 4h bar close and stamps the row
  with ``bar_ts`` = the open epoch of the just-CLOSED 4h bar (the same
  bar-open key probes record as signal_bar_ts, so rows join to probe
  decisions/outcomes directly). ``asof_ts`` records the actual fetch time —
  features are a snapshot ~5min after that bar's close, NOT an exact
  bar-boundary aggregate.
- Trade features come from REST ``fetch_trades``, which reaches only ~the
  last 1000 trades. On liquid symbols that window can be MINUTES, not the
  full 4h bar; on quiet symbols it can span MORE than one bar. The features
  therefore describe "the most recent trade window as of asof_ts", and
  ``window_trades_n`` records the raw fetched sample size so any screen can
  condition on (or reject) thin windows. This limitation is structural to
  keyless REST harvesting and is disclosed rather than hidden.
- Book features are a single top-20-level snapshot at asof_ts.

Schema (PK symbol, venue, bar_ts):
- signed flow:      signed_volume, buy_sell_imbalance, trade_count,
                    large_trade_count, notional_total
- liquidity state:  book_imbalance (top-20 bid_usd / (bid_usd + ask_usd)),
                    spread_bps, depth_top20_bid_usd, depth_top20_ask_usd
- sample size:      window_trades_n (raw trades fetched; see above)
- provenance:       asof_ts (fetch epoch), source (e.g. 'ccxt.bybit.rest')
"""

from __future__ import annotations

import sqlite3
from typing import Optional

# Number of book levels aggregated per side. Frozen with the schema.
BOOK_LEVELS = 20

# A trade is "large" when price * amount >= this many USD(T). Frozen with the
# schema (a moved threshold is a NEW column/pre-registration, not a tweak).
LARGE_TRADE_NOTIONAL_USD = 10_000.0

_SCHEMA = """
CREATE TABLE IF NOT EXISTS microstructure_features (
    symbol               TEXT,
    venue                TEXT,
    bar_ts               INTEGER,  -- 4h bar-open epoch of the just-closed bar
    signed_volume        REAL,     -- ┐ signed flow (aggressor side from
    buy_sell_imbalance   REAL,     -- │ ccxt trade 'side'):
    trade_count          INTEGER,  -- │ (buy-sell)/(buy+sell) volume,
    large_trade_count    INTEGER,  -- │ usable trades, >=10k USD trades,
    notional_total       REAL,     -- ┘ sum(price*amount)
    book_imbalance       REAL,     -- ┐ liquidity state at asof_ts:
    spread_bps           REAL,     -- │ top-20 bid/(bid+ask) USD depth,
    depth_top20_bid_usd  REAL,     -- │ (ask-bid)/mid * 1e4,
    depth_top20_ask_usd  REAL,     -- ┘ USD depth per side
    window_trades_n      INTEGER,  -- raw trades fetched (sample size; header)
    asof_ts              REAL,     -- provenance: fetch epoch
    source               TEXT,     -- provenance: e.g. 'ccxt.bybit.rest'
    PRIMARY KEY (symbol, venue, bar_ts)
);
CREATE INDEX IF NOT EXISTS idx_microstructure_features_symbol
    ON microstructure_features(symbol);
"""

_COLUMNS = (
    "symbol",
    "venue",
    "bar_ts",
    "signed_volume",
    "buy_sell_imbalance",
    "trade_count",
    "large_trade_count",
    "notional_total",
    "book_imbalance",
    "spread_bps",
    "depth_top20_bid_usd",
    "depth_top20_ask_usd",
    "window_trades_n",
    "asof_ts",
    "source",
)


def ensure_schema(conn: sqlite3.Connection) -> None:
    """Create the microstructure_features table + index (idempotent)."""
    conn.executescript(_SCHEMA)
    conn.commit()


def insert_row(conn: sqlite3.Connection, row: dict) -> None:
    """INSERT OR REPLACE one feature row keyed on (symbol, venue, bar_ts).

    Missing feature keys persist as NULL (a partially failed fetch is
    recorded honestly, never guessed)."""
    values = tuple(row.get(c) for c in _COLUMNS)
    placeholders = ", ".join("?" for _ in _COLUMNS)
    conn.execute(
        f"INSERT OR REPLACE INTO microstructure_features "
        f"({', '.join(_COLUMNS)}) VALUES ({placeholders})",
        values,
    )
    conn.commit()


def compute_trade_features(trades) -> Optional[dict]:
    """Signed-flow features from a ccxt ``fetch_trades`` list.

    Aggressor side is the ccxt trade ``side`` ('buy' = taker buy). Trades
    missing a usable side/price/amount are excluded from the math but still
    counted in ``window_trades_n`` (the raw sample size — see module header
    for the ~last-1000-trades REST window limitation).

    Returns None on an empty/None input (fail-soft, never raises); returns
    None too when no trade in the window is usable.
    """
    if not trades:
        return None
    buy_vol = 0.0
    sell_vol = 0.0
    notional_total = 0.0
    trade_count = 0
    large_trade_count = 0
    for t in trades:
        try:
            side = t.get("side")
            price = float(t.get("price"))
            amount = float(t.get("amount"))
        except (AttributeError, TypeError, ValueError):
            continue
        if side not in ("buy", "sell") or price <= 0 or amount <= 0:
            continue
        notional = price * amount
        if side == "buy":
            buy_vol += amount
        else:
            sell_vol += amount
        notional_total += notional
        trade_count += 1
        if notional >= LARGE_TRADE_NOTIONAL_USD:
            large_trade_count += 1
    if trade_count == 0:
        return None
    total_vol = buy_vol + sell_vol
    return {
        "signed_volume": buy_vol - sell_vol,
        "buy_sell_imbalance": (buy_vol - sell_vol) / total_vol if total_vol > 0 else None,
        "trade_count": trade_count,
        "large_trade_count": large_trade_count,
        "notional_total": notional_total,
        "window_trades_n": len(trades),
    }


def compute_book_features(orderbook) -> Optional[dict]:
    """Liquidity-state features from a ccxt ``fetch_order_book`` snapshot.

    Aggregates the top ``BOOK_LEVELS`` (20) price levels per side into USD
    depth, the bid-share book imbalance bid/(bid+ask), and the top-of-book
    spread in bps. Returns None on a missing/empty side (fail-soft, never
    raises) — an unreadable book is recorded as NULLs, never guessed.
    """
    if not orderbook:
        return None
    try:
        bids = orderbook.get("bids") or []
        asks = orderbook.get("asks") or []
    except AttributeError:
        return None
    if not bids or not asks:
        return None
    try:
        bid_usd = sum(float(p) * float(a) for p, a, *_ in bids[:BOOK_LEVELS])
        ask_usd = sum(float(p) * float(a) for p, a, *_ in asks[:BOOK_LEVELS])
        best_bid = float(bids[0][0])
        best_ask = float(asks[0][0])
    except (IndexError, TypeError, ValueError):
        return None
    if best_bid <= 0 or best_ask <= 0 or (bid_usd + ask_usd) <= 0:
        return None
    mid = (best_bid + best_ask) / 2.0
    return {
        "book_imbalance": bid_usd / (bid_usd + ask_usd),
        "spread_bps": (best_ask - best_bid) / mid * 1e4,
        "depth_top20_bid_usd": bid_usd,
        "depth_top20_ask_usd": ask_usd,
    }
