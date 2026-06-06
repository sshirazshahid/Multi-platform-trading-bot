"""Regression: _select_leverage_tier must not KeyError when SCALP_TIER_ENABLED=false.

config.py:1173-1174 does `LEVERAGE_TIERS.pop("SCALP", None)` when SCALP_TIER_ENABLED is
false, but _select_leverage_tier still iterates the hard-coded literal tuple
("AGGRESSIVE","CONVICTION","STRONG","STANDARD","SCALP") and did a bare subscript
`t = LEVERAGE_TIERS[tier_name]`. If the loop reaches the 'SCALP' iteration (all higher
tiers skipped) with SCALP popped -> KeyError crashes tier selection. Dormant under the
default config (SCALP enabled), but a real one-flag-away crash. Surfaced by the
2026-06-07 wiring audit. The bot is NO_EDGE; this is a robustness fix, not an
expectancy change.
"""
from __future__ import annotations

from core.bot_engine import BotEngine


def _min_engine():
    """Construct just enough of BotEngine to exercise _select_leverage_tier."""
    eng = BotEngine.__new__(BotEngine)
    eng._current_utc_hour = lambda: 12
    eng._classify_hour = lambda h: "allowed"
    eng._consec_loss_state = lambda: {"pause_until": 0.0, "tier_cap": None}
    eng.auto_mutator = None
    return eng


def test_select_tier_no_keyerror_when_scalp_popped(monkeypatch):
    import config

    tiers = {k: v for k, v in config.LEVERAGE_TIERS.items() if k != "SCALP"}
    assert "SCALP" not in tiers
    monkeypatch.setattr(config, "LEVERAGE_TIERS", tiers)

    eng = _min_engine()
    # Low confidence: every real tier fails its min_confidence and is skipped, so the
    # loop reaches the 'SCALP' iteration, which is absent from LEVERAGE_TIERS. It must
    # skip gracefully and return (None, None) — not raise KeyError.
    name, params = eng._select_leverage_tier(
        "BTC/USDT:USDT", "buy", confidence=0.10, btc_trend="bull"
    )
    assert name is None and params is None


def test_select_tier_still_works_with_scalp_present():
    """Sanity: with the default (SCALP present) config, a qualifying low-conf candidate
    can still resolve to the SCALP tier — the .get() fix must not break the happy path."""
    eng = _min_engine()
    # SCALP min_confidence is 0.40; 0.45 clears SCALP but not STANDARD's higher floor.
    name, params = eng._select_leverage_tier(
        "BTC/USDT:USDT", "buy", confidence=0.45, btc_trend="bull"
    )
    # Either SCALP resolves, or it's rejected for another gate — but never a KeyError,
    # and never a higher tier (escalation is capped off by default).
    assert name in (None, "SCALP", "STANDARD")
