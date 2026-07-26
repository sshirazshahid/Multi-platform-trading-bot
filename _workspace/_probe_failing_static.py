"""Static probe of historically-failing contracts (no pytest needed)."""
from __future__ import annotations

import inspect
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

OUT = Path(__file__).resolve().parent / "failing_static_probe.txt"
lines: list[str] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    status = "PASS" if ok else "FAIL"
    lines.append(f"{status}: {name}" + (f" — {detail}" if detail else ""))


# 1) reject_reason stash in _execute_open
from core.bot_engine import BotEngine

src = inspect.getsource(BotEngine._execute_open)
misses = []
src_lines = src.splitlines()
for i, ln in enumerate(src_lines):
    if ln.strip() == "return False":
        j = i - 1
        found = False
        checked = 0
        while j >= 0 and checked < 5:
            stripped = src_lines[j].strip()
            j -= 1
            if not stripped:
                continue
            checked += 1
            if "reject_reason" in stripped:
                found = True
                break
        if not found:
            misses.append(f"line {i}: {ln.strip()} (prev: {src_lines[i-1].strip() if i else '?'})")
check("execute_open_reject_reason", not misses, "; ".join(misses[:5]))

# 2) daily-loss lock
from core import risk_manager as rm

m = re.search(r"def _daily_loss_reason\(self[\s\S]+?(?=\n    def |\Z)", inspect.getsource(rm.RiskManager))
body = m.group(0) if m else ""
check(
    "daily_loss_lock",
    bool(re.search(r"with self\._lock:[\s\S]{0,160}?_daily_pnl", body)),
    "no lock window" if body else "missing method",
)

# 3) mfe hook
from core.order_manager import OrderManager

check(
    "mfe_hook",
    "_update_trade_extremes" in inspect.getsource(OrderManager.check_sl_tp),
)

# 4) pending maker lock on class init source
init_src = inspect.getsource(OrderManager.__init__)
check("_pending_maker_lock_init", "_pending_maker_lock" in init_src)

# 5) config defaults in clean env (subprocess)
import subprocess

env = os.environ.copy()
for key in (
    "OPERATING_MODE",
    "ENTRY_POLICY",
    "APPROVED_PAPER_STRATEGIES",
    "APPROVED_LIVE_STRATEGIES",
    "CONTROLLED_LIVE_ENABLED",
    "PAPER_TRADING_PROFILE",
    "PAPER_PROFILE_STARTED_AT",
    "MAX_PORTFOLIO_EXPOSURE_PCT",
    "MAX_AGGREGATE_OPEN_RISK_PCT",
):
    env.pop(key, None)
env["PYTHON_DOTENV_DISABLED"] = "1"
code = """
import config
print('ENTRY_POLICY', config.ENTRY_POLICY)
print('APPROVED_PAPER', sorted(config.APPROVED_PAPER_STRATEGIES))
print('max_open', config.RISK['max_open_positions'])
print('lev', config.RISK['futures_max_leverage'])
print('risk_pct', config.VOL_TARGET_SIZING['per_trade_risk_pct'])
print('exposure', config.MAX_PORTFOLIO_EXPOSURE_PCT)
print('agg', config.MAX_AGGREGATE_OPEN_RISK_PCT)
assert config.ENTRY_POLICY == 'SHADOW_ONLY'
assert config.APPROVED_PAPER_STRATEGIES == frozenset()
assert config.APPROVED_LIVE_STRATEGIES == frozenset()
assert config.RISK['max_open_positions'] == 3
assert config.RISK['futures_max_leverage'] == 1.5
assert config.VOL_TARGET_SIZING['per_trade_risk_pct'] == 0.0025
assert config.MAX_PORTFOLIO_EXPOSURE_PCT == 10.0
assert config.MAX_AGGREGATE_OPEN_RISK_PCT == 0.0075
print('CONFIG_OK')
"""
completed = subprocess.run(
    [sys.executable, "-c", code],
    cwd=ROOT,
    env=env,
    capture_output=True,
    text=True,
)
check(
    "config_shadow_defaults",
    completed.returncode == 0,
    (completed.stderr or completed.stdout)[:500],
)

# 6) kill switch visibility
from pathlib import Path as P
from core.kill_switch import KILL_SWITCH_PATH
import core.kill_switch as ks

tmpdir = ROOT / "_workspace" / "_ks_probe"
tmpdir.mkdir(parents=True, exist_ok=True)
# don't actually write production kill switch; just check property wiring
src_ks = inspect.getsource(rm.RiskManager.is_halted)
check("is_halted_uses_persistent", "_persistent_breaker_reason" in src_ks)
src_pb = inspect.getsource(rm.RiskManager._persistent_breaker_reason)
check("persistent_checks_kill", "kill_switch" in src_pb.lower() or "entries_halted" in src_pb)

# 7) slice gate counts
try:
    from scripts.s3_vertical_slice import run_s3_slice
    from tempfile import TemporaryDirectory

    with TemporaryDirectory() as td:
        report = run_s3_slice(symbol="BTC", venue="binance", registry_path=P(td) / "a.json")
        keys = set(report["verdict"]["sub_gates"].keys())
        expected = {
            "n_floor",
            "profit_factor",
            "expectancy_ci",
            "folds",
            "pbo",
            "dsr",
            "multiple_comparisons",
            "confirmation_window",
            "paper_edge_v1",
        }
        check("s3_gates", keys == expected, f"got={sorted(keys)}")
except Exception as e:
    check("s3_gates", False, repr(e))

OUT.write_text("\n".join(lines) + "\n", encoding="utf-8")
print(OUT.read_text(encoding="utf-8"))
