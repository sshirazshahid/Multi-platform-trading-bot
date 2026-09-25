"""Print the owner's plain-English scoreboard and save it to reports/.

    python scripts/owner_scoreboard.py

Read-only over the bot's data/ files; safe to run while the bot is running.
Also available from TradingBot.bat, option [S].
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.owner_scoreboard import write_scoreboard  # noqa: E402


def main(argv: Optional[list[str]] = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    path = write_scoreboard(ROOT)
    print(path.read_text(encoding="utf-8"))
    print(f"(saved to {path})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
