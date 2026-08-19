"""
core/order_mgmt/maker_first.py — OrderManager _MakerFirstMixin mixin (Phase D4).
"""
import json
import threading
import time
import uuid
from types import SimpleNamespace

from loguru import logger

from core.order_mgmt.helpers import (
    PENDING_MAKER_ENTRIES_PATH,
    _MAKER_CHASE_GUARD_PCT,
    _maker_first_cfg,
    _mid_from_ticker,
    _safe_ticker_px,
)
from exchanges.base import BaseExchange
from utils.atomic_io import atomic_write_json

class _MakerFirstMixin:
    @staticmethod
    def _maker_base(symbol: str | None) -> str:
        """Canonical base key used by position and pending-intent exclusivity."""
        return str(symbol or "").split("/")[0].split(":")[0].upper()

    def _pending_maker_count(self, *, exclude_key: str | None = None) -> int:
        """Number of live reservations, optionally excluding one resolver."""
        with self._pending_maker_lock:
            return sum(
                1 for key in self._pending_maker
                if not exclude_key or key != exclude_key
            )

    def pending_maker_reservations(
        self, *, exclude_key: str | None = None
    ) -> list:
        """Return Position-shaped immutable views of pending PAPER exposure.

        A maker intent has no Position yet, but it has consumed the same scarce
        portfolio resources: one slot, one base asset, gross notional and
        stop-risk headroom.  The conservative entry price includes the maximum
        allowed chase distance so a taker fallback cannot exceed its booking.
        """
        if _maker_first_cfg().get("enabled", False) or self._pending_maker:
            self._maker_first_boot()
        reservations = []
        with self._pending_maker_lock:
            rows = list(self._pending_maker.items())
        for key, intent in rows:
            if exclude_key and key == exclude_key:
                continue
            try:
                limit_px = float(intent.get("limit_px") or 0.0)
                signal_px = float(intent.get("signal_px") or limit_px)
                size = abs(float(intent.get("size") or 0.0))
                stop_frac = float(intent.get("sl_pct") or 0.0)
                if limit_px <= 0 or size <= 0 or stop_frac <= 0:
                    # Malformed reservations remain visible and therefore fail
                    # aggregate-risk closed instead of silently disappearing.
                    entry_px = max(limit_px, signal_px, 0.0)
                    stop_loss = 0.0
                else:
                    entry_px = max(limit_px, signal_px) * (
                        1.0 + _MAKER_CHASE_GUARD_PCT
                    )
                    stop_loss = (
                        entry_px * (1.0 - stop_frac)
                        if intent.get("side") == "buy"
                        else entry_px * (1.0 + stop_frac)
                    )
                reservations.append(SimpleNamespace(
                    id=f"pending-maker:{key}",
                    reservation_key=key,
                    exchange=str(intent.get("exchange") or ""),
                    symbol=str(intent.get("symbol") or ""),
                    side=str(intent.get("side") or ""),
                    market_type=str(intent.get("market_type") or "futures"),
                    strategy=str(intent.get("strategy") or ""),
                    size=size,
                    entry_price=entry_px,
                    stop_loss=stop_loss,
                    is_pending_maker_reservation=True,
                ))
            except (TypeError, ValueError):
                reservations.append(SimpleNamespace(
                    id=f"pending-maker:{key}",
                    reservation_key=key,
                    exchange=str(intent.get("exchange") or ""),
                    symbol=str(intent.get("symbol") or ""),
                    side=str(intent.get("side") or ""),
                    market_type="futures",
                    strategy=str(intent.get("strategy") or ""),
                    size=0.0,
                    entry_price=0.0,
                    stop_loss=0.0,
                    is_pending_maker_reservation=True,
                ))
        return reservations

    def _maker_fill_risk_rejection(
        self,
        *,
        reservation_key: str | None,
        size: float,
        fill_price: float,
        stop_loss: float,
    ) -> str | None:
        """Return a fail-closed PAPER maker fill-time portfolio rejection."""
        try:
            from config import MAX_AGGREGATE_OPEN_RISK_PCT
            from config import MAX_PORTFOLIO_EXPOSURE_PCT
            from config import STRESSED_EXIT_COST_FRAC
            from core.risk_manager import (
                aggregate_open_risk_breached,
                exposure_breached,
            )

            key = str(reservation_key or "").strip()
            if not key:
                raise ValueError("maker reservation_key is missing")
            with self._pending_maker_lock:
                if key not in self._pending_maker:
                    raise ValueError("maker reservation_key is not pending")

            provider = self.portfolio_equity_provider
            if not callable(provider):
                raise ValueError("paper portfolio equity provider is unavailable")
            equity = provider()
            current = list(self.tracker.get_open() or [])
            current.extend(self.pending_maker_reservations(exclude_key=key))
            notional = abs(float(size) * float(fill_price))
            stop_frac = abs(float(stop_loss) - float(fill_price)) / float(fill_price)

            if exposure_breached(
                current,
                notional,
                equity,
                MAX_PORTFOLIO_EXPOSURE_PCT,
            ):
                logger.warning(
                    "[MakerFirst] fill blocked by portfolio exposure cap"
                )
                return "maker_fill_portfolio_exposure_cap"
            if aggregate_open_risk_breached(
                current,
                notional,
                stop_frac,
                equity,
                MAX_AGGREGATE_OPEN_RISK_PCT,
                STRESSED_EXIT_COST_FRAC,
            ):
                logger.warning(
                    "[MakerFirst] fill blocked by aggregate open-risk cap"
                )
                return "maker_fill_aggregate_open_risk_cap"
            return None
        except Exception as exc:
            logger.warning(
                f"[MakerFirst] fill-time portfolio risk check failed closed: {exc}"
            )
            return "maker_fill_risk_error"

    @staticmethod
    def _stable_maker_resolution_id(intent: dict) -> str:
        """Return the persisted child id, deriving a stable id for legacy state."""
        existing = str(intent.get("resolution_decision_id") or "").strip()
        if existing:
            return existing
        seed = "|".join((
            str(intent.get("parent_decision_id") or intent.get("decision_id") or "direct"),
            str(intent.get("exchange") or ""),
            str(intent.get("symbol") or ""),
            str(intent.get("side") or ""),
            str(intent.get("created_ts") or ""),
        ))
        derived = f"maker-resolution-{uuid.uuid5(uuid.NAMESPACE_URL, seed)}"
        intent["resolution_decision_id"] = derived
        return derived

    @staticmethod
    def _maker_confidence(intent: dict) -> float | None:
        raw = intent.get("decision_confidence")
        if raw is None:
            raw = intent.get("mcp_score")
            try:
                raw = float(raw) / 100.0 if raw is not None else None
            except (TypeError, ValueError):
                return None
        try:
            value = float(raw)
        except (TypeError, ValueError):
            return None
        return value if 0.0 <= value <= 1.0 else None

    def _maker_provenance_intent(self, intent: dict):
        payload = intent.get("provenance_intent")
        if not isinstance(payload, dict):
            return None
        try:
            from core.contracts import OrderIntent

            return OrderIntent.from_dict(payload)
        except Exception as exc:
            logger.error(f"[Provenance] invalid persisted maker order intent: {exc}")
            return None

    def _record_maker_resolution_decision(
        self,
        intent: dict,
        *,
        outcome: str,
        reason: str,
        execution_snapshot: dict | None = None,
        filled_position=None,
    ) -> bool:
        """Append one child terminal decision; never mutate the parent row."""
        if not getattr(self, "enforce_event_provenance", False):
            return True
        try:
            from core.decision_provenance import record_terminal_decision
            from core.warehouse import get_warehouse

            parent_id = str(
                intent.get("parent_decision_id")
                or intent.get("decision_id")
                or ""
            )
            action = {
                "type": "OPEN",
                "symbol": intent.get("symbol"),
                "exchange": intent.get("exchange"),
                "market_type": intent.get("market_type", "futures"),
                "side": intent.get("side"),
                "strategy_id": (
                    intent.get("strategy_id")
                    or intent.get("strategy")
                    or "unassigned"
                ),
                "strategy_version": intent.get("model_version"),
                "model_version": intent.get("model_version"),
                "confidence": self._maker_confidence(intent),
                "candidate_id": intent.get("candidate_id"),
                "decision_id": self._stable_maker_resolution_id(intent),
                "parent_decision_id": parent_id,
                "resolution_kind": "paper_maker_first",
                "execution_snapshot": execution_snapshot,
            }
            if filled_position is not None:
                action.update({
                    "exchange_symbol": intent.get("symbol"),
                    "filled_quantity": float(filled_position.size),
                    "filled_price": float(filled_position.entry_price),
                })
            record_terminal_decision(
                get_warehouse(),
                action,
                outcome=outcome,
                reason=reason,
                stage="maker_resolution",
            )
            return True
        except Exception as exc:
            logger.error(
                f"[Provenance] maker resolution write failed for "
                f"{intent.get('symbol')}: {exc}"
            )
            try:
                self.risk.latch_incident(
                    f"maker resolution provenance failed: {exc}",
                    category="execution",
                )
            except Exception:
                pass
            return False

    def _register_pending_maker(
        self, exchange: BaseExchange, key: str, intent: dict
    ) -> tuple[bool, str]:
        """Atomically reserve and durably persist one virtual maker intent."""
        base = self._maker_base(intent.get("symbol"))
        with self._pending_maker_lock:
            if key in self._pending_maker:
                return False, "maker_first_pending"
            if any(
                self._maker_base(existing.get("symbol")) == base
                for existing in self._pending_maker.values()
            ):
                return False, "maker_first_base_reserved"

            resolution_id = self._stable_maker_resolution_id(intent)
            provenance = self._append_order_intent(
                exchange,
                str(intent["symbol"]),
                str(intent.get("market_type") or "futures"),
                str(intent["side"]),
                float(intent["size"]),
                resolution_id,
                str(intent.get("strategy_id") or intent.get("strategy") or "unassigned"),
                intent.get("candidate_id"),
                float(intent.get("leverage") or 1),
                order_type="limit",
                limit_price=float(intent["limit_px"]),
                post_only=True,
                parent_decision_id=str(intent.get("parent_decision_id") or ""),
                confidence=self._maker_confidence(intent),
            )
            if provenance is False:
                return False, "order_intent_persistence_failed"
            intent["provenance_intent"] = (
                provenance.to_dict() if provenance is not None else None
            )
            self._pending_maker[key] = intent
            if provenance is not None:
                self._append_execution_event(
                    provenance,
                    "acknowledged",
                    quantity=float(intent["size"]),
                    price=float(intent["limit_px"]),
                    context={"paper": True, "virtual_post_only": True},
                )
            if self._persist_pending_maker():
                return True, "maker_first_pending"

            self._pending_maker.pop(key, None)
            if provenance is not None:
                self._append_execution_event(
                    provenance,
                    "error",
                    reason="maker_pending_persistence_failed",
                )
            self._record_maker_resolution_decision(
                intent,
                outcome="error",
                reason="maker_pending_persistence_failed",
            )
            return False, "maker_pending_persistence_failed"

    def _record_maker_nonfill(
        self,
        intent: dict,
        *,
        outcome: str,
        reason: str,
        event_type: str = "cancelled",
        execution_snapshot: dict | None = None,
    ) -> None:
        provenance = self._maker_provenance_intent(intent)
        if provenance is not None:
            self._append_execution_event(
                provenance,
                event_type,
                reason=reason,
                context={"paper": True, "virtual_post_only": True},
            )
        self._record_maker_resolution_decision(
            intent,
            outcome=outcome,
            reason=reason,
            execution_snapshot=execution_snapshot,
        )

    def _maker_first_boot(self):
        """One-time boot sweep for maker-first paper entries.

        Pending intents left on disk by a dead process are CANCELLED (never
        ghost-opened): they are not loaded into memory, and the state file is
        rewritten with an empty pending map. Soak counters and per-fill
        measurement rows survive the restart. Lazy — first enabled use.
        """
        if self._maker_first_booted:
            return
        self._maker_first_booted = True
        try:
            if not self._pending_maker_path.exists():
                return
            state = json.loads(
                self._pending_maker_path.read_text(encoding="utf-8"))
            counters = state.get("counters") or {}
            for k in self._maker_counters:
                self._maker_counters[k] = int(
                    counters.get(k, self._maker_counters[k]))
            self._maker_fills = list(state.get("fills") or [])[-200:]
            stale = state.get("pending") or {}
            if stale:
                # A restart cancels the virtual orders, but cancellation is a
                # real terminal resolution.  Emit a distinct append-only child
                # decision for each one before clearing the state; never turn
                # the parent's DEFERRED row into a fabricated rejection/fill.
                for stale_intent in stale.values():
                    if isinstance(stale_intent, dict):
                        self._record_maker_nonfill(
                            stale_intent,
                            outcome="cancelled",
                            reason="maker_restart_cancelled",
                        )
                logger.warning(
                    f"[MakerFirst] boot sweep: cancelled {len(stale)} stale "
                    f"pending intent(s) from a prior process "
                    f"({sorted(stale)}) — never ghost-opened")
                self._persist_pending_maker()
        except Exception as e:
            logger.warning(f"[MakerFirst] boot sweep failed: {e}")

    def _persist_pending_maker(self):
        """Persist pending intents + soak counters so a restart cancels
        cleanly (the boot sweep discards whatever is pending here)."""
        try:
            with self._pending_maker_lock:
                payload = {
                    "pending": self._pending_maker,
                    "counters": self._maker_counters,
                    "fills": self._maker_fills[-200:],
                }
            self._pending_maker_path.parent.mkdir(parents=True, exist_ok=True)
            atomic_write_json(self._pending_maker_path, payload, indent=2)
            return True
        except Exception as e:
            logger.warning(f"[MakerFirst] persist failed: {e}")
            return False

    def _maker_wick_through(self, exchange: BaseExchange, intent: dict) -> bool:
        """True when a CLOSED 1m bar that opened AFTER the intent printed
        strictly through the resting limit (wick fill between monitor ticks).

        Bars that opened before the intent existed cannot honestly fill it,
        and a still-forming bar is not consulted (its extreme is not final).
        """
        try:
            candles = exchange.fetch_ohlcv(
                intent["symbol"], "1m", limit=3,
                market_type=intent["market_type"]) or []
        except Exception:
            return False
        limit_px = float(intent["limit_px"])
        created = float(intent.get("created_ts") or 0)
        now = time.time()
        for c in candles:
            try:
                ts = float(c[0]) / 1000.0
                high, low = float(c[2]), float(c[3])
            except (TypeError, ValueError, IndexError):
                continue
            if ts < created or ts + 60.0 > now:
                continue  # pre-intent bar, or bar still forming
            if intent["side"] == "buy" and low < limit_px:
                return True
            if intent["side"] == "sell" and high > limit_px:
                return True
        return False

    def _resolve_pending_maker_entries(self, exchange: BaseExchange):
        """Resolve this venue's pending virtual maker intents (monitor tick).

        HONEST FILL RULE: the resting limit fills as MAKER only when the
        market trades STRICTLY THROUGH it — ticker last, or a post-intent
        closed 1m bar extreme, strictly beyond the limit; a touch never
        fills. After timeout_sec: taker fallback at the CURRENT price, unless
        the market ran > _MAKER_CHASE_GUARD_PCT past the signal price, in
        which case the entry is abandoned (maker_chase_abandoned).
        """
        self._maker_first_boot()
        if not self._pending_maker:
            return
        cfg = _maker_first_cfg()
        # Align PAPER with LIVE (2026-08-19). When MAKER_ONLY governs the live
        # executor (wait max_wait_sec, then SKIP with no market fallback —
        # core/smart_executor.py), paper must do the SAME, or paper P&L is
        # drawn from a ~2x-larger entry population (taker fallbacks live would
        # never take). Reading the live knob here means the two paths cannot
        # drift: flip MAKER_ONLY_ENABLED and both change together.
        try:
            from config import MAKER_ONLY as _mo
            maker_only = bool(_mo.get("enabled", False))
            mo_wait = float(_mo.get("max_wait_sec", 120))
        except Exception:
            maker_only, mo_wait = False, 120.0
        timeout = mo_wait if maker_only else float(cfg.get("timeout_sec", 45))
        now = time.time()
        keys = [k for k, v in self._pending_maker.items()
                if v.get("exchange") == exchange.name]
        for key in keys:
            intent = self._pending_maker.get(key)
            if not intent:
                continue
            symbol, side = intent["symbol"], intent["side"]
            limit_px = float(intent["limit_px"])
            try:
                ticker = exchange.fetch_ticker(symbol, intent["market_type"])
            except Exception as e:
                logger.debug(f"[MakerFirst] {symbol}: ticker fetch failed ({e})")
                continue
            last = _safe_ticker_px(ticker, "last")
            # Strict trade-through on the live print (touch does NOT fill) …
            filled = last > 0 and (
                last < limit_px if side == "buy" else last > limit_px)
            # … or on a post-intent closed 1m bar extreme (wick fill).
            if not filled:
                filled = self._maker_wick_through(exchange, intent)
            if filled:
                self._finalize_maker_intent(exchange, key, intent, "maker")
                continue
            if now - float(intent.get("created_ts") or now) <= timeout:
                continue  # still resting inside the timeout window
            # Timed out. Runaway guard: never chase a market that ran beyond
            # the guard fraction of the ORIGINAL signal price.
            signal_px = float(intent.get("signal_px") or 0)
            cross_px = _safe_ticker_px(
                ticker, "ask" if side == "buy" else "bid") or last
            ran = 0.0
            if signal_px > 0 and cross_px > 0:
                ran = ((cross_px - signal_px) / signal_px if side == "buy"
                       else (signal_px - cross_px) / signal_px)
            if ran > _MAKER_CHASE_GUARD_PCT:
                abandoned = self._pending_maker.pop(key, None) or intent
                self._maker_counters["abandoned"] += 1
                self._record_maker_nonfill(
                    abandoned,
                    outcome="cancelled",
                    reason="maker_chase_abandoned",
                )
                self._persist_pending_maker()
                self.last_open_reject = "maker_chase_abandoned"
                logger.info(
                    f"[MakerFirst] {symbol} {side.upper()}: chase abandoned — "
                    f"price ran {ran * 100:.2f}% past signal {signal_px:.6g} "
                    f"(guard {_MAKER_CHASE_GUARD_PCT * 100:.1f}%)")
                continue
            if maker_only:
                # Mirror live MAKER_ONLY: the resting maker never filled, so
                # LIVE would SKIP with no market fallback. Paper does the same
                # — booking a taker fill here is exactly the population
                # divergence this alignment removes.
                skipped = self._pending_maker.pop(key, None) or intent
                self._maker_counters["maker_only_skip"] = (
                    self._maker_counters.get("maker_only_skip", 0) + 1)
                self._record_maker_nonfill(
                    skipped, outcome="cancelled", reason="maker_only_skip")
                self._persist_pending_maker()
                self.last_open_reject = "maker_only_skip"
                logger.info(
                    f"[MakerFirst] {symbol} {side.upper()}: maker-only timeout "
                    f"after {timeout:.0f}s — skipped, no taker fallback "
                    f"(mirrors live MAKER_ONLY)")
                continue
            self._finalize_maker_intent(
                exchange, key, intent, "taker_fallback")

    def _finalize_maker_intent(self, exchange: BaseExchange, key: str,
                               intent: dict, fill_type: str):
        """Open the position for a resolved intent through the normal open
        path (all gates re-checked), then log the soak measurement.

        fill_type 'maker' fills AT the resting limit with the venue maker
        fee and no slippage; 'taker_fallback' pays the full current-price
        taker fill like any market order.

        Intent is removed from pending only after a successful open OR after
        a recorded nonfill — never silently dropped mid-flight (2026-07-23 audit).
        """
        side = intent["side"]
        limit_px = float(intent["limit_px"])
        sl_pct = float(intent.get("sl_pct") or 0.0)
        tp_pct = float(intent.get("tp_pct") or 0.0)
        # Provisional SL/TP off the limit; open_position re-derives both off
        # the ACTUAL fill so the ACCURACY band geometry stays exact.
        if side == "buy":
            prov_sl, prov_tp = limit_px * (1 - sl_pct), limit_px * (1 + tp_pct)
        else:
            prov_sl, prov_tp = limit_px * (1 + sl_pct), limit_px * (1 - tp_pct)
        # 2026-07-21: maker_intent MUST ride along — open_position's terminal
        # provenance write reads ctx["maker_intent"]; an empty dict raised
        # "candidate symbol is missing" and latched an execution-incident HALT
        # after the very first maker fill.
        # 2026-07-23: also carry provenance_intent + authorization strategy_id
        # so entry policy and append-only intent lineage stay consistent.
        ctx = {
            "reservation_key": key,
            "fill_type": fill_type,
            "fill_px": limit_px,
            "sl_pct": sl_pct,
            "tp_pct": tp_pct,
            "maker_intent": intent,
            "provenance_intent": intent.get("provenance_intent"),
        }
        resolved_snapshot = intent.get("execution_snapshot")
        if getattr(self, "enforce_event_provenance", False):
            try:
                from config import (
                    EXECUTION_BOOK_DEPTH_LEVELS,
                    EXECUTION_BOOK_MAX_AGE_SEC,
                    MAX_ENTRY_SLIPPAGE_BPS,
                )
                from core.execution_guard import fetch_and_validate_execution_book

                refreshed = fetch_and_validate_execution_book(
                    exchange,
                    venue=self._provenance_venue(exchange.name),
                    market_type=intent["market_type"],
                    canonical_symbol=intent["symbol"],
                    exchange_symbol=intent["symbol"],
                    side=side,
                    requested_quantity=float(intent["size"]),
                    max_slippage_bps=MAX_ENTRY_SLIPPAGE_BPS,
                    max_age_seconds=EXECUTION_BOOK_MAX_AGE_SEC,
                    limit=EXECUTION_BOOK_DEPTH_LEVELS,
                    realtime_provider=self.realtime_streams,
                )
                if not refreshed.allowed:
                    raise ValueError(refreshed.reason)
                resolved_snapshot = refreshed.to_action_dict()
            except Exception as exc:
                self.last_open_reject = "maker_resolution_book_invalid"
                logger.warning(
                    f"[MakerFirst] {intent['symbol']}: fresh execution book "
                    f"required at resolution: {exc}"
                )
                self._pending_maker.pop(key, None)
                self._record_maker_nonfill(
                    intent,
                    outcome="cancelled",
                    reason="maker_resolution_book_invalid",
                )
                self._persist_pending_maker()
                return
        # The pending decision is the immutable DEFERRED parent.  A maker
        # resolution creates its own terminal child; the trade must link to
        # that FILLED child while retaining the original decision as lineage.
        parent_decision_id = str(
            intent.get("parent_decision_id")
            or intent.get("decision_id")
            or ""
        ).strip()
        resolution_decision_id = self._stable_maker_resolution_id(intent)
        pos = self.open_position(
            exchange, intent["symbol"], side, intent["market_type"],
            intent["strategy"], float(intent["size"]), prov_sl, prov_tp,
            leverage=int(intent.get("leverage") or 1),
            candidate_id=intent.get("candidate_id"),
            mcp_score=intent.get("mcp_score"),
            model_version=intent.get("model_version"),
            decision_id=resolution_decision_id,
            execution_snapshot=resolved_snapshot,
            authorization_strategy_id=intent.get("strategy_id"),
            decision_confidence=intent.get("decision_confidence"),
            decision_parent_id=parent_decision_id or None,
            _maker_first_ctx=ctx)
        if pos is None:
            reject = self.last_open_reject or "maker_finalize_open_rejected"
            logger.warning(
                f"[MakerFirst] {intent['symbol']} {side.upper()}: resolved as "
                f"{fill_type} but the open was rejected "
                f"({reject}) — intent dropped with nonfill record")
            self._pending_maker.pop(key, None)
            self._record_maker_nonfill(
                intent,
                outcome="rejected",
                reason=str(reject),
            )
            self._persist_pending_maker()
            return
        self._pending_maker.pop(key, None)
        self._maker_counters[fill_type] = (
            self._maker_counters.get(fill_type, 0) + 1)
        elapsed = time.time() - float(intent.get("created_ts") or time.time())
        signal_px = float(intent.get("signal_px") or 0)
        if fill_type == "maker":
            from core.position_tracker import _fee_rate as _mf_fee_rate
            mt = intent["market_type"]
            fee_saved_bps = (
                _mf_fee_rate(mt, exchange.name, "taker")
                - _mf_fee_rate(mt, exchange.name, "maker")) * 1e4
            px_delta_bps = 0.0
            if signal_px > 0:
                px_delta_bps = ((signal_px - pos.entry_price) / signal_px
                                if side == "buy" else
                                (pos.entry_price - signal_px) / signal_px) * 1e4
            self._maker_fills.append({
                "ts": time.time(), "symbol": intent["symbol"], "side": side,
                "signal_px": signal_px, "fill_px": float(pos.entry_price),
                "px_delta_bps": round(px_delta_bps, 3),
                "fee_saved_bps": round(fee_saved_bps, 3),
                "wait_sec": round(elapsed, 1),
            })
            logger.info(
                f"[MakerFirst] {intent['symbol']} {side.upper()}: "
                f"filled as MAKER after {elapsed:.0f}s "
                f"(saved ~{fee_saved_bps + px_delta_bps:.1f} bps: "
                f"fee {fee_saved_bps:.1f} + px {px_delta_bps:+.1f}; "
                f"signal {signal_px:.6g} → fill {pos.entry_price:.6g})")
        else:
            logger.info(
                f"[MakerFirst] {intent['symbol']} {side.upper()}: taker "
                f"FALLBACK after {elapsed:.0f}s @ {pos.entry_price:.6g} "
                f"(never traded through {limit_px:.6g})")
        self._persist_pending_maker()

