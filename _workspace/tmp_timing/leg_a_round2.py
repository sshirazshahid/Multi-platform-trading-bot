"""THROWAWAY round 2 — read-only. Forced-index GROUP BY, rowid tails, features_json shape."""
from __future__ import annotations

import json
import sqlite3
import statistics
import time
from collections import Counter
from pathlib import Path

ROOT = Path(r"D:\Downloads\Trading_Bot")
DB = ROOT / "data" / "warehouse.sqlite"
URI = f"file:{DB.as_posix()}?mode=ro"
RESULTS = []


def connect():
    c = sqlite3.connect(URI, uri=True, timeout=5.0)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA query_only = ON")
    c.execute("PRAGMA busy_timeout = 4000")
    return c


def t(label, sql, params=(), runs=4, post=None):
    times, rows, extra = [], None, None
    for _ in range(runs):
        t0 = time.perf_counter()
        conn = connect()
        try:
            rows = conn.execute(sql, params).fetchall()
            if post:
                extra = post(rows)
        finally:
            conn.close()
        times.append((time.perf_counter() - t0) * 1000)
    med = statistics.median(times[1:])
    RESULTS.append((label, times[0], med))
    print(f"{label:<66} cold={times[0]:>8.1f}ms warm_med={med:>8.1f}ms rows={len(rows)}")
    if extra:
        print("      ", extra)
    return rows


now = time.time()
h1, m5, m15, d1 = now - 3600, now - 300, now - 900, now - 86400

print("=" * 118)
print("EXPLAIN QUERY PLAN — forced-index variants")
print("=" * 118)
conn = connect()
variants = {
    "GROUP BY INDEXED BY ts": "SELECT decision, skip_reason, count(*) FROM candidates INDEXED BY idx_candidates_ts WHERE ts >= ? GROUP BY 1,2",
    "GROUP BY +ORDER BY ts": "SELECT decision, skip_reason, count(*) FROM candidates INDEXED BY idx_candidates_ts WHERE ts >= ? GROUP BY 1,2 ORDER BY 3 DESC",
    "subquery id-bounded": "SELECT decision, skip_reason, count(*) FROM candidates WHERE id > (SELECT max(id)-3000 FROM candidates) GROUP BY 1,2",
    "rowid tail 2000": "SELECT ts, decision, skip_reason FROM candidates ORDER BY rowid DESC LIMIT 2000",
    "ts DESC 2000": "SELECT ts, decision, skip_reason FROM candidates ORDER BY ts DESC LIMIT 2000",
    "de rowid DESC 50": "SELECT * FROM decision_events ORDER BY rowid DESC LIMIT 50",
    "de occurred_at 50": "SELECT * FROM decision_events ORDER BY occurred_at DESC LIMIT 50",
}
for n, s in variants.items():
    p = (h1,) if "?" in s else ()
    print(f"  {n:<26} " + " | ".join(r["detail"] for r in conn.execute("EXPLAIN QUERY PLAN " + s, p)))
conn.close()

print()
print("=" * 118)
print("A. candidates GROUP BY — planner-forced onto idx_candidates_ts")
print("=" * 118)
for lbl, since in (("5 min", m5), ("15 min", m15), ("1 h", h1), ("24 h", d1)):
    t(f"FORCED idx_ts: GROUP BY decision,skip_reason WHERE ts>=now-{lbl}",
      "SELECT decision, skip_reason, count(*) AS c FROM candidates INDEXED BY idx_candidates_ts "
      "WHERE ts >= ? GROUP BY 1,2 ORDER BY c DESC", (since,), runs=4)
t("UNFORCED (planner default) GROUP BY 1h  [the forbidden query]",
  "SELECT decision, skip_reason, count(*) AS c FROM candidates WHERE ts >= ? GROUP BY 1,2", (h1,), runs=3)
t("FORCED idx_ts: GROUP BY decision ONLY, 1h",
  "SELECT decision, count(*) AS c FROM candidates INDEXED BY idx_candidates_ts WHERE ts >= ? GROUP BY 1", (h1,))
t("FORCED idx_ts: GROUP BY decision ONLY, 24h  [covering index]",
  "SELECT decision, count(*) AS c FROM candidates INDEXED BY idx_candidates_ts WHERE ts >= ? GROUP BY 1", (d1,))

print()
print("=" * 118)
print("B. candidates recent slices")
print("=" * 118)
t("ts DESC LIMIT 200 (ts,decision,skip_reason,confidence,symbol)",
  "SELECT ts, symbol, decision, skip_reason, confidence FROM candidates ORDER BY ts DESC LIMIT 200")
t("ts DESC LIMIT 2000 (5 cols)",
  "SELECT ts, symbol, decision, skip_reason, confidence FROM candidates ORDER BY ts DESC LIMIT 2000")
t("rowid DESC LIMIT 2000 (5 cols)",
  "SELECT ts, symbol, decision, skip_reason, confidence FROM candidates ORDER BY rowid DESC LIMIT 2000")
t("ts>=now-3600 ORDER BY ts DESC (whole hour, 5 cols)",
  "SELECT ts, symbol, decision, skip_reason, confidence FROM candidates WHERE ts >= ? ORDER BY ts DESC", (h1,))
t("ts>=now-300 + features_json (5 min, WITH features)",
  "SELECT ts, symbol, decision, skip_reason, confidence, features_json FROM candidates WHERE ts >= ? ORDER BY ts DESC", (m5,))
