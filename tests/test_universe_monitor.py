"""No-network tests for the broad USDT perpetual shadow monitor."""

from __future__ import annotations

import math
import sqlite3
import time

import pytest

from core.universe_monitor import (
    UniverseMonitor,
    UniverseMonitorConfig,
    classify_mover_base,
    normalize_ticker,
)

HOUR_MS = 60 * 60 * 1000
DAY_MS = 24 * HOUR_MS


class _FakeExchange:
    def __init__(self, tickers=None, markets=None, error=None):
        self._tickers = dict(tickers or {})
        self.markets = dict(markets or {})
        self.exchange = self
        self.error = error
        self.calls = 0
        self.market_types = []

    def set_tickers(self, tickers):
        self._tickers = dict(tickers)

    def fetch_tickers(self, *, market_type="spot"):
        self.calls += 1
        self.market_types.append(market_type)
        if self.error:
            raise self.error
        return dict(self._tickers)


def _ticker(price, qv=10_000_000.0, *, ts=None, pct=None, symbol=None, **extra):
    row = {"last": price, "quoteVolume": qv}
    if ts is not None:
        row["timestamp"] = ts
    if pct is not None:
        row["percentage"] = pct
    if symbol is not None:
        row["symbol"] = symbol
    row.update(extra)
    return row


def _market(symbol, *, base=None, **overrides):
    row = {
        "id": symbol.replace("/", "").replace(":USDT", ""),
        "symbol": symbol,
        "base": base or symbol.split("/", 1)[0],
        "quote": "USDT",
        "settle": "USDT",
        "swap": True,
        "linear": True,
        "active": True,
    }
    row.update(overrides)
    return row


def _cfg(**overrides):
    values = {
        "min_quote_volume_usdt": 1_000_000.0,
        "max_ticker_age_s": 60.0,
        "reference_tolerance_s": 10 * 60.0,
        "per_direction_per_horizon": 3,
        "max_shortlist": 18,
    }
    values.update(overrides)
    return UniverseMonitorConfig(**values)


def _by_base(result):
    return {row.base: row for row in result.shortlist}


def test_scan_fetches_each_venue_once_and_filters_bad_contracts(tmp_path):
    now = 1_800_000_000_000
    valid = "GOOD/USDT:USDT"
    derived = "DERIVED/USDT:USDT"
    spot = "SPOT/USDT"
    inverse = "INV/USD:BTC"
    markets = {
        valid: _market(valid),
        spot: _market(spot, quote="USDT", settle="USDT", swap=False),
        inverse: _market(inverse, quote="USD", settle="BTC", linear=False),
    }
    venue = _FakeExchange(
        {
            valid: _ticker(10.0, ts=now, pct=4.0),
            # Missing exchange timestamp is valid because the just-received
            # batch time supplies a conservative freshness bound.
            derived: _ticker(None, qv=None, close=2.0, baseVolume=1_000_000),
            "STALE/USDT:USDT": _ticker(5.0, ts=now - 61_000),
            "NAN/USDT:USDT": _ticker(math.nan, ts=now),
            "LOW/USDT:USDT": _ticker(2.0, qv=999_999.0, ts=now),
            "NOQV/USDT:USDT": _ticker(2.0, qv=None, ts=now),
            spot: _ticker(2.0, ts=now),
            inverse: _ticker(2.0, ts=now),
            "AMBIGUOUSUSDT": _ticker(2.0, ts=now),
        },
        markets=markets,
    )
    broken = _FakeExchange(error=RuntimeError("venue unavailable"))
    monitor = UniverseMonitor(
        {"binance": venue, "bybit": broken},
        db_path=tmp_path / "universe.sqlite",
        config=_cfg(),
    )

    result = monitor.scan(now_ms=now)

    assert venue.calls == broken.calls == 1
    assert venue.market_types == broken.market_types == ["futures"]
    assert result.venue_fetches == 2
    assert result.accepted_tickers == 2
    assert monitor.snapshot_count() == 2
    assert set(_by_base(result)) == {"GOOD"}  # DERIVED has no 24h seed yet
    assert result.rejection_counts == {
        "low_quote_volume": 1,
        "missing_or_invalid_price": 1,
        "missing_or_invalid_quote_volume": 1,
        "not_linear": 1,
        "not_perpetual": 1,
        "stale_ticker": 1,
        "unverified_contract_type": 1,
    }
    assert "bybit" in result.venue_errors


