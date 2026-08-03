"""TDD spec for the ListingShortProbeAgent (rev3 post-listing perp short).

LOG-ONLY shadow probe integration of the pipeline's first CONFIRMED_GO candidate
(_workspace/strategy_pipeline/03_rev3_audit_findings.md). Every test here pins a
binding condition from that audit or a charter boundary:

- pure score/pump/mtm math (no market data synthesized for a verdict)
- detect-then-enter lifecycle with the day-1 (24h) entry delay
- crypto-only + funding-charged + shortable universe filters
- 3%-notional, 4-concurrent (12%) cap, chronological skip
- per-bar intra-hold MTM path logged (binding condition #1)
- concurrent account-MTM drawdown logged (binding condition #2)
- day-1 execution realism captured (binding condition #4)
- a discriminating score logged that VARIES (binding condition #5)
- structural LOG-ONLY: no order path reachable
- state survives a restart (persisted known-listings + pending entries)

Run: venv/Scripts/python.exe -m pytest tests/test_listing_short_probe.py -v
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_listing_probe_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_listing_probe_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


HOUR = 3600
DAY = 24 * HOUR


def _candles(start_ts_s: int, closes, highs=None, lows=None):
    """Build [ts_ms, o, h, l, c, v] 1h candles from a close list."""
    out = []
    for i, c in enumerate(closes):
        ts_ms = (start_ts_s + i * HOUR) * 1000
        h = highs[i] if highs else max(c * 1.01, c)
        low = lows[i] if lows else min(c * 0.99, c)
        out.append([ts_ms, c, h, low, c, 1000.0])
    return out


class _Providers:
    """Stub read-only providers. No write endpoint exists on this object —
    that is the structural LOG-ONLY guarantee the probe relies on."""

    def __init__(self, *, universe, ohlcv, market_data, balance=420.0, now=0):
        self._universe = list(universe)
        self._ohlcv = ohlcv                # {sym: [candles...]} full history from listing
        self._market_data = market_data    # {sym: {...}}
        self._balance = balance
        self.now = now
        self.calls = {"markets": 0, "ohlcv": 0, "market_data": 0}

    def markets(self):
        self.calls["markets"] += 1
        return list(self._universe)

    def market_data(self, sym):
        self.calls["market_data"] += 1
        return dict(self._market_data.get(sym, {}))

    def ohlcv(self, sym, timeframe, since_ms):
        self.calls["ohlcv"] += 1
        rows = self._ohlcv.get(sym, [])
        return [r for r in rows if r[0] >= int(since_ms)]

    def balance(self):
        return self._balance

    def now_fn(self):
        return self.now


def _make_probe(wh, providers, state_path, meta_provider=None):
    """Build the probe. ``meta_provider`` defaults to a stub returning a market
    dict with NO underlyingType — i.e. metadata IS available and says nothing
    about tradfi, which must never exclude a crypto perp. Every test injects a
    stub so the ccxt-backed default provider is never reached from the suite."""
    from core.agents.listing_short_probe_agent import ListingShortProbeAgent
    return ListingShortProbeAgent(
        warehouse=wh,
        markets_provider=providers.markets,
        market_data_provider=providers.market_data,
        ohlcv_provider=providers.ohlcv,
        account_balance_provider=providers.balance,
        market_meta_provider=meta_provider or (lambda sym: {"info": {}}),
        now_fn=providers.now_fn,
        state_path=state_path,
    )


def _md(bid=100.0, ask=100.1, last=100.0, qv=5_000_000.0, funding=0.0004, active=True):
    return {"bid": bid, "ask": ask, "last": last, "quoteVolume": qv,
            "funding_rate": funding, "active": active}


def _md_no_book(last=100.0, qv=5_000_000.0, funding=0.0004, active=True):
    """The SHAPE THE LIVE VENUE ACTUALLY RETURNS: binance USDT-M fetch_ticker
    carries no bid/ask (the 24hr-ticker endpoint has none), only last +
    quoteVolume. Measured 2026-07-28 on BTC/USDT:USDT (last=63638.4,
    quoteVolume=9.69e9) and on all 25 rows the probe has logged."""
    return {"bid": None, "ask": None, "last": last, "quoteVolume": qv,
            "funding_rate": funding, "active": active}


def _meta(underlying_type=None, **info_extra):
    """A venue market dict as ccxt load_markets returns it."""
    info = dict(info_extra)
    if underlying_type is not None:
        info["underlyingType"] = underlying_type
    return {"info": info}


# ── Pure math ────────────────────────────────────────────────────────────
def test_is_crypto_base_mirrors_screen():
    """The probe's crypto filter must stay byte-identical to the frozen screen."""
    from core.agents.listing_short_probe_agent import (
        EQUITY_COMMODITY_BASES as probe_set,
    )
    from core.agents.listing_short_probe_agent import (
        is_crypto_base as probe_is_crypto,
    )
    from research.screen_listing_short import EQUITY_COMMODITY_BASES as screen_set
    from research.screen_listing_short import is_crypto_base as screen_is_crypto

    assert set(probe_set) == set(screen_set)
    for base in ["BTC", "SOMI", "AAPL", "XAU", "币安人生", "", "COIN"]:
        assert probe_is_crypto(base) == screen_is_crypto(base)


