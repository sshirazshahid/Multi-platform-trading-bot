"""Snapshot UPPERCASE public config attributes for decomposition equivalence checks."""
from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path


def snapshot_config() -> dict[str, str]:
    if "config" in sys.modules:
        importlib.reload(sys.modules["config"])
    else:
        import config  # noqa: F401

    import config as cfg

    out: dict[str, str] = {}
    for name in sorted(dir(cfg)):
        if not name.isupper() or name.startswith("_"):
            continue
        out[name] = repr(getattr(cfg, name))
    return out


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    if str(root) not in sys.path:
        sys.path.insert(0, str(root))

    out_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        "_workspace/tmp_timing/config_snapshot.json"
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    snap = snapshot_config()
    out_path.write_text(json.dumps(snap, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"Wrote {len(snap)} keys to {out_path}")


if __name__ == "__main__":
    main()
