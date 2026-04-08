"""
backtest_split.py — 70/30 Train/Test Strategy Backtester

Splits historical trade data into:
  TRAIN (70%): Used to optimize strategy parameters & learn patterns
  TEST  (30%): Held out to validate that learned patterns generalise

Usage:
    python backtest_split.py              # Run on all profiles
    python backtest_split.py --profile aggressive
    python backtest_split.py --days 60    # Use last 60 days of trades

Output:
    data/backtest/train_test_report.json
    data/backtest/train_test_report.html  (opens in browser)

Metrics computed on each split:
    Win rate, net PnL, avg win, avg loss, profit factor,
    max consecutive wins/losses, Sharpe ratio, max drawdown,
    per-strategy breakdown, per-exchange breakdown
"""

import json
import math
import os
import webbrowser
from collections import defaultdict
from datetime    import datetime
from pathlib     import Path

PROFILES     = ["conservative", "moderate", "aggressive"]
TRAIN_RATIO  = 0.70   # 70% train, 30% test
REPORT_DIR   = Path("data/backtest")
REPORT_JSON  = REPORT_DIR / "train_test_report.json"
REPORT_HTML  = REPORT_DIR / "train_test_report.html"


# ── Data loading ──────────────────────────────────────────────────────

def load_all_trades(profile_filter: str = None) -> list:
    """Load all closed trades from positions.json files."""
    all_trades = []
    seen_ids   = set()

    def _ingest(path: Path, profile: str):
        if not path.exists():
            return
        try:
            data   = json.loads(path.read_text(encoding="utf-8"))
            closed = data.get("closed", [])
            for t in closed:
                if t.get("exit_price") is None:
                    continue
                pid = t.get("id", "")
                if pid in seen_ids:
                    continue
                seen_ids.add(pid)
                t["_profile"] = profile
                all_trades.append(t)
        except Exception as e:
            print(f"  Warning: Could not read {path}: {e}")

    # Single-bot positions
    _ingest(Path("data/positions.json"), "single")

    # Multi-profile positions
    for prof in PROFILES:
        if profile_filter and prof != profile_filter:
            continue
        _ingest(Path(f"data/profiles/{prof}/positions.json"), prof)

    # Sort chronologically by close_time
    all_trades.sort(key=lambda t: t.get("close_time", 0))
    return all_trades


# ── Split ─────────────────────────────────────────────────────────────

def split_train_test(trades: list, ratio: float = TRAIN_RATIO) -> tuple:
    """Split trades chronologically. First 70% = train, last 30% = test."""
    n     = len(trades)
    split = int(n * ratio)
    train = trades[:split]
    test  = trades[split:]
    return train, test


# ── Metrics calculation ──────────────────────────────────────────────