def test_score_is_monotone_and_varies():
    from core.agents.listing_short_probe_agent import listing_short_score

    # monotone increasing in pump (bigger fade => stronger short)
    assert listing_short_score(2.0, 0.0) > listing_short_score(0.1, 0.0)
    # monotone increasing in funding (short receives + / pays -)
    assert listing_short_score(1.0, 0.01) > listing_short_score(1.0, -0.01)
    # varies across distinct proposals
    scores = {listing_short_score(p, f)
              for p, f in [(0.1, -0.001), (0.8, 0.0), (2.9, 0.005)]}
    assert len(scores) == 3


def test_compute_pump_pct():
    from core.agents.listing_short_probe_agent import compute_pump_pct

    # listing at 100, first-24h high 390 => +290% pump (the SOMI shape)
    assert compute_pump_pct(highs=[110, 390, 200], listing_px=100.0) == pytest.approx(2.9)
    assert compute_pump_pct(highs=[], listing_px=100.0) == 0.0


def test_unrealized_short_return_sign():
    from core.agents.listing_short_probe_agent import unrealized_short_return

    # price falls => short in profit (positive)
    assert unrealized_short_return(100.0, 80.0) == pytest.approx(0.2)
    # price pumps => short underwater (negative)
    assert unrealized_short_return(100.0, 130.0) == pytest.approx(-0.3)


def test_concurrent_account_mtm_drawdown():
    from core.agents.listing_short_probe_agent import concurrent_account_mtm

    # two shorts both underwater at the same calendar bar => account MTM stacks
    bars_by_pos = {
        "a": [(0, -0.5), (HOUR, -0.9)],   # -50%, then -90% short return
        "b": [(HOUR, -0.9)],
    }
    series, max_dd = concurrent_account_mtm(bars_by_pos, stake_frac=0.03)
    # at ts=HOUR both open: mtm = 0.03*(-0.9) + 0.03*(-0.9) = -0.054 => dd ~5.4%
    assert max_dd == pytest.approx(0.054, abs=1e-6)
    assert series[-1][1] == pytest.approx(-0.054, abs=1e-6)


