"""Analysis-only instruments — format fix + entry block (2026-06-02; lifted 2026-06-11).

Owner observed that commodity/equity perps (XAU XAG CL BZ COPPER + TSLA NVDA
AMZN AAPL GOOGL META MSFT MSTR COIN) ARE live perps on Binance/Bybit/Bitget
(ccxt `XAU/USDT:USDT`, raw `XAUUSDT`). They were hard-blocked from entries
(no screened edge). 2026-06-11 owner UNBLOCK directive ("unblock all symbols.
add new symbols which are listed on all connected exchanges"): the block is
now OPT-IN via ANALYSIS_ONLY_ENFORCED (default OFF — symbols tradeable).

This pins:
  1. The default: block UNENFORCED -> is_analysis_only() is False for everything
  2. The match logic, intact behind the flag (re-armable via env)
  3. _execute_open still consults the predicate (source-level)
  4. _collect_all_coins always includes analysis-only bases (source-level)
  5. mcp_brain OHLCV fetch has a perp fallback for perp-only instruments (source-level)
"""

from __future__ import annotations

from pathlib import Path

# ── 1. The safety predicate (pure) ────────────────────────────────────


def test_analysis_only_bases_cover_observed_instruments():
    # the bases set is the perp-only instrument REGISTRY (mcp_brain fetch
    # routing + _collect_all_coins) — it must stay populated even unblocked
    from config import ANALYSIS_ONLY_BASES

    for b in ("XAU", "XAG", "CL", "BZ", "COPPER", "TSLA", "NVDA", "AMZN", "MSTR"):
        assert b in ANALYSIS_ONLY_BASES, f"{b} missing from ANALYSIS_ONLY_BASES"


def test_default_unenforced_blocks_nothing(monkeypatch):
    # 2026-06-11 owner unblock: with the flag at its default (off), the
    # entry block is inert — commodity/equity perps are tradeable
    import config

    monkeypatch.setattr(config, "ANALYSIS_ONLY_ENFORCED", False)
    assert config.is_analysis_only("XAU/USDT:USDT") is False
    assert config.is_analysis_only("TSLAUSDT") is False
    assert config.is_analysis_only("BTC/USDT:USDT") is False


def test_env_default_is_unenforced():
    # no ANALYSIS_ONLY_ENFORCED in .env -> the directive's default applies
    import os

    import config

    if os.getenv("ANALYSIS_ONLY_ENFORCED") is None:
        assert config.ANALYSIS_ONLY_ENFORCED is False


def test_is_analysis_only_matches_perp_and_spot_forms(monkeypatch):
    # match logic preserved behind the flag (re-arm with ANALYSIS_ONLY_ENFORCED=true)
    import config

    monkeypatch.setattr(config, "ANALYSIS_ONLY_ENFORCED", True)
    assert config.is_analysis_only("XAU/USDT:USDT") is True  # perp (ccxt)
    assert config.is_analysis_only("XAU/USDT") is True  # spot-name form
    assert config.is_analysis_only("COPPER/USDT:USDT") is True
    assert config.is_analysis_only("TSLA/USDT:USDT") is True
    assert config.is_analysis_only("XAUUSDT") is True  # raw exchange id (no slash)
    assert config.is_analysis_only("TSLAUSDT") is True


def test_is_analysis_only_does_not_block_crypto(monkeypatch):
    import config

    monkeypatch.setattr(config, "ANALYSIS_ONLY_ENFORCED", True)
    assert config.is_analysis_only("BTC/USDT:USDT") is False
    assert config.is_analysis_only("ETH/USDT") is False
    assert config.is_analysis_only("ATOM/USDT:USDT") is False
    # XAUT (Tether Gold) is a crypto-native token, NOT an analysis-only perp —
    # must not be caught by a sloppy prefix match on "XAU" (slash or raw form).
    assert config.is_analysis_only("XAUT/USDT") is False
    assert config.is_analysis_only("XAUTUSDT") is False  # raw id must not collide with XAU
    assert config.is_analysis_only("BTCUSDT") is False  # raw crypto id stays tradeable


# ── 2. Hard live-entry block ──────────────────────────────────────────


def test_execute_open_hard_blocks_analysis_only():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    idx = src.index("def _execute_open")
    block = src[idx : idx + 3000]
    assert "is_analysis_only" in block, "_execute_open must consult is_analysis_only"
    assert "[AnalysisOnly]" in block, "_execute_open must log the analysis-only block"
    assert "return False" in block


# ── 3. Analysis-tracking: included in the analyzed coin set ───────────


def test_collect_all_coins_includes_analysis_only():
    src = Path("core/bot_engine.py").read_text(encoding="utf-8")
    idx = src.index("def _collect_all_coins")
    block = src[idx : idx + 2000]
    assert "ANALYSIS_ONLY_BASES" in block, (
        "_collect_all_coins must include analysis-only bases so they are analyzed"
    )


# ── 4. Format fix: perp fallback for perp-only instruments ────────────


def test_mcp_brain_has_perp_fallback():
    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    assert 'f"{coin}/USDT:USDT"' in src, (
        "mcp_brain OHLCV fetch must fall back to the linear perp when spot 404s"
    )
    assert "Perp fallback" in src
