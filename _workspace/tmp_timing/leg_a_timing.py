"""THROWAWAY timing harness for Mission Control 'brain view' design leg A.

STRICTLY read-only: sqlite opened mode=ro + PRAGMA query_only=ON, files opened 'rb'.
Never writes to data/. Not part of the product.
"""
from __future__ import annotations

import json
import os
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:\Downloads\Trading_Bot")
DB = ROOT / "data" / "warehouse.sqlite"
URI = f"file:{DB.as_posix()}?mode=ro"

RESULTS: list[dict] = []


def connect():
    c = sqlite3.connect(URI, uri=True, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only = ON")
    c.execute("PRAGMA busy_timeout = 4000")
    return c


def time_sql(label: str, sql: str, params=(), runs: int = 4, post=None):
    """Full per-request cost: connect + pragmas + query + fetchall + close."""
    times = []
    rows = None
    extra = None
    for _ in range(runs):
        t0 = time.perf_counter()
        conn = connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            if post is not None:
                extra = post(rows)
        finally:
            conn.close()
        times.append((time.perf_counter() - t0) * 1000.0)
    rec = {
        "label": label,
        "cold_ms": round(times[0], 1),
        "warm_median_ms": round(statistics.median(times[1:]), 1),
        "warm_all_ms": [round(t, 1) for t in times[1:]],
        "nrows": len(rows) if rows is not None else None,
        "extra": extra,
    }
    RESULTS.append(rec)
    print(f"{label:<62} cold={rec['cold_ms']:>8.1f}ms  warm_med={rec['warm_median_ms']:>8.1f}ms  rows={rec['nrows']}")
    return rows


def time_py(label: str, fn, runs: int = 4):
    times = []
    out = None
    for _ in range(runs):
        t0 = time.perf_counter()
        out = fn()
        times.append((time.perf_counter() - t0) * 1000.0)
    rec = {
        "label": label,
        "cold_ms": round(times[0], 1),
        "warm_median_ms": round(statistics.median(times[1:]), 1),
        "warm_all_ms": [round(t, 1) for t in times[1:]],
        "nrows": None,
        "extra": None,
    }
    RESULTS.append(rec)
    print(f"{label:<62} cold={rec['cold_ms']:>8.1f}ms  warm_med={rec['warm_median_ms']:>8.1f}ms")
    return out


print("=" * 110)
print("SANITY: schema + storage types")
print("=" * 110)
conn = connect()
print("candidates cols:", [r[1] for r in conn.execute("PRAGMA table_info(candidates)")])
print("decision_events cols:", [r[1] for r in conn.execute("PRAGMA table_info(decision_events)")])
print("candidates typeof(ts):", [tuple(r) for r in conn.execute(
    "SELECT typeof(ts), ts FROM candidates ORDER BY rowid DESC LIMIT 3")])
print("decision_events typeof(occurred_at):", [tuple(r) for r in conn.execute(
    "SELECT typeof(occurred_at), occurred_at FROM decision_events ORDER BY rowid DESC LIMIT 3")])
print("candidates count:", conn.execute("SELECT count(*) FROM candidates").fetchone()[0])
print("decision_events count:", conn.execute("SELECT count(*) FROM decision_events").fetchone()[0])
print()
print("PRAGMA index_list(candidates):", [tuple(r) for r in conn.execute("PRAGMA index_list(candidates)")])
for r in conn.execute("PRAGMA index_list(candidates)"):
    print("   idx", r[1], "->", [tuple(x) for x in conn.execute(f"PRAGMA index_info({r[1]!r})")])
print("PRAGMA index_list(decision_events):", [tuple(r) for r in conn.execute("PRAGMA index_list(decision_events)")])
for r in conn.execute("PRAGMA index_list(decision_events)"):
    print("   idx", r[1], "->", [tuple(x) for x in conn.execute(f"PRAGMA index_info({r[1]!r})")])
print()
print("sqlite_master index DDL:")
for r in conn.execute("SELECT name, tbl_name, sql FROM sqlite_master WHERE type='index' AND tbl_name IN ('candidates','decision_events')"):
    print("  ", tuple(r))
conn.close()

now = time.time()
h1 = now - 3600
m5 = now - 300
d1 = now - 86400

print()
print("=" * 110)
print("EXPLAIN QUERY PLAN")
print("=" * 110)
conn = connect()
plans = {
    "count 1h": ("SELECT count(*) FROM candidates WHERE ts >= ?", (h1,)),
    "groupby 1h": ("SELECT decision, skip_reason, count(*) FROM candidates WHERE ts >= ? GROUP BY 1,2", (h1,)),
    "groupby 5m": ("SELECT decision, skip_reason, count(*) FROM candidates WHERE ts >= ? GROUP BY 1,2", (m5,)),
    "rowid tail 2000": ("SELECT ts, decision, skip_reason, confidence FROM candidates ORDER BY rowid DESC LIMIT 2000", ()),
    "ts DESC 2000": ("SELECT ts, decision, skip_reason, confidence FROM candidates ORDER BY ts DESC LIMIT 2000", ()),
    "de last50 occurred_at": ("SELECT * FROM decision_events ORDER BY occurred_at DESC LIMIT 50", ()),
    "de groupby action": ("SELECT action, count(*) FROM decision_events GROUP BY action", ()),
}
for name, (sql, p) in plans.items():
    rows = conn.execute("EXPLAIN QUERY PLAN " + sql, p).fetchall()
    print(f"  {name:<24} " + " | ".join(r["detail"] for r in rows))
conn.close()

print()
print("=" * 110)
print("1. decision_events")
print("=" * 110)
rows = time_sql("de: last 50 by occurred_at (all cols incl payload_json)",
                "SELECT * FROM decision_events ORDER BY occurred_at DESC LIMIT 50")
time_sql("de: last 50 by occurred_at (scalars only, NO payload_json)",
         "SELECT event_id, decision_id, occurred_at, venue, canonical_symbol, strategy_id, action, side "
         "FROM decision_events ORDER BY occurred_at DESC LIMIT 50")
time_sql("de: GROUP BY action over WHOLE table",
         "SELECT action, count(*) AS c FROM decision_events GROUP BY action ORDER BY c DESC")
time_sql("de: GROUP BY action+strategy_id whole table",
         "SELECT action, strategy_id, count(*) AS c FROM decision_events GROUP BY 1,2 ORDER BY c DESC")

conn = connect()
print("\n  freshness:")
print("   newest occurred_at:", conn.execute("SELECT max(occurred_at) FROM decision_events").fetchone()[0])
print("   oldest occurred_at:", conn.execute("SELECT min(occurred_at) FROM decision_events").fetchone()[0])
for lbl, sec in (("1h", 3600), ("6h", 21600), ("24h", 86400), ("7d", 604800)):
    iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now - sec))
    n = conn.execute("SELECT count(*) FROM decision_events WHERE occurred_at >= ?", (iso,)).fetchone()[0]
    print(f"   rows in last {lbl}: {n}")
