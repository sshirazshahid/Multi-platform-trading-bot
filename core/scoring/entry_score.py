"""Entry scoring: standard, scalp, and routing."""
from datetime import datetime, timezone

from loguru import logger

from core.scoring.helpers import (
    _SCALP_FALLBACK_VETO_PREFIXES,
    _apply_accuracy_target,
)

def score_coin(*, data_coordinator, coin: str, data: dict, ei: dict) -> dict:
        """
        Systematic rules-based scoring engine v3.1 (relaxed).

        2026-04-14 v3.1: v3 was too strict — 25 trades in 60 days across
        10 symbols (not enough data to build an edge). Relaxed:
        - EMA slope moved from REQUIRED to BONUS
        - ADX required 25 → 20
        - Volume 1.5x → 1.2x
        - Regime filter: ADX < 15, BB < 1.0%
        - 4 REQUIRED + 2+ BONUS (was 5 + 2)

        ═══════════════════════════════════════════════════════════════
        LONG ENTRY (side='buy'):
          REQUIRED (all 4 must be true):
            R1. 4h EMA 20 > EMA 50                    (trend direction)
            R2. 1h EMA 20 > EMA 50                    (confirmation TF)
            R3. 1h RSI in [38, 68]                     (not exhausted)
            R4. 1h ADX >= 20                           (some trend present)
          BONUS (need 2+ for entry, score >= 66):
            B1. 1h MACD histogram > 0 AND rising   +12 (momentum)
            B2. 4h EMA 20 slope > 0                +8  (trend accel)
            B3. 15m EMA cross or alignment         +10 (entry timing)
            B4. 1h vol >= 1.2x average             +8  (participation)
            B5. 1h swing HH + HL (5-bar)           +8  (structure)
            B6. Funding + orderbook combined        +5  (microstructure)
            B7. VWAP confirmation (price near VWAP)  +5  (value area)
            B8. SMC Structure (OB + BOS/ChoCH)     +10 (smart money structure)
            B9. SMC Entry (FVG + Fib + S/D + OTE)  +10 (institutional entry zone)
            B10. SMC Confirmation (sweep+vol+PA)    +8  (smart money confirmation)

        REGIME FILTER:
          4h ADX < 15 → SKIP  |  4h BB width < 1.0% → SKIP

        SL/TP:
          SL = max(1.5%, min(3%, 1.2 * ATR_1h_pct))
          TP = 2.0 * SL
        ═══════════════════════════════════════════════════════════════
        """
        ei_4h = ei.get("4h", {})
        ei_1h = ei.get("1h", {})
        ei_15m = ei.get("15m", {})
        fr = data.get("funding", {}).get(coin, {})
        ob = data.get("orderbook", {}).get(coin, {})
        _zero = {"score": 0, "side": "buy", "layers_ok": 0, "confidence": 0,
                 "sl_pct": 3.0, "tp_pct": 6.0, "reason": ""}

        if not ei_4h or not ei_1h:
            _zero["reason"] = "no_data"
            return _zero

        # ═══ REGIME FILTER — relaxed from v3 ═══════════════════════
        adx_4h = ei_4h.get("adx", 0)
        bb_width_4h = ei_4h.get("bb_width", 99)
        if adx_4h < 15:
            _zero["reason"] = f"regime_chop(4h_adx={adx_4h:.0f}<15)"
            return _zero
        # Upper bound added 2026-08-15 per frozen prereg 77
        # (_workspace/strategy_pipeline/77_prereg_adx_band_scorer_alignment.md,
        # sha256 12149d6e2efe...). This scorer selects FOR trend while the
        # band-regime veto rejects adx_4h>30 as toxic, so 65.4% of everything
        # it approved was vetoed at execution — 2,531 approvals, 0 opens, 38h
        # idle. 30 is not a new number: it is the veto's own pre-registered
        # bucket edge from screen 13_band_conditional. HONESTY: the surviving
        # 20-30 band is itself significantly NEGATIVE (mean -0.1551/trade,
        # 95% CI [-0.2232, -0.0871]) — this restores FLOW, never EDGE.
        if adx_4h > 30:
            _zero["reason"] = f"regime_toxic_trend(4h_adx={adx_4h:.0f}>30)"
            return _zero
        if bb_width_4h < 1.0:
            _zero["reason"] = f"regime_squeeze(4h_bb={bb_width_4h:.1f}%<1.0%)"
            return _zero

        # ═══ NO-TRADE CONDITIONS — extreme volatility, news candle, thin liquidity
        atr_1h_pct = ei_1h.get("atr_pct", 0)
        if atr_1h_pct > 5.0:
            _zero["reason"] = f"extreme_volatility(1h_atr={atr_1h_pct:.1f}%>5%)"
            return _zero

        avg_body = ei_1h.get("avg_body_20", 0)
        last_body = ei_1h.get("last_body", 0)
        if avg_body > 0 and last_body > 3 * avg_body:
            _zero["reason"] = f"news_candle(body={last_body:.2f}>3x_avg={avg_body:.2f})"
            return _zero

        ob_depth = (
            min(ob.get("bid_depth_usd", 0), ob.get("ask_depth_usd", 0))
            if ob else 0
        ) or (ob.get("depth_usd", 0) if ob else 0)
        if ob_depth > 0 and ob_depth < 5000:
            _zero["reason"] = f"thin_liquidity(depth=${ob_depth:.0f}<$5000)"
            return _zero

        # ── Determine side from 4h EMA 20/50 ───────────────────────
        ema20_above_50_4h = ei_4h.get("ema20_above_50", False)
        ema20_above_50_1h = ei_1h.get("ema20_above_50", False)

        # Clear direction required — if EMAs are within 0.1%, skip
        ema20_4h = ei_4h.get("ema20", 0)
        ema50_4h = ei_4h.get("ema50", 1)
        if ema50_4h > 0:
            ema_gap_pct = abs(ema20_4h - ema50_4h) / ema50_4h * 100
            if ema_gap_pct < 0.1:
                _zero["reason"] = f"4h_emas_flat(gap={ema_gap_pct:.2f}%)"
                return _zero

        side = "buy" if ema20_above_50_4h else "sell"

        # ═══ CHECK 4 REQUIRED CONDITIONS ═════════════════════════════
        req_pass = 0
        req_reasons = []

        # R1: 4h EMA gap >= 0.15% (meaningful trend separation, not just crossing)
        # Note: side is derived from ema20_above_50_4h, so checking the bool
        # alone would be tautological. The gap threshold ensures the EMA cross
        # is decisive, not noise.
        r1 = ema_gap_pct >= 0.15
        if r1:
            req_pass += 1
            req_reasons.append(f"4h_ema_gap={ema_gap_pct:.2f}%")

        # R2: 1h EMA 20 > 50 confirms direction
        r2 = ema20_above_50_1h if side == "buy" else not ema20_above_50_1h
        if r2:
            req_pass += 1
            req_reasons.append("1h_ema")

        # R3: RSI in sweet spot — slightly wider than v3
        rsi_1h = ei_1h.get("rsi", 50)
        if side == "buy":
            r3 = 38 <= rsi_1h <= 68
        else:
            r3 = 32 <= rsi_1h <= 62
        if r3:
            req_pass += 1
            req_reasons.append(f"rsi={rsi_1h:.0f}")

        # R4: 1h ADX >= 20 (some trend present)
        adx_1h = ei_1h.get("adx", 0)
        if adx_1h >= 20:
            req_pass += 1
            req_reasons.append(f"adx={adx_1h:.0f}")

        # ALL 4 required must pass
        if req_pass < 4:
            _zero["reason"] = f"req_fail({req_pass}/4: {', '.join(req_reasons)})"
            return _zero

        # ═══ COUNT BONUS CONDITIONS ═══════════════════════════════════
        score = 50  # Base score for passing all 4 required
        bonus_count = 0
        bonus_reasons = []

        # B1: MACD — REMOVED (v4: anti-predictive, 25% WR when active)
        macd_hist = ei_1h.get("macd_hist", 0)
        macd_rising = ei_1h.get("macd_hist_rising", False)
        if side == "buy":
            b1 = macd_hist > 0 and macd_rising
        else:
            b1 = macd_hist < 0 and not macd_rising
        if b1:
            # v4: score += 0 (was +12), bonus_count NOT incremented
            bonus_reasons.append(f"macd_logged={'+'if macd_hist>0 else '-'}{abs(macd_hist):.4f}")

        # B2: 4h EMA 20 slope (was REQUIRED in v3, now BONUS — v4: reweighted +8→+3)
        ema20_slope_4h = ei_4h.get("ema20_slope", 0)
        if side == "buy" and ema20_slope_4h > 0:
            bonus_count += 1
            score += 3
            bonus_reasons.append("4h_slope_up")
        elif side == "sell" and ema20_slope_4h < 0:
            bonus_count += 1
            score += 3
            bonus_reasons.append("4h_slope_dn")

        # B3: 15m timing — REMOVED (v4: zero WR delta, pure noise)
        if ei_15m:
            if side == "buy":
                b3 = ei_15m.get("ema20_above_50", False) or ei_15m.get("ema_cross_up", False)
            else:
                b3 = not ei_15m.get("ema20_above_50", True) or ei_15m.get("ema_cross_down", False)
            if b3:
                # v4: score += 0 (was +10), bonus_count NOT incremented
                bonus_reasons.append("15m_timing_logged")

        # B4: 1h volume >= 1.2x average (participation)
        vol_1h = ei_1h.get("vol_ratio", 0)
        if vol_1h >= 1.2:
            bonus_count += 1
            score += 8
            bonus_reasons.append(f"vol={vol_1h:.1f}x")

        # B5: Market structure — 5-bar swing HH+HL (longs) or LL+LH (shorts)
        if side == "buy":
            b5 = ei_1h.get("hh", False) and ei_1h.get("hl", False)
        else:
            b5 = ei_1h.get("ll", False) and ei_1h.get("lh", False)
        if b5:
            bonus_count += 1
            score += 8
            bonus_reasons.append("structure")

        # B6: Funding + orderbook combined (microstructure edge)
        # 2026-05-27: Enhanced version uses DataCoordinator orderbook feed
        # when available (imbalance momentum + depth ratio). Falls back to
        # legacy Binance depth fetch otherwise.
        funding_rate = fr.get("funding_rate", 0)
        imb = ob.get("imbalance", 0)
        micro_ok = False
        _b6_pts = 7  # legacy points (v4: reweighted +5→+7)
        _b6_label = f"micro(f={funding_rate*100:+.3f}%,ob={imb:+.2f})"

        # B6 master gate (2026-05-30): orderbook imbalance is unscreenable and
        # the funding leg screened NO_EDGE — bonus disabled by default. When
        # b6_orderbook_enabled is False, NEITHER the enhanced nor the legacy
        # path may set micro_ok, so no B6 points are awarded.
        _b6_enabled = True
        try:
            from config import DATA_FEEDS as _df_cfg_b6_gate
            _b6_enabled = bool(_df_cfg_b6_gate.get("b6_orderbook_enabled", True))
        except (ImportError, AttributeError):
            pass

        # Try enhanced orderbook feed first
        _enhanced_b6_used = False
        try:
            _df_cfg_b6 = {}
            try:
                from config import DATA_FEEDS as _df_cfg_b6
            except (ImportError, AttributeError):
                pass
            if (_b6_enabled
                    and _df_cfg_b6.get("enhanced_b6_enabled", True)
                    and data_coordinator is not None):
                _ob_ctx = data_coordinator.get_market_context(
                    coin).orderbook
                if _ob_ctx and not _ob_ctx.get("stale", True):
                    _enh_imb = _ob_ctx.get("imbalance", 0)
                    _imb_mom = _ob_ctx.get("imbalance_momentum", 0)
                    _depth_log = _ob_ctx.get("depth_ratio_log", 0)
                    if side == "buy":
                        # Buy: want bid-heavy book + positive imbalance momentum
                        micro_ok = (funding_rate <= 0.0003
                                    and _enh_imb > 0.05
                                    and (_imb_mom > 0 or _depth_log > 0.1))
                    elif side == "sell":
                        micro_ok = (funding_rate >= -0.0003
                                    and _enh_imb < -0.05
                                    and (_imb_mom < 0 or _depth_log < -0.1))
                    if micro_ok:
                        _b6_pts = int(_df_cfg_b6.get("enhanced_b6_points", 7))
                        _b6_label = (
                            f"micro+(imb={_enh_imb:+.3f},"
                            f"mom={_imb_mom:+.3f},"
                            f"f={funding_rate*100:+.3f}%)")
                    _enhanced_b6_used = True
        except Exception:
            pass

        # Legacy fallback (also gated by the B6 master switch)
        if _b6_enabled and not _enhanced_b6_used and not micro_ok:
            if side == "buy" and funding_rate <= 0.0003 and imb > 0.05:
                micro_ok = True
            elif side == "sell" and funding_rate >= -0.0003 and imb < -0.05:
                micro_ok = True

        if micro_ok:
            bonus_count += 1
            score += _b6_pts
            bonus_reasons.append(_b6_label)

        # B7: VWAP confirmation — price above VWAP for longs, below for shorts
        # v4 reweighted +5→+15 ("strongest scalp signal"); 2026-06-06 (audit + owner): reverted to
        # +5 — the +15 was unvalidated (no OOS evidence) and over-weighted one feature on a NO_EDGE
        # tape, driving more entries / fee bleed without edge.
        pvw = ei_1h.get("price_vs_vwap", 0)
        if side == "buy" and 0.05 < pvw < 2.0:
            bonus_count += 1
            score += 5
            bonus_reasons.append(f"vwap=+{pvw:.2f}%")
        elif side == "sell" and -2.0 < pvw < -0.05:
            bonus_count += 1
            score += 5
            bonus_reasons.append(f"vwap={pvw:.2f}%")

        # ═══ SMART MONEY / ICT BONUS CONDITIONS (2026-04-16) ═══════
        # Three composite scores from utils/smart_money.py, computed from
        # the same OHLCV data (no extra API calls). Each score is 0-100.
        # Uses 1h TF as primary, with 4h for higher-TF structure confirmation.

        smc_struct_1h  = ei_1h.get("smc_structure", 0)
        smc_entry_1h   = ei_1h.get("smc_entry", 0)
        smc_conf_1h    = ei_1h.get("smc_confirmation", 0)
        smc_struct_4h  = ei_4h.get("smc_structure", 0)

        # Smart money detail for side-alignment checks
        smc_d = ei_1h.get("smc_detail", {})
        ms_data = smc_d.get("ms", {})
        sweep_data = smc_d.get("sweep", {})
        pa_data = smc_d.get("pa", {})

        # B8: SMC Structure — order blocks + BOS/ChoCH (10 pts)
        # Requires alignment: bullish structure for longs, bearish for shorts.
        b8 = False
        if smc_struct_1h >= 40:
            ms_trend = ms_data.get("trend", "ranging")
            if side == "buy" and ms_trend == "bullish":
                b8 = True
            elif side == "sell" and ms_trend == "bearish":
                b8 = True
            # 4h fallback: must also check side alignment via 4h market structure
            if not b8 and smc_struct_4h >= 50:
                ms_4h = ei_4h.get("smc_detail", {}).get("ms", {})
                ms_trend_4h = ms_4h.get("trend", "ranging")
                if side == "buy" and ms_trend_4h == "bullish":
                    b8 = True
                elif side == "sell" and ms_trend_4h == "bearish":
                    b8 = True
        if b8:
            bonus_count += 1
            score += 10
            bonus_reasons.append(f"smc_struct={smc_struct_1h:.0f}")

        # B9: SMC Entry Zone — FVG + Fibonacci + S/D + ICT OTE (10 pts)
        # Price at institutional entry zone: fib confluence, FVG, or demand/supply.
        # Side-aligned: bullish zones for longs, bearish for shorts.
        b9 = False
        if smc_entry_1h >= 40:
            fvg_data = smc_d.get("fvg", {})
            sd_data = smc_d.get("sd", {})
            ote_data = smc_d.get("ote", {})
            fib_data = smc_d.get("fib", {})
            if side == "buy":
                b9 = (fvg_data.get("price_in_bullish_fvg", False)
                      or sd_data.get("price_in_demand", False)
                      or (ote_data.get("in_ote_zone", False) and ote_data.get("ote_bias") == "bullish")
                      or (fib_data.get("at_fib_zone", False) and fib_data.get("fib_bias") == "bullish"))
            else:
                b9 = (fvg_data.get("price_in_bearish_fvg", False)
                      or sd_data.get("price_in_supply", False)
                      or (ote_data.get("in_ote_zone", False) and ote_data.get("ote_bias") == "bearish")
                      or (fib_data.get("at_fib_zone", False) and fib_data.get("fib_bias") == "bearish"))
            # 2026-04-16 (post-audit): side-blind fallback REMOVED.
            # Previously: `if not b9 and smc_entry_1h >= 60: b9 = True`,
            # which awarded +10 to a SHORT entry in a bullish FVG or
            # demand zone — the exact context that should deny the bonus.
            # This was a contributor to the 36% WR on shorts vs 44% on longs.
        if b9:
            bonus_count += 1
            score += 10
            bonus_reasons.append(f"smc_entry={smc_entry_1h:.0f}")

        # B10: SMC Confirmation — liquidity sweep + volume profile + price action (8 pts)
        # Extra conviction: recent stop-hunt, price at value node, bullish/bearish pattern.
        b10 = False
        if smc_conf_1h >= 40:
            if side == "buy":
                b10 = (sweep_data.get("bullish_sweep", False)
                       or pa_data.get("pattern_score", 0) >= 1)
            else:
                b10 = (sweep_data.get("bearish_sweep", False)
                       or pa_data.get("pattern_score", 0) <= -1)
            # 2026-04-16 (post-audit): side-blind fallback REMOVED (same
            # reason as B9). A bullish sweep on a short trade was pushing
            # B10 to True.
        if b10:
            bonus_count += 1
            score += 10  # v4: reweighted +8→+10
            bonus_reasons.append(f"smc_conf={smc_conf_1h:.0f}")

        # ═══ DATA-FEED BONUS CONDITIONS + VETO GATES (2026-05-27) ═════
        # New signals from core/data_coordinator.py that address the
        # anti-predictive score problem. Each is independently switchable
        # via config.DATA_FEEDS.
        _veto_reason = None
        try:
            _df_cfg = {}
            try:
                from config import DATA_FEEDS as _df_cfg
            except (ImportError, AttributeError):
                pass

            _mctx = None
            if data_coordinator is not None:
                _mctx = data_coordinator.get_market_context(coin)

            if _mctx is not None:
                # ── B11: Funding Rate Alignment (+7 pts) ──────────────
                # FR z-score aligns with proposed side:
                #   Extreme neg FR (shorts crowded) + long entry = bonus
                #   Extreme pos FR (longs crowded) + short entry = bonus
                if _df_cfg.get("b11_funding_enabled", True):
                    _fr = _mctx.funding
                    if _fr and not _fr.get("stale", True):
                        _fr_z = _fr.get("fr_zscore", 0.0)
                        _fr_signal = _fr.get("fr_side_signal", "neutral")
                        _z_thresh = _df_cfg.get("b11_fr_zscore_threshold", 1.5)
                        # For shorts, use stricter threshold if configured
                        if side == "sell" and _df_cfg.get("short_side_stricter_feeds"):
                            _z_thresh = _df_cfg.get("short_fr_zscore_threshold", 1.0)
                        b11 = False
                        if (side == "buy" and _fr_signal == "bullish"
                                and abs(_fr_z) >= _z_thresh):
                            b11 = True
                        elif (side == "sell" and _fr_signal == "bearish"
                              and abs(_fr_z) >= _z_thresh):
                            b11 = True
                        if b11:
                            _pts = int(_df_cfg.get("b11_funding_points", 7))
                            bonus_count += 1
                            score += _pts
                            bonus_reasons.append(
                                f"fr_z={_fr_z:+.1f}({_fr_signal})")

                # ── B12: OI-Price Divergence Confirmation (+8 pts) ────
                # "continuation" signal = new money backing the move.
                # Awards bonus when OI confirms the price trend direction.
                if _df_cfg.get("b12_oi_enabled", True):
                    _oi = _mctx.open_interest
                    if _oi and not _oi.get("stale", True):
                        _div = _oi.get("oi_price_divergence", "unknown")
                        _conv = _oi.get("oi_conviction", 0.0)
                        _conv_min = _df_cfg.get("b12_oi_conviction_min", 0.3)
                        b12 = False
                        if _div == "continuation" and _conv >= _conv_min:
                            # Trend continuation: new money entering
                            b12 = True
                        elif (_div == "capitulation" and side == "buy"
                              and _conv >= _conv_min):
                            # Longs capitulating = bounce likely = bullish
                            b12 = True
                        elif (_div == "building" and side == "sell"
                              and _conv >= _conv_min):
                            # New shorts building = bearish continuation
                            b12 = True
                        if b12:
                            _pts = int(_df_cfg.get("b12_oi_points", 8))
                            bonus_count += 1
                            score += _pts
                            bonus_reasons.append(
                                f"oi_{_div}(c={_conv:.2f})")

                # ── B13: Smart Money Alignment (+5 pts) ───────────────
                # Coin is in top-20 smart money inflow on Binance Web3.
                # Only awards for buys (smart money accumulation = bullish).
                if _df_cfg.get("b13_smart_money_enabled", True):
                    _sm = _mctx.smart_money
                    if _sm and not _sm.get("stale", True):
                        if _sm.get("smart_money_inflow", False) and side == "buy":
                            _pts = int(_df_cfg.get(
                                "b13_smart_money_points", 5))
                            bonus_count += 1
                            score += _pts
                            _smr = _sm.get("smart_money_rank", "?")
                            bonus_reasons.append(
                                f"smart_money(rank={_smr})")

                # ═══ VETO GATES ═══════════════════════════════════════

                # V1: OI Exhaustion Veto — blocks longs when move is
                # short-covering (price up + OI down with high conviction)
                if _df_cfg.get("v1_oi_exhaustion_veto", True):
                    _oi = _mctx.open_interest
                    if _oi and not _oi.get("stale", True):
                        _div = _oi.get("oi_price_divergence", "unknown")
                        _conv = _oi.get("oi_conviction", 0.0)
                        _v1_min = _df_cfg.get(
                            "v1_oi_exhaustion_conviction_min", 0.5)
                        if (_div == "exhaustion" and side == "buy"
                                and _conv >= _v1_min):
                            _veto_reason = (
                                f"V1_oi_exhaustion(div={_div},c={_conv:.2f})")
                        elif (_div == "building" and side == "buy"
                              and _conv >= _v1_min):
                            # New shorts building while we want to go long
                            _veto_reason = (
                                f"V1_oi_building(div={_div},c={_conv:.2f})")

                # V3: Slippage Veto — estimated slippage too high
                if (_df_cfg.get("v3_slippage_veto", True)
                        and _veto_reason is None):
                    _ob = _mctx.orderbook
                    if _ob and not _ob.get("stale", True):
                        _max_bps = _df_cfg.get("v3_slippage_max_bps", 30.0)
                        if side == "buy":
                            _slip = _ob.get("slippage_buy_bps", 0.0)
                        else:
                            _slip = _ob.get("slippage_sell_bps", 0.0)
                        if _slip > _max_bps:
                            _veto_reason = (
                                f"V3_slippage({_slip:.1f}bps>{_max_bps:.0f})")

        except Exception as _feed_err:
            # Data feed scoring failed — continue with base score
            logger.debug(f"[MCP-Score] data feed scoring error: {_feed_err}")

        # If any VETO gate fired, return zero score
        if _veto_reason is not None:
            _zero["reason"] = f"veto:{_veto_reason}"
            return _zero

        layers_ok = req_pass + bonus_count

        # ── Distribution-fitted SL/TP (Phase 2 / Task B) ───────────
        # Fit to realized MAE distribution per symbol; falls back to
        # ATR×1.8 / ATR×4.5 when the cell has < 30 warehouse rows. Both
        # halves of the bot now route SL/TP through DistFitSL so there
        # is a single source of truth (the previous split between this
        # block and risk_manager.get_sl_tp produced inconsistent stops).
        atr_1h_pct = ei_1h.get("atr_pct", 2.0) or 2.0
        # 2026-05-03 (Phase 15): per-pair empirical overrides take precedence
        # over the ATR/DistFit path. PAIR_OVERRIDES is a thin operator-tuned
        # table from 30d realized R:R data — see config.py for methodology.
        _ov = None
        try:
            from config import PAIR_OVERRIDES as _PAIR_OV
            _sym_full = coin if "/" in coin else f"{coin}/USDT:USDT"
            _ov_raw = _PAIR_OV.get(_sym_full) or _PAIR_OV.get(coin)
            # Phase 15.3: per-side schema. {"long": {sl,tp}, "short": {sl,tp}}
            # takes precedence over flat schema. Side key normalised to lower.
            if isinstance(_ov_raw, dict):
                _side_key = (side or "").lower()
                if _side_key in ("buy", "long") and "long" in _ov_raw:
                    _ov = _ov_raw["long"]
                elif _side_key in ("sell", "short") and "short" in _ov_raw:
                    _ov = _ov_raw["short"]
                elif "sl_pct" in _ov_raw:  # flat schema
                    _ov = _ov_raw
        except Exception:
            _ov = None
        _pair_override_applied = False
        if _ov is not None:
            sl_pct = max(1.5, float(_ov.get("sl_pct", 1.5)))
            tp_pct = max(sl_pct + 0.1, float(_ov.get("tp_pct", sl_pct * 2.0)))
            _pair_override_applied = True  # blocks STAR ×1.25 stacking
        else:
            try:
                from core.dist_fit_sl import DistFitSL
                _entry_px = ei_1h.get("price", 0) or 0
                _fit = DistFitSL().compute(
                    symbol=coin if "/" in coin else f"{coin}/USDT:USDT",
                    side=side, atr_pct=atr_1h_pct / 100.0,
                )
                sl_pct = max(1.5, _fit.sl_pct * 100.0)  # convert back to percent + floor
                tp_pct = _fit.tp_pct * 100.0
            except Exception:
                sl_pct = max(1.5, min(3.5, atr_1h_pct * 1.5))
                tp_pct = sl_pct * 2.0

        # ── Phase 15.3 Layer C: volatility-adaptive SL multiplier ──
        # ATR magnitude → SL scale: low-vol regimes use tighter SL,
        # high-vol regimes use wider SL to survive normal noise.
        # Floor: never go below 1.5% (zone-tightening invariant).
        if atr_1h_pct < 1.0:
            _vol_mult = 0.7   # low-vol regime — tighter SL
            _vol_label = "low_vol"
        elif atr_1h_pct > 2.5:
            _vol_mult = 1.4   # high-vol regime — wider SL
            _vol_label = "high_vol"
        else:
            _vol_mult = 1.0
            _vol_label = "normal_vol"
        sl_pct_pre_vol = sl_pct
        sl_pct = max(1.5, sl_pct * _vol_mult)
        # Preserve R:R ratio when scaling SL
        if sl_pct_pre_vol > 0:
            tp_pct = tp_pct * (sl_pct / sl_pct_pre_vol)

        # ── Phase 15.3 Layer D: confidence-scaled SL — DISABLED (v4) ──
        # 454-trade data: MCP score is anti-predictive (r=-0.285).
        # score>=85 entries are the WORST performers — widening SL
        # by 1.2x maximizes loss. All SL now uniform.
        _conf_mult = 1.0
        sl_pct_pre_conf = sl_pct
        sl_pct = max(1.5, sl_pct * _conf_mult)
        if sl_pct_pre_conf > 0:
            tp_pct = tp_pct * (sl_pct / sl_pct_pre_conf)

        # ── Smart Money SL/TP refinement ──────────────────────────
        # If strong S/D or OB zone provides a tighter SL reference, use it.
        # Only tighten — never widen SL beyond ATR-based calculation.
        sd_1h = smc_d.get("sd", {})
        if side == "buy":
            # Demand zone below entry → SL just below zone bottom
            # Pick the NEAREST demand zone below price (highest zone_low < price)
            zones = sd_1h.get("demand_zones", [])
            price_val = ei_1h.get("price", 0) or 1
            below = [z for z in zones if z[1] < price_val]
            if below:
                nearest_zone_low = max(z[1] for z in below)  # closest demand floor
                zone_sl_pct = max(0, (price_val - nearest_zone_low) / price_val * 100 + 0.15)
                # 2026-04-18: honor the 1.5% floor — zone-tightening must not
                # push SL inside the ATR clamp (LINK bitget hit a 1.34% SL).
                if 1.5 <= zone_sl_pct < sl_pct:
                    sl_pct = zone_sl_pct
                    tp_pct = sl_pct * 2.5  # tighter SL → wider R:R
        else:
            # Supply zone above entry → SL just above zone top
            # Pick the NEAREST supply zone above price (lowest zone_high > price)
            zones = sd_1h.get("supply_zones", [])
            price_val = ei_1h.get("price", 0) or 1
            above = [z for z in zones if z[0] > price_val]
            if above:
                nearest_zone_high = min(z[0] for z in above)  # closest supply ceiling
                zone_sl_pct = max(0, (nearest_zone_high - price_val) / price_val * 100 + 0.15)
                # 2026-04-18: honor the 1.5% floor — zone-tightening must not
                # push SL inside the ATR clamp.
                if 1.5 <= zone_sl_pct < sl_pct:
                    sl_pct = zone_sl_pct
                    tp_pct = sl_pct * 2.5

        # ── STAR_SYMBOLS TP extender (2026-04-29 / Phase 14) ──────────
        # Warehouse data: ATOM avg realized R = 4.7, ARB avg R = 1.85,
        # both substantially above the 2.0 R the default tp_pct = sl_pct*2
        # assumes. STAR symbols have proven follow-through — clipping the
        # TP at 2× SL caps wins below where the realized MFE distribution
        # actually peaks. Multiply by 1.25× to push TP to ~2.5× SL on
        # default path, ~3.1× on SMC-tightened path.
        # Non-STAR symbols are NOT affected — they generally don't run
        # past 2× SL and the wider TP would just convert them to STALE.
        try:
            from config import STAR_SYMBOLS as _STAR
            _coin = coin if "/" in coin else f"{coin}/USDT:USDT"
            # Phase 15: PAIR_OVERRIDES is the final word; don't stack ×1.25
            # on top of an operator-set tp_pct.
            if _coin in _STAR and not _pair_override_applied:
                tp_pct *= 1.25
        except Exception:
            pass

        # ── ACCURACY_TARGET_MODE final TP override (owner goal 2026-07-10) ──
        # Applied LAST so it wins over pair overrides / STAR / zone R:R —
        # the 60-65% WR band is set by geometry: TP = frac x SL. Flag-off
        # returns tp_pct unchanged (byte-identical). SL untouched.
        tp_pct = _apply_accuracy_target(sl_pct, tp_pct, side=side)

        # Confidence: base 0.66 for 4 required, +0.08 per bonus, cap 0.95
        # (2026-04-17: stepped from 0.04 → 0.08 at user request; saturates at 4 bonuses)
        confidence = min(0.95, 0.66 + bonus_count * 0.08)

        # ── Kronos-inspired: weekday confidence multiplier ────────────
        # Calendar temporal awareness (Kronos uses hour/weekday/month embeddings).
        # 459-trade data: Mon 19% WR, Tue 36% WR → catastrophic weekdays.
        _weekday_mult = 1.0
        try:
            from config import WEEKDAY_CONFIDENCE_MULT
            _dow = datetime.now(timezone.utc).weekday()
            # Python weekday(): 0=Mon..6=Sun → strftime %w: 0=Sun..6=Sat
            _dow_strftime = (_dow + 1) % 7
            _weekday_mult = WEEKDAY_CONFIDENCE_MULT.get(_dow_strftime, 1.0)
            if _weekday_mult != 1.0:
                bonus_reasons.append(f"dow={_dow_strftime}(x{_weekday_mult})")
        except (ImportError, Exception):
            pass

        reason = " | ".join(req_reasons + bonus_reasons)
        return {
            "score": score,
            "layers_ok": layers_ok,
            "side": side,
            "sl_pct": round(sl_pct, 2),
            "tp_pct": round(tp_pct, 2),
            "confidence": round(confidence * _weekday_mult, 2),
            "reason": reason,
            "_weekday_mult": _weekday_mult,
        }

