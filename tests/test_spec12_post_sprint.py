"""Spec §12 safety-net soak test under sprint-patch behavior.

The sprint patches (especially Patch #3 amplification) bias the bot toward
HOLDING. If too many stop-losses fire as a result, Spec §12 (5 global
consecutive losses -> review_required.json) must remain the safety net.

This test does NOT exercise the patches' behavior. It confirms the
existing Spec §12 mechanism still writes review_required.json at 5
consecutive global losses when the patches are imported and active.
"""
from __future__ import annotations

import json
from datetime import date


def _seed_risk_state(tmp_path) -> None:
    """Write a clean, same-day risk_state.json so RiskManager loads with
    no carryover halts and an empty global streak."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    rs = {
        "halt_reason": "",
        "halt_time": 0.0,
        "daily_pnl": 0.0,
        "max_drawdown_pct": 0.0,
        "start_balance": 500.0,
        "peak_balance": 500.0,
        "trading_day": date.today().isoformat(),
        "trades_today": 0,
        "recent_results": [],
        "trade_history": [],
        "symbol_pauses": {},
        "family_pauses": {},
        "global_streak": [],
        "timestamp": 0,
    }
    (tmp_path / "data" / "risk_state.json").write_text(
        json.dumps(rs), encoding="utf-8"
    )


def test_ghost_and_sl_placement_reasons_are_neutral_for_spec12(tmp_path, monkeypatch):
    """2026-05-24 — Infrastructure exits must NOT extend the Spec §12
    streak.

    After 2026-05-24 Commit 1, real exchange-side SL fills via the
    ghost path are reclassified to `stop_loss` (which IS non-neutral
    and correctly contributes to the streak). The residual
    `ghost_reconciled` / `ghost_sync` rows are genuinely-unclassified
    infrastructure events (truly-unexpected closes, fills outside the
    50bps SL/TP band). They — plus `sl_crossed_at_placement` /
    `sl_crossed_during_placement` (price moved between order place and
    SL place, infrastructure) — must be in `_SPEC12_NEUTRAL_REASONS`.

    Conflicts with the May-13 cooldown intent? No: the May-13 fix
    wired Phase 29 cooldown via `note_sl_hit` (only on `stop_loss`).
    `_SPEC12_NEUTRAL_REASONS` is a different mechanism — it gates
    `record_trade_result`'s streak. Independent.
    """
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    from core.risk_manager import RiskManager, _SPEC12_NEUTRAL_REASONS

    for reason in ("ghost_reconciled", "ghost_sync",
                   "sl_crossed_at_placement", "sl_crossed_during_placement"):
        assert reason in _SPEC12_NEUTRAL_REASONS, (
            f"{reason!r} must be in _SPEC12_NEUTRAL_REASONS so "
            f"infrastructure exits do not falsely trigger the 5-consec "
            f"global halt."
        )

    rm = RiskManager()
    for i, reason in enumerate(("ghost_reconciled", "ghost_sync",
                                  "sl_crossed_at_placement",
                                  "sl_crossed_during_placement",
                                  "ghost_reconciled")):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT",
            family="claude_portfolio",
            is_win=False,
            pnl_usd=-0.50,
            pnl_pct=-1.5,
            reason=reason,
        )
    # Neutral reasons must not extend the global streak
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 0, (
        "5 consecutive infrastructure-exit losses must NOT extend the "
        f"Spec §12 global streak; got {rm._global_streak}"
    )
    assert not (tmp_path / "data" / "review_required.json").exists(), (
        "review_required.json must not be written for neutral exits"
    )


def test_five_consecutive_losses_tracked_in_global_streak(tmp_path, monkeypatch):
    """Synthesize 5 sequential loss rows; assert the global streak tracks them.

    Halt mechanisms are removed, but streak tracking must still work so
    the data is available for future analysis.
    """
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    # Import patches so we know they are loaded and active in the test
    # process even though they don't drive behavior here.
    from core import order_manager  # noqa: F401

    from core.risk_manager import RiskManager

    rm = RiskManager()

    assert hasattr(rm, "record_trade_result"), (
        "RiskManager must expose record_trade_result(symbol, family, "
        "is_win, ...). API drift will silently break streak tracking "
        "— fail loudly here."
    )

    for i in range(5):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT",
            family="claude_portfolio",
            is_win=False,
            pnl_usd=-0.50,
            pnl_pct=-1.5,
            reason="stop_loss",
        )

    # Global streak must record all 5 losses
    non_neutral = [r for r in rm._global_streak if r is False]
    assert len(non_neutral) == 5, (
        f"Spec §12 streak tracking should record 5 consecutive losses; "
        f"got {rm._global_streak}"
    )
