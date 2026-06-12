"""Regression pins vs the 2026-06 repo-privacy incident (repo is PUBLIC).

Runtime decision/audit artifacts hold real balances, positions, and prompts;
they must stay gitignored. Pins (tasks/plan_provenance_bundle_2026-06-12.md,
E-15 / amendment S4):

  - data/claude_audit/        (raw prompt/response audit logs)
  - data/mcp_decisions.jsonl  (active decision log)
  - data/mcp_decisions.*.jsonl (archive-rename rotation files, E-1)

Read-only: shells out to `git check-ignore`; writes nothing.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git not available")


@pytest.mark.parametrize(
    "relpath",
    [
        "data/claude_audit/calls_2099-01.jsonl",
        "data/mcp_decisions.jsonl",
        "data/mcp_decisions.20990101-000000.jsonl",  # rotation archive pattern
    ],
)
def test_runtime_artifact_is_gitignored(relpath):
    """git check-ignore exits 0 when the path is ignored, 1 when it is not."""
    proc = subprocess.run(
        ["git", "check-ignore", "-q", "--", relpath],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, (
        f"{relpath} is NOT gitignored (git check-ignore exit {proc.returncode}; "
        f"stderr={proc.stderr!r}) — privacy regression: this artifact class leaked "
        f"to the public repo in 2026-06 (see commit 0c9f44d)."
    )
