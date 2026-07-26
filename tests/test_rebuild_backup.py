from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from scripts.create_rebuild_backup import create_rebuild_backup


def test_backup_is_consistent_and_does_not_copy_env_secrets(tmp_path):
    root = tmp_path / "repo"
    data = root / "data"
    data.mkdir(parents=True)
    with sqlite3.connect(data / "warehouse.sqlite") as connection:
        connection.execute("CREATE TABLE sample (value TEXT)")
        connection.execute("INSERT INTO sample VALUES ('kept')")
    (data / "positions.json").write_text('{"open": []}', encoding="utf-8")
    (root / ".env").write_text("API_SECRET=do-not-copy", encoding="utf-8")
    (root / "config.py").write_text("VALUE = 1\n", encoding="utf-8")

    destination = create_rebuild_backup(
        root,
        output_root=tmp_path / "backups",
        now=datetime(2026, 7, 13, tzinfo=timezone.utc),
    )

    with sqlite3.connect(destination / "data" / "warehouse.sqlite") as connection:
        assert connection.execute("SELECT value FROM sample").fetchone()[0] == "kept"
    assert (destination / "data" / "positions.json").exists()
    assert not (destination / ".env").exists()

    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["config_hashes"][".env"]["sha256"]
    assert "do-not-copy" not in json.dumps(manifest)
    assert manifest["files"]["data/warehouse.sqlite"]["sha256"]
    assert "INCOMPLETE" not in manifest["files"]
