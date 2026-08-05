"""
core/engine/engine.py — BotEngine assembly (Phase D5).
"""
from core.engine.helpers import (  # noqa: F401
    CLAUDE_PORTFOLIO,
    CapitalAllocator,
    DRY_RUN,
    MCPBrain,
    SLTP_TRIGGER_MARK_PRICE,
    SpotPortfolioManager,
    TRADING_MODE,
    _boot_profile_log_lines,
    _deployable_total,
    discover_all,
    logger,
    time,
)
from core.learning_engine import LearningEngine
from core.order_manager import OrderManager
from core.position_tracker import PositionTracker
from core.risk_manager import RiskManager
from exchanges import BinanceClient, BitgetClient, BybitClient
from utils import TelegramNotifier

from core.engine.close_exec import _CloseExecMixin
from core.engine.cycle import _CycleMixin
from core.engine.entry_exec import _EntryExecMixin
from core.engine.gate_health import _GateHealthMixin
from core.engine.imported_protect import _ImportedProtectMixin
from core.engine.jobs import _JobsMixin
from core.engine.lifecycle import _LifecycleMixin
from core.engine.monitors import _MonitorsMixin
from core.engine.portfolio_state import _PortfolioStateMixin
from core.engine.probes import _ProbesMixin
from core.engine.sizing_gates import _SizingGatesMixin


class _EngineInitMixin:
    def __init__(self):
        mode_label = "DRY RUN" if DRY_RUN else "LIVE TRADING"
        logger.info("=" * 60)
        logger.info("  TRADING BOT — Smart Scanner + Learning + News")
        logger.info(f"  Trade Mode : {TRADING_MODE.upper()}")
        logger.info(f"  Run Mode   : {mode_label}")
        for _boot_line in _boot_profile_log_lines():
            logger.info(_boot_line)
        logger.info("=" * 60)

        self.notifier  = TelegramNotifier()
        self.tracker   = PositionTracker()
        self.risk      = RiskManager()
        self.order_mgr = OrderManager(self.tracker, self.risk, self.notifier)
        self.order_mgr.enforce_entry_policy = True
        self.order_mgr.enforce_event_provenance = True
        # 2026-07-18: was hardcoded True, silently ignoring the documented C3
        # revert switch — with the mark path failing in-process this halted
        # the engine on every futures paper position. Now honors the flag.
        self.order_mgr.enforce_mark_price_triggers = SLTP_TRIGGER_MARK_PRICE
        logger.info(
            f"[Engine] SL/TP trigger mode: mark_price={SLTP_TRIGGER_MARK_PRICE}"
        )
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
            try:
                from config import UNIVERSE_FLOW_LOOSEN as _ufl

                if bool((_ufl or {}).get("enabled")):
                    from pathlib import Path as _P
                    from datetime import datetime as _dt, timezone as _tz
                    import json as _json

                    _cpath = _P(
                        _ufl.get("cohort_path") or "data/universe_flow_loosen_cohort.json"
                    )
                    if not _cpath.is_absolute():
                        _cpath = _P(__file__).resolve().parents[1] / _cpath
                    if not _cpath.exists():
                        _cpath.parent.mkdir(parents=True, exist_ok=True)
                        _cpath.write_text(
                            _json.dumps(
                                {
                                    "schema_version": 1,
                                    "enabled": True,
                                    "started_at_utc": _dt.now(_tz.utc).isoformat(),
                                    "review_after_days": float(
                                        _ufl.get("review_after_days") or 7
                                    ),
                                },
                                indent=2,
                            )
                            + "\n",
                            encoding="utf-8",
                        )
                        logger.info(f"[UniverseLoosen] cohort started → {_cpath}")
            except Exception as _ufl_exc:  # pragma: no cover — non-fatal
                logger.warning(f"[UniverseLoosen] cohort mark skipped: {_ufl_exc}")
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

        # WebSocket-first market/private data.  The hub is lazy: constructing
        # it starts only an idle event-loop thread; subscriptions are opened
        # when an execution candidate or tracked position names a symbol.
        self.realtime_streams = None
        if self.active_exchanges:
            try:
                from config import (
                    BINANCE_API_KEY,
                    BINANCE_SECRET_KEY,
                    BITGET_API_KEY,
                    BITGET_PASSPHRASE,
                    BITGET_SECRET_KEY,
                    BYBIT_API_KEY,
                    BYBIT_SECRET_KEY,
                )
                from core.realtime_hub import LazyRealtimeHub

                credentials = {
                    "binance": {
                        "apiKey": BINANCE_API_KEY,
                        "secret": BINANCE_SECRET_KEY,
                    },
                    "bybit": {
                        "apiKey": BYBIT_API_KEY,
                        "secret": BYBIT_SECRET_KEY,
                    },
                    "bitget": {
                        "apiKey": BITGET_API_KEY,
                        "secret": BITGET_SECRET_KEY,
                        "password": BITGET_PASSPHRASE,
                    },
                }
                self.realtime_streams = LazyRealtimeHub(
                    credentials=credentials,
                    reconciliation_callback=self._reconcile_realtime_stream,
                )
                self.order_mgr.realtime_streams = self.realtime_streams
                private_venues = sorted(
                    venue
                    for venue, values in credentials.items()
                    if values.get("apiKey") and values.get("secret")
                )
                logger.info(
                    "[Realtime] lazy WebSocket hub enabled; "
                    f"private read streams configured={private_venues}"
                )
            except Exception as exc:
                logger.warning(
                    "[Realtime] WebSocket hub unavailable; execution will use "
                    f"bounded REST fallback until restart ({type(exc).__name__})"
                )

        self._start_time = time.time()
        self._cycle      = 0
        self._consecutive_api_fails: dict[str, int] = {}  # Per-exchange fail counter
        self._exchange_halted: set[str] = set()         # Exchanges halted due to outage
        self._api_latency: dict[str, float] = {}        # Last health-check latency (ms)
        self._last_health_check = 0  # Time-based health check (not cycle-based)
        self._last_heartbeat    = 0  # Time-based heartbeat writer (60s interval)
        # Per-exchange, per-market-type USDT balances (populated by _log_balances)
        self._balances: dict[str, dict[str, float]] = {}
        self.order_mgr.portfolio_equity_provider = (
            lambda: _deployable_total(self._balances)
        )
        # Transfer cooldown: prevent repeated transfer attempts (5 min per route)
        self._last_transfer: dict[tuple, float] = {}  # (ex, from, to) → timestamp
        # Exchange position cache — for universal position monitor (60s TTL)
        self._exchange_positions_cache: list[dict] = []
        self._exchange_positions_time: float = 0
        # Live reconciliation can place SL/TP protection for imported
        # positions. Defer it until run() passes every authorization gate and
        # the read-only venue capability preflight.
        self._live_startup_reconciliation_pending = not DRY_RUN

        if not self.active_exchanges:
            logger.error("[Engine] No exchanges connected!")
        else:
            logger.info(f"[Engine] Connected: {list(self.active_exchanges.keys())}")
            self._log_balances()
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




class BotEngine(
    _LifecycleMixin,
    _JobsMixin,
    _MonitorsMixin,
    _ImportedProtectMixin,
    _CloseExecMixin,
    _EntryExecMixin,
    _CycleMixin,
    _SizingGatesMixin,
    _PortfolioStateMixin,
    _GateHealthMixin,
    _ProbesMixin,
    _EngineInitMixin,
):
    pass
