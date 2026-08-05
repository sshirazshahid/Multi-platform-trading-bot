"""
core/engine/gate_health.py — BotEngine _GateHealthMixin mixin (Phase D5).
"""

from core.engine.helpers import *  # noqa: F403


class _GateHealthMixin:
    def _band_regime_veto(self, action: dict) -> str:
        """Band-lane-ONLY toxic-regime entry veto (2026-07-12). Returns a
        reject_reason string to veto the entry, or "" to allow.

        Evidence — pre-registered screen `_workspace/strategy_pipeline/
        13_band_conditional_screen.json` (14,555 resolved band outcomes,
        12d span, Bonferroni m=16, band baseline WR 65.7%):
          * 4h ADX > 30 at entry           -> band WR 59.0% (n=5,352,
            halves 59.0/59.0, z~-10)
          * BTC 1h ATR / 30d median < 0.7  -> band WR 55.6% (n=3,203, z~-12)
        Thresholds are the screen's pre-registered bucket edges — hardcoded
        on purpose; retuning them means a NEW pre-registered screen.

        HONESTY: WR-band protection + bleed reduction — it removes the
        conditionally-WORST buckets, it is NOT edge. Every bucket in the
        screen kept a NEGATIVE after-cost expectancy; PnL-positive still
        requires the evidence lanes.

        Scope: called ONLY from _execute_open's accuracy-band carve-out
        (_acc_mode_on), so mcp_brain scoring (which serves all consumers),
        the deep_breakout lane, and shadow probes are untouched. Fail-OPEN:
        missing ADX/ratio or any error allows the entry (protective filter,
        not an availability gate); data gaps log once per distinct message.
        """
        try:
            import config as _brf_cfg
            if not getattr(_brf_cfg, "BAND_REGIME_FILTER_ENABLED", False):
                return ""
            # Veto 1 — 4h ADX > 30. mcp_brain's algo action builder threads
            # its already-computed 4h ADX onto the action payload; sources
            # that do not carry it (e.g. Claude ingestion) fail open.
            _adx = action.get("adx_4h")
            try:
                _adx = float(_adx) if _adx is not None else None
            except (TypeError, ValueError):
                _adx = None
            if _adx is None:
                self._band_regime_log_once(
                    "adx_4h missing on action -> fail-open (adx veto skipped)")
            elif _adx > 30.0:
                return "band_regime_filter:adx_4h>30"
            # Veto 2 — BTC 1h ATR ratio vs its trailing median < 0.7. Reuses
            # the BtcVolPause baseline (C.4 gate maintains it) read-only.
            _bvp = self._get_btc_vol_pause()
            _brain = getattr(self, "mcp_brain", None)
            _ic = getattr(_brain, "_indicator_cache", None) if _brain else None
            _ratio = _bvp.current_ratio(_ic)
            if _ratio is None:
                self._band_regime_log_once(
                    "BTC ATR ratio unavailable -> fail-open (vol veto skipped)")
            elif _ratio < 0.7:
                return "band_regime_filter:btc_vol<0.7"
            return ""
        except Exception as e:
            self._band_regime_log_once(f"veto errored ({e}) -> fail-open")
            return ""

    def _band_regime_log_once(self, msg: str) -> None:
        """Warn once per distinct band-regime data-gap message (fail-open
        must be visible in the log without spamming every candidate)."""
        seen = getattr(self, "_band_regime_logged", None)
        if seen is None:
            seen = set()
            self._band_regime_logged = seen
        if msg not in seen:
            seen.add(msg)
            logger.warning(f"[BandRegime] {msg}")

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
            CELL_FILTER,
            ENTRY_STALENESS_EXIT,
            EXPECTANCY_FILTER,
            MODEL_GATE,
            SPOT_PORTFOLIO,
            SPOT_STRATEGY,
            STAR_SYMBOLS,
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

        # 4b) Phase 35 (2026-05-05) — ML model freshness check.
        # The Phase 13 LR + GBM ensemble drives MODEL_GATE and the
        # LR soft size multiplier. Models trained on stale data lose
        # predictive power in fast-moving crypto markets. freqtrade's
        # FreqAI pattern is continuous retraining; we don't auto-retrain
        # but we DO surface staleness to the operator so manual refit
        # can be triggered.
        try:
            import time as _t_p35
            from pathlib import Path as _P_p35
            ensemble = _P_p35("data/models/ensemble_futures_latest.json")
            if ensemble.exists():
                age_h = (_t_p35.time() - ensemble.stat().st_mtime) / 3600
                if age_h > 168:   # > 7 days = stale
                    findings.append(
                        f"ML ensemble model is {age_h/24:.1f} days old "
                        f"(>{168/24:.0f}d threshold) — refit via "
                        f"`python scripts/train_models.py` to keep MODEL_GATE "
                        f"and LR sizing predictions current.")
                elif age_h > 48:  # 2-7 days = warning
                    findings.append(
                        f"ML ensemble model is {age_h/24:.1f} days old "
                        f"(>2d) — consider refitting via "
                        f"`python scripts/train_models.py` for fresher predictions.")
            else:
                findings.append(
                    "ML ensemble model file not found "
                    "(data/models/ensemble_futures_latest.json) — "
                    "MODEL_GATE may be operating without learned weights.")
        except Exception as _e:
            logger.debug(f"[GateHealth] model-age check skipped: {_e}")

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

        # 5b) 2026-05-21 / 2026-08-05 — orphan OPEN warehouse rows only.
        # Age alone is not a leak: tier-geometry holds keep real opens >24h.
        # Alert when warehouse status='OPEN' has no matching positions.json
        # open (trade_id close miss). Same helper as HealthWatchdog.
        try:
            from core.health_watchdog import (
                POSITIONS_PATH as _pos_path,
                STUCK_OPEN_HOURS as _stuck_h,
                WAREHOUSE_PATH as _wh_path,
                orphan_open_trade_rows as _orphan_rows,
            )
            orphans = _orphan_rows(
                _wh_path,
                older_than_hours=_stuck_h,
                positions_path=_pos_path,
                limit=200,
            )
            n_stuck = len(orphans) if orphans is not None else 0
            if n_stuck > 0:
                findings.append(
                    f"{n_stuck} orphan warehouse trade row(s) stuck at "
                    f"status='OPEN' older than {_stuck_h}h (not in "
                    f"positions.json) — learning analytics under-counted. "
                    f"Run `python scripts/backfill_warehouse_closes.py "
                    f"--commit` to repair.")
        except Exception as _se:
            logger.debug(f"[GateHealth] stuck-open check skipped: {_se}")

        # 6) Surface findings to operator
        if findings:
            logger.warning(f"[GateHealth] {len(findings)} finding(s):")
            for i, f in enumerate(findings, 1):
                logger.warning(f"  ({i}) {f}")
        else:
            logger.info("[GateHealth] all gates healthy, no findings")

