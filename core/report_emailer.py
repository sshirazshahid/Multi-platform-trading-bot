"""
core/report_emailer.py  --  Daily Email Report

Sections:
  1.  Portfolio Snapshot     — 3 profiles ranked by score
  2.  Per-Exchange Breakdown — Binance / Bybit / Bitget
  3.  Claude AI Analysis     — market bias, risk multiplier, per-coin decisions
  4.  Open Positions         — all open trades across all profiles & exchanges
  5.  Research Summary       — Goldman picks, BlackRock regime, Bain best pick
  6.  Risk Alerts            — drawdown / halt / fear-greed / bearish warnings
  7.  Recent Trades          — last 15 closed trades (all profiles merged)

Schedule:
  - Daily at REPORT_EMAIL_HOUR UTC (default 08:00) when running Option M
  - Manual send: Option X in TradingBot.bat
  - report_emailer.py --send   | --preview | --test

Bug fixes applied:
  - Fear & Greed now correctly read from news_cache.json (not claude dict)
  - All position files (single-bot + 3 profiles) are merged correctly
"""

from __future__ import annotations

import json
import smtplib
import ssl
import time
from collections import defaultdict
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from loguru import logger

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

import os

# ── Config helpers ─────────────────────────────────────────────────────

def _cfg(key: str, default: str = "") -> str:
    val = os.getenv(key, "").strip()
    if val:
        return val
    try:
        for line in Path(".env").read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line.startswith(key + "=") and not line.startswith("#"):
                return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default


GMAIL_SENDER    = lambda: _cfg("GMAIL_SENDER")
GMAIL_PASSWORD  = lambda: _cfg("GMAIL_APP_PASSWORD")
GMAIL_RECIPIENT = lambda: _cfg("GMAIL_RECIPIENT")
REPORT_HOUR     = int(_cfg("REPORT_EMAIL_HOUR", "8"))

PROFILE_NAMES   = ("conservative", "moderate", "aggressive")
EXCHANGE_NAMES  = ("binance", "bybit", "bitget")

COMPARISON_FILE = Path("data/profiles/comparison.json")
CLAUDE_FILE     = Path("data/claude_analysis.json")
RESEARCH_FILE   = Path("data/research/report_latest.json")
NEWS_CACHE_FILE = Path("data/news_cache.json")
POSITIONS_FILE  = Path("data/positions.json")
LAST_SENT_FILE  = Path("data/last_email_sent.json")

def _profile_positions_file(name: str) -> Path:
    return Path(f"data/profiles/{name}/positions.json")

def _profile_wallet_file(name: str) -> Path:
    return Path(f"data/profiles/{name}/wallet.json")


# ═══════════════════════════════════════════════════════════════════════
# ReportEmailer
# ═══════════════════════════════════════════════════════════════════════

