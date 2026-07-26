import json, urllib.request, time

def get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "audit-probe"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read())

slugs = get("https://defillama-datasets.llama.fi/emissionsProtocolsList")
print("protocolsList count:", len(slugs))

arb = get("https://defillama-datasets.llama.fi/emissions/arbitrum")
ev = arb["metadata"]["events"]
now = time.time()
w0, w1 = 1685577600, 1780272000  # 2023-06-01 .. 2026-06-01
past = [e for e in ev if e["timestamp"] < now]
inwin = [e for e in ev if w0 <= e["timestamp"] <= w1]
cliffs = [e for e in inwin if e.get("unlockType") == "cliff"]
print(f"arbitrum: events total={len(ev)} past={len(past)} in 2023-06..2026-06 window={len(inwin)} cliffs-in-window={len(cliffs)}")
for e in cliffs[:3]:
    print("  ", time.strftime("%Y-%m-%d", time.gmtime(e["timestamp"])), e.get("category"), e.get("unlockType"), str(e.get("noOfTokens"))[:40])
print("arbitrum categories:", json.dumps(arb.get("categories"))[:300])
print("arbitrum gecko_id:", arb.get("gecko_id"))
