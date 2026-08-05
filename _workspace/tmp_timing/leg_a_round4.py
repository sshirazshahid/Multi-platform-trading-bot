import json,os,time,statistics
from collections import Counter
from pathlib import Path
JL=Path(r"D:\Downloads\Trading_Bot\data\mcp_decisions.jsonl")
def tail(n):
    with open(JL,"rb") as f:
        f.seek(0,os.SEEK_END); f.seek(max(0,f.tell()-n)); b=f.read()
    out=[]
    for l in b.split(b"\n")[1:]:
        l=l.strip()
        if not l: continue
        try: out.append(json.loads(l))
        except Exception: pass
    return out
for n in (16384,65536,262144):
    ts=[]
    for _ in range(4):
        t0=time.perf_counter(); o=tail(n); ts.append((time.perf_counter()-t0)*1000)
    span=(time.mktime(time.strptime(o[-1]['ts'][:19],"%Y-%m-%dT%H:%M:%S"))-time.mktime(time.strptime(o[0]['ts'][:19],"%Y-%m-%dT%H:%M:%S")))
    print(f"tail {n//1024:>4}KB -> {len(o):>4} cycles, span {span/60:.1f} min, warm_med {statistics.median(ts[1:]):.2f} ms, types {dict(Counter(x.get('type') for x in o))}")
o=tail(65536)
print("\ntop-level key sets by type:")
for t_ in ("rejection","portfolio","position_monitor"):
    ks=Counter()
    for x in o:
        if x.get("type")==t_: ks.update(x.keys())
    print(" ",t_,dict(ks))
print("\nFULL portfolio cycle (the RICH one):")
p=[x for x in o if x.get("type")=="portfolio"]
print(json.dumps(p[-1],indent=1)[:2600])
print("\nportfolio action key union:")
ak=Counter()
for x in p:
    for a in (x.get("decisions") or {}).get("actions") or []: ak.update(a.keys())
print(" ",dict(ak))
print("\nrejection stream (last 12) — live terminal blocks:")
for x in [y for y in o if y.get("type")=="rejection"][-12:]:
    print(f"   {x['ts'][11:19]}  {x.get('symbol'):<12} {x.get('stage'):<14} {x.get('reason')}")
print("\nrejection reason distribution in 64KB tail:",dict(Counter(x.get("reason") for x in o if x.get("type")=="rejection")))