# ── Lifecycle ────────────────────────────────────────────────────────────
def test_first_tick_seeds_and_proposes_nothing(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT", "ETH/USDT:USDT"], ohlcv={}, market_data={})
    probe = _make_probe(wh, p, tmp_path / "state.json")
    stats = probe.tick()
    assert stats["detected"] == 0
    assert stats["entered"] == 0
    # nothing written to shadow_decisions on the seeding tick
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0
    # baseline persisted
    st = json.loads((tmp_path / "state.json").read_text())
    assert st["seeded"] is True
    assert set(st["known"]) == {"BTC/USDT:USDT", "ETH/USDT:USDT"}


def test_new_listing_detected_then_entered_after_day1(wh, tmp_path):
    base_syms = ["BTC/USDT:USDT"]
    p = _Providers(universe=base_syms, ohlcv={}, market_data={}, now=1_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    # a genuinely new crypto perp appears; pumps in its first 24h then decays
    new_sym = "SOMI/USDT:USDT"
    listing_ts = 1_000_300
    closes = [100.0] + [100.0 + i for i in range(1, 40)]   # 40h of data
    highs = [c * 1.5 for c in closes]
    p._universe.append(new_sym)
    p._ohlcv[new_sym] = _candles(listing_ts, closes, highs=highs)
    p._market_data[new_sym] = _md(funding=0.0006)

    p.now = listing_ts + 60          # detection tick (well before +24h)
    stats = probe.tick()
    assert stats["detected"] == 1
    assert stats["entered"] == 0     # not yet 24h old -> no entry
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0

    p.now = listing_ts + DAY + 2 * HOUR   # past the day-1 entry delay
    stats = probe.tick()
    assert stats["entered"] >= 2          # 7d + 30d horizon variants

    rows = wh.query(
        "SELECT * FROM shadow_decisions WHERE symbol=? ORDER BY horizon_bars", (new_sym,))
    assert len(rows) == 2
    for r in rows:
        assert r["side"] == "sell"        # it is a SHORT
        assert r["decision"] == "ALLOW"
        assert r["label_status"] == "PENDING"
        assert r["entry_px"] and r["entry_px"] > 0
        assert r["sl_px"] == 0            # naked, no-SL, held-to-horizon (B2)
        assert r["tp_px"] == 0
    assert {r["horizon_bars"] for r in rows} == {7 * 24, 30 * 24}

    # companion probe rows carry the binding-condition evidence
    prows = wh.query(
        "SELECT * FROM shadow_listing_probe WHERE symbol=? AND decision='ENTER'", (new_sym,))
    assert len(prows) == 2
    pr = prows[0]
    assert pr["day1_spread_bps"] is not None and pr["day1_spread_bps"] >= 0
    assert pr["day1_funding_rate"] == pytest.approx(0.0006)
    assert pr["shortable"] == 1
    assert pr["score"] is not None
    assert pr["pump_pct"] is not None and pr["pump_pct"] > 0
    assert pr["notional_usd"] == pytest.approx(0.03 * 420.0)  # 3% of account


def test_equity_and_junk_bases_excluded(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=2_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    for junk in ["AAPL/USDT:USDT", "XAU/USDT:USDT", "币安人生/USDT:USDT"]:
        p._universe.append(junk)
        p._ohlcv[junk] = _candles(2_000_100, [100.0] * 40)
        p._market_data[junk] = _md()
    p.now = 2_000_100
    stats = probe.tick()
    assert stats["detected"] == 0  # all excluded at the crypto-only gate
    p.now = 2_000_100 + DAY + HOUR
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_concurrency_cap_skips_beyond_four(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=3_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    listing_ts = 3_000_050
    for i in range(6):  # 6 new listings, all entering the SAME 7d/30d window
        sym = f"NEW{i}/USDT:USDT"
        p._universe.append(sym)
        p._ohlcv[sym] = _candles(listing_ts + i, [100.0 + j for j in range(40)],
                                 highs=[(100.0 + j) * 1.3 for j in range(40)])
        p._market_data[sym] = _md(funding=0.0001 * (i + 1))
    p.now = listing_ts + 60
    probe.tick()  # detect all 6

    p.now = listing_ts + DAY + 2 * HOUR
    probe.tick()  # enter -> cap binds

    enters = wh.query(
        "SELECT COUNT(*) n FROM shadow_listing_probe WHERE horizon_days=7 AND decision='ENTER'")[0]["n"]
    caps = wh.query(
        "SELECT COUNT(*) n FROM shadow_listing_probe WHERE horizon_days=7 AND decision='SKIP_CAP'")[0]["n"]
    assert enters == 4          # 12% / 3% = 4 concurrent max
    assert caps == 2            # the 2 chronological latecomers are skipped + counted


def test_unshortable_and_no_funding_are_skipped(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=4_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    listing_ts = 4_000_050
    # unshortable: inactive market
    p._universe.append("DEAD/USDT:USDT")
    p._ohlcv["DEAD/USDT:USDT"] = _candles(listing_ts, [100.0] * 40)
    p._market_data["DEAD/USDT:USDT"] = _md(active=False)
    # no funding: funding_rate missing
    p._universe.append("NOFUND/USDT:USDT")
    p._ohlcv["NOFUND/USDT:USDT"] = _candles(listing_ts, [100.0] * 40)
    p._market_data["NOFUND/USDT:USDT"] = {"bid": 1.0, "ask": 1.01, "active": True}

    p.now = listing_ts + 60
    probe.tick()
    p.now = listing_ts + DAY + 2 * HOUR
    probe.tick()

    # no shadow_decisions rows for the two skips
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0
    reasons = {r["decision"] for r in wh.query(
        "SELECT decision FROM shadow_listing_probe WHERE symbol LIKE '%USDT:USDT'")}
    assert "SKIP_UNSHORTABLE" in reasons
    assert "SKIP_NO_FUNDING" in reasons


def test_intra_hold_mtm_path_and_concurrent_logged(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=5_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    sym = "PUMPY/USDT:USDT"
    listing_ts = 5_000_050
    # first 24h flat-ish, then a pump against the short, then decay
    closes = [100.0] * 26 + [100 + 30 for _ in range(20)]  # 46 bars
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, closes, highs=[c * 1.2 for c in closes])
    p._market_data[sym] = _md(funding=0.0005)

    p.now = listing_ts + 60
    probe.tick()  # detect
    p.now = listing_ts + DAY + 4 * HOUR
    probe.tick()  # enter (day-1 close ~ bar 24)
    p.now = listing_ts + DAY + 20 * HOUR
    probe.tick()  # monitor -> MTM path should now include the pump bars

    mtm = wh.query("SELECT * FROM shadow_listing_mtm ORDER BY bar_ts")
    assert len(mtm) >= 5
    # at least one bar shows the short underwater (unrealized_short_ret < 0)
    assert any(r["unrealized_short_ret"] < 0 for r in mtm)

    snaps = wh.query("SELECT * FROM shadow_listing_concurrent")
    assert len(snaps) >= 1
    assert all(s["max_drawdown"] is not None for s in snaps)


# ── D1: shortability classifier (THE BRICK) ──────────────────────────────
# Measured 2026-07-28: binance USDT-M ccxt fetch_ticker returns bid=None,
# ask=None for EVERY symbol, so `active and bid and ask` was ALWAYS False and
# the probe logged 25/25 SKIP_UNSHORTABLE in 19 days with 0 ENTER.
def test_shortable_falls_back_to_last_and_volume_when_book_absent():
    from core.agents.listing_short_probe_agent import classify_shortability

    shortable, basis = classify_shortability(_md_no_book(last=63638.4, qv=9.69e9))
    assert shortable is True
    assert basis == "last_volume"     # provenance of the evidence path used


def test_bid_ask_remains_authoritative_when_present():
    from core.agents.listing_short_probe_agent import classify_shortability

    shortable, basis = classify_shortability(_md())
    assert shortable is True
    assert basis == "bid_ask"         # no regression on the original path


@pytest.mark.parametrize("md, basis", [
    (_md(active=False), "inactive"),                       # dead market, book present
    (_md_no_book(active=False), "inactive"),               # dead market, no book
    (_md_no_book(last=None), "no_quote"),                  # no price at all
    (_md_no_book(qv=0.0), "no_quote"),                     # zero traded volume
    (_md_no_book(last=0.0, qv=1.0), "no_quote"),           # non-positive price
    ({}, "no_quote"),                                      # provider returned nothing
])
def test_not_shortable_without_live_tradeable_evidence(md, basis):
    from core.agents.listing_short_probe_agent import classify_shortability

    shortable, got_basis = classify_shortability(md)
    assert shortable is False
    assert got_basis == basis


def test_new_listing_with_no_book_enters_and_records_basis(wh, tmp_path):
    """End-to-end unbrick: the live venue's bid/ask-less ticker must ENTER."""
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=7_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    sym = "NEWCOIN/USDT:USDT"
    listing_ts = 7_000_050
    closes = [100.0 + i for i in range(40)]
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, closes, highs=[c * 1.5 for c in closes])
    p._market_data[sym] = _md_no_book(funding=0.0006)

    p.now = listing_ts + 60
    probe.tick()
    p.now = listing_ts + DAY + 2 * HOUR
    stats = probe.tick()

    assert stats["entered"] == 2      # 7d + 30d horizons
    rows = wh.query(
        "SELECT * FROM shadow_listing_probe WHERE symbol=? AND decision='ENTER'", (sym,))
    assert len(rows) == 2
    for r in rows:
        assert r["shortable"] == 1
        assert r["shortable_basis"] == "last_volume"
    assert wh.query(
        "SELECT COUNT(*) n FROM shadow_decisions WHERE symbol=?", (sym,))[0]["n"] == 2


# ── D2: spread must be NULL, never a fabricated 0.0 ──────────────────────
def test_day1_spread_is_null_when_book_absent(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=8_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    sym = "NOBOOK/USDT:USDT"
    listing_ts = 8_000_050
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, [100.0 + i for i in range(40)])
    p._market_data[sym] = _md_no_book()

    p.now = listing_ts + 60
    probe.tick()
    p.now = listing_ts + DAY + 2 * HOUR
    probe.tick()

    rows = wh.query("SELECT * FROM shadow_listing_probe WHERE symbol=?", (sym,))
    assert rows
    for r in rows:
        # unknown spread is UNKNOWN — a 0.0 here would pass any "tight spread"
        # quality gate trivially (fail-OPEN), which is worse than the brick
        assert r["day1_spread_bps"] is None


def test_day1_spread_still_measured_when_book_present(wh, tmp_path):
    from core.agents.listing_short_probe_agent import day1_spread_bps

    assert day1_spread_bps(_md(bid=100.0, ask=100.1)) == pytest.approx(
        (100.1 - 100.0) / 100.05 * 1e4)
    assert day1_spread_bps(_md_no_book()) is None
    assert day1_spread_bps(_md(bid=100.1, ask=100.0)) is None  # crossed => unknown


# ── D3: scope fidelity — tokenized equities/ETFs are OUT OF SCOPE ────────
# All 25 observed events were binance tokenized equities (underlyingType
# EQUITY x19 / HK_EQUITY x6). The frozen rev3 universe is crypto-only.
@pytest.mark.parametrize("base, underlying", [
    ("BNC", "EQUITY"), ("SKHY", "HK_EQUITY"), ("PANW", "EQUITY"),
    ("BITO", "EQUITY"), ("TBT", "EQUITY"), ("TMF", "EQUITY"),
])
def test_tokenized_equity_skipped_as_not_crypto(wh, tmp_path, base, underlying):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=9_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json",
                        meta_provider=lambda sym: _meta(underlying))

    probe.tick()  # seed
    sym = f"{base}/USDT:USDT"
    listing_ts = 9_000_050
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, [100.0 + i for i in range(40)])
    p._market_data[sym] = _md_no_book()

    p.now = listing_ts + 60
    probe.tick()
    p.now = listing_ts + DAY + 2 * HOUR
    probe.tick()

    decisions = {r["decision"] for r in wh.query(
        "SELECT decision FROM shadow_listing_probe WHERE symbol=?", (sym,))}
    assert decisions == {"SKIP_NOT_CRYPTO"}          # its OWN honest reason
    assert "SKIP_UNSHORTABLE" not in decisions       # never mislabeled again
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


@pytest.mark.parametrize("meta", [
    {"info": {}},                                    # no underlyingType at all
    {"info": {"underlyingType": "COIN"}},            # binance crypto perp
    {},                                              # venue publishes no metadata
    {"info": None},                                  # malformed metadata
])
def test_missing_underlying_type_never_excludes_a_crypto_perp(wh, tmp_path, meta):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=10_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json", meta_provider=lambda sym: meta)

    probe.tick()  # seed
    sym = "REALCOIN/USDT:USDT"
    listing_ts = 10_000_050
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, [100.0 + i for i in range(40)])
    p._market_data[sym] = _md_no_book()

    p.now = listing_ts + 60
    probe.tick()
    p.now = listing_ts + DAY + 2 * HOUR
    stats = probe.tick()

    assert stats["entered"] == 2
    assert wh.query(
        "SELECT COUNT(*) n FROM shadow_listing_probe "
        "WHERE symbol=? AND decision='SKIP_NOT_CRYPTO'", (sym,))[0]["n"] == 0


def test_unavailable_metadata_fails_closed_with_its_own_reason(wh, tmp_path):
    """A metadata OUTAGE must not silently read as 'crypto, proceed'.

    It also must not BURN a genuine listing on one transient failure: while the
    day-1 entry window is open the decision is retried (no row, nothing logged);
    only once the window expires is the honest terminal skip written. Same
    idiom as SKIP_NO_DATA. Entry timing is unaffected — the entry bar is picked
    from the candle timestamps, not from the tick that resolves it."""
    outage = {"on": True}
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=11_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json",
                        meta_provider=lambda sym: None if outage["on"] else _meta("COIN"))

    probe.tick()  # seed
    sym = "UNKNOWN/USDT:USDT"
    listing_ts = 11_000_050
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, [100.0 + i for i in range(40)])
    p._market_data[sym] = _md_no_book()

    p.now = listing_ts + 60
    probe.tick()
    p.now = listing_ts + DAY + 2 * HOUR
    probe.tick()                      # metadata down -> retry, nothing decided
    assert wh.query("SELECT COUNT(*) n FROM shadow_listing_probe "
                    "WHERE symbol=?", (sym,))[0]["n"] == 0

    outage["on"] = False              # metadata comes back inside the window
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_listing_probe "
                    "WHERE symbol=? AND decision='ENTER'", (sym,))[0]["n"] == 2


