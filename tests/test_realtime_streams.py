from __future__ import annotations

import asyncio
import time
from collections import deque
from dataclasses import FrozenInstanceError
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from types import SimpleNamespace

import pytest

import core.realtime_streams as streams
from core.realtime_hub import LazyRealtimeHub
from core.realtime_streams import (
    BookUpdateKind,
    OrderBookUpdate,
    PrivateOrderEvent,
    RealtimeCache,
    RealtimeStreamManager,
    ReconnectPolicy,
    SequenceGapError,
    SequenceRegressionError,
    StreamChannel,
    StreamKey,
    StreamSettings,
)


class FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 7, 14, tzinfo=timezone.utc)
        self.elapsed = 1_000.0

    def now(self) -> datetime:
        return self.current

    def monotonic(self) -> float:
        return self.elapsed

    def advance(self, seconds: float) -> None:
        self.current += timedelta(seconds=seconds)
        self.elapsed += seconds


class FakeAdapter:
    """A deterministic adapter whose exhausted streams block until cancelled."""

    def __init__(self, *, public=(), private=()) -> None:
        self.public = deque(public)
        self.private = deque(private)
        self.public_calls = 0
        self.private_calls = 0
        self.public_cancelled = 0
        self.private_cancelled = 0
        self.closed = False

    async def watch_order_book(self, symbol: str):
        self.public_calls += 1
        return await self._next(self.public, public=True)

    async def watch_orders(self, symbol: str):
        self.private_calls += 1
        return await self._next(self.private, public=False)

    async def _next(self, actions, *, public: bool):
        await asyncio.sleep(0)
        if actions:
            action = actions.popleft()
            if isinstance(action, BaseException):
                raise action
            return action
        try:
            await asyncio.Future()
        except asyncio.CancelledError:
            if public:
                self.public_cancelled += 1
            else:
                self.private_cancelled += 1
            raise

    async def close(self) -> None:
        self.closed = True


def book(
    clock: FakeClock,
    sequence: int,
    *,
    kind: BookUpdateKind = BookUpdateKind.SNAPSHOT,
    bids=((100, 2), (99, 3)),
    asks=((101, 2), (102, 3)),
    first_sequence=None,
    previous_sequence=None,
) -> OrderBookUpdate:
    return OrderBookUpdate(
        key=StreamKey("binance", "perpetual", "BTC/USDT:USDT"),
        kind=kind,
        bids=bids,
        asks=asks,
        event_at=clock.now(),
        received_at=clock.now(),
        sequence=sequence,
        first_sequence=first_sequence,
        previous_sequence=previous_sequence,
        source="fake.public",
        payload={"nested": {"sequence": sequence}},
    )


def order(
    clock: FakeClock,
    sequence: int,
    *,
    order_id: str = "order-1",
    status: str = "open",
) -> PrivateOrderEvent:
    return PrivateOrderEvent(
        key=StreamKey("binance", "perpetual", "BTC/USDT:USDT"),
        order_id=order_id,
        status=status,
        event_at=clock.now(),
        received_at=clock.now(),
        sequence=sequence,
        payload={"id": order_id, "status": status, "fills": [{"amount": 0.25}]},
        source="fake.private",
    )


def settings(*, attempts: int = 4) -> StreamSettings:
    return StreamSettings(
        market_silence_seconds=15.0,
        private_silence_seconds=60.0,
        reconciliation_timeout_seconds=1.0,
        close_timeout_seconds=1.0,
        reconnect=ReconnectPolicy(
            initial_delay=0.0,
            maximum_delay=0.0,
            multiplier=2.0,
            max_attempts=attempts,
        ),
    )


async def eventually(predicate, *, turns: int = 100) -> None:
    for _ in range(turns):
        if predicate():
            return
        await asyncio.sleep(0)
    raise AssertionError("condition did not become true")


def eventually_sync(predicate, *, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.005)
    raise AssertionError("condition did not become true")


