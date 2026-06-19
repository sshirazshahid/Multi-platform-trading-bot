"""ntrader/live/paper_trade.py

ZERO-CREDENTIAL live PAPER trading for the discrete TSMOM strategy.

What this is (and is NOT):
  * Streams REAL Binance public market data (klines) with NO API key, NO secret,
    NO funds at risk. api_key=None / api_secret=None => public market data only.
  * Routes order fills through a LOCAL Sandbox execution client
    (nautilus_trader.adapters.sandbox) which matches orders against an in-process
    SimulatedExchange. NO order ever leaves this process. This is PAPER trading.
  * It is NOT connected to any real account and places NO real orders.
  * Goal is CAPITAL PRESERVATION and backtest<->live PARITY, NOT profit. Every
    signal this bot has screened is NO_EDGE after costs at retail scale; this run
    exists to watch the validated discrete TSMOM mechanics on live bars, not to
    make money.

Parity: the trading decision (on_bar) is the UNTOUCHED TSMOMStrategy.on_bar from
ntrader/strategies/tsmom_strategy.py. This module only adds a thin live wrapper
(warmup via request_bars + on_historical_data seeding) so the 28d-momentum / 20d-vol
windows are populated immediately instead of waiting ~50 live days. The core
OPEN/CLOSE rule and the pretrade guard / 8% disaster stop are inherited verbatim.

Verified against installed nautilus_trader 1.228.0 (ntrader/venv_nt).
"""
from __future__ import annotations

import argparse
import pathlib
import sys
from decimal import Decimal

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

import pandas as pd  # noqa: E402

from nautilus_trader.adapters.binance.common.enums import BinanceAccountType  # noqa: E402
from nautilus_trader.adapters.binance.config import (  # noqa: E402
    BinanceDataClientConfig,
    BinanceInstrumentProviderConfig,
)
from nautilus_trader.adapters.binance.factories import BinanceLiveDataClientFactory  # noqa: E402
from nautilus_trader.adapters.sandbox.config import SandboxExecutionClientConfig  # noqa: E402
from nautilus_trader.adapters.sandbox.factory import SandboxLiveExecClientFactory  # noqa: E402
from nautilus_trader.config import LoggingConfig, TradingNodeConfig  # noqa: E402
from nautilus_trader.live.node import TradingNode  # noqa: E402
from nautilus_trader.model.data import Bar, BarType  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId  # noqa: E402

from ntrader.strategies.tsmom_strategy import TSMOMConfig, TSMOMStrategy  # noqa: E402

VENUE = "BINANCE"  # data-client venue and exec-client key MUST match for sandbox fills

# Coin -> live Binance USDT-PERP InstrumentId (zero-credential public futures klines).
_COIN_TO_INSTRUMENT = {
    "BTC": "BTCUSDT-PERP.BINANCE",
    "ETH": "ETHUSDT-PERP.BINANCE",
    "SOL": "SOLUSDT-PERP.BINANCE",
    "BNB": "BNBUSDT-PERP.BINANCE",
    "XRP": "XRPUSDT-PERP.BINANCE",
}
_TF_TO_BARSPEC = {
    "1d": "1-DAY",
    "1h": "1-HOUR",
    "1m": "1-MINUTE",  # demo only: faster mechanics, NOT the validated daily edge
}
_TF_TO_SECONDS = {"1d": 86_400, "1h": 3_600, "1m": 60}


class TSMOMLiveStrategy(TSMOMStrategy):
    """Thin LIVE wrapper. Adds warmup ONLY; inherits the bar-for-bar on_bar rule,
    pretrade guard and 8% disaster stop unchanged from TSMOMStrategy."""

    _bar_seconds = 86_400  # daily default; _build_node overrides per timeframe

    def on_start(self):
        # Parent wires instrument, _min_bars, optional warehouse, and subscribe_bars.
        super().on_start()
        bar_type = BarType.from_str(self.config.bar_type)  # -EXTERNAL required live
        # Request enough history to satisfy min_bars(lookback) = lookback + 20 + 2,
        # with margin so the signal is armed on the first live bar. The lookback
        # window is scaled to the actual timeframe so non-daily demos warm up too.
        warmup = int(self._min_bars) + 5
        start = self.clock.utc_now() - pd.Timedelta(seconds=self._bar_seconds * (warmup + 5))
        self.request_bars(bar_type, start=start, limit=warmup)
        self.log.info(
            f"[PAPER] TSMOM live wrapper armed: {self.instrument.id} "
            f"warmup~{warmup} bars; PAPER only (no real orders), capital-preservation."
        )

    def on_historical_data(self, data):
        # Warmup bars are delivered HERE, not to on_bar(). Seed the SAME bookkeeping
        # the parent on_bar uses (closes + running index) WITHOUT trading on history.
        if isinstance(data, Bar):
            self._closes.append(float(data.close))
            self._i += 1


