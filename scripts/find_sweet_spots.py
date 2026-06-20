"""Find + record conditional 'sweet spots' in the bot's PAPER trade history.

HONEST METHODOLOGY (anti-data-mining). The bot is in a CONFIRMED no-edge regime, so
slicing ~600 trades across many dimensions WILL surface good-looking slices by pure
chance (multiple-comparisons / overfitting). To avoid recording noise as signal, every
candidate is found IN-SAMPLE (older 60% of trades) and then CHECKED OUT-OF-SAMPLE
(newer 40%). A slice counts as a real candidate ONLY if it is positive in BOTH halves
with a minimum sample. Expect most/all to wash out — that washout IS the honest finding
and is itself recorded.

Dimensions: hour-of-day (UTC), side, base, mcp_score bucket, strategy_family,
hold-time bucket. Metric: per-trade realized PnL (USD), win-rate, count.
Output: reports/sweet_spots_<date>.md  (durable record). Run anytime; safe + read-only.
"""

from __future__ import annotations

import sqlite3
import sys
from datetime import date, datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

MIN_N = 8  # min trades in a slice to even consider it (both halves)
IS_FRAC = 0.60  # chronological in-sample fraction


def _load():
    con = sqlite3.connect(str(ROOT / "data" / "warehouse.sqlite"))
    cur = con.cursor()
    q = (
        "SELECT ts_entry, symbol, side, strategy_family, realized_pnl, r_multiple, "
        "hold_sec, mcp_score, exit_reason FROM trades "
        "WHERE mode='PAPER' AND status='CLOSED' AND realized_pnl IS NOT NULL "
        "ORDER BY ts_entry"
    )
    out = []
    for ts, sym, side, fam, pnl, r, hold, score, exitr in cur.execute(q):
        try:
            tsf = float(ts)
            if tsf > 1e12:
                tsf /= 1000.0
        except (TypeError, ValueError):
            continue
        out.append(
            {
                "ts": tsf,
                "base": str(sym).split("/")[0].split(":")[0],
                "side": (side or "?").lower(),
                "fam": fam or "?",
                "pnl": float(pnl),
                "r": (float(r) if r is not None else None),
                "hold": (float(hold) if hold is not None else None),
                "score": (float(score) if score is not None else None),
                "hour": datetime.fromtimestamp(tsf, timezone.utc).hour,
            }
        )
    return out


def _stats(trades):
    n = len(trades)
    if n == 0:
        return (0, 0.0, 0.0)
    wins = sum(1 for t in trades if t["pnl"] > 0)
    avg = sum(t["pnl"] for t in trades) / n
    return (n, 100.0 * wins / n, avg)


def _score_bucket(s):
    if s is None:
        return None
    if s < 65:
        return "<65"
    if s < 75:
        return "65-75"
    if s < 85:
        return "75-85"
    return "85+"


def _hold_bucket(h):
    if h is None:
        return None
    m = h / 60.0
    if m < 60:
        return "<1h"
    if m < 240:
        return "1-4h"
    if m < 720:
        return "4-12h"
    return ">12h"


