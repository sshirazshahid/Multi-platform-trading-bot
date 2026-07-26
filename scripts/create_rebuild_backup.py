"""Create a consistent, non-secret backup before correctness migrations."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.entry_policy import current_git_sha

STATE_FILES = (
    "data/warehouse.sqlite",
    "data/active_strategies.json",
    "data/carry_positions.json",
    "data/positions.json",
    "data/risk_state.json",
    "data/pending_maker_entries.json",
    "data/order_mode_state.json",
    "data/sl_widened.json",
    "data/KILL_SWITCH",
)
ARTIFACT_DIRS = ("data/strategy_specs", "data/models", "models")
CONFIG_HASH_ONLY = (".env", "config.py", "requirements.txt", "pyproject.toml")


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _copy_sqlite(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(source) as source_db, sqlite3.connect(destination) as backup_db:
        source_db.backup(backup_db)


def create_rebuild_backup(
    repo_root: Path | str = ".",
    *,
    output_root: Path | str | None = None,
    now: datetime | None = None,
) -> Path:
    root = Path(repo_root).resolve()
    timestamp = (now or datetime.now(timezone.utc)).astimezone(timezone.utc)
    out_root = Path(output_root).resolve() if output_root else root / "data" / "backups"
    name = f"correctness_rebuild_{timestamp.strftime('%Y%m%dT%H%M%SZ')}"
    destination = out_root / name
    staging = destination
    if destination.exists():
        raise FileExistsError(f"backup destination already exists: {destination}")
    staging.mkdir(parents=True)
    incomplete = staging / "INCOMPLETE"
    incomplete.write_text("backup in progress\n", encoding="utf-8")

    missing: list[str] = []
    files: dict[str, dict] = {}
    try:
        for relative in STATE_FILES:
            source = root / relative
            if not source.exists():
                missing.append(relative)
                continue
            target = staging / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            if relative == "data/warehouse.sqlite":
                _copy_sqlite(source, target)
            else:
                shutil.copy2(source, target)

        for relative in ARTIFACT_DIRS:
            source_dir = root / relative
            if not source_dir.exists():
                missing.append(relative)
                continue
            for source in source_dir.rglob("*"):
                if source.is_file():
                    target = staging / source.relative_to(root)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, target)

        for copied in staging.rglob("*"):
            if copied.is_file() and copied != incomplete:
                relative = copied.relative_to(staging).as_posix()
                files[relative] = {
                    "sha256": _sha256(copied),
                    "size": copied.stat().st_size,
                }

        config_hashes = {}
        for relative in CONFIG_HASH_ONLY:
            source = root / relative
            if source.is_file():
                config_hashes[relative] = {
                    "sha256": _sha256(source),
                    "size": source.stat().st_size,
                }
            else:
                missing.append(relative)

        manifest = {
            "schema_version": 1,
            "created_at": timestamp.isoformat(),
            "git_sha": current_git_sha(root),
            "files": dict(sorted(files.items())),
            "config_hashes": config_hashes,
            "missing": sorted(set(missing)),
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        incomplete.unlink()
        return destination
    except Exception:
        raise


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-root", default=Path(__file__).resolve().parents[1])
    parser.add_argument("--output-root")
    args = parser.parse_args()
    path = create_rebuild_backup(args.repo_root, output_root=args.output_root)
    print(path)


if __name__ == "__main__":
    main()
