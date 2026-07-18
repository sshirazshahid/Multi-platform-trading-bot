# Promotion Funnel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build `scripts/promotion_funnel.py` — an hourly, read-only monitor that tracks every strategy lane's progress toward the frozen promotion gate, diagnoses starvation, alerts on F1 regime thaw, and auto-stages an owner-sign-off dossier when a lane passes.

**Architecture:** One standalone script + one test file. All lane readers are pure functions taking connections/paths as parameters (main() wires production paths). Atomic JSON output, journal append only on state change. Two Windows scheduled tasks (hourly funnel, weekly unlock-calendar backfill). Small additive section in the daily goal report.

**Tech Stack:** Python 3.12 (repo venv), sqlite3 stdlib, pytest. No new dependencies.

## Global Constraints (from spec — every task inherits these)

- **Zero live-path imports:** `scripts/promotion_funnel.py` may import stdlib + `core.promotion_gate` CONSTANTS only. Never engine/order/exchange/config modules. (Task 1 adds the guard test.)
- Warehouse opened strictly `sqlite3.connect("file:...?mode=ro", uri=True)`.
- All file writes atomic: write `<name>.tmp`, then `os.replace`.
- Per-lane fail-open: store errors ⇒ that lane `state="ERROR"`, run continues, exit 0.
- Frozen thresholds (import from `core.promotion_gate`, never redefine): `MIN_OOS_WR=0.55`, `MIN_AUC=0.60`, `MIN_DSR=0.10`, `MAX_PBO=0.5`. Resolved floor: `RESOLVED_FLOOR = 30` (per-lane promotion floor; local constant, documented).
- The funnel never modifies config, git, or bot state. Terminal action = dossier files.
- Run tests with `venv/Scripts/python.exe -m pytest tests/test_promotion_funnel.py -q`.
- Commit after each task: only the files that task names (`git add <exact paths>` — the tree carries unrelated owner modifications; never `git add -A`).

**Ground truth (verified 2026-07-18):**
- `shadow_decisions` cols: `id, ts, model_version, symbol, side, decision, p_win, sim_pnl, sim_r_multiple, agent_id, proposal_id, ..., venue, timeframe, horizon_bars, label_status` (`label_status ∈ RESOLVED|PENDING|UNRESOLVABLE`). `shadow_outcomes` keyed by `proposal_id` with `net_pnl, r_multiple, resolved_ts, exit_reason, ...`.
- Probe agent_ids: `TsmomProbeAgent` (arms split by `timeframe` = `1h`/`4h`), `BreakoutProbeAgent`, `UnlockShortProbeAgent`, listing probe in its own table `shadow_listing_probe` (cols incl. `decision, shortable, base, created_ts`).
- `data/carry_gate_log.jsonl` lines: `{"ts": 1784231062.9, "symbol": "XRP/USDT", "venue": "bitget", "ok": false, "reason": "...", "net_edge_bps": -41.01, ...}`.
- `data/goal_progress.json` has `lanes[]` incl. `{"lane": "current_boot", "closed_trades": N, "wins": N, "wr": x, "net_pnl": x}`.
- `data/unlock_calendar/*.json` per-base files; backfill = `venv/Scripts/python.exe scripts/backfill_unlock_calendar.py --forward-days 60`.
- Spec deviation, deliberate: `core/promotion_gate.PromotionGate` class is ensemble-model-specific (model artifacts, audit log). The funnel imports the module's threshold CONSTANTS and computes lane metrics with small local pure functions; the class is untouched.

## File Structure

- Create `scripts/promotion_funnel.py` — everything (lane readers, classifier, regime watch, gate check, dossier, main). ~450 lines, one responsibility: funnel state.
- Create `tests/test_promotion_funnel.py` — all tests, tmp_path fixtures, synthetic sqlite.
- Modify `scripts/report_goal_progress.py` — one additive function + one call site (funnel section).
- Task 8 registers scheduled tasks (commands, no files).

---

### Task 1: Module skeleton, LaneState, atomic write, import guard

**Files:** Create `scripts/promotion_funnel.py`, `tests/test_promotion_funnel.py`

**Interfaces produced:** `LaneState` dataclass (`lane:str, state:str, resolved:int, wins:int, wr:float|None, floor_progress:str, accrual_rate_7d:float, eta_days:float|None, detail:dict`); `atomic_write_json(path: Path, obj) -> None`; `RESOLVED_FLOOR=30`.

- [ ] **Step 1: Write failing tests**

```python
# tests/test_promotion_funnel.py
"""Promotion funnel tests — synthetic stores only, no production data."""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts import promotion_funnel as pf  # noqa: E402


def test_lane_state_serializes_with_all_fields():
    ls = pf.LaneState(lane="x", state="ACCRUING", resolved=5, wins=3, wr=0.6,
                      floor_progress="5/30", accrual_rate_7d=1.2, eta_days=20.8,
                      detail={"k": "v"})
    d = ls.to_dict()
    assert d["lane"] == "x" and d["floor_progress"] == "5/30" and d["detail"] == {"k": "v"}


def test_atomic_write_json_replaces_not_partial(tmp_path):
    p = tmp_path / "out.json"
    pf.atomic_write_json(p, {"a": 1})
    assert json.loads(p.read_text()) == {"a": 1}
    assert not (tmp_path / "out.json.tmp").exists()


def test_zero_live_path_imports():
    banned = ("core.bot_engine", "core.order_manager", "exchanges", "config", "ccxt")
    loaded = set(sys.modules)
    for b in banned:
        assert not any(m == b or m.startswith(b + ".") for m in loaded), f"funnel pulled {b}"
```

