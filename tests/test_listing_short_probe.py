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


def _make_probe(wh, providers, state_path):
    from core.agents.listing_short_probe_agent import ListingShortProbeAgent
    return ListingShortProbeAgent(
        warehouse=wh,
        markets_provider=providers.markets,
        market_data_provider=providers.market_data,
        ohlcv_provider=providers.ohlcv,
        account_balance_provider=providers.balance,
        now_fn=providers.now_fn,
        state_path=state_path,
    )


def _md(bid=100.0, ask=100.1, last=100.0, qv=5_000_000.0, funding=0.0004, active=True):
    return {"bid": bid, "ask": ask, "last": last, "quoteVolume": qv,
            "funding_rate": funding, "active": active}


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
