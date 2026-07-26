from __future__ import annotations

import json
import inspect
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.entry_policy import (
    EntryPolicy,
    LiveApproval,
    authorize_runtime_entry,
    evaluate_entry,
    load_live_approval,
    mode_profile_for,
    parse_allowlist,
    strategy_id_for_action,
)


def test_shadow_only_denies_new_orders_but_allows_reductions():
    denied = evaluate_entry(
        policy=EntryPolicy.SHADOW_ONLY,
        operating_mode="PAPER",
        strategy_id="F1",
    )
    assert denied.allowed is False
    assert denied.reason == "entry_policy_shadow_only"

    reducing = evaluate_entry(
        policy=EntryPolicy.PROTECT_ONLY,
        operating_mode="OBSERVATION",
        strategy_id="emergency_close",
        is_reducing=True,
    )
    assert reducing.allowed is True
    assert reducing.reason == "reduce_only_always_allowed"


def test_approved_paper_requires_paper_mode_and_exact_allowlist():
    allowed = evaluate_entry(
        policy=EntryPolicy.APPROVED_PAPER,
        operating_mode="PAPER",
        strategy_id="F1",
        approved_paper={"F1"},
    )
    assert allowed.allowed is True

    assert evaluate_entry(
        policy=EntryPolicy.APPROVED_PAPER,
        operating_mode="PAPER",
        strategy_id="D1",
        approved_paper={"F1"},
    ).reason == "strategy_not_approved_for_paper"

    assert evaluate_entry(
        policy=EntryPolicy.APPROVED_PAPER,
        operating_mode="CONTROLLED_LIVE",
        strategy_id="F1",
        approved_paper={"F1"},
    ).reason == "approved_paper_requires_paper_mode"


def test_controlled_live_requires_latch_allowlist_version_sha_and_expiry():
    now = datetime(2026, 7, 13, tzinfo=timezone.utc)
    approval = LiveApproval(
        strategy_id="F1",
        strategy_version="f1-v3",
        git_sha="abc123",
        expires_at=now + timedelta(hours=1),
        approved_by="operator",
    )
    base = dict(
        policy=EntryPolicy.CONTROLLED_LIVE,
        operating_mode="CONTROLLED_LIVE",
        strategy_id="F1",
        strategy_version="f1-v3",
        approved_live={"F1"},
        live_latch_enabled=True,
        live_approval=approval,
        current_git_sha="abc123",
        now=now,
    )
    assert evaluate_entry(**base).allowed is True

    assert evaluate_entry(**{**base, "live_latch_enabled": False}).reason == "live_latch_missing"
    assert evaluate_entry(**{**base, "current_git_sha": "different"}).reason == "live_git_sha_mismatch"
    assert evaluate_entry(**{**base, "strategy_version": "f1-v4"}).reason == "live_strategy_version_mismatch"
    assert evaluate_entry(**{**base, "now": now + timedelta(hours=2)}).reason == "live_approval_expired"


def test_live_approval_loader_fails_closed_on_invalid_payload(tmp_path):
    p = tmp_path / "approval.json"
    p.write_text("{}", encoding="utf-8")
    assert load_live_approval(p) is None

    expires = datetime.now(timezone.utc) + timedelta(hours=1)
    p.write_text(json.dumps({
        "strategy_id": "F1",
        "strategy_version": "f1-v3",
        "git_sha": "abc123",
        "expires_at": expires.isoformat(),
        "approved_by": "operator",
    }), encoding="utf-8")
    loaded = load_live_approval(p)
    assert loaded is not None
    assert loaded.strategy_id == "F1"
    assert loaded.expires_at == expires


def test_mode_profiles_pin_directional_paper_and_live_canary_limits():
    paper = mode_profile_for("PAPER")
    assert paper.max_open_positions == 3
    assert paper.risk_per_trade_pct == pytest.approx(0.0025)
    assert paper.aggregate_open_risk_pct == pytest.approx(0.0075)
    assert paper.gross_exposure_pct == pytest.approx(0.10)
    assert paper.max_leverage == pytest.approx(1.5)

    aggressive = mode_profile_for("PAPER", "aggressive-research")
    assert aggressive.max_open_positions == 6
    assert aggressive.risk_per_trade_pct == pytest.approx(0.00125)
    assert aggressive.aggregate_open_risk_pct == paper.aggregate_open_risk_pct
    assert aggressive.gross_exposure_pct == paper.gross_exposure_pct
    assert aggressive.max_leverage == paper.max_leverage

    with pytest.raises(ValueError):
        mode_profile_for("CONTROLLED_LIVE", "aggressive-research")

    live = mode_profile_for("CONTROLLED_LIVE")
    assert live.max_open_positions == 1
    assert live.risk_per_trade_pct == pytest.approx(0.001)
    assert live.max_leverage == pytest.approx(1.0)


