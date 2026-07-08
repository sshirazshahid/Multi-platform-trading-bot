"""Decision Provenance Bundle — Tranche A (mode-agnostic core).

Test specs 1-8 from shiraz-main-test-plan-20260612-082500.md plus the
rotation (archive-rename) and warm-restart advice-drop behaviors pulled
into this implementation round.

Conventions:
- NEVER touches data/ — every file path is tmp_path + monkeypatch.
- Warehouse tests load core/warehouse.py as an isolated module copy so the
  class-level thread-local connection cache never leaks across tests
  (pattern from tests/test_warehouse.py).
- Engine/brain objects built via object.__new__ to bypass heavy __init__
  (pattern from tests/test_paper_no_live_writes.py).
- decision_id is uuid4: assert FORMAT not value (no flakiness).
"""

from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import re
import sys
import time
import uuid as uuid_mod
from pathlib import Path
from unittest.mock import MagicMock

import pytest

ROOT = Path(__file__).resolve().parents[1]

import core.mcp_brain as mb  # noqa: E402
from core.bot_engine import BotEngine  # noqa: E402
from core.mcp_brain import MCPBrain  # noqa: E402


def _load_warehouse_module():
    """Load core/warehouse.py as an isolated copy (fresh class-level _tls)."""
    spec = importlib.util.spec_from_file_location(
        f"warehouse_prov_copy_{uuid_mod.uuid4().hex[:8]}",
        ROOT / "core" / "warehouse.py",
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _assert_uuid4(value: str):
    u = uuid_mod.UUID(value)
    assert u.version == 4


def _make_brain_for_parse(monkeypatch, tmp_path, claude_result, meta_fill=None):
    """MCPBrain stub good enough to run _ask_claude_portfolio's parse loop."""
    brain = object.__new__(MCPBrain)
    brain._accuracy = MagicMock()
    brain._accuracy.stats.return_value = {}
    brain._last_decisions = {}
    brain._recent_tape_summary = lambda coins: []

    def _fake_call(prompt, system_prompt, call_type="portfolio", meta_out=None):
        if meta_out is not None and meta_fill:
            meta_out.update(meta_fill)
        return claude_result

    brain._call_claude = _fake_call
    monkeypatch.setattr(mb, "DECISION_LOG", tmp_path / "mcp_decisions.jsonl")
    return brain


def _open_action(symbol="BTC/USDT", **over):
    a = {
        "type": "OPEN",
        "symbol": symbol,
        "exchange": "binance",
        "market_type": "futures",
        "side": "buy",
        "leverage": 3,
        "size_pct": 5.0,
        "sl_pct": 1.5,
        "tp_pct": 2.0,
        "confidence": 0.7,
        "reason": "test",
    }
    a.update(over)
    return a


# ── Spec 1: decision_id minted per ACTION; uuid4 format; one sha per response ──


def test_decision_id_per_action_uuid4_and_shared_response_sha(monkeypatch, tmp_path):
    raw_text = '{"actions": [...synthetic raw...]}'
    result = {"actions": [_open_action("BTC/USDT"), _open_action("ETH/USDT", side="sell")]}
    brain = _make_brain_for_parse(
        monkeypatch, tmp_path, result, meta_fill={"raw": raw_text, "attempt": 2, "repaired": False}
    )

    actions = brain._ask_claude_portfolio(["BTC", "ETH"], {}, {}, [], {}, {}, [])

    assert len(actions) == 2
    ids = {a["decision_id"] for a in actions}
    assert len(ids) == 2  # one fresh id PER action
    for a in actions:
        _assert_uuid4(a["decision_id"])
    expected_sha = hashlib.sha256(raw_text.encode("utf-8")).hexdigest()
    assert {a["response_sha256"] for a in actions} == {expected_sha}


# ── Spec 2: source tagging (claude + algo); fresh ids, no reuse ──


def test_claude_actions_tagged_source_claude_with_meta(monkeypatch, tmp_path):
    result = {"actions": [_open_action()]}
    brain = _make_brain_for_parse(
        monkeypatch, tmp_path, result, meta_fill={"raw": "RAW", "attempt": 2, "repaired": True}
    )

    actions = brain._ask_claude_portfolio(["BTC"], {}, {}, [], {}, {}, [])

    a = actions[0]
    assert a["source"] == "claude"
    assert a["attempt"] == 2
    assert a["repaired"] is True


def test_algo_monitor_advice_fresh_ids_source_algo():
    brain = object.__new__(MCPBrain)
    positions = [
        {
            "id": "pos-abcdef12345",
            "symbol": "BTC/USDT",
            "side": "buy",
            "pnl_pct": 0.5,
            "age_min": 10.0,
        }
    ]

    advice1 = brain._algorithmic_position_monitor(positions, {}, {})
    advice2 = brain._algorithmic_position_monitor(positions, {}, {})

    a1, a2 = advice1["pos-abcdef12345"], advice2["pos-abcdef12345"]
    assert a1["source"] == "algo"
    _assert_uuid4(a1["decision_id"])
    _assert_uuid4(a2["decision_id"])
    assert a1["decision_id"] != a2["decision_id"]  # fresh every parse — no reuse


def test_algo_portfolio_builder_tags_provenance_source_pin():
    """Both actions.append sites in _algorithmic_portfolio mint id + algo tag."""
    src = inspect.getsource(MCPBrain._algorithmic_portfolio)
    assert src.count('"source": "algo"') >= 2
    assert src.count('"decision_id": str(uuid.uuid4())') >= 2


def test_analyze_portfolio_merge_backstop_source_pin():
    """The merge in analyze_portfolio backstops any action lacking an id."""
    src = inspect.getsource(MCPBrain.analyze_portfolio)
    assert "decision_id" in src and "uuid.uuid4()" in src


# ── Spec 3: pre-clamp capture + clamps + symbol_unlisted LOG-ONLY ──


def test_out_of_bounds_leverage_size_clamped_and_raw_recorded(monkeypatch, tmp_path):
    result = {
        "actions": [_open_action("DOGE/USDT", leverage=99, size_pct=250.0, sl_pct=9.9, tp_pct=0.4)]
    }
    brain = _make_brain_for_parse(
        monkeypatch, tmp_path, result, meta_fill={"raw": "RAW", "attempt": 1}
    )

    actions = brain._ask_claude_portfolio(["BTC", "ETH"], {}, {}, [], {}, {}, [])

    assert len(actions) == 1  # LOG-ONLY: unlisted symbol still flows
    a = actions[0]
    # pre-clamp raws
    assert a["leverage_raw"] == 99
    assert a["size_pct_raw"] == 250.0
    assert a["sl_pct_raw"] == 9.9
    assert a["tp_pct_raw"] == 0.4
    # parse-time clamps mirror config bounds
    import config

    max_lev = max(int(t.get("leverage", 1)) for t in config.LEVERAGE_TIERS.values())
    assert 1 <= a["leverage"] <= max_lev
    assert 0.0 < a["size_pct"] <= 100.0
    assert "leverage" in a["clamped"] and "size_pct" in a["clamped"]
    assert a["clamped"]["leverage"]["from"] == 99
    assert a["clamped"]["size_pct"]["from"] == 250.0
    # DOGE not in the analyzed coin set → flagged, NOT rejected
    assert a["symbol_unlisted"] is True


def test_in_bounds_action_not_marked_clamped_or_unlisted(monkeypatch, tmp_path):
    result = {"actions": [_open_action("BTC/USDT", leverage=2, size_pct=5.0)]}
    brain = _make_brain_for_parse(
        monkeypatch, tmp_path, result, meta_fill={"raw": "RAW", "attempt": 1}
    )

    actions = brain._ask_claude_portfolio(["BTC"], {}, {}, [], {}, {}, [])

    a = actions[0]
    assert a["leverage"] == 2
    assert a["size_pct"] == 5.0
    assert "clamped" not in a  # recorded only when changed
    assert a["symbol_unlisted"] is False


# ── Spec 4: rejection rows ──


def test_log_rejection_appends_typed_row(monkeypatch, tmp_path):
    log = tmp_path / "mcp_decisions.jsonl"
    monkeypatch.setattr(mb, "DECISION_LOG", log)
    brain = object.__new__(MCPBrain)

    brain.log_rejection("did-123", "BTC/USDT", "cycle_cap", "cycle_cap")

    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["type"] == "rejection"
    assert row["decision_id"] == "did-123"
    assert row["symbol"] == "BTC/USDT"
    assert row["reason"] == "cycle_cap"
    assert row["stage"] == "cycle_cap"
    assert "ts" in row


def test_engine_log_rejection_routes_to_brain(monkeypatch, tmp_path):
    log = tmp_path / "mcp_decisions.jsonl"
    monkeypatch.setattr(mb, "DECISION_LOG", log)
    eng = object.__new__(BotEngine)
    eng.mcp_brain = object.__new__(MCPBrain)

    eng._log_rejection(
        {"decision_id": "did-9", "symbol": "ETH/USDT"}, "balance_below_min", stage="execute_open"
    )

    row = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert row["decision_id"] == "did-9"
    assert row["reason"] == "balance_below_min"
    assert row["stage"] == "execute_open"


def test_engine_log_rejection_safe_when_brain_is_none():
    eng = object.__new__(BotEngine)
    eng.mcp_brain = None
    # Must be a silent no-op, never raise
    eng._log_rejection({"decision_id": "x", "symbol": "y"}, "r", stage="s")


def test_execute_open_stashes_invalid_action_reason():
    eng = object.__new__(BotEngine)
    action = {"type": "OPEN", "symbol": "", "exchange": "", "side": ""}

    assert eng._execute_open(action) is False
    assert action["reject_reason"] == "invalid_action_fields"


def test_execute_open_stashes_symbol_cooldown_reason():
    eng = object.__new__(BotEngine)
    eng._dust_skip_cooldown = {"BTC/USDT": time.time() + 600}
    action = _open_action("BTC/USDT")

    assert eng._execute_open(action) is False
    assert action["reject_reason"] == "symbol_cooldown_active"


def test_every_return_false_in_execute_open_preceded_by_reason_stash():
    """TD-3 (gate-approved): ALL exits in _execute_open stash a reason."""
    src = inspect.getsource(BotEngine._execute_open)
    lines = src.splitlines()
    misses = []
    for i, ln in enumerate(lines):
        if ln.strip() == "return False":
            j = i - 1
            while j >= 0 and not lines[j].strip():
                j -= 1
            if j < 0 or "reject_reason" not in lines[j]:
                misses.append(f"line {i}: {ln.strip()} (prev: {lines[j].strip()[:60]})")
    assert not misses, "return False without reject_reason stash:\n" + "\n".join(misses)


def test_cycle_cap_drops_logged_source_pin():
    """Capped tail (actions[MAX_ACTIONS_PER_CYCLE:]) is logged as cycle_cap."""
    src = Path(ROOT / "core" / "bot_engine.py").read_text(encoding="utf-8")
    assert "actions[MAX_ACTIONS_PER_CYCLE:]" in src
    assert '"cycle_cap"' in src


def test_execute_open_failure_logged_at_caller_source_pin():
    """_execute_open False → caller logs action.get('reject_reason','unspecified')."""
    src = Path(ROOT / "core" / "bot_engine.py").read_text(encoding="utf-8")
    assert 'action.get("reject_reason", "unspecified")' in src


# ── Spec 5: warehouse round-trip (open/close + NULL paths) ──


@pytest.fixture
def wh(tmp_path):
    mod = _load_warehouse_module()
    return mod.Warehouse(path=tmp_path / "t.sqlite")


def _open_kwargs(**over):
    kw = dict(
        exchange="binance",
        symbol="BTC/USDT",
        side="buy",
        ts_entry=1000.0,
        entry_px=70000.0,
        size=0.01,
        leverage=3,
        mode="PAPER",
    )
    kw.update(over)
    return kw


def test_warehouse_decision_id_open_close_roundtrip(wh):
    tid = wh.record_trade_open(**_open_kwargs(decision_id="open-id-1"))
    assert tid > 0
    row = wh.query("SELECT decision_id, exit_decision_id FROM trades WHERE id=?", (tid,))[0]
    assert row["decision_id"] == "open-id-1"
    assert row["exit_decision_id"] is None

    wh.record_trade_close(
        trade_id=tid,
        ts_exit=2000.0,
        exit_px=71000.0,
        realized_pnl=1.0,
        exit_reason="tp",
        exit_decision_id="close-id-2",
    )
    row = wh.query("SELECT decision_id, exit_decision_id, status FROM trades WHERE id=?", (tid,))[0]
    assert row["decision_id"] == "open-id-1"
    assert row["exit_decision_id"] == "close-id-2"
    assert row["status"] == "CLOSED"


def test_warehouse_null_decision_paths_still_insert(wh):
    """Deterministic exits / manual / ghost / reconciled rows pass no id."""
    tid = wh.record_trade_open(**_open_kwargs(strategy_family="reconciled_exchange"))
    assert tid > 0
    row = wh.query("SELECT decision_id FROM trades WHERE id=?", (tid,))[0]
    assert row["decision_id"] is None

    wh.record_trade_close(
        trade_id=tid, ts_exit=2000.0, exit_px=69000.0, realized_pnl=-1.0, exit_reason="stop_loss"
    )  # no exit_decision_id → NULL kept
    row = wh.query("SELECT exit_decision_id FROM trades WHERE id=?", (tid,))[0]
    assert row["exit_decision_id"] is None


def test_warehouse_candidates_decision_id_column_exists(wh):
    cid = wh.record_candidate(exchange="binance", symbol="BTC/USDT", decision="ALLOW", features={})
    assert cid > 0
    row = wh.query("SELECT decision_id FROM candidates WHERE id=?", (cid,))[0]
    assert row["decision_id"] is None  # column present, NULL by default


# ── Spec 6: idempotent migration ──


def test_migration_idempotent_old_and_new_schema(tmp_path):
    db = tmp_path / "mig.sqlite"
    _load_warehouse_module().Warehouse(path=db)  # fresh (old schema + ALTERs)
    _load_warehouse_module().Warehouse(path=db)  # re-open on migrated schema

    import sqlite3

    conn = sqlite3.connect(str(db))
    tcols = [r[1] for r in conn.execute("PRAGMA table_info(trades)")]
    ccols = [r[1] for r in conn.execute("PRAGMA table_info(candidates)")]
    conn.close()
    assert tcols.count("decision_id") == 1
    assert tcols.count("exit_decision_id") == 1
    assert ccols.count("decision_id") == 1


# ── Spec 7: spot-fallback retry forwards provenance kwargs ──


def test_spot_fallback_forwards_decision_and_provenance_kwargs():
    from core.order_manager import OrderManager

    om = object.__new__(OrderManager)
    om.dry_run = False
    om.mcp_brain = None
    om.tracker = MagicMock()
    om.tracker.count_open.return_value = 0
    om.blacklist = MagicMock()
    om.blacklist.is_blacklisted.return_value = False
    om.risk = MagicMock()
    om.risk.can_trade.return_value = True
    om.risk.validate_leverage.return_value = 2
    om.kelly = MagicMock()
    om.kelly.should_block_trade.return_value = (False, "")
    om._futures_disabled = set()
    om._oneway_mode = set()
    om._save_order_mode_state = MagicMock()  # NEVER touch data/
    om._check_price_band = MagicMock(return_value=True)
    om._generate_client_order_id = MagicMock(return_value="cid-1234")
    om.auto_transfer_for_trade = MagicMock()
    om.executor = MagicMock()
    om.executor.check_spread.return_value = {"ok": True, "spread_pct": 0.0001, "mid": 100.0}
    om.executor.should_use_twap.return_value = False
    om.executor.execute_limit_with_fallback.side_effect = Exception(
        "permission denied for this api-key"
    )
    om.notifier = MagicMock()
    om.last_open_reject = None

    ex = MagicMock()
    ex.name = "binance"
    ex.fetch_ticker.return_value = {"last": 100.0, "bid": 99.9, "ask": 100.1}
    ex.get_min_order_size.return_value = 0.0
    ex.round_quantity.side_effect = lambda s, q, market_type=None: q
    ex.set_leverage.return_value = 2

    om.open_position = MagicMock(return_value="SPOT_POS")  # intercept recursion
    from core.order_manager import OrderManager as OM

    result = OM.open_position(
        om,
        ex,
        "BTC/USDT:USDT",
        "buy",
        "futures",
        "claude_portfolio",
        size=1.0,
        sl=95.0,
        tp=110.0,
        leverage=2,
        price=100.0,
        candidate_id=7,
        mcp_score=88.0,
        model_version="mv1",
        decision_id="d-spotfb",
    )

    assert result == "SPOT_POS"
    om.open_position.assert_called_once_with(
        ex,
        "BTC/USDT",
        "buy",
        "spot",
        "claude_portfolio",
        1.0,
        95.0,
        110.0,
        leverage=1,
        candidate_id=7,
        mcp_score=88.0,
        model_version="mv1",
        decision_id="d-spotfb",
    )


def test_open_position_stashes_last_open_reject():
    from core.order_manager import OrderManager

    om = object.__new__(OrderManager)
    om.blacklist = MagicMock()
    om.blacklist.is_blacklisted.return_value = True
    om.last_open_reject = None
    ex = MagicMock()
    ex.name = "binance"

    from core.order_manager import OrderManager as OM

    result = OM.open_position(om, ex, "BTC/USDT", "buy", "futures", "s", size=1.0, sl=1.0, tp=2.0)

    assert result is None
    assert om.last_open_reject == "blacklisted"


# ── Spec 8: INSERT OR IGNORE collision visibility ──


def test_insert_or_ignore_collision_first_decision_id_survives(wh):
    t1 = wh.record_trade_open(**_open_kwargs(decision_id="first-writer"))
    t2 = wh.record_trade_open(**_open_kwargs(decision_id="second-writer"))

    assert t1 == t2  # same idempotency key → same row
    row = wh.query("SELECT decision_id FROM trades WHERE id=?", (t1,))[0]
    # INSERT OR IGNORE: the FIRST row wins; the second decision_id never
    # lands and is therefore visible to the reconciler as an orphan.
    assert row["decision_id"] == "first-writer"


# ── Rotation: archive-rename, never truncate ──


def test_decision_log_rotation_archives_instead_of_truncating(monkeypatch, tmp_path):
    log = tmp_path / "mcp_decisions.jsonl"
    monkeypatch.setattr(mb, "DECISION_LOG", log)
    brain = object.__new__(MCPBrain)

    pad_line = json.dumps({"ts": "t", "type": "entry", "decisions": {"pad": "x" * 1000}}) + "\n"
    with open(log, "w", encoding="utf-8") as f:
        for _ in range(2100):
            f.write(pad_line)
    assert log.stat().st_size > 2_000_000

    brain._log_decisions({"actions": []}, "portfolio")

    archives = list(tmp_path.glob("mcp_decisions.*.jsonl"))
    assert len(archives) == 1
    assert re.fullmatch(r"mcp_decisions\.\d{8}-\d{6}\.jsonl", archives[0].name)
    # history preserved: all 2100 padded rows + the row that tripped rotation
    n_archived = sum(1 for _ in open(archives[0], encoding="utf-8"))
    assert n_archived == 2101
    # active file kept at the SAME filename (health_watchdog tails it)
    assert log.exists()
    assert log.stat().st_size < 1000

    brain._log_decisions({"actions": []}, "portfolio")  # next write → active file
    assert len(log.read_text(encoding="utf-8").splitlines()) == 1


# ── Warm-restart: reloaded position_advice DISCARDED (declared change) ──


def test_warm_restart_discards_position_advice_keeps_timing(monkeypatch, tmp_path):
    state = tmp_path / "mcp_state.json"
    state.write_text(
        json.dumps(
            {
                "saved_at": time.time(),  # fresh (<10 min) — old code would restore
                "last_entry_run": 123.0,
                "last_position_run": 124.0,
                "api_calls_this_hour": 7,
                "hour_start": 125.0,
                "claude_consecutive_fails": 1,
                "claude_backoff_until": 0.0,
                "decisions": {"BTC": {"action": "BUY", "confidence": 0.7, "reason": "r"}},
                "position_advice": {
                    "pid1": {"action": "CLOSE", "confidence": 0.9, "decision_id": "stale-id"}
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(mb, "STATE_FILE", state)

    brain = object.__new__(MCPBrain)
    brain._last_decisions = {}
    brain._last_position_advice = {}
    brain._load_state()

    assert brain._last_position_advice == {}  # stale advice never consumed
    assert brain._last_decisions["BTC"]["action"] == "BUY"  # decisions kept
    assert brain._last_entry_run == 123.0  # timing/budget fields kept
    assert brain._api_calls_this_hour == 7
