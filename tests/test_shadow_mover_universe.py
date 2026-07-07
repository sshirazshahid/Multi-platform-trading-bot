"""Tests for the 2026-07-06 shadow mover-universe selection.

The log-only shadow ensemble used to watch the FIRST-listed futures pairs
(effectively BTC/ETH forever). It now watches the day's biggest |24h %|
movers among the liquidity-screened pairs — so the scalp/mean-reversion/
pattern agents continuously test the "scalp the daily movers" hypothesis on
the coins actually moving, and the resolver scores every proposal after
costs. Fail-open: any error must fall back to the legacy selection.
"""

from __future__ import annotations

from core.bot_engine import BotEngine


def _cfg():
    """Resolve config at CALL time, exactly like the engine's late import.

    The root conftest evicts/re-imports module names for skills tests, so a
    module-level ``import config`` here can hold a STALE module object while
    the engine reads the fresh one — monkeypatches would hit the wrong dict
    (observed as order-dependent failures in the full suite, 2026-07-06).
    """
    import config

    return config


class _StubExchange:
    """Models the BaseExchange WRAPPER contract (not raw ccxt): the engine
    must call fetch_tickers(market_type='futures'), and only that routing
    returns unified swap-keyed tickers ('BTC/USDT:USDT'). If the engine ever
    regresses to spot routing, this stub returns spot keys that never match
    the swap universe — reproducing the 2026-07-06 silent-fallback bug."""

    def __init__(self, tickers, raise_on_fetch=False):
        self._tickers = tickers
        self._raise = raise_on_fetch
        self.calls = 0
        self.last_market_type = None

    def fetch_tickers(self, symbols=None, market_type="spot"):
        self.calls += 1
        self.last_market_type = market_type
        if self._raise:
            raise RuntimeError("ticker endpoint down")
        if market_type != "futures":  # spot routing -> unmatchable keys
            return {s.replace(":USDT", ""): t for s, t in self._tickers.items()}
        return dict(self._tickers)


def _engine(pairs, tickers, raise_on_fetch=False):
    eng = BotEngine.__new__(BotEngine)
    eng.current_pairs = pairs
    eng.active_exchanges = {"binance": _StubExchange(tickers, raise_on_fetch)}
    return eng


def _t(pct, qv):
    return {"percentage": pct, "quoteVolume": qv}


PAIRS = {"binance": {"futures": [
    "BTC/USDT:USDT", "ETH/USDT:USDT", "DOGE/USDT:USDT",
    "SUI/USDT:USDT", "PEPE/USDT:USDT", "LTC/USDT:USDT",
]}}


def test_movers_ranked_by_abs_change_with_volume_floor(monkeypatch):
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_universe", True)
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_cap", 3)
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_min_qv_usd", 5_000_000.0)
    tickers = {
        "BTC/USDT:USDT": _t(1.0, 9e9),
        "ETH/USDT:USDT": _t(2.0, 5e9),
        "DOGE/USDT:USDT": _t(-8.0, 4e8),   # biggest |move| — negative counts
        "SUI/USDT:USDT": _t(6.5, 2e8),
        "PEPE/USDT:USDT": _t(25.0, 1e6),   # huge move, but below volume floor
        "LTC/USDT:USDT": _t(0.2, 3e8),
    }
    eng = _engine(PAIRS, tickers)
    out = eng._shadow_symbols()
    assert out == ["DOGE/USDT:USDT", "SUI/USDT:USDT", "ETH/USDT:USDT"]
    assert "PEPE/USDT:USDT" not in out  # illiquid junk excluded
    # MUST route futures — spot routing returns unmatchable keys (the
    # 2026-07-06 silent-fallback bug). Pin the routing so it can't regress.
    assert eng.active_exchanges["binance"].last_market_type == "futures"


def test_mover_cache_prevents_refetch_within_window(monkeypatch):
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_universe", True)
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_refresh_s", 900)
    eng = _engine(PAIRS, {"BTC/USDT:USDT": _t(3.0, 9e9)})
    first = eng._shadow_symbols()
    second = eng._shadow_symbols()
    assert first == second == ["BTC/USDT:USDT"]
    assert eng.active_exchanges["binance"].calls == 1