def test_normalization_marks_batch_received_time_and_rejects_future_timestamp():
    now = 1_800_000_000_000
    cfg = _cfg()
    row, reason = normalize_ticker(
        "Bybit",
        "BTC/USDT:USDT",
        _ticker(50_000.0, pct=1.0),
        now_ms=now,
        config=cfg,
    )
    assert reason is None
    assert row is not None
    assert row.timestamp_basis == "batch_received"
    assert row.source_at_ms == now
    assert row.venue == "bybit"

    row, reason = normalize_ticker(
        "bybit",
        "BTC/USDT:USDT",
        _ticker(50_000.0, pct=1.0, ts=now + 31_000),
        now_ms=now,
        config=cfg,
    )
    assert row is None
    assert reason == "future_ticker_timestamp"


def test_persistent_restart_reconstructs_hour_day_and_week_returns(tmp_path):
    t0 = 1_800_000_000_000
    db = tmp_path / "universe.sqlite"
    ex = _FakeExchange(
        {
            "UP/USDT:USDT": _ticker(100.0, ts=t0, pct=12.0),
            "DOWN/USDT:USDT": _ticker(100.0, ts=t0, pct=-8.0),
        }
    )
    cfg = _cfg()
    first = UniverseMonitor({"binance": ex}, db_path=db, config=cfg).scan(now_ms=t0)
    first_rows = _by_base(first)
    assert first_rows["UP"].selected_return_source == "exchange_ticker_24h_seed"
    assert first_rows["DOWN"].selected_return_source == "exchange_ticker_24h_seed"

    # A brand-new monitor instance proves state survives process restart.
    t1 = t0 + HOUR_MS
    ex.set_tickers(
        {
            "UP/USDT:USDT": _ticker(110.0, ts=t1, pct=99.0),
            "DOWN/USDT:USDT": _ticker(90.0, ts=t1, pct=-99.0),
        }
    )
    hourly_monitor = UniverseMonitor({"binance": ex}, db_path=db, config=cfg)
    hourly = hourly_monitor.scan(now_ms=t1)
    hourly_rows = _by_base(hourly)
    assert hourly_rows["UP"].returns_pct["1h"] == pytest.approx(10.0)
    assert hourly_rows["DOWN"].returns_pct["1h"] == pytest.approx(-10.0)
    assert hourly_rows["UP"].return_sources["1h"] == "point_in_time_snapshot"

    t24 = t0 + DAY_MS
    ex.set_tickers(
        {
            "UP/USDT:USDT": _ticker(120.0, ts=t24, pct=999.0),
            "DOWN/USDT:USDT": _ticker(80.0, ts=t24, pct=-99.0),
        }
    )
    daily = UniverseMonitor({"binance": ex}, db_path=db, config=cfg).scan(now_ms=t24)
    daily_rows = _by_base(daily)
    assert daily_rows["UP"].returns_pct["24h"] == pytest.approx(20.0)
    assert daily_rows["DOWN"].returns_pct["24h"] == pytest.approx(-20.0)
    # A point-in-time reference wins over the exchange's current 24h field.
    assert daily_rows["UP"].return_sources["24h"] == "point_in_time_snapshot"

    t7 = t0 + 7 * DAY_MS
    ex.set_tickers(
        {
            "UP/USDT:USDT": _ticker(140.0, ts=t7),
            "DOWN/USDT:USDT": _ticker(70.0, ts=t7),
        }
    )
    weekly_monitor = UniverseMonitor({"binance": ex}, db_path=db, config=cfg)
    weekly = weekly_monitor.scan(now_ms=t7)
    weekly_rows = _by_base(weekly)
    assert weekly_rows["UP"].returns_pct["7d"] == pytest.approx(40.0)
    assert weekly_rows["DOWN"].returns_pct["7d"] == pytest.approx(-30.0)
    assert weekly_monitor.snapshot_count() == 8


