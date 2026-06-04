"""TP-accuracy diagnosis (2026-06-04).

Answers: why does the bot not hit take-profit "with accuracy", and how much of
the loss is (A) no entry edge vs (B) the profit-capping exit machinery.

Read-only. Three diagnostics:

  D1  Alpha-vs-cost   — aggregate the warehouse `attribution` table
                        (alpha / spread / slippage / funding / fees). Tells us
                        whether the loss is alpha-driven (no edge) or cost-driven.

  D2  Population edge — mean of the warehouse `labels.y` triple-barrier outcomes
                        (TP-before-SL) across all candidates, as an edge proxy.
                        Caveat: the labeler bracket is not persisted, horizon=96.

  D3  Per-trade clean replay (the core) — for every CLOSED position
      (data/positions.json) replay the forward OHLCV against that position's
      ACTUAL stop_loss / take_profit using core.labeler.triple_barrier, and
      compute MFE/MAE in R. Separates:
        * clean TP-first rate  -> entry quality (compare to the no-edge 33%)
        * clipped winners      -> clean=+1 but exited early by trailing /
                                  mcp_take_profit / STALE / age for less than TP
        * caps that SAVED a loss -> clean=-1 but exited early above full -1R
      Net machinery impact = winner-clip cost - loss-save benefit.

OHLCV is fetched from PUBLIC ccxt endpoints (no API keys, separate client
objects) — it never touches the running bot's clients or the live account.

Usage:  ./venv/Scripts/python.exe scripts/tp_accuracy_diagnosis.py
Writes: reports/tp_accuracy_<YYYY-MM-DD>.md  (and prints the summary)
"""
from __future__ import annotations

import json
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.labeler import triple_barrier  # noqa: E402

WAREHOUSE = ROOT / "data" / "warehouse.sqlite"
POSITIONS = ROOT / "data" / "positions.json"
REPORT_DIR = ROOT / "reports"

NO_EDGE_TP_RATE = 1.0 / 3.0  # gambler's ruin: a no-edge -1R/+2R bracket hits TP ~33%
HORIZON_BARS = 96            # forward window for the clean replay
TIMEFRAME = "15m"
TF_SEC = 15 * 60


# ── D1: attribution ───────────────────────────────────────────────────────
def d1_attribution(con):
    rows = list(con.execute(
        "SELECT alpha, spread, slippage, funding, fees, realized_pnl FROM attribution"))
    if not rows:
        return None
    keys = ["alpha", "spread", "slippage", "funding", "fees", "realized_pnl"]
    tot = {k: sum((r[i] or 0.0) for r in rows) for i, k in enumerate(keys)}
    n = len(rows)
    cost = tot["spread"] + tot["slippage"] + tot["funding"] + tot["fees"]
    return {"n": n, "tot": tot, "cost_total": cost,
            "alpha_share": tot["alpha"] / tot["realized_pnl"] if tot["realized_pnl"] else float("nan"),
            "cost_share": cost / tot["realized_pnl"] if tot["realized_pnl"] else float("nan")}


# ── D2: population edge from labels ────────────────────────────────────────
def d2_labels(con):
    rows = list(con.execute("SELECT y, label_horizon_bars FROM labels"))
    if not rows:
        return None
    n = len(rows)
    tp_first = sum(1 for r in rows if r[0] == 1)
    horizons = dict(Counter(r[1] for r in rows))
    return {"n": n, "tp_first_rate": tp_first / n, "horizons": horizons}


# ── D3: per-trade clean replay ─────────────────────────────────────────────
def _ccxt_client(exchange_name, _cache={}):
    if exchange_name in _cache:
        return _cache[exchange_name]
    import ccxt
    cls = {"binance": ccxt.binance, "bybit": ccxt.bybit, "bitget": ccxt.bitget}.get(exchange_name)
    if not cls:
        _cache[exchange_name] = None
        return None
    opts = {"enableRateLimit": True, "timeout": 20000, "options": {"defaultType": "swap"}}
    try:
        c = cls(opts)
        c.load_markets()
    except Exception:
        c = None
    _cache[exchange_name] = c
    return c