def test_persistent_metadata_outage_logs_terminal_skip(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=12_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json", meta_provider=lambda sym: None)

    probe.tick()  # seed
    sym = "NOMETA/USDT:USDT"
    listing_ts = 12_000_050
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, [100.0 + i for i in range(40)])
    p._market_data[sym] = _md_no_book()

    p.now = listing_ts + 60
    probe.tick()
    p.now = listing_ts + 5 * DAY      # past ENTRY_DELAY + MAX_ENTRY_WAIT (1d+3d)
    probe.tick()

    decisions = {r["decision"] for r in wh.query(
        "SELECT decision FROM shadow_listing_probe WHERE symbol=?", (sym,))}
    assert decisions == {"SKIP_NO_METADATA"}
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_default_meta_provider_returns_none_on_unknown_venue():
    """The ccxt-backed default must degrade to None (-> fail-closed skip), not
    raise, when the venue has no ccxt class. No network is touched."""
    from core.agents.listing_short_probe_agent import venue_market_meta

    assert venue_market_meta("no_such_venue_xyz", "BTC/USDT:USDT") is None


def test_tradfi_gate_runs_in_addition_to_the_frozen_mirror(wh, tmp_path):
    """The is_crypto_base mirror still excludes its statics on its own — the
    new gate is ADDITIVE, never a replacement."""
    from core.agents.listing_short_probe_agent import is_crypto_base, is_tradfi_market

    assert is_crypto_base("AAPL") is False          # mirror, untouched
    assert is_tradfi_market(_meta("EQUITY")) is True
    assert is_tradfi_market(_meta("HK_EQUITY")) is True
    assert is_tradfi_market(_meta("COIN")) is False
    assert is_tradfi_market({}) is False            # defensive: no metadata != tradfi
    assert is_tradfi_market(None) is False
    assert is_tradfi_market({"info": {"contractType": "TRADIFI_PERPETUAL"}}) is True