def test_ranking_uses_percent_return_and_balances_gainers_and_losers(tmp_path):
    t0 = 1_800_000_000_000
    symbols = {
        "HIGH/USDT:USDT": 1_000.0,
        "CHEAP/USDT:USDT": 1.0,
        "LOSS/USDT:USDT": 100.0,
        "SMALLLOSS/USDT:USDT": 100.0,
    }
    ex = _FakeExchange({s: _ticker(p, ts=t0) for s, p in symbols.items()})
    cfg = _cfg(per_direction_per_horizon=2, max_shortlist=4)
    monitor = UniverseMonitor(
        {"binance": ex}, db_path=tmp_path / "universe.sqlite", config=cfg
    )
    assert monitor.scan(now_ms=t0).shortlist == ()

    t1 = t0 + HOUR_MS
    ex.set_tickers(
        {
            "HIGH/USDT:USDT": _ticker(1_010.0, ts=t1),  # +$10, only +1%
            "CHEAP/USDT:USDT": _ticker(1.10, ts=t1),    # +$0.10, but +10%
            "LOSS/USDT:USDT": _ticker(80.0, ts=t1),
            "SMALLLOSS/USDT:USDT": _ticker(99.0, ts=t1),
        }
    )
    result = monitor.scan(now_ms=t1)
    assert [(row.base, row.direction) for row in result.shortlist] == [
        ("CHEAP", "gainer"),
        ("LOSS", "loser"),
        ("HIGH", "gainer"),
        ("SMALLLOSS", "loser"),
    ]
    assert result.shortlist[0].selected_return_pct == pytest.approx(10.0)
    assert result.shortlist[1].selected_return_pct == pytest.approx(-20.0)


def test_abs_usdt_band_filters_and_ranks_by_dollar_move(tmp_path):
    """Owner $5–$200 absolute USDT band: drop dust + mega swings; rank by $."""
    t0 = 1_800_000_000_000
    symbols = {
        "MID/USDT:USDT": 50.0,      # mid-priced
        "DUST/USDT:USDT": 0.50,     # tiny abs $
        "MEGA/USDT:USDT": 100_000.0,  # BTC-scale
        "MIDLOSS/USDT:USDT": 40.0,
    }
    ex = _FakeExchange({s: _ticker(p, ts=t0) for s, p in symbols.items()})
    cfg = _cfg(
        per_direction_per_horizon=2,
        max_shortlist=4,
        abs_move_usdt_min=5.0,
        abs_move_usdt_max=200.0,
        prefer_abs_usdt_rank=True,
    )
    monitor = UniverseMonitor(
        {"binance": ex}, db_path=tmp_path / "universe.sqlite", config=cfg
    )
    monitor.scan(now_ms=t0)
    t1 = t0 + HOUR_MS
    ex.set_tickers(
        {
            "MID/USDT:USDT": _ticker(60.0, ts=t1),       # +$10 (in band)
            "DUST/USDT:USDT": _ticker(0.55, ts=t1),      # +$0.05 (below min)
            "MEGA/USDT:USDT": _ticker(100_500.0, ts=t1),  # +$500 (above max)
            "MIDLOSS/USDT:USDT": _ticker(32.0, ts=t1),   # -$8 (in band)
        }
    )
    result = monitor.scan(now_ms=t1)
    bases = [row.base for row in result.shortlist]
    assert "DUST" not in bases
    assert "MEGA" not in bases
    assert "MID" in bases
    assert "MIDLOSS" in bases
    mid = next(r for r in result.shortlist if r.base == "MID")
    # abs_usdt uses current price * |pct|/100 (50→60 = +20% → 0.20*60=$12)
    assert abs(mid.selected_return_pct) / 100.0 * mid.price == pytest.approx(12.0)


