"""
core/order_mgmt/helpers.py — module-level helpers for OrderManager (Phase D4).
"""
import json
import threading
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

from loguru import logger

from config import DRY_RUN, RISK
from core.blacklist_manager import BlacklistManager
from core.compliance_logger import ComplianceLogger
from core.kelly_sizer import KellySizer
from core.position_tracker import Position, PositionTracker
from core.post_mortem import PostMortem
from core.risk_manager import RiskManager, load_age_cutoffs
from core.sim_execution import SimExecutionModel
from core.smart_executor import SmartExecutor
from core.trailing_stop_manager import TrailingStopManager
from core.virtual_wallet import VirtualWallet
from exchanges.base import BaseExchange
from utils.atomic_io import atomic_write_json
from utils.http_redaction import redact_http_debug as _redact_http_debug
from utils.notifier import TelegramNotifier

# Permission-denied error patterns
_PERM_ERRORS = (
    "-2015", "permissions for action", "permission denied",
    "not allowed", "api-key", "IP restriction",
    "unauthorized", "forbidden",
)

# Position mode error — means exchange is in One-Way Mode, not Hedge Mode
_POSITION_MODE_ERRORS = (
    "-4061", "position side does not match",
    "positionSide", "position mode",
    "40774", "unilateral position",
    "unilateral",
    "40773", "two-way positions",   # Bitget: tradeSide close is two-way only
)

# Errors that mean "skip THIS pair, but other pairs are fine"
_SKIP_PAIR_ERRORS = (
    "-4411", "TradFi-Perps agreement",
    "agreement contract", "sign agreement",
    "not supported", "symbol not found",
    "does not have market symbol",
)

def _is_permission_error(e: Exception) -> bool:
    msg = str(e).lower()
    return any(s.lower() in msg for s in _PERM_ERRORS)


def _should_fire_partial_tp(position, price: float, partial_tp_config: dict):
    """Pure decision function: should the partial-TP fire on this position at this price?

    Returns:
        (should_fire: bool, take_size: float, partial_level: float)

    Inputs:
        position: object with .side ('buy'/'sell'), .entry_price, .take_profit, .partial_taken
        price: current monitor-cycle price for the position
        partial_tp_config: the config.PARTIAL_TP dict

    Extracted from the inline check_sl_tp logic on 2026-05-19 to enable direct
    unit testing. Behavior is byte-equivalent to the original inline block.
    """
    if not partial_tp_config.get("enabled"):
        return (False, 0.0, 0.0)
    # ACCURACY band (2026-07-10, second leak): with the compressed TP the
    # partial level sits a whisker from entry, so partial-TP fired almost
    # immediately, moved SL to breakeven, and any wiggle stopped the trade
    # at entry — fees turned would-be geometric wins into losses (3 of the
    # first 5 per-side closes: exit_px == entry_px exactly, positive
    # partial_realized_pnl, negative whole-trade). The audited 63-67%
    # geometry is PURE first-touch SL/TP — band positions never partial.
    if _is_accuracy_band_position(position):
        return (False, 0.0, 0.0)
    if getattr(position, "partial_taken", False):
        return (False, 0.0, 0.0)
    if not (position.take_profit and position.entry_price):
        return (False, 0.0, 0.0)

    take_at = partial_tp_config.get("first_take_at_pct", 0.5)
    take_sz = partial_tp_config.get("first_take_size", 0.5)

    if position.side == "buy":
        tp_dist = position.take_profit - position.entry_price
        partial_level = position.entry_price + tp_dist * take_at
        return (price >= partial_level, take_sz, partial_level)
    else:
        tp_dist = position.entry_price - position.take_profit
        partial_level = position.entry_price - tp_dist * take_at
        return (price <= partial_level, take_sz, partial_level)


# MAKER-FIRST PAPER ENTRIES (2026-07-10): abandon (never chase) a timed-out
# virtual maker entry whose market moved beyond this fraction of the original
# signal price. 0.3% per the design blueprint.
_MAKER_CHASE_GUARD_PCT = 0.003