print("   action distribution:", [tuple(r) for r in conn.execute(
    "SELECT action, count(*) FROM decision_events GROUP BY 1 ORDER BY 2 DESC")])
print("   payload bytes: avg=%s max=%s" % tuple(conn.execute(
    "SELECT round(avg(length(payload_json)),1), max(length(payload_json)) FROM decision_events").fetchone()))
print("\n  payload_json samples (3 rows, distinct actions):")
seen = set()
for r in conn.execute("SELECT action, occurred_at, canonical_symbol, strategy_id, payload_json FROM decision_events ORDER BY rowid DESC LIMIT 400"):
    if r["action"] in seen:
        continue
    seen.add(r["action"])
    print(f"   --- action={r['action']} at={r['occurred_at']} sym={r['canonical_symbol']} strat={r['strategy_id']}")
    try:
        pl = json.loads(r["payload_json"] or "{}")
    except Exception as e:  # noqa: BLE001
        print("      unparseable:", e, repr(r["payload_json"])[:200])
        continue
    print("      top-level keys:", sorted(pl.keys()))
    print("      pretty (truncated 1800 chars):")
    print("      " + json.dumps(pl, indent=1, default=str)[:1800].replace("\n", "\n      "))
    if len(seen) >= 4:
        break
conn.close()