def score_coin_scalp(coin: str, data: dict, ei: dict) -> dict:
        """Scalp-optimized scoring for 15-60 min holds, longs-only, VWAP-centric."""
        try:
            from config import SCALP_MODE as _SM
        except ImportError:
            _SM = {}

        ei_4h = ei.get("4h", {})
        ei_1h = ei.get("1h", {})
        fr = data.get("funding", {}).get(coin, {}) if data.get("funding") else {}
        ob = data.get("orderbook", {}).get(coin, {}) if data.get("orderbook") else {}

        sl_pct = _SM.get("sl_pct", 1.0)
        tp_pct = _SM.get("tp_pct", 1.8)
        _zero = {"score": 0, "side": "buy", "layers_ok": 0, "confidence": 0,
                 "sl_pct": sl_pct, "tp_pct": tp_pct, "reason": "", "_scalp": True}

        if not ei_4h or not ei_1h:
            _zero["reason"] = "no_data"
            return _zero

        # === VETO GATES ===
        atr_1h_pct = ei_1h.get("atr_pct", 0)
        if atr_1h_pct < _SM.get("min_atr_pct", 0.8):
            _zero["reason"] = f"scalp_veto:quiet(atr={atr_1h_pct:.2f}%)"
            return _zero

        adx_4h = ei_4h.get("adx", 0)
        if adx_4h < 15:
            _zero["reason"] = f"scalp_veto:ranging(4h_adx={adx_4h:.0f})"
            return _zero

        ema20_above_50_4h = ei_4h.get("ema20_above_50", False)
        side = "buy" if ema20_above_50_4h else "sell"

        if _SM.get("longs_only", True) and side == "sell":
            _zero["reason"] = "scalp_veto:shorts_disabled"
            return _zero

        # === REQUIRED (all 4) ===
        req_pass = 0
        req_reasons = []

        pvw = abs(ei_1h.get("price_vs_vwap", 99))
        if pvw <= _SM.get("vwap_distance_max_pct", 0.3):
            req_pass += 1
            req_reasons.append(f"vwap_near={pvw:.2f}%")

        rsi_1h = ei_1h.get("rsi", 50)
        if 40 <= rsi_1h <= 60:
            req_pass += 1
            req_reasons.append(f"rsi={rsi_1h:.0f}")

        adx_1h = ei_1h.get("adx", 0)
        if adx_1h >= 20:
            req_pass += 1
            req_reasons.append(f"adx={adx_1h:.0f}")

        if atr_1h_pct >= _SM.get("min_atr_pct", 0.8):
            req_pass += 1
            req_reasons.append(f"atr={atr_1h_pct:.2f}%")

        if req_pass < 4:
            _zero["reason"] = f"scalp_req_fail({req_pass}/4:{','.join(req_reasons)})"
            return _zero

        # === BONUS CONDITIONS ===
        score = 50
        bonus_count = 0
        bonus_reasons = []

        # B_VWAP (+15): near VWAP and approaching from below (longs). Left at +15: this is a
        # load-bearing, tighter-window (-0.2..0.15) calibration of the SCALP tier (it must reach
        # the scalp entry threshold) — distinct from the standard B7 reverted to +5 on 2026-06-06.
        pvw_signed = ei_1h.get("price_vs_vwap", 0)
        if side == "buy" and -0.2 <= pvw_signed <= 0.15:
            bonus_count += 1
            score += 15
            bonus_reasons.append(f"vwap_prime={pvw_signed:+.2f}%")

        # B_OB (+10): orderbook bid-heavy
        imb = ob.get("imbalance", 0) if ob else 0
        if side == "buy" and imb > 0.10:
            bonus_count += 1
            score += 10
            bonus_reasons.append(f"ob_bid={imb:+.2f}")

        # B_VOL (+8): volume surge
        vol_ratio = ei_1h.get("vol_ratio", 0)
        if vol_ratio >= 1.5:
            bonus_count += 1
            score += 8
            bonus_reasons.append(f"vol={vol_ratio:.1f}x")

        # B_STRUCTURE (+8): at demand zone
        smc_d = ei_1h.get("smc_detail", {}) or {}
        sd_data = smc_d.get("sd", {}) or {}
        price_val = ei_1h.get("price", 0) or 1
        if side == "buy":
            for z in (sd_data.get("demand_zones") or []):
                if len(z) >= 2:
                    zone_top = max(z[0], z[1])
                    dist = abs(price_val - zone_top) / price_val * 100
                    if dist <= 0.5:
                        bonus_count += 1
                        score += 8
                        bonus_reasons.append(f"demand_zone(d={dist:.2f}%)")
                        break

        # B_FUNDING (+7): negative funding favors longs
        funding_rate = fr.get("funding_rate", 0) if fr else 0
        if side == "buy" and funding_rate < 0:
            bonus_count += 1
            score += 7
            bonus_reasons.append(f"funding={funding_rate*100:+.3f}%")

        # B_SESSION (+5): peak hours
        from datetime import datetime, timezone
        utc_hour = datetime.now(timezone.utc).hour
        if utc_hour in _SM.get("peak_hours", {22, 23, 0}):
            bonus_count += 1
            score += 5
            bonus_reasons.append(f"peak_h{utc_hour}")

        layers_ok = req_pass + bonus_count
        confidence = min(0.95, 0.66 + bonus_count * 0.08)

        return {
            "score": score,
            "layers_ok": layers_ok,
            "side": side,
            "sl_pct": round(sl_pct, 2),
            "tp_pct": round(tp_pct, 2),
            "confidence": round(confidence, 2),
            "reason": " | ".join(req_reasons + bonus_reasons),
            "_scalp": True,
        }