def test_progress_required_before_connected_stream_is_healthy() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        progressing = FakeAdapter(public=[book(clock, 1)])
        manager = RealtimeStreamManager(clock=clock, settings=settings())
        manager.register(
            progressing,
            venue="binance",
            market="futures",
            symbols="BTC/USDT:USDT",
            private=False,
        )
        await manager.start()
        await eventually(lambda: manager.is_healthy("binance", "perpetual", "BTC/USDT:USDT"))
        health = manager.health("binance", "perpetual", "BTC/USDT:USDT")
        assert health.connected is True
        assert health.sequence == 1
        assert health.reason == "healthy"
        await manager.stop()

        blocked = FakeAdapter()
        no_progress = RealtimeStreamManager(clock=clock, settings=settings())
        no_progress.register(
            blocked,
            venue="binance",
            market="perpetual",
            symbols="BTC/USDT:USDT",
            private=False,
        )
        await no_progress.start()
        health = no_progress.health("binance", "perpetual", "BTC/USDT:USDT")
        assert health.connected is True
        assert health.healthy is False
        assert health.reason == "no_progress"
        await no_progress.stop()

    asyncio.run(scenario())


def test_market_and_private_silence_fail_closed_at_strict_thresholds() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        adapter = FakeAdapter(public=[book(clock, 1)], private=[(order(clock, 1),)])
        manager = RealtimeStreamManager(clock=clock, settings=settings())
        manager.register(
            adapter,
            venue="binance",
            market="perpetual",
            symbols="BTC/USDT:USDT",
        )
        await manager.start()
        await eventually(
            lambda: manager.is_healthy(
                "binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PUBLIC
            )
            and manager.is_healthy("binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PRIVATE)
        )

        clock.advance(15.0)
        assert manager.is_healthy("binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PUBLIC)
        clock.advance(0.001)
        public_health = manager.health(
            "binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PUBLIC
        )
        assert public_health.healthy is False
        assert public_health.reason == "market_silence"
        assert manager.get_order_book_dict("binance", "perpetual", "BTC/USDT:USDT") is None
        assert (
            manager.get_order_book_dict(
                "binance", "perpetual", "BTC/USDT:USDT", require_healthy=False
            )["nonce"]
            == 1
        )

        clock.advance(44.999)
        assert manager.is_healthy("binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PRIVATE)
        clock.advance(0.001)
        private_health = manager.health(
            "binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PRIVATE
        )
        assert private_health.healthy is False
        assert private_health.reason == "private_silence"
        assert manager.get_order_event("binance", "perpetual", "BTC/USDT:USDT") is None
        await manager.stop()

    asyncio.run(scenario())


def test_snapshot_delta_gap_invalidates_until_recovery_snapshot() -> None:
    clock = FakeClock()
    cache = RealtimeCache()
    cache.apply_order_book(book(clock, 10))

    with pytest.raises(SequenceGapError) as captured:
        cache.apply_order_book(
            book(
                clock,
                12,
                kind=BookUpdateKind.DELTA,
                bids=((100, 0), (99.5, 4)),
                asks=(),
            )
        )
    assert captured.value.previous_sequence == 10
    assert captured.value.received_sequence == 12
    assert cache.get_order_book("binance", "perpetual", "BTC/USDT:USDT") is None

    recovered = cache.apply_order_book(book(clock, 12))
    assert recovered.sequence == 12
    with pytest.raises(SequenceRegressionError):
        cache.apply_order_book(book(clock, 11))


def test_thread_safe_cache_lookups_return_immutable_records_and_fresh_dicts() -> None:
    clock = FakeClock()
    cache = RealtimeCache(max_private_orders=2)
    original = book(clock, 1)
    with pytest.raises(FrozenInstanceError):
        original.sequence = 99
    with pytest.raises(TypeError):
        original.payload["nested"]["sequence"] = 99

    cache.apply_order_book(original)
    snapshot = cache.apply_order_book(
        book(
            clock,
            2,
            kind=BookUpdateKind.DELTA,
            bids=((100, 0), (99.5, 5)),
            asks=((101, 4),),
            previous_sequence=1,
        )
    )
    assert snapshot.bids[0] == (Decimal("99.5"), Decimal("5"))
    first_copy = cache.get_order_book_dict("binanceusdm", "futures", "BTC/USDT:USDT")
    first_copy["bids"][0][0] = 1.0
    second_copy = cache.get_order_book_dict("binance", "perpetual", "BTC/USDT:USDT")
    assert second_copy["bids"][0] == [99.5, 5.0]
    assert second_copy["asks"][0] == [101.0, 4.0]
    assert second_copy["nonce"] == 2

    event = order(clock, 1, status="closed")
    cache.apply_order_event(event)
    found = cache.get_order_event("binance", "perpetual", "BTC/USDT:USDT", "order-1")
    assert found is event
    order_copy = cache.get_order_event_dict("binance", "perpetual", "BTC/USDT:USDT", "order-1")
    order_copy["fills"][0]["amount"] = 999
    assert (
        cache.get_order_event_dict("binance", "perpetual", "BTC/USDT:USDT", "order-1")["fills"][0][
            "amount"
        ]
        == 0.25
    )


def test_private_reconnect_reconciles_before_caching_new_event() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        adapter = FakeAdapter(
            public=[book(clock, 1)],
            private=[ConnectionError("sensitive transport detail"), (order(clock, 7),)],
        )
        requests = []

        async def reconcile(request) -> None:
            requests.append(request)

        manager = RealtimeStreamManager(
            clock=clock,
            settings=settings(),
            reconciliation_callback=reconcile,
        )
        manager.register(
            adapter,
            venue="binance",
            market="perpetual",
            symbols="BTC/USDT:USDT",
        )
        await manager.start()
        await eventually(
            lambda: manager.get_order_event("binance", "perpetual", "BTC/USDT:USDT", "order-1")
            is not None
        )

        assert [request.reason for request in requests] == ["private_reconnect"]
        assert (
            manager.get_order_event_dict("binance", "perpetual", "BTC/USDT:USDT", "order-1")[
                "sequence"
            ]
            == 7
        )
        assert manager.health(
            "binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PRIVATE
        ).healthy
        await manager.stop()
        assert adapter.closed is True

    asyncio.run(scenario())


def test_public_reconnect_exhaustion_keeps_retrying_until_recovery() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        adapter = FakeAdapter(
            public=[
                ConnectionError("failure-1"),
                ConnectionError("failure-2"),
                ConnectionError("failure-3"),
                ConnectionError("failure-4"),
                book(clock, 9),
            ]
        )
        manager = RealtimeStreamManager(clock=clock, settings=settings(attempts=2))
        manager.register(
            adapter,
            venue="binance",
            market="perpetual",
            symbols="BTC/USDT:USDT",
            private=False,
        )

        await manager.start()
        await eventually(
            lambda: manager.is_healthy(
                "binance", "perpetual", "BTC/USDT:USDT"
            )
        )

        # The old implementation returned after failure 3 (max_attempts + 1)
        # and never consumed the recovery snapshot.
        assert adapter.public_calls == 5
        assert manager.health(
            "binance", "perpetual", "BTC/USDT:USDT"
        ).sequence == 9
        assert manager.is_running is True
        await manager.stop()
        assert manager.is_running is False

    asyncio.run(scenario())


def test_private_reconnect_exhaustion_reconciles_then_recovers() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        adapter = FakeAdapter(
            public=[book(clock, 1)],
            private=[
                ConnectionError("failure-1"),
                ConnectionError("failure-2"),
                ConnectionError("failure-3"),
                ConnectionError("failure-4"),
                (order(clock, 12, status="closed"),),
            ],
        )
        requests = []

        async def reconcile(request) -> None:
            requests.append(request)

        manager = RealtimeStreamManager(
            clock=clock,
            settings=settings(attempts=2),
            reconciliation_callback=reconcile,
        )
        manager.register(
            adapter,
            venue="binance",
            market="perpetual",
            symbols="BTC/USDT:USDT",
        )

        await manager.start()
        await eventually(
            lambda: manager.get_order_event(
                "binance", "perpetual", "BTC/USDT:USDT", "order-1"
            )
            is not None
        )

        assert adapter.private_calls == 5
        assert [request.reason for request in requests] == ["private_reconnect"]
        assert manager.health(
            "binance", "perpetual", "BTC/USDT:USDT", StreamChannel.PRIVATE
        ).sequence == 12
        assert manager.is_running is True
        await manager.stop()

    asyncio.run(scenario())


def test_private_sequence_gap_runs_reconciliation_and_resubscribes() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        adapter = FakeAdapter(
            public=[book(clock, 1)],
            private=[
                (order(clock, 1, order_id="before-gap"),),
                (order(clock, 3, order_id="gap-event"),),
                (order(clock, 4, order_id="after-gap"),),
            ],
        )
        requests = []

        async def reconcile(request) -> None:
            requests.append(request)

        manager = RealtimeStreamManager(
            clock=clock,
            settings=settings(),
            reconciliation_callback=reconcile,
        )
        manager.register(
            adapter,
            venue="binance",
            market="perpetual",
            symbols="BTC/USDT:USDT",
        )
        await manager.start()
        await eventually(
            lambda: manager.get_order_event("binance", "perpetual", "BTC/USDT:USDT", "after-gap")
            is not None
        )
        assert len(requests) == 1
        assert requests[0].reason == "private_sequence_gap"
        assert requests[0].previous_sequence == 1
        assert requests[0].received_sequence == 3
        assert manager.get_order_event("binance", "perpetual", "BTC/USDT:USDT", "gap-event") is None
        await manager.stop()

    asyncio.run(scenario())


def test_stop_cancels_in_flight_deadlines_without_orphan_watchers() -> None:
    async def scenario() -> None:
        clock = FakeClock()
        adapter = FakeAdapter()
        manager = RealtimeStreamManager(clock=clock, settings=settings())
        manager.register(
            adapter,
            venue="binance",
            market="perpetual",
            symbols="BTC/USDT:USDT",
        )
        await manager.start()
        await eventually(lambda: adapter.public_calls == 1 and adapter.private_calls == 1)
        await manager.stop()
        assert adapter.public_cancelled == 1
        assert adapter.private_cancelled == 1
        assert adapter.closed is True

    asyncio.run(scenario())


def test_backoff_is_exponential_and_bounded() -> None:
    policy = ReconnectPolicy(
        initial_delay=0.25,
        maximum_delay=1.0,
        multiplier=2.0,
        max_attempts=5,
    )
    assert [policy.delay(attempt) for attempt in range(1, 6)] == [
        0.25,
        0.5,
        1.0,
        1.0,
        1.0,
    ]
    assert policy.delay(2, jitter_sample=0.0) == pytest.approx(0.4)
    assert policy.delay(2, jitter_sample=0.5) == pytest.approx(0.5)
    assert policy.delay(2, jitter_sample=1.0) == pytest.approx(0.6)
    assert policy.delay(100, jitter_sample=1.0) == 1.0
    assert ReconnectPolicy(
        initial_delay=1,
        maximum_delay=8,
        multiplier=2,
        max_attempts=2,
    ).delay(10**9) == 8


class _HubClock:
    def __init__(self) -> None:
        self.value = 0.0

    def __call__(self) -> float:
        return self.value

    def advance(self, seconds: float) -> None:
        self.value += seconds


class _CapacityManager:
    instances = []
    events = []

    def __init__(self, **kwargs) -> None:
        self.running = False
        self.stop_calls = 0
        self.venue = ""
        self.symbol = ""
        self.instances.append(self)

    def register(self, adapter, **kwargs) -> None:
        self.adapter = adapter
        self.venue = kwargs["venue"]
        self.symbol = kwargs["symbols"][0]

    async def start(self) -> None:
        self.running = True
        self.events.append(f"start:{self.venue}:{self.symbol}")

    async def stop(self) -> None:
        self.stop_calls += 1
        self.running = False
        self.events.append(f"stop:{self.venue}:{self.symbol}")
        await asyncio.sleep(0)

    @property
    def is_running(self) -> bool:
        return self.running

    def get_order_book_dict(self, *args):
        return {"venue": self.venue, "symbol": self.symbol}

    def get_order_event_dict(self, *args):
        return {"venue": self.venue, "symbol": self.symbol}

    def health(self, *args):
        return SimpleNamespace(
            healthy=self.running,
            connected=self.running,
            reason="healthy" if self.running else "stopped",
            sequence=1,
        )


def _capacity_hub(clock: _HubClock, **limits) -> LazyRealtimeHub:
    _CapacityManager.instances = []
    _CapacityManager.events = []
    return LazyRealtimeHub(
        adapter_factory=lambda *args, **kwargs: SimpleNamespace(),
        manager_factory=_CapacityManager,
        monotonic=clock,
        **limits,
    )


def _book_ready(hub: LazyRealtimeHub, venue: str, symbol: str) -> bool:
    return hub.get_order_book_dict(venue, "futures", symbol) == {
        "venue": venue,
        "symbol": symbol,
    }


def test_hub_total_cap_evicts_deterministic_lru_for_rotating_symbols() -> None:
    clock = _HubClock()
    hub = _capacity_hub(
        clock,
        max_total_connections=2,
        max_connections_per_venue=2,
        idle_timeout_seconds=1_000,
    )
    try:
        eventually_sync(lambda: _book_ready(hub, "binance", "A/USDT:USDT"))
        first = _CapacityManager.instances[-1]
        clock.advance(1)
        eventually_sync(lambda: _book_ready(hub, "binance", "B/USDT:USDT"))
        second = _CapacityManager.instances[-1]

        # Refresh A so B is the deterministic least-recently-used victim.
        clock.advance(1)
        assert _book_ready(hub, "binance", "A/USDT:USDT")
        clock.advance(1)
        eventually_sync(lambda: _book_ready(hub, "binance", "C/USDT:USDT"))
        third = _CapacityManager.instances[-1]

        assert first.stop_calls == 0
        assert second.stop_calls == 1
        assert third.running is True
        assert _CapacityManager.events.index("stop:binance:B/USDT:USDT") < (
            _CapacityManager.events.index("start:binance:C/USDT:USDT")
        )
        with hub._lock:
            assert len(hub._managers) + len(hub._pending) + len(hub._retiring) <= 2
            assert {key.symbol for key in hub._managers} == {
                "A/USDT:USDT",
                "C/USDT:USDT",
            }
    finally:
        hub.close()


def test_hub_long_rotation_never_accumulates_beyond_cap() -> None:
    clock = _HubClock()
    hub = _capacity_hub(
        clock,
        max_total_connections=3,
        max_connections_per_venue=3,
        idle_timeout_seconds=1_000,
    )
    try:
        for index in range(12):
            symbol = f"C{index:02d}/USDT:USDT"
            eventually_sync(lambda current=symbol: _book_ready(hub, "binance", current))
            clock.advance(1)
            with hub._lock:
                assert (
                    len(hub._managers) + len(hub._pending) + len(hub._retiring)
                    <= 3
                )
            assert sum(manager.running for manager in _CapacityManager.instances) <= 3

        assert len(_CapacityManager.instances) == 12
        assert all(manager.stop_calls == 1 for manager in _CapacityManager.instances[:-3])
        assert all(manager.running for manager in _CapacityManager.instances[-3:])
    finally:
        hub.close()


def test_hub_per_venue_cap_evicts_same_venue_before_other_venue() -> None:
    clock = _HubClock()
    hub = _capacity_hub(
        clock,
        max_total_connections=3,
        max_connections_per_venue=1,
        idle_timeout_seconds=1_000,
    )
    try:
        eventually_sync(lambda: _book_ready(hub, "binance", "A/USDT:USDT"))
        binance_a = _CapacityManager.instances[-1]
        clock.advance(1)
        eventually_sync(lambda: _book_ready(hub, "bybit", "B/USDT:USDT"))
        bybit_b = _CapacityManager.instances[-1]
        clock.advance(1)
        eventually_sync(lambda: _book_ready(hub, "binance", "C/USDT:USDT"))

        assert binance_a.stop_calls == 1
        assert bybit_b.stop_calls == 0
        with hub._lock:
            assert sum(key.venue == "binance" for key in hub._managers) == 1
            assert sum(key.venue == "bybit" for key in hub._managers) == 1
    finally:
        hub.close()


def test_hub_reclaims_idle_manager_before_opening_replacement() -> None:
    clock = _HubClock()
    hub = _capacity_hub(
        clock,
        max_total_connections=3,
        max_connections_per_venue=3,
        idle_timeout_seconds=5,
    )
    try:
        eventually_sync(lambda: _book_ready(hub, "binance", "A/USDT:USDT"))
        idle = _CapacityManager.instances[-1]
        clock.advance(6)
        eventually_sync(lambda: _book_ready(hub, "binance", "B/USDT:USDT"))

        assert idle.stop_calls == 1
        with hub._lock:
            assert {key.symbol for key in hub._managers} == {"B/USDT:USDT"}
    finally:
        hub.close()


def test_hub_pending_creation_consumes_capacity_and_is_not_evicted() -> None:
    class PendingCapacityManager(_CapacityManager):
        instances = []

        async def start(self) -> None:
            self.running = True
            await asyncio.Future()

    clock = _HubClock()
    hub = LazyRealtimeHub(
        adapter_factory=lambda *args, **kwargs: SimpleNamespace(),
        manager_factory=PendingCapacityManager,
        max_total_connections=1,
        max_connections_per_venue=1,
        idle_timeout_seconds=5,
        monotonic=clock,
    )
    try:
        assert hub.get_order_book_dict(
            "binance", "futures", "A/USDT:USDT"
        ) is None
        eventually_sync(
            lambda: bool(PendingCapacityManager.instances)
            and PendingCapacityManager.instances[0].running
        )

        # A second venue still cannot exceed the total cap while A is creating.
        assert hub.get_order_book_dict(
            "bybit", "futures", "B/USDT:USDT"
        ) is None
        assert len(PendingCapacityManager.instances) == 1
        with hub._lock:
            assert len(hub._pending) == 1
            assert len(hub._managers) == 0
            assert hub._connection_count_locked() == 1
            rejected = StreamKey("bybit", "perpetual", "B/USDT:USDT")
            assert hub._errors[rejected] == "CapacityLimit"
    finally:
        hub.close()

    assert PendingCapacityManager.instances[0].stop_calls == 1


def test_hub_evicts_and_recreates_unexpectedly_dead_manager() -> None:
    class StubManager:
        instances = []

        def __init__(self, **kwargs) -> None:
            self.number = len(self.instances) + 1
            self.running = False
            self.stop_calls = 0
            self.instances.append(self)

        def register(self, adapter, **kwargs) -> None:
            self.adapter = adapter

        async def start(self) -> None:
            self.running = True

        async def stop(self) -> None:
            self.stop_calls += 1
            self.running = False

        @property
        def is_running(self) -> bool:
            return self.running

        def get_order_book_dict(self, *args):
            return {"manager": self.number}

        def get_order_event_dict(self, *args):
            return {"manager": self.number}

        def health(self, *args):
            return SimpleNamespace(
                healthy=self.running,
                connected=self.running,
                reason="healthy" if self.running else "stopped",
                sequence=self.number,
            )

    hub = LazyRealtimeHub(
        adapter_factory=lambda *args, **kwargs: SimpleNamespace(),
        manager_factory=StubManager,
    )
    latest = None
    try:
        def first_ready():
            nonlocal latest
            latest = hub.get_order_book_dict(
                "binance", "futures", "BTC/USDT:USDT"
            )
            return latest == {"manager": 1}

        eventually_sync(first_ready)
        first = StubManager.instances[0]

        # Simulate an unexpected worker-task termination. A subsequent lookup
        # must remove this manager, stop it, and install a fresh one for the
        # same StreamKey instead of leaving the key permanently occupied.
        first.running = False

        def replacement_ready():
            nonlocal latest
            latest = hub.get_order_book_dict(
                "binance", "futures", "BTC/USDT:USDT"
            )
            return latest == {"manager": 2}

        eventually_sync(replacement_ready)
        assert len(StubManager.instances) == 2
        assert first.stop_calls == 1
        assert StubManager.instances[1].is_running is True
    finally:
        hub.close()

    assert StubManager.instances[1].stop_calls == 1
    assert hub._thread.is_alive() is False
    assert hub._loop.is_closed() is True


def test_hub_close_cleans_up_manager_whose_creation_is_pending() -> None:
    class PendingManager:
        instances = []

        def __init__(self, **kwargs) -> None:
            self.started = False
            self.stop_calls = 0
            self.instances.append(self)

        def register(self, adapter, **kwargs) -> None:
            self.adapter = adapter

        async def start(self) -> None:
            self.started = True
            await asyncio.Future()

        async def stop(self) -> None:
            self.stop_calls += 1

        @property
        def is_running(self) -> bool:
            return False

    hub = LazyRealtimeHub(
        adapter_factory=lambda *args, **kwargs: SimpleNamespace(),
        manager_factory=PendingManager,
    )
    hub.get_order_book_dict("binance", "futures", "BTC/USDT:USDT")
    eventually_sync(
        lambda: bool(PendingManager.instances)
        and PendingManager.instances[0].started
    )

    hub.close()

    assert PendingManager.instances[0].stop_calls == 1
    assert hub._thread.is_alive() is False
    assert hub._loop.is_closed() is True


@pytest.mark.parametrize(
    ("venue", "exchange_name", "expected_options"),
    [
        (
            "binance",
            "binanceusdm",
            {"defaultType": "future", "defaultSubType": "linear"},
        ),
        (
            "bybit",
            "bybit",
            {"defaultType": "swap", "defaultSubType": "linear"},
        ),
        (
            "bitget",
            "bitget",
            {
                "defaultType": "swap",
                "defaultSubType": "linear",
                "defaultSettle": "USDT",
            },
        ),
    ],
)
def test_ccxt_pro_factory_is_lazy_and_applies_linear_profiles(
    monkeypatch,
    venue,
    exchange_name,
    expected_options,
) -> None:
    constructed = []

    def exchange_type(name):
        class Exchange:
            def __init__(self, config) -> None:
                constructed.append((name, config))

        return Exchange

    module = SimpleNamespace(
        binanceusdm=exchange_type("binanceusdm"),
        bybit=exchange_type("bybit"),
        bitget=exchange_type("bitget"),
    )
    imports = []

    def fake_import(name):
        imports.append(name)
        return module

    monkeypatch.setattr(streams.importlib, "import_module", fake_import)
    adapter = streams.ccxt_pro_adapter_factory(
        venue,
        credentials={"apiKey": "not-logged", "secret": "not-logged-either"},
        options={"custom": True},
    )

    assert imports == ["ccxt.pro"]
    assert constructed[0][0] == exchange_name
    config = constructed[0][1]
    assert adapter.exchange.__class__ is getattr(module, exchange_name)
    assert config["enableRateLimit"] is True
    assert config["newUpdates"] is True
    assert config["options"] == {**expected_options, "custom": True}
    assert config["apiKey"] == "not-logged"