def calculate_metrics(trades: list, label: str) -> dict:
    """Calculate comprehensive trading metrics for a set of trades."""
    if not trades:
        return {"label": label, "n": 0}

    pnls     = [t.get("pnl", 0) or 0 for t in trades]
    wins     = [p for p in pnls if p > 0]
    losses   = [p for p in pnls if p <= 0]
    n        = len(trades)
    n_wins   = len(wins)
    n_losses = len(losses)

    total_pnl  = sum(pnls)
    gross_pnl  = sum(t.get("gross_pnl", 0) or 0 for t in trades)
    total_fees = sum(t.get("total_fees", 0) or 0 for t in trades)
    win_rate   = (n_wins / n * 100) if n > 0 else 0
    avg_win    = (sum(wins) / n_wins) if n_wins else 0
    avg_loss   = (sum(losses) / n_losses) if n_losses else 0

    # Profit factor = gross wins / |gross losses|
    gross_wins   = sum(wins)
    gross_losses = abs(sum(losses))
    profit_factor = (gross_wins / gross_losses) if gross_losses > 0 else float('inf')

    # Max consecutive wins/losses
    max_consec_wins = max_consec_losses = 0
    current_wins = current_losses = 0
    for p in pnls:
        if p > 0:
            current_wins += 1
            current_losses = 0
            max_consec_wins = max(max_consec_wins, current_wins)
        else:
            current_losses += 1
            current_wins = 0
            max_consec_losses = max(max_consec_losses, current_losses)

    # Sharpe ratio (annualised, assuming ~3 trades/day)
    sharpe = 0.0
    if len(pnls) >= 2:
        avg = sum(pnls) / len(pnls)
        std = math.sqrt(sum((x - avg) ** 2 for x in pnls) / len(pnls))
        if std > 0:
            sharpe = (avg / std) * math.sqrt(252 * 3)  # annualised

    # Max drawdown
    running = 0.0
    peak    = 0.0
    max_dd  = 0.0
    for p in pnls:
        running += p
        if running > peak:
            peak = running
        dd = peak - running
        if dd > max_dd:
            max_dd = dd

    # Per-strategy breakdown
    by_strategy = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        raw_s = (t.get("strategy") or "unknown").split("|")[0]
        by_strategy[raw_s]["n"]   += 1
        by_strategy[raw_s]["pnl"] += t.get("pnl", 0) or 0
        if (t.get("pnl", 0) or 0) > 0:
            by_strategy[raw_s]["wins"] += 1

    strat_data = {}
    for s, d in by_strategy.items():
        wr = (d["wins"] / d["n"] * 100) if d["n"] > 0 else 0
        strat_data[s] = {
            "trades":   d["n"],
            "wins":     d["wins"],
            "win_rate": round(wr, 1),
            "net_pnl":  round(d["pnl"], 4),
        }

    # Per-exchange breakdown
    by_exchange = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
    for t in trades:
        ex = (t.get("exchange") or "unknown").lower()
        by_exchange[ex]["n"]   += 1
        by_exchange[ex]["pnl"] += t.get("pnl", 0) or 0
        if (t.get("pnl", 0) or 0) > 0:
            by_exchange[ex]["wins"] += 1

    ex_data = {}
    for ex, d in by_exchange.items():
        wr = (d["wins"] / d["n"] * 100) if d["n"] > 0 else 0
        ex_data[ex] = {"trades": d["n"], "win_rate": round(wr, 1), "pnl": round(d["pnl"], 4)}

    # Per-side breakdown
    longs  = [t for t in trades if (t.get("side") or "") == "buy"]
    shorts = [t for t in trades if (t.get("side") or "") == "sell"]
    long_wr  = (sum(1 for t in longs if (t.get("pnl",0) or 0) > 0) / len(longs) * 100) if longs else 0
    short_wr = (sum(1 for t in shorts if (t.get("pnl",0) or 0) > 0) / len(shorts) * 100) if shorts else 0

    # Time range
    first_ts = trades[0].get("close_time", 0) if trades else 0
    last_ts  = trades[-1].get("close_time", 0) if trades else 0
    first_dt = datetime.fromtimestamp(first_ts).strftime("%Y-%m-%d %H:%M") if first_ts else "?"
    last_dt  = datetime.fromtimestamp(last_ts).strftime("%Y-%m-%d %H:%M") if last_ts else "?"

    return {
        "label":              label,
        "n":                  n,
        "wins":               n_wins,
        "losses":             n_losses,
        "win_rate":           round(win_rate, 1),
        "net_pnl":            round(total_pnl, 4),
        "gross_pnl":          round(gross_pnl, 4),
        "total_fees":         round(total_fees, 4),
        "avg_win":            round(avg_win, 4),
        "avg_loss":           round(avg_loss, 4),
        "profit_factor":      round(profit_factor, 2),
        "max_consec_wins":    max_consec_wins,
        "max_consec_losses":  max_consec_losses,
        "sharpe_ratio":       round(sharpe, 3),
        "max_drawdown":       round(max_dd, 4),
        "by_strategy":        dict(strat_data),
        "by_exchange":        dict(ex_data),
        "longs":              len(longs),
        "shorts":             len(shorts),
        "long_wr":            round(long_wr, 1),
        "short_wr":           round(short_wr, 1),
        "first_trade":        first_dt,
        "last_trade":         last_dt,
    }


# ── Overfitting detection ────────────────────────────────────────────

