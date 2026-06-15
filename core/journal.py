"""core/journal.py — daily markdown action journal (CLAUDE.md §4).

The constitution says: "Whenever an action (research, entry, exit, or update) is
taken, generate a structured markdown journal entry at /journal/YYYY-MM-DD.md."
Nothing did this — actions only went to the SQLite warehouse / JSONL. This is the
minimal, append-only, best-effort writer that honors that directive.

Best-effort by design: a journaling failure must NEVER raise into the trade path,
so log_action swallows all errors and returns None on failure.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

# Repo-root /journal/ (parents[1] from core/). Gitignored — these are runtime
# artifacts, not source.
_JOURNAL_DIR = Path(__file__).resolve().parents[1] / "journal"


def log_action(action_type: str, symbol: str, side: str = "", detail: str = "",
               *, ts: datetime | None = None, journal_dir=None):
    """Append one structured markdown line for a trade action to today's journal.

    Returns the file path on success, or None on any failure (best-effort).
    `ts` and `journal_dir` are injectable for tests.
    """
    try:
        d = Path(journal_dir) if journal_dir else _JOURNAL_DIR
        d.mkdir(parents=True, exist_ok=True)
        now = ts or datetime.now(timezone.utc)
        day = now.strftime("%Y-%m-%d")
        line = (f"- {now.strftime('%H:%M:%SZ')} **{action_type}** "
                f"{side} {symbol} {detail}").rstrip()
        fp = d / f"{day}.md"
        is_new = not fp.exists()
        with fp.open("a", encoding="utf-8") as f:
            if is_new:
                f.write(f"# Trade Journal {day}\n\n"
                        f"Structured action log (CLAUDE.md §4). UTC timestamps.\n\n")
            f.write(line + "\n")
        return fp
    except Exception:
        return None