def test_prefer_crypto_shortlist_tags_and_deprioritizes_tradfi(tmp_path):
    """META (disabled stock base) loses shortlist capacity to a crypto mover."""
    t0 = 1_800_000_000_000
    cfg = _cfg(
        per_direction_per_horizon=1,
        max_shortlist=1,
        abs_move_usdt_min=5.0,
        abs_move_usdt_max=200.0,
        prefer_abs_usdt_rank=True,
        prefer_crypto_shortlist=True,
    )
    # Same horizon/direction; META abs $ larger but non_crypto.
    ex = _FakeExchange(
        {
            "META/USDT:USDT": _ticker(100.0, ts=t0),
            "AAA/USDT:USDT": _ticker(50.0, ts=t0),
        }
    )
    monitor = UniverseMonitor(
        {"binance": ex}, db_path=tmp_path / "universe.sqlite", config=cfg
    )
    monitor.scan(now_ms=t0)
    t1 = t0 + HOUR_MS
    ex.set_tickers(
        {
            "META/USDT:USDT": _ticker(150.0, ts=t1),  # +$50
            "AAA/USDT:USDT": _ticker(60.0, ts=t1),    # +$10
        }
    )
    result = monitor.scan(now_ms=t1)
    assert len(result.shortlist) == 1
    assert result.shortlist[0].base == "AAA"
    assert result.shortlist[0].asset_class == "crypto"
    assert classify_mover_base("META") == "non_crypto"


def test_venue_metadata_tradfi_is_tagged_without_static_list(tmp_path):
    """Tokenized equities unknown to the static base lists must be tagged from
    the venue's own market metadata (info.underlyingType), including the
    HK_EQUITY variant. Measured live 2026-08-03: SKHYNIX/SNDK/MU shipped in the
    mover shortlist as asset_class=crypto because _rank classified on base name
    alone."""
    t0 = 1_800_000_000_000
    cfg = _cfg(
        per_direction_per_horizon=2,
        max_shortlist=2,
        abs_move_usdt_min=5.0,
        abs_move_usdt_max=200.0,
        prefer_abs_usdt_rank=True,
        prefer_crypto_shortlist=True,
    )
    sym = "SKHYNIX/USDT:USDT"
    markets = {
        sym: _market(
            sym,
            info={"underlyingType": "HK_EQUITY", "contractType": "PERPETUAL"},
        ),
        "AAA/USDT:USDT": _market("AAA/USDT:USDT"),
    }
    ex = _FakeExchange(
        {sym: _ticker(1000.0, ts=t0), "AAA/USDT:USDT": _ticker(50.0, ts=t0)},
        markets=markets,
    )
    monitor = UniverseMonitor(
        {"binance": ex}, db_path=tmp_path / "universe.sqlite", config=cfg
    )
    monitor.scan(now_ms=t0)
    t1 = t0 + HOUR_MS
    ex.set_tickers(
        {sym: _ticker(1080.0, ts=t1), "AAA/USDT:USDT": _ticker(60.0, ts=t1)}
    )
    result = monitor.scan(now_ms=t1)
    by_base = _by_base(result)
    # The crypto mover holds the crypto-preferred slot even though the equity
    # perp moved more dollars (+$80 vs +$10)...
    assert by_base["AAA"].asset_class == "crypto"
    # ...and the tokenized equity, admitted only as fallback fill, carries the
    # metadata-driven tag despite being absent from every static list.
    assert by_base["SKHYNIX"].asset_class == "non_crypto"
    # Direct unit pin: metadata alone flips the tag.
    assert classify_mover_base("SKHYNIX") == "crypto"  # no metadata -> stays crypto
    assert (
        classify_mover_base("SKHYNIX", {"info": {"underlyingType": "EQUITY"}})
        == "non_crypto"
    )