def _dimension(trades, key):
    groups = {}
    for t in trades:
        v = key(t)
        if v is None:
            continue
        groups.setdefault(v, []).append(t)
    return groups


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    trades = _load()
    if len(trades) < 40:
        print(f"Only {len(trades)} closed PAPER trades — too few to slice honestly.")
        return 0

    split = int(len(trades) * IS_FRAC)
    is_t, oos_t = trades[:split], trades[split:]
    base_n, base_wr, base_avg = _stats(trades)
    is_n, is_wr, is_avg = _stats(is_t)
    oos_n, oos_wr, oos_avg = _stats(oos_t)

    DIMS = [
        ("Hour of day (UTC)", lambda t: t["hour"]),
        ("Side", lambda t: t["side"]),
        ("Base", lambda t: t["base"]),
        ("MCP score bucket", lambda t: _score_bucket(t["score"])),
        ("Strategy family", lambda t: t["fam"]),
        # Hold-time DROPPED (audit #2): hold_sec is known only at EXIT and is jointly determined
        # with PnL, so slicing by it conditions on the outcome and is not actionable at entry.
    ]

    held, washed = [], []
    dim_tables = []
    for name, key in DIMS:
        is_groups = _dimension(is_t, key)
        oos_groups = _dimension(oos_t, key)
        rows = []
        for v, ts in sorted(is_groups.items(), key=lambda kv: _stats(kv[1])[2], reverse=True):
            isn, iswr, isavg = _stats(ts)
            if isn < MIN_N:
                continue
            on, owr, oavg = _stats(oos_groups.get(v, []))
            is_sweet = isavg > 0 and iswr >= 50
            holds = is_sweet and on >= MIN_N and oavg > 0
            rows.append((v, isn, iswr, isavg, on, owr, oavg, is_sweet, holds))
            if is_sweet:
                rec = {
                    "dimension": name,
                    "value": v,
                    "is_n": isn,
                    "is_wr": round(iswr, 1),
                    "is_avg_pnl": round(isavg, 4),
                    "oos_n": on,
                    "oos_wr": round(owr, 1),
                    "oos_avg_pnl": round(oavg, 4),
                }
                (held if holds else washed).append(rec)
        dim_tables.append((name, rows))

    # ---- report ----
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    L = [
        f"# Trading 'Sweet Spots' — conditional performance ({now})",
        "",
        "_Anti-data-mining: candidates are found IN-SAMPLE (older 60%) and only count if "
        "they ALSO hold OUT-OF-SAMPLE (newer 40%). On a no-edge sample, in-sample slices that "
        "look good are usually noise; an honest sweet spot must survive OOS._",
        "",
        "_Scope caveat (audit #4/#15): this conditions on the bot's OWN executed trades, so it "
        "can only attribute which TAKEN trades did better — it cannot discover an edge in "
        "untaken conditions. 'Held' = positive in BOTH halves (sign agreement only), NOT a "
        "significance test; treat any survivor as a weak hypothesis, never go-live evidence._",
        "",
        f"**Sample:** {base_n} closed PAPER trades | overall WR {base_wr:.0f}% | "
        f"avg PnL ${base_avg:+.3f}/trade  (IS {is_n}@{is_wr:.0f}%/${is_avg:+.3f}, "
        f"OOS {oos_n}@{oos_wr:.0f}%/${oos_avg:+.3f})",
        "",
        f"## Verdict: {len(held)} sweet spot(s) HELD out-of-sample, {len(washed)} washed out",
        "",
    ]

    if held:
        L.append("### ✅ Held in BOTH halves (candidates — still need more data to trust)")
        L += [
            "| dimension | value | IS n | IS WR | IS $/t | OOS n | OOS WR | OOS $/t |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in held:
            L.append(
                f"| {r['dimension']} | {r['value']} | {r['is_n']} | {r['is_wr']}% | "
                f"${r['is_avg_pnl']:+.3f} | {r['oos_n']} | {r['oos_wr']}% | ${r['oos_avg_pnl']:+.3f} |"
            )
        L.append("")
    else:
        L.append("### No in-sample sweet spot survived out-of-sample.")
        L.append(
            "_This is the expected, honest result in a no-edge regime: the good-looking "
            "in-sample slices were data-mining noise, not durable conditions._"
        )
        L.append("")

    if washed:
        L.append("### ⚠ Looked good in-sample, FAILED out-of-sample (do NOT trade these — overfit)")
        L += [
            "| dimension | value | IS n | IS WR | IS $/t | OOS n | OOS WR | OOS $/t |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for r in sorted(washed, key=lambda x: x["is_avg_pnl"], reverse=True)[:15]:
            L.append(
                f"| {r['dimension']} | {r['value']} | {r['is_n']} | {r['is_wr']}% | "
                f"${r['is_avg_pnl']:+.3f} | {r['oos_n']} | {r['oos_wr']}% | ${r['oos_avg_pnl']:+.3f} |"
            )
        L.append("")

    L.append("## Full per-dimension breakdown (in-sample, slices with n>=%d)" % MIN_N)
    for name, rows in dim_tables:
        if not rows:
            continue
        L.append(f"### {name}")
        L += [
            "| value | IS n | IS WR | IS $/t | OOS n | OOS WR | OOS $/t | flag |",
            "|---|---|---|---|---|---|---|---|",
        ]
        for v, isn, iswr, isavg, on, owr, oavg, sweet, holds in rows:
            flag = "HELD" if holds else ("overfit" if sweet else "")
            L.append(
                f"| {v} | {isn} | {iswr:.0f}% | ${isavg:+.3f} | {on} | {owr:.0f}% | "
                f"${oavg:+.3f} | {flag} |"
            )
        L.append("")

    L.append("---")
    L.append(
        "_Recorded by scripts/find_sweet_spots.py. Descriptive backward-looking analysis "
        "of realized PnL by condition — NOT a forward trade signal. 'Held' candidates are "
        "hypotheses on small samples, not validated edges; re-check as trade count grows._"
    )

    out = ROOT / "reports" / f"sweet_spots_{date.today().isoformat()}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("\n".join(L), encoding="utf-8")

    print(f"=== Sweet-spot scan ({now}) ===")
    print(f"Sample: {base_n} closed PAPER trades | WR {base_wr:.0f}% | ${base_avg:+.3f}/trade")
    print(f"VERDICT: {len(held)} held out-of-sample, {len(washed)} washed out (overfit)")
    for r in held:
        print(
            f"  HELD: {r['dimension']}={r['value']}  IS {r['is_n']}@{r['is_wr']}%/${r['is_avg_pnl']:+.3f}"
            f"  OOS {r['oos_n']}@{r['oos_wr']}%/${r['oos_avg_pnl']:+.3f}"
        )
    print(f"\nFull record: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
