"""
research/research_engine.py  --  Institutional-Grade Crypto Research Engine

8 frameworks adapted from Wall Street methodology for crypto markets:

  1. EQUITY SCREENER      (Goldman Sachs style)
     Top 15 coins by momentum, NVT ratio, volume, trend strength
     Moat ratings, bull/bear price targets, entry zones + stop-loss

  2. VALUATION MODEL      (Morgan Stanley DCF style)
     5-year revenue projection, FCF estimates, WACC equivalent
     Terminal value (exit multiple + perpetuity), DCF vs market price

  3. RISK ANALYSIS        (Bridgewater / Ray Dalio style)
     Correlation matrix, concentration risk, recession stress test
     Liquidity ratings, tail risk scenarios, hedging strategies

  4. EARNINGS PREVIEW     (JPMorgan style)
     Last 4 quarters price performance vs expectations
     Upcoming catalysts, key metrics Wall St watches
     Options-implied move equivalent, bull/bear scenarios

  5. PORTFOLIO STRATEGY   (BlackRock multi-asset style)
     Exact allocation % across BTC/ETH/alts/stablecoins
     Core vs satellite positions, expected return + max drawdown
     Tax efficiency, Investment Policy Statement

  6. QUANT TRADING        (Citadel style)
     Multi-timeframe trend direction, support/resistance levels
     MA analysis, RSI/MACD/BB readings, chart patterns
     Fibonacci levels, entry/stop/target with R:R ratio

  7. YIELD STRATEGY       (Harvard Endowment style)
     Staking yield rankings, sustainability scores
     DRIP compounding projections, safety ratings

  8. COMPETITIVE ANALYSIS (Bain & Company style)
     L1/L2 competitive landscape, market share trends
     Protocol revenue, moat analysis, SWOT for top 2
     Single best pick with investment rationale

Data sources (all free, no API keys):
  CoinGecko API    -- prices, market caps, volumes, metadata
  Alternative.me   -- Fear & Greed Index
  CryptoCompare    -- news, social sentiment

Output: data/research/daily_report.html  (opens in browser)
        data/research/daily_report.json  (structured data)
"""

from __future__ import annotations

import json
import math
import time
import urllib.request
import urllib.error
from datetime import datetime, timedelta
from pathlib  import Path
from loguru   import logger

RESEARCH_DIR = Path("data/research")
REPORT_HTML  = RESEARCH_DIR / "daily_report.html"
REPORT_JSON  = RESEARCH_DIR / "daily_report.json"

# Coins to analyse (top liquid crypto)
UNIVERSE = [
    {"symbol": "BTC",  "id": "bitcoin",         "name": "Bitcoin"},
    {"symbol": "ETH",  "id": "ethereum",         "name": "Ethereum"},
    {"symbol": "BNB",  "id": "binancecoin",      "name": "BNB"},
    {"symbol": "SOL",  "id": "solana",           "name": "Solana"},
    {"symbol": "XRP",  "id": "ripple",           "name": "XRP"},
    {"symbol": "ADA",  "id": "cardano",          "name": "Cardano"},
    {"symbol": "AVAX", "id": "avalanche-2",      "name": "Avalanche"},
    {"symbol": "DOT",  "id": "polkadot",         "name": "Polkadot"},
    {"symbol": "MATIC","id": "matic-network",    "name": "Polygon"},
    {"symbol": "LINK", "id": "chainlink",        "name": "Chainlink"},
    {"symbol": "ATOM", "id": "cosmos",           "name": "Cosmos"},
    {"symbol": "DOGE", "id": "dogecoin",         "name": "Dogecoin"},
    {"symbol": "LTC",  "id": "litecoin",         "name": "Litecoin"},
    {"symbol": "NEAR", "id": "near",             "name": "NEAR Protocol"},
    {"symbol": "ARB",  "id": "arbitrum",         "name": "Arbitrum"},
]

STABLECOIN_YIELD_PCT = 4.5  # approximate USDC/USDT staking/lending yield


# ============================================================
# HTTP helper
# ============================================================

def _get(url: str, timeout: int = 10) -> dict | list | None:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "CryptoResearch/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return json.loads(r.read().decode())
    except Exception as e:
        logger.debug("[Research] fetch {}: {}".format(url[:60], e))
        return None


# ============================================================
# Data fetchers
# ============================================================

def fetch_market_data() -> dict:
    """Fetch price, market cap, volume, change data for all coins."""
    ids = ",".join(c["id"] for c in UNIVERSE)
    url = ("https://api.coingecko.com/api/v3/coins/markets"
           "?vs_currency=usd&ids={}&order=market_cap_desc"
           "&per_page=50&page=1"
           "&price_change_percentage=1h,24h,7d,30d".format(ids))
    raw = _get(url)
    if not raw:
        return {}
    return {item["id"]: item for item in raw}


def fetch_coin_detail(coin_id: str) -> dict:
    """Fetch detailed data for one coin (supply, ATH, etc.)."""
    url = ("https://api.coingecko.com/api/v3/coins/{}?"
           "localization=false&tickers=false&market_data=true"
           "&community_data=false&developer_data=false".format(coin_id))
    return _get(url) or {}


def fetch_historical_prices(coin_id: str, days: int = 365) -> list:
    """Fetch daily OHLC for up to 365 days."""
    url = ("https://api.coingecko.com/api/v3/coins/{}/ohlc"
           "?vs_currency=usd&days={}".format(coin_id, days))
    return _get(url) or []


def fetch_fear_greed() -> dict:
    raw = _get("https://api.alternative.me/fng/?limit=30")
    if not raw:
        return {"value": 50, "label": "Neutral", "history": []}
    data = raw.get("data", [])
    current = data[0] if data else {}
    return {
        "value":   int(current.get("value", 50)),
        "label":   current.get("value_classification", "Neutral"),
        "history": [{"value": int(d["value"]), "date": d["timestamp"]}
                    for d in data[:30]],
    }


def fetch_global() -> dict:
    raw = _get("https://api.coingecko.com/api/v3/global")
    if not raw:
        return {}
    d = raw.get("data", {})
    mkt = d.get("market_cap_percentage", {})
    return {
        "total_market_cap":  d.get("total_market_cap", {}).get("usd", 0),
        "total_volume_24h":  d.get("total_volume", {}).get("usd", 0),
        "btc_dominance":     round(mkt.get("btc", 0), 1),
        "eth_dominance":     round(mkt.get("eth", 0), 1),
        "mkt_chg_24h":       round(d.get("market_cap_change_percentage_24h_usd", 0), 2),
        "active_coins":      d.get("active_cryptocurrencies", 0),
    }


# ============================================================
# Framework 1 -- Goldman Sachs Equity Screener
# ============================================================

def run_screener(market: dict) -> dict:
    """
    Score and rank all coins across 6 criteria:
      1. Momentum score  (1d + 7d + 30d price change)
      2. Volume/MCap ratio  (proxy for liquidity & activity)
      3. Market cap rank  (quality filter)
      4. Price vs 30d high  (proximity to breakout)
      5. Trend strength  (ATH drawdown -- lower is stronger)
      6. Moat rating  (protocol classification)
    """
    MOAT = {
        "bitcoin":      ("Strong",   "Network effect, store of value, scarcity"),
        "ethereum":     ("Strong",   "Developer ecosystem, DeFi/NFT infrastructure"),
        "binancecoin":  ("Moderate", "Exchange utility, burn mechanism"),
        "solana":       ("Moderate", "High throughput, growing ecosystem"),
        "ripple":       ("Moderate", "Banking partnerships, payment rails"),
        "chainlink":    ("Strong",   "Oracle monopoly, data infrastructure"),
        "arbitrum":     ("Moderate", "L2 scaling, growing TVL"),
        "near":         ("Moderate", "Sharding architecture, developer grants"),
        "cosmos":       ("Moderate", "Inter-blockchain communication hub"),
        "cardano":      ("Weak",     "Research-driven, slow adoption"),
        "avalanche-2":  ("Moderate", "Subnet architecture, institutional focus"),
        "matic-network":("Moderate", "Ethereum scaling, gaming/NFT ecosystem"),
        "polkadot":     ("Moderate", "Parachain model, cross-chain interop"),
        "dogecoin":     ("Weak",     "Community/meme driven, no utility"),
        "litecoin":     ("Weak",     "Silver to BTC, declining relevance"),
    }

    ranked = []
    for coin in UNIVERSE:
        cid  = coin["id"]
        data = market.get(cid, {})
        if not data:
            continue

        price      = data.get("current_price", 0) or 0
        mcap       = data.get("market_cap", 0) or 0
        vol_24h    = data.get("total_volume", 0) or 0
        chg_1h     = data.get("price_change_percentage_1h_in_currency", 0) or 0
        chg_24h    = data.get("price_change_percentage_24h_in_currency", 0) or 0
        chg_7d     = data.get("price_change_percentage_7d_in_currency", 0) or 0
        chg_30d    = data.get("price_change_percentage_30d_in_currency", 0) or 0
        ath        = data.get("ath", price) or price
        ath_pct    = data.get("ath_change_percentage", -50) or -50
        mcap_rank  = data.get("market_cap_rank", 99) or 99

        if price <= 0:
            continue

        # Momentum score (weighted: 30d=40%, 7d=35%, 24h=25%)
        momentum = (chg_30d * 0.40 + chg_7d * 0.35 + chg_24h * 0.25)

        # Volume/MCap ratio (higher = more active relative to size)
        vol_ratio = (vol_24h / mcap * 100) if mcap > 0 else 0

        # ATH drawdown score (less negative = closer to ATH = stronger)
        ath_score = max(0, 100 + ath_pct)  # 0-100

        # Rank score (top 10 get bonus)
        rank_score = max(0, 20 - mcap_rank * 1.5)

        # Composite score
        composite = (
            momentum  * 0.35 +
            vol_ratio * 0.20 +
            ath_score * 0.25 +
            rank_score* 0.20
        )

        moat_str, moat_reason = MOAT.get(cid, ("Weak", "Limited differentiation"))

        # Price targets (simplified momentum-based projection)
        bull_target = round(price * (1 + 0.35 + max(0, chg_30d / 100) * 0.5), 4)
        bear_target = round(price * (1 - 0.20 + min(0, chg_30d / 100) * 0.3), 4)

        # Support / resistance from ATH
        support1    = round(price * 0.92, 4)
        support2    = round(price * 0.85, 4)
        resistance1 = round(price * 1.08, 4)

        # Stop-loss below key support
        stop_loss   = round(price * 0.88, 4)

        # Entry zone
        entry_low   = round(price * 0.97, 4)
        entry_high  = round(price * 1.02, 4)

        # NVT-like ratio (market cap / volume proxy for value vs activity)
        nvt = round(mcap / vol_24h, 1) if vol_24h > 0 else 0

        ranked.append({
            "rank":         0,
            "symbol":       coin["symbol"],
            "name":         coin["name"],
            "price":        price,
            "mcap_b":       round(mcap / 1e9, 2),
            "vol_24h_b":    round(vol_24h / 1e9, 3),
            "chg_24h":      round(chg_24h, 2),
            "chg_7d":       round(chg_7d, 2),
            "chg_30d":      round(chg_30d, 2),
            "ath_drawdown": round(ath_pct, 1),
            "nvt_ratio":    nvt,
            "moat":         moat_str,
            "moat_reason":  moat_reason,
            "composite":    round(composite, 2),
            "bull_12m":     bull_target,
            "bear_12m":     bear_target,
            "entry_low":    entry_low,
            "entry_high":   entry_high,
            "stop_loss":    stop_loss,
            "support1":     support1,
            "support2":     support2,
            "resistance1":  resistance1,
            "upside_pct":   round((bull_target / price - 1) * 100, 1),
            "downside_pct": round((bear_target / price - 1) * 100, 1),
        })

    ranked.sort(key=lambda x: x["composite"], reverse=True)
    for i, r in enumerate(ranked, 1):
        r["rank"] = i

    top15 = ranked[:15]

    # P/E equivalent: NVT ratio vs sector average
    avg_nvt = sum(r["nvt_ratio"] for r in top15 if r["nvt_ratio"] > 0) / max(
        sum(1 for r in top15 if r["nvt_ratio"] > 0), 1)

    for r in top15:
        r["nvt_vs_avg"] = round(r["nvt_ratio"] - avg_nvt, 1)
        r["nvt_verdict"] = ("Overvalued" if r["nvt_vs_avg"] > 5
                            else "Undervalued" if r["nvt_vs_avg"] < -5
                            else "Fair Value")

    return {
        "top15":    top15,
        "avg_nvt":  round(avg_nvt, 1),
        "generated": datetime.now().isoformat(),
    }


