"""F7 (2026-07-20 deep audit) — funnel reporting polish.

a) render_journal printed ``eta=Noned`` when a funnel lane's eta_days was
   None (f-string interpolation of None + literal ``d``).
b) f1_lane_state silently capped entries_48h at the last 2000 log lines, so a
   busy 48h window under-reported gate evaluations with no indication.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import promotion_funnel as pf  # noqa: E402
from scripts.report_goal_progress import render_journal  # noqa: E402


def _report():
    return {"generated_utc": "2026-07-20T00:00:00+00:00", "lanes": []}


def _funnel_file(tmp_path, eta_days):
    p = tmp_path / "promotion_funnel.json"
    p.write_text(json.dumps({"lanes": [{
        "lane": "tsmom_20d_1h", "state": "IDLE", "floor_progress": "0/30",
        "eta_days": eta_days,
    }]}), encoding="utf-8")
    return p


def test_render_journal_eta_none_is_not_noned(tmp_path):
    text = render_journal(_report(), funnel_path=_funnel_file(tmp_path, None))
    assert "Noned" not in text
    assert "eta=n/a" in text


def test_render_journal_eta_numeric_keeps_days_suffix(tmp_path):
    text = render_journal(_report(), funnel_path=_funnel_file(tmp_path, 20.8))
    assert "eta=20.8d" in text


def test_f1_entries_48h_not_capped_at_2000_lines(tmp_path):
    log = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    n = 2500  # all within the 48h window
    rows = [json.dumps({"ts": now - 3600 - i, "symbol": "XRP/USDT",
                        "venue": "bitget", "net_edge_bps": -20.0})
            for i in range(n)]
    log.write_text("\n".join(rows), encoding="utf-8")
    st = pf.f1_lane_state(log, now)
    assert st.detail["entries_48h"] == n, (
        f"entries_48h={st.detail['entries_48h']} — silent 2000-line cap?")


def test_f1_old_tail_stops_scan_but_window_is_exact(tmp_path):
    """Records older than 48h beyond the window are excluded; recent ones
    (even past position 2000 from the end) are all counted."""
    log = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    old = [json.dumps({"ts": now - 50 * 3600 - i, "symbol": "XRP/USDT",
                       "venue": "bitget", "net_edge_bps": 1.0})
           for i in range(50)]
    recent = [json.dumps({"ts": now - 600 + i, "symbol": "XRP/USDT",
                          "venue": "bitget", "net_edge_bps": 5.0})
              for i in range(4)]
    log.write_text("\n".join(old + recent), encoding="utf-8")
    st = pf.f1_lane_state(log, now)
    assert st.detail["entries_48h"] == 4
    # streak logic still sees chronological order: 4 consecutive positives
    assert st.detail["alert"] is True
