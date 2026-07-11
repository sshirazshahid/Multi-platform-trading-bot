"""TDD spec for the UnlockShortProbeAgent (pre-unlock cliff short, 08b GO).

LOG-ONLY shadow probe integration of the pipeline's second CONFIRMED-GO
candidate `pre_unlock_short_capital_scaled`
(_workspace/strategy_pipeline/09_audit_candidate2_final.md). Every test pins a
binding condition from that audit or a charter boundary:

- C1 LOG-ONLY: no order path reachable (structural source scan)
- C2 per-bar intra-hold MTM logged + entry/exit ts+px, venue, realized funding,
  unlock ratio and the frozen score AT ENTRY
- C3 8%-adverse-MTM Stop-Loss-Guardian counterfactual: first breaching bar
  flagged AND a parallel sl8 shadow_decisions row so the KEYSTONE resolver
  (never the probe) produces both the raw and SL-counterfactual PnL
- C4 W1 (T-28d) and W2 (T-14d) arms only, logged/scored separately;
  W3 (T->T+3d) NOT implemented (failed its AUC gate 0.591 < 0.60)
- C5 calendar snapshotted AS-OF SIGNAL TIME; a refreshed/edited calendar can
  never rewrite an already-logged signal
- frozen constants mirror research/screen_preunlock_short.py exactly
- capital-scaling: 3% of account equity, UNLEVERED, 4-concurrent (12%) cap
  per arm, chronological skip

Run: venv/Scripts/python.exe -m pytest tests/test_unlock_short_probe.py -v
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
        "warehouse_unlock_probe_test", ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["warehouse_unlock_probe_test"] = mod
    spec.loader.exec_module(mod)
    return mod


@pytest.fixture
def wh(tmp_path):
    return _load_warehouse().Warehouse(path=tmp_path / "t.sqlite")


HOUR = 3600
DAY = 24 * HOUR


def _floor_day(ts: int) -> int:
    return ts // DAY * DAY

# A future unlock event: T at 06:00 UTC on a clean day boundary.
T_UNLOCK = _floor_day(2_000_000_000) + 6 * HOUR
W1_TARGET = _floor_day(T_UNLOCK) - 28 * DAY
W2_TARGET = _floor_day(T_UNLOCK) - 14 * DAY


def _event(ts=T_UNLOCK, tokens=50_000_000.0, ratio=0.12):
    return {"ts": ts, "tokens": tokens, "ratio": ratio}


def _doc(symbol="ARB", events=None, fetched="2026-07-11T12:00:00+00:00"):
    return {
        "symbol": symbol,
        "slug": symbol.lower(),
        "venues_with_perp": ["binance"],
        "events": events if events is not None else [_event()],
        "source": f"https://defillama-datasets.llama.fi/emissions/{symbol.lower()} "
                  f"(keyless bucket, fetched {fetched})",
    }


def _candles(start_ts_s: int, closes, highs=None, lows=None):
    """[ts_ms, o, h, l, c, v] 1h candles from a close list."""
    out = []
    for i, c in enumerate(closes):
        ts_ms = (start_ts_s + i * HOUR) * 1000
        h = highs[i] if highs else max(c * 1.01, c)
        low = lows[i] if lows else min(c * 0.99, c)
        out.append([ts_ms, c, h, low, c, 1000.0])
    return out


class _Providers:
    """Stub READ-ONLY providers. No write endpoint exists on this object —
    the structural LOG-ONLY guarantee the probe relies on."""

    def __init__(self, *, docs, perps, market_data, ohlcv, balance=420.0, now=0):
        self.docs = list(docs)              # calendar documents
        self._perps = dict(perps)           # base -> (venue, symbol)
        self._market_data = dict(market_data)   # (venue, symbol) -> {...}
        self._ohlcv = dict(ohlcv)           # (venue, symbol) -> [candles...]
        self._balance = balance
        self.now = now

    def calendar(self):
        return [dict(d) for d in self.docs]

    def perp_resolver(self, base):
        return self._perps.get(base)

    def market_data(self, venue, symbol):
        return dict(self._market_data.get((venue, symbol), {}))

    def ohlcv(self, venue, symbol, timeframe, since_ms):
        rows = self._ohlcv.get((venue, symbol), [])
        return [r for r in rows if r[0] >= int(since_ms)]

    def balance(self):
        return self._balance

    def now_fn(self):
        return self.now


def _make_probe(wh, providers, state_path):
    from core.agents.unlock_short_probe_agent import UnlockShortProbeAgent
    return UnlockShortProbeAgent(
        warehouse=wh,
        calendar_provider=providers.calendar,
        perp_resolver=providers.perp_resolver,
        market_data_provider=providers.market_data,
        ohlcv_provider=providers.ohlcv,
        account_balance_provider=providers.balance,
        now_fn=providers.now_fn,
        state_path=state_path,
    )


def _md(bid=99.9, ask=100.1, funding=0.0004, active=True, qv=5_000_000.0):
    return {"bid": bid, "ask": ask, "quoteVolume": qv,
            "funding_rate": funding, "active": active}


def _default_providers(now, *, funding=0.0004, closes=None):
    """One ARB event, binance perp, 72h of 1h candles ending after `now`."""
    start = now - 48 * HOUR
    closes = closes if closes is not None else [100.0] * 72
    return _Providers(
        docs=[_doc()],
        perps={"ARB": ("binance", "ARB/USDT:USDT")},
        market_data={("binance", "ARB/USDT:USDT"): _md(funding=funding)},
        ohlcv={("binance", "ARB/USDT:USDT"): _candles(start, closes)},
        now=now,
    )


# ── Frozen-constant mirrors (the probe must match the audited screen) ─────
def test_frozen_constants_mirror_screen():
    import research.screen_preunlock_short as screen
    from core.agents import unlock_short_probe_agent as probe

    assert probe.RATIO_MIN == screen.RATIO_MIN == 0.10
    assert probe.STAKE_FRAC == screen.STAKE_FRAC == 0.03
    assert probe.MAX_CONCURRENT == screen.MAX_CONCURRENT == 4
    assert probe.AUC_RATIO_SCALE == screen.AUC_RATIO_SCALE == 0.20
    assert probe.AUC_FUNDING_W == screen.AUC_FUNDING_W == 10.0


def test_score_mirrors_screen_auc_score():
    """The frozen pre-outcome discriminating score (08b addendum §6) must be
    byte-identical to the screen's. Never re-tune it after outcomes accrue
    (audit F4) — a new score requires a new pre-registration."""
    from core.agents.unlock_short_probe_agent import unlock_short_score
    from research.screen_preunlock_short import auc_score

    for ratio, funding in [(0.10, 0.0001), (0.12, -0.0005), (0.35, 0.001), (1.0, 0.0)]:
        assert unlock_short_score(ratio, funding) == pytest.approx(
            auc_score(ratio, funding), abs=1e-12)


def test_w3_arm_not_implemented():
    """W3 (T -> T+3d post-unlock) failed its AUC gate (0.591 < 0.60) and must
    NOT be probed (binding condition 4)."""
    from core.agents.unlock_short_probe_agent import ARMS

    assert set(ARMS) == {"W1", "W2"}
    assert ARMS["W1"] == 28 * DAY
    assert ARMS["W2"] == 14 * DAY


def test_entry_targets_match_screen_window_bounds():
    from core.agents.unlock_short_probe_agent import arm_entry_target
    from research.screen_preunlock_short import window_bounds

    for arm, w in (("W1", "W1"), ("W2", "W2")):
        assert arm_entry_target(T_UNLOCK, arm) == window_bounds(T_UNLOCK, w)[0]


# ── Signal lifecycle ──────────────────────────────────────────────────────
def test_no_signal_before_entry_window(wh, tmp_path):
    now = W1_TARGET - 2 * HOUR
    p = _default_providers(now)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0
    assert wh.query("SELECT COUNT(*) n FROM shadow_unlock_probe")[0]["n"] == 0


def test_w1_enter_writes_raw_and_sl8_decision_rows(wh, tmp_path):
    now = W1_TARGET + 300
    p = _default_providers(now)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    stats = probe.tick()
    assert stats["entered"] == 1

    rows = wh.query("SELECT * FROM shadow_decisions ORDER BY proposal_id")
    assert len(rows) == 2  # raw hold-to-T + the 8%-SL-Guardian counterfactual
    raw = [r for r in rows if not r["proposal_id"].endswith("-sl8")][0]
    sl8 = [r for r in rows if r["proposal_id"].endswith("-sl8")][0]
    assert sl8["proposal_id"] == raw["proposal_id"] + "-sl8"

    for r in (raw, sl8):
        assert r["side"] == "sell"                    # it is a SHORT
        assert r["label_status"] == "PENDING"
        assert r["entry_px"] and r["entry_px"] > 0
        assert r["tp_px"] == 0                        # no target: exit at unlock T
        assert r["venue"] == "binance"
        assert r["timeframe"] == "1h"
        # horizon = bars from entry to the SNAPSHOTTED unlock T (calendar edits
        # after entry can never move the exit)
        assert r["horizon_bars"] == (T_UNLOCK - int(r["ts"])) // HOUR
    assert raw["sl_px"] == 0                          # raw arm: naked hold to T
    assert sl8["sl_px"] == pytest.approx(raw["entry_px"] * 1.08)  # charter §2
    assert raw["model_version"] == "unlock_short_w1_v1"
    assert sl8["model_version"] == "unlock_short_w1_sl8_v1"


def test_w2_enters_separately_at_t_minus_14(wh, tmp_path):
    # W1 window first
    p = _default_providers(W1_TARGET + 300)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()
    # advance to the W2 window; refresh the candle feed around the new now
    p.now = W2_TARGET + 300
    p._ohlcv[("binance", "ARB/USDT:USDT")] = _candles(p.now - 48 * HOUR, [95.0] * 72)
    stats = probe.tick()
    assert stats["entered"] == 1

    mvs = {r["model_version"] for r in wh.query("SELECT model_version FROM shadow_decisions")}
    assert mvs == {"unlock_short_w1_v1", "unlock_short_w1_sl8_v1",
                   "unlock_short_w2_v1", "unlock_short_w2_sl8_v1"}
    arms = {r["arm"] for r in wh.query(
        "SELECT arm FROM shadow_unlock_probe WHERE decision='ENTER'")}
    assert arms == {"W1", "W2"}


def test_probe_row_snapshot_carries_binding_condition_2_fields(wh, tmp_path):
    now = W1_TARGET + 300
    p = _default_providers(now, funding=0.0007)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()

    r = wh.query("SELECT * FROM shadow_unlock_probe WHERE decision='ENTER'")[0]
    assert r["base"] == "ARB"
    assert r["symbol"] == "ARB/USDT:USDT"
    assert r["venue"] == "binance"
    assert r["arm"] == "W1"
    assert r["unlock_ts"] == T_UNLOCK                 # as-of-signal snapshot (C5)
    assert r["unlock_ratio"] == pytest.approx(0.12)
    assert r["event_tokens"] == pytest.approx(50_000_000.0)
    assert "fetched 2026-07-11T12:00:00+00:00" in r["calendar_source"]
    assert r["signal_ts"] == now
    assert r["entry_ts"] and r["entry_px"] > 0
    assert r["funding_entry"] == pytest.approx(0.0007)
    # frozen score AT ENTRY: tanh(0.12/0.20) + 10*0.0007
    from research.screen_preunlock_short import auc_score
    assert r["score"] == pytest.approx(auc_score(0.12, 0.0007))
    # capital-scaling convention: 3% of ACCOUNT equity, UNLEVERED
    assert r["stake_frac"] == pytest.approx(0.03)
    assert r["notional_usd"] == pytest.approx(0.03 * 420.0)


def test_calendar_edit_after_signal_never_rewrites_logged_row(wh, tmp_path):
    """Binding condition 5: the signal is frozen at signal time. A refreshed
    calendar with an edited ratio must neither rewrite the logged W1 row nor
    re-signal the same (event, arm)."""
    now = W1_TARGET + 300
    p = _default_providers(now)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()

    p.docs = [_doc(events=[_event(ratio=0.55)], fetched="2026-08-01T00:00:00+00:00")]
    probe.tick()  # same tick window, edited calendar
    rows = wh.query("SELECT * FROM shadow_unlock_probe WHERE arm='W1'")
    assert len(rows) == 1
    assert rows[0]["unlock_ratio"] == pytest.approx(0.12)          # original snapshot
    assert "2026-07-11" in rows[0]["calendar_source"]              # original fetch time
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 2


def test_ratio_below_threshold_ignored(wh, tmp_path):
    now = W1_TARGET + 300
    p = _default_providers(now)
    p.docs = [_doc(events=[_event(ratio=0.08)])]
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_unlock_probe")[0]["n"] == 0


def test_late_discovery_is_skip_late_never_a_retro_entry(wh, tmp_path):
    """An entry window discovered >12h late is SKIP_LATE — the probe must never
    backfill an entry price from the past (that would be a retro-signal)."""
    now = W1_TARGET + 13 * HOUR
    p = _default_providers(now)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()
    rows = wh.query("SELECT * FROM shadow_unlock_probe")
    assert len(rows) == 1
    assert rows[0]["decision"] == "SKIP_LATE"
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_no_perp_and_no_funding_are_skipped_and_counted(wh, tmp_path):
    now = W1_TARGET + 300
    start = now - 48 * HOUR
    p = _Providers(
        docs=[_doc(symbol="NOPERP"), _doc(symbol="NOFUND")],
        perps={"NOFUND": ("binance", "NOFUND/USDT:USDT")},
        market_data={("binance", "NOFUND/USDT:USDT"):
                     {"bid": 1.0, "ask": 1.01, "active": True}},  # funding missing
        ohlcv={("binance", "NOFUND/USDT:USDT"): _candles(start, [1.0] * 72)},
        now=now,
    )
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()
    by_base = {r["base"]: r["decision"] for r in wh.query(
        "SELECT base, decision FROM shadow_unlock_probe")}
    assert by_base == {"NOPERP": "SKIP_NO_PERP", "NOFUND": "SKIP_NO_FUNDING"}
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 0


def test_concurrency_cap_four_per_arm_chronological(wh, tmp_path):
    now = W1_TARGET + 300
    start = now - 48 * HOUR
    docs, perps, md, ohlcv = [], {}, {}, {}
    for i in range(5):  # 5 same-day unlock events -> same W1 entry target
        base = f"TOK{i}"
        sym = f"{base}/USDT:USDT"
        docs.append(_doc(symbol=base, events=[_event(ts=T_UNLOCK + i * HOUR)]))
        perps[base] = ("binance", sym)
        md[("binance", sym)] = _md()
        ohlcv[("binance", sym)] = _candles(start, [100.0] * 72)
    p = _Providers(docs=docs, perps=perps, market_data=md, ohlcv=ohlcv, now=now)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()

    enters = wh.query("SELECT COUNT(*) n FROM shadow_unlock_probe "
                      "WHERE arm='W1' AND decision='ENTER'")[0]["n"]
    caps = wh.query("SELECT COUNT(*) n FROM shadow_unlock_probe "
                    "WHERE arm='W1' AND decision='SKIP_CAP'")[0]["n"]
    assert enters == 4          # 12% / 3% = 4 concurrent max (per arm)
    assert caps == 1            # the chronological latecomer is skipped + counted


# ── Monitoring: MTM path, SL-Guardian counterfactual, funding ─────────────
def _entered_probe(wh, tmp_path, *, funding=0.0004):
    now = W2_TARGET + 300
    p = _default_providers(now, funding=funding)
    probe = _make_probe(wh, p, tmp_path / "s.json")
    probe.tick()  # W2 ENTER (W1 target is >12h past -> SKIP_LATE, no position)
    assert wh.query("SELECT COUNT(*) n FROM shadow_unlock_probe "
                    "WHERE decision='ENTER'")[0]["n"] == 1
    return probe, p


def test_intra_hold_mtm_path_logged_per_bar(wh, tmp_path):
    probe, p = _entered_probe(wh, tmp_path)
    entry = wh.query("SELECT entry_ts, entry_px FROM shadow_unlock_probe "
                     "WHERE decision='ENTER'")[0]
    # price decays after entry (short in profit)
    closes = [100.0 - i * 0.5 for i in range(30)]
    p._ohlcv[("binance", "ARB/USDT:USDT")] = _candles(int(entry["entry_ts"]), closes)
    p.now = int(entry["entry_ts"]) + 20 * HOUR
    probe.tick()

    mtm = wh.query("SELECT * FROM shadow_unlock_mtm ORDER BY bar_ts")
    assert len(mtm) >= 10
    assert all(m["mark_px"] > 0 for m in mtm)
    assert mtm[-1]["unrealized_short_ret"] > 0   # decay = short in profit
    snaps = wh.query("SELECT * FROM shadow_unlock_concurrent WHERE arm='W2'")
    assert len(snaps) >= 1
    assert all(s["max_drawdown"] is not None for s in snaps)


def test_sl_guardian_counterfactual_flags_first_8pct_adverse_bar(wh, tmp_path):
    probe, p = _entered_probe(wh, tmp_path)
    entry = wh.query("SELECT entry_ts, entry_px FROM shadow_unlock_probe "
                     "WHERE decision='ENTER'")[0]
    e_ts, e_px = int(entry["entry_ts"]), float(entry["entry_px"])
    # pump against the short: bar 5 is the first whose HIGH breaches entry*1.08
    closes = [e_px] * 5 + [e_px * 1.07] + [e_px * 1.15] * 10
    highs = [c * 1.001 for c in closes]
    highs[5] = e_px * 1.09          # first Guardian breach (wick)
    p._ohlcv[("binance", "ARB/USDT:USDT")] = _candles(e_ts, closes, highs=highs)
    p.now = e_ts + 16 * HOUR
    probe.tick()

    r = wh.query("SELECT * FROM shadow_unlock_probe WHERE decision='ENTER'")[0]
    assert r["sl_cf_hit_ts"] == e_ts + 5 * HOUR
    assert r["sl_cf_hit_px"] == pytest.approx(e_px * 1.08)
    # flag is set once and never moves on later (worse) bars
    p.now = e_ts + 17 * HOUR
    probe.tick()
    r2 = wh.query("SELECT * FROM shadow_unlock_probe WHERE decision='ENTER'")[0]
    assert r2["sl_cf_hit_ts"] == e_ts + 5 * HOUR


def test_funding_accrues_and_sl_counterfactual_sum_freezes_at_flag(wh, tmp_path):
    probe, p = _entered_probe(wh, tmp_path, funding=0.001)
    entry = wh.query("SELECT entry_ts, entry_px FROM shadow_unlock_probe "
                     "WHERE decision='ENTER'")[0]
    e_ts, e_px = int(entry["entry_ts"]), float(entry["entry_px"])
    flat = _candles(e_ts, [e_px] * 60)
    p._ohlcv[("binance", "ARB/USDT:USDT")] = flat

    p.now = e_ts + 9 * HOUR       # next 8h settlement bucket
    probe.tick()
    r = wh.query("SELECT * FROM shadow_unlock_probe WHERE decision='ENTER'")[0]
    # before the Guardian flag the two sums accrue in PARALLEL (one print per
    # 8h bucket, entry tick included — the listing-probe convention)
    assert r["realized_funding_rate_sum"] > 0
    assert r["sl_cf_funding_rate_sum"] == pytest.approx(r["realized_funding_rate_sum"])

    # Guardian breach in the next window -> sl counterfactual funding FREEZES
    closes = [e_px] * 12 + [e_px * 1.10] * 40
    p._ohlcv[("binance", "ARB/USDT:USDT")] = _candles(e_ts, closes)
    p.now = e_ts + 18 * HOUR      # second bucket + breach now visible
    probe.tick()
    p.now = e_ts + 27 * HOUR      # third bucket: only the raw hold keeps accruing
    probe.tick()
    r = wh.query("SELECT * FROM shadow_unlock_probe WHERE decision='ENTER'")[0]
    assert r["sl_cf_hit_ts"] is not None
    assert r["realized_funding_rate_sum"] > r["sl_cf_funding_rate_sum"]


# ── Resolver integration (raw + sl8 funding served per proposal) ──────────
def test_resolver_funding_provider_serves_unlock_rows(wh):
    spec = importlib.util.spec_from_file_location(
        "resolve_shadow_outcomes_unlock_test",
        ROOT / "scripts" / "resolve_shadow_outcomes.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    wh._conn().executescript(
        """CREATE TABLE IF NOT EXISTS shadow_unlock_probe (
            proposal_id TEXT PRIMARY KEY, decision TEXT,
            realized_funding_rate_sum REAL, sl_cf_funding_rate_sum REAL);"""
    )
    wh._conn().execute(
        "INSERT INTO shadow_unlock_probe (proposal_id, decision, "
        "realized_funding_rate_sum, sl_cf_funding_rate_sum) "
        "VALUES ('us-abc', 'ENTER', 0.0090, 0.0030)")
    wh._conn().commit()

    provider = mod._build_probe_funding_provider(wh)
    assert provider({"proposal_id": "us-abc"}) == pytest.approx(0.0090)
    # the sl8 counterfactual row reads the SL-frozen sum, not the full hold
    assert provider({"proposal_id": "us-abc-sl8"}) == pytest.approx(0.0030)
    assert provider({"proposal_id": "missing"}) == 0.0


# ── Charter / structural boundaries ───────────────────────────────────────
def test_structural_log_only_no_order_path():
    """The probe module must contain no reference to any order/write path."""
    src = (ROOT / "core" / "agents" / "unlock_short_probe_agent.py").read_text(
        encoding="utf-8")
    for forbidden in ("order_manager", "OrderManager", "create_order",
                      "open_position", "place_order", "cancel_order",
                      "mcp_brain", "risk_manager"):
        assert forbidden not in src, f"probe references a live path: {forbidden}"


def test_state_survives_restart_no_duplicate_signals(wh, tmp_path):
    state = tmp_path / "s.json"
    now = W1_TARGET + 300
    p = _default_providers(now)
    probe = _make_probe(wh, p, state)
    probe.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 2

    probe2 = _make_probe(wh, p, state)     # simulated restart
    probe2.tick()
    assert wh.query("SELECT COUNT(*) n FROM shadow_decisions")[0]["n"] == 2
    st = json.loads(state.read_text())
    assert len(st["signaled"]) == 1


def test_companion_tables_exist(wh, tmp_path):
    p = _default_providers(0)
    _make_probe(wh, p, tmp_path / "s.json")
    tables = {r["name"] for r in wh.query(
        "SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"shadow_unlock_probe", "shadow_unlock_mtm",
            "shadow_unlock_concurrent"} <= tables


def test_tick_never_raises_on_broken_providers(wh, tmp_path):
    from core.agents.unlock_short_probe_agent import UnlockShortProbeAgent

    def boom(*a, **k):
        raise RuntimeError("venue down")

    probe = UnlockShortProbeAgent(
        warehouse=wh, calendar_provider=boom, perp_resolver=boom,
        market_data_provider=boom, ohlcv_provider=boom,
        account_balance_provider=boom, now_fn=lambda: 0.0,
        state_path=tmp_path / "s.json",
    )
    stats = probe.tick()   # must fail-open, never raise into the shadow lane
    assert isinstance(stats, dict)