# ============================================================
# Framework 2 -- Morgan Stanley Valuation Model
# ============================================================

def run_valuation(market: dict, coin_id: str = "bitcoin") -> dict:
    """DCF-equivalent valuation for a crypto asset."""
    data = market.get(coin_id, {})
    coin = next((c for c in UNIVERSE if c["id"] == coin_id), UNIVERSE[0])

    price   = data.get("current_price", 30000) or 30000
    mcap    = data.get("market_cap", 0) or 0
    vol     = data.get("total_volume", 0) or 0
    supply  = data.get("circulating_supply", 19000000) or 19000000
    max_sup = data.get("max_supply") or supply * 1.1

    # Revenue proxy: network fees / transaction volume (estimate from vol)
    # We use annualised volume as network "revenue" proxy
    annual_vol       = vol * 365
    fee_rate         = 0.001   # ~0.1% average fee rate
    network_revenue  = annual_vol * fee_rate

    # 5-year projections (bear / base / bull)
    scenarios = {
        "bear": {"vol_cagr": 0.10, "fee_compression": 0.15, "terminal_mult": 3.0},
        "base": {"vol_cagr": 0.25, "fee_compression": 0.10, "terminal_mult": 5.0},
        "bull": {"vol_cagr": 0.50, "fee_compression": 0.05, "terminal_mult": 8.0},
    }

    # WACC equivalent for crypto
    # Risk-free rate (10Y US Treasury ~4.5%) + crypto risk premium
    risk_free   = 0.045
    crypto_prem = {"bitcoin": 0.15, "ethereum": 0.20}.get(coin_id, 0.25)
    wacc        = risk_free + crypto_prem

    projections = {}
    for scenario, s in scenarios.items():
        years = []
        rev   = network_revenue
        for yr in range(1, 6):
            rev    = rev * (1 + s["vol_cagr"]) * (1 - s["fee_compression"])
            fcf    = rev * 0.70   # 70% FCF margin assumption
            years.append({
                "year":    2025 + yr,
                "revenue": round(rev / 1e9, 3),
                "fcf":     round(fcf / 1e9, 3),
            })

        # Terminal value
        terminal_fcf   = years[-1]["fcf"] * 1e9
        terminal_exit  = terminal_fcf * s["terminal_mult"]
        terminal_perp  = terminal_fcf * (1 + 0.03) / (wacc - 0.03)   # g=3%

        # DCF sum
        dcf_sum = sum(
            yr["fcf"] * 1e9 / (1 + wacc) ** (i + 1)
            for i, yr in enumerate(years)
        )
        dcf_exit = dcf_sum + terminal_exit / (1 + wacc) ** 5
        dcf_perp = dcf_sum + terminal_perp / (1 + wacc) ** 5

        # Per-unit value
        price_exit = dcf_exit / max(supply, 1)
        price_perp = dcf_perp / max(supply, 1)
        avg_dcf    = (price_exit + price_perp) / 2

        projections[scenario] = {
            "years":        years,
            "terminal_exit":round(terminal_exit / 1e9, 2),
            "terminal_perp":round(terminal_perp / 1e9, 2),
            "dcf_exit_price":round(price_exit, 2),
            "dcf_perp_price":round(price_perp, 2),
            "avg_dcf_price": round(avg_dcf, 2),
            "vs_market_pct": round((avg_dcf / price - 1) * 100, 1),
            "verdict": ("Undervalued" if avg_dcf > price * 1.20
                        else "Overvalued" if avg_dcf < price * 0.80
                        else "Fairly Valued"),
        }

    # Sensitivity table: WACC 10% to 35% vs terminal multiple 2x to 10x
    sensitivity = []
    for w in [0.10, 0.15, 0.20, 0.25, 0.30, 0.35]:
        row = {"wacc": "{}%".format(int(w * 100))}
        for tm in [2, 4, 6, 8, 10]:
            base_fcf  = network_revenue * 0.70
            tv        = base_fcf * tm
            dcf       = sum(
                base_fcf * (1.25 ** yr) / (1 + w) ** yr
                for yr in range(1, 6)
            ) + tv / (1 + w) ** 5
            row["{}x".format(tm)] = round(dcf / max(supply, 1), 0)
        sensitivity.append(row)

    return {
        "coin":       coin["symbol"],
        "price":      price,
        "mcap_b":     round(mcap / 1e9, 2),
        "wacc":       "{}%".format(round(wacc * 100, 1)),
        "scenarios":  projections,
        "sensitivity":sensitivity,
        "base_verdict": projections["base"]["verdict"],
    }


# ============================================================
# Framework 3 -- Bridgewater Risk Analysis
# ============================================================

def run_risk_analysis(market: dict, fg: dict) -> dict:
    """Ray Dalio-style portfolio risk decomposition."""

    # Correlation groups (simplified from known crypto behaviour)
    CORR_GROUPS = {
        "BTC proxy":    ["BTC", "LTC"],
        "ETH ecosystem":["ETH", "MATIC", "ARB", "LINK"],
        "Alt L1":       ["SOL", "AVAX", "NEAR", "ADA", "DOT", "ATOM"],
        "Exchange":     ["BNB"],
        "Meme":         ["DOGE"],
        "Payments":     ["XRP"],
    }

    # Sector concentration (% of total mcap in universe)
    total_mcap = sum(
        (market.get(c["id"], {}).get("market_cap", 0) or 0)
        for c in UNIVERSE
    )
    sector_alloc = {}
    for sector, coins in CORR_GROUPS.items():
        sector_mcap = sum(
            (market.get(c["id"] if c != c else next(
                (x["id"] for x in UNIVERSE if x["symbol"] == c), ""), {})
             .get("market_cap", 0) or 0)
            for c in coins
        )
        sector_alloc[sector] = round(sector_mcap / max(total_mcap, 1) * 100, 1)

    # Liquidity ratings
    def liquidity_rating(vol_24h, mcap):
        ratio = vol_24h / mcap if mcap > 0 else 0
        if ratio > 0.10:  return "High",   "Can exit large position in hours"
        if ratio > 0.03:  return "Medium", "Can exit in 1-3 days without slippage"
        return             "Low",   "May face slippage on large exits"

    liquidity = {}
    for coin in UNIVERSE:
        d = market.get(coin["id"], {})
        vol  = d.get("total_volume", 0) or 0
        mcap = d.get("market_cap", 0) or 0
        rating, note = liquidity_rating(vol, mcap)
        liquidity[coin["symbol"]] = {"rating": rating, "note": note}

    # Recession / bear market stress test
    fg_val = fg.get("value", 50)
    if fg_val < 20:
        bear_severity = "Severe"
        est_drawdown  = -75
    elif fg_val < 35:
        bear_severity = "Moderate"
        est_drawdown  = -55
    else:
        bear_severity = "Mild"
        est_drawdown  = -35

    # Tail risk scenarios
    tail_risks = [
        {
            "scenario":    "Regulatory Crackdown (US/EU ban)",
            "probability": "8%",
            "drawdown_est":"-60% to -80%",
            "trigger":     "SEC classification as securities, exchange bans",
            "hedge":       "Reduce position size, hold 30%+ stablecoins",
        },
        {
            "scenario":    "Major Exchange Collapse (FTX-style)",
            "probability": "5%",
            "drawdown_est":"-40% to -60%",
            "trigger":     "Exchange insolvency, withdrawal freeze",
            "hedge":       "Self-custody hardware wallet, diversify exchanges",
        },
        {
            "scenario":    "Global Liquidity Crisis",
            "probability": "12%",
            "drawdown_est":"-50% to -70%",
            "trigger":     "Fed rate hike shock, USD rally, risk-off",
            "hedge":       "Short BTC futures hedge, increase stablecoin %",
        },
        {
            "scenario":    "Network / Protocol Hack",
            "probability": "3%",
            "drawdown_est":"-30% to -95% (affected coin)",
            "trigger":     "Smart contract exploit, bridge hack",
            "hedge":       "Diversify across Layer 1s, avoid concentrated DeFi",
        },
        {
            "scenario":    "Stablecoin De-peg",
            "probability": "4%",
            "drawdown_est":"-15% to -30% broad market",
            "trigger":     "USDT/USDC reserve concerns, bank failure",
            "hedge":       "Hold multiple stablecoins (USDC, USDT, DAI)",
        },
    ]

    # Hedging strategies
    hedges = [
        {
            "risk":     "Market Drawdown > 30%",
            "strategy": "BTC Futures Short (10-20% portfolio hedge)",
            "how":      "Open BTC/USDT perpetual short at 1-2x leverage when "
                        "Fear & Greed > 70 or weekly RSI > 75",
            "cost":     "Funding rate ~0.01%/8h (approx 0.45%/month)",
        },
        {
            "risk":     "Alt Season Reversal",
            "strategy": "Rotate 25-30% back to BTC/USDT when BTC.D rises",
            "how":      "Monitor BTC dominance daily. If BTC.D rises > 2% in "
                        "a week, reduce alt exposure.",
            "cost":     "Transaction fees only",
        },
        {
            "risk":     "Regulatory Shock",
            "strategy": "Hold 20-30% USDC + diversify geographically",
            "how":      "Keep stablecoin buffer ready to re-enter after "
                        "regulation clarity. Don't over-concentrate on US exchanges.",
            "cost":     "Opportunity cost of ~4.5% stablecoin yield foregone",
        },
    ]

    # Rebalancing suggestions
    rebalance = [
        {"asset": "BTC",          "target_pct": 35, "type": "Core"},
        {"asset": "ETH",          "target_pct": 25, "type": "Core"},
        {"asset": "BNB/SOL/AVAX", "target_pct": 15, "type": "Core"},
        {"asset": "LINK/ARB/NEAR","target_pct": 10, "type": "Satellite"},
        {"asset": "XRP/ADA/DOT",  "target_pct": 5,  "type": "Satellite"},
        {"asset": "USDC/USDT",    "target_pct": 10, "type": "Cash Buffer"},
    ]

    return {
        "correlation_groups": CORR_GROUPS,
        "sector_allocation":  sector_alloc,
        "liquidity":          liquidity,
        "bear_severity":      bear_severity,
        "est_max_drawdown":   est_drawdown,
        "tail_risks":         tail_risks,
        "hedging_strategies": hedges,
        "rebalance":          rebalance,
        "fg_value":           fg_val,
    }