def test_shortlist_is_bounded_and_deduplicated_across_venues(tmp_path):
    t0 = 1_800_000_000_000
    bases = ["DUP", "A", "B", "C", "D"]
    binance = _FakeExchange(
        {f"{base}/USDT:USDT": _ticker(100.0, qv=2_000_000, ts=t0) for base in bases}
    )
    bybit = _FakeExchange(
        {"DUP/USDT:USDT": _ticker(100.0, qv=50_000_000, ts=t0)}
    )
    cfg = _cfg(per_direction_per_horizon=10, max_shortlist=3)
    db = tmp_path / "universe.sqlite"
    monitor = UniverseMonitor(
        {"binance": binance, "bybit": bybit}, db_path=db, config=cfg
    )
    monitor.scan(now_ms=t0)

    t1 = t0 + HOUR_MS
    binance.set_tickers(
        {
            "DUP/USDT:USDT": _ticker(200.0, qv=2_000_000, ts=t1),
            "A/USDT:USDT": _ticker(110.0, qv=2_000_000, ts=t1),
            "B/USDT:USDT": _ticker(108.0, qv=2_000_000, ts=t1),
            "C/USDT:USDT": _ticker(90.0, qv=2_000_000, ts=t1),
            "D/USDT:USDT": _ticker(92.0, qv=2_000_000, ts=t1),
        }
    )
    bybit.set_tickers(
        {"DUP/USDT:USDT": _ticker(115.0, qv=50_000_000, ts=t1)}
    )
    result = monitor.scan(now_ms=t1)

    assert len(result.shortlist) == 3
    assert len({row.base for row in result.shortlist}) == 3
    duplicate = next(row for row in result.shortlist if row.base == "DUP")
    # Most liquid listing represents a cross-venue duplicate, avoiding a
    # spectacular move from the thinner venue dominating the research queue.
    assert duplicate.venue == "bybit"
    assert duplicate.selected_return_pct == pytest.approx(15.0)
    assert binance.calls == bybit.calls == 2  # one batch per venue per scan


def test_thousand_contract_screen_stays_one_network_batch_and_bounded(tmp_path):
    now = 1_800_000_000_000
    tickers = {
        f"C{i:04d}/USDT:USDT": _ticker(
            1.0 + i / 10_000,
            qv=2_000_000.0 + i,
            ts=now,
            pct=(i % 201) - 100,
        )
        for i in range(1_500)
    }
    ex = _FakeExchange(tickers)
    monitor = UniverseMonitor(
        {"binance": ex},
        db_path=tmp_path / "universe.sqlite",
        config=_cfg(
            per_direction_per_horizon=10,
            max_shortlist=12,
            max_contracts_per_venue=5_000,
        ),
    )

    result = monitor.scan(now_ms=now)

    assert ex.calls == 1
    assert result.raw_tickers == result.accepted_tickers == 1_500
    assert monitor.snapshot_count() == 1_500
    assert len(result.shortlist) == 12
    assert {row.direction for row in result.shortlist} == {"gainer", "loser"}
    assert len({row.base for row in result.shortlist}) == 12


