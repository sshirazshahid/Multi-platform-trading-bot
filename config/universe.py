"""Trading pairs, commodities, analysis-only instruments."""
import os

_TOP_SPOT = [
    "BTC/USDT",
    "ETH/USDT",
    "BNB/USDT",
    "SOL/USDT",
    "XRP/USDT",
    "DOGE/USDT",
    "ADA/USDT",
    "AVAX/USDT",
    "LINK/USDT",
    "DOT/USDT",
    "UNI/USDT",
    "LTC/USDT",
    "BCH/USDT",
    "NEAR/USDT",
    "APT/USDT",
    "FIL/USDT",
    "ARB/USDT",
    "OP/USDT",
    "ATOM/USDT",
    "SUI/USDT",
    "SEI/USDT",
    "INJ/USDT",
    "FET/USDT",
    "RENDER/USDT",
    "TIA/USDT",
    "ALGO/USDT",
    "IOTA/USDT",
    "VET/USDT",
    "PEPE/USDT",
    "WIF/USDT",
    # 2026-05-31: added XLM — liquid top-coin listed on all 3 venues (owner-directed).
    "XLM/USDT",
]
_TOP_FUTURES = [s.replace("/USDT", "/USDT:USDT") for s in _TOP_SPOT]
# 2026-05-31: PRUNED commodity perps (XAU/XAG/CL) from the traded universe (owner-directed).
# They are not listed uniformly across Binance/Bybit/Bitget, so every scan cycle logged
# "[Bybit] XAU/USDT not available — skipped" / "CL/USDT not available" noise for zero benefit
# (no validated commodity edge; this is a crypto bot). The COMMODITIES metadata block below is
# left intact (dormant) so commodities can be re-added cleanly later if ever desired.