class ReportEmailer:

    def __init__(self):
        self._last_sent_date = self._load_last_sent()

    def should_send_daily(self) -> bool:
        """Sends 4x/day at 02:00, 08:00, 14:00, 20:00 UTC."""
        now = datetime.now(timezone.utc)
        if now.hour not in [2, 8, 14, 20]:
            return False
        slot = now.strftime("%Y-%m-%d") + f"-{now.hour:02d}"
        return self._last_sent_date != slot

    def send_daily(self, force: bool = False) -> bool:
        if not force and not self.should_send_daily():
            return False
        sender    = GMAIL_SENDER()
        password  = GMAIL_PASSWORD()
        recipient = GMAIL_RECIPIENT()
        if not all([sender, password, recipient]):
            logger.warning("[Email] Cannot send — Gmail credentials not set.")
            return False
        logger.info("[Email] Building daily report...")
        try:
            subject, html = self._build_report()
            ok = self._send(sender, password, recipient, subject, html)
            if ok:
                self._save_last_sent()
                logger.info(f"[Email] Daily report sent to {recipient}")
            return ok
        except Exception as e:
            logger.error(f"[Email] Failed: {e}")
            return False

    def send_alert(self, subject: str, message: str) -> bool:
        sender    = GMAIL_SENDER()
        password  = GMAIL_PASSWORD()
        recipient = GMAIL_RECIPIENT()
        if not all([sender, password, recipient]):
            return False
        html = ("<html><body style='font-family:Arial;background:#0f172a;"
                "color:#e2e8f0;padding:24px'>"
                "<h2 style='color:#f87171'>&#9888; TradingBot Alert</h2>"
                "<p>{}</p></body></html>".format(message.replace("\n", "<br>")))
        return self._send(sender, password, recipient,
                          f"[TradingBot Alert] {subject}", html)

    def is_configured(self) -> bool:
        return bool(GMAIL_SENDER() and GMAIL_PASSWORD() and GMAIL_RECIPIENT())

    def _build_report(self) -> tuple[str, str]:
        now      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        comp     = self._load_json(COMPARISON_FILE)
        claude   = self._load_json(CLAUDE_FILE)
        research = self._load_json(RESEARCH_FILE)
        news     = self._load_json(NEWS_CACHE_FILE) or {}

        all_open, all_closed = self._merge_all_positions()

        try:
            from config import OPERATING_MODE
            op_mode = OPERATING_MODE
        except Exception:
            op_mode = "PAPER"

        # Today / 7d / 30d / All-time window stats — computed once,
        # reused by the period snapshot, subject line, and alerts.
        period_stats = self._compute_period_stats(all_closed)
        today = period_stats["today"]

        (comp or {}).get("leader", "?").upper()
        bias   = (claude or {}).get("market_bias", "neutral").upper()
        fg     = news.get("fear_greed", {}).get("value", "?")
        mode_tag = "LIVE" if op_mode == "CONTROLLED_LIVE" else op_mode
        # Subject now leads with today's PnL + WR so you can skim at a glance.
        today_tag = "{:+.2f}U {}W/{}L".format(
            today["pnl"], today["wins"], today["losses"])
        subject = "[TradingBot] {} | {} | {} | F&G={} | {}".format(
            mode_tag,
            datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M"),
            today_tag, fg, bias)

        connected_exs = self._detect_connected_exchanges(all_open, all_closed)
        ex_label      = " + ".join(e.capitalize() for e in connected_exs) or "No exchanges"

        html = _HTML_SHELL.format(
            now=now, ex_label=ex_label,
            mode_section       = self._section_mode_banner(op_mode),
            period_section     = self._section_period_snapshot(period_stats),
            risk_section       = self._section_risk_state(all_open),
            portfolio_section  = self._section_portfolio(comp),
            exchange_section   = self._section_exchange_breakdown(comp, all_open, all_closed),
            strategy_section   = self._section_strategy_breakdown(all_closed),
            kelly_section      = self._section_kelly_edge(),
            exit_reasons_section = self._section_exit_reasons(all_closed),
            claude_section     = self._section_claude(claude),
            positions_section  = self._section_open_positions(all_open),
            hourly_section     = self._section_hourly_heatmap(),
            blacklist_section  = self._section_blacklist(),
            research_section   = self._section_research(research),
            alerts_section     = self._section_alerts(comp, claude, news),
            trades_section     = self._section_recent_trades(all_closed),
            warehouse_section  = self._section_warehouse_edge(all_closed),
        )
        return subject, html

    # ── Section 0: Mode Banner ───────────────────────────────────────

    def _section_mode_banner(self, op_mode: str) -> str:
        if op_mode == "CONTROLLED_LIVE":
            try:
                from config import LEVERAGE_TIERS, MAX_LOSS_PER_TRADE_USD, RISK
                max_lev = RISK.get("futures_max_leverage", 3)
                max_pos = RISK.get("max_position_pct", 0.01) * 100
                tiers = ", ".join("{}:{}x".format(t, v["leverage"])
                                  for t, v in LEVERAGE_TIERS.items())
            except Exception:
                max_lev = 3; max_pos = 1.0; tiers = "?"; MAX_LOSS_PER_TRADE_USD = 2.0
            return """
<div style="background:#1a332a;border:2px solid #4ade80;border-radius:8px;padding:16px 20px;margin-bottom:16px">
  <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px">
    <span style="font-size:20px">&#9989;</span>
    <span style="font-size:18px;font-weight:bold;color:#4ade80">CONTROLLED_LIVE</span>
    <span style="color:#94a3b8;font-size:12px">Real orders active</span>
  </div>
  <div style="display:flex;gap:16px;flex-wrap:wrap">{}</div>
</div>""".format(_stat_boxes([
                ("Max Leverage", f"{max_lev}x", "green"),
                ("Position Size", f"{max_pos:.0f}%", "green"),
                ("Max Loss/Trade", f"${MAX_LOSS_PER_TRADE_USD:.2f}", "green"),
                ("Min Hold", "2h", "green"),
                ("R:R Ratio", "2.0:1", "green"),
                ("Tiers", tiers, "dim"),
            ]))
        elif op_mode == "OBSERVATION":
            return """
<div style="background:#1a2744;border:2px solid #38bdf8;border-radius:8px;padding:14px 20px;margin-bottom:16px">
  <span style="font-size:16px;font-weight:bold;color:#38bdf8">&#128065; OBSERVATION MODE</span>
  <span style="color:#94a3b8;font-size:12px;margin-left:12px">Data collection only, no orders placed</span>
</div>"""
        else:
            return """
<div style="background:#2a2a1a;border:2px solid #fbbf24;border-radius:8px;padding:14px 20px;margin-bottom:16px">
  <span style="font-size:16px;font-weight:bold;color:#fbbf24">&#128196; PAPER MODE</span>
  <span style="color:#94a3b8;font-size:12px;margin-left:12px">Simulated fills — no real capital at risk</span>
</div>"""

    # ── Section 1: Portfolio ──────────────────────────────────────────

    def _section_portfolio(self, comp: dict) -> str:
        if not comp:
            return _card("Portfolio Snapshot", "&#127974;",
                         "<p class='dim'>No data yet — run Option M first.</p>")

        profiles = comp.get("profiles", {})
        ranked   = comp.get("ranked",   [])
        leader   = comp.get("leader",   "")
        scans    = comp.get("scan_count", 0)
        rec      = comp.get("recommendation", "")

        total_bal    = sum(d.get("balance", 0)     for d in profiles.values())
        total_pnl    = sum(d.get("net_pnl", 0)     for d in profiles.values())
        total_fees   = sum(d.get("fees_paid", 0)   for d in profiles.values())
        total_trades = sum(d.get("total_trades", 0) for d in profiles.values())
        total_wins   = sum(d.get("wins", 0)         for d in profiles.values())
        overall_wr   = (total_wins / total_trades * 100) if total_trades else 0

        rows = ""
        for name in ranked:
            d    = profiles.get(name, {})
            bal  = d.get("balance", 0)
            ret  = d.get("return_pct", 0)
            wr   = d.get("win_rate", 0)
            pnl  = d.get("net_pnl", 0)
            fees = d.get("fees_paid", 0)
            dd   = d.get("max_drawdown", 0)
            sc   = d.get("score", 0)
            n    = d.get("total_trades", 0)
            halt = d.get("is_halted", False)
            flag  = " &#127881;" if name == leader else ""
            hflag = " <span class='red'>[HALTED]</span>" if halt else ""
            rows += _tr(
                f"<b>{name.upper()}{flag}{hflag}</b>",
                _money(bal), _pct(ret), _wr(wr),
                _pnl(pnl), _pct(-fees, neg_red=True),
                "<span class='{}'>{:.1f}%</span>".format(
                    "red" if dd >= 10 else "yellow" if dd >= 5 else "green", dd),
                str(n),
                f"<span class='yellow'>{sc:.0f}</span>",
            )
        rows += ("<tr style='border-top:2px solid #1e3a5f;font-weight:bold'>"
                 + _td("TOTAL (3 profiles)")
                 + _td(_money(total_bal))
                 + _td(_money(total_pnl, color=True))
                 + _td()
                 + _td(_wr(overall_wr))
                 + _td(_money(-total_fees))
                 + _td() + _td(str(total_trades)) + _td()
                 + "</tr>")

        rec_c = "green" if "READY" in rec else "yellow"
        body = f"""
<table>
  <tr><th>Profile</th><th>Balance</th><th>Return</th><th>Win Rate</th>
      <th>Net P&amp;L</th><th>Fees</th><th>Drawdown</th><th>Trades</th><th>Score</th></tr>
  {rows}
</table>
<p style="margin-top:14px;font-size:13px" class="{rec_c}">{rec}</p>
"""

        return _card(
            "Portfolio Snapshot &nbsp;<span class='dim' style='font-size:12px'>"
            f"Scan #{scans} &nbsp;|&nbsp; $300 total paper capital</span>",
            "&#127974;", body)

    # ── Section 2: Exchange breakdown ─────────────────────────────────

    def _section_exchange_breakdown(self, comp, all_open, all_closed):
        ex_stats = defaultdict(lambda: {
            "pnl": 0.0, "gross": 0.0, "fees": 0.0,
            "n": 0, "wins": 0, "open": 0,
            "best": 0.0, "worst": 0.0,
            "spot_n": 0, "futures_n": 0,
            "spot_pnl": 0.0, "futures_pnl": 0.0,
            "profiles": defaultdict(lambda: {"pnl":0.0,"n":0,"wins":0}),
        })
        for t in all_closed:
            ex   = (t.get("exchange") or "unknown").lower()
            pnl  = t.get("pnl", 0) or 0
            gross= t.get("gross_pnl", pnl) or pnl
            fees = t.get("total_fees", 0) or 0
            mtype= t.get("market_type", "spot")
            raw_s= t.get("strategy","") or ""
            prof = raw_s.split("|")[1] if "|" in raw_s else "single"
            s = ex_stats[ex]
            s["pnl"] += pnl; s["gross"] += gross; s["fees"] += fees; s["n"] += 1
            if pnl > 0: s["wins"] += 1
            s["best"] = max(s["best"], pnl); s["worst"] = min(s["worst"], pnl)
            if mtype == "futures": s["futures_n"] += 1; s["futures_pnl"] += pnl
            else: s["spot_n"] += 1; s["spot_pnl"] += pnl
            s["profiles"][prof]["pnl"] += pnl; s["profiles"][prof]["n"] += 1
            if pnl > 0: s["profiles"][prof]["wins"] += 1
        for p in all_open:
            ex = (p.get("exchange") or "unknown").lower()
            ex_stats[ex]["open"] += 1

        wallet_data = {}
        for pn in PROFILE_NAMES:
            wf = _profile_wallet_file(pn)
            try:
                wallet_data[pn] = json.loads(wf.read_text(encoding="utf-8")).get("balances",{}) if wf.exists() else {}
            except Exception:
                wallet_data[pn] = {}

        ex_order = ["binance","bybit","bitget"]
        active   = [ex for ex in ex_order if ex in ex_stats or
                    any(wallet_data[p].get(ex,0) > 0 for p in PROFILE_NAMES)]
        if not active:
            return _card("Per-Exchange Breakdown","&#127759;","<p class='dim'>No exchange data yet.</p>")

        EX_COLOR = {"binance":"#f0b90b","bybit":"#f7a600","bitget":"#00cba9"}
        sections_html = ""
        for ex in active:
            color = EX_COLOR.get(ex,"#94a3b8")
            s = ex_stats.get(ex,{})
            n = s.get("n",0); wins = s.get("wins",0)
            wr = (wins/n*100) if n > 0 else 0
            pnl = s.get("pnl",0); gross = s.get("gross",0); fees = s.get("fees",0)
            best = s.get("best",0); worst = s.get("worst",0); open_n = s.get("open",0)
            sp_n = s.get("spot_n",0); ft_n = s.get("futures_n",0)
            sp_pnl = s.get("spot_pnl",0); ft_pnl = s.get("futures_pnl",0)

            wallet_rows = ""
            total_wallet = 0.0
            for pn in PROFILE_NAMES:
                bal = wallet_data[pn].get(ex,0)
                if bal > 0:
                    total_wallet += bal
                    wallet_rows += _tr(pn.upper(), _money(bal))
            if total_wallet > 0:
                wallet_rows += f"<tr style='font-weight:bold'><td>TOTAL</td><td>{_money(total_wallet)}</td></tr>"

            prof_rows = ""
            for pn, pd in s.get("profiles",{}).items():
                pw = (pd["wins"]/pd["n"]*100) if pd["n"] > 0 else 0
                prof_rows += _tr(pn.upper(), str(pd["n"]), _wr(pw), _money(pd["pnl"],color=True))

            sections_html += """
<div style="margin-bottom:24px;border:1px solid {col};border-radius:8px;padding:16px;background:#1a2744">
  <h3 style="color:{col};margin:0 0 14px;font-size:16px;text-transform:uppercase;letter-spacing:1px">
    &#9679; {ex_upper} <span style="font-size:11px;color:#64748b;font-weight:normal;margin-left:12px">
    {n} trades | {open_n} open | Spot:{sp_n} Fut:{ft_n}</span></h3>
  <div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px">{stat_boxes}</div>
  <div style="display:grid;grid-template-columns:1fr 1fr;gap:16px">
    <div><p style="color:#94a3b8;font-size:11px;text-transform:uppercase;margin:0 0 6px">Wallet per Profile</p>
      <table style="width:100%"><tr><th>Profile</th><th>Balance</th></tr>{wallet_rows}</table></div>
    <div><p style="color:#94a3b8;font-size:11px;text-transform:uppercase;margin:0 0 6px">P&amp;L per Profile</p>
      <table style="width:100%"><tr><th>Profile</th><th>Trades</th><th>WR</th><th>Net P&amp;L</th></tr>{prof_rows}</table></div>
  </div>
  <div style="margin-top:14px;font-size:12px;color:#64748b">
    Spot: <span class="{sp_c}">{sp_pnl:+.4f}</span> | Fut: <span class="{ft_c}">{ft_pnl:+.4f}</span> |
    Best: <span class="green">{best:+.4f}</span> | Worst: <span class="red">{worst:+.4f}</span>
  </div>
</div>""".format(col=color, ex_upper=ex.upper(), n=n, open_n=open_n, sp_n=sp_n, ft_n=ft_n,
                 stat_boxes=_stat_boxes([
                     ("Net P&L", f"{pnl:+.4f}", "green" if pnl>=0 else "red"),
                     ("Win Rate",f"{wr:.1f}%", "green" if wr>=55 else "yellow" if wr>=40 else "red"),
                     ("Fees",    f"-{fees:.4f}", "orange"),
                     ("Gross",   f"{gross:+.4f}", "green" if gross>=0 else "red"),
                 ]),
                 wallet_rows=wallet_rows or "<tr><td colspan='2' class='dim'>No balance</td></tr>",
                 prof_rows=prof_rows or "<tr><td colspan='4' class='dim'>No trades yet</td></tr>",
                 sp_c="green" if sp_pnl>=0 else "red", ft_c="green" if ft_pnl>=0 else "red",
                 sp_pnl=sp_pnl, ft_pnl=ft_pnl, best=best, worst=worst)

        return f"<h2>&#127759; Per-Exchange Breakdown</h2><div class='card'>{sections_html}</div>"

    # ── Section 3: Claude ─────────────────────────────────────────────

    def _section_claude(self, claude: dict) -> str:
        if not claude:
            return _card("Claude AI Analysis","&#129302;","<p class='dim'>No analysis yet.</p>")
        bias = claude.get("market_bias","neutral"); note = claude.get("market_note","")
        rm = claude.get("risk_multiplier",1.0); best = claude.get("best_trade","none")
        advice = claude.get("advice",""); ts = claude.get("generated_at","")[:16]
        src = claude.get("source","embedded").upper(); call_n = claude.get("call_number",0)
        bc  = "green" if bias=="bullish" else "red" if bias=="bearish" else "yellow"
        rmc = "green" if rm<=1.0 else "yellow" if rm<=1.2 else "red"
        ALC = {"long_spot":"green","long_futures":"green","short_futures":"red","skip":"dim","evaluate":"yellow"}
        coin_rows = ""
        for coin, rec in claude.get("coins",{}).items():
            action = rec.get("action","skip"); cadj = rec.get("confidence_adj",1.0)
            reason = rec.get("reason","")[:70]; ac = ALC.get(action,"dim")
            coin_rows += _tr(f"<b>{coin}</b>",
                "<span class='{}'>{}</span>".format(ac,action.replace("_"," ").upper()),
                "<span class='{}'>{:.2f}x</span>".format("green" if cadj>=1.1 else "red" if cadj<=0.7 else "white",cadj),
                f"<span class='dim'>{reason}</span>")
        body = """
<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px">{sb}</div>
{notes}
<table><tr><th>Coin</th><th>Action</th><th>Adj</th><th>Reason</th></tr>{cr}</table>
""".format(sb=_stat_boxes([("Bias",bias.upper(),bc),("Risk",f"{rm:.2f}x",rmc),
            ("Best",best or "—","green"),("Src",f"{src} #{call_n}","dim")]),
           notes=f"<p style='margin-bottom:12px'><span class='dim'>{note[:120]}</span><br><b>Advice:</b> {advice[:120]}</p>" if (note or advice) else "",
           cr=coin_rows or "<tr><td colspan='4' class='dim'>No data</td></tr>")
        return _card(f"Claude AI &nbsp;<span class='dim' style='font-size:11px'>({src}) {ts}</span>","&#129302;",body)

    # ── Section 4: Open positions ─────────────────────────────────────

    def _section_open_positions(self, all_open: list) -> str:
        if not all_open:
            return _card("Open Positions","&#128308;","<p class='dim'>No open positions.</p>")
        by_ex = defaultdict(list)
        for p in all_open:
            by_ex[(p.get("exchange") or "?").lower()].append(p)
        rows = ""
        for ex in ["binance","bybit","bitget"] + [e for e in by_ex if e not in ("binance","bybit","bitget")]:
            pos_list = by_ex.get(ex,[])
            if not pos_list: continue
            rows += f"<tr style='background:#0f172a'><td colspan='8' style='color:#7dd3fc;font-size:11px;text-transform:uppercase;padding:8px 10px;font-weight:bold'>{ex.upper()} ({len(pos_list)})</td></tr>"
            for p in pos_list:
                side = (p.get("side") or "?").upper()
                sym  = p.get("symbol","?"); mtype = (p.get("market_type") or "spot").upper()
                entry= p.get("entry_price",0); sl=p.get("stop_loss",0); tp=p.get("take_profit",0)
                lev  = p.get("leverage",1); dur = int((time.time()-p.get("open_time",time.time()))/60)
                raw_s= p.get("strategy","") or ""; prof = raw_s.split("|")[1].upper() if "|" in raw_s else ""
                lev_s= f" {lev}x" if lev>1 else ""; sc = "green" if side=="BUY" else "red"
                rows += _tr(f"<span class='{sc}'>{side}</span>",f"<b>{sym}</b>",
                    "{}{} {}".format(mtype,lev_s,f"<span class='yellow'>[{prof}]</span>" if prof else ""),
                    f"${entry:.4f}",f"<span class='red'>${sl:.4f}</span>",
                    f"<span class='green'>${tp:.4f}</span>",f"<span class='dim'>{dur}m</span>",
                    "<span class='dim'>{}</span>".format(p.get("order_id","")[-6:]))
        body = f"<table><tr><th>Side</th><th>Symbol</th><th>Market</th><th>Entry</th><th>SL</th><th>TP</th><th>Age</th><th>ID</th></tr>{rows}</table>"
        return _card(f"Open Positions ({len(all_open)})","&#128308;",body)

    # ── Section 5: Research ───────────────────────────────────────────

    def _section_research(self, research: dict) -> str:
        if not research:
            return _card("Research Summary","&#128202;","<p class='dim'>No research yet — run Option I.</p>")
        goldman_coins = (research.get("goldman",{}) or {}).get("coins",[])[:3]
        gman_rows = ""
        for c in goldman_coins:
            tc = "green" if c.get("trend")=="Bull" else "red"
            mc = "green" if c.get("moat")=="Strong" else "yellow" if c.get("moat")=="Moderate" else "red"
            gman_rows += _tr("<b>{}</b>".format(c.get("ticker","?")),
                "${:,.2f}".format(c.get("price",0)),"<span class='{}'>{:+.1f}%</span>".format(tc,c.get("chg_30d",0)),
                "<span class='{}'>{}</span>".format(mc,c.get("moat","?")),"<span class='green'>${:,.2f}</span>".format(c.get("bull_target",0)),
                "<span class='red'>${:,.2f}</span>".format(c.get("bear_target",0)))
        br = research.get("blackrock",{}) or {}
        regime = br.get("regime","?"); reg_note = br.get("regime_note","")
        rc = "green" if "ACCUMULATION" in regime else "yellow" if "GROWTH" in regime else "red"
        bp = (research.get("bain",{}) or {}).get("best_pick",{})
        body = """<b>Goldman Sachs Top Picks:</b>
<table style="margin:8px 0 16px"><tr><th>Coin</th><th>Price</th><th>30d</th><th>Moat</th><th>Bull</th><th>Bear</th></tr>{gr}</table>
<p><b>BlackRock Regime:</b> <span class="{rc}">{regime}</span> &mdash; {rn}<br>
<span class="dim">Expected: {exp} | Max DD: {mdd}</span></p>
<p><b>Bain Best Pick:</b> <span class="green">{bp_c}</span><br><span class="dim" style="font-size:12px">{bp_r}</span></p>""".format(
            gr=gman_rows or "<tr><td colspan='6' class='dim'>Run Option I</td></tr>",
            rc=rc, regime=regime, rn=reg_note, exp=br.get("expected_return","?"), mdd=br.get("max_drawdown","?"),
            bp_c=bp.get("coin","?"), bp_r=bp.get("rationale","")[:200])
        return _card("Research Summary","&#128202;",body)

    # ── Section 6: Alerts ─────────────────────────────────────────────

    def _section_alerts(self, comp: dict, claude: dict, news: dict) -> str:
        alerts = []

        # Profile alerts
        if comp:
            for name, d in (comp.get("profiles") or {}).items():
                if d.get("is_halted"):
                    alerts.append(("error","HALTED: {} — {}".format(name.upper(),d.get("halt_reason",""))))
                if d.get("max_drawdown",0) >= 10:
                    alerts.append(("warning","DRAWDOWN: {} is {:.1f}% down from peak".format(name.upper(),d["max_drawdown"])))
                if d.get("win_rate",50) < 30 and d.get("total_trades",0) >= 5:
                    alerts.append(("warning","LOW WIN RATE: {} at {:.1f}%".format(name.upper(),d["win_rate"])))

        # Claude bias
        if claude:
            bias = claude.get("market_bias","neutral")
            if bias == "bearish":
                alerts.append(("warning","BEARISH MARKET: Claude signals caution. Position sizes reduced."))

        # Fear & Greed — read from news cache (correct source)
        fg = 50
        try:
            fg = int((news.get("fear_greed") or {}).get("value", 50))
        except Exception:
            pass
        if fg <= 15:
            alerts.append(("info",f"EXTREME FEAR (F&G={fg}): Historical buying opportunity."))
        elif fg >= 80:
            alerts.append(("warning",f"EXTREME GREED (F&G={fg}): High reversal risk. Tighten stops."))

        # Arbitrage summary
        try:
            arb_path = Path("data/arbitrage/opportunities.json")
            if arb_path.exists():
                arb = json.loads(arb_path.read_text(encoding="utf-8"))
                arb_n = arb.get("closed_arbs",0); arb_pnl = arb.get("total_pnl",0)
                if arb_n > 0:
                    alerts.append(("info",f"ARBITRAGE: {arb_n} closed arbs | PnL: {arb_pnl:+.4f} USDT"))
        except Exception:
            pass

        if not alerts:
            alerts.append(("ok","All profiles operating normally. No alerts."))

        COLORS = {"error":"#f87171","warning":"#fbbf24","info":"#38bdf8","ok":"#4ade80"}
        ICONS  = {"error":"&#9888;","warning":"&#9888;","info":"&#8505;","ok":"&#10003;"}
        alert_html = "".join(
            "<div style='padding:10px 14px;margin:6px 0;border-radius:6px;border-left:4px solid {c};background:#1a2233'>"
            "<span style='color:{c}'>{i}</span> {m}</div>".format(
                c=COLORS.get(lvl,"#94a3b8"), i=ICONS.get(lvl,""), m=msg)
            for lvl, msg in alerts)
        return _card("Risk Alerts","&#9888;&#65039;",alert_html)

    # ── Section 7: Recent trades ──────────────────────────────────────

    def _section_recent_trades(self, all_closed: list) -> str:
        recent = sorted(all_closed, key=lambda x: x.get("close_time",0), reverse=True)[:20]
        if not recent:
            return _card("Recent Trades","&#128203;","<p class='dim'>No closed trades yet.</p>")
        rows = ""; current_ex = None
        reconciled_n = 0
        for t in recent:
            ex = (t.get("exchange") or "?").lower()
            pnl = t.get("pnl",0) or 0; gross = t.get("gross_pnl",pnl) or pnl
            fees = t.get("total_fees",0) or 0; side = (t.get("side") or "?").upper()
            sym = t.get("symbol","?"); reason = t.get("close_reason","?")
            ct = t.get("close_time"); ts_str = datetime.fromtimestamp(ct, tz=timezone.utc).strftime("%m/%d %H:%M") if ct else "--"
            raw_s = t.get("strategy","?") or "?"; strat = raw_s.split("|")[0][:14]
            prof = raw_s.split("|")[1].upper()[:4] if "|" in raw_s else ""
            paper = t.get("paper_trade",True); sc = "green" if side=="BUY" else "red"; pc = "green" if pnl>=0 else "red"

            # Hold duration — close_time - open_time, formatted as "2h 15m"
            ot = t.get("open_time") or 0
            hold_str = "--"
            if ct and ot and ct >= ot:
                mins = int((ct - ot) / 60)
                if mins >= 60:
                    hold_str = f"{mins // 60}h {mins % 60:02d}m"
                else:
                    hold_str = f"{mins}m"

            # Reconciled flag (imported from exchange history, not bot-tracked)
            pid = t.get("id", "") or ""
            is_reconciled = ("RECONCILED" in pid.upper()
                             or t.get("close_reason") == "reconciled_from_exchange"
                             or raw_s.startswith("reconciled_exchange"))
            if is_reconciled:
                reconciled_n += 1
                mode = "<span class='yellow'>[R]</span>"
            elif paper:
                mode = "[P]"
            else:
                mode = "<span class='green'>[L]</span>"

            if ex != current_ex:
                current_ex = ex
                # 11 cols: Time, Mode, Prof, Side, Symbol, Strategy, Hold, Net P&L, Gross, Fees, Reason
                rows += f"<tr style='background:#0f172a'><td colspan='11' style='color:#7dd3fc;font-size:11px;text-transform:uppercase;padding:6px 10px;font-weight:bold'>{ex.upper()}</td></tr>"
            rows += _tr(f"<span class='dim'>{ts_str}</span>", mode,
                f"<span class='yellow'>{prof}</span>" if prof else "",
                f"<span class='{sc}'>{side}</span>", f"<b>{sym}</b>",
                f"<span class='dim'>{strat}</span>",
                f"<span class='dim'>{hold_str}</span>",
                "<span class='{}'>{}{:.4f}</span>".format(pc,"+" if pnl>=0 else "",pnl),
                f"<span class='dim'>{gross:+.4f}</span>",
                f"<span class='dim'>-{fees:.4f}</span>",
                f"<span class='dim'>{reason}</span>")
        legend = ("<p class='dim' style='margin-top:8px;font-size:11px'>"
                  "[P]=Paper &nbsp; <span class='green'>[L]=Live</span> &nbsp; "
                  "<span class='yellow'>[R]=Reconciled from exchange history</span>"
                  "{}</p>").format(
                      f" &nbsp;&mdash;&nbsp; <b>{reconciled_n}</b> reconciled trade(s) in view"
                      if reconciled_n else "")
        body = ("<table><tr><th>Time</th><th>Mode</th><th>Prof</th><th>Side</th>"
                "<th>Symbol</th><th>Strategy</th><th>Hold</th><th>Net P&amp;L</th>"
                "<th>Gross</th><th>Fees</th><th>Reason</th></tr>"
                + rows + "</table>" + legend)
        return _card("Recent Trades (last 20, all profiles)","&#128203;",body)

    # ── Section 8: Strategy Breakdown ──────────────────────────────────

    def _section_strategy_breakdown(self, all_closed: list) -> str:
        if not all_closed:
            return ""
        strats = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0, "fees": 0.0})
        for t in all_closed:
            raw = (t.get("strategy") or "unknown").split("|")[0]
            pnl = t.get("pnl", 0) or 0
            fees = t.get("total_fees", 0) or 0
            s = strats[raw]
            s["n"] += 1
            s["pnl"] += pnl
            s["fees"] += fees
            if pnl > 0:
                s["wins"] += 1
        rows = ""
        for name, s in sorted(strats.items(), key=lambda x: x[1]["pnl"], reverse=True):
            wr = (s["wins"] / s["n"] * 100) if s["n"] > 0 else 0
            pf = s["pnl"] / abs(s["fees"]) if s["fees"] != 0 else 0
            rows += _tr(
                f"<b>{name}</b>", str(s["n"]), _wr(wr),
                _pnl(s["pnl"]),
                "<span class='dim'>-{:.4f}</span>".format(s["fees"]),
                "<span class='{}'>{:.2f}</span>".format(
                    "green" if pf > 1 else "red", pf))
        body = "<table><tr><th>Strategy</th><th>Trades</th><th>WR</th>" \
               f"<th>Net P&amp;L</th><th>Fees</th><th>PF</th></tr>{rows}</table>"
        return _card("Strategy Breakdown", "&#128202;", body)

    # ── Section 9: Warehouse Edge ────────────────────────────────────

    def _section_warehouse_edge(self, all_closed: list) -> str:
        if not all_closed:
            return ""
        syms = defaultdict(lambda: {
            "n": 0, "wins": 0, "pnl": 0.0,
            "gross_win": 0.0, "gross_loss": 0.0,
            "hold_secs": 0.0, "hold_n": 0,
        })
        for t in all_closed:
            sym = t.get("symbol", "?")
            pnl = t.get("pnl", 0) or 0
            s = syms[sym]
            s["n"] += 1
            s["pnl"] += pnl
            if pnl > 0:
                s["wins"] += 1
                s["gross_win"]  += pnl
            elif pnl < 0:
                s["gross_loss"] += -pnl
            ot = t.get("open_time") or 0
            ct = t.get("close_time") or 0
            if ct and ot and ct >= ot:
                s["hold_secs"] += (ct - ot)
                s["hold_n"]    += 1

        ranked = sorted(syms.items(), key=lambda x: x[1]["pnl"], reverse=True)
        if len(ranked) > 20:
            ranked = ranked[:10] + ranked[-5:]
        rows = ""
        for sym, s in ranked:
            wr = (s["wins"] / s["n"] * 100) if s["n"] > 0 else 0
            ev = s["pnl"] / s["n"] if s["n"] > 0 else 0
            pf = (s["gross_win"] / s["gross_loss"]) if s["gross_loss"] > 0 \
                 else (999.0 if s["gross_win"] > 0 else 0.0)
            pf_c = "green" if pf >= 1.5 else "yellow" if pf >= 1.0 else "red"
            pf_str = "&infin;" if pf >= 999 else f"{pf:.2f}"
            avg_hold = (s["hold_secs"] / s["hold_n"]) if s["hold_n"] else 0
            if avg_hold >= 3600:
                hold_str = f"{avg_hold / 3600:.1f}h"
            elif avg_hold >= 60:
                hold_str = f"{avg_hold / 60:.0f}m"
            else:
                hold_str = "--"
            rows += _tr(
                f"<b>{sym}</b>", str(s["n"]), _wr(wr),
                _pnl(s["pnl"]),
                "<span class='{}'>{:+.4f}</span>".format(
                    "green" if ev >= 0 else "red", ev),
                f"<span class='{pf_c}'>{pf_str}</span>",
                f"<span class='dim'>{hold_str}</span>")
        body = ("<p class='dim' style='margin-bottom:10px;font-size:12px'>"
                "Top 10 and bottom 5 symbols by net P&amp;L. "
                "PF = gross_win / gross_loss. E[V] = net P&amp;L per trade.</p>"
                "<table><tr><th>Symbol</th><th>Trades</th><th>WR</th>"
                "<th>Net P&amp;L</th><th>E[V]</th><th>PF</th><th>Avg Hold</th></tr>"
                + rows + "</table>")
        return _card("Per-Symbol Edge", "&#128200;", body)

    # ── Section 10: Period Snapshot (Today / 7d / 30d / All-time) ────

    def _compute_period_stats(self, all_closed: list) -> dict:
        """Roll up trade stats across today / 7d / 30d / all-time windows.
        'Today' is computed in UTC to match the rest of the report."""
        now_ts  = time.time()
        today_d = datetime.now(timezone.utc).date()
        out = {
            "today":   {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                        "gross": 0.0, "fees": 0.0, "best": 0.0, "worst": 0.0},
            "d7":      {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                        "gross": 0.0, "fees": 0.0, "best": 0.0, "worst": 0.0},
            "d30":     {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                        "gross": 0.0, "fees": 0.0, "best": 0.0, "worst": 0.0},
            "all":     {"n": 0, "wins": 0, "losses": 0, "pnl": 0.0,
                        "gross": 0.0, "fees": 0.0, "best": 0.0, "worst": 0.0},
        }
        for t in all_closed:
            pnl   = t.get("pnl", 0) or 0
            gross = t.get("gross_pnl", pnl) or pnl
            fees  = t.get("total_fees", 0) or 0
            ct    = t.get("close_time", 0) or 0
            buckets = ["all"]
            if ct:
                if datetime.fromtimestamp(ct, tz=timezone.utc).date() == today_d:
                    buckets.append("today")
                if now_ts - ct <= 7  * 86400:
                    buckets.append("d7")
                if now_ts - ct <= 30 * 86400:
                    buckets.append("d30")
            for k in buckets:
                s = out[k]
                s["n"]     += 1
                s["pnl"]   += pnl
                s["gross"] += gross
                s["fees"]  += fees
                if pnl > 0:
                    s["wins"]  += 1
                elif pnl < 0:
                    s["losses"] += 1
                s["best"]  = max(s["best"],  pnl)
                s["worst"] = min(s["worst"], pnl)
        for k, s in out.items():
            s["wr"] = (s["wins"] / s["n"] * 100) if s["n"] else 0
            wins_usd  = sum(
                (t.get("pnl", 0) or 0) for t in all_closed
                if (t.get("pnl", 0) or 0) > 0 and self._in_window(t, k, now_ts, today_d))
            losses_usd = sum(
                -(t.get("pnl", 0) or 0) for t in all_closed
                if (t.get("pnl", 0) or 0) < 0 and self._in_window(t, k, now_ts, today_d))
            s["pf"] = (wins_usd / losses_usd) if losses_usd > 0 else (999.0 if wins_usd > 0 else 0.0)
            s["expectancy"] = (s["pnl"] / s["n"]) if s["n"] else 0
        return out

    def _in_window(self, t, key, now_ts, today_d) -> bool:
        ct = t.get("close_time", 0) or 0
        if key == "all":
            return True
        if not ct:
            return False
        if key == "today":
            return datetime.fromtimestamp(ct, tz=timezone.utc).date() == today_d
        if key == "d7":
            return now_ts - ct <= 7 * 86400
        if key == "d30":
            return now_ts - ct <= 30 * 86400
        return False

    def _section_period_snapshot(self, ps: dict) -> str:
        """Stat cards for Today / 7d / 30d / All-time."""
        def _card_row(label, s):
            wr_c = "green" if s["wr"] >= 55 else "yellow" if s["wr"] >= 45 else "red"
            pnl_c = "green" if s["pnl"] >= 0 else "red"
            pf_c  = "green" if s["pf"]  >= 1 else "red"
            return _tr(
                f"<b>{label}</b>",
                str(s["n"]),
                "<span class='green'>{}</span>".format(s["wins"]),
                "<span class='red'>{}</span>".format(s["losses"]),
                "<span class='{}'>{:.1f}%</span>".format(wr_c, s["wr"]),
                "<span class='{}'>{:+.4f}</span>".format(pnl_c, s["pnl"]),
                "<span class='dim'>{:+.4f}</span>".format(s["gross"]),
                "<span class='orange'>-{:.4f}</span>".format(s["fees"]),
                "<span class='{}'>{:.2f}</span>".format(pf_c, s["pf"]),
                "<span class='{}'>{:+.4f}</span>".format(pnl_c, s["expectancy"]),
                "<span class='green'>{:+.4f}</span>".format(s["best"]),
                "<span class='red'>{:+.4f}</span>".format(s["worst"]),
            )
        rows = (_card_row("Today",    ps["today"])
              + _card_row("7 days",   ps["d7"])
              + _card_row("30 days",  ps["d30"])
              + _card_row("All-time", ps["all"]))
        body = ("<table><tr><th>Window</th><th>Trades</th><th>W</th><th>L</th>"
                "<th>Win %</th><th>Net P&amp;L</th><th>Gross</th><th>Fees</th>"
                "<th>PF</th><th>Expect</th><th>Best</th><th>Worst</th></tr>"
                + rows + "</table>")
        return _card("Period Snapshot", "&#128197;", body)

    # ── Section 11: Risk State ───────────────────────────────────────

    def _section_risk_state(self, all_open: list) -> str:
        """Drawdown, pauses, halts, exchange health, daily PnL limit."""
        rs = self._load_json(Path("data/risk_state.json")) or {}
        halted       = bool(rs.get("is_halted"))
        halt_reason  = rs.get("halt_reason", "")
        dd_pct       = float(rs.get("max_drawdown_pct", 0) or 0)
        daily_pnl    = float(rs.get("daily_pnl", 0) or 0)
        peak_bal     = float(rs.get("peak_balance", 0) or 0)
        start_bal    = float(rs.get("start_balance", 0) or 0)
        trades_today = int(rs.get("trades_today", 0) or 0)
        recent       = rs.get("recent_results", []) or []

        # Halted exchanges — check heartbeat
        hb = self._load_json(Path("data/heartbeat.json")) or {}
        halted_exs = []
        if isinstance(hb, dict):
            for ex, info in hb.items():
                if isinstance(info, dict) and info.get("halted"):
                    halted_exs.append(ex)

        # Strategy family pauses from risk_state
        family_pauses = []
        for k, v in rs.items():
            if k.startswith("family_pause_") and v:
                fam = k.replace("family_pause_", "")
                family_pauses.append(fam)

        # Review-required file (spec §12 halt gate)
        review_required = Path("data/review_required.json").exists()

        stat_cards = _stat_boxes([
            ("Bot State",   "HALTED" if halted else "ACTIVE",
                            "red" if halted else "green"),
            ("Max DD",      f"{dd_pct:.2f}%",
                            "red" if dd_pct >= 10 else "yellow" if dd_pct >= 5 else "green"),
            ("Daily P&L",   f"{daily_pnl:+.4f}",
                            "green" if daily_pnl >= 0 else "red"),
            ("Trades/day",  str(trades_today), "dim"),
            ("Peak Bal",    f"${peak_bal:.2f}", "dim"),
            ("Start Bal",   f"${start_bal:.2f}", "dim"),
        ])

        warnings = []
        if halted:
            warnings.append(f"<span class='red'>&#9888; HALTED: {halt_reason}</span>")
        if review_required:
            warnings.append("<span class='red'>&#9888; data/review_required.json present "
                            "— owner review required before auto-resume</span>")
        if halted_exs:
            warnings.append("<span class='yellow'>&#9888; Halted exchanges: {}</span>".format(
                ", ".join(halted_exs)))
        if family_pauses:
            warnings.append("<span class='yellow'>&#9888; Paused strategy families: {}</span>".format(
                ", ".join(family_pauses)))
        if dd_pct >= 10:
            warnings.append("<span class='red'>&#9888; Drawdown ≥ 10% — max is 12% before halt</span>")

        # Recent results streak — stored as booleans (True=win) or "W"/"L" strings
        streak_html = ""
        if recent:
            def _norm(r):
                if isinstance(r, bool):
                    return "W" if r else "L"
                s = str(r).strip().upper()
                return "W" if s in ("W", "WIN", "TRUE", "1") else "L"
            symbols = [_norm(r) for r in recent[-10:]]
            streak_html = " ".join(
                "<span class='{}'>{}</span>".format(
                    "green" if s == "W" else "red", s)
                for s in symbols)
            wins_n = sum(1 for s in symbols if s == "W")
            losses_n = len(symbols) - wins_n
            streak_html = ("<p style='margin-top:10px;font-size:12px'>"
                           f"<span class='dim'>Last {len(symbols)} results ({wins_n}W/{losses_n}L):</span> "
                           + streak_html + "</p>")

        body = ("<div style='display:flex;gap:12px;flex-wrap:wrap;margin-bottom:10px'>"
                + stat_cards + "</div>"
                + "".join("<p style='margin:4px 0;font-size:13px'>" + w + "</p>"
                          for w in warnings)
                + streak_html)
        if not warnings and not streak_html:
            body += "<p class='dim' style='margin-top:8px'>No risk warnings. Bot operating normally.</p>"
        return _card("Risk State & Health", "&#128737;&#65039;", body)

    # ── Section 12: Kelly Edge per Strategy ──────────────────────────

    def _section_kelly_edge(self) -> str:
        kelly = self._load_json(Path("data/kelly_stats.json")) or {}
        if not kelly:
            return _card("Kelly Edge", "&#127922;",
                         "<p class='dim'>No Kelly stats yet — need ≥10 trades per strategy.</p>")
        rows = ""
        for strat, d in sorted(kelly.items(), key=lambda x: -(x[1].get("wins", 0) + x[1].get("losses", 0))):
            wins = int(d.get("wins", 0) or 0)
            losses = int(d.get("losses", 0) or 0)
            total = wins + losses
            if total == 0:
                continue
            wr = (wins / total * 100) if total else 0
            total_win = float(d.get("total_win", 0) or 0)
            total_loss = float(d.get("total_loss", 0) or 0)
            avg_win = total_win / wins if wins else 0
            avg_loss = total_loss / losses if losses else 0
            b = avg_win / avg_loss if avg_loss > 0 else 0
            p = wins / total if total else 0
            full_kelly = ((p * b - (1 - p)) / b * 100) if b > 0 else -100
            frac_kelly = full_kelly * 0.25
            pnl = total_win - total_loss
            edge_c = "green" if full_kelly > 0 else "red"
            pnl_c = "green" if pnl >= 0 else "red"
            wr_c = "green" if wr >= 55 else "yellow" if wr >= 45 else "red"
            # Insufficient-data flag
            status = "OK" if total >= 10 else "warmup"
            status_c = "green" if status == "OK" else "dim"
            rows += _tr(
                f"<b>{strat}</b>", str(total),
                f"<span class='{wr_c}'>{wr:.1f}%</span>",
                f"{b:.2f}",
                f"<span class='{edge_c}'>{full_kelly:+.1f}%</span>",
                f"<span class='{edge_c}'>{frac_kelly:+.1f}%</span>",
                f"<span class='{pnl_c}'>{pnl:+.4f}</span>",
                f"<span class='{status_c}'>{status}</span>",
            )
        body = ("<p class='dim' style='margin-bottom:10px;font-size:12px'>"
                "Kelly edge = (p·b − q) / b where b = avg_win / avg_loss. "
                "Negative edge → Kelly sizer blocks or sizes to MIN_POSITION. "
                "Fractional Kelly (25%) is the live multiplier.</p>"
                "<table><tr><th>Strategy</th><th>Trades</th><th>WR</th><th>R</th>"
                "<th>Full Kelly</th><th>Frac Kelly</th><th>Net P&amp;L</th><th>Status</th></tr>"
                + rows + "</table>")
        return _card("Kelly Edge per Strategy", "&#127922;", body)

    # ── Section 13: Exit Reason Breakdown ────────────────────────────

    def _section_exit_reasons(self, all_closed: list) -> str:
        if not all_closed:
            return ""
        reasons = defaultdict(lambda: {"n": 0, "wins": 0, "pnl": 0.0})
        for t in all_closed:
            r = t.get("close_reason", "unknown") or "unknown"
            pnl = t.get("pnl", 0) or 0
            d = reasons[r]
            d["n"] += 1
            d["pnl"] += pnl
            if pnl > 0:
                d["wins"] += 1
        total = sum(d["n"] for d in reasons.values())
        rows = ""
        for name, d in sorted(reasons.items(), key=lambda x: -x[1]["n"]):
            wr = (d["wins"] / d["n"] * 100) if d["n"] else 0
            share = (d["n"] / total * 100) if total else 0
            avg = d["pnl"] / d["n"] if d["n"] else 0
            pnl_c = "green" if d["pnl"] >= 0 else "red"
            rows += _tr(
                f"<b>{name}</b>", str(d["n"]),
                f"<span class='dim'>{share:.1f}%</span>",
                _wr(wr),
                "<span class='{}'>{:+.4f}</span>".format(pnl_c, d["pnl"]),
                f"<span class='{pnl_c}'>{avg:+.4f}</span>",
            )
        body = ("<table><tr><th>Reason</th><th>Count</th><th>Share</th>"
                "<th>WR</th><th>Net P&amp;L</th><th>Avg</th></tr>"
                + rows + "</table>")
        return _card("Exit Reason Breakdown", "&#128683;", body)

    # ── Section 14: Hourly Performance Heatmap ───────────────────────

    def _section_hourly_heatmap(self) -> str:
        """Top/bottom UTC hours from knowledge_model.json hour_scores.
        Supports both schemas:
          - {trades: int, win_rate: float}             (current knowledge_model.json)
          - {wins: int, losses: int, total_pnl: float} (older schema)
        """
        km = self._load_json(Path("data/knowledge_model.json")) or {}
        hours = km.get("hour_scores") or {}
        if not hours:
            return ""
        items = []
        for h, d in hours.items():
            try:
                hour = int(h)
            except (TypeError, ValueError):
                continue
            if not isinstance(d, dict):
                continue
            # Schema detection
            if "trades" in d:
                n = int(d.get("trades", 0) or 0)
                wr = float(d.get("win_rate", 0) or 0)
                wins = int(round(n * wr / 100)) if n else 0
                losses = n - wins
            else:
                wins = int(d.get("wins", 0) or 0)
                losses = int(d.get("losses", 0) or 0)
                n = wins + losses
                wr = (wins / n * 100) if n else 0
            if n == 0:
                continue
            pnl = float(d.get("total_pnl", 0) or 0)
            items.append({"hour": hour, "n": n, "wr": wr, "pnl": pnl,
                          "wins": wins, "losses": losses})
        if not items:
            return ""
        # Qualifying threshold: require ≥2 trades (knowledge_model has limited data)
        qualified = [x for x in items if x["n"] >= 2]
        if not qualified:
            qualified = items  # fall back to anything
        top = sorted(qualified, key=lambda x: (-x["wr"], -x["n"]))[:5]
        bot = sorted(qualified, key=lambda x: (x["wr"], -x["n"]))[:5]

        # PnL column only if we actually have PnL data
        has_pnl = any(abs(x["pnl"]) > 1e-9 for x in qualified)

        def _hour_row(x):
            cells = [
                "<b>{:02d}:00 UTC</b>".format(x["hour"]),
                str(x["n"]),
                "<span class='green'>{}</span>".format(x["wins"]),
                "<span class='red'>{}</span>".format(x["losses"]),
                _wr(x["wr"]),
            ]
            if has_pnl:
                cells.append("<span class='{}'>{:+.4f}</span>".format(
                    "green" if x["pnl"] >= 0 else "red", x["pnl"]))
            return _tr(*cells)

        top_rows = "".join(_hour_row(x) for x in top)
        bot_rows = "".join(_hour_row(x) for x in bot)
        hdr_cols = ["Hour", "N", "W", "L", "WR"]
        if has_pnl:
            hdr_cols.append("Net P&amp;L")
        hdr = "<tr>" + "".join(f"<th>{c}</th>" for c in hdr_cols) + "</tr>"

        # Threshold label is accurate
        threshold_label = "≥2 trades" if any(x["n"] >= 2 for x in items) else "any"
        body = ("<div style='display:grid;grid-template-columns:1fr 1fr;gap:16px'>"
                "<div><p style='color:#94a3b8;font-size:11px;text-transform:uppercase;margin:0 0 6px'>"
                f"&#11088; Best hours ({threshold_label})</p><table>{hdr}{top_rows}</table></div>"
                "<div><p style='color:#94a3b8;font-size:11px;text-transform:uppercase;margin:0 0 6px'>"
                f"&#128308; Worst hours ({threshold_label})</p><table>{hdr}{bot_rows}</table></div>"
                "</div>"
                "<p class='dim' style='margin-top:8px;font-size:11px'>"
                "From data/knowledge_model.json. Hours trending higher WR are favored by the "
                f"MCP Brain timing bonus ({len(items)} total hours tracked).</p>")
        return _card("Hourly Performance Heatmap", "&#128197;", body)

    # ── Section 15: Active Blacklist ────────────────────────────────

    def _section_blacklist(self) -> str:
        try:
            from config import BLACKLIST_HARD
            hard = sorted(BLACKLIST_HARD)
        except Exception:
            hard = []
        auto = self._load_json(Path("data/auto_mutations.json")) or {}
        blacklist_mut = auto.get("blacklist_symbols") or auto.get("symbol_blacklist") or []
        bl_mut_list = list(blacklist_mut) if isinstance(blacklist_mut, list) else \
                      list(blacklist_mut.keys()) if isinstance(blacklist_mut, dict) else []
        leverage_cap = auto.get("leverage_cap") or auto.get("max_leverage")

        rows = ""
        for s in hard:
            rows += _tr("<span class='red'>HARD</span>",
                        f"<b>{s}</b>",
                        "<span class='dim'>config.BLACKLIST_HARD</span>")
        for s in bl_mut_list:
            if s in hard:
                continue
            rows += _tr("<span class='yellow'>AUTO</span>",
                        f"<b>{s}</b>",
                        "<span class='dim'>AutoMutator (recent loss cluster)</span>")
        if leverage_cap:
            rows += _tr("<span class='yellow'>CAP</span>",
                        "<b>Leverage</b>",
                        f"<span class='dim'>AutoMutator cap → {leverage_cap}x</span>")
        if not rows:
            body = "<p class='dim'>No active blacklist entries.</p>"
        else:
            body = ("<table><tr><th>Type</th><th>Entry</th><th>Source</th></tr>"
                    + rows + "</table>")
        return _card("Active Blacklist & Caps", "&#128683;", body)

    # ── Helpers ───────────────────────────────────────────────────────

    def _merge_all_positions(self):
        all_open=[]; all_closed=[]; seen_ids=set()
        def _ingest(path):
            try:
                if path.exists():
                    d = json.loads(path.read_text(encoding="utf-8"))
                    for pos in d.get("open",[]):
                        pid=pos.get("id","")
                        if pid not in seen_ids: seen_ids.add(pid); all_open.append(pos)
                    for pos in d.get("closed",[]):
                        pid=pos.get("id","")
                        if pid not in seen_ids: seen_ids.add(pid); all_closed.append(pos)
            except Exception as e:
                logger.debug(f"[Email] _ingest {path}: {e}")
        _ingest(POSITIONS_FILE)
        for name in PROFILE_NAMES: _ingest(_profile_positions_file(name))
        all_closed.sort(key=lambda x: x.get("close_time",0),reverse=True)
        return all_open, all_closed

    def _detect_connected_exchanges(self, all_open, all_closed):
        seen=set()
        for t in all_open+all_closed:
            ex=(t.get("exchange") or "").lower()
            if ex: seen.add(ex)
        order=["binance","bybit","bitget"]
        return [e for e in order if e in seen]+[e for e in seen if e not in order]

    def _load_json(self, path):
        try:
            if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"[Email] Load {path.name}: {e}")
        return None

    def _send(self, sender, password, recipient, subject, html):
        try:
            msg=MIMEMultipart("alternative"); msg["Subject"]=subject; msg["From"]=sender; msg["To"]=recipient
            msg.attach(MIMEText(html,"html","utf-8"))
            ctx=ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com",465,context=ctx) as srv:
                srv.login(sender,password); srv.sendmail(sender,[recipient],msg.as_string())
            logger.info(f"[Email] Sent: {subject[:70]}"); return True
        except smtplib.SMTPAuthenticationError:
            logger.error("[Email] Auth failed — use a Gmail App Password."); return False
        except Exception as e:
            logger.error(f"[Email] Send failed: {e}"); return False

    def _load_last_sent(self):
        try:
            if LAST_SENT_FILE.exists(): return json.loads(LAST_SENT_FILE.read_text(encoding="utf-8")).get("date","")
        except Exception: pass
        return ""

    def _save_last_sent(self):
        try:
            LAST_SENT_FILE.parent.mkdir(parents=True,exist_ok=True)
            _slot_now = datetime.now(timezone.utc)
            _slot_key = _slot_now.strftime("%Y-%m-%d") + f"-{_slot_now.hour:02d}"
            LAST_SENT_FILE.write_text(json.dumps({"date":_slot_key,"sent_at":_slot_now.isoformat()}),encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Email] Save last-sent: {e}")