- [ ] **Step 2: Run — expect FAIL** (`ModuleNotFoundError: scripts.promotion_funnel`): `venv/Scripts/python.exe -m pytest tests/test_promotion_funnel.py -q`
- [ ] **Step 3: Write minimal implementation**

```python
# scripts/promotion_funnel.py
"""Promotion funnel — hourly read-only monitor of every strategy lane's progress
toward the frozen promotion gate. Spec: docs/superpowers/specs/2026-07-18-promotion-funnel-design.md
HARD BOUNDARY: imports stdlib + core.promotion_gate constants only. Never engine/
order/exchange/config — enforced by tests/test_promotion_funnel.py::test_zero_live_path_imports.
"""
from __future__ import annotations

import json
import os
import sqlite3
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RESOLVED_FLOOR = 30  # per-lane promotion floor (>=30 resolved, owner-signed)
FUNNEL_JSON = ROOT / "data" / "promotion_funnel.json"
DOSSIER_DIR = ROOT / "reports" / "promotion_dossiers"


@dataclass
class LaneState:
    lane: str
    state: str  # ACCRUING|STARVED|GATE_READY|STAGED|IDLE|ERROR
    resolved: int = 0
    wins: int = 0
    wr: float | None = None
    floor_progress: str = "0/30"
    accrual_rate_7d: float = 0.0
    eta_days: float | None = None
    detail: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "lane": self.lane, "state": self.state, "resolved": self.resolved,
            "wins": self.wins, "wr": self.wr, "floor_progress": self.floor_progress,
            "accrual_rate_7d": self.accrual_rate_7d, "eta_days": self.eta_days,
            "detail": self.detail,
        }


def atomic_write_json(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, default=str), encoding="utf-8")
    os.replace(tmp, path)
```

- [ ] **Step 4: Run — expect 3 PASS.**
- [ ] **Step 5: Commit:** `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py && git commit -m "feat(funnel): skeleton — LaneState, atomic write, zero-live-path guard"`

---

### Task 2: Probe-lane tracker (TSMOM ×2 arms, breakout, unlock)

**Files:** Modify `scripts/promotion_funnel.py`, `tests/test_promotion_funnel.py`

**Interfaces:** Consumes `LaneState`, `RESOLVED_FLOOR`. Produces `PROBE_LANES: dict[str, tuple[str, str|None]]` and `probe_lane_states(conn: sqlite3.Connection, now: float) -> list[LaneState]`.

- [ ] **Step 1: Write failing tests** (append to test file)

```python
import sqlite3
import time


def _mk_shadow_db(tmp_path):
    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY, ts REAL, agent_id TEXT,"
              " timeframe TEXT, proposal_id TEXT, label_status TEXT)")
    c.execute("CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)")
    return db, c


def test_probe_lane_counts_resolved_and_wins_by_arm(tmp_path):
    db, c = _mk_shadow_db(tmp_path)
    now = time.time()
    for i in range(4):  # 4 resolved 1h tsmom, 3 wins, spread over last 7d (rate>0)
        c.execute("INSERT INTO shadow_decisions (ts, agent_id, timeframe, proposal_id, label_status)"
                  " VALUES (?,?,?,?,?)", (now - i * 86400, "TsmomProbeAgent", "1h", f"p{i}", "RESOLVED"))
        c.execute("INSERT INTO shadow_outcomes VALUES (?,?,?)",
                  (f"p{i}", 1.0 if i < 3 else -1.0, now - i * 86400))
    c.execute("INSERT INTO shadow_decisions (ts, agent_id, timeframe, proposal_id, label_status)"
              " VALUES (?,?,?,?,?)", (now, "TsmomProbeAgent", "4h", "p9", "PENDING"))
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lanes = {l.lane: l for l in pf.probe_lane_states(ro, now)}
    l1 = lanes["tsmom_20d_1h"]
    assert (l1.resolved, l1.wins, l1.state) == (4, 3, "ACCRUING")
    assert l1.floor_progress == "4/30" and l1.accrual_rate_7d > 0 and l1.eta_days is not None
    assert lanes["tsmom_20d_4h"].resolved == 0
    assert lanes["breakout_60d"].state == "IDLE"  # zero proposals ever


def test_probe_lane_gate_ready_at_floor(tmp_path):
    db, c = _mk_shadow_db(tmp_path)
    now = time.time()
    for i in range(30):
        c.execute("INSERT INTO shadow_decisions (ts, agent_id, timeframe, proposal_id, label_status)"
                  " VALUES (?,?,?,?,?)", (now - i * 3600, "BreakoutProbeAgent", "4h", f"b{i}", "RESOLVED"))
        c.execute("INSERT INTO shadow_outcomes VALUES (?,?,?)", (f"b{i}", 1.0, now - i * 3600))
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    lanes = {l.lane: l for l in pf.probe_lane_states(ro, now)}
    assert lanes["breakout_60d"].state == "GATE_READY"


def test_probe_lane_error_isolated():
    ro = sqlite3.connect(":memory:")  # empty db: tables missing -> per-lane ERROR
    lanes = pf.probe_lane_states(ro, time.time())
    assert all(l.state == "ERROR" for l in lanes) and len(lanes) == len(pf.PROBE_LANES)
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: PROBE_LANES`).
- [ ] **Step 3: Write minimal implementation** (append to module)

