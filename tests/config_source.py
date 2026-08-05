"""Read config package source after Phase D monolith split."""
from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CONFIG_DIR = ROOT / "config"


def config_source_for_grep() -> str:
    """Concatenated config package source for legacy structural substring tests."""
    parts: list[str] = []
    init = CONFIG_DIR / "__init__.py"
    if init.is_file():
        parts.append(init.read_text(encoding="utf-8"))
    for path in sorted(CONFIG_DIR.glob("*.py")):
        if path.name == "__init__.py":
            continue
        parts.append(path.read_text(encoding="utf-8"))
    return "\n".join(parts)