def detect_overfitting(train_m: dict, test_m: dict) -> list:
    """Compare train vs test metrics to detect overfitting."""
    warnings = []

    if train_m["n"] < 5 or test_m["n"] < 3:
        warnings.append("INSUFFICIENT DATA: need more trades for reliable split analysis")
        return warnings

    wr_diff = train_m["win_rate"] - test_m["win_rate"]
    if wr_diff > 15:
        warnings.append(
            "OVERFIT WARNING: Win rate drops {:.1f}pp from train ({:.1f}%) to test ({:.1f}%)".format(
                wr_diff, train_m["win_rate"], test_m["win_rate"]))
    elif wr_diff < -10:
        warnings.append(
            "IMPROVING: Win rate increases {:.1f}pp in test — strategies adapting well".format(
                abs(wr_diff)))

    if train_m["net_pnl"] > 0 and test_m["net_pnl"] < 0:
        warnings.append(
            "OVERFIT CRITICAL: Train profitable ({:+.4f}) but test loses ({:+.4f})".format(
                train_m["net_pnl"], test_m["net_pnl"]))

    if train_m["profit_factor"] > 0 and test_m["profit_factor"] > 0:
        pf_ratio = test_m["profit_factor"] / max(train_m["profit_factor"], 0.01)
        if pf_ratio < 0.5:
            warnings.append(
                "DEGRADATION: Profit factor drops {:.1f}x → {:.1f}x (50%+ decline)".format(
                    train_m["profit_factor"], test_m["profit_factor"]))

    if test_m["max_drawdown"] > train_m["max_drawdown"] * 1.5:
        warnings.append(
            "RISK INCREASE: Test drawdown ({:.4f}) is 50%+ worse than train ({:.4f})".format(
                test_m["max_drawdown"], train_m["max_drawdown"]))

    # Per-strategy consistency
    for strat, train_s in train_m.get("by_strategy", {}).items():
        test_s = test_m.get("by_strategy", {}).get(strat, {})
        if train_s.get("trades", 0) >= 3 and test_s.get("trades", 0) >= 2:
            t_wr = train_s.get("win_rate", 0)
            s_wr = test_s.get("win_rate", 0)
            if t_wr - s_wr > 25:
                warnings.append(
                    "STRATEGY OVERFIT: '{}' drops from {:.0f}% (train) to {:.0f}% (test)".format(
                        strat, t_wr, s_wr))

    if not warnings:
        warnings.append("HEALTHY: Train/test performance is consistent — no overfitting detected")

    return warnings


# ── HTML report ───────────────────────────────────────────────────────

