"""
core/learning_engine.py — Learn from Trade History (Dry Run + Live)

Reads ALL closed trades from:
  - data/positions.json                     (single-bot trades)
  - data/profiles/*/positions.json          (multi-profile trades)

Feeds them into the KnowledgeModel, and produces actionable insights
plus an HTML report at data/learning_report.html.

The KnowledgeModel is the persistent layer — it survives mode switches.
The LearningEngine is the analysis layer — it runs the numbers.
"""

import json
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from loguru import logger

from core.knowledge_model import KnowledgeModel

try:
    from core.kelly_sizer import KellySizer
    from core.probability_calibrator import ProbabilityCalibrator
except ImportError:
    KellySizer = None
    ProbabilityCalibrator = None


POSITIONS_FILE   = Path("data/positions.json")
PROFILE_NAMES    = ("conservative", "moderate", "aggressive")
LEARNING_FILE    = Path("data/learning_report.json")
REPORT_HTML_FILE = Path("data/learning_report.html")

# FIX: was 5 — too few trades to declare a strategy underperforming.
# 8 trades can have 0 wins by pure bad luck during a bearish market window.
# 15 trades is the statistical minimum for a meaningful WR signal.
MIN_SAMPLE_SIZE  = 15


class LearningEngine:

    def __init__(self):
        self._last_run  = 0.0
        self._run_every = 3600
        self._insights  = {}
        self.knowledge  = KnowledgeModel()
        self.kelly      = KellySizer() if KellySizer else None
        self.calibrator = ProbabilityCalibrator() if ProbabilityCalibrator else None

    # ── Public API ────────────────────────────────────────────────────

    def learn(self, force: bool = False) -> dict:
        """Analyze ALL trades (single-bot + all 3 profiles), update knowledge model."""
        if not force and (time.time() - self._last_run) < self._run_every:
            return self._insights

        all_trades  = self._load_all_trades()
        dry_trades  = [t for t in all_trades if t.get("paper_trade", True)]
        live_trades = [t for t in all_trades if not t.get("paper_trade", True)]

        total = len(all_trades)
        if total < MIN_SAMPLE_SIZE:
            logger.info(
                f"[Learning] {total} closed trades — need {MIN_SAMPLE_SIZE}. "
                "Keep running in DRY RUN mode to build the model.")
            if dry_trades:
                self.knowledge.update_from_trades(dry_trades, mode="dry")
            return {}

        logger.info(
            f"[Learning] Analyzing {total} trades ({len(dry_trades)} paper + {len(live_trades)} live)...")

        if dry_trades:
            self.knowledge.update_from_trades(dry_trades, mode="dry")
        if live_trades:
            self.knowledge.update_from_trades(live_trades, mode="live")

        insights = self._analyze(all_trades, dry_trades, live_trades)
        self._insights  = insights
        self._last_run  = time.time()
        self._save(insights)
        self._export_html(insights, dry_trades, live_trades)
        self._log_summary(insights)

        if self.kelly:
            try:
                self.kelly.update_from_trades(all_trades)
                kelly_summary = self.kelly.get_summary()
                for strat, ks in kelly_summary.items():
                    if ks.get("edge") == "negative" and ks.get("trades", 0) >= 15:
                        logger.warning(
                            "[Kelly] {} has NEGATIVE edge: WR={:.0f}% R={:.2f}"
                            .format(strat, ks["win_rate"], ks["r_multiple"]))
            except Exception as e:
                logger.debug(f"[Learning] Kelly update: {e}")

        if self.calibrator:
            try:
                for t in all_trades:
                    conf = t.get("confidence", 0)
                    if conf > 0:
                        strat = (t.get("strategy","unknown") or "unknown").split("|")[0]
                        won   = (t.get("pnl",0) or 0) > 0
                        self.calibrator.record(conf, won, strat)
                cal_summary = self.calibrator.get_summary()
                if cal_summary.get("is_calibrated"):
                    logger.info(
                        "[Calibrator] Calibrated: avg error={:.1f}% over {} trades"
                        .format(cal_summary["avg_calibration_error"],
                                cal_summary["total_recorded"]))
            except Exception as e:
                logger.debug(f"[Learning] Calibrator update: {e}")

        return insights

    def best_strategy_for(self, symbol: str, regime: str):
        return self.knowledge.best_strategy_for(symbol, regime)

    def confidence_adjustment(self, strategy: str) -> float:
        return self.knowledge.confidence_multiplier(strategy)

    def suggestions(self) -> list:
        return self._insights.get("suggestions", [])

    # ── Load ALL positions ────────────────────────────────────────────

    def _load_all_trades(self) -> list:
        seen_ids = set()
        result   = []

        def _ingest(path: Path):
            if not path.exists():
                return
            try:
                data   = json.loads(path.read_text(encoding="utf-8"))
                closed = data.get("closed", [])
                for t in closed:
                    pid = t.get("id", "")
                    if t.get("exit_price") is None:
                        continue
                    if pid and pid in seen_ids:
                        continue
                    if pid:
                        seen_ids.add(pid)
                    result.append(t)
            except Exception as e:
                logger.debug(f"[Learning] Load {path}: {e}")

        _ingest(POSITIONS_FILE)
        for prof in PROFILE_NAMES:
            _ingest(Path(f"data/profiles/{prof}/positions.json"))

        return result

    # ── Core analysis ─────────────────────────────────────────────────

    def _analyze(self, all_trades, dry_trades, live_trades):

        def _bucket(trades):
            by_strat  = defaultdict(lambda: {
                "trades": 0, "wins": 0, "net_pnl": 0.0,
                "gross_pnl": 0.0, "total_fees": 0.0, "pnl_list": []
            })
            by_symbol = defaultdict(lambda: {
                "trades": 0, "wins": 0, "net_pnl": 0.0, "total_fees": 0.0
            })
            by_hour   = defaultdict(lambda: {"trades": 0, "wins": 0, "net_pnl": 0.0})
            by_reason = defaultdict(lambda: {"count": 0, "net_pnl": 0.0})
            for t in trades:
                raw_s = t.get("strategy", "unknown") or "unknown"
                s     = raw_s.split("|")[0]
                sym   = t.get("symbol",   "unknown")
                pnl   = t.get("pnl", 0) or 0
                fee   = t.get("total_fees", 0) or 0
                r     = t.get("close_reason", "unknown")
                ot    = t.get("open_time", 0)
                won   = pnl > 0
                by_strat[s]["trades"]     += 1
                by_strat[s]["net_pnl"]    += pnl
                by_strat[s]["total_fees"] += fee
                by_strat[s]["pnl_list"].append(pnl)
                if won:
                    by_strat[s]["wins"]      += 1
                    by_strat[s]["gross_pnl"] += pnl
                by_symbol[sym]["trades"]     += 1
                by_symbol[sym]["net_pnl"]    += pnl
                by_symbol[sym]["total_fees"] += fee
                if won: by_symbol[sym]["wins"] += 1
                if ot:
                    h = datetime.fromtimestamp(ot, tz=timezone.utc).hour
                    by_hour[h]["trades"]  += 1
                    by_hour[h]["net_pnl"] += pnl
                    if won: by_hour[h]["wins"] += 1
                by_reason[r]["count"]   += 1
                by_reason[r]["net_pnl"] += pnl
            for d in by_strat.values():
                n = d["trades"]
                d["win_rate"]    = round(d["wins"] / n * 100, 1) if n else 0
                d["avg_pnl"]     = round(d["net_pnl"] / n, 4) if n else 0
                d["avg_fee"]     = round(d["total_fees"] / n, 4) if n else 0
                d["best_trade"]  = round(max(d["pnl_list"]), 4) if d["pnl_list"] else 0
                d["worst_trade"] = round(min(d["pnl_list"]), 4) if d["pnl_list"] else 0
                del d["pnl_list"]
            for d in by_symbol.values():
                n = d["trades"]
                d["win_rate"] = round(d["wins"] / n * 100, 1) if n else 0
                d["avg_pnl"]  = round(d["net_pnl"] / n, 4) if n else 0
            for d in by_hour.values():
                n = d["trades"]
                d["win_rate"] = round(d["wins"] / n * 100, 1) if n else 0
            return dict(by_strat), dict(by_symbol), dict(by_hour), dict(by_reason)

        dry_strat,  dry_sym,  dry_hour,  dry_reason  = _bucket(dry_trades)
        live_strat, live_sym, live_hour, live_reason  = _bucket(live_trades)
        all_strat,  all_sym,  all_hour,  all_reason   = _bucket(all_trades)

        sorted_all = sorted(all_trades, key=lambda x: x.get("close_time", 0))
        recent_20  = sorted_all[-20:]
        r_wins = sum(1 for t in recent_20 if (t.get("pnl") or 0) > 0)
        r_pnl  = sum(t.get("pnl") or 0 for t in recent_20)
        r_fees = sum(t.get("total_fees") or 0 for t in recent_20)
        ov_wins = sum(1 for t in all_trades if (t.get("pnl") or 0) > 0)
        ov_pnl  = sum(t.get("pnl") or 0 for t in all_trades)
        ov_fees = sum(t.get("total_fees") or 0 for t in all_trades)

        suggestions = []
        for s, d in all_strat.items():
            if d["trades"] >= MIN_SAMPLE_SIZE:
                wr = d["win_rate"]
                if wr < 50:
                    suggestions.append(
                        "UNDERPERFORMING: '{}' WR={:.0f}% ({} trades) — "
                        "auto-disabled (below 50% threshold).".format(
                            s, wr, d["trades"]))
                # Fee-to-profit ratio check
                gross_pnl = d.get("gross_pnl", 0)
                total_fees = d.get("total_fees", d["avg_fee"] * d["trades"])
                if gross_pnl > 0 and total_fees / gross_pnl > 0.20:
                    fee_ratio = total_fees / gross_pnl
                    suggestions.append(
                        f"FEE_ALERT: '{s}' fees consume {fee_ratio:.0%} of gross profit "
                        f"(${total_fees:.2f} / ${gross_pnl:.2f}). Auto-flagging.")
                    if hasattr(self, 'knowledge'):
                        self.knowledge.flag_fee_heavy(s, fee_ratio)
                if d["avg_fee"] > 0.50:
                    suggestions.append(
                        "HIGH FEES: '{}' avg {:.2f} USDT/trade — "
                        "consider larger sizes or fewer trades.".format(s, d["avg_fee"]))
                if wr >= 65:
                    suggestions.append(
                        "STAR PERFORMER: '{}' WR={:.0f}% ({} trades) — "
                        "consider increasing allocation.".format(s, wr, d["trades"]))

        for s in set(list(dry_strat.keys()) + list(live_strat.keys())):
            dry_d  = dry_strat.get(s, {})
            live_d = live_strat.get(s, {})
            if (dry_d.get("trades", 0) >= MIN_SAMPLE_SIZE
                    and live_d.get("trades", 0) >= MIN_SAMPLE_SIZE):
                dry_wr  = dry_d.get("win_rate", 0)
                live_wr = live_d.get("win_rate", 0)
                diff    = live_wr - dry_wr
                if abs(diff) >= 15:
                    direction = "improved" if diff > 0 else "declined"
                    suggestions.append(
                        f"LIVE VS DRY: '{s}' WR {direction} from {dry_wr:.0f}% (paper) "
                        f"to {live_wr:.0f}% (live) ({diff:+.0f}pp)")

        best_hours = sorted(
            [(h, d) for h, d in all_hour.items() if d["trades"] >= 3],
            key=lambda x: x[1]["win_rate"], reverse=True
        )[:3]
        if best_hours:
            hrs = ", ".join(f"{h:02d}:00 UTC" for h, _ in best_hours)
            suggestions.append(f"BEST HOURS: Most profitable trades at {hrs}.")

        km = self.knowledge.export_status()
        if km["dry_run_trades"] > 0 and km["live_trades"] == 0:
            suggestions.append(
                "READY FOR LIVE: {} paper trades learned. "
                "Switch to LIVE mode — dry run knowledge applies immediately.".format(
                    km["dry_run_trades"]))
        elif not km["graduated"] and km["live_trades"] > 0:
            suggestions.append(
                "BLENDING: {:.0f}% graduated to live data ({}/50 live trades). "
                "Dry run priors still influencing decisions.".format(
                    km["blend_pct"], km["live_trades"]))

        return {
            "generated_at":      datetime.now().isoformat(),
            "total_trades":      len(all_trades),
            "dry_run_trades":    len(dry_trades),
            "live_trades":       len(live_trades),
            "total_net_pnl":     round(ov_pnl, 4),
            "total_fees_paid":   round(ov_fees, 4),
            "overall_win_rate":  round(ov_wins / max(len(all_trades), 1) * 100, 1),
            "recent_20_pnl":     round(r_pnl, 4),
            "recent_20_wr":      round(r_wins / max(len(recent_20), 1) * 100, 1),
            "recent_20_fees":    round(r_fees, 4),
            "by_strategy_all":   all_strat,
            "by_strategy_dry":   dry_strat,
            "by_strategy_live":  live_strat,
            "by_symbol_all":     all_sym,
            "by_symbol_dry":     dry_sym,
            "by_symbol_live":    live_sym,
            "by_hour":           all_hour,
            "by_reason":         all_reason,
            "knowledge_status":  km,
            "suggestions":       suggestions,
        }

    # ── HTML Report ───────────────────────────────────────────────────

    def _export_html(self, insights, dry_trades, live_trades):
        try:
            total  = insights.get("total_trades", 0)
            dry_n  = insights.get("dry_run_trades", 0)
            live_n = insights.get("live_trades", 0)
            wr     = insights.get("overall_win_rate", 0)
            pnl    = insights.get("total_net_pnl", 0)
            fees   = insights.get("total_fees_paid", 0)
            gen    = insights.get("generated_at", "")
            km     = insights.get("knowledge_status", {})
            blend  = km.get("blend_pct", 0)
            graded = km.get("graduated", False)

            def _strat_rows(strat_dict, label):
                rows = ""
                for s, d in sorted(strat_dict.items(),
                                    key=lambda x: x[1].get("net_pnl", 0),
                                    reverse=True):
                    c = "#4ade80" if d.get("net_pnl", 0) >= 0 else "#f87171"
                    rows += (
                        "<tr>"
                        "<td>{}</td><td>{}</td><td>{:.1f}%</td>"
                        "<td style='color:{}'>{:+.4f}</td>"
                        "<td>{:.4f}</td><td>{:+.4f}</td>"
                        "</tr>".format(
                            s, d.get("trades",0), d.get("win_rate",0),
                            c, d.get("net_pnl",0),
                            d.get("total_fees",0), d.get("avg_pnl",0)))
                return rows or f"<tr><td colspan='6'>No {label} data yet</td></tr>"

            suggestions_html = "".join(
                f"<li>{s}</li>"
                for s in insights.get("suggestions", ["No suggestions yet!"]))

            status_color = "#4ade80" if graded else "#fbbf24"
            status_label = (
                "GRADUATED — Live data drives all decisions" if graded
                else f"Blending: {blend:.0f}% graduated to live" if live_n > 0
                else f"DRY RUN — {dry_n} paper trades ready for live")

            pnl_color = "#4ade80" if pnl >= 0 else "#f87171"
            wr_color  = "#4ade80" if wr >= 50 else "#f87171"
            r20_pnl   = insights.get("recent_20_pnl", 0)
            r20_color = "#4ade80" if r20_pnl >= 0 else "#f87171"

            html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="60">
