"""
core/strategy_selector.py — Multi-Timeframe Scanner

‼ NOT IN THE LIVE BOT_ENGINE PATH (verified 2026-04-28). The single-profile
bot launched by `main.py` → `BotEngine` does NOT consult this module —
entries come from `_claude_portfolio_cycle` (MCP Brain → Claude AI) or its
`systematic_v3_1` algorithmic fallback. The ONLY caller of StrategySelector
is `core/multi_profile_runner.py` (separate entry point at
`multi_profile_main.py`) which runs 3 risk profiles simultaneously.

Phase 10.1 added `_DISABLED_LIVE_STRATEGIES = {"multitf_futures"}` here
to gate killed strategies. That gate is real protection for the
multi_profile_runner path; the main bot doesn't reach it.

If you're touching this file: make sure changes are still consistent
with what multi_profile_runner expects. Run `pytest tests/` and
`python multi_profile_main.py --report` after edits.

────────────────────────────────────────────────────────────────────────

Produces explicit Spot Long + Futures Long + Futures Short opportunities.

KEY RULES:
  SPOT:    BUY (long) ONLY. Short positions on spot are impossible.
  FUTURES: BUY (long) AND SELL (short). Leverage applies.

All "sell" direction on spot is filtered out before returning opportunities.

FIX: supertrend_spot now only fires when:
  - ADX >= 35 AND full_bull (all key TFs agree bullish), OR
  - ADX >= 28 AND full_bull in the moderate trending block
  This prevents spot LONGs in downtrends / mixed signals which were 0% WR.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import pandas as pd
from loguru import logger

# ── Timeframe weights ─────────────────────────────────────────────────
TF_WEIGHTS = {"1d": 5, "4h": 4, "1h": 3, "15m": 2, "1m": 1}
TIMEFRAMES  = list(TF_WEIGHTS.keys())

TF_CACHE_TTL = {
    "1d": 3600 * 4, "4h": 3600, "1h": 1800, "15m": 900, "1m": 120,
}
TF_LOOKBACK = {"1d": 60, "4h": 80, "1h": 100, "15m": 100, "1m": 60}

FUTURES_ADX_MIN = 25   # Lowered from 30 — more futures entries in moderate trends

# 2026-04-27 (Phase 10): kill multitf_futures emissions in the live path. The
# strategy ran in parallel with claude_portfolio's futures direction calls and
# accumulated -$11.33 across 58 trades over 30 days (mean -$0.20, 48% WR per
# warehouse attribution). Set to False if you ever want to re-enable for live.
# Backtest entries (backtest.py / auto_backtest.py) import strategies.multi_tf
# directly and bypass this gate, so research replay still works.
_DISABLED_LIVE_STRATEGIES = {"multitf_futures"}
SPOT_ADX_MIN    = 25   # Lowered from 33 — more spot entries (strategies handle quality)


# ── Pure-pandas indicators (imported from shared module) ──────────────
from utils.indicators import adx as _adx
from utils.indicators import atr as _atr
from utils.indicators import bbands as _bbands
from utils.indicators import ema as _ema
from utils.indicators import rsi as _rsi

# ── Data models ───────────────────────────────────────────────────────

@dataclass
class TFSnapshot:
    timeframe:  str
    price:      float
    rsi:        float
    adx:        float
    pdi:        float
    mdi:        float
    ema9:       float
    ema21:      float
    ema50:      float
    ema200:     float
    atr_pct:    float
    bb_width:   float
    vol_ratio:  float
    direction:  str    # "bull" | "bear" | "neutral"
    regime:     str    # "trending" | "ranging" | "volatile" | "weak_trend"
    weight:     int


@dataclass
class TradeOpportunity:
    """
    A single actionable trade signal for one symbol on one market type.

    direction:
      "buy"  = open a long position (spot or futures)
      "sell" = open a short position (FUTURES ONLY — never spot)
    """
    symbol:         str
    exchange:       str
    strategy:       str
    market_type:    str    # "spot" or "futures"
    direction:      str    # "buy" (long) or "sell" (short — futures only)
    confidence:     float  # 0.0 – 1.0
    regime:         str
    futures_symbol: str    # e.g. "BTC/USDT:USDT"; empty for spot
    adx_1h:         float  = 0.0
    rsi_1h:         float  = 50.0
    reason:         str    = ""
    timestamp:      float  = field(default_factory=time.time)

    @property
    def trade_symbol(self) -> str:
        if self.market_type == "futures" and self.futures_symbol:
            return self.futures_symbol
        return self.symbol

    @property
    def label(self) -> str:
        direction_label = "LONG" if self.direction == "buy" else "SHORT"
        return f"{self.market_type.upper()} {direction_label} {self.symbol}"


CoinAnalysis = TradeOpportunity   # legacy alias


# ── Strategy Selector ─────────────────────────────────────────────────

class StrategySelector:

    def __init__(self):
        import threading
        self._cache: dict[tuple, tuple]    = {}
        self._tf_cache: dict[tuple, tuple] = {}
        self._lock = threading.Lock()  # Thread safety for background SignalCache

    # ── Public API ────────────────────────────────────────────────────

    def analyze(self, exchange, symbol: str,
                futures_symbol: str = "",
                market_type: str = "spot") -> list[TradeOpportunity]:
        key    = (exchange.name, symbol)
        with self._lock:
            cached = self._cache.get(key)
            if cached and (time.time() - cached[1]) < 600:  # 10 min (was 30 min — too stale)
                return cached[0]

        if not futures_symbol:
            futures_symbol = _spot_to_futures(symbol)

        snapshots = {}
        for tf in TIMEFRAMES:
            snap = self._scan_timeframe(exchange, symbol, tf, market_type)
            if snap:
                snapshots[tf] = snap

        if not snapshots:
            return []

        opps = self._build_opportunities(
            symbol, exchange.name, futures_symbol, snapshots)

        # CRITICAL: filter out any spot sell signals before caching
        opps = [o for o in opps
                if not (o.market_type == "spot" and o.direction == "sell")]

        # Deduplicate: keep highest confidence per (strategy, market_type, direction)
        seen = {}
        for opp in opps:
            dedup_key = (opp.strategy, opp.market_type, opp.direction)
            if dedup_key not in seen or opp.confidence > seen[dedup_key].confidence:
                seen[dedup_key] = opp
        opps = list(seen.values())

        with self._lock:
            self._cache[key] = (opps, time.time())

        for opp in opps:
            logger.info(
                f"[Selector] {opp.label} | strat={opp.strategy} conf={opp.confidence:.0%} regime={opp.regime} | {opp.reason}")
        return opps

    def analyze_batch(self, exchange, symbols: list,
                      futures_map: dict = None,
                      market_type: str = "spot") -> dict[str, list[TradeOpportunity]]:
        results = {}
        fm = futures_map or {}
        for symbol in symbols:
            try:
                opps = self.analyze(exchange, symbol, fm.get(symbol, ""),
                                    market_type=market_type)
                if opps:
                    results[symbol] = opps
                time.sleep(0.05)  # Reduced from 0.15s — was adding 7.5s+ per scan
            except Exception as e:
                logger.debug(f"[Selector] {symbol} failed: {e}")
        return results

    def clear_cache(self, symbol: str = None):
        with self._lock:
            if symbol:
                self._cache    = {k: v for k, v in self._cache.items()    if k[1] != symbol}
                self._tf_cache = {k: v for k, v in self._tf_cache.items() if k[1] != symbol}
            else:
                self._cache.clear()
                self._tf_cache.clear()

    def summary_table(self, results: dict) -> str:
        lines = [
            "{:<16} {:<8} {:<6} {:<26} {:>5} {:>5} {:>5} {}".format(
                "Symbol", "Market", "Dir", "Strategy", "Conf", "ADX", "RSI", "Regime"),
            "-" * 88,
        ]
        all_opps = []
        for sym, opps in results.items():
            for opp in opps:
                all_opps.append(opp)
        all_opps.sort(key=lambda x: x.confidence, reverse=True)

        for opp in all_opps:
            dir_label = "LONG " if opp.direction == "buy" else "SHORT"
            lines.append(
                f"{opp.symbol:<16} {opp.market_type:<8} {dir_label:<6} {opp.strategy:<26} {opp.confidence:>4.0%} {opp.adx_1h:>5.1f} {opp.rsi_1h:>5.1f} {opp.regime}")
        return "\n".join(lines)

    # ── Per-timeframe scan ────────────────────────────────────────────

    def _scan_timeframe(self, exchange, symbol: str, tf: str,
                        market_type: str = "spot") -> TFSnapshot | None:
        key    = (exchange.name, symbol, tf)
        with self._lock:
            cached = self._tf_cache.get(key)
            if cached and (time.time() - cached[1]) < TF_CACHE_TTL.get(tf, 900):
                return cached[0]

        try:
            raw = exchange.fetch_ohlcv(symbol, tf, limit=TF_LOOKBACK[tf],
                                       market_type=market_type)
            if not raw or len(raw) < 30:
                return None

            df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
            df.dropna(inplace=True)
            if len(df) < 30:
                return None

            close  = df["close"]
            high   = df["high"]
            low    = df["low"]
            volume = df["volume"]

            adx_s, pdi_s, mdi_s = _adx(high, low, close, 14)
            atr_s               = _atr(high, low, close, 14)
            bb_l, bb_m, bb_u    = _bbands(close, 20, 2.0)

            price      = float(close.iloc[-1])
            rsi_val    = float(_rsi(close, 14).iloc[-1])
            adx_val    = float(adx_s.iloc[-1])
            pdi_val    = float(pdi_s.iloc[-1])
            mdi_val    = float(mdi_s.iloc[-1])
            ema9_val   = float(_ema(close, 9).iloc[-1])
            ema21_val  = float(_ema(close, 21).iloc[-1])
            ema50_val  = float(_ema(close, 50).iloc[-1])
            # Use EMA200 only when enough data; fallback to EMA100 or neutral
            if len(df) >= 200:
                ema200_val = float(_ema(close, 200).iloc[-1])
            elif len(df) >= 100:
                ema200_val = float(_ema(close, 100).iloc[-1])
            else:
                ema200_val = price  # insufficient data — treat as neutral
            atr_pct    = float(atr_s.iloc[-1]) / max(price, 1e-9)
            bb_m_val   = float(bb_m.iloc[-1])
            bb_width   = float(
                (bb_u.iloc[-1] - bb_l.iloc[-1]) / max(bb_m_val, 1e-9))
            vol_ma     = float(volume.rolling(20).mean().iloc[-1])
            vol_ratio  = float(volume.iloc[-1]) / max(vol_ma, 1e-9)

            # Direction vote
            bull = bear = 0
            if pdi_val > mdi_val:    bull += 1
            else:                    bear += 1
            if price > ema50_val:    bull += 1
            else:                    bear += 1
            if price > ema200_val:   bull += 1
            else:                    bear += 1
            if ema9_val > ema21_val: bull += 1
            else:                    bear += 1
            if rsi_val > 52:         bull += 1
            elif rsi_val < 48:       bear += 1

            # Was 4/5 votes required — too strict, most trending markets
            # with 3-vote agreement were classified "neutral" and missed
            direction = "bull" if bull >= 3 else ("bear" if bear >= 3 else "neutral")

            if atr_pct > 0.08:
                regime = "volatile"
            elif adx_val >= 25:
                regime = "trending"
            elif adx_val < 20:
                regime = "ranging"
            else:
                regime = "weak_trend"

            snap = TFSnapshot(
                timeframe=tf, price=price, rsi=rsi_val,
                adx=adx_val, pdi=pdi_val, mdi=mdi_val,
                ema9=ema9_val, ema21=ema21_val,
                ema50=ema50_val, ema200=ema200_val,
                atr_pct=atr_pct, bb_width=bb_width,
                vol_ratio=vol_ratio, direction=direction,
                regime=regime, weight=TF_WEIGHTS[tf],
            )
            with self._lock:
                self._tf_cache[key] = (snap, time.time())
            return snap

        except Exception as e:
            logger.debug(f"[Selector] {symbol} {tf} scan failed: {e}")
            return None

    # ── Opportunity builder ───────────────────────────────────────────

    def _build_opportunities(
        self, symbol: str, exchange_name: str,
        futures_symbol: str, snapshots: dict,
    ) -> list[TradeOpportunity]:

        total_w        = sum(s.weight for s in snapshots.values())
        bull_score     = sum(s.weight for s in snapshots.values() if s.direction == "bull")
        bear_score     = sum(s.weight for s in snapshots.values() if s.direction == "bear")
        trend_score    = sum(s.weight for s in snapshots.values() if s.regime == "trending")
        range_score    = sum(s.weight for s in snapshots.values() if s.regime == "ranging")
        volatile_score = sum(s.weight for s in snapshots.values() if s.regime == "volatile")

        adx_avg  = sum(s.adx * s.weight for s in snapshots.values()) / max(total_w, 1)
        rsi_avg  = sum(s.rsi * s.weight for s in snapshots.values()) / max(total_w, 1)
        atr_avg  = sum(s.atr_pct * s.weight for s in snapshots.values()) / max(total_w, 1)
        vol_avg  = sum(s.vol_ratio * s.weight for s in snapshots.values()) / max(total_w, 1)
        bb_avg   = sum(s.bb_width * s.weight for s in snapshots.values()) / max(total_w, 1)

        bull_pct     = bull_score    / max(total_w, 1)
        bear_pct     = bear_score    / max(total_w, 1)
        trend_pct    = trend_score   / max(total_w, 1)
        range_pct    = range_score   / max(total_w, 1)
        volatile_pct = volatile_score / max(total_w, 1)

        snap_1h = (snapshots.get("1h") or snapshots.get("4h")
                   or next(iter(snapshots.values())))

        def _opp(strategy, market_type, direction, confidence, reason,
                 regime="trending"):
            # CRITICAL: never create a spot sell opportunity
            if market_type == "spot" and direction == "sell":
                return None
            # Phase 10 kill list — see _DISABLED_LIVE_STRATEGIES at module top.
            if strategy in _DISABLED_LIVE_STRATEGIES:
                return None
            fsym = futures_symbol if market_type == "futures" else ""
            return TradeOpportunity(
                symbol=symbol, exchange=exchange_name,
                strategy=strategy, market_type=market_type,
                direction=direction, confidence=round(confidence, 3),
                regime=regime, futures_symbol=fsym,
                adx_1h=snap_1h.adx, rsi_1h=snap_1h.rsi,
                reason=reason,
            )

        # ── 1. Skip volatile ──────────────────────────────────────────
        # Was 0.60 — killed ALL signals during high-vol periods which are
        # crypto's most profitable (strongest trends, biggest breakouts)
        if volatile_pct >= 0.85:
            return []

        opps = []
        full_bull = False  # Default — set True only when ALL TFs agree bullish
        full_bear = False

        # ── 2. Trending market ────────────────────────────────────────
        if trend_pct >= 0.55 and adx_avg >= 22:
            key_tfs    = [tf for tf in ["4h","1h","15m"] if tf in snapshots]
            bull_agree = sum(1 for tf in key_tfs if snapshots[tf].direction == "bull")
            bear_agree = sum(1 for tf in key_tfs if snapshots[tf].direction == "bear")
            full_bull  = (bull_agree == len(key_tfs) and len(key_tfs) >= 2)
            full_bear  = (bear_agree == len(key_tfs) and len(key_tfs) >= 2)

            if adx_avg >= 35 and max(bull_pct, bear_pct) >= 0.7:
                if bull_pct > bear_pct:
                    conf   = min(0.95, trend_pct * adx_avg / 40)
                    reason = f"Very strong uptrend ADX={adx_avg:.1f} {trend_pct:.0%} TFs"
                    # supertrend_spot KILLED — 0% WR across 8+ trades historically.
                    # supertrend_futures KILLED 2026-04-20 — 39 trades at 41% WR,
                    # -$45.18 cumulative PnL per warehouse.sqlite. Fallback path
                    # (full_bull=False) lowered entry conviction too far; only
                    # emit futures opportunities when multi-TF is actually full.
                    if full_bull:
                        o = _opp("trend_spot", "spot", "buy", conf * 0.9, reason)
                        if o: opps.append(o)
                        o = _opp("multitf_futures", "futures", "buy", conf, reason)
                        if o: opps.append(o)
                else:
                    conf   = min(0.95, trend_pct * adx_avg / 40)
                    reason = f"Very strong downtrend ADX={adx_avg:.1f} — SHORT"
                    if full_bear:
                        o = _opp("multitf_futures", "futures", "sell", conf, reason)
                        if o: opps.append(o)

            elif adx_avg >= 22:
                if bull_pct > bear_pct and bull_pct >= 0.5:
                    conf   = min(0.85, trend_pct * 0.9)
                    reason = f"Uptrend ADX={adx_avg:.1f} RSI={rsi_avg:.1f}"
                    # supertrend_spot KILLED — use trend_spot (has ADX + R:R filters)
                    if full_bull and adx_avg >= SPOT_ADX_MIN:
                        o = _opp("trend_spot", "spot", "buy", conf, reason)
                        if o: opps.append(o)
                    if adx_avg >= FUTURES_ADX_MIN and full_bull:
                        o = _opp("multitf_futures", "futures", "buy", min(0.85, conf*1.05), reason)
                        if o: opps.append(o)

                elif bear_pct > bull_pct and bear_pct >= 0.5:
                    conf   = min(0.80, trend_pct * 0.85)
                    reason = f"Downtrend ADX={adx_avg:.1f} — Futures SHORT"
                    if adx_avg >= FUTURES_ADX_MIN and full_bear:
                        o = _opp("multitf_futures", "futures", "sell", conf, reason)
                        if o: opps.append(o)

            if full_bull and adx_avg >= 25:
                # Strong alignment — add high-confidence entries (don't clear existing)
                conf   = 0.90
                reason = f"4h+1h+15m ALL BULLISH ADX={adx_avg:.1f}"
                o = _opp("trend_spot", "spot",    "buy", conf*0.9, reason)
                if o: opps.append(o)
                o = _opp("multitf_futures", "futures", "buy", conf,     reason)
                if o: opps.append(o)

            elif full_bear and adx_avg >= 25:
                conf   = 0.90
                reason = f"4h+1h+15m ALL BEARISH ADX={adx_avg:.1f} — SHORT"
                o = _opp("multitf_futures", "futures", "sell", conf, reason)
                if o: opps.append(o)

            # DON'T return here — also check ranging/spot strategies below
            # Old code had 'return opps' which killed all spot signals

        # ── 3. Ranging + Spot Strategies ─────────────────────────────
        # Lowered threshold: also trigger when ADX < 25 (not just < 20)
        # Fixed: was `adx_avg < 25` which triggered ranging block even in trending
        # markets where adx_avg was 24.9. Changed to adx_avg < 20 (clearer ranging signal)
        if range_pct >= 0.35 or adx_avg < 20 or trend_pct < 0.35:
            if bb_avg < 0.04:
                o = _opp("grid_spot", "spot", "buy", 0.75,
                          f"BB squeeze {bb_avg*100:.1f}% ADX={adx_avg:.1f} — Grid",
                          regime="ranging")
                if o: opps.append(o)
            else:
                if rsi_avg < 42:
                    # Confidence: base 0.55 + bonus for deeper oversold
                    conf_mr = min(0.78, 0.55 + (42 - rsi_avg) * 0.015)
                    o = _opp("mean_reversion_spot", "spot", "buy",
                             conf_mr,
                             f"Ranging RSI={rsi_avg:.1f} oversold",
                             regime="ranging")
                    if o: opps.append(o)
                elif rsi_avg > 58:
                    # Overbought in ranging — futures short only (no spot short)
                    if futures_symbol and adx_avg >= 18:
                        conf_mr = min(0.65, 0.50 + (rsi_avg - 58) * 0.01)
                        o = _opp("mean_reversion_spot", "futures", "sell",
                                 conf_mr,
                                 "Ranging overbought — Futures SHORT",
                                 regime="ranging")
                        if o: opps.append(o)
            # Don't return early — check more sections

        # ── 3b. Bear-market spot: Mean reversion on deeply oversold ──
        if rsi_avg < 28 and bear_pct > 0.5:
            o = _opp("mean_reversion_spot", "spot", "buy",
                     min(0.70, 0.50 + (30 - rsi_avg) * 0.02),
                     f"Deeply oversold RSI={rsi_avg:.1f} — MR bounce",
                     regime="ranging")
            if o: opps.append(o)

        # ── 3c. Grid spot for tight-range coins ─────────────────
        if adx_avg < 20 and atr_avg < 0.020:
            o = _opp("grid_spot", "spot", "buy", 0.68,
                     f"Low vol ADX={adx_avg:.1f} ATR={atr_avg * 100:.2f}% — Grid",
                     regime="ranging")
            if o: opps.append(o)

        # ── 4. Volume breakout ────────────────────────────────────────
        if vol_avg >= 2.0 and adx_avg >= 18:
            if bull_pct > bear_pct:
                # Volume breakout spot LONG requires full_bull to avoid false breakouts
                if full_bull:
                    o = _opp("trend_spot", "spot", "buy",
                             min(0.70, vol_avg*0.28),
                             f"Volume spike {vol_avg:.1f}x ADX={adx_avg:.1f} — Breakout",
                             regime="breakout")
                    if o: opps.append(o)
            else:
                # Volume-spike short requires full_bear alignment, mirroring the
                # full_bull guard on the long side. supertrend_futures label
                # retired 2026-04-20 (see data/warehouse.sqlite stats).
                if adx_avg >= FUTURES_ADX_MIN and full_bear:
                    o = _opp("multitf_futures", "futures", "sell",
                             min(0.65, vol_avg*0.25),
                             f"Volume spike {vol_avg:.1f}x — Breakout SHORT",
                             regime="breakout")
                    if o: opps.append(o)
            # Don't return early

        # ── 5. DCA spot — only in clear bull conditions with decent confidence ──
        # REMOVED low-conviction entries (0.40-0.45) that dragged win rate down.
        # DCA only fires when bull consensus is strong enough to be meaningful.
        if bull_pct >= 0.6 and adx_avg >= 20 and rsi_avg > 40:
            o = _opp("dca_spot", "spot", "buy", 0.55,
                     f"Strong bull {bull_pct:.0%} ADX={adx_avg:.1f} — DCA",
                     regime="weak")
            if o: opps.append(o)

        # ── 6. Scalping spot — quick entries on momentum + BB signals ──
        # Fires in ranging/weak markets where bigger strategies don't trigger.
        # Scalping uses tight SL/TP so it works even in choppy conditions.
        if adx_avg < 30 and atr_avg < 0.04:
            if rsi_avg < 40 and vol_avg >= 0.8:
                o = _opp("scalping_spot", "spot", "buy",
                         min(0.65, 0.50 + (40 - rsi_avg) * 0.01),
                         f"Scalp oversold RSI={rsi_avg:.1f} vol={vol_avg:.1f}x",
                         regime="ranging")
                if o: opps.append(o)
            elif rsi_avg > 60 and vol_avg >= 0.8 and futures_symbol:
                o = _opp("scalping_spot", "futures", "sell",
                         min(0.60, 0.45 + (rsi_avg - 60) * 0.01),
                         f"Scalp overbought RSI={rsi_avg:.1f} — SHORT",
                         regime="ranging")
                if o: opps.append(o)

        return opps


# ── Utilities ─────────────────────────────────────────────────────────

def _spot_to_futures(spot_symbol: str) -> str:
    if ":" in spot_symbol:
        return spot_symbol
    if "/USDT" in spot_symbol:
        return spot_symbol + ":USDT"
    return ""


def build_futures_map(trading_pairs: dict) -> dict:
    result = {}
    for ex_name, types in trading_pairs.items():
        spot_list    = types.get("spot",    [])
        futures_list = types.get("futures", [])
        for spot_sym in spot_list:
            base = spot_sym.split("/")[0]
            for fut_sym in futures_list:
                if fut_sym.startswith(base + "/"):
                    result[spot_sym] = fut_sym
                    break
            if spot_sym not in result:
                result[spot_sym] = _spot_to_futures(spot_sym)
    return result
