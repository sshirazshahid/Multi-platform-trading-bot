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

        for sym, n_loss in sym_losses.items():
            total = sym_total.get(sym, n_loss)
            rate = n_loss / total if total else 0.0
            if n_loss >= SYMBOL_LOSS_BLACKLIST and rate >= SYMBOL_BLACKLIST_MIN_RATE:
                # Only (re)apply if not already active — prevents spam
                current_exp = self._state["blacklist"].get(sym, 0)
                if current_exp < now:
                    self._state["blacklist"][sym] = now + SYMBOL_BLACKLIST_HOURS * 3600
                    logger.warning(
                        f"[AutoMutator] BLACKLIST {sym} for {SYMBOL_BLACKLIST_HOURS}h "
                        f"— {n_loss}/{total} losses ({rate:.0%}) in last "
                        f"{LOOKBACK_ANALYSES} trades")
                    mutations_applied += 1

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

        for sym, n_loss in sym_losses_short.items():
            total = sym_total_short.get(sym, n_loss)
            rate = n_loss / total if total else 0.0
            if (n_loss >= SHORT_SYMBOL_LOSS_BLACKLIST
                    and rate >= SHORT_SYMBOL_BLACKLIST_MIN_RATE):
                key = f"SHORT:{sym}"
                current_exp = self._state["blacklist"].get(key, 0)
                if current_exp < now:
                    self._state["blacklist"][key] = (
                        now + SHORT_SYMBOL_BLACKLIST_HOURS * 3600
                    )
                    logger.warning(
                        f"[AutoMutator] SHORT-BLACKLIST {sym} for "
                        f"{SHORT_SYMBOL_BLACKLIST_HOURS}h — {n_loss}/{total} "
                        f"sell losses ({rate:.0%})"
                    )
                    mutations_applied += 1

        # ── 2) Counter-trend short losses ───────────────────────────
        short_counter_trend = sum(
            1 for a in losses
            if a.get("side") == "sell"
            and any("counter-trend" in m.lower() or "counter trend" in m.lower()
                    for m in a.get("mistakes", []))
        )
        if short_counter_trend >= SHORT_LOSS_BLOCK_COUNT:
            if self._state.get("shorts_blocked_until", 0) < now:
                self._state["shorts_blocked_until"] = now + SHORT_BLOCK_HOURS * 3600
                logger.warning(
                    f"[AutoMutator] SHORTS BLOCKED for {SHORT_BLOCK_HOURS}h "
                    f"— {short_counter_trend} counter-trend short losses")
                mutations_applied += 1

        # ── 3) Leverage-amplified losses ────────────────────────────
        leverage_losses = sum(
            1 for a in losses
            if any("leverage" in m.lower() and "amplif" in m.lower()
                   for m in a.get("mistakes", []))
        )
        if leverage_losses >= LEVERAGE_LOSS_COUNT:
            if self._state.get("leverage_cap_until", 0) < now:
                self._state["leverage_cap"] = LEVERAGE_CAP_VALUE
                self._state["leverage_cap_until"] = now + LEVERAGE_CAP_HOURS * 3600
                logger.warning(
                    f"[AutoMutator] LEVERAGE CAP {LEVERAGE_CAP_VALUE}x for "
                    f"{LEVERAGE_CAP_HOURS}h — {leverage_losses} leverage-amplified losses")
                mutations_applied += 1

        self._state["last_scan_at"] = now
        self._state["last_scan_loss_tail"] = len(losses)
        self._save()

        if mutations_applied:
            logger.info(
                f"[AutoMutator] refresh: {mutations_applied} new mutations "
                f"(losses in lookback={len(losses)}/{len(analyses)})")

    # ── Public API used by bot_engine ────────────────────────────────

    def get_effective_blacklist(self) -> set:
        """Return the set of dynamically-blacklisted symbols (still active).

        Excludes side-prefixed entries (e.g. 'SHORT:ETH/USDT:USDT') — those
        are side-specific bans surfaced via `get_short_blacklist()`.
        """
        self._expire()
        return {
            k for k in self._state["blacklist"].keys()
            if not k.startswith("SHORT:")
        }

    def get_short_blacklist(self) -> set:
        """Return the set of symbols banned for SHORTS only (still active)."""
        self._expire()
        return {
            k.split(":", 1)[1]
            for k in self._state["blacklist"].keys()
            if k.startswith("SHORT:")
        }

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
