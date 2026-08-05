"""Phase D1: split monolithic config.py into config/ package (one-time migration)."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_PY = ROOT / "config.py"
PKG = ROOT / "config"
TMP = ROOT / "_workspace" / "tmp_timing"
BEFORE = TMP / "config_snapshot_before.json"
AFTER = TMP / "config_snapshot_after.json"

# (module_name, start_line, end_line, extra_header)
# Lines are 1-indexed inclusive from original config.py
SPLITS: list[tuple[str, int, int, str]] = [
    (
        "_env.py",
        17,
        20,
        '"""Environment bootstrap — load_dotenv once."""\nimport os\n\nfrom dotenv import load_dotenv\n\n',
    ),
    (
        "credentials.py",
        25,
        35,
        '"""Exchange API credentials."""\nimport os\n\n',
    ),
    (
        "notifications.py",
        40,
        43,
        '"""Email notification settings."""\nimport os\n\n',
    ),
    (
        "modes.py",
        48,
        106,
        '''"""Operating mode, entry policy, and derived DRY_RUN flags."""
import os

from core.entry_policy import (
    AGGRESSIVE_RESEARCH_PAPER_PROFILE,
    STANDARD_PAPER_PROFILE,
    mode_profile_for,
    normalize_paper_profile,
    parse_allowlist,
)

''',
    ),
    (
        "costs.py",
        112,
        161,
        '"""Fee structure and paper slippage realism."""\nimport os\n\n',
    ),
    (
        "portfolio.py",
        166,
        188,
        '"""Portfolio cycle and trading mode."""\nimport os\n\n',
    ),
    (
        "execution.py",
        190,
        356,
        '"""Execution overrides, maker-only, SL/TP triggers, scalp mode."""\nimport os\n\n',
    ),
    (
        "probes.py",
        374,
        586,
        '''"""Shadow probes and deep-breakout lane configuration."""
import os

from config.modes import _AGGRESSIVE_PAPER_RESEARCH

''',
    ),
    (
        "gates.py",
        597,
        881,
        '''"""Accuracy band, MCP gates, model gate, economic entry gate."""
import os

from config.modes import OPERATING_MODE, PAPER_TRADING_PROFILE

''',
    ),
    (
        "scanner.py",
        886,
        893,
        '"""Legacy scanner settings (reference only)."""\n\n',
    ),
    (
        "universe.py",
        930,
        1107,
        '"""Trading pairs, commodities, analysis-only instruments."""\nimport os\n\n',
    ),
    (
        "risk.py",
        1124,
        1437,
        '"""Risk management tiers, leverage, and short disable flag."""\nimport os\n\n',
    ),
    (
        "filters.py",
        1449,
        1919,
        '"""Trading gates, expectancy filter, hour gates, universe flow."""\nimport os\n\n',
    ),
    (
        "signal.py",
        1920,
        2165,
        '''"""Signal source, machine strategy, TSMOM, execution freshness gates."""
import os

from config.modes import MODE_PROFILE
from config.risk import LEVERAGE_TIERS

''',
    ),
    (
        "strategies.py",
        2170,
        2420,
        '"""Legacy strategy parameters (backtest / reference)."""\nimport os\n\n',
    ),
    (
        "spot.py",
        2425,
        2569,
        '"""Spot portfolio, partial TP, capital allocation."""\nimport os\n\n',
    ),
    (
        "misc.py",
        2571,
        2683,
        '"""Ghost reroute instrumentation, daily loss breaker, age-aware SL."""\nimport os\n\n',
    ),
    (
        "feeds.py",
        2697,
        2792,
        '''"""External data feed configuration for MCP scoring."""
import os

from config.gates import SMART_MONEY_ENTRY_GATE

''',
    ),
]

INIT_IMPORTS = [
    "_env",
    "credentials",
    "notifications",
    "modes",
    "costs",
    "portfolio",
    "execution",
    "probes",
    "gates",
    "scanner",
    "universe",
    "risk",
    "filters",
    "signal",
    "strategies",
    "spot",
    "misc",
    "feeds",
]


def _extract_lines(start: int, end: int) -> str:
    lines = CONFIG_PY.read_text(encoding="utf-8").splitlines()
    return "\n".join(lines[start - 1 : end]) + "\n"


def _write_modules() -> None:
    PKG.mkdir(exist_ok=True)
    for mod, start, end, header in SPLITS:
        body = _extract_lines(start, end)
        (PKG / mod).write_text(header + body, encoding="utf-8")

    init_lines = [
        '"""Central configuration package — permanent facade for `import config`."""',
        "from config._env import *  # noqa: F403",
    ]
    for mod in INIT_IMPORTS[1:]:
        init_lines.append(f"from config.{mod.removesuffix('.py')} import *  # noqa: F403")
    (PKG / "__init__.py").write_text("\n".join(init_lines) + "\n", encoding="utf-8")


def _run_snapshot(path: Path) -> dict[str, str]:
    subprocess.check_call(
        [sys.executable, str(ROOT / "scripts" / "config_snapshot.py"), str(path)],
        cwd=str(ROOT),
    )
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    TMP.mkdir(parents=True, exist_ok=True)
    print("Snapshot BEFORE split...")
    before = _run_snapshot(BEFORE)

    backup = TMP / "config.py.bak"
    shutil.copy2(CONFIG_PY, backup)

    print("Writing config/ package...")
    _write_modules()

    print("Removing config.py (Windows: cannot coexist with config/)...")
    CONFIG_PY.unlink()

    print("Snapshot AFTER split...")
    after = _run_snapshot(AFTER)

    if before != after:
        def _norm_repr(val: str) -> str:
            try:
                obj = eval(val, {"__builtins__": {}})
            except Exception:
                return val
            if isinstance(obj, (set, frozenset)):
                return repr(type(obj)(sorted(obj, key=repr)))
            return val

        diff_keys = [
            k for k in before
            if k not in after or _norm_repr(before[k]) != _norm_repr(after[k])
        ]
        if diff_keys:
            only_before = set(before) - set(after)
            only_after = set(after) - set(before)
            print("EQUIVALENCE FAILED", file=sys.stderr)
            print(f"  only in before: {sorted(only_before)}", file=sys.stderr)
            print(f"  only in after: {sorted(only_after)}", file=sys.stderr)
            print(f"  semantic diffs: {diff_keys[:20]}", file=sys.stderr)
            for k in diff_keys[:5]:
                print(
                    f"  {k}:\n    before={before.get(k)!r}\n    after ={after.get(k)!r}",
                    file=sys.stderr,
                )
            if CONFIG_PY.exists():
                CONFIG_PY.unlink()
            shutil.copy2(backup, CONFIG_PY)
            shutil.rmtree(PKG)
            sys.exit(1)

    print(f"EQUIVALENCE OK — {len(before)} UPPERCASE keys (set/frozenset repr order-normalized)")


if __name__ == "__main__":
    main()