print()
print("=" * 110)
print("3. candidates — cheapest useful aggregations")
print("=" * 110)
time_sql("cand: count(*) WHERE ts >= now-300 (5 min)", "SELECT count(*) FROM candidates WHERE ts >= ?", (m5,))
time_sql("cand: count(*) WHERE ts >= now-3600 (1 h)", "SELECT count(*) FROM candidates WHERE ts >= ?", (h1,))
time_sql("cand: count(*) WHERE ts >= now-86400 (24 h)", "SELECT count(*) FROM candidates WHERE ts >= ?", (d1,))
time_sql("cand: GROUP BY decision,skip_reason WHERE ts>=now-300 (5 min)",
         "SELECT decision, skip_reason, count(*) AS c FROM candidates WHERE ts >= ? GROUP BY 1,2", (m5,), runs=3)
time_sql("cand: GROUP BY decision ONLY WHERE ts>=now-3600",
         "SELECT decision, count(*) AS c FROM candidates WHERE ts >= ? GROUP BY 1", (h1,), runs=3)
time_sql("cand: SELECT * ORDER BY ts DESC LIMIT 50", "SELECT * FROM candidates ORDER BY ts DESC LIMIT 50", runs=3)
time_sql("cand: rowid tail LIMIT 200 (4 cols)",
         "SELECT ts, decision, skip_reason, confidence FROM candidates ORDER BY rowid DESC LIMIT 200")
time_sql("cand: rowid tail LIMIT 500 (4 cols)",
         "SELECT ts, decision, skip_reason, confidence FROM candidates ORDER BY rowid DESC LIMIT 500")


def _agg(rows):
    return dict(Counter((r["decision"], r["skip_reason"]) for r in rows).most_common(5))


r2000 = time_sql("cand: rowid tail LIMIT 2000 (4 cols) + Counter agg",
                 "SELECT ts, decision, skip_reason, confidence FROM candidates ORDER BY rowid DESC LIMIT 2000",
                 post=_agg)
time_sql("cand: rowid tail LIMIT 5000 (4 cols) + Counter agg",
         "SELECT ts, decision, skip_reason, confidence FROM candidates ORDER BY rowid DESC LIMIT 5000",
         post=_agg, runs=3)
time_sql("cand: rowid tail LIMIT 20000 (4 cols)",
         "SELECT ts, decision, skip_reason, confidence FROM candidates ORDER BY rowid DESC LIMIT 20000", runs=3)
time_sql("cand: rowid tail LIMIT 2000 SELECT * (all cols)",
         "SELECT * FROM candidates ORDER BY rowid DESC LIMIT 2000", runs=3)

# monotonicity of rowid vs ts
conn = connect()
rr = conn.execute("SELECT ts FROM candidates ORDER BY rowid DESC LIMIT 2000").fetchall()
ts = [r["ts"] for r in rr if r["ts"] is not None]
print("\n  rowid-tail 2000 monotonicity: min_ts=%.1f max_ts=%.1f span_hours=%.2f  strictly_desc=%s"
      % (min(ts), max(ts), (max(ts) - min(ts)) / 3600.0, all(ts[i] >= ts[i + 1] for i in range(len(ts) - 1))))
print("  newest candidate ts age (s):", round(now - max(ts), 1))
tsall = conn.execute("SELECT ts FROM candidates ORDER BY rowid DESC LIMIT 20000").fetchall()
tv = [r["ts"] for r in tsall if r["ts"] is not None]
print("  rowid-tail 20000 span_hours=%.2f" % ((max(tv) - min(tv)) / 3600.0))
print("  candidates cols sample row:")
srow = conn.execute("SELECT * FROM candidates ORDER BY rowid DESC LIMIT 1").fetchone()
print("   ", {k: (str(srow[k])[:110]) for k in srow.keys()})
print("\n  top skip_reason last 2000 rowids:")
for (d, s), c in Counter((r["decision"], r["skip_reason"]) for r in
                         conn.execute("SELECT decision, skip_reason FROM candidates ORDER BY rowid DESC LIMIT 2000")).most_common(12):
    print(f"    {c:>5}  {d}  {str(s)[:90]}")
conn.close()

print()
print("=" * 110)
print("2. data/mcp_decisions.jsonl tail read")
print("=" * 110)
JL = ROOT / "data" / "mcp_decisions.jsonl"
print("size bytes:", JL.stat().st_size, " mtime age(s):", round(now - JL.stat().st_mtime, 1))


