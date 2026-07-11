"""
core/bot_engine.py — Smart Multi-Timeframe Bot Engine with Learning + News
Exchanges: Binance | Bybit | Bitget

FIX: _extract_usdt now handles Bybit Unified Account correctly.
     Bybit returns totalEquity in bal["total"]["USDT"], not bal["free"]["USDT"].
     Also: _log_balances now fetches Bybit balance ONCE (not spot+futures twice).
"""

import atexit
import json
import signal
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import schedule
from loguru import logger
from rich import box
from rich.console import Console
from rich.table import Table

from config import (
    DRY_RUN,
    PORTFOLIO_MIN_VALUE_USD,
    PORTFOLIO_RESCAN_MINUTES,
    RISK,
    TRADING_MODE,
    TRADING_PAIRS,
)

try:
    from config import CLAUDE_PORTFOLIO
except ImportError:
    CLAUDE_PORTFOLIO = {"enabled": True, "scan_interval_min": 15, "max_actions_per_cycle": 4}
from core.learning_engine import LearningEngine
from core.news_scanner import NewsScanner
from core.order_manager import OrderManager
from core.position_tracker import PositionTracker
from core.risk_manager import RiskManager
from exchanges import (
    BinanceClient,
    BitgetClient,
    BybitClient,
)

try:
    from core.pair_discovery import discover_all
except ImportError:
    discover_all = None
try:
    from core.mcp_brain import MCPBrain
except ImportError:
    MCPBrain = None
try:
    from core.spot_manager import SpotPortfolioManager
except ImportError:
    SpotPortfolioManager = None
try:
    from core.capital_allocator import CapitalAllocator
except ImportError:
    CapitalAllocator = None
from utils import TelegramNotifier

console = Console()


def _tier_blocked_by_cap(tier_name: str, tier_cap, escalation_enabled: bool) -> bool:
    """True if `tier_name` exceeds the allowed leverage ceiling and must be skipped.

    Leverage is capped to STANDARD (3x) when EITHER the consec-loss throttle set tier_cap to
    "STANDARD", OR confidence-driven escalation is disabled (config.CONFIDENCE_LEVERAGE_ESCALATION
    False). The latter (owner directive 2026-06-06) stops the bot sizing UP on its anti-predictive
    high-score cohort (score>=85, r=-0.285). STANDARD and SCALP are always allowed (both 3x).
    """
    cap_to_standard = (tier_cap == "STANDARD") or (not escalation_enabled)
    return bool(cap_to_standard and tier_name not in ("STANDARD", "SCALP"))


def _canonical_exit_reason(raw: str, source: str = "claude") -> str:
    """Collapse a free-text LLM close rationale into a clean machine label so the
    warehouse `exit_reason` stays a usable GROUP BY key (audit 2026-06-21 H8).

    The discretionary Claude CLOSE path passed its natural-language `reason`
    straight through, minting 39 one-off labels (e.g. "Lock +2%; OB imb -0.71
    heavy sell pressure") as the learning substrate's exit-type key. A genuine
    machine label is a short, space-free snake_case token; anything containing a
    space (or absurdly long) is prose and maps to a source-specific canonical
    close label.
    The full rationale is preserved in the [Claude] log line and, when threaded,
    via `exit_decision_id` -> mcp_decisions.jsonl. Idempotent on clean labels.
    """
    source_key = str(source or "claude").strip().lower()
    fallback = {
        "machine": "machine_close",
        "tsmom": "tsmom_close",
    }.get(source_key, "claude_close")
    r = (raw or "").strip()
    if not r:
        return fallback
    if " " not in r and len(r) <= 40:
        return r
    return fallback


def _effective_tp_threshold(take_profit, entry_price, side, leverage,
                            base_threshold, near_target_enabled,
                            near_target_frac=0.8):
    """Effective mcp_take_profit threshold, in the SAME units as net_pnl_pct
    (LEVERAGED margin-%) — audit 2026-06-21 B5.

    Flag OFF (near_target_enabled False): returns base_threshold unchanged
    (1.5 STAR / 0.5 non-STAR) so the gate is byte-identical to today.

    Flag ON: require a NEAR-PLANNED-TP exit instead of capping every winner at
    the bare floor. net_pnl_pct is LEVERAGED price-% (pnl_pct = price% * lev,
    see the builder at bot_engine.py ~4396), while the planned-TP distance
    (take_profit vs entry_price) is UN-leveraged price-%. Scale the price-%
    target by leverage so both sides share units:
        effective = near_target_frac * tp_dist_pct * lev

    Falls back to base_threshold on any invalid TP/entry/side so a missing or
    wrong-side take_profit can never make the gate fire trivially.
    """
    if not near_target_enabled:
        return base_threshold
    if not take_profit or entry_price <= 0:
        return base_threshold
    lev = max(1, int(leverage or 1))
    if str(side).lower() in ("buy", "long"):
        tp_dist_pct = (take_profit - entry_price) / entry_price * 100.0
    else:
        tp_dist_pct = (entry_price - take_profit) / entry_price * 100.0
    if tp_dist_pct <= 0:                       # missing / wrong-side TP
        return base_threshold
    return near_target_frac * tp_dist_pct * lev


MAX_PER_EXCHANGE    = CLAUDE_PORTFOLIO.get("max_per_exchange", 6)
MAX_TOTAL_POSITIONS = 8       # Max 8 total across all exchanges
NEWS_INTERVAL       = CLAUDE_PORTFOLIO.get("news_interval_min", 30) * 60
LEARN_INTERVAL      = CLAUDE_PORTFOLIO.get("learn_interval_min", 60) * 60
PORTFOLIO_CYCLE_SEC = CLAUDE_PORTFOLIO.get("scan_interval_min", 15) * 60
MAX_ACTIONS_PER_CYCLE = CLAUDE_PORTFOLIO.get("max_actions_per_cycle", 4)

# Exchanges that use a single Unified Account (fetch balance once only).
# Shared with the deployable-balance helper so the unified set and the
# PAPER-aware aggregation never drift apart.
from core.balance_utils import UNIFIED_EXCHANGES as _UNIFIED_EXCHANGES
from core.balance_utils import deployable_total as _deployable_total