TRADING_PAIRS = {
    "binance": {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
    "bybit": {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
    "bitget": {"spot": list(_TOP_SPOT), "futures": list(_TOP_FUTURES)},
}

UNIVERSE_WHITELIST = set(_TOP_SPOT) | set(_TOP_FUTURES)

# Dual-model pair adjudication FIT_BAND_PAPER bases (2026-07-22 dossier 18_*).
# Soft OPEN priority only under PAPER research — not a promotion claim.
FIT_BAND_PAPER_BASES = frozenset(
    b.strip().upper()
    for b in os.getenv(
        "FIT_BAND_PAPER_BASES", "ALGO,ARB,AVAX,ETH,LINK"
    ).split(",")
    if b.strip()
)


MEME_COINS = {"DOGE", "SHIB", "PEPE", "WIF", "BONK", "FLOKI", "TURBO", "LOOM"}

# ==============================================================
# COMMODITY METADATA
# Used by the strategy selector to apply commodity-appropriate
# parameters (slower EMAs, wider ATR, different ADX thresholds)
# ==============================================================
COMMODITIES = {
    # symbol_base → metadata
    "XAU": {
        "name": "Gold",
        "emoji": "🥇",
        "atr_mult": 1.5,  # wider SL for Gold (less volatile intraday)
        "min_adx": 18,  # Gold trends strongly — lower ADX threshold
        "corr_asset": "USD",  # inversely correlated with USD strength
    },
    "XAG": {
        "name": "Silver",
        "emoji": "🥈",
        "atr_mult": 1.8,  # Silver more volatile than Gold
        "min_adx": 20,
        "corr_asset": "XAU",  # follows Gold with amplification
    },
    "WTI": {
        "name": "Oil (WTI)",
        "emoji": "🛢️",
        "atr_mult": 2.0,  # Oil can be very volatile
        "min_adx": 22,
        "corr_asset": "USD",  # oil priced in USD
    },
    "CL": {  # alternative symbol for Oil on some exchanges
        "name": "Oil (WTI)",
        "emoji": "🛢️",
        "atr_mult": 2.0,
        "min_adx": 22,
        "corr_asset": "USD",
    },
}


def is_commodity(symbol: str) -> bool:
    """Return True if the symbol base is a commodity (Gold/Silver/Oil)."""
    import config as cfg

    base = symbol.split("/")[0].upper()
    return base in cfg.COMMODITIES


def get_commodity_meta(symbol: str) -> dict:
    """Return commodity metadata for a symbol, or {} if not a commodity."""
    import config as cfg

    base = symbol.split("/")[0].upper()
    return cfg.COMMODITIES.get(base, {})


# ==============================================================
# ANALYSIS-ONLY INSTRUMENTS (2026-06-02; env choke LIFTED 2026-06-11, then
# superseded for TRADING by pair_discovery 2026-07-20)
# Commodity + equity perpetuals that ARE live + liquid on Binance/Bybit/Bitget
# (ccxt `XAU/USDT:USDT`, raw `XAUUSDT`). Originally hard-blocked from entries
# (~5 months of history, no screened edge).
#
# 2026-06-11 (owner UNBLOCK directive #3) made `is_analysis_only()` OPT-IN via
# ANALYSIS_ONLY_ENFORCED (default OFF). That flag is NOT a TradFi on-switch.
# Load-bearing blocks that still reject these instruments:
#   1. pair_discovery `_is_tradfi_market` → `tradfi_asset:{base}`
#   2. empty COMMODITY_BASES/STOCK_BASES + `_DISABLED_BASES` (NATGAS, PAXG, …)
#   3. AccBand funnel skip `analysis_only_accband_scope` (2026-07-30/31)
#   4. directional StrategySpec regen (no BZ/CL routes)
# 2026-07-20 honesty: BZ/CL `strategy_spec_route_not_approved` was correct —
# they are Binance TRADIFI_PERPETUAL energy synthetics (4h funding), not CME
# and not crypto. MCP-on-oil has no screened edge (2026-06-02 probe: noise-like).
# The bases set is RETAINED as the perp-only instrument registry —
# mcp_brain fetch routing and _collect_all_coins depend on it; do not empty it.
# ==============================================================
ANALYSIS_ONLY_ENFORCED = os.getenv("ANALYSIS_ONLY_ENFORCED", "false").lower() == "true"
ANALYSIS_ONLY_BASES = {
    # commodities (gold, silver, WTI, Brent, copper, Henry Hub gas)
    "XAU",
    "XAG",
    "CL",
    "BZ",
    "COPPER",
    "WTI",
    "NATGAS",
    # equity perps
    "TSLA",
    "NVDA",
    "AMZN",
    "AAPL",
    "GOOGL",
    "META",
    "MSFT",
    "MSTR",
    "COIN",
}


def is_analysis_only(symbol: str) -> bool:
    """True if the symbol is entry-blocked as an analysis-only instrument.

    Always False while ANALYSIS_ONLY_ENFORCED is off (2026-06-11 owner unblock).
    That does **not** make TradFi tradeable: pair_discovery and AccBand scope
    still reject these bases. When enforced, matches the ccxt perp form
    (`XAU/USDT:USDT`), the spot-name form (`XAU/USDT`), AND the raw exchange id
    (`XAUUSDT`) by EXACT base — so crypto-native tokens that merely share a
    prefix (e.g. XAUT = Tether Gold -> `XAUTUSDT`) are NOT caught.

    A few analysis-only bases are short/generic (CL, BZ, META, COIN). The match is
    deliberately fail-SAFE: if a real crypto perp ever shared one of these exact
    tickers it would be over-blocked (never traded), never under-blocked. This is the
    single safety choke in bot_engine._execute_open, so erring toward over-block is
    correct. The raw-id branch closes the only unsafe direction — a future caller
    passing a slash-less id silently failing OPEN through the choke.
    """
    import config as cfg

    if not cfg.ANALYSIS_ONLY_ENFORCED:
        return False
    s = symbol.upper()
    base = s.split("/")[0].split(":")[0]
    if base in cfg.ANALYSIS_ONLY_BASES:
        return True
    if "/" not in s:  # raw concatenated id (e.g. 'XAUUSDT'); match base+quote EXACTLY
        for b in cfg.ANALYSIS_ONLY_BASES:
            for q in ("USDT", "USDC", "USD"):
                if s == b + q or s == f"{b}{q}:{q}":
                    return True
    return False
