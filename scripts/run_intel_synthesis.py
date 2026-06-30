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
  * On-chain net   — BTC fees / mempool congestion / difficulty (mempool.space)
  * Positioning    — derivs LSR / taker buy-sell / OI change (DerivsHarvester)
  * Funding / OI   — latest funding + 24h OI change per major from
                     data/funding_oi/<SYM>_*.csv (scripts/fetch_binance_funding_oi.py)

HARD SCOPE — read this:
  * ADVISORY and LOG-ONLY. The output is written to reports/ for HUMAN review
    (optionally emailed with --email). It is NOT a trade signal.
  * The bot's entries are governed solely by gate-validated quantitative signals
    (core/promotion_gate.py). Nothing here can change an entry/exit — this module
    imports NOTHING from the order path and nothing in the order path imports it
    (enforced by a unit test).
  * Public data is priced in; this is for awareness/research only.

Usage:
    python scripts/run_intel_synthesis.py
    python scripts/run_intel_synthesis.py --no-llm      # assemble facts, skip Claude
    python scripts/run_intel_synthesis.py --email       # also email the note (Gmail env)
    python scripts/run_intel_synthesis.py --symbols BTC ETH SOL
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import sys
import urllib.request
from datetime import date, datetime, timezone
from html import escape as _html_escape
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


def _env(key: str, default: str = "") -> str:
    """Read a setting from the environment, falling back to a .env line."""
    val = os.getenv(key, "").strip()
    if val:
        return val
    try:
        for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default


def latest_market_intel(path: Path = INTEL_HISTORY) -> dict | None:
    """Return the most recent snapshot dict from the brief history, or None."""
    if not path.exists():
        return None
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
    except Exception:  # noqa: BLE001 - unattended run must not crash on a bad read
        return None
    for line in reversed(lines):
        try:
            return json.loads(line)
        except json.JSONDecodeError:
            continue
    return None


def brief_is_stale(intel: dict | None, now: datetime, max_age_h: float = 8.0) -> bool:
    """True if the brief's ts_utc is older than max_age_h (MarketIntel cadence is ~4h).

    Returns False when the timestamp is missing/unparseable — we flag a *known*
    stale brief, never guess. Pure (now passed in) so it is unit-testable.
    """
    ts = (intel or {}).get("ts_utc")
    if not ts:
        return False
    try:
        t = datetime.strptime(ts, "%Y-%m-%d %H:%M UTC").replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return False
    return (now - t).total_seconds() > max_age_h * 3600


def _get_json(url: str, timeout: int = 20):
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-research/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


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


def fetch_onchain() -> dict | None:
    """BTC network activity from mempool.space (keyless): fees, mempool, difficulty."""
    fees = _get_json("https://mempool.space/api/v1/fees/recommended")
    mp = _get_json("https://mempool.space/api/mempool")
    out: dict = {
        "fast_fee": fees.get("fastestFee"),
        "hour_fee": fees.get("hourFee"),
        "mempool_count": mp.get("count"),
        "mempool_vsize_mb": round((mp.get("vsize") or 0) / 1_000_000, 1),
    }
    try:
        diff = _get_json("https://mempool.space/api/v1/difficulty-adjustment")
        out["diff_change_pct"] = diff.get("difficultyChange")
    except Exception:
        pass
    return out


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
            with fpath.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            if rows:
                try:
                    rec["funding"] = float(rows[-1]["funding_rate"])
                except (KeyError, ValueError):
                    pass
        opath = directory / f"{base}_oi.csv"
        if opath.exists():
            with opath.open(encoding="utf-8") as fh:
                rows = list(csv.DictReader(fh))
            if rows:
                try:
                    last = float(rows[-1]["open_interest_usd"])
                    # Reference ~24h ago by TIMESTAMP (period-agnostic): correct at any
                    # --oi-period and for merged mixed-cadence CSVs. A fixed row offset
                    # silently breaks when the sampling period differs (it did: the
                    # scheduled task writes 1h bars, not 4h).
                    last_ts = int(rows[-1]["timestamp"])
                    target = last_ts - 24 * 3600 * 1000
                    ref_row = next((r for r in rows if int(r["timestamp"]) >= target), rows[0])
                    ref = float(ref_row["open_interest_usd"])
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
    onchain: dict | None = None,
) -> str:
    """Assemble a compact, numeric facts block (pure -> unit-tested)."""
    L: list[str] = []
    if intel:
        L.append(f"### Regime (latest brief — {intel.get('ts_utc', 'time unknown')})")
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
    if onchain:
        L.append("\n### On-chain network activity (BTC, mempool.space)")
        bits = []
        if onchain.get("fast_fee") is not None:
            bits.append(
                f"fast fee {onchain['fast_fee']} sat/vB (1h tier {onchain.get('hour_fee')})"
            )
        if onchain.get("mempool_count") is not None:
            bits.append(
                f"mempool {onchain['mempool_count']:,} tx / {onchain.get('mempool_vsize_mb')} MB"
            )
        if onchain.get("diff_change_pct") is not None:
            bits.append(f"next difficulty adj {onchain['diff_change_pct']:+.1f}%")
        if bits:
            L.append("- " + "; ".join(bits))
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


