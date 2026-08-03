"""Offline tests for curated X/Twitter → news headline feed (2026-07-28)."""
from __future__ import annotations

import json

import pytest

from core.data_feeds import twitter_feed as tf
from core import news_scanner as ns


X_API_FIXTURE = {
    "data": [
        {
            "id": "123",
            "text": "Bitcoin ETF inflows hit record as institutional adoption surges",
            "author_id": "1",
            "created_at": "2026-07-27T12:00:00.000Z",
        },
        {
            "id": "124",
            "text": "Exchange hacked — funds frozen after exploit",
            "author_id": "2",
            "created_at": "2026-07-27T11:00:00.000Z",
        },
    ],
    "includes": {
        "users": [
            {"id": "1", "username": "whale_alert"},
            {"id": "2", "username": "lookonchain"},
        ]
    },
}


@pytest.fixture(autouse=True)
def _reset_health():
    ns._SOURCE_HEALTH.clear()
    yield
    ns._SOURCE_HEALTH.clear()


@pytest.fixture
def scanner(monkeypatch):
    sc = ns.NewsScanner()
    monkeypatch.setattr(ns, "_rate_ok", lambda source: True)
    return sc


def test_tweet_to_article_maps_schema_and_sentiment():
    art = tf.tweet_to_article(
        "Bitcoin surges to record high after ETF approval",
        username="CoinDesk",
        tweet_id="99",
        created_at="2026-07-27T15:30:00Z",
    )
    assert art is not None
    assert art["source"] == "X/@CoinDesk"
    assert art["link"].endswith("/status/99")
    assert art["sentiment"] > 0
    assert art["impact"] == "HIGH"
    assert "Twitter" in art["tags"]
    assert art["published"].startswith("2026-07-27")


def test_tweet_to_article_skips_empty():
    assert tf.tweet_to_article("   ", username="x") is None


def test_normalize_accounts_strips_at_and_dedupes():
    assert tf._normalize_accounts(["@Whale", "whale", " CoinDesk "]) == [
        "Whale",
        "CoinDesk",
    ]


def test_fetch_x_api_maps_fixture(monkeypatch):
    monkeypatch.setattr(
        tf,
        "_http_json",
        lambda *a, **k: X_API_FIXTURE,
    )
    arts = tf._fetch_x_api(
        ["whale_alert", "lookonchain"],
        bearer="tok",
        max_results=20,
    )
    assert len(arts) == 2
    assert arts[0]["source"] == "X/@whale_alert"
    assert arts[1]["sentiment"] < 0


def test_fetch_headlines_no_token_rss_off_fail_open(monkeypatch):
    monkeypatch.delenv("X_BEARER_TOKEN", raising=False)
    monkeypatch.delenv("TWITTER_BEARER_TOKEN", raising=False)
    assert tf.fetch_twitter_headlines({"enabled": True, "rss_fallback": False}) == []


def test_fetch_headlines_disabled():
    assert tf.fetch_twitter_headlines({"enabled": False}) == []


def test_fetch_headlines_uses_api_when_bearer_set(monkeypatch):
    monkeypatch.setenv("X_BEARER_TOKEN", "secret")
    monkeypatch.setattr(
        tf,
        "_fetch_x_api",
        lambda accounts, bearer="", max_results=20: [
            {
                "title": "Test",
                "source": "X/@whale_alert",
                "sentiment": 1,
                "impact": "LOW",
                "breaking": False,
                "tags": ["Twitter"],
                "published": "2026-07-27 12:00",
                "link": "https://x.com/whale_alert/status/1",
                "categories": "Twitter",
            }
        ],
    )
    arts = tf.fetch_twitter_headlines(
        {"enabled": True, "accounts": ["whale_alert"], "rss_fallback": False}
    )
    assert len(arts) == 1
    assert arts[0]["source"].startswith("X/@")


def test_scanner_merges_twitter_into_all_news(scanner, monkeypatch):
    monkeypatch.setattr(ns, "_fetch_xml", lambda url, timeout=8: None)
    monkeypatch.setitem(ns.NEWS_CFG, "twitter_enabled", True)
    monkeypatch.setattr(
        "core.data_feeds.twitter_feed.fetch_twitter_headlines",
        lambda cfg=None: [
            {
                "title": "Solana partnership upgrade lands",
                "source": "X/@lookonchain",
                "sentiment": 1,
                "impact": "MEDIUM",
                "breaking": False,
                "tags": ["Twitter", "lookonchain"],
                "published": "2026-07-27 12:00",
                "link": "https://x.com/lookonchain/status/1",
                "categories": "Twitter",
            }
        ],
    )
    # Force twitter rate window open
    ns._RATE_LIMIT["twitter"]["last"] = 0.0
    arts = scanner._fetch_all_news()
    assert any(a.get("source") == "X/@lookonchain" for a in arts)
    assert ns._SOURCE_HEALTH.get("twitter", {}).get("ok", 0) >= 1


def test_scanner_twitter_disabled(scanner, monkeypatch):
    monkeypatch.setattr(ns, "_fetch_xml", lambda url, timeout=8: None)
    monkeypatch.setitem(ns.NEWS_CFG, "twitter_enabled", False)

    def boom(cfg=None):
        raise AssertionError("twitter must not be called when disabled")

    monkeypatch.setattr(
        "core.data_feeds.twitter_feed.fetch_twitter_headlines", boom
    )
    assert scanner._fetch_twitter_news() == []