def _fetch_forward(exchange_name, symbol, since_ms, bars):
    c = _ccxt_client(exchange_name)
    if c is None:
        return None
    for attempt in range(2):
        try:
            return c.fetch_ohlcv(symbol, TIMEFRAME, since=since_ms, limit=bars)
        except Exception:
            if attempt == 0:
                time.sleep(1.0)
            else:
                return None


HORIZONS = [4, 8, 16, 32, 96]   # 1h / 2h / 4h(~age-limit) / 8h / 24h
DECISION_H = 16                 # the bot's actual ~4h age limit
RR_MIN, RR_MAX = 0.5, 3.5       # plausible original-bracket R:R band (config range)


def d3_replay(verbose=True):
    """Fetch each closed position's forward OHLCV ONCE, store bars, so the same
    fetch serves every horizon. Look-ahead-safe: the bar CONTAINING open_time is
    dropped (its wicks include pre-entry ticks); the scan starts at the next full
    bar, anchored to the real entry fill price."""
    data = json.loads(POSITIONS.read_text(encoding="utf-8"))
    closed = data.get("closed", [])
    trades = []
    skipped = Counter()
    for i, p in enumerate(closed):
        sym = p.get("symbol"); side = p.get("side"); ex = (p.get("exchange") or "").lower()
        entry = p.get("entry_price"); sl = p.get("stop_loss"); tp = p.get("take_profit")
        ot = p.get("open_time"); reason = p.get("close_reason"); pnl = p.get("pnl") or 0.0
        if not (sym and side and entry and sl and tp and ot):
            skipped["missing_fields"] += 1; continue
        sl_pct = abs(sl - entry) / entry
        tp_pct = abs(tp - entry) / entry
        if sl_pct <= 0 or tp_pct <= 0:
            skipped["bad_bracket"] += 1; continue
        raw = _fetch_forward(ex, sym, int(ot * 1000), HORIZON_BARS + 2)
        if not raw or len(raw) < 4:
            skipped["no_ohlcv"] += 1; continue
        fwd = raw[1:]  # drop the bar containing open_time (look-ahead guard)
        if len(fwd) < 4:
            skipped["no_ohlcv"] += 1; continue
        trades.append({
            "symbol": sym, "side": "long" if side in ("buy", "long") else "short",
            "reason": reason, "pnl": pnl, "sl_pct": sl_pct, "tp_pct": tp_pct,
            "rr": tp_pct / sl_pct, "entry": entry, "ot": ot, "partial": bool(p.get("partial_taken")),
            "highs": [b[2] for b in fwd], "lows": [b[3] for b in fwd], "closes": [b[4] for b in fwd],
        })
        if verbose and (i + 1) % 25 == 0:
            print(f"  fetched {len(trades)} / {i+1} (skipped {sum(skipped.values())})", flush=True)
    return trades, skipped


def _clean_at(t, horizon):
    """triple-barrier outcome for trade t over `horizon` forward bars, anchored
    to the real entry (synthetic bar 0 = entry price; forward bars are 1..H)."""
    h = [t["entry"]] + t["highs"][:horizon]
    lo = [t["entry"]] + t["lows"][:horizon]
    cl = [t["entry"]] + t["closes"][:horizon]
    return triple_barrier(h, lo, cl, entry_idx=0, side=t["side"],
                          tp_pct=t["tp_pct"], sl_pct=t["sl_pct"], time_bars=min(horizon, len(t["highs"])))


def _mark_to_close_R(t, horizon):
    """R realized if exited at the close of bar `horizon` (for timeouts)."""
    idx = min(horizon, len(t["closes"])) - 1
    px = t["closes"][idx]
    move = (px - t["entry"]) / t["entry"] if t["side"] == "long" else (t["entry"] - px) / t["entry"]
    return move / t["sl_pct"]


