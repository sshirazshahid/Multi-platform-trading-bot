from __future__ import annotations

import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_setup_metadata_command_does_not_run_first_time_wizard():
    completed = subprocess.run(
        [sys.executable, "setup.py", "--name"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "claude-trading-skills"
    assert "FIRST TIME SETUP" not in completed.stdout

