import json, sqlite3, time, statistics
from collections import Counter
from pathlib import Path
ROOT=Path(r"D:\Downloads\Trading_Bot"); DB=ROOT/"data"/"warehouse.sqlite"
URI=f"file:{DB.as_posix()}?mode=ro"
def c():
    x=sqlite3.connect(URI,uri=True,timeout=5.0); x.row_factory=sqlite3.Row
    x.execute("PRAGMA query_only = ON"); x.execute("PRAGMA busy_timeout = 4000"); return x
now=time.time(); h1=now-3600
conn=c()
a=sorted(tuple(r) for r in conn.execute("SELECT decision,skip_reason,count(*) FROM candidates INDEXED BY idx_candidates_ts WHERE ts>=? GROUP BY 1,2",(h1,)))
b=sorted(tuple(r) for r in conn.execute("SELECT decision,skip_reason,count(*) FROM candidates WHERE ts>=? GROUP BY 1,2",(h1,)))
print("FORCED == UNFORCED results identical:", a==b, "| groups:",len(a))
print("1h cascade:", dict(Counter({d:0 for d,_,_ in a})), "totals:", Counter())
tot=Counter()
for d,s,n in a: tot[d]+=n
print("1h decision totals:", dict(tot))
print("1h top skip_reasons:", sorted(((n,d,s) for d,s,n in a),reverse=True)[:8])
print()
print("decision_events freshness NOW:")
print("  newest:",conn.execute("SELECT max(occurred_at) FROM decision_events").fetchone()[0], "utc_now:",time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()))
for lbl,s in (("5m",300),("1h",3600),("24h",86400)):
    iso=time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime(now-s))
    print(f"   rows last {lbl}:",conn.execute("SELECT count(*) FROM decision_events WHERE occurred_at>=?",(iso,)).fetchone()[0])
print("  candidates rows last 5m:",conn.execute("SELECT count(*) FROM candidates WHERE ts>=?",(now-300,)).fetchone()[0],
      " last 1h:",conn.execute("SELECT count(*) FROM candidates WHERE ts>=?",(h1,)).fetchone()[0])
# INDEXED BY fail direction
try:
    conn.execute("SELECT count(*) FROM candidates INDEXED BY idx_does_not_exist WHERE ts>=?",(h1,)).fetchone()
except sqlite3.Error as e: print("\nINDEXED BY missing-index FAIL MODE:", type(e).__name__, e)
conn.close()
print()
JL=ROOT/"data"/"mcp_decisions.jsonl"
import os
with open(JL,"rb") as f:
    f.seek(0,os.SEEK_END); f.seek(max(0,f.tell()-65536)); blob=f.read()
lines=[l for l in blob.split(b"\n")[1:] if l.strip()]
objs=[]
for l in lines:
    try: objs.append(json.loads(l))
    except Exception: pass
print("jsonl 64KB ->",len(objs),"cycles; bytes/cycle avg %.0f"%(65536/max(1,len(objs))))
print("cycle span seconds:", round((time.mktime(time.strptime(objs[-1]['ts'][:19],"%Y-%m-%dT%H:%M:%S"))-time.mktime(time.strptime(objs[0]['ts'][:19],"%Y-%m-%dT%H:%M:%S"))),0))
for t_ in ("rejection","portfolio","position_monitor"):
    ex=[o for o in objs if o.get("type")==t_]
    if ex: print(f"  RAW {t_} example:", json.dumps(ex[-1],default=str)[:400])
print("  newest cycle age(s):", round(now-JL.stat().st_mtime,1))