def generate_html(all_m: dict, train_m: dict, test_m: dict,
                  warnings: list, profile: str) -> str:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    def metric_row(label, train_val, test_val, fmt="{}", better="higher"):
        tv = fmt.format(train_val)
        sv = fmt.format(test_val)
        if better == "higher":
            tc = "#4ade80" if train_val >= test_val else "#f87171"
            sc = "#4ade80" if test_val >= train_val else "#f87171"
        else:
            tc = "#4ade80" if train_val <= test_val else "#f87171"
            sc = "#4ade80" if test_val <= train_val else "#f87171"
        return f"<tr><td>{label}</td><td style='color:{tc}'>{tv}</td><td style='color:{sc}'>{sv}</td></tr>"

    rows = ""
    rows += metric_row("Trades", train_m["n"], test_m["n"], "{}")
    rows += metric_row("Win Rate", train_m["win_rate"], test_m["win_rate"], "{:.1f}%")
    rows += metric_row("Net PnL", train_m["net_pnl"], test_m["net_pnl"], "{:+.4f} USDT")
    rows += metric_row("Avg Win", train_m["avg_win"], test_m["avg_win"], "{:+.4f}")
    rows += metric_row("Avg Loss", train_m["avg_loss"], test_m["avg_loss"], "{:+.4f}", "lower")
    rows += metric_row("Profit Factor", train_m["profit_factor"], test_m["profit_factor"], "{:.2f}")
    rows += metric_row("Sharpe Ratio", train_m["sharpe_ratio"], test_m["sharpe_ratio"], "{:.3f}")
    rows += metric_row("Max Drawdown", train_m["max_drawdown"], test_m["max_drawdown"], "{:.4f}", "lower")
    rows += metric_row("Max Consec Wins", train_m["max_consec_wins"], test_m["max_consec_wins"], "{}")
    rows += metric_row("Max Consec Losses", train_m["max_consec_losses"], test_m["max_consec_losses"], "{}", "lower")
    rows += metric_row("Long WR", train_m["long_wr"], test_m["long_wr"], "{:.1f}%")
    rows += metric_row("Short WR", train_m["short_wr"], test_m["short_wr"], "{:.1f}%")

    # Strategy breakdown
    all_strats = set(list(train_m.get("by_strategy", {}).keys()) +
                     list(test_m.get("by_strategy", {}).keys()))
    strat_rows = ""
    for s in sorted(all_strats):
        ts_d = train_m.get("by_strategy", {}).get(s, {})
        te_d = test_m.get("by_strategy", {}).get(s, {})
        strat_rows += ("<tr><td>{}</td>"
                       "<td>{}</td><td>{:.1f}%</td><td>{:+.4f}</td>"
                       "<td>{}</td><td>{:.1f}%</td><td>{:+.4f}</td></tr>".format(
            s,
            ts_d.get("trades", 0), ts_d.get("win_rate", 0), ts_d.get("net_pnl", 0),
            te_d.get("trades", 0), te_d.get("win_rate", 0), te_d.get("net_pnl", 0)))

    # Warnings
    warn_html = ""
    for w in warnings:
        if "OVERFIT" in w or "CRITICAL" in w or "DEGRADATION" in w:
            color = "#f87171"
        elif "HEALTHY" in w or "IMPROVING" in w:
            color = "#4ade80"
        else:
            color = "#fbbf24"
        warn_html += f"<li style='color:{color}'>{w}</li>"

    # Overall verdict
    if test_m.get("n", 0) < 3:
        verdict = "INSUFFICIENT DATA"
        verdict_c = "#fbbf24"
    elif test_m["net_pnl"] > 0 and test_m["win_rate"] > 40:
        verdict = "PASS — Strategy generalises to unseen data"
        verdict_c = "#4ade80"
    elif test_m["net_pnl"] > 0:
        verdict = "MARGINAL — Profitable but low win rate in test"
        verdict_c = "#fbbf24"
    else:
        verdict = "FAIL — Strategy does not generalise"
        verdict_c = "#f87171"

    return f"""<!DOCTYPE html>
<html><head><meta charset="UTF-8">
<title>Backtest 70/30 Split</title>
<style>
  body{{background:#0f172a;color:#e2e8f0;font-family:monospace;padding:24px}}
  h1{{color:#38bdf8}} h2{{color:#7dd3fc;border-bottom:1px solid #334155;padding-bottom:6px}}
  .verdict{{background:#1e293b;padding:20px;border-radius:8px;border-left:4px solid {verdict_c};margin:20px 0}}
  .cards{{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0}}
  .card{{background:#1e293b;border-radius:8px;padding:16px 22px;min-width:150px}}
  .card .label{{color:#94a3b8;font-size:.8em;text-transform:uppercase}}
  .card .value{{font-size:1.4em;font-weight:bold;margin-top:4px}}
  table{{border-collapse:collapse;width:100%;margin-top:12px}}
  th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #1e293b}}
  th{{color:#94a3b8;font-size:.8em;text-transform:uppercase}}
  ul{{padding-left:20px}} li{{padding:4px 0}}
</style></head><body>
<h1>Backtest — 70/30 Train/Test Split</h1>
<p style="color:#64748b">Generated: {ts} | Profile: {profile} | {all_m["n"]} total trades</p>

<div class="verdict">
  <h2 style="margin:0;color:{verdict_c}">Verdict: {verdict}</h2>
  <p style="color:#94a3b8;margin:8px 0 0">Train: {train_m["first_trade"]} → {train_m["last_trade"]} ({train_m["n"]} trades) |
  Test: {test_m["first_trade"]} → {test_m["last_trade"]} ({test_m["n"]} trades)</p>
</div>

<div class="cards">
  <div class="card"><div class="label">Train WR</div>
    <div class="value" style="color:{'#4ade80' if train_m['win_rate']>=50 else '#f87171'}">{train_m['win_rate']:.1f}%</div></div>
  <div class="card"><div class="label">Test WR</div>
    <div class="value" style="color:{'#4ade80' if test_m['win_rate']>=50 else '#f87171'}">{test_m['win_rate']:.1f}%</div></div>
  <div class="card"><div class="label">Train PnL</div>
    <div class="value" style="color:{'#4ade80' if train_m['net_pnl']>=0 else '#f87171'}">{train_m['net_pnl']:+.4f}</div></div>
  <div class="card"><div class="label">Test PnL</div>
    <div class="value" style="color:{'#4ade80' if test_m['net_pnl']>=0 else '#f87171'}">{test_m['net_pnl']:+.4f}</div></div>
  <div class="card"><div class="label">Train Sharpe</div>
    <div class="value">{train_m['sharpe_ratio']:.3f}</div></div>
  <div class="card"><div class="label">Test Sharpe</div>
    <div class="value">{test_m['sharpe_ratio']:.3f}</div></div>
</div>

<h2>Head-to-Head Comparison</h2>
<table>
  <thead><tr><th>Metric</th><th>TRAIN (70%)</th><th>TEST (30%)</th></tr></thead>
  <tbody>{rows}</tbody>
</table>

<h2>Per-Strategy Breakdown</h2>
<table>
  <thead><tr><th>Strategy</th>
    <th colspan="3" style="color:#38bdf8">── TRAIN ──</th>
    <th colspan="3" style="color:#fbbf24">── TEST ──</th></tr>
  <tr><th></th><th>Trades</th><th>WR</th><th>PnL</th>
    <th>Trades</th><th>WR</th><th>PnL</th></tr></thead>
  <tbody>{strat_rows}</tbody>
</table>

<h2>Overfitting Analysis</h2>
<ul>{warn_html}</ul>

<p style="color:#334155;margin-top:30px;font-size:.8em">
  Split method: chronological (first 70% of trades = train, last 30% = test).
  This ensures no look-ahead bias — test data always comes after train data.
</p>
</body></html>"""


