"""Task B.3 — promotion-gate notifier."""
from __future__ import annotations

from unittest.mock import MagicMock


def _ev(verdict_str, shadow_n=120):
    from core.multi_agent_promotion_gate import MAEvaluation, MAVerdict
    return MAEvaluation(
        verdict=MAVerdict(verdict_str), reason="ok",
        shadow_n=shadow_n, shadow_wr=0.65, shadow_sum_pnl=10.0, shadow_sharpe=0.5,
        live_n=50, live_wr=0.35, live_sum_pnl=-5.0, live_sharpe=-0.2,
        shadow_fee_per_trade=0.04, live_fee_per_trade=0.05,
    )


def test_alert_fires_on_first_promote(tmp_path):
    from core.agents.promotion_alert import maybe_alert
    notifier = MagicMock()
    state = tmp_path / "promote_state.json"
    sent = maybe_alert(_ev("PROMOTE"), state, notifier=notifier)
    assert sent is True
    assert notifier.info.called


def test_alert_idempotent_on_repeat_promote(tmp_path):
    from core.agents.promotion_alert import maybe_alert
    notifier = MagicMock()
    state = tmp_path / "promote_state.json"
    maybe_alert(_ev("PROMOTE"), state, notifier=notifier)
    notifier.reset_mock()
    sent = maybe_alert(_ev("PROMOTE"), state, notifier=notifier)
    assert sent is False
    assert not notifier.info.called


def test_alert_not_sent_on_hold(tmp_path):
    from core.agents.promotion_alert import maybe_alert
    notifier = MagicMock()
    state = tmp_path / "promote_state.json"
    sent = maybe_alert(_ev("HOLD"), state, notifier=notifier)
    assert sent is False
    assert not notifier.info.called


def test_alert_re_fires_after_hold_then_promote(tmp_path):
    from core.agents.promotion_alert import maybe_alert
    notifier = MagicMock()
    state = tmp_path / "promote_state.json"
    maybe_alert(_ev("PROMOTE"), state, notifier=notifier)
    notifier.reset_mock()
    maybe_alert(_ev("HOLD"), state, notifier=notifier)
    sent = maybe_alert(_ev("PROMOTE"), state, notifier=notifier)
    assert sent is True
    assert notifier.info.called
