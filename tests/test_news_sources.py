"""Offline tests for the 2026-07-10 news-source repair.

Covers:
  - generic RSS parsing (news_scanner._parse_rss_items)
  - multi-source merge + per-source health counters
  - fail-open behavior (all sources dead -> empty news, neutral sentiment, no crash)
  - news_sentiment_feed scanner-cache fallback (data/news_cache.json)

All fixtures are inline; no network.
"""

from __future__ import annotations

import json
import time
from datetime import datetime

import pytest

from core import news_scanner as ns
from core.data_feeds import news_sentiment_feed as nsf

RSS_FIXTURE = """<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"><channel>
  <title>Fixture Feed</title>
  <item>
    <title>Bitcoin surges to record high as ETF approval lands</title>
    <link>https://example.com/a</link>
    <pubDate>Fri, 10 Jul 2026 06:00:00 +0000</pubDate>
    <category>BTC</category>
    <category>Markets</category>
  </item>
  <item>
    <title>Major exchange hacked, solana funds frozen</title>
    <link>https://example.com/b</link>
    <pubDate>Fri, 10 Jul 2026 05:00:00 +0000</pubDate>
    <category>SOL</category>
  </item>
  <item>
    <title></title>
    <link>https://example.com/empty</link>
  </item>
</channel></rss>"""


@pytest.fixture(autouse=True)
def _reset_health():
    ns._SOURCE_HEALTH.clear()
    yield
    ns._SOURCE_HEALTH.clear()


@pytest.fixture
def scanner(monkeypatch):
    sc = ns.NewsScanner()
    # neutralize rate limiting so each call actually attempts a fetch
    monkeypatch.setattr(ns, "_rate_ok", lambda source: True)
    return sc


class TestParseRssItems:
    def test_parses_titles_sentiment_impact(self):
        items = ns._parse_rss_items(RSS_FIXTURE, "FixtureFeed")
        # empty-title item dropped
        assert len(items) == 2
        first, second = items
        assert first["title"].startswith("Bitcoin surges")
        assert first["source"] == "FixtureFeed"
        assert first["sentiment"] > 0
        assert first["impact"] == "HIGH"  # "etf approval" + "record high"
        assert first["published"].startswith("2026-07-10")
        assert "BTC" in first["tags"]
        assert second["sentiment"] < 0
        assert second["impact"] == "HIGH"  # "hack"

    def test_bad_xml_returns_empty(self):
        assert ns._parse_rss_items("<not-xml", "X") == []


class TestMultiSourceMergeAndHealth:
    def test_merge_marks_ok(self, scanner, monkeypatch):
        monkeypatch.setattr(ns, "_fetch_xml", lambda url, timeout=8: RSS_FIXTURE)
        articles = scanner._fetch_all_news()
        # every RSS source returns the 2 fixture items
        assert len(articles) == 2 * len(ns.RSS_SOURCES)
        for key, _name, _url in ns.RSS_SOURCES:
            assert ns._SOURCE_HEALTH[key]["ok"] == 1
            assert ns._SOURCE_HEALTH[key]["dead"] == 0

    def test_dead_sources_marked(self, scanner, monkeypatch):
        monkeypatch.setattr(ns, "_fetch_xml", lambda url, timeout=8: None)
        articles = scanner._fetch_all_news()
        assert articles == []
        for key, _name, _url in ns.RSS_SOURCES:
            assert ns._SOURCE_HEALTH[key]["dead"] == 1

    def test_health_summary_string(self, scanner, monkeypatch):
        monkeypatch.setattr(ns, "_fetch_xml", lambda url, timeout=8: RSS_FIXTURE)
        scanner._fetch_all_news()
        summary = ns._health_summary()
        assert "coindesk" in summary and "ok" in summary


class TestFailOpen:
    def test_no_sources_no_crash(self, scanner, monkeypatch):
        monkeypatch.setattr(ns, "_fetch_xml", lambda url, timeout=8: None)
        monkeypatch.setattr(ns, "_fetch", lambda url, timeout=8: None)
        articles = scanner._fetch_all_news()
        assert articles == []
        assert scanner._build_sentiment(articles) == {}
        scanner._cache = {"news": articles, "sentiment": {}}
        assert scanner.get_news_signals() == []
        assert scanner.symbol_sentiment("BTC/USDT") == 0


