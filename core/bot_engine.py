"""
core/bot_engine.py — Smart Multi-Timeframe Bot Engine with Learning + News
Exchanges: Binance | Bybit | Bitget

FIX: _extract_usdt now handles Bybit Unified Account correctly.
     Bybit returns totalEquity in bal["total"]["USDT"], not bal["free"]["USDT"].
     Also: _log_balances now fetches Bybit balance ONCE (not spot+futures twice).
"""

import sys
import time
import json
import signal
import atexit
import threading
import schedule
from pathlib import Path
from loguru import logger
from rich.console import Console
from rich.table   import Table
from rich         import box
from concurrent.futures import ThreadPoolExecutor, as_completed

from datetime import datetime, timezone
from config import (
    TRADING_PAIRS, DRY_RUN, RISK,
    TRADING_MODE, PORTFOLIO_RESCAN_MINUTES, PORTFOLIO_MIN_VALUE_USD,
)
try:
    from config import CLAUDE_PORTFOLIO
except ImportError:
    CLAUDE_PORTFOLIO = {"enabled": True, "scan_interval_min": 15, "max_actions_per_cycle": 4}
from exchanges import (
    BinanceClient,
    BybitClient,   BitgetClient,
)
from exchanges.base         import BaseExchange
from core.risk_manager      import RiskManager
from core.order_manager     import OrderManager
from core.position_tracker  import PositionTracker
from core.learning_engine       import LearningEngine
from core.news_scanner          import NewsScanner
try:
    from core.pair_discovery    import discover_all
except ImportError:
    discover_all = None
try:
    from core.mcp_brain         import MCPBrain
except ImportError:
    MCPBrain = None
try:
    from core.spot_manager      import SpotPortfolioManager
except ImportError:
    SpotPortfolioManager = None
try:
    from core.capital_allocator import CapitalAllocator
except ImportError:
    CapitalAllocator = None
from utils import TelegramNotifier

console = Console()

MAX_PER_EXCHANGE    = CLAUDE_PORTFOLIO.get("max_per_exchange", 6)
MAX_TOTAL_POSITIONS = 8       # Max 8 total across all exchanges
NEWS_INTERVAL       = CLAUDE_PORTFOLIO.get("news_interval_min", 30) * 60
LEARN_INTERVAL      = CLAUDE_PORTFOLIO.get("learn_interval_min", 60) * 60
PORTFOLIO_CYCLE_SEC = CLAUDE_PORTFOLIO.get("scan_interval_min", 15) * 60
MAX_ACTIONS_PER_CYCLE = CLAUDE_PORTFOLIO.get("max_actions_per_cycle", 4)