# ============================================================
# Framework 4 -- JPMorgan Earnings Preview
# ============================================================

def run_catalyst_preview(market: dict) -> dict:
    """
    Crypto equivalent of earnings preview:
    Upcoming catalysts, historical price behaviour, implied moves.
    """
    now = datetime.now()

    # Key upcoming catalysts (research-based, updated periodically)
    catalysts = [
        {
            "event":    "Federal Reserve FOMC Meeting",
            "date":     "~May 7, 2025",
            "impact":   "High",
            "bias":     "Depends on rate decision",
            "expected": "Pause likely -- bullish for risk assets",
            "history":  "BTC +8% avg on pause, -12% avg on hike",
        },
        {
            "event":    "Bitcoin Halving Cycle Peak",
            "date":     "Oct-Dec 2025 (historically)",
            "impact":   "Very High",
            "bias":     "Bullish",
            "expected": "Post-halving bull phase historically peaks ~18 months after",
            "history":  "+400% avg gain from halving to cycle top",
        },
        {
            "event":    "Ethereum Pectra Upgrade",
            "date":     "Q2 2025",
            "impact":   "High",
            "bias":     "Bullish ETH",
            "expected": "EIP-7702 account abstraction, validator improvements",
            "history":  "ETH +30% avg pre-upgrade, sells news",
        },
        {
            "event":    "US Spot ETH ETF Flows",
            "date":     "Ongoing",
            "impact":   "Medium-High",
            "bias":     "Bullish ETH",
            "expected": "Institutional accumulation through ETF products",
            "history":  "BTC ETF brought $15B+ in first 3 months",
        },
        {
            "event":    "US Dollar Index (DXY) Monthly Close",
            "date":     "Monthly",
            "impact":   "Medium",
            "bias":     "Inverse",
            "expected": "DXY weakness = crypto strength",
            "history":  "BTC corr with DXY: -0.65 (strong inverse)",
        },
    ]

    # Key metrics Wall St watches for crypto
    metrics = []
    btc = market.get("bitcoin", {})
    eth = market.get("ethereum", {})

    if btc:
        price  = btc.get("current_price", 0)
        ath    = btc.get("ath", price)
        metrics.append({
            "metric":  "BTC ATH Distance",
            "value":   "{:.1f}%".format(btc.get("ath_change_percentage", 0)),
            "signal":  "Above -20% = late bull" if btc.get("ath_change_percentage", -50) > -20
                       else "Below -30% = accumulation zone",
            "watched": "Institutional sentiment indicator",
        })
        metrics.append({
            "metric":  "BTC 24h Volume / MCap",
            "value":   "{:.2f}%".format(
                btc.get("total_volume",0) / max(btc.get("market_cap",1), 1) * 100),
            "signal":  "High ratio = strong participation",
            "watched": "Market health indicator",
        })

    if eth:
        metrics.append({
            "metric":  "ETH / BTC Ratio",
            "value":   "{:.4f}".format(
                (eth.get("current_price",0) or 0) /
                max(btc.get("current_price",1) or 1, 1)),
            "signal":  "Rising = alt season, Falling = BTC dominance",
            "watched": "Alt season timing signal",
        })

    # Options-equivalent: implied move from 30d volatility
    implied_moves = []
    for coin in UNIVERSE[:5]:
        d       = market.get(coin["id"], {})
        chg_30d = abs(d.get("price_change_percentage_30d_in_currency", 20) or 20)
        impl    = round(chg_30d / 30 * 7, 1)   # weekly implied from 30d realised vol
        implied_moves.append({
            "coin":          coin["symbol"],
            "weekly_impl":   "±{}%".format(impl),
            "monthly_real":  "{}%".format(round(chg_30d, 1)),
            "regime":        ("High Vol" if chg_30d > 30 else
                              "Normal" if chg_30d > 15 else "Low Vol"),
        })

    # Recommended plays
    fg_val = 50  # default, overridden by caller
    plays = [
        {
            "play":      "Buy Before FOMC (if pause expected)",
            "rationale": "BTC historically rallies 3-8% in week before pause",
            "risk":      "Hike surprise causes -10% flash crash",
            "timeframe": "1-2 weeks",
        },
        {
            "play":      "Accumulate ETH ahead of Pectra",
            "rationale": "Protocol upgrades reduce sell pressure, attract devs",
            "risk":      "Delay or technical failure = sell-the-news",
            "timeframe": "3-6 months",
        },
        {
            "play":      "Wait -- Reduce risk in Extreme Greed",
            "rationale": "F&G > 75 historically precedes 20-40% corrections",
            "risk":      "Missing further upside if cycle extends",
            "timeframe": "Immediate",
        },
    ]

    return {
        "catalysts":     catalysts,
        "key_metrics":   metrics,
        "implied_moves": implied_moves,
        "plays":         plays,
    }


# ============================================================
# Framework 5 -- BlackRock Portfolio Strategy
# ============================================================

def run_portfolio_strategy(market: dict, fg: dict) -> dict:
    """Multi-asset crypto portfolio construction."""
    fg_val = fg.get("value", 50)

    # Dynamic allocation based on market regime
    if fg_val < 20:   # Extreme Fear = max allocation to crypto
        regime   = "Extreme Fear -- Max Deployment"
        alloc    = {"BTC": 35, "ETH": 25, "Large Alts": 20,
                    "Small Alts": 10, "Stablecoins": 10}
    elif fg_val < 35:
        regime   = "Fear -- Overweight Crypto"
        alloc    = {"BTC": 35, "ETH": 20, "Large Alts": 15,
                    "Small Alts": 5, "Stablecoins": 25}
    elif fg_val < 65:
        regime   = "Neutral -- Balanced"
        alloc    = {"BTC": 30, "ETH": 20, "Large Alts": 15,
                    "Small Alts": 5, "Stablecoins": 30}
    elif fg_val < 80:
        regime   = "Greed -- Underweight, Take Profits"
        alloc    = {"BTC": 25, "ETH": 15, "Large Alts": 10,
                    "Small Alts": 5, "Stablecoins": 45}
    else:
        regime   = "Extreme Greed -- Defensive"
        alloc    = {"BTC": 20, "ETH": 10, "Large Alts": 5,
                    "Small Alts": 0, "Stablecoins": 65}

    # Specific picks with tickers
    etf_picks = [
        {"category": "BTC",         "pick": "BTC/USDT",  "ticker": "BTC",
         "type": "Core", "note": "Direct spot on Binance/Bybit/Bitget"},
        {"category": "ETH",         "pick": "ETH/USDT",  "ticker": "ETH",
         "type": "Core", "note": "Direct spot or staked ETH"},
        {"category": "Large Alts",  "pick": "BNB/USDT",  "ticker": "BNB",
         "type": "Core", "note": "Exchange utility, fee discount"},
        {"category": "Large Alts",  "pick": "SOL/USDT",  "ticker": "SOL",
         "type": "Core", "note": "High throughput L1, growing ecosystem"},
        {"category": "Large Alts",  "pick": "AVAX/USDT", "ticker": "AVAX",
         "type": "Core", "note": "Subnet architecture, institutional DeFi"},
        {"category": "Small Alts",  "pick": "LINK/USDT", "ticker": "LINK",
         "type": "Satellite", "note": "Oracle leader, data infrastructure moat"},
        {"category": "Small Alts",  "pick": "ARB/USDT",  "ticker": "ARB",
         "type": "Satellite", "note": "L2 scaling leader, growing TVL"},
        {"category": "Stablecoins", "pick": "USDC",      "ticker": "USDC",
         "type": "Cash", "note": "Earn 4-5% via CeFi lending"},
    ]

    # Expected return range (historical basis, crypto-specific)
    hist_returns = {
        "bull_year":     "150-400%",
        "neutral_year":  "20-60%",
        "bear_year":     "-50% to -70%",
        "avg_cagr_5y":   "~65% (BTC-weighted)",
        "max_drawdown":  "-75% to -85% (bear market peak to trough)",
    }

    # Investment Policy Statement (one-pager)
    ips = {
        "objective":    "Capital appreciation through crypto asset exposure",
        "horizon":      "3-5 years minimum holding period",
        "risk_tolerance":"High -- can withstand 60-70% drawdowns",
        "benchmark":    "BTC total return index",
        "rebalancing":  "Quarterly or when any asset drifts > 5% from target",
        "tax_strategy": "Hold > 12 months for LTCG treatment. Use tax-loss "
                        "harvesting in down years. Consider crypto-native accounts.",
        "liquidity":    "Maintain 10-30% stablecoins for opportunistic buying",
        "exclusions":   "No leverage > 3x. No anonymous/unaudited protocols.",
    }

    return {
        "regime":       regime,
        "allocation":   alloc,
        "fg_value":     fg_val,
        "picks":        etf_picks,
        "returns":      hist_returns,
        "ips":          ips,
    }


# ============================================================
# Framework 6 -- Citadel Quant Trading
# ============================================================