```python
PROBE_LANES: dict[str, tuple[str, str | None]] = {
    "tsmom_20d_1h": ("TsmomProbeAgent", "1h"),
    "tsmom_20d_4h": ("TsmomProbeAgent", "4h"),
    "breakout_60d": ("BreakoutProbeAgent", None),
    "unlock_short": ("UnlockShortProbeAgent", None),
}


def _accrual(resolved_ts: list[float], now: float) -> tuple[float, float | None, int]:
    """(rate_per_day over 7d, eta_days to floor, resolved_count)."""
    n = len(resolved_ts)
    recent = [t for t in resolved_ts if t >= now - 7 * 86400]
    rate = len(recent) / 7.0
    remaining = max(0, RESOLVED_FLOOR - n)
    eta = (remaining / rate) if rate > 0 and remaining > 0 else (0.0 if remaining == 0 else None)
    return rate, eta, n


def probe_lane_states(conn: sqlite3.Connection, now: float) -> list[LaneState]:
    out: list[LaneState] = []
    for lane, (agent, timeframe) in PROBE_LANES.items():
        try:
            tf_sql = " AND d.timeframe = ?" if timeframe else ""
            args: tuple = (agent, timeframe) if timeframe else (agent,)
            rows = conn.execute(
                "SELECT o.net_pnl, o.resolved_ts FROM shadow_decisions d"
                " JOIN shadow_outcomes o ON o.proposal_id = d.proposal_id"
                f" WHERE d.agent_id = ? AND d.label_status = 'RESOLVED'{tf_sql}", args).fetchall()
            n_prop = conn.execute(
                f"SELECT COUNT(*) FROM shadow_decisions d WHERE d.agent_id = ?{tf_sql}",
                args).fetchone()[0]
            wins = sum(1 for pnl, _ in rows if (pnl or 0) > 0)
            rate, eta, n = _accrual([t for _, t in rows if t], now)
            state = ("IDLE" if n_prop == 0 else
                     "GATE_READY" if n >= RESOLVED_FLOOR else "ACCRUING")
            out.append(LaneState(lane, state, n, wins, (wins / n) if n else None,
                                 f"{n}/{RESOLVED_FLOOR}", round(rate, 3),
                                 round(eta, 1) if eta is not None else None,
                                 {"proposals": n_prop, "agent_id": agent}))
        except sqlite3.Error as exc:
            out.append(LaneState(lane, "ERROR", detail={"error": str(exc)}))
    return out
```

- [ ] **Step 4: Run — expect all PASS.**
- [ ] **Step 5: Commit:** `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py && git commit -m "feat(funnel): probe-lane tracker with per-arm accrual + ETA"`

---

### Task 3: Listing lane + tokenized/crypto classifier + unlock-calendar starvation

**Files:** Modify `scripts/promotion_funnel.py`, `tests/test_promotion_funnel.py`

**Interfaces:** Produces `classify_base(base: str) -> str` (`"tokenized"|"crypto"`), `listing_lane_state(conn, now) -> LaneState`, `unlock_calendar_coverage(cal_dir: Path, now: float) -> dict` (keys: `forward_days: float, starved: bool, backfill_cmd: str`).

- [ ] **Step 1: Write failing tests**

```python
def test_classifier_tokenized_vs_crypto():
    assert pf.classify_base("TZA") == "tokenized"     # leveraged-ETF explicit list
    assert pf.classify_base("SOXS") == "tokenized"
    assert pf.classify_base("NVDA") == "tokenized"    # static stock set
    assert pf.classify_base("XAU") == "tokenized"     # commodity set
    assert pf.classify_base("PEPE") == "crypto"


def test_listing_lane_starved_when_all_recent_tokenized(tmp_path):
    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, decision TEXT,"
              " shortable INTEGER, created_ts REAL)")
    now = time.time()
    for i, b in enumerate(["TZA", "SOXS", "NVDA"]):
        c.execute("INSERT INTO shadow_listing_probe VALUES (?,?,?,?,?)",
                  (f"ls{i}", b, "SKIP_UNSHORTABLE", 0, now - i * 86400))
    c.commit()
    ro = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    ls = pf.listing_lane_state(ro, now)
    assert ls.state == "STARVED"
    assert ls.detail["crypto_native_listings_30d"] == 0
    assert ls.detail["tokenized_listings_30d"] == 3


def test_unlock_calendar_coverage_flags_short_horizon(tmp_path):
    cal = tmp_path / "unlock_calendar"
    cal.mkdir()
    now = time.time()
    (cal / "AAA.json").write_text(json.dumps(
        {"events": [{"ts": now + 10 * 86400}]}))          # only 10 days forward
    cov = pf.unlock_calendar_coverage(cal, now)
    assert cov["forward_days"] < 30 and cov["starved"] is True
    assert "--forward-days 60" in cov["backfill_cmd"]
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: classify_base`).
- [ ] **Step 3: Write minimal implementation**

