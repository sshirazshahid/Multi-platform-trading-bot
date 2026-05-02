"""
core/spot_manager.py — Spot Portfolio Manager

Tracks existing spot holdings across exchanges, evaluates each holding
for HOLD / SCALE_OUT / SELL / HEDGE decisions using trend + momentum
analysis. Fully autonomous — executes sell/hedge actions without approval.
"""

import json
import time
from pathlib import Path
from dataclasses import dataclass, asdict
from loguru import logger

try:
    from config import (
        DRY_RUN, SPOT_PORTFOLIO as CFG,
        CAPITAL_ALLOCATION,
    )
except ImportError:
    DRY_RUN = True
    CFG = {"enabled": True, "scan_interval_min": 30}
    CAPITAL_ALLOCATION = {}


STATE_FILE = Path("data/spot_portfolio.json")
RECOMMENDATIONS_FILE = Path("data/spot_recommendations.jsonl")


@dataclass
class HoldingInfo:
    coin: str
    exchange: str
    balance: float
    avg_cost: float
    current_price: float
    unrealized_pnl: float
    unrealized_pnl_pct: float
    peak_price: float
    last_updated: float


class SpotPortfolioManager:

    def __init__(self, exchanges: dict, risk_manager=None, mcp_brain=None):
        self._exchanges = exchanges
        self._risk = risk_manager
        self._mcp_brain = mcp_brain  # optional — used by score_spot_candidate
        self._holdings: dict[str, dict[str, HoldingInfo]] = {}
        self._state_file = STATE_FILE
        self._load_state()

    def score_spot_candidate(
        self,
        *,
        symbol: str,
        feats: dict,
        rule_score: float = 65.0,
    ) -> dict:
        """Calibrated p_win for a spot entry candidate.

        Mirrors `MCPBrain.score_via_model(market_type='spot')`. When no spot
        model has been promoted, returns the rule-only sigmoid fallback so
        the caller can still apply a soft threshold.

        Args:
            symbol:     e.g. 'BTC/USDT'.
            feats:      flat dict matching the trainer's FEATURE_KEYS.
            rule_score: long-only spot rule score (0-100). Plug in your own
                        deterministic spot scorer here.

        Returns: same shape as `MCPBrain.score_via_model` —
            {p_win_lr, p_win_gbm, p_win_ensemble, model_version}.
        """
        if self._mcp_brain is None:
            from core.mcp_brain import _sigmoid
            return {
                "p_win_lr":       float("nan"),
                "p_win_gbm":      float("nan"),
                "p_win_ensemble": _sigmoid((float(rule_score) - 65.0) / 8.0),
                "model_version":  None,
            }
        return self._mcp_brain.score_via_model(
            market_type="spot", feats=feats, rule_score=rule_score,
        )

    def scan_holdings(self) -> dict:
        """Pull spot balances from all connected exchanges."""
        results = {}
        for ex_name, exchange in self._exchanges.items():
            if not getattr(exchange, "_connected", False):
                continue
            try:
                balance = exchange.fetch_balance("spot")
                if not balance:
                    continue
                holdings = {}
                for coin, info in balance.items():
                    if coin in ("USDT", "USD", "BUSD"):
                        continue
                    free = float(info.get("free", 0) if isinstance(info, dict) else 0)
                    total = float(info.get("total", free) if isinstance(info, dict) else free)
                    if total < 0.0001:
                        continue
                    symbol = f"{coin}/USDT"
                    ticker = exchange.fetch_ticker(symbol, "spot")
                    price = float(ticker.get("last", 0)) if ticker else 0
                    if price <= 0:
                        continue
                    cost = self._fetch_cost_basis(exchange, ex_name, coin, symbol)
                    if cost <= 0:
                        cost = price
                    value = total * price
                    if value < 1.0:
                        continue
                    unrealized = (price - cost) * total
                    unrealized_pct = (price - cost) / cost if cost > 0 else 0
                    prev = self._holdings.get(ex_name, {}).get(coin)
                    peak = max(price, prev.peak_price if prev else price)
                    holdings[coin] = HoldingInfo(
                        coin=coin, exchange=ex_name, balance=total,
                        avg_cost=cost, current_price=price,
                        unrealized_pnl=unrealized,
                        unrealized_pnl_pct=unrealized_pct,
                        peak_price=peak, last_updated=time.time(),
                    )
                if holdings:
                    results[ex_name] = holdings
            except Exception as e:
                logger.debug(f"[SpotMgr] {ex_name} scan error: {e}")
        self._holdings = results
        self._save_state()
        return results

    def _fetch_cost_basis(self, exchange, ex_name: str, coin: str,
                          symbol: str) -> float:
        """Average buy price from exchange trade history."""
        if DRY_RUN:
            prev = self._holdings.get(ex_name, {}).get(coin)
            return prev.avg_cost if prev else 0
        try:
            since = int((time.time() - 90 * 86400) * 1000)
            trades = exchange.fetch_my_trades(symbol, since=since, limit=200)
            if not trades:
                return 0
            total_cost = 0
            total_qty = 0
            for t in trades:
                if t.get("side") == "buy":
                    qty = float(t.get("amount", 0))
                    px = float(t.get("price", 0))
                    total_cost += qty * px
                    total_qty += qty
            return total_cost / total_qty if total_qty > 0 else 0
        except Exception as e:
            logger.debug(f"[SpotMgr] Cost basis {coin}: {e}")
            return 0

    def evaluate_holding(self, coin: str, exchange_name: str,
                         exchange=None) -> dict:
        """Evaluate a holding and return an action recommendation.
        Returns {action: HOLD|SCALE_OUT|SELL|HEDGE, reason, confidence, value_usdt}
        """
        holding = self._holdings.get(exchange_name, {}).get(coin)
        if not holding:
            return {"action": "HOLD", "reason": "no_data", "confidence": 0.0}

        h = holding
        result = {"action": "HOLD", "reason": "", "confidence": 0.5,
                  "value_usdt": h.balance * h.current_price}

        # ── SPOT-PROTECT-V1 peak-drawdown stop (2026-05-01) ──────────────
        # Replaces the EMA/RSI exit chain when SPOT_STRATEGY.enabled=True.
        # Mechanical rules:
        #   drawdown >= drawdown_full_pct → SELL (full exit)
        #   drawdown >= drawdown_half_pct → SCALE_OUT (half, peak resets)
        # Skipped for positions below min_position_usd (no rules apply to
        # tiny holdings — those are Component A's dust-consolidation job).
        # When enabled=False, falls through to legacy TA logic.
        try:
            from config import SPOT_STRATEGY as _SS
        except ImportError:
            _SS = {"enabled": False}
        if _SS.get("enabled", False):
            value_usdt = h.balance * h.current_price
            min_pos = float(_SS.get("min_position_usd", 50.0))
            half_pct = float(_SS.get("drawdown_half_pct", 0.25))
            full_pct = float(_SS.get("drawdown_full_pct", 0.40))
            if value_usdt < min_pos:
                # Too small for active rules — let dust-consolidation
                # handle it. HOLD short-circuit avoids the heavyweight
                # OHLCV fetch on positions we don't act on.
                result["reason"] = (
                    f"below_min_position(${value_usdt:.2f}<${min_pos:.0f})")
                return result
            if h.peak_price > 0:
                drawdown = (h.peak_price - h.current_price) / h.peak_price
            else:
                drawdown = 0.0
            # Full exit dominates half (check higher threshold first).
            if drawdown >= full_pct:
                return {
                    "action": "SELL", "confidence": 0.90,
                    "reason": (
                        f"peak_drawdown_full(dd={drawdown:.1%}>="
                        f"{full_pct:.0%})"),
                    "value_usdt": value_usdt,
                }
            if drawdown >= half_pct:
                # Reset peak so subsequent rules trigger only on a NEW
                # drawdown, not the same continuing one.
                h.peak_price = h.current_price
                self._save_state()
                return {
                    "action": "SCALE_OUT", "confidence": 0.85,
                    "reason": (
                        f"peak_drawdown_half(dd={drawdown:.1%}>="
                        f"{half_pct:.0%})"),
                    "value_usdt": value_usdt,
                }
            # ── SPOT-PROTECT-V2 model-driven early SCALE_OUT (May 2026) ──
            # When the spot ensemble model is loaded AND signals low p_win
            # AND we're already drawing down past the early threshold, take
            # half off well before the V1 25% trigger. Lets the model add
            # value on holdings the deterministic peak-DD rules would still
            # be holding through. Skipped silently when no model is loaded.
            try:
                from config import SPOT_PROTECT_V2 as _SPV2
            except ImportError:
                _SPV2 = {"enabled": True, "drawdown_pct": 0.15, "p_win_floor": 0.40}
            if _SPV2.get("enabled", True):
                early_dd = float(_SPV2.get("drawdown_pct", 0.15))
                p_floor  = float(_SPV2.get("p_win_floor", 0.40))
                if drawdown >= early_dd and self._mcp_brain is not None:
                    try:
                        score = self.score_spot_candidate(
                            symbol=f"{coin}/USDT",
                            feats={},
                            rule_score=50.0,
                        )
                        p_ens = float(score.get("p_win_ensemble") or 0.0)
                        model_v = score.get("model_version")
                        if model_v and p_ens < p_floor:
                            h.peak_price = h.current_price
                            self._save_state()
                            return {
                                "action": "SCALE_OUT", "confidence": 0.78,
                                "reason": (
                                    f"spot_v2_model(dd={drawdown:.1%}>="
                                    f"{early_dd:.0%},p_win={p_ens:.2f}<"
                                    f"{p_floor:.2f},v={model_v})"),
                                "value_usdt": value_usdt,
                            }
                    except Exception as _spe:
                        logger.debug(f"[SpotMgr] V2 score skipped: {_spe}")

            # Neither trigger fired — HOLD but skip the legacy TA path
            # entirely so behavior is fully deterministic.
            result["reason"] = (
                f"peak_drawdown_ok(dd={drawdown:.1%}<{half_pct:.0%})")
            result["confidence"] = 0.65
            return result

        # Fetch 4h candle data for trend analysis
        symbol = f"{coin}/USDT"
        if exchange:
            try:
                candles = exchange.fetch_ohlcv(symbol, "4h", limit=60, market_type="spot")
                if candles and len(candles) >= 30:
                    closes = [float(c[4]) for c in candles]
                    import numpy as np
                    ema20 = self._ema(closes, 20)
                    ema50 = self._ema(closes, 50)
                    rsi = self._rsi(closes, 14)

                    trend_bullish = ema20 > ema50
                    trend_bearish = ema20 < ema50
                    drawdown_from_peak = (h.peak_price - h.current_price) / h.peak_price \
                        if h.peak_price > 0 else 0

                    # HEDGE: drawdown > threshold AND trend bearish
                    hedge_dd = CFG.get("hedge_drawdown_pct", 0.10)
                    if drawdown_from_peak > hedge_dd and trend_bearish and \
                            CFG.get("hedge_via_futures", True):
                        return {"action": "HEDGE", "confidence": 0.85,
                                "reason": f"drawdown={drawdown_from_peak:.1%} trend=bearish",
                                "value_usdt": result["value_usdt"]}

                    # SELL: trend bearish + structure break (EMA20 < EMA50)
                    if trend_bearish and rsi > 40 and \
                            CFG.get("sell_on_structure_break", True):
                        return {"action": "SELL", "confidence": 0.75,
                                "reason": f"structure_break ema20<ema50 rsi={rsi:.0f}",
                                "value_usdt": result["value_usdt"]}

                    # SCALE_OUT: large unrealized profit + RSI overbought
                    scale_threshold = CFG.get("scale_out_threshold_pct", 0.20)
                    if h.unrealized_pnl_pct > scale_threshold and rsi > 65:
                        return {"action": "SCALE_OUT", "confidence": 0.70,
                                "reason": f"profit={h.unrealized_pnl_pct:.1%} rsi={rsi:.0f}",
                                "value_usdt": result["value_usdt"]}

                    # HOLD: trend bullish or neutral
                    result["reason"] = f"trend={'bull' if trend_bullish else 'neutral'} rsi={rsi:.0f}"
                    result["confidence"] = 0.65 if trend_bullish else 0.45
            except Exception as e:
                logger.debug(f"[SpotMgr] Evaluation error {coin}: {e}")
                result["reason"] = f"eval_error: {e}"

        return result

    def get_unrealized_pnl(self) -> dict:
        """Per-holding unrealized PnL summary."""
        summary = {}
        for ex_name, holdings in self._holdings.items():
            for coin, h in holdings.items():
                key = f"{ex_name}:{coin}"
                summary[key] = {
                    "balance": h.balance,
                    "avg_cost": h.avg_cost,
                    "current_price": h.current_price,
                    "unrealized_pnl": h.unrealized_pnl,
                    "unrealized_pnl_pct": h.unrealized_pnl_pct,
                    "value_usdt": h.balance * h.current_price,
                }
        return summary

    def run_cycle(self) -> list:
        """Main entry point: scan → evaluate each → return action list.

        2026-04-14 learning-first pivot: if CFG.recommendation_only is True,
        every non-HOLD decision is appended to data/spot_recommendations.jsonl.
        The bot never sells, trims, or hedges spot autonomously during the
        rebuild. Spec §13.
        """
        if not CFG.get("enabled", True):
            return []
        actions = []
        recommend_only = CFG.get("recommendation_only", True)
        self.scan_holdings()
        for ex_name, holdings in self._holdings.items():
            exchange = self._exchanges.get(ex_name)
            for coin, holding in holdings.items():
                eval_result = self.evaluate_holding(coin, ex_name, exchange)
                if eval_result["action"] != "HOLD":
                    eval_result["coin"] = coin
                    eval_result["exchange"] = ex_name
                    eval_result["symbol"] = f"{coin}/USDT"
                    actions.append(eval_result)
                    tag = "[RECOMMEND-ONLY]" if recommend_only else ""
                    logger.info(
                        f"[SpotMgr] {tag} {ex_name}:{coin} → {eval_result['action']} "
                        f"({eval_result['reason']})")
                    if recommend_only:
                        self._emit_recommendation(eval_result)
        return actions

    def _emit_recommendation(self, action: dict) -> None:
        """Append a spot action recommendation to jsonl. Never calls exchange."""
        entry = {**action, "ts": time.time(), "executed": False}
        try:
            RECOMMENDATIONS_FILE.parent.mkdir(parents=True, exist_ok=True)
            with RECOMMENDATIONS_FILE.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except Exception as e:
            logger.warning(f"[SpotMgr] Could not write recommendation: {e}")

    @staticmethod
    def _ema(data: list, period: int) -> float:
        """Simple EMA of last value from a list of floats."""
        if len(data) < period:
            return data[-1] if data else 0
        multiplier = 2 / (period + 1)
        ema = sum(data[:period]) / period
        for val in data[period:]:
            ema = (val - ema) * multiplier + ema
        return ema

    @staticmethod
    def _rsi(data: list, period: int = 14) -> float:
        """Simple RSI of last value from a list of floats."""
        if len(data) < period + 1:
            return 50.0
        deltas = [data[i] - data[i-1] for i in range(1, len(data))]
        gains = [d if d > 0 else 0 for d in deltas[-period:]]
        losses = [-d if d < 0 else 0 for d in deltas[-period:]]
        avg_gain = sum(gains) / period
        avg_loss = sum(losses) / period
        if avg_loss == 0:
            return 100.0
        rs = avg_gain / avg_loss
        return 100 - (100 / (1 + rs))

    def _save_state(self):
        try:
            STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {}
            for ex_name, holdings in self._holdings.items():
                data[ex_name] = {coin: asdict(h) for coin, h in holdings.items()}
            STATE_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug(f"[SpotMgr] Save error: {e}")

    def _load_state(self):
        try:
            if STATE_FILE.exists():
                data = json.loads(STATE_FILE.read_text(encoding="utf-8"))
                for ex_name, holdings in data.items():
                    self._holdings[ex_name] = {}
                    for coin, h in holdings.items():
                        self._holdings[ex_name][coin] = HoldingInfo(**h)
                logger.info(f"[SpotMgr] Loaded state: "
                            f"{sum(len(h) for h in self._holdings.values())} holdings")
        except Exception as e:
            logger.debug(f"[SpotMgr] Load error: {e}")
