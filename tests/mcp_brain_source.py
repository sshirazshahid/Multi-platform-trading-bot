"""Read MCP scoring implementation source after Phase D monolith split."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCORING_DIR = ROOT / "core" / "scoring"
FACADE = ROOT / "core" / "mcp_brain.py"


def mcp_brain_source_for_grep() -> str:
    """Concatenated scoring package + facade for legacy structural substring tests."""
    order = (
        "constants.py",
        "helpers.py",
        "data_sources.py",
        "entry_score.py",
        "portfolio.py",
        "position_monitor.py",
        "accuracy_tracker.py",
        "brain.py",
    )
    parts = [FACADE.read_text(encoding="utf-8")]
    for name in order:
        path = SCORING_DIR / name
        if path.is_file():
            parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
