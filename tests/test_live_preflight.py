"""CONTROLLED_LIVE capability preflight (Codex preflight.py port, 2026-07-12).

An ADDITIONAL live-latch condition in core/live_gate.py — never a replacement
for the signed-checklist latch. Per exchange it verifies, READ-ONLY (no order
is ever placed, modified, or cancelled):
  (a) server-clock skew within tolerance;
  (b) position mode (one-way vs hedge) matches what order code assumes;
  (c) margin mode as expected (capability check when no expectation given);
  (d) a protective conditional order type is placeable — validate-only
      introspection (param builder + venue capability flags);
  (e) min-notional floors for the whitelisted symbols fetched and sane.

Any failure → the live gate stays CLOSED (SystemExit) with the specific
reason. Dormant in OBSERVATION/PAPER. All tests are mock-only — no network.
"""

from __future__ import annotations

import pytest

from core.live_gate import (
    enforce_live_preflight_gate,
    preflight_exchange,
    run_live_preflight,
)

SYMBOLS = ["BTC/USDT:USDT", "ETH/USDT:USDT"]


def _markets(min_cost=5.0, min_amount=0.001):
    limits = {}
    if min_cost is not None:
        limits["cost"] = {"min": min_cost}
    if min_amount is not None:
        limits["amount"] = {"min": min_amount}
    return {s: {"symbol": s, "limits": limits, "active": True} for s in SYMBOLS}


class FakeClient:
    """Bare ccxt-like client. Every check passes with the defaults."""

    def __init__(
        self,
        *,
        skew_ms=0,
        hedged=False,
        margin_mode="isolated",
        has=None,
        markets=None,
        fetch_time_error=None,
    ):
        self._skew = skew_ms
        self._hedged = hedged
        self._margin_mode = margin_mode
        self._fetch_time_error = fetch_time_error
        self.has = (
            has
            if has is not None
            else {
                "fetchPositionMode": True,
                "fetchMarginMode": True,
                "setMarginMode": True,
                "createStopOrder": True,
            }
        )
        self.markets = markets if markets is not None else _markets()
        self.orders_placed = []

    def fetch_time(self):
        if self._fetch_time_error is not None:
            raise self._fetch_time_error
        return 1_000_000 + self._skew

    def milliseconds(self):
        return 1_000_000

    def fetch_position_mode(self, symbol=None):
        return {"hedged": self._hedged}

    def fetch_margin_mode(self, symbol):
        return {"marginMode": self._margin_mode}

    def load_markets(self):
        return self.markets

    def market(self, symbol):
        return self.markets[symbol]

    def create_order(self, *a, **kw):  # pragma: no cover — must never run
        self.orders_placed.append((a, kw))
        raise AssertionError("preflight must NEVER place an order")


class Wrapper:
    """BaseExchange-shaped: raw ccxt client under .exchange."""

    def __init__(self, client):
        self.exchange = client


class Untouchable:
    """Raises on ANY attribute access — proves PAPER dormancy."""

    def __getattr__(self, item):
        raise AssertionError(f"preflight touched exchange attr {item!r} in PAPER")


# --- per-exchange check matrix ------------------------------------------------


def test_all_checks_pass():
    rep = preflight_exchange("bybit", FakeClient(), SYMBOLS, expect_one_way=True)
    assert rep["passed"] is True, rep["failures"]
    assert rep["failures"] == []


def test_wrapper_client_is_unwrapped():
    rep = preflight_exchange("bybit", Wrapper(FakeClient()), SYMBOLS, expect_one_way=True)
    assert rep["passed"] is True, rep["failures"]


def test_clock_skew_beyond_tolerance_fails():
    rep = preflight_exchange("binance", FakeClient(skew_ms=5000), SYMBOLS, expect_one_way=True)
    assert rep["passed"] is False
    assert any("clock skew" in f for f in rep["failures"])


def test_fetch_time_error_fails_closed():
    rep = preflight_exchange(
        "binance", FakeClient(fetch_time_error=RuntimeError("down")), SYMBOLS, expect_one_way=True
    )
    assert rep["passed"] is False
    assert any("clock" in f for f in rep["failures"])


def test_hedge_mode_when_order_code_assumes_one_way_fails():
    rep = preflight_exchange("bybit", FakeClient(hedged=True), SYMBOLS, expect_one_way=True)
    assert rep["passed"] is False
    assert any("position mode" in f for f in rep["failures"])


def test_one_way_mode_when_order_code_assumes_hedge_fails():
    rep = preflight_exchange("binance", FakeClient(hedged=False), SYMBOLS, expect_one_way=False)
    assert rep["passed"] is False
    assert any("position mode" in f for f in rep["failures"])


def test_hedge_expectation_matches_hedged_venue_passes():
    rep = preflight_exchange("binance", FakeClient(hedged=True), SYMBOLS, expect_one_way=False)
    assert rep["passed"] is True, rep["failures"]


