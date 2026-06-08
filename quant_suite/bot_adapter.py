"""
bot_adapter.py - PAPER-SAFE bridge between the ensemble engine and the bot.

SAFETY (hard): never places/modifies/cancels an order; no trading client imported;
returns advisory dicts only. Disabled unless QS_ENSEMBLE_ENABLED=1. Refuses to
generate for an automated loop when OPERATING_MODE=CONTROLLED_LIVE.

Modes:
  * FUTURES (scan_top / generate_setups): confluence long+short, per-coin timeframe.
  * SPOT (scan_spot): confluence LONG-ONLY on spot pairs (accumulate in uptrends).

Per-coin timeframe map (data/per_coin_tf.json) from quant_suite.timeframe_research
overrides the global sweet TF (1h) where a coin tested better (e.g. ETH/XRP on 2h).
"""
from __future__ import annotations
import os, json
from pathlib import Path
import pandas as pd, ccxt
from . import engine as E
from . import feeds

PLACES_ORDERS = False
ENABLED = os.getenv("QS_ENSEMBLE_ENABLED", "0").lower() in ("1", "true", "yes")
MODE = os.getenv("QS_ENSEMBLE_MODE", "confluence")
RISK_PCT = float(os.getenv("QS_RISK_PCT", "0.005"))
# Validated: R:R 2.0 hits TP ~44%% vs 36%% at 2.5 (higher win%/PF). Tunable via QS_RR.
TREND_RR = float(os.getenv("QS_RR", "2.0"))
TF = os.getenv("QS_ENSEMBLE_TF", "1h")
HTF = os.getenv("QS_ENSEMBLE_HTF", "4h")
BARS = int(os.getenv("QS_ENSEMBLE_BARS", "500"))
PER_COIN_TF = os.getenv("QS_PER_COIN_TF", "1").lower() in ("1", "true", "yes")
# Pure-price mode (default ON per user): ignore sentiment / fear&greed / news.
PURE_PRICE = os.getenv("QS_PURE_PRICE", "1").lower() in ("1", "true", "yes")
TF_HTF = {"15m": "1h", "30m": "2h", "1h": "4h", "2h": "8h", "4h": "1D", "1d": "1W"}
TIER_SIZE = {1: 0.5, 2: 1.0, 3: 1.25}
ROOT = Path(__file__).resolve().parents[1]


def _operating_mode() -> str:
    m = os.getenv("OPERATING_MODE")
    if m:
        return m.upper()
    try:
        import config
        return getattr(config, "OPERATING_MODE", "PAPER")
    except Exception:
        return "PAPER"


def _new_ex(market: str = "future"):
    return ccxt.binance({"options": {"defaultType": market}, "enableRateLimit": True})


def _fetch(ex, sym, tf, limit=None):
    d = pd.DataFrame(ex.fetch_ohlcv(sym, tf, limit=limit or BARS),
                     columns=["ts", "open", "high", "low", "close", "volume"])
    d["dt"] = pd.to_datetime(d["ts"], unit="ms", utc=True)
    return d


def _percoin_map() -> dict:
    if not PER_COIN_TF:
        return {}
    p = ROOT / "data" / "per_coin_tf.json"
    try:
        return json.loads(p.read_text()) if p.exists() else {}
    except Exception:
        return {}


def _tf_for(coin: str, pmap: dict):
    tf = pmap.get(coin, TF)
    return tf, TF_HTF.get(tf, HTF)


def _build_setup(sig, sym, equity, mode, op, market="futures", tf=TF):
    if sig.get("side", 0) == 0:
        return None
    if market == "spot" and sig["side"] != 1:
        return None
    side = "buy" if sig["side"] == 1 else "sell"
    px = sig["close"]
    sl_d = min(max(1.5 * sig["atr_pct"] / 100.0, 0.015), 0.035)
    trend = sig.get("trend", True)
    rr = TREND_RR if trend else 1.5
    sl = px * (1 - sl_d) if side == "buy" else px * (1 + sl_d)
    tp = px * (1 + sl_d * rr) if side == "buy" else px * (1 - sl_d * rr)
    tier = int(sig.get("tier", 1)); size_mult = TIER_SIZE.get(tier, 1.0)
    if PURE_PRICE:
        news = {}; news_mult = 1.0; sv = 0
    else:
        news = feeds.load_news_risk(); news_mult = feeds.news_size_multiplier()
        sv = feeds.sentiment_vote(sym.split("/")[0])
    sent_mult = 1.0
    if sv > 0:
        sent_mult = 1.1 if side == "buy" else 0.5
    elif sv < 0:
        sent_mult = 1.1 if side == "sell" else 0.5
    fng_mult = 1.0 if PURE_PRICE else feeds.fng_side_multiplier(side)
    eff_mult = max(0.2, size_mult * news_mult * sent_mult * fng_mult)
    notional = equity * RISK_PCT * eff_mult / sl_d
    return {"symbol": sym, "market": market, "tf": tf, "side": side, "entry": round(px, 8),
            "stop_loss": round(sl, 8), "take_profit": round(tp, 8),
            "sl_pct": round(sl_d * 100, 3), "rr": rr, "conviction_tier": tier,
            "size_mult": round(eff_mult, 3), "base_tier_mult": size_mult,
            "news_risk": news.get("level"), "news_mult": news_mult,
            "fng": feeds.load_market_sentiment().get("classification"), "fng_mult": fng_mult,
            "sentiment_vote": sv, "notional_usd": round(notional, 2),
            "risk_pct": round(RISK_PCT * eff_mult * 100, 3),
            "regime": "trend" if trend else "range", "mf_score": sig.get("mf_score"),
            "mode": mode, "operating_mode": op, "places_orders": PLACES_ORDERS,
            "advisory_only": True}