def email_note(subject: str, markdown_body: str) -> bool:
    """Email the note via Gmail SMTP (same env vars as core/report_emailer.py).

    Self-contained on purpose (stdlib only) so the advisory layer never imports
    the bot. Returns False (never raises) if creds are absent or the send fails;
    the password is never logged.
    """
    sender, pw, rcpt = _env("GMAIL_SENDER"), _env("GMAIL_APP_PASSWORD"), _env("GMAIL_RECIPIENT")
    if not (sender and pw and rcpt):
        print("  [warn] email: GMAIL_SENDER/APP_PASSWORD/RECIPIENT not configured; skipped")
        return False
    import smtplib
    import ssl
    from email.mime.multipart import MIMEMultipart
    from email.mime.text import MIMEText

    html = (
        "<html><body style='font-family:monospace;background:#0f172a;color:#e2e8f0;padding:20px'>"
        "<pre style='white-space:pre-wrap;font-size:13px;line-height:1.5'>"
        + _html_escape(markdown_body)
        + "</pre></body></html>"
    )
    msg = MIMEMultipart("alternative")
    msg["Subject"], msg["From"], msg["To"] = subject, sender, rcpt
    msg.attach(MIMEText(markdown_body, "plain", "utf-8"))
    msg.attach(MIMEText(html, "html", "utf-8"))
    try:
        ctx = ssl.create_default_context()
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, context=ctx, timeout=30) as s:
            s.login(sender, pw)
            s.sendmail(sender, [rcpt], msg.as_string())
        print(f"  [email] sent to {rcpt}")
        return True
    except Exception as e:  # noqa: BLE001 - never let email failure crash the run
        print(f"  [warn] email send failed: {str(e)[:100]}")
        return False


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--symbols", nargs="+", default=DEFAULT_SYMBOLS)
    ap.add_argument("--no-llm", action="store_true", help="Assemble facts only; skip Claude")
    ap.add_argument("--email", action="store_true", help="Also email the note (Gmail env vars)")
    args = ap.parse_args(argv)

    failed: list[str] = []
    now_dt = datetime.now(timezone.utc)
    now = now_dt.strftime("%Y-%m-%d %H:%M UTC")

    intel = latest_market_intel()
    if intel is None:
        failed.append("market_intel brief (run market_intel_report.py first)")
    elif brief_is_stale(intel, now_dt):
        failed.append(f"market_intel brief STALE ({intel.get('ts_utc', '?')})")
    mvrv = mvrv_context(_safe(fetch_mvrv_z, {}, failed, "MVRV-Z"))
    onchain = _safe(fetch_onchain, None, failed, "on-chain (mempool.space)")
    derivs = _safe(lambda: derivs_summary(args.symbols), {}, failed, "derivs positioning")
    fundoi = _safe(lambda: funding_oi_summary(args.symbols), {}, failed, "funding/OI CSVs")

    facts_md = build_facts_md(intel, mvrv, derivs, fundoi, onchain)
    note, llm_used = _safe(
        lambda: synthesize(facts_md, now, args.no_llm),
        ("_(LLM synthesis unavailable this run; raw facts below.)_\n\n" + facts_md, False),
        failed,
        "LLM synthesis",
    )

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
    try:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(body, encoding="utf-8")
    except Exception as e:  # noqa: BLE001 - report write must not crash the run
        print(f"  [warn] failed to write report: {str(e)[:80]}")
        failed.append("report write")

    emailed = (
        email_note(f"[TradingBot] Daily Intel Synthesis — {date.today().isoformat()}", body)
        if args.email
        else False
    )

    rec = {
        "ts_utc": now,
        "mvrv_z": (mvrv or {}).get("z"),
        "mvrv_zone": (mvrv or {}).get("zone"),
        "btc_fast_fee": (onchain or {}).get("fast_fee"),
        "fng_value": (intel or {}).get("fng_value"),
        "btc_dom": (intel or {}).get("btc_dom"),
        "llm_used": llm_used,
        "emailed": emailed,
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
    if onchain and onchain.get("fast_fee") is not None:
        print(
            f"BTC fast fee: {onchain['fast_fee']} sat/vB | mempool {onchain.get('mempool_count')}"
        )
    print(
        f"LLM synthesis: {'yes' if llm_used else 'no'} | emailed: {emailed} | "
        f"sources failed: {len(set(failed))}"
    )
    print(f"Note written to: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
