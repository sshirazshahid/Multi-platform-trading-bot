"""
core/order_mgmt/manager.py — OrderManager assembly (Phase D4).
"""
import json
import threading
import time
from pathlib import Path

from loguru import logger

from config import DRY_RUN, RISK
from core.blacklist_manager import BlacklistManager
from core.compliance_logger import ComplianceLogger
from core.kelly_sizer import KellySizer
from core.order_mgmt.closing import _ClosingMixin
from core.order_mgmt.entry import _EntryMixin
from core.order_mgmt.funds import _FundsMixin
from core.order_mgmt.helpers import (
    CLOSE_FAIL_COUNT_PATH,
    ORDER_MODE_STATE_PATH,
    PAPER_FUNDING_WINDOWS_PATH,
    PENDING_MAKER_ENTRIES_PATH,
    SL_WIDENED_STATE_PATH,
)
from core.order_mgmt.maker_first import _MakerFirstMixin
from core.order_mgmt.monitor import _MonitorMixin
from core.order_mgmt.paper_funding import _PaperFundingMixin
from core.order_mgmt.protection import _ProtectionMixin
from core.order_mgmt.provenance import _ProvenanceMixin
from core.order_mgmt.state import _StateMixin
from core.position_tracker import PositionTracker
from core.post_mortem import PostMortem
from core.risk_manager import RiskManager
from core.sim_execution import SimExecutionModel
from core.smart_executor import SmartExecutor
from core.trailing_stop_manager import TrailingStopManager
from core.virtual_wallet import VirtualWallet
from utils.notifier import TelegramNotifier


class OrderManager(
    _MonitorMixin,
    _MakerFirstMixin,
    _PaperFundingMixin,
    _ClosingMixin,
    _ProtectionMixin,
    _EntryMixin,
    _FundsMixin,
    _ProvenanceMixin,
    _StateMixin,
):
    def __init__(self, tracker: PositionTracker, risk: RiskManager,
                 notifier: TelegramNotifier):
        self.tracker    = tracker
        self.risk       = risk
        self.notifier   = notifier
        self.dry_run    = DRY_RUN
        self.trailing   = TrailingStopManager()
        self.blacklist  = BlacklistManager()
        self.compliance = ComplianceLogger(dry_run=DRY_RUN)
        self.wallet     = VirtualWallet()
        self.post_mortem = PostMortem()
        self.kelly       = KellySizer()
        self.executor    = SmartExecutor()
        self.sim         = SimExecutionModel()  # DRY_RUN realism
        self.mcp_brain   = None  # Set by bot_engine after construction
        # Application composition enables this final new-exposure latch. It is
        # opt-in here so isolated research/backtest uses of OrderManager remain
        # deterministic; BotEngine enables it immediately after construction.
        self.enforce_entry_policy = False
        self.enforce_event_provenance = False
        self.enforce_mark_price_triggers = False
        # BotEngine composes this with its PAPER deployable-equity snapshot.
        # Maker resolution fails closed when the provider is absent or invalid.
        self.portfolio_equity_provider = None
        self._mark_data_incidents: set[str] = set()
        # Provenance (2026-06-12): last open_position rejection reason —
        # read by bot_engine when open_position returns None so the
        # rejection row carries the order-manager-level cause.
        self.last_open_reject: str | None = None
        # Exchange registry for paths (like _finalize_close) that don't
        # receive an exchange parameter. BotEngine populates this via
        # set_exchanges() after constructing its exchange dict.
        self._exchanges: dict = {}
        # LazyRealtimeHub is composed by BotEngine.  Keeping this optional
        # preserves deterministic isolated tests while production entry and
        # maker-resolution paths prefer a healthy venue WebSocket book.
        self.realtime_streams = None
        # Track exchanges where futures is disabled (don't retry)
        # Track exchanges using One-Way Mode (no positionSide param)
        # Bitget is ALWAYS one-way — pre-populate to avoid first-order 40774 error
        self._order_mode_path = ORDER_MODE_STATE_PATH
        saved = self._load_order_mode_state()
        self._futures_disabled = saved.get("futures_disabled", set())
        self._oneway_mode = saved.get("oneway_mode", {"bitget"})
        # Track positions where SL was already widened once (prevent infinite widening)
        self._sl_widened_path = SL_WIDENED_STATE_PATH
        self._sl_widened = self._load_sl_widened()

        # Idempotency: track recently placed client order IDs to avoid duplicates
        self._recent_client_ids: set = set()
        self._recent_client_ids_max = 500

        # DRY_RUN funding accrual: last settled 8h window PER EXCHANGE.
        # Real exchanges settle funding at 00/08/16 UTC; we settle when the
        # current UTC hour first matches one of those after the last tick.
        # Keyed by exchange name: accrue_paper_funding runs once per venue, so a
        # shared scalar let the FIRST venue each window consume it for ALL —
        # only that venue accrued funding (audit 2026-07-07).
        # A5 (2026-07-16): persisted — in-memory only, a restart inside a
        # settled window forgot it and charged funding a second time.
        self._funding_windows_path = PAPER_FUNDING_WINDOWS_PATH
        self._last_funding_hour: dict = self._load_funding_windows()

        # MAKER-FIRST PAPER ENTRIES (2026-07-10): pending virtual post-only
        # entry intents keyed "Exchange:SYMBOL", plus soak counters. Persisted
        # to data/pending_maker_entries.json so a restart CANCELS cleanly
        # (never ghost-opens). Boot is lazy (first enabled use) so the file is
        # untouched while the flag is off.
        self._pending_maker: dict = {}
        # Registration and resolver ticks can run on different threads.  The
        # lock makes the conflict check + reservation insert atomic, and keeps
        # a resolving intent reserved until it has a terminal outcome.
        self._pending_maker_lock = threading.RLock()
        self._maker_resolving: set[str] = set()
        self._maker_counters: dict = {
            "maker": 0, "taker_fallback": 0, "abandoned": 0}
        # Per-maker-fill measurement rows (signal_px vs fill_px delta) for
        # the soak readout; persisted alongside the counters, capped at 200.
        self._maker_fills: list = []
        self._pending_maker_path = PENDING_MAKER_ENTRIES_PATH
        self._maker_first_booted = False

        # Close failure counter: {position_id: fail_count}
        # After 3 failures, force-close in tracker to break infinite loops
        self._close_fail_path = CLOSE_FAIL_COUNT_PATH
        self._close_fail_count: dict = self._load_close_fail_count()

        # B7-P2 (audit 2026-06-21) — per-position close/SL-mutation lock.
        # One RLock per position id (RLock is MANDATORY: _replace_exchange_sl ->
        # _place_exchange_sl_tp fail-closed re-enters close_position on the SAME
        # thread; a plain Lock would self-deadlock the SL monitor). Default-OFF
        # (PER_POSITION_LOCK_ENABLED) — LIVE-only value, fully inert in PAPER.
        from config import PER_POSITION_LOCK_ENABLED as _ppl
        self._per_pos_lock_enabled = bool(_ppl)
        self._pos_locks: dict = {}
        self._pos_locks_guard = threading.Lock()
        # Acquire timeout (< the 10s SL/TP monitor cadence) so a stuck lock can
        # never freeze/stack the monitor; on timeout we proceed unserialized.
        self._pos_lock_timeout = 5.0

        if DRY_RUN:
            logger.warning(
                "[Orders] DRY RUN MODE — no real orders placed. "
                "Paper wallet starts at $"
                + str(int(self.wallet._start))
                + " USDT per exchange."
            )
            logger.info(self.wallet.statement())
        else:
            logger.warning("[Orders] LIVE MODE — real orders will be placed.")

