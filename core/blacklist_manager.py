"""
core/blacklist_manager.py — Per-profile symbol blacklisting.

FIX: Each profile gets its own isolated blacklist file
     (data/profiles/{name}/blacklist.json) so one profile's stop-losses
     don't block trades on other profiles.
FIX: SL counter resets when a blacklist entry expires.
FIX: A win resets the consecutive SL counter.
"""

import json
import time
from pathlib import Path
from loguru  import logger

DEFAULT_FILE = Path("data/blacklist.json")

DEFAULTS = {
    "consecutive_sl_limit": 3,
    "auto_expiry_hours":    24,
    "volatility_spike_pct": 0.15,
    "manual_list":          [],
}


class BlacklistManager:

    def __init__(self, profile_name: str = None):
        """
        profile_name: if set, uses data/profiles/{name}/blacklist.json.
                      Leave None for the single-bot (uses data/blacklist.json).
        """
        try:
            from config import BLACKLIST as cfg
            self.cfg = {**DEFAULTS, **cfg}
        except (ImportError, AttributeError):
            self.cfg = DEFAULTS.copy()

        self._file       = (Path(f"data/profiles/{profile_name}/blacklist.json")
                            if profile_name else DEFAULT_FILE)
        self._list:      dict = {}
        self._sl_counts: dict = {}

        self._load()

        for sym in self.cfg.get("manual_list", []):
            self.add(sym, "manual", permanent=True)

    # ── Public API ───────────────────────────────────────────────────

    def is_blacklisted(self, symbol: str) -> bool:
        self._purge_expired()
        return symbol in self._list

    def add(self, symbol: str, reason: str, permanent: bool = False):
        expiry   = 0.0 if permanent else time.time() + self.cfg["auto_expiry_hours"] * 3600
        duration = "permanent" if permanent else f"expires in {self.cfg['auto_expiry_hours']}h"
        self._list[symbol] = {"reason": reason, "expires": expiry, "added_at": time.time()}
        self._save()
        logger.warning(f"[Blacklist] {symbol} BLACKLISTED — {reason} ({duration})")

    def remove(self, symbol: str):
        if symbol in self._list:
            del self._list[symbol]
            self._sl_counts.pop(symbol, None)
            self._save()
            logger.info(f"[Blacklist] {symbol} removed.")

    def record_stop_loss(self, symbol: str):
        self._sl_counts[symbol] = self._sl_counts.get(symbol, 0) + 1
        count = self._sl_counts[symbol]
        limit = self.cfg["consecutive_sl_limit"]
        logger.debug(f"[Blacklist] {symbol} SL count: {count}/{limit}")
        if count >= limit:
            self.add(symbol, f"{count} consecutive stop losses")
            self._sl_counts[symbol] = 0  # reset after blacklisting

    def record_win(self, symbol: str):
        """A win resets the consecutive SL counter."""
        prev = self._sl_counts.get(symbol, 0)
        if prev > 0:
            logger.debug(f"[Blacklist] {symbol} WIN — SL counter reset (was {prev})")
        self._sl_counts[symbol] = 0

    def check_volatility(self, symbol: str, candle: list):
        if not candle: return
        try:
            o, h, l, c = candle[1], candle[2], candle[3], candle[4]
            spike = max(abs(c - o) / max(o, 1e-9), (h - l) / max(o, 1e-9))
            limit = self.cfg["volatility_spike_pct"]
            if spike >= limit:
                self.add(symbol,
                         f"volatility spike {spike*100:.1f}% (limit {limit*100:.0f}%)")
        except Exception as e:
            logger.debug(f"[Blacklist] volatility check: {e}")

    def get_all(self) -> dict:
        self._purge_expired()
        return dict(self._list)

    def clear_all(self):
        """Remove all non-permanent entries."""
        non_perm = [s for s, d in self._list.items() if d.get("expires", 0) > 0]
        for s in non_perm:
            del self._list[s]
        self._sl_counts.clear()
        self._save()
        if non_perm:
            logger.info(f"[Blacklist] Cleared {len(non_perm)} entries.")

    # ── Internal ─────────────────────────────────────────────────────

    def _purge_expired(self):
        now     = time.time()
        expired = [s for s, e in self._list.items()
                   if e["expires"] > 0 and now > e["expires"]]
        for s in expired:
            logger.info(f"[Blacklist] {s} expired — removed.")
            del self._list[s]
            self._sl_counts.pop(s, None)  # reset SL count on expiry
        if expired:
            self._save()

    def _save(self):
        try:
            self._file.parent.mkdir(parents=True, exist_ok=True)
            self._file.write_text(json.dumps(self._list, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[Blacklist] Save: {e}")

    def _load(self):
        try:
            if self._file.exists():
                self._list = json.loads(self._file.read_text(encoding="utf-8"))
                self._purge_expired()
                if self._list:
                    logger.info(f"[Blacklist] Loaded {len(self._list)} entries: "
                                f"{list(self._list.keys())}")
        except Exception as e:
            logger.debug(f"[Blacklist] Load: {e}")
            self._list = {}