def run_quant_analysis(market: dict, coin_id: str = "bitcoin") -> dict:
    """Technical + statistical trading analysis."""
    coin = next((c for c in UNIVERSE if c["id"] == coin_id), UNIVERSE[0])
    data = market.get(coin_id, {})
    price = data.get("current_price", 0) or 0

    if price <= 0:
        return {"error": "No price data"}

    chg_24h = data.get("price_change_percentage_24h_in_currency", 0) or 0
    chg_7d  = data.get("price_change_percentage_7d_in_currency",  0) or 0
    chg_30d = data.get("price_change_percentage_30d_in_currency", 0) or 0

    # Trend direction
    daily_trend  = "Bullish" if chg_24h > 1 else "Bearish" if chg_24h < -1 else "Neutral"
    weekly_trend = "Bullish" if chg_7d  > 5 else "Bearish" if chg_7d  < -5 else "Neutral"
    monthly_trend= "Bullish" if chg_30d > 10 else "Bearish" if chg_30d < -10 else "Neutral"

    # Moving averages (estimated from price + % change)
    ma50_est  = price / (1 + chg_30d / 100 * 0.60)   # rough
    ma100_est = price / (1 + chg_30d / 100 * 0.85)
    ma200_est = price / (1 + (chg_7d + chg_30d) / 100 * 1.20)

    ma50_signal  = "Above" if price > ma50_est  else "Below"
    ma100_signal = "Above" if price > ma100_est else "Below"
    ma200_signal = "Above" if price > ma200_est else "Below"

    # RSI estimate (momentum proxy)
    avg_gain = max(0, chg_7d / 7 * 1.5)
    avg_loss = max(0, -chg_7d / 7 * 1.5)
    rs  = avg_gain / max(avg_loss, 0.001)
    rsi = round(100 - 100 / (1 + rs), 1)
    rsi = max(10, min(90, rsi))   # clamp

    rsi_signal = ("Overbought -- consider reducing" if rsi > 70
                  else "Oversold -- watch for reversal" if rsi < 30
                  else "Neutral RSI")

    # MACD estimate
    macd_signal = ("Bullish cross" if chg_7d > 5 and chg_24h > 0
                   else "Bearish cross" if chg_7d < -5 and chg_24h < 0
                   else "No clear cross")

    # Bollinger Bands (price vs estimated bands)
    volatility = abs(chg_30d) / 100
    bb_upper   = round(price * (1 + volatility * 0.8), 2)
    bb_lower   = round(price * (1 - volatility * 0.8), 2)
    bb_signal  = ("Near upper band -- overbought" if price > bb_upper * 0.97
                  else "Near lower band -- oversold" if price < bb_lower * 1.03
                  else "Mid-band -- neutral")

    # Support / resistance
    ath = data.get("ath", price * 2) or price * 2
    atl = data.get("atl", price * 0.1) or price * 0.1

    # Fibonacci levels from ATH to ATL
    fib_range = ath - atl
    fib_levels = {
        "0.236": round(atl + fib_range * 0.236, 2),
        "0.382": round(atl + fib_range * 0.382, 2),
        "0.500": round(atl + fib_range * 0.500, 2),
        "0.618": round(atl + fib_range * 0.618, 2),
        "0.786": round(atl + fib_range * 0.786, 2),
    }

    # Support / resistance zones
    support1    = round(price * 0.92, 2)
    support2    = round(price * 0.85, 2)
    resistance1 = round(price * 1.08, 2)
    resistance2 = round(price * 1.15, 2)

    # Chart pattern
    if chg_30d > 20 and chg_7d > 5:
        pattern = "Potential Cup Formation -- bullish continuation"
    elif chg_30d < -20 and chg_7d > 0:
        pattern = "Potential Rounded Bottom -- reversal watch"
    elif chg_30d > 15 and chg_7d < -5:
        pattern = "Potential Head & Shoulders -- watch neckline"
    else:
        pattern = "Consolidation / Range -- wait for breakout"

    # Entry, stop-loss, target, R:R
    if monthly_trend == "Bullish" and rsi < 65:
        entry  = round(price * 0.98, 2)  # slight pullback entry
        stop   = round(price * 0.90, 2)  # 10% below entry
        target = round(price * 1.25, 2)  # 25% above entry
        rr     = round((target - entry) / max(entry - stop, 1), 2)
        setup  = "Long Setup"
    elif monthly_trend == "Bearish" and rsi > 55:
        entry  = round(price * 1.02, 2)
        stop   = round(price * 1.10, 2)
        target = round(price * 0.80, 2)
        rr     = round((entry - target) / max(stop - entry, 1), 2)
        setup  = "Short Setup (Futures Only)"
    else:
        entry  = round(price * 0.98, 2)
        stop   = round(price * 0.91, 2)
        target = round(price * 1.15, 2)
        rr     = round((target - entry) / max(entry - stop, 1), 2)
        setup  = "Cautious Long -- Wait for Confirmation"

    return {
        "coin":           coin["symbol"],
        "price":          price,
        "trends":         {"daily": daily_trend, "weekly": weekly_trend,
                           "monthly": monthly_trend},
        "moving_averages":{"MA50": {"est": round(ma50_est, 2),
                                     "signal": ma50_signal},
                           "MA100":{"est": round(ma100_est, 2),
                                     "signal": ma100_signal},
                           "MA200":{"est": round(ma200_est, 2),
                                     "signal": ma200_signal}},
        "rsi":            {"value": rsi, "signal": rsi_signal},
        "macd":           {"signal": macd_signal},
        "bollinger":      {"upper": bb_upper, "lower": bb_lower,
                           "signal": bb_signal},
        "fibonacci":      fib_levels,
        "pattern":        pattern,
        "support":        [support1, support2],
        "resistance":     [resistance1, resistance2],
        "trade_setup":    {"type": setup, "entry": entry, "stop": stop,
                           "target": target, "rr": "{}:1".format(rr)},
    }


# ============================================================
# Framework 7 -- Harvard Endowment Yield Strategy
# ============================================================

def run_yield_strategy(market: dict) -> dict:
    """Crypto yield / staking income strategy."""

    # Staking yields (research-based estimates, market rates)
    YIELDS = [
        {"symbol":"ETH",  "name":"Ethereum",       "yield":3.5,  "score":9,
         "payout_ratio":45, "mechanism":"PoS Validator Staking",
         "risk":"Low -- native protocol yield"},
        {"symbol":"SOL",  "name":"Solana",          "yield":6.5,  "score":8,
         "payout_ratio":52, "mechanism":"PoS Delegation",
         "risk":"Low -- 27-day unbonding"},
        {"symbol":"ADA",  "name":"Cardano",         "yield":4.2,  "score":8,
         "payout_ratio":61, "mechanism":"Liquid Delegation",
         "risk":"Low -- no lockup"},
        {"symbol":"ATOM", "name":"Cosmos",          "yield":14.0, "score":7,
         "payout_ratio":78, "mechanism":"PoS Staking",
         "risk":"Medium -- 21-day unbonding, high inflation"},
        {"symbol":"DOT",  "name":"Polkadot",        "yield":12.0, "score":7,
         "payout_ratio":72, "mechanism":"Nominated PoS",
         "risk":"Medium -- 28-day unbonding, inflation dilution"},
        {"symbol":"MATIC","name":"Polygon",         "yield":5.5,  "score":7,
         "payout_ratio":55, "mechanism":"PoS Delegation",
         "risk":"Low -- protocol migration risk"},
        {"symbol":"NEAR", "name":"NEAR Protocol",   "yield":8.5,  "score":7,
         "payout_ratio":66, "mechanism":"PoS Staking",
         "risk":"Medium -- smaller ecosystem"},
        {"symbol":"BNB",  "name":"BNB",             "yield":2.5,  "score":8,
         "payout_ratio":35, "mechanism":"BNB Chain Staking",
         "risk":"Low -- centralised risk"},
        {"symbol":"LINK", "name":"Chainlink",       "yield":4.5,  "score":8,
         "payout_ratio":48, "mechanism":"LINK Staking v0.2",
         "risk":"Low -- capped capacity"},
        {"symbol":"XRP",  "name":"XRP",             "yield":2.0,  "score":6,
         "payout_ratio":30, "mechanism":"Limited staking (XRPL)",
         "risk":"Medium -- regulatory uncertainty"},
        {"symbol":"AVAX", "name":"Avalanche",       "yield":7.5,  "score":7,
         "payout_ratio":58, "mechanism":"PoS Validation",
         "risk":"Medium -- 2-week lockup"},
        {"symbol":"USDC", "name":"USD Coin",        "yield":4.5,  "score":10,
         "payout_ratio":100, "mechanism":"CeFi Lending / Money Market",
         "risk":"Very Low -- stablecoin, no price risk"},
        {"symbol":"ARB",  "name":"Arbitrum",        "yield":3.0,  "score":6,
         "payout_ratio":42, "mechanism":"Liquidity Provision",
         "risk":"High -- IL risk, governance token"},
        {"symbol":"LTC",  "name":"Litecoin",        "yield":0.5,  "score":5,
         "payout_ratio":25, "mechanism":"Limited -- PoW mining",
         "risk":"High -- no native staking"},
        {"symbol":"BTC",  "name":"Bitcoin",         "yield":1.5,  "score":7,
         "payout_ratio":20, "mechanism":"Wrapped BTC lending (CeFi)",
         "risk":"Medium -- counterparty risk with custodian"},
    ]

    # Sort by safety (score desc), then yield
    YIELDS.sort(key=lambda x: (x["score"], x["yield"]), reverse=True)
    for i, y in enumerate(YIELDS, 1):
        y["rank"] = i
        y["sustainable"] = "Yes" if y["payout_ratio"] < 70 else "Watch"

    # Monthly income projection ($10,000 portfolio)
    portfolio_size = 10000
    monthly_income = sum(
        portfolio_size * 0.10 * y["yield"] / 100 / 12
        for y in YIELDS[:8]   # top 8 by score
    )

    # DRIP compounding over 10 years (top pick: ETH at 3.5%)
    drip_years = []
    balance    = portfolio_size
    for yr in range(1, 11):
        balance = balance * (1 + 0.035)   # ETH staking rate
        drip_years.append({"year": 2025 + yr, "balance": round(balance, 2)})

    # Dividend growth rate estimate (staking yield trend)
    growth_notes = {
        "ETH":  "+0.1-0.3%/year as validators increase",
        "SOL":  "Declining as network matures (-0.5%/year)",
        "ATOM": "Governance vote may change inflation",
        "DOT":  "Declining inflation schedule (-1%/year)",
        "USDC": "Tracks Federal Funds Rate",
    }

    return {
        "picks":          YIELDS,
        "monthly_income": round(monthly_income, 2),
        "drip_10y":       drip_years,
        "growth_notes":   growth_notes,
        "portfolio_size": portfolio_size,
    }


# ============================================================
# Framework 8 -- Bain Competitive Analysis
# ============================================================

