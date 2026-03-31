"""
utils/notifier.py — Telegram notification stub.

Telegram notifications are optional and disabled by default.
Set TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID in .env to enable.
"""

import os
from loguru import logger


class TelegramNotifier:
    """
    Optional Telegram bot for trade alerts and daily summaries.
    If TELEGRAM_BOT_TOKEN is not set, all methods are no-ops.
    """

    def __init__(self):
        self._token   = os.getenv("TELEGRAM_BOT_TOKEN", "")
        self._chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        self._enabled = bool(self._token and self._chat_id)
        if self._enabled:
            logger.info("[Telegram] Notifications enabled.")

    def alert(self, message: str):
        if not self._enabled:
            return
        self._send(f"🤖 {message}")

    def daily_summary(self, trades: int, wins: int, losses: int,
                      pnl: float, balance: float):
        if not self._enabled:
            return
        wr = (wins / trades * 100) if trades else 0
        self._send(
            f"📊 Daily Summary\n"
            f"Trades: {trades}  W:{wins} L:{losses}  WR:{wr:.1f}%\n"
            f"Net P&L: {pnl:+.4f} USDT\n"
            f"Balance: {balance:.2f} USDT"
        )

    def _send(self, text: str):
        try:
            import requests
            url = f"https://api.telegram.org/bot{self._token}/sendMessage"
            requests.post(url, json={
                "chat_id": self._chat_id,
                "text":    text,
                "parse_mode": "HTML",
            }, timeout=10)
        except Exception as e:
            logger.debug(f"[Telegram] Send failed: {e}")
