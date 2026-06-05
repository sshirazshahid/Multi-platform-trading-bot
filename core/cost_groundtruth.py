"""Ground-truth trade-cost aggregation from real exchange fills.

Settles the contested cost-vs-alpha split (modeled ~$11.5 fees vs tp_accuracy's modeled
~$30.5 / ~90%): neither prior estimate used real fills. This sums REAL fees from
exchange.fetch_my_trades over the live trades' (exchange, symbol) pairs.

The exchange fetcher is INJECTED so this core is pure and unit-testable with mocks — no live
account access in the test suite. A live run (read-only fetch_my_trades, no orders, no warehouse
write) wires a real ccxt fetcher and is best done owner-supervised. Fees are settleable from fills;
realized spread/slippage is NOT in fetch_my_trades (needs historical mid) and stays modeled.
"""
from __future__ import annotations


def _fill_fee(fill) -> float:
    """Absolute fee cost from a ccxt fill (handles 'fee'={'cost':x} and 'fees'=[{'cost':x},...])."""
    f = fill.get("fee")
    if isinstance(f, dict) and f.get("cost") is not None:
        return abs(float(f["cost"]))
    fs = fill.get("fees")
    if isinstance(fs, list):
        return sum(abs(float(x.get("cost") or 0.0)) for x in fs if isinstance(x, dict))
    return 0.0


def aggregate_real_fees(trades, fetch_fills, *, pad_s: float = 3600.0) -> dict:
    """Sum REAL fees from exchange fills over the trades' (exchange, symbol) windows.

    trades: iterable of dicts with exchange, symbol, ts_entry, ts_exit (sec), fee (modeled $).
    fetch_fills(exchange, symbol, since_ms) -> list of ccxt fill dicts (each with timestamp ms and
        'fee'/'fees'). Injected so this is pure/testable; the live wiring passes a ccxt fetcher.
    pad_s: seconds of slack around each (exchange,symbol) [min ts_entry, max ts_exit] window.

    Returns {real_fee_total, modeled_fee_total, n_fills, n_pairs, by_exchange}. Real fees are
    summed only over fills whose timestamp falls inside the padded window (excludes unrelated fills).
    """
    pairs: dict = {}
    modeled = 0.0
    for t in trades:
        ex, sym = t["exchange"], t["symbol"]
        modeled += float(t.get("fee") or 0.0)
        te = float(t["ts_entry"])
        tx = float(t.get("ts_exit") or te)
        lo, hi = pairs.get((ex, sym), (te, tx))
        pairs[(ex, sym)] = (min(lo, te), max(hi, tx))

    real = 0.0
    n_fills = 0
    by_exchange: dict = {}
    for (ex, sym), (lo, hi) in pairs.items():
        lo_ms = (lo - pad_s) * 1000.0
        hi_ms = (hi + pad_s) * 1000.0
        fills = fetch_fills(ex, sym, int(lo_ms)) or []
        for fl in fills:
            ts = fl.get("timestamp")
            if ts is not None and not (lo_ms <= float(ts) <= hi_ms):
                continue
            fee = _fill_fee(fl)
            real += fee
            n_fills += 1
            by_exchange[ex] = by_exchange.get(ex, 0.0) + fee

    return {
        "real_fee_total": real,
        "modeled_fee_total": modeled,
        "n_fills": n_fills,
        "n_pairs": len(pairs),
        "by_exchange": by_exchange,
    }