```python
# Static copies from core/pair_discovery.py (2026-07-18) — provenance comment per
# spec: keeps the funnel's import surface at zero beyond promotion_gate constants.
_STOCK_BASES = {"AAPL", "TSLA", "GOOG", "GOOGL", "AMZN", "MSFT", "META", "NVDA",
                "NFLX", "AMD", "COIN", "MSTR", "GME", "AMC", "PLTR", "BABA", "TSM",
                "INTC", "PYPL", "SQ", "SHOP", "UBER", "ABNB", "SNAP", "SPY", "QQQ"}
_COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL", "BRENT", "UKOIL", "USOIL", "GOLD",
                    "SILVER", "COPPER", "NATGAS"}
# Leveraged/inverse-ETF tickers seen in venue tokenized-equity listings
# (TZA/SOXS observed live 2026-07-18; extend list as new ones appear).
_ETF_EXPLICIT = {"TZA", "SOXS", "SOXL", "TQQQ", "SQQQ", "UVXY", "SPXS", "SPXL",
                 "LABU", "LABD"}


def classify_base(base: str) -> str:
    b = (base or "").upper()
    if b in _STOCK_BASES or b in _COMMODITY_BASES or b in _ETF_EXPLICIT:
        return "tokenized"
    return "crypto"


def listing_lane_state(conn: sqlite3.Connection, now: float) -> LaneState:
    try:
        rows = conn.execute(
            "SELECT base, decision, created_ts FROM shadow_listing_probe"
            " WHERE created_ts >= ?", (now - 30 * 86400,)).fetchall()
        resolved = conn.execute(
            "SELECT COUNT(*) FROM shadow_listing_probe WHERE decision NOT LIKE 'SKIP%'"
        ).fetchone()[0]
        native = sum(1 for b, _, _ in rows if classify_base(b) == "crypto")
        tokenized = len(rows) - native
        state = ("STARVED" if rows and native == 0 else
                 "GATE_READY" if resolved >= RESOLVED_FLOOR else
                 "ACCRUING" if resolved else "STARVED" if rows else "IDLE")
        return LaneState("listing_short", state, resolved, 0, None,
                         f"{resolved}/{RESOLVED_FLOOR}", 0.0, None,
                         {"crypto_native_listings_30d": native,
                          "tokenized_listings_30d": tokenized,
                          "note": "starved while venue listing flow is tokenized-equity"})
    except sqlite3.Error as exc:
        return LaneState("listing_short", "ERROR", detail={"error": str(exc)})


def unlock_calendar_coverage(cal_dir: Path, now: float) -> dict:
    horizon = 0.0
    try:
        for f in cal_dir.glob("*.json"):
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            events = data.get("events", data if isinstance(data, list) else [])
            for ev in events:
                if not isinstance(ev, dict):
                    continue
                ts = float(ev.get("ts") or ev.get("timestamp") or 0)
                horizon = max(horizon, ts)
    except OSError:
        pass
    fwd = max(0.0, (horizon - now) / 86400.0)
    return {"forward_days": round(fwd, 1), "starved": fwd < 30,
            "backfill_cmd": ("venv/Scripts/python.exe scripts/backfill_unlock_calendar.py"
                             " --forward-days 60")}
```