def route_score_coin(brain, coin: str, data: dict, ei: dict) -> dict:
        """Route to scalp or standard scorer; AccBand quiet fall-through.

        When SCALP_MODE is enabled and the scalp path returns a protective
        quiet/ranging veto, PAPER+MAX_FLOW_BAND falls through to
        ``_score_coin`` so AccBand research is not starved by scalp ATR
        gates. Does not loosen ``SCALP_MIN_ATR`` (hashed-prereg required).
        """
        try:
            from config import SCALP_MODE as _SM_route
        except ImportError:
            _SM_route = {"enabled": False}
        if not _SM_route.get("enabled", False):
            return brain._score_coin(coin, data, ei)

        result = brain._score_coin_scalp(coin, data, ei)
        reason = str(result.get("reason") or "")
        if not any(reason.startswith(p) for p in _SCALP_FALLBACK_VETO_PREFIXES):
            return result
        # Lazy import preserves test monkeypatches on core.mcp_brain facade.
        from core.mcp_brain import _max_flow_scalp_fallback_enabled

        if not _max_flow_scalp_fallback_enabled():
            return result

        std = dict(brain._score_coin(coin, data, ei) or {})
        std["_scalp"] = False
        std["_scalp_fallback"] = reason
        prior = str(std.get("reason") or "").strip()
        std["reason"] = (
            f"scalp_fallback({reason})|{prior}" if prior else f"scalp_fallback({reason})"
        )
        return std