# Mutable runtime artifacts are module-level so test harnesses can redirect
# every default constructor away from the production ``data/`` directory.
# Keeping these paths inline in ``__init__`` previously let ordinary unit tests
# load and overwrite the running bot's close counters and execution state.
ORDER_MODE_STATE_PATH = Path("data/order_mode_state.json")
SL_WIDENED_STATE_PATH = Path("data/sl_widened.json")
PAPER_FUNDING_WINDOWS_PATH = Path("data/paper_funding_windows.json")
PENDING_MAKER_ENTRIES_PATH = Path("data/pending_maker_entries.json")
CLOSE_FAIL_COUNT_PATH = Path("data/close_fail_count.json")


def _maker_first_cfg() -> dict:
    """Live view of config.MAKER_FIRST_PAPER (lazy so tests can monkeypatch)."""
    try:
        from config import MAKER_FIRST_PAPER
        return MAKER_FIRST_PAPER
    except (ImportError, AttributeError):
        return {"enabled": False}


def _safe_ticker_px(ticker: dict, key: str) -> float:
    """Positive float from a ticker field, else 0.0."""
    try:
        v = float((ticker or {}).get(key) or 0)
        return v if v > 0 else 0.0
    except (TypeError, ValueError):
        return 0.0


def _mcp_confidence_size_multiplier(confidence, layers_aligned) -> float:
    """Return a final-order multiplier that can never increase approved risk.

    Confidence and layer count already affect scorer admission and upstream
    sizing. Increasing quantity again inside OrderManager happened after the
    portfolio gross- and aggregate-risk checks and double-used an uncalibrated
    score. High confidence therefore stays at 1.0; a marginal non-zero score
    may still reduce exposure.
    """
    try:
        confidence = float(confidence or 0.0)
        float(layers_aligned or 0.0)  # validate malformed scorer state
    except (TypeError, ValueError):
        return 1.0
    return 0.80 if 0.0 < confidence < 0.60 else 1.0


def _mid_from_ticker(ticker: dict) -> float:
    """Extract mid price (bid+ask)/2 from a ccxt ticker dict.

    Returns 0.0 when bid or ask are missing. Attribution.record skips rows
    where either mid is 0, so an unparseable ticker degrades cleanly.
    """
    try:
        bid = float(ticker.get("bid") or 0)
        ask = float(ticker.get("ask") or 0)
        if bid > 0 and ask > 0:
            return (bid + ask) / 2.0
    except (TypeError, ValueError):
        pass
    return 0.0


def _position_age_minutes(open_time, *, now: float | None = None) -> float:
    """Return a non-negative age for epoch or legacy ISO/datetime values."""

    current = time.time() if now is None else float(now)
    if open_time in (None, ""):
        return 0.0
    try:
        opened = float(open_time)
    except (TypeError, ValueError):
        try:
            from datetime import datetime, timezone

            if isinstance(open_time, datetime):
                parsed = open_time
            else:
                parsed = datetime.fromisoformat(str(open_time).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            opened = parsed.timestamp()
        except (TypeError, ValueError, OverflowError):
            return 0.0
    if not (opened > 0):
        return 0.0
    return max(0.0, (current - opened) / 60.0)


def _mark_from_ticker(ticker: dict, *, now: float | None = None) -> float:
    """Return a fresh positive mark price from a ccxt-shaped ticker."""
    ticker = ticker or {}
    info = ticker.get("info") if isinstance(ticker.get("info"), dict) else {}
    for value in (
        ticker.get("markPrice"),
        ticker.get("mark"),
        info.get("markPrice"),
        info.get("markPx"),
        info.get("mark_price"),
    ):
        try:
            mark = float(value)
        except (TypeError, ValueError):
            continue
        if mark <= 0:
            continue
        timestamp = ticker.get("timestamp")
        if timestamp is None:
            return 0.0
        try:
            event_ts = float(timestamp)
            if event_ts > 100_000_000_000:
                event_ts /= 1000.0
            age = (time.time() if now is None else float(now)) - event_ts
            if age > 15.0 or age < -2.0:
                return 0.0
        except (TypeError, ValueError):
            return 0.0
        return mark
    return 0.0


def _is_position_mode_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _POSITION_MODE_ERRORS)


