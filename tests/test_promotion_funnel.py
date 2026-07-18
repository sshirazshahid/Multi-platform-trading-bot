"""Promotion funnel tests — synthetic stores only, no production data."""
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import promotion_funnel as pf  # noqa: E402


def test_lane_state_serializes_with_all_fields():
    ls = pf.LaneState(lane="x", state="ACCRUING", resolved=5, wins=3, wr=0.6,
                      floor_progress="5/30", accrual_rate_7d=1.2, eta_days=20.8,
                      detail={"k": "v"})
    d = ls.to_dict()
    assert d["lane"] == "x" and d["floor_progress"] == "5/30" and d["detail"] == {"k": "v"}


def test_atomic_write_json_replaces_not_partial(tmp_path):
    p = tmp_path / "out.json"
    pf.atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert not (tmp_path / "out.json.tmp").exists()


def test_zero_live_path_imports():
    """Funnel import purity, checked in a FRESH interpreter: the test session
    itself loads banned modules (tests/conftest.py autouse fixtures import
    core.order_manager / core.kill_switch), so inspecting this process's
    sys.modules would test the harness, not the funnel."""
    banned = ("core.bot_engine", "core.order_manager", "exchanges", "config", "ccxt")
    code = (
        f"import sys; sys.path.insert(0, {str(ROOT)!r});\n"
        "import scripts.promotion_funnel\n"
        f"banned = {banned!r}\n"
        "loaded = set(sys.modules)\n"
        "bad = [b for b in banned\n"
        "       if any(m == b or m.startswith(b + '.') for m in loaded)]\n"
        "assert not bad, f'funnel pulled {bad}'\n"
    )
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert res.returncode == 0, f"funnel import purity failed:\n{res.stderr}"
