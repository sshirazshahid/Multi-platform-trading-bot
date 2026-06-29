#!/usr/bin/env python3
"""Intel Synthesis — advisory, LOG-ONLY digest of REAL public market data.

This is the honest, "swarm of *information*" layer: it assembles the real,
keyless data the bot already collects from multiple orthogonal sources and asks
Claude (via the Max-subscription CLI) to synthesize ONE short awareness note.

It is the opposite of a social-*simulation* forecaster: every input is real,
priced-in market data — never simulated opinion.

Sources aggregated (all free / no API key):
  * Regime brief   — latest snapshot from data/market_intel_history.jsonl
                     (produced by scripts/market_intel_report.py)
  * On-chain value — BTC MVRV-Z score (bitcoin-data.com)
  * Positioning    — derivs LSR / taker buy-sell / OI change (DerivsHarvester)
  * Funding / OI   — latest funding + 24h OI change per major from
                     data/funding_oi/<SYM>_*.csv (scripts/fetch_binance_funding_oi.py)

HARD SCOPE — read this:
  * ADVISORY and LOG-ONLY. The output is written to reports/ for HUMAN review.
  * It is NOT a trade signal. The bot's entries are governed solely by
    gate-validated quantitative signals (core/promotion_gate.py). Nothing here
    can change an entry/exit — this module imports NOTHING from the order path
    and nothing in the order path imports it (enforced by a unit test).
  * Public data is priced in; this is for awareness/research only.

Usage:
    python scripts/run_intel_synthesis.py
    python scripts/run_intel_synthesis.py --no-llm      # assemble facts, skip Claude
    python scripts/run_intel_synthesis.py --symbols BTC ETH SOL
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import urllib.request
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MVRVZ_URL = "https://bitcoin-data.com/v1/mvrv-zscore"  # keyless; mirrors run_mvrv_z_screen
INTEL_HISTORY = ROOT / "data" / "market_intel_history.jsonl"
FUNDING_OI_DIR = ROOT / "data" / "funding_oi"
OUT_HISTORY = ROOT / "data" / "intel_synthesis_history.jsonl"
DEFAULT_SYMBOLS = ["BTC", "ETH", "SOL", "BNB", "XRP"]

SYSTEM_PROMPT = (
    "You are a market-awareness analyst writing a short internal research note for the "
    "operator of a systematic crypto trading bot. You are given ONLY real, public, "
    "already-priced-in market data, assembled below.\n"
    "RULES (follow exactly):\n"
    "1. This note is ADVISORY and LOG-ONLY. It is NOT a trade signal, entry/exit "
    "instruction, or price prediction. The bot's trades are decided solely by "
    "gate-validated quantitative signals; nothing you write changes them.\n"
    "2. Use ONLY the facts provided. Do NOT invent market reasons, causation, or price "
    "targets. If the data is mixed or inconclusive, say so plainly.\n"
    "3. Be concise and numeric. Structure with these headers exactly:\n"
    "   ## Regime read  (2-3 sentences)\n"
    "   ## Notable shifts & divergences  (bullets across the sources)\n"
    "   ## Risks to watch  (bullets)\n"
    "   ## What would change this picture  (one line)\n"
    "4. No hype, no buy/sell recommendations, no confidence theater. Frame around "
    "capital preservation."
)


def _safe(fn, default, failed: list, label: str):
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - degrade gracefully, surface in footer
        print(f"  [warn] {label}: {str(e)[:100]}")
        failed.append(label)
        return default


def latest_market_intel(path: Path = INTEL_HISTORY) -> dict | None:
    """Return the most recent snapshot dict from the brief history, or None."""
    if not path.exists():
        return None
    lines = path.read_text(encoding="utf-8").strip().splitlines()
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def fetch_mvrv_z() -> dict[str, float]:
    """Keyless BTC MVRV-Z history {date: zscore} (mirrors run_mvrv_z_screen)."""
    req = urllib.request.Request(MVRVZ_URL, headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=60) as r:
        rows = json.load(r)
    out: dict[str, float] = {}
    for row in rows:
        try:
            out[str(row["d"])] = float(row["mvrvZscore"])
        except (KeyError, TypeError, ValueError):
            continue
    return out


def mvrv_context(series: dict[str, float]) -> dict | None:
    """Latest MVRV-Z + percentile vs full history + a plain valuation zone.

    Pure given the series (unit-tested). Zones are descriptive context only,
    NOT thresholds the bot acts on.
    """
    if not series:
        return None
    latest_date = max(series)
    z = series[latest_date]
    vals = sorted(series.values())
    below = sum(1 for v in vals if v <= z)
    pct = 100.0 * below / len(vals)
    if z < 0:
        zone = "deep value (historically a bottom zone)"
    elif z < 2:
        zone = "below-average valuation"
    elif z < 4:
        zone = "mid / fair valuation"
    elif z < 6:
        zone = "elevated valuation"
    else:
        zone = "historically euphoric / top zone"
    return {"date": latest_date, "z": z, "pctl": pct, "zone": zone}


def funding_oi_summary(bases: list[str], directory: Path = FUNDING_OI_DIR) -> dict[str, dict]:
    """Latest funding rate + ~24h OI change per base from the local CSVs.

    Reads the CSVs written by fetch_binance_funding_oi.py. Missing files are
    skipped silently (the downloader may not have run for a symbol yet).
    """
    out: dict[str, dict] = {}
    for base in bases:
        rec: dict = {}
        fpath = directory / f"{base}_funding.csv"
        if fpath.exists():
            rows = list(csv.DictReader(fpath.open(encoding="utf-8")))
            if rows:
                try:
                    rec["funding"] = float(rows[-1]["funding_rate"])
                except (KeyError, ValueError):
                    pass
        opath = directory / f"{base}_oi.csv"
        if opath.exists():
            rows = list(csv.DictReader(opath.open(encoding="utf-8")))
            if rows:
                try:
                    last = float(rows[-1]["open_interest_usd"])
                    ref = float(rows[max(0, len(rows) - 7)]["open_interest_usd"])  # ~24h @4h bars
                    if ref:
                        rec["oi_chg_24h_pct"] = 100.0 * (last / ref - 1)
                    rec["oi_usd"] = last
                except (KeyError, ValueError, IndexError):
                    pass
        if rec:
            out[base] = rec
    return out


def derivs_summary(bases: list[str]) -> dict[str, dict]:
    """LSR / taker buy-sell / OI change / funding via DerivsHarvester (lazy import)."""
    from core.data_sources.derivs import DerivsHarvester  # lazy: heavy, optional

    snap = DerivsHarvester().snapshot(bases, force=True)
    return {c: d for c, d in (snap or {}).items() if not d.get("stale")}


def build_facts_md(
    intel: dict | None,
    mvrv: dict | None,
    derivs: dict[str, dict] | None,
    fundoi: dict[str, dict] | None,
) -> str:
    """Assemble a compact, numeric facts block (pure -> unit-tested)."""
    L: list[str] = []
    if intel:
        L.append("### Regime (latest brief)")
        if intel.get("fng_value") is not None:
            L.append(f"- Fear & Greed: {intel.get('fng_value')} ({intel.get('fng_class')})")
        if intel.get("btc_dom") is not None:
            L.append(f"- BTC dominance: {intel['btc_dom']:.1f}% | ETH dom: {intel.get('eth_dom')}")
        if intel.get("breadth_green") is not None and intel.get("breadth_total"):
            L.append(f"- Breadth: {intel['breadth_green']}/{intel['breadth_total']} liquid green")
        if intel.get("top_movers"):
            L.append(f"- Top volume: {', '.join(intel['top_movers'][:8])}")
        if intel.get("pos_fund"):
            L.append(f"- Crowded longs (funding+): {', '.join(intel['pos_fund'][:6])}")
        if intel.get("neg_fund"):
            L.append(f"- Crowded shorts (funding-): {', '.join(intel['neg_fund'][:6])}")
    if mvrv:
        L.append("\n### On-chain valuation (BTC MVRV-Z)")
        L.append(
            f"- MVRV-Z = {mvrv['z']:.2f} as of {mvrv['date']} "
            f"({mvrv['pctl']:.0f}th pctl of history) — {mvrv['zone']}"
        )
    if derivs:
        L.append("\n### Positioning (derivs)")
        for c, d in derivs.items():
            L.append(
                f"- {c}: long/short ratio={d.get('lsr')}, taker buy/sell={d.get('taker_ls')}, "
                f"OI 1h chg%={d.get('oi_chg_pct')}, funding={d.get('funding')}"
            )
    if fundoi:
        L.append("\n### Funding & open interest (local history)")
        for c, d in fundoi.items():
            parts = []
            if "funding" in d:
                parts.append(f"funding={d['funding'] * 100:+.4f}%")
            if "oi_chg_24h_pct" in d:
                parts.append(f"OI 24h chg={d['oi_chg_24h_pct']:+.1f}%")
            if parts:
                L.append(f"- {c}: {', '.join(parts)}")
    return "\n".join(L) if L else "_No facts could be assembled this run._"


def synthesize(facts_md: str, now: str, no_llm: bool) -> tuple[str, bool]:
    """Return (note_text, llm_used). With no_llm, returns the raw facts only."""
    if no_llm:
        return ("_(LLM synthesis skipped: --no-llm)_\n\n" + facts_md, False)
    from utils.claude_client import call_claude_cli  # lazy: optional dependency

    user = (
        f"Assembled real-data facts as of {now}:\n\n{facts_md}\n\n"
        "Write the advisory note per the rules. Markdown only, no preamble."
    )
    out = call_claude_cli(
        prompt=user, system_prompt=SYSTEM_PROMPT, model="claude-opus-4-8", effort="low", timeout=180
    )
    if not out:
        return ("_(LLM synthesis unavailable this run; raw facts below.)_\n\n" + facts_md, False)
    return (out.strip(), True)


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--no-llm", action="store_true", help="Assemble facts only; skip Claude")
    args = ap.parse_args(argv)

    failed: list[str] = []
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    intel = latest_market_intel()
    if intel is None:
        failed.append("market_intel brief (run market_intel_report.py first)")
    mvrv = mvrv_context(_safe(fetch_mvrv_z, {}, failed, "MVRV-Z"))
    derivs = _safe(lambda: derivs_summary(args.symbols), {}, failed, "derivs positioning")
    fundoi = _safe(lambda: funding_oi_summary(args.symbols), {}, failed, "funding/OI CSVs")

    facts_md = build_facts_md(intel, mvrv, derivs, fundoi)
    note, llm_used = synthesize(facts_md, now, args.no_llm)

    header = (
        f"# Intel Synthesis — {now}\n\n"
        "_ADVISORY / LOG-ONLY. Synthesized from REAL public data (not simulation). "
        "NOT a trade signal — the bot's gate-validated signals are unaffected. Public "
        "data is priced in; for awareness/research only._\n\n"
        "---\n\n"
    )
    body = header + note + "\n"
    if failed:
        body += f"\n> ⚠ Sources unavailable this run: {', '.join(sorted(set(failed)))}\n"

    out = ROOT / "reports" / f"intel_synthesis_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(body, encoding="utf-8")

    rec = {
        "ts_utc": now,
        "mvrv_z": (mvrv or {}).get("z"),
        "mvrv_zone": (mvrv or {}).get("zone"),
        "fng_value": (intel or {}).get("fng_value"),
        "btc_dom": (intel or {}).get("btc_dom"),
        "llm_used": llm_used,
        "note_len": len(note),
        "failed": sorted(set(failed)),
    }
    try:
        OUT_HISTORY.parent.mkdir(parents=True, exist_ok=True)
        with OUT_HISTORY.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception as e:  # noqa: BLE001
        print(f"  [warn] history append: {str(e)[:80]}")

    print(f"\n=== Intel Synthesis ({now}) — ADVISORY / LOG-ONLY, not a trade signal ===")
    if mvrv:
        print(f"MVRV-Z: {mvrv['z']:.2f} ({mvrv['zone']})")
    print(f"LLM synthesis: {'yes' if llm_used else 'no'} | sources failed: {len(set(failed))}")
    print(f"Note written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