def _is_skip_pair_error(e: Exception) -> bool:
    msg = str(e)
    return any(s in msg for s in _SKIP_PAIR_ERRORS)


def build_sl_tp_order_params(
    ex_name: str, side: str, oneway: bool,
    trigger_price: float, is_sl: bool,
    mark_trigger: bool = None,
) -> tuple[str, dict]:
    """Return (order_type, params) for an SL or TP conditional order.

    Separated out so it is trivially unit-testable. Per-exchange shape rules:
      Binance USD-M → STOP_MARKET / TAKE_PROFIT_MARKET + stopPrice + reduceOnly
      Bitget        → ONLY stopLossPrice (SL) or takeProfitPrice (TP); ccxt
                      rejects the combo with triggerPrice
                      ("createOrder() params can only contain one of
                      triggerPrice, stopLossPrice, takeProfitPrice,
                      trailingPercent")
      Bybit v5      → triggerPrice + stopLossPrice/takeProfitPrice +
                      explicit triggerDirection (ccxt only auto-infers
                      triggerDirection in the stopLossPrice-only branch)

    ``mark_trigger`` (C3, tpbot retrofit 2026-07-08): trigger on MARK price
    instead of the venue's last-price default — immune to single rogue
    last-price prints and synced with the liquidation engine. None reads
    config.SLTP_TRIGGER_MARK_PRICE (default ON). Per-venue params verified
    against ccxt 4.5.54 request builders: Binance workingType passthrough,
    Bybit request['triggerBy'/'slTriggerBy'/'tpTriggerBy'], Bitget
    request['triggerType'] (whose ccxt default is already mark_price — the
    explicit param pins it against ccxt upgrades). All three survive the
    client one-way retries, which strip only positionSide/holdSide/tradeSide.
    Flag OFF is byte-identical to the pre-C3 shapes.
    """
    if mark_trigger is None:
        import config as _cfg
        mark_trigger = bool(getattr(_cfg, "SLTP_TRIGGER_MARK_PRICE", True))
    ex_lower = ex_name.lower()

    if ex_lower == "binance":
        order_type = "STOP_MARKET" if is_sl else "TAKE_PROFIT_MARKET"
        params = {"stopPrice": trigger_price, "reduceOnly": True}
        if mark_trigger:
            params["workingType"] = "MARK_PRICE"
        if not oneway:
            params["positionSide"] = "LONG" if side == "buy" else "SHORT"
        return order_type, params

    if ex_lower == "bitget":
        params = {"stopLossPrice": trigger_price} if is_sl \
            else {"takeProfitPrice": trigger_price}
        if mark_trigger:
            params["triggerType"] = "mark_price"
        if oneway:
            params["reduceOnly"] = True
        else:
            params["positionSide"] = "LONG" if side == "buy" else "SHORT"
            params["reduceOnly"] = True
        return "market", params

    # Bybit v5 (default for everything else)
    params = {"triggerPrice": trigger_price}
    if is_sl:
        params["stopLossPrice"] = trigger_price
        params["triggerDirection"] = "below" if side == "buy" else "above"
        if mark_trigger:
            params["triggerBy"] = "MarkPrice"
            params["slTriggerBy"] = "MarkPrice"
    else:
        params["takeProfitPrice"] = trigger_price
        params["triggerDirection"] = "above" if side == "buy" else "below"
        if mark_trigger:
            params["triggerBy"] = "MarkPrice"
            params["tpTriggerBy"] = "MarkPrice"
    if oneway:
        params["reduceOnly"] = True
    else:
        params["positionSide"] = "LONG" if side == "buy" else "SHORT"
        params["reduceOnly"] = True
    return "market", params




