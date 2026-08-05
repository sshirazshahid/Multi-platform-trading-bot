#!/usr/bin/env python3
"""research/sim_mtsi_inventory.py — Micro Two-Sided Inventory offline sim.

Implements the frozen prereg ``55_prereg_mtsi_inventory``:
Avellaneda–Stoikov-style reservation quotes, hard $1 gross inventory,
mild FV tilt, maker fills with fee + adverse-selection haircut.
SPOT = fees only; FUTURES = fees + funding.

Research-only. Does not place orders or touch allowlists.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Sequence

ROOT = Path(__file__).resolve().parents[1]
PREREG_JSON = ROOT / "_workspace/strategy_pipeline/55_prereg_mtsi_inventory.json"
PREREG_MD = ROOT / "_workspace/strategy_pipeline/55_prereg_mtsi_inventory.md"
OUT_JSON = ROOT / "_workspace/strategy_pipeline/55_screen_mtsi_inventory.json"
OUT_MD = ROOT / "_workspace/strategy_pipeline/55_screen_mtsi_inventory.md"
STATUS_PATH = ROOT / "data/mtsi_status.json"

MAX_GROSS_USD = 1.0


@dataclass
class Clip:
    side: str  # buy | sell
    px: float
    notional_usd: float
    pnl_usd: float
    inventory_after: float


@dataclass
class SimResult:
    cell_id: str
    market: str
    n_clips: int
    mean_clip_pnl_usd: float
    profit_factor: float
    max_abs_inventory_usd: float
    max_single_clip_usd: float
    total_pnl_usd: float
    clips: list[Clip] = field(default_factory=list)
    verdict: str = "NO_GO"
    reasons: list[str] = field(default_factory=list)


def verify_prereg(
    prereg_json: Path = PREREG_JSON,
    prereg_md: Path = PREREG_MD,
) -> dict[str, Any]:
    """Fail closed if MD sha256 does not match frozen JSON."""
    meta = json.loads(prereg_json.read_text(encoding="utf-8"))
    raw = prereg_md.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    expected = str(meta.get("sha256_md", ""))
    if digest != expected:
        raise ValueError(
            f"prereg hash mismatch: got {digest} expected {expected}"
        )
    if len(raw) != int(meta.get("bytes_md", -1)):
        raise ValueError("prereg bytes_md mismatch")
    return meta


def _clip(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


def generate_synthetic_path(
    n: int = 2000,
    mid0: float = 100.0,
    sigma: float = 0.002,
    seed: int = 7,
) -> list[dict[str, float]]:
    """Deterministic mid path + microprice residual (no look-ahead)."""
    # Simple LCG for reproducibility without numpy dependency.
    state = seed & 0xFFFFFFFF

    def rnd() -> float:
        nonlocal state
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        return state / 0x100000000

    bars: list[dict[str, float]] = []
    mid = mid0
    for i in range(n):
        shock = (rnd() - 0.5) * 2.0 * sigma * mid
        mid = max(1e-9, mid + shock)
        # Microprice residual in relative terms (known at bar open).
        residual = (rnd() - 0.5) * 0.0002
        funding = 0.0001 * math.sin(i / 50.0)  # tiny oscillating funding
        bars.append(
            {
                "mid": mid,
                "micro_residual": residual,
                "funding_rate_8h": funding,
                "sigma": abs(shock / mid) if mid else sigma,
            }
        )
    return bars


def run_cell(
    cell: dict[str, Any],
    bars: Sequence[dict[str, float]],
    *,
    fees: dict[str, float],
    adverse_frac: float = 0.5,
    clip_usd: float = 0.25,
    tau: float = 1.0,
) -> SimResult:
    market = str(cell["market"])
    gamma = float(cell["gamma"])
    floor_bps = float(cell["half_spread_floor_bps"])
    tilt_max_bps = float(cell["tilt_max_bps"])
    maker_fee = float(
        fees["futures_maker"] if market == "futures" else fees["spot_maker"]
    )
    fee_floor = max(maker_fee, floor_bps / 1e4)

    q_usd = 0.0
    avg_entry = 0.0
    clips: list[Clip] = []
    max_abs_q = 0.0
    max_clip = 0.0
    gross_win = 0.0
    gross_loss = 0.0
    funding_pnl = 0.0

    for bar in bars:
        mid = float(bar["mid"])
        sigma = max(1e-8, float(bar.get("sigma", 0.001)))
        residual = float(bar.get("micro_residual", 0.0))
        tilt = _clip(residual, -tilt_max_bps / 1e4, tilt_max_bps / 1e4)
        q_norm = q_usd / MAX_GROSS_USD
        # Reservation in price space; inventory skew + mild FV tilt (relative).
        r = mid * (1.0 - q_norm * gamma * tau * 0.01 + tilt)
        half_frac = max(fee_floor, floor_bps / 1e4)
        half_px = mid * half_frac
        bid = r - half_px
        ask = r + half_px

        # Funding on futures inventory each bar (approx continuous accrual).
        if market == "futures" and q_usd != 0.0:
            fr = float(bar.get("funding_rate_8h", 0.0))
            # Longs pay when funding > 0.
            funding_pnl -= q_usd * fr / max(1.0, len(bars) / 3.0)

        # Inventory-driven one-sided quoting at the bound.
        allow_buy = q_usd < MAX_GROSS_USD - 1e-12
        allow_sell = q_usd > -MAX_GROSS_USD + 1e-12
        if abs(q_usd) >= MAX_GROSS_USD - 1e-9:
            if q_usd > 0:
                allow_buy = False
            else:
                allow_sell = False

        # Intra-bar range from sigma (no look-ahead beyond this bar).
        rng = max(sigma, 1e-6) * mid
        low = mid - rng
        high = mid + rng

        # Tape-cross fill model: bar range touches quote → maker fill.
        filled_side = None
        fill_px = mid
        if allow_buy and low <= bid:
            filled_side = "buy"
            fill_px = bid
        elif allow_sell and high >= ask:
            filled_side = "sell"
            fill_px = ask

        if filled_side is None:
            max_abs_q = max(max_abs_q, abs(q_usd))
            continue

        room = MAX_GROSS_USD - abs(q_usd)
        if filled_side == "buy" and q_usd < 0:
            # Reducing short — full clip up to covering short + room.
            notional = min(clip_usd, abs(q_usd) + room)
        elif filled_side == "sell" and q_usd > 0:
            notional = min(clip_usd, abs(q_usd) + room)
        else:
            notional = min(clip_usd, room)
        notional = min(notional, MAX_GROSS_USD)
        if notional <= 1e-12:
            max_abs_q = max(max_abs_q, abs(q_usd))
            continue

        # Mark-to-inventory PnL for the filled clip + fee + AS haircut.
        as_cost = adverse_frac * half_frac * notional
        fee_cost = maker_fee * notional
        # Realized vs average inventory when reducing; else zero until exit.
        realized = 0.0
        if filled_side == "sell" and q_usd > 0:
            close_n = min(notional, q_usd)
            realized = (fill_px - avg_entry) / mid * close_n if mid else 0.0
        elif filled_side == "buy" and q_usd < 0:
            close_n = min(notional, -q_usd)
            realized = (avg_entry - fill_px) / mid * close_n if mid else 0.0

        pnl = realized - fee_cost - as_cost
        if filled_side == "buy":
            new_q = q_usd + notional
            if q_usd >= 0:
                # Adding long — update VWAP.
                avg_entry = (
                    (avg_entry * q_usd + fill_px * notional) / new_q
                    if new_q > 0
                    else fill_px
                )
            elif new_q > 0:
                avg_entry = fill_px
            q_usd = new_q
        else:
            new_q = q_usd - notional
            if q_usd <= 0:
                avg_entry = (
                    (avg_entry * (-q_usd) + fill_px * notional) / (-new_q)
                    if new_q < 0
                    else fill_px
                )
            elif new_q < 0:
                avg_entry = fill_px
            q_usd = new_q

        # Hard invariant backstop.
        if abs(q_usd) > MAX_GROSS_USD + 1e-9:
            q_usd = _clip(q_usd, -MAX_GROSS_USD, MAX_GROSS_USD)

        max_abs_q = max(max_abs_q, abs(q_usd))
        max_clip = max(max_clip, notional)
        if pnl >= 0:
            gross_win += pnl
        else:
            gross_loss += abs(pnl)
        clips.append(
            Clip(
                side=filled_side,
                px=fill_px,
                notional_usd=notional,
                pnl_usd=pnl,
                inventory_after=q_usd,
            )
        )

    # Fold funding into total for futures.
    if clips and market == "futures":
        per = funding_pnl / len(clips)
        for c in clips:
            c.pnl_usd += per
        # Recompute win/loss after funding allocation.
        gross_win = sum(c.pnl_usd for c in clips if c.pnl_usd >= 0)
        gross_loss = sum(abs(c.pnl_usd) for c in clips if c.pnl_usd < 0)

    n = len(clips)
    total = sum(c.pnl_usd for c in clips)
    mean = total / n if n else 0.0
    pf = (gross_win / gross_loss) if gross_loss > 1e-15 else (math.inf if gross_win > 0 else 0.0)

    reasons: list[str] = []
    verdict = "GO"
    if n < 200:
        verdict = "INSUFFICIENT_DATA"
        reasons.append(f"n_clips={n}<200")
    else:
        if mean <= 0:
            verdict = "NO_GO"
            reasons.append(f"mean_clip_pnl_usd={mean:.6g}<=0")
        if not (pf > 1.0):
            verdict = "NO_GO"
            reasons.append(f"profit_factor={pf:.6g}<=1")
        if max_abs_q > MAX_GROSS_USD + 1e-9:
            verdict = "NO_GO"
            reasons.append(f"inventory_breach={max_abs_q}")
        if max_clip > MAX_GROSS_USD + 1e-9:
            verdict = "NO_GO"
            reasons.append(f"clip_breach={max_clip}")
        if not reasons and verdict == "GO":
            reasons.append("all_point_gates_cleared")

    return SimResult(
        cell_id=str(cell["id"]),
        market=market,
        n_clips=n,
        mean_clip_pnl_usd=mean,
        profit_factor=pf if math.isfinite(pf) else 999.0,
        max_abs_inventory_usd=max_abs_q,
        max_single_clip_usd=max_clip,
        total_pnl_usd=total,
        clips=clips,
        verdict=verdict,
        reasons=reasons,
    )


def screen_all(
    *,
    bars: Sequence[dict[str, float]] | None = None,
    write_artifacts: bool = False,
) -> dict[str, Any]:
    meta = verify_prereg()
    path = bars if bars is not None else generate_synthetic_path()
    results = [
        run_cell(
            cell,
            path,
            fees=meta["fees"],
            adverse_frac=float(meta["adverse_selection_frac_of_half_spread"]),
        )
        for cell in meta["cells"]
    ]
    go_cells = [r for r in results if r.verdict == "GO"]
    # Adjacent same-market same-sign rule.
    final = "NO_GO"
    if go_cells:
        by_m: dict[str, list[SimResult]] = {}
        for r in go_cells:
            by_m.setdefault(r.market, []).append(r)
        for group in by_m.values():
            if len(group) >= 2 and all(g.mean_clip_pnl_usd > 0 for g in group):
                final = "GO"
                break
        if final != "GO" and len(go_cells) == 1:
            final = "NO_GO"
            go_cells[0].reasons.append("adjacent_same_market_cell_missing")
            go_cells[0].verdict = "NO_GO"

    payload = {
        "prereg_id": meta["prereg_id"],
        "expectation": meta["expectation"],
        "final_verdict": final if not go_cells or final == "NO_GO" else final,
        "cells": [
            {
                "id": r.cell_id,
                "market": r.market,
                "n_clips": r.n_clips,
                "mean_clip_pnl_usd": r.mean_clip_pnl_usd,
                "profit_factor": r.profit_factor,
                "max_abs_inventory_usd": r.max_abs_inventory_usd,
                "max_single_clip_usd": r.max_single_clip_usd,
                "total_pnl_usd": r.total_pnl_usd,
                "verdict": r.verdict,
                "reasons": r.reasons,
            }
            for r in results
        ],
        "honesty": (
            "MTSI offline synthetic screen — geometry/inventory control ≠ proven edge. "
            "CEX maker fees + adverse selection expected to yield NO_GO."
        ),
    }
    # Recompute final strictly: any cell still GO after adjacent rule?
    still_go = [c for c in payload["cells"] if c["verdict"] == "GO"]
    if len(still_go) >= 2:
        markets = {c["market"] for c in still_go}
        payload["final_verdict"] = (
            "GO"
            if any(
                sum(1 for c in still_go if c["market"] == m) >= 2 for m in markets
            )
            else "NO_GO"
        )
    else:
        payload["final_verdict"] = "NO_GO"

    if write_artifacts:
        OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
        OUT_JSON.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        lines = [
            "# 55 — MTSI screen results",
            "",
            f"**Final verdict:** `{payload['final_verdict']}`",
            f"**Expectation:** `{meta['expectation']}`",
            "",
            "| Cell | Market | n | mean PnL | PF | max\\|q\\| | verdict |",
            "|------|--------|---|----------|----|---------|---------|",
        ]
        for c in payload["cells"]:
            lines.append(
                f"| {c['id']} | {c['market']} | {c['n_clips']} | "
                f"{c['mean_clip_pnl_usd']:.6g} | {c['profit_factor']:.4g} | "
                f"{c['max_abs_inventory_usd']:.4g} | {c['verdict']} |"
            )
        lines.extend(["", payload["honesty"], ""])
        OUT_MD.write_text("\n".join(lines), encoding="utf-8")
        _write_status(payload, results)

    return payload


def _write_status(payload: dict[str, Any], results: Iterable[SimResult]) -> None:
    """Mission Control poll surface — inventory + clip histogram."""
    STATUS_PATH.parent.mkdir(parents=True, exist_ok=True)
    # Prefer futures F2 as the display cell if present.
    pick = next((r for r in results if r.cell_id == "F2"), None)
    if pick is None:
        pick = next(iter(results), None)
    hist = [0] * 11
    if pick and pick.clips:
        for c in pick.clips:
            # Bucket pnl in 0.01 USD bins centered at 0 → index 5.
            idx = int(_clip(round(c.pnl_usd / 0.01) + 5, 0, 10))
            hist[idx] += 1
        last_q = pick.clips[-1].inventory_after
    else:
        last_q = 0.0
    status = {
        "family": "mtsi_inventory_v1",
        "final_verdict": payload["final_verdict"],
        "max_gross_inventory_usd": MAX_GROSS_USD,
        "inventory_usd": last_q,
        "inventory_utilization": abs(last_q) / MAX_GROSS_USD,
        "clip_pnl_histogram": hist,
        "display_cell": pick.cell_id if pick else None,
        "n_clips": pick.n_clips if pick else 0,
        "mean_clip_pnl_usd": pick.mean_clip_pnl_usd if pick else 0.0,
        "honesty": payload["honesty"],
        "candle_spark": [],  # filled by MC from optional OHLCV later
    }
    STATUS_PATH.write_text(json.dumps(status, indent=2), encoding="utf-8")


def main() -> None:
    payload = screen_all(write_artifacts=True)
    print(
        f"MTSI final={payload['final_verdict']} "
        f"cells={len(payload['cells'])} "
        f"expectation={payload['expectation']}"
    )


if __name__ == "__main__":
    main()
