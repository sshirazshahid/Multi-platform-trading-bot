#!/usr/bin/env python3
"""Aggressive research-hunt tick — continuous search for NEW tradeable edge.

Owner directive 2026-08-15: "Build aggressive-research loop maintaining
current trading posture." Aggression is spent on SEARCH, never on capital.

Complements scripts/run_research_loop_tick.py, which answers "is the bot
healthy?". This answers a different question, on a schedule:

    Is there new evidence that makes a NEW screen possible,
    or an OLD frozen screen re-runnable?

Two jobs, both bounded:
  1. DATA READINESS - every accruing feed is measured against the sample floor
     a screen would need. When a feed crosses its floor, the loop PROPOSES a
     candidate (a prereg to write); it does not write or run one. An unattended
     process must never author its own hypothesis.
  2. FROZEN RE-RUNS - screens whose verdict was INSUFFICIENT_DATA are re-run as
     sample accrues, but ONLY while the prereg's sha256 still matches the value
     pinned here. An edited prereg is a NEW pre-registration and its re-run is
     refused, loudly.

TRADING POSTURE IS UNTOUCHED, structurally: this module imports no order path,
no exchange client, and no config mutation. It cannot open, size, or block a
trade, and it cannot emit a GO - only a human, after adversarial audit, can.
Pinned by tests/test_research_hunt_loop.py.

Run:  venv/Scripts/python.exe scripts/run_research_hunt.py [--dry-run] [--rerun]
"""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
REPORT_PATH = ROOT / "data" / "research_hunt_latest.json"
WS = ROOT / "_workspace" / "strategy_pipeline"


@dataclass(frozen=True)
class Asset:
    """An accruing data feed and the sample floor a screen would need."""
    name: str
    path: Path
    min_rows: int
    min_days: int
    note: str = ""


@dataclass(frozen=True)
class FrozenScreen:
    """A pre-registered screen that can be honestly re-run as sample grows."""
    name: str
    prereg: Path
    prereg_sha256: str
    script: Path
    note: str = ""


# ── What is accruing, and what each would need to be screenable ─────────────
# Floors follow the pipeline's own convention: >=30 events per testable cell,
# and enough calendar span that one regime cannot masquerade as an edge.
ASSETS = (
    Asset("l2_book_imbalance", ROOT / "data/l2_history.jsonl",
          min_rows=60_000, min_days=120,
          note="hourly depth imbalance; 53d screened NO_GO (ledger 2026-08-13). "
               "A NEW prereg needs much longer span or sub-hourly data."),
    Asset("options_skew_rr25", ROOT / "data/skew_history.jsonl",
          min_rows=8_000, min_days=120,
          note="Deribit rr25; 53d screened NO_GO (ledger 2026-08-13). Reopen "
               "path: rr25 LEVEL/percentile or expiry-conditioned, NEW prereg."),
    Asset("liquidations", ROOT / "data/liquidations_history.jsonl",
          min_rows=200_000, min_days=240,
          note="feeds frozen screen 41 (liq-cascade); see FROZEN_SCREENS."),
    Asset("funding_history", ROOT / "data/funding_history",
          min_rows=0, min_days=0,
          note="F1 substrate; staleness here is a HARVEST gap, not a screen gap."),
    Asset("aggtrades_vpin", ROOT / "data/aggtrades_vpin",
          min_rows=0, min_days=0,
          note="VPIN NO_GO (theta unreachable). Reopen needs CDF/percentile theta."),
)

# ── Frozen screens whose verdict was sample-limited ────────────────────────
FROZEN_SCREENS = (
    FrozenScreen(
        "41_liq_cascade",
        WS / "41_prereg_liq_cascade.md",
        "41fa8452d00c9ddf",  # 16-char prefix of the frozen prereg sha256
        ROOT / "research" / "screen_liq_cascade.py",
        note="6 ETH/BTC short_flush cells sat at n=21-29 vs a 30 floor; "
             "they accrue ~2-3 events/week.",
    ),
)


def _iter_rows(path: Path):
    """Yield parsed JSONL rows, skipping unreadable ones."""
    with path.open("r", encoding="utf-8", errors="ignore") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except Exception:
                continue


