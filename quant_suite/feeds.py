"""
feeds.py - perception->engine bridge for external data (news, sentiment).

The bot's Python cannot call MCP connectors (LunarCrush / MT Newswires) directly.
So Claude (interactively, or via the scheduled daily task) fetches + assesses them
and writes cache files under data/; this module READS those caches and exposes
graceful helpers (neutral no-op when a cache is absent/stale).

  data/news_flags.json   -> {"risk_score":0-1,"level":..,"drivers":[..],"generated":..}
  data/sentiment.json    -> {"BTC":{"vote":+1/0/-1,"galaxy":..,"sentiment":..}, ...}
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
_MAX_AGE_H = 12  # caches older than this are treated as stale -> neutral


def _load(name: str) -> dict:
    p = ROOT / "data" / name
    if not p.exists():
        return {}
    try:
        d = json.loads(p.read_text())
    except Exception:
        return {}
    gen = d.get("generated")
    if gen:
        try:
            age = (datetime.now(timezone.utc) - datetime.fromisoformat(str(gen).replace("Z", "+00:00"))).total_seconds() / 3600
            if age > _MAX_AGE_H:
                d["_stale"] = True
        except Exception:
            pass
    return d


def load_news_risk() -> dict:
    d = _load("news_flags.json")
    if not d or d.get("_stale"):
        return {"risk_score": 0.0, "level": "neutral", "drivers": [], "stale": True}
    return {"risk_score": float(d.get("risk_score", 0.0)), "level": d.get("level", "low"),
            "drivers": d.get("drivers", []), "generated": d.get("generated"), "stale": False}


def news_size_multiplier() -> float:
    """Risk-off -> trade smaller (event/gap risk cuts both directions)."""
    r = load_news_risk()["risk_score"]
    return 0.3 if r >= 0.66 else (0.6 if r >= 0.33 else 1.0)


def load_sentiment() -> dict:
    d = _load("sentiment.json")
    return {} if d.get("_stale") else d


def sentiment_vote(coin: str) -> int:
    s = load_sentiment().get(str(coin).upper(), {})
    try:
        return int(s.get("vote", 0))
    except Exception:
        return 0


def load_market_sentiment() -> dict:
    """Crypto Fear & Greed (free, alternative.me). Written by the scan layer via web_fetch."""
    d = _load("market_sentiment.json")
    return {} if d.get("_stale") else d


def fng_side_multiplier(side: str) -> float:
    """Extreme fear -> trim shorts / favor longs (squeeze risk); extreme greed -> opposite."""
    v = load_market_sentiment().get("value")
    if v is None:
        return 1.0
    v = float(v)
    if v <= 20:
        return 1.1 if side == "buy" else 0.6
    if v >= 80:
        return 0.6 if side == "buy" else 1.1
    if v <= 40:
        return 1.05 if side == "buy" else 0.85
    if v >= 60:
        return 0.85 if side == "buy" else 1.05
    return 1.0


def refresh_lunarcrush_sentiment(api_key: str | None = None) -> dict:
    """HOST-SIDE helper: populate data/sentiment.json from the LunarCrush REST API
    (free tier: 100/day; one coins/list call covers the universe). No-op without a
    key. Activates the engine's per-coin sentiment vote. Not run in the sandbox."""
    import os
    key = api_key or os.getenv("LUNARCRUSH_API_KEY")
    if not key:
        return {"ok": False, "reason": "no LUNARCRUSH_API_KEY env"}
    try:
        import requests
        r = requests.get("https://lunarcrush.com/api4/public/coins/list/v1",
                         headers={"Authorization": f"Bearer {key}"}, timeout=20)
        rows = r.json().get("data", [])
        flat = {}
        for x in rows:
            sym = str(x.get("symbol", "")).upper()
            gs = x.get("galaxy_score")
            if not sym or gs is None:
                continue
            flat[sym] = {"vote": 1 if gs >= 65 else (-1 if gs <= 35 else 0),
                         "galaxy": gs, "sentiment": x.get("sentiment"), "alt_rank": x.get("alt_rank")}
        flat["generated"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flat["source"] = "LunarCrush REST coins/list/v1"
        (ROOT / "data" / "sentiment.json").write_text(json.dumps(flat, indent=2))
        return {"ok": True, "coins": len(flat) - 2}
    except Exception as e:
        return {"ok": False, "reason": str(e)[:140]}
