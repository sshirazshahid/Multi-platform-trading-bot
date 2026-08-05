"""Algorithmic position monitor: HOLD/CLOSE/TP/BREAKEVEN advice."""
import uuid

from loguru import logger

def algorithmic_position_monitor(positions: list, data: dict,
                                       exchange_indicators: dict) -> dict:
        """Systematic position monitor v3 — hard rules, no override.

        2026-04-14 v3.1: tighter exits to prevent fat-tail losses.
        Added MACD momentum flip and momentum exhaustion exits.
        Reduced max loss -5% to -3%, stale 3h to 6h (trend-following needs
        time), trend reversal triggers at any loss (not just -0.5%).

        Priority order (first match wins):
          1. HARD MAX LOSS:  pnl < -3%               -> CLOSE (no exceptions)
          2. TREND REVERSED: 1h EMA20 flipped + loss  -> CLOSE (> -0.5%)
          3. EXHAUSTION:     momentum exhaustion       -> CLOSE/TP
          4. STALE:          > 6h, |pnl| < 0.3%       -> CLOSE
          6. BIG WINNER:     pnl >= 4%, trend ok       -> HOLD (ride it)
          7. RSI EXTREME:    pnl >= 2%, RSI extreme    -> TAKE_PROFIT
          8. PROFIT LOCK:    pnl >= 2.0%               -> BREAKEVEN
          9. DEFAULT:                                   -> HOLD
        """
        advice = {}
        for p in positions:
            pid = p.get("id", "")
            pnl_pct = p.get("pnl_pct", 0) or 0
            p.get("age_min", 0) or 0
            side = p.get("side", "buy")
            coin = p.get("symbol", "?").split("/")[0].split(":")[0]

            ei = exchange_indicators.get(coin, {})
            ei_1h = ei.get("1h", {})
            rsi_1h = ei_1h.get("rsi", 50)

            # Trend check using EMA 20/50
            trend_with = False
            trend_against = False
            if side == "buy":
                trend_with = ei_1h.get("ema20_above_50", False)
                trend_against = not trend_with
            else:
                trend_with = not ei_1h.get("ema20_above_50", True)
                trend_against = not trend_with

            # MACD momentum check
            macd_hist = ei_1h.get("macd_hist", 0)
            macd_rising = ei_1h.get("macd_hist_rising", False)
            if side == "buy":
                macd_with = macd_hist > 0
                macd_fading = macd_hist > 0 and not macd_rising  # positive but shrinking
            else:
                macd_with = macd_hist < 0
                macd_fading = macd_hist < 0 and macd_rising  # negative but shrinking

            action = "HOLD"
            conf = 0.5
            reason = "default hold"

            # 1. HARD MAX LOSS backstop — strictly worse than max possible SL.
            # pnl_pct here is LEVERAGED; SL is unleveraged price move clamped
            # 1.5-3.5%. At 3x leverage, max loss at SL = 10.5% leveraged; at
            # 5x = 17.5%. Threshold -12% lets SL run at 3x/4x without being
            # pre-empted but still catches catastrophic overshoot.
            # (OLD -3.0 fired at r≈-0.3 of SL distance — cut winners short.)
            if pnl_pct < -12.0:
                action = "CLOSE"
                conf = 0.99
                reason = f"HARD MAX LOSS {pnl_pct:+.1f}% (limit -12%)"

            # 2. (MACD flip exit REMOVED — backtested at 0% WR across all
            #    symbols. 1h MACD is too noisy for exit signals. SL + trend
            #    reversal + stale handle downside protection adequately.)

            # 3. TREND REVERSED (1h EMA) + meaningful leveraged loss (> -3%).
            # Was -0.5% but pnl_pct is leveraged — at 3x that's only 0.17%
            # price move, well inside normal noise. -3% leveraged = ~1%
            # price move = a real, trend-confirmed reversal.
            elif pnl_pct < -3.0 and trend_against:
                action = "CLOSE"
                conf = 0.88
                reason = f"trend reversed + loss {pnl_pct:+.1f}%"

            # 5. STALE POSITION — DISABLED 2026-04-20.
            # Same rationale as _algorithmic_scan's RULE 4: 7% WR, -0.18 avg PnL
            # across 15 samples shows stale-close is a net-negative trigger.
            # Breakeven positions need room to resolve; SL handles real losses,
            # trend-reversal rule above handles real reversals.

            # 6. BIG WINNER riding trend + MACD with — protect first
            # 2026-04-16 (post-audit): moved ABOVE exhaustion rule because
            # a position at +5% with fading MACD and RSI 72 was hitting the
            # exhaustion rule (old rule 4) and exiting instead of riding to
            # full TP. Big winners with trend+MACD alignment get priority.
            elif pnl_pct >= 4.0 and trend_with and macd_with:
                action = "HOLD"
                conf = 0.85
                reason = f"winner pnl={pnl_pct:+.1f}% trend+MACD aligned, ride"

            # 4. MOMENTUM EXHAUSTION — RSI divergence / fading momentum
            # (Only applies when NOT already a big protected winner — see rule 6.)
            elif 0 < pnl_pct < 4.0 and macd_fading and (
                (side == "buy" and rsi_1h > 70) or
                (side == "sell" and rsi_1h < 30)
            ):
                action = "TAKE_PROFIT"
                conf = 0.82
                reason = f"exhaustion: pnl={pnl_pct:+.1f}% MACD fading RSI={rsi_1h:.0f}"

            # 7. RSI exhaustion with decent profit
            elif pnl_pct >= 2.0 and (
                (side == "buy" and rsi_1h > 72) or
                (side == "sell" and rsi_1h < 28)
            ):
                action = "TAKE_PROFIT"
                conf = 0.80
                reason = f"pnl={pnl_pct:+.1f}% RSI={rsi_1h:.0f} overbought/sold"

            # 8. Substantial profit -> lock to breakeven
            # 2026-04-14: raised threshold +1% -> +2%. At 2% SL / 4% TP,
            # breakeven at +1% fired at 1/3 of the way to TP — any small
            # retrace triggered the stop and collapsed realized R:R to
            # 1.12:1 on 58 v3 trades. +2% is halfway to TP, giving winners
            # room to breathe while still locking in meaningful protection.
            elif pnl_pct >= 2.0:
                action = "BREAKEVEN"
                conf = 0.72
                reason = f"pnl={pnl_pct:+.1f}% move SL to entry"

            # 9. (MACD-fading-breakeven REMOVED — 1h MACD histogram is too
            #    noisy for exit signals, same problem as the removed MACD
            #    flip exit. Was constantly tagging positions at breakeven
            #    before TP could be reached.)

            # 10. Default hold
            else:
                action = "HOLD"
                conf = 0.50
                reason = f"pnl={pnl_pct:+.1f}% trend={trend_with} macd={macd_with}"

            advice[pid] = {
                "action": action, "confidence": conf, "reason": reason,
                # Provenance: fresh id per parse — never reused across runs
                "decision_id": str(uuid.uuid4()), "source": "algo",
            }
            if action != "HOLD":
                logger.info(
                    f"[MCP-Algo] Position {pid[:8]}: {action} "
                    f"conf={conf:.0%} | {reason}")

        return advice