def _build_node(coins, timeframe, equity, lookback):
    bar_spec = _TF_TO_BARSPEC[timeframe]
    instrument_ids = [InstrumentId.from_str(_COIN_TO_INSTRUMENT[c]) for c in coins]

    data_config = BinanceDataClientConfig(
        api_key=None,        # None => PUBLIC market data only (no creds, no funds)
        api_secret=None,
        account_type=BinanceAccountType.USDT_FUTURES,
        us=False,
        instrument_provider=BinanceInstrumentProviderConfig(
            load_ids=frozenset(instrument_ids),  # MUST load the traded instruments
        ),
    )
    # Sandbox: LOCAL simulated fills driven by the live bar feed. reject_stop_orders
    # MUST be False or the strategy's mandatory 8% disaster stop is rejected.
    exec_config = SandboxExecutionClientConfig(
        venue=VENUE,
        starting_balances=[f"{equity:.0f} USDT"],
        account_type="MARGIN",          # matches backtest venue; default_leverage=1 below
        default_leverage=Decimal(1),    # TSMOM preservation: leverage pinned to 1
        bar_execution=True,             # bars move the matching engine
        reject_stop_orders=False,       # <-- allow the 8% stop-market (default is True)
    )

    config = TradingNodeConfig(
        trader_id="TSMOM-PAPER-001",
        logging=LoggingConfig(log_level="INFO"),
        data_clients={VENUE: data_config},
        exec_clients={VENUE: exec_config},
    )
    node = TradingNode(config=config)
    node.add_data_client_factory(VENUE, BinanceLiveDataClientFactory)
    node.add_exec_client_factory(VENUE, SandboxLiveExecClientFactory)
    node.build()

    for iid in instrument_ids:
        bar_type = f"{iid}-{bar_spec}-LAST-EXTERNAL"
        strat = TSMOMLiveStrategy(TSMOMConfig(
            instrument_id=str(iid),
            bar_type=bar_type,
            lookback=lookback,
            sl_pct=8.0,
            leverage=1,
            enable_stop=True,
            record_warehouse=False,
        ))
        strat._bar_seconds = _TF_TO_SECONDS[timeframe]
        node.trader.add_strategy(strat)
    return node


def main():
    ap = argparse.ArgumentParser(
        description="Zero-credential live PAPER TSMOM (real Binance public bars, "
                    "local sandbox fills, NO real orders).")
    ap.add_argument("--coins", default="BTC",
                    help="comma list of " + ",".join(_COIN_TO_INSTRUMENT))
    ap.add_argument("--timeframe", default="1d", choices=list(_TF_TO_BARSPEC),
                    help="1d = validated daily TSMOM; 1h/1m = demo mechanics only")
    ap.add_argument("--equity", type=float, default=10_000.0)
    ap.add_argument("--lookback", type=int, default=28)
    a = ap.parse_args()

    coins = [c.strip().upper() for c in a.coins.split(",") if c.strip()]
    bad = [c for c in coins if c not in _COIN_TO_INSTRUMENT]
    if bad:
        ap.error(f"unknown coin(s): {bad}; choose from {list(_COIN_TO_INSTRUMENT)}")

    print("=" * 70)
    print("  TSMOM LIVE *PAPER* TRADING  (NautilusTrader 1.228.0)")
    print("  - REAL Binance public market data, ZERO credentials, ZERO funds at risk")
    print("  - Fills are LOCAL sandbox simulation; NO real orders are ever sent")
    print("  - Objective: CAPITAL PRESERVATION + backtest/live parity, NOT profit")
    print(f"  coins={coins} timeframe={a.timeframe} equity={a.equity} "
          f"lookback={a.lookback}")
    if a.timeframe != "1d":
        print("  WARNING: non-daily timeframe is a mechanics demo, NOT the validated "
              "daily-TSMOM edge.")
    print("  Ctrl-C to stop cleanly.")
    print("=" * 70)

    node = _build_node(coins, a.timeframe, a.equity, a.lookback)
    try:
        node.run()  # blocks; installs SIGINT/SIGTERM -> stop
    finally:
        node.dispose()


if __name__ == "__main__":
    main()
