"""
core/engine/helpers.py — BotEngine module helpers and constants (Phase D5).

FIX: _extract_usdt now handles Bybit Unified Account correctly.
     Bybit returns totalEquity in bal["total"]["USDT"], not bal["free"]["USDT"].
     Also: _log_balances now fetches Bybit balance ONCE (not spot+futures twice).
"""

import atexit
import json
import math
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
    SLTP_TRIGGER_MARK_PRICE,
    TRADING_MODE,
    TRADING_PAIRS,
)

try:
    from config import PORTFOLIO_CYCLE as CLAUDE_PORTFOLIO
except ImportError:
    CLAUDE_PORTFOLIO = {"enabled": True, "scan_interval_min": 15, "max_actions_per_cycle": 4}
from core.learning_engine import LearningEngine
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
from utils.atomic_io import atomic_write_json

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


def _live_entry_clock_drift_rejection(
    operating_mode: str,
    exchange_name: str,
    drift_by_exchange: dict | None,
    threshold_ms: float,
) -> str | None:
    """Fail closed on missing or excessive venue clock drift in live mode."""

    if str(operating_mode or "").upper() != "CONTROLLED_LIVE":
        return None
    raw = (drift_by_exchange or {}).get(str(exchange_name or "").lower())
    if isinstance(raw, bool):
        return "clock_drift_unavailable"
    try:
        drift_ms = float(raw)
        threshold = float(threshold_ms)
    except (TypeError, ValueError):
        return "clock_drift_unavailable"
    if not math.isfinite(drift_ms) or not math.isfinite(threshold) or threshold <= 0:
        return "clock_drift_unavailable"
    if abs(drift_ms) > threshold:
        return "clock_drift_exceeded"
    return None


def _is_mcp_directional_paper_futures(
    strategy_id: str,
    market_type: str,
    operating_mode: str,
    *,
    is_tsmom: bool = False,
) -> bool:
    """Return whether the P0 economic gate owns this entry.

    Catalog aliases (``mcp_registry``/``algo_det``) resolve to the canonical
    ``MCP_DIRECTIONAL_PAPER`` ID.  Explicitly excluding tsmom keeps its
    momentum-flip/no-TP contract out of a bracket-expectancy calculation.
    Carry and deep-breakout use separate runners and never enter this path.
    """

    if is_tsmom or str(operating_mode or "").strip().upper() != "PAPER":
        return False
    if str(market_type or "").strip().lower() not in {
        "future",
        "futures",
        "perp",
        "perpetual",
        "swap",
    }:
        return False
    try:
        from core.strategy_program import strategy_program_entry

        entry = strategy_program_entry(strategy_id)
        return bool(
            entry is not None
            and entry.spec.strategy_id == "MCP_DIRECTIONAL_PAPER"
        )
    except Exception:
        # The strategy catalog is an authorization dependency.  The caller has
        # already passed that gate, but an import failure here must not turn the
        # economic rule into an accidental bypass for its known aliases.
        return str(strategy_id or "").strip().lower() in {
            "mcp_directional_paper",
            "mcp_registry",
            "algo_det",
        }


_MCP_DIRECTIONAL_RESEARCH_STRATEGY_TAGS = frozenset({
    "claude_portfolio",
    "algo_det",
    "mcp_registry",
    "mcp_directional_paper",
})


def _utc_day_bounds(now_ts: float) -> tuple[float, float]:
    """Return [start, end) epoch bounds for the UTC calendar day of now_ts."""
    from datetime import datetime, timezone

    start = datetime.fromtimestamp(float(now_ts), tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ).timestamp()
    return start, start + 86400.0


def _is_mcp_directional_research_position(pos) -> bool:
    if bool(getattr(pos, "_accuracy_band", False)):
        return True
    strat = str(getattr(pos, "strategy", "") or "").strip().lower()
    return strat in _MCP_DIRECTIONAL_RESEARCH_STRATEGY_TAGS


def count_mcp_directional_opens_utc_day(tracker, *, now_ts: float | None = None) -> int:
    """Count MCP directional futures opens (open+closed) in the current UTC day.

    Used by the AccBand research tuition cap. Does not count F1 carry or
    shadow-probe lanes.
    """
    import time as _time

    if tracker is None:
        return 0
    now = float(now_ts if now_ts is not None else _time.time())
    start, end = _utc_day_bounds(now)
    positions = []
    try:
        positions.extend(tracker.get_open() or [])
    except Exception:
        pass
    try:
        with tracker._lock:
            positions.extend(list(tracker._closed))
    except Exception:
        pass
    n = 0
    for pos in positions:
        ot = float(getattr(pos, "open_time", 0) or 0)
        if not (start <= ot < end):
            continue
        mt = str(getattr(pos, "market_type", "") or "").lower()
        if mt not in {"future", "futures", "perp", "perpetual", "swap"}:
            continue
        if not _is_mcp_directional_research_position(pos):
            continue
        n += 1
    return n


