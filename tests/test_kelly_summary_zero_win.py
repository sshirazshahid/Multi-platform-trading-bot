from __future__ import annotations

from pathlib import Path


def test_kelly_summary_all_loss_strategy_does_not_divide_by_zero(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    Path("data").mkdir()

    from core.kelly_sizer import KellySizer

    kelly = KellySizer()
    kelly.update_from_trades(
        [
            {"strategy": "machine", "pnl": -1.0, "pnl_pct": -0.01},
            {"strategy": "machine", "pnl": -2.0, "pnl_pct": -0.02},
            {"strategy": "machine", "pnl": -3.0, "pnl_pct": -0.03},
        ]
    )

    summary = kelly.get_summary()

    assert summary["machine"]["r_multiple"] == 0.0
    assert summary["machine"]["full_kelly"] == -100.0
    assert summary["machine"]["edge"] == "negative"
