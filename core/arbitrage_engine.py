"""
core/arbitrage_engine.py — Cross-Exchange Arbitrage Engine

8 Institutional Filters + Claude AI confidence scoring.
Works in DRY_RUN (paper trades) and LIVE (real orders) modes.
Supports Binance, Bybit, Bitget.
"""

from __future__ import annotations

import json
import time
import uuid
from dataclasses import dataclass, field
from datetime    import datetime
from pathlib     import Path
from loguru      import logger

MIN_SPREAD_PCT  = 0.004    # 0.40% minimum net spread
MAX_SPREAD_PCT  = 0.08     # 8% cap (wider likely = stale price data)
MIN_VOLUME_USDT = 10_000   # minimum 24h volume on both sides
POSITION_PCT    = 0.02     # 2% of balance per arb leg
MAX_OPEN_ARBS   = 3        # max simultaneous arb positions
ARB_TIMEOUT_MIN = 30       # auto-close if not filled within N minutes

ZSCORE_THRESHOLD = 2.0
LIQUIDITY_MIN    = 10_000

ARB_DATA_FILE = Path("data/arbitrage/opportunities.json")

# Commodity futures symbol map per exchange (ccxt unified format)
# WTI crude oil is "WTI/USDT:USDT" in ccxt unified format
_COMMODITY_FUTURES = {
    "binance": {
        "XAU/USDT:USDT": "Gold",
        "XAG/USDT:USDT": "Silver",
    },
    "bybit": {
        "XAU/USDT:USDT": "Gold",
        "XAG/USDT:USDT": "Silver",
        "WTI/USDT:USDT": "Oil",
    },
    "bitget": {
        "XAU/USDT:USDT": "Gold",
        "XAG/USDT:USDT": "Silver",
        "WTI/USDT:USDT": "Oil",
    },
}


@dataclass
class ArbOpportunity:
    id:            str
    symbol:        str
    buy_exchange:  str
    sell_exchange: str
    buy_price:     float
    sell_price:    float
    gross_spread:  float
    net_spread:    float
    buy_fee_pct:   float
    sell_fee_pct:  float
    volume_buy:    float
    volume_sell:   float
    zscore:        float
    framework_scores: dict = field(default_factory=dict)
    confidence:    float = 0.0
    timestamp:     float = field(default_factory=time.time)
    dry_run:       bool  = True

    @property
    def label(self) -> str:
        return "ARB {}: {} @{:.4f} → {} @{:.4f} spread={:.3f}%".format(
            self.symbol,
            self.buy_exchange,  self.buy_price,
            self.sell_exchange, self.sell_price,
            self.net_spread * 100,
        )


@dataclass
class ArbPosition:
    id:            str
    opportunity:   ArbOpportunity
    size:          float
    buy_order_id:  str  = ""
    sell_order_id: str  = ""
    opened_at:     float = field(default_factory=time.time)
    closed_at:     float = 0.0
    pnl:           float = 0.0
    status:        str   = "open"


class SpreadHistory:
    """Rolling window of spread observations for Citadel z-score."""

    def __init__(self, window: int = 50):
        self._window  = window
        self._history: dict[str, list[float]] = {}

    def push(self, key: str, spread: float):
        h = self._history.setdefault(key, [])
        h.append(spread)
        if len(h) > self._window:
            del h[0]

    def zscore(self, key: str, spread: float) -> float:
        h = self._history.get(key, [])
        if len(h) < 10:
            return 0.0
        import statistics
        mean = statistics.mean(h)
        std  = statistics.stdev(h)
        if std < 1e-9:
            return 0.0
        return (spread - mean) / std