def test_disabled_flag_uses_legacy_selection(monkeypatch):
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_universe", False)
    eng = _engine(PAIRS, {"DOGE/USDT:USDT": _t(-9.0, 9e9)})
    out = eng._shadow_symbols()
    assert out == ["BTC/USDT:USDT", "ETH/USDT:USDT"]  # legacy first-2
    assert eng.active_exchanges["binance"].calls == 0


def test_ticker_failure_falls_back_to_legacy(monkeypatch):
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_universe", True)
    eng = _engine(PAIRS, {}, raise_on_fetch=True)
    assert eng._shadow_symbols() == ["BTC/USDT:USDT", "ETH/USDT:USDT"]


def test_empty_tickers_fall_back_loudly_and_cache(monkeypatch):
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_universe", True)
    monkeypatch.setitem(_cfg().SHADOW_MODE,"mover_refresh_s", 900)
    eng = _engine(PAIRS, {})
    assert eng._shadow_symbols() == ["BTC/USDT:USDT", "ETH/USDT:USDT"]
    # Fallback must be CACHED: the ticker call may not re-fire every shadow
    # tick when a venue misbehaves.
    eng._shadow_symbols()
    assert eng.active_exchanges["binance"].calls == 1


def test_config_mover_defaults_pinned():
    # The engine reads SHADOW_MODE.get(key, hardcoded_default): a typo'd or
    # deleted key in config.py would pass monkeypatched tests while the
    # SHADOW_MOVER_* env knobs silently die. Pin the shipped defaults.
    sm = _cfg().SHADOW_MODE
    assert sm.get("mover_universe") is True
    assert sm.get("mover_cap") == 10
    assert sm.get("mover_refresh_s") == 900
    assert sm.get("mover_min_qv_usd") == 5_000_000.0


def test_legacy_selection_semantics_preserved():
    eng = BotEngine.__new__(BotEngine)
    eng.current_pairs = {
        "binance": {"futures": ["A/USDT:USDT", "B/USDT:USDT", "C/USDT:USDT"]},
        "bybit": {"futures": ["D/USDT:USDT", "E/USDT:USDT"]},
    }
    assert eng._shadow_symbols_legacy(5) == [
        "A/USDT:USDT", "B/USDT:USDT", "D/USDT:USDT", "E/USDT:USDT",
    ]


def _concrete_base(ccxt_obj, futures_params):
    """A concrete BaseExchange instance for wrapper-level tests (the class is
    abstract; clearing __abstractmethods__ is the standard test idiom)."""
    from exchanges.base import BaseExchange

    concrete = type("_ConcreteExchange", (BaseExchange,), {})
    concrete.__abstractmethods__ = frozenset()
    ex = concrete.__new__(concrete)
    ex.exchange = ccxt_obj
    ex._connected = True
    ex._futures_params = futures_params
    return ex


def test_base_wrapper_fetch_tickers_routes_futures():
    """The fix itself: BaseExchange.fetch_tickers(market_type='futures') must
    pass _futures_params() so ccxt hits the perp endpoint. This is what the
    raw ex.exchange.fetch_tickers() bypassed."""

    class _FakeCcxt:
        def __init__(self):
            self.last_params = None

        def fetch_tickers(self, symbols=None, params=None):
            self.last_params = params
            return {"BTC/USDT:USDT": {"percentage": 1.0, "quoteVolume": 1e9}}

    # _futures_params() is {} for binance; the wrapper must still route perps
    # via params['type']='swap' (the bug was relying on the empty hook alone).
    ex = _concrete_base(_FakeCcxt(), lambda: {})
    out = ex.fetch_tickers(market_type="futures")
    assert out == {"BTC/USDT:USDT": {"percentage": 1.0, "quoteVolume": 1e9}}
    assert ex.exchange.last_params == {"type": "swap"}  # perp routing forced
    ex.fetch_tickers(market_type="spot")
    assert ex.exchange.last_params == {}  # spot routing passes no futures params


def test_base_wrapper_fetch_tickers_fails_open():
    class _BrokenCcxt:
        def fetch_tickers(self, symbols=None, params=None):
            raise RuntimeError("endpoint 500")

    ex = _concrete_base(_BrokenCcxt(), lambda: {"type": "future"})
    assert ex.fetch_tickers(market_type="futures") == {}  # never raises