# Exchanges that use a single Unified Account (fetch balance once only)
_UNIFIED_EXCHANGES = {"bybit"}


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
            logger.info("[Engine] AutoMutator enabled — mutations will self-apply from post-mortems")
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
        # Universe filter: runtime quality checks before trading
        try:
            from core.pair_discovery import UniverseFilter
            self.universe_filter = UniverseFilter()
        except ImportError:
            self.universe_filter = None

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
                from config import BLACKLIST_HARD, ALLOWED_HOURS_UTC
                bl = set(BLACKLIST_HARD or set())
                ah = set(ALLOWED_HOURS_UTC) if ALLOWED_HOURS_UTC else None
            except Exception:
                bl, ah = set(), None
            self._shadow_runner = ShadowRunner(
                warehouse=wh,
                ctx_provider=self._shadow_ctx_for_symbol,
                free_balance_provider=self._shadow_free_balance,
                symbols_provider=self._shadow_symbols,
                blacklist=bl, allowed_hours=ah,
                enabled_flag=self._shadow_enabled_flag,
            )
            logger.info("[Engine] ShadowRunner initialized (multi-agent, log-only)")
        except Exception as e:
            logger.error(f"[Engine] ShadowRunner init failed: {e}")
            self._shadow_runner = None

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
        """Universe for shadow eval — first 5 futures pairs (cap so OHLCV
        fetch budget stays bounded)."""
        out: list[str] = []
        for _ename, pairs in (self.current_pairs or {}).items():
            for sym in (pairs.get("futures") or [])[:2]:
                if sym not in out:
                    out.append(sym)
            if len(out) >= 5:
                break
        return out[:5]

    def _shadow_ctx_for_symbol(self, symbol: str) -> dict | None:
        """Best-effort OHLCV pull for the multi-timeframe agent context.
        Cached at the exchange-client level — no extra API hit beyond what
        live already does. Returns None if data is unavailable."""
        if not self.active_exchanges:
            return None
        ex = next(iter(self.active_exchanges.values()))
        try:
            import pandas as pd
            ctx: dict = {"symbol": symbol}
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
            EXPECTANCY_FILTER, ENTRY_STALENESS_EXIT, CELL_FILTER,
            SPOT_PORTFOLIO, SPOT_STRATEGY, MODEL_GATE, STAR_SYMBOLS,
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
        return (priority + others)[:40]

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
            BLACKLIST_HARD, WHITELIST_SYMBOLS, ALLOWED_HOURS_UTC,
            PEAK_HOURS_UTC, BLOCKED_HOURS_UTC, SHORTS_REQUIRE_BTC_BEAR,
            MAX_LOSS_PER_TRADE_PCT, LEVERAGE_TIERS,
        )

        total_open = self.tracker.count_open()
        max_new = max(0, RISK.get("max_open_positions", 8) - total_open)

        total_bal = sum(
            b.get("spot", 0) + b.get("futures", 0)
            for ex, b in self._balances.items()
            if ex not in _UNIFIED_EXCHANGES
        ) + sum(
            b.get("spot", 0)
            for ex, b in self._balances.items()
            if ex in _UNIFIED_EXCHANGES
        )

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

    def _load_dynamic_blocked_hours(self) -> set:
        """Read hour_gate_evidence.json and return its `blocked` set.

        Returns empty set when:
        - file is missing
        - file is older than _HOUR_GATE_MAX_AGE_DAYS (stale evidence is ignored)
        - file is malformed
        Cached for _HOUR_GATE_TTL_SEC so disk hits are bounded.
        """
        now = time.time()
        if (now - getattr(self, "_hour_gate_loaded_at", 0)) < self._HOUR_GATE_TTL_SEC:
            return getattr(self, "_hour_gate_cached", set())

        blocked: set = set()
        try:
            p = self._HOUR_GATE_PATH
            if p.exists():
                age_days = (now - p.stat().st_mtime) / 86400
                if age_days <= self._HOUR_GATE_MAX_AGE_DAYS:
                    data = json.loads(p.read_text(encoding="utf-8"))
                    raw = data.get("blocked") or []
                    blocked = {int(h) for h in raw if isinstance(h, (int, float))}
        except Exception as e:
            logger.debug(f"[HourGate] evidence load skipped: {e}")
            blocked = set()

        self._hour_gate_cached = blocked
        self._hour_gate_loaded_at = now
        return blocked

    def _classify_hour(self, hour: int) -> str:
        """Return 'peak' | 'allowed' | 'warmup' | 'blocked'.

        Combines the static config sets with `data/hour_gate_evidence.json`
        which is refreshed weekly by `scripts/refresh_hour_gates.py`.
        Evidence-based blocked hours take precedence over peak/warmup.
        """
        from config import (
            ALLOWED_HOURS_UTC, WARMUP_HOURS_UTC,
            PEAK_HOURS_UTC, BLOCKED_HOURS_UTC,
        )
        dynamic_blocked = self._load_dynamic_blocked_hours()
        if hour in BLOCKED_HOURS_UTC or hour in dynamic_blocked:
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
            from config import BTC_TREND_TIMEFRAME, BTC_TREND_EMA_PERIOD

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
            CONSEC_LOSS_DOWNGRADE_COUNT, CONSEC_LOSS_DOWNGRADE_HOURS,
            CONSEC_LOSS_PAUSE_COUNT,     CONSEC_LOSS_PAUSE_HOURS,
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
        from config import (LEVERAGE_TIERS, WHITELIST_SYMBOLS,
                             HIGH_ATR_PCT_THRESHOLD, STAR_SYMBOLS)

        hour = self._current_utc_hour()
        hour_class = self._classify_hour(hour)
        is_star = symbol in STAR_SYMBOLS

        if hour_class == "blocked":
            logger.info(
                f"[Tier] {symbol} REJECTED: hour {hour:02d} UTC is in BLOCKED_HOURS_UTC")
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
        # 2026-05-01: high-score anti-EV cap. score >= 85 in claude_portfolio
        # data is net-negative. Force STANDARD tier so we still take the
        # trade but don't size it up. tier_cap is also set by the consec-
        # loss throttle above; combine via "tightest wins".
        if mcp_score >= 85.0:
            tier_cap = "STANDARD"

        # Try tiers highest → lowest (20x → 15x → 10x → 5x)
        for tier_name in ("AGGRESSIVE", "CONVICTION", "STRONG", "STANDARD"):
            if tier_cap == "STANDARD" and tier_name != "STANDARD":
                continue

            t = LEVERAGE_TIERS[tier_name]
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
        return True  # UNBLOCK_ALL/A — clamp disabled for entry path

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

        logger.info(
            f"[Claude] Portfolio cycle: {len(all_coins)} coins, "
            f"{len(open_positions)} open positions, "
            f"balance=${risk_envelope.get('total_balance', 0):.0f}")

        actions = self.mcp_brain.analyze_portfolio(
            coins=all_coins,
            open_positions=open_positions,
            exchange_balances=dict(self._balances),
            risk_envelope=risk_envelope,
            recent_trades=recent_trades,
            news_context=news_context,
        )

        if not actions:
            logger.info("[Claude] No actions this cycle")
            self._cycle += 1
            return

        # Cap actions per cycle
        actions = actions[:MAX_ACTIONS_PER_CYCLE]

        executed = 0
        for action in actions:
            try:
                if action["type"] == "OPEN":
                    if self._execute_open(action):
                        executed += 1
                elif action["type"] == "CLOSE":
                    if self._execute_close(action):
                        executed += 1
            except Exception as e:
                logger.error(f"[Claude] Action execution error: {e}")

        # Fund ops (transfers between spot/futures)
        fund_ops = self.mcp_brain.last_fund_ops()
        if fund_ops:
            self._execute_fund_ops(fund_ops)

        self._cycle += 1
        logger.info(
            f"[Claude] Cycle complete: {executed}/{len(actions)} actions executed")

    def _execute_open(self, action: dict) -> bool:
        """Validate and execute an OPEN action from Claude. Returns True if executed."""
        from config import (
            BLACKLIST_HARD, SHORTS_REQUIRE_BTC_BEAR,
            OPERATING_MODE, CONTROLLED_LIVE_ENABLED,
            UNIVERSE_WHITELIST, TRADING_MODE,
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
            return False

        # ── LEARNING-FIRST MODE GATES (spec §3, §13) ─────────────────────
        # (A) OBSERVATION mode: never place any order, even paper. The
        #     candidate will still be recorded in the warehouse (Phase B),
        #     but execution short-circuits here.
        if OPERATING_MODE == "OBSERVATION":
            logger.info(
                f"[Mode] OBSERVATION — skipping execution for {ex_name}:{symbol} {side}"
            )
            return False

        # (B) CONTROLLED_LIVE requires the env latch in addition to config.
        if OPERATING_MODE == "CONTROLLED_LIVE" and not CONTROLLED_LIVE_ENABLED:
            logger.error(
                "[Mode] OPERATING_MODE=CONTROLLED_LIVE but CONTROLLED_LIVE_ENABLED "
                "env var is not 'true'. Refusing to place live orders. Aborting."
            )
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
                return False

        # (C.2) Short gate — 2026-04-24
        # SELL trades were running 14.3% WR (1W/6L) over the last 72h and 38.7%
        # over 30d vs buys at 45.6% / 41.9%. Block new shorts until a rolling
        # 30-SELL window clears 45% WR. Gate auto-re-enables once recovery WR
        # breaks 45% — no manual flag flip needed. Buys bypass the gate.
        if side == "sell":
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
                    return False
            except Exception as _ssfe:
                logger.debug(f"[ShortFilter] gate skipped ({_ssfe}) -- defaulting to ALLOW")

        # (D) Per-symbol pause (spec §12). Family pause is checked at (D.1)
        # below once strategy_family is known.
        if self.risk and self.risk.is_symbol_paused(symbol):
            logger.info(f"[Risk/Spec12] {symbol} is paused — skipping")
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
            from core.features  import FeatureVector as _FV
            from core.meta_filter import MetaFilter as _MetaFilter
            from core.warehouse  import get_warehouse as _get_wh
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
                    # Patch the candidate row's decision so the warehouse
                    # reflects the meta-filter SKIP, not the scorer's ALLOW.
                    try:
                        _get_wh().query(
                            "UPDATE candidates SET decision='SKIP', skip_reason=? WHERE id=?",
                            (f"meta:{_decision.reason}", _cid),
                        )
                    except Exception:
                        pass
                    return False
                if _decision.decision == "REVIEW":
                    # Phase D will ask ClaudeCode; for now, conservative SKIP.
                    try:
                        _get_wh().query(
                            "UPDATE candidates SET decision='REVIEW', skip_reason=? WHERE id=?",
                            (f"meta:{_decision.reason}", _cid),
                        )
                    except Exception:
                        pass
                    logger.info("[MetaFilter] REVIEW -> SKIP (Phase D pending)")
                    return False
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
            from config import CELL_FILTER as _CF, STAR_SYMBOLS as _STAR
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
                    return False

        # ── EXPECTANCY FILTER (2026-05-01 Tier 1.2) ──────────────────────
        # Per-symbol abstain: if recent realised mean PnL < min_expected,
        # skip even if cell-filter allowed. Self-correcting as data
        # accumulates — a STAR symbol whose recent performance flipped
        # negative gets auto-vetoed without manual STAR_SYMBOLS edits.
        # Returns None on insufficient evidence (< min_sample_size in
        # lookback) — we let those through, won't block on noise.
        try:
            from config import EXPECTANCY_FILTER as _EF
        except ImportError:
            _EF = {"enabled": False}
        if _EF.get("enabled", True):
            try:
                from core.warehouse import get_warehouse as _gw
                from config import STAR_SYMBOLS as _STAR_FOR_EF
                _sym_key = symbol if ":" in symbol else f"{symbol}:USDT"
                # Operator whitelist — bypass the floor entirely.
                _whitelist = _EF.get("whitelist") or set()
                if symbol in _whitelist or _sym_key in _whitelist:
                    logger.info(
                        f"[Expectancy] WHITELISTED {symbol} {side} — bypass floor"
                    )
                    _exp = None  # short-circuit; treat as insufficient-data path
                else:
                    _exp = _gw().recent_expectancy(
                        _sym_key,
                        side=side,
                        days=int(_EF.get("lookback_days", 30)),
                        min_n=int(_EF.get("min_sample_size", 5)),
                    )
                # STAR symbols use a relaxed floor (just must not be
                # net-negative). Cell-filter has already validated them
                # as proven-edge; the expectancy filter's role here is
                # to catch regime-flip into outright loss only.
                _is_star = (symbol in _STAR_FOR_EF or _sym_key in _STAR_FOR_EF)
                _floor = float(
                    _EF.get("min_expected_star", 0.00) if _is_star
                    else _EF.get("min_expected_dollar", 0.05)
                )
                if _exp is not None and _exp["mean"] < _floor:
                    _tag = "STAR" if _is_star else "non-STAR"
                    logger.info(
                        f"[Expectancy] BLOCKED {symbol} {side} ({_tag}): recent mean "
                        f"${_exp['mean']:+.3f} < ${_floor:.2f} floor "
                        f"(n={_exp['n']}, WR={_exp['win_rate']:.0%})")
                    try:
                        if (_cid := int(action.get("candidate_id") or 0)) > 0:
                            _gw().query(
                                "UPDATE candidates SET decision='SKIP', "
                                "skip_reason=? WHERE id=?",
                                (f"expectancy:mean${_exp['mean']:+.2f}<${_floor:.2f}",
                                 _cid),
                            )
                    except Exception:
                        pass
                    return False
            except Exception as _ee:
                logger.debug(f"[Expectancy] check skipped ({_ee}) — defaulting to ALLOW")

        # ── HIGH-WR MECHANISM GATES (applied BEFORE legacy checks) ──────

        # (a) Symbol blacklist — evidence-based hard block (static + dynamic)
        symbol_key = symbol if ":" in symbol else f"{symbol}:USDT"
        if symbol in BLACKLIST_HARD or symbol_key in BLACKLIST_HARD:
            logger.info(f"[Claude] BLOCKED by blacklist: {symbol}")
            return False
        if self.auto_mutator:
            dyn_bl = self.auto_mutator.get_effective_blacklist()
            if symbol in dyn_bl or symbol_key in dyn_bl:
                logger.info(f"[Claude] BLOCKED by dynamic (post-mortem) blacklist: {symbol}")
                return False
            if side == "sell" and self.auto_mutator.shorts_blocked():
                logger.info(
                    f"[Claude] BLOCKED: shorts disabled by AutoMutator "
                    f"(counter-trend short losses in recent post-mortems)")
                return False

        # (a2) Caution symbol — soft gate. Knowledge-model WR<35% triggers
        # caution, but high-conviction signals (conf>=0.90) still pass.
        # Rationale: the caution list is auto-built from historical losses,
        # many of which were driven by bot-side bugs (SL-placement failures,
        # stale-close rules) that have since been fixed. A permanent symbol
        # block punishes setups we'd now take. Confidence 0.90 is the
        # natural break in the 30d ALLOW histogram (~top 50%).
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
                        f"(<50% WR, conf={confidence:.2f} < 0.90)")
                    return False
        except Exception:
            pass

        # (b) Spot — buy-only (no short on spot)
        if market_type == "spot" and side == "sell":
            logger.warning(f"[Claude] BLOCKED: Cannot short on spot")
            return False

        # (c) BTC macro trend + side filter
        btc_trend = self._get_btc_trend()
        if side == "sell" and SHORTS_REQUIRE_BTC_BEAR and btc_trend != "bear":
            logger.info(
                f"[Claude] BLOCKED: SHORT {symbol} requires BTC 4h macro-bear, "
                f"current trend={btc_trend}")
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
            return False

        # Tier controls leverage; algorithm's ATR-based SL/TP are preferred
        # when provided (they adapt to each coin's volatility). Tier SL/TP
        # only used as fallback when the algorithm doesn't send its own.
        leverage = tier_params["leverage"]
        size_pct = tier_params["size_pct"] * 100.0   # selector uses fraction; rest of fn uses %
        algo_sl  = action.get("sl_pct", 0)
        algo_tp  = action.get("tp_pct", 0)
        if algo_sl > 0 and algo_tp > 0:
            sl_pct = algo_sl
            tp_pct = algo_tp
        else:
            sl_pct = tier_params["sl_pct"] * 100.0
            tp_pct = tier_params["tp_pct"] * 100.0

        # R:R validation (always > min_rr_ratio because tiers define tp > 2x sl, but sanity-check)
        min_rr = RISK.get("min_rr_ratio", 1.8)
        actual_rr = tp_pct / sl_pct if sl_pct > 0 else 0
        if actual_rr < min_rr:
            logger.info(
                f"[Claude] BLOCKED: {symbol} R:R {actual_rr:.2f}:1 "
                f"< {min_rr:.1f}:1 minimum (SL={sl_pct}% TP={tp_pct}%)")
            return False

        exchange = self.active_exchanges.get(ex_name)
        if not exchange:
            logger.warning(f"[Claude] Exchange '{ex_name}' not connected")
            return False

        # ── Exchange health gate — block new trades on halted exchanges ──
        if self.is_exchange_halted(ex_name):
            logger.warning(
                f"[Claude] BLOCKED: {ex_name} is HALTED (API unreachable) "
                f"— no new trades until recovered")
            return False

        # ── Strategy gate: block caution (<50% WR) and fee-heavy strategies ──
        strategy_name = action.get("strategy", "")

        # (D.1) Per-family pause (spec §12) — now that strategy_family is known.
        if strategy_name and self.risk and self.risk.is_family_paused(strategy_name):
            logger.info(f"[Risk/Spec12] strategy family '{strategy_name}' is paused — skipping")
            return False

        if strategy_name:
            try:
                from core.knowledge_model import KnowledgeModel
                km = KnowledgeModel()
                if km.is_caution_strategy(strategy_name):
                    logger.info(
                        f"[Claude] BLOCKED: strategy '{strategy_name}' is caution "
                        f"(<50% WR) — auto-disabled")
                    return False
                if strategy_name in km.get_fee_heavy_strategies():
                    logger.info(
                        f"[Claude] BLOCKED: strategy '{strategy_name}' is fee-heavy "
                        f"(fees >20% of gross profit) — auto-disabled")
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
                return False

        # Risk manager circuit breakers
        if not self.risk.can_trade(self.tracker.count_open()):
            logger.warning(f"[Claude] BLOCKED by risk manager: {self.risk.halt_reason}")
            return False

        # Per-exchange position limit
        ex_open = self.tracker.count_open(exchange=exchange.name)
        if ex_open >= MAX_PER_EXCHANGE:
            logger.info(f"[Claude] {ex_name}: {ex_open}/{MAX_PER_EXCHANGE} positions — full")
            return False

        # Total position limit (from config, not module constant)
        total_open = self.tracker.count_open()
        _max_positions = RISK.get("max_open_positions", 8)
        if total_open >= _max_positions:
            logger.info(f"[Claude] Total {total_open}/{_max_positions} — full")
            return False

        # No duplicate base asset on same exchange
        base_asset = symbol.split("/")[0]
        all_open = self.tracker.get_open(exchange=exchange.name)
        already_has = any(
            p.symbol.split("/")[0] == base_asset for p in all_open
        )
        if already_has:
            logger.info(f"[Claude] {base_asset} already open on {ex_name} — skip")
            return False

        # Correlation check — prevent over-concentration in correlated assets
        total_bal = sum(
            b.get("spot", 0) + b.get("futures", 0)
            for ex, b in self._balances.items()
            if ex not in _UNIFIED_EXCHANGES
        ) + sum(
            b.get("spot", 0)
            for ex, b in self._balances.items()
            if ex in _UNIFIED_EXCHANGES
        )
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
                logger.info(f"[Claude] Auto-transfer ${xfer:.2f} {other}->{market_type} on {ex_name}")
                try:
                    if exchange.transfer(xfer, other, market_type):
                        ex_bals[other] -= xfer
                        ex_bals[market_type] = mtype_bal + xfer
                        mtype_bal += xfer
                except Exception as e:
                    logger.debug(f"[Claude] Auto-transfer failed: {e}")
            if mtype_bal < min_trade_bal:
                logger.info(f"[Claude] {ex_name} {market_type} balance ${mtype_bal:.2f} < ${min_trade_bal}")
                return False

        # Add :USDT suffix for futures
        trade_symbol = symbol
        if market_type == "futures" and ":" not in symbol:
            trade_symbol = symbol + ":USDT"

        # Compute position size from size_pct (apply correlation + meta-filter reduction)
        size_fraction = size_pct / 100.0
        corr_mult = corr_info.get("size_multiplier", 1.0) if total_bal > 0 else 1.0
        if corr_mult < 1.0:
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
        notional = mtype_bal * size_fraction
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
            return False

        # ── Hard loss clamp — final safety rail ──
        # Rejects any trade where worst-case loss at SL exceeds
        # MAX_LOSS_PER_TRADE_PCT of the market-type balance. This is the
        # guardrail that makes 20x tiers survivable: a 20x AGGRESSIVE trade
        # with 0.8% SL on 2% of balance only risks 0.32% — well under the $2 cap.
        _clamp_lev = leverage if market_type == "futures" else 1
        if not self._within_loss_clamp(mtype_bal, notional, _clamp_lev, sl_pct / 100.0):
            return False

        # Get current price for sizing
        try:
            ticker = exchange.fetch_ticker(trade_symbol, market_type)
            price = float(ticker.get("last", 0) or 0)
        except Exception as e:
            logger.warning(f"[Claude] fetch_ticker {trade_symbol}: {e}")
            return False
        if price <= 0:
            return False

        # Compute SL/TP prices
        if side == "buy":
            stop_loss   = price * (1 - sl_pct / 100)
            take_profit = price * (1 + tp_pct / 100)
        else:
            stop_loss   = price * (1 + sl_pct / 100)
            take_profit = price * (1 - tp_pct / 100)

        # Compute size in base units
        if market_type == "futures":
            size = (notional * leverage) / price
        else:
            size = notional / price

        # Pre-check: will this size survive exchange rounding?
        # BTC at $83k with step=0.001 needs min $83 notional (or $16.6 at 5x).
        # Skip early instead of wasting API calls on doomed orders.
        try:
            step = exchange.get_amount_precision(trade_symbol)
            if step > 0 and size < step:
                min_notional = step * price / max(leverage, 1)
                logger.info(
                    f"[Claude] {symbol}: size {size:.8f} < step {step} "
                    f"(need ${min_notional:.0f} at {leverage}x, have ${notional:.0f}) — skip")
                return False
        except Exception:
            pass

        # Set leverage
        if market_type == "futures" and leverage > 1:
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
                strategy="claude_portfolio",
                size=size, price=price,
                sl=stop_loss, tp=take_profit,
                leverage=leverage,
                candidate_id=_cid if (_cid or 0) > 0 else None,
                mcp_score=action.get("mcp_score"),
                model_version=action.get("model_version"),
            )
            if pos is None:
                logger.info(f"[Claude] open_position returned None — rejected by order manager")
                return False
            # Warehouse record_trade_open now happens inside open_position
            # (before SL placement) so fail-closed paths don't lose the row.
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
            return False

    def _execute_close(self, action: dict) -> bool:
        """Find and close a position by ID. Returns True if closed."""
        # Spec §4: OBSERVATION mode must not send any live orders
        from config import OPERATING_MODE as _close_mode
        if _close_mode == "OBSERVATION":
            logger.info("[Mode] OBSERVATION — close blocked (no live orders)")
            return False

        position_id = action.get("position_id", "")
        reason = action.get("reason", "claude_portfolio_close")

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

        # Find exchange client
        exchange = None
        for ex_name, ex in self.active_exchanges.items():
            if ex_name == target.exchange.lower() or ex_name in target.exchange.lower():
                exchange = ex
                break

        if not exchange:
            logger.warning(f"[Claude] Exchange for {target.exchange} not connected")
            return False

        logger.info(
            f"[Claude] EXECUTING CLOSE: {target.symbol} {target.side} "
            f"on {target.exchange} | {reason[:60]}")

        try:
            self.order_mgr.close_position(exchange, target, reason)
            return True
        except Exception as e:
            logger.error(f"[Claude] close_position failed: {e}")
            return False

    # ── Scheduled tasks ───────────────────────────────────────────────

    def _check_all_sl_tp(self):
        """Check SL/TP for all open positions — parallel across exchanges."""
        if self.tracker.count_open() == 0:
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

    def _write_heartbeat(self):
        """Write heartbeat file for external monitoring."""
        try:
            import psutil, os
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
            "last_trade_time": last_trade_time,
            "active_exchanges": list(self.active_exchanges.keys()),
            "halted_exchanges": list(self._exchange_halted),
            "api_latency_ms": {k: round(v, 0) for k, v in self._api_latency.items()},
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
                    prev_fails = self._consecutive_api_fails.get(ex_name, 0)
                    self._consecutive_api_fails[ex_name] = 0
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
                logger.warning(
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

    def _run_mcp_position_monitor(self):
        """Run MCP Brain position monitor — scans ALL positions across ALL exchanges.
        Merges bot-tracked positions with exchange-discovered positions (futures + spot).
        MCP Brain is the SOLE authority on hold/close/take-profit for the entire portfolio."""
        if not self.mcp_brain or not self.mcp_brain.is_enabled:
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

                    if action == "TAKE_PROFIT":
                        # Fee-aware: estimate round-trip fees and require net profit >= 0.5%
                        from config import FEE
                        if p.market_type == "futures":
                            rt_fee_pct = (FEE.get("futures_maker", 0.0002) + FEE.get("futures_taker", 0.0005)) * 100
                        else:
                            rt_fee_pct = (FEE.get("spot_maker", 0.001) + FEE.get("spot_taker", 0.001)) * 100
                        net_pnl_pct = _pnl_pct - rt_fee_pct
                        if net_pnl_pct >= 0.5 and conf >= 0.60:
                            for ex_name, exchange in self.active_exchanges.items():
                                if ex_name == p.exchange.lower() or ex_name in p.exchange.lower():
                                    logger.warning(
                                        f"[MCP-Brain] TAKE PROFIT: {p.symbol} {p.side} "
                                        f"pnl={_pnl_pct:+.1f}% net~{net_pnl_pct:+.1f}% "
                                        f"conf={conf:.0%} — {reason}")
                                    self.order_mgr.close_position(
                                        exchange, p, "mcp_take_profit")
                                    break
                        else:
                            logger.debug(
                                f"[MCP-Brain] TAKE_PROFIT skipped: {p.symbol} "
                                f"pnl={_pnl_pct:+.1f}% net~{net_pnl_pct:+.1f}% conf={conf:.0%} "
                                f"(need net>=0.5% + conf>=60%)")

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
                        # Higher thresholds for external positions (unknown age/history)
                        if action == "CLOSE" and conf < 0.85:
                            logger.debug(
                                f"[MCP-Brain] EXT CLOSE skipped: {pos_entry['symbol']} on {ex_name} "
                                f"conf={conf:.0%} < 85% threshold")
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

    def _close_external_position(self, ex_name: str, pos: dict, reason: str):
        """Close an exchange-discovered position NOT tracked internally.
        Futures: market close order. Spot: market sell coins."""
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

    # ── Main run ──────────────────────────────────────────────────────

    def run(self):
        # Spec Appendix B: refuse to run CONTROLLED_LIVE without a signed checklist.
        try:
            from config import OPERATING_MODE as _op_mode
            from core.live_gate import enforce_controlled_live_gate
            enforce_controlled_live_gate(_op_mode)
        except SystemExit:
            raise
        except Exception as _e:
            logger.warning(f"[LiveGate] check error (non-fatal): {_e}")

        logger.info("[Engine] Bot started — Ctrl+C to stop")
        self.notifier.alert(
            f"Bot started | {TRADING_MODE.upper()} | "
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
