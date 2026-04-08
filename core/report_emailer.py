"""
core/report_emailer.py  --  Daily Email Report

Sections:
  1.  Portfolio Snapshot     — 3 profiles ranked by score
  2.  Per-Exchange Breakdown — Binance / MEXC / Bybit / Bitget
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
from datetime    import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text      import MIMEText
from pathlib     import Path
from loguru      import logger

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
EXCHANGE_NAMES  = ("binance", "mexc", "bybit", "bitget")

COMPARISON_FILE = Path("data/profiles/comparison.json")
CLAUDE_FILE     = Path("data/claude_analysis.json")
RESEARCH_FILE   = Path("data/research/report_latest.json")
NEWS_CACHE_FILE = Path("data/news_cache.json")
POSITIONS_FILE  = Path("data/positions.json")
LAST_SENT_FILE  = Path("data/last_email_sent.json")

def _profile_positions_file(name: str) -> Path:
    return Path("data/profiles/{}/positions.json".format(name))

def _profile_wallet_file(name: str) -> Path:
    return Path("data/profiles/{}/wallet.json".format(name))


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
        slot = now.strftime("%Y-%m-%d") + "-{:02d}".format(now.hour)
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
                logger.info("[Email] Daily report sent to {}".format(recipient))
            return ok
        except Exception as e:
            logger.error("[Email] Failed: {}".format(e))
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
                          "[TradingBot Alert] {}".format(subject), html)

    def is_configured(self) -> bool:
        return bool(GMAIL_SENDER() and GMAIL_PASSWORD() and GMAIL_RECIPIENT())

    def _build_report(self) -> tuple[str, str]:
        now      = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
        comp     = self._load_json(COMPARISON_FILE)
        claude   = self._load_json(CLAUDE_FILE)
        research = self._load_json(RESEARCH_FILE)
        news     = self._load_json(NEWS_CACHE_FILE) or {}

        all_open, all_closed = self._merge_all_positions()

        leader = (comp or {}).get("leader", "?").upper()
        bias   = (claude or {}).get("market_bias", "neutral").upper()
        fg     = news.get("fear_greed", {}).get("value", "?")
        subject = "[TradingBot] Daily Report {} | {} leads | F&G={} | {}".format(
            datetime.now(timezone.utc).strftime("%Y-%m-%d"),
            leader, fg, bias)

        connected_exs = self._detect_connected_exchanges(all_open, all_closed)
        ex_label      = " + ".join(e.capitalize() for e in connected_exs) or "No exchanges"

        html = _HTML_SHELL.format(
            now=now, ex_label=ex_label,
            portfolio_section  = self._section_portfolio(comp),
            exchange_section   = self._section_exchange_breakdown(comp, all_open, all_closed),
            claude_section     = self._section_claude(claude),
            positions_section  = self._section_open_positions(all_open),
            research_section   = self._section_research(research),
            alerts_section     = self._section_alerts(comp, claude, news),
            trades_section     = self._section_recent_trades(all_closed),
        )
        return subject, html

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
                "<b>{}{}{}</b>".format(name.upper(), flag, hflag),
                _money(bal), _pct(ret), _wr(wr),
                _pnl(pnl), _pct(-fees, neg_red=True),
                "<span class='{}'>{:.1f}%</span>".format(
                    "red" if dd >= 10 else "yellow" if dd >= 5 else "green", dd),
                str(n),
                "<span class='yellow'>{:.0f}</span>".format(sc),
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
        body = """
<table>
  <tr><th>Profile</th><th>Balance</th><th>Return</th><th>Win Rate</th>
      <th>Net P&amp;L</th><th>Fees</th><th>Drawdown</th><th>Trades</th><th>Score</th></tr>
  {rows}