def accband_research_open_budget_allows(
    tracker,
    max_opens,
    *,
    now_ts: float | None = None,
) -> tuple[bool, int]:
    """Return (allowed, opens_today). max_opens=None disables the cap."""
    if max_opens is None:
        return True, 0
    count = count_mcp_directional_opens_utc_day(tracker, now_ts=now_ts)
    return count < int(max_opens), count


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
LEARN_INTERVAL      = CLAUDE_PORTFOLIO.get("learn_interval_min", 60) * 60
PORTFOLIO_CYCLE_SEC = CLAUDE_PORTFOLIO.get("scan_interval_min", 15) * 60
MAX_ACTIONS_PER_CYCLE = CLAUDE_PORTFOLIO.get("max_actions_per_cycle", 4)

# Exchanges that use a single Unified Account (fetch balance once only).
# Shared with the deployable-balance helper so the unified set and the
# PAPER-aware aggregation never drift apart.
from core.balance_utils import UNIFIED_EXCHANGES as _UNIFIED_EXCHANGES
from core.balance_utils import deployable_total as _deployable_total

# Wiring/skew failures — a missing symbol, a renamed attribute, a typo in a
# lazily-imported path. These are never per-venue noise: they break the same
# way on every call until someone ships a fix, so they must be loud wherever
# a broad `except Exception` would otherwise swallow them (2026-07-26).
_STRUCTURAL_ERRORS = (ImportError, AttributeError, NameError)


# 2026-08-19 council: the bot had been idle for 52h with the banner showing
# only "EntryPolicy: SHADOW_ONLY". The FACT was visible; the REASON was not,
# and the owner asked "why no trades" four times. Idle-by-design must say so,
# say whose decision it was, and say when it will be revisited.
IDLE_POLICY_ORIGIN = "owner directive 'maximize PAPER then cash', 2026-08-18"
IDLE_POLICY_BASIS = ("cash beat every measured mask on 2,109 closed trades "
                     "(_workspace/strategy_pipeline/73_plan_paper_then_cash.md)")
IDLE_POLICY_NEXT_REVIEW = "Polymarket prereg screen, ~2026-09-08 (needs 2-4wk accrual)"


def idle_by_policy_lines(entry_policy) -> list:
    """Banner lines explaining a by-design idle state. Empty when trading."""
    if str(entry_policy or "").upper() not in ("SHADOW_ONLY", "PROTECT_ONLY"):
        return []
    return [
        f"  IdleByPolicy: NEW ENTRIES OFF by {IDLE_POLICY_ORIGIN}",
        f"                basis: {IDLE_POLICY_BASIS}",
        f"                shadow probes + Polymarket keep accruing; next review: "
        f"{IDLE_POLICY_NEXT_REVIEW}",
        "                to resume PAPER flow: ENTRY_POLICY=APPROVED_PAPER + supervisor restart",
    ]


