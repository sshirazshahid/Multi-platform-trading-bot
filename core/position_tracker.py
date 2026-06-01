"""
core/position_tracker.py — Track positions with fee-adjusted PnL.

Every trade is tagged paper_trade=True (DRY RUN) or False (LIVE).
This tag is used by the learning engine to separate dry run knowledge
from live knowledge and blend them correctly.

Fee structure:
  entry_fee  = size * entry_price * fee_rate
  exit_fee   = size * exit_price  * fee_rate
  gross_pnl  = price movement only
  net_pnl    = gross_pnl - entry_fee - exit_fee   ← real money kept/lost
"""

import json
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

from loguru import logger

_DEFAULT_SPOT_FEE    = 0.001
_DEFAULT_FUTURES_FEE = 0.0005


def _fee_rate(market_type: str) -> float:
    try:
        from config import FEE
        if market_type == "futures":
            return FEE.get("futures_taker", _DEFAULT_FUTURES_FEE)
        return FEE.get("spot_taker", _DEFAULT_SPOT_FEE)
    except (ImportError, AttributeError):
        return _DEFAULT_FUTURES_FEE if market_type == "futures" \
               else _DEFAULT_SPOT_FEE


def _is_dry_run() -> bool:
    try:
        from config import DRY_RUN
        return DRY_RUN
    except (ImportError, AttributeError):
        return True


def match_ghost_ledger_record(pos, records):
    """Find the most recent ledger record matching a ghost-detected position.

    Returns ``(exit_price, best_record)`` where ``exit_price`` is > 0 when
    a match was found, else ``(0.0, None)``.

    Matching policy:
      - Symbol must match exactly (each exchange's fetch_closed_pnl
        normalises to ccxt unified ``"BASE/QUOTE:QUOTE"`` already).
      - Side filter applies only when the record carries a non-None side.
        Binance's income ledger does not carry side; we trust the
        (symbol, time) match for those rows.
      - close_time must be > 0 and >= pos.open_time.

    Exit-price sourcing:
      - Use ``record["exit_price"]`` when > 0 (Bybit + Bitget).
      - Otherwise back it out from realized_pnl + entry_price + size + side
        (Binance income-ledger only reports the income amount). This
        produces a close_price such that the close path's downstream
        ``(exit - entry) * size`` computes to roughly the exchange's
        realized PnL.
    """
    if not records:
        return 0.0, None
    candidates = []
    pos_side = (pos.side or "").lower()
    for r in records:
        if r.get("symbol") != pos.symbol:
            continue
        rside_raw = r.get("side")
        if rside_raw is not None:
            rside = str(rside_raw).lower()
            if rside and rside != pos_side:
                opp = "buy" if pos_side == "sell" else "sell"
                if rside != opp:
                    continue
        ct = float(r.get("close_time", 0) or 0)
        if ct <= 0 or ct < float(pos.open_time or 0):
            continue
        candidates.append(r)
    if not candidates:
        return 0.0, None
    candidates.sort(
        key=lambda x: float(x.get("close_time", 0) or 0), reverse=True)
    best = candidates[0]
    exit_px = float(best.get("exit_price", 0) or 0)
    if exit_px <= 0:
        rpnl = float(best.get("realized_pnl", 0) or 0)
        sz = float(pos.size or 0)
        if sz > 0 and rpnl != 0 and (pos.entry_price or 0) > 0:
            sign = 1.0 if pos_side == "buy" else -1.0
            exit_px = float(pos.entry_price) + (rpnl / (sz * sign))
    if exit_px <= 0:
        return 0.0, None
    return exit_px, best


# Tolerance band for matching a ghost fill to an exchange-side SL/TP trigger.
# 50bps absorbs realistic slippage on conditional fills and the few-bps drift
# between pos.stop_loss (rounded at placement) and the ledger exit_price.
_CONDITIONAL_FILL_TOLERANCE = 0.005


def _classify_conditional_fill(pos, exit_px):
    """Return "stop_loss" / "take_profit" if `exit_px` matches an
    exchange-placed conditional within 50bps; else None.

    The ghost path is the *primary* exit route for SCALP-tier trades that
    round-trip in seconds — the position vanishes from fetch_positions
    between bot cycles, so the bot only learns about the close through
    reconciliation. When the exchange-side SL or TP was the actual
    trigger, the close should be labelled as such rather than as a
    ghost. Reserves ghost_* for genuinely-unexpected fills (no flag set,
    or exit price outside the band of both conditionals).

    Local-monitor closes (`_exchange_sl=False`) are skipped — they
    should already be routing through the normal `close_position` path
    via the position monitor, not through sync_with_exchanges.
    """
    try:
        ex_sl = bool(getattr(pos, "_exchange_sl", False))
        sl = float(getattr(pos, "stop_loss", 0) or 0)
        if ex_sl and sl > 0 and abs(exit_px - sl) / sl <= _CONDITIONAL_FILL_TOLERANCE:
            return "stop_loss"
        ex_tp = bool(getattr(pos, "_exchange_tp", False))
        tp = float(getattr(pos, "take_profit", 0) or 0)
        if ex_tp and tp > 0 and abs(exit_px - tp) / tp <= _CONDITIONAL_FILL_TOLERANCE:
            return "take_profit"
    except (TypeError, ValueError, ZeroDivisionError):
        return None
    return None