# ═══════════════════════════════════════════════════════════════════════
# HTML helpers
# ═══════════════════════════════════════════════════════════════════════

def _card(title,icon,body):
    return f"\n<h2>{icon} {title}</h2>\n<div class=\"card\">\n  {body}\n</div>"

def _tr(*cells):
    return "<tr>"+"".join(f"<td>{c}</td>" for c in cells)+"</tr>"

def _td(content=""):
    return f"<td>{content}</td>"

def _money(val,color=False):
    sign="+" if val>=0 else ""; c=("green" if val>=0 else "red") if color else "white"
    return f"<span class='{c}'>{sign}{val:.4f} USDT</span>"

def _pct(val,neg_red=False):
    c="green" if val>=0 else "red"
    if neg_red: c="red" if val!=0 else "dim"
    return f"<span class='{c}'>{val:+.2f}%</span>"

def _wr(val):
    c="green" if val>=55 else "yellow" if val>=45 else "red"
    return f"<span class='{c}'>{val:.1f}%</span>"

def _pnl(val):
    sign="+" if val>=0 else ""; c="green" if val>=0 else "red"
    return f"<span class='{c}'>{sign}{val:.4f}</span>"

def _stat_boxes(items):
    boxes=""
    COLS={"green":"#4ade80","red":"#f87171","yellow":"#fbbf24","orange":"#fb923c","dim":"#94a3b8","white":"#e2e8f0"}
    for label,value,color in items:
        boxes+="""<div style="background:#0f172a;border-radius:6px;padding:10px 14px;min-width:110px;border:1px solid #1e3a5f">
  <div style="font-size:10px;color:#64748b;text-transform:uppercase;margin-bottom:4px">{l}</div>
  <div style="font-size:16px;font-weight:bold;color:{c}">{v}</div>
</div>""".format(l=label,v=value,c=COLS.get(color,"#e2e8f0"))
    return boxes