class BotEngine:

    def __init__(self):
        mode_label = "DRY RUN" if DRY_RUN else "LIVE TRADING"
        logger.info("=" * 60)
        logger.info("  TRADING BOT — Smart Scanner + Learning + News")
        logger.info(f"  Trade Mode : {TRADING_MODE.upper()}")
        logger.info(f"  Run Mode   : {mode_label}")
        logger.info("=" * 60)

        self.notifier  = TelegramNotifier()
        self.tracker   = PositionTracker()
        self.risk      = RiskManager()
        self.order_mgr = OrderManager(self.tracker, self.risk, self.notifier)
        # 2026-04-26: route every PositionTracker.close() — including ghost
        # syncs detected by sync_with_exchanges — through OrderManager's
        # post-close pipeline (warehouse, risk, Spec §12, blacklist,
        # trailing cleanup, post-mortem, notifier). Before this wiring,
        # ghost-closed losses bypassed all safety rails.
        self.tracker.on_close = self.order_mgr._finalize_close
        # 2026-05-19 Patch #0 — Ghost-class reroute instrumentation needs
        # order_manager.verify_exchange_sl_alive/tp_alive accessible from
        # sync_with_exchanges. Wired here (after order_mgr exists) rather
        # than via __init__ to avoid changing the tracker constructor.
        self.tracker.order_manager = self.order_mgr
        self.learner   = LearningEngine()
        self.news      = NewsScanner()
        self.mcp_brain = MCPBrain() if MCPBrain else None
        self.order_mgr.mcp_brain = self.mcp_brain  # Wire MCP exit intelligence

        # Spot portfolio manager + capital allocator (autonomous)
        self.spot_manager = None
        self.capital_allocator = None

        # Closed-loop post-mortem learner (evidence-driven auto-tightening)
        try:
            from core.auto_mutator import AutoMutator
            self.auto_mutator = AutoMutator()
            logger.info("[Engine] AutoMutator init — EVIDENCE-TRACKING ONLY "
                        "(mutation writes + blacklist/short reads disabled 2026-05/06; "
                        "applies no live mutations)")
        except Exception as e:
            logger.debug(f"[Engine] AutoMutator init failed: {e}")
            self.auto_mutator = None

        # Health watchdog — observes heartbeat, halts, SL failures, loss streaks.
        # Constructed after notifier+risk so checks have what they need.
        try:
            from core.health_watchdog import HealthWatchdog
            self.watchdog = HealthWatchdog(
                bot_engine=self, notifier=self.notifier, risk_manager=self.risk,
            )
            logger.info("[Engine] HealthWatchdog enabled")
        except Exception as e:
            logger.debug(f"[Engine] HealthWatchdog init failed: {e}")
            self.watchdog = None
        try:
            from config import SELF_HEALING
            if SELF_HEALING.get("enabled", True):
                from core.self_healing_supervisor import SelfHealingSupervisor
                # async_heavy: run retrain/replay off the shared scheduler
                # thread so a ~60-min subprocess can't stall the portfolio
                # cycle / position monitor / watchdog (audit 2026-07-07). A
                # user-set SELF_HEALING["async_heavy"] still wins.
                self.self_healer = SelfHealingSupervisor(
                    {"async_heavy": True, **SELF_HEALING})
                logger.info("[Engine] SelfHealingSupervisor enabled")
            else:
                self.self_healer = None
        except Exception as e:
            logger.debug(f"[Engine] SelfHealingSupervisor init failed: {e}")
            self.self_healer = None
        # Universe filter: runtime quality checks before trading
        try:
            from core.pair_discovery import UniverseFilter
            self.universe_filter = UniverseFilter()
        except ImportError:
            self.universe_filter = None

        # Phase 23 (2026-05-04): per-symbol cooldown after dust-skip OR
        # calibrator hard-refuse. User flagged "PEAK gate but only 1
        # trade open" — investigation showed the same 3 symbols (BTC,
        # ETH, LINK) being re-proposed every 5min for 30+ minutes
        # because (a) BTC/ETH dust-skipped on exchange step lots and
        # (b) LINK had a calibrator-zero pattern where Phase 18's soft
        # mult clamp couldn't kill the trade. Cooldown stops the noise.
        # In-memory only — restart wipes it (intentional).
        self._dust_skip_cooldown: dict[str, float] = {}

        self.exchanges = {
            "binance": BinanceClient(),
            "bybit":   BybitClient(),
            "bitget":  BitgetClient(),
        }
        self.active_exchanges = {
            name: ex for name, ex in self.exchanges.items()
            if getattr(ex, "_connected", False)
        }
        # Hand the exchange registry to OrderManager so _finalize_close can
        # fetch live funding history without an exchange parameter.
        try:
            self.order_mgr.set_exchanges(self.exchanges)
        except Exception as _se:
            logger.debug(f"[Engine] order_mgr.set_exchanges skipped: {_se}")

        self._start_time = time.time()
        self._cycle      = 0
        self._consecutive_api_fails: dict[str, int] = {}  # Per-exchange fail counter
        self._exchange_halted: set[str] = set()         # Exchanges halted due to outage
        self._api_latency: dict[str, float] = {}        # Last health-check latency (ms)
        self._last_health_check = 0  # Time-based health check (not cycle-based)
        self._last_heartbeat    = 0  # Time-based heartbeat writer (60s interval)
        # Per-exchange, per-market-type USDT balances (populated by _log_balances)
        self._balances: dict[str, dict[str, float]] = {}
        # Transfer cooldown: prevent repeated transfer attempts (5 min per route)
        self._last_transfer: dict[tuple, float] = {}  # (ex, from, to) → timestamp
        # Exchange position cache — for universal position monitor (60s TTL)
        self._exchange_positions_cache: list[dict] = []
        self._exchange_positions_time: float = 0

        if not self.active_exchanges:
            logger.error("[Engine] No exchanges connected!")
        else:
            logger.info(f"[Engine] Connected: {list(self.active_exchanges.keys())}")
            self._log_balances()
            # Sync tracked positions with exchange — close ghosts + import manual on startup
            if not DRY_RUN:
                imported = self.tracker.sync_with_exchanges(self.active_exchanges)
                if imported:
                    self._protect_imported_positions(imported)

            # Wire exchange clients to MCP Brain for direct OHLCV fetching
            if self.mcp_brain:
                self.mcp_brain.set_exchanges(self.active_exchanges)

            # Initialize spot manager + capital allocator after exchanges are connected
            if SpotPortfolioManager:
                try:
                    self.spot_manager = SpotPortfolioManager(
                        self.active_exchanges, self.risk,
                        mcp_brain=self.mcp_brain,
                    )
                except Exception as e:
                    logger.debug(f"[Engine] SpotManager init: {e}")
            if CapitalAllocator:
                try:
                    self.capital_allocator = CapitalAllocator(
                        self.active_exchanges, self.spot_manager,
                        self.risk, self.order_mgr)
                except Exception as e:
                    logger.debug(f"[Engine] CapitalAllocator init: {e}")

        self.current_pairs = self._resolve_pairs()

        # Dynamic pair discovery
        if discover_all:
            try:
                discovered = discover_all(self.active_exchanges)
                for ename, pairs in discovered.items():
                    existing = self.current_pairs.get(ename, {"spot": [], "futures": []})
                    for mtype in ("spot", "futures"):
                        for sym in pairs.get(mtype, []):
                            if sym not in existing.get(mtype, []):
                                existing.setdefault(mtype, []).append(sym)
                    self.current_pairs[ename] = existing
                total = sum(len(p.get("spot",[]))+len(p.get("futures",[]))
                            for p in self.current_pairs.values())
                logger.info(f"[Engine] Total pairs after discovery: {total}")
            except Exception as e:
                logger.debug(f"[Engine] Pair discovery: {e}")

        # Build DCA/rebalance strategy pool (lightweight — no scanner strategies)
        self._build_strategy_pool()

        try:
            self.news.scan()
        except Exception:
            pass

        # 2026-05-01: surface wired-but-misconfigured gate state at startup
        # so silent-mode flags don't go undetected. Logs WARNING-level
        # findings; bot continues either way.
        try:
            self._gate_health_check()
        except Exception as e:
            logger.debug(f"[GateHealth] check skipped ({e})")

        # 2026-05-02: Phase A multi-agent shadow runner. Runs in parallel
        # to live, writes to warehouse.shadow_decisions, places NO orders.
        # Auto-disables on kill criteria (see config.SHADOW_MODE).
        self._shadow_runner = None
        self._shadow_thread = None
        try:
            from config import SHADOW_MODE
            if SHADOW_MODE.get("enabled"):
                self._init_shadow_runner()
        except Exception as e:
            logger.debug(f"[Engine] Shadow init skipped: {e}")

    def _init_shadow_runner(self) -> None:
        """Lazy-init the multi-agent ShadowRunner. Failures don't crash the bot."""
        try:
            from core.shadow_runner import ShadowRunner
            from core.warehouse import Warehouse
            wh = getattr(self, "warehouse", None) or Warehouse()
            self.warehouse = wh
            try:
                from config import ALLOWED_HOURS_UTC, BLACKLIST_HARD
                bl = set(BLACKLIST_HARD or set())
                ah = set(ALLOWED_HOURS_UTC) if ALLOWED_HOURS_UTC else None
            except Exception:
                bl, ah = set(), None
            extra_probes = (self._build_listing_probe(wh) + self._build_unlock_probe(wh)
                            + self._build_tsmom_probe(wh) + self._build_breakout_probe(wh))
            self._shadow_runner = ShadowRunner(
                warehouse=wh,
                ctx_provider=self._shadow_ctx_for_symbol,
                free_balance_provider=self._shadow_free_balance,
                symbols_provider=self._shadow_symbols,
                blacklist=bl, allowed_hours=ah,
                enabled_flag=self._shadow_enabled_flag,
                extra_probes=extra_probes,
            )
            logger.info("[Engine] ShadowRunner initialized (multi-agent, log-only)")
        except Exception as e:
            logger.error(f"[Engine] ShadowRunner init failed: {e}")
            self._shadow_runner = None

    def _build_listing_probe(self, wh) -> list:
        """Construct the log-only ListingShortProbeAgent with READ-ONLY market
        providers. Fail-open: any error yields no probe (the shadow lane runs
        without it). The probe never receives an order path."""
        try:
            from config import LISTING_SHORT_PROBE
            if not LISTING_SHORT_PROBE.get("enabled"):
                return []
            from core.agents.listing_short_probe_agent import ListingShortProbeAgent
            probe = ListingShortProbeAgent(
                warehouse=wh,
                markets_provider=self._listing_markets,
                market_data_provider=self._listing_market_data,
                ohlcv_provider=self._listing_ohlcv,
                account_balance_provider=self._shadow_free_balance,
                venue=str(LISTING_SHORT_PROBE.get("venue", "binance")),
            )
            self._listing_probe = probe
            logger.info("[Engine] ListingShortProbeAgent registered (log-only shadow probe)")
            return [probe]
        except Exception as e:
            logger.warning(f"[Engine] listing-short probe init skipped: {e}")
            return []

    def _build_unlock_probe(self, wh) -> list:
        """Construct the log-only UnlockShortProbeAgent (pipeline 08b
        CONFIRMED-GO, 2026-07-11) with READ-ONLY calendar/market providers.
        Fail-open: any error yields no probe (the shadow lane runs without
        it). The probe never receives an order path."""
        try:
            from config import UNLOCK_SHORT_PROBE
            if not UNLOCK_SHORT_PROBE.get("enabled"):
                return []
            from core.agents.unlock_short_probe_agent import UnlockShortProbeAgent
            probe = UnlockShortProbeAgent(
                warehouse=wh,
                perp_resolver=self._unlock_perp_resolver,
                market_data_provider=self._unlock_market_data,
                ohlcv_provider=self._unlock_ohlcv,
                account_balance_provider=self._shadow_free_balance,
                calendar_dir=str(UNLOCK_SHORT_PROBE.get("calendar_dir",
                                                        "data/unlock_calendar")),
            )
            self._unlock_probe = probe
            logger.info("[Engine] UnlockShortProbeAgent registered (log-only shadow probe)")
            return [probe]
        except Exception as e:
            logger.warning(f"[Engine] unlock-short probe init skipped: {e}")
            return []

    def _build_tsmom_probe(self, wh) -> list:
        """Construct the log-only TsmomProbeAgent — the OWNER-DIRECTED
        TSMOM-20d regime-watch forward paper test (2026-07-11). NOT a pipeline
        GO: TSMOM is a refuted family and the Codex backtest did not meet the
        reopen bar; see the agent's module docstring. Fail-open: any error
        yields no probe. The probe never receives an order path. The market
        data / OHLCV providers are the generic venue-parameterized READ-ONLY
        helpers already used by the unlock probe."""
        try:
            from config import TSMOM_PROBE
            if not TSMOM_PROBE.get("enabled"):
                return []
            from core.agents.tsmom_probe_agent import TsmomProbeAgent
            probe = TsmomProbeAgent(
                warehouse=wh,
                ohlcv_provider=self._unlock_ohlcv,
                market_data_provider=self._unlock_market_data,
                account_balance_provider=self._shadow_free_balance,
                venue=str(TSMOM_PROBE.get("venue", "bybit")),
            )
            self._tsmom_probe = probe
            logger.info("[Engine] TsmomProbeAgent registered (log-only shadow probe; "
                        "owner-directed regime-watch, NOT a pipeline GO)")
            return [probe]
        except Exception as e:
            logger.warning(f"[Engine] tsmom probe init skipped: {e}")
            return []

    def _build_breakout_probe(self, wh) -> list:
        """Construct the log-only BreakoutProbeAgent — the OWNER-DIRECTED
        forward paper test of the Codex deep-run winner breakout_60d
        (2026-07-11). NOT a pipeline GO: textbook breakout is a refuted
        family and the deep run fails our frozen capital-preservation gates;
        see the agent's module docstring. Fail-open; never receives an order
        path; reuses the generic venue-parameterized READ-ONLY providers."""
        try:
            from config import BREAKOUT_PROBE
            if not BREAKOUT_PROBE.get("enabled"):
                return []
            from core.agents.breakout_probe_agent import BreakoutProbeAgent
            probe = BreakoutProbeAgent(
                warehouse=wh,
                ohlcv_provider=self._unlock_ohlcv,
                market_data_provider=self._unlock_market_data,
                account_balance_provider=self._shadow_free_balance,
                venue=str(BREAKOUT_PROBE.get("venue", "bybit")),
            )
            self._breakout_probe = probe
            logger.info("[Engine] BreakoutProbeAgent registered (log-only shadow probe; "
                        "owner-directed Codex deep-run winner, NOT a pipeline GO)")
            return [probe]
        except Exception as e:
            logger.warning(f"[Engine] breakout probe init skipped: {e}")
            return []

    def _run_deep_breakout_lane(self):
        """Scheduled tick for the ACTIVE PAPER deep-breakout lane (owner
        directive 2026-07-11, cohort strategy_family='deep_breakout').
        Lazy-init; never raises into the scheduler. The lane's own
        assert_paper_only() re-refuses every tick off-PAPER — any
        PaperOnlyViolation lands here and is logged loudly, and no order
        path is ever reached."""
        try:
            lane = getattr(self, "_deep_breakout_lane", None)
            if lane is None:
                from config import DEEP_BREAKOUT_LANE as _dbl_cfg
                from core.deep_breakout_lane import DeepBreakoutLane
                from core.warehouse import get_warehouse
                lane = DeepBreakoutLane(
                    exchanges=self.active_exchanges,
                    order_manager=self.order_mgr,
                    tracker=self.tracker,
                    risk=self.risk,
                    equity_provider=lambda: _deployable_total(self._balances),
                    ohlcv_provider=self._unlock_ohlcv,
                    warehouse=get_warehouse(),
                    entry_pause_check=self._deep_breakout_entry_paused,
                    cfg=_dbl_cfg,
                )
                self._deep_breakout_lane = lane
                logger.info(
                    "[Engine] DeepBreakoutLane initialized (ACTIVE PAPER, "
                    "binance+bybit, cohort strategy_family='deep_breakout')")
            lane.tick()
        except Exception as e:
            # includes PaperOnlyViolation — the lane never trades off-PAPER
            logger.error(f"[Engine] deep-breakout lane tick refused/failed: {e}")

    def _deep_breakout_entry_paused(self) -> tuple:
        """(paused, reason) — the same market-wide BTC vol circuit breaker
        _execute_open applies at C.4, for the deep-breakout lane's entries.
        A2 convention: a BROKEN evaluation blocks entries (fail-closed)."""
        try:
            from config import BTC_VOL_PAUSE as _bvp_cfg
        except ImportError:
            return (False, "")
        if not _bvp_cfg.get("enabled", False):
            return (False, "")
        try:
            _bvp = getattr(self, "_btc_vol_pause", None)
            if _bvp is None:
                from core.btc_vol_pause import BtcVolPause
                _bvp = BtcVolPause()
                self._btc_vol_pause = _bvp
            _ei = getattr(self.mcp_brain, "_indicator_cache", None) if self.mcp_brain else None
            paused, reason, _ = _bvp.update_and_evaluate(_ei)
            return (bool(paused), str(reason or "btc_vol_pause"))
        except Exception as e:
            return (True, f"btc_vol_pause_error:{e}")

    def _unlock_venue_order(self) -> tuple:
        try:
            from config import UNLOCK_SHORT_PROBE
            return tuple(UNLOCK_SHORT_PROBE.get("venue_order") or ())
        except Exception:
            return ("binance", "bybit", "bitget")

    def _unlock_perp_resolver(self, base: str):
        """(venue, unified symbol) of the FIRST venue in the screen's frozen
        fee-preference order listing an active USDT-linear perp for ``base``.
        Read-only; None when no venue lists it (probe logs SKIP_NO_PERP)."""
        want = f"{base}/USDT:USDT"
        for venue in self._unlock_venue_order():
            try:
                ex = (self.active_exchanges or {}).get(venue)
                client = getattr(ex, "exchange", None)
                m = (getattr(client, "markets", None) or {}).get(want)
                if m and m.get("swap") and m.get("active", True):
                    return (venue, want)
            except Exception:
                continue
        return None

    def _unlock_market_data(self, venue: str, symbol: str) -> dict:
        """Best bid/ask/quoteVolume + current funding rate for a perp on
        ``venue``. Read-only; fail-open to {} so a missing symbol skips."""
        out: dict = {}
        try:
            ex = (self.active_exchanges or {}).get(venue)
            if ex is None:
                return out
            t = ex.fetch_ticker(symbol, market_type="futures") or {}
            out.update({
                "bid": t.get("bid"), "ask": t.get("ask"), "last": t.get("last"),
                "quoteVolume": (t.get("quoteVolume")
                                or (t.get("info", {}) or {}).get("quoteVolume")),
                "active": True,
            })
            client = getattr(ex, "exchange", None)
            if client is not None and hasattr(client, "fetch_funding_rate"):
                fr = client.fetch_funding_rate(symbol) or {}
                out["funding_rate"] = fr.get("fundingRate")
        except Exception as e:
            logger.debug(f"[UnlockProbe] market_data {venue} {symbol} failed: {e}")
        return out

    def _unlock_ohlcv(self, venue: str, symbol: str, timeframe: str, since_ms: int) -> list:
        """1h candles on ``venue`` since ``since_ms``. Read-only."""
        try:
            ex = (self.active_exchanges or {}).get(venue)
            if ex is None:
                return []
            client = getattr(ex, "exchange", None)
            if client is not None:
                return client.fetch_ohlcv(
                    symbol, timeframe, since=int(since_ms), limit=1000,
                    params=self._futures_ohlcv_params(ex)) or []
            return ex.fetch_ohlcv(symbol, timeframe, limit=1000, market_type="futures") or []
        except Exception as e:
            logger.debug(f"[UnlockProbe] ohlcv {venue} {symbol} failed: {e}")
            return []

    def _listing_venue_client(self):
        """The exchange client that supplies listing-probe market data (binance
        USDT-M perps). READ-ONLY use only."""
        try:
            from config import LISTING_SHORT_PROBE
            venue = str(LISTING_SHORT_PROBE.get("venue", "binance"))
        except Exception:
            venue = "binance"
        return (self.active_exchanges or {}).get(venue)

    def _listing_markets(self) -> list:
        """Current tradeable Binance USDT-M perp unified symbols. Read-only."""
        try:
            ex = self._listing_venue_client()
            client = getattr(ex, "exchange", None)
            markets = getattr(client, "markets", None) or {}
            out = []
            for sym, m in markets.items():
                if (m.get("swap") and m.get("active", True)
                        and m.get("quote") == "USDT" and m.get("settle") == "USDT"):
                    out.append(sym)
            return out
        except Exception as e:
            logger.debug(f"[ListingProbe] markets fetch failed: {e}")
            return []

    def _listing_market_data(self, symbol: str) -> dict:
        """Best bid/ask/last/quoteVolume + current funding rate for a perp.
        Read-only; fail-open to {} so a missing symbol simply skips."""
        out: dict = {}
        try:
            ex = self._listing_venue_client()
            if ex is None:
                return out
            t = ex.fetch_ticker(symbol, market_type="futures") or {}
            out.update({
                "bid": t.get("bid"), "ask": t.get("ask"), "last": t.get("last"),
                "quoteVolume": (t.get("quoteVolume")
                                or (t.get("info", {}) or {}).get("quoteVolume")),
                "active": True,
            })
            client = getattr(ex, "exchange", None)
            if client is not None and hasattr(client, "fetch_funding_rate"):
                fr = client.fetch_funding_rate(symbol) or {}
                out["funding_rate"] = fr.get("fundingRate")
        except Exception as e:
            logger.debug(f"[ListingProbe] market_data {symbol} failed: {e}")
        return out

    def _listing_ohlcv(self, symbol: str, timeframe: str, since_ms: int) -> list:
        """Forward 1h candles for a listing since ``since_ms``. Read-only."""
        try:
            ex = self._listing_venue_client()
            if ex is None:
                return []
            client = getattr(ex, "exchange", None)
            if client is not None:
                return client.fetch_ohlcv(
                    symbol, timeframe, since=int(since_ms), limit=1000,
                    params=self._futures_ohlcv_params(ex)) or []
            return ex.fetch_ohlcv(symbol, timeframe, limit=1000, market_type="futures") or []
        except Exception as e:
            logger.debug(f"[ListingProbe] ohlcv {symbol} failed: {e}")
            return []

    @staticmethod
    def _futures_ohlcv_params(ex) -> dict:
        try:
            return ex._futures_params() if hasattr(ex, "_futures_params") else {}
        except Exception:
            return {}

    def _shadow_enabled_flag(self) -> bool:
        try:
            from config import SHADOW_MODE
            return bool(SHADOW_MODE.get("enabled"))
        except Exception:
            return False

    def _shadow_free_balance(self) -> float:
        """Sum of free USDT across active exchanges (sizing basis)."""
        total = 0.0
        for _name, ex in (self.active_exchanges or {}).items():
            try:
                bal = ex.fetch_balance("futures")
                total += float((bal.get("free") or {}).get("USDT") or 0.0)
            except Exception:
                pass
        return total

    def _shadow_symbols(self) -> list:
        """Universe for shadow eval — the day's biggest |24h %| MOVERS among
        the liquidity-screened futures pairs (2026-07-06 owner ask: keep
        testing 'scalp the daily movers' in the log-only lane). One batched
        fetch_tickers call ranks the whole screened universe, cached for
        ``mover_refresh_s``; capped at ``mover_cap`` so the OHLCV ctx budget
        stays bounded. Fail-open on ANY error to the legacy first-N pairs —
        the shadow lane must never go dark on a ticker hiccup."""
        try:
            from config import SHADOW_MODE
            cap = int(SHADOW_MODE.get("mover_cap", 10))
            if not SHADOW_MODE.get("mover_universe", True):
                return self._shadow_symbols_legacy(5)  # true pre-change budget
            now = time.time()
            cached = getattr(self, "_shadow_mover_cache", None)
            if cached and now - cached[0] < float(SHADOW_MODE.get("mover_refresh_s", 900)):
                return list(cached[1])
            screened: list[str] = []
            for _ename, pairs in (self.current_pairs or {}).items():
                for sym in (pairs.get("futures") or []):
                    if sym not in screened:
                        screened.append(sym)
            if not screened or not self.active_exchanges:
                return self._shadow_symbols_legacy(cap)
            ex = next(iter(self.active_exchanges.values()))
            # Route through the wrapper's fetch_tickers(market_type='futures'),
            # which passes _futures_params() so the call hits the PERP endpoint
            # and returns unified swap-keyed tickers ('BTC/USDT:USDT'). The raw
            # ccxt fetch_tickers() routes by the client's steady-state
            # defaultType='spot' and returns spot keys that never match the
            # swap universe — the silent-fallback bug found 2026-07-06 (24h of
            # shadow proposals stuck on BTC/ETH). Fail-open to {} inside.
            raw = ex.fetch_tickers(market_type="futures") if ex else {}
            min_qv = float(SHADOW_MODE.get("mover_min_qv_usd", 5_000_000))
            moves: list[tuple[float, str]] = []
            for sym in screened:
                t = raw.get(sym)
                if not t:
                    continue
                pct, qv = t.get("percentage"), t.get("quoteVolume")
                if pct is None or qv is None or float(qv) < min_qv:
                    continue
                moves.append((abs(float(pct)), sym))
            moves.sort(reverse=True)
            out = [s for _, s in moves[:cap]]
            if not out:
                # Screened symbols but zero usable tickers = key-format or
                # venue trouble. Be LOUD (silent fallback is undiagnosable)
                # and cache the fallback so the ticker call is not re-fired
                # on every 300s shadow tick.
                logger.warning(
                    f"[Shadow] mover ranking empty ({len(screened)} screened, "
                    f"{len(raw) if raw else 0} tickers) — legacy fallback"
                )
                out = self._shadow_symbols_legacy(cap)
            self._shadow_mover_cache = (now, out)
            return list(out)
        except Exception as e:
            # WARNING, not DEBUG: a silent debug-level fallback here is exactly
            # what hid the 2026-07-06 mover bug for 24h. Make it visible.
            logger.warning(f"[Shadow] mover universe fallback (exception): {e}")
            try:
                return self._shadow_symbols_legacy(5)
            except Exception:
                return []  # never let the provider raise into the tick

    def _shadow_symbols_legacy(self, cap: int = 5) -> list:
        """Pre-2026-07-06 selection: first futures pairs per exchange."""
        out: list[str] = []
        for _ename, pairs in (self.current_pairs or {}).items():
            for sym in (pairs.get("futures") or [])[:2]:
                if sym not in out:
                    out.append(sym)
            if len(out) >= cap:
                break
        return out[:cap]

    def _shadow_ctx_for_symbol(self, symbol: str) -> dict | None:
        """Best-effort OHLCV pull for the multi-timeframe agent context.
        Cached at the exchange-client level — no extra API hit beyond what
        live already does. Returns None if data is unavailable."""
        if not self.active_exchanges:
            return None
        ex = next(iter(self.active_exchanges.values()))
        try:
            import pandas as pd
            # 2026-07-07: stamp the venue actually supplying the candles.
            # Without it every shadow Proposal defaulted to venue='binance'
            # even when priced off bybit/bitget, so the outcome resolver
            # replayed the WRONG venue's candles (mispriced barrier hits) or
            # left non-binance symbols PENDING forever.
            ctx: dict = {
                "symbol": symbol,
                "venue": str(getattr(ex, "name", "") or "binance").lower(),
            }
            for tf, key, lim in (
                ("5m", "ohlcv_5m", 80),
                ("15m", "ohlcv_15m", 60),
                ("1h", "ohlcv_1h", 100),
                ("4h", "ohlcv_4h", 60),
            ):
                try:
                    bars = ex.fetch_ohlcv(symbol, timeframe=tf, limit=lim)
                    if not bars or len(bars) < 30:
                        return None
                    df = pd.DataFrame(bars, columns=["timestamp", "open", "high", "low", "close", "volume"])
                    ctx[key] = df
                except Exception:
                    return None
            return ctx
        except Exception:
            return None

    def _shadow_loop(self, stop_event) -> None:
        """Daemon loop — invokes ShadowRunner.tick() on the configured cadence."""
        from config import SHADOW_MODE
        interval = max(60, int(SHADOW_MODE.get("tick_interval_s", 300)))
        while not stop_event.is_set():
            try:
                if self._shadow_runner is not None:
                    self._shadow_runner.tick()
            except Exception as e:
                logger.debug(f"[Shadow] tick error: {e}")
            stop_event.wait(interval)

    def _gate_health_check(self) -> None:
        """Surface wired-but-misconfigured gates at startup.

        Each finding is a WARNING — bot still starts, but operator sees
        the actual state of every defensive layer in one place. Designed
        to catch silent-mode flags (e.g., SPOT recommendation_only=True
        means SPOT-PROTECT-V1 logs but never sells), missing exchange-
        side SL on tracked positions, and gates that are wired but
        ineffective due to threshold mis-calibration.
        """
        from config import (
            CELL_FILTER,
            ENTRY_STALENESS_EXIT,
            EXPECTANCY_FILTER,
            MODEL_GATE,
            SPOT_PORTFOLIO,
            SPOT_STRATEGY,
            STAR_SYMBOLS,
        )
        findings: list[str] = []

        # 1) Active gates summary
        gate_lines = []
        gate_lines.append(
            f"cell-filter={'on' if CELL_FILTER.get('enabled') else 'OFF'} "
            f"(stars={len(STAR_SYMBOLS)}, "
            f"band=[{CELL_FILTER.get('score_band_min')},"
            f"{CELL_FILTER.get('score_band_max')}])")
        gate_lines.append(
            f"expectancy={'on' if EXPECTANCY_FILTER.get('enabled') else 'OFF'} "
            f"(floor=${EXPECTANCY_FILTER.get('min_expected_dollar')}, "
            f"star_floor=${EXPECTANCY_FILTER.get('min_expected_star')})")
        gate_lines.append(
            f"staleness={'on' if ENTRY_STALENESS_EXIT.get('enabled') else 'OFF'} "
            f"(gap={ENTRY_STALENESS_EXIT.get('invalidation_gap_pct')}%, "
            f"min_hold={ENTRY_STALENESS_EXIT.get('min_hold_minutes')}min)")
        model_active = (MODEL_GATE.get('enabled')
                        and not MODEL_GATE.get('shadow_only'))
        gate_lines.append(
            f"model-gate={'live' if model_active else ('shadow' if MODEL_GATE.get('enabled') else 'OFF')} "
            f"(p_win>={MODEL_GATE.get('threshold_futures')})")
        spot_active = (SPOT_STRATEGY.get('enabled')
                       and not SPOT_PORTFOLIO.get('recommendation_only', True))
        gate_lines.append(
            f"spot-protect={'live' if spot_active else 'recommend-only'} "
            f"(half=-{SPOT_STRATEGY.get('drawdown_half_pct')*100:.0f}%, "
            f"full=-{SPOT_STRATEGY.get('drawdown_full_pct')*100:.0f}%)")
        logger.info(f"[GateHealth] {' | '.join(gate_lines)}")

        # 2) Silent-mode flags — wired but doesn't act
        if SPOT_PORTFOLIO.get("recommendation_only", True) and SPOT_STRATEGY.get("enabled"):
            findings.append(
                "SPOT-PROTECT-V1 is enabled but SPOT_PORTFOLIO.recommendation_only=True — "
                "drawdown triggers are computed but never sell. Set "
                "recommendation_only=False to enable autonomous defense.")

        # 3) Calibration sanity — expectancy floor must be reachable
        floor = EXPECTANCY_FILTER.get("min_expected_dollar", 0.05)
        # Heuristic: at $4-5 notional and 0.1% fees, round-trip is ~$0.01.
        # A floor > $0.50 is almost certainly mis-calibrated.
        if EXPECTANCY_FILTER.get("enabled") and floor > 0.50:
            findings.append(
                f"EXPECTANCY_FILTER floor=${floor:.2f} is suspiciously high — at "
                f"current notional this is >50× round-trip fee and may block "
                f"all symbols. Sanity-check vs warehouse data.")

        # 4) Open positions without exchange-side SL — flag count, not error
        try:
            naked = [p for p in self.tracker.get_open()
                     if not getattr(p, "_exchange_sl", False)
                     and p.market_type == "futures"
                     and not p.paper_trade]
            if naked:
                findings.append(
                    f"{len(naked)} live futures position(s) have no exchange-side "
                    f"SL — relying on bot's monitor cycle for soft-SL only. "
                    f"If bot crashes, these are unprotected.")
        except Exception:
            pass

        # 4b) Phase 35 (2026-05-05) — ML model freshness check.
        # The Phase 13 LR + GBM ensemble drives MODEL_GATE and the
        # LR soft size multiplier. Models trained on stale data lose
        # predictive power in fast-moving crypto markets. freqtrade's
        # FreqAI pattern is continuous retraining; we don't auto-retrain
        # but we DO surface staleness to the operator so manual refit
        # can be triggered.
        try:
            import time as _t_p35
            from pathlib import Path as _P_p35
            ensemble = _P_p35("data/models/ensemble_futures_latest.json")
            if ensemble.exists():
                age_h = (_t_p35.time() - ensemble.stat().st_mtime) / 3600
                if age_h > 168:   # > 7 days = stale
                    findings.append(
                        f"ML ensemble model is {age_h/24:.1f} days old "
                        f"(>{168/24:.0f}d threshold) — refit via "
                        f"`python scripts/train_models.py` to keep MODEL_GATE "
                        f"and LR sizing predictions current.")
                elif age_h > 48:  # 2-7 days = warning
                    findings.append(
                        f"ML ensemble model is {age_h/24:.1f} days old "
                        f"(>2d) — consider refitting via "
                        f"`python scripts/train_models.py` for fresher predictions.")
            else:
                findings.append(
                    "ML ensemble model file not found "
                    "(data/models/ensemble_futures_latest.json) — "
                    "MODEL_GATE may be operating without learned weights.")
        except Exception as _e:
            logger.debug(f"[GateHealth] model-age check skipped: {_e}")

        # 5) Recent gate-stack soak status (last-7d closed trade volume)
        try:
            import sqlite3 as _sq
            import time as _time
            con = _sq.connect("data/warehouse.sqlite")
            since_7d = int(_time.time()) - 7 * 86400
            n_recent = con.execute(
                "SELECT COUNT(*) FROM trades WHERE status='CLOSED' AND ts_entry >= ?",
                (since_7d,)).fetchone()[0]
            con.close()
            if n_recent < 10:
                findings.append(
                    f"only {n_recent} closed trade(s) in last 7d — gate-stack "
                    f"calibration is statistically unverified.")
        except Exception:
            pass

        # 5b) 2026-05-21 — stuck-OPEN warehouse rows older than 24h.
        # When trade_id_by_key misses for any reason, _finalize_close
        # can't patch the warehouse row to CLOSED. The bot still books
        # the close into positions.json + risk + compliance correctly,
        # but learning analytics (which read trades WHERE status='CLOSED')
        # under-count by however many rows are stuck. Surface here so
        # operator can run scripts/backfill_warehouse_closes.py.
        # Excludes rows newer than 24h: those are genuinely-active
        # positions held overnight, not leaks.
        try:
            import sqlite3 as _sq
            import time as _time
            con = _sq.connect("data/warehouse.sqlite")
            cutoff = _time.time() - 24 * 3600
            n_stuck = con.execute(
                "SELECT COUNT(*) FROM trades WHERE status='OPEN' AND ts_entry < ?",
                (cutoff,)).fetchone()[0]
            con.close()
            if n_stuck > 0:
                findings.append(
                    f"{n_stuck} warehouse trade row(s) stuck at status='OPEN' "
                    f"and older than 24h — learning analytics under-counted "
                    f"by {n_stuck} closes. Run "
                    f"`python scripts/backfill_warehouse_closes.py --commit` "
                    f"to repair.")
        except Exception as _se:
            logger.debug(f"[GateHealth] stuck-open check skipped: {_se}")

        # 6) Surface findings to operator
        if findings:
            logger.warning(f"[GateHealth] {len(findings)} finding(s):")
            for i, f in enumerate(findings, 1):
                logger.warning(f"  ({i}) {f}")
        else:
            logger.info("[GateHealth] all gates healthy, no findings")

    # ── Strategy pool ─────────────────────────────────────────────────

    def _build_strategy_pool(self):
        """Build lightweight pool — only DCA + rebalance (scheduled independently).
        All entry/exit decisions now come from Claude portfolio cycle."""
        kwargs = dict(order_manager=self.order_mgr, risk_manager=self.risk)
        self.pool = {}
        for name, mod_cls, mtype in [
            ("dca_spot",  ("strategies.dca_strategy", "DCAStrategy"),         "spot"),
            ("rebalance", ("strategies.rebalancing",  "RebalancingStrategy"), "spot"),
        ]:
            try:
                mod = __import__(mod_cls[0], fromlist=[mod_cls[1]])
                self.pool[name] = getattr(mod, mod_cls[1])(**kwargs, market_type=mtype)
            except (ImportError, AttributeError):
                pass
        logger.info(f"[Engine] Strategy pool (DCA/rebal only): {list(self.pool.keys())}")

    # ── Pair resolution ───────────────────────────────────────────────

    def _resolve_pairs(self) -> dict:
        if TRADING_MODE == "portfolio":
            logger.info("[Engine] Portfolio mode — scanning wallets...")
            import portfolio_scanner as ps
            ps.MIN_VALUE_USD = PORTFOLIO_MIN_VALUE_USD
            pairs = ps.build_portfolio_pairs(self.active_exchanges)
            logger.info("[Engine] Scan complete.")
            return pairs

        if TRADING_MODE == "all":
            logger.info("[Engine] ALL mode — scanning everything available...")
            return self._resolve_all_mode_pairs()

        logger.info("[Engine] USDT-only mode — using configured pairs.")
        return TRADING_PAIRS

    def _resolve_all_mode_pairs(self) -> dict:
        """
        ALL mode: Aggressive discovery of every tradeable market.
        1. Start with configured TRADING_PAIRS as base
        2. Scan wallet holdings (like portfolio mode)
        3. Run expanded pair discovery (higher limits, all asset classes)
        4. Merge everything — maximum opportunity surface
        """
        # Start with configured base pairs
        pairs = {}
        for ex_name, type_dict in TRADING_PAIRS.items():
            pairs[ex_name] = {
                "spot":    list(type_dict.get("spot", [])),
                "futures": list(type_dict.get("futures", [])),
            }

        # Layer 1: Scan wallet holdings (portfolio scan)
        try:
            import portfolio_scanner as ps
            ps.MIN_VALUE_USD = 1.0  # Lower threshold — scan everything
            for ex_name, exchange in self.active_exchanges.items():
                if ex_name not in pairs:
                    pairs[ex_name] = {"spot": [], "futures": []}
                for pair in ps.scan_spot_holdings(exchange):
                    if pair not in pairs[ex_name]["spot"]:
                        pairs[ex_name]["spot"].append(pair)
                for pair in ps.scan_futures_holdings(exchange):
                    if pair not in pairs[ex_name]["futures"]:
                        pairs[ex_name]["futures"].append(pair)
        except Exception as e:
            logger.debug(f"[Engine] ALL mode wallet scan: {e}")

        # Layer 2: Aggressive pair discovery (expanded limits)
        try:
            from core.pair_discovery import discover_all_mode
            discovered = discover_all_mode(self.active_exchanges)
            for ex_name, disc_pairs in discovered.items():
                if ex_name not in pairs:
                    pairs[ex_name] = {"spot": [], "futures": []}
                for mtype in ("spot", "futures"):
                    for sym in disc_pairs.get(mtype, []):
                        if sym not in pairs[ex_name][mtype]:
                            pairs[ex_name][mtype].append(sym)
        except Exception as e:
            logger.debug(f"[Engine] ALL mode discovery: {e}")

        total = sum(
            len(p.get("spot", [])) + len(p.get("futures", []))
            for p in pairs.values()
        )
        logger.info(
            f"[Engine] ALL mode: {total} total pairs across "
            f"{len(pairs)} exchanges")
        for ex_name, type_dict in pairs.items():
            logger.info(
                f"[Engine]   {ex_name.upper()}: "
                f"{len(type_dict['spot'])} spot + "
                f"{len(type_dict['futures'])} futures")

        return pairs

    def _rescan_portfolio(self):
        if TRADING_MODE not in ("portfolio", "all"):
            return
        logger.info("[Engine] Re-scanning portfolio...")
        import portfolio_scanner as ps
        ps.MIN_VALUE_USD = PORTFOLIO_MIN_VALUE_USD
        new_pairs = ps.build_portfolio_pairs(self.active_exchanges)
        added = []
        for ex_name, type_dict in new_pairs.items():
            old = self.current_pairs.get(ex_name, {"spot": [], "futures": []})
            for mtype in ("spot", "futures"):
                for sym in type_dict.get(mtype, []):
                    if sym not in old.get(mtype, []):
                        added.append((ex_name, mtype, sym))
        if added:
            logger.info(f"[Engine] New holdings: {added}")
            self.current_pairs = new_pairs
        else:
            logger.info("[Engine] No new holdings.")

    # ── Balances ──────────────────────────────────────────────────────

    def _log_balances(self):
        logger.info("[Engine] Fetching balances...")
        # Two parallel totals (2026-05-02 fix):
        #   total        = sum of FREE margin (used for sizing, conservative)
        #   total_equity = sum of WALLET BALANCE (used for drawdown tracking,
        #                  stable across position open/close margin shuffles)
        # Drawdown computed against equity prevents phantom flakes from margin
        # reallocation. Sizing uses free margin to avoid "ab not enough" rejects.
        total = 0.0
        total_equity = 0.0
        balances = {}
        equity_cache = getattr(self, "_equity_balances", {})
        if DRY_RUN:
            wallet = self.order_mgr.wallet
            for ex_name in self.active_exchanges:
                bal = wallet.balance(ex_name)
                logger.info(
                    f"[Engine] {ex_name.upper()} virtual: {bal:.4f} USDT (paper)")
                total += bal
                total_equity += bal  # paper: free == equity
                balances[ex_name] = {"spot": bal, "futures": bal}
                equity_cache[ex_name] = {"spot": bal, "futures": bal}
        else:
            for name, ex in self.active_exchanges.items():
                # Retain previous balance on fetch failure (don't default to 0 for 15 min)
                prev = self._balances.get(name, {"spot": 0.0, "futures": 0.0})
                balances[name] = {"spot": prev.get("spot", 0.0), "futures": prev.get("futures", 0.0)}
                prev_eq = equity_cache.get(name, {"spot": 0.0, "futures": 0.0})
                eq_now = {"spot": prev_eq.get("spot", 0.0), "futures": prev_eq.get("futures", 0.0)}
                if name in _UNIFIED_EXCHANGES:
                    # Bybit: single unified account — fetch once only
                    try:
                        bal  = ex.fetch_balance("spot")
                        usdt = self._extract_usdt(bal, name)
                        usdt_eq = self._extract_usdt_equity(bal, name)
                        if usdt > 0:
                            logger.info(f"[Engine] {name.upper()} unified: free=${usdt:.2f} equity=${usdt_eq:.2f}")
                            total += usdt
                            balances[name] = {"spot": usdt, "futures": usdt}
                        else:
                            total += balances[name]["spot"]  # Use retained balance
                        if usdt_eq > 0:
                            total_equity += usdt_eq
                            eq_now = {"spot": usdt_eq, "futures": 0.0}
                        else:
                            total_equity += eq_now["spot"]  # retained equity
                    except Exception as e:
                        logger.debug(f"[Engine] {name} balance: {e}")
                        total += balances[name]["spot"]  # Use retained balance
                        total_equity += eq_now["spot"]
                else:
                    for mtype in ("spot", "futures"):
                        try:
                            bal  = ex.fetch_balance(mtype)
                            usdt = self._extract_usdt(bal, name)
                            usdt_eq = self._extract_usdt_equity(bal, name)
                            if usdt > 0:
                                balances[name][mtype] = usdt
                                logger.info(
                                    f"[Engine] {name.upper()} {mtype}: free=${usdt:.2f} equity=${usdt_eq:.2f}")
                            total += balances[name][mtype]
                            if usdt_eq > 0:
                                eq_now[mtype] = usdt_eq
                            total_equity += eq_now[mtype]
                        except Exception as e:
                            logger.debug(f"[Engine] {name} balance: {e}")
                            total += balances[name][mtype]  # Use retained balance
                            total_equity += eq_now[mtype]
                equity_cache[name] = eq_now
        self._balances = balances
        self._equity_balances = equity_cache
        if total > 0 or total_equity > 0:
            # Drawdown tracker reads EQUITY (stable wallet balance).
            # Sizing reads FREE via self._balances (conservative).
            equity_for_risk = total_equity if total_equity > 0 else total
            if not getattr(self, '_balance_initialized', False):
                self.risk.set_start_balance(equity_for_risk)
                self._balance_initialized = True
            else:
                self.risk.update_current_balance(equity_for_risk)
            logger.info(
                f"[Engine] Total USDT: free=${total:.2f} equity=${total_equity:.2f} "
                f"(drawdown tracked against equity)")
        else:
            logger.warning(
                "[Engine] Could not read balance — "
                "ensure 'Enable Reading' is on in your exchange API settings.")

    def _extract_usdt(self, bal: dict, exchange_name: str = "") -> float:
        """
        Extract USDT balance from a ccxt fetch_balance() response.

        Bybit Unified Account uses multiple balance fields that are NOT
        interchangeable for order sizing:
          - totalEquity            = wallet + unrealized PnL + ALL collateral
                                     (BTC/ETH posted as margin, locked margin
                                     on open positions). This is display-only
                                     and NOT spendable as fresh USDT margin.
          - totalWalletBalance     = deposited balance excluding unrealized PnL
          - totalAvailableBalance  = free USDT-equivalent margin for NEW orders
                                     ← this is what sizing must use
          - per-coin availableToWithdraw = free USDT (excludes locked margin)

        2026-04-12 FIX (Bybit 110007 "ab not enough"):
        Previously preferred totalEquity → bot thought it had $700 available
        when only $400 was actually free, sized trades on $700 and Bybit
        rejected them. Now we try totalAvailableBalance first, then the
        per-coin USDT availableToWithdraw, and only fall back to
        totalEquity as a last-resort display value.
        """
        if not bal:
            return 0.0

        ex = exchange_name.lower() if exchange_name else ""

        # ── Bybit: Use totalAvailableBalance for sizing, NOT totalEquity
        if ex == "bybit":
            try:
                lst = bal.get("info", {}).get("result", {}).get("list", [])
                if lst and isinstance(lst, list):
                    acct = lst[0] if lst else {}
                    # Priority 1: free margin available for new orders
                    for field in ("totalAvailableBalance",
                                  "totalMarginBalance",
                                  "totalWalletBalance"):
                        val = acct.get(field)
                        if val is not None:
                            try:
                                v = float(val)
                                if v > 0:
                                    return v
                            except (TypeError, ValueError):
                                pass
                    # Priority 2: per-coin USDT free balance
                    coins = acct.get("coin", [])
                    if isinstance(coins, list):
                        for c in coins:
                            if c.get("coin") == "USDT":
                                for f2 in ("availableToWithdraw",
                                           "availableBalance",
                                           "walletBalance"):
                                    val2 = c.get(f2)
                                    if val2 is not None:
                                        try:
                                            v2 = float(val2)
                                            if v2 > 0:
                                                return v2
                                        except (TypeError, ValueError):
                                            pass
                    # Priority 3 (last resort): totalEquity — display only,
                    # NOT reliable for sizing but better than returning 0.
                    te = acct.get("totalEquity")
                    if te is not None:
                        try:
                            v = float(te)
                            if v > 0:
                                logger.warning(
                                    "[Bybit] _extract_usdt falling back to "
                                    "totalEquity — available margin fields "
                                    "missing. Sizing may be over-estimated.")
                                return v
                        except (TypeError, ValueError):
                            pass
            except Exception:
                pass
            # Fallback: ccxt parsed fields — prefer free over total
            usdt = bal.get("USDT") or {}
            if isinstance(usdt, dict):
                for key in ("free", "total"):
                    val = usdt.get(key)
                    if val is not None:
                        try:
                            v = float(val)
                            if v > 0:
                                return v
                        except (TypeError, ValueError):
                            pass
            free_d = bal.get("free") or {}
            if isinstance(free_d, dict):
                val = free_d.get("USDT")
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
            total_d = bal.get("total") or {}
            if isinstance(total_d, dict):
                val = total_d.get("USDT")
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
            return 0.0

        # ── Standard ccxt (Binance, Bitget) ────────────────────────────
        usdt = bal.get("USDT")
        if isinstance(usdt, dict):
            for key in ("free", "total"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
        free = bal.get("free", {})
        if isinstance(free, dict) and free.get("USDT"):
            try:
                return float(free["USDT"])
            except (TypeError, ValueError):
                pass
        total = bal.get("total", {})
        if isinstance(total, dict) and total.get("USDT"):
            try:
                return float(total["USDT"])
            except (TypeError, ValueError):
                pass
        return 0.0

    def _extract_usdt_equity(self, bal: dict, exchange_name: str = "") -> float:
        """Return wallet equity (free + locked margin) for drawdown tracking.

        Different from `_extract_usdt` which returns FREE balance for sizing.

        Why a separate function:
          Drawdown should track stable wallet equity, not free margin which
          swings violently as positions open/close (margin gets locked/freed).
          Using free margin for drawdown tracking causes phantom flake-rejects
          and false halts every time the bot opens or closes a position.

          Pre-fix (2026-05-02): bot used `_extract_usdt` for both sizing and
          drawdown. Bybit returned $50 free + $157 locked = $207 wallet, but
          bot tracked drawdown against the $50 free number — which fluctuated
          with margin allocation, not P&L. Result: 53 phantom flake rejections
          per day from margin-locking noise misinterpreted as balance drops.

        Field priority:
          - Bybit: `totalWalletBalance` (deposited + closed PnL, excludes
                   unrealized — stable across position state changes).
                   Falls back to `totalMarginBalance`, then 'total' from
                   ccxt's parsed view.
          - Other: ccxt's `total` field (free + used) — wallet balance.
                   Falls back to `free` if total missing (degraded but safe).
        """
        if not bal:
            return 0.0
        ex = exchange_name.lower() if exchange_name else ""

        if ex == "bybit":
            try:
                lst = bal.get("info", {}).get("result", {}).get("list", [])
                if lst and isinstance(lst, list):
                    acct = lst[0] if lst else {}
                    # totalWalletBalance excludes unrealized PnL — stable across
                    # margin allocation. totalMarginBalance similar fallback.
                    for field in ("totalWalletBalance", "totalMarginBalance"):
                        val = acct.get(field)
                        if val is not None:
                            try:
                                v = float(val)
                                if v > 0:
                                    return v
                            except (TypeError, ValueError):
                                pass
                    # Per-coin walletBalance for USDT — same idea per-coin
                    coins = acct.get("coin", [])
                    if isinstance(coins, list):
                        for c in coins:
                            if c.get("coin") == "USDT":
                                val2 = c.get("walletBalance")
                                if val2 is not None:
                                    try:
                                        v2 = float(val2)
                                        if v2 > 0:
                                            return v2
                                    except (TypeError, ValueError):
                                        pass
            except Exception:
                pass

        # Standard ccxt: prefer 'total' (wallet = free + used) over 'free'
        usdt = bal.get("USDT")
        if isinstance(usdt, dict):
            for key in ("total", "free"):
                val = usdt.get(key)
                if val is not None:
                    try:
                        v = float(val)
                        if v > 0:
                            return v
                    except (TypeError, ValueError):
                        pass
        total_d = bal.get("total", {})
        if isinstance(total_d, dict) and total_d.get("USDT"):
            try:
                return float(total_d["USDT"])
            except (TypeError, ValueError):
                pass
        free_d = bal.get("free", {})
        if isinstance(free_d, dict) and free_d.get("USDT"):
            try:
                return float(free_d["USDT"])
            except (TypeError, ValueError):
                pass
        return 0.0

    # ── Claude Portfolio Cycle (SOLE entry/exit authority) ──────────────

    def _collect_all_coins(self) -> list:
        """Collect all unique base assets from TRADING_PAIRS."""
        all_coins = set()
        for pairs in self.current_pairs.values():
            for sym in pairs.get("spot", []) + pairs.get("futures", []):
                base = sym.split("/")[0].split(":")[0]
                all_coins.add(base)
        # Also add coins from open positions
        for p in self.tracker.get_open():
            base = p.symbol.split("/")[0].split(":")[0]
            all_coins.add(base)
        # Prioritize major coins
        _priority = {"BTC", "ETH", "SOL", "BNB", "XRP", "DOGE",
                     "XAU", "ADA", "AVAX", "LINK", "DOT"}
        priority = sorted(all_coins & _priority)
        others = sorted(all_coins - _priority)
        result = (priority + others)[:40]
        # 2026-06-02: always include the analysis-only research instruments
        # (commodity/equity perps) beyond the cap so they are fetched + scored +
        # warehoused for the eventual edge screen. They are hard-blocked from
        # live entry in _execute_open, so analyzing them never risks a trade.
        from config import ANALYSIS_ONLY_BASES
        for _b in sorted(ANALYSIS_ONLY_BASES):
            if _b not in result:
                result.append(_b)
        return result

    def _build_position_snapshot(self) -> list:
        """Build snapshot of all open positions for Claude."""
        result = []
        for p in self.tracker.get_open():
            current_price = 0
            for ex_name, exchange in self.active_exchanges.items():
                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                    try:
                        ticker = exchange.fetch_ticker(p.symbol, p.market_type)
                        current_price = float(ticker.get("last", 0) or 0)
                    except Exception:
                        pass
                    break
            if not current_price:
                # Use entry price as fallback so position is never invisible
                current_price = p.entry_price
            lev = max(1, getattr(p, "leverage", 1) or 1)
            if p.side == "buy":
                pnl_pct = (current_price - p.entry_price) / p.entry_price * 100 * lev
            else:
                pnl_pct = (p.entry_price - current_price) / p.entry_price * 100 * lev
            result.append({
                "id": p.id,
                "symbol": p.symbol,
                "side": p.side,
                "entry_price": p.entry_price,
                "current_price": current_price,
                "pnl_pct": round(pnl_pct, 2),
                "stop_loss": p.stop_loss,
                "take_profit": p.take_profit,
                "age_min": p.duration_minutes,
                "exchange": p.exchange,
                "market_type": p.market_type,
                "leverage": p.leverage,
                "strategy": getattr(p, "strategy", ""),
            })
        return result

    def _build_risk_envelope(self) -> dict:
        """Build risk constraints for Claude, including the active mechanism gates.

        Exposing gates to Claude means it stops proposing trades that will
        be blocked downstream — tighter loop, fewer wasted cycles.
        """
        from config import (
            ALLOWED_HOURS_UTC,
            BLACKLIST_HARD,
            BLOCKED_HOURS_UTC,
            LEVERAGE_TIERS,
            MAX_LOSS_PER_TRADE_PCT,
            PEAK_HOURS_UTC,
            SHORTS_REQUIRE_BTC_BEAR,
            WHITELIST_SYMBOLS,
        )

        total_open = self.tracker.count_open()
        max_new = max(0, RISK.get("max_open_positions", 8) - total_open)

        total_bal = _deployable_total(self._balances)

        daily_loss_pct = 0
        if total_bal > 0:
            daily_loss_pct = abs(min(0, self.risk.daily_pnl)) / total_bal

        dd_pct = RISK.get("max_drawdown_pct", 0.25)

        # Live mechanism state — fed to Claude so its proposals respect gates
        hour = self._current_utc_hour()
        hour_class = self._classify_hour(hour)
        btc_trend = self._get_btc_trend()

        # Effective blacklist = static ∪ dynamic (AutoMutator)
        dyn_bl = (self.auto_mutator.get_effective_blacklist()
                  if self.auto_mutator else set())
        effective_bl = sorted(set(BLACKLIST_HARD) | dyn_bl)

        # Throttle state
        throttle = self._consec_loss_state()
        throttle_paused = throttle["pause_until"] > time.time()
        tier_cap = throttle.get("tier_cap") or (
            "STANDARD" if (self.auto_mutator
                           and self.auto_mutator.get_leverage_cap() is not None)
            else None)

        mutator_snap = (self.auto_mutator.snapshot()
                        if self.auto_mutator else {})

        return {
            "max_new_positions": max_new,
            "total_open": total_open,
            "daily_loss_pct": round(daily_loss_pct, 4),
            "drawdown_headroom_pct": round((dd_pct - daily_loss_pct) * 100, 1),
            "total_balance": round(total_bal, 2),
            # Mechanism state — helps Claude propose better trades
            "hour_utc": hour,
            "hour_class": hour_class,           # peak|allowed|warmup|blocked
            "btc_4h_trend": btc_trend,          # bull|bear|neutral
            "blacklist": effective_bl,
            "whitelist": sorted(WHITELIST_SYMBOLS),
            "allowed_hours_utc": sorted(ALLOWED_HOURS_UTC),
            "peak_hours_utc": sorted(PEAK_HOURS_UTC),
            "blocked_hours_utc": sorted(BLOCKED_HOURS_UTC),
            "shorts_require_btc_bear": SHORTS_REQUIRE_BTC_BEAR,
            "shorts_allowed_now": btc_trend == "bear",
            "max_loss_per_trade_pct": MAX_LOSS_PER_TRADE_PCT,
            "leverage_tiers": {
                name: {
                    "leverage": t["leverage"],
                    "min_confidence": t["min_confidence"],
                    "sl_pct": t["sl_pct"],
                    "tp_pct": t["tp_pct"],
                    "requires_whitelist": t["requires_whitelist"],
                    "requires_peak_hour": t["requires_peak_hour"],
                    "requires_btc_aligned": t["requires_btc_aligned"],
                }
                for name, t in LEVERAGE_TIERS.items()
            },
            "consec_loss_throttled": throttle_paused,
            "effective_tier_cap": tier_cap,     # None | "STANDARD"
            "auto_mutations": mutator_snap,
        }

    def _get_recent_trades(self, n: int = 20) -> list:
        """Get last N closed trades with P&L for accuracy feedback."""
        closed = getattr(self.tracker, '_closed', [])[-n:]
        result = []
        for t in closed:
            result.append({
                "symbol": t.symbol,
                "side": t.side,
                "pnl": round(getattr(t, "pnl", 0) or 0, 4),
                "strategy": getattr(t, "strategy", ""),
                "exchange": t.exchange,
            })
        return result

    # ==============================================================
    # HIGH-WR MECHANISM GATES (2026-04-11 rewrite)
    # Hour gate + blacklist + BTC macro trend + leverage tier selector
    # + $-loss-per-trade clamp + consecutive-loss throttle
    # ==============================================================

    def _current_utc_hour(self) -> int:
        return datetime.now(timezone.utc).hour

    # Hour-gate evidence file (refreshed by scripts/refresh_hour_gates.py).
    # Cached for 5 min in-process so the file is read at most ~12×/hour.
    _HOUR_GATE_PATH = Path("data/hour_gate_evidence.json")
    _HOUR_GATE_TTL_SEC = 300
    _HOUR_GATE_MAX_AGE_DAYS = 14

    def _load_hour_gate_evidence(self) -> dict:
        """Read hour_gate_evidence.json → {"blocked": set, "profitable": set}.

        Empty sets when:
        - file is missing
        - file is older than _HOUR_GATE_MAX_AGE_DAYS (stale evidence is ignored)
        - file is malformed
        Cached for _HOUR_GATE_TTL_SEC so disk hits are bounded.
        """
        now = time.time()
        if (now - getattr(self, "_hour_gate_loaded_at", 0)) < self._HOUR_GATE_TTL_SEC:
            return getattr(self, "_hour_gate_cached",
                           {"blocked": set(), "profitable": set()})

        ev: dict = {"blocked": set(), "profitable": set()}
        try:
            p = self._HOUR_GATE_PATH
            if p.exists():
                age_days = (now - p.stat().st_mtime) / 86400
                if age_days <= self._HOUR_GATE_MAX_AGE_DAYS:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    for k in ("blocked", "profitable"):
                        raw = data.get(k) or []
                        ev[k] = {int(h) for h in raw if isinstance(h, (int, float))}
        except Exception as e:
            logger.debug(f"[HourGate] evidence load skipped: {e}")
            ev = {"blocked": set(), "profitable": set()}

        self._hour_gate_cached = ev
        self._hour_gate_loaded_at = now
        return ev

    def _load_dynamic_blocked_hours(self) -> set:
        """Back-compat shim: the `blocked` set from the evidence file."""
        return self._load_hour_gate_evidence()["blocked"]

    def _classify_hour(self, hour: int) -> str:
        """Return 'peak' | 'allowed' | 'warmup' | 'blocked'.

        Combines the static config sets with `data/hour_gate_evidence.json`
        which is refreshed weekly by `scripts/refresh_hour_gates.py`.

        2026-06-11 (owner: "Trade only in those hours where its profitable"):
        profit-only mode — when HOUR_GATE_PROFIT_ONLY is on and the evidence
        file is fresh with a non-empty `profitable` list, every hour NOT on
        that list classifies as 'blocked'. Fail-open on missing/stale/empty
        evidence (an empty list is indistinguishable from insufficient data;
        a silently-halted bot is worse than an ungated one). Supersedes the
        2026-05-27 decision that disabled the dynamic hour gate.
        """
        from config import (
            BLOCKED_HOURS_UTC,
            HOUR_GATE_PROFIT_ONLY,
            PEAK_HOURS_UTC,
            WARMUP_HOURS_UTC,
        )
        if hour in BLOCKED_HOURS_UTC:
            return "blocked"
        if HOUR_GATE_PROFIT_ONLY:
            _prof = self._load_hour_gate_evidence().get("profitable")
            if _prof and hour not in _prof:
                return "blocked"
        if hour in PEAK_HOURS_UTC:
            return "peak"
        if hour in WARMUP_HOURS_UTC:
            return "warmup"
        # Default: allowed (any hour not explicitly blocked/warmup/peak)
        return "allowed"

    def _get_btc_trend(self) -> str:
        """
        Return 'bull' | 'bear' | 'neutral' from BTC 4h EMA200 slope.
        Cached 15 minutes to avoid repeated API calls.

        Rules:
          - close > EMA200 AND EMA200 5-bar slope > +0.2% → bull
          - close < EMA200 AND EMA200 5-bar slope < −0.2% → bear
          - otherwise → neutral
        """
        cache_ttl = 900  # 15 minutes
        now = time.time()
        if getattr(self, '_btc_trend_cached_at', 0) + cache_ttl > now:
            return getattr(self, '_btc_trend_cached', "neutral")

        try:
            import pandas as pd

            from config import BTC_TREND_EMA_PERIOD, BTC_TREND_TIMEFRAME

            exchange = (self.active_exchanges.get('binance')
                        or next(iter(self.active_exchanges.values()), None))
            if not exchange:
                return "neutral"

            raw = exchange.fetch_ohlcv(
                "BTC/USDT", BTC_TREND_TIMEFRAME,
                BTC_TREND_EMA_PERIOD + 20, "spot")
            if not raw or len(raw) < BTC_TREND_EMA_PERIOD + 5:
                return "neutral"

            df = pd.DataFrame(raw, columns=["ts", "o", "h", "l", "c", "v"])
            ema = df["c"].ewm(span=BTC_TREND_EMA_PERIOD, adjust=False).mean()
            last_ema = float(ema.iloc[-1])
            past_ema = float(ema.iloc[-6])
            close = float(df["c"].iloc[-1])

            slope_pct = (last_ema - past_ema) / past_ema if past_ema > 0 else 0.0
            above_ema = close > last_ema

            if above_ema and slope_pct > 0.002:
                trend = "bull"
            elif not above_ema and slope_pct < -0.002:
                trend = "bear"
            else:
                trend = "neutral"

            self._btc_trend_cached = trend
            self._btc_trend_cached_at = now
            logger.info(
                f"[BTC-Trend] {trend} "
                f"(slope={slope_pct*100:+.2f}%, close_above_ema={above_ema})")
            # Persist for the dashboard's BTC macro panel (dashboard.py reads
            # data/btc_trend.json; nothing wrote it before -> blank panel). Local
            # file, atomic, no trade-logic impact. Write the trend string too so the
            # dashboard's BULL/BEAR matches the bot's actual +/-0.2% logic, not just
            # the raw slope sign.
            try:
                _btc_p = Path("data/btc_trend.json")
                _btc_p.parent.mkdir(parents=True, exist_ok=True)
                _btc_tmp = _btc_p.with_name(_btc_p.name + ".tmp")
                _btc_tmp.write_text(json.dumps({
                    "trend": trend,
                    "ema200_slope": slope_pct,
                    "close_above_ema": above_ema,
                    "ts": now,
                }), encoding="utf-8")
                _btc_tmp.replace(_btc_p)
            except Exception as _btc_e:
                logger.debug(f"[BTC-Trend] persist skipped: {_btc_e}")
            return trend
        except Exception as e:
            logger.debug(f"[BTC-Trend] error: {e}")
            return "neutral"

    def _consec_loss_state(self) -> dict:
        """
        Inspect recent_results for consecutive-loss throttle state.
        Returns {'pause_until': ts, 'downgrade_until': ts, 'tier_cap': str|None}.
        """
        from config import (
            CONSEC_LOSS_DOWNGRADE_COUNT,
            CONSEC_LOSS_DOWNGRADE_HOURS,
            CONSEC_LOSS_PAUSE_COUNT,
            CONSEC_LOSS_PAUSE_HOURS,
        )

        state = getattr(self, '_consec_loss_state_cached',
                        {"pause_until": 0, "downgrade_until": 0, "tier_cap": None})
        now = time.time()

        # Expire any prior throttle windows
        if state.get("pause_until", 0) < now:
            state["pause_until"] = 0
        if state.get("downgrade_until", 0) < now:
            state["downgrade_until"] = 0
            state["tier_cap"] = None

        # Count consecutive losses at the tail of recent_results
        results = getattr(self.risk, '_recent_results', [])
        tail_losses = 0
        for r in reversed(results):
            if not r:
                tail_losses += 1
            else:
                break

        # Escalate throttles — fire ONCE per streak, tracked by _last_pause_streak.
        # Previous bug: pause expired → tail_losses still high → re-triggered
        # immediately → infinite deadlock (no trades → no wins → streak never clears).
        last_streak = getattr(self, '_last_pause_streak', 0)
        if tail_losses >= CONSEC_LOSS_PAUSE_COUNT and tail_losses != last_streak and state["pause_until"] <= 0:
            state["pause_until"] = now + CONSEC_LOSS_PAUSE_HOURS * 3600
            self._last_pause_streak = tail_losses
            logger.warning(
                f"[Throttle] PAUSE: {tail_losses} consecutive losses "
                f"→ paused for {CONSEC_LOSS_PAUSE_HOURS}h (one-shot, won't re-trigger)")
        elif tail_losses >= CONSEC_LOSS_DOWNGRADE_COUNT and state["downgrade_until"] <= 0:
            state["downgrade_until"] = now + CONSEC_LOSS_DOWNGRADE_HOURS * 3600
            state["tier_cap"] = "STANDARD"
            logger.warning(
                f"[Throttle] DOWNGRADE: {tail_losses} consecutive losses "
                f"→ tier cap = STANDARD for {CONSEC_LOSS_DOWNGRADE_HOURS}h")

        self._consec_loss_state_cached = state
        return state

    def _select_leverage_tier(self, symbol: str, side: str,
                              confidence: float, btc_trend: str,
                              atr_pct: float = 0.0,
                              mcp_score: float = 0.0) -> tuple:
        """
        Select the highest leverage tier this candidate qualifies for.
        Returns (tier_name, tier_params) or (None, None) if rejected.
        Gates checked in order:
          hour class → throttle pause → tier requirements → high-ATR clamp →
          high-score over-confidence cap.

        2026-05-01: added `mcp_score` argument to support the high-score
        anti-EV cap. claude_portfolio realized data shows score 85+ trades
        flip to NEGATIVE expectancy:
          score 65-74:  n=18  +$2.41   avg+$0.134  WR=44%
          score 75-84:  n=20  +$4.86   avg+$0.243  WR=40%
          score 85-100: n=16  -$2.98   avg-$0.186  WR=31%   ← anti-EV
        High-score setups appear to over-fit late-trend / parabolic
        continuations that mean-revert. Cap them at STANDARD tier so we
        still take the trade (don't waste the signal entirely) but at
        smaller size than the score implies.
        """
        from config import HIGH_ATR_PCT_THRESHOLD, LEVERAGE_TIERS, STAR_SYMBOLS, WHITELIST_SYMBOLS

        hour = self._current_utc_hour()
        hour_class = self._classify_hour(hour)
        is_star = symbol in STAR_SYMBOLS

        if hour_class == "blocked":
            logger.info(
                f"[Tier] {symbol} REJECTED: hour {hour:02d} UTC blocked by hour "
                f"gate (static BLOCKED_HOURS_UTC or profit-only evidence)")
            return None, None

        # Throttle pause short-circuits everything
        throttle = self._consec_loss_state()
        if throttle["pause_until"] > time.time():
            rem_min = (throttle["pause_until"] - time.time()) / 60
            logger.warning(
                f"[Tier] {symbol} REJECTED: consec-loss pause "
                f"({rem_min:.0f}m remaining)")
            return None, None
        tier_cap = throttle.get("tier_cap")

        in_whitelist = symbol in WHITELIST_SYMBOLS

        # For longs, BTC bull/neutral is aligned.
        # For shorts, BTC bear is aligned (handled upstream but reflected here).
        if side == "buy":
            btc_aligned = btc_trend in ("bull", "neutral")
        else:
            btc_aligned = btc_trend == "bear"

        is_peak_hour    = hour_class == "peak"
        # Include "warmup" — half-size handler at line 860 depends on this not rejecting
        # the trade upstream. Before: warmup hours were gated out here, making the
        # half-size path unreachable and silently discarding WARMUP_HOURS_UTC opportunities.
        is_allowed_hour = hour_class in ("peak", "allowed", "warmup")
        high_atr        = atr_pct >= HIGH_ATR_PCT_THRESHOLD
        # 2026-05-12 (phase51): anti-EV cap removed; score 85+ mapped to AGGRESSIVE (10x).
        # 2026-06-06 (audit + owner directive): REVERSED — the MCP score is anti-predictive
        # (r=-0.285; score>=85 = worst cohort), so confidence no longer escalates leverage above
        # STANDARD unless config.CONFIDENCE_LEVERAGE_ESCALATION is True. tier_cap (consec-loss
        # throttle) still caps independently.
        from config import CONFIDENCE_LEVERAGE_ESCALATION

        # Try tiers highest → lowest (10x → 5x → 4x → 3x → 3x SCALP)
        # 2026-05-22: SCALP fallback for chop hours when blended conf is 0.40-0.55.
        for tier_name in ("AGGRESSIVE", "CONVICTION", "STRONG", "STANDARD", "SCALP"):
            if _tier_blocked_by_cap(tier_name, tier_cap, CONFIDENCE_LEVERAGE_ESCALATION):
                continue

            # .get() not [] — config.py pops 'SCALP' from LEVERAGE_TIERS when
            # SCALP_TIER_ENABLED=false, but this loop iterates a hard-coded tuple
            # that still includes 'SCALP'. A bare subscript KeyErrors on the popped
            # tier; skip a disabled/absent tier gracefully instead.
            t = LEVERAGE_TIERS.get(tier_name)
            if t is None:
                continue
            if confidence < t["min_confidence"]:
                continue
            if t["requires_whitelist"] and not in_whitelist:
                continue
            if t["requires_allowed_hour"] and not is_allowed_hour:
                continue
            if t["requires_peak_hour"] and not is_peak_hour:
                continue
            if t["requires_btc_aligned"] and not btc_aligned:
                continue
            if high_atr and tier_name != "STANDARD":
                # High vol forces STANDARD regardless of qualification
                continue

            params = dict(t)
            # Warmup hours — half size
            # 2026-04-28 (L99 ALL-IN): warmup half-size disabled. The half
            # was the cause of the DOGE rejection ($16.27 < $30 min); under
            # L99 the user wants every signal sized at full tier. Restore
            # by un-commenting the multiplier.
            if hour_class == "warmup":
                pass  # params["size_pct"] *= 0.5  # L99 ALL-IN: keep full size
            # STAR_SYMBOLS — proven net-positive in claude_portfolio (Phase 12.4).
            # 2026-04-28 (L99 ALL-IN): cap raised 0.20 → 1.0 because the L99
            # base size is 0.50; the 0.20 cap would have SHRUNK STAR symbols
            # below the non-STAR baseline. Cap at 1.0 = no cap in practice;
            # the underlying balance and exchange limits do the clamping.
            if is_star:
                params["size_pct"] = min(1.0, params["size_pct"] * 1.3)

            # AutoMutator leverage cap (post-mortem evidence)
            if self.auto_mutator:
                cap = self.auto_mutator.get_leverage_cap()
                if cap is not None and params["leverage"] > cap:
                    logger.warning(
                        f"[Tier] {symbol} leverage capped {params['leverage']}x → {cap}x "
                        f"(AutoMutator: recent leverage-amplified losses)")
                    params["leverage"] = cap

            logger.info(
                f"[Tier] {symbol} → {tier_name} "
                f"(conf={confidence:.0%}, hour={hour:02d}/{hour_class}, "
                f"wl={in_whitelist}{' STAR' if is_star else ''}, "
                f"btc={btc_trend}, atr%={atr_pct*100:.2f}%, "
                f"lev={params['leverage']}x, size={params['size_pct']*100:.1f}%, "
                f"sl={params['sl_pct']*100:.1f}%)")
            return tier_name, params

        logger.info(
            f"[Tier] {symbol} REJECTED: no tier qualifies "
            f"(conf={confidence:.0%}, hour={hour:02d}/{hour_class}, "
            f"wl={in_whitelist}, btc_trend={btc_trend})")
        return None, None

    def _within_loss_clamp(self, balance: float, notional: float,
                            leverage: int, sl_pct: float) -> bool:
        """
        Dual hard clamp: reject trades where expected loss exceeds either
          (a) MAX_LOSS_PER_TRADE_PCT × balance, or
          (b) MAX_LOSS_PER_TRADE_USD (absolute dollar ceiling).
        The dollar ceiling catches strategies that bypass mcp_brain's $4 sizer
        (legacy Supertrend etc. that ran outside the scoring engine and
        produced -$11/-$8 fat-tail losses in the Apr 2026 review).

        2026-04-28 (UNBLOCK_ALL/A): user directive "Dont block any trades"
        forces this to always return True. The post-trade outlier safety
        (risk_manager.record_trade_result → MAX_LOSS_PER_TRADE_USD review
        flag) still operates — that path is a SAFETY response, not an
        entry-side block, and was kept intentional. Restore by removing
        the early return below.
        """
        # return True  # UNBLOCK_ALL/A — RE-ENABLED 2026-05-30 (audit): per-trade loss
        #                clamp restored. To disable again, uncomment this single line.
        pass  # fall through to the loss-clamp logic below

        # ── Original logic preserved below (unreachable until early-return removed):
        from config import MAX_LOSS_PER_TRADE_PCT, MAX_LOSS_PER_TRADE_USD
        if balance <= 0:
            return False
        expected_loss = notional * leverage * sl_pct
        pct_limit = balance * MAX_LOSS_PER_TRADE_PCT
        usd_limit = MAX_LOSS_PER_TRADE_USD
        limit = min(pct_limit, usd_limit)
        ok = expected_loss <= limit
        if not ok:
            binding = "USD" if usd_limit < pct_limit else "PCT"
            logger.warning(
                f"[Loss-Clamp] REJECTED ({binding}): expected loss ${expected_loss:.2f} "
                f"> limit ${limit:.2f} "
                f"(notional=${notional:.2f} × lev={leverage}x × sl={sl_pct*100:.2f}%)")
        return ok

    def _min_notional_floor(self, symbol: str, step: float, price: float,
                            leverage: int, market_type: str,
                            notional: float, base_notional: float,
                            mtype_bal: float, sl_pct: float) -> float:
        """MIN-NOTIONAL FLOOR (2026-07-10, owner UNBLOCK directive).

        When the EV-opinion size-multiplier stack (Phase 17 rolling-50,
        Phase 18 calibrator, Phase 27 per-symbol EV, ...) crushes size
        below the exchange lot minimum, return the floored margin (the
        exchange minimum for this symbol) instead of dust-skipping —
        but ONLY when ALL hold:
          (a) the pre-multiplier base notional could afford the floor
              (undo opinion-downsizing only; never override balance/
              margin reality),
          (b) the hard loss clamp passes at the floored size,
          (c) the §2 portfolio-exposure cap passes at the floored size
              (fail-CLOSED on error, same as the main rail).
        Returns 0.0 when the floor must NOT apply (caller keeps the
        original skip + 30min cooldown). Default OFF via
        config.MIN_NOTIONAL_FLOOR — flag off is byte-identical.
        """
        try:
            from config import MIN_NOTIONAL_FLOOR as _MNF
        except ImportError:
            return 0.0
        if not _MNF.get("enabled", False):
            return 0.0
        lev = leverage if market_type == "futures" else 1
        floor_notional = step * price / max(lev, 1)
        # (a) opinion-downsizing only — base sizing must afford the floor
        if base_notional < floor_notional:
            return 0.0
        # (b) hard loss clamp at the floored size
        if not self._within_loss_clamp(
                mtype_bal, floor_notional, max(lev, 1), sl_pct / 100.0):
            return 0.0
        # (c) §2 exposure cap at the floored gross notional — fail-CLOSED
        try:
            from config import MAX_PORTFOLIO_EXPOSURE_PCT as _MAX_EXP
            from core.risk_manager import exposure_breached as _exp_breached
            _equity = _deployable_total(self._balances)
            if _exp_breached(self.tracker.get_open(), step * price,
                             _equity, _MAX_EXP):
                return 0.0
        except Exception as _fe:
            logger.warning(
                f"[Claude] min-notional floor exposure check FAILED, "
                f"keeping skip: {_fe}")
            return 0.0
        logger.info(
            f"[Claude] {symbol} min-notional floor: "
            f"${notional:.2f}->${floor_notional:.2f} "
            f"(opinion-downsized below exchange min; UNBLOCK directive)")
        return floor_notional

    def _recent_side_wr(self, side: str, limit: int = 30) -> tuple:
        """Rolling win-rate of the last `limit` CLOSED trades on a given side.

        Pulls directly from the warehouse so it reflects REAL PnL (not
        positions.json fallback prices used by ghost_sync). Excludes:
          - sl_placement_failed: infrastructure failure (ccxt routing bug
            fixed 2026-04-22); pre-fix noise should not penalize the gate.
          - systematic_close: MCP-monitor close path disabled 2026-04-24
            (1W/17L historical). Including these disabled-mode losses in
            a rolling side-WR gate would keep the gate latched on data
            that cannot recur under current config.

        Returns (wr, n). wr is None when insufficient sample.
        """
        try:
            from core.warehouse import get_warehouse
            rows = get_warehouse().query(
                "SELECT realized_pnl FROM trades WHERE status='CLOSED' "
                "AND side=? AND exit_reason NOT IN ('sl_placement_failed', 'systematic_close') "
                "ORDER BY ts_exit DESC LIMIT ?",
                (side, int(limit)),
            )
        except Exception:
            return (None, 0)
        n = len(rows or [])
        if n == 0:
            return (None, 0)
        wins = sum(1 for r in rows if (r.get("realized_pnl") or 0) > 0)
        return (wins / n, n)

    def _claude_portfolio_cycle(self):
        """Single unified cycle: gather data -> Claude decides -> execute.
        Replaces the old _scan_and_trade + _run_mcp_brain pipeline."""
        if not self.mcp_brain or not self.mcp_brain.is_enabled:
            logger.warning("[Claude] MCP Brain not available — skipping portfolio cycle")
            return

        # 2026-06-13: roll daily counters at cycle start, decoupled from the
        # entry path. can_trade() (the other rollover caller) is only reached
        # via _execute_open, so when entries are gated upstream for >1 day the
        # daily_pnl/trades_today counters froze at stale values and dashboards
        # read a stale "today". This keeps them fresh regardless of entries.
        if self.risk:
            try:
                self.risk.roll_day_if_needed()
            except Exception as e:
                logger.debug(f"[Risk] day-rollover check: {e}")

        # Refresh closed-loop mutations from latest post-mortems
        if self.auto_mutator:
            try:
                self.auto_mutator.refresh()
            except Exception as e:
                logger.debug(f"[Claude] AutoMutator refresh: {e}")

        self._log_balances()

        all_coins = self._collect_all_coins()
        open_positions = self._build_position_snapshot()
        risk_envelope = self._build_risk_envelope()
        recent_trades = self._get_recent_trades(20)

        # Gather news context for MCP Brain
        news_context = {}
        try:
            news_context = self.news.get_market_context()
            news_signals = self.news.get_news_signals()
            if news_signals:
                logger.info(
                    f"[Claude] News signals: {len(news_signals)} actionable "
                    f"({sum(1 for s in news_signals if s.get('signal') == 'bullish')} bull, "
                    f"{sum(1 for s in news_signals if s.get('signal') == 'bearish')} bear)")
                news_context["news_signals"] = news_signals
        except Exception as e:
            logger.debug(f"[Claude] News context error: {e}")

        # Signal source switch: each source returns the same action-dict
        # contract, so execution/risk/warehouse plumbing below stays shared.
        from config import SIGNAL_SOURCE
        _sig_tag = {
            "tsmom": "TSMOM",
            "machine": "Machine",
            "s3": "S3",
            "mcp_det": "MCPDet",
            "none": "NoSignal",
        }.get(SIGNAL_SOURCE, "Claude")
        logger.info(
            f"[{_sig_tag}] Portfolio cycle: {len(all_coins)} coins, "
            f"{len(open_positions)} open positions, "
            f"balance=${risk_envelope.get('total_balance', 0):.0f}")

        if SIGNAL_SOURCE == "tsmom":
            _signal = self._tsmom_signal()
        elif SIGNAL_SOURCE == "s3":
            _signal = self._s3_signal()
        elif SIGNAL_SOURCE == "machine":
            _signal = self._machine_signal()
        elif SIGNAL_SOURCE == "mcp_det":
            _signal = self._mcp_det_signal()
        elif SIGNAL_SOURCE == "none":
            _signal = self._none_signal()
        else:
            _signal = self.mcp_brain
        actions = _signal.analyze_portfolio(
            coins=all_coins,
            open_positions=open_positions,
            exchange_balances=dict(self._balances),
            risk_envelope=risk_envelope,
            recent_trades=recent_trades,
            news_context=news_context,
        )

        # Deterministic-source observability: periodically log WHY it is quiet.
        if SIGNAL_SOURCE in ("tsmom", "machine", "s3"):
            import time as _t_w
            _watch_attr = f"_{SIGNAL_SOURCE}_watch_last"
            if _t_w.time() - getattr(self, _watch_attr, 0.0) >= 1800:
                try:
                    logger.info(f"[{_sig_tag}] watch: {_signal.watch_summary()}")
                except Exception:
                    pass
                setattr(self, _watch_attr, _t_w.time())

        if not actions:
            logger.info(f"[{_sig_tag}] No actions this cycle")
            self._cycle += 1
            return

        # Cap actions per cycle — provenance: the dropped tail is logged as
        # rejection rows so capped decisions never silently vanish (E-4).
        for _dropped in actions[MAX_ACTIONS_PER_CYCLE:]:
            self._log_rejection(_dropped, "cycle_cap", stage="cycle_cap")
        actions = actions[:MAX_ACTIONS_PER_CYCLE]

        executed = 0
        for action in actions:
            try:
                if action["type"] == "OPEN":
                    if self._execute_open(action):
                        executed += 1
                    else:
                        # Provenance: _execute_open stashes reject_reason at
                        # every exit; unset stash logs as "unspecified".
                        self._log_rejection(
                            action,
                            action.get("reject_reason", "unspecified"),
                            stage="execute_open")
                elif action["type"] == "CLOSE":
                    if self._execute_close(action):
                        executed += 1
            except Exception as e:
                logger.error(f"[Claude] Action execution error: {e}")

        # Fund ops (transfers between spot/futures) are MCP-brain only.
        if SIGNAL_SOURCE == "mcp" and self.mcp_brain:
            fund_ops = self.mcp_brain.last_fund_ops()
            if fund_ops:
                self._execute_fund_ops(fund_ops)

        self._cycle += 1
        logger.info(
            f"[Claude] Cycle complete: {executed}/{len(actions)} actions executed")

    def _tsmom_signal(self):
        """Lazily build + cache the long-only TSMOM signal (SIGNAL_SOURCE=tsmom path).

        Reuses the engine's live exchange clients (self.exchanges) so it pulls candles from
        the same venues as the MCP path. Built lazily because exchanges are wired after
        mcp_brain in __init__; only constructed if the flag actually selects it.
        """
        cached = getattr(self, "_tsmom", None)
        if cached is None:
            from config import TSMOM_LOOKBACK_DAYS
            from core.tsmom_signal import TSMOMSignal
            cached = TSMOMSignal(exchanges=self.exchanges,
                                 lookback_days=TSMOM_LOOKBACK_DAYS)
            self._tsmom = cached
        return cached

    def _s3_signal(self):
        """Lazily build + cache the S3 multi-horizon momentum ensemble
        (SIGNAL_SOURCE=s3). Same venues/clients as the tsmom path."""
        cached = getattr(self, "_s3", None)
        if cached is None:
            from config import S3_LOOKBACKS_DAYS
            from core.tsmom_signal import S3EnsembleSignal
            cached = S3EnsembleSignal(exchanges=self.exchanges,
                                      lookbacks=S3_LOOKBACKS_DAYS)
            self._s3 = cached
        return cached

    def _machine_signal(self):
        """Lazily build + cache the deterministic machine signal source."""
        cached = getattr(self, "_machine_signal_obj", None)
        if cached is None:
            from config import MACHINE_STRATEGY
            from core.machine_signal import MachineSignal
            cached = MachineSignal(exchanges=self.exchanges, config=MACHINE_STRATEGY)
            self._machine_signal_obj = cached
        return cached

    def _none_signal(self):
        """SIGNAL_SOURCE=none (Rev 5 carry-first): no-op signal — directional
        entries OFF, analyze_portfolio always returns []. The carry runner is
        the only opener; deterministic monitoring still manages open positions."""
        cached = getattr(self, "_none_signal_obj", None)
        if cached is None:
            class _NoSignal:
                @staticmethod
                def analyze_portfolio(**_kwargs):
                    return []
            cached = _NoSignal()
            self._none_signal_obj = cached
        return cached

    def _mcp_det_signal(self):
        """Lazily build + cache the deterministic MCPStrategyScorer
        (SIGNAL_SOURCE=mcp_det). Wraps the existing self.mcp_brain so the algo
        scoring stack is reused; the Claude blend is removed entirely (LLM
        demoted to a research-only warehouse note)."""
        cached = getattr(self, "_mcp_det_obj", None)
        if cached is None:
            from core.mcp_strategy_scorer import MCPStrategyScorer
            from core.strategy_spec import load_all_specs
            cached = MCPStrategyScorer(
                brain=self.mcp_brain,
                specs=load_all_specs(),
                warehouse=getattr(self, "warehouse", None),
            )
            self._mcp_det_obj = cached
        return cached

    def _log_rejection(self, action: dict, reason: str, stage: str):
        """Provenance (2026-06-12): route an action rejection to the MCP
        decision log, keyed by the action's decision_id. Safe no-op when the
        brain ref is None or lacks log_rejection (old versions / tests)."""
        try:
            brain = getattr(self, "mcp_brain", None)
            if brain is None or not hasattr(brain, "log_rejection"):
                return
            brain.log_rejection(
                action.get("decision_id"),
                action.get("symbol", ""),
                reason,
                stage,
            )
        except Exception as e:
            logger.debug(f"[Provenance] rejection log skipped: {e}")

    def _btc_cross_regime_multiplier(self, side: str) -> tuple:
        """Phase 31 (2026-05-05): BTC cross-regime soft veto.

        Audit found 04-07 disaster: 5 SHORT trades all hit SL on the
        same day during a BTC bull squeeze. SHORTS_REQUIRE_BTC_BEAR
        config exists but is off per UNBLOCK_ALL — so we apply a SOFT
        multiplier instead of hard veto:

          BUY  + BTC trend bear  → ×0.6  (counter-trend size cut)
          SELL + BTC trend bull  → ×0.6
          aligned or BTC neutral → ×1.0

        Composes with Phase 27 (per-symbol EV), Phase 28 (asymmetric
        SHORT), Phase 22 (regime).
        """
        try:
            trend = self._get_btc_trend()
        except Exception:
            return 1.0, ""
        if side == "buy" and trend == "bear":
            return 0.6, "btc_bear_vs_buy"
        if side == "sell" and trend == "bull":
            return 0.6, "btc_bull_vs_sell"
        return 1.0, ""

    def _hour_of_day_multiplier(self, side: str) -> tuple:
        """Phase 32 (2026-05-05): hour-of-day bleed multiplier.

        Pure-data hour cell EV check: queries warehouse for last 30d
        realized PnL on (utc_hour, side) and downsizes when current
        hour cell shows historical losses with sample size >=10.

        Tiers:
          n < 10:                ×1.0  (insufficient data)
          mean >= 0:             ×1.0  (positive EV)
          -$0.20 <= mean < 0:    ×0.75 (mildly negative)
          mean < -$0.20:         ×0.50 (strongly negative)

        Complements Phase 27 per-(symbol, side) by adding the
        orthogonal dimension of (hour, side). Self-correcting: as
        the hour cell improves, multiplier auto-restores.
        """
        try:
            from datetime import datetime, timezone
            cur_hour = datetime.now(timezone.utc).hour
            from core.warehouse import get_warehouse
            rows = list(get_warehouse().query(
                "SELECT realized_pnl FROM trades WHERE status='CLOSED' "
                "AND side=? "
                "AND CAST(strftime('%H', ts_entry, 'unixepoch') AS INT)=? "
                "AND ts_entry > strftime('%s', 'now', '-30 days')",
                (side, cur_hour),
            ))
            if len(rows) < 10:
                return 1.0, ""
            pnls = [float(r[0] or 0) for r in rows]
            mean = sum(pnls) / len(pnls)
            if mean >= 0:
                return 1.0, ""
            if mean >= -0.20:
                return 0.75, f"hour{cur_hour}_{side}_neg_${mean:+.2f}_n{len(pnls)}"
            return 0.50, f"hour{cur_hour}_{side}_negstrong_${mean:+.2f}_n{len(pnls)}"
        except Exception as e:
            logger.debug(f"[HourEV] check skipped: {e}")
            return 1.0, ""

    def _ev_per_symbol_multiplier(self, symbol: str, side: str) -> tuple:
        """Phase 27 (2026-05-05): Graduated historical-EV size multiplier.

        Pure data-driven 'test before trade' check. Queries warehouse
        for the last N days of realised PnL on (symbol, side) and
        returns a size multiplier in [0.0, 1.0]:

          n < min_sample_size:        1.0  (insufficient data, allow)
          mean >= 0:                  1.0  (positive EV — full size)
          -0.20 <= mean < 0:          0.75 (mildly negative — downsize)
          -0.50 <= mean < -0.20:      0.50 (moderately negative)
          mean < -0.50:               0.25 (catastrophic — smallest size;
                                      2026-06-11 owner "Don't block any
                                      trades": was 0.0 hard-block, re-arm
                                      via RISK["ev_catastrophic_block_enabled"])

        Whitelisted symbols (config.EXPECTANCY_FILTER.whitelist) bypass.

        Self-correcting: as a symbol's EV improves, the multiplier auto
        ratchets back up. No curated lists, no operator override beyond
        the explicit whitelist.
        """
        try:
            from config import EXPECTANCY_FILTER as _EF
        except ImportError:
            return 1.0, ""
        if not _EF.get("enabled"):
            return 1.0, ""
        try:
            from core.warehouse import get_warehouse as _gw
            sym_key = symbol if ":" in symbol else f"{symbol}:USDT"
            wl = _EF.get("whitelist") or set()
            if symbol in wl or sym_key in wl:
                return 1.0, "whitelisted"
            days = int(_EF.get("lookback_days", 30))
            min_n = int(_EF.get("min_sample_size", 5))
            exp = _gw().recent_expectancy(
                sym_key, side=side, days=days, min_n=min_n,
                whole_trade=_EF.get("whole_trade", False))
            if exp is None:
                # insufficient data — allow at full size (don't block on noise)
                return 1.0, ""
            mean = float(exp.get("mean", 0.0))
            n = int(exp.get("n", 0))
            wr = float(exp.get("win_rate", 0.0))
            # 2026-06-11: informational CI tag — is this negative mean a
            # statistical SIGNAL or NOISE? Tiers/reason strings untouched.
            _ci = exp.get("mean_ci95")
            if _ci and len(_ci) == 2 and mean < 0.0 and _ci[1] == _ci[1]:
                _tag = "NOISE(ci straddles 0)" if _ci[1] > 0.0 else "SIGNAL"
                logger.info(
                    f"[EV-CI] {symbol} {side} mean=${mean:+.2f} n={n} "
                    f"ci95=[{_ci[0]:+.2f},{_ci[1]:+.2f}] {_tag}")
            if mean < -0.50 and n >= 5:
                _reason = f"ev_catastrophic_${mean:+.2f}_n{n}_wr{wr:.0%}"
                if RISK.get("ev_catastrophic_block_enabled", False):
                    return 0.0, _reason
                # 2026-06-11 (owner: "Don't block any trades"): smallest
                # graduated size instead of refusing; ratchet unchanged.
                return 0.25, _reason
            if mean < -0.20:
                return 0.5, f"ev_neg_med_${mean:+.2f}_n{n}"
            if mean < 0.0:
                return 0.75, f"ev_neg_mild_${mean:+.2f}_n{n}"
            return 1.0, ""
        except Exception as e:
            logger.debug(f"[EV] _ev_per_symbol_multiplier exception: {e}")
            return 1.0, ""

    def _execute_open(self, action: dict) -> bool:
        """Validate and execute an OPEN action from Claude. Returns True if executed."""
        from config import (
            BLACKLIST_HARD,
            CONTROLLED_LIVE_ENABLED,
            OPERATING_MODE,
            SHORTS_REQUIRE_BTC_BEAR,
            TRADING_MODE,
            UNIVERSE_WHITELIST,
        )

        symbol     = action.get("symbol", "")
        ex_name    = action.get("exchange", "").lower()
        market_type = action.get("market_type", "futures")
        side       = action.get("side", "").lower()
        confidence = action.get("confidence", 0)
        # leverage / size / sl / tp are now assigned by the leverage tier selector
        # below; Claude's suggested values are IGNORED to enforce the mechanism.

        if not symbol or not ex_name or side not in ("buy", "sell"):
            logger.warning(f"[Claude] Invalid OPEN action: {action}")
            action["reject_reason"] = "invalid_action_fields"
            return False

        # Phase 23 (2026-05-04): per-symbol cooldown — see __init__ context.
        # Silent early-exit when the symbol was just dust-skipped or
        # calibrator-refused. Stops MCP/news/sizing waste on the same
        # signal every 5min for 30min.
        try:
            import time as _t_p23
            _cool_until = self._dust_skip_cooldown.get(symbol, 0.0)
            if _cool_until > 0:
                if _t_p23.time() < _cool_until:
                    action["reject_reason"] = "symbol_cooldown_active"
                    return False
                # cooldown expired — clear it
                del self._dust_skip_cooldown[symbol]
        except Exception:
            pass

        # (A0) ANALYSIS-ONLY hard block (2026-06-02). Commodity/equity perps
        # (gold, oil, stock perps) are real + liquid but too new (~5mo) to have a
        # screened edge. They are fetched + warehoused for research only and must
        # NEVER reach an order until screened. This is the single safety choke;
        # it fires in EVERY mode (incl. paper) and well before any sizing/order work.
        from config import is_analysis_only
        if is_analysis_only(symbol):
            logger.info(
                f"[AnalysisOnly] BLOCKED open {ex_name}:{symbol} {side} — "
                f"analysis/data-collection only (unscreened; no live entry)")
            action["reject_reason"] = "analysis_only_symbol"
            return False

        # Phase 29 (2026-05-05): freqtrade-style post-SL CooldownPeriod +
        # per-pair-side StoplossGuard. After a position closes via SL on
        # this (symbol, side):
        #   - Layer 1: 180min hard cooldown (2026-06-11: 30 → 180 and
        #     re-armed as a BLOCK; the 2026-05-27 advisory-only mode let
        #     ADA/DOT/BNB/APT be re-shorted 9-12x into 70 SLs on Jun 11)
        #   - Layer 2: 6h lock if 2+ SL in last 24h (escalation)
        # Time-based protection on (symbol, side), NOT an edge-opinion
        # block — owner kept Phase 29 under UNBLOCK. Fail-open on error.
        try:
            _sl_active, _sl_reason = self.risk.is_sl_cooldown_active(symbol, side)
            if _sl_active:
                logger.info(
                    f"[Risk29] BLOCKED open {symbol} {side} — {_sl_reason}")
                action["reject_reason"] = "sl_cooldown_active"
                return False
        except Exception as _e:
            logger.debug(f"[Risk29] sl-cooldown check skipped: {_e}")

        # ── LEARNING-FIRST MODE GATES (spec §3, §13) ─────────────────────
        # (A) OBSERVATION mode: never place any order, even paper. The
        #     candidate will still be recorded in the warehouse (Phase B),
        #     but execution short-circuits here.
        if OPERATING_MODE == "OBSERVATION":
            logger.info(
                f"[Mode] OBSERVATION — skipping execution for {ex_name}:{symbol} {side}"
            )
            action["reject_reason"] = "observation_mode"
            return False

        # (B) CONTROLLED_LIVE requires the env latch in addition to config.
        from core.live_gate import live_latch_permits_execution
        if not live_latch_permits_execution(OPERATING_MODE, CONTROLLED_LIVE_ENABLED):
            logger.error(
                "[Mode] OPERATING_MODE=CONTROLLED_LIVE but CONTROLLED_LIVE_ENABLED "
                "env var is not 'true'. Refusing to place live orders. Aborting."
            )
            action["reject_reason"] = "live_latch_missing"
            return False

        # (C) Universe gate. In TRADING_MODE=all the discovery pipeline
        # (pair_discovery.discover_all_mode) feeds the scanner every liquid
        # USDT perp on each exchange, and the downstream gates (MCP score
        # >=65, meta-filter, universe_filter spread/vol/depth, risk
        # manager) enforce quality. The static UNIVERSE_WHITELIST was a
        # learning-first backstop from the 30-symbol static TRADING_PAIRS
        # era; it would contradict "ALL pairs" mode. Skip it when
        # TRADING_MODE='all'. Keep it active in usdt_only/portfolio modes.
        if TRADING_MODE != "all":
            symbol_for_whitelist = symbol if symbol in UNIVERSE_WHITELIST else (
                symbol if ":USDT" in symbol else f"{symbol}:USDT"
            )
            if (symbol not in UNIVERSE_WHITELIST
                    and symbol_for_whitelist not in UNIVERSE_WHITELIST):
                logger.info(
                    f"[Mode] BLOCKED: {symbol} outside configured universe "
                    f"(TRADING_MODE={TRADING_MODE}, universe size="
                    f"{len(UNIVERSE_WHITELIST)})"
                )
                action["reject_reason"] = "outside_universe_whitelist"
                return False

        # (C.2) Short gate — 2026-04-24, gated behind SHORT_GATE_ENABLED
        # (Phase 33, 2026-05-05). User directive: "Remove any blocks."
        # Phase 28 (asymmetric SHORT SL+size) and Phase 27 (per-symbol
        # graduated EV) handle SHORT-side risk in a per-trade,
        # data-driven way without a blanket pause.
        try:
            from config import SHORT_GATE_ENABLED as _SGE
        except ImportError:
            _SGE = True  # default-on for safety if config missing flag
        if _SGE and side == "sell":
            try:
                sell_wr, sell_n = self._recent_side_wr("sell", limit=30)
            except Exception as _e:
                logger.debug(f"[ShortGate] WR probe failed: {_e}")
                sell_wr, sell_n = None, 0
            if sell_n >= 10 and sell_wr is not None and sell_wr < 0.45:
                logger.info(
                    f"[ShortGate] SELL blocked — rolling WR {sell_wr*100:.1f}% "
                    f"over last {sell_n} SELL closes < 45% threshold. "
                    f"Auto-lifts once recovery WR ≥ 45%."
                )
                action["reject_reason"] = "sell_wr_gate"
                return False

        # (C.3a) Side-aware AutoMutator short blacklist (May 2026).
        # Symbols where the recent short cohort has lost ≥3 with ≥65% rate
        # are temporarily un-shortable. Long entries on the same symbol
        # remain allowed.
        if side == "sell" and self.auto_mutator is not None:
            try:
                _short_bl = self.auto_mutator.get_short_blacklist()
                if symbol in _short_bl or symbol.split(":")[0] in _short_bl:
                    logger.info(
                        f"[AutoMutator] SHORT-blacklisted: {symbol} "
                        f"(active short ban)"
                    )
                    action["reject_reason"] = "short_blacklisted"
                    return False
            except Exception as _ame:
                logger.debug(f"[AutoMutator] short blacklist probe failed: {_ame}")

        # (C.3) Short-side filter — block SELL into a BTC up-aligned regime.
        # Catches Claude-AI-proposed actions that bypass _algorithmic_portfolio's
        # gate. See core/short_side_filter.py for evidence and rationale.
        try:
            from config import SHORT_SIDE_FILTER as _SSF_CFG
        except ImportError:
            _SSF_CFG = {"enabled": True}
        if side == "sell" and _SSF_CFG.get("enabled", True):
            try:
                from core.short_side_filter import (
                    evaluate as _ssf_eval,
                )
                from core.short_side_filter import (
                    extract_btc_trends as _ssf_btc,
                )
                _ei_cache = getattr(self.mcp_brain, "_indicator_cache", None) if self.mcp_brain else None
                _btc4, _btc1 = _ssf_btc(_ei_cache)
                _sym_sent = None
                try:
                    _sym_sent = self.news.symbol_sentiment(symbol)
                except Exception:
                    _sym_sent = None
                _ssf_d = _ssf_eval(
                    side="sell", symbol=symbol,
                    btc_4h_uptrend=_btc4, btc_1h_uptrend=_btc1,
                    symbol_news_sentiment=_sym_sent,
                )
                if _ssf_d.block:
                    logger.info(
                        f"[ShortFilter] BLOCKED {symbol} sell -- {_ssf_d.reason}"
                    )
                    action["reject_reason"] = "short_filter_blocked"
                    return False
            except Exception as _ssfe:
                logger.debug(f"[ShortFilter] gate skipped ({_ssfe}) -- defaulting to ALLOW")

        # (C.4) Market-wide BTC volatility circuit-breaker (2026-06-04 owner
        # directive). Pauses NEW entries (any side) during a violent BTC move,
        # auto-resuming when vol normalises. Direction-agnostic (no beta bias);
        # NEW ENTRIES ONLY; fail-OPEN. See core/btc_vol_pause.py.
        try:
            from config import BTC_VOL_PAUSE as _BVP_CFG
        except ImportError:
            _BVP_CFG = {"enabled": False}
        if _BVP_CFG.get("enabled", False):
            try:
                _bvp = getattr(self, "_btc_vol_pause", None)
                if _bvp is None:
                    from core.btc_vol_pause import BtcVolPause
                    _bvp = BtcVolPause()
                    self._btc_vol_pause = _bvp
                _ei_bvp = getattr(self.mcp_brain, "_indicator_cache", None) if self.mcp_brain else None
                _paused, _preason, _ = _bvp.update_and_evaluate(_ei_bvp)
                if _paused:
                    logger.info(f"[BtcVolPause] WAIT -- {_preason} -- skipping new entry {symbol}")
                    action["reject_reason"] = "btc_vol_pause"
                    return False
            except Exception as _bvpe:
                # A2 audit: fail CLOSED — a broken vol-pause evaluation blocks the
                # new entry rather than defaulting to ALLOW.
                logger.warning(f"[BtcVolPause] gate FAILED, blocking entry {symbol}: {_bvpe}")
                action["reject_reason"] = "btc_vol_pause_error"
                return False

        # (C.5) Path-dependent drawdown circuit-breaker (opt-in, 2026-06-28).
        # Per-day loss caps are blind to slow-bleed clustering (e.g. 2%/day for
        # 30 days ~= 45% drawdown that never trips a 5% daily limit). This refuses
        # NEW entries when equity is >X% below its running peak, auto-resuming
        # after a hysteresis recovery. NEW ENTRIES ONLY; fail-OPEN. Default OFF
        # (env DRAWDOWN_PAUSE_ENABLED). See core/drawdown_pause.py.
        try:
            _dp = getattr(self, "_drawdown_pause", None)
            if _dp is None:
                from core.drawdown_pause import DrawdownPause
                _dp = DrawdownPause()
                self._drawdown_pause = _dp
            _dd_peak = float(getattr(self.risk, "peak_balance", 0.0) or 0.0)
            _dd_equity = _deployable_total(self._balances)
            _dd_paused, _dd_reason, _ = _dp.update_and_evaluate(_dd_peak, _dd_equity)
            if _dd_paused:
                logger.info(f"[DrawdownBreaker] WAIT -- {_dd_reason} -- skipping new entry {symbol}")
                action["reject_reason"] = "drawdown_breaker"
                return False
        except Exception as _dpe:
            # A2 audit: fail CLOSED — a broken drawdown-breaker evaluation blocks
            # the new entry rather than defaulting to ALLOW.
            logger.warning(f"[DrawdownBreaker] gate FAILED, blocking entry {symbol}: {_dpe}")
            action["reject_reason"] = "drawdown_breaker_error"
            return False

        # (D) Per-symbol pause (spec §12). Family pause is checked at (D.1)
        # below once strategy_family is known.
        if self.risk and self.risk.is_symbol_paused(symbol):
            logger.info(f"[Risk/Spec12] {symbol} is paused — skipping")
            action["reject_reason"] = "symbol_paused"
            return False

        # (E) Meta-filter quality gate (spec §8). The feature snapshot was
        #     already written to the warehouse candidates table during scoring
        #     (core.mcp_brain._algorithmic_portfolio); we hydrate a
        #     FeatureVector from that row and run the rule-based evaluator.
        #     Failure modes are non-fatal — missing candidate_id or warehouse
        #     errors default to ALLOW so the meta-filter never blocks purely
        #     due to infrastructure issues.
        _meta_size_multiplier = 1.0
        _atr_frac_hint = 0.0   # Fed to _select_leverage_tier for high-ATR clamp
        try:
            import json as _j

            from core.features import FeatureVector as _FV
            from core.meta_filter import MetaFilter as _MetaFilter
            from core.warehouse import get_warehouse as _get_wh
            _cid = int(action.get("candidate_id") or -1)
            _fv = None
            if _cid > 0:
                _rows = _get_wh().query(
                    "SELECT features_json FROM candidates WHERE id=?", (_cid,),
                )
                if _rows:
                    _feat = _j.loads(_rows[0].get("features_json") or "{}")
                    # Surface 1h ATR to the tier selector (below) as a fraction.
                    # _feat stores percent (e.g. 2.5 for 2.5%); tier expects fraction.
                    try:
                        _v = _feat.get("atr_pct_1h")
                        if isinstance(_v, (int, float)) and _v > 0:
                            _atr_frac_hint = float(_v) / 100.0
                    except Exception:
                        pass
                    # Build a FeatureVector from the stored snapshot.
                    _adx_4h = _feat.get("adx_4h")
                    _bb_4h  = _feat.get("bb_width_4h") or 0
                    _regime = None
                    if _adx_4h is not None:
                        if _adx_4h < 15:
                            _regime = "chop"
                        elif _bb_4h < 1.0:
                            _regime = "squeeze"
                        elif _bb_4h > 5.0:
                            _regime = "expansion"
                        else:
                            _regime = "trend"
                    # Compute percentiles against 30d warehouse window
                    # so meta-filter SKIP rules actually fire.
                    from core.features import _pctl
                    _spread_pctl = None
                    _vol_pctl = None
                    try:
                        _since = time.time() - 30 * 86400
                        _sample_rows = _get_wh().query(
                            "SELECT features_json FROM candidates "
                            "WHERE symbol=? AND ts >= ? AND features_json IS NOT NULL",
                            (symbol, _since),
                        )
                        _sp_samp, _at_samp = [], []
                        for _sr in _sample_rows:
                            try:
                                _sf = _j.loads(_sr.get("features_json") or "{}")
                            except Exception:
                                continue
                            if isinstance(_sf.get("spread_pct"), (int, float)):
                                _sp_samp.append(float(_sf["spread_pct"]))
                            if isinstance(_sf.get("atr_pct_1h"), (int, float)):
                                _at_samp.append(float(_sf["atr_pct_1h"]))
                        _spread_pctl = _pctl(_feat.get("spread_pct"), _sp_samp)
                        _vol_pctl = _pctl(_feat.get("atr_pct_1h"), _at_samp)
                    except Exception:
                        pass

                    # RiskManager._global_streak is a list[bool] (True=win). The
                    # meta-filter wants an int trailing-loss count — compute it here.
                    _streak_list = getattr(self.risk, "_global_streak", None) if self.risk else None
                    _loss_streak_int = 0
                    if isinstance(_streak_list, list):
                        for _is_win in reversed(_streak_list):
                            if _is_win:
                                break
                            _loss_streak_int += 1
                    _fv = _FV(
                        spread_pctl=_spread_pctl,
                        vol_pctl=_vol_pctl,
                        funding_bias=_feat.get("funding_rate"),
                        ob_imbalance=_feat.get("ob_imbalance"),
                        trend_strength=(_adx_4h / 100.0) if _adx_4h is not None else None,
                        regime_label=_regime,
                        hour_of_day=time.gmtime().tm_hour,
                        day_of_week=time.gmtime().tm_wday,
                        recent_loss_streak=_loss_streak_int,
                        exchange_id=ex_name,
                        symbol_id=symbol,
                        raw=_feat,
                    )
            if _fv is not None:
                _mcp_score_for_filter = action.get("mcp_score") or action.get("score")
                try:
                    _mcp_score_for_filter = float(_mcp_score_for_filter) if _mcp_score_for_filter is not None else None
                except (TypeError, ValueError):
                    _mcp_score_for_filter = None
                _decision = _MetaFilter().evaluate(
                    _fv, side=side, confidence=confidence,
                    mcp_score=_mcp_score_for_filter,
                )
                logger.info(
                    f"[MetaFilter] {symbol} {side}: {_decision.decision} "
                    f"-- {_decision.reason}"
                )
                if _decision.decision == "SKIP":
                    # 2026-05-27 (UNBLOCK directive): log only, don't block.
                    # If MCP Brain scored it correctly and TP is set, trade it.
                    # Warehouse still records the meta-filter opinion for audit.
                    try:
                        _get_wh().query(
                            "UPDATE candidates SET skip_reason=? WHERE id=?",
                            (f"meta_advisory:{_decision.reason}", _cid),
                        )
                    except Exception:
                        pass
                    logger.info(f"[MetaFilter] SKIP advisory (not blocking): {_decision.reason}")
                if _decision.decision == "REVIEW":
                    logger.info(f"[MetaFilter] REVIEW advisory (not blocking): {_decision.reason}")
                _meta_size_multiplier = float(_decision.size_multiplier or 1.0)
        except Exception as _mfe:
            logger.debug(f"[MetaFilter] skipped ({_mfe}) -- defaulting to ALLOW")

        # ── LR MODEL SOFT SIZE MULTIPLIER (Phase 13.5b → 13.6) ──────────
        # Wire the shadow LR model as a SOFT size multiplier. The model
        # has AUC ≈ 0.68 / DSR ≈ 0.18 — marginal signal, NOT deployable
        # as a hard gate (would block too many true positives), but
        # usable as a directional nudge on sizing. Map p_win to a
        # multiplier in [0.7, 1.3]: high-confidence trades get +30%,
        # low-confidence get -30%. Neutral 1.0x when model unavailable.
        # Combined with _meta_size_multiplier multiplicatively below.
        _lr_size_multiplier = 1.0
        try:
            if _fv is not None:  # only if features were hydrated for meta-filter
                from core.shadow_predictor import ShadowPredictor as _SP
                p_win = _SP.get().predict_p_win(_feat)
                if p_win is not None:
                    # Linear map: p_win=0.5 → 1.0x; p_win=0.65 → 1.3x;
                    # p_win=0.35 → 0.7x. Clamp to [0.7, 1.3] so the
                    # model never zeroes a trade or doubles size — that
                    # would over-trust a marginal-signal model.
                    _lr_size_multiplier = max(0.7, min(1.3, 1.0 + 2.0 * (p_win - 0.5)))
                    logger.info(
                        f"[LR-SIZE] {symbol} {side}: p_win={p_win:.2f} "
                        f"→ size×{_lr_size_multiplier:.2f}")
        except Exception as _lre:
            logger.debug(f"[LR-SIZE] skipped ({_lre}) — neutral 1.0x")

        # ── CELL-FILTER ENTRY GATE (2026-05-01) ──────────────────────────
        # Only fire on proven-edge cells. STAR symbols are always allowed
        # (subject to existing tier-cap on score>=85). Non-STAR symbols
        # require mcp_score in the proven [70, 84] band — score >= 85 is
        # anti-EV per claude_portfolio attribution. See
        # docs/superpowers/specs/2026-05-01-cell-filter-entry-gate-design.md
        try:
            from config import CELL_FILTER as _CF
            from config import STAR_SYMBOLS as _STAR
        except ImportError:
            _CF = {"enabled": False}
            _STAR = set()
        if _CF.get("enabled", True):
            _symbol_key = symbol if ":" in symbol else f"{symbol}:USDT"
            _is_star = (
                _CF.get("star_overrides", True)
                and (symbol in _STAR or _symbol_key in _STAR)
            )
            _score = action.get("mcp_score") or action.get("score") or 0.0
            try:
                _score = float(_score)
            except (TypeError, ValueError):
                _score = 0.0
            _band_min = float(_CF.get("score_band_min", 70.0))
            _band_max = float(_CF.get("score_band_max", 84.0))
            # 2026-05-01: star_only mode — block ALL non-STAR regardless
            # of score. 30-day BAND tier was -$3.61 / 36 trades (-10%/trade).
            # Operator chose to skip non-STAR entirely.
            if not _is_star and _CF.get("star_only", False):
                logger.info(
                    f"[CellFilter] BLOCKED {symbol}: non-STAR + star_only mode "
                    f"(BAND tier was -$3.61/36 trades over 30d)")
                try:
                    if (_cid := int(action.get("candidate_id") or 0)) > 0:
                        from core.warehouse import get_warehouse as _gw
                        _gw().query(
                            "UPDATE candidates SET decision='SKIP', "
                            "skip_reason=? WHERE id=?",
                            ("cell_filter:non_star_blocked_star_only_mode", _cid),
                        )
                except Exception:
                    pass
                action["reject_reason"] = "cell_filter_non_star"
                return False
            if not _is_star:
                if _score < _band_min:
                    logger.info(
                        f"[CellFilter] BLOCKED {symbol}: non-STAR + "
                        f"score {_score:.1f} < {_band_min:.1f} band_min")
                    # Patch warehouse skip-reason if we have a candidate id.
                    try:
                        if (_cid := int(action.get("candidate_id") or 0)) > 0:
                            from core.warehouse import get_warehouse as _gw
                            _gw().query(
                                "UPDATE candidates SET decision='SKIP', "
                                "skip_reason=? WHERE id=?",
                                ("cell_filter:score_below_band", _cid),
                            )
                    except Exception:
                        pass
                    action["reject_reason"] = "cell_filter_score_below_band"
                    return False
                if _score > _band_max:
                    logger.info(
                        f"[CellFilter] BLOCKED {symbol}: non-STAR + "
                        f"score {_score:.1f} > {_band_max:.1f} band_max "
                        f"(anti-EV per claude_portfolio data)")
                    try:
                        if (_cid := int(action.get("candidate_id") or 0)) > 0:
                            from core.warehouse import get_warehouse as _gw
                            _gw().query(
                                "UPDATE candidates SET decision='SKIP', "
                                "skip_reason=? WHERE id=?",
                                ("cell_filter:score_above_band", _cid),
                            )
                    except Exception:
                        pass
                    action["reject_reason"] = "cell_filter_score_above_band"
                    return False

        # ── PHASE 27 (2026-05-05): GRADUATED EV TEST-BEFORE-TRADE ────────
        # User directive: "Scan 24/7. No emotions. No bias. Just data.
        # Test before it trades." This block translates that into a
        # warehouse-grounded EV check on (symbol, side) before sizing.
        #
        # Replaces the prior BINARY filter (Tier 1.2 / Phase 20-disabled)
        # which locked symbols out forever once recent mean dropped under
        # the floor — chicken-and-egg, no path to recovery. Phase 27
        # uses GRADUATED tiers: as evidence worsens, size shrinks; only
        # catastrophic EV (< -$0.50 mean over n>=5) hard-blocks. As the
        # symbol's EV improves, size auto-restores. Pure data, no
        # curated lists. Self-correcting.
        #
        # Multiplier saved to `_ev_symbol_mult` and applied in the
        # size_fraction chain alongside Phase 17/18/22.
        _ev_symbol_mult = 1.0
        _ev_symbol_reason = ""
        try:
            _ev_symbol_mult, _ev_symbol_reason = self._ev_per_symbol_multiplier(
                symbol, side)
        except Exception as _ee:
            logger.debug(f"[EV] check skipped ({_ee}) — defaulting to ALLOW")
        if _ev_symbol_mult <= 0.0:
            logger.warning(
                f"[EV] BLOCKED {symbol} {side} ({_ev_symbol_reason}). "
                f"Phase 27: catastrophic historical EV — refuse to trade.")
            try:
                from core.warehouse import get_warehouse as _gw_block
                if (_cid := int(action.get("candidate_id") or 0)) > 0:
                    _gw_block().query(
                        "UPDATE candidates SET decision='SKIP', "
                        "skip_reason=? WHERE id=?",
                        (f"ev_phase27:{_ev_symbol_reason}", _cid),
                    )
            except Exception:
                pass
            action["reject_reason"] = "ev_negative_cell"
            return False

        # ── 2026-05-03 (Phase 16) → 2026-05-04 (Phase 22): Regime-aware gate
        # COUNTER-TREND remains HARD BLOCK (stronger evidence of bad edge):
        #   - long signal in TRENDING_DOWN regime → skip
        #   - short signal in TRENDING_UP regime → skip
        # VOLATILE: SOFT multiplier (×0.4 by default, tunable via
        #   RISK.regime_volatile_size_mult). Phase 16's hard-block on
        #   volatile produced 10+ rejections per hour during normal market
        #   conditions (BTC at 0.79% ATR getting "vol_extreme" via 95th-pctl
        #   relative classification). Per UNBLOCK_ALL philosophy + same
        #   pattern as Phase 17/18 soft-multipliers, we now SIZE DOWN
        #   instead of veto. Flip RISK.regime_volatile_block_enabled=True
        #   to restore the hard block.
        # RANGING / TRENDING-aligned regime → allow at full size.
        # Regime detector cached 15min — check is cheap.
        _regime_size_mult = 1.0   # default — full size unless volatile soft-mult
        try:
            from core.market_regime import (
                REGIME_TRENDING_DOWN,
                REGIME_TRENDING_UP,
                REGIME_VOLATILE,
                MarketRegimeDetector,
            )
            if not hasattr(self, "_regime_detector"):
                self._regime_detector = MarketRegimeDetector()
            ex_obj = self.exchanges.get(ex_name)
            if ex_obj is not None:
                regime = self._regime_detector.detect(ex_obj, symbol)
                _block = False
                _why = ""
                # Counter-trend: hard block DISABLED 2026-06-11 (owner:
                # "Don't block any trades") — soft size-down like VOLATILE.
                # Re-arm via RISK["regime_countertrend_block_enabled"].
                _ct = (side == "buy" and regime == REGIME_TRENDING_DOWN) or (
                    side == "sell" and regime == REGIME_TRENDING_UP)
                if _ct and RISK.get("regime_countertrend_block_enabled", False):
                    _why = ("regime:trending_down_blocks_long" if side == "buy"
                            else "regime:trending_up_blocks_short")
                    _block = True
                elif _ct:
                    _regime_size_mult = float(
                        RISK.get("regime_countertrend_size_mult", 0.4))
                    logger.info(
                        f"[Regime] {symbol} {side} COUNTER-TREND — soft size "
                        f"×{_regime_size_mult:.2f} (UNBLOCK 2026-06-11)")
                # Volatile: Phase 22 soft multiplier (or hard block if user re-enabled)
                elif not _ct and regime == REGIME_VOLATILE:
                    if RISK.get("regime_volatile_block_enabled", False):
                        _block, _why = True, "regime:volatile"
                    else:
                        _regime_size_mult = float(
                            RISK.get("regime_volatile_size_mult", 0.4))
                        logger.info(
                            f"[Regime] {symbol} {side} VOLATILE — "
                            f"soft size ×{_regime_size_mult:.2f} (Phase 22)")
                if _block:
                    logger.info(f"[Regime] BLOCKED {symbol} {side}: {_why}")
                    try:
                        if (_cid := int(action.get("candidate_id") or 0)) > 0:
                            from core.warehouse import get_warehouse as _gw
                            _gw().query(
                                "UPDATE candidates SET decision='SKIP', "
                                "skip_reason=? WHERE id=?",
                                (_why, _cid),
                            )
                    except Exception:
                        pass
                    action["reject_reason"] = "regime_blocked"
                    return False
        except Exception as _re:
            logger.debug(f"[Regime] check skipped ({_re}) — defaulting to ALLOW")

        # ── HIGH-WR MECHANISM GATES (applied BEFORE legacy checks) ──────

        # (a) Symbol blacklist — evidence-based hard block (static + dynamic)
        symbol_key = symbol if ":" in symbol else f"{symbol}:USDT"
        if symbol in BLACKLIST_HARD or symbol_key in BLACKLIST_HARD:
            logger.info(f"[Claude] BLOCKED by blacklist: {symbol}")
            action["reject_reason"] = "blacklist_hard"
            return False
        if self.auto_mutator:
            dyn_bl = self.auto_mutator.get_effective_blacklist()
            if symbol in dyn_bl or symbol_key in dyn_bl:
                # 2026-06-11 (owner: "Don't block any trades"): enforcement is
                # opt-in; the mutator keeps TRACKING loss clusters either way.
                if RISK.get("auto_mutator_block_enabled", False):
                    logger.info(f"[Claude] BLOCKED by dynamic (post-mortem) blacklist: {symbol}")
                    action["reject_reason"] = "blacklist_dynamic"
                    return False
                logger.info(
                    f"[Claude] dynamic blacklist hit {symbol} — NOT blocked "
                    f"(UNBLOCK 2026-06-11; tracking only)")
            if side == "sell" and self.auto_mutator.shorts_blocked():
                logger.info(
                    "[Claude] BLOCKED: shorts disabled by AutoMutator "
                    "(counter-trend short losses in recent post-mortems)")
                action["reject_reason"] = "shorts_disabled_automutator"
                return False

        # (a2) Caution symbol — soft gate. Knowledge-model WR<35% triggers
        # caution, but high-conviction signals (conf>=0.90) still pass.
        # Rationale: the caution list is auto-built from historical losses,
        # many of which were driven by bot-side bugs (SL-placement failures,
        # stale-close rules) that have since been fixed. A permanent symbol
        # block punishes setups we'd now take. Confidence 0.90 is the
        # natural break in the 30d ALLOW histogram (~top 50%).
        # 2026-05-04 (Phase 19): gate now config-flagged. Default False per
        # UNBLOCK_ALL — Phase 16 adaptive sizing + Phase 18 calibrator already
        # size down low-EV setups organically. Set RISK.caution_symbol_block_enabled
        # = True to restore. Stale "<50% WR" log corrected to "<35% WR" — actual
        # threshold is in knowledge_model.py:284 (wr < 35).
        if RISK.get("caution_symbol_block_enabled", False):
            try:
                from core.knowledge_model import KnowledgeModel
                _km = KnowledgeModel()
                if _km.is_caution_symbol(symbol) or _km.is_caution_symbol(symbol_key):
                    if confidence >= 0.90:
                        logger.info(
                            f"[Claude] caution-symbol OVERRIDE {symbol} "
                            f"(conf={confidence:.2f} >= 0.90) — high-conviction pass")
                    else:
                        logger.info(
                            f"[Claude] BLOCKED: {symbol} is caution symbol "
                            f"(<35% WR, conf={confidence:.2f} < 0.90)")
                        action["reject_reason"] = "caution_symbol_low_conf"
                        return False
            except Exception:
                pass

        # (b) Spot — buy-only (no short on spot)
        if market_type == "spot" and side == "sell":
            logger.warning("[Claude] BLOCKED: Cannot short on spot")
            action["reject_reason"] = "spot_short_not_possible"
            return False

        # (c) BTC macro trend + side filter
        btc_trend = self._get_btc_trend()
        if side == "sell" and SHORTS_REQUIRE_BTC_BEAR and btc_trend != "bear":
            logger.info(
                f"[Claude] BLOCKED: SHORT {symbol} requires BTC 4h macro-bear, "
                f"current trend={btc_trend}")
            action["reject_reason"] = "short_requires_btc_bear"
            return False
        # 2026-04-12: Removed bear-macro long-blocking gate. The scoring
        # engine already requires per-coin 4h+1h EMA20>50 alignment for
        # longs — if a coin is trending up despite BTC being bearish,
        # the setup is valid. The old gate was killing legitimate longs
        # and concentrating all trades on Binance (most whitelist pairs).

        # (e) Leverage tier selector — also enforces hour gate + throttle + whitelist
        # ATR hint comes from the warehouse candidate features (atr_pct_1h), converted
        # to a fraction above. Falls back to 0 (no clamp) if the candidate row is missing.
        # The tier selector also handles: BLOCKED_HOURS_UTC, consec-loss pause/downgrade,
        # min-confidence threshold, whitelist requirement, BTC alignment, peak hour.
        tier_name, tier_params = self._select_leverage_tier(
            symbol_key, side, confidence, btc_trend,
            atr_pct=_atr_frac_hint,
            mcp_score=float(action.get("mcp_score") or action.get("score") or 0.0),
        )
        if tier_params is None:
            action["reject_reason"] = "no_leverage_tier"
            return False

        # Tier controls leverage; algorithm's ATR-based SL/TP are preferred
        # when provided (they adapt to each coin's volatility). Tier SL/TP
        # only used as fallback when the algorithm doesn't send its own.
        leverage = tier_params["leverage"]
        size_pct = tier_params["size_pct"] * 100.0   # selector uses fraction; rest of fn uses %
        try:
            algo_sl = float(action.get("sl_pct", 0) or 0)
            algo_tp = float(action.get("tp_pct", 0) or 0)
        except Exception:
            algo_sl = 0.0
            algo_tp = 0.0
        # Phase 2b: a tsmom OPEN gets a WIDE disaster stop (the signal's sl_pct,
        # ~8%), NO take-profit (tp_pct=0 — the exit is the daily momentum flip),
        # leverage 1, and bypasses the scalp R:R gate below. Without this the
        # tier-fallback silently swaps the 8% stop for a ~1.5% scalp stop and the
        # R:R gate rejects the entry (tp=0 -> R:R=0). See core/tsmom_signal.py.
        from core.tsmom_signal import is_tsmom_action, tsmom_entry_shape
        _is_tsmom_entry = is_tsmom_action(action)
        if _is_tsmom_entry:
            sl_pct, tp_pct, leverage, _tsmom_bypass_rr = tsmom_entry_shape(action)
            _used_action_sltp = False
        else:
            _tsmom_bypass_rr = False
            _used_action_sltp = algo_sl > 0 and algo_tp > 0
            if _used_action_sltp:
                sl_pct = algo_sl
                tp_pct = algo_tp
            else:
                sl_pct = tier_params["sl_pct"] * 100.0
                tp_pct = tier_params["tp_pct"] * 100.0

        min_rr = RISK.get("min_rr_ratio", 1.8)
        if _used_action_sltp:
            _action_rr = tp_pct / sl_pct if sl_pct > 0 else 0
            if _action_rr < min_rr:
                _tier_sl = tier_params["sl_pct"] * 100.0
                _tier_tp = tier_params["tp_pct"] * 100.0
                _tier_rr = _tier_tp / _tier_sl if _tier_sl > 0 else 0
                if _tier_rr >= min_rr:
                    logger.info(
                        f"[Claude] {symbol} action SL/TP R:R {_action_rr:.2f}:1 "
                        f"below {min_rr:.1f}:1; using {tier_name} tier shape "
                        f"(SL={_tier_sl:.2f}% TP={_tier_tp:.2f}%)")
                    sl_pct = _tier_sl
                    tp_pct = _tier_tp

        # Phase 28 (2026-05-05): asymmetric SHORT-side risk reduction.
        # Audit of 267 closed trades:
        #   SHORT side: 102 trades, 37.3% WR, $-52.12 sum  (79% of bleed)
        #   BUY  side: 165 trades, 44.8% WR, $-14.48 sum
        # Per-side asymmetric SL: tighter SHORT stop caps per-loss damage
        # at the cost of more frequent SL touches. Net per-trade EV math
        # (assuming WR drops 4 points to 33% but per-loss shrinks 40%):
        #   old: 0.37 × $win − 0.63 × $loss        ≈ −$0.51
        #   new: 0.33 × $win − 0.67 × ($loss×0.60) ≈ −$0.19
        # ~$0.32/trade improvement → ~$5/30d at current SHORT volume.
        # Size also cut 25% on shorts as additive de-risk on the bad side.
        # ShortGate (existing) still gates entry on 30d SELL WR<45%.
        # Phase 27 graduated EV still downsizes per-(symbol, side) cell.
        if side == "sell":
            _orig_sl = sl_pct
            _orig_size = size_pct
            sl_pct = sl_pct * 0.60
            size_pct = size_pct * 0.75
            logger.info(
                f"[Claude] {symbol} SHORT asymmetric: "
                f"SL {_orig_sl:.2f}%→{sl_pct:.2f}% size "
                f"{_orig_size:.1f}%→{size_pct:.1f}% (Phase 28)")

        # ── ACCURACY_TARGET_MODE chokepoint (owner goal 2026-07-10) ──────────
        # The FINAL TP authority: actions reach here from several builders
        # (algorithmic block, Claude ingestion clamp, SCALP tier defaults,
        # tier_params) — the first live entry proved builder-level overrides
        # miss some paths (ARB opened sl=0.8/tp=1.3 via the scalp path). Apply
        # the band geometry to every executed futures entry with a real TP;
        # tsmom (tp_pct=0, exit = momentum flip) is excluded. Flag-off = no-op.
        _acc_mode_on = False
        if tp_pct > 0 and not _tsmom_bypass_rr:
            from core.mcp_brain import _apply_accuracy_target
            _tp_before_acc = tp_pct
            tp_pct = _apply_accuracy_target(sl_pct, tp_pct, side=side)
            _acc_mode_on = tp_pct != _tp_before_acc
            if _acc_mode_on:
                logger.info(
                    f"[Claude] {symbol} ACCURACY band: TP {_tp_before_acc:.2f}%"
                    f"→{tp_pct:.2f}% (SL={sl_pct:.2f}%, target WR 60-65%)")

        # R:R validation (always > min_rr_ratio because tiers define tp > 2x sl, but sanity-check)
        # Phase 2b: tsmom has no take-profit (R:R undefined) — its only price rail
        # is the wide disaster stop, so the R:R gate does not apply.
        # ACCURACY_TARGET_MODE: the inverted shape (R:R ~0.5) is INTENTIONAL —
        # the min-R:R gate exists to catch malformed proposals, not the band.
        actual_rr = tp_pct / sl_pct if sl_pct > 0 else 0
        if actual_rr < min_rr and not _tsmom_bypass_rr and not _acc_mode_on:
            logger.info(
                f"[Claude] BLOCKED: {symbol} R:R {actual_rr:.2f}:1 "
                f"< {min_rr:.1f}:1 minimum (SL={sl_pct}% TP={tp_pct}%)")
            action["reject_reason"] = "rr_below_min"
            return False

        exchange = self.active_exchanges.get(ex_name)
        if not exchange:
            logger.warning(f"[Claude] Exchange '{ex_name}' not connected")
            action["reject_reason"] = "exchange_not_connected"
            return False

        # ── Exchange health gate — block new trades on halted exchanges ──
        if self.is_exchange_halted(ex_name):
            logger.warning(
                f"[Claude] BLOCKED: {ex_name} is HALTED (API unreachable) "
                f"— no new trades until recovered")
            action["reject_reason"] = "exchange_halted"
            return False

        # ── Strategy gate: block caution (<50% WR) and fee-heavy strategies ──
        strategy_name = action.get("strategy", "")

        # (D.1) Per-family pause (spec §12) — now that strategy_family is known.
        if strategy_name and self.risk and self.risk.is_family_paused(strategy_name):
            logger.info(f"[Risk/Spec12] strategy family '{strategy_name}' is paused — skipping")
            action["reject_reason"] = "family_paused"
            return False

        if strategy_name and RISK.get("caution_strategy_block_enabled", False):
            try:
                from core.knowledge_model import KnowledgeModel
                km = KnowledgeModel()
                if km.is_caution_strategy(strategy_name):
                    logger.info(
                        f"[Claude] BLOCKED: strategy '{strategy_name}' is caution "
                        f"(<50% WR) — auto-disabled")
                    action["reject_reason"] = "caution_strategy"
                    return False
                if strategy_name in km.get_fee_heavy_strategies():
                    logger.info(
                        f"[Claude] BLOCKED: strategy '{strategy_name}' is fee-heavy "
                        f"(fees >20% of gross profit) — auto-disabled")
                    action["reject_reason"] = "fee_heavy_strategy"
                    return False
            except Exception:
                pass

        # ── Universe filter: spread, volatility, depth, halt checks ──
        if self.universe_filter:
            uf_result = self.universe_filter.check(exchange, symbol, market_type)
            if not uf_result["ok"]:
                logger.info(
                    f"[Claude] BLOCKED by universe filter: {symbol} — "
                    f"{', '.join(uf_result['reasons'])}")
                action["reject_reason"] = "universe_filter_blocked"
                return False

        # Risk manager circuit breakers
        if not self.risk.can_trade(self.tracker.count_open()):
            logger.warning(f"[Claude] BLOCKED by risk manager: {self.risk.halt_reason}")
            action["reject_reason"] = "risk_halted"
            return False

        # Per-exchange position limit
        ex_open = self.tracker.count_open(exchange=exchange.name)
        if ex_open >= MAX_PER_EXCHANGE:
            logger.info(f"[Claude] {ex_name}: {ex_open}/{MAX_PER_EXCHANGE} positions — full")
            action["reject_reason"] = "exchange_position_limit"
            return False

        # Total position limit (from config, not module constant)
        total_open = self.tracker.count_open()
        _max_positions = RISK.get("max_open_positions", 8)
        if total_open >= _max_positions:
            logger.info(f"[Claude] Total {total_open}/{_max_positions} — full")
            action["reject_reason"] = "total_position_limit"
            return False

        # No duplicate base asset across ANY exchange.
        # 2026-05-24 — Was filtered by exchange.name, which let the same
        # base asset open on every venue in a single 5-min cycle. The
        # May-13 BNB cascade (-$18.66) is exactly this pattern with three
        # concurrent BNB opens. Memory: project_may13_cascade_fixes_2026_05_13.
        base_asset = symbol.split("/")[0]
        all_open = self.tracker.get_open()
        already_has = any(
            p.symbol.split("/")[0] == base_asset for p in all_open
        )
        if already_has:
            logger.info(
                f"[Claude] {base_asset} already open (any exchange) — skip")
            action["reject_reason"] = "symbol_already_open"
            return False

        # Correlation check — prevent over-concentration in correlated assets
        total_bal = _deployable_total(self._balances)
        corr_info = {"can_add": True, "size_multiplier": 1.0}
        if total_bal > 0:
            corr_info = self.risk.check_correlation(
                symbol, self.tracker.get_open(), total_bal)
            if not corr_info.get("can_add", True):
                logger.info(
                    f"[Claude] BLOCKED: {symbol} correlation group "
                    f"'{corr_info.get('group', '?')}' at "
                    f"{corr_info.get('current_pct', 0)*100:.0f}% exposure "
                    f"(max {corr_info.get('max_pct', 0)*100:.0f}%)")
                action["reject_reason"] = "correlation_exposure_cap"
                return False

        # Balance check
        ex_bals = self._balances.get(ex_name, {})
        mtype_bal = ex_bals.get(market_type, 0.0)
        min_trade_bal = 8.0 if market_type == "futures" else 3.0
        if mtype_bal < min_trade_bal:
            # Try auto-transfer
            other = "spot" if market_type == "futures" else "futures"
            other_bal = ex_bals.get(other, 0.0)
            xfer_key = (ex_name, other, market_type)
            xfer_cooldown = time.time() - self._last_transfer.get(xfer_key, 0) < 300
            can_xfer = (other_bal >= 6.0
                        and ex_name not in ("bybit",)
                        and not xfer_cooldown)
            if can_xfer:
                xfer = min(other_bal * 0.70, 200.0)
                self._last_transfer[xfer_key] = time.time()
                # SAFETY (audit 2026-06-03): exchange.transfer() is a REAL live-account fund
                # move (Binance Universal Transfer / Bitget transfer). In PAPER it must NEVER
                # touch the live account — simulate the move in-memory only. Mirrors the
                # DRY_RUN gating already on set_leverage and _execute_fund_ops. This was the
                # one transfer site missed by the May-31 PAPER->live leak fix.
                try:
                    did_xfer = True if DRY_RUN else exchange.transfer(xfer, other, market_type)
                    if did_xfer:
                        ex_bals[other] -= xfer
                        ex_bals[market_type] = mtype_bal + xfer
                        mtype_bal += xfer
                        logger.info(
                            f"[Claude] {'[DRY] simulated ' if DRY_RUN else ''}auto-transfer "
                            f"${xfer:.2f} {other}->{market_type} on {ex_name}")
                except Exception as e:
                    logger.debug(f"[Claude] Auto-transfer failed: {e}")
            if mtype_bal < min_trade_bal:
                logger.info(f"[Claude] {ex_name} {market_type} balance ${mtype_bal:.2f} < ${min_trade_bal}")
                action["reject_reason"] = "balance_below_min"
                return False

        # Add :USDT suffix for futures
        trade_symbol = symbol
        if market_type == "futures" and ":" not in symbol:
            trade_symbol = symbol + ":USDT"

        # Compute position size from size_pct (apply correlation + meta-filter reduction)
        size_fraction = size_pct / 100.0
        # 2026-06-11: group-bucket taper superseded by the Portfolio ES
        # soft-cap later in this chain (real EWMA covariance vs static
        # buckets that assume zero cross-group corr — measured BTC/DOT
        # rho~0.85). check_correlation still runs above for can_add
        # logging/audit. Re-arm the bucket taper via
        # RISK["corr_group_taper_enabled"]=True (e.g. if ES_RISK is off).
        corr_mult = corr_info.get("size_multiplier", 1.0) if total_bal > 0 else 1.0
        if corr_mult < 1.0 and RISK.get("corr_group_taper_enabled", False):
            size_fraction *= corr_mult
            logger.info(
                f"[Claude] {symbol} size reduced {corr_mult:.0%} "
                f"(correlation group '{corr_info.get('group', '?')}')")
        # Apply meta-filter size modifier (spec §8 — quality-based de-risking)
        if _meta_size_multiplier < 1.0:
            size_fraction *= _meta_size_multiplier
            logger.info(
                f"[Claude] {symbol} size reduced {_meta_size_multiplier:.0%} "
                f"(meta-filter quality gate)")
        # Apply LR model soft size multiplier (Phase 13.6).
        # Symmetric in [0.7, 1.3] — can BOTH increase and decrease size
        # based on the model's p_win prediction. Never gates; never zeroes.
        if _lr_size_multiplier != 1.0:
            size_fraction *= _lr_size_multiplier
            logger.info(
                f"[Claude] {symbol} size ×{_lr_size_multiplier:.2f} "
                f"(LR model p_win)")
        # MIN-NOTIONAL FLOOR (2026-07-10): snapshot what the BASE sizing
        # affords BEFORE the EV-opinion multiplier stack below (Phase 17
        # rolling-50, Phase 18 calibrator, Phase 27 per-symbol EV). Used at
        # the exchange-min pre-check to undo opinion-downsizing only —
        # never to exceed what balance/margin reality allowed.
        _pre_ev_notional = size_fraction * mtype_bal
        # 2026-05-03 (Phase 17 fix): Phase 16 adaptive sizing was wired
        # into RiskManager.calculate_position_size, which the live Claude
        # portfolio path NEVER calls — Ruflo reviewer flagged this as
        # dead code. Apply the rolling-EV multiplier directly here so
        # the closed feedback loop actually fires on live trades.
        try:
            _ev_mult = self.risk._adaptive_size_multiplier()
        except Exception:
            _ev_mult = 1.0
        if _ev_mult != 1.0:
            size_fraction *= _ev_mult
            logger.info(
                f"[Claude] {symbol} size ×{_ev_mult:.2f} "
                f"(adaptive sizing — rolling-50 EV)")
        # 2026-05-04 (Phase 18): ProbabilityCalibrator soft size multiplier.
        # Sister fix to Phase 17 — second instance of dead-code wiring
        # found by Ruflo audit. The calibrator was never called from the
        # entry path; now we feed predicted_conf at close (in
        # order_manager._finalize_close) and use the calibrated value
        # here as a [0.7, 1.3] symmetric size multiplier.
        #
        # 2026-05-04 (Phase 23): hard-refuse layer ON TOP of the soft mult.
        # Soft-discounting a signal the calibrator has *measured* as 9%
        # actual win-rate (raw conf 85%, n=35) is structurally negative
        # EV: even at 70% size you lose. Refuse instead of size-down.
        # Use divergence (`abs(_calibrated - _raw_conf) > 0.02`) to
        # distinguish "calibrator has data" from "calibrate() fell
        # through to raw_conf" — the old `_calibrated > 0` guard
        # silently skipped the catastrophic 0.0 case (Phase 18 bug).
        try:
            _raw_score = float(
                action.get("mcp_score") or action.get("score") or 0.0)
            _raw_conf = _raw_score / 100.0 if _raw_score > 0 else 0.0
            if _raw_conf > 0:
                _calibrated = self.order_mgr.calibrator.calibrate(
                    _raw_conf, "claude_portfolio")
                _has_calib_data = abs(_calibrated - _raw_conf) > 0.02
                # Phase 23 — hard-refuse when actual edge has collapsed.
                # Phase 40 (2026-05-10): threshold 0.40 → 0.30. The calibrator
                # was fit on stale data (pre-Phase-39: 5x leverage disaster
                # days, mcp_brain_close drag, blacklist symbols still trading).
                # Avg calib error = 41.8% — essentially noise. After Phase 39
                # structural fixes, refusing every trade <40% creates a
                # chicken-and-egg: 0 trades → no fresh data → calibrator
                # never updates. Math: at 30% WR + 2:1 R:R, EV = -0.10 / trade
                # — soft mult sizes those down to 70% so worst-case bleed is
                # contained. This unblocks fresh data collection.
                if _has_calib_data and _calibrated < 0.30:
                    # 2026-06-11 (owner: "Don't block any trades"): Phase 40
                    # hard-refuse is opt-in via RISK["calibrator_hard_refuse_enabled"];
                    # default path relies on the Phase 18 soft mult below (0.7 floor).
                    if RISK.get("calibrator_hard_refuse_enabled", False):
                        import time as _t_p23r
                        self._dust_skip_cooldown[symbol] = _t_p23r.time() + 1800
                        logger.warning(
                            f"[Claude] {symbol} REFUSED: calibrator predicts "
                            f"actual win-rate {_calibrated:.0%} for raw conf "
                            f"{_raw_conf:.0%} (Phase 40 hard-refuse < 30%, "
                            f"30min cooldown).")
                        action["reject_reason"] = "calibrator_hard_refuse"
                        return False
                    logger.info(
                        f"[Claude] {symbol} calibrator predicts {_calibrated:.0%} "
                        f"(<30%) — NOT refused (UNBLOCK 2026-06-11); soft mult applies")
                # Soft mult — existing Phase 18 behavior, unchanged
                if _has_calib_data:
                    _cal_mult = max(0.7, min(1.3,
                                             _calibrated / max(_raw_conf, 0.01)))
                    size_fraction *= _cal_mult
                    logger.info(
                        f"[Claude] {symbol} size ×{_cal_mult:.2f} "
                        f"(calibrator: raw={_raw_conf:.2f} -> "
                        f"calibrated={_calibrated:.2f})")
        except Exception:
            pass
        # 2026-05-05 (Phase 27): historical-EV per-symbol multiplier from
        # _ev_per_symbol_multiplier (set earlier). 0.0 already short-circuited
        # at the EV check above (return False); here we apply 0.5 / 0.75
        # downsize tiers on graduated-negative-EV cases.
        if _ev_symbol_mult < 1.0:
            size_fraction *= _ev_symbol_mult
            logger.info(
                f"[Claude] {symbol} size ×{_ev_symbol_mult:.2f} "
                f"(Phase 27 EV: {_ev_symbol_reason})")

        # Phase 31 (2026-05-05): BTC cross-regime soft veto. Counter-trend
        # to BTC's macro direction gets ×0.6 — addresses the 04-07
        # disaster (5 shorts SL during BTC squeeze).
        try:
            _btc_mult, _btc_reason = self._btc_cross_regime_multiplier(side)
            if _btc_mult < 1.0:
                size_fraction *= _btc_mult
                logger.info(
                    f"[Claude] {symbol} size ×{_btc_mult:.2f} "
                    f"(Phase 31 BTC: {_btc_reason})")
        except Exception:
            pass

        # Phase 32 (2026-05-05): hour-of-day bleed multiplier. Pure data
        # check on (current_utc_hour, side) realized PnL over 30d.
        # Orthogonal dimension to Phase 27's (symbol, side).
        try:
            _hr_mult, _hr_reason = self._hour_of_day_multiplier(side)
            if _hr_mult < 1.0:
                size_fraction *= _hr_mult
                logger.info(
                    f"[Claude] {symbol} size ×{_hr_mult:.2f} "
                    f"(Phase 32 hour-EV: {_hr_reason})")
        except Exception:
            pass
        # 2026-05-04 (Phase 22): regime soft-multiplier. When regime gate
        # detected VOLATILE earlier in this function, _regime_size_mult was
        # set to RISK.regime_volatile_size_mult (default 0.4) instead of
        # hard-blocking. Apply it here as the FINAL multiplier in the
        # chain, AFTER Phase 17 ev_mult and Phase 18 cal_mult so we always
        # de-risk by the same fraction regardless of what came before.
        if _regime_size_mult != 1.0:
            size_fraction *= _regime_size_mult
            logger.info(
                f"[Claude] {symbol} size ×{_regime_size_mult:.2f} "
                f"(regime soft-mult — Phase 22)")

        # ── PORTFOLIO ES SOFT-CAP (2026-06-11) ────────────────────────
        # Supersedes the group-bucket corr taper with a real portfolio
        # risk measure: EWMA-cov parametric ES_97.5 of the open book +
        # candidate (signed legs — longs/shorts net). SOFT taper only
        # (floor 0.25) — never blocks. Fail-OPEN on any data gap/error.
        try:
            from config import ES_RISK as _ES_CFG
        except ImportError:
            _ES_CFG = {"enabled": False}
        if _ES_CFG.get("enabled", False) and total_bal > 0:
            try:
                _pr = getattr(self, "_portfolio_risk", None)
                if _pr is None:
                    from core.portfolio_risk import PortfolioRisk
                    _pr = PortfolioRisk(self.active_exchanges, _ES_CFG)
                    self._portfolio_risk = _pr
                _cand_lev = leverage if market_type == "futures" else 1
                _cand_usd = mtype_bal * size_fraction * _cand_lev
                _es = _pr.evaluate_candidate(
                    open_positions=self.tracker.get_open(),
                    cand_base=symbol.split("/")[0].upper(),
                    cand_side=side, cand_notional_usd=_cand_usd,
                    equity=total_bal)
                if _es.factor < 1.0:
                    size_fraction *= _es.factor
                    logger.info(
                        f"[PortfolioES] {symbol} size x{_es.factor:.2f} "
                        f"(ES proj ${_es.es_projected_usd:.0f} > budget "
                        f"${_es.budget_usd:.0f}, q={_ES_CFG.get('q', 0.975)})")
            except Exception as _ese:
                logger.debug(f"[PortfolioES] skipped ({_ese}) — fail-open 1.0x")
        notional = mtype_bal * size_fraction

        # ── VOL-TARGET RISK-BUDGET CEILING (2026-06-11) ──
        # Cap margin so worst-case loss at the planned SL is at most
        # per_trade_risk_pct of this pocket. min() with the multiplier-
        # chain notional: the chain still de-risks below budget; the
        # budget only trims the wide-SL tail. Uses the POST-Phase-28
        # sl_pct (the SL actually placed). Fail-open: any error or
        # degenerate sl leaves notional unchanged.
        try:
            from config import VOL_TARGET_SIZING as _VTS
        except ImportError:
            _VTS = {"enabled": False}
        if _VTS.get("enabled", False) and sl_pct > 0:
            try:
                from core.vol_target import risk_budget_margin as _rbm
                _lev_eff = leverage if market_type == "futures" else 1
                _budget = _rbm(mtype_bal, sl_pct, _lev_eff,
                               float(_VTS.get("per_trade_risk_pct", 0.005)))
                if _budget < notional:
                    logger.info(
                        f"[VolTarget] {symbol} margin ${notional:.2f} -> "
                        f"${_budget:.2f} (risk "
                        f"{float(_VTS.get('per_trade_risk_pct', 0.005)):.2%} "
                        f"of ${mtype_bal:.0f} @ SL {sl_pct:.2f}% x {_lev_eff}x)")
                    notional = _budget
            except Exception as _vte:
                logger.debug(f"[VolTarget] skipped ({_vte}) — chain notional kept")

        # 2026-04-24 (cost-floor logic) → 2026-04-28 (L99 ALL-IN):
        # min-notional floor reduced to the exchange-side minimum ($5).
        # User directive: maximum aggression — let small trades through;
        # at 99x leverage the cost floor doesn't bind. Restore the
        # cost-floor tiers (10/30/50) by reverting this hunk.
        min_notional = 5.0
        if notional < min_notional:
            logger.info(
                f"[Claude] Notional ${notional:.2f} < ${min_notional:.2f} minimum "
                f"(mtype_bal=${mtype_bal:.2f})")
            action["reject_reason"] = "notional_below_min"
            return False

        # ── Hard loss clamp — final safety rail ──
        # Rejects any trade where worst-case loss at SL exceeds
        # MAX_LOSS_PER_TRADE_PCT of the market-type balance. This is the
        # guardrail that makes 20x tiers survivable: a 20x AGGRESSIVE trade
        # with 0.8% SL on 2% of balance only risks 0.32% — well under the $2 cap.
        _clamp_lev = leverage if market_type == "futures" else 1
        if not self._within_loss_clamp(mtype_bal, notional, _clamp_lev, sl_pct / 100.0):
            action["reject_reason"] = "loss_clamp_exceeded"
            return False

        # Get current price for sizing
        try:
            ticker = exchange.fetch_ticker(trade_symbol, market_type)
            price = float(ticker.get("last", 0) or 0)
        except Exception as e:
            logger.warning(f"[Claude] fetch_ticker {trade_symbol}: {e}")
            action["reject_reason"] = "ticker_fetch_failed"
            return False
        if price <= 0:
            action["reject_reason"] = "price_invalid"
            return False

        # Compute SL/TP prices
        if side == "buy":
            stop_loss   = price * (1 - sl_pct / 100)
            take_profit = price * (1 + tp_pct / 100)
        else:
            stop_loss   = price * (1 + sl_pct / 100)
            take_profit = price * (1 - tp_pct / 100)
        # Phase 2b: tsmom has NO take-profit. tp_pct=0 would make take_profit==entry
        # (fires on the first tick); force a literal 0 so the TP triggers stay inert
        # (the wide stop + momentum-flip CLOSE are the only exits).
        if _is_tsmom_entry:
            take_profit = 0.0

        # CLAUDE.md §2 (R4): hard leverage cap. LEVERAGE_TIERS can hand back 3x
        # (STANDARD/SCALP), but the constitution caps active leverage at 2.5x and the
        # existing config futures_max_leverage was never actually binding. Clamp here so
        # no entry — on any signal path — exceeds it. tsmom already passes leverage=1.
        if market_type == "futures":
            from config import RISK as _RISK_LEV
            _lev_cap = _RISK_LEV.get("futures_max_leverage", 2)
            if leverage > _lev_cap:
                logger.info(f"[Risk] §2 leverage clamp: {symbol} {leverage}x -> {_lev_cap}x")
                leverage = _lev_cap

        # Compute size in base units
        if market_type == "futures":
            size = (notional * leverage) / price
        else:
            size = notional / price

        # CLAUDE.md §2 (R2): portfolio-exposure circuit breaker. Reject if this entry
        # would push total open GROSS NOTIONAL over MAX_PORTFOLIO_EXPOSURE_PCT of equity.
        # Nothing else enforced the constitution's 12% cap (only a position-COUNT limit).
        try:
            from config import MAX_PORTFOLIO_EXPOSURE_PCT as _MAX_EXP
            from core.risk_manager import exposure_breached as _exp_breached
            _equity = _deployable_total(self._balances)
            if _exp_breached(self.tracker.get_open(), size * price, _equity, _MAX_EXP):
                logger.warning(
                    f"[Risk] §2 EXPOSURE CAP: {symbol} would exceed {_MAX_EXP:g}% of "
                    f"${_equity:.0f} open exposure — blocked")
                action["reject_reason"] = "portfolio_exposure_cap"
                return False
        except Exception as _ee:
            # A2 audit: this is a HARD risk rail — a broken exposure check must
            # BLOCK the entry (fail-closed), not silently fall through to open.
            logger.warning(f"[Risk] exposure-cap check FAILED, blocking entry: {_ee}")
            action["reject_reason"] = "exposure_cap_error"
            return False

        # Pre-check: will this size survive exchange rounding?
        # BTC at $83k with step=0.001 needs min $83 notional (or $16.6 at 5x).
        # Skip early instead of wasting API calls on doomed orders.
        try:
            step = exchange.get_amount_precision(trade_symbol)
            if step > 0 and size < step:
                # MIN-NOTIONAL FLOOR (2026-07-10, UNBLOCK directive): try to
                # floor back UP to the exchange minimum BEFORE dust-skipping.
                # The helper re-runs the loss clamp + §2 exposure cap at the
                # floored size and refuses unless the pre-multiplier base
                # sizing could afford it. Flag off -> 0.0 -> skip unchanged.
                _floor_notional = self._min_notional_floor(
                    symbol, step, price, leverage, market_type,
                    notional, _pre_ev_notional, mtype_bal, sl_pct)
                if _floor_notional > 0:
                    notional = _floor_notional
                    size = step
            if step > 0 and size < step:
                min_notional = step * price / max(leverage, 1)
                # Phase 23 (2026-05-04): set 30min cooldown to stop
                # re-pitching the same symbol every 5min when the
                # multiplier chain crushes it below exchange step lot.
                import time as _t_p23d
                self._dust_skip_cooldown[symbol] = _t_p23d.time() + 1800
                logger.info(
                    f"[Claude] {symbol}: size {size:.8f} < step {step} "
                    f"(need ${min_notional:.0f} at {leverage}x, "
                    f"have ${notional:.0f}) — skip + 30min cooldown")
                action["reject_reason"] = "size_below_step"
                return False
        except Exception:
            pass

        # Set leverage (LIVE only — 2026-05-31: was an ungated live-account
        # write firing in PAPER too; mirrors order_manager open_position:658).
        if market_type == "futures" and leverage > 1 and not DRY_RUN:
            try:
                exchange.set_leverage(trade_symbol, leverage)
            except Exception as e:
                logger.debug(f"[Claude] set_leverage: {e}")

        logger.info(
            f"[Claude] EXECUTING OPEN: {trade_symbol} {side.upper()} on {ex_name} "
            f"({market_type}) {leverage}x | size={size:.6g} notional=${notional:.2f} "
            f"SL={stop_loss:.6g} TP={take_profit:.6g} conf={confidence:.0%}")

        try:
            _cid = action.get("candidate_id")
            pos = self.order_mgr.open_position(
                exchange, trade_symbol, side, market_type,
                # Phase 2b: thread the signal source into the persisted
                # Position.strategy tag so exit gating can identify a tsmom
                # position at monitor time (rides positions.json + warehouse).
                strategy=action.get("source") or "claude_portfolio",
                size=size, price=price,
                sl=stop_loss, tp=take_profit,
                leverage=leverage,
                candidate_id=_cid if (_cid or 0) > 0 else None,
                mcp_score=action.get("mcp_score"),
                model_version=action.get("model_version"),
                decision_id=action.get("decision_id"),
            )
            if pos is None:
                logger.info("[Claude] open_position returned None — rejected by order manager")
                _omr = getattr(self.order_mgr, "last_open_reject", None)
                action["reject_reason"] = f"order_manager:{_omr or 'unspecified'}"
                return False
            # ACCURACY band marker (2026-07-10 time-exit leak fix): stamp
            # entries whose TP the band rewrote so the position monitor
            # suppresses STALE/AGE_LIMIT/scalp time exits inside
            # ACCURACY_TARGET_MODE["max_hold_hours"] and first-touch SL/TP
            # governs (order_manager._accuracy_band_hold_active; the tp-dist
            # < sl-dist geometry fallback there covers restarts, since this
            # dynamic attr does not persist to positions.json).
            if _acc_mode_on:
                pos._accuracy_band = True
            # Warehouse record_trade_open now happens inside open_position
            # (before SL placement) so fail-closed paths don't lose the row.
            # CLAUDE.md §4: structured markdown journal of the action (best-effort).
            try:
                from core import journal as _journal
                _journal.log_action(
                    "OPEN", trade_symbol, side,
                    f"{leverage}x size={size:.6g} notional=${notional:.0f} "
                    f"SL={sl_pct:.2f}% conf={confidence:.0%} src={action.get('source', '')}")
            except Exception:
                pass
            return True
        except Exception as e:
            logger.error(f"[Claude] open_position failed: {e}")
            # Spec §12 hardening: 3 rejections in 10 minutes on one symbol
            # pauses that symbol for 2 hours. Harmless if risk_manager lacks
            # the method (old versions).
            try:
                if self.risk and hasattr(
                    self.risk, "note_order_rejection"
                ):
                    self.risk.note_order_rejection(symbol, str(e))
            except Exception:
                pass
            action["reject_reason"] = "open_position_exception"
            return False

    def _execute_close(self, action: dict) -> bool:
        """Find and close a position by ID. Returns True if closed."""
        # Spec §4: OBSERVATION mode must not send any live orders
        from config import OPERATING_MODE as _close_mode
        if _close_mode == "OBSERVATION":
            logger.info("[Mode] OBSERVATION — close blocked (no live orders)")
            return False

        position_id = action.get("position_id", "")
        source = str(action.get("source", "claude") or "claude").lower()
        reason = action.get("reason", "claude_portfolio_close")
        # Canonicalize the LLM's free-text rationale to a clean machine label so
        # the warehouse exit_reason stays a usable GROUP BY key (audit H8). The
        # raw prose is preserved in the log line + exit_decision_id below.
        canon_reason = _canonical_exit_reason(reason, source=source)

        if not position_id:
            logger.warning("[Claude] CLOSE action missing position_id")
            return False

        # Find position by ID (full or prefix match)
        target = None
        for p in self.tracker.get_open():
            if p.id == position_id or p.id.startswith(position_id):
                target = p
                break

        if not target:
            logger.info(f"[Claude] Position {position_id[:8]} not found — may be closed already")
            return False

        # A4 (audit 2026-06-21): tsmom positions own their own exit (momentum
        # flip / disaster stop, Phase 2b). The SL/TP loop and the 30s monitor
        # already enforce this (order_manager.check_sl_tp / _run_mcp_position_
        # monitor); _execute_close was the one path missing it, so a discretionary
        # portfolio-cycle CLOSE could market-close a tsmom hold. ALWAYS-ON invariant.
        from core.tsmom_signal import (
            is_tsmom_action as _is_tsmom_action,
        )
        from core.tsmom_signal import (
            is_tsmom_position as _is_tsmom_pos,
        )
        if _is_tsmom_pos(target) and not _is_tsmom_action(action):
            logger.info(
                f"[Claude] portfolio CLOSE skipped — tsmom owns its exit: "
                f"{target.symbol} {target.id[:8]}")
            return False

        # Deep-breakout lane positions (2026-07-11) likewise own their exit
        # (2.2xATR SL / 3R TP / 126-bar max-hold via the lane tick + the §2 8%
        # guardian). A discretionary portfolio CLOSE would clip the researched
        # 3R geometry. The lane itself closes via order_manager.close_position
        # directly, never through this path. ALWAYS-ON invariant.
        from core.deep_breakout_lane import (
            is_deep_breakout_position as _is_db_pos,
        )
        if _is_db_pos(target):
            logger.info(
                f"[Claude] portfolio CLOSE skipped — deep_breakout owns its "
                f"exit: {target.symbol} {target.id[:8]}")
            return False

        try:
            from core.machine_signal import (
                is_machine_action as _is_machine_action,
            )
            from core.machine_signal import (
                is_machine_position as _is_machine_pos,
            )
        except Exception:
            _is_machine_action = lambda _action: False
            _is_machine_pos = lambda _pos: False
        if _is_machine_pos(target) and not _is_machine_action(action):
            logger.info(
                f"[Claude] portfolio CLOSE skipped - machine owns its exit: "
                f"{target.symbol} {target.id[:8]}")
            return False

        # Find exchange client
        exchange = None
        for ex_name, ex in self.active_exchanges.items():
            if ex_name == target.exchange.lower() or ex_name in target.exchange.lower():
                exchange = ex
                break

        if not exchange:
            logger.warning(f"[Claude] Exchange for {target.exchange} not connected")
            return False

        # A4 (audit 2026-06-21): flag-gated discretionary-close guard. When
        # enabled, refuse a Claude-sourced CLOSE of a NON-disaster position so
        # SL/TP/trailing own the exit (mirrors the 2026-04-24 monitor-CLOSE
        # suppression). A genuine catastrophic loss (net <= -8% price, spec §2
        # disaster stop) still passes (escape hatch). Default-OFF => unchanged.
        # Algo-fallback CLOSE (source != "claude") is exempt — it only fires on a
        # real loss-cut.
        from config import PORTFOLIO_DISCRETIONARY_CLOSE_GUARD_ENABLED as _pdcg
        if _pdcg and action.get("source") == "claude":
            try:
                _tk = exchange.fetch_ticker(target.symbol, target.market_type)
                _last = float(_tk.get("last") or _tk.get("close") or 0)
                if _last > 0:
                    _, _net_pct, _ = self.order_mgr._net_pnl_at_price(target, _last)
                    if _net_pct > -8.0:   # not a disaster -> let SL/TP own it
                        logger.info(
                            f"[Claude] portfolio CLOSE suppressed (discretionary, "
                            f"non-disaster net={_net_pct:+.2f}%) — SL/TP/trailing own "
                            f"exits (2026-04-24 monitor-policy parity): "
                            f"{target.symbol} {target.id[:8]}")
                        return False
            except Exception as _e:
                logger.debug(f"[Claude] discretionary-close guard skipped: {_e}")

        logger.info(
            f"[Claude] EXECUTING CLOSE: {target.symbol} {target.side} "
            f"on {target.exchange} | reason={canon_reason} | rationale={reason[:80]}")

        try:
            # Provenance: thread the CLOSE decision's id so the warehouse row
            # receives exit_decision_id via _finalize_close. The canonical
            # reason is what lands in exit_reason; the prose stays in the log.
            self.order_mgr.close_position(
                exchange, target, canon_reason,
                decision_id=action.get("decision_id"))
            return True
        except Exception as e:
            logger.error(f"[Claude] close_position failed: {e}")
            return False

    # ── Scheduled tasks ───────────────────────────────────────────────

    def _check_all_sl_tp(self):
        """Check SL/TP for all open positions — parallel across exchanges."""
        # Maker-first (2026-07-11): pending virtual intents are entries WITHOUT
        # positions — on an empty book the old zero-open early-return starved
        # the resolver forever (INJ/ARB intents hung 2h past their 45s timeout;
        # the entries were silently lost). Run the monitor whenever intents
        # are pending, even with zero open positions.
        if (self.tracker.count_open() == 0
                and not getattr(self.order_mgr, "_pending_maker", None)):
            return

        def _check_one(ex_name, exchange, mtype):
            try:
                self.order_mgr.check_sl_tp(exchange, mtype)
            except Exception as e:
                logger.debug(f"[Engine] SL/TP check {ex_name}/{mtype}: {e}")

        with ThreadPoolExecutor(max_workers=8) as pool:
            futs = []
            for ex_name, exchange in self.active_exchanges.items():
                for mtype in ("spot", "futures"):
                    futs.append(pool.submit(_check_one, ex_name, exchange, mtype))
            for f in as_completed(futs):
                f.result()  # Propagate any unhandled exception

    def _sltp_monitor_loop(self, stop_event: threading.Event):
        """Dedicated thread: monitors SL/TP every 10 seconds, never blocked by scans."""
        while not stop_event.is_set():
            try:
                self._check_all_sl_tp()
            except Exception as e:
                logger.debug(f"[Engine] SL/TP monitor: {e}")
            stop_event.wait(10)  # Check every 10 seconds

    def _heartbeat_portfolio_es(self):
        """Live ES snapshot of the open book for the dashboard.
        Fail-quiet → None (2026-06-11 Portfolio ES soft-cap)."""
        try:
            from config import ES_RISK as _cfg
            if not _cfg.get("enabled", False):
                return None
            _pr = getattr(self, "_portfolio_risk", None)
            if _pr is None:
                from core.portfolio_risk import PortfolioRisk
                _pr = PortfolioRisk(self.active_exchanges, _cfg)
                self._portfolio_risk = _pr
            _eq = _deployable_total(self._balances)
            _pr.evaluate_book(self.tracker.get_open(), _eq)
            return _pr.last_snapshot()
        except Exception:
            return None

    def _write_heartbeat(self):
        """Write heartbeat file for external monitoring."""
        try:
            import os

            import psutil
            process = psutil.Process(os.getpid())
            mem_mb = process.memory_info().rss / 1024 / 1024
        except Exception:
            mem_mb = 0

        # Find last trade time from tracker
        last_trade_time = None
        try:
            closed = self.tracker.get_closed_positions()
            if closed:
                last_trade_time = max(
                    p.close_time for p in closed if getattr(p, "close_time", None)
                ) if any(getattr(p, "close_time", None) for p in closed) else None
        except Exception:
            pass

        heartbeat = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": int(time.time() - self._start_time),
            "cycle_count": self._cycle,
            "open_positions": self.tracker.count_open(),
            "per_exchange_positions": {
                ex_name: self.tracker.count_open(exchange=ex.name)
                for ex_name, ex in self.active_exchanges.items()
            },
            # 2026-06-11: live portfolio ES vs budget (None = unavailable)
            "portfolio_es": self._heartbeat_portfolio_es(),
            "last_trade_time": last_trade_time,
            "active_exchanges": list(self.active_exchanges.keys()),
            "halted_exchanges": list(self._exchange_halted),
            "api_latency_ms": {k: round(v, 0) for k, v in self._api_latency.items()},
            # C8 (2026-07-08): per-venue clock offset (ms, NTP-style sample);
            # null = venue lacks fetch_time or the sample failed this cycle.
            "clock_drift_ms": {
                k: (round(v, 1) if isinstance(v, (int, float)) else None)
                for k, v in getattr(self, "_clock_drift_ms", {}).items()
            },
            "is_halted": self.risk.is_halted,
            "halt_reason": self.risk.halt_reason if self.risk.is_halted else None,
            "daily_pnl": getattr(self.risk, "_daily_pnl", 0),
            "spot_manager_active": self.spot_manager is not None,
            "capital_allocator_active": self.capital_allocator is not None,
            "memory_mb": round(mem_mb, 1),
        }
        try:
            Path("data/heartbeat.json").write_text(
                json.dumps(heartbeat, indent=2), encoding="utf-8")
        except Exception:
            pass

    def _try_reconnect(self, ex_name: str, exchange) -> None:
        """Rebuild ccxt client + clear halt if rebuild succeeds.
        Used both at first-halt transition and during periodic retries."""
        try:
            exchange._init_exchange()
            if getattr(exchange, '_connected', False):
                logger.info(f"[Health] {ex_name} reconnected")
                self._consecutive_api_fails[ex_name] = 0
                self._exchange_halted.discard(ex_name)
                if ex_name not in self.active_exchanges:
                    self.active_exchanges[ex_name] = exchange
                    logger.info(
                        f"[Health] {ex_name} re-added to active_exchanges")
                try:
                    self.notifier.alert(
                        f"{ex_name.upper()} reconnected successfully after outage.")
                except Exception:
                    pass
        except Exception as e:
            logger.debug(f"[Health] {ex_name} reconnect attempt failed: {e}")

    def _check_exchange_health(self):
        """Verify exchanges are reachable + measure latency.
        Auto-halt trading on exchange after 3 consecutive failures.
        Auto-resume when exchange recovers."""
        self._last_health_check = time.time()
        for ex_name, exchange in list(self.active_exchanges.items()):
            try:
                t0 = time.time()
                ticker = exchange.fetch_ticker("BTC/USDT", "spot")
                latency_ms = (time.time() - t0) * 1000
                self._api_latency[ex_name] = latency_ms
                # Accept any price field — some exchanges populate `close`
                # or bid/ask but not `last` on every ticker
                price = None
                if ticker:
                    price = (ticker.get("last") or ticker.get("close")
                             or ticker.get("bid") or ticker.get("ask"))
                if price:
                    self._consecutive_api_fails.get(ex_name, 0)
                    self._consecutive_api_fails[ex_name] = 0
                    # C8 (2026-07-08): sample venue clock drift each healthy
                    # cycle. None = venue lacks fetch_time / call failed —
                    # never a health failure. Sustained drift (2 consecutive
                    # samples over threshold) warns, mirroring the
                    # high-latency counter pattern; the health_watchdog
                    # additionally edge-alerts off the stored map.
                    if not hasattr(self, "_clock_drift_ms"):
                        self._clock_drift_ms = {}
                    _drift = sample_clock_drift_ms(exchange)
                    self._clock_drift_ms[ex_name] = _drift
                    _drift_key = f"{ex_name}_clock_drift"
                    try:
                        from config import CLOCK_DRIFT_ALERT_MS as _drift_thr
                    except ImportError:
                        _drift_thr = 500
                    if _drift is not None and abs(_drift) > _drift_thr:
                        _dcnt = self._consecutive_api_fails.get(_drift_key, 0) + 1
                        self._consecutive_api_fails[_drift_key] = _dcnt
                        if _dcnt >= 2:
                            logger.warning(
                                f"[Health] {ex_name} CLOCK DRIFT "
                                f"{_drift:+.0f}ms (>{_drift_thr}ms) for "
                                f"{_dcnt} consecutive samples — signed "
                                f"requests at risk; check NTP/w32tm sync")
                    else:
                        self._consecutive_api_fails.pop(_drift_key, None)
                    # Auto-resume if previously halted
                    if ex_name in self._exchange_halted:
                        self._exchange_halted.discard(ex_name)
                        logger.info(
                            f"[Health] {ex_name} RECOVERED — resuming trading "
                            f"(latency={latency_ms:.0f}ms)")
                        self.notifier.alert(
                            f"{ex_name.upper()} recovered after outage — "
                            f"trading resumed (latency={latency_ms:.0f}ms)")
                    elif latency_ms > 5000:
                        logger.warning(
                            f"[Health] {ex_name}: HIGH LATENCY {latency_ms:.0f}ms")
                        high_lat_key = f"{ex_name}_high_latency"
                        cnt = self._consecutive_api_fails.get(high_lat_key, 0) + 1
                        self._consecutive_api_fails[high_lat_key] = cnt
                        if cnt >= 3:
                            logger.warning(
                                f"[Health] {ex_name} DEGRADED — latency >5s "
                                f"for {cnt} consecutive checks")
                            self.notifier.alert(
                                f"{ex_name.upper()} degraded: latency >5s "
                                f"for {cnt} checks — consider pausing")
                    else:
                        self._consecutive_api_fails.pop(
                            f"{ex_name}_high_latency", None)
                else:
                    keys = list(ticker.keys()) if ticker else []
                    raise Exception(f"Empty ticker (keys={keys[:5]})")
            except Exception as e:
                fails = self._consecutive_api_fails.get(ex_name, 0) + 1
                self._consecutive_api_fails[ex_name] = fails
                self._api_latency[ex_name] = -1
                # Area 2 (2026-05-20): first-attempt failure is usually a
                # transient API blip — the existing retry path handles it.
                # Only escalate to WARNING on the 2nd+ failure.
                _level_fn = logger.debug if fails == 1 else logger.warning
                _level_fn(
                    f"[Health] {ex_name} health check FAILED "
                    f"(attempt {fails}): {e}")
                if fails >= 3 and ex_name not in self._exchange_halted:
                    self._exchange_halted.add(ex_name)
                    open_on_ex = self.tracker.count_open(exchange=exchange.name)
                    logger.error(
                        f"[Health] {ex_name} HALTED — {fails} consecutive failures. "
                        f"No new trades will be placed on {ex_name} until recovered.")
                    self.notifier.error(
                        f"EXCHANGE HALTED: {ex_name.upper()} unreachable for "
                        f"{fails} consecutive checks.\n"
                        f"Open positions on {ex_name}: {open_on_ex}\n"
                        f"Trading HALTED on this exchange. "
                        f"Will auto-resume when API recovers.\n"
                        f"Attempting auto-reconnect...")
                    self._try_reconnect(ex_name, exchange)
                # 2026-05-03: prior code only retried once at fails==3.
                # If a stale ccxt session keeps returning empty tickers,
                # the bot would stay halted forever (observed: 519+ fails
                # over 8.5h on a stuck process). Retry every 5 fails so
                # the client gets recycled periodically without a full
                # bot restart.
                elif (
                    ex_name in self._exchange_halted
                    and fails % 5 == 0
                ):
                    logger.info(
                        f"[Health] {ex_name} stale-halt retry "
                        f"(fail #{fails}, every 5 attempts)")
                    self._try_reconnect(ex_name, exchange)
                # 5+ fails: close losing positions to protect capital
                if fails >= 5:
                    try:
                        positions = self.tracker.get_open(exchange=exchange.name)
                        for pos in positions:
                            if getattr(pos, "unrealized_pnl", 0) < 0:
                                logger.warning(
                                    f"[Health] Force-closing losing position "
                                    f"{pos.symbol} on {ex_name} (5+ API fails)")
                                self.order_mgr.close_position(
                                    exchange, pos, reason="exchange_outage_5_fails")
                    except Exception as close_err:
                        logger.error(f"[Health] Force-close error: {close_err}")

    def is_exchange_halted(self, exchange_name: str) -> bool:
        """Check if an exchange is currently halted due to health failures."""
        return exchange_name.lower() in self._exchange_halted

    def _sync_positions(self):
        """Periodically verify tracked LIVE positions still exist on exchange."""
        try:
            imported = self.tracker.sync_with_exchanges(self.active_exchanges)
            if imported:
                self._protect_imported_positions(imported)
        except Exception as e:
            logger.debug(f"[Engine] Position sync: {e}")

    def _replace_exchange_sl(self, exchange, pos, new_sl: float) -> bool:
        """B7-P2 wrapper: serialize this TIGHTEN/BREAKEVEN SL re-place with the
        OrderManager's close/SL-mutation on the same position id, via the ONE
        shared per-id RLock (self.order_mgr._position_lock). Default-OFF; timeout
        backstop so it can never freeze the 30s monitor. RLock-safe: this path
        re-enters om.close_position via the fail-closed _place_exchange_sl_tp."""
        om = self.order_mgr
        if not getattr(om, "_per_pos_lock_enabled", False):
            return self._replace_exchange_sl_impl(exchange, pos, new_sl)
        lk = om._position_lock(pos.id)
        acquired = lk.acquire(timeout=om._pos_lock_timeout)
        if not acquired:
            logger.error(
                f"[Lock] engine _replace_exchange_sl _position_lock TIMEOUT for "
                f"{pos.id} after 5s — proceeding WITHOUT serialization")
        try:
            return self._replace_exchange_sl_impl(exchange, pos, new_sl)
        finally:
            if acquired:
                lk.release()

    def _replace_exchange_sl_impl(self, exchange, pos, new_sl: float) -> bool:
        """Cancel the existing SL conditional order on the exchange and place
        a new one at `new_sl`. Used by TIGHTEN/BREAKEVEN actions.

        Returns True iff the replace actually succeeded — callers should
        only mutate `pos.stop_loss` on True so in-memory SL stays consistent
        with the exchange.

        2026-04-25 (post-audit): pre-flight check added. BREAKEVEN moves
        targeted entry+fees and Bitget rejected the new SL whenever mark
        price had pulled back through that level (code 40917 "Stop price
        for long positions please < mark price"). Cancel-then-place had
        already removed the working SL, so the fail-closed policy market-
        closed otherwise-healthy winning trades. The check now refuses
        replaces that would be rejected, keeping the existing SL intact.

        2026-04-16 (post-audit): Previously these actions only mutated
        `pos.stop_loss` in memory. The exchange kept running the wider SL,
        and because `_exchange_sl=True` silences local SL monitoring, the
        tightened value was never actually enforced anywhere.
        """
        if not pos or not new_sl or new_sl <= 0:
            return False
        if pos.market_type != "futures":
            return False  # spot has no exchange-side SL to replace

        # Pre-flight: refuse SL placements that the exchange would reject
        # for being on the wrong side of current price. For longs the SL
        # must sit BELOW current price; for shorts ABOVE. Otherwise the
        # cancel-then-place cycle below would strip the working SL and
        # the fail-closed policy in _place_exchange_sl_tp would market-
        # close the position. Skip the move when the new level is invalid;
        # the next monitor cycle will try again with a refreshed price.
        try:
            tkr = exchange.fetch_ticker(pos.symbol, pos.market_type)
            last = float((tkr or {}).get("last") or (tkr or {}).get("close") or 0)
        except Exception as e:
            logger.debug(f"[Replace-SL] mark check unavailable for {pos.symbol}: {e}")
            last = 0.0
        if last > 0:
            side = (pos.side or "").lower()
            if side == "buy" and new_sl >= last:
                logger.info(
                    f"[Replace-SL] SKIP {pos.symbol} BUY: target SL {new_sl:.6g} "
                    f">= last {last:.6g} (would be exchange-rejected); keeping current SL")
                return False
            if side == "sell" and new_sl <= last:
                logger.info(
                    f"[Replace-SL] SKIP {pos.symbol} SELL: target SL {new_sl:.6g} "
                    f"<= last {last:.6g} (would be exchange-rejected); keeping current SL")
                return False

        # 2026-05-31 (CRITICAL fix): PAPER must never write to the live venue.
        # A simulated position has no real order to cancel/replace; issuing a
        # real cancel_all_orders + SL placement here was leaking onto live
        # accounts (Bitget 43023 -> fail-closed paper winners; Binance/Bybit
        # accepted -> phantom resting orders). Mirror the open_position gate
        # (order_manager.py:1045): clear the exchange-SL flags so the dry_run
        # check_sl_tp wick-trigger enforces the moved level, and report success
        # so the caller updates pos.stop_loss in-memory only.
        if DRY_RUN:
            pos._exchange_sl = False
            pos._exchange_tp = False
            return True

        # Cancel existing conditional orders for this symbol. We cancel
        # ALL conditional orders on the symbol because we did not store
        # the specific SL order ID at placement time — the symbol is a
        # narrow enough scope for one position.
        try:
            if hasattr(exchange, "cancel_all_orders"):
                exchange.cancel_all_orders(pos.symbol, pos.market_type)
            else:
                # ccxt-level fallback
                exchange.exchange.cancel_all_orders(pos.symbol)
        except Exception as e:
            logger.warning(
                f"[Replace-SL] cancel_all_orders failed for {pos.symbol}: "
                f"{str(e)[:120]} — proceeding with replacement")

        # Clear flags so a placement failure falls back to local monitoring
        pos._exchange_sl = False
        pos._exchange_tp = False

        # Re-place both SL and TP at current tracker values
        try:
            self.order_mgr._place_exchange_sl_tp(
                exchange, pos, new_sl, pos.take_profit,
                pos.side, pos.symbol, pos.size, pos.market_type)
            return True
        except Exception as e:
            logger.error(
                f"[Replace-SL] re-place failed for {pos.symbol}: {str(e)[:150]}")
            return False

    def _protect_imported_positions(self, positions: list) -> None:
        """Compute ATR-based SL/TP for manually-imported positions and place
        them on the exchange.  Falls back to liquidation-price fallback if
        OHLCV is unavailable (leaves the existing tracker stop_loss intact).

        2026-04-16 follow-up: imported positions arrive with stop_loss=liq
        (placeholder) and take_profit=0. This method refines them to
        ATR*1.5 / 2.5:1 RR using the risk manager, then asks the order
        manager to place protective orders on the exchange.
        """
        import pandas as pd

        from utils.indicators import atr as _atr

        for pos in positions:
            try:
                exchange = self.active_exchanges.get(pos.exchange.lower())
                if not exchange:
                    logger.warning(
                        f"[Protect] No client for {pos.exchange} — "
                        f"cannot place SL for imported {pos.symbol}")
                    continue

                # 2026-04-20: stale-list guard. The caller passes a snapshot
                # of positions, but concurrent check_sl_tp / close flows may
                # have already closed the position. Placing SL on a flat
                # symbol cascades into a naked reverse via the close-retry
                # path. Re-verify against the exchange and tracker before
                # proceeding.
                if not getattr(pos, "is_open", True):
                    continue
                try:
                    _live = exchange.fetch_positions([pos.symbol]) or []
                    _live_sz = 0.0
                    for _p in _live:
                        _live_sz = abs(float(_p.get("contracts") or _p.get("contractSize") or 0))
                        if _live_sz > 0:
                            break
                    if _live_sz <= 0:
                        logger.info(
                            f"[Protect] {pos.symbol} flat on exchange — "
                            f"skipping SL placement (stale-list guard)")
                        continue
                except Exception as _fe:
                    logger.debug(
                        f"[Protect] live-size check failed for {pos.symbol}: "
                        f"{str(_fe)[:120]} — proceeding")

                # Fetch 1h OHLCV for ATR(14)
                try:
                    raw = exchange.fetch_ohlcv(pos.symbol, "1h", 100, "futures")
                except Exception as e:
                    logger.warning(
                        f"[Protect] OHLCV fetch failed for {pos.symbol}: "
                        f"{str(e)[:120]} — SL left at liquidation fallback")
                    continue
                if not raw or len(raw) < 20:
                    logger.warning(
                        f"[Protect] Insufficient OHLCV for {pos.symbol} "
                        f"({len(raw) if raw else 0} bars) — SL left at fallback")
                    continue

                df = pd.DataFrame(raw, columns=["ts", "o", "h", "l", "c", "v"])
                atr_val = float(_atr(df["h"], df["l"], df["c"], 14).iloc[-1] or 0)
                if atr_val <= 0:
                    logger.warning(
                        f"[Protect] ATR invalid for {pos.symbol} — fallback")
                    continue

                # Compute SL/TP via risk manager. ATR*1.5 SL, 2.5:1 RR.
                sl, tp = self.risk.get_sl_tp(
                    entry=pos.entry_price,
                    side=pos.side,
                    atr=atr_val,
                    atr_sl_mult=1.5,
                    atr_tp_mult=3.75,  # 1.5 * 2.5 = 2.5:1 RR
                    leverage=pos.leverage,
                )

                # Update tracker state
                pos.stop_loss = sl
                pos.take_profit = tp

                # Place on exchange
                try:
                    self.order_mgr._place_exchange_sl_tp(
                        exchange, pos, sl, tp, pos.side,
                        pos.symbol, pos.size, "futures")
                    logger.info(
                        f"[Protect] {pos.symbol} {pos.side.upper()} "
                        f"imported SL={sl:.4f} TP={tp:.4f} (ATR={atr_val:.4f})")
                except Exception as e:
                    logger.error(
                        f"[Protect] SL placement failed for {pos.symbol}: "
                        f"{str(e)[:150]}")

            except Exception as e:
                logger.error(
                    f"[Protect] Unexpected error protecting {getattr(pos, 'symbol', '?')}: "
                    f"{str(e)[:150]}")

        # Persist the updated SL/TP
        try:
            self.tracker._save()
        except Exception:
            pass

    def _fetch_news(self):
        try:
            self.news.scan(force=True)
        except Exception as e:
            logger.debug(f"[Engine] News fetch error: {e}")

    def _run_dca(self):
        """Run DCA strategy on top coins across all exchanges.
        Gates (in order):
          1. Static BLACKLIST_HARD — never accumulate a repeat loser
          2. AutoMutator dynamic blacklist — post-mortem-driven
          3. MCP Brain — don't DCA-buy coins MCP says to SELL
        BTC macro trend is intentionally NOT checked: DCA is meant to
        accumulate on dips, so bear macro is the *desired* environment.
        """
        from config import BLACKLIST_HARD
        dca = self.pool.get("dca_spot")
        if not dca:
            return
        dca_coins = ["BTC/USDT", "ETH/USDT", "SOL/USDT", "BNB/USDT"]
        dyn_bl = (self.auto_mutator.get_effective_blacklist()
                  if self.auto_mutator else set())

        for ex_name, exchange in self.active_exchanges.items():
            for symbol in dca_coins:
                # Normalize to futures key for blacklist check (futures keys
                # are authoritative: repeat losses on futures also gate spot
                # accumulation of the same base asset).
                sym_fut_key = f"{symbol}:USDT"
                if (symbol in BLACKLIST_HARD or sym_fut_key in BLACKLIST_HARD
                    or symbol in dyn_bl or sym_fut_key in dyn_bl):
                    logger.debug(
                        f"[DCA] {symbol}: BLOCKED by blacklist — skipping")
                    continue

                # MCP Brain gate: don't DCA-buy coins MCP says to SELL
                if self.mcp_brain and self.mcp_brain.is_enabled:
                    base = symbol.split("/")[0]
                    mcp_dec = self.mcp_brain.last_decisions().get(base, {})
                    if mcp_dec.get("action") == "SELL":
                        logger.debug(
                            f"[DCA] {symbol}: MCP says SELL — skipping DCA buy")
                        continue
                try:
                    dca.run(exchange, symbol)
                    # 2026-05-03: track today's DCA buys so SpotPortfolioManager
                    # can avoid SELLing the same asset DCA just bought.
                    # Same-day opposing-order risk surfaced by Ruflo strategy review.
                    if not hasattr(self, "_dca_buys_today"):
                        self._dca_buys_today: dict = {}
                    base = symbol.split("/")[0]  # "BTC/USDT" → "BTC"
                    self._dca_buys_today[base] = time.time()
                except Exception as e:
                    logger.debug(f"[DCA] {symbol} on {ex_name}: {e}")

    def _run_rebalance(self):
        """Run portfolio rebalancing once per day."""
        rebal = self.pool.get("rebalance")
        if not rebal:
            return
        for ex_name, exchange in self.active_exchanges.items():
            try:
                rebal.run(exchange)
            except Exception as e:
                logger.debug(f"[Rebalance] {ex_name}: {e}")

    def _run_spot_evaluation(self):
        """Run spot portfolio evaluation cycle (every 30 min).

        2026-05-01: when SPOT_PORTFOLIO['recommendation_only'] is False,
        execute SELL/SCALE_OUT actions. SPOT-PROTECT-V1 emits these only
        on peak-drawdown >= 25% (half) / 40% (full). Recommendation-only
        was the 2026-04-14 learning-first pivot default; flipping to
        execute requires explicit user authorization.
        """
        if not self.spot_manager:
            return
        try:
            actions = self.spot_manager.run_cycle()
            if not actions:
                return
            logger.info(f"[SpotMgr] Cycle: {len(actions)} actions "
                        f"({', '.join(a.get('action','?') for a in actions)})")

            from config import SPOT_PORTFOLIO as _SP
            if _SP.get("recommendation_only", True):
                # Recommendation-only mode: spot_manager already wrote
                # to data/spot_recommendations.jsonl; nothing to execute.
                return

            for a in actions:
                # 2026-05-03: Multi-strategy coordination gate.
                # If DCA bought this asset within the last 24h, skip
                # SELL/SCALE_OUT — opposing-order risk surfaced by
                # the Ruflo strategy review.
                act = a.get("action", "")
                sym = a.get("symbol", "")
                base = sym.split("/")[0] if sym else ""
                dca_buys = getattr(self, "_dca_buys_today", {}) or {}
                last_dca = dca_buys.get(base, 0)
                if act in ("SELL", "SCALE_OUT") and last_dca > 0:
                    age_h = (time.time() - last_dca) / 3600
                    if age_h < 24:
                        logger.warning(
                            f"[SpotMgr] BLOCKED {act} {sym}: DCA bought "
                            f"{base} {age_h:.1f}h ago — opposing-order skip")
                        continue
                self._execute_spot_action(a)
        except Exception as e:
            logger.error(f"[SpotMgr] Cycle error: {e}")

    def _execute_spot_action(self, action: dict) -> None:
        """Execute one SPOT-PROTECT-V1 action (SELL full or SCALE_OUT half).

        Safety guards:
          * Only handles SELL and SCALE_OUT (no buys, no rotations)
          * Verifies free balance from the exchange before sizing
          * Skips if free balance is dust (< $5)
          * Market-sells only — no limit orders, simplest path
          * Wraps failures so one symbol's error doesn't stall the cycle
        """
        try:
            kind = action.get("action")
            if kind not in ("SELL", "SCALE_OUT"):
                return  # ignore HOLD / unknown actions defensively
            # 2026-05-31: PAPER must not touch REAL spot holdings. This path
            # market-sells the owner's actual coins; gate it off in DRY_RUN so
            # paper-research mode never trades real money. (Reversible.)
            if DRY_RUN:
                logger.info(
                    f"[SpotMgr] [DRY] would {kind} {action.get('exchange')}:{action.get('coin')} "
                    f"— skipped (PAPER: no real spot trades)")
                return

            ex_name = action.get("exchange")
            coin    = action.get("coin")
            symbol  = action.get("symbol") or f"{coin}/USDT"
            reason  = action.get("reason", "")

            exchange = self.exchanges.get(ex_name)
            if exchange is None:
                logger.warning(f"[SpotMgr] no exchange '{ex_name}' for {symbol}")
                return

            bal = exchange.fetch_balance(market_type="spot") or {}
            free = float((bal.get(coin, {}) or {}).get("free", 0.0))
            if free <= 0:
                logger.warning(f"[SpotMgr] {ex_name}:{coin} no free balance")
                return

            # Resolve current price for dust-check; reuse manager state.
            holdings = self.spot_manager._holdings.get(ex_name, {})  # noqa: SLF001
            h = holdings.get(coin)
            px = float(getattr(h, "current_price", 0.0)) if h else 0.0
            if px <= 0:
                logger.warning(f"[SpotMgr] {ex_name}:{coin} no current price")
                return

            sell_qty = free if kind == "SELL" else free * 0.5
            sell_value_usd = sell_qty * px
            if sell_value_usd < 5.0:
                logger.info(
                    f"[SpotMgr] {ex_name}:{coin} skipping {kind} — "
                    f"sell value ${sell_value_usd:.2f} < $5 dust floor")
                return

            logger.warning(
                f"[SpotMgr] EXECUTING {kind} {ex_name}:{coin}: "
                f"qty={sell_qty:.6g} ≈ ${sell_value_usd:.2f} | reason={reason}")
            order = exchange.create_order(
                symbol=symbol, order_type="market", side="sell",
                amount=sell_qty, price=None, market_type="spot",
            )
            order_id = (order or {}).get("id", "?")
            logger.info(
                f"[SpotMgr] {kind} placed {ex_name}:{coin} | order_id={order_id}")
            # Reset the drawdown peak ONLY here — after the trim is confirmed
            # placed (audit 2026-07-07). A SELL fully exits so its peak is moot;
            # SCALE_OUT keeps half the position and must re-baseline the peak so
            # the next drawdown rule fires on a NEW decline, not the same one.
            if kind == "SCALE_OUT" and order:
                try:
                    self.spot_manager.reset_peak(coin, ex_name)
                except Exception as _rpe:
                    logger.debug(f"[SpotMgr] reset_peak skipped: {_rpe}")
        except Exception as e:
            logger.error(
                f"[SpotMgr] execution failed for {action.get('exchange')}:"
                f"{action.get('coin')}: {e}")

    def _run_capital_allocation(self):
        """Run capital allocation cycle (every 15 min)."""
        if not self.capital_allocator:
            return
        try:
            executed = self.capital_allocator.run_cycle()
            if executed:
                logger.info(f"[CapAlloc] Cycle: {len(executed)} actions executed")
        except Exception as e:
            logger.error(f"[CapAlloc] Cycle error: {e}")

    def _daily_self_check(self):
        """Daily health audit at 00:00 UTC — exchange connectivity, data freshness,
        strategy health, balance reconciliation, risk state."""
        report = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "exchanges": {},
            "risk": {},
            "positions": {},
            "strategies": {},
        }
        # Exchange connectivity
        for ex_name, exchange in self.exchanges.items():
            connected = getattr(exchange, "_connected", False)
            halted = ex_name in self._exchange_halted
            latency = self._api_latency.get(ex_name, -1)
            report["exchanges"][ex_name] = {
                "connected": connected,
                "halted": halted,
                "latency_ms": round(latency, 0) if latency > 0 else -1,
                "consecutive_fails": self._consecutive_api_fails.get(ex_name, 0),
            }
        # Risk state
        report["risk"] = {
            "is_halted": self.risk.is_halted,
            "halt_reason": self.risk.halt_reason if self.risk.is_halted else None,
            "daily_pnl": getattr(self.risk, "_daily_pnl", 0),
        }
        # Open positions
        open_count = self.tracker.count_open()
        report["positions"] = {
            "open_count": open_count,
            "per_exchange": {
                ex_name: self.tracker.count_open(exchange=ex.name)
                for ex_name, ex in self.active_exchanges.items()
            },
        }
        # Strategy health from knowledge model
        try:
            from core.knowledge_model import KnowledgeModel
            km = KnowledgeModel()
            caution = []
            fee_heavy = km.get_fee_heavy_strategies()
            for strat in km.model.get("strategies", {}):
                if km.is_caution_strategy(strat):
                    caution.append(strat)
            report["strategies"] = {
                "caution_strategies": caution,
                "fee_heavy_strategies": list(fee_heavy),
            }
        except Exception:
            pass
        # Save report
        try:
            Path("data").mkdir(parents=True, exist_ok=True)
            Path("data/daily_check.json").write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8")
        except Exception:
            pass
        # Notify
        ex_status = ", ".join(
            f"{n}:{'OK' if r.get('connected') and not r.get('halted') else 'DOWN'}"
            for n, r in report["exchanges"].items())
        summary = (
            f"Daily Self-Check\n"
            f"Exchanges: {ex_status}\n"
            f"Open positions: {open_count}\n"
            f"Risk halted: {self.risk.is_halted}"
        )
        logger.info(f"[Engine] {summary}")
        self.notifier.alert(summary)

        # 2026-05-01: also generate the gate-effectiveness report and STAR
        # review as part of the daily check. These produce dated markdown
        # reports under data/reports/ that the operator can review.
        # Failures here do NOT crash the daily check — best-effort only.
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/gate_effectiveness_report.py", "--window", "7"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=120, capture_output=True, check=False,
            )
            logger.info("[Engine] daily gate effectiveness report generated")
        except Exception as e:
            logger.debug(f"[Engine] gate effectiveness report skipped: {e}")
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/star_review.py", "--window", "30"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=60, capture_output=True, check=False,
            )
            logger.info("[Engine] daily STAR review generated")
        except Exception as e:
            logger.debug(f"[Engine] STAR review skipped: {e}")

        # 2026-05-02: orphan stop-order cleanup. Bybit accumulates stuck
        # conditional orders when positions close without cancelling their
        # SL/TP. Per-symbol limit is ~10; once hit, new entries fail-close.
        # Run --commit (cancels orphans automatically). Script's defensive
        # design (per-symbol position verification, dry-run on uncertainty)
        # makes auto-run safe.
        try:
            import subprocess
            result = subprocess.run(
                [sys.executable, "scripts/cleanup_orphan_stop_orders.py",
                 "--exchange", "bybit", "--commit"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=180, capture_output=True, check=False,
            )
            out = (result.stdout or b"").decode("utf-8", errors="replace")
            cancelled = "Cancelled:"
            if cancelled in out:
                line = next((l for l in out.splitlines() if cancelled in l), "")
                logger.info(f"[Engine] daily orphan stop-order cleanup: {line.strip()}")
            else:
                logger.info("[Engine] daily orphan stop-order cleanup: no orphans")
        except Exception as e:
            logger.debug(f"[Engine] orphan stop-order cleanup skipped: {e}")
        # 2026-05-02: shadow vs live daily compare report (Phase A.13)
        try:
            import subprocess
            subprocess.run(
                [sys.executable, "scripts/shadow_vs_live_report.py",
                 "--window-hours", "24"],
                cwd=str(Path(__file__).resolve().parents[1]),
                timeout=60, capture_output=True, check=False,
            )
            logger.info("[Engine] daily shadow-vs-live report generated")
        except Exception as e:
            logger.debug(f"[Engine] shadow compare report skipped: {e}")

    def _execute_fund_ops(self, fund_ops: list):
        """Execute MCP Brain fund management operations: TRANSFER, SELL_PORTFOLIO, BUY_PORTFOLIO."""
        # 2026-05-31: PAPER must not move real money or trade real spot. These
        # ops hit live exchange transfer/spot endpoints; gate off in DRY_RUN so
        # paper-research mode is fully simulated. (Reversible.)
        if DRY_RUN:
            if fund_ops:
                logger.info(
                    f"[MCP-FundOps] [DRY] {len(fund_ops)} fund op(s) skipped "
                    f"(PAPER: no real transfers / spot trades)")
            return
        for op in fund_ops:
            op_type = op.get("op", "")
            ex_name = op.get("exchange", "").lower()
            exchange = self.active_exchanges.get(ex_name)
            if not exchange:
                logger.warning(f"[MCP-FundOps] Exchange '{ex_name}' not active — skip {op_type}")
                continue

            try:
                if op_type == "TRANSFER":
                    from_acct = op.get("from", "spot")
                    to_acct = op.get("to", "futures")
                    amount = float(op.get("amount", 0))
                    if amount < 1:
                        continue
                    # Binance, Bitget support transfers; Bybit is unified (no need)
                    if ex_name not in ("binance", "bitget"):
                        logger.debug(f"[MCP-FundOps] {ex_name} unified or no transfer support")
                        continue
                    logger.info(
                        f"[MCP-FundOps] TRANSFER {ex_name.upper()}: "
                        f"${amount:.2f} USDT {from_acct}→{to_acct} | {op.get('reason','')[:50]}")
                    success = exchange.transfer(amount, from_acct, to_acct)
                    if success:
                        # Update cached balance
                        bals = self._balances.get(ex_name, {"spot": 0, "futures": 0})
                        bals[from_acct] = max(0, bals.get(from_acct, 0) - amount)
                        bals[to_acct] = bals.get(to_acct, 0) + amount
                        self._balances[ex_name] = bals
                        logger.info(f"[MCP-FundOps] Transfer OK: {ex_name} balances updated")
                    else:
                        logger.warning(f"[MCP-FundOps] Transfer FAILED on {ex_name}")

                elif op_type == "SELL_PORTFOLIO":
                    coin = op.get("coin", "")
                    if not coin or coin in ("USDT", "BUSD", "USDC"):
                        continue
                    symbol = f"{coin}/USDT"
                    reason = op.get("reason", "MCP fund rebalance")
                    # Get current holding
                    try:
                        bal = exchange.fetch_balance("spot")
                        held = float(bal.get("free", {}).get(coin, 0) or 0)
                    except Exception:
                        held = 0
                    if held <= 0:
                        logger.debug(f"[MCP-FundOps] No {coin} on {ex_name} to sell")
                        continue
                    # Check minimum notional
                    try:
                        t = exchange.fetch_ticker(symbol, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0 or held * px < 5.0:
                        logger.debug(f"[MCP-FundOps] {coin} on {ex_name}: ${held*px:.2f} < $5 min")
                        continue
                    logger.info(
                        f"[MCP-FundOps] SELL {coin} on {ex_name.upper()}: "
                        f"{held:.6g} ~${held*px:.2f} | {reason[:50]}")
                    try:
                        exchange.create_order(symbol, "market", "sell", held,
                                              None, {}, "spot")
                        logger.info(f"[MCP-FundOps] Sold {held:.6g} {coin} on {ex_name}")
                    except Exception as e:
                        logger.warning(f"[MCP-FundOps] Sell {coin} on {ex_name} failed: {e}")

                elif op_type == "BUY_PORTFOLIO":
                    coin = op.get("coin", "")
                    amount = float(op.get("amount", 0))
                    if not coin or amount < 5:
                        continue
                    symbol = f"{coin}/USDT"
                    try:
                        t = exchange.fetch_ticker(symbol, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0:
                        continue
                    qty = amount / px
                    logger.info(
                        f"[MCP-FundOps] BUY {coin} on {ex_name.upper()}: "
                        f"${amount:.2f} ({qty:.6g} {coin}) | {op.get('reason','')[:50]}")
                    try:
                        exchange.create_order(symbol, "market", "buy", qty,
                                              px, {}, "spot")
                        logger.info(f"[MCP-FundOps] Bought {qty:.6g} {coin} on {ex_name}")
                    except Exception as e:
                        logger.warning(f"[MCP-FundOps] Buy {coin} on {ex_name} failed: {e}")

            except Exception as e:
                logger.error(f"[MCP-FundOps] {op_type} on {ex_name} error: {e}")

    # ── Universal position scanner ──────────────────────────────────
    _STABLECOINS = {"USDT", "BUSD", "USDC", "FDUSD", "TUSD", "DAI", "USDD"}
    _COMMODITY_BASES = {"XAU", "XAG", "WTI", "CL"}

    def _fetch_all_exchange_positions(self) -> list[dict]:
        """Fetch ALL open positions from ALL connected exchanges.
        Returns futures positions + spot holdings as a unified list.
        Cached for 60 seconds to avoid redundant API calls."""
        now = time.time()
        if now - self._exchange_positions_time < 60 and self._exchange_positions_cache:
            return list(self._exchange_positions_cache)

        results = []

        def _fetch_one_exchange(ex_name, exchange):
            positions = []
            # ── Futures positions ──
            if True:
                try:
                    raw = exchange.fetch_positions()
                    for ep in (raw or []):
                        size = float(ep.get("contracts") or ep.get("contractSize") or 0)
                        if size <= 0:
                            continue
                        symbol = ep.get("symbol", "")
                        side_raw = (ep.get("side") or "").lower()
                        if side_raw not in ("long", "short"):
                            continue
                        side = "buy" if side_raw == "long" else "sell"
                        entry = float(ep.get("entryPrice") or 0)
                        mark = float(ep.get("markPrice") or ep.get("lastPrice") or 0)
                        upnl = float(ep.get("unrealizedPnl") or 0)
                        lev = int(ep.get("leverage") or 1)
                        liq = float(ep.get("liquidationPrice") or 0)
                        pnl_pct = 0
                        if entry > 0:
                            if side == "buy":
                                pnl_pct = (mark - entry) / entry * 100
                            else:
                                pnl_pct = (entry - mark) / entry * 100
                        base = symbol.split("/")[0].split(":")[0]
                        asset_class = "commodity" if base in self._COMMODITY_BASES else "crypto_futures"
                        positions.append({
                            "id": f"EX-{ex_name}-{symbol}-{side}",
                            "symbol": symbol,
                            "side": side,
                            "entry_price": entry,
                            "current_price": mark,
                            "pnl": round(upnl, 4),
                            "pnl_pct": round(pnl_pct, 2),
                            "stop_loss": 0,
                            "take_profit": 0,
                            "age_min": 0,
                            "exchange": ex_name,
                            "market_type": "futures",
                            "leverage": lev,
                            "liquidation_price": liq,
                            "source": "exchange",
                            "size": size,
                            "usdt_value": round(mark * size, 2) if mark else 0,
                            "asset_class": asset_class,
                        })
                except Exception as e:
                    logger.debug(f"[ExScan] {ex_name} futures fetch: {e}")

            # ── Spot holdings ──
            try:
                bal = exchange.fetch_balance("spot")
                total_bal = bal.get("total", {})
                for asset, amt in total_bal.items():
                    amt = float(amt or 0)
                    if amt <= 0 or asset in self._STABLECOINS:
                        continue
                    # Get current price
                    sym = f"{asset}/USDT"
                    try:
                        t = exchange.fetch_ticker(sym, "spot")
                        px = float(t.get("last") or t.get("close") or 0)
                    except Exception:
                        px = 0
                    if px <= 0:
                        continue
                    usdt_val = amt * px
                    if usdt_val < 5.0:
                        continue
                    asset_class = "commodity" if asset in self._COMMODITY_BASES else "crypto_spot"
                    positions.append({
                        "id": f"SPOT-{ex_name}-{asset}",
                        "symbol": sym,
                        "side": "buy",
                        "entry_price": 0,
                        "current_price": px,
                        "pnl": 0,
                        "pnl_pct": 0,
                        "stop_loss": 0,
                        "take_profit": 0,
                        "age_min": 0,
                        "exchange": ex_name,
                        "market_type": "spot",
                        "leverage": 1,
                        "liquidation_price": 0,
                        "source": "exchange",
                        "size": amt,
                        "usdt_value": round(usdt_val, 2),
                        "asset_class": asset_class,
                    })
            except Exception as e:
                logger.debug(f"[ExScan] {ex_name} spot balance: {e}")
            return positions

        with ThreadPoolExecutor(max_workers=4) as pool:
            futures = {
                pool.submit(_fetch_one_exchange, name, ex): name
                for name, ex in self.active_exchanges.items()
            }
            for fut in as_completed(futures, timeout=30):
                try:
                    results.extend(fut.result())
                except Exception as e:
                    logger.debug(f"[ExScan] fetch error: {e}")

        self._exchange_positions_cache = results
        self._exchange_positions_time = time.time()
        # 2026-04-16: Persist the universal snapshot so the dashboard
        # (a separate process) can display the same unified view instead
        # of running its own parallel LiveFetcher that can silently fail
        # when a client init errors at startup.
        try:
            import json as _json
            from pathlib import Path as _Path
            _path = _Path("data/exchange_positions.json")
            _path.parent.mkdir(parents=True, exist_ok=True)
            _path.write_text(_json.dumps({
                "ts": self._exchange_positions_time,
                "positions": results,
            }, indent=2), encoding="utf-8")
        except Exception as _e:
            logger.debug(f"[ExScan] cache write failed: {_e}")
        return results

    def _unrealized_pnl_frac(self, p) -> float:
        """Compute unrealized PnL as a fraction (e.g. 0.012 = +1.2%) using
        the current mark price from the exchange. Returns 0.0 on any error.

        Notes:
        - Fraction is computed against entry_price (unleveraged) so the
          [0%, 2%) bands in Areas 4 + 5 refer to PRICE move, not equity move.
          A +1% price move on a 2x position = +2% on equity but only +1% by
          this fraction. This matches the existing SL/TP percentages.
        """
        ex = None
        for ex_name, exchange in self.active_exchanges.items():
            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                ex = exchange
                break
        if ex is None:
            return 0.0
        try:
            ticker = ex.fetch_ticker(p.symbol, p.market_type)
            info = ticker.get("info", {}) or {}
            mark = info.get("markPrice") or info.get("mark")
            try:
                price = float(mark) if mark else 0.0
            except (TypeError, ValueError):
                price = 0.0
            if price <= 0:
                price = float(ticker.get("last") or 0.0)
            if price <= 0 or p.entry_price <= 0:
                return 0.0
            if p.side == "buy":
                return (price - p.entry_price) / p.entry_price
            else:
                return (p.entry_price - price) / p.entry_price
        except Exception:
            return 0.0

    def _maybe_capture_small_tp(self, p) -> bool:
        """Area 5 — Deterministic small-TP capture.

        Fires when:
          - config.AUTO_SMALL_TP_ENABLED is True
          - position is futures (spot keeps existing logic)
          - symbol NOT in STAR_SYMBOLS (those ride per Phase 46)
          - age >= AUTO_SMALL_TP_MIN_AGE_MIN
          - pnl_frac in [AUTO_SMALL_TP_MIN_PNL_FRAC, AUTO_SMALL_TP_MAX_PNL_FRAC)

        Action: market close via order_mgr.close_position with reason
        'auto_small_tp_1pct'. Returns True iff a close was actually fired.

        NOT a re-enable of Phase 39 disabled CLOSE — that was Claude's
        discretionary, narrative-driven decision (1W/17L over 388 trades).
        This is a deterministic threshold rule, equivalent in nature to a
        take-profit limit order placed at entry+1%.
        """
        try:
            from config import (
                AUTO_SMALL_TP_ENABLED,
                AUTO_SMALL_TP_MAX_PNL_FRAC,
                AUTO_SMALL_TP_MIN_AGE_MIN,
                AUTO_SMALL_TP_MIN_PNL_FRAC,
                STAR_SYMBOLS,
            )
        except ImportError:
            return False
        if not AUTO_SMALL_TP_ENABLED:
            return False
        if getattr(p, "market_type", "") != "futures":
            return False
        if p.symbol in (STAR_SYMBOLS or set()):
            return False
        try:
            age_min = float(p.duration_minutes)
        except Exception:
            return False
        if age_min < AUTO_SMALL_TP_MIN_AGE_MIN:
            return False

        pnl_frac = self._unrealized_pnl_frac(p)
        if not (AUTO_SMALL_TP_MIN_PNL_FRAC <= pnl_frac < AUTO_SMALL_TP_MAX_PNL_FRAC):
            return False

        # Resolve the exchange and fire close_position
        ex = None
        for ex_name, exchange in self.active_exchanges.items():
            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                ex = exchange
                break
        if ex is None:
            return False

        logger.info(
            f"[AutoSmallTP] {p.symbol} {p.side.upper()} age={age_min:.0f}m "
            f"pnl=+{pnl_frac*100:.2f}% — capturing at market")
        self.order_mgr.close_position(ex, p, "auto_small_tp_1pct")
        return True

    def _maybe_tighten_aged_position(self, p) -> bool:
        """Area 4 — Age-aware SL→breakeven tightener.

        Fires when:
          - config.AGE_AWARE_SL_ENABLED is True
          - position is futures (spot has no leverage-side ghost problem)
          - age >= AGE_AWARE_SL_MIN_AGE_MIN
          - pnl_frac in [AGE_AWARE_SL_MIN_PNL_FRAC, AGE_AWARE_SL_MAX_PNL_FRAC)
          - new breakeven SL is TIGHTER than current SL (never widens — Spec §2)

        Action: replaces exchange-side SL with entry × (1 ± 2·fee_rate ± 0.0005).

        Returns True iff an SL replacement actually fired.
        """
        try:
            from config import (
                AGE_AWARE_SL_ENABLED,
                AGE_AWARE_SL_MAX_PNL_FRAC,
                AGE_AWARE_SL_MIN_AGE_MIN,
                AGE_AWARE_SL_MIN_PNL_FRAC,
                FEE,
            )
        except ImportError:
            return False
        if not AGE_AWARE_SL_ENABLED:
            return False
        if getattr(p, "market_type", "") != "futures":
            return False
        try:
            age_min = float(p.duration_minutes)
        except Exception:
            return False
        if age_min < AGE_AWARE_SL_MIN_AGE_MIN:
            return False

        pnl_frac = self._unrealized_pnl_frac(p)
        if not (AGE_AWARE_SL_MIN_PNL_FRAC <= pnl_frac < AGE_AWARE_SL_MAX_PNL_FRAC):
            return False

        # Breakeven SL — covers round-trip fee + 5bp buffer
        rate = FEE.get("futures_taker", 0.0005)
        if p.side == "buy":
            new_sl = p.entry_price * (1 + 2 * rate + 0.0005)
            # Refuse to widen: must be ABOVE current SL for a long
            if new_sl <= float(p.stop_loss or 0):
                return False
        else:
            new_sl = p.entry_price * (1 - 2 * rate - 0.0005)
            # Refuse to widen: must be BELOW current SL for a short
            if new_sl >= float(p.stop_loss or 0):
                return False

        # Replace the exchange-side SL
        ex = None
        for ex_name, exchange in self.active_exchanges.items():
            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                ex = exchange
                break
        if ex is None:
            return False
        if not self._replace_exchange_sl(ex, p, new_sl):
            return False
        old_sl = p.stop_loss
        p.stop_loss = new_sl
        logger.info(
            f"[AgeAwareSL] {p.symbol} {p.side.upper()} age={age_min:.0f}m "
            f"pnl={pnl_frac*100:+.2f}% SL→breakeven "
            f"({old_sl:.6g}→{new_sl:.6g})")
        return True

    def _run_mcp_position_monitor(self):
        """Run MCP Brain position monitor — scans ALL positions across ALL exchanges.
        Merges bot-tracked positions with exchange-discovered positions (futures + spot).
        MCP Brain is the SOLE authority on hold/close/take-profit for the entire portfolio."""
        if not self.mcp_brain or not self.mcp_brain.is_enabled:
            return
        # 2026-06-15: under SIGNAL_SOURCE=tsmom the bot makes NO Claude/MCP decisions.
        # tsmom owns entries + its own momentum-flip exits, and the deterministic SL/TP
        # loop (its own 10s thread) keeps disaster stops live. Skip the MCP advisory
        # monitor entirely so the bot is purely tsmom. Logged once to avoid 90s spam.
        from config import SIGNAL_SOURCE as _SIG_MON
        if _SIG_MON in ("tsmom", "machine", "none"):
            _logged_attr = f"_{_SIG_MON}_monitor_off_logged"
            if not getattr(self, _logged_attr, False):
                logger.info(
                    f"[Engine] {_SIG_MON.upper()} mode - MCP position monitor disabled "
                    "(deterministic SL/TP still active; no Claude/MCP advisory).")
                setattr(self, _logged_attr, True)
            return
        try:
            # ── Phase 1: Bot-tracked positions ──
            open_positions = self.tracker.get_open()
            tracker_map = {}          # id -> Position object
            tracked_keys = set()      # (exchange, symbol_norm, side) for dedup
            pos_data = []

            for p in open_positions:
                current_price = 0
                for ex_name, exchange in self.active_exchanges.items():
                    if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                        try:
                            ticker = exchange.fetch_ticker(p.symbol, p.market_type)
                            current_price = float(ticker.get("last", 0) or 0)
                        except Exception:
                            pass
                        break
                if not current_price:
                    # Fallback to entry price so position is still visible to MCP
                    current_price = p.entry_price
                lev = max(1, getattr(p, "leverage", 1) or 1)
                if p.side == "buy":
                    unrealized_pnl = (current_price - p.entry_price) * p.size
                    pnl_pct = (current_price - p.entry_price) / p.entry_price * 100 * lev
                else:
                    unrealized_pnl = (p.entry_price - current_price) * p.size
                    pnl_pct = (p.entry_price - current_price) / p.entry_price * 100 * lev

                base = p.symbol.split("/")[0].split(":")[0]
                asset_class = "commodity" if base in self._COMMODITY_BASES else (
                    "crypto_futures" if p.market_type == "futures" else "crypto_spot")

                pd = {
                    "id": p.id,
                    "symbol": p.symbol,
                    "side": p.side,
                    "entry_price": p.entry_price,
                    "current_price": current_price,
                    "pnl": round(unrealized_pnl, 4),
                    "pnl_pct": round(pnl_pct, 2),
                    "stop_loss": p.stop_loss,
                    "take_profit": p.take_profit,
                    "age_min": p.duration_minutes,
                    "exchange": p.exchange,
                    "market_type": p.market_type,
                    "leverage": p.leverage,
                    "liquidation_price": 0,
                    "source": "tracker",
                    "size": p.size,
                    "usdt_value": round(current_price * p.size, 2),
                    "asset_class": asset_class,
                }
                pos_data.append(pd)
                tracker_map[p.id] = p
                sym_norm = p.symbol.split(":")[0]
                tracked_keys.add((p.exchange.lower(), sym_norm, p.side))

            # ── Phase 2: Exchange-discovered positions (futures + spot) ──
            exchange_positions = self._fetch_all_exchange_positions()

            # ── Phase 3: Deduplicate and merge ──
            for ep in exchange_positions:
                sym_norm = ep["symbol"].split(":")[0]
                key = (ep["exchange"].lower(), sym_norm, ep["side"])
                if key in tracked_keys:
                    continue  # already tracked by bot — skip exchange duplicate
                pos_data.append(ep)

            if not pos_data:
                return

            # ── Priority cap: max 20 positions for MCP Brain ──
            # Tracked first, then external futures by value, then external spot by value
            tracked_data = [d for d in pos_data if d["source"] == "tracker"]
            ext_futures = sorted(
                [d for d in pos_data if d["source"] == "exchange" and d["market_type"] == "futures"],
                key=lambda x: x.get("usdt_value", 0), reverse=True)
            ext_spot = sorted(
                [d for d in pos_data if d["source"] == "exchange" and d["market_type"] == "spot"],
                key=lambda x: x.get("usdt_value", 0), reverse=True)
            capped = tracked_data[:20]
            remaining = 20 - len(capped)
            if remaining > 0:
                capped.extend(ext_futures[:remaining])
                remaining = 20 - len(capped)
            if remaining > 0:
                capped.extend(ext_spot[:remaining])
            pos_data = capped

            n_tracked = len(tracked_data)
            n_ext_fut = len(ext_futures)
            n_ext_spot = len(ext_spot)
            if n_ext_fut or n_ext_spot:
                logger.info(
                    f"[MCP-Monitor] {n_tracked} tracked + {n_ext_fut} ext-futures "
                    f"+ {n_ext_spot} ext-spot = {len(pos_data)} sent to MCP Brain")

            # ── Phase 3.5 (2026-05-20): Deterministic exits BEFORE MCP brain ──
            # Areas 5 + 4 are pure threshold rules (no judgment, no narrative).
            # Running them here means they fire even on cycles where Claude
            # would have said HOLD. Ordering matters: Area 5 (close) is decisive
            # over Area 4 (SL move) — check Area 5 first, return early on fire.
            #
            # Only TRACKER positions get this treatment — external (exchange-
            # discovered) positions don't have full Position state (stop_loss,
            # entry_price, side as a Position attr) and are handled in the
            # source=='exchange' branch of the MCP-advice loop below.
            from core.deep_breakout_lane import (
                is_deep_breakout_position as _is_db_pos,
            )
            from core.tsmom_signal import is_tsmom_position as _is_tsmom_pos
            for pid_local, p_local in list(tracker_map.items()):
                try:
                    # Phase 2b: tsmom positions own their exit (momentum flip) —
                    # skip the deterministic small-TP capture + age→BE tightener.
                    if _is_tsmom_pos(p_local):
                        continue
                    # Deep-breakout lane (2026-07-11): SL/TP + 126-bar max-hold
                    # own the exit — small-TP capture / age→BE would clip the
                    # researched 3R geometry.
                    if _is_db_pos(p_local):
                        continue
                    if self._maybe_capture_small_tp(p_local):
                        continue  # Area 5 fired — position closing; skip Area 4
                    self._maybe_tighten_aged_position(p_local)
                except Exception as _de:
                    logger.debug(
                        f"[DeterministicExits] {p_local.symbol}: {_de}")

            # ── Phase 4: Send to MCP Brain ──
            advice = self.mcp_brain.monitor_positions(pos_data)
            if not advice:
                return

            # ── Phase 5: Apply actions ──
            for pos_entry in pos_data:
                pid = pos_entry["id"]
                adv = advice.get(pid, {})
                # Also try short ID (MCP Brain returns first 8 chars as keys)
                if not adv and len(pid) > 8:
                    adv = advice.get(pid[:8], {})
                action = adv.get("action", "")
                if not action or action == "HOLD":
                    continue

                source = pos_entry["source"]
                conf = adv.get("confidence", 0)
                reason = adv.get("reason", "")[:60]
                _pnl_pct = pos_entry.get("pnl_pct", 0)

                # ═══ TRACKER POSITIONS — use existing close_position path ═══
                if source == "tracker":
                    p = tracker_map.get(pid)
                    if not p:
                        continue

                    # Phase 2b: tsmom positions are held to their momentum-flip
                    # CLOSE — no MCP advice (TAKE_PROFIT/TIGHTEN/BREAKEVEN) may
                    # clip or ratchet them. The wide disaster stop is the only rail.
                    if _is_tsmom_pos(p):
                        continue
                    # Deep-breakout lane: no MCP advice may clip or ratchet a
                    # lane hold either — its rails are SL/TP, the §2 8%
                    # guardian, and the lane's 126-bar max-hold close.
                    if _is_db_pos(p):
                        continue

                    if action == "TAKE_PROFIT":
                        # Fee-aware: estimate round-trip fees and require net profit >= threshold
                        # Phase 46 (2026-05-10): STAR symbols get HIGHER threshold (1.5%
                        # vs 0.5%) so they're allowed to ride to bigger wins. Historical
                        # R:R for STARs (ATOM, ARB) is 1.85+ — capping their profits at
                        # +0.5% leaves big upside on the table. Non-STAR symbols keep
                        # the +0.5% threshold (their realized R:R is closer to 1:1).
                        # The 4 historical full-TP hits (avg $2.83) demonstrate that
                        # bigger wins are achievable when positions are given room.
                        from config import FEE
                        try:
                            from config import STAR_SYMBOLS as _STAR
                        except ImportError:
                            _STAR = set()
                        if p.market_type == "futures":
                            rt_fee_pct = (FEE.get("futures_maker", 0.0002) + FEE.get("futures_taker", 0.0005)) * 100
                        else:
                            rt_fee_pct = (FEE.get("spot_maker", 0.001) + FEE.get("spot_taker", 0.001)) * 100
                        net_pnl_pct = _pnl_pct - rt_fee_pct
                        # Phase 46: STAR threshold 1.5%, non-STAR 0.5%
                        is_star = p.symbol in _STAR
                        _base_threshold = 1.5 if is_star else 0.5
                        # B5 (audit 2026-06-21): when near_target_exit is ON,
                        # require a near-PLANNED-TP exit instead of the bare floor.
                        # net_pnl_pct is LEVERAGED, so the helper scales the
                        # price-% TP distance by leverage. Flag OFF => returns
                        # _base_threshold (byte-identical to today).
                        # ⚠ UNITS INVARIANT: net_pnl_pct is leveraged ONLY because
                        # this block is fenced by `source == "tracker"` above (that
                        # builder applies *lev). Do NOT route the exchange-sourced
                        # UNLEVERAGED pnl_pct here, or the threshold becomes lev×
                        # too strict and winners never exit.
                        from config import RISK as _RISK_NT
                        tp_threshold = _effective_tp_threshold(
                            take_profit=p.take_profit,
                            entry_price=p.entry_price,
                            side=p.side,
                            leverage=p.leverage,
                            base_threshold=_base_threshold,
                            near_target_enabled=_RISK_NT.get("near_target_exit_enabled", False),
                            near_target_frac=_RISK_NT.get("near_target_frac", 0.8),
                        )
                        if net_pnl_pct >= tp_threshold and conf >= 0.60:
                            for ex_name, exchange in self.active_exchanges.items():
                                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                    star_tag = " [STAR ride]" if is_star else ""
                                    logger.warning(
                                        f"[MCP-Brain] TAKE PROFIT{star_tag}: {p.symbol} {p.side} "
                                        f"pnl={_pnl_pct:+.1f}% net~{net_pnl_pct:+.1f}% "
                                        f"conf={conf:.0%} (threshold={tp_threshold}%) — {reason}")
                                    self.order_mgr.close_position(
                                        exchange, p, "mcp_take_profit")
                                    break
                        else:
                            star_note = " [STAR — letting it run]" if is_star else ""
                            logger.debug(
                                f"[MCP-Brain] TAKE_PROFIT skipped{star_note}: {p.symbol} "
                                f"pnl={_pnl_pct:+.1f}% net~{net_pnl_pct:+.1f}% conf={conf:.0%} "
                                f"(need net>={tp_threshold}% + conf>=60%)")

                    elif action == "CLOSE":
                        # 2026-04-24: systematic_close disabled. Historical 1W/17L
                        # over 388 trades ($-16.42 total). The monitor was closing
                        # positions with adverse-indicator narratives but the
                        # subsequent price action rarely confirmed — net-negative
                        # drag. SL, trailing_stop, and mcp_take_profit own exits
                        # now. Suggestion is logged but not actioned; if a real
                        # hard-loss case appears, the scan-CLOSE rules in
                        # mcp_brain (leveraged-aware -12%/-3% thresholds) still
                        # fire and hit close_position with `systematic_close`
                        # from there — but only as a catastrophic safety rail,
                        # not a discretionary close.
                        logger.info(
                            f"[MCP-Brain] CLOSE suggested but systematic_close "
                            f"disabled (2026-04-24 policy): {p.symbol} "
                            f"conf={conf:.0%} pnl={_pnl_pct:+.1f}% — {reason[:60]}")

                    elif action == "TIGHTEN":
                        for ex_name, exchange in self.active_exchanges.items():
                            if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                try:
                                    t = exchange.fetch_ticker(p.symbol, p.market_type)
                                    cur = float(t.get("last", 0))
                                    if cur > 0 and p.stop_loss > 0:
                                        new_sl = None
                                        if p.side == "buy":
                                            candidate = p.stop_loss + (cur - p.stop_loss) * 0.3
                                            if candidate > p.stop_loss:
                                                new_sl = candidate
                                        else:
                                            candidate = p.stop_loss - (p.stop_loss - cur) * 0.3
                                            if candidate < p.stop_loss:
                                                new_sl = candidate
                                        if new_sl is not None:
                                            logger.info(
                                                f"[MCP-Brain] TIGHTEN: {p.symbol} {p.side.upper()} "
                                                f"SL {p.stop_loss:.6g} → {new_sl:.6g}")
                                            if self._replace_exchange_sl(exchange, p, new_sl):
                                                p.stop_loss = new_sl
                                except Exception as _e:
                                    logger.debug(f"[MCP-Brain] TIGHTEN {p.symbol}: {_e}")
                                break

                    elif action == "BREAKEVEN":
                        from core.position_tracker import _fee_rate
                        rate = _fee_rate(p.market_type)
                        new_be = None
                        if p.side == "buy":
                            be = p.entry_price * (1 + rate * 2 + 0.0005)
                            if be > p.stop_loss:
                                new_be = be
                        else:
                            be = p.entry_price * (1 - rate * 2 - 0.0005)
                            if be < p.stop_loss:
                                new_be = be
                        if new_be is not None:
                            logger.info(
                                f"[MCP-Brain] BREAKEVEN: {p.symbol} {p.side.upper()} "
                                f"SL {p.stop_loss:.6g} → {new_be:.6g}")
                            for ex_name, exchange in self.active_exchanges.items():
                                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                    try:
                                        if self._replace_exchange_sl(exchange, p, new_be):
                                            p.stop_loss = new_be
                                    except Exception as _e:
                                        logger.debug(f"[MCP-Brain] BREAKEVEN replace {p.symbol}: {_e}")
                                    break

                    # WIDEN removed — spec §2 forbids widening stop losses.

                # ═══ EXTERNAL POSITIONS — direct exchange execution ═══
                elif source == "exchange":
                    ex_name = pos_entry["exchange"]
                    mtype = pos_entry["market_type"]

                    if action in ("TAKE_PROFIT", "CLOSE"):
                        # Phase 39 (2026-05-09): CLOSE disabled for external positions.
                        # mcp_brain_close accumulated -$20.09 over 44 trades (30% WR).
                        # The monitor was cutting positions early against subsequent
                        # price action. SL, trailing_stop, and mcp_take_profit own
                        # all exits now — CLOSE is demoted to logged-only.
                        if action == "CLOSE":
                            logger.info(
                                f"[MCP-Brain] EXT CLOSE suppressed (Phase 39 policy): "
                                f"{pos_entry['symbol']} on {ex_name} conf={conf:.0%} — {reason[:60]}")
                            continue
                        if action == "TAKE_PROFIT":
                            if mtype == "futures" and (_pnl_pct < 1.0 or conf < 0.70):
                                logger.debug(
                                    f"[MCP-Brain] EXT TP skipped: {pos_entry['symbol']} on {ex_name} "
                                    f"pnl={_pnl_pct:+.1f}% conf={conf:.0%}")
                                continue
                            if mtype == "spot" and conf < 0.80:
                                logger.debug(
                                    f"[MCP-Brain] EXT SPOT TP skipped: {pos_entry['symbol']} on {ex_name} "
                                    f"conf={conf:.0%} < 80%")
                                continue

                        if DRY_RUN:
                            logger.info(
                                f"[MCP-Brain] [DRY] EXT {action}: {pos_entry['symbol']} {pos_entry['side']} "
                                f"on {ex_name} ({mtype}) — {reason}")
                        else:
                            self._close_external_position(ex_name, pos_entry, reason)

                    elif action in ("TIGHTEN", "BREAKEVEN"):
                        logger.debug(
                            f"[MCP-Brain] EXT {action} N/A: {pos_entry['symbol']} on {ex_name} "
                            f"(no tracked SL for external positions)")

            # Persist SL changes for tracker positions
            self.tracker._save()

        except Exception as e:
            logger.debug(f"[Engine] MCP Position Monitor: {e}")

    def _ext_position_still_open(self, exchange, symbol: str) -> bool:
        """Confirm a non-zero live position for `symbol` before a reduceOnly-stripped
        close retry. Fail-CLOSED: any fetch error returns False so the caller aborts
        the retry — a stripped market order on an already-flat symbol would FILL and
        open an untracked NAKED REVERSE (no SL, no tracker entry). Ported from
        order_manager._close_position_impl's 2026-04-20 naked-short guard."""
        try:
            live = exchange.fetch_positions([symbol]) or []
        except Exception as fe:
            logger.warning(
                f"[MCP-Brain] fetch_positions failed during ext close-retry guard "
                f"for {symbol}: {str(fe)[:120]} — treating as closed to avoid naked reverse")
            return False
        for p in live:
            try:
                sz = abs(float(p.get("contracts") or p.get("contractSize") or 0))
            except (TypeError, ValueError):
                sz = 0.0
            if sz > 0:
                return True
        return False

    def _close_external_position(self, ex_name: str, pos: dict, reason: str):
        """Close an exchange-discovered position NOT tracked internally.
        Futures: market close order. Spot: market sell coins."""
        # Defense-in-depth: this places REAL reduceOnly futures closes / spot sells
        # of the owner's actual coins. The sole caller (the MCP position monitor) is
        # already DRY_RUN-gated, but a real-money side effect must never rely on
        # caller governance alone — self-gate so PAPER/OBSERVATION can never reach
        # the exchange even via a future call site. No-op in CONTROLLED_LIVE.
        if DRY_RUN:
            logger.info(
                f"[MCP-Brain] [DRY] EXT CLOSE skipped (self-gate): "
                f"{pos.get('symbol')} {pos.get('side')} on {ex_name} "
                f"({pos.get('market_type')}) | {reason}")
            return
        exchange = self.active_exchanges.get(ex_name)
        if not exchange:
            return

        symbol = pos["symbol"]
        market_type = pos["market_type"]
        side = pos["side"]
        size = pos["size"]

        if market_type == "spot":
            coin = symbol.split("/")[0]
            try:
                bal = exchange.fetch_balance("spot")
                held = float(bal.get("free", {}).get(coin, 0) or 0)
            except Exception:
                held = 0
            if held <= 0:
                return
            try:
                t = exchange.fetch_ticker(symbol, "spot")
                px = float(t.get("last") or 0)
            except Exception:
                px = 0
            if px <= 0 or held * px < 5.0:
                return
            logger.warning(
                f"[MCP-Brain] SELL SPOT {coin} on {ex_name}: "
                f"{held:.6g} ~${held * px:.2f} | {reason}")
            try:
                exchange.create_order(symbol, "market", "sell", held, None, {}, "spot")
            except Exception as e:
                logger.error(f"[MCP-Brain] Spot sell {coin} on {ex_name} failed: {e}")

        elif market_type == "futures":
            close_side = "sell" if side == "buy" else "buy"
            ex_lower = ex_name.lower()
            params = {}
            if ex_lower in self.order_mgr._oneway_mode:
                if getattr(exchange, '_is_oneway', False):
                    params["reduceOnly"] = True
            else:
                params["positionSide"] = "LONG" if side == "buy" else "SHORT"
                params["reduceOnly"] = True

            logger.warning(
                f"[MCP-Brain] CLOSE EXT FUTURES {symbol} {side} on {ex_name}: "
                f"size={size} | {reason}")
            try:
                exchange.create_order(
                    symbol, "market", close_side, size, None, params, "futures")
            except Exception as e:
                err = str(e)
                if "reduceonly" in err.lower() or "-1106" in err:
                    self.order_mgr._oneway_mode.add(ex_lower)
                    if not self._ext_position_still_open(exchange, symbol):
                        logger.info(
                            f"[MCP-Brain] {symbol} on {ex_name} already flat — "
                            f"skipping naked-reverse retry (fail-closed guard) — "
                            f"external position already closed")
                        return
                    try:
                        exchange.create_order(
                            symbol, "market", close_side, size, None, {}, "futures")
                    except Exception as e2:
                        logger.error(f"[MCP-Brain] Ext futures close retry: {e2}")
                elif "no position" in err.lower() or "does not exist" in err.lower():
                    logger.info(f"[MCP-Brain] Ext position already closed: {symbol}")
                elif any(s in err.lower() for s in (
                        "unilateral", "position mode", "positionside", "-4061", "40774")):
                    self.order_mgr._oneway_mode.add(ex_lower)
                    _ow = {"reduceOnly": True} if getattr(exchange, '_is_oneway', False) else {}
                    if not self._ext_position_still_open(exchange, symbol):
                        logger.info(
                            f"[MCP-Brain] {symbol} on {ex_name} already flat — "
                            f"skipping naked-reverse retry (fail-closed guard) — "
                            f"external position already closed")
                        return
                    try:
                        exchange.create_order(
                            symbol, "market", close_side, size, None, _ow, "futures")
                    except Exception as e2:
                        logger.error(f"[MCP-Brain] Ext futures close (one-way): {e2}")
                else:
                    logger.error(f"[MCP-Brain] Ext futures close failed: {e}")

    def _run_learning(self):
        try:
            self.learner.learn(force=True)
        except Exception as e:
            logger.debug(f"[Engine] Learning error: {e}")

        # Update Kelly stats from closed trades
        if hasattr(self, 'order_mgr') and hasattr(self.order_mgr, 'kelly'):
            try:
                closed_trades = [
                    vars(p) if hasattr(p, '__dict__') else p
                    for p in self.tracker._closed
                ]
                self.order_mgr.kelly.update_from_trades(closed_trades)
            except Exception as e:
                logger.debug(f"[Engine] Kelly update error: {e}")

        # Clean up stale sl_widened entries for closed positions
        if hasattr(self, 'order_mgr'):
            try:
                self.order_mgr.cleanup_sl_widened()
            except Exception:
                pass

    def _run_optimizer(self):
        from core.auto_optimizer import AutoOptimizer
        logger.info("[Engine] Starting auto-optimization...")
        for ex in self.active_exchanges.values():
            try:
                AutoOptimizer(ex).run_all()
                break
            except Exception as e:
                logger.error(f"[Engine] Optimizer: {e}")

    def _run_self_healing(self):
        healer = getattr(self, "self_healer", None)
        if healer is None:
            return
        try:
            report = healer.tick()
            verdict = report.get("verdict")
            if verdict not in {"SKIPPED_COOLDOWN", "DISABLED"}:
                logger.info(
                    f"[SelfHeal] verdict={verdict} "
                    f"actions={len(report.get('actions') or [])}"
                )
        except Exception as e:
            logger.debug(f"[SelfHeal] tick skipped: {e}")

    def _run_self_improve(self):
        """PAPER-only autonomous mutate->validate->promote cycle (WS4).

        Gated by ``guardrails.self_improve_enabled()`` (env SELF_IMPROVE_ENABLED, hard
        PAPER). Builds short-lookback machine-strategy candidates, runs them through the
        honest gate + trade-sequence Monte Carlo, and promotes winners shadow->active-paper
        (never live — the kernel + PAPER latch forbid it). Fully fail-soft: any error is
        logged and skipped so it can never disturb trading."""
        try:
            from core.decision.guardrails import self_improve_enabled

            if not self_improve_enabled():
                return
            from core.decision import promotion_loop
            from research.mutator import build_candidates

            candidates = build_candidates()
            if not candidates:
                return
            report = promotion_loop.tick(candidates)
            if report.get("n_promoted"):
                logger.info(
                    f"[SelfImprove] promoted {report['n_promoted']}/{report['n_candidates']} "
                    "variant(s) shadow->active-paper (PAPER)"
                )
            else:
                logger.debug(
                    f"[SelfImprove] {report.get('n_candidates', 0)} candidates evaluated, "
                    "0 promoted (no edge cleared the gate)"
                )
        except Exception as e:
            logger.debug(f"[SelfImprove] cycle skipped: {e}")

    # ── Main run ──────────────────────────────────────────────────────

    def run(self):
        # Spec Appendix B: refuse to run CONTROLLED_LIVE without a signed checklist.
        try:
            from config import (
                CONTROLLED_LIVE_ENABLED as _live_enabled,
            )
            from config import (
                OPERATING_MODE as _op_mode,
            )
            from config import (
                SIGNAL_SOURCE as _live_signal_source,
            )
            from core.live_gate import (
                enforce_controlled_live_gate,
                enforce_strategy_readiness_gate,
            )
            enforce_controlled_live_gate(
                _op_mode,
                controlled_live_enabled=_live_enabled,
            )
            _strategy_family = (
                "machine" if _live_signal_source == "machine"
                else ("tsmom" if _live_signal_source == "tsmom" else None)
            )
            enforce_strategy_readiness_gate(
                _op_mode,
                strategy_family=_strategy_family,
            )
        except SystemExit:
            raise
        except Exception as _e:
            logger.warning(f"[LiveGate] check error (non-fatal): {_e}")

        logger.info("[Engine] Bot started — Ctrl+C to stop")
        # `schedule` is module-global. Clear jobs left by a prior in-process
        # watchdog restart before registering this engine's jobs.
        schedule.clear()
        # 2026-06-15: announce the active signal source up front so it's unambiguous
        # which engine is deciding trades (the cycle/monitor logs alone read "[Claude]").
        from config import SIGNAL_SOURCE as _SIG_RUN
        if _SIG_RUN == "tsmom":
            logger.info(
                "[Engine] Signal source: TSMOM (long-only majors, capital-preservation) "
                "— Claude/MCP entry scoring AND position monitor DISABLED")
        elif _SIG_RUN == "machine":
            logger.info(
                "[Engine] Signal source: MACHINE (deterministic OHLCV ensemble) "
                "- Claude/MCP entry scoring AND position monitor DISABLED")
        else:
            logger.info(f"[Engine] Signal source: {_SIG_RUN} (Claude/MCP scoring path)")
        if _SIG_RUN != "machine":
            # The self-healing strategy-adaptation and promotion loops tune the
            # MACHINE signal and write data/adaptive_machine_config.json — which is
            # ONLY read by MachineSignal. Under any other source those writes are
            # never consumed, so their "promoted" reports are DORMANT (no live
            # effect). Say so plainly to avoid false "self-improvement is acting" reads.
            logger.info(
                f"[Engine] NOTE: machine-strategy self-improvement (self-healing adapt + "
                f"promotion loop) is DORMANT under SIGNAL_SOURCE={_SIG_RUN} — adaptive "
                f"config writes are not consumed by the live signal.")
        self.notifier.alert(
            f"Bot started | {TRADING_MODE.upper()} | "
            f"signal={_SIG_RUN} | "
            f"{'DRY RUN $' + str(int(self.order_mgr.wallet.start_balance)) if DRY_RUN else 'LIVE'}"
        )

        # ── CLAUDE PORTFOLIO: single unified cycle (replaces per-exchange scans + MCP brain)
        schedule.every(PORTFOLIO_CYCLE_SEC).seconds.do(self._claude_portfolio_cycle)
        _pos_mon_sec = CLAUDE_PORTFOLIO.get("position_monitor_sec", 120)
        schedule.every(_pos_mon_sec).seconds.do(self._run_mcp_position_monitor)
        schedule.every(NEWS_INTERVAL).seconds.do(self._fetch_news)
        schedule.every(LEARN_INTERVAL).seconds.do(self._run_learning)
        try:
            from config import ENABLE_DCA, ENABLE_REBALANCE
        except ImportError:
            ENABLE_DCA = False
            ENABLE_REBALANCE = False
        if ENABLE_DCA:
            schedule.every(4).hours.do(self._run_dca)
        if ENABLE_REBALANCE:
            schedule.every(24).hours.do(self._run_rebalance)

        # Spot portfolio evaluation (30 min) + capital allocation (15 min)
        if self.spot_manager:
            schedule.every(30).minutes.do(self._run_spot_evaluation)
        if self.capital_allocator:
            schedule.every(15).minutes.do(self._run_capital_allocation)

        # Daily self-check at midnight UTC
        schedule.every().day.at("00:00").do(self._daily_self_check)
        schedule.every().day.at("00:00").do(self._daily_summary)
        # Health watchdog tick — once per minute. Cheap, in-process.
        if self.watchdog is not None:
            schedule.every(60).seconds.do(self.watchdog.tick)
        if getattr(self, "self_healer", None) is not None:
            schedule.every(15).minutes.do(self._run_self_healing)
        # Autonomous PAPER self-improvement (WS4): mutate->validate->promote, hard-latched
        # to PAPER by core.decision.guardrails. Default-on in PAPER via SELF_IMPROVE_ENABLED;
        # never armed off-PAPER (the check is re-evaluated inside _run_self_improve too).
        try:
            from core.decision.guardrails import self_improve_enabled as _si_enabled

            if _si_enabled():
                schedule.every(LEARN_INTERVAL).seconds.do(self._run_self_improve)
                logger.info("[Engine] Self-improvement loop scheduled (PAPER, learn cadence)")
        except Exception:
            pass
        # ── Deep-breakout ACTIVE PAPER lane (owner directive 2026-07-11) ──
        # Places real PAPER orders (cohort strategy_family='deep_breakout';
        # ~33% WR by design — excluded from the accuracy-band metrics). The
        # log-only BreakoutProbeAgent keeps collecting frozen-gate evidence in
        # parallel. Scheduled ONLY in PAPER; the lane additionally re-refuses
        # every tick via assert_paper_only().
        try:
            from config import DEEP_BREAKOUT_LANE as _DBL_SCHED
            if _DBL_SCHED.get("enabled"):
                if DRY_RUN:
                    _dbl_tick = max(60, int(_DBL_SCHED.get("tick_sec", 300)))
                    schedule.every(_dbl_tick).seconds.do(self._run_deep_breakout_lane)
                    logger.info(
                        f"[Engine] Deep-breakout PAPER lane scheduled "
                        f"(venues={list(_DBL_SCHED.get('venues', ()))}, "
                        f"tick={_dbl_tick}s, 4h-boundary evaluation)")
                else:
                    logger.error(
                        "[Engine] DEEP_BREAKOUT_LANE_ENABLED but mode is "
                        "CONTROLLED_LIVE — lane REFUSED (PAPER-only hard gate; "
                        "going live requires an owner decision + code change)")
        except Exception as _dbl_e:
            logger.debug(f"[Engine] deep-breakout lane scheduling skipped: {_dbl_e}")

        # Balance refresh happens inside _claude_portfolio_cycle, but also on schedule
        schedule.every(15).minutes.do(self._log_balances)
        if not DRY_RUN:
            # Ghost detection cadence — was 5 minutes, which left up to a
            # 5-minute window between an exchange-side SL/TP fill and the
            # bot noticing the position vanished. That window is the root
            # cause of the high ghost_sync rate (memory:
            # project_ghost_closes_bypass_safety_rails_2026_04_26).
            # 15s is well within rate-limit budgets for fetch_positions
            # across all three exchanges (12 calls/min/venue ≪ any
            # documented limit) and brings ghost detection close enough
            # to the SL/TP monitor's own 10s cadence that warehouse PnL
            # tracks live state in near real time.
            schedule.every(15).seconds.do(self._sync_positions)

        if TRADING_MODE in ("portfolio", "all"):
            schedule.every(PORTFOLIO_RESCAN_MINUTES).minutes.do(
                self._rescan_portfolio)

        logger.info("[Engine] Running initial Claude portfolio cycle + learn...")
        self._run_learning()
        self._claude_portfolio_cycle()   # Claude makes first decisions
        self._run_mcp_position_monitor() # Position monitor after first cycle

        # Start dedicated SL/TP monitor thread — runs every 10s, never blocked by scans
        self._stop_event = threading.Event()
        self._sltp_thread = threading.Thread(
            target=self._sltp_monitor_loop,
            args=(self._stop_event,),
            daemon=True, name="sltp-monitor")
        self._sltp_thread.start()
        logger.info("[Engine] SL/TP monitor thread started (10s interval)")

        # Phase A multi-agent shadow runner — daemon thread, daemon=True so
        # bot exits cleanly even if shadow loop hangs. Live trading is never
        # blocked by shadow work.
        if self._shadow_runner is not None:
            self._shadow_thread = threading.Thread(
                target=self._shadow_loop,
                args=(self._stop_event,),
                daemon=True, name="shadow-runner")
            self._shadow_thread.start()
            try:
                from config import SHADOW_MODE
                _interval = SHADOW_MODE.get("tick_interval_s", 300)
            except Exception:
                _interval = 300
            logger.info(f"[Engine] Shadow runner thread started ({_interval}s interval)")

        # Register signal handlers for clean shutdown
        def _signal_handler(signum, frame):
            sig_name = signal.Signals(signum).name if hasattr(signal, 'Signals') else signum
            logger.info(f"[Engine] Signal {sig_name} received — shutting down")
            self._stop_event.set()
            self._shutdown()
            raise SystemExit(0)

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)
        atexit.register(self._shutdown)

        try:
            while True:
                schedule.run_pending()

                # Watchdog: restart SL/TP thread if it died (but not during shutdown)
                if not self._sltp_thread.is_alive() and not getattr(self, '_shutdown_done', False) and not self._stop_event.is_set():
                    logger.warning("[Engine] SL/TP thread DIED — restarting")
                    self._stop_event = threading.Event()
                    self._sltp_thread = threading.Thread(
                        target=self._sltp_monitor_loop,
                        args=(self._stop_event,),
                        daemon=True, name="sltp-monitor")
                    self._sltp_thread.start()
                    open_count = self.tracker.count_open()
                    self.notifier.error(
                        f"CRITICAL: SL/TP monitor thread crashed and was restarted.\n"
                        f"Open positions: {open_count}\n"
                        f"All positions were UNMONITORED until restart.\n"
                        f"Check logs for the crash cause.")

                # Watchdog: check exchange connectivity every 60s (time-based)
                if time.time() - self._last_health_check >= 60:
                    self._check_exchange_health()

                # Heartbeat: write status file every 60s for external monitors
                if time.time() - self._last_heartbeat >= 60:
                    self._last_heartbeat = time.time()
                    self._write_heartbeat()

                self._print_live_status()
                time.sleep(5)
        except (KeyboardInterrupt, SystemExit):
            logger.info("[Engine] Shutdown received")
            self._stop_event.set()
            self._shutdown()
        except Exception as e:
            logger.critical(f"[Engine] FATAL ERROR in main loop: {e}", exc_info=True)
            self._stop_event.set()
            try:
                open_count = self.tracker.count_open()
                self.notifier.error(
                    f"FATAL: Main loop crashed!\n"
                    f"Error: {e}\n"
                    f"Open positions: {open_count}\n"
                    f"Bot will attempt restart in 30s...")
            except Exception:
                pass
            self._shutdown()
            # Exit with non-zero code so auto_restart.bat restarts the process
            # (avoids recursive self.run() which would overflow the stack on repeated crashes)
            logger.info("[Engine] Exiting for auto-restart in 10s...")
            import sys
            sys.exit(1)

    def _shutdown(self):
        if getattr(self, '_shutdown_done', False):
            return
        self._shutdown_done = True
        schedule.clear()
        logger.info("[Engine] Shutting down...")
        s = self.tracker.summary()
        try:
            extras = self._build_daily_summary_extras(0.0)
        except Exception:
            extras = {}
        self.notifier.daily_summary(
            s["total_trades"], s["wins"], s["losses"], s["total_pnl"], 0.0,
            gross_pnl=s.get("gross_pnl"),
            total_fees=s.get("total_fees"),
            avg_win=s.get("avg_win"),
            avg_loss=s.get("avg_loss"),
            paper_trades=s.get("paper_trades"),
            live_trades=s.get("live_trades"),
            open_positions=s.get("open_positions"),
            **extras,
        )
        try:
            summary = self.order_mgr.compliance.export_summary()
            if summary:
                logger.info(
                    f"[Compliance] {summary.get('month')} — "
                    f"trades={summary.get('trades')} "
                    f"pnl={summary.get('total_pnl'):+.4f} USDT "
                    f"fees={summary.get('total_fees'):.4f} USDT")
        except Exception:
            pass
        self._run_learning()
        self._print_full_summary()
        logger.info("[Engine] Stopped.")

    def _daily_summary(self):
        s = self.tracker.summary()
        if DRY_RUN:
            balance = self.order_mgr.wallet.total_balance()
        else:
            # Daily summary shows user's actual wallet (free + locked margin),
            # NOT just free margin. Use equity extractor for an accurate picture.
            balance = 0.0
            for name, ex in self.active_exchanges.items():
                if name in _UNIFIED_EXCHANGES:
                    try:
                        bal = ex.fetch_balance("spot")
                        balance += self._extract_usdt_equity(bal, name)
                    except Exception:
                        pass
                else:
                    for mtype in ("spot", "futures"):
                        try:
                            bal = ex.fetch_balance(mtype)
                            balance += self._extract_usdt_equity(bal, name)
                        except Exception:
                            pass
        # Build rich extras for the daily email
        extras = self._build_daily_summary_extras(balance)
        self.notifier.daily_summary(
            s["total_trades"], s["wins"], s["losses"], s["total_pnl"], balance,
            gross_pnl=s.get("gross_pnl"),
            total_fees=s.get("total_fees"),
            avg_win=s.get("avg_win"),
            avg_loss=s.get("avg_loss"),
            paper_trades=s.get("paper_trades"),
            live_trades=s.get("live_trades"),
            open_positions=s.get("open_positions"),
            **extras,
        )

    def _build_daily_summary_extras(self, balance: float) -> dict:
        """Compute per-exchange, per-strategy, hour, best/worst, drawdown."""
        extras: dict = {}
        try:
            closed = list(self.tracker._closed)
        except Exception:
            closed = []

        # Per-exchange
        per_ex: dict = {}
        for p in closed:
            ex = getattr(p, "exchange", "?") or "?"
            d = per_ex.setdefault(ex, {"trades": 0, "wins": 0, "pnl": 0.0})
            d["trades"] += 1
            d["pnl"]    += float(p.pnl or 0)
            if (p.pnl or 0) > 0:
                d["wins"] += 1
        for ex, d in per_ex.items():
            d["win_rate"] = (d["wins"] / d["trades"] * 100) if d["trades"] else 0
        if per_ex:
            extras["per_exchange"] = per_ex

        # Per-strategy
        per_strat: dict = {}
        for p in closed:
            s = getattr(p, "strategy", "?") or "?"
            d = per_strat.setdefault(s, {"trades": 0, "wins": 0, "pnl": 0.0})
            d["trades"] += 1
            d["pnl"]    += float(p.pnl or 0)
            if (p.pnl or 0) > 0:
                d["wins"] += 1
        for s, d in per_strat.items():
            d["win_rate"] = (d["wins"] / d["trades"] * 100) if d["trades"] else 0
        if per_strat:
            extras["per_strategy"] = per_strat

        # Best / worst
        if closed:
            best = max(closed, key=lambda p: (p.pnl or 0))
            worst = min(closed, key=lambda p: (p.pnl or 0))
            extras["best_trade"] = {
                "symbol": best.symbol, "side": best.side,
                "pnl": float(best.pnl or 0),
            }
            extras["worst_trade"] = {
                "symbol": worst.symbol, "side": worst.side,
                "pnl": float(worst.pnl or 0),
            }

        # Drawdown from peak equity (running)
        try:
            equity = 0.0
            peak = balance
            running = balance - sum(float(p.pnl or 0) for p in closed)
            peak = running
            for p in sorted(closed, key=lambda x: float(getattr(x, "close_time", 0) or 0)):
                running += float(p.pnl or 0)
                if running > peak:
                    peak = running
            equity = running
            if peak > 0:
                extras["peak_equity"] = peak
                extras["drawdown_pct"] = (equity - peak) / peak * 100
        except Exception:
            pass

        # Unrealized open PnL
        try:
            open_pnl = 0.0
            for p in self.tracker.get_open():
                ex = self.active_exchanges.get(getattr(p, "exchange", ""))
                if not ex:
                    continue
                try:
                    tkr = ex.fetch_ticker(p.symbol, p.market_type)
                    last = float(tkr.get("last", 0) or 0)
                    if last <= 0:
                        continue
                    if p.side == "buy":
                        open_pnl += (last - p.entry_price) * p.size
                    else:
                        open_pnl += (p.entry_price - last) * p.size
                except Exception:
                    pass
            extras["open_pnl"] = open_pnl
        except Exception:
            pass

        # Halt status
        try:
            if self.risk.is_halted:
                extras["halt_status"] = self.risk.halt_reason or "HALTED"
        except Exception:
            pass

        # Hour scores from knowledge model (now that hour_scores include net_pnl)
        try:
            from core.knowledge_model import KnowledgeModel
            km = KnowledgeModel()
            hs = km._model.get("hour_scores") if hasattr(km, "_model") else None
            if hs:
                extras["hour_scores"] = hs
        except Exception:
            pass

        return extras

    def _print_live_status(self):
        s        = self.tracker.summary()
        uptime_m = int((time.time() - self._start_time) / 60)
        mode     = "[DRY]" if DRY_RUN else "[LIVE]"
        halted   = f" [HALTED: {self.risk.halt_reason}]" if self.risk.is_halted else ""
        fg       = self.news.fear_greed_value()
        logger.debug(
            f"{mode}{halted} up={uptime_m}m scans={self._cycle} "
            f"pnl={s['total_pnl']:+.4f} fees={s['total_fees']:.4f} "
            f"open={s['open_positions']} W={s['wins']} L={s['losses']} "
            f"F&G={fg}")

    def _print_full_summary(self):
        s      = self.tracker.summary()
        uptime = (time.time() - self._start_time) / 3600
        wallet = self.order_mgr.wallet.total_balance() if DRY_RUN else 0
        table  = Table(title="Trading Bot — Final Summary", box=box.ROUNDED)
        table.add_column("Metric", style="cyan", no_wrap=True)
        table.add_column("Value",  style="green")
        c = "green" if s["total_pnl"] >= 0 else "red"
        table.add_row("Uptime",        f"{uptime:.1f} hours")
        table.add_row("Mode",          f"{'DRY RUN ($' + str(int(self.order_mgr.wallet.start_balance)) + ' start)' if DRY_RUN else 'LIVE'}")
        table.add_row("Trade Mode",    TRADING_MODE.upper())
        table.add_row("Exchanges",     str(list(self.active_exchanges.keys())))
        table.add_row("Scan Cycles",   str(self._cycle))
        table.add_row("Total Trades",  str(s["total_trades"]))
        table.add_row("Wins / Losses", f"{s['wins']} / {s['losses']}")
        table.add_row("Win Rate",      f"{s['win_rate']:.1f}%")
        table.add_row("Gross PnL",     f"{s['gross_pnl']:+.4f} USDT")
        table.add_row("Fees Paid",     f"-{s['total_fees']:.4f} USDT")
        table.add_row("Net PnL",       f"[{c}]{s['total_pnl']:+.4f}[/{c}] USDT")
        table.add_row("Avg Win",       f"{s['avg_win']:+.4f} USDT")
        table.add_row("Avg Loss",      f"{s['avg_loss']:+.4f} USDT")
        if DRY_RUN:
            table.add_row("Paper Wallet", f"{wallet:.4f} USDT")
        table.add_row("Open Pos",      str(s["open_positions"]))
        table.add_row("Daily PnL",     f"[{c}]{self.risk.daily_pnl:+.4f}[/{c}] USDT")
        table.add_row("Fear & Greed",  f"{self.news.fear_greed_value()} — {self.news.fear_greed_label()}")
        table.add_row("Trending",      ", ".join(self.news.trending_coins()[:5]) or "—")
        if self.risk.is_halted:
            table.add_row("Halted", f"YES — {self.risk.halt_reason}")
        else:
            table.add_row("Halted", "No")
        table.add_row(
            "Blacklisted",
            str(list(self.order_mgr.blacklist.get_all().keys())) or "None")
        console.print(table)


# ── Clock-drift sampling (C8, tpbot retrofit 2026-07-08) ─────────────────────
def sample_clock_drift_ms(exchange) -> float | None:
    """One-shot NTP-style clock offset sample vs a venue, in milliseconds.

    offset = server_time - (t0 + t1) / 2 — the request midpoint compensates
    network latency to first order. Positive = the venue clock is AHEAD of
    this machine (local clock behind). The repo's own deployment notes call
    Windows clock drift "the #1 silent killer" of signed requests, yet until
    C8 drift was only detected AFTER a -1021/10002 rejection.

    Returns None when the raw ccxt client is missing, lacks fetch_time, or
    the call fails/returns 0 — a missing sample is NOT a health failure and
    must never be conflated with drift.
    """
    import time as _t
    try:
        raw = getattr(exchange, "exchange", None)
        if raw is None or not hasattr(raw, "fetch_time"):
            return None
        t0 = _t.time() * 1000.0
        server_ms = raw.fetch_time()
        t1 = _t.time() * 1000.0
        server_ms = float(server_ms or 0)
        if server_ms <= 0:
            return None
        return server_ms - ((t0 + t1) / 2.0)
    except Exception:
        return None