<title>Trading Bot — Knowledge Report</title>
<style>
  body{{background:#0f172a;color:#e2e8f0;font-family:monospace;padding:24px;}}
  h1{{color:#38bdf8;margin-bottom:4px;}}
  h2{{color:#7dd3fc;border-bottom:1px solid #334155;padding-bottom:6px;margin-top:28px;}}
  .meta{{color:#64748b;font-size:.85em;margin-bottom:16px;}}
  .status{{background:#1e293b;border-radius:8px;padding:14px 20px;margin-bottom:20px;border-left:4px solid {sc};}}
  .cards{{display:flex;gap:16px;flex-wrap:wrap;margin:16px 0 24px;}}
  .card{{background:#1e293b;border-radius:8px;padding:16px 22px;min-width:150px;}}
  .card .label{{color:#94a3b8;font-size:.8em;text-transform:uppercase;}}
  .card .value{{font-size:1.5em;font-weight:bold;margin-top:4px;}}
  table{{border-collapse:collapse;width:100%;margin-top:12px;}}
  th,td{{padding:8px 12px;text-align:left;border-bottom:1px solid #1e293b;}}
  th{{color:#94a3b8;font-size:.8em;text-transform:uppercase;}}
  tr:hover{{background:#1e293b;}}
  .tab-btn{{background:#1e293b;border:none;color:#94a3b8;padding:8px 18px;cursor:pointer;margin-right:4px;border-radius:4px;font-family:monospace;}}
  .tab-btn.active{{color:#38bdf8;border-bottom:2px solid #38bdf8;}}
  .tab-content{{display:none;}}
  .tab-content.active{{display:block;}}
  ul{{padding-left:20px;}}
  li{{padding:6px 0;color:#fbbf24;}}
</style>
</head>
<body>
<h1>Trading Bot — Knowledge Report</h1>
<div class="meta">Generated: {gen} &nbsp;|&nbsp; {total} total trades (all profiles merged)</div>
<div class="status">
  <strong style="color:{sc}">Learning Status: {sl}</strong><br>
  <span style="color:#94a3b8">Paper: {dry_n} &nbsp;|&nbsp; Live: {live_n} &nbsp;|&nbsp; Stars: {stars} &nbsp;|&nbsp; Caution: {caution}</span>
</div>
<div class="cards">
  <div class="card"><div class="label">Net PnL</div><div class="value" style="color:{pc}">{pnl:+.4f}</div></div>
  <div class="card"><div class="label">Win Rate</div><div class="value" style="color:{wrc}">{wr:.1f}%</div></div>
  <div class="card"><div class="label">Fees Paid</div><div class="value" style="color:#f97316">{fees:.4f}</div></div>
  <div class="card"><div class="label">Paper Trades</div><div class="value" style="color:#a78bfa">{dry_n}</div></div>
  <div class="card"><div class="label">Live Trades</div><div class="value" style="color:#34d399">{live_n}</div></div>
  <div class="card"><div class="label">Recent 20 PnL</div><div class="value" style="color:{r20c}">{r20:+.4f}</div></div>
</div>
<h2>Strategy Performance</h2>
<div>
  <button class="tab-btn active" onclick="showTab('all')">All Trades</button>
  <button class="tab-btn" onclick="showTab('dry')">Paper Only</button>
  <button class="tab-btn" onclick="showTab('live')">Live Only</button>
</div>
<div id="tab-all" class="tab-content active">
  <table><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Net PnL</th><th>Fees</th><th>Avg/Trade</th></tr>{all_rows}</table></div>
<div id="tab-dry" class="tab-content">
  <table><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Net PnL</th><th>Fees</th><th>Avg/Trade</th></tr>{dry_rows}</table></div>
<div id="tab-live" class="tab-content">
  <table><tr><th>Strategy</th><th>Trades</th><th>Win Rate</th><th>Net PnL</th><th>Fees</th><th>Avg/Trade</th></tr>{live_rows}</table></div>
<h2>Suggestions &amp; Insights</h2>
<ul>{sugg}</ul>
<script>
function showTab(name){{
  document.querySelectorAll('.tab-content').forEach(el=>el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el=>el.classList.remove('active'));
  document.getElementById('tab-'+name).classList.add('active');
  event.target.classList.add('active');
}}
</script>
<p style="color:#334155;margin-top:40px;font-size:.8em;">
  Auto-refreshes 60s &nbsp;|&nbsp; data/knowledge_model.json &nbsp;|&nbsp; All 3 profiles merged
</p>
</body></html>""".format(
                sc=status_color, sl=status_label, gen=gen, total=total,
                dry_n=dry_n, live_n=live_n,
                stars=km.get("star_strategies",[]),
                caution=km.get("caution_strategies",[]),
                pc=pnl_color, pnl=pnl, wr=wr, fees=fees,
                wrc=wr_color, r20c=r20_color, r20=r20_pnl,
                all_rows=_strat_rows(insights.get("by_strategy_all",{}), "combined"),
                dry_rows=_strat_rows(insights.get("by_strategy_dry",{}),  "paper"),
                live_rows=_strat_rows(insights.get("by_strategy_live",{}), "live"),
                sugg=suggestions_html,
            )

            REPORT_HTML_FILE.parent.mkdir(parents=True, exist_ok=True)
            REPORT_HTML_FILE.write_text(html, encoding="utf-8")
            logger.info(f"[Learning] Report: {REPORT_HTML_FILE.resolve()}")
        except Exception as e:
            logger.debug(f"[Learning] HTML error: {e}")

    # ── Helpers ───────────────────────────────────────────────────────

    def _save(self, insights):
        try:
            LEARNING_FILE.parent.mkdir(parents=True, exist_ok=True)
            LEARNING_FILE.write_text(
                json.dumps(insights, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Learning] Save error: {e}")

    def _log_summary(self, insights):
        total  = insights.get("total_trades", 0)
        dry_n  = insights.get("dry_run_trades", 0)
        live_n = insights.get("live_trades", 0)
        wr     = insights.get("overall_win_rate", 0)
        pnl    = insights.get("total_net_pnl", 0)
        fees   = insights.get("total_fees_paid", 0)
        logger.info(
            f"[Learning] {total} trades ({dry_n} paper / {live_n} live) | "
            f"WR={wr:.1f}% | Net PnL={pnl:+.4f} USDT | Fees={fees:.4f} USDT")
        for s in insights.get("suggestions", [])[:5]:
            # FIX: UNDERPERFORMING warnings need 15+ trades to be meaningful.
            # Show at DEBUG level only — don't spam INFO logs with small-sample noise.
            if "UNDERPERFORMING" in s:
                logger.debug(f"[Learning] (low sample) {s}")
            else:
                logger.info(f"[Learning] INSIGHT: {s}")