</table>
<p style="margin-top:14px;font-size:13px" class="{rc}">{rec}</p>
""".format(rows=rows, rc=rec_c, rec=rec)

        return _card(
            "Portfolio Snapshot &nbsp;<span class='dim' style='font-size:12px'>"
            "Scan #{} &nbsp;|&nbsp; $300 total paper capital</span>".format(scans),
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

        ex_order = ["binance","mexc","bybit","bitget"]
        active   = [ex for ex in ex_order if ex in ex_stats or
                    any(wallet_data[p].get(ex,0) > 0 for p in PROFILE_NAMES)]
        if not active:
            return _card("Per-Exchange Breakdown","&#127759;","<p class='dim'>No exchange data yet.</p>")

        EX_COLOR = {"binance":"#f0b90b","mexc":"#00b4d8","bybit":"#f7a600","bitget":"#00cba9"}
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
                wallet_rows += "<tr style='font-weight:bold'><td>TOTAL</td><td>{}</td></tr>".format(_money(total_wallet))

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
                     ("Net P&L", "{:+.4f}".format(pnl), "green" if pnl>=0 else "red"),
                     ("Win Rate","{:.1f}%".format(wr), "green" if wr>=55 else "yellow" if wr>=40 else "red"),
                     ("Fees",    "-{:.4f}".format(fees), "orange"),
                     ("Gross",   "{:+.4f}".format(gross), "green" if gross>=0 else "red"),
                 ]),
                 wallet_rows=wallet_rows or "<tr><td colspan='2' class='dim'>No balance</td></tr>",
                 prof_rows=prof_rows or "<tr><td colspan='4' class='dim'>No trades yet</td></tr>",
                 sp_c="green" if sp_pnl>=0 else "red", ft_c="green" if ft_pnl>=0 else "red",
                 sp_pnl=sp_pnl, ft_pnl=ft_pnl, best=best, worst=worst)

        return "<h2>&#127759; Per-Exchange Breakdown</h2><div class='card'>{}</div>".format(sections_html)

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
            coin_rows += _tr("<b>{}</b>".format(coin),
                "<span class='{}'>{}</span>".format(ac,action.replace("_"," ").upper()),
                "<span class='{}'>{:.2f}x</span>".format("green" if cadj>=1.1 else "red" if cadj<=0.7 else "white",cadj),
                "<span class='dim'>{}</span>".format(reason))
        body = """