# ── Idempotent additive migration (live table already has 25 rows) ───────
_LEGACY_DDL = """
CREATE TABLE shadow_listing_probe (
    proposal_id                TEXT PRIMARY KEY,
    symbol                     TEXT,
    base                       TEXT,
    horizon_days               INTEGER,
    decision                   TEXT,
    detected_ts                INTEGER,
    entry_ts                   INTEGER,
    entry_px                   REAL,
    listing_px                 REAL,
    stake_frac                 REAL,
    notional_usd               REAL,
    day1_spread_bps            REAL,
    day1_funding_rate          REAL,
    shortable                  INTEGER,
    quote_volume_usd           REAL,
    pump_pct                   REAL,
    score                      REAL,
    realized_funding_rate_sum  REAL,
    last_funding_bucket        INTEGER,
    concurrent_open_at_entry   INTEGER,
    created_ts                 INTEGER
);
"""


def test_migration_adds_shortable_basis_to_a_preexisting_table(wh, tmp_path):
    conn = wh._conn()
    conn.executescript(_LEGACY_DDL)
    conn.execute("INSERT INTO shadow_listing_probe (proposal_id, decision) "
                 "VALUES ('ls-legacy','SKIP_UNSHORTABLE')")
    conn.commit()

    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={})
    # F3a (2026-07-29): migration is DEFERRED to the first tick — construction
    # must never ALTER the live table (bot_engine._build_probe is fail-open,
    # so a construction-time DB error silently removed the whole lane).
    _make_probe(wh, p, tmp_path / "state.json").tick()   # first tick migrates
    _make_probe(wh, p, tmp_path / "state2.json").tick()  # idempotent: no error

    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(shadow_listing_probe)").fetchall()}
    assert "shortable_basis" in cols
    legacy = wh.query("SELECT * FROM shadow_listing_probe WHERE proposal_id='ls-legacy'")
    assert legacy[0]["shortable_basis"] is None          # pre-existing rows unharmed
    conn.execute("INSERT INTO shadow_listing_probe (proposal_id, shortable_basis) "
                 "VALUES ('ls-new','last_volume')")
    conn.commit()


