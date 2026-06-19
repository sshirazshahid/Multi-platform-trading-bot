"""Phase 0 smoke: prove NautilusTrader installs and a backtest runs WITH FILLS here."""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from nautilus_trader.backtest.engine import BacktestEngine  # noqa: E402
from nautilus_trader.config import (  # noqa: E402
    BacktestEngineConfig,
    LoggingConfig,
    StrategyConfig,
)
from nautilus_trader.model.currencies import USDT  # noqa: E402
from nautilus_trader.model.data import BarType  # noqa: E402
from nautilus_trader.model.enums import AccountType, OmsType, OrderSide  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId, TraderId  # noqa: E402
from nautilus_trader.model.objects import Money  # noqa: E402
from nautilus_trader.test_kit.providers import TestInstrumentProvider  # noqa: E402
from nautilus_trader.trading.strategy import Strategy  # noqa: E402

from ntrader.data_ingest.load_parquet_bars import frame_to_bars, load_frame  # noqa: E402


class BuyHoldConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    qty: float = 0.01


class BuyHold(Strategy):
    def __init__(self, config: BuyHoldConfig):
        super().__init__(config)
        self._bought = False

    def on_start(self):
        self.instrument = self.cache.instrument(InstrumentId.from_str(self.config.instrument_id))
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    def on_bar(self, bar):
        if not self._bought and self.portfolio.is_flat(self.instrument.id):
            order = self.order_factory.market(
                instrument_id=self.instrument.id,
                order_side=OrderSide.BUY,
                quantity=self.instrument.make_qty(self.config.qty),
            )
            self.submit_order(order)
            self._bought = True


def main():
    engine = BacktestEngine(
        BacktestEngineConfig(trader_id=TraderId("NT-SMOKE-001"),
                             logging=LoggingConfig(bypass_logging=True))
    )
    instr = TestInstrumentProvider.btcusdt_perp_binance()
    venue = instr.id.venue
    engine.add_venue(venue=venue, oms_type=OmsType.NETTING, account_type=AccountType.MARGIN,
                     base_currency=USDT, starting_balances=[Money(10_000, USDT)])
    engine.add_instrument(instr)
    bt = BarType.from_str(f"{instr.id}-1-DAY-LAST-EXTERNAL")
    df = load_frame("BTC", source="synth", n_bars=120)
    bars = frame_to_bars(df, instr, bt)
    engine.add_data(bars)
    engine.add_strategy(BuyHold(BuyHoldConfig(instrument_id=str(instr.id), bar_type=str(bt), qty=0.01)))
    engine.run()
    fills = engine.trader.generate_order_fills_report()
    acct = engine.portfolio.account(venue)
    print("BARS:", len(bars), "FILLS:", len(fills), "BALANCE:", acct.balance_total(USDT))
    engine.dispose()
    assert len(bars) > 0 and len(fills) >= 1, "smoke failed: no bars or no fills"
    print("PHASE0_SMOKE_OK")


if __name__ == "__main__":
    main()