<div style="display:flex;gap:16px;flex-wrap:wrap;margin-bottom:16px">{sb}</div>
{notes}
<table><tr><th>Coin</th><th>Action</th><th>Adj</th><th>Reason</th></tr>{cr}</table>
""".format(sb=_stat_boxes([("Bias",bias.upper(),bc),("Risk","{:.2f}x".format(rm),rmc),
            ("Best",best or "—","green"),("Src","{} #{}".format(src,call_n),"dim")]),
           notes="<p style='margin-bottom:12px'><span class='dim'>{}</span><br><b>Advice:</b> {}</p>".format(note[:120],advice[:120]) if (note or advice) else "",
           cr=coin_rows or "<tr><td colspan='4' class='dim'>No data</td></tr>")
        return _card("Claude AI &nbsp;<span class='dim' style='font-size:11px'>({}) {}</span>".format(src,ts),"&#129302;",body)

    # ── Section 4: Open positions ─────────────────────────────────────

    def _section_open_positions(self, all_open: list) -> str:
        if not all_open:
            return _card("Open Positions","&#128308;","<p class='dim'>No open positions.</p>")
        by_ex = defaultdict(list)
        for p in all_open:
            by_ex[(p.get("exchange") or "?").lower()].append(p)
        rows = ""
        for ex in ["binance","mexc","bybit","bitget"] + [e for e in by_ex if e not in ("binance","mexc","bybit","bitget")]:
            pos_list = by_ex.get(ex,[])
            if not pos_list: continue
            rows += "<tr style='background:#0f172a'><td colspan='8' style='color:#7dd3fc;font-size:11px;text-transform:uppercase;padding:8px 10px;font-weight:bold'>{} ({})</td></tr>".format(ex.upper(),len(pos_list))
            for p in pos_list:
                side = (p.get("side") or "?").upper()
                sym  = p.get("symbol","?"); mtype = (p.get("market_type") or "spot").upper()
                entry= p.get("entry_price",0); sl=p.get("stop_loss",0); tp=p.get("take_profit",0)
                lev  = p.get("leverage",1); dur = int((time.time()-p.get("open_time",time.time()))/60)
                raw_s= p.get("strategy","") or ""; prof = raw_s.split("|")[1].upper() if "|" in raw_s else ""
                lev_s= " {}x".format(lev) if lev>1 else ""; sc = "green" if side=="BUY" else "red"
                rows += _tr("<span class='{}'>{}</span>".format(sc,side),"<b>{}</b>".format(sym),
                    "{}{} {}".format(mtype,lev_s,"<span class='yellow'>[{}]</span>".format(prof) if prof else ""),
                    "${:.4f}".format(entry),"<span class='red'>${:.4f}</span>".format(sl),
                    "<span class='green'>${:.4f}</span>".format(tp),"<span class='dim'>{}m</span>".format(dur),
                    "<span class='dim'>{}</span>".format(p.get("order_id","")[-6:]))
        body = "<table><tr><th>Side</th><th>Symbol</th><th>Market</th><th>Entry</th><th>SL</th><th>TP</th><th>Age</th><th>ID</th></tr>{}</table>".format(rows)
        return _card("Open Positions ({})".format(len(all_open)),"&#128308;",body)

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
            alerts.append(("info","EXTREME FEAR (F&G={}): Historical buying opportunity.".format(fg)))
        elif fg >= 80:
            alerts.append(("warning","EXTREME GREED (F&G={}): High reversal risk. Tighten stops.".format(fg)))

        # Arbitrage summary
        try:
            arb_path = Path("data/arbitrage/opportunities.json")
            if arb_path.exists():
                arb = json.loads(arb_path.read_text(encoding="utf-8"))
                arb_n = arb.get("closed_arbs",0); arb_pnl = arb.get("total_pnl",0)
                if arb_n > 0:
                    alerts.append(("info","ARBITRAGE: {} closed arbs | PnL: {:+.4f} USDT".format(arb_n,arb_pnl)))
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
        recent = sorted(all_closed, key=lambda x: x.get("close_time",0), reverse=True)[:15]
        if not recent:
            return _card("Recent Trades","&#128203;","<p class='dim'>No closed trades yet.</p>")
        rows = ""; current_ex = None
        for t in recent:
            ex = (t.get("exchange") or "?").lower()
            pnl = t.get("pnl",0) or 0; gross = t.get("gross_pnl",pnl) or pnl
            fees = t.get("total_fees",0) or 0; side = (t.get("side") or "?").upper()
            sym = t.get("symbol","?"); reason = t.get("close_reason","?")
            ct = t.get("close_time"); ts_str = datetime.fromtimestamp(ct).strftime("%m/%d %H:%M") if ct else "--"
            raw_s = t.get("strategy","?") or "?"; strat = raw_s.split("|")[0][:14]
            prof = raw_s.split("|")[1].upper()[:4] if "|" in raw_s else ""
            paper = t.get("paper_trade",True); sc = "green" if side=="BUY" else "red"; pc = "green" if pnl>=0 else "red"
            mode = "[P]" if paper else "<span class='green'>[L]</span>"
            if ex != current_ex:
                current_ex = ex
                rows += "<tr style='background:#0f172a'><td colspan='9' style='color:#7dd3fc;font-size:11px;text-transform:uppercase;padding:6px 10px;font-weight:bold'>{}</td></tr>".format(ex.upper())
            rows += _tr("<span class='dim'>{}</span>".format(ts_str), mode,
                "<span class='yellow'>{}</span>".format(prof) if prof else "",
                "<span class='{}'>{}</span>".format(sc,side), "<b>{}</b>".format(sym),
                "<span class='dim'>{}</span>".format(strat),
                "<span class='{}'>{}{:.4f}</span>".format(pc,"+" if pnl>=0 else "",pnl),
                "<span class='dim'>{:+.4f}</span>".format(gross),
                "<span class='dim'>-{:.4f}</span>".format(fees),
                "<span class='dim'>{}</span>".format(reason))
        body = "<table><tr><th>Time</th><th>Mode</th><th>Prof</th><th>Side</th><th>Symbol</th><th>Strategy</th><th>Net P&amp;L</th><th>Gross</th><th>Fees</th><th>Reason</th></tr>{}</table>".format(rows)
        return _card("Recent Trades (last 15, all profiles)","&#128203;",body)

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
                logger.debug("[Email] _ingest {}: {}".format(path,e))
        _ingest(POSITIONS_FILE)
        for name in PROFILE_NAMES: _ingest(_profile_positions_file(name))
        all_closed.sort(key=lambda x: x.get("close_time",0),reverse=True)
        return all_open, all_closed

    def _detect_connected_exchanges(self, all_open, all_closed):
        seen=set()
        for t in all_open+all_closed:
            ex=(t.get("exchange") or "").lower()
            if ex: seen.add(ex)
        order=["binance","mexc","bybit","bitget"]
        return [e for e in order if e in seen]+[e for e in seen if e not in order]

    def _load_json(self, path):
        try:
            if path.exists(): return json.loads(path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug("[Email] Load {}: {}".format(path.name,e))
        return None

    def _send(self, sender, password, recipient, subject, html):
        try:
            msg=MIMEMultipart("alternative"); msg["Subject"]=subject; msg["From"]=sender; msg["To"]=recipient
            msg.attach(MIMEText(html,"html","utf-8"))
            ctx=ssl.create_default_context()
            with smtplib.SMTP_SSL("smtp.gmail.com",465,context=ctx) as srv:
                srv.login(sender,password); srv.sendmail(sender,[recipient],msg.as_string())
            logger.info("[Email] Sent: {}".format(subject[:70])); return True
        except smtplib.SMTPAuthenticationError:
            logger.error("[Email] Auth failed — use a Gmail App Password."); return False
        except Exception as e:
            logger.error("[Email] Send failed: {}".format(e)); return False

    def _load_last_sent(self):
        try:
            if LAST_SENT_FILE.exists(): return json.loads(LAST_SENT_FILE.read_text(encoding="utf-8")).get("date","")
        except Exception: pass
        return ""

    def _save_last_sent(self):
        try:
            LAST_SENT_FILE.parent.mkdir(parents=True,exist_ok=True)
            _slot_now = datetime.now(timezone.utc)
            _slot_key = _slot_now.strftime("%Y-%m-%d") + "-{:02d}".format(_slot_now.hour)
            LAST_SENT_FILE.write_text(json.dumps({"date":_slot_key,"sent_at":_slot_now.isoformat()}),encoding="utf-8")
        except Exception as e:
            logger.debug("[Email] Save last-sent: {}".format(e))


# ═══════════════════════════════════════════════════════════════════════
# HTML helpers
# ═══════════════════════════════════════════════════════════════════════

def _card(title,icon,body):
    return "\n<h2>{} {}</h2>\n<div class=\"card\">\n  {}\n</div>".format(icon,title,body)

def _tr(*cells):
    return "<tr>"+"".join("<td>{}</td>".format(c) for c in cells)+"</tr>"

def _td(content=""):
    return "<td>{}</td>".format(content)

def _money(val,color=False):
    sign="+" if val>=0 else ""; c=("green" if val>=0 else "red") if color else "white"
    return "<span class='{}'>{}{:.4f} USDT</span>".format(c,sign,val)

def _pct(val,neg_red=False):
    c="green" if val>=0 else "red"
    if neg_red: c="red" if val!=0 else "dim"
    return "<span class='{}'>{:+.2f}%</span>".format(c,val)

def _wr(val):
    c="green" if val>=55 else "yellow" if val>=45 else "red"
    return "<span class='{}'>{:.1f}%</span>".format(c,val)

def _pnl(val):
    sign="+" if val>=0 else ""; c="green" if val>=0 else "red"
    return "<span class='{}'>{}{:.4f}</span>".format(c,sign,val)

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
{portfolio_section}
{exchange_section}
{claude_section}
{positions_section}
{research_section}
{alerts_section}
{trades_section}
<div class="footer">
  Automated report — Binance + MEXC + Bybit + Bitget | Crypto + Gold + Silver + Oil<br>
  <b>DISCLAIMER:</b> This is a dry-run paper-trading system. Not financial advice.
</div>
</div>
</body>
</html>"""


if __name__ == "__main__":
    import sys
    try:
        from dotenv import load_dotenv; load_dotenv()
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
            "<p>Daily reports at {:02d}:00 UTC.</p></body></html>".format(REPORT_HOUR))
        print("\n  {} — {}".format("SUCCESS" if ok else "FAILED", r)); sys.exit(0 if ok else 1)
    elif cmd == "--preview":
        print("\n  Building preview...\n")
        subject, html = emailer._build_report()
        out = Path("data/email_preview.html"); out.parent.mkdir(parents=True,exist_ok=True)
        out.write_text(html,encoding="utf-8")
        print("  Subject: {}\n  Preview: {}".format(subject, out.resolve()))
        try: import os; os.startfile(str(out.resolve()))
        except Exception: pass
    else:
        print("\n  Sending daily report...\n")
        ok = emailer.send_daily(force=True)
        print("\n  {} — {}".format("SUCCESS" if ok else "FAILED", GMAIL_RECIPIENT()))
        sys.exit(0 if ok else 1)