def _tier_geometry_hold_active(pos, age_hours: float) -> bool:
    """True while a position's planned tier geometry defers time exits.

    2026-08-03 owner-directed STALE fix, mirroring the 2026-07-10 ACCURACY
    band hold below: post-AccBand tier geometry (TP 2.0-3.75% vs SL
    0.9-1.5%) needs time to reach either barrier — the first 10 post-fix
    resolved trades show 4 STALE exits and 0 full take-profits. While the
    planned R:R (from the position's own stored entry/SL/TP prices) is
    >= min_planned_rr and age < max_hold_hours, first-touch SL/TP governs.
    Past the horizon (zombie protection), with the flag off, or with
    missing/degenerate barrier prices the hold never applies.
    """
    try:
        from config import TIER_GEOMETRY_TIME_EXIT_HOLD as _cfg
    except ImportError:
        return False
    if not _cfg.get("enabled"):
        return False
    if age_hours >= float(_cfg.get("max_hold_hours", 72.0)):
        return False
    # Scalp lane keeps its own stale economics (2026-05-28 verdict: $1-2
    # scalps have no edge after costs; their aged-flat closes free capital
    # by design and must never be deferred by tier geometry).
    if getattr(pos, "_scalp", False) or (
            "scalp" in str(getattr(pos, "strategy", "")).lower()):
        return False
    entry = float(getattr(pos, "entry_price", 0.0) or 0.0)
    sl = float(getattr(pos, "stop_loss", 0.0) or 0.0)
    tp = float(getattr(pos, "take_profit", 0.0) or 0.0)
    if entry <= 0 or sl <= 0 or tp <= 0:
        return False
    if str(getattr(pos, "side", "")).lower() in ("buy", "long"):
        risk, reward = entry - sl, tp - entry
    else:
        risk, reward = sl - entry, entry - tp
    if risk <= 0 or reward <= 0:
        return False
    return (reward / risk) >= float(_cfg.get("min_planned_rr", 1.0))


# ─────────────────────────────────────────────────────────────────────
# ACCURACY_TARGET_MODE time-exit suppression (2026-07-10 leak fix).
#
# Warehouse diagnosis: with the band live (TP = 0.4-0.5 x SL), take_profit
# exits ran 10/10 wins while STALE (n=5) and AGE_LIMIT (n=1) were ALL
# losses — the Phase-14-era time cutoffs close band trades BEFORE the
# tight TP can be touched, converting geometric wins into guaranteed
# losses. The band's 63-67% WR was audited on pure first-touch SL/TP
# with a 72h horizon, so time-based closes are suppressed on band
# positions inside ACCURACY_TARGET_MODE["max_hold_hours"]. SL/TP/hard
# max-loss authorities are untouched — only the TIME exits defer.
# ─────────────────────────────────────────────────────────────────────
def _is_accuracy_band_position(position) -> bool:
    """True when this position was opened under ACCURACY_TARGET_MODE.

    Band marker: ``position._accuracy_band`` (stamped at the
    bot_engine._execute_open chokepoint when the band rewrites tp_pct) OR
    the restart-surviving geometry fallback — TP distance strictly shorter
    than the IMMUTABLE entry-stop distance, which non-band entries can
    never have (the min-R:R gate enforces tp >= min_rr x sl everywhere
    else; tsmom carries tp=0 and never matches). Flag off -> always False.
    Fails closed on any bad input.
    """
    try:
        from config import ACCURACY_TARGET_MODE as _acc
    except ImportError:
        return False
    if not _acc.get("enabled", False):
        return False
    # `is True` (not truthiness): the chokepoint stamps a literal True, and
    # anything else (e.g. a MagicMock auto-attribute in tests, or a stale
    # serialized value) must fall through to the geometry check instead of
    # silently classifying the position as band.
    if getattr(position, "_accuracy_band", False) is True:
        return True
    try:
        entry = float(getattr(position, "entry_price", 0) or 0)
        tp = float(getattr(position, "take_profit", 0) or 0)
        if hasattr(position, "entry_risk_stop"):
            sl = float(position.entry_risk_stop() or 0)
        else:
            sl = float(getattr(position, "stop_loss", 0) or 0)
        if entry > 0 and tp > 0 and sl > 0:
            sl_frac = abs(entry - sl) / entry
            tp_frac = abs(tp - entry) / entry
            # Sanity bounds: real stops/targets live within ~10% of entry
            # (charter Stop-Loss Guardian is 8%; ATR clamps 1.5-3.5%). A
            # distance beyond 20% is garbage input (mock/corrupt/stale) —
            # never classify band on it (float(MagicMock) == 1.0 made a
            # fake "99% stop" pass this check before the bound).
            if 0 < sl_frac <= 0.20 and 0 < tp_frac <= 0.20:
                return tp_frac < sl_frac
    except (TypeError, ValueError, ZeroDivisionError):
        pass
    return False