def _scan(ex, candidates, equity, mode, op, market):
    """Shared per-coin-TF scan over a list of (symbol) candidates."""
    pmap = _percoin_map()
    btc_sym = "BTC/USDT:USDT" if market == "futures" else "BTC/USDT"
    btc_by_tf = {}
    out = []
    for sym in candidates:
        try:
            coin = sym.split("/")[0]
            tf, htf = _tf_for(coin, pmap)
            if tf not in btc_by_tf:
                btc_by_tf[tf] = _fetch(ex, btc_sym, tf)
            d = _fetch(ex, sym, tf)
            if len(d) < 120:
                continue
            dd = E.prep(d, df_btc1h=btc_by_tf[tf], is_benchmark=sym.startswith("BTC"), htf_rule=htf)
            m = "confluence_long" if market == "spot" else mode
            st = _build_setup(E.latest_signal(dd, mode=m, confirmed=True), sym, equity, mode, op, market, tf)
            if st:
                out.append(st)
        except Exception:
            pass
    return out


def generate_setups(symbols, account_equity: float = 10000.0, mode: str | None = None):
    """Advisory FUTURES setups for an explicit symbol list (per-coin TF). No orders."""
    mode = mode or MODE
    op = _operating_mode()
    if op == "CONTROLLED_LIVE":
        return []
    ex = _new_ex("future"); ex.load_markets()
    return _scan(ex, symbols, account_equity, mode, op, "futures")


def scan_top(n: int = 30, account_equity: float = 10000.0, mode: str | None = None):
    """Top-N liquid FUTURES confluence scan (per-coin TF) + BTC context. Paper-safe."""
    mode = mode or MODE
    op = _operating_mode()
    if op == "CONTROLLED_LIVE":
        return {"setups": [], "operating_mode": op, "refused": True, "scanned": 0}
    ex = _new_ex("future"); ex.load_markets()
    t = ex.fetch_tickers()
    liq = sorted([(s, v) for s, v in t.items()
                  if ":USDT" in s and v.get("quoteVolume") and v.get("last") is not None],
                 key=lambda x: -x[1]["quoteVolume"])[:n]
    _mv = sorted([(s.split("/")[0], v.get("percentage"), v.get("quoteVolume"))
                  for s, v in t.items() if ":USDT" in s and v.get("percentage") is not None
                  and (v.get("quoteVolume") or 0) > 5e7], key=lambda x: -x[1])
    gainers = [{"coin": c, "pct": round(p, 2), "volM": round(q / 1e6)} for c, p, q in _mv[:6]]
    losers = [{"coin": c, "pct": round(p, 2), "volM": round(q / 1e6)} for c, p, q in _mv[-6:]]
    btc = _fetch(ex, "BTC/USDT:USDT", TF)
    bsig = E.latest_signal(E.prep(btc, is_benchmark=True, htf_rule=HTF), mode="confluence")
    setups = _scan(ex, [s for s, _ in liq], account_equity, mode, op, "futures")
    return {"setups": setups, "scanned": len(liq), "mode": mode, "operating_mode": op,
            "per_coin_tf": PER_COIN_TF, "gainers": gainers, "losers": losers,
            "btc": {"adx_4h": bsig.get("adx_4h"), "trend": bsig.get("trend"),
                    "rsi": bsig.get("rsi"), "close": bsig.get("close")}}


def scan_spot(n: int = 20, account_equity: float = 10000.0):
    """Top-N liquid SPOT confluence LONG-ONLY scan (accumulate in uptrends). Paper-safe."""
    op = _operating_mode()
    if op == "CONTROLLED_LIVE":
        return {"setups": [], "operating_mode": op, "refused": True, "scanned": 0}
    ex = _new_ex("spot"); ex.load_markets()
    btc = _fetch(ex, "BTC/USDT", TF)
    bsig = E.latest_signal(E.prep(btc, is_benchmark=True, htf_rule=HTF), mode="confluence")
    btc_bull = (bsig.get("rg_vote", 0) == 1) or (bsig.get("mf_vote", 0) == 1 and bool(bsig.get("trend")))
    if not btc_bull:
        return {"setups": [], "scanned": 0, "mode": "confluence_long(spot)",
                "operating_mode": op, "btc_macro_bullish": False,
                "note": "Spot longs gated OFF: BTC macro not bullish (spot = accumulate only in confirmed uptrends)."}
    t = ex.fetch_tickers()
    liq = sorted([(s, v) for s, v in t.items()
                  if s.endswith("/USDT") and ":" not in s and v.get("quoteVolume") and v.get("last") is not None],
                 key=lambda x: -x[1]["quoteVolume"])[:n]
    setups = _scan(ex, [s for s, _ in liq], account_equity, "confluence_long", op, "spot")
    return {"setups": setups, "scanned": len(liq), "mode": "confluence_long(spot)",
            "operating_mode": op, "per_coin_tf": PER_COIN_TF, "btc_macro_bullish": True}


def load_latest_setups() -> dict:
    p = ROOT / "data" / "ensemble_setups.json"
    if not p.exists():
        return {"setups": [], "stale": True}
    try:
        return json.loads(p.read_text())
    except Exception:
        return {"setups": [], "stale": True}


def is_enabled() -> bool:
    return ENABLED and _operating_mode() != "CONTROLLED_LIVE"


if __name__ == "__main__":
    print("ENABLED:", is_enabled(), "| mode:", MODE, "| TF:", TF, "| per-coin:", PER_COIN_TF, "| op:", _operating_mode())
    print(json.dumps(scan_top(20), indent=2, default=str))
