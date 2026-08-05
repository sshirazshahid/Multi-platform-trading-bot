"""Phase D4 — aggregated order_mgmt source for wiring-pin scans."""
from __future__ import annotations

from pathlib import Path


def order_manager_impl_source() -> str:
    """Concatenated order_mgmt implementation source."""
    root = Path(__file__).resolve().parents[1] / "core" / "order_mgmt"
    chunks = []
    for path in sorted(root.glob("*.py")):
        if path.name == "__init__.py":
            continue
        chunks.append(path.read_text(encoding="utf-8"))
    return "\n\n".join(chunks)
