"""Harvest Hyperliquid hourly funding (F1 timing conditioner — data only).

Queue item (30_edge_queue #4): free HL API as a *signal* vs local
``data/carry_gate_log.jsonl``. This script NEVER places orders and is not
wired into MCP directional opens.

Cadence (owner / autoplan 2026-07-30 T6):
  - Intended schedule: hourly (or with the promotion-funnel schtask stagger).
  - Output: append-only ``data/hl_funding_history.jsonl``.
  - Dedup: same ``coin`` within ``--dedup-sec`` (default 3300s ≈ 55m) is skipped
    so a double-fire does not inflate the series.
  - Exit codes: ``0`` wrote ≥1 new row (or all coins were recent dupes);
    ``1`` total failure (API/parse error, or zero matching coins and no dupes).

Usage:
  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py
  ./venv/Scripts/python.exe scripts/harvest_hl_funding.py --coins BTC ETH SOL
"""
from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "hl_funding_history.jsonl"
API = "https://api.hyperliquid.xyz/info"
DEFAULT_COINS = ("BTC", "ETH", "SOL", "ARB", "AVAX", "LINK")
DEFAULT_DEDUP_SEC = 3300.0


def fetch_meta_and_ctxs() -> dict:
    req = urllib.request.Request(
        API,
        data=json.dumps({"type": "metaAndAssetCtxs"}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def _recent_coins(path: Path, *, now: float, dedup_sec: float) -> set[str]:
    """Coins already written within the dedup window (newest-first scan)."""
    if not path.exists() or dedup_sec <= 0:
        return set()
    recent: set[str] = set()
    cut = now - dedup_sec
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return set()
    for ln in reversed(lines):
        ln = ln.strip()
        if not ln:
            continue
        try:
            row = json.loads(ln)
            ts = float(row.get("ts") or 0)
            coin = str(row.get("coin") or "").upper()
        except (TypeError, ValueError, json.JSONDecodeError):
            continue
        if ts < cut:
            break
        if coin:
            recent.add(coin)
    return recent


def harvest_rows(
    payload: object,
    want: set[str],
    *,
    now: float,
    skip_coins: set[str] | None = None,
) -> list[dict]:
    """Build funding rows from a metaAndAssetCtxs payload (testable)."""
    if not isinstance(payload, (list, tuple)) or len(payload) < 2:
        raise ValueError("HL metaAndAssetCtxs payload malformed")
    meta, ctxs = payload[0], payload[1]
    if not isinstance(meta, dict) or not isinstance(ctxs, (list, tuple)):
        raise ValueError("HL metaAndAssetCtxs payload malformed")
    universe = meta.get("universe") or []
    skip = skip_coins or set()
    rows: list[dict] = []
    for i, asset in enumerate(universe):
        if not isinstance(asset, dict):
            continue
        name = str(asset.get("name") or "").upper()
        if name not in want or name in skip:
            continue
        ctx = ctxs[i] if i < len(ctxs) and isinstance(ctxs[i], dict) else {}
        try:
            funding = float(ctx.get("funding"))
        except (TypeError, ValueError):
            continue
        rows.append(
            {
                "ts": now,
                "venue": "hyperliquid",
                "coin": name,
                "funding": funding,
                "mark_px": ctx.get("markPx"),
                "open_interest": ctx.get("openInterest"),
                "premium": ctx.get("premium"),
            }
        )
    return rows


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description="Harvest Hyperliquid funding prints (data only; no orders)."
    )
    ap.add_argument("--coins", nargs="*", default=list(DEFAULT_COINS))
    ap.add_argument(
        "--dedup-sec",
        type=float,
        default=DEFAULT_DEDUP_SEC,
        help="Skip coins already written within this many seconds (0=off)",
    )
    ap.add_argument(
        "--out",
        type=Path,
        default=OUT,
        help="Append target jsonl (default: data/hl_funding_history.jsonl)",
    )
    args = ap.parse_args(argv)
    want = {c.upper() for c in args.coins}
    if not want:
        print("ERROR: --coins is empty", file=sys.stderr)
        return 1

    now = time.time()
    try:
        payload = fetch_meta_and_ctxs()
        skip = _recent_coins(args.out, now=now, dedup_sec=float(args.dedup_sec))
        rows = harvest_rows(payload, want, now=now, skip_coins=skip)
    except (urllib.error.URLError, TimeoutError, OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"ERROR: HL funding harvest failed: {exc}", file=sys.stderr)
        return 1

    dupes = want & skip if args.dedup_sec > 0 else set()
    if not rows and not dupes:
        print(
            "ERROR: zero HL funding rows for requested coins "
            f"{sorted(want)} (API returned no match)",
            file=sys.stderr,
        )
        return 1
    if not rows and dupes:
        print(
            f"ok: skipped {len(dupes)} recent duplicate coin(s) "
            f"{sorted(dupes)} (dedup_sec={args.dedup_sec})"
        )
        return 0

    args.out.parent.mkdir(parents=True, exist_ok=True)
    with args.out.open("a", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r) + "\n")
    print(f"appended {len(rows)} HL funding rows -> {args.out}")
    for r in rows:
        print(f"  {r['coin']}: funding={r['funding']}")
    if dupes:
        print(f"  (skipped recent dupes: {sorted(dupes)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
