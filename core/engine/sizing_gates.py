"""
core/engine/sizing_gates.py — BotEngine _SizingGatesMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403
from core.engine.helpers import (
    _deployable_total,
    _is_mcp_directional_paper_futures,
    _tier_blocked_by_cap,
)


class _SizingGatesMixin:
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
                            mtype_bal: float, sl_pct: float,
                            risk_positions=None) -> float:
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
          (c) both portfolio-exposure and aggregate stop-risk caps pass at
              the floored size, including pending maker reservations
              (fail-CLOSED on error, same as the main rails).
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
        # (c) Both portfolio caps at the floored gross notional — fail-CLOSED.
        try:
            from config import (
                MAX_AGGREGATE_OPEN_RISK_PCT as _MAX_OPEN_RISK,
                MAX_PORTFOLIO_EXPOSURE_PCT as _MAX_EXP,
                STRESSED_EXIT_COST_FRAC as _EXIT_STRESS,
            )
            from core.risk_manager import (
                aggregate_open_risk_breached as _aggregate_breached,
                exposure_breached as _exp_breached,
            )
            _equity = _deployable_total(self._balances)
            _positions = (
                list(risk_positions)
                if risk_positions is not None
                else list(self.tracker.get_open() or [])
            )
            _floor_gross = step * price
            if _exp_breached(_positions, _floor_gross, _equity, _MAX_EXP):
                return 0.0
            if _aggregate_breached(
                _positions,
                _floor_gross,
                sl_pct / 100.0,
                _equity,
                _MAX_OPEN_RISK,
                _EXIT_STRESS,
            ):
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
            from core.decision_provenance import outcome_for_rejection

            self._log_terminal_decision(
                action,
                outcome=outcome_for_rejection(reason, stage),
                reason=reason,
                stage=stage,
            )
        except Exception as e:
            logger.error(f"[Provenance] terminal rejection write failed: {e}")
        try:
            brain = getattr(self, "mcp_brain", None)
            if brain is not None and hasattr(brain, "log_rejection"):
                brain.log_rejection(
                    action.get("decision_id"),
                    action.get("symbol", ""),
                    reason,
                    stage,
                )
        except Exception as e:
            logger.debug(f"[Provenance] rejection log skipped: {e}")

    def _log_terminal_decision(
        self, action: dict, *, outcome, reason: str, stage: str
    ) -> bool:
        """Write one immutable terminal outcome for a candidate."""
        try:
            from core.decision_provenance import record_terminal_decision
            from core.warehouse import get_warehouse

            warehouse = getattr(self, "warehouse", None) or get_warehouse()
            self.warehouse = warehouse
            record_terminal_decision(
                warehouse,
                action,
                outcome=outcome,
                reason=reason,
                stage=stage,
            )
            return True
        except Exception as exc:
            logger.error(
                f"[Provenance] terminal decision persistence failed for "
                f"{action.get('decision_id')}: {exc}"
            )
            return False

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

    @staticmethod
    @staticmethod
    def _validated_promoted_futures_model_version() -> str | None:
        """Return the version on the currently valid promotion pointer.

        ``MCPBrain.score_via_model`` only emits a model version after this same
        pointer validator admits the bundle. Revalidating at the final order
        boundary also catches a pointer that became stale or was replaced
        between signal generation and submission.
        """

        try:
            latest = Path("data/models/ensemble_futures_latest.json")
            payload = json.loads(latest.read_text(encoding="utf-8"))
            from core.promotion_gate import validate_model_pointer

            allowed, reason, _ = validate_model_pointer(
                payload, market_type="futures"
            )
            if not allowed:
                logger.warning(
                    f"[EconomicGate] promoted futures pointer unavailable: {reason}"
                )
                return None
            version = str(payload.get("model_version") or "").strip()
            return version or None
        except Exception as exc:
            logger.warning(
                f"[EconomicGate] promoted futures pointer validation failed: {exc}"
            )
            return None

    def _apply_mcp_directional_economic_gate(
        self,
        action: dict,
        *,
        strategy_id: str,
        operating_mode: str,
        market_type: str,
        exchange_name: str,
        sl_pct: float,
        tp_pct: float,
        entry_quote_usdt: float,
        is_tsmom: bool = False,
    ) -> bool:
        """Apply the final after-cost gate and attach its complete audit row.

        Non-owned strategy lanes return unchanged. Owned entries fail closed on
        missing inputs, promotion ambiguity, or non-positive stressed EV.
        """

        if not _is_mcp_directional_paper_futures(
            strategy_id,
            market_type,
            operating_mode,
            is_tsmom=is_tsmom,
        ):
            return True

        try:
            quote = float(entry_quote_usdt)
            entry_fee_usdt = float(action.get("expected_entry_fee_usdt"))
            entry_slippage_usdt = float(
                action.get("expected_entry_slippage_usdt")
            )
            if not math.isfinite(quote) or quote <= 0.0:
                quote = float("nan")
            entry_fee_frac = entry_fee_usdt / quote
            entry_slippage_frac = entry_slippage_usdt / quote
        except (TypeError, ValueError, ZeroDivisionError):
            quote = float("nan")
            entry_fee_frac = None
            entry_slippage_frac = None

        try:
            from config import (
                MCP_DIRECTIONAL_ECONOMIC_GATE,
                SLIPPAGE,
                STRESSED_EXIT_COST_FRAC,
            )
            from core.cost_model import fee_rate

            exit_fee_frac = fee_rate(exchange_name, "futures", "taker")
            exit_slippage_frac = float(SLIPPAGE["pct_close"])
            gate_cfg = MCP_DIRECTIONAL_ECONOMIC_GATE
            exit_cost_floor = float(STRESSED_EXIT_COST_FRAC)
        except Exception:
            exit_fee_frac = None
            exit_slippage_frac = None
            exit_cost_floor = None
            gate_cfg = {}

        from core.economic_entry_gate import evaluate_directional_entry

        # 2026-07-21 paper_fallback: config resolves the mode to "strict"
        # unless PAPER + MAX_FLOW_BAND (F3 gate); the operating_mode belt
        # below is defense-in-depth for this per-call parameter.
        gate_mode = str(gate_cfg.get("mode") or "strict").strip().lower()
        paper_fallback = (
            gate_mode == "paper_fallback"
            and str(operating_mode or "").upper() == "PAPER"
        )

        decision = evaluate_directional_entry(
            model_version=action.get("model_version"),
            promoted_model_version=self._validated_promoted_futures_model_version(),
            p_win=action.get("p_win_ensemble"),
            stop_frac=float(sl_pct) / 100.0,
            target_frac=float(tp_pct) / 100.0,
            entry_fee_frac=entry_fee_frac,
            entry_slippage_frac=entry_slippage_frac,
            exit_fee_frac=exit_fee_frac,
            exit_slippage_frac=exit_slippage_frac,
            stressed_exit_cost_floor_frac=exit_cost_floor,
            fee_stress_multiplier=gate_cfg.get("fee_stress_multiplier"),
            slippage_stress_multiplier=gate_cfg.get(
                "slippage_stress_multiplier"
            ),
            probability_margin=gate_cfg.get("probability_margin"),
            paper_fallback=paper_fallback,
        )
        audit = decision.to_dict()
        audit.update({
            "applied": True,
            "strategy_id": "MCP_DIRECTIONAL_PAPER",
            "venue": str(exchange_name or "").lower(),
            "market_type": "futures",
            "entry_quote_usdt": quote if math.isfinite(quote) else None,
            "stressed_exit_cost_floor_frac": exit_cost_floor,
            "promotion_authority": "ensemble_futures_latest.json",
        })
        action["economic_entry_gate"] = audit
        action["economic_gate_reason"] = decision.reason
        action["economic_gate_required_p_win"] = decision.required_p_win
        action["economic_gate_stressed_expectancy_frac"] = (
            decision.stressed_expectancy_frac
        )

        # Persist the audit alongside the exact venue book snapshot used for
        # this terminal decision. Both rejected and filled paths retain it.
        try:
            execution = action.get("execution_snapshot")
            snapshot = dict(execution.get("snapshot") or {})
            context = dict(snapshot.get("context") or {})
            context["economic_entry_gate"] = audit
            snapshot["context"] = context
            execution["snapshot"] = snapshot
        except (AttributeError, TypeError, ValueError):
            # The execution-book guard already requires a valid snapshot in
            # production; audit attachment failure must not hide the decision.
            pass

        if decision.allowed:
            if decision.reason == "economic_gate_paper_fallback_pass":
                logger.info(
                    "[EconomicGate] paper_fallback admission (no promoted "
                    f"model): {action.get('symbol')} "
                    f"breakeven_wr={decision.breakeven_p_win:.3f} stressed"
                )
                return True
            logger.info(
                f"[EconomicGate] PASS {exchange_name}:{action.get('symbol')} "
                f"p={decision.p_win:.3f} required={decision.required_p_win:.3f} "
                f"EV={decision.stressed_expectancy_frac * 10_000:+.2f}bps "
                f"cost={decision.stressed_round_trip_cost_frac * 10_000:.2f}bps "
                f"model={decision.model_version}"
            )
            return True

        logger.info(
            f"[EconomicGate] BLOCKED {exchange_name}:{action.get('symbol')} "
            f"reason={decision.reason} p={decision.p_win} "
            f"required={decision.required_p_win} "
            f"EV={decision.stressed_expectancy_frac} "
            f"model={decision.model_version} promoted={decision.promoted_model_version}"
        )
        return False

