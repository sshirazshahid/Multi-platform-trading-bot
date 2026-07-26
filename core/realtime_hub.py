"""Synchronous, lazy bridge to the asynchronous realtime stream manager."""

from __future__ import annotations

import asyncio
import concurrent.futures
import math
import threading
import time
from collections.abc import Callable, Mapping
from typing import Any

from core.realtime_streams import (
    RealtimeStreamManager,
    StreamKey,
    ccxt_pro_adapter_factory,
)

DEFAULT_MAX_TOTAL_CONNECTIONS = 24
DEFAULT_MAX_CONNECTIONS_PER_VENUE = 8
DEFAULT_IDLE_TIMEOUT_SECONDS = 15 * 60.0


class LazyRealtimeHub:
    """Start one bounded ccxt.pro stream pair per execution-sensitive symbol.

    A first lookup schedules the subscription and returns ``None`` until the
    stream has made progress. This lets the caller use REST as a temporary
    fallback while preserving WebSocket as the primary source thereafter.
    """

    def __init__(
        self,
        *,
        credentials: Mapping[str, Mapping[str, Any]] | None = None,
        reconciliation_callback: Callable[[Any], Any] | None = None,
        adapter_factory: Callable[..., Any] = ccxt_pro_adapter_factory,
        manager_factory: Callable[..., RealtimeStreamManager] = RealtimeStreamManager,
        max_total_connections: int = DEFAULT_MAX_TOTAL_CONNECTIONS,
        max_connections_per_venue: int = DEFAULT_MAX_CONNECTIONS_PER_VENUE,
        idle_timeout_seconds: float = DEFAULT_IDLE_TIMEOUT_SECONDS,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        for name, value in (
            ("max_total_connections", max_total_connections),
            ("max_connections_per_venue", max_connections_per_venue),
        ):
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if (
            isinstance(idle_timeout_seconds, bool)
            or not isinstance(idle_timeout_seconds, (int, float))
            or not math.isfinite(float(idle_timeout_seconds))
            or float(idle_timeout_seconds) <= 0
        ):
            raise ValueError("idle_timeout_seconds must be a positive finite number")
        if not callable(monotonic):
            raise TypeError("monotonic must be callable")

        self._credentials = {
            str(venue).lower(): {
                key: value for key, value in values.items() if value
            }
            for venue, values in (credentials or {}).items()
        }
        self._reconciliation_callback = reconciliation_callback
        self._adapter_factory = adapter_factory
        self._manager_factory = manager_factory
        self._max_total_connections = int(max_total_connections)
        self._max_connections_per_venue = int(max_connections_per_venue)
        self._idle_timeout_seconds = float(idle_timeout_seconds)
        self._monotonic = monotonic
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._run_loop,
            daemon=True,
            name="realtime-stream-hub",
        )
        self._thread.start()
        self._lock = threading.RLock()
        self._managers: dict[StreamKey, RealtimeStreamManager] = {}
        self._pending: dict[StreamKey, concurrent.futures.Future[Any]] = {}
        self._retiring: dict[StreamKey, concurrent.futures.Future[Any]] = {}
        self._last_used: dict[StreamKey, float] = {}
        self._errors: dict[StreamKey, str] = {}
        self._closed = False

    def _run_loop(self) -> None:
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            # ``loop.stop()`` alone leaks the Proactor self-pipe sockets on
            # Windows. Drain any last cancellation callbacks, then close the
            # owned loop in its own thread.
            pending = tuple(asyncio.all_tasks(self._loop))
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            try:
                self._loop.run_until_complete(self._loop.shutdown_asyncgens())
            except Exception:
                pass
            self._loop.close()

    @staticmethod
    def _market_name(market: str) -> str:
        return "perpetual" if str(market).lower() in {
            "future", "futures", "perp", "perpetual", "swap"
        } else str(market).lower()

    @staticmethod
    def _manager_is_running(manager: RealtimeStreamManager) -> bool:
        try:
            return bool(manager.is_running)
        except Exception:
            return False

    def _connection_count_locked(self, venue: str | None = None) -> int:
        """Count live, creating, and independently retiring connections.

        A pending replacement owns the single slot of the manager it first
        stops, so that victim is intentionally represented only by the pending
        key. Standalone idle retirements remain counted until their stop
        coroutine completes, preventing a transient over-cap connection.
        """

        keys = tuple(self._managers) + tuple(self._pending) + tuple(self._retiring)
        if venue is None:
            return len(keys)
        return sum(key.venue == venue for key in keys)

    def _has_capacity_locked(self, venue: str) -> bool:
        return (
            self._connection_count_locked() < self._max_total_connections
            and self._connection_count_locked(venue)
            < self._max_connections_per_venue
        )

    def _remove_manager_locked(self, key: StreamKey) -> RealtimeStreamManager | None:
        manager = self._managers.pop(key, None)
        if manager is not None:
            self._last_used.pop(key, None)
        return manager

    def _reclaimable_locked(
        self,
        now: float,
        *,
        exclude: frozenset[StreamKey] = frozenset(),
    ) -> list[tuple[StreamKey, RealtimeStreamManager]]:
        reclaimable: list[tuple[float, StreamKey, RealtimeStreamManager]] = []
        for key, manager in self._managers.items():
            if key in exclude:
                continue
            last_used = self._last_used.get(key, float("-inf"))
            idle = now - last_used >= self._idle_timeout_seconds
            if idle or not self._manager_is_running(manager):
                reclaimable.append((last_used, key, manager))
        reclaimable.sort(key=lambda item: (item[0], item[1]))
        return [(key, manager) for _last_used, key, manager in reclaimable]

    def _lru_key_locked(self, venue: str | None = None) -> StreamKey | None:
        candidates = [
            key for key in self._managers if venue is None or key.venue == venue
        ]
        if not candidates:
            return None
        return min(
            candidates,
            key=lambda key: (self._last_used.get(key, float("-inf")), key),
        )

    async def _stop_managers(
        self,
        managers: tuple[RealtimeStreamManager, ...],
    ) -> None:
        unique = tuple({id(manager): manager for manager in managers}.values())
        if unique:
            await asyncio.gather(
                *(manager.stop() for manager in unique),
                return_exceptions=True,
            )

    def _retirement_done(
        self,
        keys: tuple[StreamKey, ...],
        future: concurrent.futures.Future[Any],
    ) -> None:
        with self._lock:
            for key in keys:
                if self._retiring.get(key) is future:
                    self._retiring.pop(key, None)
            try:
                future.result()
            except Exception:
                pass

    def _schedule_retirement_locked(
        self,
        victims: list[tuple[StreamKey, RealtimeStreamManager]],
    ) -> None:
        if not victims:
            return
        keys = tuple(key for key, _manager in victims)
        managers = tuple(manager for _key, manager in victims)
        future = asyncio.run_coroutine_threadsafe(
            self._stop_managers(managers), self._loop
        )
        for key in keys:
            self._retiring[key] = future
        future.add_done_callback(
            lambda completed, retired_keys=keys: self._retirement_done(
                retired_keys, completed
            )
        )

    def _ensure(self, venue: str, market: str, symbol: str) -> StreamKey:
        key = StreamKey(venue, self._market_name(market), symbol)
        with self._lock:
            if self._closed:
                return key
            now = float(self._monotonic())
            if key in self._pending:
                self._last_used[key] = now
                return key
            if key in self._retiring:
                self._errors[key] = "CapacityRetirementInProgress"
                return key
            manager = self._managers.get(key)
            if manager is not None and self._manager_is_running(manager):
                self._last_used[key] = now
                self._errors.pop(key, None)
                idle_victims = self._reclaimable_locked(
                    now, exclude=frozenset((key,))
                )
                for victim_key, _victim in idle_victims:
                    self._remove_manager_locked(victim_key)
                self._schedule_retirement_locked(idle_victims)
                return key

            replacement_victims: list[
                tuple[StreamKey, RealtimeStreamManager]
            ] = []
            if manager is not None:
                # A channel task should normally retry forever. If it still
                # terminated because of an unexpected BaseException or a
                # future regression, remove the dead manager before scheduling
                # its replacement so this key can never become unrecreatable.
                self._remove_manager_locked(key)
                replacement_victims.append((key, manager))

            # When capacity is available, replace at most one idle/dead
            # connection. One pending creation can safely reserve one retiring
            # connection's slot; removing several here would let concurrent
            # callers temporarily oversubscribe while those stops are pending.
            if not replacement_victims and self._has_capacity_locked(key.venue):
                reclaimable = self._reclaimable_locked(now)
                if reclaimable:
                    victim_key, victim = reclaimable[0]
                    self._remove_manager_locked(victim_key)
                    replacement_victims.append((victim_key, victim))

            # Pending creations consume capacity and are never cancelled merely
            # to admit a newer symbol. Prefer a same-venue LRU victim when the
            # venue cap binds, then a global LRU victim when the total cap binds.
            while not self._has_capacity_locked(key.venue):
                if (
                    self._connection_count_locked(key.venue)
                    >= self._max_connections_per_venue
                ):
                    victim_key = self._lru_key_locked(key.venue)
                else:
                    victim_key = self._lru_key_locked()
                if victim_key is None:
                    self._schedule_retirement_locked(replacement_victims)
                    self._errors[key] = "CapacityLimit"
                    return key
                victim = self._remove_manager_locked(victim_key)
                if victim is not None:
                    replacement_victims.append((victim_key, victim))

            managers_to_stop = tuple(
                manager for _victim_key, manager in replacement_victims
            )
            coroutine = (
                self._create_after_stops(key, managers_to_stop)
                if managers_to_stop
                else self._create_stream(key)
            )
            future = asyncio.run_coroutine_threadsafe(
                coroutine, self._loop
            )
            self._pending[key] = future
            self._last_used[key] = now
            self._errors.pop(key, None)
            future.add_done_callback(lambda completed, stream_key=key: self._done(
                stream_key, completed
            ))
        return key

    async def _create_stream(self, key: StreamKey) -> RealtimeStreamManager:
        credentials = self._credentials.get(key.venue, {})
        private = bool(credentials.get("apiKey") and credentials.get("secret"))
        adapter = self._adapter_factory(
            key.venue,
            market=key.market,
            credentials=credentials,
        )
        manager = self._manager_factory(
            reconciliation_callback=self._reconciliation_callback,
        )
        registered = False
        try:
            manager.register(
                adapter,
                venue=key.venue,
                market=key.market,
                symbols=(key.symbol,),
                private=private,
            )
            registered = True
            await manager.start()
            return manager
        except BaseException:
            # ``close()`` cancels pending creation futures. Always close an
            # adapter that was already constructed so shutdown cannot leak a
            # websocket or private user stream.
            try:
                if registered:
                    await manager.stop()
                else:
                    await adapter.close()
            except BaseException:
                pass
            raise

    async def _replace_stream(
        self,
        key: StreamKey,
        stale_manager: RealtimeStreamManager,
    ) -> RealtimeStreamManager:
        return await self._create_after_stops(key, (stale_manager,))

    async def _create_after_stops(
        self,
        key: StreamKey,
        stale_managers: tuple[RealtimeStreamManager, ...],
    ) -> RealtimeStreamManager:
        await self._stop_managers(stale_managers)
        return await self._create_stream(key)

    def _done(
        self,
        key: StreamKey,
        future: concurrent.futures.Future[Any],
    ) -> None:
        with self._lock:
            self._pending.pop(key, None)
            try:
                manager = future.result()
            except Exception as exc:  # secrets are never copied into this value
                if not self._closed:
                    self._errors[key] = type(exc).__name__
            else:
                if self._closed:
                    self._schedule_retirement_locked([(key, manager)])
                else:
                    self._managers[key] = manager
                    self._last_used[key] = float(self._monotonic())
                    self._errors.pop(key, None)

    def get_order_book_dict(
        self,
        venue: str,
        market: str,
        symbol: str,
    ) -> dict[str, Any] | None:
        key = self._ensure(venue, market, symbol)
        with self._lock:
            manager = self._managers.get(key)
        if manager is None:
            return None
        return manager.get_order_book_dict(key.venue, key.market, key.symbol)

    def get_order_event_dict(
        self,
        venue: str,
        market: str,
        symbol: str,
        order_id: str | None = None,
    ) -> dict[str, Any] | None:
        key = self._ensure(venue, market, symbol)
        with self._lock:
            manager = self._managers.get(key)
        if manager is None:
            return None
        return manager.get_order_event_dict(
            key.venue, key.market, key.symbol, order_id
        )

    def health(self) -> dict[str, dict[str, Any]]:
        with self._lock:
            managers = tuple(self._managers.items())
            pending = tuple(self._pending)
            retiring = tuple(self._retiring)
            errors = dict(self._errors)
        report: dict[str, dict[str, Any]] = {}
        for key, manager in managers:
            health = manager.health(key.venue, key.market, key.symbol)
            report["|".join((key.venue, key.market, key.symbol))] = {
                "healthy": health.healthy,
                "connected": health.connected,
                "reason": health.reason,
                "sequence": health.sequence,
            }
        for key in pending:
            report["|".join((key.venue, key.market, key.symbol))] = {
                "healthy": False, "connected": False, "reason": "warming"
            }
        for key in retiring:
            report["|".join((key.venue, key.market, key.symbol))] = {
                "healthy": False, "connected": False, "reason": "retiring"
            }
        for key, error_type in errors.items():
            report["|".join((key.venue, key.market, key.symbol))] = {
                "healthy": False,
                "connected": False,
                "reason": f"stream_error:{error_type}",
            }
        return report

    async def _stop_all(self) -> None:
        with self._lock:
            managers = tuple(self._managers.values())
            self._managers.clear()
            self._last_used.clear()
        if managers:
            await asyncio.gather(
                *(manager.stop() for manager in managers),
                return_exceptions=True,
            )

    def close(self, timeout: float = 10.0) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            pending = tuple(self._pending.values())
            retiring = tuple(set(self._retiring.values()))
        for future in pending:
            future.cancel()
        try:
            asyncio.run_coroutine_threadsafe(
                self._stop_all(), self._loop
            ).result(timeout=timeout)
        except Exception:
            pass
        if retiring:
            concurrent.futures.wait(retiring, timeout=timeout)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=timeout)


__all__ = [
    "DEFAULT_IDLE_TIMEOUT_SECONDS",
    "DEFAULT_MAX_CONNECTIONS_PER_VENUE",
    "DEFAULT_MAX_TOTAL_CONNECTIONS",
    "LazyRealtimeHub",
]
