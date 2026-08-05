"""
core/engine/imported_protect.py — BotEngine _ImportedProtectMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403


class _ImportedProtectMixin:
    def _protect_imported_positions(self, positions: list) -> None:
        """Compute ATR-based SL/TP for manually-imported positions and place
        them on the exchange.  Falls back to liquidation-price fallback if
        OHLCV is unavailable (leaves the existing tracker stop_loss intact).

        2026-04-16 follow-up: imported positions arrive with stop_loss=liq
        (placeholder) and take_profit=0. This method refines them to
        ATR*1.5 / 2.5:1 RR using the risk manager, then asks the order
        manager to place protective orders on the exchange.
        """
        import pandas as pd

        from utils.indicators import atr as _atr

        for pos in positions:
            try:
                exchange = self.active_exchanges.get(pos.exchange.lower())
                if not exchange:
                    logger.warning(
                        f"[Protect] No client for {pos.exchange} — "
                        f"cannot place SL for imported {pos.symbol}")
                    continue

                # 2026-04-20: stale-list guard. The caller passes a snapshot
                # of positions, but concurrent check_sl_tp / close flows may
                # have already closed the position. Placing SL on a flat
                # symbol cascades into a naked reverse via the close-retry
                # path. Re-verify against the exchange and tracker before
                # proceeding.
                if not getattr(pos, "is_open", True):
                    continue
                try:
                    _live = exchange.fetch_positions([pos.symbol]) or []
                    _live_sz = 0.0
                    for _p in _live:
                        _live_sz = abs(float(_p.get("contracts") or _p.get("contractSize") or 0))
                        if _live_sz > 0:
                            break
                    if _live_sz <= 0:
                        logger.info(
                            f"[Protect] {pos.symbol} flat on exchange — "
                            f"skipping SL placement (stale-list guard)")
                        continue
                except Exception as _fe:
                    logger.debug(
                        f"[Protect] live-size check failed for {pos.symbol}: "
                        f"{str(_fe)[:120]} — proceeding")

                # Fetch 1h OHLCV for ATR(14)
                try:
                    raw = exchange.fetch_ohlcv(pos.symbol, "1h", 100, "futures")
                except Exception as e:
                    logger.warning(
                        f"[Protect] OHLCV fetch failed for {pos.symbol}: "
                        f"{str(e)[:120]} — SL left at liquidation fallback")
                    continue
                if not raw or len(raw) < 20:
                    logger.warning(
                        f"[Protect] Insufficient OHLCV for {pos.symbol} "
                        f"({len(raw) if raw else 0} bars) — SL left at fallback")
                    continue

                df = pd.DataFrame(raw, columns=["ts", "o", "h", "l", "c", "v"])
                atr_val = float(_atr(df["h"], df["l"], df["c"], 14).iloc[-1] or 0)
                if atr_val <= 0:
                    logger.warning(
                        f"[Protect] ATR invalid for {pos.symbol} — fallback")
                    continue

                # Compute SL/TP via risk manager. ATR*1.5 SL, 2.5:1 RR.
                sl, tp = self.risk.get_sl_tp(
                    entry=pos.entry_price,
                    side=pos.side,
                    atr=atr_val,
                    atr_sl_mult=1.5,
                    atr_tp_mult=3.75,  # 1.5 * 2.5 = 2.5:1 RR
                    leverage=pos.leverage,
                )

                # Update tracker state
                pos.stop_loss = sl
                pos.take_profit = tp

                # Place on exchange
                try:
                    self.order_mgr._place_exchange_sl_tp(
                        exchange, pos, sl, tp, pos.side,
                        pos.symbol, pos.size, "futures")
                    logger.info(
                        f"[Protect] {pos.symbol} {pos.side.upper()} "
                        f"imported SL={sl:.4f} TP={tp:.4f} (ATR={atr_val:.4f})")
                except Exception as e:
                    logger.error(
                        f"[Protect] SL placement failed for {pos.symbol}: "
                        f"{str(e)[:150]}")

            except Exception as e:
                logger.error(
                    f"[Protect] Unexpected error protecting {getattr(pos, 'symbol', '?')}: "
                    f"{str(e)[:150]}")

        # Persist the updated SL/TP
        try:
            self.tracker._save()
        except Exception:
            pass