@dataclass
class Position:
    id:           str
    exchange:     str
    symbol:       str
    side:         str
    market_type:  str
    strategy:     str
    entry_price:  float
    size:         float
    stop_loss:    float
    take_profit:  float
    leverage:     int             = 1
    open_time:    float           = field(default_factory=time.time)
    close_time:   Optional[float] = None
    exit_price:   Optional[float] = None
    entry_fee:    float           = 0.0
    exit_fee:     float           = 0.0
    total_fees:   float           = 0.0
    gross_pnl:    Optional[float] = None
    pnl:          Optional[float] = None
    pnl_pct:      Optional[float] = None
    close_reason: Optional[str]   = None
    order_id:     Optional[str]   = None
    partial_taken: bool            = False
    # ── Forward-attribution fields (Phase 2 / Task A) ─────────────────
    # Defaults of 0.0 keep backward-compat with already-open positions
    # whose JSON payload predates these fields. attribution.record skips
    # rows where either mid is 0, so unset values produce no bad data.
    entry_mid:    float           = 0.0
    exit_mid:     float           = 0.0
    funding_paid: float           = 0.0
    # ── Mode tag — this is the key field for learning separation ──────
    paper_trade:  bool            = field(default_factory=_is_dry_run)
    # ── Exchange-side SL/TP registration flags ────────────────────────
    # True once OrderManager._place_exchange_sl_tp succeeds. Persisted
    # so a restart doesn't try to re-place orders that are already live.
    # _exchange_tp MUST be a declared field (not a dynamic attr) so
    # dataclasses.asdict() includes it in positions.json saves.
    _exchange_sl: bool            = False
    _exchange_tp: bool            = False
    # ── Predicted confidence at entry (Phase 18, 2026-05-04) ──────────
    # MCP score / 100, recorded for the ProbabilityCalibrator feed.
    # Defaults to 0.0 so old positions in JSON load fine; calibrator
    # skips trades with confidence == 0.
    confidence:   float           = 0.0

    def __post_init__(self):
        rate = _fee_rate(self.market_type)
        # Only calculate entry_fee if not already set (e.g. loaded from JSON)
        if self.entry_fee == 0.0:
            self.entry_fee = self.size * self.entry_price * rate
        # total_fees = entry_fee + exit_fee (exit_fee is 0 until close())
        if self.total_fees == 0.0:
            self.total_fees = self.entry_fee + self.exit_fee

    def close(self, exit_price: float, reason: str):
        self.exit_price   = exit_price
        self.close_time   = time.time()
        self.close_reason = reason
        rate              = _fee_rate(self.market_type)
        self.exit_fee     = self.size * exit_price * rate
        self.total_fees   = self.entry_fee + self.exit_fee

        # PnL = price_diff * size.  DO NOT multiply by leverage here!
        # `size` already represents the leveraged position quantity
        # (risk_manager computes: qty = balance * pct * leverage / price).
        # The exchange fills `size` units — leverage only affects margin,
        # not the position size or its P&L.
        if self.side == "buy":
            self.gross_pnl = (exit_price - self.entry_price) * self.size
        else:
            self.gross_pnl = (self.entry_price - exit_price) * self.size

        self.pnl     = self.gross_pnl - self.total_fees
        # pnl_pct = return on margin (accounts for leverage)
        notional     = self.entry_price * self.size
        margin       = notional / max(self.leverage, 1)
        self.pnl_pct = (self.pnl / margin * 100) if margin > 0 else 0.0

    @property
    def is_open(self) -> bool:
        return self.exit_price is None

    @property
    def duration_minutes(self) -> float:
        end = self.close_time or time.time()
        return (end - self.open_time) / 60

    @property
    def notional(self) -> float:
        return self.size * self.entry_price


# 2026-05-19 Patch #0 — Ghost-class reroute instrumentation (log-only).
# Computes the hypothetical Patch #1 decision for every ghost-detected
# close and logs it. Does NOT change close behavior. Read by
# scripts/ghost_reroute_report.py to gate Patch #1 deployment.
#
# Deviation from plan: function takes `exchange_instance` (a BaseExchange,
# not the string `pos.exchange`). `verify_exchange_sl_alive` /
# `verify_exchange_tp_alive` both call `exchange.name.lower()` and would
# raise AttributeError on a string, getting silently swallowed by the
# except block — producing zero instrumentation events in production and
# silently failing the Day 4–5 decision gate by absence-of-data.
#
# 2026-05-19 follow-up fix — code-review caught three issues in the
# original commit (357d025):
#   1. Original helper looked up self._last_mark_price, which is never
#      written anywhere — uPnL always returned 0.0, every event logged
#      would_reroute=False. Fixed: caller resolves the current mark via
#      ex.fetch_ticker(...)["last"] (same pattern as bot_engine) and
#      passes it in via `current_mark`. uPnL is computed inline here.
#   2. Original used `pos.side == "long"`; codebase canonical convention
#      is "buy" / "sell" (see Position.close() and other side checks in
#      this file). Fixed below.
#   3. ghost_reroute_report.py hardcoded notional_proxy=$50.0; real
#      position notionals vary ~5x. Fixed by emitting `notional=...`
#      (pos.size * pos.entry_price) in the log line and reading it back
#      in the report.
def _patch0_instrument_ghost(tracker, pos, order_manager,
                             exchange_instance, ghost_class_reason,
                             current_mark=None):
    """Log what Patch #1 would have done; no behavior change.

    Args:
        tracker: PositionTracker (unused now — kept for API compat).
        pos: Position-like object with .symbol, .side ("buy"/"sell"),
            .exchange, .entry_price, .size.
        order_manager: OrderManager for verify_exchange_sl/tp_alive.
        exchange_instance: BaseExchange (NOT the string pos.exchange —
            verify_exchange_*_alive calls exchange.name.lower()).
        ghost_class_reason: short label for the close path triggering
            instrumentation (e.g. "ghost_sync", "ghost_reconciled").
        current_mark: current mark price for `pos.symbol`, resolved by
            the caller via the exchange instance. None ⇒ uPnL logged as
            NaN; would_reroute=False. Caller is responsible for handling
            its own fetch errors and passing None on failure.
    """
    import config
    if not getattr(config, "GHOST_REROUTE_INSTRUMENT", False):
        return
    try:
        # uPnL fraction (unleveraged), signed. Uses canonical "buy"/"sell".
        entry = float(pos.entry_price or 0)
        if (current_mark is None) or (entry <= 0):
            upnl_pct = float("nan")
        else:
            sign = 1.0 if pos.side == "buy" else -1.0
            upnl_pct = sign * (float(current_mark) - entry) / entry

        # Real position notional (size × entry_price). The report uses
        # this to compute saved-pnl per event — replaces the previous
        # hardcoded $50 proxy that made the gate decision random.
        notional = float(pos.size or 0) * entry

        if upnl_pct > 0:  # NaN > 0 is False, so missing mark ⇒ skip checks
            sl_alive = bool(order_manager.verify_exchange_sl_alive(
                exchange_instance, pos))
            tp_alive = bool(order_manager.verify_exchange_tp_alive(
                exchange_instance, pos))
            would_reroute = sl_alive and tp_alive
        else:
            sl_alive = None
            tp_alive = None
            would_reroute = False
        logger.info(
            f"GHOST_REROUTE_INSTRUMENT: symbol={pos.symbol} "
            f"side={pos.side} exchange={pos.exchange} "
            f"upnl_pct={upnl_pct:.4f} notional={notional:.4f} "
            f"sl_alive={sl_alive} tp_alive={tp_alive} "
            f"would_reroute={would_reroute} reason={ghost_class_reason}"
        )
    except Exception as e:
        logger.warning(
            f"GHOST_REROUTE_INSTRUMENT: failed to compute hypothetical "
            f"for {getattr(pos, 'symbol', '?')}: {str(e)[:120]}"
        )