def test_structural_log_only_no_order_path():
    """The probe module must contain no reference to any order/write path."""
    src = (ROOT / "core" / "agents" / "listing_short_probe_agent.py").read_text(
        encoding="utf-8")
    for forbidden in ("order_manager", "OrderManager", "create_order",
                      "open_position", "place_order", "cancel_order"):
        assert forbidden not in src, f"probe references an order path: {forbidden}"


def test_state_survives_restart(wh, tmp_path):
    state = tmp_path / "state.json"
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=6_000_000)
    probe = _make_probe(wh, p, state)
    probe.tick()  # seed

    sym = "REBOOT/USDT:USDT"
    listing_ts = 6_000_050
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, [100.0 + i for i in range(40)])
    p._market_data[sym] = _md()
    p.now = listing_ts + 60
    probe.tick()  # detect -> pending persisted

    # brand-new instance (simulated restart) reads the same state file
    probe2 = _make_probe(wh, p, state)
    p.now = listing_ts + DAY + 2 * HOUR
    stats = probe2.tick()          # the pending listing must still enter
    assert stats["entered"] >= 2
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions WHERE symbol=?", (sym,))[0]["n"] == 2


def test_companion_tables_exist(wh, tmp_path):
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={})
    _make_probe(wh, p, tmp_path / "state.json")
    tables = {r["name"] for r in wh.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shadow_listing_probe", "shadow_listing_mtm",
            "shadow_listing_concurrent"} <= tables


def test_shadow_runner_calls_extra_probes(wh):
    from core.shadow_runner import ShadowRunner

    class _CountingProbe:
        name = "CountingProbe"

        def __init__(self):
            self.ticks = 0

        def tick(self):
            self.ticks += 1
            return {"detected": 0}

    probe = _CountingProbe()
    runner = ShadowRunner(
        warehouse=wh,
        ctx_provider=lambda s: None,
        free_balance_provider=lambda: 100.0,
        symbols_provider=lambda: [],
        allowed_hours=set(range(24)),
        extra_probes=[probe],
    )
    runner.tick()
    assert probe.ticks == 1


def test_shadow_runner_disabled_skips_extra_probes(wh):
    from core.shadow_runner import ShadowRunner

    class _CountingProbe:
        name = "CountingProbe"
        ticks = 0

        def tick(self):
            type(self).ticks += 1

    probe = _CountingProbe()
    runner = ShadowRunner(
        warehouse=wh,
        ctx_provider=lambda s: None,
        free_balance_provider=lambda: 100.0,
        symbols_provider=lambda: [],
        enabled_flag=lambda: False,
        extra_probes=[probe],
    )
    runner.tick()
    assert probe.ticks == 0


# ── F3a/F3b/F3c hardening (2026-07-29) ───────────────────────────────────────

class _FlakyConnProxy:
    """Passes everything to the real connection but fails PRAGMA/ALTER while
    the shared flag is on — simulating 'database is locked' at boot."""

    def __init__(self, real, fail):
        self._real = real
        self._fail = fail

    def execute(self, sql, *a, **k):
        if self._fail["on"] and (
            "PRAGMA table_info" in sql or sql.lstrip().upper().startswith("ALTER")
        ):
            import sqlite3
            raise sqlite3.OperationalError("database is locked")
        return self._real.execute(sql, *a, **k)

    def __getattr__(self, name):
        return getattr(self._real, name)


