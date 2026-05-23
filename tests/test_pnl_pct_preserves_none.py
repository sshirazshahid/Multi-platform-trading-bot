"""`_finalize_close` must preserve None for pnl_pct (2026-05-24).

Bug: `pnl_pct = pos.pnl_pct or 0.0` converted None → 0.0. The
downstream `record_trade_result` then checks
    `if pnl_pct is not None and abs(pnl_pct) < 0.5: return`
so 0.0 trips the scratch gate and the loss is silently NOT counted.
This particularly bites ghost-reclassified SL fills where the
position may have been imported without entry context (pnl_pct=None
but pnl_usd is real).

`record_trade_result` already documents accepting None: "pnl_pct may
be None when the trade was reconciled from the exchange ledger
without entry/size context".
"""
from __future__ import annotations

import re
from pathlib import Path


def test_finalize_close_does_not_coerce_pnl_pct_to_zero():
    """Structural pin: the `pos.pnl_pct or 0.0` coercion must be gone."""
    src = (Path(__file__).resolve().parents[1]
           / "core" / "order_manager.py").read_text(encoding="utf-8")
    # Find _finalize_close body.
    m = re.search(
        r"def _finalize_close\(self[^)]*\)[\s\S]+?(?=\n    def |\Z)",
        src,
    )
    assert m is not None
    body = m.group(0)
    # The coercion-as-assignment must not be present (comment mentions are fine).
    assert not re.search(r"^\s*pnl_pct\s*=\s*pos\.pnl_pct\s*or\s*0\.0", body, re.M), (
        "_finalize_close still coerces None→0.0 via `pnl_pct = pos.pnl_pct or 0.0`; "
        "this scratches real losses by tripping the SPEC12_SCRATCH_PCT gate."
    )
    # And the simple assignment must remain.
    assert re.search(r"pnl_pct\s*=\s*pos\.pnl_pct(?!\s*or)", body), (
        "_finalize_close must read pnl_pct directly so None survives "
        "to record_trade_pnl / record_trade_result which both handle None."
    )


def test_record_trade_result_passes_through_none_pnl_pct():
    """Sanity: record_trade_result with pnl_pct=None must not scratch."""
    import json
    import tempfile
    from datetime import date
    from pathlib import Path as P
    import os

    with tempfile.TemporaryDirectory() as tdir:
        cwd = os.getcwd()
        os.chdir(tdir)
        try:
            (P(tdir) / "data").mkdir()
            (P(tdir) / "data" / "risk_state.json").write_text(json.dumps({
                "is_halted": False, "halt_reason": "", "halt_time": 0.0,
                "daily_pnl": 0.0, "max_drawdown_pct": 0.0,
                "start_balance": 500.0, "peak_balance": 500.0,
                "trading_day": date.today().isoformat(),
                "trades_today": 0, "recent_results": [],
                "trade_history": [], "symbol_pauses": {},
                "family_pauses": {}, "global_streak": [], "timestamp": 0,
            }), encoding="utf-8")
            from core.risk_manager import RiskManager
            rm = RiskManager()
            rm.record_trade_result(
                symbol="BTC/USDT:USDT", family="claude_portfolio",
                is_win=False, pnl_usd=-0.50,
                pnl_pct=None,  # the key — was being smashed to 0.0
                reason="stop_loss",
            )
            assert rm._global_streak == [False], (
                f"loss with pnl_pct=None must extend streak; got "
                f"{rm._global_streak}"
            )
        finally:
            os.chdir(cwd)