class PositionTracker:

    SAVE_PATH = Path("data/positions.json")
    BACKUP_PATH = Path("data/positions.json.bak")

    def __init__(self):
        self._open:   dict = {}
        self._closed: list = []
        # Phase 24 (2026-05-05): short-lived ledger of recently-closed
        # positions so sync_with_exchanges can label leftover-size
        # re-imports as RECONCILE (cosmetic) instead of MANUAL (warning).
        # Pruned to last 5 min on every record/match call. List of dicts:
        #   {exchange, symbol, side, size, entry_price, close_time}
        self._recent_closes: list[dict] = []
        # RLock so methods already holding the lock (add/close/sync) can
        # call _save() — which also acquires the lock — without deadlocking.
        # Required because bot_engine's _sltp_monitor_loop calls tracker._save()
        # from a daemon thread concurrently with the main schedule thread.
        self._lock = threading.RLock()
        # 2026-04-26: post-close hook. Wired by BotEngine to
        # OrderManager._finalize_close so warehouse/risk/Spec§12/blacklist/
        # trailing/notifier all fire for EVERY close path (normal exits AND
        # ghost-syncs detected during exchange reconciliation). Without this,
        # exchange-side SL/TP fills and fail-closed cleanups silently bypass
        # the safety rails — a -$0.64 ghost-reconciled DOT loss on 04-26
        # never reached daily_pnl, the SELL streak counter, or the warehouse.
        self.on_close = None
        # 2026-05-20 Area 1 — Two-pass ghost reconcile. First sync that
        # detects a missing position adds it here with the timestamp.
        # Second sync 15s later attempts ledger lookup again — most lagged
        # ledger writes appear within 15-30s of the actual fill.
        # Map: position_id -> first_seen_ts.
        self._pending_ghost_reconcile: dict[str, float] = {}
        # 2026-05-24 — Dedup keys seeded by the ghost loop after a
        # successful ledger reconcile. Keys carry the LEDGER ct
        # (not now()), so reconcile_closed_pnl can dedup the same fill
        # against its own ct-bucketed existing_keys check. Without
        # this, ghost-closed positions get re-imported as
        # `reconciled_from_exchange` because pos.close_time=now() lands
        # in a different 10-min bucket than the ledger's actual ct.
        # Set of (exchange_lower, symbol, side, int(ledger_ct // 600)).
        self._ghost_just_closed_dedup_keys: set = set()
        self._load()

    def add(self, position: Position):
        with self._lock:
            self._open[position.id] = position
            tag = "[PAPER]" if position.paper_trade else "[LIVE]"
            logger.info(
                f"[Positions] {tag} OPENED {position.side.upper()} "
                f"{position.symbol} @ {position.entry_price:.4f} | "
                f"size={position.size:.6f} notional={position.notional:.2f} USDT | "
                f"entry_fee={position.entry_fee:.4f} USDT | id={position.id}"
            )
            self._save()

    def close(self, position_id: str, exit_price: float, reason: str):
        with self._lock:
            pos = self._open.pop(position_id, None)
            if not pos:
                logger.warning(f"[Positions] Unknown id: {position_id}")
                return None
            pos.close(exit_price, reason)
            self._closed.append(pos)
            # Trim closed list to prevent unbounded memory growth
            if len(self._closed) > 500:
                self._closed = self._closed[-500:]
            # Phase 24 — record for reconcile-label matching in sync.
            self._record_recent_close(pos)
            # Clean up pending ghost reconcile entry if present — the
            # position was closed by a different path (normal close,
            # paper_ghost_cleanup, etc.), so the pending retry is stale.
            pending = getattr(self, "_pending_ghost_reconcile", None)
            if pending is not None:
                pending.pop(position_id, None)

            tag  = "[PAPER]" if pos.paper_trade else "[LIVE]"
            sign = "+" if pos.pnl >= 0 else ""
            logger.info(
                f"[Positions] {tag} CLOSED {pos.symbol} | "
                f"Gross: {pos.gross_pnl:+.4f} USDT | "
                f"Fees: -{pos.total_fees:.4f} USDT "
                f"(in={pos.entry_fee:.4f} out={pos.exit_fee:.4f}) | "
                f"Net PnL: {sign}{pos.pnl:.4f} USDT ({pos.pnl_pct:+.2f}%) | "
                f"reason={reason}"
            )
            self._save()
        # Fire post-close hook OUTSIDE the lock — _finalize_close may take a
        # while (warehouse write, exchange ledger lookup, notifier email) and
        # we don't want to block other position-tracker readers. Failures are
        # swallowed so a hook bug can never lose the close itself.
        # getattr() instead of attribute access so the multi_profile_runner
        # path (which builds a tracker via __new__ and skips __init__) does
        # not AttributeError here.
        on_close = getattr(self, "on_close", None)
        if on_close is not None:
            try:
                on_close(pos, exit_price, reason)
            except Exception as e:
                logger.error(f"[Positions] on_close hook failed for {pos.symbol}: {e}")
        return pos

    # Phase 24 (2026-05-05): RECONCILE-vs-MANUAL re-import labelling.
    # Bot tracker can drift from exchange reality (partial fills, untracked
    # ghosts, manual user adds). When a close is followed within ~5min by
    # sync importing leftover size on the same (ex, sym, side), tag the
    # import as RECONCILE so it doesn't pollute warning logs / dashboard
    # stats. Functional behaviour unchanged: SL+TP still get placed.
    def _record_recent_close(self, pos: "Position") -> None:
        """Append a close record. Called from close() under the lock."""
        now = time.time()
        self._recent_closes.append({
            "exchange": (pos.exchange or "").lower(),
            "symbol": pos.symbol,
            "side": pos.side,
            "size": float(pos.size or 0),
            "entry_price": float(pos.entry_price or 0),
            "close_time": now,
        })
        cutoff = now - 300
        self._recent_closes = [
            r for r in self._recent_closes if r["close_time"] >= cutoff
        ]

    def _match_recent_close(self, exchange: str, symbol: str, side: str,
                            size: float, entry: float) -> Optional[dict]:
        """Return a recent close that matches a re-imported position so we
        can tag it RECONCILE. All-or-nothing match:
          - same (exchange, symbol, side)
          - size within 0.5% of recorded close size
          - entry price within 1% of recorded entry (skip if either is 0)
          - within last 5 min (the prune already enforces this)
        """
        if not self._recent_closes:
            return None
        now = time.time()
        cutoff = now - 300
        ex_lower = (exchange or "").lower()
        for r in reversed(self._recent_closes):
            if r["close_time"] < cutoff:
                continue
            if (r["exchange"] != ex_lower
                    or r["symbol"] != symbol
                    or r["side"] != side):
                continue
            rsize = r["size"]
            if rsize <= 0 or size <= 0:
                continue
            if abs(size - rsize) / max(rsize, 1e-9) > 0.005:
                continue
            rentry = r["entry_price"]
            if rentry > 0 and entry > 0:
                if abs(entry - rentry) / rentry > 0.01:
                    continue
            return r
        return None

    def get_open(self, exchange: str = None, symbol: str = None) -> list:
        with self._lock:
            positions = list(self._open.values())
        if exchange:
            positions = [p for p in positions
                         if p.exchange.lower() == exchange.lower()]
        if symbol:
            positions = [p for p in positions if p.symbol == symbol]
        return positions

    def count_open(self, exchange: str = None) -> int:
        with self._lock:
            if exchange:
                return sum(1 for p in self._open.values()
                           if p.exchange.lower() == exchange.lower())
            return len(self._open)

    def summary(self) -> dict:
        with self._lock:
            closed = list(self._closed)
        total      = len(closed)
        wins       = [p for p in closed if (p.pnl or 0) > 0]
        losses     = [p for p in closed if (p.pnl or 0) <= 0]
        total_pnl  = sum(p.pnl        or 0 for p in closed)
        gross_pnl  = sum(p.gross_pnl  or 0 for p in closed)
        total_fees = sum(p.total_fees  or 0 for p in closed)
        win_rate   = (len(wins) / total * 100) if total > 0 else 0
        avg_win    = (sum(p.pnl for p in wins)   / len(wins))   if wins   else 0
        avg_loss   = (sum(p.pnl for p in losses) / len(losses)) if losses else 0
        paper_n    = sum(1 for p in closed if p.paper_trade)
        live_n     = total - paper_n
        return {
            "total_trades":   total,
            "paper_trades":   paper_n,
            "live_trades":    live_n,
            "wins":           len(wins),
            "losses":         len(losses),
            "win_rate":       round(win_rate, 2),
            "gross_pnl":      round(gross_pnl, 4),
            "total_fees":     round(total_fees, 4),
            "total_pnl":      round(total_pnl, 4),
            "avg_win":        round(avg_win, 4),
            "avg_loss":       round(avg_loss, 4),
            "open_positions": self.count_open(),
        }

    def sync_with_exchanges(self, active_exchanges: dict) -> list:
        """Bidirectional sync with exchanges.
          - Close ghost positions that disappeared from the exchange.
          - Import manual positions opened outside the bot (2026-04-16).
          - Clean up paper_trade leftovers when running LIVE.

        Returns list of newly-imported Position objects (empty on ghost-only sync)
        so the caller can place protective SL/TP orders.
        """
        from config import DRY_RUN

        # Clean up paper_trade positions that shouldn't exist in LIVE mode.
        # These are leftovers from a previous DRY_RUN session — they were never
        # placed on the exchange, so close_position() would fail with
        # "position is zero" on every 10-second cycle.
        if not DRY_RUN and self._open:
            paper_ghosts = [p for p in self._open.values() if p.paper_trade]
            for pos in paper_ghosts:
                logger.warning(
                    f"[Positions] PAPER GHOST in LIVE mode: {pos.symbol} "
                    f"{pos.side.upper()} on {pos.exchange} — auto-closing")
                self.close(pos.id, pos.entry_price, "paper_ghost_cleanup")
            if paper_ghosts:
                logger.info(
                    f"[Positions] Cleaned {len(paper_ghosts)} paper position(s) "
                    f"from previous DRY_RUN session")

        # Collect all active positions from exchanges (raw snapshot with entry/size).
        # exchange_positions: (exchange_lower, symbol, side) -> raw ep dict
        exchange_positions: dict = {}
        # 2026-05-30 FIX — Track which exchanges returned a VALID snapshot this
        # cycle. A transient fetch_positions() error (e.g. Binance
        # "load_markets failed") must NOT be read as "no positions here" --
        # otherwise every live position on that exchange is mis-flagged as a
        # ghost and false-closed, then re-imported as a duplicate RECONCILE-*
        # record next cycle. Only exchanges in this set are ghost-checked.
        fetched_ok_exchanges: set = set()
        for ex_name, exchange in active_exchanges.items():
            try:
                positions = exchange.fetch_positions()
                if positions is None:
                    raise ValueError("fetch_positions() returned None")
                for ep in positions:
                    size = float(ep.get("contracts") or ep.get("contractSize") or 0)
                    if size == 0:
                        continue
                    side_raw = (ep.get("side") or "").lower()
                    if side_raw not in ("long", "short"):
                        continue
                    side = "buy" if side_raw == "long" else "sell"
                    sym = ep.get("symbol", "")
                    exchange_positions[(ex_name.lower(), sym, side)] = ep
                # Mark success only AFTER the full snapshot parsed cleanly.
                fetched_ok_exchanges.add(ex_name.lower())
            except Exception as e:
                logger.warning(f"[Positions] Sync fetch {ex_name} failed: {str(e)[:150]}")

        # ── Import manual positions (2026-04-16) ──
        # Any position the exchange reports that isn't tracked locally is a
        # manual position the user opened outside the bot. Import it so the
        # position monitor, SL/TP manager, and dashboard see it.
        tracked_keys = set()
        for p in self._open.values():
            tracked_keys.add((p.exchange.lower(), p.symbol, p.side))

        imported_list: list = []
        for key, ep in exchange_positions.items():
            if key in tracked_keys:
                continue
            ex_name, sym, side = key
            size = float(ep.get("contracts") or ep.get("contractSize") or 0)
            entry = float(ep.get("entryPrice") or ep.get("info", {}).get("avgPrice") or 0)
            if size <= 0 or entry <= 0:
                continue
            try:
                lev = int(float(ep.get("leverage") or 1))
            except (TypeError, ValueError):
                lev = 1
            liq = float(ep.get("liquidationPrice") or 0)
            # Phase 24 (2026-05-05): if this leftover size matches a
            # close we just booked (within 5min, ±0.5% size, ±1% entry),
            # label it RECONCILE — the bot's close successfully booked
            # PnL on its tracked share but the exchange shows leftover.
            # Functional behaviour identical (SL/TP still placed by
            # _protect_imported_positions); the relabel quiets the
            # warning + dashboard pollution.
            recon_match = self._match_recent_close(
                ex_name, sym, side, size, entry)
            if recon_match:
                pid = f"RECONCILE-{ex_name}-{sym}-{side}-{int(time.time())}"
                strat = "reconcile"
                _log_label = "RECONCILED leftover position"
                _log_warn = False
            else:
                pid = f"MANUAL-{ex_name}-{sym}-{side}-{int(time.time())}"
                strat = "manual"
                _log_label = "IMPORTED manual position"
                _log_warn = True
            pos = Position(
                id=pid,
                exchange=ex_name,
                symbol=sym,
                side=side,
                market_type="futures",
                strategy=strat,
                entry_price=entry,
                size=size,
                stop_loss=liq,      # best available — liquidation as fallback SL
                take_profit=0.0,
                leverage=lev,
                paper_trade=False,  # manual positions are always real
            )
            with self._lock:
                self._open[pid] = pos
            # 2026-05-25 — Bug 2 fix (no-edge-forensics audit): MANUAL/
            # RECONCILE imports were added to _open WITHOUT a warehouse
            # record_trade_open. When they later close via _finalize_close,
            # trade_id_by_key() finds no row → the close update is skipped →
            # the warehouse row stays OPEN forever (~57 stuck-OPEN rows).
            # Create the OPEN row now so the close can patch it. Idempotent.
            try:
                from core.warehouse import get_warehouse as _gw
                from config import OPERATING_MODE as _mode
                _gw().record_trade_open(
                    exchange=ex_name, symbol=sym, side=side,
                    ts_entry=float(pos.open_time),
                    entry_px=entry, size=size, leverage=lev,
                    market_type="futures",
                    strategy_family=strat,
                    mode=_mode,
                )
            except Exception as _we:
                logger.warning(
                    f"[Positions] import record_trade_open FAILED {sym} "
                    f"(close will not reconcile to warehouse): {_we}")
            _msg = (
                f"[Positions] {_log_label}: {sym} {side.upper()} "
                f"on {ex_name} @ {entry:.4f} size={size:.6f} lev={lev}x | id={pid}"
            )
            if _log_warn:
                logger.warning(_msg)
            else:
                logger.info(_msg)
            imported_list.append(pos)

        if imported_list:
            with self._lock:
                self._save()
            logger.info(f"[Positions] Synced: imported {len(imported_list)} manual position(s)")

        live_open = [p for p in self._open.values() if not p.paper_trade]
        if not live_open:
            return imported_list

        # Close any tracked LIVE FUTURES positions not found on exchange.
        # SPOT positions are NOT checked — fetch_positions() only returns
        # futures, so every spot position would falsely appear as a ghost.
        ghosts = []
        for pos in live_open:
            if pos.market_type != "futures":
                continue  # skip spot — can't verify via fetch_positions()
            # 2026-05-30 FIX — Only ghost-check positions on exchanges that
            # returned a valid snapshot this cycle. If fetch_positions()
            # failed for this exchange, we cannot tell "closed" from "API
            # error", so we keep the position open and retry next cycle.
            # This is the core fix for the ghost_sync -> reopen churn.
            if pos.exchange.lower() not in fetched_ok_exchanges:
                logger.warning(
                    f"[Positions] Skipping ghost-check for {pos.symbol} "
                    f"{pos.side.upper()} on {pos.exchange}: snapshot "
                    f"unavailable this cycle (fetch failed) -- keeping open")
                continue
            key = (pos.exchange.lower(), pos.symbol, pos.side)
            if key not in exchange_positions:
                ghosts.append(pos)

        for pos in ghosts:
            logger.warning(
                f"[Positions] GHOST detected: {pos.symbol} {pos.side.upper()} "
                f"on {pos.exchange} — not found on exchange. Auto-closing.")
            ex = active_exchanges.get(pos.exchange.lower())

            # 2026-05-19 Patch #0 — instrumentation (log-only, no behavior change).
            # Resolve current mark via fetch_ticker (same pattern as
            # bot_engine._position_monitor: ticker["last"]) so the
            # instrument helper can compute real uPnL. Fail-soft: any
            # error → current_mark=None → upnl_pct logged as NaN and
            # would_reroute=False (event still recorded for rate counting).
            order_manager = getattr(self, "order_manager", None)
            if order_manager is not None and ex is not None:
                current_mark = None
                try:
                    tkr = ex.fetch_ticker(pos.symbol, pos.market_type)
                    current_mark = float(tkr.get("last", 0) or 0) or None
                except Exception as _e:
                    logger.debug(
                        f"GHOST_REROUTE_INSTRUMENT: fetch_ticker "
                        f"{pos.symbol} on {pos.exchange} failed: "
                        f"{str(_e)[:120]}"
                    )
                _patch0_instrument_ghost(self, pos, order_manager, ex,
                                         "ghost_sync",
                                         current_mark=current_mark)

            close_price = pos.entry_price  # last-resort fallback
            close_reason = "ghost_sync"
            resolved_src = "entry_fallback"

            # 2026-04-24 / 2026-05-20: Reconcile ghost against exchange history
            # BEFORE falling back to ticker. The exchange ledger has the actual
            # SL/TP fill price — much more accurate than ticker.last.
            #
            # 2026-05-20 (Area 1): three improvements over the original code:
            #   1. Window widened from 6h → GHOST_LEDGER_WINDOW_H (24h default).
            #      Bitget and Bybit sometimes write ledger entries with several
            #      hours of lag; 24h captures these without bloating results.
            #   2. Two-pass reconcile: if the first sync finds no ledger record,
            #      record the position in _pending_ghost_reconcile and try again
            #      on the next sync cycle 15s later. Most lagged writes land in
            #      that 15-30s window. Only finalize as ghost_sync if BOTH the
            #      pending recheck AND the fresh attempt fail.
            #   3. Fallback price source is exchange mark_price (not ticker.last)
            #      when present. For SL/TP-triggered closes the trigger fires
            #      off mark price, so mark is closer to the actual fill.
            try:
                from config import GHOST_LEDGER_WINDOW_H, GHOST_PENDING_REQUEUE
            except ImportError:
                GHOST_LEDGER_WINDOW_H = 24
                GHOST_PENDING_REQUEUE = True

            reconciled = False
            if ex and hasattr(ex, "fetch_closed_pnl"):
                try:
                    since_ms = int((time.time() - GHOST_LEDGER_WINDOW_H * 3600) * 1000)
                    records = ex.fetch_closed_pnl(
                        since_ms=since_ms, symbol=pos.symbol) or []
                    exit_px, best = match_ghost_ledger_record(pos, records)
                    if exit_px > 0:
                        close_price = exit_px
                        reconciled = True
                        resolved_src = "exchange_ledger"
                        close_reason = "ghost_reconciled"
                        # 2026-05-24 — Seed reconcile_closed_pnl dedup
                        # with the LEDGER ct (best["close_time"]). The
                        # default existing_keys check uses pos.close_time
                        # which the upcoming tracker.close() sets to now(),
                        # so a fill that occurred hours ago would slot in
                        # a different 10-min bucket and get re-imported.
                        _ledger_ct = float((best or {}).get("close_time", 0) or 0)
                        if _ledger_ct > 0:
                            self._ghost_just_closed_dedup_keys.add(
                                (pos.exchange.lower(), pos.symbol,
                                 pos.side, int(_ledger_ct // 600))
                            )
                        # 2026-05-24: If exit matches an exchange-placed
                        # SL/TP within 50bps, relabel to stop_loss /
                        # take_profit. Ghost path is the primary exit route
                        # for SCALP-tier trades — keeping these as ghost_*
                        # hides real exit attribution.
                        real_reason = _classify_conditional_fill(pos, exit_px)
                        if real_reason is not None:
                            close_reason = real_reason
                            logger.info(
                                f"[Positions] ghost->{real_reason} "
                                f"reclassified {pos.symbol} "
                                f"side={pos.side} exit_px={exit_px:.6g} "
                                f"sl={getattr(pos, 'stop_loss', 0):.6g} "
                                f"tp={getattr(pos, 'take_profit', 0):.6g}"
                            )
                        logger.info(
                            f"[Positions] GHOST reconciled via ledger: "
                            f"{pos.symbol} {pos.side.upper()} "
                            f"exit={exit_px:.6g} "
                            f"realized_pnl={best.get('realized_pnl') if best else None}"
                        )
                        # Clear from pending map if present
                        self._pending_ghost_reconcile.pop(pos.id, None)
                except Exception as e:
                    logger.debug(
                        f"[Positions] ghost reconcile lookup failed "
                        f"{pos.symbol}: {str(e)[:120]}")

            # If still not reconciled, decide between (a) parking for retry or
            # (b) finalizing now with the mark_price fallback.
            if not reconciled:
                pending_already = pos.id in self._pending_ghost_reconcile
                if GHOST_PENDING_REQUEUE and not pending_already:
                    # First time seeing this missing position — park it for the
                    # next sync cycle (15s later). Most lagged ledger writes land
                    # within that window. Do NOT close yet.
                    self._pending_ghost_reconcile[pos.id] = time.time()
                    logger.debug(
                        f"[Positions] GHOST pending reconcile: {pos.symbol} "
                        f"{pos.side.upper()} — will retry ledger in 15s")
                    continue  # skip this ghost for now; keep in _open
                else:
                    # Either two-pass disabled OR already pending → finalize now
                    # via mark_price fallback (preferred) or ticker.last.
                    if ex:
                        try:
                            ticker = ex.fetch_ticker(pos.symbol, pos.market_type)
                            info = ticker.get("info", {}) or {}
                            mark = info.get("markPrice") or info.get("mark")
                            try:
                                tp = float(mark) if mark else 0.0
                            except (TypeError, ValueError):
                                tp = 0.0
                            if tp <= 0:
                                tp = float(
                                    ticker.get("last") or ticker.get("close") or 0)
                            if tp > 0:
                                close_price = tp
                                resolved_src = (
                                    "mark_price_fallback" if mark
                                    else "ticker_fallback")
                                # 2026-05-24: Same reclassification as the
                                # ledger path. If mark/ticker is within 50bps
                                # of an exchange-placed SL/TP, the close was
                                # really an SL/TP fill — relabel from
                                # ghost_sync to stop_loss / take_profit.
                                real_reason = _classify_conditional_fill(pos, tp)
                                if real_reason is not None:
                                    close_reason = real_reason
                                    logger.info(
                                        f"[Positions] ghost->{real_reason} "
                                        f"reclassified {pos.symbol} "
                                        f"side={pos.side} exit_px={tp:.6g} "
                                        f"sl={getattr(pos, 'stop_loss', 0):.6g} "
                                        f"tp={getattr(pos, 'take_profit', 0):.6g}"
                                    )
                        except Exception:
                            pass
                    self._pending_ghost_reconcile.pop(pos.id, None)

            # Demote routine GHOST detection to INFO when the reconcile path
            # produced a clean ledger match (Area 2 noise cleanup) — keep WARNING
            # only when we had to fall back to mark/ticker (genuine desync).
            log_fn = logger.info if reconciled else logger.warning
            log_fn(
                f"[Positions] GHOST close price source={resolved_src} "
                f"price={close_price:.6g}")
            self.close(pos.id, close_price, close_reason)

        if ghosts:
            logger.info(f"[Positions] Synced: closed {len(ghosts)} ghost position(s)")
        else:
            logger.debug("[Positions] Sync: all LIVE positions verified on exchange")

        # Prune stale _pending_ghost_reconcile entries: any position id
        # that is no longer in _open (closed by ghost loop above, or by
        # another path, or after a restart) should be removed. Also drop
        # entries older than 5 minutes to prevent unbounded growth.
        if self._pending_ghost_reconcile:
            now = time.time()
            stale_ids = [
                pid for pid, ts in self._pending_ghost_reconcile.items()
                if pid not in self._open or (now - ts) > 300
            ]
            for pid in stale_ids:
                self._pending_ghost_reconcile.pop(pid, None)

        # 2026-04-17: Reconcile realized-PnL from exchange history for trades
        # that were manually closed (or closed while the bot was offline) so
        # today's Wins and all-time totals reflect every actual close.
        try:
            self.reconcile_closed_pnl(active_exchanges)
        except Exception as e:
            logger.debug(f"[Positions] reconcile_closed_pnl failed: {str(e)[:150]}")

        return imported_list

    def reconcile_closed_pnl(self, active_exchanges: dict,
                              since_ts: float = None) -> int:
        """Pull realized-PnL ledger from each exchange and import any closed
        trades not already in `_closed` (deduped by exchange+symbol+minute).

        Returns count of newly-imported records. Runs defensively: any one
        exchange failing only prints a debug line and skips that source.

        Default window: last 48h (catches today's closes + yesterday's without
        bloating memory). Caller can widen via `since_ts` (unix seconds).
        """
        from datetime import datetime

        if since_ts is None:
            since_ts = time.time() - 48 * 3600
        since_ms = int(since_ts * 1000)

        # 2026-04-27: dedup widened from 60s to 600s and now includes side.
        # Exchanges occasionally report the same close at slightly different
        # timestamps across two 48h-window fetches (LINK on 04-27 was reported
        # at 05:36:36 then 05:48:02 — both real, both for the same fill —
        # producing two `reconciled_from_exchange` records that both passed
        # the per-minute dedup). A 10-minute bucket plus side eliminates that
        # without risking real intraday round-trip dedups (the 600s key is
        # still tight enough that re-entering the same symbol/side after
        # closing won't collide if the exits are >10min apart).
        with self._lock:
            existing_keys = set()
            for p in self._closed:
                if p.close_time:
                    existing_keys.add(
                        (p.exchange.lower(), p.symbol, p.side,
                         int(p.close_time // 600)))
                # 2026-05-25 — Bug 5 fix: also seed the open_time bucket.
                # on_close fires OUTSIDE the lock (line ~401), so a ghost
                # reconciled in the same pass can have its dedup key cleared
                # before the next cycle. A ghost close's pos.close_time is
                # set to now() but the ledger ct (used to seed the dedup key)
                # was hours earlier — different 600s buckets. Seeding BOTH
                # close_time and open_time buckets catches the ledger-ct-
                # based key regardless of which timestamp the duplicate uses.
                if getattr(p, "open_time", 0):
                    existing_keys.add(
                        (p.exchange.lower(), p.symbol, p.side,
                         int(float(p.open_time) // 600)))
            # 2026-05-24 — Merge ghost-just-closed dedup keys (seeded with
            # LEDGER ct, not now()) so reconcile doesn't double-import a
            # fill the ghost loop just reconciled in the same sync pass.
            # Without this, every ghost_reconciled creates a duplicate
            # `reconciled_from_exchange` row whose ct lands in a different
            # 10-min bucket than pos.close_time (= now()).
            existing_keys.update(self._ghost_just_closed_dedup_keys)
            self._ghost_just_closed_dedup_keys.clear()
            # Also collect (exchange, symbol, side) for live tracked positions
            # so we can skip importing a reconcile record when the bot will
            # book the same close itself via the ghost-sync path. Without
            # this skip, LINK got a `reconciled_from_exchange` AND a
            # `ghost_reconciled` record for the same fill.
            live_keys = {
                (p.exchange.lower(), p.symbol, p.side)
                for p in self._open.values()
                if not p.paper_trade
            }

        imported = 0
        for ex_name, exchange in active_exchanges.items():
            if not hasattr(exchange, "fetch_closed_pnl"):
                continue
            try:
                records = exchange.fetch_closed_pnl(since_ms=since_ms) or []
            except Exception as e:
                logger.debug(f"[Reconcile] {ex_name} fetch failed: {str(e)[:100]}")
                continue

            for r in records:
                ct = float(r.get("close_time", 0) or 0)
                if ct < since_ts:
                    continue
                sym = r.get("symbol", "")
                ex_lower = str(r.get("exchange", ex_name)).lower()
                # 2026-05-24 — Binance income ledger returns side=None
                # (the underlying API doesn't carry side). The previous
                # `(r.get("side") or "buy") or "buy"` defaulted EVERY
                # Binance short close to "buy", poisoning per-side WR
                # stats (SHORT_SIDE_FILTER, Phase 27, AutoMutator).
                # Now: when side is None, check both buy/sell variants
                # against live_keys / existing_keys; if neither matches,
                # skip the import entirely rather than fabricate a side.
                # Dollar PnL for the genuinely-orphan minority is lost,
                # but per-side analytics stay clean.
                raw_side = r.get("side")
                if raw_side is None:
                    sides_to_check = ("buy", "sell")
                    if any((ex_lower, sym, s) in live_keys
                           for s in sides_to_check):
                        continue
                    if any((ex_lower, sym, s, int(ct // 600)) in existing_keys
                           for s in sides_to_check):
                        continue
                    logger.debug(
                        f"[Reconcile] Skipping {ex_name} no-side record "
                        f"for {sym} @ ct={ct} — side unknown and no "
                        f"buy/sell variant matched tracked/closed positions."
                    )
                    continue
                side = raw_side
                # Skip if a live tracked position with the same identity
                # exists — the live close path (sync_with_exchange →
                # tracker.close → _finalize_close) will produce the
                # canonical record. Importing here would double-count.
                if (ex_lower, sym, side) in live_keys:
                    continue
                k = (ex_lower, sym, side, int(ct // 600))
                if k in existing_keys:
                    continue

                pnl = float(r.get("realized_pnl", 0) or 0)
                size = float(r.get("size", 0) or 0)
                entry = float(r.get("entry_price", 0) or 0)
                exit_p = float(r.get("exit_price", 0) or 0)
                leverage = int(r.get("leverage", 1) or 1)

                # Binance's income ledger doesn't always carry entry/size.
                # Per binary-baking-melody Phase 2A: when ALL three context
                # fields are missing (entry/exit/size), trust ONLY the dollar
                # realized_pnl from the exchange. Do NOT synthesize entry=1.0
                # or size=1.0 — those values previously poisoned positions.json
                # and the warehouse with bogus entry prices, and the resulting
                # pnl_pct = pnl/margin*100 looked like a real -5% to -30% loss
                # but was a fabrication that bypassed the 1.5-3.5% ATR SL clamp.
                # pnl_pct is set to None; downstream consumers (knowledge_model,
                # kelly_sizer) must skip None for percentage math while still
                # counting the dollar PnL — those changes ship in Phases 2C/2D.
                no_context = (entry <= 0) and (exit_p <= 0) and (size <= 0)

                sym_id = sym.replace("/", "_").replace(":", "_")
                pid = f"RECONCILED-{ex_lower}-{sym_id}-{int(ct)}"
                pos = Position(
                    id=pid,
                    exchange=ex_lower,
                    symbol=sym,
                    side=side,
                    market_type="futures",
                    strategy="reconciled_exchange",
                    # When no context, store 0.0 (not 1.0) so downstream code
                    # can detect "unknown" via the falsy entry/size and skip
                    # any percentage math. The dollar PnL below is authoritative.
                    entry_price=entry if entry > 0 else 0.0,
                    size=size if size > 0 else 0.0,
                    stop_loss=0.0,
                    take_profit=0.0,
                    leverage=leverage,
                    open_time=max(ct - 60, 0.0),
                    paper_trade=False,
                )
                # Overwrite Position's computed fields with exchange ground truth.
                # Exchange realized_pnl is already net of fees, so zero out fees.
                # exit_price=0.0 (not None) when missing: keeps is_open False
                # so the closed position is not misread as still-open. The
                # 0.0 sentinel is consistent with entry_price/size handling
                # above — downstream readers check `> 0` to decide validity.
                pos.exit_price   = exit_p if exit_p > 0 else 0.0
                pos.close_time   = ct
                pos.close_reason = (
                    "reconciled_no_context" if no_context
                    else "reconciled_from_exchange"
                )
                pos.entry_fee    = 0.0
                pos.exit_fee     = 0.0
                pos.total_fees   = 0.0
                pos.gross_pnl    = pnl
                pos.pnl          = pnl
                if no_context:
                    pos.pnl_pct = None
                else:
                    notional   = entry * size
                    margin     = notional / max(leverage, 1) if notional else 1
                    pos.pnl_pct = (pnl / margin * 100) if margin > 0 else 0.0

                with self._lock:
                    self._closed.append(pos)
                    existing_keys.add(k)
                imported += 1
                # 2026-05-25 — Bug 1 fix (no-edge-forensics audit): this
                # reconcile path appended directly to _closed and NEVER
                # fired on_close, so ~89 reconciled_* trades existed in
                # positions.json with no warehouse row at all — invisible
                # to learning_engine / WR aggregations. Write a complete
                # open+close warehouse row here directly. These positions
                # were never opened through open_position(), so there is no
                # pre-existing OPEN row to patch — record_trade_open is
                # idempotent (INSERT OR IGNORE on exch+sym+ts_entry+side)
                # and returns the id for the close update.
                try:
                    from core.warehouse import get_warehouse as _gw
                    from config import OPERATING_MODE as _mode
                    _wh = _gw()
                    _tid = _wh.record_trade_open(
                        exchange=ex_lower, symbol=sym, side=side,
                        ts_entry=float(pos.open_time),
                        entry_px=entry if entry > 0 else 0.0,
                        size=size if size > 0 else 0.0,
                        leverage=leverage,
                        market_type="futures",
                        strategy_family="reconciled_exchange",
                        mode=_mode,
                    )
                    if _tid and _tid > 0:
                        _wh.record_trade_close(
                            trade_id=_tid,
                            ts_exit=ct,
                            exit_px=exit_p if exit_p > 0 else 0.0,
                            realized_pnl=pnl,
                            exit_reason=pos.close_reason,
                        )
                except Exception as _whe:
                    logger.warning(
                        f"[Reconcile] warehouse write FAILED for {sym} "
                        f"(row will be invisible to learning): {_whe}")
                logger.warning(
                    f"[Reconcile] IMPORTED closed trade: {sym} {side.upper()} "
                    f"on {ex_lower} pnl={pnl:+.4f} USDT "
                    f"at {datetime.fromtimestamp(ct).isoformat(timespec='seconds')} "
                    f"id={pid}")

        if imported:
            with self._lock:
                if len(self._closed) > 500:
                    self._closed = self._closed[-500:]
                self._save()
            logger.info(
                f"[Reconcile] Imported {imported} closed trade(s) from exchange history")
        return imported

    def _save(self):
        """Atomic save: write to temp file, then rename over original.

        Serialized via self._lock (RLock) so concurrent callers from the
        schedule main thread and the _sltp_monitor_loop daemon don't
        overlap and overwrite each other's committed state.
        """
        with self._lock:
            self.SAVE_PATH.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "open":   [asdict(p) for p in self._open.values()],
                "closed": [asdict(p) for p in self._closed[-500:]],
            }
            tmp_path = self.SAVE_PATH.with_suffix(".tmp")
            try:
                tmp_path.write_text(json.dumps(data, indent=2), encoding="utf-8")
                # Backup current file before replacing
                if self.SAVE_PATH.exists():
                    try:
                        self.BACKUP_PATH.write_bytes(self.SAVE_PATH.read_bytes())
                    except Exception:
                        pass
                tmp_path.replace(self.SAVE_PATH)
            except Exception as e:
                logger.error(f"[Positions] Save failed: {e}")
                # Clean up temp file on failure
                try:
                    tmp_path.unlink(missing_ok=True)
                except Exception:
                    pass

    def _load(self):
        if not self.SAVE_PATH.exists():
            # Try backup if main file missing
            if self.BACKUP_PATH.exists():
                logger.warning("[Positions] Main file missing, loading from backup")
                source = self.BACKUP_PATH
            else:
                return
        else:
            source = self.SAVE_PATH
        try:
            raw = source.read_text(encoding="utf-8")
            data = json.loads(raw)
            for d in data.get("open", []):
                d.setdefault("entry_fee",   0.0)
                d.setdefault("exit_fee",    0.0)
                d.setdefault("total_fees",  0.0)
                d.setdefault("gross_pnl",   None)
                d.setdefault("paper_trade", True)   # old records = paper
                try:
                    p = Position(**d)
                    self._open[p.id] = p
                except Exception as e:
                    logger.warning(f"[Tracker] Skipping malformed open position: {e}")
            for d in data.get("closed", []):
                d.setdefault("entry_fee",   0.0)
                d.setdefault("exit_fee",    0.0)
                d.setdefault("total_fees",  0.0)
                d.setdefault("gross_pnl",   None)
                d.setdefault("paper_trade", True)
                try:
                    self._closed.append(Position(**d))
                except Exception as e:
                    logger.warning(f"[Tracker] Skipping malformed closed position: {e}")
            paper_n = sum(1 for p in self._closed if p.paper_trade)
            live_n  = len(self._closed) - paper_n
            logger.info(
                f"[Positions] Loaded {len(self._open)} open, "
                f"{len(self._closed)} closed "
                f"({paper_n} paper / {live_n} live)"
            )
        except json.JSONDecodeError as e:
            logger.error(f"[Positions] Corrupt JSON in {source}: {e}")
            # Try backup if main was corrupt
            if source == self.SAVE_PATH and self.BACKUP_PATH.exists():
                logger.warning("[Positions] Trying backup file...")
                source_bak = self.BACKUP_PATH
                try:
                    data = json.loads(source_bak.read_text(encoding="utf-8"))
                    for d in data.get("open", []):
                        d.setdefault("entry_fee", 0.0)
                        d.setdefault("exit_fee", 0.0)
                        d.setdefault("total_fees", 0.0)
                        d.setdefault("gross_pnl", None)
                        d.setdefault("paper_trade", True)
                        try:
                            p = Position(**d)
                            self._open[p.id] = p
                        except Exception as e:
                            logger.warning(f"[Tracker] Skipping malformed open position: {e}")
                    for d in data.get("closed", []):
                        d.setdefault("entry_fee", 0.0)
                        d.setdefault("exit_fee", 0.0)
                        d.setdefault("total_fees", 0.0)
                        d.setdefault("gross_pnl", None)
                        d.setdefault("paper_trade", True)
                        try:
                            self._closed.append(Position(**d))
                        except Exception as e:
                            logger.warning(f"[Tracker] Skipping malformed closed position: {e}")
                    logger.info(f"[Positions] Recovered from backup: {len(self._open)} open, {len(self._closed)} closed")
                except Exception as e2:
                    logger.error(f"[Positions] Backup also failed: {e2}")
        except Exception as e:
            logger.error(f"[Positions] Load error: {e}")
