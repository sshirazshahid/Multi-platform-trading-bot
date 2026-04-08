"""
research/engine.py  --  Institutional Crypto Research Engine

Eight analyst frameworks applied to your crypto portfolio:

  1. Goldman Screener     -- Top coins by momentum + volume + technicals
  2. Morgan Stanley DCF  -- NVT-based crypto valuation model
  3. Bridgewater Risk     -- Correlation, concentration, stress tests
  4. JPMorgan Catalyst   -- Upcoming events, implied moves, entry plays
  5. BlackRock Allocation -- Multi-asset allocation with ETF picks
  6. Citadel Quant        -- Multi-TF trend, S/R levels, Fibonacci
  7. Harvard Yield        -- Staking yields, DRIP projections
  8. Bain Competitive     -- Exchange + L1 competitive landscape

Runs standalone:  python research/engine.py
Output:           data/research/report.html   (self-contained HTML)
                  data/research/report_YYYY-MM-DD.json

Free data sources only (no paid API keys required):
  CoinGecko, Alternative.me, Binance public API
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
import urllib.error
from datetime import datetime, timezone
from pathlib  import Path
from loguru   import logger

try:
    from utils.logger import setup_logger
except ImportError:
    pass

OUTPUT_DIR  = Path("data/research")
HTML_OUT    = OUTPUT_DIR / "report.html"
JSON_LATEST = OUTPUT_DIR / "report_latest.json"


def _now_iso() -> str:
    """Return current UTC time as ISO string (no deprecation warning)."""
    return datetime.now(timezone.utc).isoformat()


def _now_str(fmt: str = "%Y-%m-%d %H:%M UTC") -> str:
    """Return formatted UTC time string."""
    return datetime.now(timezone.utc).strftime(fmt)


def _now_date() -> str:
    """Return YYYY-MM-DD in UTC."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ================================================================== #
# DATA LAYER  --  free public APIs                                   #
# ================================================================== #

COINS = ["bitcoin","ethereum","binancecoin","solana",
         "ripple","cardano","dogecoin","avalanche-2"]

COIN_TICKERS = {
    "bitcoin":"BTC","ethereum":"ETH","binancecoin":"BNB",
    "solana":"SOL","ripple":"XRP","cardano":"ADA",
    "dogecoin":"DOGE","avalanche-2":"AVAX",
}

HEADERS = {"User-Agent": "TradingBot-Research/1.0",
           "Accept": "application/json"}


def _get(url: str, timeout: int = 10) -> dict | list | None:
    try:
        req = urllib.request.Request(url, headers=HEADERS)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.debug("[Research] GET failed {}: {}".format(url[:60], e))
        return None


def fetch_market_data() -> dict:
    """CoinGecko /coins/markets -- price, volume, market cap, change."""
    logger.info("[Research] Fetching market data...")
    url = (
        "https://api.coingecko.com/api/v3/coins/markets"
        "?vs_currency=usd&ids={}&order=market_cap_desc"
        "&sparkline=false&price_change_percentage=1h,24h,7d,30d".format(
            ",".join(COINS))
    )
    data = _get(url) or []
    return {c["id"]: c for c in data if "id" in c}


def fetch_historical_prices(coin_id: str, days: int = 365) -> list:
    """CoinGecko /coins/{id}/market_chart -- daily prices."""
    url = ("https://api.coingecko.com/api/v3/coins/{}/market_chart"
           "?vs_currency=usd&days={}&interval=daily".format(coin_id, days))
    data = _get(url) or {}
    return data.get("prices", [])   # [[timestamp_ms, price], ...]


def fetch_fear_greed() -> dict:
    data = _get("https://api.alternative.me/fng/?limit=30") or {}
    return {"current": data.get("data",[{}])[0],
            "history": data.get("data",[])}


def fetch_global() -> dict:
    data = _get("https://api.coingecko.com/api/v3/global") or {}
    return data.get("data", {})


def fetch_staking_yields() -> dict:
    """Approximate staking yields -- static researched values."""
    return {
        "ETH":  {"yield_pct": 3.5,  "method": "Liquid staking (Lido/Rocket Pool)",
                 "safety": 9, "notes": "Native protocol, battle-tested"},
        "SOL":  {"yield_pct": 6.5,  "method": "Native staking via validators",
                 "safety": 8, "notes": "High APY, inflation-based"},
        "ADA":  {"yield_pct": 3.0,  "method": "Delegation to stake pools",
                 "safety": 8, "notes": "Low risk, non-custodial"},
        "AVAX": {"yield_pct": 8.0,  "method": "Validator delegation",
                 "safety": 7, "notes": "Higher yield, lock-up required"},
        "BNB":  {"yield_pct": 2.5,  "method": "BNB locked staking (Binance)",
                 "safety": 7, "notes": "Exchange custody risk"},
        "XRP":  {"yield_pct": 0.0,  "method": "No native staking",
                 "safety": 10, "notes": "No yield available"},
        "BTC":  {"yield_pct": 0.0,  "method": "No native staking",
                 "safety": 10, "notes": "WBTC/lending only (higher risk)"},
        "DOGE": {"yield_pct": 0.0,  "method": "No native staking",
                 "safety": 10, "notes": "No yield available"},
    }


# ================================================================== #
# INDICATOR HELPERS                                                   #
# ================================================================== #

def _ema(prices: list, period: int) -> list:
    if not prices or period <= 0: return prices
    k   = 2 / (period + 1)
    ema = [prices[0]]
    for p in prices[1:]:
        ema.append(p * k + ema[-1] * (1 - k))
    return ema


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1: return 50.0
    deltas = [closes[i] - closes[i-1] for i in range(1, len(closes))]
    gains  = [max(d, 0) for d in deltas[-period:]]
    losses = [abs(min(d, 0)) for d in deltas[-period:]]
    avg_g  = sum(gains)  / period
    avg_l  = sum(losses) / period
    if avg_l == 0: return 100.0
    return round(100 - 100 / (1 + avg_g / avg_l), 1)


def _sma(prices: list, period: int) -> float:
    if len(prices) < period: return prices[-1] if prices else 0
    return sum(prices[-period:]) / period


def _stddev(prices: list, period: int) -> float:
    if len(prices) < period: return 0
    sub = prices[-period:]
    mu  = sum(sub) / len(sub)
    return math.sqrt(sum((x - mu)**2 for x in sub) / len(sub))


def _max_drawdown(prices: list) -> float:
    if not prices: return 0
    peak = prices[0]; mdd = 0
    for p in prices:
        if p > peak: peak = p
        dd = (peak - p) / peak
        if dd > mdd: mdd = dd
    return round(mdd * 100, 1)


def _pearson(a: list, b: list) -> float:
    n = min(len(a), len(b))
    if n < 2: return 0
    a, b  = a[-n:], b[-n:]
    ma    = sum(a) / n;  mb = sum(b) / n
    num   = sum((a[i]-ma)*(b[i]-mb) for i in range(n))
    denom = math.sqrt(sum((x-ma)**2 for x in a) *
                       sum((x-mb)**2 for x in b))
    return round(num / denom, 3) if denom else 0


def _fibonacci_levels(high: float, low: float) -> dict:
    rng = high - low
    return {
        "0%":    round(high, 4),
        "23.6%": round(high - 0.236 * rng, 4),
        "38.2%": round(high - 0.382 * rng, 4),
        "50.0%": round(high - 0.500 * rng, 4),
        "61.8%": round(high - 0.618 * rng, 4),
        "78.6%": round(high - 0.786 * rng, 4),
        "100%":  round(low,  4),
    }


def _support_resistance(prices: list) -> dict:
    if len(prices) < 30: return {}
    recent = prices[-30:]
    pivot  = (max(recent) + min(recent) + recent[-1]) / 3
    r1 = 2 * pivot - min(recent)
    r2 = pivot + (max(recent) - min(recent))
    s1 = 2 * pivot - max(recent)
    s2 = pivot - (max(recent) - min(recent))
    return {
        "R2": round(r2, 4), "R1": round(r1, 4),
        "Pivot": round(pivot, 4),
        "S1": round(s1, 4), "S2": round(s2, 4),
    }


# ================================================================== #
# FRAMEWORK 1  --  GOLDMAN SACHS SCREENER                            #
# ================================================================== #