class ArbitrageEngine:

    def __init__(self, active_exchanges: dict, dry_run: bool = True,
                 profile_name: str = "single",
                 open_position_fn=None, close_position_fn=None):
        self.exchanges       = active_exchanges
        self.dry_run         = dry_run
        self.profile_name    = profile_name
        self._open_fn        = open_position_fn
        self._close_fn       = close_position_fn
        self._spread_history = SpreadHistory(window=60)
        self._open_arbs: list[ArbPosition]   = []
        self._closed_arbs: list[ArbPosition] = []
        self._scan_count     = 0
        self._research       = self._load_research()
        self._news_cache     = self._load_news_cache()

        logger.info(
            "[Arb] Engine started | mode={} | exchanges={} | "
            "min_spread={:.2f}% | profile={}".format(
                "DRY_RUN" if dry_run else "LIVE",
                list(active_exchanges.keys()),
                MIN_SPREAD_PCT * 100,
                profile_name,
            )
        )

    def scan_and_trade(self, symbols: list[str]) -> list[ArbOpportunity]:
        self._scan_count += 1
        self._research   = self._load_research()
        self._news_cache = self._load_news_cache()

        logger.info("[Arb] Scan #{} — {} symbols × {} exchange pairs".format(
            self._scan_count, len(symbols),
            len(self.exchanges) * (len(self.exchanges) - 1) // 2))

        found    = []
        ex_names = list(self.exchanges.keys())

        for i, ex_a in enumerate(ex_names):
            for ex_b in ex_names[i + 1:]:
                for symbol in symbols:
                    try:
                        opp = self._check_pair(
                            symbol, ex_a, ex_b,
                            self.exchanges[ex_a], self.exchanges[ex_b])
                        if opp:
                            found.append(opp)
                    except Exception as e:
                        logger.debug("[Arb] {}/{} {}: {}".format(
                            ex_a, ex_b, symbol, e))
                    time.sleep(0.1)

        found.sort(key=lambda o: o.net_spread, reverse=True)

        open_count = len([a for a in self._open_arbs if a.status == "open"])
        for opp in found:
            if open_count >= MAX_OPEN_ARBS:
                break
            if opp.confidence >= 0.60:
                ok = self._execute(opp)
                if ok:
                    open_count += 1

        self._check_exits()
        self._save_log(found)
        return found

    def check_exits(self):
        self._check_exits()

    def summary(self) -> dict:
        closed = self._closed_arbs
        total  = len(closed)
        wins   = [a for a in closed if a.pnl > 0]
        return {
            "total_arbs": total,
            "wins":       len(wins),
            "losses":     total - len(wins),
            "win_rate":   round(len(wins)/total*100, 1) if total else 0,
            "total_pnl":  round(sum(a.pnl for a in closed), 4),
            "open":       len([a for a in self._open_arbs if a.status == "open"]),
            "scan_count": self._scan_count,
        }

    def _check_pair(self, symbol: str,
                    ex_a_name: str, ex_b_name: str,
                    ex_a, ex_b) -> ArbOpportunity | None:
        tick_a = ex_a.fetch_ticker(symbol, "spot")
        tick_b = ex_b.fetch_ticker(symbol, "spot")
        if not tick_a or not tick_b:
            return None

        price_a = float(tick_a.get("last") or tick_a.get("close") or 0)
        price_b = float(tick_b.get("last") or tick_b.get("close") or 0)
        vol_a   = float(tick_a.get("quoteVolume") or
                        tick_a.get("baseVolume", 0) * price_a or 0)
        vol_b   = float(tick_b.get("quoteVolume") or
                        tick_b.get("baseVolume", 0) * price_b or 0)

        if price_a <= 0 or price_b <= 0:
            return None

        if price_a < price_b:
            buy_ex, sell_ex   = ex_a_name, ex_b_name
            buy_price         = price_a
            sell_price        = price_b
            vol_buy, vol_sell = vol_a, vol_b
        else:
            buy_ex, sell_ex   = ex_b_name, ex_a_name
            buy_price         = price_b
            sell_price        = price_a
            vol_buy, vol_sell = vol_b, vol_a

        buy_fee  = self._fee(buy_ex,  "spot_taker")
        sell_fee = self._fee(sell_ex, "spot_taker")

        gross_spread = (sell_price - buy_price) / buy_price
        net_spread   = gross_spread - buy_fee - sell_fee

        hist_key = "{}-{}-{}".format(
            min(buy_ex, sell_ex), max(buy_ex, sell_ex), symbol)
        self._spread_history.push(hist_key, gross_spread)
        zscore = self._spread_history.zscore(hist_key, gross_spread)

        if net_spread <= 0 or net_spread > MAX_SPREAD_PCT:
            return None

        fw_scores, confidence, reject_reason = self._institutional_filters(
            symbol, net_spread, gross_spread, zscore, vol_buy, vol_sell)

        if reject_reason:
            logger.debug("[Arb] {} {}/{}: REJECTED — {}".format(
                symbol, buy_ex, sell_ex, reject_reason))
            return None

        if net_spread < MIN_SPREAD_PCT:
            return None

        opp = ArbOpportunity(
            id=str(uuid.uuid4())[:8],
            symbol=symbol,
            buy_exchange=buy_ex, sell_exchange=sell_ex,
            buy_price=buy_price, sell_price=sell_price,
            gross_spread=round(gross_spread, 6),
            net_spread=round(net_spread, 6),
            buy_fee_pct=buy_fee, sell_fee_pct=sell_fee,
            volume_buy=round(vol_buy, 2),
            volume_sell=round(vol_sell, 2),
            zscore=round(zscore, 2),
            framework_scores=fw_scores,
            confidence=round(confidence, 3),
            dry_run=self.dry_run,
        )
        logger.info("[Arb] OPPORTUNITY: {}  conf={:.0%}  z={:.1f}".format(
            opp.label, confidence, zscore))
        return opp

    def _institutional_filters(
        self, symbol: str, net_spread: float,
        gross_spread: float, zscore: float,
        vol_buy: float, vol_sell: float,
    ) -> tuple[dict, float, str]:
        coin   = symbol.split("/")[0]
        scores = {}
        rejects = []

        # 1. Goldman Sachs — coin moat
        GOLDMAN = {"BTC":1.0,"ETH":0.95,"BNB":0.80,"SOL":0.80,
                   "XRP":0.75,"ADA":0.65,"DOGE":0.55,"AVAX":0.70}
        goldman = GOLDMAN.get(coin, 0.30)
        scores["goldman"] = goldman
        if goldman < 0.40:
            rejects.append("Goldman: moat too low")

        # 2. Morgan Stanley — research data
        ms_score = 0.7
        research = self._research or {}
        for cd in (research.get("goldman") or {}).get("coins") or []:
            if cd.get("ticker") == coin:
                moat = cd.get("moat","Moderate")
                ms_score = {"Strong":0.9,"Moderate":0.7,"Weak":0.4}.get(moat, 0.6)
                break
        scores["morgan_stanley"] = ms_score

        # 3. Bridgewater — spread sanity
        bridge = min(1.0, net_spread / MIN_SPREAD_PCT * 0.5 + 0.5)
        if gross_spread > 0.03:
            bridge *= 0.5
            rejects.append("Bridgewater: spread {:.2f}% suspiciously large".format(
                gross_spread * 100))
        scores["bridgewater"] = round(bridge, 3)

        # 4. JPMorgan — Fear & Greed gate
        jpm = 0.7
        fg = 50
        try:
            fg = int((self._news_cache.get("fear_greed") or {}).get("value", 50))
        except Exception:
            pass
        if fg < 10:
            jpm = 0.4
            rejects.append("JPMorgan: Extreme Fear F&G={}".format(fg))
        elif fg > 85:
            jpm = 0.6
        else:
            jpm = 0.8
        scores["jpmorgan"] = jpm

        # 5. BlackRock — regime filter
        br = 0.75
        regime = (research.get("blackrock") or {}).get("regime","GROWTH")
        if "VOLATILE" in regime or "CRASH" in regime:
            br = 0.3
            rejects.append("BlackRock: regime '{}' too volatile".format(regime))
        elif "ACCUMULATION" in regime or "GROWTH" in regime:
            br = 0.85
        scores["blackrock"] = br

        # 6. Citadel — z-score (zscore == 0.0 means < 10 observations — neutral, not reject)
        citadel = min(1.0, max(0.0, zscore / (ZSCORE_THRESHOLD * 2)))
        if zscore == 0.0:
            citadel = 0.5  # neutral — not enough history to judge
        elif zscore < ZSCORE_THRESHOLD:
            rejects.append("Citadel: z={:.1f} < {:.1f}".format(
                zscore, ZSCORE_THRESHOLD))
        scores["citadel"] = round(citadel, 3)

        # 7. Harvard — liquidity
        min_vol    = min(vol_buy, vol_sell)
        harv_score = min(1.0, min_vol / (LIQUIDITY_MIN * 10))
        if min_vol < LIQUIDITY_MIN:
            rejects.append("Harvard: volume ${:,.0f} < min".format(min_vol))
        scores["harvard"] = round(harv_score, 3)

        # 8. Bain Capital — coin tier
        BAIN = {"BTC":1.0,"ETH":0.95,"BNB":0.8,"SOL":0.8,
                "XRP":0.75,"ADA":0.65,"AVAX":0.7,"DOGE":0.6}
        bain = BAIN.get(coin, 0.45)
        scores["bain"] = bain
        if bain < 0.50:
            rejects.append("Bain: tier too low")

        weights = {
            "goldman":0.15,"morgan_stanley":0.12,"bridgewater":0.14,
            "jpmorgan":0.12,"blackrock":0.14,"citadel":0.16,
            "harvard":0.10,"bain":0.07,
        }
        confidence   = sum(scores.get(k,0) * w for k, w in weights.items())
        spread_bonus = max(0.0, min(0.10, (net_spread - MIN_SPREAD_PCT) / MIN_SPREAD_PCT * 0.05))
        confidence   = min(1.0, confidence + spread_bonus)

        return scores, round(confidence, 3), "; ".join(rejects) if rejects else ""

    def _execute(self, opp: ArbOpportunity) -> bool:
        buy_ex  = self.exchanges.get(opp.buy_exchange)
        sell_ex = self.exchanges.get(opp.sell_exchange)
        if not buy_ex or not sell_ex:
            return False

        try:
            bal       = self._get_balance(buy_ex)
            size_usdt = bal * POSITION_PCT
            size      = size_usdt / opp.buy_price
            if size <= 0:
                return False
        except Exception:
            return False

        arb_id = "ARB-{}-{}-{}".format(
            opp.buy_exchange[:3].upper(),
            opp.sell_exchange[:3].upper(),
            str(uuid.uuid4())[:6],
        )
        strategy_tag = "arbitrage|{}".format(self.profile_name)

        if self.dry_run:
            buy_pos = sell_pos = None
            if self._open_fn:
                buy_pos = self._open_fn(
                    buy_ex, opp.symbol, "buy", "spot",
                    strategy_tag, size,
                    opp.buy_price * (1 - 0.005),
                    opp.sell_price * (1 - 0.001),
                    1, opp.buy_price,
                )
                if buy_pos:
                    sell_pos = self._open_fn(
                        sell_ex, opp.symbol, "sell", "spot",
                        strategy_tag, size,
                        opp.sell_price * (1 + 0.005),
                        opp.buy_price  * (1 + 0.001),
                        1, opp.sell_price,
                    )
            arb_pos = ArbPosition(
                id=arb_id, opportunity=opp, size=size,
                buy_order_id=buy_pos.id  if buy_pos  else "paper-buy",
                sell_order_id=sell_pos.id if sell_pos else "paper-sell",
            )
            self._open_arbs.append(arb_pos)
            logger.info(
                "[Arb][DRY] OPENED {} | size={:.6f} | "
                "exp_net={:.4f} USDT | conf={:.0%}".format(
                    opp.label, size,
                    opp.net_spread * size * opp.buy_price,
                    opp.confidence))
            return True

        else:
            try:
                buy_order = buy_ex.create_order(
                    opp.symbol, "market", "buy", size, market_type="spot")
            except Exception as e:
                logger.error("[Arb][LIVE] Buy leg failed: {}".format(e))
                return False
            try:
                sell_order = sell_ex.create_order(
                    opp.symbol, "market", "sell", size, market_type="spot")
            except Exception as e:
                # Buy succeeded but sell failed — unhedged! Rollback buy leg
                logger.error(
                    "[Arb][LIVE] Sell leg failed after buy filled — "
                    "rolling back buy with market sell: {}".format(e))
                try:
                    buy_ex.create_order(
                        opp.symbol, "market", "sell", size, market_type="spot")
                    logger.info("[Arb][LIVE] Rollback sell executed on {}".format(
                        opp.buy_exchange))
                except Exception as e2:
                    logger.critical(
                        "[Arb][LIVE] ROLLBACK FAILED — manual intervention needed! "
                        "{} {} bought on {} but unsold: {}".format(
                            size, opp.symbol, opp.buy_exchange, e2))
                return False
            arb_pos = ArbPosition(
                id=arb_id, opportunity=opp, size=size,
                buy_order_id=buy_order.get("id","?"),
                sell_order_id=sell_order.get("id","?"),
            )
            self._open_arbs.append(arb_pos)
            logger.info("[Arb][LIVE] Orders placed — buy:{} sell:{}".format(
                arb_pos.buy_order_id, arb_pos.sell_order_id))
            return True

    def _check_exits(self):
        now = time.time()
        for arb in list(self._open_arbs):
            if arb.status != "open":
                continue
            if (now - arb.opened_at) / 60 >= ARB_TIMEOUT_MIN:
                self._close_arb(arb, "timeout")

    def _close_arb(self, arb: ArbPosition, reason: str = "target"):
        opp = arb.opportunity
        arb.pnl       = round(opp.net_spread * arb.size * opp.buy_price, 4)
        arb.closed_at = time.time()
        arb.status    = "closed"
        self._open_arbs.remove(arb)
        self._closed_arbs.append(arb)
        if len(self._closed_arbs) > 200:
            self._closed_arbs = self._closed_arbs[-200:]
        logger.info("[Arb] CLOSED {} reason={} est_pnl={:+.4f} USDT".format(
            opp.label, reason, arb.pnl))

    def _fee(self, exchange_name: str, fee_type: str) -> float:
        try:
            from config import FEE
            key = "{}_{}".format(exchange_name.lower(), fee_type)
            if key in FEE:
                return FEE[key]
            return FEE.get(fee_type, 0.001)
        except Exception:
            return 0.001

    def _get_balance(self, exchange) -> float:
        try:
            bal = exchange.fetch_balance("spot")
            ex = exchange.name.lower() if hasattr(exchange, 'name') else ""

            # Bybit unified: raw API totalEquity
            if ex == "bybit":
                try:
                    lst = bal.get("info", {}).get("result", {}).get("list", [])
                    if lst and isinstance(lst, list):
                        acct = lst[0] if lst else {}
                        for field in ("totalEquity", "totalWalletBalance", "totalAvailableBalance"):
                            val = acct.get(field)
                            if val is not None:
                                try:
                                    v = float(val)
                                    if v > 0:
                                        return v
                                except (TypeError, ValueError):
                                    pass
                        coins = acct.get("coin", [])
                        if isinstance(coins, list):
                            for c in coins:
                                if c.get("coin") == "USDT":
                                    for f2 in ("equity", "walletBalance"):
                                        val2 = c.get(f2)
                                        if val2 is not None:
                                            try:
                                                v2 = float(val2)
                                                if v2 > 0:
                                                    return v2
                                            except (TypeError, ValueError):
                                                pass
                except Exception:
                    pass

            # Standard: total first (Bybit unified), then free
            usdt = bal.get("USDT")
            if isinstance(usdt, dict):
                for key in ("total", "free"):
                    val = usdt.get(key)
                    if val is not None:
                        v = float(val)
                        if v > 0:
                            return v
            total = bal.get("total", {})
            if isinstance(total, dict) and total.get("USDT"):
                return float(total["USDT"])
            free = bal.get("free", {})
            if isinstance(free, dict) and free.get("USDT"):
                return float(free["USDT"])
        except Exception:
            pass
        return 100.0

    def _load_research(self) -> dict:
        try:
            p = Path("data/research/report_latest.json")
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _load_news_cache(self) -> dict:
        try:
            p = Path("data/news_cache.json")
            if p.exists():
                return json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            pass
        return {}

    def _save_log(self, opportunities: list[ArbOpportunity]):
        try:
            ARB_DATA_FILE.parent.mkdir(parents=True, exist_ok=True)
            data = {
                "updated_at":    datetime.now().isoformat(),
                "scan_count":    self._scan_count,
                "open_arbs":     len([a for a in self._open_arbs if a.status == "open"]),
                "closed_arbs":   len(self._closed_arbs),
                "total_pnl":     round(sum(a.pnl for a in self._closed_arbs), 4),
                "opportunities": [
                    {
                        "id":           o.id,
                        "symbol":       o.symbol,
                        "buy_ex":       o.buy_exchange,
                        "sell_ex":      o.sell_exchange,
                        "buy_price":    o.buy_price,
                        "sell_price":   o.sell_price,
                        "net_spread":   o.net_spread,
                        "gross_spread": o.gross_spread,
                        "confidence":   o.confidence,
                        "zscore":       o.zscore,
                        "vol_buy":      o.volume_buy,
                        "vol_sell":     o.volume_sell,
                        "frameworks":   o.framework_scores,
                        "timestamp":    o.timestamp,
                    }
                    for o in opportunities[:20]
                ],
            }
            ARB_DATA_FILE.write_text(
                json.dumps(data, indent=2), encoding="utf-8")
        except Exception as e:
            logger.debug("[Arb] Save log: {}".format(e))