def test_contract_master_survives_restart_and_preserves_explicit_delisting(tmp_path):
    t0 = 1_800_000_000_000
    t1 = t0 + HOUR_MS
    t2 = t1 + HOUR_MS
    listed_at = t0 - 30 * DAY_MS
    symbol = "LIFE/USDT:USDT"
    db = tmp_path / "universe.sqlite"
    initial_market = _market(
        symbol,
        contractSize=0.01,
        contractMultiplier=10.0,
        precision={"price": 0.1, "amount": 0.001},
        limits={"cost": {"min": 5.0}},
        info={"onboardDate": listed_at},
    )
    exchange = _FakeExchange(
        {symbol: _ticker(10.0, ts=t0, pct=1.0)},
        markets={symbol: initial_market},
    )
    exchange.precisionMode = 4  # CCXT TICK_SIZE; no conversion ambiguity.

    first = UniverseMonitor({"binance": exchange}, db_path=db, config=_cfg()).scan(
        now_ms=t0
    )

    assert exchange.calls == 1
    assert first.contract_counts["observed_contracts"] == 1
    assert first.contract_counts["persisted_total"] == 1
    assert first.contract_counts["persisted_active"] == 1
    assert first.contract_metadata_coverage == {
        "active": 1.0,
        "amount_step": 1.0,
        "contract_multiplier": 1.0,
        "contract_size": 1.0,
        "delisted_at_ms": 0.0,
        "listed_at_ms": 1.0,
        "market_id": 1.0,
        "min_notional": 1.0,
        "price_tick": 1.0,
    }
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            """
            SELECT market_id, base, quote, settle, linear, swap, active,
                   contract_size, contract_multiplier, price_tick, amount_step,
                   min_notional, listed_at_ms, delisted_at_ms,
                   first_seen_at_ms, last_seen_at_ms
            FROM universe_contract_master
            WHERE venue = 'binance' AND symbol = ?
            """,
            (symbol,),
        ).fetchone()
    assert row == (
        "LIFEUSDT",
        "LIFE",
        "USDT",
        "USDT",
        1,
        1,
        1,
        0.01,
        10.0,
        0.1,
        0.001,
        5.0,
        listed_at,
        None,
        t0,
        t0,
    )

    # A fresh process view observes an explicit venue delisting.  Sparse later
    # metadata must not erase contract specifications learned previously.
    delisted_market = _market(
        symbol,
        active=False,
        info={"delistTime": t1},
    )
    exchange.markets = {symbol: delisted_market}
    exchange.set_tickers({symbol: _ticker(10.0, ts=t1, pct=1.0)})
    second = UniverseMonitor({"binance": exchange}, db_path=db, config=_cfg()).scan(
        now_ms=t1
    )
    assert second.accepted_tickers == 0  # no change to executable/ranked universe
    assert second.contract_counts["persisted_inactive"] == 1
    assert second.contract_counts["persisted_inactive_or_delisted"] == 1
    with sqlite3.connect(db) as conn:
        state = conn.execute(
            """
            SELECT active, contract_size, listed_at_ms, delisted_at_ms,
                   first_seen_at_ms, last_seen_at_ms
            FROM universe_contract_master WHERE venue = 'binance' AND symbol = ?
            """,
            (symbol,),
        ).fetchone()
    assert state == (0, 0.01, listed_at, t1, t0, t1)

    # Disappearance is not treated as a second delisting signal.  The inactive
    # history remains queryable and only last_seen stays at the last observation.
    exchange.markets = {}
    exchange.set_tickers({})
    third = UniverseMonitor({"binance": exchange}, db_path=db, config=_cfg()).scan(
        now_ms=t2
    )
    assert third.contract_counts["persisted_total"] == 1
    assert third.contract_counts["retained_not_seen_this_scan"] == 1
    with sqlite3.connect(db) as conn:
        retained = conn.execute(
            """
            SELECT active, delisted_at_ms, last_seen_at_ms
            FROM universe_contract_master WHERE venue = 'binance' AND symbol = ?
            """,
            (symbol,),
        ).fetchone()
    assert retained == (0, t1, t1)
    assert exchange.calls == 3  # exactly one ticker batch per scan


def test_contract_master_does_not_invent_listing_age(tmp_path):
    now = 1_800_000_000_000
    symbol = "UNKNOWNAGE/USDT:USDT"
    db = tmp_path / "universe.sqlite"
    exchange = _FakeExchange(
        {symbol: _ticker(1.0, ts=now)},
        markets={symbol: _market(symbol)},
    )

    result = UniverseMonitor({"bybit": exchange}, db_path=db, config=_cfg()).scan(
        now_ms=now
    )

    assert result.contract_metadata_coverage["listed_at_ms"] == 0.0
    with sqlite3.connect(db) as conn:
        listed_at_ms, first_seen_at_ms = conn.execute(
            """
            SELECT listed_at_ms, first_seen_at_ms
            FROM universe_contract_master WHERE venue = 'bybit' AND symbol = ?
            """,
            (symbol,),
        ).fetchone()
    assert listed_at_ms is None
    assert first_seen_at_ms == now


