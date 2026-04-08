"""
utils/notifier.py — Email notification system via Gmail SMTP.

Setup:
  1. Enable 2-Step Verification: myaccount.google.com/security
  2. Create an App Password: myaccount.google.com/apppasswords
  3. Set GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT in .env
"""

import smtplib
import ssl
import re
from email.mime.text      import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime             import datetime
from loguru               import logger
from config import (
    GMAIL_SENDER, GMAIL_APP_PASSWORD, GMAIL_RECIPIENT,
    EMAIL_SUBJECT_PREFIX, DRY_RUN
)


class EmailNotifier:
    """Send trading alerts via Gmail SMTP using an App Password."""

    SMTP_HOST = "smtp.gmail.com"
    SMTP_PORT = 465

    def __init__(self):
        self.sender    = GMAIL_SENDER
        self.password  = GMAIL_APP_PASSWORD
        self.recipient = GMAIL_RECIPIENT
        self.prefix    = EMAIL_SUBJECT_PREFIX
        self.enabled   = bool(self.sender and self.password and self.recipient)
        if not self.enabled:
            logger.warning("[Notifier] Email disabled — set GMAIL_SENDER, "
                           "GMAIL_APP_PASSWORD, GMAIL_RECIPIENT in .env")

    def send(self, subject: str, body_html: str):
        if not self.enabled:
            return
        full_subject = f"{self.prefix} {subject}"
        msg = MIMEMultipart("alternative")
        msg["Subject"] = full_subject
        msg["From"]    = f"Trading Bot <{self.sender}>"
        msg["To"]      = self.recipient
        plain = re.sub(r"<[^>]+>", "", body_html.replace("<br>", "\n").replace("</tr>", "\n"))
        msg.attach(MIMEText(plain, "plain"))
        msg.attach(MIMEText(body_html, "html"))
        ctx = ssl.create_default_context()
        try:
            with smtplib.SMTP_SSL(self.SMTP_HOST, self.SMTP_PORT, context=ctx) as server:
                server.login(self.sender, self.password)
                server.sendmail(self.sender, self.recipient, msg.as_string())
            logger.info(f"[Notifier] Email sent: {full_subject}")
        except smtplib.SMTPAuthenticationError:
            logger.error("[Notifier] Gmail auth failed — use an App Password, not your regular password.")
        except Exception as e:
            logger.error(f"[Notifier] Email send failed: {e}")

    @staticmethod
    def _row(label: str, value: str, color: str = "") -> str:
        val_style = f"color:{color};font-weight:600;" if color else ""
        return (f"<tr>"
                f"<td style='padding:6px 12px;color:#6b7280;font-size:13px;'>{label}</td>"
                f"<td style='padding:6px 12px;font-size:13px;{val_style}'>{value}</td>"
                f"</tr>")

    def _table(self, rows_html: str, header: str, header_color: str) -> str:
        mode_badge = ("<span style='background:#fef3c7;color:#92400e;padding:2px 8px;"
                      "border-radius:4px;font-size:11px;'>DRY RUN</span>" if DRY_RUN else
                      "<span style='background:#fee2e2;color:#991b1b;padding:2px 8px;"
                      "border-radius:4px;font-size:11px;'>LIVE</span>")
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        return (f"<div style='font-family:Arial,sans-serif;max-width:520px;margin:0 auto;'>"
                f"<div style='background:{header_color};padding:14px 20px;border-radius:8px 8px 0 0;'>"
                f"<h2 style='margin:0;color:#fff;font-size:16px;'>{header}</h2>"
                f"<span style='color:rgba(255,255,255,0.75);font-size:11px;'>{ts} {mode_badge}</span>"
                f"</div>"
                f"<table style='width:100%;border-collapse:collapse;background:#fff;"
                f"border:1px solid #e5e7eb;border-top:none;border-radius:0 0 8px 8px;'>"
                f"{rows_html}</table>"
                f"<p style='color:#9ca3af;font-size:11px;text-align:center;margin-top:8px;'>"
                f"Trading Bot</p></div>")

    def trade_opened(self, exchange: str, symbol: str, side: str,
                     entry: float, size: float, sl: float, tp: float,
                     strategy: str, market_type: str):
        c    = "#16a34a" if side.upper() == "BUY" else "#dc2626"
        rows = (self._row("Exchange",    f"{exchange.upper()} ({market_type.upper()})")
                + self._row("Strategy",  strategy)
                + self._row("Symbol",    f"<strong>{symbol}</strong>")
                + self._row("Side",      side.upper(), c)
                + self._row("Entry",     f"{entry:.6f} USDT")
                + self._row("Size",      f"{size:.6f}")
                + self._row("Stop Loss", f"{sl:.6f} USDT", "#dc2626")
                + self._row("Take Profit", f"{tp:.6f} USDT", "#16a34a"))
        self.send(f"Trade Opened -- {symbol} {side.upper()}",
                  self._table(rows, "Trade Opened", c))

    def trade_closed(self, exchange: str, symbol: str, side: str,
                     entry: float, exit_price: float, pnl: float,
                     pnl_pct: float, reason: str):
        win  = pnl >= 0
        c    = "#16a34a" if win else "#dc2626"
        sign = "+" if win else ""
        rows = (self._row("Exchange", exchange.upper())
                + self._row("Symbol",   f"<strong>{symbol}</strong>")
                + self._row("Side",     side.upper())
                + self._row("Entry",    f"{entry:.6f} USDT")
                + self._row("Exit",     f"{exit_price:.6f} USDT")
                + self._row("PnL",      f"{sign}{pnl:.4f} USDT ({sign}{pnl_pct:.2f}%)", c)
                + self._row("Reason",   reason))
        self.send(f"Trade Closed -- {symbol} | {sign}{pnl:.4f} USDT",
                  self._table(rows, "Trade Closed", c))

    def daily_summary(self, total_trades: int, wins: int, losses: int,
                      total_pnl: float, balance: float):
        win_rate = (wins / total_trades * 100) if total_trades > 0 else 0
        c        = "#16a34a" if total_pnl >= 0 else "#dc2626"
        sign     = "+" if total_pnl >= 0 else ""
        rows = (self._row("Total Trades", str(total_trades))
                + self._row("Wins",       str(wins),   "#16a34a")
                + self._row("Losses",     str(losses), "#dc2626")
                + self._row("Win Rate",   f"{win_rate:.1f}%")
                + self._row("Total PnL",  f"{sign}{total_pnl:.4f} USDT", c)
                + self._row("Balance",    f"{balance:.4f} USDT"))
        self.send(f"Daily Summary -- PnL: {sign}{total_pnl:.4f} USDT",
                  self._table(rows, "Daily Summary", "#1d4ed8"))

    def alert(self, message: str):
        self.send("Bot Alert",
                  f"<div style='font-family:Arial;max-width:520px;background:#fffbeb;"
                  f"border:1px solid #fcd34d;border-radius:8px;padding:16px;'>"
                  f"<h3 style='margin:0 0 8px;color:#92400e;'>Alert</h3>"
                  f"<p style='color:#78350f;font-size:14px;margin:0;'>{message}</p></div>")

    def error(self, message: str):
        self.send("Bot Error",
                  f"<div style='font-family:Arial;max-width:520px;background:#fef2f2;"
                  f"border:1px solid #fca5a5;border-radius:8px;padding:16px;'>"
                  f"<h3 style='margin:0 0 8px;color:#991b1b;'>Error</h3>"
                  f"<pre style='color:#7f1d1d;font-size:12px;margin:0;'>{message}</pre></div>")


# Alias so existing imports of TelegramNotifier keep working
TelegramNotifier = EmailNotifier
