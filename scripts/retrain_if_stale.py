"""Retrain model-gate ensembles when latest pointers are stale or invalid.

This is a schedulable wrapper around the existing training pipeline. It does
not loosen promotion criteria; `train_models.py --auto-promote` still decides
whether a newly trained model may replace the latest pointer.
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.promotion_gate import validate_model_pointer  # noqa: E402


def pointer_status(market: str, *, max_age_days: int) -> dict:
    path = ROOT / "data" / "models" / f"ensemble_{market}_latest.json"
    if not path.exists():
        return {"market": market, "ok": False, "reason": "latest pointer missing"}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {"market": market, "ok": False, "reason": f"latest pointer invalid: {exc}"}
    ok, reason, diag = validate_model_pointer(
        payload,
        market_type=market,
        max_age_days=max_age_days,
    )
    return {"market": market, "ok": bool(ok), "reason": reason, "diag": diag}


def build_commands(
    markets: list[str], *, tag: str, quick: bool, skip_build: bool
) -> list[list[str]]:
    commands: list[list[str]] = []
    if not skip_build:
        commands.append(
            [sys.executable, "scripts/build_features_dataset.py", "--print-every", "500"]
        )
        for market in markets:
            commands.append(
                [
                    sys.executable,
                    "scripts/build_labels.py",
                    "--market",
                    market,
                    "--print-every",
                    "500",
                ]
            )
    train_market = "both" if set(markets) == {"futures", "spot"} else markets[0]
    train_cmd = [
        sys.executable,
        "scripts/train_models.py",
        "--market",
        train_market,
        "--tag",
        tag,
        "--auto-promote",
    ]
    if quick:
        train_cmd.append("--quick")
    commands.append(train_cmd)
    return commands


def _log_line(handle, message: str) -> None:
    print(message, flush=True)
    if handle is not None:
        handle.write(message + "\n")
        handle.flush()


def run_commands(
    commands: list[list[str]],
    *,
    dry_run: bool,
    log_dir: Path | None = None,
    step_timeout_sec: int = 0,
) -> int:
    log_handle = None
    if log_dir is not None:
        log_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        log_handle = (log_dir / f"retrain_if_stale_{stamp}.log").open(
            "w",
            encoding="utf-8",
        )
        _log_line(log_handle, f"log={log_handle.name}")
    for cmd in commands:
        printable = " ".join(cmd)
        _log_line(log_handle, f"cmd={printable}")
        if dry_run:
            continue
        started = time.time()
        proc = subprocess.Popen(
            cmd,
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        try:
            while True:
                line = proc.stdout.readline()
                if line:
                    _log_line(log_handle, line.rstrip())
                if proc.poll() is not None:
                    break
                if step_timeout_sec > 0 and time.time() - started > step_timeout_sec:
                    proc.terminate()
                    try:
                        proc.wait(timeout=15)
                    except subprocess.TimeoutExpired:
                        proc.kill()
                    _log_line(log_handle, f"timeout={step_timeout_sec}s cmd={printable}")
                    if log_handle is not None:
                        log_handle.close()
                    return 124
            for line in proc.stdout:
                _log_line(log_handle, line.rstrip())
        finally:
            proc.stdout.close()
        if proc.returncode != 0:
            _log_line(log_handle, f"exit={proc.returncode} cmd={printable}")
            if log_handle is not None:
                log_handle.close()
            return int(proc.returncode or 1)
    if log_handle is not None:
        log_handle.close()
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--market", choices=["futures", "spot", "both"], default="both")
    parser.add_argument("--max-age-days", type=int, default=7)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--quick", action="store_true")
    parser.add_argument("--skip-build", action="store_true")
    parser.add_argument("--tag", default="")
    parser.add_argument("--log-dir", default="data/retrain_logs")
    parser.add_argument("--step-timeout-sec", type=int, default=0)
    args = parser.parse_args()

    markets = ["futures", "spot"] if args.market == "both" else [args.market]
    statuses = [pointer_status(m, max_age_days=args.max_age_days) for m in markets]
    for status in statuses:
        print(f"model={status['market']} ok={status['ok']} reason={status['reason']}")

    needs_retrain = args.force or any(not s["ok"] for s in statuses)
    if not needs_retrain:
        print("retrain=skipped")
        return 0

    tag = args.tag or "auto_" + datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    commands = build_commands(markets, tag=tag, quick=args.quick, skip_build=args.skip_build)
    return run_commands(
        commands,
        dry_run=args.dry_run,
        log_dir=ROOT / args.log_dir,
        step_timeout_sec=args.step_timeout_sec,
    )


if __name__ == "__main__":
    raise SystemExit(main())