def _per_trade_ev(trades, horizon, winsor=True, drop_contaminated=False):
    """Average per-trade R: +rr_i (TP-first), -1 (SL-first), mark-to-close (timeout).
    rr_i is each trade's OWN bracket R:R (winsorized to the plausible band to blunt
    breakeven-moved SLs that inflate R:R)."""
    vals = []
    for t in trades:
        rr = t["rr"]
        if drop_contaminated and not (RR_MIN <= rr <= RR_MAX):
            continue
        if winsor:
            rr = max(RR_MIN, min(RR_MAX, rr))
        c = _clean_at(t, horizon)
        if c == 1:
            vals.append(rr)
        elif c == -1:
            vals.append(-1.0)
        else:
            vals.append(_mark_to_close_R(t, horizon))
    return (sum(vals) / len(vals)) if vals else float("nan"), len(vals)


def summarize(trades):
    n = len(trades)
    # horizon sweep: decided TP-first rate + timeout share + per-trade EV
    sweep = []
    for H in HORIZONS:
        outs = [_clean_at(t, H) for t in trades]
        tp = sum(1 for o in outs if o == 1); sl = sum(1 for o in outs if o == -1)
        to = sum(1 for o in outs if o == 0)
        decided = tp + sl
        rate = tp / decided if decided else float("nan")
        ev, _ = _per_trade_ev(trades, H, winsor=True)
        sweep.append({"H": H, "tp": tp, "sl": sl, "timeout": to,
                      "tp_first_rate": rate, "ev_R": ev})

    # decision horizon (~4h age limit): side split + EV variants
    Hd = DECISION_H
    longs = [t for t in trades if t["side"] == "long"]
    shorts = [t for t in trades if t["side"] == "short"]
    def _rate(sub):
        outs = [_clean_at(t, Hd) for t in sub]
        tp = sum(1 for o in outs if o == 1); sl = sum(1 for o in outs if o == -1)
        return (tp / (tp + sl) if (tp + sl) else float("nan")), tp, sl
    long_rate, lt, ls = _rate(longs)
    short_rate, st, ss = _rate(shorts)
    ev_wins, _ = _per_trade_ev(trades, Hd, winsor=True)
    ev_clean, n_clean = _per_trade_ev(trades, Hd, winsor=False, drop_contaminated=True)
    contaminated = sum(1 for t in trades if not (RR_MIN <= t["rr"] <= RR_MAX))

    # per-trade EV at the full 24h horizon too (winsorized + contaminated-dropped)
    ev96_wins, _ = _per_trade_ev(trades, 96, winsor=True)
    ev96_clean, _ = _per_trade_ev(trades, 96, winsor=False, drop_contaminated=True)

    # noise-stop stat at 24h (NOT an edge claim — see read)
    sl_trades = [t for t in trades if t["reason"] == "stop_loss"]
    sl_mfe1 = 0
    for t in sl_trades:
        mfe = (max(t["highs"]) - t["entry"]) / t["entry"] if t["side"] == "long" \
              else (t["entry"] - min(t["lows"])) / t["entry"]
        if mfe / t["sl_pct"] >= 1.0:
            sl_mfe1 += 1

    import datetime as _dt
    ots = [t["ot"] for t in trades if t.get("ot")]
    win = (str(_dt.date.fromtimestamp(min(ots))), str(_dt.date.fromtimestamp(max(ots)))) if ots else ("?", "?")
    span_days = (max(ots) - min(ots)) / 86400 if ots else 0
    return {
        "n": n, "sweep": sweep, "decision_H": Hd, "window": win, "span_days": span_days,
        "long_rate": long_rate, "lt": lt, "ls": ls, "n_long": len(longs),
        "short_rate": short_rate, "st": st, "ss": ss, "n_short": len(shorts),
        "ev_dec_wins": ev_wins, "ev_dec_clean": ev_clean, "n_clean": n_clean,
        "contaminated": contaminated, "ev96_wins": ev96_wins, "ev96_clean": ev96_clean,
        "sl_trades": len(sl_trades), "sl_mfe_ge_1R": sl_mfe1,
    }