def _accuracy_band_hold_active(position, age_hours) -> bool:
    """True while a band position is exempt from time-based exits
    (STALE / AGE_LIMIT / AGE_LOSS / scalp time wall / scalp stale).

    Delegates the band identification to :func:`_is_accuracy_band_position`
    (shared with the partial-TP suppression). Past ``max_hold_hours``
    (default 72) returns False so the existing time exits provide
    zombie-position protection. Flag off -> always False (byte-identical
    monitor behavior). Fails closed on any bad input.
    """
    try:
        from config import ACCURACY_TARGET_MODE as _acc
    except ImportError:
        return False
    if not _acc.get("enabled", False):
        return False
    try:
        if float(age_hours) >= float(_acc.get("max_hold_hours", 72)):
            return False
    except (TypeError, ValueError):
        return False
    return _is_accuracy_band_position(position)


def max_hold_force_flat_hours(pos, standard_max_age_h: float) -> float:
    """Family horizon (hours) for hard ``max_hold_force_flat`` closes.

    Band → ACCURACY max_hold_hours; tier-geometry → TIER max_hold_hours;
    else → standard_max_age_h (RISK.max_position_age_hours).
    """
    try:
        from config import ACCURACY_TARGET_MODE as _acc
        from config import TIER_GEOMETRY_TIME_EXIT_HOLD as _tg
    except ImportError:
        return float(standard_max_age_h)
    try:
        if _is_accuracy_band_position(pos):
            return float(_acc.get("max_hold_hours", 72))
        if _tier_geometry_hold_active(pos, 0.0):
            return float(_tg.get("max_hold_hours", 72))
    except Exception:
        pass
    return float(standard_max_age_h)


