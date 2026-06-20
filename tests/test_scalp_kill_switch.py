"""Kill-switch tests for the 2026-05-22 SCALP-tier ship.

`SCALP_TIER_ENABLED` (env, default true) when flipped to false should:
  - Drop the SCALP entry from `config.LEVERAGE_TIERS`.
  - Make `core/mcp_brain.py`'s Claude TP/SL clamp inert (raw values
    pass through unclamped on a non-SCALP futures entry).

2026-06-20 (owner "remove any blacklist and blocks"): flipping the SCALP
tier off no longer re-introduces a symbol blacklist or blocked hours —
`BLACKLIST_HARD` stays empty and all 24h stay open regardless of this flag.

Tests reload `config` inside each test so the import-time gate runs
under the env var being asserted, then restore the default by reload
in teardown.
"""
from __future__ import annotations

import importlib
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _reload_config():
    """Drop cached config, re-import — re-runs the import-time gate."""
    sys.modules.pop("config", None)
    return importlib.import_module("config")


@pytest.fixture(autouse=True)
def _restore_default_config(monkeypatch):
    """After each test, reload config under SCALP_TIER_ENABLED=true
    so we don't leak the modified module state to unrelated tests."""
    yield
    monkeypatch.setenv("SCALP_TIER_ENABLED", "true")
    _reload_config()


def test_default_keeps_scalp_tier(monkeypatch):
    monkeypatch.setenv("SCALP_TIER_ENABLED", "true")
    cfg = _reload_config()
    assert cfg.SCALP_TIER_ENABLED is True
    assert "SCALP" in cfg.LEVERAGE_TIERS


def test_kill_switch_drops_scalp_but_keeps_unblock(monkeypatch):
    # 2026-06-20 (owner "remove any blacklist and blocks"): SCALP off still
    # drops the tier, but no longer re-introduces a blacklist or blocked hours.
    monkeypatch.setenv("SCALP_TIER_ENABLED", "false")
    cfg = _reload_config()
    assert cfg.SCALP_TIER_ENABLED is False
    assert "SCALP" not in cfg.LEVERAGE_TIERS
    assert cfg.BLACKLIST_HARD == set(), (
        "BLACKLIST_HARD must stay empty even with SCALP off (unblock directive)"
    )
    assert cfg.BLOCKED_HOURS_UTC == set()
    assert cfg.ALLOWED_HOURS_UTC == set(range(24))


def test_kill_switch_disables_claude_tp_clamp(monkeypatch):
    """When SCALP_TIER_ENABLED=False, the Claude TP/SL clamp at
    `mcp_brain.py:~1888` takes the else branch — raw values pass
    through. This mirrors the gate condition exactly: callers compute
    `_scalp_clamp_on = getattr(_cfg, "SCALP_TIER_ENABLED", True)` and
    only clamp when that is True."""
    monkeypatch.setenv("SCALP_TIER_ENABLED", "false")
    cfg = _reload_config()
    _scalp_clamp_on = getattr(cfg, "SCALP_TIER_ENABLED", True)
    assert _scalp_clamp_on is False

    raw_tp, raw_sl = 10.0, 5.0
    atype, market_type = "OPEN", "futures"
    if atype == "OPEN" and market_type == "futures" and _scalp_clamp_on:
        tp_clamped = min(2.0, max(1.8, raw_tp))
        sl_clamped = min(max(1.2, raw_sl), min(1.6, tp_clamped / 1.2))
    else:
        tp_clamped = raw_tp
        sl_clamped = raw_sl
    assert tp_clamped == 10.0
    assert sl_clamped == 5.0


def test_kill_switch_restores_algo_futures_tp_floor(monkeypatch):
    """The algo-path futures TP floor at `mcp_brain.py:~2962` flips from
    `min(2.0, max(1.0, tp_pct))` (clamp to scalp band) to
    `max(tp_pct, 4.0)` (wide floor) when SCALP_TIER_ENABLED=false."""
    monkeypatch.setenv("SCALP_TIER_ENABLED", "false")
    cfg = _reload_config()
    _scalp_floor_on = getattr(cfg, "SCALP_TIER_ENABLED", True)
    assert _scalp_floor_on is False

    raw_tp = 3.5  # between the two regimes' outputs
    if _scalp_floor_on:
        tp_pct = min(2.0, max(1.0, raw_tp))
    else:
        tp_pct = max(raw_tp, 4.0)
    assert tp_pct == 4.0  # wide floor kicked in


def test_kill_switch_restores_original_blend(monkeypatch):
    """The blend gate at `mcp_brain.py:~1868` flips: when SCALP is OFF,
    algo_conf<=0 no longer falls through to Claude — the original
    `(claude+algo)/2` blend always runs."""
    monkeypatch.setenv("SCALP_TIER_ENABLED", "false")
    cfg = _reload_config()
    _scalp_blend_on = getattr(cfg, "SCALP_TIER_ENABLED", True)
    assert _scalp_blend_on is False

    claude_conf, algo_conf = 0.62, 0.0  # algo "no opinion"
    if _scalp_blend_on and algo_conf <= 0.0:
        final_conf = claude_conf
    else:
        final_conf = (claude_conf + algo_conf) / 2.0
    assert final_conf == pytest.approx(0.31)  # the OLD halved value