def test_contract_master_handles_thousand_prefix_metadata_in_one_ticker_batch(tmp_path):
    now = 1_800_000_000_000
    markets = {
        f"PFX{i:04d}/USDT:USDT": _market(f"PFX{i:04d}/USDT:USDT")
        for i in range(1_005)
    }
    ticker_symbols = list(markets)[:2]
    exchange = _FakeExchange(
        {symbol: _ticker(1.0, ts=now) for symbol in ticker_symbols},
        markets=markets,
    )
    db = tmp_path / "universe.sqlite"

    result = UniverseMonitor({"bitget": exchange}, db_path=db, config=_cfg()).scan(
        now_ms=now
    )

    assert exchange.calls == 1
    assert exchange.market_types == ["futures"]
    assert result.raw_tickers == result.accepted_tickers == 2
    assert result.contract_counts["metadata_entries_seen"] == 1_005
    assert result.contract_counts["observed_contracts"] == 1_005
    assert result.contract_counts["persisted_total"] == 1_005
    assert result.contract_metadata_coverage["market_id"] == 1.0
    with sqlite3.connect(db) as conn:
        prefixed = conn.execute(
            "SELECT COUNT(*) FROM universe_contract_master WHERE symbol LIKE 'PFX%'"
        ).fetchone()[0]
    assert prefixed == 1_005


