"""DeepBreakoutLane — ACTIVE PAPER-trading lane for the Codex deep_breakout strategy.

Owner directive 2026-07-11: "start trading aggressively". The bot is in PAPER
mode (sim fills via core/sim_execution.py), so this lane actively places PAPER
orders through the NORMAL order path (order_manager.open_position) — unlike the
log-only core/agents/breakout_probe_agent.py, which stays untouched and keeps
collecting frozen-gate evidence in parallel. "Aggressive" means trading every
valid signal at the researched size, NOT exceeding the researched config.

PAPER-ONLY HARD GATE (binding, rail 1): ``assert_paper_only()`` refuses both
construction and every tick unless ``config.DRY_RUN`` is True (i.e.
OPERATING_MODE != CONTROLLED_LIVE). Going live later requires an explicit owner
decision AND a code change — per Codex's own doc ("Do not connect this strategy
to live orders until it passes forward testing", DEEP_60D_BREAKOUT_STRATEGY.md)
and our promotion-gate rule. Pinned by tests/test_deep_breakout_lane.py.

STRATEGY (frozen Codex config; signal math IMPORTED from the tested probe —
never re-implemented here):
- 4h chart, CLOSED bars only (no forming-bar look-ahead; same closed-bar
  discipline as backtest_v3's closed_4h_bar fix)
- LONG on close above the prior 360-bar high, SHORT on close below the prior
  360-bar low (channel shifted one bar — the signal bar's extremes excluded)
- stop = entry -/+ 2.2 x ATR(14, Wilder); target = 3x initial risk
- max hold 126 bars (21d) -> lane-owned close through the normal close path
- one position per BASE across venues; re-entry earliest one bar after the
  exit bar; one evaluation per 4h boundary

WIN-RATE WARNING / COHORT SEPARATION (rail 2): this is a 3:1 R:R trend system,
~33% WR BY DESIGN. Every order carries strategy='deep_breakout' (->
trades.strategy_family) so the accuracy-band cohort metrics (owner's 65-67% WR
goal) exclude it — scripts/report_goal_progress.py and the dashboard THIS-BOOT
lane filter the family out.

SIZING (rail 3): researched 1% equity risk per trade / risk distance, notional
<= 2x equity per position (within the 2.5x charter clamp), max 4 concurrent
lane positions AND lane gross notional <= 6% of equity (so the band lane keeps
room under the shared 12% portfolio cap). The §2 12% exposure check
(core.risk_manager.exposure_breached) and correlation sizing apply ON TOP; the
order path's own gates (risk.can_trade incl. the daily-loss breaker, min
notional, position caps) are NOT bypassed. leverage=1.

VENUES (rail 4): binance + bybit only (Codex cross-exchange confidence:
binance 78.8, bybit 57.4). bitget EXCLUDED — 44.7 on only ~100 days of usable
4h history. Binance-first at signal time.

EXITS (rail 5): the standard SL/TP monitoring path (sim wick-triggers in
paper, exchange-side conditionals in live), the charter §2 8% Stop-Loss
Guardian backstop (order_manager's DEEP-BREAKOUT block), and this lane's
126-bar max-hold close via order_manager.close_position — which fires every
on_close hook. The mcp lane's scalp machinery (partial-TP, trailing, BE,
entry-staleness, age/stale, discretionary CLOSE) is suppressed for lane
positions because it would clip the researched 3R geometry (tsmom precedent).
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Callable, Optional

from loguru import logger

# Signal math + frozen Codex constants come from the TESTED probe modules —
# one implementation, two consumers (log-only probe + this active lane).
from core.agents.breakout_probe_agent import (
    BAR_S,
    MAX_HOLD_BARS,
    REWARD_RISK,
    STOP_ATR,
    TIMEFRAME,
    WINDOW_BARS,
    breakout_signal_last,
)
from core.agents.tsmom_probe_agent import ATR_LEN, wilder_atr_last
from core.kill_switch import entries_halted

STRATEGY_FAMILY = "deep_breakout"
MODEL_VERSION = "deep_breakout_4h_v1"
MAX_HOLD_SEC = MAX_HOLD_BARS * BAR_S  # 126 x 4h = 504h = 21d


class PaperOnlyViolation(RuntimeError):
    """Raised when the lane is touched outside PAPER (config.DRY_RUN False)."""


def assert_paper_only() -> None:
    """Rail 1: structural PAPER-only gate — read config.DRY_RUN at CALL time.

    Not a soft check: construction and every tick() go through here. Going
    live requires an explicit owner decision and a code change.
    """
    import config

    if not getattr(config, "DRY_RUN", False):
        raise PaperOnlyViolation(
            "deep_breakout lane is PAPER-ONLY: OPERATING_MODE is CONTROLLED_LIVE "
            "(config.DRY_RUN=False). Codex forward gate + promotion-gate rule: "
            "going live requires an explicit owner decision and a code change."
        )


def is_deep_breakout_position(pos) -> bool:
    """True if a tracked Position was opened by this lane.

    Keys on the persisted ``Position.strategy`` tag (set at entry via
    open_position(strategy=...)), so the exit policy follows how the position
    was OPENED — same pattern as tsmom_signal.is_tsmom_position.
    """
    return str(getattr(pos, "strategy", "") or "").lower() == STRATEGY_FAMILY


class DeepBreakoutLane:
    """Active PAPER lane: evaluate closed 4h bars, enter via order_manager,
    enforce the 126-bar max-hold. See module header for the binding rails."""

    def __init__(
        self,
        *,
        exchanges: dict,
        order_manager,
        tracker,
        risk,
        equity_provider: Callable[[], float],
        ohlcv_provider: Callable[[str, str, str, int], list],
        warehouse=None,
        entry_pause_check: Optional[Callable[[], tuple]] = None,
        cfg: Optional[dict] = None,
        now_fn: Callable[[], float] = time.time,
        state_path=None,
    ):
        assert_paper_only()  # rail 1: cannot even construct off-PAPER
        if cfg is None:
            import config as _c

            cfg = getattr(_c, "DEEP_BREAKOUT_LANE", {}) or {}
        self._cfg = dict(cfg)
        self._exchanges = exchanges or {}
        self._om = order_manager
        self._tracker = tracker
        self._risk = risk
        self._equity = equity_provider
        self._ohlcv = ohlcv_provider
        self._wh = warehouse
        self._pause_check = entry_pause_check
        self._now = now_fn
        self._state_path = Path(state_path or "data/deep_breakout_lane.json")
        # base -> open ts (s) of the last 4h bar already evaluated/acted on.
        # Persisted so a restart never re-signals an already-acted bar.
        self._last_seen: dict = self._load_state()

    # ── public tick (scheduled ~5min; no-ops off the 4h boundary) ────────
    def tick(self) -> dict:
        assert_paper_only()  # rail 1: re-checked at the entry point EVERY tick
        now = int(self._now())
        stats = {"evaluated": 0, "entered": 0, "max_hold_closed": 0, "skipped": 0}
        stats["max_hold_closed"] = self._close_expired(now)

        # BTC vol pause (rail 5) — same market-wide breaker as _execute_open
        # C.4, evaluated once per tick. A2 convention: a BROKEN gate blocks
        # entries (fail-closed) rather than defaulting to ALLOW.
        paused, pause_reason = False, ""
        if self._pause_check is not None:
            try:
                paused, pause_reason = self._pause_check()
            except Exception as e:
                paused, pause_reason = True, f"pause_check_error:{e}"

        # Entries-only kill switch (Codex port, 2026-07-12): blocks NEW lane
        # entries only — the max-hold close above and all monitoring keep
        # running. Transient like the vol pause: the signal bar is NOT marked
        # seen, so entries resume within the bar once data/KILL_SWITCH is
        # removed. State-change logging lives in core.kill_switch.
        if not paused and entries_halted():
            paused, pause_reason = True, "kill_switch_active"

        for symbol in self._cfg.get("symbols", ()):
            base = str(symbol).split("/")[0].split(":")[0]
            try:
                result = self._eval_base(base, symbol, now, paused, pause_reason)
                stats["evaluated"] += 1
                if result == "entered":
                    stats["entered"] += 1
                elif result not in ("noop", "no_signal"):
                    stats["skipped"] += 1
            except Exception as e:
                logger.warning(f"[DeepBreakout] {base} eval error: {e}")
        return stats

    # ── entry evaluation ─────────────────────────────────────────────────
    def _eval_base(self, base: str, symbol: str, now: int,
                   paused: bool, pause_reason: str) -> str:
        # Open ts of the last COMPLETED 4h bar on the exchange epoch grid.
        expected_bar = (now // BAR_S - 1) * BAR_S
        if self._last_seen.get(base, -1) >= expected_bar:
            return "noop"  # this boundary already evaluated — cheap no-op

        venue, candles = self._closed_candles(symbol, now)
        if venue is None or len(candles) < WINDOW_BARS + 1:
            # insufficient history won't change within this bar
            self._mark_seen(base, expected_bar)
            return "no_data"
        latest_ts = int(candles[-1][0]) // 1000
        if self._last_seen.get(base, -1) >= latest_ts:
            return "noop"  # venue hasn't served a NEW closed bar yet — retry

        sig, edge = breakout_signal_last(candles, WINDOW_BARS)
        if sig == 0 or edge is None or edge <= 0:
            self._mark_seen(base, latest_ts)
            return "no_signal"
        atrv = wilder_atr_last(candles, ATR_LEN)
        if atrv is None or atrv <= 0:
            self._mark_seen(base, latest_ts)
            return "no_atr"

        lane_open = self._lane_open()
        # rail 4: ONE position per BASE across venues — this bar's signal is
        # forgone while the base is held (re-entry rule handles the rest).
        if any(p.symbol.split("/")[0].split(":")[0] == base for p in lane_open):
            self._mark_seen(base, latest_ts)
            return "occupied"
        # rail 6: re-entry earliest one bar AFTER the exit bar (probe rule:
        # a signal bar at or before the exit bar never re-enters).
        exit_bar = self._last_exit_bar(base)
        if exit_bar is not None and latest_ts <= exit_bar:
            self._mark_seen(base, latest_ts)
            return "reentry_wait"

        # Transient gates below deliberately do NOT mark the bar as seen: a
        # pause may lift / a slot may free while the signal bar is still the
        # latest closed bar. Once a NEWER bar closes, this signal expires.
        if paused:
            logger.info(f"[DeepBreakout] {base}: entry paused — {pause_reason}")
            return "paused"
        if len(lane_open) >= int(self._cfg.get("max_concurrent", 4)):
            return "lane_full"
        try:
            equity = float(self._equity() or 0.0)
        except Exception:
            equity = 0.0
        if equity <= 0:
            return "no_equity"

        close = float(candles[-1][4])
        risk_distance = STOP_ATR * atrv
        # rail 3: researched sizing — 1% equity risk / risk distance,
        # capped at 2x equity notional (within the 2.5x charter clamp) ...
        units = equity * float(self._cfg.get("risk_pct", 1.0)) / 100.0 / risk_distance
        units = min(units, equity * float(self._cfg.get("max_notional_multiple", 2.0)) / close)
        notional = units * close
        # ... then clamped by the lane's own gross-exposure budget (6% of
        # equity — keeps the band lane's room under the shared 12% cap)
        lane_gross = 0.0
        for p in lane_open:
            try:
                lane_gross += abs(float(p.size or 0) * float(p.entry_price or 0))
            except (TypeError, ValueError):
                continue
        headroom = equity * float(self._cfg.get("lane_exposure_cap_pct", 6.0)) / 100.0 - lane_gross
        if headroom <= 0:
            return "lane_exposure_cap"
        notional = min(notional, headroom)

        # rail 3/5: existing risk rails apply ON TOP — correlation sizing ...
        try:
            corr = self._risk.check_correlation(
                symbol, self._tracker.get_open(), equity) or {}
        except Exception as e:
            logger.debug(f"[DeepBreakout] correlation check skipped: {e}")
            corr = {"can_add": True, "size_multiplier": 1.0}
        if not corr.get("can_add", True):
            logger.info(f"[DeepBreakout] {base}: correlation cap — skipped")
            return "correlation_cap"
        notional *= float(corr.get("size_multiplier", 1.0) or 1.0)

        # ... and the charter §2 12% portfolio gross-notional circuit breaker
        # (fail-closed inside exposure_breached on a bad equity reading).
        import config as _c
        from core.risk_manager import exposure_breached

        if exposure_breached(self._tracker.get_open(), notional, equity,
                             float(getattr(_c, "MAX_PORTFOLIO_EXPOSURE_PCT", 12.0))):
            logger.info(f"[DeepBreakout] {base}: §2 portfolio exposure cap — skipped")
            return "portfolio_exposure_cap"

        units = notional / close
        # SL/TP anchored to the signal-bar close — the Codex reference
        # geometry (bot/strategies/deep_breakout.py: stop/target off `close`).
        sl_px = close - sig * risk_distance
        tp_px = close + sig * risk_distance * REWARD_RISK
        if units <= 0 or sl_px <= 0 or tp_px <= 0:
            self._mark_seen(base, latest_ts)
            return "bad_geometry"
        side = "buy" if sig > 0 else "sell"
        exchange = self._exchanges.get(venue)
        if exchange is None:
            return "no_exchange"

        # One attempt per bar — win or lose, this bar is spent. The order
        # path's own gates (risk.can_trade incl. daily-loss breaker, min
        # notional, blacklist, position caps) run inside open_position.
        self._mark_seen(base, latest_ts)
        pos = self._om.open_position(
            exchange, symbol, side, "futures",
            strategy=STRATEGY_FAMILY, size=units, sl=sl_px, tp=tp_px,
            leverage=1, order_type="market", model_version=MODEL_VERSION,
        )
        if pos is None:
            reject = getattr(self._om, "last_open_reject", None)
            logger.info(
                f"[DeepBreakout] {venue}:{symbol} {side} rejected by order path: {reject}")
            return "rejected"
        logger.info(
            f"[DeepBreakout] OPEN {venue}:{symbol} {side} units={units:.6g} "
            f"sl={sl_px:.6g} tp={tp_px:.6g} notional=${notional:.2f} "
            f"(signal bar {latest_ts})")
        return "entered"

    # ── data ──────────────────────────────────────────────────────────────
    def _closed_candles(self, symbol: str, now: int) -> tuple:
        """(venue, CLOSED 4h bars oldest-first) from the first configured
        venue serving data — binance-first (rail 4). The forming bar is
        dropped so a signal never repaints (rail 6)."""
        need = WINDOW_BARS + ATR_LEN + 8
        since_ms = (now - need * BAR_S) * 1000
        for venue in self._cfg.get("venues", ("binance", "bybit")):
            if venue not in self._exchanges:
                continue
            try:
                raw = self._ohlcv(venue, symbol, TIMEFRAME, since_ms) or []
            except Exception as e:
                logger.debug(f"[DeepBreakout] ohlcv {venue} {symbol} failed: {e}")
                continue
            out = []
            for c in raw:
                try:
                    bar_ts = int(c[0]) // 1000
                    px = float(c[4])
                except (IndexError, TypeError, ValueError):
                    continue
                if bar_ts + BAR_S <= now and px > 0:
                    out.append(c)
            if out:
                out.sort(key=lambda c: c[0])
                return venue, out
        return None, []

    def _lane_open(self) -> list:
        try:
            return [p for p in self._tracker.get_open() if is_deep_breakout_position(p)]
        except Exception:
            return []

    def _last_exit_bar(self, base: str):
        """Open ts (s) of the 4h bar containing the lane's most recent exit on
        ``base`` — from the append-only warehouse trades table, so the
        re-entry rule survives restarts. None when unknown (fail-open: the
        tracker occupancy check above still guards overlap)."""
        if self._wh is None:
            return None
        try:
            rows = self._wh.query(
                "SELECT MAX(ts_exit) AS m FROM trades "
                "WHERE strategy_family=? AND ts_exit IS NOT NULL AND symbol LIKE ?",
                (STRATEGY_FAMILY, f"{base}/%"),
            )
            m = rows[0]["m"] if rows else None
            if m is None:
                return None
            return int(float(m)) // BAR_S * BAR_S
        except Exception as e:
            logger.debug(f"[DeepBreakout] last-exit query failed: {e}")
            return None

    # ── max-hold: the lane-owned time exit (126 bars = 504h) ─────────────
    def _close_expired(self, now: int) -> int:
        """Close lane positions held >= MAX_HOLD_SEC through the NORMAL close
        path (order_manager.close_position) so every on_close hook fires."""
        closed = 0
        for p in self._lane_open():
            try:
                opened = float(getattr(p, "open_time", 0.0) or 0.0)
                if opened <= 0 or now - opened < MAX_HOLD_SEC:
                    continue
                exchange = self._exchanges.get(str(p.exchange or "").lower())
                if exchange is None:
                    logger.warning(
                        f"[DeepBreakout] max-hold: no client for {p.exchange} "
                        f"({p.symbol}) — will retry")
                    continue
                logger.info(
                    f"[DeepBreakout] MAX-HOLD close {p.symbol} {p.side} "
                    f"(held {(now - opened) / 3600:.0f}h >= {MAX_HOLD_SEC // 3600}h)")
                self._om.close_position(exchange, p, "max_hold")
                closed += 1
            except Exception as e:
                logger.warning(
                    f"[DeepBreakout] max-hold close failed for "
                    f"{getattr(p, 'symbol', '?')}: {e}")
        return closed

    # ── per-bar bookkeeping (persisted across restarts) ──────────────────
    def _mark_seen(self, base: str, bar_ts: int) -> None:
        if self._last_seen.get(base, -1) >= int(bar_ts):
            return
        self._last_seen[base] = int(bar_ts)
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            self._state_path.write_text(
                json.dumps({"last_seen_bar": self._last_seen}, indent=1),
                encoding="utf-8",
            )
        except OSError as e:
            logger.debug(f"[DeepBreakout] state persist failed: {e}")

    def _load_state(self) -> dict:
        try:
            raw = json.loads(self._state_path.read_text(encoding="utf-8"))
            seen = raw.get("last_seen_bar") or {}
            return {str(k): int(v) for k, v in seen.items()}
        except (OSError, ValueError, TypeError):
            return {}