class TestFeedScannerCacheFallback:
    def _write_cache(self, tmp_path, *, age_sec: int, news: list) -> None:
        payload = {
            "fetched_at": datetime.fromtimestamp(time.time() - age_sec).isoformat(),
            "news": news,
        }
        p = tmp_path / "news_cache.json"
        p.write_text(json.dumps(payload), encoding="utf-8")
        return p

    def test_fresh_cache_mapped(self, tmp_path, monkeypatch):
        p = self._write_cache(
            tmp_path,
            age_sec=60,
            news=[
                {
                    "title": "Exchange hacked, funds stolen",
                    "sentiment": -3,
                    "published": "2026-07-10 06:00",
                    "source": "CoinDesk",
                    "tags": ["BTC"],
                },
                {
                    "title": "Bitcoin trading volume steady",
                    "sentiment": 0,
                    "published": "",
                    "source": "Decrypt",
                    "tags": [],
                },
            ],
        )
        monkeypatch.setattr(nsf, "SCANNER_CACHE_PATH", p)
        feed = nsf.NewsSentimentFeed(cache_ttl=0)
        articles = feed._articles_from_scanner_cache()
        assert len(articles) == 2
        assert articles[0]["sentiment"] == "NEGATIVE"  # mapped from int -3
        assert articles[0]["categories"] == [{"NAME": "BTC"}]
        assert articles[0]["published_on"] > 0
        assert articles[1]["sentiment"] == "NEUTRAL"  # reclassified locally

    def test_stale_cache_ignored(self, tmp_path, monkeypatch):
        p = self._write_cache(
            tmp_path,
            age_sec=3 * 3600,
            news=[
                {"title": "Old news", "sentiment": 1, "published": "", "source": "X", "tags": []},
            ],
        )
        monkeypatch.setattr(nsf, "SCANNER_CACHE_PATH", p)
        feed = nsf.NewsSentimentFeed(cache_ttl=0)
        assert feed._articles_from_scanner_cache() == []

    def test_missing_cache_fail_open(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nsf, "SCANNER_CACHE_PATH", tmp_path / "nope.json")
        feed = nsf.NewsSentimentFeed(cache_ttl=0)
        assert feed._articles_from_scanner_cache() == []

    def test_fetch_uses_cache_when_network_dead(self, tmp_path, monkeypatch):
        p = self._write_cache(
            tmp_path,
            age_sec=60,
            news=[
                {
                    "title": "Bitcoin exchange hacked in massive exploit",
                    "sentiment": -2,
                    "published": "2026-07-10 06:00",
                    "source": "CoinDesk",
                    "tags": ["BTC"],
                },
            ],
        )
        monkeypatch.setattr(nsf, "SCANNER_CACHE_PATH", p)

        def dead_call(limit=50):
            raise OSError("network down")

        monkeypatch.setattr(
            "core.data_feeds._coindesk_caller.call_coindesk_news",
            dead_call,
            raising=False,
        )
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: (_ for _ in ()).throw(OSError("network down")),
        )
        feed = nsf.NewsSentimentFeed(cache_ttl=0)
        result = feed.fetch(["BTC"])
        assert result["BTC"]["negative_count"] >= 1
        assert result["__market__"]["article_count_2h"] == 1

    def test_all_dead_neutral_no_crash(self, tmp_path, monkeypatch):
        monkeypatch.setattr(nsf, "SCANNER_CACHE_PATH", tmp_path / "nope.json")

        def dead_call(limit=50):
            raise OSError("network down")

        monkeypatch.setattr(
            "core.data_feeds._coindesk_caller.call_coindesk_news",
            dead_call,
            raising=False,
        )
        monkeypatch.setattr(
            "urllib.request.urlopen",
            lambda *a, **k: (_ for _ in ()).throw(OSError("network down")),
        )
        feed = nsf.NewsSentimentFeed(cache_ttl=0)
        result = feed.fetch(["BTC", "ETH"])
        assert result["BTC"]["sentiment_score"] == 0.0
        assert result["BTC"]["has_veto_news"] is False
        assert result["__market__"]["sentiment_score"] == 0.0


# ── 2026-07-10 wiring audit: the actionable-signals dead wire ─────────────────
def test_news_signals_consumed_by_prompt_builder():
    """bot_engine stuffs news_context["news_signals"] (and logs 'News
    signals: N actionable') — the mcp_brain prompt builder must READ that
    key or the entire actionable-signal computation influences nothing
    (the dead wire found by the 2026-07-10 integration audit)."""
    from pathlib import Path

    src = Path("core/mcp_brain.py").read_text(encoding="utf-8")
    assert 'nc.get("news_signals"' in src, (
        "prompt builder must consume news_context['news_signals']"
    )
    assert "NEWS SIGNALS:" in src
