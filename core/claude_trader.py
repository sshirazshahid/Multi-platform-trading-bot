"""
core/claude_trader.py — Claude AI-Powered Daily Trading Engine

Every day (or on-demand) this engine:
  1. Fetches live market data for all configured symbols
  2. Computes technical indicators (RSI, ADX, EMA, ATR, BB, volume)
  3. Fetches Fear & Greed index + trending coins
  4. Sends the full market snapshot to the Claude API
  5. Claude responds with structured trade decisions (JSON)
  6. Decisions are executed as paper trades (or live trades in LIVE mode)
  7. A human-readable analysis report is saved to data/claude_analysis/

Claude's analysis covers:
  - Market regime assessment (bull / bear / sideways)
  - Per-coin opportunity rating (Strong Buy / Buy / Hold / Sell / Strong Sell)
  - Entry price, stop-loss, take-profit recommendations
  - Confidence score and reasoning for each decision
  - Overall portfolio allocation suggestion
  - Risk warnings

The Claude model used: claude-sonnet-4-20250514 (Sonnet 4 — best balance of
speed and analytical depth for financial decisions)
"""

from __future__ import annotations

import json
import time
import uuid
import math
import traceback
from datetime  import datetime
from pathlib   import Path

import numpy as np
import pandas as pd
import requests
from loguru import logger


# ── Output paths ─────────────────────────────────────────────────────
ANALYSIS_DIR = Path("data/claude_analysis")
TRADES_FILE  = Path("data/claude_trades.json")
REPORT_FILE  = Path("data/claude_analysis/latest_report.html")

# Claude model for API fallback
CLAUDE_MODEL   = "claude-sonnet-4-20250514"

# Minimum confidence for Claude to place a trade
MIN_CONFIDENCE = 0.60


# ── Indicator helpers (pure numpy/pandas) ────────────────────────────

def _ema(s, p):
    return s.ewm(span=p, adjust=False).mean()

