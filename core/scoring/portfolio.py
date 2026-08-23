"""Algorithmic portfolio scan: score universe, emit OPEN/CLOSE actions."""
import uuid

from loguru import logger

try:
    from core.warehouse import get_warehouse
except ImportError:
    get_warehouse = None

from core.scoring.data_sources import _microstructure_features
from core.scoring.entry_score import route_score_coin
from core.scoring.helpers import (
    _accband_tradfi_scope_reason,
    _entry_score_floor,
    _format_scalp_rule_skip_reason,
)

def algorithmic_portfolio(brain, coins, data, exchange_indicators,
                                open_positions, exchange_balances,
                                risk_envelope) -> list:
        """Pure algorithmic scoring v3.1 — no Claude needed.
        Score all coins, pick top entries with score >= 66 AND 6+/10 layers.

        2026-04-14 v3.1: 4 required + 2+ bonus = score >= 66, layers >= 6.
        Regime filter (ADX/BB) kills entries before scoring even starts.
        Risk: 1.0% per trade, $4 hard cap.
        """
        actions = []
        max_new = risk_envelope.get("max_new_positions", 0)
        if max_new <= 0:
            return []

        # Build the union of static + dynamic blacklist bases to pre-filter
        blacklist_bases: set = set()
        try:
            import config as _cfg
            for sym in getattr(_cfg, "BLACKLIST_HARD", []) or []:
                blacklist_bases.add(sym.split("/")[0].upper())
        except Exception:
            pass
        try:
            from core.auto_mutator import AutoMutator as _AM
            dyn = _AM().get_effective_blacklist()
            for sym in dyn:
                blacklist_bases.add(sym.split("/")[0].upper())
        except Exception:
            pass

        # Score all coins
        scored = []
        skipped_bl = 0
        open_bases = {p.get("symbol", "").split("/")[0] for p in open_positions}
        for coin in coins:
            if coin in open_bases:
                continue  # Already have a position
            if coin.upper() in blacklist_bases:
                skipped_bl += 1
                continue  # Guaranteed to be rejected downstream
            ei = exchange_indicators.get(coin, {})
            if not ei:
                # Record the drop. Every OTHER elimination in this loop leaves a
                # typed candidates row; until 2026-08-21 this one left nothing,
                # so the per-cycle denominator was unknowable and a degraded
                # indicator feed was indistinguishable from a quiet market.
                # Measured live that day: universe 55 coins every cycle while
                # "Exchange indicators: N coins" alternated 13 / 40 -- i.e.
                # 15-42 of 55 (27-76%) vanished silently, every cycle.
                # Fail-soft: telemetry must never be able to halt trading.
                if get_warehouse is not None:
                    try:
                        get_warehouse().record_candidate(
                            exchange="*",
                            symbol=f"{coin}/USDT",
                            market_type="futures",
                            strategy_family="systematic_v3_1",
                            decision="SKIP",
                            skip_reason="no_indicator_data",
                        )
                    except Exception:
                        pass
                continue  # No indicator data
            # AccBand PAPER funnel: do not ALLOW ANALYSIS_ONLY TradFi bases
            # (MSFT/NVDA/…) — they pollute allow-rate without a screened edge.
            _scope = _accband_tradfi_scope_reason(coin)
            if _scope:
                result = {
                    "score": 0,
                    "layers_ok": 0,
                    "side": "buy",
                    "sl_pct": 0.0,
                    "tp_pct": 0.0,
                    "confidence": 0.0,
                    "reason": _scope,
                    "_scalp": True,
                }
            else:
                # Scalp mode routing (v4) + quiet/ranging fall-through under
                # PAPER+MAX_FLOW_BAND (2026-08-01 drought regression).
                result = route_score_coin(brain, coin, data, ei)

            # ── Calibrated p_win blend (Phase 6) ─────────────────────
            # Build the model-input feature dict from the snapshot keys the
            # trainer learned on. Falls back to rule-only when no model is
            # loaded — `score_via_model` returns p_ens=sigmoid((score-65)/8)
            # in that case so the gate logic below stays uniform.
            ei_1h = ei.get("1h", {})
            ei_4h = ei.get("4h", {})
            model_input = {
                "score":       result.get("score"),
                "layers_ok":   result.get("layers_ok"),
                "confidence":  result.get("confidence"),
                "sl_pct":      result.get("sl_pct"),
                "tp_pct":      result.get("tp_pct"),
                "rsi_1h":      ei_1h.get("rsi"),
                "adx_1h":      ei_1h.get("adx"),
                "adx_4h":      ei_4h.get("adx"),
                "atr_pct_1h":  ei_1h.get("atr_pct"),
                "bb_width_4h": ei_4h.get("bb_width"),
                "vol_ratio":   ei_1h.get("vol_ratio"),
                "ema20_above_50_4h": ei_4h.get("ema20_above_50"),
                "ema20_above_50_1h": ei_1h.get("ema20_above_50"),
                "funding_rate": (data.get("funding", {}).get(coin, {}) or {}).get("funding_rate"),
                "ob_imbalance": (data.get("orderbook", {}).get(coin, {}) or {}).get("imbalance"),
            }
            mscore = brain.score_via_model(
                market_type="futures",
                feats=model_input,
                rule_score=float(result.get("score") or 0),
            )
            result["p_win_lr"]       = mscore["p_win_lr"]
            result["p_win_gbm"]      = mscore["p_win_gbm"]
            result["p_win_ensemble"] = mscore["p_win_ensemble"]
            result["model_version"]  = mscore["model_version"]

            # Warehouse candidate emission — every scored symbol (spec §4, §6).
            # ALLOW if BOTH the rule gate AND the model gate pass.
            try:
                from config import MODEL_GATE
            except ImportError:
                MODEL_GATE = {"enabled": False, "shadow_only": True,
                              "threshold_futures": 0.55, "threshold_spot": 0.58}
            if result.get("_scalp"):
                try:
                    from config import SCALP_MODE as _SM_gate
                except ImportError:
                    _SM_gate = {}
                rule_gate = result["score"] >= _entry_score_floor(True, _SM_gate) and result["layers_ok"] >= 4
            else:
                rule_gate = result["score"] >= _entry_score_floor(False) and result["layers_ok"] >= 6
            model_gate_active = (
                MODEL_GATE.get("enabled", False)
                and not MODEL_GATE.get("shadow_only", False)
                and mscore["model_version"] is not None
            )
            if model_gate_active:
                threshold = float(MODEL_GATE.get("threshold_futures", 0.55))
                model_pass = float(result["p_win_ensemble"]) >= threshold
            else:
                model_pass = True  # shadow-only or no model loaded
            gate_pass = rule_gate and model_pass
            decision = "ALLOW" if gate_pass else "SKIP"
            if not rule_gate:
                if result.get("_scalp"):
                    try:
                        from config import SCALP_MODE as _SM_floor
                    except ImportError:
                        _SM_floor = {}
                    _floor = _entry_score_floor(True, _SM_floor)
                    skip_reason = _format_scalp_rule_skip_reason(
                        result, floor=_floor
                    )
                else:
                    skip_reason = result.get("reason") or "gate_fail"
            elif not model_pass:
                skip_reason = (
                    f"model_gate(p_ens={result['p_win_ensemble']:.3f}<"
                    f"{threshold:.2f},v={mscore['model_version']})"
                )
            else:
                skip_reason = ""
            cand_id = -1
            if get_warehouse is not None:
                try:
                    wh = get_warehouse()
                    feat = dict(model_input)
                    feat["reason"] = result.get("reason")
                    feat["p_win_lr"]       = mscore["p_win_lr"]
                    feat["p_win_gbm"]      = mscore["p_win_gbm"]
                    feat["p_win_ensemble"] = mscore["p_win_ensemble"]
                    feat["model_version"]  = mscore["model_version"]
                    feat.update(_microstructure_features(coin, data))  # 2026-05-25 microstructure capture
                    # 2026-05-27: capture data-feed enrichment features for model training
                    try:
                        if brain._data_coordinator is not None:
                            _wh_ctx = brain._data_coordinator.get_market_context(coin)
                            if _wh_ctx.funding:
                                feat["fr_zscore"] = _wh_ctx.funding.get("fr_zscore", 0)
                                feat["fr_side_signal"] = _wh_ctx.funding.get("fr_side_signal", "neutral")
                            if _wh_ctx.open_interest:
                                feat["oi_delta_6h_pct"] = _wh_ctx.open_interest.get("oi_delta_6h_pct", 0)
                                feat["oi_divergence"] = _wh_ctx.open_interest.get("oi_price_divergence", "unknown")
                                feat["oi_conviction"] = _wh_ctx.open_interest.get("oi_conviction", 0)
                            if _wh_ctx.orderbook:
                                feat["ob_imb_momentum"] = _wh_ctx.orderbook.get("imbalance_momentum", 0)
                                feat["ob_spread_bps"] = _wh_ctx.orderbook.get("spread_bps", 0)
                                feat["ob_slippage_bps"] = _wh_ctx.orderbook.get(
                                    "slippage_buy_bps" if result.get("side") == "buy" else "slippage_sell_bps", 0)
                            if _wh_ctx.smart_money:
                                feat["smart_money_inflow"] = 1 if _wh_ctx.smart_money.get("smart_money_inflow") else 0
                                feat["crowd_signal"] = _wh_ctx.smart_money.get("crowd_signal", "neutral")
                    except Exception:
                        pass  # fail-open: missing enrichment features = neutral in model
                    cand_id = wh.record_candidate(
                        exchange="*",  # exchange picked later
                        symbol=f"{coin}/USDT",
                        market_type="futures",
                        side=result.get("side"),
                        strategy_family="systematic_v3_1",
                        entry_px=None,
                        stop_px=None,
                        target_px=None,
                        leverage=None,
                        size_pct=None,
                        confidence=result.get("confidence"),
                        decision=decision,
                        skip_reason=skip_reason,
                        features=feat,
                    )
                    # Phase 13.5 — shadow LR prediction. Logs to warehouse
                    # `predictions` table only; does NOT influence gate_pass.
                    # Phase 13.5b — pass candidate_id + market_type so:
                    #   1. predictions row JOINs cleanly to trades via
                    #      candidate_id (trades.candidate_id == this cand_id
                    #      when the candidate becomes a trade);
                    #   2. predictions.symbol uses the same "BASE/QUOTE:QUOTE"
                    #      format the trades table uses.
                    if gate_pass:  # only log for trades we're actually about to take
                        try:
                            import time as _time

                            from core.shadow_predictor import ShadowPredictor
                            ShadowPredictor.get().log_entry(
                                ts=_time.time(),
                                symbol=f"{coin}/USDT",
                                side=str(result.get("side", "")),
                                features=feat,
                                warehouse=wh,
                                candidate_id=cand_id,
                                market_type="futures",
                            )
                        except Exception as _se:
                            logger.debug(f"[Shadow] log_entry skipped: {_se}")
                except Exception as _we:
                    logger.debug(f"[MCP-Algo] warehouse emit failed for {coin}: {_we}")

            if gate_pass:
                result["_candidate_id"] = cand_id
                scored.append((coin, result))

        if skipped_bl:
            logger.debug(f"[MCP-Algo] pre-filtered {skipped_bl} blacklisted coins")

        # ── SHORT-SIDE FILTER (May 2026) ─────────────────────────────
        # Drop SELL candidates while BTC trends up on both 4h+1h. Warehouse
        # evidence: shorts net -$54 vs longs net -$4. Filter is opt-in via
        # config.SHORT_SIDE_FILTER.enabled (default True).
        try:
            from config import SHORT_SIDE_FILTER as _SSF
        except ImportError:
            _SSF = {"enabled": True}
        if _SSF.get("enabled", True):
            try:
                from core.short_side_filter import (
                    evaluate as _ssf_eval,
                )
                from core.short_side_filter import (
                    extract_btc_trends as _ssf_btc,
                )
                btc_4h_up, btc_1h_up = _ssf_btc(exchange_indicators)
                _filtered = []
                _ssf_blocked = 0
                for coin, result in scored:
                    if (result.get("side") or "").lower() == "sell":
                        d = _ssf_eval(
                            side="sell",
                            symbol=f"{coin}/USDT",
                            btc_4h_uptrend=btc_4h_up,
                            btc_1h_uptrend=btc_1h_up,
                            symbol_news_sentiment=None,
                        )
                        if d.block:
                            _ssf_blocked += 1
                            logger.info(
                                f"[ShortFilter] SKIP {coin}/USDT sell -- {d.reason}"
                            )
                            continue
                    _filtered.append((coin, result))
                if _ssf_blocked:
                    logger.info(
                        f"[ShortFilter] blocked {_ssf_blocked} short candidate(s) "
                        f"(btc_4h_up={btc_4h_up}, btc_1h_up={btc_1h_up})"
                    )
                scored = _filtered
            except Exception as _ssfe:
                logger.debug(f"[ShortFilter] skipped ({_ssfe}) -- defaulting to ALLOW")

        # Sort by score descending, take top N
        scored.sort(key=lambda x: x[1]["score"], reverse=True)

        # Track how many positions we've assigned to each exchange this cycle
        _ex_assigned = {}

        for coin, result in scored[:max_new]:
            _signal_tf = (exchange_indicators.get(coin) or {}).get("4h", {})
            _signal_venue = str(_signal_tf.get("source_venue") or "").lower()
            _signal_market = str(_signal_tf.get("source_market_type") or "").lower()
            if not _signal_venue or _signal_market not in {"spot", "futures"}:
                logger.warning(
                    f"[MCP-Algo] SKIP {coin}: indicator source provenance missing"
                )
                continue
            # Pick best exchange — distribute across exchanges, prefer highest balance
            candidates = []
            for ex_name, bals in exchange_balances.items():
                if str(ex_name).lower() != _signal_venue:
                    continue
                assigned = _ex_assigned.get(ex_name, 0)
                fut_bal = bals.get("futures", 0)
                spot_bal = bals.get("spot", 0)
                if (_signal_market == "futures" and fut_bal >= 8
                        and result["side"] in ("buy", "sell")):
                    candidates.append((ex_name, "futures", fut_bal, assigned))
                elif (_signal_market == "spot" and spot_bal >= 8
                      and result["side"] == "buy"):
                    candidates.append((ex_name, "spot", spot_bal, assigned))

            if not candidates:
                continue

            # Sort: least assigned first, then highest balance
            candidates.sort(key=lambda c: (c[3], -c[2]))
            best_ex = candidates[0][0]
            best_mtype = candidates[0][1]
            _ex_assigned[best_ex] = _ex_assigned.get(best_ex, 0) + 1

            # Spot: side must be buy, adjust SL/TP minimums
            side = result["side"]
            from config import RISK as _RISK_CFG
            leverage = min(3, _RISK_CFG.get("futures_max_leverage", 3)) if best_mtype == "futures" else 1

            if result.get("_scalp"):
                # Scalp trades use fixed SL/TP from config, bypass all clamping
                sl_pct = result["sl_pct"]
                tp_pct = result["tp_pct"]
            else:
                # 2026-05-24 — Gated on config.SCALP_TIER_ENABLED. When off,
                # the futures TP reverts to the pre-May-22 4.0% floor.
                import config as _cfg_floor
                _scalp_floor_on = getattr(_cfg_floor, "SCALP_TIER_ENABLED", True)
                if best_mtype == "spot":
                    if side == "sell":
                        continue  # Can't short on spot
                    sl_pct = max(result["sl_pct"], 2.0)
                    # 2026-05-22: spot TP floor stays at 2.0% (round-trip
                    # spot fees on Binance ~0.20% leave room for a 2.0% TP).
                    # Previously 4.0% — too wide for the user's scalp directive.
                    tp_pct = max(result["tp_pct"], 2.0)
                else:
                    sl_pct = max(result["sl_pct"], 1.5)
                    if _scalp_floor_on:
                        # 2026-05-22: futures TP clamped to user's scalp band
                        # [1.0%, 2.0%]. Previously floored at 4.0% — too wide.
                        tp_pct = min(2.0, max(1.0, result["tp_pct"]))
                    else:
                        # Pre-2026-05-22 wide TP floor.
                        tp_pct = max(result["tp_pct"], 4.0)

            # ── RISK-BASED SIZING v3 ───────────────────────────────
            # 2026-04-14 v3: 1.0% risk + $4 hard cap.
            # Previous 1.5%/$5 still produced -$11 and -$8 fat-tail
            # losses. On a $420 account, 1% = $4.20 which aligns with
            # the dollar cap. Combined with tighter SL [1.5%, 3%] this
            # keeps max theoretical loss per trade under control.
            #
            # Formula: size_pct = RISK% * 100 / (sl% * leverage)
            RISK_PER_TRADE_PCT = 1.0  # 1.0% of account per trade
            from config import MAX_LOSS_PER_TRADE_USD
            MAX_LOSS_USD = MAX_LOSS_PER_TRADE_USD  # $2.00 from config

            # Get total portfolio value for dollar-cap conversion.
            # Unified exchanges (Bybit) — and ALL exchanges in PAPER mode —
            # store the same balance in both spot and futures, so the shared
            # helper counts each wallet once to avoid double-counting.
            from core.balance_utils import deployable_total as _deployable_total
            total_bal = _deployable_total(exchange_balances)

            size_pct = RISK_PER_TRADE_PCT * 100.0 / (sl_pct * leverage)

            # Dollar cap: max_size_pct where loss = total_bal * size_pct/100 * leverage * sl_pct/100 <= MAX_LOSS_USD
            if total_bal > 0 and leverage > 0 and sl_pct > 0:
                max_size_from_usd = MAX_LOSS_USD / (total_bal * leverage * sl_pct / 100.0) * 100.0
                size_pct = min(size_pct, max_size_from_usd)

            max_pos_pct = _RISK_CFG.get("max_position_pct", 0.05) * 100  # 1% live, 5% paper
            size_pct = min(max_pos_pct, max(0.5, size_pct))

            actions.append({
                "type": "OPEN",
                "symbol": f"{coin}/USDT",
                "exchange": best_ex,
                "market_type": best_mtype,
                "side": side,
                "leverage": leverage,
                "size_pct": size_pct,
                "sl_pct": sl_pct,
                "tp_pct": tp_pct,
                "confidence": result["confidence"],
                "mcp_score": result.get("score", 0),
                # Band regime filter (2026-07-12): thread the 4h ADX the scorer
                # already computed so _execute_open's band-lane veto can read
                # it without recomputing. Sources without it fail open there.
                "adx_4h": (exchange_indicators.get(coin) or {}).get("4h", {}).get("adx"),
                "model_version": result.get("model_version"),
                "p_win_ensemble": result.get("p_win_ensemble"),
                "reason": (
                    f"ALGO score={result['score']} layers={result['layers_ok']}/7 "
                    f"p_ens={result.get('p_win_ensemble', float('nan')):.3f} "
                    f"{result['reason']}"
                ),
                "position_id": "",
                "candidate_id": result.get("_candidate_id", -1),
                # Provenance: algo-built actions mint their own fresh ids
                "decision_id": str(uuid.uuid4()),
                "source": "algo",
                "signal_venue": _signal_venue,
                "signal_market_type": _signal_market,
                "signal_symbol": _signal_tf.get("source_symbol"),
                "signal_candle_ts": _signal_tf.get("candle_ts"),
            })
            logger.info(
                f"[MCP-Algo] OPEN {coin}/USDT {side} on {best_ex} "
                f"({best_mtype}) score={result['score']} "
                f"layers={result['layers_ok']}/7 conf={result['confidence']:.0%}")

        # Also generate CLOSE actions for losing positions
        for p in open_positions:
            pnl_pct = p.get("pnl_pct", 0) or 0
            p.get("age_min", 0) or 0
            coin = p.get("symbol", "").split("/")[0].split(":")[0]
            ei = exchange_indicators.get(coin, {})
            ei_4h = ei.get("4h", {})
            ei_1h = ei.get("1h", {})
            side = p.get("side", "buy")

            # 2026-04-12: Hard exit rules. Trend reversal = 1h EMA 20 crosses
            # below EMA 50 (not just ema_dir which uses 9/21/50 alignment).
            if side == "buy" and not ei_1h.get("ema20_above_50", True):
                pass
            elif side == "sell" and ei_1h.get("ema20_above_50", False):
                pass

            should_close = False
            reason = ""
            # 2026-04-21: pnl_pct is LEVERAGED. Scan-CLOSE thresholds were
            # aligned with the monitor (-12 / -3) so they can no longer fire
            # inside the SL's own leveraged-price range. Prior thresholds
            # (-5 / -1.5 / any-loss+aged) tripped at 0.17-1.67% price moves
            # at 3x leverage — well inside SL distance — and closed trades
            # at 0W/10L (systematic_close in warehouse, 2026-04-14..21).
            # HARD RULE 1: Absolute max loss — catastrophic overshoot only.
            if pnl_pct < -12.0:
                should_close = True
                reason = f"ALGO HARD MAX LOSS {pnl_pct:+.1f}%"
            # RULE 2 REMOVED 2026-04-25: "pnl < -3% leveraged + trend reversed"
            # produced 0W/10L all-time (firings on 2026-04-20..23 averaged
            # -1% PRICE i.e. inside the 1.5%-3.5% SL band — SL would have
            # caught the real losses, the rule just exited noise pullbacks
            # before recovery). Same pathology as the already-removed Rule 3.
            # SL/trailing/Rule 1 own normal exits; Rule 1 alone is enough as
            # the catastrophic-overshoot safety rail.
            # RULE 3 REMOVED 2026-04-21: "any loss + trend_against + aged>90m"
            # was a stale-close variant firing on tiny leveraged drawdowns and
            # produced the 0W/10L systematic_close bucket. Leveraged-aware
            # Rule 2 above catches real trend-reversed losses; monitor's own
            # rules handle the rest.
            # RULE 4: Stale flat position — DISABLED 2026-04-20.
            # Warehouse data: 15 trades closed by systematic_close stale rules
            # at 7% WR, avg -0.18 PnL each — the rule kills breakeven positions
            # before they can resolve. Let SL / trend-reversal / TP catch real
            # outcomes; funding cost (~0.02%/8h) is trivial vs the -0.18 avg
            # bleed this rule produces. Re-enable only after scored data shows
            # stale positions have negative forward expectancy.

            if should_close and p.get("id"):
                actions.append({
                    "type": "CLOSE",
                    "symbol": p.get("symbol", ""),
                    "exchange": (p.get("exchange") or "").lower(),
                    "market_type": p.get("market_type", "futures"),
                    "side": side,
                    "leverage": 1,
                    "size_pct": 0,
                    "sl_pct": 0,
                    "tp_pct": 0,
                    "confidence": 0.80,
                    "reason": reason,
                    "position_id": p["id"],
                    "decision_id": str(uuid.uuid4()),
                    "source": "algo",
                })
                logger.info(f"[MCP-Algo] CLOSE {p.get('symbol','')} — {reason}")

        return actions
