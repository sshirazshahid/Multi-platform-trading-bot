import sqlite3
from pathlib import Path

c = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
print(
    "econ skips",
    list(
        c.execute(
            "SELECT skip_reason, COUNT(1) n FROM candidates "
            "WHERE skip_reason LIKE 'economic%' GROUP BY 1 ORDER BY n DESC LIMIT 20"
        )
    ),
)
print("total candidates", c.execute("SELECT COUNT(1) FROM candidates").fetchone())
print(
    "recent 7d",
    list(
        c.execute(
            "SELECT decision, skip_reason, COUNT(1) n FROM candidates "
            "WHERE ts > strftime('%s','now') - 7*86400 "
            "GROUP BY 1, 2 ORDER BY n DESC LIMIT 25"
        )
    ),
)
# mcp decisions sample reject reasons if present
p = Path("data/mcp_decisions.jsonl")
print("mcp_decisions exists", p.exists(), "size", p.stat().st_size if p.exists() else 0)