def test_runtime_config_uses_shadow_policy_empty_allowlist_and_typed_profile():
    # Validate source defaults in an isolated interpreter.  The operator's
    # real .env intentionally enables approved PAPER strategies and must not
    # make this safety-default test environment-dependent.
    env = os.environ.copy()
    for key in (
        "OPERATING_MODE",
        "ENTRY_POLICY",
        "APPROVED_PAPER_STRATEGIES",
        "APPROVED_LIVE_STRATEGIES",
        "CONTROLLED_LIVE_ENABLED",
        "PAPER_TRADING_PROFILE",
        "PAPER_PROFILE_STARTED_AT",
        # Also clear knobs that override MODE_PROFILE-derived defaults when
        # present in the parent process environ (load_dotenv side-effect).
        "MAX_PORTFOLIO_EXPOSURE_PCT",
        "MAX_AGGREGATE_OPEN_RISK_PCT",
    ):
        env.pop(key, None)
    env["PYTHON_DOTENV_DISABLED"] = "1"
    root = Path(__file__).resolve().parents[1]
    code = """
import config
checks = [
    ('ENTRY_POLICY', config.ENTRY_POLICY, 'SHADOW_ONLY'),
    ('APPROVED_PAPER_STRATEGIES', config.APPROVED_PAPER_STRATEGIES, frozenset()),
    ('APPROVED_LIVE_STRATEGIES', config.APPROVED_LIVE_STRATEGIES, frozenset()),
    ('max_open_positions', config.RISK['max_open_positions'], 3),
    ('futures_max_leverage', config.RISK['futures_max_leverage'], 1.5),
    ('per_trade_risk_pct', config.VOL_TARGET_SIZING['per_trade_risk_pct'], 0.0025),
    ('MAX_PORTFOLIO_EXPOSURE_PCT', config.MAX_PORTFOLIO_EXPOSURE_PCT, 10.0),
    ('MAX_AGGREGATE_OPEN_RISK_PCT', config.MAX_AGGREGATE_OPEN_RISK_PCT, 0.0075),
]
for name, got, want in checks:
    assert got == want, f'{name}: got {got!r} want {want!r}'
"""
    completed = subprocess.run(
        [sys.executable, "-c", code],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr or completed.stdout


def test_aggressive_profile_changes_only_paper_sampling_envelope():
    env = os.environ.copy()
    env.update({
        "PYTHON_DOTENV_DISABLED": "1",
        "OPERATING_MODE": "PAPER",
        "PAPER_TRADING_PROFILE": "AGGRESSIVE_RESEARCH",
    })
    for key in (
        "ENTRY_POLICY", "APPROVED_PAPER_STRATEGIES", "APPROVED_LIVE_STRATEGIES",
        "CONTROLLED_LIVE_ENABLED", "SHADOW_TICK_INTERVAL_S", "SHADOW_MOVER_CAP",
        "SHADOW_MOVER_REFRESH_S", "BROAD_UNIVERSE_PER_DIRECTION",
        "BROAD_UNIVERSE_SHORTLIST_CAP",
    ):
        env.pop(key, None)
    code = """
import config
assert config.PAPER_TRADING_PROFILE == 'AGGRESSIVE_RESEARCH'
assert config.RISK['max_open_positions'] == 6
assert config.VOL_TARGET_SIZING['per_trade_risk_pct'] == 0.00125
assert config.MAX_AGGREGATE_OPEN_RISK_PCT == 0.0075
assert config.MAX_PORTFOLIO_EXPOSURE_PCT == 10.0
assert config.RISK['futures_max_leverage'] == 1.5
assert config.SHADOW_MODE['tick_interval_s'] == 60
assert config.SHADOW_MODE['mover_cap'] == 30
assert config.BROAD_UNIVERSE_MONITOR['shortlist_cap'] == 36
"""
    completed = subprocess.run(
        [sys.executable, "-c", code], cwd=Path(__file__).resolve().parents[1],
        env=env, capture_output=True, text=True, check=False,
    )
    assert completed.returncode == 0, completed.stderr


def test_allowlist_parser_is_exact_deduplicated_and_blank_safe():
    assert parse_allowlist("") == frozenset()
    assert parse_allowlist(" F1, D1,F1 ") == frozenset({"F1", "D1"})


def test_mcp_is_only_a_deterministic_registry_compatibility_alias():
    assert strategy_id_for_action({}, "mcp") == "mcp_registry"
    assert strategy_id_for_action({}, "mcp_det") == "mcp_registry"
    assert strategy_id_for_action({"strategy": "F1"}, "mcp") == "F1"

    from core.bot_engine import BotEngine

    source = inspect.getsource(BotEngine._claude_portfolio_cycle)
    assert 'SIGNAL_SOURCE in ("mcp", "mcp_det")' in source
    assert "last_fund_ops" not in source


def test_production_order_manager_rechecks_policy_at_final_open_boundary(monkeypatch):
    from core.entry_policy import EntryAuthorization
    from core.order_manager import OrderManager

    monkeypatch.setattr(
        "core.entry_policy.authorize_runtime_entry",
        lambda *_args, **_kwargs: EntryAuthorization(
            False, "entry_policy_shadow_only", "SHADOW_ONLY", "F1"
        ),
    )
    manager = OrderManager.__new__(OrderManager)
    manager.enforce_entry_policy = True
    result = manager.open_position(
        None, "BTC/USDT:USDT", "buy", "futures", "F1", 1.0, 90.0, 120.0
    )
    assert result is None
    assert manager.last_open_reject == "entry_policy_shadow_only"


def test_order_manager_rechecks_the_upstream_authorized_identity(monkeypatch):
    from core.entry_policy import EntryAuthorization
    from core.order_manager import OrderManager

    seen = []

    def deny(strategy_id, **_kwargs):
        seen.append(strategy_id)
        return EntryAuthorization(False, "test_stop", "APPROVED_PAPER", strategy_id)

    monkeypatch.setattr("core.entry_policy.authorize_runtime_entry", deny)
    manager = OrderManager.__new__(OrderManager)
    manager.enforce_entry_policy = True
    result = manager.open_position(
        None, "BTC/USDT:USDT", "buy", "futures", "claude", 1.0, 90.0, 120.0,
        authorization_strategy_id="mcp_registry",
    )
    assert result is None
    assert seen == ["mcp_registry"]


def test_all_known_entry_lanes_reference_the_shared_policy():
    root = Path(__file__).resolve().parents[1]
    paths = (
        "core/bot_engine.py",
        "core/order_manager.py",
        "core/deep_breakout_lane.py",
        "core/arbitrage_engine.py",
        "core/direct_executor.py",
        "core/capital_allocator.py",
        "strategies/dca_strategy.py",
        "strategies/rebalancing.py",
    )
    for relative in paths:
        source = (root / relative).read_text(encoding="utf-8")
        assert "authorize_runtime_entry" in source, relative


def test_unknown_policy_and_missing_strategy_fail_closed():
    assert evaluate_entry(
        policy="not-a-policy",
        operating_mode="PAPER",
        strategy_id="F1",
    ).reason == "invalid_entry_policy"
    assert evaluate_entry(
        policy=EntryPolicy.APPROVED_PAPER,
        operating_mode="PAPER",
        strategy_id="",
        approved_paper={"F1"},
    ).reason == "strategy_id_missing"


def test_runtime_catalog_prevents_shadow_or_unknown_allowlist_bypass(monkeypatch):
    import config

    monkeypatch.setattr(config, "ENTRY_POLICY", "APPROVED_PAPER")
    monkeypatch.setattr(config, "OPERATING_MODE", "PAPER")
    monkeypatch.setattr(
        config,
        "APPROVED_PAPER_STRATEGIES",
        frozenset({"F1", "F2_XVENUE_FUNDING_SPREAD", "future_plugin"}),
    )

    assert authorize_runtime_entry("F1", strategy_version="correctness-v1").allowed
    f2 = authorize_runtime_entry(
        "F2_XVENUE_FUNDING_SPREAD", strategy_version="shadow-v1"
    )
    assert f2.allowed is False and "shadow_only" in f2.reason
    unknown = authorize_runtime_entry("future_plugin", strategy_version="v1")
    assert unknown.allowed is False
    assert unknown.reason == "strategy_not_in_frozen_program"
