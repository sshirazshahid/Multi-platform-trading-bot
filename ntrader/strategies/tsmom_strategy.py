"""Strategy A — faithful NautilusTrader port of the LIVE discrete TSMOM signal.

Mirrors core/tsmom_signal.py bar-for-bar: long-only, open when N-day momentum > 0
and flat, close on momentum flip. Decisions are driven by an INTERNAL signal-state
flag (``_intended_long``), NOT by async fill state, so the OPEN/CLOSE decision-bar
sequence matches the pure-python reference exactly regardless of fill timing.

Risk: every order is gated by the authoritative pretrade_guard (CLAUDE.md §2). A
mandatory 8% disaster stop is placed atomically on position open (fail-closed: if
the stop can't be placed, the position is closed). Warehouse logging mirrors
core/warehouse.py via the shim (separate sqlite). leverage is pinned to 1.
"""
from __future__ import annotations

import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

from nautilus_trader.config import StrategyConfig  # noqa: E402
from nautilus_trader.model.currencies import USDT  # noqa: E402
from nautilus_trader.model.data import BarType  # noqa: E402
from nautilus_trader.model.enums import OrderSide  # noqa: E402
from nautilus_trader.model.identifiers import InstrumentId  # noqa: E402
from nautilus_trader.trading.strategy import Strategy  # noqa: E402

from ntrader.strategies.pretrade_guard import GuardLimits, check_order  # noqa: E402
from ntrader.strategies.tsmom_reference import min_bars, vol_scaled_size_pct  # noqa: E402


class TSMOMConfig(StrategyConfig, frozen=True):
    instrument_id: str
    bar_type: str
    lookback: int = 28
    sl_pct: float = 8.0
    leverage: int = 1
    enable_stop: bool = True
    record_warehouse: bool = False
    warehouse_path: str | None = None
    fallback_equity: float = 10_000.0


class TSMOMStrategy(Strategy):
    def __init__(self, config: TSMOMConfig):
        super().__init__(config)
        self._closes: list[float] = []
        self._i: int = -1
        self._intended_long: bool = False
        self.decision_bars: list[tuple[int, str]] = []
        self._wh = None
        self._open_trade_id = None

    def on_start(self):
        assert int(self.config.leverage) == 1, "TSMOM preservation: leverage must be 1"
        self.instrument = self.cache.instrument(InstrumentId.from_str(self.config.instrument_id))
        self._min_bars = min_bars(self.config.lookback)
        if self.config.record_warehouse:
            from ntrader.adapters.warehouse_shim import WarehouseShim
            self._wh = WarehouseShim(self.config.warehouse_path)
        self.subscribe_bars(BarType.from_str(self.config.bar_type))

    # ── equity / exposure helpers ────────────────────────────────────────────
    def _equity(self) -> float:
        try:
            acct = self.portfolio.account(self.instrument.id.venue)
            bal = acct.balance_total(USDT)
            if bal is not None:
                return float(bal.as_double())
        except Exception:
            pass
        return float(self.config.fallback_equity)

    def _existing_gross(self, px: float) -> float:
        # PORTFOLIO-WIDE gross exposure across ALL open positions at the venue
        # (CLAUDE.md section 2 12% cap is portfolio-wide, not per-instrument).
        try:
            exps = self.portfolio.net_exposures(self.instrument.id.venue)
            if exps:
                return sum(abs(float(m.as_double())) for m in exps.values())
        except Exception:
            pass
        tot = 0.0
        for p in self.cache.positions_open(instrument_id=self.instrument.id):
            tot += abs(float(p.quantity.as_double())) * px
        return tot

    # ── signal ────────────────────────────────────────────────────────────────
    def on_bar(self, bar):
        self._i += 1
        px = float(bar.close)
        self._closes.append(px)
        if (self._i + 1) < self._min_bars:
            return
        mom = self._closes[-1] / self._closes[-1 - int(self.config.lookback)] - 1.0
        mom_positive = mom > 0.0
        if mom_positive and not self._intended_long:
            self._intended_long = True
            self.decision_bars.append((self._i, "OPEN"))
            self._try_open(px)
        elif (not mom_positive) and self._intended_long:
            self._intended_long = False
            self.decision_bars.append((self._i, "CLOSE"))
            self._do_close()

    def _try_open(self, px: float):
        size_pct = vol_scaled_size_pct(self._closes)
        equity = self._equity()
        lev = int(self.config.leverage)
        notional = equity * (size_pct / 100.0) * lev
        qty = (notional / px) if px > 0 else 0.0
        guard = check_order(
            equity=equity, order_notional=qty * px,
            existing_gross_notional=self._existing_gross(px),
            sl_pct=float(self.config.sl_pct), leverage=lev, limits=GuardLimits(),
        )
        if (not guard.allowed) or qty <= 0:
            if self._wh:
                self._wh.record_candidate(
                    exchange="SIM", symbol=str(self.instrument.id.symbol), side="buy",
                    leverage=lev, size_pct=size_pct, decision="SKIP", skip_reason=guard.reason)
            return
        order = self.order_factory.market(
            instrument_id=self.instrument.id, order_side=OrderSide.BUY,
            quantity=self.instrument.make_qty(qty))
        self.submit_order(order)
        if self._wh:
            self._wh.record_candidate(
                exchange="SIM", symbol=str(self.instrument.id.symbol), side="buy",
                leverage=lev, size_pct=size_pct, entry_px=px, decision="OPEN")

    def _do_close(self):
        for p in self.cache.positions_open(instrument_id=self.instrument.id):
            self.close_position(p)
        try:
            self.cancel_all_orders(self.instrument.id)
        except Exception:
            pass

    # ── lifecycle: stop + warehouse ────────────────────────────────────────────
    def on_position_opened(self, event):
        pos = self.cache.position(event.position_id)
        if pos is None:
            return
        entry_px = float(pos.avg_px_open)
        if self._wh:
            self._open_trade_id = self._wh.record_trade_open(
                exchange="SIM", symbol=str(self.instrument.id.symbol), side="buy",
                ts_entry=float(getattr(pos, "ts_opened", 0) or 0) / 1e9, entry_px=entry_px,
                size=abs(float(pos.quantity.as_double())), leverage=int(self.config.leverage),
                strategy_family="tsmom", mode="PAPER")
        if self.config.enable_stop:
            self._place_stop(pos, entry_px)

    def _place_stop(self, pos, entry_px: float):
        stop_px = entry_px * (1.0 - float(self.config.sl_pct) / 100.0)
        try:
            stop = self.order_factory.stop_market(
                instrument_id=self.instrument.id, order_side=OrderSide.SELL,
                quantity=self.instrument.make_qty(abs(float(pos.quantity.as_double()))),
                trigger_price=self.instrument.make_price(stop_px), reduce_only=True)
            self.submit_order(stop)
        except Exception as e:  # fail-closed: no stop -> don't hold the position
            self.log.error(f"stop placement failed, fail-closed close: {e}")
            self.close_position(pos)

    def on_position_closed(self, event):
        if self._wh and self._open_trade_id:
            try:
                pnl = float(event.realized_pnl.as_double())
            except Exception:
                pnl = float(getattr(event, "realized_pnl", 0.0) or 0.0)
            self._wh.record_trade_close(
                trade_id=self._open_trade_id,
                ts_exit=float(getattr(event, "ts_event", 0) or 0) / 1e9,
                exit_px=float(getattr(event, "last_px", 0) or 0), realized_pnl=pnl,
                exit_reason="tsmom_flip_or_stop")
            self._open_trade_id = None