# ── Main ──────────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="70/30 Train/Test Backtest")
    parser.add_argument("--profile", default=None, help="Filter by profile name")
    parser.add_argument("--days", type=int, default=None, help="Only use last N days")
    args = parser.parse_args()

    print("\n" + "=" * 60)
    print("  BACKTEST — 70/30 TRAIN/TEST SPLIT")
    print("=" * 60)

    trades = load_all_trades(args.profile)
    if not trades:
        print("  No trades found. Run the bot in DRY RUN first.")
        return

    # Filter by days if specified
    if args.days:
        import time
        cutoff = time.time() - args.days * 86400
        trades = [t for t in trades if (t.get("close_time", 0) or 0) >= cutoff]
        print(f"  Filtered to last {args.days} days: {len(trades)} trades")

    profile_label = args.profile or "all profiles"
    print(f"  Profile: {profile_label}")
    print(f"  Total trades: {len(trades)}")

    # Split
    train, test = split_train_test(trades, TRAIN_RATIO)
    print(f"  Train: {len(train)} trades ({TRAIN_RATIO*100:.0f}%)")
    print(f"  Test:  {len(test)} trades ({(1-TRAIN_RATIO)*100:.0f}%)")
    print()

    # Calculate metrics
    all_m   = calculate_metrics(trades, "ALL")
    train_m = calculate_metrics(train, "TRAIN")
    test_m  = calculate_metrics(test, "TEST")

    # Detect overfitting
    warnings = detect_overfitting(train_m, test_m)

    # Print summary
    print("  {:<20} {:>10} {:>10}".format("Metric", "TRAIN", "TEST"))
    print("  " + "-" * 42)
    print("  {:<20} {:>10} {:>10}".format("Trades", train_m["n"], test_m["n"]))
    print("  {:<20} {:>9.1f}% {:>9.1f}%".format("Win Rate", train_m["win_rate"], test_m["win_rate"]))
    print("  {:<20} {:>+10.4f} {:>+10.4f}".format("Net PnL", train_m["net_pnl"], test_m["net_pnl"]))
    print("  {:<20} {:>10.2f} {:>10.2f}".format("Profit Factor", train_m["profit_factor"], test_m["profit_factor"]))
    print("  {:<20} {:>10.3f} {:>10.3f}".format("Sharpe", train_m["sharpe_ratio"], test_m["sharpe_ratio"]))
    print("  {:<20} {:>10.4f} {:>10.4f}".format("Max Drawdown", train_m["max_drawdown"], test_m["max_drawdown"]))
    print()

    for w in warnings:
        icon = "✗" if "OVERFIT" in w or "FAIL" in w or "CRITICAL" in w else (
               "✓" if "HEALTHY" in w or "IMPROVING" in w else "!")
        print(f"  {icon} {w}")
    print()

    # Save results
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    report = {
        "generated":    datetime.now().isoformat(),
        "profile":      profile_label,
        "train_ratio":  TRAIN_RATIO,
        "total_trades": len(trades),
        "all":          all_m,
        "train":        train_m,
        "test":         test_m,
        "warnings":     warnings,
    }
    REPORT_JSON.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"  JSON: {REPORT_JSON}")

    # Generate HTML
    html = generate_html(all_m, train_m, test_m, warnings, profile_label)
    REPORT_HTML.write_text(html, encoding="utf-8")
    print(f"  HTML: {REPORT_HTML}")

    # Open in browser
    try:
        webbrowser.open(str(REPORT_HTML.resolve()))
    except Exception:
        pass

    print("\n" + "=" * 60)


if __name__ == "__main__":
    main()
