import sqlite3
import time

c = sqlite3.connect("file:data/warehouse.sqlite?mode=ro", uri=True)
now = time.time()
for label, mins in [("60m", 60), ("6h", 360), ("24h", 1440), ("7d", 10080)]:
    since = now - mins * 60
    rows = list(
        c.execute(
            "SELECT decision, COUNT(1) n FROM candidates WHERE ts > ? GROUP BY 1 ORDER BY n DESC",
            (since,),
        )
    )
    total = sum(n for _, n in rows)
    allow = sum(n for d, n in rows if str(d).upper() != "SKIP")
    print(label, "total", total, "allowed_non_skip", allow, "by", rows[:8])

since = now - 24 * 3600
print("\n24h top SKIP:")
for r, n in c.execute(
    "SELECT skip_reason, COUNT(1) n FROM candidates WHERE ts > ? AND UPPER(decision)='SKIP' "
    "GROUP BY 1 ORDER BY n DESC LIMIT 20",
    (since,),
):
    print(f"{n:6d} {(r or '')[:160]}")

print("\n24h ALLOW decisions:")
for r in c.execute(
    "SELECT decision, COUNT(1) n FROM candidates WHERE ts > ? AND UPPER(decision)<>'SKIP' GROUP BY 1",
    (since,),
):
    print(r)

print("\n24h ALLOW with skip_reason nonempty:")
print(
    list(
        c.execute(
            "SELECT COUNT(1), SUM(CASE WHEN skip_reason IS NOT NULL AND skip_reason<>'' THEN 1 ELSE 0 END) "
            "FROM candidates WHERE ts > ? AND UPPER(decision)<>'SKIP'",
            (since,),
        )
    )
)
