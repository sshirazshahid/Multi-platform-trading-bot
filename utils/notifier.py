"""
utils/notifier.py — Email notification system via Gmail SMTP.

Setup:
  1. Enable 2-Step Verification: myaccount.google.com/security
  2. Create an App Password: myaccount.google.com/apppasswords
  3. Set GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT in .env

Every method keeps its original positional signature for backward compatibility
and accepts optional keyword extras that render additional sections:

  - trade_opened(..., leverage=, confidence=, risk_usd=, rr_ratio=, tier=,
                 balance=, open_positions=, total_exposure=, reasons=, ...)
  - trade_closed(..., duration_sec=, r_multiple=, leverage=, total_fees=,
                 entry_fee=, exit_fee=, gross_pnl=, balance_after=,
                 partial_taken=, sl_moved_to_be=, strategy=, ...)
  - daily_summary(..., per_exchange=, per_strategy=, best_trade=, worst_trade=,
                  avg_win=, avg_loss=, expectancy=, drawdown_pct=, peak_equity=,
                  gross_pnl=, total_fees=, open_pnl=, open_positions=,
                  halt_status=, paper_trades=, live_trades=, ...)
"""

import re
import smtplib
import ssl
import time
from datetime import datetime, timedelta
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText

from loguru import logger

from config import DRY_RUN, EMAIL_SUBJECT_PREFIX, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT, GMAIL_SENDER


def _fmt_duration(seconds: float) -> str:
    if not seconds or seconds < 0:
        return "—"
    td = timedelta(seconds=int(seconds))
    days = td.days
    hrs, rem = divmod(td.seconds, 3600)
    mins, _ = divmod(rem, 60)
    if days:
        return f"{days}d {hrs}h {mins}m"
    if hrs:
        return f"{hrs}h {mins}m"
    return f"{mins}m"


def _fmt_price(v: float) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return str(v)
    if v >= 1000:
        return f"{v:,.2f}"
    if v >= 1:
        return f"{v:,.4f}"
    return f"{v:.6f}"


def _fmt_usd(v: float, signed: bool = False) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return str(v)
    sign = "+" if (signed and v >= 0) else ""
    return f"{sign}{v:,.4f} USDT"


def _fmt_pct(v: float, signed: bool = False) -> str:
    if v is None:
        return "—"
    try:
        v = float(v)
    except Exception:
        return str(v)
    sign = "+" if (signed and v >= 0) else ""
    return f"{sign}{v:.2f}%"


