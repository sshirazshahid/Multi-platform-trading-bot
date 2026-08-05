"""
core/order_mgmt/state.py — OrderManager _StateMixin mixin (Phase D4).
"""
import json
import threading
import time

from loguru import logger

from core.order_mgmt.helpers import (
    CLOSE_FAIL_COUNT_PATH,
    ORDER_MODE_STATE_PATH,
    PAPER_FUNDING_WINDOWS_PATH,
    SL_WIDENED_STATE_PATH,
    _mark_from_ticker,
)
from core.position_tracker import Position
from exchanges.base import BaseExchange

class _StateMixin:
    def set_exchanges(self, exchanges: dict) -> None:
        """Wire the BotEngine exchange registry. Used by _finalize_close
        to look up the exchange for funding-history fetches without having
        an exchange object passed in."""
        self._exchanges = dict(exchanges or {})

    def _exchange_for(self, name: str):
        """Return the exchange client for `name` (case-insensitive), or None."""
        if not name or not self._exchanges:
            return None
        key = str(name).lower()
        for k, v in self._exchanges.items():
            if str(k).lower() == key:
                return v
        return None

    def _update_trade_extremes(self, pos, price: float) -> None:
        """Running per-position MFE/MAE from the 10s monitor (C7).

        Fractions of entry price, unleveraged, positive magnitudes (the
        shadow_outcomes convention). In-memory peaks on the position;
        the warehouse row is updated only when a NEW extreme appears and at
        most once per 5s per position — trending moves never hammer SQLite.
        DistFitSL fits on these instead of exit-price proxies. Poll-sampled,
        so wick extremes between ticks are missed — still strictly less
        biased than the proxies. Never raises. Restart caveat: in-memory
        peaks reset; the warehouse keeps the best-known values.
        """
        try:
            entry = float(getattr(pos, "entry_price", 0) or 0)
            px = float(price or 0)
            if entry <= 0 or px <= 0:
                return
            sign = 1.0 if str(getattr(pos, "side", "")).lower() == "buy" else -1.0
            frac = sign * (px - entry) / entry
            mfe = max(float(getattr(pos, "_mfe_frac", 0.0) or 0.0),
                      max(frac, 0.0))
            mae = max(float(getattr(pos, "_mae_frac", 0.0) or 0.0),
                      max(-frac, 0.0))
            improved = (mfe > float(getattr(pos, "_mfe_frac", -1.0) or 0.0)
                        or mae > float(getattr(pos, "_mae_frac", -1.0) or 0.0))
            pos._mfe_frac = mfe
            pos._mae_frac = mae
            if improved:
                pos._ext_dirty = True  # unpersisted extreme pending
            if not bool(getattr(pos, "_ext_dirty", False)):
                return
            import time as _t
            now = _t.time()
            if now - float(getattr(pos, "_ext_persist_ts", 0.0) or 0.0) < 5.0:
                return  # throttled — dirty flag makes a later tick catch up
            pos._ext_persist_ts = now
            from core.warehouse import get_warehouse
            ok = get_warehouse().update_trade_extremes(
                exchange=str(getattr(pos, "exchange", "") or ""),
                symbol=str(getattr(pos, "symbol", "") or ""),
                side=str(getattr(pos, "side", "") or ""),
                ts_entry=float(getattr(pos, "open_time", 0) or 0),
                mfe=mfe, mae=mae)
            # Review fix 2026-07-09 (LOW): clear the dirty flag only on a
            # successful write (update_trade_extremes returns bool, never
            # raises) — clearing it beforehand cancelled the documented
            # catch-up after a failed/locked write, losing final extremes.
            pos._ext_dirty = not ok
        except Exception as e:
            logger.debug(f"[Extremes] update skipped: {e}")

    @staticmethod
    def _maintenance_margin_rate(exchange, pos) -> float:
        """Best available PAPER maintenance rate, normalized to a fraction."""
        try:
            explicit = float(getattr(pos, "maintenance_margin_rate", 0) or 0)
            if explicit > 0:
                return explicit / 100.0 if explicit > 0.1 else explicit
        except (TypeError, ValueError):
            pass
        try:
            client = getattr(exchange, "exchange", None)
            market = client.market(pos.symbol) if client is not None else {}
            info = market.get("info") if isinstance(market, dict) else {}
            for key in (
                "maintenanceMarginRate",
                "maintMarginRate",
                "maintMarginPercent",
                "maintenanceMarginPercent",
            ):
                value = info.get(key) if isinstance(info, dict) else None
                if value is None:
                    continue
                rate = float(value)
                if rate > 0:
                    return rate / 100.0 if rate > 0.1 else rate
        except (AttributeError, TypeError, ValueError):
            pass
        try:
            from config import PAPER_MAINTENANCE_MARGIN_RATE

            fallback = float(PAPER_MAINTENANCE_MARGIN_RATE)
        except (ImportError, TypeError, ValueError):
            fallback = 0.01
        return min(0.2, max(0.0001, fallback))

    def _required_futures_mark(self, exchange, pos, ticker: dict) -> float:
        mark = _mark_from_ticker(ticker)
        if mark > 0:
            return mark
        try:
            payload = exchange.fetch_mark_index(pos.symbol, "futures") or {}
            mark = float(payload.get("mark") or 0)
            stamp = float(payload.get("mark_ts") or payload.get("ts") or 0)
            # Unit-skew hardening (2026-07-20, AXS follow-up): a producer
            # regression to millisecond stamps would compute a hugely negative
            # age and silently zero the mark every cycle — the exact failure
            # class behind the 07-18 latch. Mirrors _mark_from_ticker.
            if stamp > 100_000_000_000:
                stamp /= 1000.0
            age = time.time() - stamp
            # 2026-07-18: window widened 15s -> 90s. fetch_mark_index's
            # mark-OHLCV fallback serves the last CLOSED 1m candle, so ages
            # of 30-120s are structural, not a fault; the 15s bound made
            # this helper return 0 for EVERY futures paper position (first
            # hit: AXS — latched a data incident + halted the engine on the
            # first band trade). 90s still bounds trigger staleness; the
            # exchange-side conditionals remain the primary trigger (C3).
            if mark > 0 and -2.0 <= age <= 90.0:
                return mark
        except (AttributeError, TypeError, ValueError):
            pass
        return 0.0

    def _target_traded_through(self, pos, price: float) -> bool:
        target = float(getattr(pos, "take_profit", 0) or 0)
        if target <= 0:
            return False
        if not self.enforce_mark_price_triggers:
            return price >= target if pos.side == "buy" else price <= target
        # A resting reduce-only limit touching the mark does not prove a fill.
        return price > target if pos.side == "buy" else price < target

    def _close_take_profit(self, exchange, pos, observed_price: float):
        """Model an already-resting PAPER target as a limit fill at target."""
        if self.dry_run and self.enforce_mark_price_triggers:
            return self.close_position(
                exchange,
                pos,
                "take_profit",
                float(pos.take_profit),
                order_type="limit",
            )
        return self.close_position(
            exchange, pos, "take_profit", float(observed_price)
        )

    def _record_lifecycle(self, pos, event_type: str,
                          payload: dict = None) -> None:
        """Best-effort mid-trade audit row (C6, tpbot retrofit 2026-07-08).

        Writes ORDER_PLACED/FILL/PARTIAL_TP/SL_MOVE rows into
        warehouse.trade_events so the signal->order->fill->TP->close chain
        is auditable after the fact (the Jun-4 attribution-corruption class).
        Never raises and never blocks a trading path — a lost audit row is
        strictly better than a disturbed order flow.
        """
        try:
            from core.warehouse import get_warehouse
            get_warehouse().record_lifecycle_event(
                exchange=str(getattr(pos, "exchange", "") or ""),
                symbol=str(getattr(pos, "symbol", "") or ""),
                side=str(getattr(pos, "side", "") or ""),
                ts_entry=float(getattr(pos, "open_time", 0) or 0),
                event_type=event_type,
                payload=payload or {},
            )
        except Exception as e:
            logger.debug(f"[Warehouse] lifecycle {event_type} skipped: {e}")

    def flatten_all(self, reason: str, exchange_name: str = None,
                    market_type: str = None) -> dict:
        """Close every open tracked position (C9, tpbot retrofit 2026-07-08).

        The close-all primitive that never existed: breakers and the operator
        previously had nothing to CALL to flatten the book. This is a
        callable primitive ONLY — per the owner's no-auto-halt directive it
        is deliberately NOT wired to any automatic trigger (pinned by
        tests/test_flatten_all.py source-scan), and adding one is a policy
        decision for the owner, not a code default.

        Every close routes through ``close_position`` — the single close
        authority — so warehouse/wallet/risk/journal/notifier hooks fire
        exactly as for any other exit. Optional filters: ``exchange_name``
        (case-insensitive) and ``market_type`` ('futures'/'spot').

        Never raises. Returns {"closed": int, "failed": [(symbol, err)],
        "skipped": int} — skipped counts positions whose venue client could
        not be resolved from the registry (close attempt impossible).
        """
        result = {"closed": 0, "failed": [], "skipped": 0}
        try:
            open_positions = list(self.tracker.get_open() or [])
        except Exception as e:
            logger.error(f"[Orders] flatten_all: tracker.get_open failed: {e}")
            result["failed"].append(("<tracker>", str(e)[:200]))
            return result
        want_ex = str(exchange_name).lower() if exchange_name else None
        logger.warning(
            f"[Orders] FLATTEN_ALL invoked (reason={reason}, "
            f"exchange={want_ex or 'ALL'}, market={market_type or 'ALL'}, "
            f"open={len(open_positions)})")
        for pos in open_positions:
            try:
                if want_ex and str(getattr(pos, "exchange", "")).lower() != want_ex:
                    continue
                if market_type and getattr(pos, "market_type", None) != market_type:
                    continue
                ex = self._exchange_for(getattr(pos, "exchange", None))
                if ex is None:
                    logger.error(
                        f"[Orders] flatten_all: no client for exchange "
                        f"{getattr(pos, 'exchange', '?')} — skipping "
                        f"{getattr(pos, 'symbol', '?')}")
                    result["skipped"] += 1
                    continue
                res = self.close_position(ex, pos, reason=reason)
                if res is not None:
                    result["closed"] += 1
                    continue
                # Review fix 2026-07-09 (MEDIUM, confirmed twice):
                # close_position signals failure by RETURNING None (lock
                # timeout, no close price, venue close unconfirmed) — it does
                # not raise, so counting unconditionally reported a flat book
                # during the exact outages this primitive exists for. None is
                # also the benign no-op for a position that closed in a race;
                # the tracker decides which one it was.
                still_open = True
                try:
                    still_open = any(
                        getattr(p, "id", None) == pos.id
                        for p in (self.tracker.get_open() or []))
                except Exception:
                    still_open = True  # can't verify -> fail-closed: failed
                if still_open:
                    result["failed"].append(
                        (getattr(pos, "symbol", "?"),
                         "close unconfirmed (returned None; still open)"))
                else:
                    result["closed"] += 1
            except Exception as e:
                logger.error(
                    f"[Orders] flatten_all: close failed for "
                    f"{getattr(pos, 'symbol', '?')}: {str(e)[:200]}")
                result["failed"].append(
                    (getattr(pos, "symbol", "?"), str(e)[:200]))
        logger.warning(
            f"[Orders] FLATTEN_ALL done: closed={result['closed']} "
            f"failed={len(result['failed'])} skipped={result['skipped']}")
        return result

    def _load_order_mode_state(self) -> dict:
        """Load persisted _futures_disabled and _oneway_mode sets."""
        try:
            if self._order_mode_path.exists():
                data = json.loads(self._order_mode_path.read_text(encoding="utf-8"))
                return {
                    "futures_disabled": set(data.get("futures_disabled", [])),
                    "oneway_mode": set(data.get("oneway_mode", ["bitget"])),
                }
        except Exception:
            pass
        return {"futures_disabled": set(), "oneway_mode": {"bitget"}}

    def _save_order_mode_state(self):
        """Persist _futures_disabled and _oneway_mode so they survive restarts."""
        try:
            self._order_mode_path.parent.mkdir(parents=True, exist_ok=True)
            self._order_mode_path.write_text(json.dumps({
                "futures_disabled": list(self._futures_disabled),
                "oneway_mode": list(self._oneway_mode),
            }), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Orders] Failed to save order mode state: {e}")

    def _load_funding_windows(self) -> dict:
        """Restore per-venue settled funding windows (A5) — never raises."""
        try:
            if self._funding_windows_path.exists():
                data = json.loads(self._funding_windows_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    return {
                        str(k): int(v) for k, v in data.items()
                        if isinstance(v, (int, float))
                    }
        except Exception:
            pass
        return {}

    def _save_funding_windows(self):
        try:
            self._funding_windows_path.parent.mkdir(parents=True, exist_ok=True)
            self._funding_windows_path.write_text(
                json.dumps(self._last_funding_hour), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Orders] Failed to save funding windows: {e}")

    def _load_sl_widened(self) -> set:
        try:
            if self._sl_widened_path.exists():
                data = json.loads(self._sl_widened_path.read_text(encoding="utf-8"))
                return set(data)
        except Exception:
            pass
        return set()

    def _save_sl_widened(self):
        try:
            self._sl_widened_path.parent.mkdir(parents=True, exist_ok=True)
            self._sl_widened_path.write_text(
                json.dumps(list(self._sl_widened)), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Orders] Failed to save sl_widened: {e}")

    def cleanup_sl_widened(self):
        """Remove entries for positions that are no longer open."""
        open_ids = {p.id for p in self.tracker.get_open()}
        stale = self._sl_widened - open_ids
        if stale:
            self._sl_widened -= stale
            self._save_sl_widened()
            logger.debug(f"[Orders] Cleaned {len(stale)} stale sl_widened entries")

    def _load_close_fail_count(self) -> dict:
        try:
            if self._close_fail_path.exists():
                data = json.loads(
                    self._close_fail_path.read_text(encoding="utf-8"))
                # Only keep entries for positions that still exist
                open_ids = {p.id for p in self.tracker.get_open()}
                restored = {k: v for k, v in data.items() if k in open_ids}
                if restored:
                    logger.info(
                        f"[Orders] Restored close_fail_count for "
                        f"{len(restored)} position(s)")
                return restored
        except Exception:
            pass
        return {}

    def _save_close_fail_count(self):
        try:
            self._close_fail_path.parent.mkdir(parents=True, exist_ok=True)
            self._close_fail_path.write_text(
                json.dumps(self._close_fail_count), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Orders] Failed to save close_fail_count: {e}")

    def _position_lock(self, pid: str) -> threading.RLock:
        """Return the one shared RLock for this position id (created on first
        use under the guard). RLock — not Lock — because the fail-closed path
        _replace_exchange_sl -> _place_exchange_sl_tp -> close_position re-enters
        on the SAME thread for the SAME id (proof 2026-06-21). The guard holds
        ONLY for the dict get-or-create; the per-id lock is acquired by the
        caller OUTSIDE the guard so the guard never nests with anything."""
        with self._pos_locks_guard:
            lk = self._pos_locks.get(pid)
            if lk is None:
                lk = threading.RLock()
                self._pos_locks[pid] = lk
            return lk

    @staticmethod
    def _close_order_confirmed(order) -> bool:
        """Return True only when a close order response proves venue receipt/fill."""
        if not isinstance(order, dict):
            return False
        status = str(order.get("status") or "").lower()
        if status in {"rejected", "failed", "canceled", "cancelled"}:
            return False
        if order.get("id"):
            return True
        try:
            if float(order.get("filled") or 0) > 0:
                return True
        except Exception:
            pass
        return status in {"closed", "filled"}