def test_margin_mode_mismatch_fails_when_expected():
    rep = preflight_exchange(
        "bybit",
        FakeClient(margin_mode="cross"),
        SYMBOLS,
        expect_one_way=True,
        expected_margin_mode="isolated",
    )
    assert rep["passed"] is False
    assert any("margin mode" in f for f in rep["failures"])


def test_margin_mode_match_passes_when_expected():
    rep = preflight_exchange(
        "bybit",
        FakeClient(margin_mode="isolated"),
        SYMBOLS,
        expect_one_way=True,
        expected_margin_mode="isolated",
    )
    assert rep["passed"] is True, rep["failures"]


def test_no_protective_order_path_fails():
    client = FakeClient(
        has={
            "fetchPositionMode": True,
            "fetchMarginMode": True,
            "setMarginMode": True,
            "createStopOrder": False,
            "createTriggerOrder": False,
            "createStopMarketOrder": False,
        }
    )
    rep = preflight_exchange("bybit", client, SYMBOLS, expect_one_way=True)
    assert rep["passed"] is False
    assert any("protective" in f for f in rep["failures"])


def test_missing_min_notional_floor_fails():
    client = FakeClient(markets=_markets(min_cost=None, min_amount=None))
    rep = preflight_exchange("bybit", client, SYMBOLS, expect_one_way=True)
    assert rep["passed"] is False
    assert any("floor" in f for f in rep["failures"])


def test_insane_min_notional_fails():
    client = FakeClient(markets=_markets(min_cost=1e9))
    rep = preflight_exchange("bybit", client, SYMBOLS, expect_one_way=True)
    assert rep["passed"] is False
    assert any("sanity" in f for f in rep["failures"])


def test_no_whitelisted_symbol_resolvable_fails():
    client = FakeClient(markets={})
    rep = preflight_exchange("bybit", client, SYMBOLS, expect_one_way=True)
    assert rep["passed"] is False
    assert any("resolved" in f for f in rep["failures"])


def test_preflight_never_places_an_order():
    client = FakeClient()
    preflight_exchange("bybit", client, SYMBOLS, expect_one_way=True)
    assert client.orders_placed == []


# --- run_live_preflight aggregation --------------------------------------------


def test_run_live_preflight_aggregates_all_exchanges():
    exchanges = {"bybit": FakeClient(), "binance": FakeClient(skew_ms=9000)}
    ok, failures, reports = run_live_preflight(
        exchanges, SYMBOLS, expect_one_way={"bybit": True, "binance": True}
    )
    assert ok is False
    assert any("binance" in f for f in failures)
    assert len(reports) == 2


def test_run_live_preflight_all_green():
    exchanges = {"bybit": FakeClient(), "binance": FakeClient()}
    ok, failures, _ = run_live_preflight(
        exchanges, SYMBOLS, expect_one_way={"bybit": True, "binance": True}
    )
    assert ok is True
    assert failures == []


def test_run_live_preflight_empty_exchanges_fails():
    ok, failures, _ = run_live_preflight({}, SYMBOLS)
    assert ok is False
    assert failures


# --- enforce: the ADDITIONAL live-latch condition -------------------------------


def test_enforce_dormant_in_paper_and_observation():
    for mode in ("PAPER", "OBSERVATION", "", None):
        enforce_live_preflight_gate(mode, exchanges={"bybit": Untouchable()}, symbols=SYMBOLS)


def test_enforce_blocks_controlled_live_on_failure_with_reason():
    exchanges = {"bybit": FakeClient(skew_ms=9000)}
    notes = []
    with pytest.raises(SystemExit) as exc:
        enforce_live_preflight_gate(
            "CONTROLLED_LIVE",
            exchanges=exchanges,
            symbols=SYMBOLS,
            notify=notes.append,
            expect_one_way={"bybit": True},
        )
    msg = str(exc.value)
    assert "REFUSING" in msg
    assert "clock skew" in msg
    assert notes and "clock skew" in notes[0]


def test_enforce_passes_controlled_live_when_green():
    notes = []
    enforce_live_preflight_gate(
        "CONTROLLED_LIVE",
        exchanges={"bybit": FakeClient()},
        symbols=SYMBOLS,
        notify=notes.append,
        expect_one_way={"bybit": True},
    )
    assert notes == []


def test_enforce_fails_closed_on_internal_error():
    """An erroring preflight must keep the latch CLOSED, never open it."""

    class Exploding:
        def __getattr__(self, item):
            raise RuntimeError("wrapper exploded")

    with pytest.raises(SystemExit):
        enforce_live_preflight_gate(
            "CONTROLLED_LIVE", exchanges={"bybit": Exploding()}, symbols=SYMBOLS
        )


def test_enforce_notify_failure_does_not_mask_the_block():
    def bad_notify(msg):
        raise RuntimeError("notifier down")

    with pytest.raises(SystemExit):
        enforce_live_preflight_gate(
            "CONTROLLED_LIVE",
            exchanges={"bybit": FakeClient(skew_ms=9000)},
            symbols=SYMBOLS,
            notify=bad_notify,
            expect_one_way={"bybit": True},
        )