def assess_asset(a: Asset) -> dict:
    """Measure a feed against its screen floor. Never raises."""
    st = {"name": a.name, "ready": False, "rows": 0, "days": 0.0,
          "age_h": None, "reason": "", "note": a.note}
    try:
        if not a.path.exists():
            st["reason"] = "absent - nothing accruing"
            return st
        st["age_h"] = round((time.time() - a.path.stat().st_mtime) / 3600.0, 1)
        if a.path.is_dir():
            files = [p for p in a.path.rglob("*") if p.is_file()]
            st["rows"] = len(files)
            st["reason"] = f"directory feed ({len(files)} files)"
            st["ready"] = a.min_rows > 0 and len(files) >= a.min_rows
            return st
        rows = 0
        first = last = None
        for d in _iter_rows(a.path):
            rows += 1
            h = d.get("hour") or d.get("ts") or d.get("timestamp")
            if isinstance(h, (int, float)) and h > 0:
                h = int(h if h < 10**12 else h / 1000)
                first = h if first is None else min(first, h)
                last = h if last is None else max(last, h)
        st["rows"] = rows
        st["days"] = round(((last - first) / 86400.0) if first and last else 0.0, 1)
        if rows < a.min_rows:
            st["reason"] = f"{rows:,} rows < {a.min_rows:,} row floor"
        elif st["days"] < a.min_days:
            st["reason"] = f"{st['days']} days < {a.min_days}-day span floor"
        else:
            st["ready"] = True
            st["reason"] = (f"{rows:,} rows over {st['days']}d - clears the floor; "
                            "a NEW hashed prereg may now be written")
    except Exception as e:  # a broken feed is a finding, not a crash
        st["reason"] = f"unreadable: {type(e).__name__}: {str(e)[:80]}"
    return st


def rerun_allowed(s: FrozenScreen) -> tuple:
    """A frozen screen may re-run ONLY if its prereg is byte-identical."""
    if not s.prereg.exists():
        return False, "prereg missing - cannot verify the frozen spec"
    if not s.script.exists():
        return False, "screen script missing"
    actual = hashlib.sha256(s.prereg.read_bytes()).hexdigest()
    pinned = (s.prereg_sha256 or "").strip()
    if pinned and not actual.startswith(pinned[:16]):
        return False, (f"prereg hash CHANGED (pinned {pinned[:16]}..., "
                       f"actual {actual[:16]}...) - that is a NEW "
                       "pre-registration; refusing to re-run")
    return True, f"prereg hash verified ({actual[:16]}...)"


def run_screen(s: FrozenScreen, timeout: int = 900) -> dict:
    """Execute a frozen screen and capture its verdict line. Never raises."""
    out = {"name": s.name, "ran": False, "verdict": None, "tail": ""}
    try:
        p = subprocess.run(
            [sys.executable, str(s.script)],
            cwd=str(ROOT), capture_output=True, text=True, timeout=timeout,
        )
        out["ran"] = True
        tail = (p.stdout or "")[-1500:]
        out["tail"] = tail
        for line in reversed(tail.splitlines()):
            if "VERDICT" in line.upper():
                out["verdict"] = line.strip()
                break
    except subprocess.TimeoutExpired:
        out["tail"] = f"timeout after {timeout}s"
    except Exception as e:
        out["tail"] = f"{type(e).__name__}: {str(e)[:120]}"
    return out


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Aggressive research-hunt tick")
    ap.add_argument("--dry-run", action="store_true",
                    help="report to stdout only; write nothing")
    ap.add_argument("--rerun", action="store_true",
                    help="execute frozen screens whose prereg hash verifies")
    args = ap.parse_args(argv)

    assets = [assess_asset(a) for a in ASSETS]
    screens, actions = [], []

    for s in FROZEN_SCREENS:
        ok, why = rerun_allowed(s)
        row = {"name": s.name, "rerun_allowed": ok, "reason": why,
               "note": s.note, "result": None}
        if ok and args.rerun:
            row["result"] = run_screen(s)
            if row["result"].get("verdict"):
                actions.append(f"{s.name}: {row['result']['verdict']}")
        screens.append(row)

    candidates = [
        {"asset": a["name"],
         "next_step": "write a NEW hashed pre-registration, then screen",
         "why": a["reason"], "note": a["note"]}
        for a in assets if a["ready"]
    ]
    if not candidates:
        actions.append("no feed cleared its screen floor this tick - "
                       "nothing new is screenable yet")

    report = {
        "ts": int(time.time()),
        "assets": assets,
        "screens": screens,
        "candidates": candidates,
        "actions": actions,
        # Stated explicitly so any reader (or future automation) can see this
        # loop is search-only and left the trading machine alone.
        "posture": {
            "trading_unchanged": True,
            "orders_placed": 0,
            "config_mutations": 0,
            "note": "search-only loop: proposes preregs and re-runs frozen "
                    "screens; never authors a hypothesis, never emits a "
                    "verdict of its own, never touches gates/leverage/mode.",
        },
    }

    print(f"[ResearchHunt] assets={len(assets)} ready={len(candidates)} "
          f"screens={len(screens)}")
    for a in assets:
        print(f"   {'READY' if a['ready'] else '  -  '} {a['name']:22s} {a['reason']}")
    for s in screens:
        print(f"   {'RERUN-OK' if s['rerun_allowed'] else 'BLOCKED '} "
              f"{s['name']:22s} {s['reason']}")
        if s.get("result") and s["result"].get("verdict"):
            print(f"       -> {s['result']['verdict']}")
    for a in actions:
        print(f"   ACTION: {a}")

    if args.dry_run:
        print("[ResearchHunt] --dry-run: nothing written")
        return 0
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(report, indent=1), encoding="utf-8")
    print(f"[ResearchHunt] wrote {REPORT_PATH}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
