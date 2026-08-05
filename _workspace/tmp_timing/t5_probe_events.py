import sqlite3
import json
from collections import Counter

c = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
cols = [x[1] for x in c.execute("PRAGMA table_info(decision_events)")]
print("cols", cols)
n = c.execute("SELECT COUNT(1) FROM decision_events").fetchone()[0]
print("n", n)
# sample row
row = c.execute("SELECT * FROM decision_events ORDER BY rowid DESC LIMIT 1").fetchone()
print("sample", dict(zip(cols, row)) if row else None)

# reason distribution if reason-like column exists
for col in cols:
    if "reason" in col.lower() or col in ("outcome", "stage", "event_type"):
        print(
            col,
            list(
                c.execute(
                    f"SELECT {col}, COUNT(1) n FROM decision_events "
                    f"GROUP BY 1 ORDER BY n DESC LIMIT 12"
                )
            ),
        )

# look for economic in any text columns
text_cols = [x for x in cols if x in ("reason", "payload", "details", "json", "meta", "event_json")]
print("text_cols candidates", [x for x in cols if "json" in x.lower() or x in ("payload","reason","details")])
