"""Adversarial challenge harness tests for MTSI."""

from __future__ import annotations

from research.challenge_mtsi import run_all_challenges


def test_mtsi_challenge_harness_passes() -> None:
    report = run_all_challenges()
    assert report["ok"] is True
    names = {c["name"] for c in report["challenges"]}
    assert names >= {
        "fee_understatement",
        "one_dollar_invariants",
        "spot_vs_futures_funding",
        "latency_honesty",
    }