def _rsi(s, p=14):
    d = s.diff()
    g = d.clip(lower=0).ewm(span=p, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(span=p, adjust=False).mean()
    return 100 - 100 / (1 + g / l.replace(0, np.nan))

def _atr(high, low, close, p=14):
    tr = pd.concat([
        high - low,
        (high - close.shift(1)).abs(),
        (low  - close.shift(1)).abs(),
    ], axis=1).max(axis=1)
    return tr.ewm(span=p, adjust=False).mean()

def _adx(high, low, close, p=14):
    tr   = _atr(high, low, close, p)
    up   = high.diff()
    down = -low.diff()
    pdm  = up.where((up > down) & (up > 0), 0.0)
    mdm  = down.where((down > up) & (down > 0), 0.0)
    pdi  = 100 * pdm.ewm(span=p, adjust=False).mean() / tr.replace(0, np.nan)
    mdi  = 100 * mdm.ewm(span=p, adjust=False).mean() / tr.replace(0, np.nan)
    dx   = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return dx.ewm(span=p, adjust=False).mean(), pdi, mdi

def _bbands(s, p=20, std=2.0):
    mid   = s.rolling(p).mean()
    sigma = s.rolling(p).std()
    return mid - std * sigma, mid, mid + std * sigma


# ── Market snapshot builder ───────────────────────────────────────────

def build_coin_snapshot(exchange, symbol: str, market_type: str = "spot") -> dict | None:
    """Fetch OHLCV and compute all indicators for one coin."""
    try:
        raw = exchange.fetch_ohlcv(symbol, "1h", limit=120, market_type=market_type)
        if not raw or len(raw) < 50:
            return None

        df = pd.DataFrame(raw, columns=["ts","open","high","low","close","volume"])
        df.dropna(inplace=True)
        if len(df) < 50:
            return None

        close  = df["close"]
        high   = df["high"]
        low    = df["low"]
        volume = df["volume"]

        adx_s, pdi_s, mdi_s = _adx(high, low, close, 14)
        atr_s  = _atr(high, low, close, 14)
        bb_l, bb_m, bb_u = _bbands(close, 20, 2.0)

        price     = float(close.iloc[-1])
        price_1h  = float(close.iloc[-2])
        price_24h = float(close.iloc[-25]) if len(close) > 25 else price
        price_7d  = float(close.iloc[-169]) if len(close) > 169 else price
        rsi       = float(_rsi(close, 14).iloc[-1])
        adx       = float(adx_s.iloc[-1])
        pdi       = float(pdi_s.iloc[-1])
        mdi       = float(mdi_s.iloc[-1])
        ema9      = float(_ema(close, 9).iloc[-1])
        ema21     = float(_ema(close, 21).iloc[-1])
        ema50     = float(_ema(close, 50).iloc[-1])
        ema200    = float(_ema(close, min(len(df), 200)).iloc[-1])
        atr       = float(atr_s.iloc[-1])
        atr_pct   = atr / max(price, 1e-9)
        bb_upper  = float(bb_u.iloc[-1])
        bb_lower  = float(bb_l.iloc[-1])
        bb_mid    = float(bb_m.iloc[-1])
        bb_width  = (bb_upper - bb_lower) / max(bb_mid, 1e-9)
        vol_ma    = float(volume.rolling(20).mean().iloc[-1])
        vol_ratio = float(volume.iloc[-1]) / max(vol_ma, 1e-9)

        # 1h, 24h, 7d returns
        ret_1h  = (price - price_1h)  / max(price_1h, 1e-9) * 100
        ret_24h = (price - price_24h) / max(price_24h, 1e-9) * 100
        ret_7d  = (price - price_7d)  / max(price_7d, 1e-9) * 100

        # Price position relative to Bollinger Bands
        bb_position = (price - bb_lower) / max(bb_upper - bb_lower, 1e-9)  # 0=lower, 1=upper

        # Trend alignment
        above_ema9   = price > ema9
        above_ema21  = price > ema21
        above_ema50  = price > ema50
        above_ema200 = price > ema200
        ema9_above_21= ema9 > ema21
        bull_signals = sum([above_ema9, above_ema21, above_ema50, above_ema200,
                            ema9_above_21, pdi > mdi, rsi > 50])

        # Simple regime
        if atr_pct > 0.08:     regime = "VOLATILE"
        elif adx >= 25:        regime = "TRENDING"
        elif adx < 20:         regime = "RANGING"
        else:                  regime = "WEAK_TREND"

        # Bias
        if bull_signals >= 5:  bias = "BULLISH"
        elif bull_signals <= 2: bias = "BEARISH"
        else:                  bias = "NEUTRAL"

        return {
            "symbol":      symbol,
            "market_type": market_type,
            "price":       round(price, 6),
            "ret_1h":      round(ret_1h, 2),
            "ret_24h":     round(ret_24h, 2),
            "ret_7d":      round(ret_7d, 2),
            "rsi":         round(rsi, 1),
            "adx":         round(adx, 1),
            "pdi":         round(pdi, 1),
            "mdi":         round(mdi, 1),
            "ema9":        round(ema9, 6),
            "ema21":       round(ema21, 6),
            "ema50":       round(ema50, 6),
            "ema200":      round(ema200, 6),
            "atr":         round(atr, 6),
            "atr_pct":     round(atr_pct * 100, 2),   # as %
            "bb_upper":    round(bb_upper, 6),
            "bb_lower":    round(bb_lower, 6),
            "bb_mid":      round(bb_mid, 6),
            "bb_width_pct":round(bb_width * 100, 2),
            "bb_position": round(bb_position, 3),       # 0=at lower, 1=at upper
            "vol_ratio":   round(vol_ratio, 2),
            "bull_signals":bull_signals,                # out of 7
            "regime":      regime,
            "bias":        bias,
        }
    except Exception as e:
        logger.debug("[ClaudeTrader] Snapshot error for {}: {}".format(symbol, e))
        return None


# ── Claude call (CLI-first, API-fallback) ────────────────────────────

def call_claude(prompt: str, system: str) -> str | None:
    """Call Claude via CLI first, fall back to Anthropic API."""
    from utils.claude_client import call_claude as _call
    return _call(
        prompt,
        system_prompt=system,
        model="sonnet",
        api_model=CLAUDE_MODEL,
        max_tokens=4096,
        timeout=120,
    )


# ── Main engine ───────────────────────────────────────────────────────

class ClaudeTrader:
    """
    Claude AI-powered daily trading engine.

    Call run() once per day (or on-demand from the launcher).
    Claude analyzes the full market snapshot and returns structured
    trade decisions which are executed as paper (or live) trades.
    """

    def __init__(self, dry_run: bool = True):
        from config import TRADING_PAIRS, DRY_RUN
        self.dry_run      = dry_run if dry_run is not None else DRY_RUN
        self.trading_pairs= TRADING_PAIRS
        self._trades: list = self._load_trades()

        logger.info(
            "[ClaudeTrader] Initialized | mode={} | model={}".format(
                "DRY RUN" if self.dry_run else "LIVE", CLAUDE_MODEL
            )
        )

    # ── Public entry point ────────────────────────────────────────────

    def run(self) -> dict:
        """
        Full daily analysis cycle.
        Returns the analysis result dict (also saved to disk).
        """
        logger.info("[ClaudeTrader] Starting daily analysis...")
        started_at = datetime.now()

        # ── 1. Connect exchanges ──────────────────────────────────────
        from exchanges import BinanceClient, MEXCClient
        exchanges = {
            "binance": BinanceClient(),
            "mexc":    MEXCClient(),
        }
        active = {n: ex for n, ex in exchanges.items()
                  if getattr(ex, "_connected", False)}
        if not active:
            logger.error("[ClaudeTrader] No exchanges connected.")
            return {}
        logger.info("[ClaudeTrader] Connected: {}".format(list(active.keys())))

        # ── 2. Build market snapshots ─────────────────────────────────
        snapshots = {}
        for ex_name, exchange in active.items():
            pairs = self.trading_pairs.get(ex_name, {})
            for symbol in pairs.get("spot", []):
                if symbol not in snapshots:
                    snap = build_coin_snapshot(exchange, symbol, "spot")
                    if snap:
                        snapshots[symbol] = snap
                        logger.info(
                            "[ClaudeTrader] {} | {} | RSI={} ADX={} {}".format(
                                symbol, snap["regime"], snap["rsi"],
                                snap["adx"], snap["bias"]
                            )
                        )
                    time.sleep(0.3)

        if not snapshots:
            logger.error("[ClaudeTrader] No market data available.")
            return {}

        # ── 3. Fetch sentiment ────────────────────────────────────────
        sentiment = self._fetch_sentiment()

        # ── 4. Get existing paper positions ──────────────────────────
        open_positions = self._get_open_positions()

        # ── 5. Build Claude prompt ────────────────────────────────────
        system_prompt = self._build_system_prompt()
        user_prompt   = self._build_user_prompt(
            snapshots, sentiment, open_positions
        )

        # ── 6. Ask Claude ─────────────────────────────────────────────
        logger.info("[ClaudeTrader] Sending market data to Claude...")
        response_text = call_claude(user_prompt, system_prompt)

        if not response_text:
            logger.error("[ClaudeTrader] No response from Claude API.")
            return {}

        logger.info("[ClaudeTrader] Claude responded ({} chars)".format(
            len(response_text)))

        # ── 7. Parse decisions ────────────────────────────────────────
        decisions = self._parse_decisions(response_text)
        logger.info(
            "[ClaudeTrader] Parsed {} trade decisions".format(len(decisions))
        )

        # ── 8. Execute trades ─────────────────────────────────────────
        executed_trades = []
        for decision in decisions:
            if decision.get("action") in ("BUY", "SELL") and \
               decision.get("confidence", 0) >= MIN_CONFIDENCE:
                trade = self._execute_decision(decision, active)
                if trade:
                    executed_trades.append(trade)

        # ── 9. Save results ───────────────────────────────────────────
        result = {
            "run_at":        started_at.isoformat(),
            "finished_at":   datetime.now().isoformat(),
            "mode":          "DRY RUN" if self.dry_run else "LIVE",
            "symbols_scanned":len(snapshots),
            "sentiment":     sentiment,
            "decisions":     decisions,
            "executed":      executed_trades,
            "raw_response":  response_text,
        }

        self._save_result(result, snapshots)
        self._export_html_report(result, snapshots)

        logger.info(
            "[ClaudeTrader] Done | {} symbols | {} decisions | {} executed".format(
                len(snapshots), len(decisions), len(executed_trades)
            )
        )
        return result

    # ── Prompt builder ────────────────────────────────────────────────

    def _build_system_prompt(self) -> str:
        return """You are an expert crypto trading analyst with deep knowledge of
technical analysis, market microstructure, and risk management.

You receive a full market snapshot with technical indicators for multiple
crypto assets. Your job is to produce structured trade recommendations.

RULES:
- Only recommend trades with clear technical justification
- Never recommend more than 5 trades per session
- For each trade provide: symbol, action, market_type, direction, entry_price,
  stop_loss, take_profit, confidence (0.0-1.0), timeframe, and reasoning
- SPOT trades: BUY direction only (cannot short spot)
- FUTURES trades: BUY (long) or SELL (short)
- confidence < 0.60 = skip the trade
- In EXTREME FEAR (Fear&Greed < 20): only SHORT futures or no trades
- In EXTREME GREED (Fear&Greed > 80): tighten stop-losses
- Always respect the Risk:Reward ratio >= 2:1

You MUST respond with a JSON object in this exact format:
{
  "market_assessment": "Brief 2-3 sentence overview of current market conditions",
  "overall_bias": "BULLISH | BEARISH | NEUTRAL",
  "risk_level": "LOW | MEDIUM | HIGH | EXTREME",
  "trades": [
    {
      "symbol": "BTC/USDT",
      "action": "BUY | SELL | HOLD | SKIP",
      "market_type": "spot | futures",
      "direction": "long | short",
      "entry_price": 95000.0,
      "stop_loss": 93000.0,
      "take_profit": 100000.0,
      "confidence": 0.78,
      "timeframe": "4h | 1d",
      "reasoning": "Explanation of why this trade makes sense"
    }
  ],
  "skipped_symbols": ["ETH/USDT"],
  "skip_reasons": {"ETH/USDT": "Reason why skipped"},
  "portfolio_advice": "Overall portfolio management advice",
  "warnings": ["Any risk warnings"]
}

ONLY return the JSON object. No other text."""

    def _build_user_prompt(self, snapshots: dict,
                            sentiment: dict,
                            open_positions: list) -> str:
        now = datetime.now().strftime("%Y-%m-%d %H:%M UTC")

        # Format sentiment
        fg    = sentiment.get("fear_greed", 50)
        fg_l  = sentiment.get("fear_greed_label", "Neutral")
        trend = sentiment.get("trending", [])
        chg   = sentiment.get("market_cap_change_24h", 0)
        btc_d = sentiment.get("btc_dominance", 0)

        lines = [
            "CRYPTO MARKET ANALYSIS REQUEST",
            "Time: {}".format(now),
            "",
            "MARKET SENTIMENT:",
            "  Fear & Greed Index: {} ({})".format(fg, fg_l),
            "  Market Cap Change 24h: {:+.2f}%".format(chg),
            "  BTC Dominance: {:.1f}%".format(btc_d),
        ]
        if trend:
            lines.append("  Trending Coins: {}".format(
                ", ".join(trend[:5])))

        lines += ["", "TECHNICAL DATA PER COIN:", "=" * 60]

        for symbol, s in snapshots.items():
            b = s["bias"]
            bias_arrow = "UP" if b == "BULLISH" else ("DOWN" if b == "BEARISH" else "FLAT")
            lines.append(
                "\n{} [{}] [{}]".format(symbol, s["regime"], bias_arrow)
            )
            lines.append(
                "  Price:  ${:,.6f}  |  1h: {:+.2f}%  24h: {:+.2f}%  7d: {:+.2f}%".format(
                    s["price"], s["ret_1h"], s["ret_24h"], s["ret_7d"]
                )
            )
            lines.append(
                "  RSI:    {:.1f}  |  ADX: {:.1f}  (+DI {:.1f} / -DI {:.1f})".format(
                    s["rsi"], s["adx"], s["pdi"], s["mdi"]
                )
            )
            lines.append(
                "  EMA:    9={:.4f}  21={:.4f}  50={:.4f}  200={:.4f}".format(
                    s["ema9"], s["ema21"], s["ema50"], s["ema200"]
                )
            )
            lines.append(
                "  BB:     lower={:.4f}  mid={:.4f}  upper={:.4f}  "
                "width={:.1f}%  pos={:.2f}".format(
                    s["bb_lower"], s["bb_mid"], s["bb_upper"],
                    s["bb_width_pct"], s["bb_position"]
                )
            )
            lines.append(
                "  ATR:    {:.4f} ({:.2f}% of price)  |  "
                "Volume ratio: {:.2f}x avg  |  "
                "Bull signals: {}/7".format(
                    s["atr"], s["atr_pct"], s["vol_ratio"], s["bull_signals"]
                )
            )

        if open_positions:
            lines += ["", "CURRENT OPEN POSITIONS:", "=" * 60]
            for pos in open_positions:
                lines.append(
                    "  {} {} {} @ {:.4f} | SL={:.4f} TP={:.4f}".format(
                        pos.get("side","?").upper(),
                        pos.get("symbol","?"),
                        pos.get("market_type","spot"),
                        pos.get("entry_price", 0),
                        pos.get("stop_loss", 0),
                        pos.get("take_profit", 0),
                    )
                )
        else:
            lines += ["", "CURRENT OPEN POSITIONS: None"]

        mode_line = (
            "IMPORTANT: This is DRY RUN (paper trading). "
            "No real money at risk. Be more aggressive with entry criteria "
            "since this is for learning purposes."
            if self.dry_run
            else
            "IMPORTANT: This is LIVE trading. Real money at risk. "
            "Be conservative and only take the highest conviction trades."
        )
        lines += ["", mode_line, "",
                  "Please analyze all coins and provide trade recommendations."]

        return "\n".join(lines)

    # ── Decision parser ───────────────────────────────────────────────

    def _parse_decisions(self, response_text: str) -> list:
        """Extract and validate trade decisions from Claude's JSON response."""
        try:
            # Strip markdown code fences if present
            text = response_text.strip()
            if text.startswith("```"):
                lines = text.split("\n")
                text  = "\n".join(
                    l for l in lines
                    if not l.startswith("```")
                )

            data = json.loads(text)

            assessment = data.get("market_assessment", "")
            bias       = data.get("overall_bias", "NEUTRAL")
            risk_level = data.get("risk_level", "MEDIUM")
            advice     = data.get("portfolio_advice", "")
            warnings   = data.get("warnings", [])
            trades     = data.get("trades", [])

            logger.info(
                "[ClaudeTrader] Market: {} | Bias: {} | Risk: {}".format(
                    assessment[:80], bias, risk_level
                )
            )
            if advice:
                logger.info("[ClaudeTrader] Advice: {}".format(advice[:100]))
            for w in warnings:
                logger.warning("[ClaudeTrader] WARNING: {}".format(w))

            validated = []
            for t in trades:
                action = t.get("action", "").upper()
                if action not in ("BUY", "SELL"):
                    continue
                conf = float(t.get("confidence", 0))
                if conf < 0.50:
                    continue
                t["action"]     = action
                t["confidence"] = conf
                t["bias"]       = bias
                t["risk_level"] = risk_level
                t["assessment"] = assessment
                validated.append(t)

            return validated

        except json.JSONDecodeError as e:
            logger.error(
                "[ClaudeTrader] JSON parse error: {} | "
                "Response preview: {}...".format(e, response_text[:200])
            )
            return []
        except Exception as e:
            logger.error("[ClaudeTrader] Parse error: {}".format(e))
            return []

    # ── Trade executor ────────────────────────────────────────────────

    def _execute_decision(self, decision: dict, active_exchanges: dict) -> dict | None:
        """Execute one trade decision as a paper (or live) order."""
        symbol      = decision.get("symbol", "")
        action      = decision.get("action", "")
        market_type = decision.get("market_type", "spot")
        direction   = decision.get("direction", "long")
        entry       = float(decision.get("entry_price", 0))
        sl          = float(decision.get("stop_loss",   0))
        tp          = float(decision.get("take_profit", 0))
        conf        = float(decision.get("confidence",  0))
        reasoning   = decision.get("reasoning", "")

        if not symbol or not entry:
            return None

        # Validate: spot cannot short
        if market_type == "spot" and direction == "short":
            logger.warning(
                "[ClaudeTrader] Skipping {} — cannot short on spot".format(symbol)
            )
            return None

        # Validate R:R
        if action == "BUY" and (entry - sl) > 0:
            rr = (tp - entry) / (entry - sl)
            if rr < 1.5:
                logger.warning(
                    "[ClaudeTrader] {} R:R={:.1f} too low — skipped".format(
                        symbol, rr
                    )
                )
                return None
        elif action == "SELL" and (sl - entry) > 0:
            rr = (entry - tp) / (sl - entry)
            if rr < 1.5:
                logger.warning(
                    "[ClaudeTrader] {} SHORT R:R={:.1f} too low — skipped".format(
                        symbol, rr
                    )
                )
                return None

        side = "buy" if action == "BUY" else "sell"

        # Paper trade execution
        trade_id    = "CLAUDE-{}".format(uuid.uuid4().hex[:8].upper())
        start_bal   = 1000.0   # paper wallet
        pos_size_pct= 0.05     # 5% of balance per Claude trade
        leverage    = 2 if market_type == "futures" else 1
        notional    = start_bal * pos_size_pct * leverage
        qty         = notional / entry if entry > 0 else 0

        trade_record = {
            "id":           trade_id,
            "timestamp":    datetime.now().isoformat(),
            "source":       "claude_ai",
            "symbol":       symbol,
            "side":         side,
            "market_type":  market_type,
            "direction":    direction,
            "entry_price":  entry,
            "stop_loss":    sl,
            "take_profit":  tp,
            "quantity":     round(qty, 8),
            "notional":     round(notional, 2),
            "leverage":     leverage,
            "confidence":   conf,
            "reasoning":    reasoning,
            "dry_run":      self.dry_run,
            "status":       "open",
        }

        self._trades.append(trade_record)
        self._save_trades()

        mode = "[DRY RUN]" if self.dry_run else "[LIVE]"
        logger.info(
            "{} CLAUDE TRADE {} | {} {} {} @ {:.4f} | "
            "SL={:.4f} TP={:.4f} qty={:.6f} conf={:.0%}".format(
                mode, trade_id,
                side.upper(), market_type.upper(), symbol,
                entry, sl, tp, qty, conf
            )
        )
        logger.info(
            "  Reasoning: {}".format(reasoning[:120])
        )

        return trade_record

    # ── Helpers ───────────────────────────────────────────────────────

    def _fetch_sentiment(self) -> dict:
        """Fetch Fear & Greed index and global market data."""
        result = {"fear_greed": 50, "fear_greed_label": "Neutral",
                  "market_cap_change_24h": 0.0, "btc_dominance": 50.0,
                  "trending": []}
        try:
            r = requests.get(
                "https://api.alternative.me/fng/?limit=1", timeout=10
            )
            if r.status_code == 200:
                d = r.json()
                v = int(d["data"][0]["value"])
                l = d["data"][0]["value_classification"]
                result["fear_greed"]       = v
                result["fear_greed_label"] = l
        except Exception:
            pass

        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/global", timeout=10
            )
            if r.status_code == 200:
                gd = r.json().get("data", {})
                result["market_cap_change_24h"] = gd.get(
                    "market_cap_change_percentage_24h_usd", 0)
                btc = gd.get("market_cap_percentage", {}).get("btc", 50)
                result["btc_dominance"] = btc
        except Exception:
            pass

        try:
            r = requests.get(
                "https://api.coingecko.com/api/v3/search/trending",
                timeout=10
            )
            if r.status_code == 200:
                coins = r.json().get("coins", [])
                result["trending"] = [
                    c["item"]["symbol"].upper() for c in coins[:5]
                ]
        except Exception:
            pass

        return result

    def _get_open_positions(self) -> list:
        """Load currently open paper positions."""
        p = Path("data/positions.json")
        if not p.exists():
            return []
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
            return d.get("open", [])
        except Exception:
            return []

    def _load_trades(self) -> list:
        if TRADES_FILE.exists():
            try:
                return json.loads(TRADES_FILE.read_text(encoding="utf-8"))
            except Exception:
                pass
        return []

    def _save_trades(self):
        TRADES_FILE.parent.mkdir(parents=True, exist_ok=True)
        TRADES_FILE.write_text(
            json.dumps(self._trades, indent=2), encoding="utf-8"
        )

    def _save_result(self, result: dict, snapshots: dict):
        ANALYSIS_DIR.mkdir(parents=True, exist_ok=True)
        ts   = datetime.now().strftime("%Y-%m-%d_%H-%M")
        path = ANALYSIS_DIR / "analysis_{}.json".format(ts)
        out  = dict(result)
        out["snapshots"] = snapshots
        path.write_text(json.dumps(out, indent=2), encoding="utf-8")
        logger.info("[ClaudeTrader] Saved: {}".format(path))

    def _export_html_report(self, result: dict, snapshots: dict):
        """Write a styled HTML report of Claude's analysis."""
        try:
            decisions  = result.get("decisions", [])
            executed   = result.get("executed", [])
            sentiment  = result.get("sentiment", {})
            assessment = ""
            bias       = "NEUTRAL"
            risk_lev   = "MEDIUM"
            advice     = ""
            if decisions:
                assessment = decisions[0].get("assessment", "")
                bias       = decisions[0].get("bias", "NEUTRAL")
                risk_lev   = decisions[0].get("risk_level", "MEDIUM")

            fg    = sentiment.get("fear_greed", 50)
            fg_l  = sentiment.get("fear_greed_label", "Neutral")
            chg   = sentiment.get("market_cap_change_24h", 0)
            btc_d = sentiment.get("btc_dominance", 50)

            bias_color = (
                "#4ade80" if bias == "BULLISH" else
                "#f87171" if bias == "BEARISH" else "#fbbf24"
            )
            risk_color = (
                "#4ade80" if risk_lev == "LOW" else
                "#fbbf24" if risk_lev == "MEDIUM" else
                "#f97316" if risk_lev == "HIGH" else "#f87171"
            )
            fg_color = (
                "#4ade80" if fg >= 60 else
                "#f87171" if fg <= 30 else "#fbbf24"
            )

            # Trades table
            trade_rows = ""
            for t in decisions:
                action = t.get("action", "")
                conf   = t.get("confidence", 0)
                sym    = t.get("symbol", "?")
                mt     = t.get("market_type", "spot")
                entry  = t.get("entry_price", 0)
                sl_p   = t.get("stop_loss",   0)
                tp_p   = t.get("take_profit", 0)
                reason = t.get("reasoning",   "")[:100]
                a_col  = "#4ade80" if action == "BUY" else "#f87171"
                was_exec = any(e.get("symbol") == sym for e in executed)
                exec_badge = (
                    '<span style="color:#4ade80;font-size:0.75em">EXECUTED</span>'
                    if was_exec else
                    '<span style="color:#64748b;font-size:0.75em">SKIPPED</span>'
                )
                trade_rows += (
                    "<tr>"
                    "<td>{}</td>"
                    "<td style='color:{}'>{}</td>"
                    "<td>{}</td>"
                    "<td>{:.4f}</td>"
                    "<td>{:.4f}</td>"
                    "<td>{:.4f}</td>"
                    "<td>{:.0%}</td>"
                    "<td>{}</td>"
                    "<td style='color:#94a3b8;font-size:0.8em'>{}</td>"
                    "</tr>".format(
                        sym, a_col, action, mt,
                        entry, sl_p, tp_p, conf,
                        exec_badge, reason
                    )
                )

            # Snapshot table
            snap_rows = ""
            for sym, s in snapshots.items():
                b = s["bias"]
                b_col = (
                    "#4ade80" if b == "BULLISH" else
                    "#f87171" if b == "BEARISH" else "#fbbf24"
                )
                r_col = (
                    "#f97316" if s["regime"] == "VOLATILE" else
                    "#38bdf8" if s["regime"] == "TRENDING" else "#94a3b8"
                )
                snap_rows += (
                    "<tr>"
                    "<td>{}</td>"
                    "<td>{:.4f}</td>"
                    "<td style='color:{}'>{:+.2f}%</td>"
                    "<td style='color:{}'>{:+.2f}%</td>"
                    "<td>{:.1f}</td>"
                    "<td>{:.1f}</td>"
                    "<td style='color:{}'>{}</td>"
                    "<td style='color:{}'>{}</td>"
                    "<td>{:.2f}x</td>"
                    "</tr>".format(
                        sym, s["price"],
                        "#4ade80" if s["ret_1h"] >= 0 else "#f87171", s["ret_1h"],
                        "#4ade80" if s["ret_24h"] >= 0 else "#f87171", s["ret_24h"],
                        s["rsi"], s["adx"],
                        r_col, s["regime"],
                        b_col, b,
                        s["vol_ratio"],
                    )
                )

            run_time = result.get("run_at", "")[:16]
            mode     = result.get("mode", "DRY RUN")

            html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta http-equiv="refresh" content="300">
