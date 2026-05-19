"""Spec §12 safety-net soak test under sprint-patch behavior.

The sprint patches (especially Patch #3 amplification) bias the bot toward
HOLDING. If too many stop-losses fire as a result, Spec §12 (5 global
consecutive losses -> halt + 4h auto-resume) must remain the safety net.

This test does NOT exercise the patches' behavior. It confirms the
existing Spec §12 mechanism still trips at 5 consecutive global losses
when the patches are imported and active.
"""
from __future__ import annotations

import json
from datetime import date


def _seed_risk_state(tmp_path) -> None:
    """Write a clean, same-day risk_state.json so RiskManager loads with
    no carryover halts and an empty global streak."""
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    rs = {
        "is_halted": False,
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


def test_five_consecutive_losses_writes_review_required(tmp_path, monkeypatch):
    """Synthesize 5 sequential loss rows; assert is_halted=True AND
    review_required.json appears.

    The sprint patches are imported at the top of the test so they are
    loaded into the test process even though they are inert for this
    invariant check.
    """
    monkeypatch.chdir(tmp_path)
    _seed_risk_state(tmp_path)

    # Import patches so we know they are loaded and active in the test
    # process even though they don't drive behavior here.
    import config  # noqa: F401
    from core import order_manager  # noqa: F401
    from core.risk_manager import RiskManager

    rm = RiskManager()

    # The RiskManager API exposes record_trade_result(symbol, family,
    # is_win, pnl_usd, pnl_pct, reason). We must:
    #  - pass is_win=False
    #  - pass a non-neutral `reason` (e.g. "stop_loss") so the call is
    #    NOT silently skipped by the _SPEC12_NEUTRAL_REASONS guard
    #  - pass pnl_pct beyond the scratch threshold (|pnl_pct| >=
    #    _SPEC12_SCRATCH_PCT == 0.5) so the row is not classed as a
    #    scratch and dropped
    #  - pass a non-outlier pnl_usd (>= -MAX_LOSS_PER_TRADE_USD) so we
    #    only trip the 5-consec-global path, not the outlier path
    assert hasattr(rm, "record_trade_result"), (
        "RiskManager must expose record_trade_result(symbol, family, "
        "is_win, ...). API drift will silently break the Spec §12 "
        "safety net — fail loudly here."
    )

    for i in range(5):
        rm.record_trade_result(
            symbol=f"X{i}/USDT:USDT",
            family="claude_portfolio",
            is_win=False,
            pnl_usd=-0.50,   # well under MAX_LOSS_PER_TRADE_USD ($2 default)
            pnl_pct=-1.5,    # |1.5| > _SPEC12_SCRATCH_PCT (0.5) — not a scratch
            reason="stop_loss",  # not in _SPEC12_NEUTRAL_REASONS
        )

    halted = bool(getattr(rm, "is_halted", False))
    review_file = tmp_path / "data" / "review_required.json"
    review_file_exists = review_file.exists()

    assert halted and review_file_exists, (
        f"Spec §12 did not trip on 5 consecutive global losses. "
        f"is_halted={halted}, review_required exists={review_file_exists}. "
        f"global_streak={rm._global_streak}. "
        f"This means the sprint patches MIGHT have broken the existing "
        f"halt mechanism — investigate before proceeding."
    )

    # Sanity: the persisted halt_reason should mention spec12
    assert "spec12" in rm.halt_reason.lower(), (
        f"halt_reason='{rm.halt_reason}' does not look like a Spec §12 "
        f"halt — another guard tripped instead."
    )

    # Sanity: the review_required.json payload should mention the
    # 5-consec-global reason and force-observation action.
    flag_events = json.loads(review_file.read_text(encoding="utf-8"))
    assert isinstance(flag_events, list) and flag_events, (
        f"review_required.json malformed: {flag_events!r}"
    )
    last = flag_events[-1]
    assert "consecutive global losses" in last.get("reason", ""), (
        f"latest review flag reason not Spec §12: {last!r}"
    )
    assert last.get("action") == "force_observation_mode", (
        f"latest review flag action not force_observation_mode: {last!r}"
    )