def run_competitive_analysis(market: dict) -> dict:
    """Layer 1 blockchain competitive landscape."""

    # Top L1/L2 competitors
    COMPETITORS = [
        {
            "name":      "Ethereum",
            "symbol":    "ETH",
            "category":  "L1 Smart Contract",
            "moat":      "Strong",
            "moat_type": "Network / Developer Ecosystem",
            "strengths": ["Largest developer community", "DeFi/NFT standard",
                          "Institutional adoption (ETF)", "EIP roadmap"],
            "weaknesses":["Gas fees", "Slower finality", "Complex UX"],
        },
        {
            "name":      "Solana",
            "symbol":    "SOL",
            "category":  "L1 High Performance",
            "moat":      "Moderate",
            "moat_type": "Speed / Cost / DePIN ecosystem",
            "strengths": ["65k TPS, sub-$0.01 fees", "Meme coin culture",
                          "Firedancer upgrade", "Mobile crypto (Saga)"],
            "weaknesses":["Outage history", "Centralisation concerns",
                          "VC token unlock risk"],
        },
        {
            "name":      "BNB Chain",
            "symbol":    "BNB",
            "category":  "L1 Exchange-backed",
            "moat":      "Moderate",
            "moat_type": "Exchange utility / Fee discount",
            "strengths": ["Binance ecosystem", "Low fees", "High volume",
                          "BSC DeFi"],
            "weaknesses":["Centralised", "Regulatory risk", "BNB burn reliance"],
        },
        {
            "name":      "Avalanche",
            "symbol":    "AVAX",
            "category":  "L1 Subnet Architecture",
            "moat":      "Moderate",
            "moat_type": "Institutional DeFi / Subnets",
            "strengths": ["Custom subnet model", "Institutional DeFi (Ava Labs)",
                          "Fast finality"],
            "weaknesses":["Smaller ecosystem", "Competition from L2s",
                          "Subnet adoption slow"],
        },
        {
            "name":      "Arbitrum",
            "symbol":    "ARB",
            "category":  "L2 Optimistic Rollup",
            "moat":      "Moderate",
            "moat_type": "ETH security + low cost",
            "strengths": ["ETH security", "Growing TVL", "Stylus (multi-language)",
                          "Orbit chains"],
            "weaknesses":["ARB token utility limited", "Competition from Base/OP",
                          "Withdrawal delay"],
        },
        {
            "name":      "Polkadot",
            "symbol":    "DOT",
            "category":  "L0 Interoperability",
            "moat":      "Weak",
            "moat_type": "Cross-chain communication",
            "strengths": ["Parachain model", "JAM upgrade", "W3F grants"],
            "weaknesses":["Complex UX", "Slow adoption", "Ecosystem fragmentation",
                          "DOT inflation"],
        },
    ]

    # Market cap from live data
    for c in COMPETITORS:
        sym_map = {"ETH":"ethereum","SOL":"solana","BNB":"binancecoin",
                   "AVAX":"avalanche-2","ARB":"arbitrum","DOT":"polkadot"}
        cid  = sym_map.get(c["symbol"], "")
        d    = market.get(cid, {})
        c["mcap_b"]     = round((d.get("market_cap", 0) or 0) / 1e9, 1)
        c["price"]      = d.get("current_price", 0) or 0
        c["chg_30d"]    = round(d.get("price_change_percentage_30d_in_currency", 0) or 0, 1)
        c["vol_24h_b"]  = round((d.get("total_volume", 0) or 0) / 1e9, 2)

    # Revenue proxy (TVL-weighted, protocol fees)
    PROTOCOL_FEES = {
        "ETH": {"annual_fee_m": 420, "margin_pct": 85},
        "SOL": {"annual_fee_m": 85,  "margin_pct": 80},
        "BNB": {"annual_fee_m": 140, "margin_pct": 75},
        "AVAX":{"annual_fee_m": 18,  "margin_pct": 78},
        "ARB": {"annual_fee_m": 32,  "margin_pct": 70},
        "DOT": {"annual_fee_m": 8,   "margin_pct": 72},
    }
    for c in COMPETITORS:
        fees = PROTOCOL_FEES.get(c["symbol"], {})
        c["annual_fee_m"]  = fees.get("annual_fee_m", 0)
        c["margin_pct"]    = fees.get("margin_pct", 0)

    # Market share trends (3-year)
    MSHARE = {
        "ETH": {"2022": 19.0, "2023": 17.5, "2024": 15.5, "trend": "Declining (L2 cannibalisation)"},
        "SOL": {"2022": 1.2,  "2023": 1.8,  "2024": 4.2,  "trend": "Rapidly gaining"},
        "BNB": {"2022": 4.1,  "2023": 3.8,  "2024": 3.3,  "trend": "Slowly declining"},
        "AVAX":{"2022": 1.5,  "2023": 1.0,  "2024": 0.9,  "trend": "Flat to declining"},
        "ARB": {"2022": 0.1,  "2023": 0.5,  "2024": 0.7,  "trend": "Growing but from low base"},
        "DOT": {"2022": 1.8,  "2023": 1.2,  "2024": 0.8,  "trend": "Declining"},
    }
    for c in COMPETITORS:
        ms = MSHARE.get(c["symbol"], {})
        c["mshare_2022"] = ms.get("2022", 0)
        c["mshare_2024"] = ms.get("2024", 0)
        c["mshare_trend"]= ms.get("trend", "Unknown")

    # Management quality
    MGMT = {
        "ETH": ("A+", "Ethereum Foundation -- decentralised, research-driven"),
        "SOL": ("A",  "Anatoly Yakovenko -- visionary, execution-focused"),
        "BNB": ("B+", "CZ successor -- centralised risk, execution strong"),
        "AVAX":("B+", "Emin Gün Sirer -- academic + commercial balance"),
        "ARB": ("B",  "Offchain Labs -- strong tech, token design criticised"),
        "DOT": ("B-", "Gavin Wood -- visionary but slow execution"),
    }
    for c in COMPETITORS:
        grade, note = MGMT.get(c["symbol"], ("B", "Unknown"))
        c["mgmt_grade"] = grade
        c["mgmt_note"]  = note

    # SWOT for top 2
    swot = {
        "ETH": {
            "strengths":    ["Largest developer ecosystem", "ETF institutional access",
                             "Lindy effect (9 years)", "EIP upgrade pipeline"],
            "weaknesses":   ["High gas in congestion", "Slower than competitors",
                             "UX complexity"],
            "opportunities":["L2 ecosystem growth", "Real-world asset tokenisation",
                             "Enterprise adoption"],
            "threats":      ["Solana gaining market share", "Regulatory uncertainty",
                             "L2 fee cannibalisation"],
        },
        "SOL": {
            "strengths":    ["Speed + cost advantage", "Consumer crypto UX",
                             "Meme coin + DePIN narrative"],
            "weaknesses":   ["Past outages damage trust", "Centralisation concerns",
                             "VC unlock overhang"],
            "opportunities":["Mobile crypto (Saga 2)", "DePIN growth",
                             "Institutional adoption lag"],
            "threats":      ["ETH L2s improving speed", "Regulatory action on FTX ties",
                             "Network reliability questions"],
        },
    }

    # Best pick
    best_pick = {
        "coin":      "ETH",
        "rationale": ("ETH remains the strongest risk-adjusted opportunity: "
                      "institutional access via spot ETF, dominant developer ecosystem, "
                      "Pectra upgrade catalyst in Q2 2025, and deflationary tokenomics. "
                      "Trading at a discount to BTC on ETH/BTC ratio. "
                      "Target: $4,500-$6,000 by end of 2025 bull cycle."),
        "entry":     "Accumulate in $2,200-$2,800 range",
        "target":    "$4,500 (12-month base case)",
        "risk":      "Stop below $1,800 (key support)",
    }

    return {
        "competitors":     COMPETITORS,
        "swot":            swot,
        "best_pick":       best_pick,
    }


# ============================================================
# Report generator
# ============================================================