<title>Claude AI Trading Analysis</title>
<style>
  body  {{ background:#0f172a; color:#e2e8f0; font-family:monospace; padding:24px; }}
  h1    {{ color:#38bdf8; }}
  h2    {{ color:#7dd3fc; border-bottom:1px solid #334155; padding-bottom:6px; margin-top:28px; }}
  .meta {{ color:#64748b; font-size:0.85em; }}
  .card {{ background:#1e293b; border-radius:8px; padding:16px 20px; margin:12px 0; }}
  .assessment {{ color:#e2e8f0; line-height:1.6; }}
  table {{ border-collapse:collapse; width:100%; margin-top:12px; }}
  th,td {{ padding:8px 12px; text-align:left; border-bottom:1px solid #1e293b; font-size:0.9em; }}
  th    {{ color:#94a3b8; font-size:0.8em; text-transform:uppercase; }}
  tr:hover {{ background:#1e293b; }}
  .badge-dry  {{ color:#a78bfa; font-size:0.8em; }}
  .badge-live {{ color:#f87171; font-size:0.8em; }}
</style>
</head>
<body>
<h1>Claude AI Trading Analysis</h1>
<div class="meta">
  Run: {run_time} &nbsp;|&nbsp;
  Mode: <span class="{'badge-dry' if 'DRY' in mode else 'badge-live'}">{mode}</span> &nbsp;|&nbsp;
  Model: {model}
</div>

<div class="card" style="border-left:4px solid {bias_color}">
  <strong>Market Assessment</strong><br>
  <div class="assessment">{assessment}</div>
  <div style="margin-top:8px">
    Overall Bias: <span style="color:{bias_color}">{bias}</span>
    &nbsp;|&nbsp; Risk Level: <span style="color:{risk_color}">{risk_lev}</span>
    &nbsp;|&nbsp; Fear & Greed: <span style="color:{fg_color}">{fg} ({fg_l})</span>
    &nbsp;|&nbsp; Mkt 24h: <span style="color:{'#4ade80' if chg >= 0 else '#f87171'}">{chg:+.2f}%</span>
    &nbsp;|&nbsp; BTC Dom: {btc_d:.1f}%
  </div>
</div>

<h2>Trade Decisions ({n_trades} total, {n_exec} executed)</h2>
<table>
  <tr>
    <th>Symbol</th><th>Action</th><th>Type</th>
    <th>Entry</th><th>SL</th><th>TP</th>
    <th>Conf</th><th>Status</th><th>Reasoning</th>
  </tr>
  {trade_rows}
</table>

<h2>Market Snapshot</h2>
<table>
  <tr>
    <th>Symbol</th><th>Price</th><th>1h</th><th>24h</th>
    <th>RSI</th><th>ADX</th><th>Regime</th><th>Bias</th><th>Volume</th>
  </tr>
  {snap_rows}
</table>

<p style="color:#334155; margin-top:40px; font-size:0.8em;">
  Auto-refreshes every 5 min &nbsp;|&nbsp; Claude analysis: {model}
</p>
</body>
</html>""".format(
                run_time=run_time, mode=mode, model=CLAUDE_MODEL,
                bias_color=bias_color, assessment=assessment or "No assessment.",
                bias=bias, risk_color=risk_color, risk_lev=risk_lev,
                fg_color=fg_color, fg=fg, fg_l=fg_l, chg=chg, btc_d=btc_d,
                n_trades=len(decisions), n_exec=len(executed),
                trade_rows=trade_rows or "<tr><td colspan='9'>No trades recommended</td></tr>",
                snap_rows=snap_rows,
            )

            REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
            REPORT_FILE.write_text(html, encoding="utf-8")
            logger.info("[ClaudeTrader] HTML report: {}".format(REPORT_FILE.resolve()))
        except Exception as e:
            logger.debug("[ClaudeTrader] HTML export error: {}".format(e))