_HTML_SHELL = """<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
  body      {{ font-family:'Segoe UI',Arial,sans-serif;background:#0f172a;color:#e2e8f0;margin:0;padding:0; }}
  .wrapper  {{ max-width:780px;margin:0 auto;padding:24px 16px; }}
  h1        {{ color:#38bdf8;font-size:22px;margin:0 0 4px; }}
  h2        {{ color:#7dd3fc;font-size:15px;border-bottom:1px solid #1e3a5f;padding-bottom:6px;margin:28px 0 12px; }}
  .meta     {{ color:#64748b;font-size:12px;margin-bottom:20px; }}
  .card     {{ background:#1e293b;border-radius:8px;padding:16px 20px;margin:10px 0;border:1px solid #1e3a5f; }}
  table     {{ border-collapse:collapse;width:100%;font-size:13px; }}
  th        {{ background:#0f172a;color:#94a3b8;padding:7px 10px;text-align:left;font-size:11px;text-transform:uppercase; }}
  td        {{ padding:6px 10px;border-bottom:1px solid #152032; }}
  .green    {{ color:#4ade80; }} .red {{ color:#f87171; }} .yellow {{ color:#fbbf24; }}
  .orange   {{ color:#fb923c; }} .dim {{ color:#94a3b8; }} .white {{ color:#e2e8f0; }}
  .footer   {{ color:#334155;font-size:11px;margin-top:32px;padding-top:16px;border-top:1px solid #1e293b; }}
</style>
</head>
<body>
<div class="wrapper">
<h1>&#128200; Trading Bot &mdash; Daily Report</h1>
<p class="meta">{now} &nbsp;|&nbsp; {ex_label}</p>
{mode_section}
{period_section}
{risk_section}
{portfolio_section}
{exchange_section}
{strategy_section}
{kelly_section}
{exit_reasons_section}
{claude_section}
{positions_section}
{hourly_section}
{blacklist_section}
{research_section}
{alerts_section}
{trades_section}
{warehouse_section}
<div class="footer">
  Automated report — Binance + Bybit + Bitget | MCP Brain v3.1 | Learning-first (Apr 2026)<br>
  <b>DISCLAIMER:</b> Trading involves risk. Past performance is not indicative of future results.
</div>
</div>
</body>
</html>"""


