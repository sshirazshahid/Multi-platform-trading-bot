"""Honesty labels for scalp score-floor SKIPs + AccBand TradFi scope (2026-07-30).

Mission Control was aggregating bare ``vwap_near=…|rsi=…`` strings as unique
families (~25% of 6h SKIPs). Those are 4/4-req passes that failed the entry
score floor — they must carry a stable ``scalp_score_below_floor`` prefix.

AccBand + MAX_FLOW_BAND PAPER must not count ANALYSIS_ONLY TradFi bases
(MSFT/…) as ALLOW noise in the decision funnel.
"""
from __future__ import annotations

import core.mcp_brain as mb


def test_format_scalp_skip_preserves_veto_and_req_fail():
    assert (
        mb._format_scalp_rule_skip_reason(
            {"score": 0, "reason": "scalp_veto:quiet(atr=0.51%)"}, floor=66
        )
        == "scalp_veto:quiet(atr=0.51%)"
    )
    assert mb._format_scalp_rule_skip_reason(
        {"score": 0, "reason": "scalp_req_fail(3/4:rsi=51,adx=33,atr=0.84%)"},
        floor=66,
    ).startswith("scalp_req_fail")


def test_format_scalp_skip_labels_score_below_floor():
    detail = "vwap_near=0.35% | rsi=48 | adx=22 | atr=0.86%"
    out = mb._format_scalp_rule_skip_reason(
        {"score": 50, "reason": detail, "_scalp": True}, floor=66
    )
    assert out.startswith("scalp_score_below_floor(50<66):")
    assert detail in out


def test_format_scalp_skip_preserves_accband_scope():
    assert (
        mb._format_scalp_rule_skip_reason(
            {"score": 0, "reason": "analysis_only_accband_scope"}, floor=66
        )
        == "analysis_only_accband_scope"
    )


def test_accband_tradfi_scope_skips_msft_when_geometry_on(monkeypatch):
    monkeypatch.setattr(
        "config.ACCURACY_TARGET_MODE",
        {"enabled": True},
        raising=False,
    )
    monkeypatch.setattr(
        "config.ANALYSIS_ONLY_BASES",
        {"MSFT", "NVDA"},
        raising=False,
    )
    # Patch the names the helper imports from config
    import config

    monkeypatch.setattr(config, "ACCURACY_TARGET_MODE", {"enabled": True})
    monkeypatch.setattr(config, "ANALYSIS_ONLY_BASES", {"MSFT", "NVDA"})
    assert mb._accband_tradfi_scope_reason("MSFT") == "analysis_only_accband_scope"
    assert mb._accband_tradfi_scope_reason("BTC") is None


def test_tradfi_scope_still_skips_when_accband_geometry_disabled(monkeypatch):
    """TradFi exclusion must NOT depend on AccBand geometry being enabled.

    Supersedes test_accband_tradfi_scope_off_when_geometry_disabled (2026-07-30),
    which asserted the opposite. That assertion was measured wrong in production:
    ACCURACY_TARGET_MODE.enabled is False on the live bot, so the helper returned
    None for every base and tokenized equity flowed straight into ALLOW --
    18 of 41 ALLOWs in one hour on 2026-07-31 were META/USDT.

    The reason to keep tokenized equity out of the crypto decision funnel is that
    it has no screened edge and pollutes the allow-rate metric (see the call-site
    comment at mcp_brain.py:3821). That rationale is independent of the exit
    geometry, so the guard must not be gated on it.
    """
    import config

    monkeypatch.setattr(config, "ACCURACY_TARGET_MODE", {"enabled": False})
    monkeypatch.setattr(config, "ANALYSIS_ONLY_BASES", {"MSFT", "META"})
    assert mb._accband_tradfi_scope_reason("MSFT") == "analysis_only_accband_scope"
    assert mb._accband_tradfi_scope_reason("META") == "analysis_only_accband_scope"
    # crypto is unaffected in either geometry mode
    assert mb._accband_tradfi_scope_reason("BTC") is None


def test_tradfi_scope_handles_pair_form_and_missing_base(monkeypatch):
    """The live path passes a bare base, but a pair form must not slip through."""
    import config

    monkeypatch.setattr(config, "ACCURACY_TARGET_MODE", {"enabled": False})
    monkeypatch.setattr(config, "ANALYSIS_ONLY_BASES", {"META"})
    assert mb._accband_tradfi_scope_reason("META/USDT") == "analysis_only_accband_scope"
    assert mb._accband_tradfi_scope_reason("") is None
    assert mb._accband_tradfi_scope_reason(None) is None