class ResearchEngine:

    def __init__(self):
        RESEARCH_DIR.mkdir(parents=True, exist_ok=True)

    def run(self, coin_id: str = "bitcoin") -> dict:
        """Run all 8 frameworks and generate full report."""
        logger.info("[Research] Starting institutional analysis -- 8 frameworks...")

        logger.info("[Research] 1/8 Fetching market data...")
        market = fetch_market_data()
        if not market:
            logger.error("[Research] Failed to fetch market data")
            return {}

        logger.info("[Research] 2/8 Fetching Fear & Greed + Global...")
        fg     = fetch_fear_greed()
        glb    = fetch_global()

        logger.info("[Research] 3/8 Running Goldman Screener...")
        screener = run_screener(market)
        time.sleep(1)   # rate limit

        logger.info("[Research] 4/8 Running Morgan Stanley Valuation...")
        valuation = run_valuation(market, coin_id)
        time.sleep(1)

        logger.info("[Research] 5/8 Running Bridgewater Risk Analysis...")
        risk = run_risk_analysis(market, fg)
        time.sleep(1)

        logger.info("[Research] 6/8 Running JPMorgan Catalyst Preview...")
        catalyst = run_catalyst_preview(market)
        time.sleep(1)

        logger.info("[Research] 7/8 Running BlackRock Portfolio Strategy...")
        portfolio = run_portfolio_strategy(market, fg)
        time.sleep(1)

        logger.info("[Research] 8a/8 Running Citadel Quant Analysis...")
        quant = run_quant_analysis(market, coin_id)
        time.sleep(1)

        logger.info("[Research] 8b/8 Running Harvard Yield Strategy...")
        yield_s = run_yield_strategy(market)
        time.sleep(1)

        logger.info("[Research] 8c/8 Running Bain Competitive Analysis...")
        competitive = run_competitive_analysis(market)

        report = {
            "generated_at": datetime.now().isoformat(),
            "coin_focus":   coin_id,
            "market_data":  glb,
            "fear_greed":   fg,
            "screener":     screener,
            "valuation":    valuation,
            "risk":         risk,
            "catalyst":     catalyst,
            "portfolio":    portfolio,
            "quant":        quant,
            "yield":        yield_s,
            "competitive":  competitive,
        }

        self._save_json(report)
        self._generate_html(report)
        logger.info("[Research] Complete. Report: {}".format(REPORT_HTML.resolve()))
        return report

    def _save_json(self, report: dict):
        try:
            REPORT_JSON.write_text(
                json.dumps(report, indent=2, default=str), encoding="utf-8")
        except Exception as e:
            logger.debug("[Research] JSON save: {}".format(e))

    def _generate_html(self, report: dict):
        """Generate the full institutional research HTML report."""
        ts   = report["generated_at"][:16]
        fg   = report["fear_greed"]
        glb  = report["market_data"]
        sc   = report["screener"]
        val  = report["valuation"]
        risk = report["risk"]
        cat  = report["catalyst"]
        port = report["portfolio"]
        quant= report["quant"]
        yld  = report["yield"]
        comp = report["competitive"]

        fg_val   = fg.get("value", 50)
        fg_label = fg.get("label", "Neutral")
        fg_color = ("#4ade80" if fg_val > 60 else
                    "#f87171" if fg_val < 30 else "#fbbf24")

        mkt_chg = glb.get("mkt_chg_24h", 0)
        mkt_c   = "#4ade80" if mkt_chg >= 0 else "#f87171"

        # ── SCREENER TABLE ──────────────────────────────────────────
        moat_color = {"Strong": "#4ade80", "Moderate": "#fbbf24", "Weak": "#f87171"}
        screener_rows = ""
        for r in sc.get("top15", []):
            mc  = moat_color.get(r["moat"], "#94a3b8")
            upc = "#4ade80" if r["upside_pct"] > 0 else "#f87171"
            d1c = "#4ade80" if r["chg_24h"] >= 0 else "#f87171"
            d7c = "#4ade80" if r["chg_7d"]  >= 0 else "#f87171"
            vc  = "#4ade80" if r["nvt_verdict"] == "Undervalued" else (
                  "#f87171" if r["nvt_verdict"] == "Overvalued" else "#fbbf24")
            screener_rows += """
            <tr>
              <td style="font-weight:bold;color:#38bdf8">{rank}</td>
              <td style="font-weight:bold">{symbol}</td>
              <td>${price:,.4f}</td>
              <td>${mcap_b:.1f}B</td>
              <td style="color:{d1c}">{chg_24h:+.1f}%</td>
              <td style="color:{d7c}">{chg_7d:+.1f}%</td>
              <td>{nvt_ratio:.0f}x</td>
              <td style="color:{vc}">{nvt_verdict}</td>
              <td style="color:{mc}">{moat}</td>
              <td style="color:{upc}">{upside_pct:+.0f}%</td>
              <td>${bull_12m:,.2f}</td>
              <td style="color:#f87171">${bear_12m:,.2f}</td>
              <td style="color:#94a3b8">${entry_low:,.4f}-${entry_high:,.4f}</td>
              <td style="color:#f87171">${stop_loss:,.4f}</td>
            </tr>""".format(**r, d1c=d1c, d7c=d7c, mc=mc, upc=upc, vc=vc)

        # ── VALUATION TABLE ─────────────────────────────────────────
        val_rows = ""
        for s_name, s_data in val.get("scenarios", {}).items():
            vc = ("#4ade80" if s_data["verdict"] == "Undervalued" else
                  "#f87171" if s_data["verdict"] == "Overvalued" else "#fbbf24")
            val_rows += """
            <tr>
              <td style="text-transform:capitalize;font-weight:bold">{s}</td>
              <td>${avg:.2f}</td>
              <td style="color:{vc}">{vs:+.1f}%</td>
              <td style="color:{vc}">{verdict}</td>
              <td>${tv_exit:.1f}B</td>
              <td>${tv_perp:.1f}B</td>
            </tr>""".format(
                s=s_name, avg=s_data["avg_dcf_price"],
                vs=s_data["vs_market_pct"], verdict=s_data["verdict"],
                tv_exit=s_data["terminal_exit"], tv_perp=s_data["terminal_perp"],
                vc=vc)

        # Sensitivity table
        sens_data = val.get("sensitivity", [])
        sens_header = ""
        if sens_data:
            cols = [k for k in sens_data[0].keys() if k != "wacc"]
            sens_header = "<tr><th>WACC \\ Exit</th>" + "".join(
                "<th>{}x</th>".format(c) for c in cols) + "</tr>"
        sens_rows = ""
        for row in sens_data:
            cols = [k for k in row.keys() if k != "wacc"]
            price_now = val.get("price", 1)
            cells = ""
            for c in cols:
                v = row[c]
                color = "#4ade80" if v > price_now * 1.2 else (
                        "#f87171" if v < price_now * 0.8 else "#fbbf24")
                cells += "<td style='color:{}'>${:,.0f}</td>".format(color, v)
            sens_rows += "<tr><td style='font-weight:bold'>{}</td>{}</tr>".format(
                row["wacc"], cells)

        # ── RISK TABLE ──────────────────────────────────────────────
        tail_rows = ""
        for tr in risk.get("tail_risks", []):
            tail_rows += """
            <tr>
              <td style="font-weight:bold">{scenario}</td>
              <td style="color:#fbbf24">{probability}</td>
              <td style="color:#f87171">{drawdown_est}</td>
              <td style="color:#94a3b8">{trigger}</td>
              <td style="color:#4ade80">{hedge}</td>
            </tr>""".format(**tr)

        hedge_rows = ""
        for h in risk.get("hedging_strategies", []):
            hedge_rows += """
            <tr>
              <td style="font-weight:bold;color:#f87171">{risk}</td>
              <td style="color:#4ade80">{strategy}</td>
              <td style="color:#94a3b8">{how}</td>
              <td style="color:#fbbf24">{cost}</td>
            </tr>""".format(**h)

        rebal_rows = ""
        for rb in risk.get("rebalance", []):
            tc = "#4ade80" if rb["type"] == "Core" else (
                 "#94a3b8" if rb["type"] == "Cash Buffer" else "#fbbf24")
            rebal_rows += """
            <tr>
              <td style="font-weight:bold">{asset}</td>
              <td style="color:{tc};font-weight:bold">{target_pct}%</td>
              <td style="color:{tc}">{type}</td>
            </tr>""".format(**rb, tc=tc)

        # ── CATALYST TABLE ──────────────────────────────────────────
        cat_rows = ""
        for c in cat.get("catalysts", []):
            ic = "#4ade80" if c["impact"] in ("Very High", "High") else "#fbbf24"
            bc = "#4ade80" if "bullish" in c["bias"].lower() else (
                 "#f87171" if "bearish" in c["bias"].lower() else "#fbbf24")
            cat_rows += """
            <tr>
              <td style="font-weight:bold">{event}</td>
              <td style="color:#94a3b8">{date}</td>
              <td style="color:{ic}">{impact}</td>
              <td style="color:{bc}">{bias}</td>
              <td style="color:#94a3b8">{history}</td>
            </tr>""".format(**c, ic=ic, bc=bc)

        implied_rows = ""
        for im in cat.get("implied_moves", []):
            rc = "#f87171" if im["regime"] == "High Vol" else (
                 "#fbbf24" if im["regime"] == "Normal" else "#4ade80")
            implied_rows += """
            <tr>
              <td style="font-weight:bold">{coin}</td>
              <td style="color:#fbbf24;font-weight:bold">{weekly_impl}</td>
              <td>{monthly_real}</td>
              <td style="color:{rc}">{regime}</td>
            </tr>""".format(**im, rc=rc)

        # ── PORTFOLIO ───────────────────────────────────────────────
        alloc_rows = ""
        for asset, pct in port.get("allocation", {}).items():
            bar_w = int(pct * 2)
            alloc_rows += """
            <tr>
              <td style="font-weight:bold">{asset}</td>
              <td style="font-weight:bold;color:#38bdf8">{pct}%</td>
              <td>
                <div style="background:#38bdf8;height:14px;width:{bar_w}px;
                     border-radius:2px;display:inline-block"></div>
              </td>
            </tr>""".format(asset=asset, pct=pct, bar_w=bar_w)

        pick_rows = ""
        for p in port.get("picks", []):
            tc = "#4ade80" if p["type"] == "Core" else (
                 "#94a3b8" if p["type"] == "Cash" else "#fbbf24")
            pick_rows += """
            <tr>
              <td style="font-weight:bold;color:#38bdf8">{ticker}</td>
              <td>{pick}</td>
              <td style="color:{tc}">{type}</td>
              <td style="color:#94a3b8">{note}</td>
            </tr>""".format(**p, tc=tc)

        # ── QUANT ────────────────────────────────────────────────────
        trends   = quant.get("trends", {})
        mas      = quant.get("moving_averages", {})
        rsi_data = quant.get("rsi", {})
        trade    = quant.get("trade_setup", {})
        fibs     = quant.get("fibonacci", {})

        def trend_color(t):
            return "#4ade80" if t=="Bullish" else "#f87171" if t=="Bearish" else "#fbbf24"

        ma_rows = ""
        for ma_name, ma_data in mas.items():
            sc_ = "#4ade80" if ma_data["signal"] == "Above" else "#f87171"
            ma_rows += """
            <tr>
              <td style="font-weight:bold">{}</td>
              <td>${:.2f}</td>
              <td style="color:{}">{}</td>
            </tr>""".format(ma_name, ma_data["est"], sc_, ma_data["signal"])

        fib_rows = ""
        for level, price_fib in fibs.items():
            fib_rows += """
            <tr>
              <td style="color:#fbbf24">Fib {}</td>
              <td>${:,.2f}</td>
            </tr>""".format(level, price_fib)

        # ── YIELD ────────────────────────────────────────────────────
        yield_rows = ""
        for y in yld.get("picks", [])[:15]:
            sc_ = "#4ade80" if y["score"] >= 8 else (
                  "#f87171" if y["score"] < 6 else "#fbbf24")
            sus = "#4ade80" if y["sustainable"] == "Yes" else "#f87171"
            yield_rows += """
            <tr>
              <td style="font-weight:bold">{rank}</td>
              <td style="font-weight:bold;color:#38bdf8">{symbol}</td>
              <td style="color:#4ade80;font-weight:bold">{yield_:.1f}%</td>
              <td style="color:{sc}">{score}/10</td>
              <td>{payout_ratio}%</td>
              <td style="color:{sus}">{sustainable}</td>
              <td style="color:#94a3b8">{mechanism}</td>
              <td style="color:{rc}">{risk}</td>
            </tr>""".format(**y, yield_=y["yield"], sc=sc_, sus=sus,
                            rc="#4ade80" if "Low" in y["risk"] else (
                               "#f87171" if "High" in y["risk"] else "#fbbf24"))

        drip_rows = ""
        for d in yld.get("drip_10y", []):
            drip_rows += """
            <tr>
              <td>{year}</td>
              <td style="color:#4ade80">${balance:,.2f}</td>
              <td style="color:#94a3b8">{growth:.1f}%</td>
            </tr>""".format(
                year=d["year"], balance=d["balance"],
                growth=(d["balance"] / 10000 - 1) * 100)

        # ── COMPETITIVE ──────────────────────────────────────────────
        comp_rows = ""
        for c in comp.get("competitors", []):
            mc  = moat_color.get(c["moat"], "#94a3b8")
            pcc = "#4ade80" if c["chg_30d"] >= 0 else "#f87171"
            ms_trend_c = ("#4ade80" if "gaining" in c.get("mshare_trend","") else
                          "#f87171" if "declining" in c.get("mshare_trend","").lower()
                          else "#fbbf24")
            comp_rows += """
            <tr>
              <td style="font-weight:bold;color:#38bdf8">{symbol}</td>
              <td>{name}</td>
              <td>${mcap_b:.1f}B</td>
              <td>${annual_fee_m}M</td>
              <td>{margin_pct}%</td>
              <td style="color:{mc}">{moat}</td>
              <td>{mshare_2022}%</td>
              <td>{mshare_2024}%</td>
              <td style="color:{ms_trend_c}">{mshare_trend}</td>
              <td style="color:#fbbf24">{mgmt_grade}</td>
            </tr>""".format(**c, mc=mc, ms_trend_c=ms_trend_c, pcc=pcc)

        bp = comp.get("best_pick", {})

        # ETH SWOT
        eth_swot = comp.get("swot", {}).get("ETH", {})
        sol_swot = comp.get("swot", {}).get("SOL", {})

        def swot_list(items):
            return "".join("<li>{}</li>".format(i) for i in items)

        html = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta http-equiv="refresh" content="3600">
  <title>Crypto Institutional Research Report</title>
  <style>
    * {{ box-sizing: border-box; margin: 0; padding: 0; }}
    body {{ background: #0a0e1a; color: #e2e8f0; font-family: 'Segoe UI', monospace; }}
    .container {{ max-width: 1400px; margin: 0 auto; padding: 20px; }}

    /* Header */
    .report-header {{ background: linear-gradient(135deg, #0f172a, #1e3a5f);
      border: 1px solid #1e40af; border-radius: 12px; padding: 28px; margin-bottom: 24px; }}
    .report-header h1 {{ color: #38bdf8; font-size: 1.8em; margin-bottom: 8px; }}
    .report-header .sub {{ color: #64748b; font-size: 0.9em; }}
    .badges {{ display: flex; gap: 12px; margin-top: 16px; flex-wrap: wrap; }}
    .badge {{ padding: 6px 14px; border-radius: 20px; font-size: 0.8em;
              font-weight: bold; border: 1px solid; }}

    /* Sections */
    .section {{ background: #0f172a; border: 1px solid #1e293b;
                border-radius: 12px; padding: 24px; margin-bottom: 20px; }}
    .section-header {{ display: flex; align-items: center; gap: 12px;
                       margin-bottom: 20px; }}
    .section-title {{ color: #38bdf8; font-size: 1.2em; font-weight: bold; }}
    .firm-badge {{ background: #1e293b; color: #94a3b8; padding: 4px 12px;
                   border-radius: 12px; font-size: 0.75em; }}

    /* Tables */
    table {{ width: 100%; border-collapse: collapse; font-size: 0.85em; }}
    th {{ background: #0a0e1a; color: #64748b; padding: 10px 12px;
           text-align: left; font-size: 0.8em; text-transform: uppercase;
           border-bottom: 2px solid #1e293b; white-space: nowrap; }}
    td {{ padding: 9px 12px; border-bottom: 1px solid #1e293b;
           vertical-align: middle; }}
    tr:hover {{ background: #1e293b40; }}

    /* Cards */
    .card-grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                  gap: 12px; margin-bottom: 20px; }}
    .card {{ background: #1e293b; border-radius: 8px; padding: 16px; }}
    .card .label {{ color: #64748b; font-size: 0.75em; text-transform: uppercase;
                    margin-bottom: 4px; }}
    .card .value {{ font-size: 1.4em; font-weight: bold; }}
    .card .sub-val {{ color: #94a3b8; font-size: 0.8em; margin-top: 4px; }}

    /* SWOT */
    .swot-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .swot-box {{ background: #1e293b; border-radius: 8px; padding: 14px; }}
    .swot-box h4 {{ font-size: 0.85em; text-transform: uppercase;
                    margin-bottom: 10px; }}
    .swot-box ul {{ padding-left: 16px; }}
    .swot-box li {{ font-size: 0.82em; color: #94a3b8; margin-bottom: 4px; }}

    /* Best pick */
    .best-pick {{ background: linear-gradient(135deg, #0d2137, #0f2a1e);
                  border: 1px solid #22c55e; border-radius: 12px; padding: 20px; }}
    .best-pick h3 {{ color: #22c55e; margin-bottom: 12px; }}

    /* IPS */
    .ips-grid {{ display: grid; grid-template-columns: 1fr 1fr; gap: 12px; }}
    .ips-item {{ background: #1e293b; border-radius: 6px; padding: 12px; }}
    .ips-item .key {{ color: #64748b; font-size: 0.75em; text-transform: uppercase; }}
    .ips-item .val {{ color: #e2e8f0; font-size: 0.85em; margin-top: 4px; }}

    /* Scroll */
    .table-scroll {{ overflow-x: auto; }}
    .mt {{ margin-top: 16px; }}
    .mb {{ margin-bottom: 16px; }}
    h3 {{ color: #7dd3fc; margin: 16px 0 10px; font-size: 1em; }}
    h4 {{ color: #94a3b8; margin: 12px 0 8px; font-size: 0.9em; }}
  </style>
</head>
<body>
<div class="container">

  <!-- HEADER -->
  <div class="report-header">
    <h1>&#127760; Institutional Crypto Research Report</h1>
    <div class="sub">8 Frameworks | Goldman Sachs | Morgan Stanley | Bridgewater |
      JPMorgan | BlackRock | Citadel | Harvard Endowment | Bain & Company</div>
    <div class="badges">
      <span class="badge" style="color:{fg_color};border-color:{fg_color}">
        F&G: {fg_val} -- {fg_label}</span>
      <span class="badge" style="color:{mkt_c};border-color:{mkt_c}">
        Market Cap 24h: {mkt_chg:+.2f}%</span>
      <span class="badge" style="color:#38bdf8;border-color:#38bdf8">
        BTC Dom: {btc_dom:.1f}%</span>
      <span class="badge" style="color:#94a3b8;border-color:#334155">
        Generated: {ts} UTC</span>
    </div>
  </div>

  <!-- 1. SCREENER (Goldman Sachs) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">1. Crypto Equity Screener -- Top 15 Picks</div>
      <span class="firm-badge">Goldman Sachs Framework</span>
    </div>
    <p style="color:#64748b;font-size:0.85em;margin-bottom:16px">
      Ranked by composite momentum + liquidity + ATH proximity score.
      NVT Ratio = Market Cap / 24h Volume (crypto P/E equivalent).
      Sector average NVT: <strong style="color:#38bdf8">{avg_nvt:.1f}x</strong>
    </p>
    <div class="table-scroll">
      <table>
        <thead>
          <tr>
            <th>#</th><th>Coin</th><th>Price</th><th>MCap</th>
            <th>24h %</th><th>7d %</th><th>NVT</th><th>NVT Verdict</th>
            <th>Moat</th><th>Upside</th><th>Bull 12M</th><th>Bear 12M</th>
            <th>Entry Zone</th><th>Stop-Loss</th>
          </tr>
        </thead>
        <tbody>{screener_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- 2. VALUATION (Morgan Stanley) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">2. Valuation Model -- {coin_sym}</div>
      <span class="firm-badge">Morgan Stanley DCF Framework</span>
    </div>
    <div class="card-grid">
      <div class="card">
        <div class="label">Current Price</div>
        <div class="value">${val_price:,.2f}</div>
        <div class="sub-val">Market Cap: ${val_mcap}B</div>
      </div>
      <div class="card">
        <div class="label">WACC Equivalent</div>
        <div class="value">{wacc}</div>
        <div class="sub-val">Risk-free 4.5% + crypto premium</div>
      </div>
      <div class="card">
        <div class="label">Base Case Verdict</div>
        <div class="value" style="color:{verdict_color}">{base_verdict}</div>
        <div class="sub-val">DCF vs market price</div>
      </div>
    </div>
    <h3>DCF Scenarios (5-year projection)</h3>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Scenario</th><th>Avg DCF Price</th><th>vs Market</th>
          <th>Verdict</th><th>Terminal (Exit)</th><th>Terminal (Perp)</th></tr>
        </thead>
        <tbody>{val_rows}</tbody>
      </table>
    </div>
    <h3>Sensitivity Table (WACC vs Terminal Multiple)</h3>
    <div class="table-scroll">
      <table>
        <thead>{sens_header}</thead>
        <tbody>{sens_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- 3. RISK (Bridgewater) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">3. Risk Analysis</div>
      <span class="firm-badge">Bridgewater / Ray Dalio Framework</span>
    </div>
    <div class="card-grid">
      <div class="card">
        <div class="label">Bear Market Severity</div>
        <div class="value" style="color:#f87171">{bear_sev}</div>
        <div class="sub-val">F&G={fg_val2}</div>
      </div>
      <div class="card">
        <div class="label">Est. Max Drawdown</div>
        <div class="value" style="color:#f87171">{est_dd}%</div>
        <div class="sub-val">Stress test estimate</div>
      </div>
    </div>
    <h3>Tail Risk Scenarios</h3>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Scenario</th><th>Probability</th><th>Est. Drawdown</th>
          <th>Trigger</th><th>Hedge</th></tr>
        </thead>
        <tbody>{tail_rows}</tbody>
      </table>
    </div>
    <h3>Hedging Strategies (Top 3 Risks)</h3>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Risk</th><th>Strategy</th><th>How to Implement</th><th>Cost</th></tr>
        </thead>
        <tbody>{hedge_rows}</tbody>
      </table>
    </div>
    <h3>Rebalancing Suggestions</h3>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Asset</th><th>Target %</th><th>Type</th></tr></thead>
        <tbody>{rebal_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- 4. CATALYST (JPMorgan) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">4. Catalyst Preview</div>
      <span class="firm-badge">JPMorgan Earnings Preview Framework</span>
    </div>
    <h3>Upcoming Catalysts</h3>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Event</th><th>Date</th><th>Impact</th><th>Bias</th>
          <th>Historical Pattern</th></tr>
        </thead>
        <tbody>{cat_rows}</tbody>
      </table>
    </div>
    <h3>Implied Move (Weekly Volatility by Coin)</h3>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Coin</th><th>Weekly Implied Move</th>
        <th>30d Realised Vol</th><th>Regime</th></tr></thead>
        <tbody>{implied_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- 5. PORTFOLIO (BlackRock) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">5. Portfolio Strategy</div>
      <span class="firm-badge">BlackRock Multi-Asset Framework</span>
    </div>
    <div class="card-grid">
      <div class="card">
        <div class="label">Current Regime</div>
        <div class="value" style="color:{fg_color};font-size:1em">{regime}</div>
      </div>
      <div class="card">
        <div class="label">Expected Annual Return</div>
        <div class="value">{neutral_ret}</div>
        <div class="sub-val">Neutral year historical</div>
      </div>
      <div class="card">
        <div class="label">Max Drawdown (Bad Year)</div>
        <div class="value" style="color:#f87171">{max_dd}</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
      <div>
        <h3>Allocation %</h3>
        <table>
          <thead><tr><th>Asset Class</th><th>Allocation</th><th>Bar</th></tr></thead>
          <tbody>{alloc_rows}</tbody>
        </table>
      </div>
      <div>
        <h3>Specific Picks</h3>
        <table>
          <thead><tr><th>Ticker</th><th>Pick</th><th>Type</th><th>Note</th></tr></thead>
          <tbody>{pick_rows}</tbody>
        </table>
      </div>
    </div>
    <h3 class="mt">Investment Policy Statement</h3>
    <div class="ips-grid">
      {ips_items}
    </div>
  </div>

  <!-- 6. QUANT (Citadel) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">6. Quant Trading Analysis -- {q_coin}</div>
      <span class="firm-badge">Citadel Technical Framework</span>
    </div>
    <div class="card-grid">
      <div class="card">
        <div class="label">Daily Trend</div>
        <div class="value" style="color:{d_color}">{d_trend}</div>
      </div>
      <div class="card">
        <div class="label">Weekly Trend</div>
        <div class="value" style="color:{w_color}">{w_trend}</div>
      </div>
      <div class="card">
        <div class="label">Monthly Trend</div>
        <div class="value" style="color:{m_color}">{m_trend}</div>
      </div>
      <div class="card">
        <div class="label">RSI</div>
        <div class="value">{rsi_val}</div>
        <div class="sub-val">{rsi_sig}</div>
      </div>
      <div class="card">
        <div class="label">Bollinger Signal</div>
        <div class="value" style="font-size:0.85em">{bb_sig}</div>
      </div>
      <div class="card">
        <div class="label">MACD</div>
        <div class="value" style="font-size:0.85em">{macd_sig}</div>
      </div>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px">
      <div>
        <h3>Moving Averages</h3>
        <table>
          <thead><tr><th>MA</th><th>Est. Level</th><th>Signal</th></tr></thead>
          <tbody>{ma_rows}</tbody>
        </table>
        <h3 class="mt">Fibonacci Levels</h3>
        <table>
          <thead><tr><th>Level</th><th>Price</th></tr></thead>
          <tbody>{fib_rows}</tbody>
        </table>
      </div>
      <div>
        <h3>Chart Pattern</h3>
        <div class="card" style="margin-bottom:12px">
          <div class="value" style="font-size:0.9em;color:#fbbf24">{pattern}</div>
        </div>
        <h3>Trade Setup</h3>
        <div class="card">
          <div class="label">Setup Type</div>
          <div class="value" style="font-size:0.9em;color:#38bdf8">{setup_type}</div>
          <div style="margin-top:12px;display:grid;grid-template-columns:1fr 1fr;gap:8px">
            <div><div class="label">Entry</div>
              <div style="color:#fbbf24">${entry}</div></div>
            <div><div class="label">Stop-Loss</div>
              <div style="color:#f87171">${stop}</div></div>
            <div><div class="label">Target</div>
              <div style="color:#4ade80">${target}</div></div>
            <div><div class="label">R:R Ratio</div>
              <div style="color:#38bdf8">{rr}</div></div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- 7. YIELD (Harvard) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">7. Yield Strategy -- Staking &amp; Income</div>
      <span class="firm-badge">Harvard Endowment Framework</span>
    </div>
    <div class="card-grid">
      <div class="card">
        <div class="label">Monthly Income ($10K portfolio)</div>
        <div class="value" style="color:#4ade80">${monthly_inc:.2f}</div>
        <div class="sub-val">Top 8 positions</div>
      </div>
      <div class="card">
        <div class="label">ETH DRIP (10yr, 3.5%)</div>
        <div class="value" style="color:#4ade80">${drip_10:.2f}</div>
        <div class="sub-val">From $10,000 initial</div>
      </div>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>#</th><th>Coin</th><th>Yield</th><th>Safety</th>
          <th>Payout Ratio</th><th>Sustainable</th><th>Mechanism</th><th>Risk</th></tr>
        </thead>
        <tbody>{yield_rows}</tbody>
      </table>
    </div>
    <h3>DRIP Compounding Projection (ETH Staking, 10 years)</h3>
    <div class="table-scroll">
      <table>
        <thead><tr><th>Year</th><th>Portfolio Value</th><th>Total Return</th></tr></thead>
        <tbody>{drip_rows}</tbody>
      </table>
    </div>
  </div>

  <!-- 8. COMPETITIVE (Bain) -->
  <div class="section">
    <div class="section-header">
      <div class="section-title">8. Competitive Analysis -- L1/L2 Landscape</div>
      <span class="firm-badge">Bain &amp; Company Framework</span>
    </div>
    <div class="table-scroll">
      <table>
        <thead>
          <tr><th>Coin</th><th>Name</th><th>MCap</th><th>Annual Fees</th>
          <th>Margin</th><th>Moat</th><th>Share '22</th><th>Share '24</th>
          <th>Trend</th><th>Mgmt</th></tr>
        </thead>
        <tbody>{comp_rows}</tbody>
      </table>
    </div>
    <div style="display:grid;grid-template-columns:1fr 1fr;gap:20px;margin-top:20px">
      <div>
        <h3>ETH SWOT Analysis</h3>
        <div class="swot-grid">
          <div class="swot-box">
            <h4 style="color:#4ade80">Strengths</h4>
            <ul>{eth_s}</ul>
          </div>
          <div class="swot-box">
            <h4 style="color:#f87171">Weaknesses</h4>
            <ul>{eth_w}</ul>
          </div>
          <div class="swot-box">
            <h4 style="color:#38bdf8">Opportunities</h4>
            <ul>{eth_o}</ul>
          </div>
          <div class="swot-box">
            <h4 style="color:#fbbf24">Threats</h4>
            <ul>{eth_t}</ul>
          </div>
        </div>
      </div>
      <div>
        <h3>SOL SWOT Analysis</h3>
        <div class="swot-grid">
          <div class="swot-box">
            <h4 style="color:#4ade80">Strengths</h4>
            <ul>{sol_s}</ul>
          </div>
          <div class="swot-box">
            <h4 style="color:#f87171">Weaknesses</h4>
            <ul>{sol_w}</ul>
          </div>
          <div class="swot-box">
            <h4 style="color:#38bdf8">Opportunities</h4>
            <ul>{sol_o}</ul>
          </div>
          <div class="swot-box">
            <h4 style="color:#fbbf24">Threats</h4>
            <ul>{sol_t}</ul>
          </div>
        </div>
      </div>
    </div>
    <div class="best-pick mt">
      <h3>&#11088; Best Pick: {bp_coin}</h3>
      <p style="color:#94a3b8;margin-bottom:12px">{bp_rationale}</p>
      <div class="card-grid">
        <div class="card"><div class="label">Entry</div>
          <div class="value" style="color:#fbbf24;font-size:0.95em">{bp_entry}</div></div>
        <div class="card"><div class="label">12M Target</div>
          <div class="value" style="color:#4ade80">{bp_target}</div></div>
        <div class="card"><div class="label">Stop-Loss</div>
          <div class="value" style="color:#f87171">{bp_risk}</div></div>
      </div>
    </div>
  </div>

  <p style="color:#1e293b;text-align:center;padding:20px;font-size:0.75em">
    For educational purposes only. Not financial advice.
    Data from CoinGecko, Alternative.me. Generated {ts}.
  </p>

</div>
</body>
</html>""".format(
            ts=ts, fg_val=fg_val, fg_label=fg_label, fg_color=fg_color,
            mkt_chg=mkt_chg, mkt_c=mkt_c,
            btc_dom=glb.get("btc_dominance", 0),
            avg_nvt=sc.get("avg_nvt", 0),
            screener_rows=screener_rows,
            coin_sym=val.get("coin","BTC"),
            val_price=val.get("price",0),
            val_mcap=val.get("mcap_b",0),
            wacc=val.get("wacc","?"),
            base_verdict=val.get("base_verdict","?"),
            verdict_color=("#4ade80" if val.get("base_verdict","")=="Undervalued"
                           else "#f87171" if val.get("base_verdict","")=="Overvalued"
                           else "#fbbf24"),
            val_rows=val_rows,
            sens_header=sens_header, sens_rows=sens_rows,
            bear_sev=risk.get("bear_severity","?"),
            fg_val2=fg_val,
            est_dd=risk.get("est_max_drawdown",0),
            tail_rows=tail_rows, hedge_rows=hedge_rows, rebal_rows=rebal_rows,
            cat_rows=cat_rows, implied_rows=implied_rows,
            regime=port.get("regime","?"),
            neutral_ret=port.get("returns",{}).get("neutral_year","?"),
            max_dd=port.get("returns",{}).get("max_drawdown","?"),
            alloc_rows=alloc_rows, pick_rows=pick_rows,
            ips_items="".join(
                "<div class='ips-item'><div class='key'>{}</div>"
                "<div class='val'>{}</div></div>".format(k.replace("_"," ").title(), v)
                for k, v in port.get("ips",{}).items()),
            q_coin=quant.get("coin","BTC"),
            d_trend=trends.get("daily","?"), d_color=trend_color(trends.get("daily","")),
            w_trend=trends.get("weekly","?"), w_color=trend_color(trends.get("weekly","")),
            m_trend=trends.get("monthly","?"), m_color=trend_color(trends.get("monthly","")),
            rsi_val=rsi_data.get("value","?"), rsi_sig=rsi_data.get("signal","?"),
            bb_sig=quant.get("bollinger",{}).get("signal","?"),
            macd_sig=quant.get("macd",{}).get("signal","?"),
            ma_rows=ma_rows, fib_rows=fib_rows,
            pattern=quant.get("pattern","?"),
            setup_type=trade.get("type","?"),
            entry=trade.get("entry",0), stop=trade.get("stop",0),
            target=trade.get("target",0), rr=trade.get("rr","?"),
            monthly_inc=yld.get("monthly_income",0),
            drip_10=yld.get("drip_10y",[-1])[-1].get("balance",0) if yld.get("drip_10y") else 0,
            yield_rows=yield_rows, drip_rows=drip_rows,
            comp_rows=comp_rows,
            eth_s=swot_list(eth_swot.get("strengths",[])),
            eth_w=swot_list(eth_swot.get("weaknesses",[])),
            eth_o=swot_list(eth_swot.get("opportunities",[])),
            eth_t=swot_list(eth_swot.get("threats",[])),
            sol_s=swot_list(sol_swot.get("strengths",[])),
            sol_w=swot_list(sol_swot.get("weaknesses",[])),
            sol_o=swot_list(sol_swot.get("opportunities",[])),
            sol_t=swot_list(sol_swot.get("threats",[])),
            bp_coin=bp.get("coin","?"),
            bp_rationale=bp.get("rationale","?"),
            bp_entry=bp.get("entry","?"),
            bp_target=bp.get("target","?"),
            bp_risk=bp.get("risk","?"),
        )

        try:
            REPORT_HTML.write_text(html, encoding="utf-8")
        except Exception as e:
            logger.error("[Research] HTML write failed: {}".format(e))