class _FlakyWarehouse:
    def __init__(self, wh, fail):
        self._real_wh = wh
        self._fail = fail

    def _conn(self):
        return _FlakyConnProxy(self._real_wh._conn(), self._fail)

    def __getattr__(self, name):
        return getattr(self._real_wh, name)


def test_migration_failure_leaves_probe_alive_and_retried(wh, tmp_path):
    """F3a: a failed column migration must NOT kill construction (bot_engine's
    fail-open _build_probe would silently drop the lane for the whole boot).
    It must degrade loudly-but-alive and retry on a later tick."""
    fail = {"on": True}
    flaky = _FlakyWarehouse(wh, fail)
    p = _Providers(universe=[], ohlcv={}, market_data={}, now=1_000_000)
    probe = _make_probe(flaky, p, tmp_path / "st.json")   # must NOT raise
    stats = probe.tick()                                   # migration fails
    assert stats == {"detected": 0, "entered": 0, "skipped": 0, "mtm_rows": 0}
    fail["on"] = False
    probe.tick()                                           # DB recovered
    cols = {r[1] for r in wh._conn().execute(
        "PRAGMA table_info(shadow_listing_probe)").fetchall()}
    assert "shortable_basis" in cols                       # migration completed


def test_raising_market_data_does_not_consume_listing(wh, tmp_path):
    """F3c: a raising market-data provider must not mark the listing decided
    with NO row written (silent evidence loss). The pending entry stays
    unconsumed and a later tick with a working provider writes its rows."""
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=1_000_000)
    probe = _make_probe(wh, p, tmp_path / "state.json")
    probe.tick()  # seed

    new_sym = "SOMI/USDT:USDT"
    listing_ts = 1_000_300
    closes = [100.0] + [100.0 + i for i in range(1, 40)]
    p._universe.append(new_sym)
    p._ohlcv[new_sym] = _candles(listing_ts, closes)
    p._market_data[new_sym] = _md(funding=0.0006)

    p.now = listing_ts + 60
    assert probe.tick()["detected"] == 1

    def _raising_md(sym):
        raise RuntimeError("provider outage")

    good_md = probe._market_data
    probe._market_data = _raising_md
    p.now = listing_ts + DAY + 2 * HOUR
    probe.tick()  # provider raises mid-entry
    assert probe._state["pending"][new_sym]["entered"] is False, (
        "a raising provider must not consume the listing")
    n = wh.query("SELECT COUNT(*) n FROM shadow_listing_probe WHERE symbol=?",
                 (new_sym,))[0]["n"]
    assert n == 0

    probe._market_data = good_md                  # provider recovers
    stats = probe.tick()
    assert stats["entered"] >= 2                  # evidence was not lost


def test_negative_cache_suppresses_repeat_load_failures(monkeypatch):
    """F3b: a failed load_markets is remembered per venue for a short TTL so a
    persistent outage does not hammer the venue every tick for ~4 days."""
    import core.agents.listing_short_probe_agent as mod
    calls = {"n": 0}

    def fake_load(venue):
        calls["n"] += 1
        return None

    monkeypatch.setattr(mod, "_load_venue_markets", fake_load)
    monkeypatch.setattr(mod, "_MARKETS_CACHE", {})
    monkeypatch.setattr(mod, "_MARKETS_ATTEMPT_TS", {}, raising=False)

    assert mod.venue_market_meta("binance", "X/USDT:USDT", now=1000.0) is None
    assert mod.venue_market_meta("binance", "X/USDT:USDT", now=1100.0) is None
    assert calls["n"] == 1                         # inside NEG TTL: no re-load
    later = 1000.0 + mod.MARKETS_NEG_TTL_S + 1
    assert mod.venue_market_meta("binance", "X/USDT:USDT", now=later) is None
    assert calls["n"] == 2                         # TTL elapsed: re-attempt


def test_negative_cache_covers_symbol_miss(monkeypatch):
    """A successful load that still lacks the symbol must not re-fetch every
    call either (brand-new listing absent from the venue snapshot)."""
    import core.agents.listing_short_probe_agent as mod
    calls = {"n": 0}

    def fake_load(venue):
        calls["n"] += 1
        return {"OTHER/USDT:USDT": {"info": {}}}

    monkeypatch.setattr(mod, "_load_venue_markets", fake_load)
    monkeypatch.setattr(mod, "_MARKETS_CACHE", {})
    monkeypatch.setattr(mod, "_MARKETS_ATTEMPT_TS", {}, raising=False)

    assert mod.venue_market_meta("binance", "NEW/USDT:USDT", now=1000.0) is None
    assert mod.venue_market_meta("binance", "NEW/USDT:USDT", now=1100.0) is None
    assert calls["n"] == 1
    # the known symbol is still served from the cached snapshot
    assert mod.venue_market_meta("binance", "OTHER/USDT:USDT", now=1100.0) == {"info": {}}
    assert calls["n"] == 1