if __name__ == "__main__":
    import sys
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    cmd = sys.argv[1] if len(sys.argv) > 1 else "--send"
    emailer = ReportEmailer()
    if cmd == "--test":
        print("\n  Testing email credentials...\n")
        s=GMAIL_SENDER(); p=GMAIL_PASSWORD(); r=GMAIL_RECIPIENT()
        if not all([s,p,r]):
            print("  ERROR: Missing credentials in .env"); sys.exit(1)
        ok = emailer._send(s,p,r,"[TradingBot] Test email",
            "<html><body style='font-family:Arial;background:#0f172a;color:#e2e8f0;padding:24px'>"
            "<h2 style='color:#4ade80'>&#10003; Email test successful!</h2>"
            f"<p>Daily reports at {REPORT_HOUR:02d}:00 UTC.</p></body></html>")
        print("\n  {} — {}".format("SUCCESS" if ok else "FAILED", r)); sys.exit(0 if ok else 1)
    elif cmd == "--preview":
        print("\n  Building preview...\n")
        subject, html = emailer._build_report()
        out = Path("data/email_preview.html"); out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(html,encoding="utf-8")
        print(f"  Subject: {subject}\n  Preview: {out.resolve()}")
        try:
            import os
            os.startfile(str(out.resolve()))
        except Exception:
            pass
    else:
        print("\n  Sending daily report...\n")
        ok = emailer.send_daily(force=True)
        print("\n  {} — {}".format("SUCCESS" if ok else "FAILED", GMAIL_RECIPIENT()))
        sys.exit(0 if ok else 1)
