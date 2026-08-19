"""Multi-venue listing-short OBSERVATION arms (prereg 86, sha256 3c660f70…).

The binance listing-short lane is the pipeline's strongest CONFIRMED_GO, but
binance's crypto-native listing rate collapsed to ~1.5/month. Prereg 86 adds
bybit/bitget OBSERVATION arms — log-only, expectation NO_GO (a cross-listed
token has no day-1 discovery to fade). The properties tested here are the
binding non-pooling clauses:

  * The binance instance keeps its EXACT historical identity (model_version
    `listing_short_probe_v1`, name, state path) — byte-compatible with all
    accrued history. New venues get suffixed identities + separate state.
  * `shadow_listing_probe` rows carry `venue`; historical rows stay NULL and
    count as binance.
  * The FROZEN binance funnel lane counts only NULL/'binance' rows — bybit or
    bitget rows can never pollute it.

Run: venv/Scripts/python.exe -m pytest tests/test_listing_multivenue.py -v
"""

from __future__ import annotations

import importlib.util
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.agents.listing_short_probe_agent import (  # noqa: E402
    LISTING_SHORT_MODEL_VERSION,
    ListingShortProbeAgent,
)


def _load_warehouse():
    spec = importlib.util.spec_from_file_location(
        "warehouse_mv_test", ROOT / "core" / "warehouse.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


def _agent(wh, tmp_path, venue="binance", state_path=None):
    return ListingShortProbeAgent(
        warehouse=wh,
        markets_provider=lambda: [],
        market_data_provider=lambda sym: {},
        ohlcv_provider=lambda sym, tf, since: [],
        account_balance_provider=lambda: 5000.0,
        market_meta_provider=lambda sym: {"info": {}},
        now_fn=time.time,
        state_path=state_path,
        venue=venue,
    )


# ── identity: binance byte-compat, new venues suffixed ──────────────────────

def test_binance_identity_is_byte_compatible(wh, tmp_path):
    a = _agent(wh, tmp_path, venue="binance")
    assert a.model_version == LISTING_SHORT_MODEL_VERSION == "listing_short_probe_v1"
    assert a.name == "ListingShortProbeAgent"
    assert str(a._state_path).replace("\\", "/").endswith(
        "data/shadow_listing_state.json")


def test_bybit_identity_is_suffixed_and_isolated(wh, tmp_path):
    a = _agent(wh, tmp_path, venue="bybit")
    assert a.model_version == "listing_short_probe_bybit_v1"
    assert a.name == "ListingShortProbeAgent_bybit"
    assert str(a._state_path).replace("\\", "/").endswith(
        "data/shadow_listing_state_bybit.json")


def test_explicit_state_path_still_wins(wh, tmp_path):
    a = _agent(wh, tmp_path, venue="bitget", state_path=str(tmp_path / "x.json"))
    assert a._state_path == tmp_path / "x.json"


# ── venue lands in the telemetry rows ───────────────────────────────────────

def test_probe_rows_carry_venue(wh, tmp_path):
    a = _agent(wh, tmp_path, venue="bybit")
    a._log_skip("ABC/USDT:USDT", "SKIP_NO_DATA", int(time.time()),
                detected_ts=int(time.time()))
    rows = wh.query("SELECT venue, decision FROM shadow_listing_probe")
    assert rows and rows[0]["venue"] == "bybit"


def test_null_venue_rows_survive_for_history(wh, tmp_path):
    """Historical rows (pre-86) have NULL venue and must stay NULL."""
    a = _agent(wh, tmp_path, venue="binance")
    a._log_skip("OLD/USDT:USDT", "SKIP_NO_DATA", int(time.time()),
                detected_ts=int(time.time()))
    # binance instance writes its venue explicitly; the point is the COLUMN
    # tolerates NULL for legacy rows:
    wh._conn().execute(
        "UPDATE shadow_listing_probe SET venue=NULL")
    wh._conn().commit()
    assert wh.query("SELECT venue FROM shadow_listing_probe")[0]["venue"] is None


# ── config: venues parse ────────────────────────────────────────────────────

def test_config_default_is_single_binance(monkeypatch):
    monkeypatch.delenv("SHADOW_LISTING_PROBE_VENUES", raising=False)
    import importlib

    import config.probes as cp
    importlib.reload(cp)
    assert tuple(cp.LISTING_SHORT_PROBE["venues"]) == ("binance",)


def test_config_parses_csv_venues(monkeypatch):
    monkeypatch.setenv("SHADOW_LISTING_PROBE_VENUES", "binance, bybit,bitget")
    import importlib

    import config.probes as cp
    importlib.reload(cp)
    assert tuple(cp.LISTING_SHORT_PROBE["venues"]) == ("binance", "bybit", "bitget")
    monkeypatch.delenv("SHADOW_LISTING_PROBE_VENUES", raising=False)
    importlib.reload(cp)


# ── engine builds one instance per venue ────────────────────────────────────

def test_engine_builds_one_instance_per_venue(wh, tmp_path, monkeypatch):
    import config
    from core.bot_engine import BotEngine

    monkeypatch.setattr(
        config, "LISTING_SHORT_PROBE",
        {**config.LISTING_SHORT_PROBE, "enabled": True,
         "venues": ("binance", "bybit", "bitget")},
        raising=False)
    eng = BotEngine.__new__(BotEngine)
    eng.active_exchanges = {}
    eng._shadow_free_balance = lambda: 5000.0
    spec = next(s for s in eng._PROBE_SPECS if s["config"] == "LISTING_SHORT_PROBE")
    probes = eng._build_probe(wh, spec)
    assert len(probes) == 3
    assert [p._venue for p in probes] == ["binance", "bybit", "bitget"]
    assert probes[0].model_version == "listing_short_probe_v1"
    assert {p.model_version for p in probes[1:]} == {
        "listing_short_probe_bybit_v1", "listing_short_probe_bitget_v1"}
    assert len({str(p._state_path) for p in probes}) == 3, "state must not be shared"


# ── the frozen funnel lane is venue-pinned ──────────────────────────────────

def test_funnel_binance_lane_excludes_other_venues(wh, tmp_path):
    import scripts.promotion_funnel as pf

    conn = wh._conn()
    _agent(wh, tmp_path)  # ensure schema + venue column
    now = int(time.time())
    for pid, venue in (("ls-null", None), ("ls-bnb", "binance"), ("ls-byb", "bybit")):
        conn.execute(
            "INSERT OR REPLACE INTO shadow_listing_probe "
            "(proposal_id, symbol, base, horizon_days, decision, detected_ts, "
            " entry_ts, entry_px, listing_px, stake_frac, notional_usd, "
            " day1_funding_rate, shortable, quote_volume_usd, pump_pct, score, "
            " concurrent_open_at_entry, created_ts, venue) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pid, "AAA/USDT:USDT", "AAA", 7, "ENTER", now, now, 1.0, 1.0,
             0.03, 150.0, 0.0, 1, 1e6, 0.1, 0.5, 1, now, venue))
        conn.execute(
            "INSERT OR REPLACE INTO shadow_outcomes "
            "(proposal_id, net_pnl, resolved_ts) VALUES (?,?,?)",
            (pid, 1.0, now))
    conn.commit()

    lane = pf.listing_lane_state(conn, float(now))
    assert lane.resolved == 2, (
        f"frozen lane must count NULL+binance only, got {lane.resolved}")
    bybit_lane = pf.listing_lane_state(conn, float(now), venue="bybit")
    assert bybit_lane.resolved == 1


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