def test_positive_cache_behavior_preserved(monkeypatch):
    """Regression guard: a cached present symbol is served without re-loading."""
    import core.agents.listing_short_probe_agent as mod
    calls = {"n": 0}

    def fake_load(venue):
        calls["n"] += 1
        return {"X/USDT:USDT": {"info": {"k": 1}}}

    monkeypatch.setattr(mod, "_load_venue_markets", fake_load)
    monkeypatch.setattr(mod, "_MARKETS_CACHE", {})
    monkeypatch.setattr(mod, "_MARKETS_ATTEMPT_TS", {}, raising=False)

    assert mod.venue_market_meta("binance", "X/USDT:USDT", now=1000.0) == {"info": {"k": 1}}
    assert mod.venue_market_meta("binance", "X/USDT:USDT", now=2000.0) == {"info": {"k": 1}}
    assert calls["n"] == 1


# ── Detect-time TradFi filter + legacy reclassify (STARVED unbrick) ───────
def test_tradfi_skipped_at_detection_without_day1_wait(wh, tmp_path):
    """TradFi must not sit in pending for a day then look like a shortability fault."""
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=20_000_000)
    probe = _make_probe(
        wh, p, tmp_path / "state.json",
        meta_provider=lambda sym: _meta("EQUITY", contractType="TRADIFI_PERPETUAL"),
    )
    probe.tick()  # seed
    sym = "SOFI/USDT:USDT"
    p._universe.append(sym)
    p.now = 20_000_100
    n = probe.tick()["detected"]
    assert n == 0  # not counted as a crypto detect
    decisions = {r["decision"] for r in wh.query(
        "SELECT decision FROM shadow_listing_probe WHERE symbol=?", (sym,))}
    assert decisions == {"SKIP_NOT_CRYPTO"}
    st = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert st["pending"][sym]["entered"] is True


def test_crypto_still_enters_after_tradfi_detect_filter(wh, tmp_path):
    """Emit-path regression: genuine crypto with Binance-shaped no-book ticker ENTERs."""
    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=21_000_000)
    probe = _make_probe(
        wh, p, tmp_path / "state.json",
        meta_provider=lambda sym: _meta("COIN"),
    )
    probe.tick()
    sym = "NEWCRYPTO/USDT:USDT"
    listing_ts = 21_000_050
    p._universe.append(sym)
    p._ohlcv[sym] = _candles(listing_ts, [100.0 + i for i in range(40)])
    p._market_data[sym] = _md_no_book(funding=0.0005)
    p.now = listing_ts + 60
    assert probe.tick()["detected"] == 1
    p.now = listing_ts + DAY + 2 * HOUR
    stats = probe.tick()
    assert stats["entered"] == 2
    assert wh.query(
        "SELECT COUNT(*) n FROM shadow_listing_probe "
        "WHERE symbol=? AND decision='ENTER'", (sym,))[0]["n"] == 2


def test_legacy_unshortable_tradfi_rows_reclassified(wh, tmp_path):
    """Historical SKIP_UNSHORTABLE on TradFi must become SKIP_NOT_CRYPTO once."""
    from core.agents.probe_common import insert_row

    p = _Providers(universe=["BTC/USDT:USDT"], ohlcv={}, market_data={}, now=22_000_000)
    probe = _make_probe(
        wh, p, tmp_path / "state.json",
        meta_provider=lambda s: _meta("EQUITY", contractType="TRADIFI_PERPETUAL"),
    )
    probe.tick()  # seed + create schema

    sym = "LEGACYEQ/USDT:USDT"
    insert_row(wh, "shadow_listing_probe", {
        "proposal_id": "ls-legacy01", "symbol": sym, "base": "LEGACYEQ",
        "horizon_days": 0, "decision": "SKIP_UNSHORTABLE",
        "detected_ts": 1, "entry_ts": 2, "entry_px": 1.0, "listing_px": 1.0,
        "stake_frac": 0.03, "notional_usd": 10.0, "day1_spread_bps": None,
        "day1_funding_rate": 0.0, "shortable": 0, "shortable_basis": None,
        "quote_volume_usd": 1.0, "pump_pct": 0.0, "score": 0.0,
        "realized_funding_rate_sum": 0.0, "last_funding_bucket": None,
        "concurrent_open_at_entry": 0, "created_ts": int(__import__("time").time()),
    }, replace=True)

    probe.tick()  # reclassify
    rows = wh.query(
        "SELECT decision FROM shadow_listing_probe WHERE symbol=?", (sym,))
    assert rows and rows[0]["decision"] == "SKIP_NOT_CRYPTO"
    st = json.loads((tmp_path / "state.json").read_text(encoding="utf-8"))
    assert st.get("reclassified_tradfi_skips_v1") is True
