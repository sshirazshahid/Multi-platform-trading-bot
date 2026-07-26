import json, urllib.request

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "audit-probe"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

raw = get("https://defillama-datasets.llama.fi/emissions/aptos")
d = json.loads(raw)
print("TOP KEYS:", list(d.keys()))
for k in d.keys():
    v = d[k]
    if isinstance(v, dict):
        print(f"  {k}: dict keys={list(v.keys())[:10]}")
    elif isinstance(v, list):
        print(f"  {k}: list len={len(v)} sample={str(v[:1])[:200]}")
    else:
        print(f"  {k}: {str(v)[:200]}")
meta = d.get("metadata") or d.get("meta") or {}
if meta:
    print("META KEYS:", list(meta.keys()))
    ev = meta.get("events")
    if ev:
        print("EVENTS n =", len(ev), "sample:", json.dumps(ev[:2])[:500])
cats = d.get("categories")
if cats: print("CATEGORIES:", json.dumps(cats)[:400])
# label jumps = cliff detection sanity
dd = d.get("documentedData", {}).get("data", [])
print("documentedData labels:", [s.get("label") for s in dd])
s0 = dd[0]["data"] if dd else []
print("series span:", s0[0]["timestamp"] if s0 else None, "->", s0[-1]["timestamp"] if s0 else None, f"({len(s0)} points)")
