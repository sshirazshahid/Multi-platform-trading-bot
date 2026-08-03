"""
core/data_feeds/twitter_feed.py — Curated X/Twitter crypto accounts → news headlines.

Advisory research input only (same sentiment/impact path as RSS). Fail-open:
missing credentials, HTTP errors, or dead mirrors return [] and never block
the news scan / trading loop.

Fetch order:
  1. X API v2 recent search when ``X_BEARER_TOKEN`` / ``TWITTER_BEARER_TOKEN``
     is set (official, preferred).
  2. Optional RSSHub user feeds (``TWITTER_RSS_FALLBACK=true``) — best-effort
     keyless mirrors; many public instances are flaky.

HONESTY: tweet noise is high; this enriches headlines for research/sentiment,
it does not create a Twitter-alpha trading edge.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Iterable

from loguru import logger

# Default curated crypto journalists / on-chain / market accounts (handles only).
# Override via NEWS["twitter_accounts"] or TWITTER_ACCOUNTS env (comma-separated).
DEFAULT_TWITTER_ACCOUNTS: tuple[str, ...] = (
    "whale_alert",
    "lookonchain",
    "WuBlockchain",
    "DocumentingBTC",
    "CoinDesk",
    "TheBlock__",
    "cointelegraph",
    "Tier10",
    "santimentfeed",
    "glassnode",
)

_USER_AGENT = "TradingBot/2.0 (news research; +https://github.com/local)"


def _bearer_token() -> str:
    return (
        os.getenv("X_BEARER_TOKEN", "").strip()
        or os.getenv("TWITTER_BEARER_TOKEN", "").strip()
    )


def _normalize_accounts(accounts: Iterable[str] | None) -> list[str]:
    if not accounts:
        return list(DEFAULT_TWITTER_ACCOUNTS)
    out: list[str] = []
    seen: set[str] = set()
    for raw in accounts:
        handle = str(raw or "").strip().lstrip("@")
        if not handle:
            continue
        key = handle.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(handle)
    return out


def _parse_tweet_time(raw: str) -> str:
    """Return ``YYYY-MM-DD HH:MM`` (UTC) for news_scanner sort keys."""
    if not raw:
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
    try:
        dt = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M")
    except (TypeError, ValueError):
        return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")


def tweet_to_article(
    text: str,
    *,
    username: str,
    tweet_id: str = "",
    created_at: str = "",
    score_fn=None,
    impact_fn=None,
) -> dict | None:
    """Map one tweet into the NewsScanner article schema."""
    title = " ".join((text or "").split())
    if not title:
        return None
    # Cap length like RSS path (~140 used elsewhere for MCP; keep more context).
    if len(title) > 280:
        title = title[:277] + "..."
    handle = username.lstrip("@")
    link = (
        f"https://x.com/{handle}/status/{tweet_id}"
        if tweet_id
        else f"https://x.com/{handle}"
    )
    if score_fn is None or impact_fn is None:
        from core.news_scanner import _classify_impact, _score_headline

        score_fn = score_fn or _score_headline
        impact_fn = impact_fn or _classify_impact
    return {
        "title": title,
        "link": link,
        "published": _parse_tweet_time(created_at),
        "categories": "Twitter",
        "sentiment": int(score_fn(title)),
        "impact": str(impact_fn(title)),
        "breaking": False,
        "tags": ["Twitter", handle],
        "source": f"X/@{handle}",
    }


def _http_json(url: str, *, headers: dict | None = None, timeout: int = 12) -> dict | list | None:
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Accept": "application/json",
            **(headers or {}),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8", errors="replace"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError, json.JSONDecodeError, OSError) as exc:
        logger.debug(f"[Twitter] HTTP failed: {exc}")
        return None


def _fetch_x_api(
    accounts: list[str],
    *,
    bearer: str,
    max_results: int = 20,
) -> list[dict]:
    """X API v2 recent search for ``from:user`` OR-query (one request)."""
    if not bearer or not accounts:
        return []
    # Recent-search query length limit is ~512 chars — keep account batch small.
    batch = accounts[:12]
    query = " OR ".join(f"from:{u}" for u in batch) + " -is:reply -is:retweet"
    params = urllib.parse.urlencode(
        {
            "query": query,
            "max_results": str(max(10, min(int(max_results), 100))),
            "tweet.fields": "created_at,author_id,lang",
            "expansions": "author_id",
            "user.fields": "username",
        }
    )
    url = f"https://api.twitter.com/2/tweets/search/recent?{params}"
    data = _http_json(url, headers={"Authorization": f"Bearer {bearer}"})
    if not isinstance(data, dict):
        return []
    users = {
        str(u.get("id")): str(u.get("username") or "")
        for u in (data.get("includes") or {}).get("users") or []
        if u.get("id")
    }
    articles: list[dict] = []
    for tw in data.get("data") or []:
        text = tw.get("text") or ""
        uid = str(tw.get("author_id") or "")
        username = users.get(uid) or "unknown"
        art = tweet_to_article(
            text,
            username=username,
            tweet_id=str(tw.get("id") or ""),
            created_at=str(tw.get("created_at") or ""),
        )
        if art:
            articles.append(art)
    return articles


def _fetch_rsshub_user(
    username: str,
    *,
    base_url: str,
    fetch_xml,
    parse_rss_items,
) -> list[dict]:
    """Best-effort RSSHub ``/twitter/user/:id`` mirror (keyless)."""
    handle = username.lstrip("@")
    base = (base_url or "https://rsshub.app").rstrip("/")
    url = f"{base}/twitter/user/{handle}"
    xml_text = fetch_xml(url, timeout=10)
    if not xml_text:
        return []
    items = parse_rss_items(xml_text, f"X/@{handle}")
    # Retag source consistently even if RSS title channel differs.
    for it in items:
        it["source"] = f"X/@{handle}"
        tags = list(it.get("tags") or [])
        if "Twitter" not in tags:
            tags.insert(0, "Twitter")
        if handle not in tags:
            tags.append(handle)
        it["tags"] = tags
    return items


def fetch_twitter_headlines(cfg: dict | None = None) -> list[dict]:
    """Return NewsScanner-shaped headlines from curated X accounts.

    ``cfg`` keys (all optional):
      enabled, accounts, max_results, rss_fallback, rsshub_base
    """
    cfg = cfg or {}
    if cfg.get("enabled", True) is False:
        return []

    env_accounts = os.getenv("TWITTER_ACCOUNTS", "").strip()
    if env_accounts:
        accounts = _normalize_accounts(env_accounts.split(","))
    else:
        accounts = _normalize_accounts(cfg.get("accounts"))
    if not accounts:
        return []

    max_results = int(cfg.get("max_results") or 20)
    articles: list[dict] = []

    bearer = _bearer_token()
    if bearer:
        try:
            articles = _fetch_x_api(accounts, bearer=bearer, max_results=max_results)
            if articles:
                logger.info(
                    f"[Twitter] X API fetched {len(articles)} tweets "
                    f"from {len(accounts)} curated accounts"
                )
                return articles
            logger.warning("[Twitter] X API returned 0 tweets — trying RSS fallback")
        except Exception as exc:
            logger.warning(f"[Twitter] X API error (fail-open): {exc}")
    else:
        logger.info(
            "[Twitter] No X_BEARER_TOKEN/TWITTER_BEARER_TOKEN — "
            "skipping official API (RSS fallback may still run)"
        )

    rss_on = cfg.get("rss_fallback")
    if rss_on is None:
        rss_on = os.getenv("TWITTER_RSS_FALLBACK", "true").lower() == "true"
    if not rss_on:
        return []

    try:
        from core.news_scanner import _fetch_xml, _parse_rss_items
    except Exception as exc:
        logger.debug(f"[Twitter] RSS helpers unavailable: {exc}")
        return []

    base = str(cfg.get("rsshub_base") or os.getenv("TWITTER_RSSHUB_BASE", "https://rsshub.app"))
    # Cap RSS fan-out to avoid long scan cycles on dead mirrors.
    for handle in accounts[:8]:
        try:
            articles.extend(
                _fetch_rsshub_user(
                    handle,
                    base_url=base,
                    fetch_xml=_fetch_xml,
                    parse_rss_items=_parse_rss_items,
                )
            )
        except Exception as exc:
            logger.debug(f"[Twitter] RSSHub @{handle} failed: {exc}")

    if articles:
        logger.info(f"[Twitter] RSS fallback fetched {len(articles)} items")
    else:
        logger.debug("[Twitter] No tweets this cycle (fail-open)")
    return articles