def test_contract_master_schema_additively_migrates_early_shadow_table(tmp_path):
    now = 1_800_000_000_000
    db = tmp_path / "universe.sqlite"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE universe_contract_master (
                venue TEXT NOT NULL,
                symbol TEXT NOT NULL,
                PRIMARY KEY (venue, symbol)
            )
            """
        )
        conn.execute(
            "INSERT INTO universe_contract_master VALUES (?, ?)",
            ("legacy", "OLD/USDT:USDT"),
        )

    symbol = "NEW/USDT:USDT"
    exchange = _FakeExchange(
        {symbol: _ticker(1.0, ts=now)},
        markets={symbol: _market(symbol, contractSize=1.0)},
    )
    monitor = UniverseMonitor({"binance": exchange}, db_path=db, config=_cfg())
    result = monitor.scan(now_ms=now)

    assert result.contract_counts["persisted_total"] == 2
    with sqlite3.connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(universe_contract_master)")
        }
        legacy = conn.execute(
            "SELECT symbol FROM universe_contract_master WHERE venue = 'legacy'"
        ).fetchone()
    assert {
        "market_id",
        "contract_size",
        "min_notional",
        "listed_at_ms",
        "delisted_at_ms",
        "first_seen_at_ms",
        "last_seen_at_ms",
    }.issubset(columns)
    assert legacy == ("OLD/USDT:USDT",)


def test_reference_outside_tolerance_is_not_used(tmp_path):
    t0 = 1_800_000_000_000
    ex = _FakeExchange({"GAP/USDT:USDT": _ticker(100.0, ts=t0)})
    monitor = UniverseMonitor(
        {"binance": ex},
        db_path=tmp_path / "universe.sqlite",
        config=_cfg(reference_tolerance_s=5 * 60.0),
    )
    monitor.scan(now_ms=t0)
    # At +2h the only old point is an hour before the desired 1h reference,
    # outside tolerance.  It must not be mislabeled as a 1h return.
    t2 = t0 + 2 * HOUR_MS
    ex.set_tickers({"GAP/USDT:USDT": _ticker(120.0, ts=t2)})
    result = monitor.scan(now_ms=t2)
    assert result.shortlist == ()


def test_extreme_or_nonfinite_ticker_percentage_is_not_a_cold_start_seed(tmp_path):
    now = 1_800_000_000_000
    ex = _FakeExchange(
        {
            "HUGE/USDT:USDT": _ticker(1.0, ts=now, pct=10_000.0),
            "NANPCT/USDT:USDT": _ticker(1.0, ts=now, pct=math.nan),
            "OPEN/USDT:USDT": _ticker(110.0, ts=now, open=100.0),
        }
    )
    result = UniverseMonitor(
        {"binance": ex},
        db_path=tmp_path / "universe.sqlite",
        config=_cfg(max_abs_return_pct=1_000.0),
    ).scan(now_ms=now)
    assert [row.base for row in result.shortlist] == ["OPEN"]
    assert result.shortlist[0].selected_return_pct == pytest.approx(10.0)


def test_botengine_wires_broad_monitor_only_to_shadow_symbols(
    tmp_path, monkeypatch
):
    """The integration scans beyond current_pairs without mutating that latch."""
    import config
    from core.bot_engine import BotEngine

    now = int(time.time() * 1000)
    binance = _FakeExchange(
        {
            "BTC/USDT:USDT": _ticker(50_000.0, ts=now, pct=1.0),
            "OUTSIDE/USDT:USDT": _ticker(2.0, ts=now, pct=25.0),
            "FALLER/USDT:USDT": _ticker(3.0, ts=now, pct=-20.0),
        }
    )
    bybit = _FakeExchange(
        {"BYBITONLY/USDT:USDT": _ticker(4.0, ts=now, pct=12.0)}
    )
    monkeypatch.setitem(config.SHADOW_MODE, "mover_universe", True)
    monkeypatch.setitem(config.SHADOW_MODE, "mover_cap", 4)
    monkeypatch.setitem(config.SHADOW_MODE, "mover_refresh_s", 900)
    monkeypatch.setitem(config.BROAD_UNIVERSE_MONITOR, "enabled", True)
    monkeypatch.setitem(
        config.BROAD_UNIVERSE_MONITOR, "db_path", str(tmp_path / "broad.sqlite")
    )
    monkeypatch.setitem(
        config.BROAD_UNIVERSE_MONITOR, "min_quote_volume_usdt", 1_000_000.0
    )
    monkeypatch.setitem(config.BROAD_UNIVERSE_MONITOR, "shortlist_cap", 4)
    # This test covers broad-universe wiring, not the optional absolute-USDT
    # research band. Isolate it from the operator's .env profile.
    monkeypatch.setitem(config.BROAD_UNIVERSE_MONITOR, "abs_move_usdt_min", 0.0)
    monkeypatch.setitem(
        config.BROAD_UNIVERSE_MONITOR, "abs_move_usdt_max", float("inf")
    )
    monkeypatch.setitem(
        config.BROAD_UNIVERSE_MONITOR, "prefer_abs_usdt_rank", False
    )

    engine = BotEngine.__new__(BotEngine)
    engine.current_pairs = {
        "binance": {"spot": [], "futures": ["BTC/USDT:USDT"]}
    }
    expected_executable_pairs = {
        "binance": {"spot": [], "futures": ["BTC/USDT:USDT"]}
    }
    engine.active_exchanges = {"binance": binance, "bybit": bybit}

    symbols = engine._shadow_symbols()
    assert {"OUTSIDE/USDT:USDT", "FALLER/USDT:USDT"}.issubset(symbols)
    assert engine.current_pairs == expected_executable_pairs
    assert binance.calls == bybit.calls == 1
    assert engine._broad_universe_monitor.research_only is True
    assert engine._shadow_symbol_venues["BYBITONLY/USDT:USDT"] == "bybit"
    # The provider-level refresh cache prevents another all-market request.
    assert engine._shadow_symbols() == symbols
    assert binance.calls == bybit.calls == 1


def test_shadow_context_uses_the_venue_selected_by_broad_ranking():
    from core.bot_engine import BotEngine

    bars = [[i * 60_000, 1.0, 1.1, 0.9, 1.0, 10.0] for i in range(100)]

    class _ContextExchange:
        def __init__(self, name):
            self.name = name
            self.calls = 0

        def fetch_ohlcv(self, symbol, timeframe="1h", limit=100):
            self.calls += 1
            return bars[-limit:]

    binance = _ContextExchange("binance")
    bybit = _ContextExchange("bybit")
    engine = BotEngine.__new__(BotEngine)
    engine.active_exchanges = {"binance": binance, "bybit": bybit}
    engine._shadow_symbol_venues = {"ONLY/USDT:USDT": "bybit"}

    ctx = engine._shadow_ctx_for_symbol("ONLY/USDT:USDT")
    assert ctx is not None
    assert ctx["venue"] == "bybit"
    assert bybit.calls == 4
    assert binance.calls == 0
