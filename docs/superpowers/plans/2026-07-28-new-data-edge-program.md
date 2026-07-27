# New-Data Edge Program Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement the approved New-Data Edge Program spec — instrument census, harvester look-ahead repair, ledger bookkeeping, and the hashed Workstream-A prereg — so the microstructure and gamma-expiry screens can run honestly when their data matures.

**Architecture:** All work is research/collection-lane only: a read-only census script, a bar-bounding repair to the microstructure harvester, one spec correction, one ledger row, and one prereg artifact. Nothing touches order flow, risk, or the live bot process. Screens themselves are OUT of this plan (data-gated: Workstream A ≈ 2026-08-24+, flow cells 30d after Task 4 deploys; C2 when 33_'s floor is met).

**Tech Stack:** Python 3.12 (./venv/Scripts/python.exe), sqlite3 stdlib, pytest. No new dependencies.

## Global Constraints

- The trading bot is LIVE (PAPER) from this tree. Never restart/kill it; never touch `.env`, `config.py`, `core/bot_engine.py`, `core/order_manager.py`, `core/mcp_brain.py` (another session is editing those), or any live rail.
- `data/warehouse.sqlite`: tests NEVER touch it — use tmp/in-memory sqlite fixtures. The census opens it `mode=ro` + `PRAGMA query_only=ON` only. The harvester's write path runs only via its schtask, not manually during this plan.
- PUBLIC repo: never stage anything under `data/`; never commit secrets.
- Before EVERY commit: run `git diff --cached --stat` FIRST and verify the staged list is exactly this task's files — the index has carried other sessions' pre-staged files before (2026-07-28: an 11-file sweep-in).
- Never run the full pytest suite (it has clobbered the live paper wallet). Run only the test files named in each task.
- Spec of record: `docs/superpowers/specs/2026-07-28-new-data-edge-program-design.md` (ai-reviewer APPROVED at 2940238, confidence 93). Binding carried-forward items: U1 ledger row; H2 no-price-oscillator constraint; `bar_ts` join hazard until Task 4 lands.
- Discovery post-approval: `_workspace/strategy_pipeline/33_prereg_c2_gamma_expiry.{md,json}` (FROZEN 2026-07-24) already implements Workstream B with the gamma model pinned (Black-76, r=0), daily 08:00 UTC expiry events, 4 Holm-corrected variants, forward-only counting from 2026-07-24. Task 1 corrects the spec to defer to it. 33_ is BTC-only BY FROZEN DESIGN — ETH is a possible FUTURE prereg, never an edit to 33_.

---

### Task 1: Spec correction — Workstream B defers to the frozen 33_ prereg

**Files:**
- Modify: `docs/superpowers/specs/2026-07-28-new-data-edge-program-design.md` (Workstream B section)

**Interfaces:**
- Consumes: `_workspace/strategy_pipeline/33_prereg_c2_gamma_expiry.md` (read-only, existing)
- Produces: corrected spec text later tasks and the ledger row cite

- [ ] **Step 1: Read the frozen prereg** — `_workspace/strategy_pipeline/33_prereg_c2_gamma_expiry.md` and `.json` in full. Confirm: status FROZEN_PREREG, date 2026-07-24, symbol BTCUSDT, daily 08:00 UTC expiry event, 4 variants, Holm multiplicity, expectation lean_NO_GO.

- [ ] **Step 2: Replace the spec's "## Workstream B" section body** with exactly:

```markdown
## Workstream B — C2 gamma-expiry (SUPERSEDED BY EXISTING FROZEN PREREG — discovery 2026-07-28)

Post-approval discovery: `_workspace/strategy_pipeline/33_prereg_c2_gamma_expiry.{md,json}`
(FROZEN_PREREG, 2026-07-24) already implements this workstream, with everything this spec
demanded pinned before any outcome: Black-76 gamma (r=0, IV = `mark_iv`), the 07:30Z AM
snapshot as the observation, daily 08:00 UTC Deribit expiry events on BTCUSDT (binance_usdtm),
4 pre-enumerated variants under Holm correction, stressed costs, and forward-only counting
from 2026-07-24 ("ALL outcome data is post-prereg forward"). Expectation: lean_NO_GO.

Consequences, superseding this spec's earlier statements:
- **This program writes NO new C2 prereg.** 33_ is the artifact of record; editing it is
  forbidden (frozen). The R6 counting-start requirement is SATISFIED by 33_'s own terms.
- **Timeline corrected:** events are DAILY expiries, not monthly cliffs — the ≥30-event
  floor is ~5–6 weeks from 2026-07-24 (conditioned cell needs ≥30 history days for the
  ATM-OI percentile, so the conditioned verdict trails the unconditioned one). The prior
  "~15–30 months" estimate assumed monthly events and is void.
- **ETH is not Step B0 of this workstream.** 33_ is BTC-only by frozen design. Archiving
  ETH chains forward (the harvester already accepts `BTC ETH` args) is a cheap OPTIONAL
  enabler for a FUTURE, separate ETH prereg — owner's choice, no effect on 33_ (Task 6).
- This program's only C2 obligations: the U1 ledger Open-section row citing 33_ (Task 2),
  and letting the screen fire per 33_'s own gates when its floor is met, under the
  both-agree verdict rule.
```

- [ ] **Step 3: Update the header Status line** — append `; Workstream B superseded by frozen 33_ prereg (2026-07-28 discovery)` to the `**Status:**` line.

- [ ] **Step 4: Commit (spec file ONLY — verify index first)**

```bash
git diff --cached --stat   # MUST be empty before staging
git add docs/superpowers/specs/2026-07-28-new-data-edge-program-design.md
git diff --cached --stat   # MUST list exactly 1 file
git commit -m "docs(specs): Workstream B defers to existing frozen 33_ C2 prereg (post-approval discovery)"
```

---

### Task 2: U1 — C2 Open-section ledger row

**Files:**
- Modify: `.claude/skills/refuted-families-ledger/SKILL.md` (the `## Open (INSUFFICIENT_DATA — not refuted, not screenable yet)` table)

**Interfaces:**
- Consumes: 33_ prereg facts from Task 1
- Produces: the ledger row honesty-rail 4 requires; cited by future C2 verdicts

- [ ] **Step 1: Add one row to the Open table**, after the delisting row, exactly:

```markdown
| C2 gamma-expiry reversal (BTC perp, daily 08:00 UTC Deribit expiry; ATM-OI>p90 ∧ GEX<0 conditioning) | FROZEN_PREREG accruing forward-only since 2026-07-24 (`_workspace/strategy_pipeline/33_prereg_c2_gamma_expiry.{md,json}`): Black-76 gamma from self-archived 07:30Z chain snapshots, 4 Holm-corrected variants, expectation lean_NO_GO. Adjudicated INSUFFICIENT_DATA 2026-07-22 (18_final); the 08:00-reversal substitute WITHOUT options conditioning remains forbidden. Screen fires when ≥30 forward events resolve (~5–6 weeks from start; conditioned cell needs ≥30 snapshot-history days). Verdict via strategy-evidence-pipeline under both-agree. | 2026-07-28 |
```

- [ ] **Step 2: Commit (ledger file ONLY — verify index first)**

```bash
git diff --cached --stat   # empty
git add .claude/skills/refuted-families-ledger/SKILL.md
git diff --cached --stat   # exactly 1 file
git commit -m "docs(ledger): add C2 gamma-expiry Open-section row (U1 obligation from ai-reviewer)"
```

---

### Task 3: A0 instrument census script

**Files:**
- Create: `research/microstructure_census.py`
- Test: `tests/test_microstructure_census.py`

**Interfaces:**
- Consumes: `microstructure_features` table schema (columns: symbol, venue, bar_ts, signed_volume, buy_sell_imbalance, trade_count, large_trade_count, notional_total, book_imbalance, spread_bps, depth_top20_bid_usd, depth_top20_ask_usd, window_trades_n, asof_ts, source)
- Produces: `census(conn) -> dict` with keys `per_symbol` (dict symbol -> {bars, first_bar_ts, last_bar_ts, asof_lag_hours_min, asof_lag_hours_max, asof_lag_hours_avg, saturation_rate}), `venues` (list), `total_rows`, `saturated_rows`, `generated_utc`. CLI writes `_workspace/strategy_pipeline/<NN>_a0_census.json` + `.md`.

- [ ] **Step 1: Write the failing test**

```python
"""A0 census: must be able to FAIL on the defects that matter (spec R3)."""
import sqlite3

from research.microstructure_census import census

BAR_4H = 4 * 3600


def _db(rows):
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE TABLE microstructure_features ("
        "symbol TEXT, venue TEXT, bar_ts INTEGER, window_trades_n INTEGER,"
        " asof_ts REAL, source TEXT)"
    )
    conn.executemany(
        "INSERT INTO microstructure_features VALUES (?,?,?,?,?,?)", rows
    )
    return conn


def test_census_reports_lag_saturation_and_venues():
    t0 = 1_785_000_000
    rows = [
        # ETH: asof lands 5h after bar OPEN (=1h after 4h-bar close), saturated
        ("ETH/USDT:USDT", "bybit", t0, 1000, t0 + 5 * 3600, "ccxt.bybit.rest"),
        # ETH second bar: unsaturated window
        ("ETH/USDT:USDT", "bybit", t0 + BAR_4H, 400, t0 + BAR_4H + 4.5 * 3600, "ccxt.bybit.rest"),
    ]
    c = census(_db(rows))
    eth = c["per_symbol"]["ETH/USDT:USDT"]
    assert eth["bars"] == 2
    assert eth["saturation_rate"] == 0.5          # 1 of 2 rows at the 1000 cap
    # lag is measured from bar CLOSE (bar_ts + 4h), in hours
    assert abs(eth["asof_lag_hours_min"] - 0.5) < 1e-9
    assert abs(eth["asof_lag_hours_max"] - 1.0) < 1e-9
    assert c["venues"] == ["bybit"]               # single-venue coverage exposed
    assert c["total_rows"] == 2 and c["saturated_rows"] == 1


def test_census_empty_table_is_explicit_not_zeroes():
    c = census(_db([]))
    assert c["total_rows"] == 0
    assert c["per_symbol"] == {}
    assert c["venues"] == []
```

- [ ] **Step 2: Run to verify it fails** — `./venv/Scripts/python.exe -m pytest tests/test_microstructure_census.py -v` — Expected: FAIL, `ModuleNotFoundError: research.microstructure_census`.

- [ ] **Step 3: Implement `research/microstructure_census.py`**

```python
"""A0 instrument census for the microstructure store (spec R3).

Read-only. Reports the defects a screen must know about — asof-lag vs bar
close, window saturation, venue coverage — never merely row counts.
CLI: ./venv/Scripts/python.exe research/microstructure_census.py
     (opens data/warehouse.sqlite mode=ro; writes the census JSON+md to
      _workspace/strategy_pipeline/ under the next free index)
"""
from __future__ import annotations

import datetime as dt
import json
import sqlite3
from pathlib import Path

BAR_SECONDS = 4 * 3600
SATURATION_N = 1000  # REST fetch_trades cap (module header, microstructure_store)


def census(conn: sqlite3.Connection) -> dict:
    rows = conn.execute(
        "SELECT symbol, venue, bar_ts, window_trades_n, asof_ts "
        "FROM microstructure_features"
    ).fetchall()
    per: dict[str, dict] = {}
    venues: set[str] = set()
    saturated = 0
    for symbol, venue, bar_ts, n, asof_ts in rows:
        venues.add(venue)
        s = per.setdefault(symbol, {"bars": 0, "first_bar_ts": bar_ts,
                                    "last_bar_ts": bar_ts, "_lags": [], "_sat": 0})
        s["bars"] += 1
        s["first_bar_ts"] = min(s["first_bar_ts"], bar_ts)
        s["last_bar_ts"] = max(s["last_bar_ts"], bar_ts)
        if asof_ts is not None:
            s["_lags"].append((asof_ts - (bar_ts + BAR_SECONDS)) / 3600.0)
        if n is not None and n >= SATURATION_N:
            s["_sat"] += 1
            saturated += 1
    for s in per.values():
        lags, sat = s.pop("_lags"), s.pop("_sat")
        s["saturation_rate"] = sat / s["bars"] if s["bars"] else None
        s["asof_lag_hours_min"] = min(lags) if lags else None
        s["asof_lag_hours_max"] = max(lags) if lags else None
        s["asof_lag_hours_avg"] = sum(lags) / len(lags) if lags else None
    return {
        "per_symbol": per,
        "venues": sorted(venues),
        "total_rows": len(rows),
        "saturated_rows": saturated,
        "generated_utc": dt.datetime.now(dt.timezone.utc).isoformat(),
    }


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    conn = sqlite3.connect(
        f"file:{root / 'data' / 'warehouse.sqlite'}?mode=ro", uri=True
    )
    conn.execute("PRAGMA query_only=ON")
    result = census(conn)
    ws = root / "_workspace" / "strategy_pipeline"
    used = {int(p.name.split("_")[0]) for p in ws.iterdir()
            if p.name[:2].isdigit()}
    nn = max(used) + 1
    out = ws / f"{nn}_a0_census.json"
    out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    md = [f"# {nn} — A0 microstructure instrument census",
          f"Generated: {result['generated_utc']}  ",
          f"Rows: {result['total_rows']} | saturated: {result['saturated_rows']} "
          f"| venues: {', '.join(result['venues']) or 'NONE'}", ""]
    for sym, s in sorted(result["per_symbol"].items()):
        md.append(f"- `{sym}`: {s['bars']} bars, saturation "
                  f"{s['saturation_rate']:.0%}, asof-lag "
                  f"{s['asof_lag_hours_min']:.2f}–{s['asof_lag_hours_max']:.2f}h")
    (ws / f"{nn}_a0_census.md").write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"census -> {out}")


if __name__ == "__main__":
    main()
```

- [ ] **Step 4: Run to verify pass** — same pytest command — Expected: 2 passed.
- [ ] **Step 5: Run the CLI once against the live store (read-only)** — `./venv/Scripts/python.exe research/microstructure_census.py` — expect ~43 symbols / ~12+ bars each / bybit-only / saturation ≈ 100%, matching the ai-reviewer's measurements (516 rows on 2026-07-28).
- [ ] **Step 6: Commit** (script + test + the two census artifacts; verify index first as in Task 1 Step 4) — `git commit -m "feat(research): A0 microstructure instrument census (read-only, R3 defect-reporting)"`

---

### Task 4: R2a — harvester bar-bounding + window provenance

**Files:**
- Modify: `core/microstructure_store.py` (schema + `compute_trade_features` + `ensure_schema`)
- Modify: `scripts/harvest_microstructure.py` (pass bar bounds)
- Test: `tests/test_microstructure_store.py` (extend if it exists; create with exactly the tests below otherwise)

**Interfaces:**
- Consumes: ccxt trade dicts (`timestamp` ms, `side`, `price`, `amount`)
- Produces: `compute_trade_features(trades, bar_start_ts=None, bar_end_ts=None) -> Optional[dict]` — when bounds are given, aggregates ONLY trades with `bar_start_ts <= t < bar_end_ts` (epoch seconds) and adds keys `window_start_ts`, `window_end_ts` (min/max fetched-trade epoch seconds over the RAW sample), `bounded` (1). Without bounds: legacy behavior + `bounded` 0. New nullable columns: `window_start_ts REAL`, `window_end_ts REAL`, `bounded INTEGER`.

- [ ] **Step 1: Write the failing tests** (append to the test file)

```python
import sqlite3

from core.microstructure_store import compute_trade_features, ensure_schema


def _t(ts_s, side, price, amount):
    return {"timestamp": ts_s * 1000, "side": side, "price": price,
            "amount": amount}


def test_bar_bounded_aggregation_excludes_out_of_bar_trades():
    bar0, bar1 = 1_785_000_000, 1_785_000_000 + 4 * 3600
    trades = [
        _t(bar0 - 10, "buy", 100.0, 1.0),      # before bar -> excluded
        _t(bar0 + 60, "buy", 100.0, 2.0),      # in bar
        _t(bar1 + 60, "sell", 100.0, 5.0),     # AFTER bar close (the look-ahead) -> excluded
    ]
    f = compute_trade_features(trades, bar_start_ts=bar0, bar_end_ts=bar1)
    assert f["signed_volume"] == 2.0            # only the in-bar buy
    assert f["trade_count"] == 1
    assert f["bounded"] == 1
    assert f["window_trades_n"] == 3            # raw fetched sample, unchanged meaning
    assert f["window_start_ts"] == bar0 - 10    # provenance covers the raw window
    assert f["window_end_ts"] == bar1 + 60


def test_unbounded_call_keeps_legacy_behavior_and_flags_it():
    f = compute_trade_features([_t(1, "buy", 100.0, 1.0)])
    assert f["signed_volume"] == 1.0
    assert f["bounded"] == 0


def test_bounded_with_zero_in_bar_trades_returns_none():
    bar0, bar1 = 1_785_000_000, 1_785_000_000 + 4 * 3600
    assert compute_trade_features(
        [_t(bar1 + 5, "buy", 100.0, 1.0)], bar_start_ts=bar0, bar_end_ts=bar1
    ) is None


def test_ensure_schema_adds_new_columns_to_existing_table():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE microstructure_features "
                 "(symbol TEXT, venue TEXT, bar_ts INTEGER, "
                 "PRIMARY KEY (symbol, venue, bar_ts))")   # legacy shape
    ensure_schema(conn)                                    # must not raise
    cols = {r[1] for r in conn.execute(
        "PRAGMA table_info(microstructure_features)")}
    assert {"window_start_ts", "window_end_ts", "bounded"} <= cols
    ensure_schema(conn)                                    # idempotent
```

- [ ] **Step 2: Run to verify failure** — `./venv/Scripts/python.exe -m pytest tests/test_microstructure_store.py -v` — Expected: new tests FAIL (TypeError on kwargs / missing columns).

- [ ] **Step 3: Implement in `core/microstructure_store.py`**
  1. In `_SCHEMA`'s CREATE TABLE, after `window_trades_n      INTEGER,` add three lines: `window_start_ts      REAL,` / `window_end_ts        REAL,` / `bounded              INTEGER,` — and add `"window_start_ts", "window_end_ts", "bounded",` to `_COLUMNS` immediately before `"asof_ts"`.
  2. In `ensure_schema`, after `conn.executescript(_SCHEMA)`, add the idempotent migration:

```python
    existing = {r[1] for r in conn.execute(
        "PRAGMA table_info(microstructure_features)")}
    for col, typ in (("window_start_ts", "REAL"), ("window_end_ts", "REAL"),
                     ("bounded", "INTEGER")):
        if col not in existing:
            conn.execute(
                f"ALTER TABLE microstructure_features ADD COLUMN {col} {typ}")
```

  3. Change the signature to `def compute_trade_features(trades, bar_start_ts=None, bar_end_ts=None):` and, after the `if not trades: return None` guard, insert:

```python
    ts_all = [t.get("timestamp") for t in trades
              if isinstance(t, dict) and t.get("timestamp") is not None]
    window_start = min(ts_all) / 1000.0 if ts_all else None
    window_end = max(ts_all) / 1000.0 if ts_all else None
    bounded = bar_start_ts is not None and bar_end_ts is not None
    if bounded:
        work = [t for t in trades if isinstance(t, dict)
                and t.get("timestamp") is not None
                and bar_start_ts <= t["timestamp"] / 1000.0 < bar_end_ts]
    else:
        work = trades
```

  Loop over `work` instead of `trades`; keep `"window_trades_n": len(trades)` (raw sample, meaning unchanged) and add to the returned dict: `"window_start_ts": window_start, "window_end_ts": window_end, "bounded": 1 if bounded else 0`. Update the module docstring's HONEST-SEMANTICS block: bounded=1 rows aggregate strictly in-bar trades; bounded=0/NULL rows are the legacy post-close-window shape and any consumer must anchor them at `asof_ts`.
  4. In `scripts/harvest_microstructure.py`, find the `compute_trade_features(trades)` call and pass `bar_start_ts=<bar-open var>, bar_end_ts=<bar-open var> + 4 * 3600` — read the surrounding lines and use the script's actual bar-open variable name (it is the value written as `bar_ts`).

- [ ] **Step 4: Run to verify pass** — same pytest command — Expected: all pass, including any pre-existing tests (legacy path unchanged).
- [ ] **Step 5: Record the epoch** — the flow-cell accrual epoch = this commit's timestamp; note in the commit body: "H1 screenable ≈ this date + 30d".
- [ ] **Step 6: Commit** (the two source files + test file ONLY; verify index) — `git commit -m "fix(microstructure): bar-bound trade aggregation + window provenance (R2a; flow-cell epoch starts at deploy)"`

---

### Task 5: Workstream A prereg artifact (hashed)

**Files:**
- Create: `_workspace/strategy_pipeline/<NN>_prereg_microstructure_screen.md` (NN = next free index — check `ls _workspace/strategy_pipeline | sort -n`; Task 3's census may have consumed one)

**Interfaces:**
- Consumes: Task 3's census artifacts (embedded verbatim); spec section "Workstream A"
- Produces: the frozen grid every future A-screen must implement verbatim; its sha256 recorded in-file and in the commit message

- [ ] **Step 1: Write the prereg** with these exact frozen choices (copy this block into the artifact):

```markdown
# NN — Pre-registration: microstructure screens (Workstream A)

**Status:** FROZEN before any outcome computation · **Spec:** docs/superpowers/specs/2026-07-28-new-data-edge-program-design.md (2940238 + Task-1 correction)
**Census (A0), embedded verbatim below** — bybit-only, single-venue evidence; all
outcome windows anchored at asof_ts for unbounded (bounded=0/NULL) rows.

## Enumerated grid — m is FIXED at 5; Stage-0 attrition never shrinks it

| # | Cell | Signal (frozen) | Outcome (frozen) | Class |
|---|---|---|---|---|
| 1 | H1-decile | bounded signed_volume percentile vs trailing 30d (per symbol), two-tail pooled: enter WITH flow sign at >=p90 / <=p10 | next 1 bar (4h) log return, sign-aligned, after stressed costs | directional |
| 2 | H1-quintile | same, thresholds p80/p20 | same | directional |
| 3 | H2-1bar | book_imbalance z (trailing 30d, ddof=1) >= +2 or <= -2 AND (depth_top20_bid_usd + depth_top20_ask_usd) below its trailing-30d median: enter AGAINST the imbalance sign | next 1 bar from asof_ts, after stressed costs | directional |
| 4 | H2-2bar | same trigger | next 2 bars from asof_ts | directional |
| 5 | H3-depth | (depth_top20_bid_usd + depth_top20_ask_usd) < trailing-30d p10 | REPORT-ONLY ΔEV of vetoing band-lane entries in those bars: ΔEV > 0, MC P(ΔEV>0) >= 0.95, flow retention >= 10% | overlay report |

- Bonferroni denominator: 4 (directional cells). H3 reported separately, never gated in.
- NO cell carries a price-oscillator or price-z leg (binding; H2's z is over BOOK imbalance).
- Stage-0: >=30 triggers per cell counted on DISTINCT bar timestamps; else that cell is
  INSUFFICIENT_DATA. Attrition does not shrink the denominator.
- H1 cells use ONLY bounded=1 rows (post-R2a data); H2/H3 use asof-anchored outcomes.
- OOS split: in-sample = accrual days 1–18 from each cell's data epoch; OOS = day 19 onward.
  Caveat (binding, verdict must quote it): at this depth the 0.55 OOS-WR floor is weakly
  informative.
- Costs: 1.5x taker fee + 2x slippage (5bps/side base) + exit floor; funding charged on
  windows crossing 8h settlements.
- Gates: DSR >= 0.10, OOS-WR >= 0.55 (directional), MC P(total>0) >= 0.95, maxDD p95 <= 0.25.
- Verdict: strategy-evidence-pipeline, both-agree (Fable + Codex). Expectation: NO_GO.
```

- [ ] **Step 2: Embed Task 3's census markdown verbatim** at the artifact's end (copy the `<NN>_a0_census.md` content in full).
- [ ] **Step 3: Hash and pin** — `./venv/Scripts/python.exe -c "import hashlib,sys;print(hashlib.sha256(open(sys.argv[1],'rb').read()).hexdigest())" _workspace/strategy_pipeline/<NN>_prereg_microstructure_screen.md` — then append the hash as the file's last line (`sha256(pre-hash-body) = <hex>`), noting in that line that the hash covers the body above it.
- [ ] **Step 4: Commit** (artifact ONLY; verify index) — commit message includes the hash: `git commit -m "research(prereg): freeze microstructure screen grid m=5 (sha256 <first-12-hex>)"`

---

### Task 6 (OPTIONAL — owner's choice, ask before executing): start ETH chain archival

**Files:**
- Modify: Windows scheduled tasks `TradingBot_DeribitChainSnap_AM` / `_PM` (arguments only — no repo file changes)

**Interfaces:**
- Consumes: `scripts/harvest_deribit_chain_snapshots.py` (already accepts `BTC ETH` args)
- Produces: `data/deribit_chain_snapshots/ETH_YYYY-MM.jsonl` accruing forward — enabler for a FUTURE separate ETH prereg; zero effect on frozen 33_

- [ ] **Step 1: Confirm with the owner** this is wanted (it archives more data forward; it does NOT accelerate 33_).
- [ ] **Step 2: Update both task actions** (PowerShell):

```powershell
foreach ($t in 'TradingBot_DeribitChainSnap_AM','TradingBot_DeribitChainSnap_PM') {
  $task = Get-ScheduledTask -TaskName $t
  $a = New-ScheduledTaskAction -Execute 'D:\Downloads\Trading_Bot\venv\Scripts\python.exe' `
       -Argument 'D:\Downloads\Trading_Bot\scripts\harvest_deribit_chain_snapshots.py BTC ETH' `
       -WorkingDirectory 'D:\Downloads\Trading_Bot'
  Set-ScheduledTask -TaskName $t -Action $a -Trigger $task.Triggers -Settings $task.Settings | Out-Null
  (Get-ScheduledTask -TaskName $t).Actions.Arguments   # verify
}
```

- [ ] **Step 3: One manual run to verify** — `D:\Downloads\Trading_Bot\venv\Scripts\python.exe scripts\harvest_deribit_chain_snapshots.py BTC ETH` then confirm `data/deribit_chain_snapshots/ETH_2026-07.jsonl` exists with >0 lines. (This script's writes to data/ are its designed function — the one sanctioned data/ writer in this plan.)
- [ ] **Step 4: No commit** (nothing in the repo changed). Record the change in the execution notes.

---

## Explicitly OUT of this plan

Running any screen (data-gated: H2/H3 ≈ 2026-08-24; H1 ≈ Task-4-epoch + 30d; C2 per 33_'s floor); any probe; wiring the H3 veto anywhere (owner-signed separate proposal); editing 33_; touching the live bot.