# ─────────────────────────────────────────────────────────────────────
# 2026-05-19 Patch #3 — mcp_take_profit path amplification helper.
#
# Wraps soft-close call sites (STALE / AGE_LIMIT / AGE_LOSS) so that
# when a position is "close to firing TP" (proximity score
# ≥ config.MCP_TP_PROXIMITY_THRESHOLD) the soft-close is deferred by
# config.MCP_TP_GRACE_SEC seconds. After grace expires the original
# close fires with `_post_grace` suffix on the exit_reason.
#
# SL paths (stop_loss / sl_failed / sl_placement_failed) are NEVER
# gated — they always close immediately. SL is the last line of
# defense; amplifying it would defeat the circuit breaker.
#
# Grace state lives on `position._mcp_tp_grace_until` (set dynamically
# on the Position dataclass, same pattern as `_ghost_reroute_count` in
# Task 1). No module-level state — each position carries its own
# timer, so leak-across-positions is impossible.
# ─────────────────────────────────────────────────────────────────────
def _try_soft_close(order_manager, position, soft_reason, proceed_fn):
    """Soft-close decision gate.

    Args:
        order_manager: OrderManager instance (currently unused; kept for
            API symmetry with `_patch0_instrument_ghost` and so future
            callers can read config off the manager if needed).
        position: Position object with `_mcp_tp_grace_until` writable.
            Callers must have set `position.current_px` and
            `position.unrealized_pnl_pct` (fraction) before invoking,
            so `score_take_profit_proximity` has the data it needs.
        soft_reason: short label for the close path (e.g. "STALE",
            "AGE_LIMIT", "AGE_LOSS"). SL paths bypass entirely.
        proceed_fn: callable taking a reason-string. Should perform the
            close and return whatever the caller wants returned. This
            helper returns `proceed_fn(reason)` when closing.

    Returns:
        None when the close is deferred (still in grace, or first time
        proximity meets threshold). Otherwise the return value of
        `proceed_fn(reason)`. Reason is `f"{soft_reason}_post_grace"`
        on the post-grace fire path, otherwise the original
        `soft_reason`.
    """
    # Local imports keep ruff/isort happy and avoid circular-import
    # risk with `core.mcp_brain` (which transitively re-imports parts
    # of this module). `time` is already imported at module level but
    # we re-bind here for explicitness — proximity grace math reads
    # `_time.time()` once.
    import time as _time  # noqa: I001 — function-local by design
    import config as _cfg  # noqa: I001
    from core.mcp_brain import score_take_profit_proximity  # noqa: I001

    # Invariant: SL paths never gated.
    SL_REASONS = ("stop_loss", "sl_failed", "sl_placement_failed")
    if soft_reason in SL_REASONS:
        return proceed_fn(soft_reason)

    # Master kill-switch — flag off bypasses amplification entirely.
    if not getattr(_cfg, "MCP_TP_AMPLIFY_ENABLED", True):
        return proceed_fn(soft_reason)

    threshold = float(getattr(_cfg, "MCP_TP_PROXIMITY_THRESHOLD", 0.7))
    grace_sec = int(getattr(_cfg, "MCP_TP_GRACE_SEC", 1800))

    try:
        proximity = float(score_take_profit_proximity(position))
    except Exception:
        proximity = 0.0

    # Below threshold → close immediately, no defer.
    # BUT: if grace_until was previously set (proximity crossed threshold
    # earlier and we're now back below it), preserve the `_post_grace`
    # audit signal so sprint_kpi.py can count amplified closes correctly.
    # Without this, the close emits the bare `soft_reason` and the fact
    # that the position was ever deferred becomes invisible.
    if proximity < threshold:
        prior_grace = float(getattr(position, "_mcp_tp_grace_until", 0) or 0)
        if prior_grace > 0:
            try:
                logger.info(
                    f"[Orders] MCP_TP_AMPLIFY: proximity dropped below "
                    f"threshold for {getattr(position, 'symbol', '?')} "
                    f"after grace was set — firing "
                    f"{soft_reason}_post_grace")
            except Exception:
                pass
            return proceed_fn(f"{soft_reason}_post_grace")
        return proceed_fn(soft_reason)

    now = _time.time()
    grace_until = float(getattr(position, "_mcp_tp_grace_until", 0) or 0)

    # First time crossing threshold — set grace and defer.
    if grace_until <= 0:
        position._mcp_tp_grace_until = now + grace_sec
        try:
            logger.info(
                f"[Orders] MCP_TP_AMPLIFY: deferring {soft_reason} on "
                f"{getattr(position, 'symbol', '?')} "
                f"(prox={proximity:.2f}, grace={grace_sec}s)")
        except Exception:
            pass
        return None

    # Inside the grace window → still defer.
    if now < grace_until:
        return None

    # Grace expired → close with `_post_grace` suffix.
    try:
        logger.info(
            f"[Orders] MCP_TP_AMPLIFY: grace expired for "
            f"{getattr(position, 'symbol', '?')} — firing "
            f"{soft_reason}_post_grace")
    except Exception:
        pass
    return proceed_fn(f"{soft_reason}_post_grace")
