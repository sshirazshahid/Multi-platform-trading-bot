"""
core/engine/entry_exec.py — BotEngine _EntryExecMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403
from core.engine.helpers import (
    _deployable_total,
    _is_mcp_directional_paper_futures,
    _live_entry_clock_drift_rejection,
    accband_research_open_budget_allows,
)


class _EntryExecMixin:
    def _execute_open(self, action: dict) -> bool:
        """Validate and execute an OPEN action from Claude. Returns True if executed."""
        from config import (
            BLACKLIST_HARD,
            CLOCK_DRIFT_ALERT_MS,
            CONTROLLED_LIVE_ENABLED,
            OPERATING_MODE,
            SHORTS_REQUIRE_BTC_BEAR,
            TRADING_MODE,
            UNIVERSE_WHITELIST,
            is_analysis_only,
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

        # Cheap, symbol-local gates stay first so repeated proposals are
        # rejected before venue checks, sizing, scoring, or other I/O.
        try:
            import time as _t_p23

            _cool_until = self._dust_skip_cooldown.get(symbol, 0.0)
            if _cool_until > 0:
                if _t_p23.time() < _cool_until:
                    action["reject_reason"] = "symbol_cooldown_active"
                    return False
                del self._dust_skip_cooldown[symbol]
        except Exception:
            pass

        if is_analysis_only(symbol):
            logger.info(
                f"[AnalysisOnly] BLOCKED open {ex_name}:{symbol} {side} - "
                "analysis/data-collection only (unscreened; no live entry)"
            )
            action["reject_reason"] = "analysis_only_symbol"
            return False

        _clock_rejection = _live_entry_clock_drift_rejection(
            OPERATING_MODE,
            ex_name,
            getattr(self, "_clock_drift_ms", None),
            CLOCK_DRIFT_ALERT_MS,
        )
        if _clock_rejection:
            logger.warning(
                f"[ClockDrift] BLOCKED live entry {ex_name}:{symbol}: "
                f"{_clock_rejection}"
            )
            action["reject_reason"] = _clock_rejection
            return False

        # Entries-only kill switch (Codex port, 2026-07-12): data/KILL_SWITCH
        # present -> refuse this NEW entry before any sizing/order work.
        # Monitoring, SL/TP management, maker-resolver, reconciliation and
        # shadow probes keep running (deliberately NOT Codex's skip-whole-cycle
        # semantics). State-change logging lives in core.kill_switch; removing
        # the file resumes entries without a restart.
        from core.kill_switch import entries_halted
        if entries_halted():
            action["reject_reason"] = "kill_switch_active"
            return False

        # Blueprint Phase 1: soft-stale latch blocks NEW entries only
        # (forward-feed / API soft-stale). Opens keep running under SL/TP.
        try:
            from core.soft_stale_latch import soft_stale_entries_blocked

            if soft_stale_entries_blocked():
                action["reject_reason"] = "soft_stale_entry_block"
                return False
        except Exception as _sse:
            logger.warning(f"[SoftStale] entry check failed closed: {_sse}")
            action["reject_reason"] = "soft_stale_entry_block"
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

        # Deterministic new-exposure latch. This is separate from signal
        # generation so SHADOW_ONLY still records candidates while no order can
        # pass this boundary. The OrderManager repeats the check as defense in
        # depth for future plugins using the shared production instance.
        from config import SIGNAL_SOURCE
        from core.entry_policy import authorize_runtime_entry, strategy_id_for_action

        _strategy_id = strategy_id_for_action(action, SIGNAL_SOURCE)
        _authorization = authorize_runtime_entry(
            _strategy_id,
            strategy_version=action.get("strategy_version") or action.get("model_version"),
        )
        action["entry_policy"] = _authorization.policy
        action["entry_policy_reason"] = _authorization.reason
        action["strategy_id"] = _strategy_id
        if not _authorization.allowed:
            logger.info(
                f"[EntryPolicy] BLOCKED {ex_name}:{symbol} {_strategy_id}: "
                f"{_authorization.reason}"
            )
            action["reject_reason"] = _authorization.reason
            return False

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

        # ── SMART-MONEY HARD ENTRY (2026-07-24 Approach 1) ─────────────
        # PAPER+MAX_FLOW_BAND only (config-gated). Fail-open on stale feed.
        try:
            from config import SMART_MONEY_ENTRY_GATE as _sm_gate
            if _sm_gate.get("enabled"):
                _base = symbol.split("/")[0].replace(":USDT", "") if symbol else ""
                _sm = None
                try:
                    from core.data_coordinator import get_coordinator
                    _coord = get_coordinator()
                    if _coord is not None:
                        _sm = _coord.get_market_context(_base).smart_money
                except Exception:
                    _sm = None
                _sm_reason = smart_money_entry_rejection(
                    side,
                    _sm,
                    enabled=True,
                    fail_open_stale=bool(_sm_gate.get("fail_open_stale", True)),
                )
                if _sm_reason:
                    logger.info(
                        f"[SmartMoney] BLOCKED {ex_name}:{symbol} {side} — {_sm_reason}"
                    )
                    action["reject_reason"] = _sm_reason
                    return False
        except Exception as _sm_exc:
            logger.debug(f"[SmartMoney] gate skipped on error: {_sm_exc}")

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
                _ssf_d = _ssf_eval(
                    side="sell", symbol=symbol,
                    btc_4h_uptrend=_btc4, btc_1h_uptrend=_btc1,
                    symbol_news_sentiment=None,
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
                _bvp = self._get_btc_vol_pause()
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
            from config import ACCURACY_TARGET_MODE as _acc_cfg
            from core.mcp_brain import _apply_accuracy_target
            _tp_before_acc = tp_pct
            tp_pct = _apply_accuracy_target(sl_pct, tp_pct, side=side)
            # Band lane = inverted geometry after apply (tp < sl), NOT merely
            # "TP value changed". Entries that already carried a compressed TP
            # used to skip the stamp + regime filter (open ETH 2026-07-28 had
            # tp%0.50/sl%0.80 with _accuracy_band=False).
            _acc_mode_on = (
                bool(_acc_cfg.get("enabled"))
                and sl_pct > 0 and tp_pct > 0 and tp_pct < sl_pct
            )
            if _acc_mode_on and tp_pct != _tp_before_acc:
                logger.info(
                    f"[Claude] {symbol} ACCURACY band: TP {_tp_before_acc:.2f}%"
                    f"→{tp_pct:.2f}% (SL={sl_pct:.2f}%, target WR 60-65%)")
            if _acc_mode_on:
                # ── BAND REGIME FILTER (2026-07-12) — band-lane-ONLY veto ────
                # Inside the _acc_mode_on carve-out by design: the evidence
                # (screen 13_band_conditional) was measured on band outcomes,
                # so mcp_brain scoring, the deep_breakout lane, and shadow
                # probes are untouched. WR-band protection + bleed reduction,
                # NOT edge (all screen buckets stay after-cost negative).
                _brf_reason = self._band_regime_veto(action)
                if _brf_reason:
                    logger.info(
                        f"[BandRegime] BLOCKED {symbol} — {_brf_reason} "
                        f"(band-lane toxic regime; screen 13_band_conditional)")
                    action["reject_reason"] = _brf_reason
                    return False

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
                detail = ", ".join(uf_result["reasons"]) or "unspecified"
                logger.info(
                    f"[Claude] BLOCKED by universe filter: {symbol} — {detail}")
                # Keep stable family prefix for funnel grouping; append detail
                # so Mission Control / drought diagnosis can see chop vs spread.
                action["reject_reason"] = f"universe_filter_blocked:{detail}"
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
                from config import STRESSED_EXIT_COST_FRAC as _EXIT_STRESS
                from core.vol_target import risk_budget_margin as _rbm
                _lev_eff = leverage if market_type == "futures" else 1
                _budget = _rbm(mtype_bal, sl_pct, _lev_eff,
                               float(_VTS.get("per_trade_risk_pct", 0.0025)),
                               _EXIT_STRESS)
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
            _risk_positions = list(self.tracker.get_open() or [])
            if DRY_RUN:
                _risk_positions.extend(
                    self.order_mgr.pending_maker_reservations()
                )
            if _exp_breached(_risk_positions, size * price, _equity, _MAX_EXP):
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

        # Aggregate stop-risk cap: gross exposure alone does not bound loss
        # when stops differ. Include every futures stop plus a stressed exit
        # cost and deny when the total exceeds the typed mode profile budget.
        try:
            from config import (
                MAX_AGGREGATE_OPEN_RISK_PCT as _MAX_OPEN_RISK,
            )
            from config import (
                STRESSED_EXIT_COST_FRAC as _EXIT_STRESS,
            )
            from core.risk_manager import aggregate_open_risk_breached

            if aggregate_open_risk_breached(
                _risk_positions,
                size * price,
                sl_pct / 100.0,
                _equity,
                _MAX_OPEN_RISK,
                _EXIT_STRESS,
            ):
                logger.warning(
                    f"[Risk] aggregate open risk cap reached for {symbol}: "
                    f"budget={_MAX_OPEN_RISK:.2%} of equity"
                )
                action["reject_reason"] = "aggregate_open_risk_cap"
                return False
        except Exception as _are:
            logger.warning(
                f"[Risk] aggregate open-risk check FAILED, blocking entry: {_are}"
            )
            action["reject_reason"] = "aggregate_open_risk_error"
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
                    notional, _pre_ev_notional, mtype_bal, sl_pct,
                    risk_positions=_risk_positions)
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

        # Quantize before evaluating depth so the guard walks the exact amount
        # that the common paper/live create_order boundary will submit.
        try:
            size = float(exchange.round_quantity(
                trade_symbol, size, market_type=market_type))
        except Exception as _quantize_error:
            logger.warning(
                f"[ExecutionGuard] {ex_name}:{trade_symbol} quantity "
                f"quantization failed: {_quantize_error}"
            )
            action["reject_reason"] = "quantity_quantization_failed"
            return False
        if size <= 0:
            action["reject_reason"] = "quantity_quantized_to_zero"
            return False

        # A target venue's own book is the only valid execution snapshot.
        # Generic market features may rank a candidate, but they cannot justify
        # a fill on another venue. Walk the final size and retain expected costs
        # for the append-only decision record.
        try:
            from config import (
                EXECUTION_BOOK_DEPTH_LEVELS as _BOOK_LEVELS,
            )
            from config import (
                EXECUTION_BOOK_MAX_AGE_SEC as _BOOK_MAX_AGE,
            )
            from config import (
                MAX_ENTRY_SLIPPAGE_BPS as _MAX_ENTRY_SLIP,
            )
            from core.cost_model import fee_rate as _entry_fee_rate
            from core.execution_guard import fetch_and_validate_execution_book

            _book_decision = fetch_and_validate_execution_book(
                exchange,
                venue=ex_name,
                market_type=market_type,
                canonical_symbol=trade_symbol,
                exchange_symbol=trade_symbol,
                side=side,
                requested_quantity=size,
                max_slippage_bps=_MAX_ENTRY_SLIP,
                max_age_seconds=_BOOK_MAX_AGE,
                limit=_BOOK_LEVELS,
                realtime_provider=self.realtime_streams,
            )
            action["execution_snapshot"] = _book_decision.to_action_dict()
            # Decimal-safe: quote_cost/vwap/mid/filled_quantity arrive as
            # Decimal from the quantization retrofit; fee rate is float.
            # Mixing them raised TypeError and failed every entry closed
            # (first hit: AXS 2026-07-18 04:45:41).
            action["expected_entry_fee_usdt"] = float(
                _book_decision.quote_cost
            ) * _entry_fee_rate(ex_name, market_type, "taker")
            action["expected_entry_slippage_usdt"] = float(
                abs(_book_decision.vwap - _book_decision.mid)
            ) * float(_book_decision.filled_quantity)
            if not _book_decision.allowed:
                logger.info(
                    f"[ExecutionGuard] BLOCKED {ex_name}:{trade_symbol} {side}: "
                    f"{_book_decision.reason}"
                )
                action["reject_reason"] = _book_decision.reason
                return False
        except Exception as _book_error:
            logger.warning(
                f"[ExecutionGuard] {ex_name}:{trade_symbol} failed closed: "
                f"{_book_error}"
            )
            action["reject_reason"] = "execution_book_guard_error"
            return False

        # AccBand / MCP directional research tuition cap (PAPER+MAX_FLOW_BAND).
        # Caps NEW opens per UTC day; does not touch F1, tsmom, or live.
        if _is_mcp_directional_paper_futures(
            _strategy_id,
            market_type,
            OPERATING_MODE,
            is_tsmom=_is_tsmom_entry,
        ):
            try:
                from config import ACCBAND_RESEARCH_MAX_OPENS_UTC_DAY as _acc_cap
            except Exception:
                _acc_cap = None
            _allowed, _opens_today = accband_research_open_budget_allows(
                self.tracker, _acc_cap
            )
            action["accband_research_opens_utc_day"] = _opens_today
            action["accband_research_max_opens_utc_day"] = _acc_cap
            if not _allowed:
                logger.info(
                    f"[AccBandBudget] BLOCKED {ex_name}:{symbol} — "
                    f"{_opens_today}/{_acc_cap} MCP directional opens UTC day"
                )
                action["reject_reason"] = "accband_research_daily_open_budget"
                return False

        # P0 economic execution gate. The scorer/candidate/shadow pipeline has
        # already run; this check affects only the order-producing boundary.
        # It deliberately executes after the FINAL SL/TP geometry, quantity
        # quantization, and venue-book walk so probability and friction use the
        # exact bracket and entry snapshot that would be submitted. Catalog
        # scoping leaves tsmom, carry, deep-breakout, spot, and live lanes
        # unchanged.
        if not self._apply_mcp_directional_economic_gate(
            action,
            strategy_id=_strategy_id,
            operating_mode=OPERATING_MODE,
            market_type=market_type,
            exchange_name=ex_name,
            sl_pct=sl_pct,
            tp_pct=tp_pct,
            entry_quote_usdt=float(_book_decision.quote_cost),
            is_tsmom=_is_tsmom_entry,
        ):
            action["reject_reason"] = str(
                action.get("economic_gate_reason") or "economic_gate_rejected"
            )
            return False

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
                execution_snapshot=action.get("execution_snapshot"),
                authorization_strategy_id=action.get("strategy_id"),
            )
            if pos is None:
                logger.info("[Claude] open_position returned None — rejected by order manager")
                _omr = getattr(self.order_mgr, "last_open_reject", None)
                action["reject_reason"] = f"order_manager:{_omr or 'unspecified'}"
                return False
            # ACCURACY band marker (2026-07-10 time-exit leak fix): stamp
            # inverted-geometry entries so the position monitor suppresses
            # STALE/AGE_LIMIT/scalp time exits + partial-TP inside
            # ACCURACY_TARGET_MODE["max_hold_hours"] and first-touch SL/TP
            # governs. Declared Position field (2026-07-25) persists via
            # asdict; geometry fallback in _is_accuracy_band_position covers
            # legacy rows that predate the stamp.
            if _acc_mode_on:
                pos._accuracy_band = True
                try:
                    with self.tracker._lock:
                        self.tracker._save()
                except Exception:
                    pass
            action["exchange_symbol"] = trade_symbol
            action["filled_quantity"] = float(pos.size)
            action["filled_price"] = float(pos.entry_price)
            if not self._log_terminal_decision(
                action,
                outcome="filled",
                reason="position_opened",
                stage="order_manager",
            ):
                # The fill already exists, so keep managing it and latch a
                # manual-clear incident rather than hiding the position.
                try:
                    self.risk.latch_incident(
                        "filled entry missing terminal provenance",
                        category="execution",
                    )
                except Exception:
                    pass
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

