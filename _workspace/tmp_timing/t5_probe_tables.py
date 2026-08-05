import sqlite3

c = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
tables = [
    r[0]
    for r in c.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY 1"
    )
]
print([t for t in tables if "term" in t.lower() or "decision" in t.lower() or "reject" in t.lower()])
for t in tables:
    if "term" in t.lower() or t in ("decision_outcomes", "terminal_decisions", "open_rejections"):
        cols = [x[1] for x in c.execute(f"PRAGMA table_info({t})")]
        n = c.execute(f"SELECT COUNT(1) FROM {t}").fetchone()[0]
        print(t, n, cols)
        if "reason" in cols:
            print(
                " top reasons",
                list(
                    c.execute(
                        f"SELECT reason, COUNT(1) n FROM {t} "
                        f"GROUP BY 1 ORDER BY n DESC LIMIT 15"
                    )
                ),
            )