t("ts DESC LIMIT 60 + features_json",
  "SELECT ts, symbol, side, decision, skip_reason, confidence, features_json FROM candidates ORDER BY ts DESC LIMIT 60")

print()
print("=" * 118)
print("C. decision_events by rowid instead of occurred_at")
print("=" * 118)
t("de: rowid DESC LIMIT 50 (all cols incl payload_json)",
  "SELECT * FROM decision_events ORDER BY rowid DESC LIMIT 50")
t("de: rowid DESC LIMIT 50 (scalars only)",
  "SELECT event_id, decision_id, occurred_at, venue, canonical_symbol, strategy_id, action, side "
  "FROM decision_events ORDER BY rowid DESC LIMIT 50")
t("de: rowid DESC LIMIT 200 (payload_json only)",
  "SELECT occurred_at, action, canonical_symbol, payload_json FROM decision_events ORDER BY rowid DESC LIMIT 200")
t("de: rowid DESC LIMIT 400 + json parse -> reason_codes Counter",
  "SELECT occurred_at, action, canonical_symbol, payload_json FROM decision_events ORDER BY rowid DESC LIMIT 400",
  post=lambda rows: Counter(
      tuple(json.loads(r["payload_json"] or "{}").get("reason_codes") or []) for r in rows).most_common(6))
t("de: GROUP BY action whole table", "SELECT action, count(*) FROM decision_events GROUP BY 1")

print()
print("=" * 118)
print("D. features_json actual shape")
print("=" * 118)
conn = connect()
rows = conn.execute(
    "SELECT ts, symbol, decision, skip_reason, features_json FROM candidates ORDER BY ts DESC LIMIT 300").fetchall()
keys = Counter()
sizes = []
for r in rows:
    try:
        f = json.loads(r["features_json"] or "{}")
    except Exception:  # noqa: BLE001
        continue
    keys.update(f.keys())
    sizes.append(len(r["features_json"] or ""))
print("  n=300 recent rows; features_json bytes avg=%.0f max=%d" % (sum(sizes) / max(1, len(sizes)), max(sizes or [0])))
print("  key frequency:", dict(keys.most_common(40)))
print("\n  three full examples (SKIP / ALLOW):")
shown = set()
for r in rows:
    d = r["decision"]
    if d in shown and len(shown) >= 2:
        continue
    shown.add(d)
    print(f"   [{d}] {r['symbol']}  skip_reason={r['skip_reason']!r}")
    print("      ", json.dumps(json.loads(r["features_json"] or "{}"), indent=None)[:900])
    if len(shown) >= 2 and sum(1 for _ in shown) >= 2:
        break
print("\n  decision_events terminal-stage / reason distribution (last 400 rows):")
tc = Counter()
sc = Counter()
for r in conn.execute("SELECT payload_json FROM decision_events ORDER BY rowid DESC LIMIT 400"):
    try:
        p = json.loads(r["payload_json"] or "{}")
    except Exception:  # noqa: BLE001
        continue
    ctx = p.get("context") or {}
    tc[ctx.get("reason")] += 1
    sc[(ctx.get("terminal_stage"), ctx.get("terminal_outcome"))] += 1
print("   context.reason:", dict(tc.most_common(12)))
print("   (terminal_stage, terminal_outcome):", dict(sc))
conn.close()

print()
print("=" * 118)
print("E. combined 'brain endpoint' simulation — everything one poll would read")
print("=" * 118)


def brain_payload():
    conn = connect()
    try:
        out = {}
        out["counts"] = dict(
            conn.execute(
                "SELECT decision, count(*) AS c FROM candidates INDEXED BY idx_candidates_ts "
                "WHERE ts >= ? GROUP BY 1", (time.time() - 3600,)).fetchall())
        out["recent"] = [
            dict(r) for r in conn.execute(
                "SELECT ts, symbol, side, decision, skip_reason, confidence, features_json "
                "FROM candidates ORDER BY ts DESC LIMIT 60")]
        out["terminal"] = [
            dict(r) for r in conn.execute(
                "SELECT occurred_at, action, canonical_symbol, payload_json "
                "FROM decision_events ORDER BY rowid DESC LIMIT 60")]
    finally:
        conn.close()
    for r in out["recent"]:
        try:
            r["features"] = json.loads(r.pop("features_json") or "{}")
        except Exception:  # noqa: BLE001
            r["features"] = {}
    for r in out["terminal"]:
        try:
            r["payload"] = json.loads(r.pop("payload_json") or "{}")
        except Exception:  # noqa: BLE001
            r["payload"] = {}
    return out


times = []
for _ in range(5):
    t0 = time.perf_counter()
    pay = brain_payload()
    times.append((time.perf_counter() - t0) * 1000)
print("  brain endpoint (1h forced GROUP BY + 60 candidates w/ features + 60 decision_events w/ payload)")
print("   cold=%.1fms  warm_median=%.1fms  all=%s" % (times[0], statistics.median(times[1:]), [round(x, 1) for x in times[1:]]))
print("   serialized JSON bytes:", len(json.dumps(pay, default=str)))

print()
print("=" * 118)
for lbl, cold, med in RESULTS:
    print(f"  {med:>9.1f} ms  (cold {cold:>8.1f})  {lbl}")
