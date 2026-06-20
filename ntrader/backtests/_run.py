"""Shared backtest runners for Strategy A.

A generic HIGH-PRECISION (8dp) USDT-settled perp is used as a price container so real
low-priced coins (XRP, etc.) are NOT rounded by the bar wrangler. Identity is
irrelevant to bar-for-bar parity and per-coin metrics (one engine per coin).
"""
from __future__ import annotations

import pathlib
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from nautilus_trader.backtest.engine import BacktestEngine  # noqa: E402
from nautilus_trader.config import BacktestEngineConfig, LoggingConfig  # noqa: E402
from nautilus_trader.model.currencies import BTC, USDT  # noqa: E402
from nautilus_trader.model.data import BarType  # noqa: E402
from nautilus_trader.model.enums import AccountType, OmsType, OrderType  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId, Symbol, TraderId, Venue  # noqa: E402
from nautilus_trader.model.instruments import CryptoPerpetual  # noqa: E402
from nautilus_trader.model.objects import Money, Price, Quantity  # noqa: E402

from ntrader.data_ingest.load_parquet_bars import closes_of, frame_to_bars, load_frame  # noqa: E402
from ntrader.strategies.tsmom_strategy import TSMOMConfig, TSMOMStrategy  # noqa: E402


def _engine(tid="NT-A-001"):
    return BacktestEngine(BacktestEngineConfig(
        trader_id=TraderId(tid), logging=LoggingConfig(bypass_logging=True)))


def _perp():
    """Generic 8dp USDT-settled perp: no price rounding for any coin (XRP, BNB, ...)."""
    return CryptoPerpetual(
        instrument_id=InstrumentId(symbol=Symbol("GENUSDT-PERP"), venue=Venue("SIM")),
        raw_symbol=Symbol("GENUSDT"),
        base_currency=BTC, quote_currency=USDT, settlement_currency=USDT, is_inverse=False,
        price_precision=8, price_increment=Price(1e-08, precision=8),
        size_precision=8, size_increment=Quantity(1e-08, precision=8),
        max_quantity=Quantity.from_str("1000000000.0"), min_quantity=Quantity(1e-08, precision=8),
        max_notional=None, min_notional=Money(0.01, USDT),
        max_price=Price(100000000, precision=8), min_price=Price(1e-08, precision=8),
        margin_init=Decimal("0.0"), margin_maint=Decimal("0.0"),
        maker_fee=Decimal("0.0010"), taker_fee=Decimal("0.0010"),
        ts_event=0, ts_init=0,
    )


def run_strategy_a(base="BTC", source="synth", lookback=28, n_bars=420, enable_stop=False,
                   starting_equity=10_000.0, record_warehouse=False, warehouse_path=None,
                   frame=None):
    engine = _engine()
    instr = _perp()
    venue = instr.id.venue
    engine.add_venue(venue=venue, oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                     base_currency=USDT, starting_balances=[Money(starting_equity, USDT)])
    engine.add_instrument(instr)
    bt = BarType.from_str(f"{instr.id}-1-DAY-LAST-EXTERNAL")
    df = frame if frame is not None else load_frame(base, source=source, n_bars=n_bars)
    bars = frame_to_bars(df, instr, bt)
    engine.add_data(bars)
    strat = TSMOMStrategy(TSMOMConfig(
        instrument_id=str(instr.id), bar_type=str(bt), lookback=lookback, sl_pct=8.0,
        enable_stop=enable_stop, record_warehouse=record_warehouse, warehouse_path=warehouse_path))
    engine.add_strategy(strat)
    engine.run()
    orders = engine.cache.orders()
    out = {
        "decision_bars": list(strat.decision_bars),
        "closes": closes_of(df),
        "n_bars": len(bars),
        "fills": len(engine.trader.generate_order_fills_report()),
        "stop_orders": sum(1 for o in orders if o.order_type == OrderType.STOP_MARKET),
        "open_at_end": len(engine.cache.positions_open()),
        "final_balance": float(engine.portfolio.account(venue).balance_total(USDT).as_double()),
        "start_equity": float(starting_equity),
    }
    engine.dispose()
    return out