def _boot_profile_log_lines() -> list:
    """One in-process boot log line per max-flow knob (2026-07-19 spec T5).

    Restart verification rule: threshold / cooldown / geometry / profile
    must be readable from the NEW boot log itself — never re-derived by a
    subprocess re-parse. Values are read from config at call time so the
    lines always describe THIS process."""
    try:
        from config import (
            ACCURACY_TARGET_MODE as _acc,
        )
        from config import (
            ENTRY_POLICY as _entry_policy,
        )
        from config import (
            MCP_DIRECTIONAL_ECONOMIC_GATE as _egate,
        )
        from config import (
            MCP_ENTRY_MIN_SCORE as _floor,
        )
        from config import (
            PAPER_PROFILE_STARTED_AT as _epoch,
        )
        from config import (
            PAPER_TRADING_PROFILE as _profile,
        )
        from config import (
            SIGNAL_SOURCE as _sig,
        )
        from config import (
            SL_COOLDOWN_ENABLED as _sl_cd,
        )
    except Exception as exc:  # pragma: no cover — config import is load-bearing
        return [f"  Profile   : UNAVAILABLE ({exc})"]
    floor_txt = "default(66/65)" if _floor is None else f"{_floor:g}"
    acc_on = bool(_acc.get("enabled"))
    frac_buy = _acc.get("tp_frac_buy") or _acc.get("tp_frac_of_sl")
    frac_sell = _acc.get("tp_frac_sell") or _acc.get("tp_frac_of_sl")
    try:
        from config import BAND_REGIME_FILTER_ENABLED as _brf
    except Exception:  # pragma: no cover
        _brf = False
    try:
        from config import SMART_MONEY_ENTRY_GATE as _smg_cfg
        _smg = bool(_smg_cfg.get("enabled"))
    except Exception:  # pragma: no cover
        _smg = False
    lines = [
        f"  SignalSrc : {_sig}",
        f"  EntryPolicy: {_entry_policy}",
        *idle_by_policy_lines(_entry_policy),
        f"  Profile   : {_profile} (epoch={_epoch or 'n/a'})",
        f"  EntryFloor: MCP_ENTRY_MIN_SCORE={floor_txt}",
        f"  SLCooldown: {'enabled' if _sl_cd else 'DISABLED (sl_cooldown_disabled_by_profile)'}",
        (
            f"  AccBand   : {'ON' if acc_on else 'OFF'}"
            + (f" (fracs buy={frac_buy}/sell={frac_sell})" if acc_on else "")
        ),
        f"  BandRegime: {'ON (ADX>30 / BTC vol<0.7 veto)' if _brf else 'OFF'}",
        (
            f"  SmartMoney: {'ON (hard entry gate)' if _smg else 'OFF'}"
        ),
        f"  EconGate  : mode={_egate.get('mode', 'strict')}",
        (
            "  TradFi    : blocked (USDT-M oil/metal/stock perps; not CME; "
            "tradfi_asset + AccBand scope — ANALYSIS_ONLY_ENFORCED is not the switch)"
        ),
    ]
    try:
        from config import UNIVERSE_FLOW_LOOSEN as _ufl

        if bool((_ufl or {}).get("enabled")):
            lines.append(
                "  UniverseLoosen: ON (V1 mild spread/depth/chop — 7d review)"
            )
        else:
            lines.append("  UniverseLoosen: OFF")
    except Exception:  # pragma: no cover
        lines.append("  UniverseLoosen: UNAVAILABLE")
    # Statistical contract (2026-07-24 harden): AccBand shapes WR by geometry;
    # dual-goal (band WR + profit) is CONFIRMED_NO_GO on the measured no-edge path.
    if acc_on:
        lines.append(
            "  AccBandNote: WR geometry research only; dual-goal profit "
            "CONFIRMED_NO_GO (screen 30_*); expectancy ~-0.24R class - not edge"
        )
    return lines


def smart_money_entry_rejection(
    side: str,
    sm: dict | None,
    *,
    enabled: bool,
    fail_open_stale: bool = True,
) -> str:
    """Pure gate for Approach-1 smart-money hard entry (2026-07-24).

    Returns reject_reason or "" to allow. When ``enabled`` is False → "".
    Missing/stale feed → "" if fail_open_stale else ``smart_money_feed_stale``.
    buy requires smart_money_inflow; sell rejects while inflow is True.
    """
    if not enabled:
        return ""
    side = (side or "").lower()
    if side not in ("buy", "sell"):
        return "smart_money_invalid_side"
    if not sm or sm.get("stale", True):
        return "" if fail_open_stale else "smart_money_feed_stale"
    inflow = bool(sm.get("smart_money_inflow", False))
    if side == "buy" and not inflow:
        return "smart_money_required_inflow"
    if side == "sell" and inflow:
        return "smart_money_block_short_while_inflow"
    return ""



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
        # 2026-07-27: the midpoint cancels latency only on a SYMMETRIC round
        # trip, so every sample carries an inherent +/-(rtt/2) error bar. Worse,
        # exchanges/base.py retries 3x with exponential backoff and t0/t1
        # straddle that whole sequence, so a retried call yields an "offset"
        # that is pure latency — a venue whose clock is perfect reads as
        # +rtt/2. Discard the sample when that error bar alone exceeds the
        # alert threshold: it cannot distinguish drift from slowness. None
        # already means "no sample" everywhere and is never counted as drift.
        # (Bitget alerted +1507ms on 07-22 and +684ms on 07-27 while the other
        # two venues stayed under 50ms; a wrong LOCAL clock offsets every venue
        # together, so those were latency artifacts, not clock drift.)
        try:
            from config import CLOCK_DRIFT_ALERT_MS as _thr
        except ImportError:
            _thr = 500
        if (t1 - t0) / 2.0 > _thr:
            return None
        return server_ms - ((t0 + t1) / 2.0)
    except Exception:
        return None