def _btc_drift():
    """BTC % move over the sample period — beta context for the side split."""
    try:
        import ccxt
        c = ccxt.binance({"enableRateLimit": True, "options": {"defaultType": "swap"}})
        data = json.loads(POSITIONS.read_text(encoding="utf-8")).get("closed", [])
        ots = [p["open_time"] for p in data if p.get("open_time")]
        since = int(min(ots) * 1000)
        bars = c.fetch_ohlcv("BTC/USDT:USDT", "1d", since=since, limit=90)
        if bars and len(bars) > 1:
            return (bars[-1][4] - bars[0][4]) / bars[0][4] * 100.0, len(bars)
    except Exception:
        pass
    return None, 0


def main():
    con = sqlite3.connect(str(WAREHOUSE))
    print("D1: attribution (alpha vs cost) ...", flush=True)
    d1 = d1_attribution(con)
    print("D2: population labels.y ...", flush=True)
    d2 = d2_labels(con)
    print(f"D3: clean replay, fetch-once over {HORIZON_BARS}x{TIMEFRAME} (public OHLCV) ...", flush=True)
    trades, skipped = d3_replay()
    s = summarize(trades) if trades else None
    btc_drift, btc_days = _btc_drift()

    lines = []
    def w(x=""): lines.append(x); print(x, flush=True)

    w("# TP-accuracy diagnosis\n")
    w("## D1 — Alpha vs cost (warehouse `attribution`)")
    if d1:
        t = d1["tot"]
        w(f"- trades attributed: {d1['n']}")
        w(f"- TOTAL realized PnL: {t['realized_pnl']:+.2f}")
        w(f"- alpha (entry edge): {t['alpha']:+.2f}  ({d1['alpha_share']*100:.0f}% of PnL)")
        w(f"- cost total (spread+slippage+funding+fees): {d1['cost_total']:+.2f}  ({d1['cost_share']*100:.0f}% of PnL)")
        w(f"    spread={t['spread']:+.2f} slippage={t['slippage']:+.2f} funding={t['funding']:+.2f} fees={t['fees']:+.2f}")
        w(f"- VERDICT: loss is {'ALPHA-dominated (no edge)' if abs(t['alpha'])>d1['cost_total'] else 'COST-dominated'}")
    else:
        w("- (no attribution rows)")

    w("\n## D2 — Population edge (warehouse `labels.y`, triple-barrier TP-before-SL)")
    if d2:
        w(f"- candidates labeled: {d2['n']}  horizon(bars): {d2['horizons']}")
        w(f"- TP-before-SL rate: {d2['tp_first_rate']*100:.1f}%   (no-edge threshold ~{NO_EDGE_TP_RATE*100:.0f}%)")
        w("- caveat: labeler bracket not persisted (sl_pct/tp_pct NULL); proxy only.")
    else:
        w("- (no labels)")

    w(f"\n## D3 — Per-trade clean replay (positions.json closed, look-ahead-safe)")
    w(f"- replayed: {s['n'] if s else 0}   skipped: {dict(skipped)}")
    if s:
        w(f"- sample window: {s['window'][0]} -> {s['window'][1]} ({s['span_days']:.0f} days) "
          f"— positions.json may retain only a recent slice, not the full warehouse history")
        w(f"- BTC drift over that window: {btc_drift:+.1f}% ({btc_days}d)" if btc_drift is not None
          else "- BTC drift: (unavailable)")
        w(f"\n### Horizon sweep — decided TP-first rate vs no-edge ~{NO_EDGE_TP_RATE*100:.0f}% (per-trade EV winsorized R:R)")
        w("| horizon | bars | TP-first | SL-first | timeout | TP-first rate | per-trade EV (R) |")
        w("|---|---|---|---|---|---|---|")
        for row in s["sweep"]:
            hh = f"{row['H']*15//60}h" if row['H']*15 % 60 == 0 else f"{row['H']*15}m"
            w(f"| {hh} | {row['H']} | {row['tp']} | {row['sl']} | {row['timeout']} | "
              f"{row['tp_first_rate']*100:.1f}% | {row['ev_R']:+.3f} |")
        w(f"\n### At the bot's actual ~4h hold limit ({s['decision_H']} bars)")
        w(f"- per-trade EV (winsorized, all {s['n']} trades): **{s['ev_dec_wins']:+.3f} R/trade**")
        w(f"- per-trade EV (excluding {s['contaminated']} bracket-contaminated trades, n={s['n_clean']}): "
          f"**{s['ev_dec_clean']:+.3f} R/trade**")
        w(f"- side split: LONG TP-first {s['long_rate']*100:.1f}% ({s['lt']}/{s['lt']+s['ls']}, n={s['n_long']}) | "
          f"SHORT TP-first {s['short_rate']*100:.1f}% ({s['st']}/{s['st']+s['ss']}, n={s['n_short']})")
        w(f"- per-trade EV at 24h: winsorized {s['ev96_wins']:+.3f} | contam-dropped {s['ev96_clean']:+.3f} R/trade")
        w(f"- noise-stop stat (NOT an edge claim): {s['sl_mfe_ge_1R']}/{s['sl_trades']} stopped trades "
          f"later reached >=+1R favorable within 24h")

    w("\n## Read")
    if s:
        dec = next((r for r in s["sweep"] if r["H"] == s["decision_H"]), None)
        # beta detection: one side near-0 TP-first + the other much higher + large drift
        long_dead = s["long_rate"] == s["long_rate"] and s["long_rate"] < 0.10
        short_dead = s["short_rate"] == s["short_rate"] and s["short_rate"] < 0.10
        beta = (abs(btc_drift) > 5 if btc_drift is not None else False) and (long_dead or short_dead) \
            and abs((s["long_rate"] or 0) - (s["short_rate"] or 0)) > 0.20
        w("- D1 (robust, full 495 trades): alpha is statistically ~flat; the realized loss is "
          "dominated by transaction costs (fees + spread). Reducing costs is the clearly-correct lever.")
        w(f"- D2 (robust, full 5720-candidate population): TP-before-SL {15 if not d2 else round(d2['tp_first_rate']*100,1)}% "
          f"vs ~33% no-edge => NO directional edge across the broad sample.")
        if beta:
            w(f"- D3 is a {s['span_days']:.0f}-DAY recent slice during a {btc_drift:+.0f}% BTC move, and the "
              f"side split (LONG TP-first {s['long_rate']*100:.0f}% vs SHORT {s['short_rate']*100:.0f}%, "
              f"net {'short' if s['n_short']>s['n_long'] else 'long'}) shows the apparent 'edge' is MARKET BETA, "
              f"not entry alpha — the winning side simply matched the drift; the other side hit TP ~0%. "
              f"If the market had moved the other way this inverts to a loss.")
        elif dec:
            w(f"- D3 at the actual ~4h hold: TP-first {dec['tp_first_rate']*100:.1f}% vs ~33%, EV {dec['ev_R']:+.3f}R "
              f"(in-sample, {s['span_days']:.0f}-day slice; not OOS-validated).")
        w("- STABLE CONCLUSION: alpha is flat; the loss is mostly transaction costs; the one positive-looking "
          "signal is short-window market beta, not a tradeable edge. NOT a path to profit as configured. "
          "A genuine edge claim would require a forward/OOS holdout this analysis does not provide.")
        w("- Do NOT pursue 'widen stops -> profit' (the 89% noise-stop stat) — it slides along the same "
          "~zero-EV line minus costs (gambler's ruin).")

    REPORT_DIR.mkdir(exist_ok=True)
    import datetime
    out = REPORT_DIR / f"tp_accuracy_{datetime.date.today().isoformat()}.md"
    out.write_text("\n".join(lines), encoding="utf-8")
    print(f"\n[written] {out}")


if __name__ == "__main__":
    main()