class EmailNotifier:
    """Send trading alerts via Gmail SMTP using an App Password."""

    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 465
    SMTP_TIMEOUT_SECONDS = 15

    def __init__(self):
        self.sender    = GMAIL_SENDER
        self.password  = GMAIL_APP_PASSWORD
        self.recipient = GMAIL_RECIPIENT
        self.prefix    = EMAIL_SUBJECT_PREFIX
        self.enabled   = bool(self.sender and self.password and self.recipient)
        self._last_send: dict[str, float] = {}
        self._send_cooldown = 300  # 5 minutes
        if not self.enabled:
            logger.warning("[Notifier] Email disabled — set GMAIL_SENDER, "
                           "GMAIL_APP_PASSWORD, GMAIL_RECIPIENT in .env")

    # ─────────────────────────────────────────────────────────────────
    # Transport
    # ─────────────────────────────────────────────────────────────────

    def send(self, subject: str, body_html: str, *, bypass_cooldown: bool = False):
        if not self.enabled:
            return False
        subject_key = subject[:50]
        now = time.time()
        if not bypass_cooldown and now - self._last_send.get(subject_key, 0) < self._send_cooldown:
            return True  # a matching notification was delivered recently
        full_subject = f"{self.prefix} {subject}"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = full_subject
        msg["From"]    = f"Trading Bot <{self.sender}>"
        msg["To"]      = self.recipient
        plain = re.sub(r"<[^>]+>", "",
                       body_html.replace("<br>", "\n")
                                .replace("</tr>", "\n")
                                .replace("</h3>", "\n"))
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(body_html, "html"))
        ctx = ssl.create_default_context()
        try:
            with smtplib.SMTP_SSL(
                self.SMTP_HOST,
                self.SMTP_PORT,
                context=ctx,
                timeout=self.SMTP_TIMEOUT_SECONDS,
            ) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipient, msg.as_string())
            # A failed transport must not consume the cooldown. Watchdogs can
            # retry on the next tick after a transient SMTP outage.
            self._last_send[subject_key] = now
            logger.info(f"[Notifier] Email sent: {full_subject}")
            return True
        except smtplib.SMTPAuthenticationError:
            logger.error("[Notifier] Gmail auth failed — use an App Password, not your regular password.")
            return False
        except Exception as e:
            logger.error(f"[Notifier] Email send failed: {e}")
            return False

    # ─────────────────────────────────────────────────────────────────
    # HTML helpers
    # ─────────────────────────────────────────────────────────────────

    @staticmethod
    def _row(label: str, value: str, color: str = "") -> str:
        val_style = f"color:{color};font-weight:600;" if color else ""
        return (f"<tr>"
                f"<td style='padding:6px 12px;color:#6b7280;font-size:13px;"
                f"border-bottom:1px solid #f3f4f6;'>{label}</td>"
                f"<td style='padding:6px 12px;font-size:13px;{val_style}"
                f"border-bottom:1px solid #f3f4f6;'>{value}</td>"
                f"</tr>")

    @staticmethod
    def _section_header(title: str) -> str:
        return (f"<tr><td colspan='2' style='padding:10px 12px 6px;"
                f"background:#f9fafb;color:#374151;font-size:11px;"
                f"text-transform:uppercase;letter-spacing:0.5px;"
                f"font-weight:700;border-top:1px solid #e5e7eb;"
                f"border-bottom:1px solid #e5e7eb;'>{title}</td></tr>")

    def _table(self, rows_html: str, header: str, header_color: str,
               subtitle: str = "") -> str:
        mode_badge = ("<span style='background:#fef3c7;color:#92400e;padding:2px 8px;"
                      "border-radius:4px;font-size:11px;'>DRY RUN</span>" if DRY_RUN else
                      "<span style='background:#fee2e2;color:#991b1b;padding:2px 8px;"
                      "border-radius:4px;font-size:11px;'>LIVE</span>")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        sub_html = (f"<div style='color:rgba(255,255,255,0.85);font-size:12px;"
                    f"margin-top:2px;'>{subtitle}</div>" if subtitle else "")
        return (f"<div style='font-family:Arial,sans-serif;max-width:640px;margin:0 auto;'>"
                f"<div style='background:{header_color};padding:14px 20px;"
                f"border-radius:8px 8px 0 0;'>"
                f"<h2 style='margin:0;color:#fff;font-size:16px;'>{header}</h2>"
                f"{sub_html}"
                f"<span style='color:rgba(255,255,255,0.75);font-size:11px;'>"
                f"{ts} {mode_badge}</span>"
                f"</div>"
                f"<table style='width:100%;border-collapse:collapse;background:#fff;"
                f"border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;'>"
                f"{rows_html}</table>"
                f"<p style='color:#9ca3af;font-size:11px;text-align:center;margin-top:8px;'>"
                f"Trading Bot</p></div>")

    def _grid_table(self, title: str, headers: list, rows: list) -> str:
        """Compact tabular block used inside a section (for per-exchange / per-strategy)."""
        if not rows:
            return ""
        thead = "".join(
            f"<th style='padding:6px 10px;font-size:11px;color:#6b7280;"
            f"text-transform:uppercase;text-align:left;"
            f"border-bottom:1px solid #e5e7eb;'>{h}</th>" for h in headers
        )
        tbody = ""
        for r in rows:
            cells = "".join(
                f"<td style='padding:5px 10px;font-size:12px;"
                f"border-bottom:1px solid #f3f4f6;'>{c}</td>" for c in r
            )
            tbody += f"<tr>{cells}</tr>"
        return (f"<tr><td colspan='2' style='padding:0;'>"
                f"<div style='padding:8px 12px 2px;font-size:11px;color:#374151;"
                f"text-transform:uppercase;letter-spacing:0.5px;font-weight:700;"
                f"background:#f9fafb;border-top:1px solid #e5e7eb;'>{title}</div>"
                f"<table style='width:100%;border-collapse:collapse;background:#fff;'>"
                f"<thead><tr>{thead}</tr></thead>"
                f"<tbody>{tbody}</tbody></table>"
                f"</td></tr>")

    # ─────────────────────────────────────────────────────────────────
    # trade_opened — richer, backwards compatible
    # ─────────────────────────────────────────────────────────────────

    def trade_opened(self, exchange: str, symbol: str, side: str,
                     entry: float, size: float, sl: float, tp: float,
                     strategy: str, market_type: str, **extra):
        c = "#16a34a" if side.upper() == "BUY" else "#dc2626"

        # ── Derived risk metrics ──
        leverage = extra.get("leverage")
        notional = size * entry if entry and size else None
        margin   = (notional / leverage) if (notional and leverage) else None
        sl_dist_pct = None
        tp_dist_pct = None
        if entry and sl and tp:
            if side.upper() == "BUY":
                sl_dist_pct = (entry - sl) / entry * 100
                tp_dist_pct = (tp - entry) / entry * 100
            else:
                sl_dist_pct = (sl - entry) / entry * 100
                tp_dist_pct = (entry - tp) / entry * 100
        rr_ratio = extra.get("rr_ratio")
        if rr_ratio is None and sl_dist_pct and tp_dist_pct and sl_dist_pct > 0:
            rr_ratio = tp_dist_pct / sl_dist_pct
        risk_usd = extra.get("risk_usd")
        if risk_usd is None and notional and sl_dist_pct:
            risk_usd = notional * sl_dist_pct / 100.0
        reward_usd = extra.get("reward_usd")
        if reward_usd is None and notional and tp_dist_pct:
            reward_usd = notional * tp_dist_pct / 100.0

        # ── Setup section ──
        rows  = self._section_header("Setup")
        rows += self._row("Exchange",   f"{exchange.upper()} ({market_type.upper()})")
        rows += self._row("Symbol",     f"<strong>{symbol}</strong>")
        rows += self._row("Side",       side.upper(), c)
        rows += self._row("Strategy",   strategy)
        if extra.get("tier"):
            rows += self._row("Tier", str(extra["tier"]))
        if extra.get("confidence") is not None:
            rows += self._row("Confidence", f"{float(extra['confidence'])*100:.0f}%")
        if extra.get("score") is not None:
            rows += self._row("MCP Score", f"{float(extra['score']):.1f}")
        if extra.get("layers_ok") is not None:
            rows += self._row("Layers Passed", str(extra["layers_ok"]))
        if extra.get("hour_signal"):
            rows += self._row("Hour Signal", str(extra["hour_signal"]))
        if extra.get("regime"):
            rows += self._row("Market Regime", str(extra["regime"]))
        if extra.get("correlation_group"):
            rows += self._row("Correlation Group", str(extra["correlation_group"]))
        if extra.get("meta_filter"):
            rows += self._row("Meta-Filter", str(extra["meta_filter"]))

        # ── Price section ──
        rows += self._section_header("Price & Size")
        rows += self._row("Entry",      _fmt_price(entry))
        rows += self._row("Size",       f"{size:.8f}")
        if notional:
            rows += self._row("Notional", _fmt_usd(notional))
        if leverage:
            rows += self._row("Leverage", f"{leverage}x")
        if margin:
            rows += self._row("Margin Required", _fmt_usd(margin))

        # ── Risk section ──
        rows += self._section_header("Risk & Reward")
        sl_line = _fmt_price(sl)
        if sl_dist_pct is not None:
            sl_line += f"  <span style='color:#9ca3af;'>(−{sl_dist_pct:.2f}%)</span>"
        tp_line = _fmt_price(tp)
        if tp_dist_pct is not None:
            tp_line += f"  <span style='color:#9ca3af;'>(+{tp_dist_pct:.2f}%)</span>"
        rows += self._row("Stop Loss",    sl_line, "#dc2626")
        rows += self._row("Take Profit",  tp_line, "#16a34a")
        if rr_ratio:
            rows += self._row("R:R Ratio",  f"{rr_ratio:.2f}:1")
        if risk_usd:
            rows += self._row("Risk at SL", _fmt_usd(risk_usd), "#dc2626")
        if reward_usd:
            rows += self._row("Reward at TP", _fmt_usd(reward_usd), "#16a34a")
        if extra.get("risk_pct_of_balance") is not None:
            rows += self._row("Risk % of Balance",
                              _fmt_pct(extra["risk_pct_of_balance"]))

        # ── Account section ──
        acct_rows = []
        if extra.get("balance") is not None:
            acct_rows.append(("Balance Before", _fmt_usd(extra["balance"])))
        if extra.get("open_positions") is not None:
            acct_rows.append(("Open Positions", str(extra["open_positions"])))
        if extra.get("total_exposure") is not None:
            acct_rows.append(("Total Exposure", _fmt_usd(extra["total_exposure"])))
        if extra.get("exposure_pct") is not None:
            acct_rows.append(("Exposure %", _fmt_pct(extra["exposure_pct"])))
        if acct_rows:
            rows += self._section_header("Account")
            for label, val in acct_rows:
                rows += self._row(label, val)

        # ── Reasons section (why we entered) ──
        reasons = extra.get("reasons")
        if reasons:
            rows += self._section_header("Entry Reasons")
            if isinstance(reasons, (list, tuple)):
                text = "<br>".join(f"&bull; {r}" for r in reasons)
            else:
                text = str(reasons).replace("|", "<br>&bull;")
                text = "&bull; " + text
            rows += (f"<tr><td colspan='2' style='padding:8px 12px;"
                     f"font-size:12px;color:#374151;"
                     f"border-bottom:1px solid #f3f4f6;'>{text}</td></tr>")

        subtitle = f"{strategy.upper()} &mdash; {market_type.upper()}"
        if rr_ratio:
            subtitle += f" &mdash; R:R {rr_ratio:.1f}:1"
        self.send(f"Trade Opened -- {symbol} {side.upper()}",
                  self._table(rows, f"Trade Opened: {symbol} {side.upper()}",
                              c, subtitle))

    # ─────────────────────────────────────────────────────────────────
    # trade_closed — richer, backwards compatible
    # ─────────────────────────────────────────────────────────────────

    def trade_closed(self, exchange: str, symbol: str, side: str,
                     entry: float, exit_price: float, pnl: float,
                     pnl_pct: float, reason: str, **extra):
        win  = (pnl or 0) >= 0
        c    = "#16a34a" if win else "#dc2626"
        sign = "+" if win else ""

        # ── Derive metrics from extras where possible ──
        duration = extra.get("duration_sec")
        if duration is None and extra.get("open_time") and extra.get("close_time"):
            duration = float(extra["close_time"]) - float(extra["open_time"])

        size       = extra.get("size")
        leverage   = extra.get("leverage")
        gross_pnl  = extra.get("gross_pnl")
        entry_fee  = extra.get("entry_fee")
        exit_fee   = extra.get("exit_fee")
        total_fees = extra.get("total_fees")
        sl_price   = extra.get("stop_loss")
        tp_price   = extra.get("take_profit")
        r_mult     = extra.get("r_multiple")

        # R-multiple = (exit - entry) / (entry - sl) side-aware
        if r_mult is None and sl_price and entry and exit_price:
            try:
                if side.lower() == "buy":
                    denom = float(entry) - float(sl_price)
                    if denom > 0:
                        r_mult = (float(exit_price) - float(entry)) / denom
                else:
                    denom = float(sl_price) - float(entry)
                    if denom > 0:
                        r_mult = (float(entry) - float(exit_price)) / denom
            except Exception:
                r_mult = None

        price_move_pct = None
        if entry and exit_price:
            try:
                if side.lower() == "buy":
                    price_move_pct = (float(exit_price) - float(entry)) / float(entry) * 100
                else:
                    price_move_pct = (float(entry) - float(exit_price)) / float(entry) * 100
            except Exception:
                pass

        # ── Outcome section ──
        outcome = "WIN" if win else "LOSS"
        if reason and "stop" in str(reason).lower():
            outcome_color = "#dc2626"
        elif reason and ("take_profit" in str(reason).lower() or "tp" in str(reason).lower()):
            outcome_color = "#16a34a"
        else:
            outcome_color = c

        rows  = self._section_header("Outcome")
        rows += self._row("Symbol",       f"<strong>{symbol}</strong>")
        rows += self._row("Exchange",     exchange.upper())
        rows += self._row("Side",         side.upper())
        if extra.get("strategy"):
            rows += self._row("Strategy",   str(extra["strategy"]))
        rows += self._row("Result",       outcome, outcome_color)
        rows += self._row("Close Reason", str(reason))
        if duration is not None:
            rows += self._row("Hold Time", _fmt_duration(duration))

        # ── Price section ──
        rows += self._section_header("Price")
        rows += self._row("Entry",      _fmt_price(entry))
        rows += self._row("Exit",       _fmt_price(exit_price))
        if price_move_pct is not None:
            rows += self._row("Price Move",
                              _fmt_pct(price_move_pct, signed=True),
                              "#16a34a" if price_move_pct >= 0 else "#dc2626")
        if sl_price:
            rows += self._row("Stop Loss", _fmt_price(sl_price))
        if tp_price:
            rows += self._row("Take Profit", _fmt_price(tp_price))

        # ── PnL section ──
        rows += self._section_header("Profit & Loss")
        if gross_pnl is not None:
            rows += self._row("Gross PnL",
                              _fmt_usd(gross_pnl, signed=True),
                              "#16a34a" if gross_pnl >= 0 else "#dc2626")
        if entry_fee is not None:
            rows += self._row("Entry Fee", _fmt_usd(entry_fee), "#6b7280")
        if exit_fee is not None:
            rows += self._row("Exit Fee",  _fmt_usd(exit_fee), "#6b7280")
        if total_fees is not None:
            rows += self._row("Total Fees", _fmt_usd(total_fees), "#6b7280")
        rows += self._row("Net PnL",
                          f"{sign}{pnl:.4f} USDT ({sign}{pnl_pct:.2f}%)", c)
        if r_mult is not None:
            rows += self._row("R-Multiple",
                              f"{r_mult:+.2f}R",
                              "#16a34a" if r_mult >= 0 else "#dc2626")

        # ── Execution flags ──
        flags = []
        if extra.get("partial_taken"):
            flags.append("Partial TP taken")
        if extra.get("sl_moved_to_be"):
            flags.append("SL moved to breakeven")
        if extra.get("trailing_active"):
            flags.append("Trailing stop active")
        if extra.get("slippage_usd") is not None:
            flags.append(f"Slippage: {_fmt_usd(extra['slippage_usd'])}")
        if leverage:
            flags.append(f"Leverage {leverage}x")
        if size is not None:
            flags.append(f"Size {float(size):.8f}")
        if flags:
            rows += self._section_header("Execution")
            for f in flags:
                rows += (f"<tr><td colspan='2' style='padding:5px 12px;"
                         f"font-size:12px;color:#374151;"
                         f"border-bottom:1px solid #f3f4f6;'>&bull; {f}</td></tr>")

        # ── Account section ──
        acct_rows = []
        if extra.get("balance_after") is not None:
            acct_rows.append(("Balance After", _fmt_usd(extra["balance_after"])))
        if extra.get("daily_pnl") is not None:
            acct_rows.append(("Daily PnL",
                              _fmt_usd(extra["daily_pnl"], signed=True)))
        if extra.get("daily_trades") is not None:
            acct_rows.append(("Daily Trades", str(extra["daily_trades"])))
        if extra.get("daily_wr") is not None:
            acct_rows.append(("Daily Win Rate",
                              f"{float(extra['daily_wr']):.1f}%"))
        if extra.get("open_positions") is not None:
            acct_rows.append(("Open Positions", str(extra["open_positions"])))
        if acct_rows:
            rows += self._section_header("Account Status")
            for label, val in acct_rows:
                rows += self._row(label, val)

        subtitle = f"Net: {sign}{pnl:.4f} USDT ({sign}{pnl_pct:.2f}%)"
        if r_mult is not None:
            subtitle += f" &mdash; {r_mult:+.2f}R"
        self.send(
            f"Trade Closed -- {symbol} | {sign}{pnl:.4f} USDT",
            self._table(rows,
                        f"Trade Closed: {symbol} ({outcome})",
                        c, subtitle)
        )

    # ─────────────────────────────────────────────────────────────────
    # daily_summary — richer, backwards compatible
    # ─────────────────────────────────────────────────────────────────

    def daily_summary(self, total_trades: int, wins: int, losses: int,
                      total_pnl: float, balance: float, **extra):
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        c = "#16a34a" if total_pnl >= 0 else "#dc2626"
        sign = "+" if total_pnl >= 0 else ""

        gross_pnl  = extra.get("gross_pnl")
        total_fees = extra.get("total_fees")
        avg_win    = extra.get("avg_win")
        avg_loss   = extra.get("avg_loss")
        expectancy = extra.get("expectancy")
        if expectancy is None and avg_win is not None and avg_loss is not None and total_trades:
            try:
                p_win = wins / total_trades
                p_lose = losses / total_trades
                expectancy = p_win * float(avg_win) + p_lose * float(avg_loss)
            except Exception:
                expectancy = None
        profit_factor = extra.get("profit_factor")
        if profit_factor is None and avg_win is not None and avg_loss and losses and wins:
            try:
                gross_win = float(avg_win) * wins
                gross_lose = abs(float(avg_loss) * losses)
                if gross_lose > 0:
                    profit_factor = gross_win / gross_lose
            except Exception:
                profit_factor = None

        # ── Overview ──
        rows  = self._section_header("Overview")
        rows += self._row("Total Trades", str(total_trades))
        rows += self._row("Wins",         str(wins),   "#16a34a")
        rows += self._row("Losses",       str(losses), "#dc2626")
        rows += self._row("Win Rate",     f"{win_rate:.1f}%")
        if extra.get("paper_trades") is not None:
            rows += self._row("Paper Trades", str(extra["paper_trades"]))
        if extra.get("live_trades") is not None:
            rows += self._row("Live Trades",  str(extra["live_trades"]))

        # ── P&L ──
        rows += self._section_header("Profit & Loss")
        if gross_pnl is not None:
            rows += self._row("Gross PnL", _fmt_usd(gross_pnl, signed=True),
                              "#16a34a" if gross_pnl >= 0 else "#dc2626")
        if total_fees is not None:
            rows += self._row("Total Fees", _fmt_usd(total_fees), "#6b7280")
        rows += self._row("Net PnL", f"{sign}{total_pnl:.4f} USDT", c)
        if avg_win is not None:
            rows += self._row("Avg Win", _fmt_usd(avg_win, signed=True), "#16a34a")
        if avg_loss is not None:
            rows += self._row("Avg Loss", _fmt_usd(avg_loss, signed=True), "#dc2626")
        if expectancy is not None:
            rows += self._row("Expectancy / Trade",
                              _fmt_usd(expectancy, signed=True),
                              "#16a34a" if expectancy >= 0 else "#dc2626")
        if profit_factor is not None:
            rows += self._row("Profit Factor", f"{profit_factor:.2f}")

        # ── Best / worst trade ──
        best = extra.get("best_trade")
        worst = extra.get("worst_trade")
        if best or worst:
            rows += self._section_header("Extremes")
            if best:
                label = f"{best.get('symbol', '?')} {best.get('side', '').upper()}"
                rows += self._row("Best Trade",
                                  f"{label}  {_fmt_usd(best.get('pnl'), signed=True)}",
                                  "#16a34a")
            if worst:
                label = f"{worst.get('symbol', '?')} {worst.get('side', '').upper()}"
                rows += self._row("Worst Trade",
                                  f"{label}  {_fmt_usd(worst.get('pnl'), signed=True)}",
                                  "#dc2626")

        # ── Account ──
        rows += self._section_header("Account")
        rows += self._row("Balance", _fmt_usd(balance))
        if extra.get("peak_equity") is not None:
            rows += self._row("Peak Equity", _fmt_usd(extra["peak_equity"]))
        if extra.get("drawdown_pct") is not None:
            rows += self._row("Drawdown",
                              _fmt_pct(extra["drawdown_pct"]),
                              "#dc2626" if float(extra["drawdown_pct"]) < 0 else "")
        if extra.get("open_positions") is not None:
            rows += self._row("Open Positions", str(extra["open_positions"]))
        if extra.get("open_pnl") is not None:
            opnl = float(extra["open_pnl"])
            rows += self._row("Open (Unrealized) PnL",
                              _fmt_usd(opnl, signed=True),
                              "#16a34a" if opnl >= 0 else "#dc2626")
        if extra.get("halt_status"):
            rows += self._row("Halt Status", str(extra["halt_status"]), "#dc2626")

        # ── Per-exchange ──
        per_ex = extra.get("per_exchange") or {}
        if per_ex:
            ex_rows = []
            for ex_name, st in per_ex.items():
                ex_rows.append([
                    str(ex_name).upper(),
                    str(st.get("trades", 0)),
                    f"{st.get('win_rate', 0):.1f}%",
                    _fmt_usd(st.get("pnl", 0), signed=True),
                ])
            rows += self._grid_table(
                "Per-Exchange",
                ["Exchange", "Trades", "WR", "Net PnL"],
                ex_rows,
            )

        # ── Per-strategy ──
        per_strat = extra.get("per_strategy") or {}
        if per_strat:
            st_rows = []
            for s_name, st in per_strat.items():
                st_rows.append([
                    str(s_name),
                    str(st.get("trades", 0)),
                    f"{st.get('win_rate', 0):.1f}%",
                    _fmt_usd(st.get("pnl", 0), signed=True),
                ])
            rows += self._grid_table(
                "Per-Strategy",
                ["Strategy", "Trades", "WR", "Net PnL"],
                st_rows,
            )

        # ── Per-hour heatmap (top/bottom) ──
        hour_scores = extra.get("hour_scores") or {}
        if hour_scores:
            sorted_hours = sorted(
                hour_scores.items(),
                key=lambda kv: kv[1].get("net_pnl", 0) if isinstance(kv[1], dict) else 0,
                reverse=True,
            )
            top = sorted_hours[:3]
            bot = [h for h in sorted_hours[-3:] if h not in top]
            h_rows = []
            for h, d in top + bot:
                if not isinstance(d, dict):
                    continue
                h_rows.append([
                    f"{int(h):02d}:00",
                    str(d.get("trades", 0)),
                    f"{d.get('win_rate', 0):.1f}%",
                    _fmt_usd(d.get("net_pnl", 0), signed=True),
                ])
            if h_rows:
                rows += self._grid_table(
                    "Hour-of-Day (top/bottom)",
                    ["Hour", "Trades", "WR", "Net PnL"],
                    h_rows,
                )

        subtitle = f"{total_trades} trades &mdash; WR {win_rate:.1f}% &mdash; {sign}{total_pnl:.4f} USDT"
        self.send(
            f"Daily Summary -- PnL: {sign}{total_pnl:.4f} USDT",
            self._table(rows, "Daily Summary", "#1d4ed8", subtitle)
        )

    # ─────────────────────────────────────────────────────────────────
    # Operational alerts
    # ─────────────────────────────────────────────────────────────────

    def alert(self, message: str, *, title: str = "Alert",
              context: dict | None = None):
        """Operational alert. Optional context dict renders as key/value rows."""
        body_rows = (f"<tr><td colspan='2' style='padding:12px;"
                     f"font-size:13px;color:#78350f;"
                     f"border-bottom:1px solid #fcd34d;'>{message}</td></tr>")
        if context:
            body_rows += self._section_header("Context")
            for k, v in context.items():
                body_rows += self._row(str(k), str(v))
        return self.send(
            title if title != "Alert" else "Bot Alert",
            f"<div style='font-family:Arial,sans-serif;max-width:560px;"
            f"margin:0 auto;'>"
            f"<div style='background:#f59e0b;padding:12px 20px;"
            f"border-radius:8px 8px 0 0;'>"
            f"<h2 style='margin:0;color:#fff;font-size:15px;'>{title}</h2>"
            f"</div>"
            f"<table style='width:100%;border-collapse:collapse;"
            f"background:#fffbeb;border:1px solid #fcd34d;border-top:none;"
            f"border-radius:0 0 8px 8px;'>{body_rows}</table></div>",
        )

    def error(self, message: str, *, title: str = "Error",
              context: dict | None = None):
        body_rows = (f"<tr><td colspan='2' style='padding:12px;"
                     f"font-size:13px;color:#7f1d1d;font-family:monospace;"
                     f"white-space:pre-wrap;"
                     f"border-bottom:1px solid #fca5a5;'>{message}</td></tr>")
        if context:
            body_rows += self._section_header("Context")
            for k, v in context.items():
                body_rows += self._row(str(k), str(v))
        self.send(title if title != "Error" else "Bot Error",
                  f"<div style='font-family:Arial,sans-serif;max-width:560px;"
                  f"margin:0 auto;'>"
                  f"<div style='background:#dc2626;padding:12px 20px;"
                  f"border-radius:8px 8px 0 0;'>"
                  f"<h2 style='margin:0;color:#fff;font-size:15px;'>{title}</h2>"
                  f"</div>"
                  f"<table style='width:100%;border-collapse:collapse;"
                  f"background:#fef2f2;border:1px solid #fca5a5;border-top:none;"
                  f"border-radius:0 0 8px 8px;'>{body_rows}</table></div>",
                  bypass_cooldown=True)

    def sl_failed(self, symbol: str, exchange: str, side: str,
                  size: float, sl: float, reason: str, **extra):
        """Emergency: exchange rejected SL placement — position is unprotected."""
        ctx = {
            "Exchange":  exchange.upper(),
            "Symbol":    symbol,
            "Side":      side.upper(),
            "Size":      f"{size:.8f}",
            "Intended SL": _fmt_price(sl),
            "Error":     str(reason)[:200],
        }
        if extra.get("entry_price"):
            ctx["Entry"] = _fmt_price(extra["entry_price"])
        if extra.get("leverage"):
            ctx["Leverage"] = f"{extra['leverage']}x"
        self.error(
            f"EMERGENCY — Stop-Loss placement FAILED on {symbol}. "
            f"Position is UNPROTECTED on the exchange. "
            f"Local monitor is the only safety net.",
            title="SL FAILED — POSITION UNPROTECTED",
            context=ctx,
        )

    def halt(self, reason: str, **extra):
        ctx = {k: str(v) for k, v in extra.items()}
        self.error(
            f"Trading halted: {reason}",
            title="Bot Halted",
            context=ctx or None,
        )

    def resume(self, reason: str, **extra):
        ctx = {k: str(v) for k, v in extra.items()}
        self.alert(
            f"Trading resumed: {reason}",
            title="Bot Resumed",
            context=ctx or None,
        )


# Alias so existing imports of TelegramNotifier keep working
TelegramNotifier = EmailNotifier
