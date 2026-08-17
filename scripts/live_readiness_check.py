"""Execute every CONTROLLED_LIVE gate against the CURRENT config and report.

    venv/Scripts/python.exe scripts/live_readiness_check.py

Why this is a script and not a document: on 2026-08-17 a 39-finding
live-readiness audit was produced in a chat session and never written to disk.
Days later only the finding TITLES survived, and when they were re-checked
several did not reproduce — the promotion funnel was healthy, and the
daily-loss breaker the audit flagged was already refused by the live gate.
Work had nearly been done against three phantoms. A document decays; a script
that runs the real gate code cannot.

READ-ONLY and OFFLINE. It never starts the engine, never constructs an
exchange client, and never places, modifies, or cancels an order. It re-execs
itself once with OPERATING_MODE=CONTROLLED_LIVE so the gates evaluate their
live branch, then calls them directly and records each SystemExit reason. The
running bot is untouched.

Two classes of check:

  CONFIG    — the enforce_* gates in core/live_gate.py. They assert
              configuration values and are the authority on whether a live
              start would be refused.
  BEHAVIOUR — things no config assertion reaches, because they are code paths
              rather than values. A gate cannot tell you the position monitor
              will sell coins the bot never bought.

Exit code 0 = every gate passes; 1 = at least one would refuse.
A non-zero exit is the NORMAL, healthy result while the bot is in PAPER.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

REPORT_PATH = ROOT / "reports" / "live_readiness_latest.json"
LIVE = "CONTROLLED_LIVE"


def _reexec_as_live() -> int:
    """Re-run this script with the live mode set, so gates take the live branch."""
    env = dict(os.environ)
    env["OPERATING_MODE"] = LIVE
    env["LIVE_READINESS_CHILD"] = "1"
    return subprocess.call([sys.executable, str(Path(__file__).resolve())], env=env)


def _wrap(text: str, indent: int, width: int = 96) -> list[str]:
    out: list[str] = []
    line = ""
    for w in text.split():
        if line and len(line) + len(w) + 1 > width - indent:
            out.append(line)
            line = w
        else:
            line = f"{line} {w}".strip()
    if line:
        out.append(line)
    return out


def _run(name: str, fn) -> dict:
    """Call one gate. SystemExit => it would refuse; its message is the reason."""
    prefix = "[LiveGate] REFUSING TO START in CONTROLLED_LIVE: "
    try:
        fn()
        return {"check": name, "kind": "CONFIG", "ok": True, "reason": ""}
    except SystemExit as e:
        return {"check": name, "kind": "CONFIG", "ok": False,
                "reason": str(e).replace(prefix, "")}
    except Exception as e:  # a gate that cannot evaluate is never a pass
        return {"check": name, "kind": "CONFIG", "ok": False,
                "reason": f"gate could not be evaluated: {type(e).__name__}: {e}"}


def _behaviour_checks(cfg) -> list[dict]:
    """Live-money code paths that no configuration assertion reaches."""
    rows: list[dict] = []

    ext_on = bool(getattr(cfg, "EXTERNAL_POSITION_ACTIONS_ENABLED", False))
    rows.append({
        "check": "external_position_actions", "kind": "BEHAVIOUR",
        "ok": not ext_on,
        "reason": "" if not ext_on else (
            "ENABLED - the position monitor may market-close the owner's "
            "MANUAL futures positions and market-SELL their spot coins, driven "
            "by an mcp_score this repo measures as non-predictive. DRY_RUN "
            "no-ops the path, so PAPER has accrued no evidence for it."),
    })

    # Would live adopt risk state written by another mode? peak_balance drives
    # the drawdown halt, so a paper peak is fiction against real capital.
    path = ROOT / "data" / "risk_state.json"
    try:
        st = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        st = {}
    saved = str(st.get("operating_mode") or "").upper()
    rows.append({
        "check": "risk_state_provenance", "kind": "BEHAVIOUR",
        "ok": True,  # informational: the loader now refuses cross-mode state
        "reason": "" if not st or saved == LIVE else (
            f"data/risk_state.json was written by "
            f"{saved or 'an unstamped pre-2026-08-18 run'} "
            f"(peak_balance={st.get('peak_balance')!r}); the loader DISCARDS "
            f"it and starts live fresh. Expected - surfaced so it is not a "
            f"surprise on the day."),
    })
    return rows


def main() -> int:
    import config as cfg
    from core.live_gate import (
        enforce_controlled_live_gate,
        enforce_live_runtime_invariants,
        enforce_model_gate_readiness,
        enforce_strategy_readiness_gate,
    )

    if cfg.OPERATING_MODE != LIVE:
        print(f"[!] config resolved OPERATING_MODE={cfg.OPERATING_MODE}, "
              f"expected {LIVE} - cannot evaluate the live branch.")
        return 1

    rows = [
        _run("checklist_signed",
             lambda: enforce_controlled_live_gate(
                 LIVE, controlled_live_enabled=cfg.CONTROLLED_LIVE_ENABLED)),
        _run("runtime_invariants", lambda: enforce_live_runtime_invariants(LIVE)),
        _run("strategy_readiness", lambda: enforce_strategy_readiness_gate(LIVE)),
        _run("model_gate_readiness", lambda: enforce_model_gate_readiness(LIVE)),
    ]
    rows += _behaviour_checks(cfg)
    # The venue-capability preflight needs live clients; deliberately not run.
    rows.append({"check": "exchange_preflight", "kind": "CONFIG", "ok": None,
                 "reason": "NOT RUN - requires live exchange clients; it runs "
                           "at real startup via enforce_live_preflight_gate"})

    width = max(len(r["check"]) for r in rows)
    origin = os.getenv("OPERATING_MODE_ORIGINAL", "PAPER")
    print(f"\nLIVE READINESS - evaluated {datetime.now(timezone.utc):%Y-%m-%d %H:%M}Z"
          f"   (this process only; the bot stays {origin})\n")
    for r in rows:
        mark = "SKIP" if r["ok"] is None else ("PASS" if r["ok"] else "REFUSE")
        print(f"  [{mark:^6}] {r['check']:<{width}}  {r['kind']}")
        for line in _wrap(r["reason"], width + 14):
            print(f"{' ' * (width + 14)}{line}")

    blocking = [r for r in rows if r["ok"] is False]
    print(f"\n  {len(blocking)} of {len(rows)} checks would REFUSE a live start.")
    if blocking:
        print("  While the bot is in PAPER this is the expected, healthy result.")

    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps({
        "generated_utc": datetime.now(timezone.utc).isoformat(),
        "blocking": len(blocking), "checks": rows,
    }, indent=2), encoding="utf-8")
    print(f"  report: {REPORT_PATH.relative_to(ROOT)}")
    return 1 if blocking else 0


if __name__ == "__main__":
    if os.getenv("LIVE_READINESS_CHILD") != "1":
        os.environ.setdefault("OPERATING_MODE_ORIGINAL",
                              os.getenv("OPERATING_MODE", "PAPER"))
        raise SystemExit(_reexec_as_live())
    raise SystemExit(main())
