"""Terminal ANSI helpers for the dashboard package."""
from __future__ import annotations

import os
import re as _re
import sys
import time

from dashboard.state import (
    BOLD,
    CYAN,
    DIM,
    GREEN,
    ORANGE,
    RED,
    RESET,
    SILVER,
    WHITE,
    YELLOW,
    _ASSET_ICON,
    _COMMODITY_BASES,
    _START_TIME,
    _STOCK_BASES,
)

def enable_ansi():
    try:
        import ctypes
        ctypes.windll.kernel32.SetConsoleMode(
            ctypes.windll.kernel32.GetStdHandle(-11), 7)
    except Exception:
        pass
    # Force UTF-8 output to support ═ ✓ ✗ ★ ● characters on Windows
    if os.name == "nt":
        os.system("chcp 65001 >nul 2>&1")
    try:
        import io
        sys.stdout = io.TextIOWrapper(
            sys.stdout.buffer, encoding="utf-8", errors="replace", line_buffering=True)
    except Exception:
        pass


def clr():
    os.system("cls" if os.name == "nt" else "clear")


def col(text, code):
    return code + str(text) + RESET


# ── ANSI-aware width helpers ──────────────────────────────────────────
# `col()` wraps text in ANSI escape codes which Python's str.format counts
# toward field width (e.g. "{:<11}".format(col("Period", DIM)) sees a
# 14-char string for a 6-char visible word and skips padding entirely).
# Use vlen / vljust / vrjust to align colored cells visibly.
import re as _re
_ANSI_RE = _re.compile(r"\x1b\[[0-9;]*m")
def vlen(s):
    """Visible length of a string after stripping ANSI escape sequences."""
    return len(_ANSI_RE.sub("", s)) if s else 0
def vljust(s, width):
    """Left-justify `s` to `width` visible columns, ignoring ANSI codes."""
    pad = max(0, width - vlen(s))
    return (s if s is not None else "") + " " * pad
def vrjust(s, width):
    """Right-justify `s` to `width` visible columns, ignoring ANSI codes."""
    pad = max(0, width - vlen(s))
    return " " * pad + (s if s is not None else "")


def pnl_str(val, suffix=" USDT"):
    sign = "+" if val >= 0 else ""
    c    = GREEN if val >= 0 else RED
    return col("{}{:.4f}{}".format(sign, val, suffix), c)


def pnl_str_short(val):
    sign = "+" if val >= 0 else ""
    c    = GREEN if val >= 0 else RED
    return col("{}{:.4f}".format(sign, val), c)


def wr_col(wr):
    return GREEN if wr >= 55 else (YELLOW if wr >= 45 else RED)


def fg_str(value):
    v = int(value)
    if v >= 75:  return col("{} Extreme Greed".format(v), GREEN)
    if v >= 55:  return col("{} Greed".format(v),         GREEN)
    if v >= 45:  return col("{} Neutral".format(v),       YELLOW)
    if v >= 25:  return col("{} Fear".format(v),          ORANGE)
    return col("{} Extreme Fear".format(v), RED)


def _asset_tag(symbol: str) -> str:
    """Return a colored tag for commodities/stocks, or empty string."""
    base = symbol.split("/")[0].upper()
    if base in _ASSET_ICON:
        return " " + _ASSET_ICON[base]
    if base in _STOCK_BASES:
        return " " + col("$", BLUE)
    return ""


def _uptime_str() -> str:
    elapsed = time.time() - _START_TIME
    h, rem = divmod(int(elapsed), 3600)
    m, s = divmod(rem, 60)
    if h > 0:
        return "{}h {}m".format(h, m)
    return "{}m {}s".format(m, s)