- [ ] **Step 4: Run — expect PASS.** (If the real calendar JSON shape differs from `{"events":[{"ts":...}]}`, read ONE file in `data/unlock_calendar/` and adjust `unlock_calendar_coverage`'s field names + the test fixture to match — the <30-forward-days starvation rule itself is invariant.)
- [ ] **Step 5: Commit:** `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py && git commit -m "feat(funnel): listing starvation classifier + unlock-calendar coverage"`

---

### Task 4: F1RegimeWatch + band-cohort lane

**Files:** Modify `scripts/promotion_funnel.py`, `tests/test_promotion_funnel.py`

**Interfaces:** Produces `f1_lane_state(gate_log: Path, now: float) -> LaneState` (alert = same venue+symbol `net_edge_bps > 0` on ≥3 consecutive appearances within 48h window; `detail` carries `alert: bool, top_edges: list, entries_48h: int`), `band_lane_state(goal_json: Path) -> LaneState`.

- [ ] **Step 1: Write failing tests**

```python
def _write_gate_log(p, entries):
    p.write_text("\n".join(json.dumps(e) for e in entries), encoding="utf-8")


def test_f1_alert_needs_three_consecutive_positives(tmp_path):
    log = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    mk = lambda i, edge: {"ts": now - 600 + i * 60, "symbol": "XRP/USDT",
                          "venue": "bitget", "net_edge_bps": edge}
    _write_gate_log(log, [mk(0, 5.0), mk(1, 6.0)])          # only 2 consecutive
    assert pf.f1_lane_state(log, now).detail["alert"] is False
    _write_gate_log(log, [mk(0, 5.0), mk(1, 6.0), mk(2, 7.0)])
    st = pf.f1_lane_state(log, now)
    assert st.detail["alert"] is True and st.state == "ACCRUING"
    assert st.detail["top_edges"][0]["symbol"] == "XRP/USDT"


def test_f1_idle_and_error_paths(tmp_path):
    log = tmp_path / "carry_gate_log.jsonl"
    now = time.time()
    _write_gate_log(log, [{"ts": now, "symbol": "XRP/USDT", "venue": "bitget",
                           "net_edge_bps": -20.0}])
    st = pf.f1_lane_state(log, now)
    assert st.state == "IDLE" and st.detail["alert"] is False
    assert pf.f1_lane_state(tmp_path / "missing.jsonl", now).state == "ERROR"


def test_band_lane_reads_current_boot(tmp_path):
    gp = tmp_path / "goal_progress.json"
    gp.write_text(json.dumps({"lanes": [
        {"lane": "current_boot", "closed_trades": 3, "wins": 2, "wr": 0.667,
         "net_pnl": 1.5}]}))
    st = pf.band_lane_state(gp)
    assert (st.resolved, st.wins, st.wr) == (3, 2, 0.667)
    assert st.state == "ACCRUING" and st.detail["net_pnl"] == 1.5
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write minimal implementation**

```python
def f1_lane_state(gate_log: Path, now: float) -> LaneState:
    try:
        lines = gate_log.read_text(encoding="utf-8", errors="replace").strip().splitlines()
    except OSError as exc:
        return LaneState("f1_carry", "ERROR", detail={"error": str(exc)})
    recent = []
    for ln in lines[-2000:]:
        try:
            e = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if float(e.get("ts", 0)) >= now - 48 * 3600:
            recent.append(e)
    streaks: dict[tuple, int] = {}
    best: dict[tuple, float] = {}
    alert = False
    for e in recent:  # log is append-ordered; consecutive = successive entries per key
        key = (e.get("venue"), e.get("symbol"))
        edge = float(e.get("net_edge_bps") or 0)
        best[key] = max(best.get(key, -1e9), edge)
        streaks[key] = streaks.get(key, 0) + 1 if edge > 0 else 0
        if streaks[key] >= 3:
            alert = True
    top = sorted(({"venue": k[0], "symbol": k[1], "best_edge_bps": round(v, 2)}
                  for k, v in best.items()), key=lambda d: -d["best_edge_bps"])[:5]
    state = "ACCRUING" if alert else ("IDLE" if recent else "STARVED")
    return LaneState("f1_carry", state, detail={
        "alert": alert, "top_edges": top, "entries_48h": len(recent),
        "note": "alert = net_edge_bps>0 on >=3 consecutive gate evals (hysteresis)"})


def band_lane_state(goal_json: Path) -> LaneState:
    try:
        data = json.loads(goal_json.read_text(encoding="utf-8"))
        boot = next(l for l in data.get("lanes", []) if l.get("lane") == "current_boot")
    except (OSError, json.JSONDecodeError, StopIteration) as exc:
        return LaneState("band_cohort", "ERROR", detail={"error": str(exc)})
    n, w = int(boot.get("closed_trades") or 0), int(boot.get("wins") or 0)
    return LaneState("band_cohort", "ACCRUING" if n else "IDLE", n, w,
                     boot.get("wr"), f"{n}/{RESOLVED_FLOOR}",
                     detail={"net_pnl": boot.get("net_pnl"),
                             "note": "tuning protocol owned by band program, funnel reports only"})
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit:** `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py && git commit -m "feat(funnel): F1 regime watch with hysteresis + band cohort lane"`

---

### Task 5: GateRunner (frozen thresholds on lane outcomes)

**Files:** Modify `scripts/promotion_funnel.py`, `tests/test_promotion_funnel.py`

**Interfaces:** Produces `run_gate(outcomes: list[dict], market: str = "futures") -> dict` where each outcome dict has `net_pnl: float, p_win: float|None`. Returns `{"passed": bool, "gates": {name: {"value", "threshold", "ok"}}}`. Imports `MIN_OOS_WR, MIN_AUC, MIN_DSR, MAX_PBO` from `core.promotion_gate` (constants only — verify this import does not pull banned modules; if `core.promotion_gate` transitively imports config/engine code, copy the four constants with a provenance comment instead and note it in the commit message).

- [ ] **Step 1: Write failing tests**

```python
def _outcomes(n_win, n_loss, p_hi=0.7, p_lo=0.3):
    """Wins carry high p_win, losses low — a discriminating score (AUC ~1)."""
    return ([{"net_pnl": 1.0, "p_win": p_hi}] * n_win
            + [{"net_pnl": -1.0, "p_win": p_lo}] * n_loss)


def test_gate_passes_on_strong_discriminating_lane():
    res = pf.run_gate(_outcomes(24, 6))   # WR 0.80, AUC 1.0
    assert res["passed"] is True
    assert res["gates"]["oos_wr"]["ok"] and res["gates"]["auc"]["ok"]


def test_gate_fails_on_nondiscriminating_score():
    res = pf.run_gate(_outcomes(24, 6, p_hi=0.5, p_lo=0.5))  # AUC 0.5
    assert res["passed"] is False and res["gates"]["auc"]["ok"] is False


def test_gate_fails_below_wr_floor():
    res = pf.run_gate(_outcomes(15, 15))  # WR 0.50 < 0.55
    assert res["passed"] is False and res["gates"]["oos_wr"]["ok"] is False
```

- [ ] **Step 2: Run — expect FAIL** (`AttributeError: run_gate`).
- [ ] **Step 3: Write minimal implementation**

```python
import math

from core.promotion_gate import MAX_PBO, MIN_AUC, MIN_DSR, MIN_OOS_WR  # constants only


def _auc(scores_pos: list[float], scores_neg: list[float]) -> float:
    if not scores_pos or not scores_neg:
        return 0.5
    wins = sum(1.0 if p > n else 0.5 if p == n else 0.0
               for p in scores_pos for n in scores_neg)
    return wins / (len(scores_pos) * len(scores_neg))


def _dsr(pnls: list[float]) -> float:
    """DSR proxy: sharpe*sqrt(n) through normal CDF vs zero skill
    (sr_var = 1/n convention — matches the 2026-06-06 gate fix)."""
    n = len(pnls)
    if n < 2:
        return 0.0
    mu = sum(pnls) / n
    sd = (sum((x - mu) ** 2 for x in pnls) / (n - 1)) ** 0.5
    if sd == 0:
        return 1.0 if mu > 0 else 0.0
    z = (mu / sd) * math.sqrt(n)
    return 0.5 * (1.0 + math.erf(z / math.sqrt(2)))


def run_gate(outcomes: list[dict], market: str = "futures") -> dict:
    n = len(outcomes)
    pnls = [float(o.get("net_pnl") or 0) for o in outcomes]
    wins = [o for o in outcomes if float(o.get("net_pnl") or 0) > 0]
    wr = len(wins) / n if n else 0.0
    pos = [float(o.get("p_win")) for o in wins if o.get("p_win") is not None]
    neg = [float(o.get("p_win")) for o in outcomes
           if float(o.get("net_pnl") or 0) <= 0 and o.get("p_win") is not None]
    auc = _auc(pos, neg)
    dsr = _dsr(pnls)
    gates = {
        "n_resolved": {"value": n, "threshold": RESOLVED_FLOOR, "ok": n >= RESOLVED_FLOOR},
        "oos_wr": {"value": round(wr, 4), "threshold": MIN_OOS_WR, "ok": wr >= MIN_OOS_WR},
        "auc": {"value": round(auc, 4), "threshold": MIN_AUC, "ok": auc >= MIN_AUC},
        "dsr": {"value": round(dsr, 4), "threshold": MIN_DSR, "ok": dsr >= MIN_DSR},
        # PBO needs fold structure a single forward stream lacks; informational —
        # the dossier flags it for the owner's sign-off review.
        "pbo": {"value": None, "threshold": MAX_PBO, "ok": True,
                "note": "not computable on single forward stream"},
    }
    return {"passed": all(g["ok"] for g in gates.values()), "gates": gates}
```

- [ ] **Step 4: Run — expect PASS.** Also re-run `test_zero_live_path_imports` — if the `core.promotion_gate` import dragged in banned modules, apply the constants-copy fallback from the Interfaces note.
- [ ] **Step 5: Commit:** `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py && git commit -m "feat(funnel): gate runner on frozen thresholds"`

---

### Task 6: DossierBuilder (idempotent, never applies)

**Files:** Modify `scripts/promotion_funnel.py`, `tests/test_promotion_funnel.py`

**Interfaces:** Produces `build_dossier(lane: LaneState, gate_result: dict, outcomes: list[dict], out_root: Path, today: str) -> Path | None` — returns dossier dir; `None` if it already exists (idempotent).

- [ ] **Step 1: Write failing test**

```python
def test_dossier_written_complete_and_idempotent(tmp_path):
    lane = pf.LaneState(lane="tsmom_20d_1h", state="GATE_READY", resolved=30, wins=20,
                        wr=0.667, floor_progress="30/30")
    gate = {"passed": True, "gates": {"oos_wr": {"value": 0.667, "threshold": 0.55, "ok": True}}}
    outcomes = [{"net_pnl": 1.0, "p_win": 0.7}] * 30
    d = pf.build_dossier(lane, gate, outcomes, tmp_path, "20260718")
    assert d is not None and (d / "evidence.md").exists() and (d / "evidence.json").exists()
    assert (d / "proposed_change.patch").exists()
    md = (d / "evidence.md").read_text(encoding="utf-8")
    assert "owner sign-off" in md.lower() and "0.667" in md
    assert pf.build_dossier(lane, gate, outcomes, tmp_path, "20260718") is None  # idempotent
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write minimal implementation**

```python
def build_dossier(lane: LaneState, gate_result: dict, outcomes: list[dict],
                  out_root: Path, today: str) -> Path | None:
    d = out_root / f"{lane.lane}_{today}"
    if d.exists():
        return None
    d.mkdir(parents=True)
    ev = {"lane": lane.to_dict(), "gate": gate_result, "n_outcomes": len(outcomes),
          "generated_utc": datetime.now(timezone.utc).isoformat()}
    atomic_write_json(d / "evidence.json", ev)
    rows = "\n".join(f"| {g} | {v['value']} | {v['threshold']} | {'PASS' if v['ok'] else 'FAIL'} |"
                     for g, v in gate_result["gates"].items())
    (d / "evidence.md").write_text(
        f"# Promotion dossier — {lane.lane} ({today})\n\n"
        f"Gate verdict: **{'PASS' if gate_result['passed'] else 'FAIL'}** on "
        f"{lane.resolved} resolved outcomes (WR {lane.wr}).\n\n"
        f"| gate | value | threshold | verdict |\n|---|---|---|---|\n{rows}\n\n"
        "**This dossier stages evidence only. Promotion requires OWNER SIGN-OFF: "
        "review, then apply proposed_change.patch manually.** Re-read the lane's "
        "binding caveats in its integration report "
        "(_workspace/strategy_pipeline/ 11_/12_ files) before signing.\n",
        encoding="utf-8")
    (d / "proposed_change.patch").write_text(
        f"# PROPOSED (not applied): promote {lane.lane}\n"
        f"# core/strategy_program.py: set the lane's StrategyProgramEntry status\n"
        f"# SHADOW_ONLY -> PAPER_CANDIDATE (paper_eligible=True). Owner applies by hand.\n",
        encoding="utf-8")
    return d
```

- [ ] **Step 4: Run — expect PASS.**
- [ ] **Step 5: Commit:** `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py && git commit -m "feat(funnel): dossier builder — evidence + proposed patch, owner-signed only"`

---

### Task 7: main() — orchestration, state diff, journal append

**Files:** Modify `scripts/promotion_funnel.py`, `tests/test_promotion_funnel.py`

**Interfaces:** Produces `compute_all(paths: dict, now: float) -> dict`, `persist(doc: dict, paths: dict) -> None`, `main() -> int`. `paths` keys: `warehouse, gate_log, goal_json, cal_dir, funnel_json, dossier_dir, journal_dir`.

- [ ] **Step 1: Write failing test**

```python
def test_compute_all_and_journal_on_state_change(tmp_path):
    db = tmp_path / "wh.sqlite"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE shadow_decisions (id INTEGER PRIMARY KEY, ts REAL, agent_id TEXT,"
              " timeframe TEXT, proposal_id TEXT, label_status TEXT)")
    c.execute("CREATE TABLE shadow_outcomes (proposal_id TEXT, net_pnl REAL, resolved_ts REAL)")
    c.execute("CREATE TABLE shadow_listing_probe (proposal_id TEXT, base TEXT, decision TEXT,"
              " shortable INTEGER, created_ts REAL)")
    c.commit()
    (tmp_path / "carry_gate_log.jsonl").write_text("")
    (tmp_path / "goal_progress.json").write_text(json.dumps({"lanes": []}))
    (tmp_path / "unlock_calendar").mkdir()
    paths = {"warehouse": db, "gate_log": tmp_path / "carry_gate_log.jsonl",
             "goal_json": tmp_path / "goal_progress.json",
             "cal_dir": tmp_path / "unlock_calendar",
             "funnel_json": tmp_path / "promotion_funnel.json",
             "dossier_dir": tmp_path / "dossiers", "journal_dir": tmp_path / "journal"}
    now = time.time()
    doc1 = pf.compute_all(paths, now)
    assert {l["lane"] for l in doc1["lanes"]} >= {"tsmom_20d_1h", "listing_short",
                                                  "f1_carry", "band_cohort", "unlock_short"}
    pf.persist(doc1, paths)            # first run: journal written (all states new)
    files = list((tmp_path / "journal").glob("*.md"))
    assert len(files) == 1
    first = files[0].read_text(encoding="utf-8")
    doc2 = pf.compute_all(paths, now + 60)
    pf.persist(doc2, paths)            # no state change: journal unchanged
    assert files[0].read_text(encoding="utf-8") == first
```

- [ ] **Step 2: Run — expect FAIL.**
- [ ] **Step 3: Write minimal implementation**

```python
def compute_all(paths: dict, now: float) -> dict:
    lanes: list[LaneState] = []
    try:
        conn = sqlite3.connect(f"file:{paths['warehouse']}?mode=ro", uri=True)
        lanes += probe_lane_states(conn, now)
        lanes.append(listing_lane_state(conn, now))
        conn.close()
    except sqlite3.Error as exc:
        lanes += [LaneState(l, "ERROR", detail={"error": str(exc)})
                  for l in [*PROBE_LANES, "listing_short"]]
    for ls in lanes:
        if ls.lane == "unlock_short" and ls.state != "ERROR":
            cov = unlock_calendar_coverage(paths["cal_dir"], now)
            ls.detail["calendar"] = cov
            if cov["starved"]:
                ls.state = "STARVED"
    lanes.append(f1_lane_state(paths["gate_log"], now))
    lanes.append(band_lane_state(paths["goal_json"]))
    today = datetime.now(timezone.utc).strftime("%Y%m%d")
    for ls in lanes:  # gate + dossier on any GATE_READY probe lane
        if ls.state == "GATE_READY" and ls.lane in PROBE_LANES:
            agent, timeframe = PROBE_LANES[ls.lane]
            conn = sqlite3.connect(f"file:{paths['warehouse']}?mode=ro", uri=True)
            tf_sql = " AND d.timeframe = ?" if timeframe else ""
            args: tuple = (agent, timeframe) if timeframe else (agent,)
            outcomes = [{"net_pnl": r[0], "p_win": r[1]} for r in conn.execute(
                "SELECT o.net_pnl, d.p_win FROM shadow_decisions d JOIN shadow_outcomes o"
                " ON o.proposal_id = d.proposal_id WHERE d.agent_id = ?"
                f" AND d.label_status = 'RESOLVED'{tf_sql}", args)]
            conn.close()
            gate = run_gate(outcomes)
            ls.detail["gate"] = gate
            if gate["passed"]:
                if build_dossier(ls, gate, outcomes, Path(paths["dossier_dir"]), today):
                    ls.state = "STAGED"
    return {"generated_utc": datetime.now(timezone.utc).isoformat(),
            "resolved_floor": RESOLVED_FLOOR,
            "lanes": [ls.to_dict() for ls in lanes]}


def persist(doc: dict, paths: dict) -> None:
    prev = {}
    try:
        prev = {l["lane"]: l["state"]
                for l in json.loads(Path(paths["funnel_json"]).read_text(
                    encoding="utf-8"))["lanes"]}
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    atomic_write_json(Path(paths["funnel_json"]), doc)
    changes = [(l["lane"], prev.get(l["lane"]), l["state"]) for l in doc["lanes"]
               if prev.get(l["lane"]) != l["state"]]
    f1 = next((l for l in doc["lanes"] if l["lane"] == "f1_carry"), None)
    alert = bool(f1 and f1["detail"].get("alert"))
    if not changes and not alert:
        return
    jdir = Path(paths["journal_dir"])
    jdir.mkdir(parents=True, exist_ok=True)
    day = jdir / f"{datetime.now(timezone.utc):%Y-%m-%d}.md"
    lines = [f"\n## {datetime.now(timezone.utc):%H:%M}Z — Promotion funnel"]
    lines += [f"- {lane}: {old or 'NEW'} → **{new}**" for lane, old, new in changes]
    if alert:
        lines.append(f"- ⚠ F1 REGIME ALERT: positive net edge sustained — "
                     f"{f1['detail']['top_edges'][:3]}")
    with day.open("a", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def main() -> int:
    paths = {"warehouse": ROOT / "data" / "warehouse.sqlite",
             "gate_log": ROOT / "data" / "carry_gate_log.jsonl",
             "goal_json": ROOT / "data" / "goal_progress.json",
             "cal_dir": ROOT / "data" / "unlock_calendar",
             "funnel_json": FUNNEL_JSON, "dossier_dir": DOSSIER_DIR,
             "journal_dir": ROOT / "journal"}
    doc = compute_all(paths, time.time())
    persist(doc, paths)
    print(f"promotion funnel -> {FUNNEL_JSON}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run full file — expect all PASS.**
- [ ] **Step 5: Commit:** `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py && git commit -m "feat(funnel): orchestration, state-diff journal, main entrypoint"`

---

### Task 8: Goal-report section, scheduled tasks, live smoke run

**Files:** Modify `scripts/report_goal_progress.py` (additive only; reporting glue — smoke-verified rather than unit-tested).

- [ ] **Step 1: Add funnel section** — in `render_journal(report)` immediately before its final `return "\n".join(lines) + "\n"`, insert:

```python
    # Promotion funnel summary (data/promotion_funnel.json, written hourly by
    # scripts/promotion_funnel.py; absent file = funnel task not scheduled yet).
    try:
        funnel = json.loads((ROOT / "data" / "promotion_funnel.json").read_text(
            encoding="utf-8"))
        lines.append("")
        lines.append("### Promotion funnel")
        for lane in funnel.get("lanes", []):
            lines.append(f"- {lane['lane']}: {lane['state']} ({lane['floor_progress']}"
                         f", eta={lane['eta_days']}d)")
    except (OSError, json.JSONDecodeError, KeyError):
        lines.append("- promotion funnel: no data (task not scheduled yet)")
```

- [ ] **Step 2: Verify report still runs:** `venv/Scripts/python.exe scripts/report_goal_progress.py` → prints `goal progress -> ...`, no traceback.
- [ ] **Step 3: Live smoke run (read-only against production stores):** `venv/Scripts/python.exe scripts/promotion_funnel.py` → expect `promotion funnel -> ...promotion_funnel.json`. Inspect the JSON: `tsmom_20d_1h` resolved ≈ 25 (2026-07-18 ground truth), `listing_short` STARVED with `tokenized_listings_30d > 0`, `f1_carry` IDLE with negative top edges, `band_cohort` matching `goal_progress.json`.
- [ ] **Step 4: Register scheduled tasks (Git Bash `//` escaping):**

```bash
schtasks //Create //SC HOURLY //TN "TradingBot_PromotionFunnel" //TR "\"D:\Downloads\Trading_Bot\venv\Scripts\python.exe\" \"D:\Downloads\Trading_Bot\scripts\promotion_funnel.py\"" //F
schtasks //Create //SC WEEKLY //D SUN //TN "TradingBot_UnlockCalendar" //TR "\"D:\Downloads\Trading_Bot\venv\Scripts\python.exe\" \"D:\Downloads\Trading_Bot\scripts\backfill_unlock_calendar.py\" --forward-days 60" //ST 06:00 //F
```

Expected: `SUCCESS` twice. Verify: `schtasks //Query //TN "TradingBot_PromotionFunnel"`.
- [ ] **Step 5: Full suite + commit:** `venv/Scripts/python.exe -m pytest tests/test_promotion_funnel.py -q` (all pass) then `git add scripts/promotion_funnel.py tests/test_promotion_funnel.py scripts/report_goal_progress.py && git commit -m "feat(funnel): goal-report section + hourly/weekly scheduled tasks"`

---

## Self-Review (done at authoring)

- **Spec coverage:** Architecture→T1/T7/T8; LaneTracker→T2; StarvationDiagnostics→T3; F1RegimeWatch→T4; GateRunner→T5 (constants-only deviation documented in Ground truth); DossierBuilder→T6; accrual fix (weekly backfill task)→T8; goal-report patch→T8; error handling→T2/T3/T4/T7 ERROR paths; honesty constraints→dossier text + band lane note + PBO informational flag. No gaps.
- **Placeholders:** the two conditionals (T3 calendar-shape check, T5 import-purity fallback) are explicit contingency instructions with the invariant stated — not TBDs.
- **Type consistency:** `LaneState.to_dict()` keys consumed identically by `persist()` and the T8 report section; `PROBE_LANES` tuple shape identical in T2/T7; `paths` dict keys identical in T7 test and `main()`.