def tail_parse(nbytes: int):
    with open(JL, "rb") as fh:
        fh.seek(0, os.SEEK_END)
        size = fh.tell()
        fh.seek(max(0, size - nbytes))
        blob = fh.read()
    lines = blob.split(b"\n")
    if len(lines) > 1:
        lines = lines[1:]  # discard partial first line
    out = []
    for ln in lines:
        ln = ln.strip()
        if not ln:
            continue
        try:
            out.append(json.loads(ln))
        except Exception:  # noqa: BLE001, S110 - tolerate half-written last line
            pass
    return out


cyc64 = time_py("jsonl: tail 64KB read + json.loads all lines", lambda: tail_parse(64 * 1024))
time_py("jsonl: tail 16KB read + parse", lambda: tail_parse(16 * 1024))
time_py("jsonl: tail 256KB read + parse", lambda: tail_parse(256 * 1024))
time_py("jsonl: FULL file parse (for contrast)",
        lambda: [json.loads(x) for x in JL.read_bytes().split(b"\n") if x.strip()], runs=2)

print(f"\n  64KB tail -> {len(cyc64)} cycles parsed")
if cyc64:
    print("  cycle ts range:", cyc64[0].get("ts"), "->", cyc64[-1].get("ts"))
    print("  cycle top-level keys:", sorted(cyc64[-1].keys()))
    print("  types seen:", Counter(c.get("type") for c in cyc64))
    print("  decisions-per-cycle:", Counter(len(c.get("decisions") or {}) for c in cyc64).most_common(6))
    last = cyc64[-1]
    dec = last.get("decisions") or {}
    print("  last cycle ts:", last.get("ts"), "n_decisions:", len(dec))
    for i, (k, v) in enumerate(dec.items()):
        print("    ", k, "->", json.dumps(v, default=str)[:260])
        if i >= 5:
            break
    allkeys = Counter()
    acts = Counter()
    srcs = Counter()
    confs = Counter()
    for c in cyc64:
        for v in (c.get("decisions") or {}).values():
            if isinstance(v, dict):
                allkeys.update(v.keys())
                acts[v.get("action")] += 1
                srcs[v.get("source")] += 1
                confs[v.get("confidence")] += 1
    print("  union of per-decision keys:", dict(allkeys))
    print("  actions:", dict(acts))
    print("  sources:", dict(srcs))
    print("  confidence values:", dict(confs.most_common(8)))
    # any richer nested structure?
    rich = [v for c in cyc64 for v in (c.get("decisions") or {}).values()
            if isinstance(v, dict) and any(isinstance(x, (dict, list)) for x in v.values())]
    print("  decisions containing nested dict/list values:", len(rich))
    reasons = Counter(v.get("reason") for c in cyc64 for v in (c.get("decisions") or {}).values() if isinstance(v, dict))
    print("  distinct reason strings (top 8):", reasons.most_common(8))

print()
print("=" * 110)
print("5. mcp_state.json / knowledge_model.json")
print("=" * 110)
for name in ("mcp_state.json", "knowledge_model.json"):
    p = ROOT / "data" / name
    if not p.exists():
        print(f"  {name}: ABSENT")
        continue
    st = p.stat()
    print(f"\n  {name}: {st.st_size} bytes, mtime age {round(now - st.st_mtime, 1)}s")
    obj = time_py(f"json: read+parse {name}", lambda p=p: json.loads(p.read_bytes()))
    if isinstance(obj, dict):
        print("   top-level keys:", list(obj.keys())[:40])
        for k, v in list(obj.items())[:14]:
            if isinstance(v, dict):
                print(f"     {k}: dict[{len(v)}] sample_keys={list(v.keys())[:6]}")
                sk = next(iter(v.values()), None)
                if isinstance(sk, dict):
                    print(f"        sample value: {json.dumps(sk, default=str)[:300]}")
            elif isinstance(v, list):
                print(f"     {k}: list[{len(v)}] first={json.dumps(v[:1], default=str)[:220]}")
            else:
                print(f"     {k}: {str(v)[:160]}")

print()
print("=" * 110)
print("SUMMARY TABLE (warm median)")
print("=" * 110)
for r in RESULTS:
    print(f"  {r['warm_median_ms']:>9.1f} ms   (cold {r['cold_ms']:>8.1f})  {r['label']}")
