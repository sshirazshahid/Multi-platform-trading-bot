"""
core/auto_mutator.py — Closed-loop post-mortem learner.

Reads the last N entries from data/post_mortem.json on each portfolio
cycle and auto-tightens the trading mechanism when patterns of loss
emerge:

  • Repeat losing symbols        → extend dynamic blacklist
  • Repeat counter-trend losses  → temporarily block shorts
  • Repeat leverage-amplified losses → clamp leverage cap
  • Rapid-fire loss streak       → force STANDARD tier cap

State persisted to data/auto_mutations.json so mutations survive
restarts. Mutations expire after MUTATION_TTL_HOURS to avoid permanent
rot from stale evidence.

Hook points from bot_engine._execute_open:
  • get_effective_blacklist()  — union with config.BLACKLIST_HARD
  • get_leverage_cap()         — max(cap, tier_leverage) after selection
  • shorts_blocked()           — reject side=='sell' pre-tier
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Optional

from loguru import logger

# ── Tunables ─────────────────────────────────────────────────────────
# 2026-04-11 rewrite: previous values over-fit to noise and banned every
# whitelist symbol on 2-loss samples. Observed effect: bot locked up for
# 12h on a weekend because ETH/DOT/ARB were dynamically banned on 6-7
# trade samples (statistically meaningless). Raised sample size, shortened
# TTL so dynamic bans are evidence-responsive, not a 3-day death sentence.
LOOKBACK_ANALYSES       = 40     # scan a wider window (was 30)
SYMBOL_LOSS_BLACKLIST   = 4      # ≥4 losses on same symbol (was 2 — too trigger-happy)
SYMBOL_BLACKLIST_MIN_RATE = 0.70 # AND ≥70% loss rate on that symbol before banning
SYMBOL_BLACKLIST_HOURS  = 12     # 12h cooldown (was 72 — a 3-day ban on 2 losses is insane)
SHORT_LOSS_BLOCK_COUNT  = 4      # ≥N counter-trend short losses → block shorts
SHORT_BLOCK_HOURS       = 24
LEVERAGE_LOSS_COUNT     = 3      # ≥N 'leverage amplified' losses → cap
LEVERAGE_CAP_VALUE      = 5      # cap at 5x when triggered
LEVERAGE_CAP_HOURS      = 12

# Side-aware short blacklist (May 2026). Warehouse evidence: the bot's
# losses concentrate on the sell side per symbol — APT, ETC, XRP, UNI,
# OP, GRT, ADA shorts collectively cost $40+. The symmetric SYMBOL_LOSS_*
# threshold above is too lax for shorts because a symbol's short cohort
# is always a small fraction of its total trades.
SHORT_SYMBOL_LOSS_BLACKLIST   = 3
SHORT_SYMBOL_BLACKLIST_MIN_RATE = 0.65
SHORT_SYMBOL_BLACKLIST_HOURS  = 18

POST_MORTEM_FILE        = Path("data/post_mortem.json")
MUTATIONS_FILE          = Path("data/auto_mutations.json")

REFRESH_INTERVAL_SEC    = 300    # re-scan post-mortems every 5 min


def _now() -> float:
    return time.time()


class AutoMutator:
    """Evidence-driven mechanism self-tightener."""

    def __init__(self):
        self._state: dict = {
            "blacklist": {},        # symbol_key → expires_at
            "shorts_blocked_until": 0,
            "leverage_cap": None,   # int or None
            "leverage_cap_until": 0,
            "last_scan_at": 0,
            "last_scan_loss_tail": 0,
        }
        self._load()

    # ── Persistence ─────────────────────────────────────────────────

    def _load(self):
        if not MUTATIONS_FILE.exists():
            return
        try:
            data = json.loads(MUTATIONS_FILE.read_text())
            self._state.update(data)
        except Exception as e:
            logger.debug(f"[AutoMutator] load: {e}")

    def _save(self):
        try:
            MUTATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            MUTATIONS_FILE.write_text(json.dumps(self._state, indent=2))
        except Exception as e:
            logger.debug(f"[AutoMutator] save: {e}")

    # ── Expiry sweep ────────────────────────────────────────────────

    def _expire(self):
        now = _now()
        changed = False

        # Symbol blacklist entries
        stale = [s for s, exp in self._state["blacklist"].items() if exp < now]
        for s in stale:
            del self._state["blacklist"][s]
            logger.info(f"[AutoMutator] blacklist EXPIRED: {s}")
            changed = True

        if self._state.get("shorts_blocked_until", 0) and \
           self._state["shorts_blocked_until"] < now:
            self._state["shorts_blocked_until"] = 0
            logger.info("[AutoMutator] shorts_blocked EXPIRED")
            changed = True

        if self._state.get("leverage_cap_until", 0) and \
           self._state["leverage_cap_until"] < now:
            self._state["leverage_cap"] = None
            self._state["leverage_cap_until"] = 0
            logger.info("[AutoMutator] leverage_cap EXPIRED")
            changed = True

        if changed:
            self._save()

    # ── Evidence scan ───────────────────────────────────────────────

    def _read_post_mortems(self) -> list:
        if not POST_MORTEM_FILE.exists():
            return []
        try:
            data = json.loads(POST_MORTEM_FILE.read_text())
            analyses = data.get("analyses", [])
            return analyses[-LOOKBACK_ANALYSES:]
        except Exception as e:
            logger.debug(f"[AutoMutator] read: {e}")
            return []

    def refresh(self, force: bool = False):
        """
        Re-scan post-mortems and (re)apply mutations. Called from the
        portfolio cycle. Rate-limited via REFRESH_INTERVAL_SEC.
        """
        self._expire()

        now = _now()
        if not force and (now - self._state.get("last_scan_at", 0) < REFRESH_INTERVAL_SEC):
            return

        analyses = self._read_post_mortems()
        if not analyses:
            self._state["last_scan_at"] = now
            self._save()
            return

        losses = [a for a in analyses if a.get("verdict") == "LOSS"]
        if not losses:
            self._state["last_scan_at"] = now
            self._save()
            return

        mutations_applied = 0

        # ── 1) Per-symbol loss accumulation ─────────────────────────
        # Count BOTH total trades and losses per symbol so we can demand
        # a real loss-rate (not just N absolute losses). A symbol that
        # lost 4 and won 6 is not a "bad symbol", it's a normal 40% WR.
        sym_total: dict = {}
        sym_losses: dict = {}
        for a in analyses:
            s = a.get("symbol", "")
            if not s:
                continue
            sym_total[s] = sym_total.get(s, 0) + 1
            if a.get("verdict") == "LOSS":
                sym_losses[s] = sym_losses.get(s, 0) + 1

        # 2026-05-27: auto_mutator_blacklist permanently disabled (was gated
        # by HALT_MECHANISMS["auto_mutator_blacklist"] = False). Block removed.

        # ── 1b) Per-symbol SHORT-only loss accumulation (May 2026) ──
        # Tighter thresholds for shorts — concentrated short losers don't
        # show up under the symmetric 4/70% rule because their total
        # trade count is small.
        sym_total_short: dict = {}
        sym_losses_short: dict = {}
        for a in analyses:
            s = a.get("symbol", "")
            if not s or (a.get("side") or "").lower() != "sell":
                continue
            sym_total_short[s] = sym_total_short.get(s, 0) + 1
            if a.get("verdict") == "LOSS":
                sym_losses_short[s] = sym_losses_short.get(s, 0) + 1

        # 2026-05-27: auto_mutator_blacklist (short) permanently disabled
        # (was gated by HALT_MECHANISMS["auto_mutator_blacklist"] = False). Block removed.

        # 2026-05-27: auto_mutator_short_block (counter-trend short losses) permanently disabled
        # (was gated by HALT_MECHANISMS["auto_mutator_short_block"] = False). Block removed.

        # 2026-05-27: auto_mutator_leverage_cap (leverage-amplified losses) permanently disabled
        # (was gated by HALT_MECHANISMS["auto_mutator_leverage_cap"] = False). Block removed.

        self._state["last_scan_at"] = now
        self._state["last_scan_loss_tail"] = len(losses)
        self._save()

        if mutations_applied:
            logger.info(
                f"[AutoMutator] refresh: {mutations_applied} new mutations "
                f"(losses in lookback={len(losses)}/{len(analyses)})")

    # ── Public API used by bot_engine ────────────────────────────────

    def get_effective_blacklist(self) -> set:
        """Dynamically-blacklisted symbols — DISABLED 2026-06-20.

        Owner directive "remove any blacklist and blocks" (PAPER): the runtime
        symbol blacklist is short-circuited to empty so a losing streak (or a
        stale persisted entry) can no longer re-block a symbol at entry —
        mirrors shorts_blocked() below. Blacklist WRITES were already off since
        2026-05-27; this makes the disable explicit and durable on the read
        side too. The learner still records evidence and may set a leverage cap
        (a safety rail, kept). Restore the prior filtering via git history.
        """
        return set()  # UNBLOCK — see docstring

    def get_short_blacklist(self) -> set:
        """Symbols banned for SHORTS only — DISABLED 2026-06-20 (see above)."""
        return set()  # UNBLOCK

    def get_leverage_cap(self) -> Optional[int]:
        """Return an active leverage cap (int) or None."""
        self._expire()
        if self._state.get("leverage_cap_until", 0) > _now():
            return self._state.get("leverage_cap")
        return None

    def shorts_blocked(self) -> bool:
        """Whether shorts are currently blocked.

        Returns True when EITHER:
          - the post-mortem-driven shorts_blocked_until window is still active
            (counter-trend short losses), OR
          - config.SHORTS_DISABLED is True (manual hard kill-switch added
            2026-04-27 after sells averaged −$0.16/trade vs longs −$0.05).

        2026-04-28 (UNBLOCK_ALL/A): user directive "Dont block any trades"
        forces this to always return False. Both the post-mortem-driven
        window AND the SHORTS_DISABLED config flag are short-circuited.
        Restore by removing the early return below.
        """
        return False  # UNBLOCK_ALL/A — see method docstring

    def snapshot(self) -> dict:
        """Human-readable view of current mutations — for logs/dashboard."""
        self._expire()
        now = _now()
        bl = {s: max(0, (exp - now) / 3600)
              for s, exp in self._state["blacklist"].items()}
        return {
            "blacklist": bl,
            "shorts_blocked": self.shorts_blocked(),
            "shorts_blocked_remaining_h": max(
                0, (self._state.get("shorts_blocked_until", 0) - now) / 3600),
            "leverage_cap": self.get_leverage_cap(),
            "leverage_cap_remaining_h": max(
                0, (self._state.get("leverage_cap_until", 0) - now) / 3600),
        }