def goldman_screener(market_data: dict, price_histories: dict) -> dict:
    logger.info("[Research] Goldman Screener...")

    MOAT = {
        "bitcoin":    ("Strong",   "Store of value narrative, network effect, "
                                    "institutional adoption, scarcity (21M cap)"),
        "ethereum":   ("Strong",   "Smart contract platform monopoly, DeFi/NFT "
                                    "infrastructure, staking yields"),
        "binancecoin":("Moderate", "Exchange token utility, BSC ecosystem, burn mechanism"),
        "solana":     ("Moderate", "High throughput, low fees, developer ecosystem growth"),
        "ripple":     ("Moderate", "Banking partnerships, cross-border payment rails, ODL"),
        "cardano":    ("Weak",     "Academic rigor but slow delivery, limited DeFi adoption"),
        "dogecoin":   ("Weak",     "Meme culture, no development roadmap, unlimited supply"),
        "avalanche-2":("Moderate", "Subnet architecture, EVM compatible, institutional subnets"),
    }

    results = []
    for coin_id, data in market_data.items():
        hist   = price_histories.get(coin_id, [])
        closes = [p[1] for p in hist] if hist else []

        price   = data.get("current_price", 0)
        mktcap  = data.get("market_cap", 0)
        vol_24h = data.get("total_volume", 0)
        chg_30d = data.get("price_change_percentage_30d_in_currency", 0) or 0
        chg_7d  = data.get("price_change_percentage_7d_in_currency",  0) or 0
        chg_24h = data.get("price_change_percentage_24h_in_currency", 0) or 0

        rsi_val    = _rsi(closes, 14) if len(closes) >= 15 else 50.0
        sma50      = _sma(closes, 50)  if len(closes) >= 50 else price
        sma200     = _sma(closes, 200) if len(closes) >= 200 else price
        vol_ratio  = vol_24h / mktcap if mktcap > 0 else 0
        mdd        = _max_drawdown(closes[-90:]) if len(closes) >= 90 else 50

        mom_score   = min(30, max(0, 15 + chg_30d * 0.5))
        vol_score   = min(20, vol_ratio * 2000)
        trend_score = 20 if sma50 > sma200 else 5
        rsi_score   = 15 if 40 <= rsi_val <= 65 else (8 if 30 <= rsi_val <= 75 else 2)
        mc_score    = min(15, math.log10(max(mktcap, 1)) - 5) if mktcap > 1e6 else 0
        total_score = mom_score + vol_score + trend_score + rsi_score + mc_score

        nvt        = round(mktcap / max(vol_24h, 1), 1)
        nvt_rating = ("Cheap" if nvt < 15 else "Fair" if nvt < 40 else
                      "Rich"  if nvt < 80 else "Very Rich")

        moat, moat_detail = MOAT.get(coin_id, ("Weak", "Unknown"))

        results.append({
            "coin": coin_id,
            "ticker":       COIN_TICKERS.get(coin_id, coin_id[:4].upper()),
            "price":        price,
            "score":        round(total_score, 1),
            "mktcap":       mktcap,
            "vol_24h":      vol_24h,
            "chg_24h":      round(chg_24h, 2),
            "chg_7d":       round(chg_7d,  2),
            "chg_30d":      round(chg_30d, 2),
            "rsi":          rsi_val,
            "sma50":        round(sma50, 4),
            "sma200":       round(sma200, 4),
            "trend":        "Bull" if sma50 > sma200 else "Bear",
            "nvt":          nvt,
            "nvt_rating":   nvt_rating,
            "max_dd_90d":   mdd,
            "moat":         moat,
            "moat_detail":  moat_detail,
            "bull_target":  round(price * 1.35, 4),
            "bear_target":  round(price * 0.72, 4),
            "entry_low":    round(price * 0.96, 4),
            "entry_high":   round(price * 1.01, 4),
            "stop_loss":    round(price * 0.92, 4),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    return {"framework": "Goldman Sachs Screener",
            "generated": _now_iso(), "coins": results}


# ================================================================== #
# FRAMEWORK 2  --  MORGAN STANLEY DCF                                #
# ================================================================== #

def morgan_stanley_dcf(market_data: dict, price_histories: dict) -> dict:
    logger.info("[Research] Morgan Stanley DCF...")

    PROTOCOL_FEES_M = {
        "bitcoin":0,"ethereum":2200,"binancecoin":800,
        "solana":60,"ripple":10,"cardano":15,
        "dogecoin":5,"avalanche-2":40,
    }
    GROWTH_RATES = {
        "bitcoin":0.12,"ethereum":0.25,"binancecoin":0.15,
        "solana":0.45,"ripple":0.18,"cardano":0.20,
        "dogecoin":0.05,"avalanche-2":0.35,
    }
    BETAS = {
        "bitcoin":1.0,"ethereum":1.15,"binancecoin":1.20,
        "solana":1.50,"ripple":1.30,"cardano":1.35,
        "dogecoin":1.60,"avalanche-2":1.45,
    }

    results = []
    for coin_id, data in market_data.items():
        price   = data.get("current_price", 0)
        mktcap  = data.get("market_cap", 0)
        supply  = data.get("circulating_supply", 1)
        vol_24h = data.get("total_volume", 0)

        fees_m = PROTOCOL_FEES_M.get(coin_id, 10)
        cagr   = GROWTH_RATES.get(coin_id, 0.15)
        beta   = BETAS.get(coin_id, 1.2)
        wacc   = 0.045 + beta * 0.08

        fcf_years = [round(fees_m * ((1 + cagr) ** yr), 1) for yr in range(1, 6)]

        terminal_growth  = 0.03
        tv_perpetuity    = fcf_years[-1] * (1 + terminal_growth) / max(wacc - terminal_growth, 0.001)
        tv_exit_multiple = fcf_years[-1] * 20

        pv_fcfs     = sum(fcf / (1 + wacc) ** yr for yr, fcf in enumerate(fcf_years, 1))
        pv_tv_perp  = tv_perpetuity    / (1 + wacc) ** 5
        pv_tv_exit  = tv_exit_multiple / (1 + wacc) ** 5

        intrinsic_perp = (pv_fcfs + pv_tv_perp) * 1e6 / max(supply, 1)
        intrinsic_exit = (pv_fcfs + pv_tv_exit) * 1e6 / max(supply, 1)
        intrinsic_avg  = (intrinsic_perp + intrinsic_exit) / 2

        verdict = ("Undervalued" if price < intrinsic_avg * 0.85 else
                   "Overvalued"  if price > intrinsic_avg * 1.15 else
                   "Fairly Valued")

        sens = {}
        for adj in [-0.02, -0.01, 0.0, 0.01, 0.02]:
            w    = wacc + adj
            pv_f = sum(fcf / (1 + w) ** yr for yr, fcf in enumerate(fcf_years, 1))
            tv_s = fcf_years[-1] * (1 + terminal_growth) / max(w - terminal_growth, 0.001)
            pv_t = tv_s / (1 + w) ** 5
            sens["{:.0%}".format(w)] = round((pv_f + pv_t) * 1e6 / max(supply, 1), 4)

        results.append({
            "coin":             coin_id,
            "ticker":           COIN_TICKERS.get(coin_id, "?"),
            "current_price":    price,
            "mktcap_b":         round(mktcap / 1e9, 2),
            "protocol_fees_m":  fees_m,
            "cagr_5yr":         round(cagr * 100, 1),
            "wacc":             round(wacc * 100, 1),
            "beta":             beta,
            "fcf_projections":  dict(zip(["Y1","Y2","Y3","Y4","Y5"], fcf_years)),
            "tv_exit_multiple": round(tv_exit_multiple, 1),
            "tv_perpetuity":    round(tv_perpetuity, 1),
            "intrinsic_exit":   round(intrinsic_exit, 4),
            "intrinsic_perp":   round(intrinsic_perp, 4),
            "intrinsic_avg":    round(intrinsic_avg,  4),
            "nvt_fair_value":   round(vol_24h * 365 * 25 / max(supply, 1), 4),
            "nvt_current":      round(mktcap / max(vol_24h * 365, 1), 1),
            "verdict":          verdict,
            "upside_pct":       round((intrinsic_avg / max(price, 1) - 1) * 100, 1),
            "sensitivity":      sens,
        })

    return {
        "framework": "Morgan Stanley DCF Valuation",
        "generated": _now_iso(),
        "assumptions": {
            "risk_free_rate": "4.5%", "equity_risk_premium": "8.0%",
            "terminal_growth": "3.0%", "exit_multiple": "20x",
        },
        "coins": results,
    }


# ================================================================== #
# FRAMEWORK 3  --  BRIDGEWATER RISK                                  #
# ================================================================== #

def bridgewater_risk(market_data: dict, price_histories: dict) -> dict:
    logger.info("[Research] Bridgewater Risk Analysis...")

    return_series = {}
    for coin_id in COINS:
        hist   = price_histories.get(coin_id, [])
        closes = [p[1] for p in hist[-90:]] if hist else []
        if len(closes) > 10:
            return_series[coin_id] = [(closes[i]/closes[i-1])-1
                                       for i in range(1, len(closes))]

    corr_matrix = {}
    for a in return_series:
        corr_matrix[a] = {}
        for b in return_series:
            if a == b:
                corr_matrix[a][b] = 1.0
            else:
                n = min(len(return_series[a]), len(return_series[b]))
                corr_matrix[a][b] = _pearson(
                    return_series[a][-n:], return_series[b][-n:])

    SECTORS = {
        "Store of Value":   ["bitcoin","dogecoin"],
        "Smart Contract L1":["ethereum","solana","cardano","avalanche-2"],
        "Exchange Token":   ["binancecoin"],
        "Payment/Remit":    ["ripple"],
    }
    mktcaps  = {c: market_data.get(c,{}).get("market_cap",0) for c in COINS}
    total_mc = sum(mktcaps.values())
    sector_exposure = {
        s: round(sum(mktcaps.get(c,0) for c in coins) / max(total_mc,1) * 100, 1)
        for s, coins in SECTORS.items()
    }

    LIQUIDITY = {
        "bitcoin":1,"ethereum":2,"binancecoin":3,"solana":4,
        "ripple":5,"cardano":6,"dogecoin":7,"avalanche-2":8,
    }
    def _liq(r):
        return ("Very High" if r<=2 else "High" if r<=4 else
                "Moderate" if r<=6 else "Low")

    STRESS_MILD = {"bitcoin":-22,"ethereum":-30,"binancecoin":-28,
                   "solana":-40,"ripple":-35,"cardano":-38,
                   "dogecoin":-45,"avalanche-2":-42}
    STRESS_BEAR = {"bitcoin":-55,"ethereum":-65,"binancecoin":-62,
                   "solana":-78,"ripple":-70,"cardano":-72,
                   "dogecoin":-82,"avalanche-2":-75}
    STRESS_SWAN = {"bitcoin":-80,"ethereum":-85,"binancecoin":-87,
                   "solana":-92,"ripple":-88,"cardano":-90,
                   "dogecoin":-95,"avalanche-2":-91}

    tail_risks = [
        {"scenario":"Regulatory ban (US/EU)",          "probability":"12%",
         "impact":"Severe",       "estimated_drawdown":"-60% to -85%",
         "hedge":"Hold USDC/USDT, reduce altcoin exposure"},
        {"scenario":"BTC ETF outflows + macro tightening","probability":"25%",
         "impact":"Moderate",     "estimated_drawdown":"-30% to -50%",
         "hedge":"Add short BTC futures hedge 10-15% of portfolio"},
        {"scenario":"Stablecoin de-peg (USDT/USDC)",   "probability":"5%",
         "impact":"Catastrophic", "estimated_drawdown":"-50% to -90%",
         "hedge":"Diversify stablecoin holdings, hold some fiat"},
        {"scenario":"Major exchange hack / collapse",   "probability":"8%",
         "impact":"Severe",       "estimated_drawdown":"-40% to -70%",
         "hedge":"Self-custody hardware wallet for >50% of holdings"},
        {"scenario":"ETH network failure / competitor", "probability":"10%",
         "impact":"Moderate (ETH specific)","estimated_drawdown":"-50% to -70% ETH",
         "hedge":"Diversify L1 exposure across SOL, AVAX, ADA"},
    ]

    # FIX: key names match the HTML template exactly (risk, strategy, instrument, size)
    hedges = [
        {"risk":       "General market decline",
         "strategy":   "Short BTC/ETH futures hedge equal to 15% of portfolio notional",
         "instrument": "BTC/USDT:USDT perpetual futures (short)",
         "size":       "10-15% of portfolio"},
        {"risk":       "Altcoin underperformance vs BTC",
         "strategy":   "Increase BTC allocation to 50%+ during bear signals",
         "instrument": "Rotate altcoins to BTC/ETH",
         "size":       "Rebalance when BTC.D > 55%"},
        {"risk":       "Tail risk / black swan",
         "strategy":   "Maintain 20-30% stablecoin buffer (USDC)",
         "instrument": "USDC on Binance or self-custody",
         "size":       "20-30% of total portfolio"},
    ]

    targets = {
        "bitcoin":40.0,"ethereum":25.0,"binancecoin":10.0,"solana":8.0,
        "ripple":5.0,"cardano":4.0,"avalanche-2":5.0,"dogecoin":3.0,
    }

    per_coin = []
    for coin_id in COINS:
        hist   = price_histories.get(coin_id, [])
        closes = [p[1] for p in hist[-90:]] if hist else [1]
        per_coin.append({
            "coin":             coin_id,
            "ticker":           COIN_TICKERS.get(coin_id, "?"),
            "liquidity_rank":   LIQUIDITY.get(coin_id, 8),
            "liquidity_label":  _liq(LIQUIDITY.get(coin_id, 8)),
            "max_drawdown_90d": _max_drawdown(closes),
            "stress_mild":      STRESS_MILD.get(coin_id, -35),
            "stress_bear":      STRESS_BEAR.get(coin_id, -65),
            "stress_blk_swan":  STRESS_SWAN.get(coin_id, -85),
            "target_weight":    targets.get(coin_id, 0),
        })

    return {
        "framework":          "Bridgewater Risk Analysis",
        "generated":          _now_iso(),
        "correlation_matrix": corr_matrix,
        "sector_exposure":    sector_exposure,
        "tail_risks":         tail_risks,
        "hedging_strategies": hedges,
        "rebalancing_targets":targets,
        "per_coin":           per_coin,
    }


# ================================================================== #
# FRAMEWORK 4  --  JPMORGAN CATALYST                                 #
# ================================================================== #

def jpmorgan_catalyst(market_data: dict, fear_greed: dict) -> dict:
    logger.info("[Research] JPMorgan Catalyst Analysis...")

    fg_val     = int(fear_greed.get("current", {}).get("value", 50))
    fg_history = fear_greed.get("history", [])

    CATALYSTS = {
        "bitcoin": [
            {"event":"Next BTC Halving (estimated Apr 2028)",
             "impact":"Historically +150-400% within 12 months post-halving","timing":"~24 months away"},
            {"event":"US Bitcoin Strategic Reserve policy clarity",
             "impact":"Bullish -- could add $50-100B institutional demand","timing":"2025-2026"},
            {"event":"Spot BTC ETF monthly flows",
             "impact":"Key demand driver -- watch BlackRock IBIT flows","timing":"Monthly"},
        ],
        "ethereum": [
            {"event":"EIP-4844 full rollout",
             "impact":"Reduces L2 fees 10x, increases throughput","timing":"Active"},
            {"event":"Ethereum ETF staking inclusion",
             "impact":"Adds yield to ETH ETFs, increases AUM","timing":"2025-2026"},
            {"event":"Pectra upgrade",
             "impact":"Validator improvements, EIP-7702 account abstraction","timing":"2025"},
        ],
        "solana": [
            {"event":"Firedancer client mainnet",
             "impact":"1M+ TPS capacity, reduces outages","timing":"2025"},
            {"event":"Solana ETF approval",
             "impact":"Institutional access -- $5-20B potential inflows","timing":"2025-2026"},
            {"event":"Memecoin season volume",
             "impact":"Network fees surge -- bullish for SOL","timing":"Cyclical"},
        ],
        "binancecoin": [
            {"event":"Quarterly BNB burn",
             "impact":"Deflationary -- reduces supply","timing":"Quarterly"},
            {"event":"Binance regulatory resolutions",
             "impact":"Reducing legal overhang supports BNB","timing":"Ongoing"},
        ],
        "ripple": [
            {"event":"XRP ETF approval",
             "impact":"Grayscale/Bitwise applications pending","timing":"2025"},
            {"event":"RLUSD stablecoin adoption",
             "impact":"Increases XRP Ledger utility and ODL volume","timing":"2025"},
        ],
        "cardano": [
            {"event":"Plomin hard fork (governance)",
             "impact":"Decentralized governance live","timing":"Active"},
            {"event":"Hydra scaling layer adoption",
             "impact":"Theoretical 1M TPS -- needs real usage","timing":"2025-2026"},
        ],
        "avalanche-2": [
            {"event":"Institutional subnet deployments",
             "impact":"T-Mobile, AWS, JPMorgan exploring","timing":"2025"},
            {"event":"Avalanche9000 upgrade",
             "impact":"Reduces subnet creation cost by 99%","timing":"Active"},
        ],
        "dogecoin": [
            {"event":"Elon Musk / X payment integration",
             "impact":"If X integrates DOGE: massive retail catalyst","timing":"Speculative / 2025+"},
            {"event":"DOGE ETF approval",
             "impact":"Legitimizes DOGE as asset class","timing":"2025-2026"},
        ],
    }

    results = []
    for coin_id, catalysts in CATALYSTS.items():
        chg7 = market_data.get(coin_id,{}).get(
            "price_change_percentage_7d_in_currency", 0) or 0
        if fg_val < 25:
            play = "BUY (accumulate in extreme fear)"
        elif fg_val > 75:
            play = "WAIT / REDUCE (extreme greed, wait for pullback)"
        elif chg7 > 10:
            play = "WAIT (overbought short-term, look for dip)"
        elif chg7 < -10:
            play = "BUY BEFORE (oversold, catalyst-driven recovery likely)"
        else:
            play = "BUY BEFORE (neutral market, position ahead of catalysts)"

        results.append({
            "coin":             coin_id,
            "ticker":           COIN_TICKERS.get(coin_id, "?"),
            "price":            market_data.get(coin_id,{}).get("current_price", 0),
            "chg_7d":           round(chg7, 2),
            "catalysts":        catalysts,
            "recommended_play": play,
        })

    if len(fg_history) >= 7:
        week_avg = sum(int(x.get("value",50)) for x in fg_history[:7]) / 7
        trend    = ("Improving" if int(fg_history[0].get("value",50)) > week_avg
                    else "Deteriorating")
    else:
        week_avg = fg_val; trend = "Stable"

    return {
        "framework":          "JPMorgan Catalyst Analysis",
        "generated":          _now_iso(),
        "fear_greed_current": fg_val,
        "fear_greed_7d_avg":  round(week_avg, 1),
        "fear_greed_trend":   trend,
        "coins":              results,
    }


# ================================================================== #
# FRAMEWORK 5  --  BLACKROCK ALLOCATION                              #
# ================================================================== #

def blackrock_allocation(market_data: dict, fg_val: int) -> dict:
    logger.info("[Research] BlackRock Portfolio Allocation...")

    if   fg_val < 20: regime = "ACCUMULATION"; regime_note = "Extreme Fear -- historical best entry period"
    elif fg_val < 45: regime = "GROWTH";       regime_note = "Fear/Neutral -- add positions gradually"
    elif fg_val < 70: regime = "CAUTION";      regime_note = "Neutral/Greed -- maintain positions, tighten stops"
    else:             regime = "DEFENSIVE";    regime_note = "Extreme Greed -- reduce risk, increase stablecoin buffer"

    ALLOCATIONS = {
        "ACCUMULATION": {
            "BTC":{"pct":40,"role":"Core",      "rationale":"Safest entry in fear"},
            "ETH":{"pct":25,"role":"Core",      "rationale":"Ecosystem play"},
            "SOL":{"pct":10,"role":"Satellite", "rationale":"High beta growth"},
            "BNB":{"pct": 8,"role":"Satellite", "rationale":"Exchange utility"},
            "AVAX":{"pct":5,"role":"Satellite", "rationale":"L1 diversifier"},
            "USDC":{"pct":12,"role":"Cash",     "rationale":"Dry powder for dips"},
        },
        "GROWTH": {
            "BTC":{"pct":35,"role":"Core",      "rationale":"Market anchor"},
            "ETH":{"pct":25,"role":"Core",      "rationale":"Staking yield + growth"},
            "SOL":{"pct":12,"role":"Satellite", "rationale":"High TPS narrative"},
            "BNB":{"pct": 8,"role":"Satellite", "rationale":"Quarterly burns"},
            "XRP":{"pct": 5,"role":"Satellite", "rationale":"Regulatory clarity"},
            "AVAX":{"pct":5,"role":"Satellite", "rationale":"Institutional subnets"},
            "USDC":{"pct":10,"role":"Cash",     "rationale":"Tactical reserve"},
        },
        "CAUTION": {
            "BTC":{"pct":45,"role":"Core",      "rationale":"Flight to quality"},
            "ETH":{"pct":20,"role":"Core",      "rationale":"Staking buffer"},
            "USDC":{"pct":20,"role":"Cash",     "rationale":"Risk reduction"},
            "BNB":{"pct": 8,"role":"Satellite", "rationale":"Reduced"},
            "SOL":{"pct": 7,"role":"Satellite", "rationale":"Trimmed"},
        },
        "DEFENSIVE": {
            "BTC":{"pct":50,"role":"Core","rationale":"Maximum quality"},
            "USDC":{"pct":35,"role":"Cash","rationale":"Capital preservation"},
            "ETH":{"pct":15,"role":"Core","rationale":"Staking yield only"},
        },
    }

    ETF_PICKS = {
        "BTC exposure":  [{"ticker":"IBIT","name":"iShares Bitcoin Trust (BlackRock)","type":"Spot BTC ETF"},
                          {"ticker":"FBTC","name":"Fidelity Wise Origin Bitcoin Fund","type":"Spot BTC ETF"}],
        "ETH exposure":  [{"ticker":"ETHA","name":"iShares Ethereum Trust (BlackRock)","type":"Spot ETH ETF"},
                          {"ticker":"FETH","name":"Fidelity Ethereum Fund","type":"Spot ETH ETF"}],
        "Broad crypto":  [{"ticker":"BITW","name":"Bitwise 10 Crypto Index Fund","type":"Index Fund"},
                          {"ticker":"HODL","name":"VanEck Bitcoin ETF","type":"Spot BTC ETF"}],
        "Direct holding":[{"ticker":"Binance","name":"Direct purchase on Binance","type":"Spot/Futures"},
                          {"ticker":"MEXC","name":"Direct purchase on MEXC","type":"Spot/Futures"}],
    }

    REGIME_STATS = {
        "ACCUMULATION":{"expected_return":"40-120%","max_drawdown":"35-55%"},
        "GROWTH":      {"expected_return":"20-60%", "max_drawdown":"25-45%"},
        "CAUTION":     {"expected_return":"5-25%",  "max_drawdown":"15-30%"},
        "DEFENSIVE":   {"expected_return":"0-10%",  "max_drawdown":"5-15%"},
    }
    stats = REGIME_STATS.get(regime, REGIME_STATS["GROWTH"])

    ips = (
        "INVESTMENT POLICY STATEMENT\n"
        "Objective: Grow crypto portfolio over 3-5 year horizon\n"
        "Risk Tolerance: High (suitable for sophisticated investors only)\n"
        "Benchmark: Bitcoin total return\n"
        "Rebalancing: Monthly or when any position drifts >10% from target\n"
        "Current Regime: {regime} (F&G={fg})\n"
        "Max Single Position: 50% (BTC only)\n"
        "Minimum Cash Buffer: 10% in USDC at all times\n"
        "Stop-Loss Policy: Exit 25% of position at -20% drawdown from entry\n"
        "Tax Strategy: Hold >12 months for long-term capital gains treatment\n"
        "Leverage Policy: Max 3x on BTC/ETH futures; no leverage on altcoins"
    ).format(regime=regime, fg=fg_val)

    return {
        "framework":       "BlackRock Multi-Asset Allocation",
        "generated":       _now_iso(),
        "regime":          regime,
        "regime_note":     regime_note,
        "fear_greed":      fg_val,
        "allocation":      ALLOCATIONS.get(regime, ALLOCATIONS["GROWTH"]),
        "etf_picks":       ETF_PICKS,
        "expected_return": stats["expected_return"],
        "max_drawdown":    stats["max_drawdown"],
        "ips":             ips,
    }


# ================================================================== #
# FRAMEWORK 6  --  CITADEL QUANT                                     #
# ================================================================== #

def citadel_quant(market_data: dict, price_histories: dict) -> dict:
    logger.info("[Research] Citadel Quant Analysis...")

    PATTERNS = {
        "bitcoin":    "Potential Cup & Handle forming (weekly chart)",
        "ethereum":   "Bull flag consolidation after recent breakout",
        "solana":     "Ascending triangle -- watch breakout above resistance",
        "binancecoin":"Pennant formation -- coiling for directional move",
        "ripple":     "Inverse Head & Shoulders -- neckline near resistance",
        "cardano":    "Descending channel -- needs breakout above upper line",
        "dogecoin":   "No clear pattern -- choppy, meme-driven price action",
        "avalanche-2":"Double bottom forming -- strong if confirms above neckline",
    }

    results = []
    for coin_id in COINS:
        hist   = price_histories.get(coin_id, [])
        closes = [p[1] for p in hist] if hist else []
        if not closes: continue

        price   = closes[-1]
        sma50   = _sma(closes, 50)  if len(closes) >= 50  else price
        sma100  = _sma(closes, 100) if len(closes) >= 100 else price
        sma200  = _sma(closes, 200) if len(closes) >= 200 else price
        rsi_val = _rsi(closes, 14)

        ema12 = _ema(closes, 12)
        ema26 = _ema(closes, 26)
        macd_line   = [ema12[i] - ema26[i] for i in range(len(ema26))]
        signal_line = _ema(macd_line, 9)
        macd_val    = round(macd_line[-1],   4)
        signal_val  = round(signal_line[-1], 4)
        macd_hist   = round(macd_val - signal_val, 4)
        macd_cross  = "Bullish" if macd_val > signal_val else "Bearish"

        bb_mid   = _sma(closes, 20)
        bb_std   = _stddev(closes, 20)
        bb_upper = round(bb_mid + 2 * bb_std, 4)
        bb_lower = round(bb_mid - 2 * bb_std, 4)
        bb_pct_b = round((price - (bb_mid - 2*bb_std)) / max(4*bb_std, 1e-9), 3)

        sr    = _support_resistance(closes)
        highs = [p[2] for p in hist] if hist and len(hist[0]) > 2 else [price * 1.5]
        lows  = [p[3] for p in hist] if hist and len(hist[0]) > 3 else [price * 0.5]
        h52   = max(highs[-252:]) if len(highs) >= 252 else max(highs)
        l52   = min(lows[-252:])  if len(lows)  >= 252 else min(lows)
        fib   = _fibonacci_levels(h52, l52)

        trend_d = "Bullish" if price > sma50  else "Bearish"
        trend_w = "Bullish" if price > sma100 else "Bearish"
        trend_m = "Bullish" if price > sma200 else "Bearish"
        trend_overall = ("Strong Uptrend"   if all(t=="Bullish" for t in [trend_d,trend_w,trend_m]) else
                         "Strong Downtrend" if all(t=="Bearish" for t in [trend_d,trend_w,trend_m]) else "Mixed")

        atr_val = _stddev(closes[-14:], 14) if len(closes) >= 14 else price * 0.03
        entry   = round(price * 1.002, 4)
        sl      = round(price - 1.5 * atr_val, 4)
        tp1     = round(price + 2.0 * atr_val, 4)
        tp2     = round(price + 4.0 * atr_val, 4)
        rr      = round((tp1 - entry) / max(entry - sl, 1e-9), 2)

        results.append({
            "coin":          coin_id,
            "ticker":        COIN_TICKERS.get(coin_id, "?"),
            "price":         round(price, 4),
            "trend_daily":   trend_d,
            "trend_weekly":  trend_w,
            "trend_monthly": trend_m,
            "trend_overall": trend_overall,
            "sma50":         round(sma50,  4),
            "sma100":        round(sma100, 4),
            "sma200":        round(sma200, 4),
            "price_vs_sma50":  round((price/sma50  - 1) * 100, 1),
            "price_vs_sma200": round((price/sma200 - 1) * 100, 1),
            "rsi":           rsi_val,
            "rsi_signal":    ("Overbought" if rsi_val>70 else "Oversold" if rsi_val<30 else "Neutral"),
            "macd":          macd_val,
            "macd_signal":   signal_val,
            "macd_hist":     macd_hist,
            "macd_cross":    macd_cross,
            "bb_upper":      bb_upper,
            "bb_lower":      bb_lower,
            "bb_pct_b":      bb_pct_b,
            "bb_squeeze":    bb_std / bb_mid < 0.04,
            "support_resistance": sr,
            "fibonacci":     fib,
            "chart_pattern": PATTERNS.get(coin_id, "No clear pattern"),
            "entry":         entry,
            "stop_loss":     sl,
            "tp1":           tp1,
            "tp2":           tp2,
            "risk_reward":   rr,
            "h52":           round(h52, 4),
            "l52":           round(l52, 4),
        })

    return {"framework": "Citadel Quantitative Analysis",
            "generated": _now_iso(), "coins": results}


# ================================================================== #
# FRAMEWORK 7  --  HARVARD YIELD                                     #
# ================================================================== #

def harvard_yield(market_data: dict, portfolio_size_usd: float = 10000) -> dict:
    logger.info("[Research] Harvard Yield Strategy...")

    COIN_ID_MAP = {
        "BTC":"bitcoin","ETH":"ethereum","BNB":"binancecoin",
        "SOL":"solana","XRP":"ripple","ADA":"cardano",
        "DOGE":"dogecoin","AVAX":"avalanche-2",
    }

    yields = fetch_staking_yields()
    denom  = sum(v["yield_pct"] * v["safety"] / 100 for v in yields.values())

    results = []
    for coin, y in yields.items():
        coin_id    = COIN_ID_MAP.get(coin, coin.lower())
        price      = market_data.get(coin_id, {}).get("current_price", 1)
        apy        = y["yield_pct"] / 100
        alloc_usd  = (portfolio_size_usd * y["yield_pct"] * y["safety"] / 100
                      / max(denom, 1e-9))
        monthly    = alloc_usd * apy / 12
        drip_10yr  = alloc_usd * ((1 + apy / 12) ** 120) - alloc_usd

        sustainability = (
            "Unsustainable -- inflation-based yield" if apy > 0.15 else
            "Moderate risk -- monitor protocol health" if apy > 0.08 else
            "Sustainable -- protocol-revenue backed"  if apy > 0.02 else
            "No yield / minimal"
        )

        results.append({
            "coin":           coin,
            "coin_id":        coin_id,
            "price":          price,
            "yield_pct":      y["yield_pct"],
            "method":         y["method"],
            "safety_score":   y["safety"],
            "notes":          y["notes"],
            "alloc_usd":      round(alloc_usd, 2),
            "monthly_income": round(monthly, 2),
            "annual_income":  round(monthly * 12, 2),
            "drip_10yr_gain": round(drip_10yr, 2),
            "sustainability": sustainability,
        })

    results.sort(key=lambda x: (-x["safety_score"], -x["yield_pct"]))
    total_monthly = sum(r["monthly_income"] for r in results)
    total_annual  = total_monthly * 12

    return {
        "framework":          "Harvard Endowment Yield Strategy",
        "generated":          _now_iso(),
        "portfolio_size":     portfolio_size_usd,
        "total_monthly":      round(total_monthly, 2),
        "total_annual":       round(total_annual, 2),
        "yield_on_portfolio": round(total_annual / portfolio_size_usd * 100, 2),
        "coins":              results,
    }


# ================================================================== #
# FRAMEWORK 8  --  BAIN COMPETITIVE                                  #
# ================================================================== #

def bain_competitive(market_data: dict) -> dict:
    logger.info("[Research] Bain Competitive Analysis...")

    def _mc_b(cid): return round(market_data.get(cid,{}).get("market_cap",0)/1e9, 1)

    L1_ANALYSIS = {
        "ethereum": {
            "ticker":"ETH","mktcap_b":_mc_b("ethereum"),
            "moat":"Strong -- Network effect + DeFi/NFT monopoly + EIP-1559 burns",
            "tps_current":15,"fees_usd":"$0.50-5.00 (L1) / $0.01-0.10 (L2)",
            "revenue_m":2200,"profit_margin_pct":65,
            "mktshare_trend":"Declining (58% to 47% of smart contract TVL 2022-2024)",
            "mgmt_rating":8,"mgmt_note":"Vitalik Buterin -- long-term thinker, excellent capital allocation",
            "strengths":["Largest developer ecosystem","$50B+ TVL","ETF approved","EIP-1559 deflationary"],
            "weaknesses":["High fees vs competitors","Slow upgrade pace","L2 fragmentation"],
            "opportunities":["Institutional adoption","Real-world asset tokenization","ETH ETF staking"],
            "threats":["Solana speed advantage","Regulatory security classification","L2s cannibalizing fees"],
        },
        "solana": {
            "ticker":"SOL","mktcap_b":_mc_b("solana"),
            "moat":"Moderate -- Speed + cost advantage; centralization concerns",
            "tps_current":2000,"fees_usd":"$0.00025 avg",
            "revenue_m":60,"profit_margin_pct":40,
            "mktshare_trend":"Rising sharply (5% to 18% of smart contract activity 2022-2024)",
            "mgmt_rating":7,"mgmt_note":"Anatoly Yakovenko -- technical excellence, execution focus",
            "strengths":["1000x lower fees than ETH","2000 TPS real","Firedancer 1M TPS","Memecoin/NFT hub"],
            "weaknesses":["5 major network outages 2022-2024","VC token concentration","FTX legacy"],
            "opportunities":["Firedancer mainnet","SOL ETF","DePIN ecosystem","Payments use case"],
            "threats":["ETH L2s catching up on speed","Outage reputation risk","Regulatory scrutiny"],
        },
        "avalanche-2": {
            "ticker":"AVAX","mktcap_b":_mc_b("avalanche-2"),
            "moat":"Moderate -- Subnet architecture unique; enterprise focus",
            "tps_current":4500,"fees_usd":"$0.01-0.10",
            "revenue_m":40,"profit_margin_pct":35,
            "mktshare_trend":"Stable (3-5% of L1 activity)",
            "mgmt_rating":7,"mgmt_note":"Emin Gün Sirer -- academic credibility + business development",
            "strengths":["Subnet architecture","EVM compatible","Institutional subnets","Fast finality"],
            "weaknesses":["Complex architecture","Lower developer mindshare","Smaller ecosystem"],
            "opportunities":["Avalanche9000 -- 99% cost reduction","T-Mobile subnet","Gaming subnets"],
            "threats":["ETH L2s as alternative","SOL speed competition","VC concentration"],
        },
        "cardano": {
            "ticker":"ADA","mktcap_b":_mc_b("cardano"),
            "moat":"Weak -- Academic rigour but slow execution, limited adoption",
            "tps_current":250,"fees_usd":"$0.17 avg",
            "revenue_m":15,"profit_margin_pct":20,
            "mktshare_trend":"Declining (peaked 2021, minimal DeFi activity)",
            "mgmt_rating":5,"mgmt_note":"Charles Hoskinson -- strong narrative, slow delivery vs promises",
            "strengths":["Formal verification","Peer-reviewed research","Low fees","Sustainability focus"],
            "weaknesses":["Very slow development","Minimal real-world usage","Limited DeFi TVL","Missing DApps"],
            "opportunities":["Plomin governance","Hydra scaling","African market focus","Midnight privacy chain"],
            "threats":["All competitors outpacing adoption","Developer migration","Narrative fatigue"],
        },
        "binancecoin": {
            "ticker":"BNB","mktcap_b":_mc_b("binancecoin"),
            "moat":"Moderate -- Exchange captive demand + quarterly burns",
            "tps_current":2200,"fees_usd":"$0.10-0.50",
            "revenue_m":800,"profit_margin_pct":45,
            "mktshare_trend":"Stable but declining (regulatory pressure on Binance)",
            "mgmt_rating":6,"mgmt_note":"Richard Teng (post-CZ) -- rebuilding trust after DOJ settlement",
            "strengths":["Binance volume captive demand","Quarterly token burns","BSC DeFi ecosystem"],
            "weaknesses":["Centralized control","Regulatory risk (ongoing)","Dependent on Binance health"],
            "opportunities":["Opbnb L2 growth","Post-regulatory clarity rally","BNB ETF speculation"],
            "threats":["Exchange regulatory crackdown","CeDeFi competition","User migration to DEXs"],
        },
    }

    EXCHANGES = {
        "Binance":     {"mktshare_pct":38, "vol_b_daily":12,  "moat":"Scale + liquidity network effect"},
        "Coinbase":    {"mktshare_pct":12, "vol_b_daily":3.5, "moat":"US regulatory compliance + institutional trust"},
        "OKX":         {"mktshare_pct":10, "vol_b_daily":3.2, "moat":"Asian market + derivatives depth"},
        "Bybit":       {"mktshare_pct": 9, "vol_b_daily":2.8, "moat":"Perpetuals + copy trading"},
        "Kraken":      {"mktshare_pct": 5, "vol_b_daily":1.5, "moat":"Security reputation + fiat on-ramps"},
        "MEXC":        {"mktshare_pct": 4, "vol_b_daily":1.2, "moat":"New token listings + low fees"},
        "Hyperliquid": {"mktshare_pct": 3, "vol_b_daily":0.8, "moat":"DEX perpetuals -- disruptive model"},
    }

    best_pick = {
        "coin": "Ethereum (ETH)",
        "rationale": (
            "ETH wins the competitive analysis on 4 of 5 criteria: "
            "(1) Strongest moat via network effects + $50B TVL, "
            "(2) Highest revenue ($2.2B/yr protocol fees + deflationary), "
            "(3) Only L1 with spot ETF AND staking yield narrative, "
            "(4) Real-world asset tokenization pipeline (BlackRock BUIDL). "
            "Risk: L2 fee cannibalization. "
            "Price target 12M: $4,500-$6,000 bull case / $1,800 bear case."
        ),
    }

    return {
        "framework":          "Bain Competitive Strategy Analysis",
        "generated":          _now_iso(),
        "l1_landscape":       L1_ANALYSIS,
        "exchange_landscape": EXCHANGES,
        "best_pick":          best_pick,
    }


# ================================================================== #
# HTML REPORT GENERATOR                                               #
# ================================================================== #

def generate_html_report(all_results: dict) -> str:
    g    = all_results["goldman"]
    dcf  = all_results["dcf"]
    bw   = all_results["bridgewater"]
    jp   = all_results["jpmorgan"]
    br   = all_results["blackrock"]
    cit  = all_results["citadel"]
    hv   = all_results["harvard"]
    bain = all_results["bain"]
    ts   = _now_str()

    def _fmt(v, prefix="$", decimals=2):
        if v is None: return "N/A"
        if isinstance(v, (int, float)):
            return "{}{:,.{}f}".format(prefix, v, decimals)
        return str(v)

    def _pct(v):
        if v is None: return "N/A"
        return "{}{:.1f}%".format("+" if v >= 0 else "", v)

    # ── Goldman rows ──────────────────────────────────────────────────
    goldman_rows = ""
    for c in g["coins"][:8]:
        tc  = "#4ade80" if c["trend"] == "Bull" else "#f87171"
        mc  = {"Strong":"#4ade80","Moderate":"#fbbf24","Weak":"#f87171"}.get(c["moat"],"#94a3b8")
        goldman_rows += (
            "<tr><td><b>{ticker}</b></td><td>{price}</td>"
            "<td style='color:{tc}'>{chg30}</td><td>{rsi}</td>"
            "<td style='color:{tc}'>{trend}</td>"
            "<td>{nvt} <small style='color:#94a3b8'>({nvt_r})</small></td>"
            "<td style='color:{mc}'>{moat}</td>"
            "<td style='color:#4ade80'>{bull}</td><td style='color:#f87171'>{bear}</td>"
            "<td style='color:#94a3b8'>{el} – {eh}</td>"
            "<td style='color:#f87171'>{sl}</td></tr>"
        ).format(
            ticker=c["ticker"], price=_fmt(c["price"]),
            chg30=_pct(c["chg_30d"]), rsi=c["rsi"],
            trend=c["trend"], tc=tc, nvt=c["nvt"], nvt_r=c["nvt_rating"],
            moat=c["moat"], mc=mc,
            bull=_fmt(c["bull_target"]), bear=_fmt(c["bear_target"]),
            el=_fmt(c["entry_low"]), eh=_fmt(c["entry_high"]),
            sl=_fmt(c["stop_loss"]),
        )

    # ── DCF rows ──────────────────────────────────────────────────────
    dcf_rows = ""
    for c in dcf["coins"]:
        vc = {"Undervalued":"#4ade80","Fairly Valued":"#fbbf24",
              "Overvalued":"#f87171"}.get(c["verdict"],"#e2e8f0")
        dcf_rows += (
            "<tr><td><b>{ticker}</b></td><td>{price}</td><td>{mc}B</td>"
            "<td>{wacc}%</td><td>{beta}</td>"
            "<td style='color:{vc}'>{iv}</td><td style='color:{vc}'>{up}</td>"
            "<td style='color:{vc}'><b>{verdict}</b></td></tr>"
        ).format(
            ticker=c["ticker"], price=_fmt(c["current_price"]),
            mc=c["mktcap_b"], wacc=c["wacc"], beta=c["beta"],
            iv=_fmt(c["intrinsic_avg"]), up=_pct(c["upside_pct"]),
            verdict=c["verdict"], vc=vc,
        )

    # ── Bridgewater rows ──────────────────────────────────────────────
    bw_rows = ""
    for c in bw["per_coin"]:
        bw_rows += (
            "<tr><td><b>{ticker}</b></td><td>{liq}</td>"
            "<td style='color:#f87171'>{mdd}%</td>"
            "<td style='color:#fbbf24'>{mild}%</td>"
            "<td style='color:#f87171'>{bear}%</td>"
            "<td style='color:#ef4444'>{swan}%</td>"
            "<td>{target}%</td></tr>"
        ).format(
            ticker=c["ticker"], liq=c["liquidity_label"],
            mdd=c["max_drawdown_90d"],
            mild=c["stress_mild"], bear=c["stress_bear"],
            swan=c["stress_blk_swan"], target=c["target_weight"],
        )

    # ── Correlation table ─────────────────────────────────────────────
    cm   = bw["correlation_matrix"]
    keys = list(cm.keys())[:5]
    corr_html = "<table><tr><th></th>" + "".join(
        "<th>{}</th>".format(COIN_TICKERS.get(c,"?")) for c in keys) + "</tr>"
    for a in keys:
        corr_html += "<tr><td><b>{}</b></td>".format(COIN_TICKERS.get(a,"?"))
        for b in keys:
            v  = cm.get(a,{}).get(b, 0)
            bg = ("rgba(74,222,128,0.3)" if v >= 0.7 else
                  "rgba(248,113,113,0.3)" if v <= 0.3 else
                  "rgba(251,191,36,0.2)")
            corr_html += "<td style='background:{}'>{}</td>".format(bg, v)
        corr_html += "</tr>"
    corr_html += "</table>"

    # ── Catalyst rows ─────────────────────────────────────────────────
    cat_rows = ""
    for c in jp["coins"]:
        pc = "#4ade80" if "BUY" in c["recommended_play"] else "#fbbf24"
        for cat in c["catalysts"][:1]:
            cat_rows += (
                "<tr><td><b>{ticker}</b></td><td>{event}</td>"
                "<td style='color:#94a3b8'><small>{timing}</small></td>"
                "<td style='color:#94a3b8'><small>{impact}</small></td>"
                "<td style='color:{pc}'>{play}</td></tr>"
            ).format(
                ticker=c["ticker"], event=cat["event"],
                timing=cat["timing"], impact=cat["impact"][:50],
                play=c["recommended_play"][:40], pc=pc,
            )

    # ── Allocation rows ───────────────────────────────────────────────
    alloc_rows = ""
    for ticker, info in br["allocation"].items():
        rc = ("#4ade80" if info["role"]=="Core" else
              "#fbbf24" if info["role"]=="Satellite" else "#94a3b8")
        alloc_rows += (
            "<tr><td><b>{t}</b></td><td style='color:{rc}'>{role}</td>"
            "<td><b>{pct}%</b></td>"
            "<td style='color:#94a3b8'><small>{rat}</small></td></tr>"
        ).format(t=ticker, role=info["role"], rc=rc,
                 pct=info["pct"], rat=info["rationale"])

    # ── Citadel rows ──────────────────────────────────────────────────
    cit_rows = ""
    for c in cit["coins"]:
        tdc  = "#4ade80" if c["trend_daily"]=="Bullish" else "#f87171"
        rsic = ("#f87171" if c["rsi"]>70 else "#4ade80" if c["rsi"]<30 else "#fbbf24")
        cit_rows += (
            "<tr><td><b>{ticker}</b></td>"
            "<td style='color:{tdc}'>{td}</td><td>{tw}</td><td>{tm}</td>"
            "<td>{sma50}</td><td>{sma200}</td>"
            "<td style='color:{rc}'>{rsi}</td><td>{macd_c}</td>"
            "<td>{bb}</td><td style='color:#94a3b8'>{entry}</td>"
            "<td style='color:#f87171'>{sl}</td>"
            "<td style='color:#4ade80'>{tp1}</td><td>{rr}:1</td></tr>"
        ).format(
            ticker=c["ticker"], td=c["trend_daily"], tdc=tdc,
            tw=c["trend_weekly"], tm=c["trend_monthly"],
            sma50=_fmt(c["sma50"]), sma200=_fmt(c["sma200"]),
            rsi=c["rsi"], rc=rsic, macd_c=c["macd_cross"],
            bb="{:.2f}".format(c["bb_pct_b"]),
            entry=_fmt(c["entry"]), sl=_fmt(c["stop_loss"]),
            tp1=_fmt(c["tp1"]), rr=c["risk_reward"],
        )

    # ── Harvard rows ──────────────────────────────────────────────────
    hv_rows = ""
    for c in hv["coins"]:
        yc = "#4ade80" if c["yield_pct"] >= 3 else "#94a3b8"
        hv_rows += (
            "<tr><td><b>{coin}</b></td>"
            "<td style='color:{yc}'>{yld}%</td><td>{safety}/10</td>"
            "<td>${monthly}</td><td>${annual}</td>"
            "<td>${drip}</td>"
            "<td style='color:#94a3b8'><small>{sust}</small></td></tr>"
        ).format(
            coin=c["coin"], yc=yc, yld=c["yield_pct"],
            safety=c["safety_score"], monthly=c["monthly_income"],
            annual=c["annual_income"], drip=int(c["drip_10yr_gain"]),
            sust=c["sustainability"],
        )

    # ── ETF html ──────────────────────────────────────────────────────
    etf_html = ""
    for cat, picks in br["etf_picks"].items():
        etf_html += "<div style='margin:8px 0'><b style='color:#7dd3fc'>{}</b><br>".format(cat)
        for e in picks:
            etf_html += (
                "<div style='margin:2px 0 2px 8px'>"
                "<span class='tag'>{ticker}</span> {name} "
                "<small style='color:#94a3b8'>({type})</small></div>"
            ).format(**e)
        etf_html += "</div>"

    # ── Bain L1 html ──────────────────────────────────────────────────
    moat_colors = {"Strong":"#4ade80","Moderate":"#fbbf24","Weak":"#f87171"}
    bain_l1_html = ""
    for name, v in bain["l1_landscape"].items():
        moat_short = v["moat"].split("--")[0].strip()
        mc = moat_colors.get(moat_short, "#94a3b8")
        bain_l1_html += (
            "<div class='card'><div class='grid2'><div>"
            "<span class='firm'>{ticker}</span> <b>{name}</b> · "
            "<span class='badge' style='background:{mc};color:#0f172a'>{moat_short}</span><br>"
            "Mkt Cap: ${mcap}B · Revenue: ${rev}M/yr · TPS: {tps} · Fees: {fees}<br>"
            "Mkt Share: <span style='color:#94a3b8'>{mktshare}</span><br>"
            "Management: {mgmt_stars}/10 — {mgmt_note}"
            "</div><div>"
            "<small style='color:#4ade80'>Strengths:</small> {s}<br>"
            "<small style='color:#f87171'>Weaknesses:</small> {w}<br>"
            "<small style='color:#fbbf24'>Opportunities:</small> {o}<br>"
            "<small style='color:#94a3b8'>Threats:</small> {t}"
            "</div></div></div>"
        ).format(
            ticker=v["ticker"], name=name, mcap=v["mktcap_b"],
            moat_short=moat_short, mc=mc,
            rev=v["revenue_m"], fees=v["fees_usd"],
            tps=v.get("tps_current","N/A"),
            mktshare=v["mktshare_trend"],
            mgmt_stars=v["mgmt_rating"],
            mgmt_note=v["mgmt_note"],
            s=" · ".join(v["strengths"]),
            w=" · ".join(v["weaknesses"]),
            o=" · ".join(v["opportunities"]),
            t=" · ".join(v["threats"]),
        )

    # ── Tail risks ────────────────────────────────────────────────────
    tail_risks_html = "".join(
        "<div class='card' style='margin:4px 0;padding:8px 12px'>"
        "<span class='badge' style='background:#1e3a5f;color:#f87171'>{probability}</span> "
        "<b>{scenario}</b> · {impact} · Est DD: {estimated_drawdown} · "
        "<span style='color:#94a3b8'>Hedge: {hedge}</span></div>".format(**r)
        for r in bw["tail_risks"]
    )

    # ── Hedges -- FIX: use correct key names ──────────────────────────
    hedges_html = "".join(
        "<div class='card' style='margin:4px 0;padding:8px 12px'>"
        "<b>{risk}</b> &rarr; {strategy} "
        "<span class='tag'>{instrument}</span> "
        "<span class='tag'>{size}</span></div>".format(
            risk=h["risk"],
            strategy=h["strategy"],
            instrument=h["instrument"],
            size=h["size"],
        )
        for h in bw["hedging_strategies"]
    )

    # ── Fibonacci html ────────────────────────────────────────────────
    fib_html = "".join(
        "<div class='card' style='margin:4px 0;padding:8px 12px'>"
        "<b>{ticker}</b> · Pattern: <i style='color:#fbbf24'>{pat}</i> · "
        "Fib levels (52w: ${lo} – ${hi}): ".format(
            ticker=c["ticker"], pat=c["chart_pattern"],
            lo=_fmt(c["l52"]), hi=_fmt(c["h52"]))
        + " | ".join("{}: {}".format(k, _fmt(v)) for k, v in c["fibonacci"].items())
        + "</div>"
        for c in cit["coins"]
    )

    # ── Exchange html ─────────────────────────────────────────────────
    exchange_html = (
        "<table><tr><th>Exchange</th><th>Market Share</th>"
        "<th>Daily Vol ($B)</th><th>Moat</th></tr>"
        + "".join(
            "<tr><td><b>{}</b></td><td>{:.0f}%</td><td>${:.1f}B</td>"
            "<td style='color:#94a3b8'>{}</td></tr>".format(
                k, v["mktshare_pct"], v["vol_b_daily"], v["moat"])
            for k, v in bain["exchange_landscape"].items()
        )
        + "</table>"
    )

    regime_c = {"ACCUMULATION":"#4ade80","GROWTH":"#34d399",
                "CAUTION":"#fbbf24","DEFENSIVE":"#f87171"}.get(br["regime"],"#94a3b8")
    bp = bain["best_pick"]

    # ── Assemble HTML ─────────────────────────────────────────────────
    html = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Institutional Crypto Research Report</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{background:#0f172a;color:#e2e8f0;font-family:'Segoe UI',monospace;font-size:13px;line-height:1.5}}
.container{{max-width:1400px;margin:0 auto;padding:24px}}
h1{{color:#38bdf8;font-size:24px;margin-bottom:4px}}
h2{{color:#7dd3fc;font-size:16px;border-bottom:1px solid #1e3a5f;padding-bottom:6px;margin:28px 0 14px}}
h3{{color:#94a3b8;font-size:13px;margin:12px 0 6px;text-transform:uppercase;letter-spacing:1px}}
.meta{{color:#64748b;font-size:12px;margin-bottom:20px}}
.card{{background:#1e293b;border-radius:8px;padding:16px 20px;margin:10px 0;border:1px solid #1e3a5f}}
.badge{{display:inline-block;padding:3px 10px;border-radius:12px;font-size:11px;font-weight:bold;margin:2px}}
table{{border-collapse:collapse;width:100%;margin:8px 0;font-size:12px}}
th{{background:#0f172a;color:#94a3b8;padding:7px 10px;text-align:left;font-size:11px;text-transform:uppercase;letter-spacing:.5px;border-bottom:1px solid #334155}}
td{{padding:6px 10px;border-bottom:1px solid #1e293b;vertical-align:top}}
tr:hover td{{background:rgba(30,58,95,0.2)}}
.grid2{{display:grid;grid-template-columns:1fr 1fr;gap:16px;margin:10px 0}}
.grid3{{display:grid;grid-template-columns:1fr 1fr 1fr;gap:16px;margin:10px 0}}
.stat{{background:#0f172a;border-radius:6px;padding:12px 16px;text-align:center}}
.stat-val{{font-size:22px;font-weight:bold;color:#38bdf8}}
.stat-lbl{{font-size:11px;color:#64748b;margin-top:2px}}
.section-header{{background:linear-gradient(90deg,#1e293b,#0f172a);border-left:3px solid #38bdf8;padding:10px 16px;margin:24px 0 12px;border-radius:0 6px 6px 0}}
.firm{{font-size:11px;color:#38bdf8;font-weight:bold;letter-spacing:1px;text-transform:uppercase}}
.tag{{display:inline-block;background:#1e3a5f;color:#7dd3fc;padding:2px 8px;border-radius:4px;font-size:11px;margin:2px}}
</style>
</head>
<body>
<div class="container">

<h1>&#127970; Institutional Crypto Research Report</h1>
<p class="meta">Generated: {ts} &nbsp;|&nbsp; 8 Analyst Frameworks &nbsp;|&nbsp;
BTC · ETH · BNB · SOL · XRP · ADA · DOGE · AVAX &nbsp;|&nbsp; Binance + MEXC</p>

<div class="grid3">
  <div class="stat">
    <div class="stat-val" style="color:{regime_c}">{regime}</div>
    <div class="stat-lbl">Market Regime (BlackRock)</div>
  </div>
  <div class="stat">
    <div class="stat-val">{fg}</div>
    <div class="stat-lbl">Fear &amp; Greed Index</div>
  </div>
  <div class="stat">
    <div class="stat-val">${hv_monthly}/mo</div>
    <div class="stat-lbl">Staking Yield on $10K (Harvard)</div>
  </div>
</div>

<div class="section-header">
  <div class="firm">Goldman Sachs · Equity Research</div>
  <h2 style="margin:4px 0 0;border:none">Framework 1 — Crypto Screener</h2>
</div>
<p style="color:#94a3b8;margin-bottom:10px">Score: Momentum 30% · Volume 20% · Trend 20% · RSI 15% · Mkt Cap 15%</p>
<table>
  <tr><th>Ticker</th><th>Price</th><th>30d</th><th>RSI</th><th>Trend</th>
  <th>NVT (P/E proxy)</th><th>Moat</th><th>Bull Target</th><th>Bear Target</th>
  <th>Entry Zone</th><th>Stop-Loss</th></tr>
  {goldman_rows}
</table>
<h3>Competitive Moat Detail</h3>
{moat_detail}

<div class="section-header">
  <div class="firm">Morgan Stanley · Investment Banking</div>
  <h2 style="margin:4px 0 0;border:none">Framework 2 — DCF Valuation</h2>
</div>
<p style="color:#94a3b8;margin-bottom:10px">WACC = 4.5% rf + β × 8% ERP · Terminal: 20x exit + 3% perpetuity</p>
<table>
  <tr><th>Ticker</th><th>Price</th><th>Mkt Cap</th><th>WACC</th><th>Beta</th>
  <th>DCF Fair Value</th><th>Upside</th><th>Verdict</th></tr>
  {dcf_rows}
</table>
<h3>5-Year FCF &amp; Sensitivity</h3>
{dcf_sensitivity}

<div class="section-header">
  <div class="firm">Bridgewater Associates · Risk Management</div>
  <h2 style="margin:4px 0 0;border:none">Framework 3 — Portfolio Risk Analysis</h2>
</div>
<div class="grid2">
  <div class="card"><h3>Sector Concentration</h3>{sector_exposure}</div>
  <div class="card"><h3>Correlation Matrix (90-day)</h3>{corr_html}</div>
</div>
<h3>Stress Test &amp; Liquidity</h3>
<table>
  <tr><th>Coin</th><th>Liquidity</th><th>90d Max DD</th>
  <th>Mild (-30%)</th><th>Bear (-60%)</th><th>Black Swan (-85%)</th><th>Target %</th></tr>
  {bw_rows}
</table>
<h3>Tail Risk Scenarios</h3>{tail_risks_html}
<h3>Hedging Strategies</h3>{hedges_html}

<div class="section-header">
  <div class="firm">JPMorgan Chase · Equity Research</div>
  <h2 style="margin:4px 0 0;border:none">Framework 4 — Catalyst Calendar</h2>
</div>
<div class="card" style="background:#0f172a;margin-bottom:12px">
  F&amp;G: {fg} ({fg_trend} · 7d avg: {fg_7d_avg}) &nbsp;|&nbsp; Signal: {fg_signal}
</div>
<table>
  <tr><th>Ticker</th><th>Key Catalyst</th><th>Timing</th><th>Impact</th><th>Play</th></tr>
  {cat_rows}
</table>

<div class="section-header">
  <div class="firm">BlackRock · Portfolio Strategy</div>
  <h2 style="margin:4px 0 0;border:none">Framework 5 — Multi-Asset Allocation</h2>
</div>
<div class="card">
  <b>Regime: <span style="color:{regime_c}">{regime}</span></b> — {regime_note}<br>
  <b>Expected Return:</b> {exp_ret} &nbsp;|&nbsp; <b>Max Drawdown:</b> {max_dd}
</div>
<div class="grid2">
  <div>
    <h3>Allocation</h3>
    <table><tr><th>Asset</th><th>Role</th><th>Weight</th><th>Rationale</th></tr>
    {alloc_rows}</table>
  </div>
  <div><h3>ETF &amp; Fund Picks</h3>{etf_html}</div>
</div>
<div class="card" style="background:#0f172a;white-space:pre-line;font-size:12px">{ips}</div>

<div class="section-header">
  <div class="firm">Citadel · Quantitative Trading</div>
  <h2 style="margin:4px 0 0;border:none">Framework 6 — Technical Analysis</h2>
</div>
<table>
  <tr><th>Ticker</th><th>Daily</th><th>Weekly</th><th>Monthly</th>
  <th>SMA50</th><th>SMA200</th><th>RSI</th><th>MACD</th><th>BB%B</th>
  <th>Entry</th><th>Stop</th><th>TP1</th><th>R:R</th></tr>
  {cit_rows}
</table>
<h3>Chart Patterns &amp; Fibonacci</h3>{fib_html}

<div class="section-header">
  <div class="firm">Harvard Endowment · Income Strategy</div>
  <h2 style="margin:4px 0 0;border:none">Framework 7 — Staking Yield &amp; DRIP</h2>
</div>
<div class="card" style="background:#0f172a">
  Portfolio $10,000 &nbsp;|&nbsp; Monthly: ${hv_monthly} &nbsp;|&nbsp;
  Annual Yield: {hv_yield}% &nbsp;|&nbsp; 10yr DRIP: ${hv_10yr}
</div>
<table>
  <tr><th>Coin</th><th>APY</th><th>Safety</th><th>Monthly</th>
  <th>Annual</th><th>10yr DRIP</th><th>Sustainability</th></tr>
  {hv_rows}
</table>
<p style="color:#94a3b8;margin-top:8px"><small>Verify current yields on protocol websites before staking.</small></p>

<div class="section-header">
  <div class="firm">Bain &amp; Company · Competitive Strategy</div>
  <h2 style="margin:4px 0 0;border:none">Framework 8 — L1 Competitive Landscape</h2>
</div>
{bain_l1_html}
<div class="card" style="border:1px solid #4ade80;margin-top:16px">
  <div class="firm" style="color:#4ade80">SINGLE BEST PICK</div>
  <h3 style="color:#4ade80;margin:6px 0">{bp_coin}</h3>
  <p>{bp_rationale}</p>
</div>
<h3>Exchange Landscape</h3>
{exchange_html}

<div style="margin-top:32px;padding:16px;background:#1e293b;border-radius:8px;font-size:11px;color:#64748b">
  <b style="color:#94a3b8">DISCLAIMER:</b> Informational purposes only. Not financial advice.
  Crypto is highly volatile. Past performance does not guarantee future results.
  Sources: CoinGecko (free API), Alternative.me, Binance public API.
  Generated: {ts}
</div>
</div>
</body>
</html>""".format(
        ts=ts, regime=br["regime"], regime_c=regime_c,
        regime_note=br["regime_note"],
        fg=jp["fear_greed_current"],
        fg_trend=jp["fear_greed_trend"],
        fg_7d_avg=jp["fear_greed_7d_avg"],
        fg_signal=("Extreme Fear = Buy Zone" if jp["fear_greed_current"] < 25 else
                   "Extreme Greed = Caution" if jp["fear_greed_current"] > 75 else "Neutral"),
        hv_monthly=hv["total_monthly"],
        hv_yield=hv["yield_on_portfolio"],
        hv_10yr=int(sum(c["drip_10yr_gain"] for c in hv["coins"])),
        exp_ret=br["expected_return"],
        max_dd=br["max_drawdown"],
        goldman_rows=goldman_rows,
        moat_detail="".join(
            "<div class='card' style='padding:8px 12px;margin:4px 0'>"
            "<span class='badge' style='background:{c};color:#0f172a'>{m}</span> "
            "<b>{t}</b> — {d}</div>".format(
                c={"Strong":"#4ade80","Moderate":"#fbbf24","Weak":"#f87171"}.get(c["moat"],"#94a3b8"),
                m=c["moat"], t=c["ticker"], d=c["moat_detail"])
            for c in g["coins"]
        ),
        dcf_rows=dcf_rows,
        dcf_sensitivity="".join(
            "<div class='card' style='margin:4px 0;padding:8px 12px'>"
            "<b>{}</b> WACC {}% · FCF: {} · Sensitivity: {}</div>".format(
                c["ticker"], c["wacc"],
                ", ".join("{}:${}M".format(k,v) for k,v in c["fcf_projections"].items()),
                " | ".join("{}: {}".format(k, _fmt(v)) for k,v in c["sensitivity"].items()))
            for c in dcf["coins"]
        ),
        corr_html=corr_html,
        sector_exposure="".join(
            "<div style='margin:4px 0'><span style='color:#38bdf8;font-weight:bold'>{}</span>: {}%</div>".format(k,v)
            for k,v in bw["sector_exposure"].items()
        ),
        bw_rows=bw_rows,
        tail_risks_html=tail_risks_html,
        hedges_html=hedges_html,
        cat_rows=cat_rows,
        alloc_rows=alloc_rows,
        etf_html=etf_html,
        ips=br["ips"],
        cit_rows=cit_rows,
        fib_html=fib_html,
        hv_rows=hv_rows,
        bain_l1_html=bain_l1_html,
        exchange_html=exchange_html,
        bp_coin=bp["coin"],
        bp_rationale=bp["rationale"],
    )

    return html


# ================================================================== #
# MAIN RUNNER                                                         #
# ================================================================== #

def run():
    try:
        setup_logger()
    except Exception:
        pass

    logger.info("=" * 60)
    logger.info("  INSTITUTIONAL RESEARCH ENGINE  --  8 Frameworks")
    logger.info("=" * 60)

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("[Research] Step 1/3 -- Fetching market data...")
    market_data = fetch_market_data()
    if not market_data:
        logger.error("[Research] Failed to fetch market data. Check internet connection.")
        return

    logger.info("[Research] Step 2/3 -- Fetching historical prices (1-2 min)...")
    price_histories = {}
    for coin_id in COINS:
        hist = fetch_historical_prices(coin_id, days=365)
        if hist:
            price_histories[coin_id] = hist
        time.sleep(1.5)   # respect CoinGecko rate limit

    fear_greed  = fetch_fear_greed()
    fg_val      = int(fear_greed.get("current", {}).get("value", 50))

    logger.info("[Research] Step 3/3 -- Running 8 frameworks...")

    all_results = {
        "goldman":     goldman_screener(market_data, price_histories),
        "dcf":         morgan_stanley_dcf(market_data, price_histories),
        "bridgewater": bridgewater_risk(market_data, price_histories),
        "jpmorgan":    jpmorgan_catalyst(market_data, fear_greed),
        "blackrock":   blackrock_allocation(market_data, fg_val),
        "citadel":     citadel_quant(market_data, price_histories),
        "harvard":     harvard_yield(market_data, portfolio_size_usd=10000),
        "bain":        bain_competitive(market_data),
        "generated_at":_now_iso(),
        "fear_greed":  fg_val,
    }

    # Save JSON
    json_path = OUTPUT_DIR / "report_{}.json".format(_now_date())
    json_path.write_text(
        json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    JSON_LATEST.write_text(
        json.dumps(all_results, indent=2, default=str), encoding="utf-8")
    logger.info("[Research] JSON saved: {}".format(json_path))

    # Generate HTML
    logger.info("[Research] Generating HTML report...")
    html = generate_html_report(all_results)
    HTML_OUT.write_text(html, encoding="utf-8")
    logger.info("[Research] HTML saved: {}".format(HTML_OUT.resolve()))

    # Log summary
    g = all_results["goldman"]
    logger.info("[Research] === SUMMARY ===")
    for c in g["coins"][:3]:
        logger.info("[Research]   {} score={} moat={} 30d={:+.1f}% bull={} bear={}".format(
            c["ticker"], c["score"], c["moat"],
            c["chg_30d"], c["bull_target"], c["bear_target"]))
    logger.info("[Research] BlackRock regime: {} | F&G={}".format(
        all_results["blackrock"]["regime"], fg_val))
    logger.info("[Research] Bain best pick: {}".format(
        all_results["bain"]["best_pick"]["coin"]))
    logger.info("[Research] Done. Open: {}".format(HTML_OUT.resolve()))
    return all_results


if __name__ == "__main__":
    run()
