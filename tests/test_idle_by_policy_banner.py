"""A by-design idle state must SAY it is by design — and whose decision it was.

2026-08-19 council finding: the bot sat idle 52h with the boot banner reading
only "EntryPolicy: SHADOW_ONLY". The fact was visible; the reason (owner
directive, cash beat every measured mask), the origin, and the next review
date were not — and the owner asked "why no trades" four times. Three council
voices independently named this a communication defect, not an evidence gap.
"""
from __future__ import annotations

from core.engine.helpers import idle_by_policy_lines
from core.engine.monitors import _idle_by_policy_field


def test_shadow_only_banner_states_origin_basis_and_next_review():
    text = "\n".join(idle_by_policy_lines("SHADOW_ONLY"))
    assert "IdleByPolicy" in text and "NEW ENTRIES OFF" in text
    assert "owner directive" in text            # whose decision
    assert "73_plan_paper_then_cash" in text    # the measured basis
    assert "next review" in text                # when it changes
    assert "APPROVED_PAPER" in text             # how to undo it


def test_trading_policies_print_nothing():
    for pol in ("APPROVED_PAPER", "CONTROLLED_LIVE", "", None):
        assert idle_by_policy_lines(pol) == []


def test_heartbeat_field_mirrors_banner():
    f = _idle_by_policy_field("SHADOW_ONLY")
    assert f["new_entries"] == "OFF" and "owner directive" in f["origin"]
    assert _idle_by_policy_field("APPROVED_PAPER") is None
